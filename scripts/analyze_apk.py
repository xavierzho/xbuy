#!/usr/bin/env python3
"""Lightweight i茅台 APK report (no aapt): hash, native libs, dex string hints, vs runtime/version.json."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_JSON = ROOT / "runtime" / "version.json"
REPORT_DEFAULT = ROOT / "downloads" / "imoutai_apk_analysis.json"

NATIVE_HINTS = (
    "bangcle",
    "dexvmp",
    "haotian",
    "haotiansec",
    "emulator",
    "CryptoSeed",
)

DEX_STRING_PATTERNS = (
    re.compile(r"^1\.9\.\d+$"),
    re.compile(r"^10\d{3}$"),  # versionCode-like
    re.compile(r"V3\.\d+.*imaotai", re.I),
    re.compile(r"v1\.\d+\.\d+\.\d+"),  # sign wasm version hints
    re.compile(r"bangcle", re.I),
    re.compile(r"everisk", re.I),
    re.compile(r"com\.moutai\.mall"),
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_native_libs(zf: zipfile.ZipFile) -> list[str]:
    out: list[str] = []
    for name in zf.namelist():
        if name.startswith("lib/arm64-v8a/") and name.endswith(".so"):
            out.append(name.split("/")[-1])
    return sorted(out)


def dex_string_hits(zf: zipfile.ZipFile) -> dict[str, list[str]]:
    hits: dict[str, set[str]] = {p.pattern: set() for p in DEX_STRING_PATTERNS}
    dex_names = [n for n in zf.namelist() if re.fullmatch(r"classes\d*\.dex", n)]
    for dex_name in dex_names:
        data = zf.read(dex_name)
        try:
            text = subprocess.check_output(
                ["strings", "-"],
                input=data,
                stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="replace")
        except (subprocess.CalledProcessError, FileNotFoundError):
            text = data.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or len(line) > 120:
                continue
            for pat in DEX_STRING_PATTERNS:
                if pat.search(line):
                    hits[pat.pattern].add(line)
    return {k: sorted(v)[:20] for k, v in hits.items() if v}


def load_runtime_baseline() -> dict:
    if not VERSION_JSON.is_file():
        return {}
    return json.loads(VERSION_JSON.read_text(encoding="utf-8"))


def main() -> int:
    apk = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "downloads" / "imoutai.apk"
    out_json = Path(sys.argv[2]) if len(sys.argv) > 2 else REPORT_DEFAULT
    if not apk.is_file():
        print(f"APK not found: {apk}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(apk) as zf:
        natives = list_native_libs(zf)
        dex_hits = dex_string_hits(zf)

    baseline = load_runtime_baseline()
    app_versions = dex_hits.get(r"^1\.9\.\d+$", [])
    version_codes = dex_hits.get(r"^10\d{3}$", [])

    report = {
        "apk": str(apk),
        "sha256": sha256_file(apk),
        "size_bytes": apk.stat().st_size,
        "native_libs_arm64": natives,
        "native_security_libs": [n for n in natives if any(h in n.lower() for h in NATIVE_HINTS)],
        "dex_string_hits": dex_hits,
        "inferred": {
            "app_version_candidates": app_versions,
            "version_code_candidates": version_codes,
        },
        "runtime_version_json": {
            "appVersion": baseline.get("appVersion"),
            "versionCode": baseline.get("versionCode"),
            "sdkVersion": baseline.get("sdkVersion"),
            "signWasmVersion": baseline.get("signWasmVersion"),
        },
        "notes": [
            "DEX strings are heuristic; confirm with REA inspect_android_package or aapt dump badging.",
            "Official APK channel may lag iOS App Store version.",
        ],
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nWrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
