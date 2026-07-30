#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 iOS App 包里的安全 SDK 线索。

输入可以是 `.ipa`、`Payload/*.app` 或已解包目录。工具会输出：
  - Framework/dylib/Mach-O 列表
  - JS/WASM/资源文件列表
  - Bangcle/BB/BS/百度浩天相关字符串命中
  - 可疑二进制的 strings 摘要
"""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = ROOT / "tools" / "audit" / "ios_app_security"

MARKERS = [
    "bangcle",
    "Bangcle",
    "邦邦",
    "_bb_h",
    "_bb_w",
    "_bb_a",
    "_sdk_v_",
    "Sdk-Ver-Bb",
    "Content-Web-Bb",
    "Content-Hh-Bb",
    "Content-Info-Bb",
    "__wasm_sign_wasm__",
    "__wasm_sign_version__",
    "__SIGN_WASM_VERSION__",
    "run_wac",
    "bs_h5",
    "haotian",
    "Haotian",
    "百度",
    "浩天",
    "fk1.moutai519.com.cn",
    "h5.moutai519.com.cn",
    "resource.moutai519.com.cn",
]

RESOURCE_SUFFIXES = {
    ".js",
    ".json",
    ".html",
    ".htm",
    ".wasm",
    ".bin",
    ".bundle",
    ".plist",
}


@dataclass
class FileInfo:
    path: str
    bytes: int
    sha256: str
    kind: str


@dataclass
class Hit:
    path: str
    marker: str
    line: int
    preview: str


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_text(cmd: list[str], timeout: int = 20) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
    return (proc.stdout or "") + (proc.stderr or "")


def extract_ipa(source: Path, work_dir: Path) -> Path:
    unzip_dir = work_dir / "ipa"
    unzip_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["unzip", "-q", str(source), "-d", str(unzip_dir)], check=True)
    return unzip_dir


def resolve_app_root(source: Path, work_dir: Path) -> Path:
    if source.suffix.lower() == ".ipa":
        root = extract_ipa(source, work_dir)
    else:
        root = source
    if root.suffix == ".app":
        return root
    apps = sorted(root.glob("Payload/*.app"))
    if apps:
        return apps[0]
    apps = sorted(root.rglob("*.app"))
    if apps:
        return apps[0]
    return root


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def classify_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".framework":
        return "framework"
    if suffix == ".dylib":
        return "dylib"
    if suffix == ".wasm":
        return "wasm"
    if suffix == ".js":
        return "js"
    if suffix == ".plist":
        return "plist"
    if path.is_file():
        info = run_text(["file", "-b", str(path)], timeout=5)
        if "Mach-O" in info:
            return "macho"
        if "WebAssembly" in info:
            return "wasm"
        if "text" in info or "JSON" in info or "HTML" in info:
            return "text"
    return "other"


def collect_files(app_root: Path) -> list[FileInfo]:
    infos: list[FileInfo] = []
    for path in app_root.rglob("*"):
        if path.is_dir():
            if path.suffix.lower() == ".framework":
                infos.append(FileInfo(rel(path, app_root), 0, "", "framework_dir"))
            continue
        suffix = path.suffix.lower()
        kind = classify_file(path) if suffix in RESOURCE_SUFFIXES or suffix in {".dylib", ""} else "asset"
        if kind in {"asset", "other"} and suffix not in RESOURCE_SUFFIXES:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 80 * 1024 * 1024:
            digest = ""
        else:
            digest = sha256_file(path)
        infos.append(FileInfo(rel(path, app_root), size, digest, kind))
    return infos


def read_text_file(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8", "gb18030", "latin1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def scan_text(path: Path, app_root: Path, markers: list[str]) -> list[Hit]:
    hits: list[Hit] = []
    try:
        text = read_text_file(path)
    except Exception:
        return hits
    lines = text.splitlines() or [text]
    for index, line in enumerate(lines, 1):
        for marker in markers:
            if marker in line:
                preview = line.strip()
                if len(preview) > 600:
                    pos = preview.find(marker)
                    start = max(0, pos - 220)
                    preview = preview[start : start + 600]
                hits.append(Hit(rel(path, app_root), marker, index, preview))
    return hits


def scan_binary_strings(path: Path, app_root: Path, markers: list[str]) -> list[Hit]:
    hits: list[Hit] = []
    try:
        output = run_text(["strings", "-a", str(path)], timeout=30)
    except Exception:
        return hits
    for index, line in enumerate(output.splitlines(), 1):
        for marker in markers:
            if marker in line:
                preview = line.strip()
                if len(preview) > 600:
                    pos = preview.find(marker)
                    start = max(0, pos - 220)
                    preview = preview[start : start + 600]
                hits.append(Hit(rel(path, app_root), marker, index, preview))
    return hits


def scan_hits(app_root: Path, markers: list[str]) -> list[Hit]:
    hits: list[Hit] = []
    for path in app_root.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        kind = classify_file(path)
        if suffix in {".js", ".json", ".html", ".htm", ".plist"} or kind == "text":
            hits.extend(scan_text(path, app_root, markers))
        elif suffix in {".dylib", ".wasm", ".bin"} or kind == "macho":
            hits.extend(scan_binary_strings(path, app_root, markers))
    return hits


def read_info_plist(app_root: Path) -> dict[str, object]:
    plist_path = app_root / "Info.plist"
    if not plist_path.exists():
        return {}
    try:
        with plist_path.open("rb") as f:
            data = plistlib.load(f)
        return {
            "CFBundleIdentifier": data.get("CFBundleIdentifier", ""),
            "CFBundleShortVersionString": data.get("CFBundleShortVersionString", ""),
            "CFBundleVersion": data.get("CFBundleVersion", ""),
            "CFBundleExecutable": data.get("CFBundleExecutable", ""),
        }
    except Exception as exc:  # noqa: BLE001 - 审计工具记录错误
        return {"error": f"{type(exc).__name__}: {exc}"}


def macho_loads(app_root: Path, executable_name: str) -> list[str]:
    if not executable_name:
        return []
    exe = app_root / executable_name
    if not exe.exists():
        return []
    output = run_text(["otool", "-L", str(exe)], timeout=20)
    return [line.strip() for line in output.splitlines()[1:] if line.strip()]


def write_report(source: Path, out_dir: Path, app_root: Path, files: list[FileInfo], hits: list[Hit], info: dict[str, object], loads: list[str]) -> None:
    report = {
        "source": str(source),
        "app_root": str(app_root),
        "info_plist": info,
        "linked_libraries": loads,
        "file_count": len(files),
        "hit_count": len(hits),
        "files": [asdict(item) for item in files],
        "hits": [asdict(hit) for hit in hits],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ios_app_security_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# iOS App 安全 SDK 扫描",
        "",
        f"- Source: `{source}`",
        f"- App root: `{app_root}`",
        f"- Bundle ID: `{info.get('CFBundleIdentifier', '')}`",
        f"- Version: `{info.get('CFBundleShortVersionString', '')}`",
        f"- Build: `{info.get('CFBundleVersion', '')}`",
        f"- Files: `{len(files)}`",
        f"- Hits: `{len(hits)}`",
        "",
        "## Linked Libraries",
        "",
    ]
    if loads:
        lines.extend(f"- `{item}`" for item in loads)
    else:
        lines.append("- No linked library output.")

    lines.extend(["", "## Marker Hits", ""])
    if hits:
        for hit in hits[:300]:
            lines.append(f"- `{hit.marker}` in `{hit.path}` line `{hit.line}`: {hit.preview}")
    else:
        lines.append("- No marker hits.")

    lines.extend(["", "## Interesting Files", ""])
    for item in files[:500]:
        if item.kind in {"framework_dir", "dylib", "macho", "wasm", "js", "plist", "text"}:
            lines.append(f"- `{item.kind}` `{item.path}` `{item.bytes}` `{item.sha256[:16]}`")
    (out_dir / "ios_app_security_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="扫描 iOS App 包里的 BS/BB/Bangcle 安全 SDK 线索。")
    parser.add_argument("source", help=".ipa、Payload/*.app 或已解包目录。")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="输出目录。")
    parser.add_argument("--marker", action="append", default=[], help="额外搜索标记，可重复传。")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    out_dir = Path(args.out_dir).expanduser().resolve()

    with tempfile.TemporaryDirectory(prefix="rushbuy-ios-scan-") as tmp:
        work_dir = Path(tmp)
        app_root = resolve_app_root(source, work_dir)
        info = read_info_plist(app_root)
        files = collect_files(app_root)
        hits = scan_hits(app_root, MARKERS + args.marker)
        loads = macho_loads(app_root, str(info.get("CFBundleExecutable") or ""))
        write_report(source, out_dir, app_root, files, hits, info, loads)

    print(f"[ios-scan] source={source}")
    print(f"[ios-scan] files={len(files)} hits={len(hits)}")
    print(f"[ios-scan] report={out_dir / 'ios_app_security_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
