#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从拉取的 bs_h5.min.js 中拆出安全相关 webpack 模块。

注意：bs_h5 属于 BS/百度浩天这层，不直接等同于项目里的 bb_sdk_1_9_6.js。
本工具只用于确认 H5 安全资源里出现的存储键、接口、wasm 调用线索。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MARKERS = [
    "__wasm_sign_wasm__",
    "__wasm_sign_version__",
    "__wasm_sign_ok__",
    "__SIGN_WASM_VERSION__",
    "__SIGN_WASM_OK__",
    "_sdk_v_",
    "Sdk-Ver-Bb",
    "Content-Web-Bb",
    "Content-Hh-Bb",
    "/api/v1/1/2",
    "/api/v1/1/1",
    "/bbprbdata/upload",
    "run_wac",
    "loadSignWasm",
    "checkAndUpdateSignWasm",
    "V3.5.0",
]


@dataclass
class ModuleHit:
    module_id: str
    path: str
    bytes: int
    sha256: str
    markers: list[str]


def find_latest_bundle_dir(base: Path) -> Path:
    dirs = [p for p in base.iterdir() if p.is_dir()]
    if not dirs:
        raise FileNotFoundError(f"没有找到 bundle 输出目录: {base}")
    return sorted(dirs)[-1]


def find_bs_h5_file(bundle_dir: Path) -> Path:
    candidates = sorted((bundle_dir / "resources").glob("*bs_h5.min.js"))
    if not candidates:
        candidates = sorted(bundle_dir.rglob("*bs_h5.min.js"))
    if not candidates:
        raise FileNotFoundError(f"没有找到 bs_h5.min.js: {bundle_dir}")
    return candidates[0]


def skip_string(text: str, i: int) -> int:
    quote = text[i]
    i += 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i + 1
        i += 1
    return i


def skip_comment(text: str, i: int) -> int:
    if text.startswith("//", i):
        end = text.find("\n", i + 2)
        return len(text) if end < 0 else end + 1
    if text.startswith("/*", i):
        end = text.find("*/", i + 2)
        return len(text) if end < 0 else end + 2
    return i


def find_matching_brace(text: str, open_index: int) -> int:
    depth = 0
    i = open_index
    while i < len(text):
        ch = text[i]
        if ch in ("'", '"', "`"):
            i = skip_string(text, i)
            continue
        if text.startswith("//", i) or text.startswith("/*", i):
            i = skip_comment(text, i)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def extract_modules(text: str) -> list[tuple[str, str]]:
    modules: list[tuple[str, str]] = []
    pattern = re.compile(r"([A-Za-z_$][\w$]*|\d+):function\s*\(")
    for match in pattern.finditer(text):
        module_id = match.group(1)
        brace = text.find("{", match.end())
        if brace < 0:
            continue
        end = find_matching_brace(text, brace)
        if end < 0:
            continue
        modules.append((module_id, text[match.start() : end + 1]))
    return modules


def write_hits(bundle: Path, out_dir: Path, markers: list[str]) -> dict[str, object]:
    text = bundle.read_text(encoding="utf-8", errors="replace")
    modules = extract_modules(text)
    hit_dir = out_dir / "security_modules"
    hit_dir.mkdir(parents=True, exist_ok=True)

    hits: list[ModuleHit] = []
    for module_id, body in modules:
        matched = [m for m in markers if m in body]
        if not matched:
            continue
        target = hit_dir / f"module_{module_id}.js"
        target.write_text(body, encoding="utf-8")
        raw = body.encode("utf-8")
        hits.append(
            ModuleHit(
                module_id=module_id,
                path=str(target.relative_to(out_dir)),
                bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                markers=matched,
            )
        )

    report = {
        "bundle": str(bundle),
        "bundle_bytes": bundle.stat().st_size,
        "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "module_count": len(modules),
        "hit_count": len(hits),
        "hits": [asdict(hit) for hit in hits],
    }
    (out_dir / "security_bundle_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="分析 bs_h5.min.js 中的安全相关模块。")
    parser.add_argument(
        "bundle_dir",
        nargs="?",
        default="",
        help="extract_h5_bundles.py 的输出目录；为空时使用最新目录。",
    )
    parser.add_argument("--marker", action="append", default=[], help="额外标记，可重复传。")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    base = ROOT / "tools" / "audit" / "h5_bundles"
    bundle_dir = Path(args.bundle_dir).expanduser() if args.bundle_dir else find_latest_bundle_dir(base)
    bundle = find_bs_h5_file(bundle_dir)
    report = write_hits(bundle, bundle_dir, DEFAULT_MARKERS + args.marker)

    print(f"[security-bundle] bundle={bundle}")
    print(f"[security-bundle] module_count={report['module_count']} hit_count={report['hit_count']}")
    for hit in report["hits"]:
        print(f"[security-bundle] module={hit['module_id']} bytes={hit['bytes']} markers={','.join(hit['markers'])}")
    print(f"[security-bundle] report={bundle_dir / 'security_bundle_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
