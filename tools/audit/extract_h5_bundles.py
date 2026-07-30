#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提取 H5 页面和它引用的 JS/CSS bundle。

用途：
  1. 拉取 H5 入口 HTML。
  2. 从 HTML 和 TraceLens 日志中提取 JS/CSS/JSON 资源 URL。
  3. 下载资源并生成 manifest，便于后续 diff H5 安全参数。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENTRY = "https://h5.moutai519.com.cn/mt/item/smsp-detail?appConfig=2_1_2"
DEFAULT_OUT_DIR = ROOT / "tools" / "audit" / "h5_bundles"
DEFAULT_TRACE_DIR = ROOT / "tools" / "TraceLens" / "logs" / "tracelens"

RESOURCE_RE = re.compile(
    r"https?://[^\s\"'<>\\]+|(?:src|href)\s*=\s*[\"']([^\"']+)[\"']",
    re.I,
)
SCRIPT_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.I)
LINK_RE = re.compile(r"<link[^>]+href=[\"']([^\"']+)[\"']", re.I)
INTERESTING_RE = re.compile(
    r"(\.js(?:[?#][^\"'\s<>\\]*)?$|\.css(?:[?#][^\"'\s<>\\]*)?$|\.json(?:[?#][^\"'\s<>\\]*)?$|"
    r"bs_h5|bangcle|mt-wap|chunk|bundle|hotfix)",
    re.I,
)


@dataclass
class SavedResource:
    url: str
    path: str
    status: int
    bytes: int
    sha256: str
    content_type: str
    error: str = ""


def now_tag() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def normalize_url(url: str, base: str) -> str:
    text = (url or "").strip()
    if not text or text.startswith(("javascript:", "data:", "#")):
        return ""
    return urllib.parse.urljoin(base, text)


def safe_name(url: str, index: int) -> str:
    parsed = urllib.parse.urlparse(url)
    name = Path(parsed.path).name or "index.html"
    if "." not in name:
        name += ".html"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{index:03d}_{digest}_{name}"


def decode_body(data: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower()
    if "gzip" in enc:
        return gzip.decompress(data)
    if "deflate" in enc:
        try:
            return zlib.decompress(data)
        except zlib.error:
            return zlib.decompress(data, -zlib.MAX_WBITS)
    return data


def fetch(url: str, *, timeout: int, proxy: str = "", cookie: str = "", user_agent: str = "") -> tuple[int, dict[str, str], bytes]:
    headers = {
        "User-Agent": user_agent or (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 moutai-mall"
        ),
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",
    }
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context()
    handlers: list[urllib.request.BaseHandler] = [urllib.request.HTTPSHandler(context=context)]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        info = dict(resp.headers.items())
        return int(resp.status), info, decode_body(raw, info.get("Content-Encoding", ""))


def fetch_with_curl(url: str, *, timeout: int, proxy: str = "", cookie: str = "", user_agent: str = "") -> tuple[int, dict[str, str], bytes]:
    marker = b"\n__CURL_META_BEGIN__\n"
    ua = user_agent or (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 moutai-mall"
    )
    cmd = [
        "curl",
        "-L",
        "-sS",
        "--compressed",
        "--max-time",
        str(timeout),
        "-A",
        ua,
        "-H",
        "Accept: */*",
        "-H",
        "Accept-Language: zh-CN,zh;q=0.9",
    ]
    if proxy:
        cmd.extend(["-x", proxy])
    if cookie:
        cmd.extend(["-H", f"Cookie: {cookie}"])
    cmd.extend(["-w", marker.decode("ascii") + "%{http_code}\n%{content_type}\n", url])
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 5, check=False)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"curl exit {proc.returncode}: {err}")
    if marker not in proc.stdout:
        raise RuntimeError("curl meta marker not found")
    body, meta = proc.stdout.rsplit(marker, 1)
    lines = meta.decode("utf-8", errors="replace").splitlines()
    status = int(lines[0] or 0) if lines else 0
    content_type = lines[1] if len(lines) > 1 else ""
    return status, {"Content-Type": content_type, "X-Fetch-Backend": "curl"}, body


def fetch_with_fallback(url: str, *, timeout: int, proxy: str = "", cookie: str = "", user_agent: str = "", curl_fallback: bool = True) -> tuple[int, dict[str, str], bytes]:
    try:
        return fetch(url, timeout=timeout, proxy=proxy, cookie=cookie, user_agent=user_agent)
    except Exception:
        if not curl_fallback:
            raise
        return fetch_with_curl(url, timeout=timeout, proxy=proxy, cookie=cookie, user_agent=user_agent)


def text_from_bytes(data: bytes, content_type: str = "") -> str:
    charset = ""
    match = re.search(r"charset=([\w.-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    for enc in [charset, "utf-8", "gb18030"]:
        if not enc:
            continue
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def extract_urls_from_html(html: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for regex in (SCRIPT_RE, LINK_RE):
        for hit in regex.findall(html):
            url = normalize_url(hit, base_url)
            if url:
                urls.append(url)
    for match in RESOURCE_RE.finditer(html):
        raw = match.group(0)
        if raw.lower().startswith(("src", "href")):
            raw = match.group(1) or ""
        url = normalize_url(raw, base_url)
        if url:
            urls.append(url)
    return dedupe(urls)


def extract_urls_from_logs(paths: Iterable[Path]) -> list[str]:
    urls: list[str] = []
    url_re = re.compile(r"https?://[^\s\"'<>\\]+")
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        urls.extend(url_re.findall(text))
    return dedupe(urls)


def dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = value.strip().rstrip("),，")
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def is_interesting(url: str, include_all: bool) -> bool:
    if include_all:
        return True
    return bool(INTERESTING_RE.search(url))


def save_resource(index: int, url: str, out_dir: Path, args: argparse.Namespace) -> SavedResource:
    rel_name = safe_name(url, index)
    target = out_dir / "resources" / rel_name
    try:
        status, headers, body = fetch_with_fallback(
            url,
            timeout=args.timeout,
            proxy=args.proxy,
            cookie=args.cookie,
            user_agent=args.user_agent,
            curl_fallback=not args.no_curl_fallback,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return SavedResource(
            url=url,
            path=str(target.relative_to(out_dir)),
            status=status,
            bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            content_type=headers.get("Content-Type", ""),
        )
    except Exception as exc:  # noqa: BLE001 - 审计工具要把失败写进 manifest
        return SavedResource(
            url=url,
            path=str(target.relative_to(out_dir)),
            status=0,
            bytes=0,
            sha256="",
            content_type="",
            error=f"{type(exc).__name__}: {exc}",
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="提取 H5 页面和 JS/CSS bundle。")
    parser.add_argument("--entry", default=DEFAULT_ENTRY, help="H5 入口 URL。")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="输出目录。")
    parser.add_argument("--trace-dir", default=str(DEFAULT_TRACE_DIR), help="TraceLens 日志目录。")
    parser.add_argument("--from-trace", action="store_true", help="从 TraceLens 日志补充资源 URL。")
    parser.add_argument("--include-all", action="store_true", help="下载所有 URL，默认只下载 JS/CSS/JSON/BB 相关资源。")
    parser.add_argument("--proxy", default=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or "", help="代理，例如 http://127.0.0.1:7897。")
    parser.add_argument("--cookie", default="", help="请求 H5 时附带的 Cookie。")
    parser.add_argument("--user-agent", default="", help="自定义 User-Agent。")
    parser.add_argument("--timeout", type=int, default=15, help="单个请求超时时间。")
    parser.add_argument("--limit", type=int, default=80, help="最多下载资源数。")
    parser.add_argument("--no-curl-fallback", action="store_true", help="关闭 curl 兜底下载。")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    base_out = Path(args.out_dir).expanduser()
    out_dir = base_out / now_tag()
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "entry": args.entry,
        "created_at": int(time.time()),
        "out_dir": str(out_dir),
        "proxy": args.proxy,
        "resources": [],
    }

    print(f"[h5] entry={args.entry}")
    print(f"[h5] out={out_dir}")
    urls: list[str] = []
    try:
        status, headers, html_bytes = fetch_with_fallback(
            args.entry,
            timeout=args.timeout,
            proxy=args.proxy,
            cookie=args.cookie,
            user_agent=args.user_agent,
            curl_fallback=not args.no_curl_fallback,
        )
        html_text = text_from_bytes(html_bytes, headers.get("Content-Type", ""))
        (out_dir / "entry.html").write_text(html_text, encoding="utf-8")
        manifest["entry_status"] = status
        manifest["entry_content_type"] = headers.get("Content-Type", "")
        manifest["entry_bytes"] = len(html_bytes)
        manifest["entry_sha256"] = hashlib.sha256(html_bytes).hexdigest()
        urls = extract_urls_from_html(html_text, args.entry)
    except Exception as exc:  # noqa: BLE001 - 入口失败时仍可用 TraceLens URL 继续拉资源
        manifest["entry_status"] = 0
        manifest["entry_error"] = f"{type(exc).__name__}: {exc}"
        print(f"[h5] entry ERR {manifest['entry_error']}")
        if not args.from_trace:
            raise

    if args.from_trace:
        trace_dir = Path(args.trace_dir).expanduser()
        urls.extend(extract_urls_from_logs(trace_dir.glob("*")))
        urls = dedupe(urls)

    urls = [url for url in urls if is_interesting(url, args.include_all)]
    urls = urls[: max(0, args.limit)]
    manifest["candidate_count"] = len(urls)

    for idx, url in enumerate(urls, 1):
        result = save_resource(idx, url, out_dir, args)
        print(f"[h5] {idx:03d} {result.status or 'ERR'} {result.bytes:>8} {url}")
        manifest["resources"].append(asdict(result))  # type: ignore[index]

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[h5] manifest={out_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
