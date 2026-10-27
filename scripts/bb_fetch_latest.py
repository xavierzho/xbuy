#!/usr/bin/env python3
"""Pull latest runtime/sign_wasm.bin from Bangcle/H5 (wraps nodejs/bb/update.cjs --sync)."""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Update runtime/sign_wasm.bin + version.json")
    parser.add_argument("--check", action="store_true", help="compare only, no write")
    parser.add_argument("--auto", action="store_true", help="apply when changed")
    parser.add_argument("--sync", action="store_true", help="always pull and write (default)")
    parser.add_argument(
        "--expect-sign-version",
        default="",
        help="warn if result signWasmVersion differs (or set MT_BB_SIGN_EXPECT_VERSION)",
    )
    args = parser.parse_args()
    mode = "sync"
    if args.check:
        mode = "check"
    elif args.auto:
        mode = "auto"

    os.environ.setdefault("MT_BB_UPDATE_SIGN_FALLBACK", "0" if mode == "sync" else os.environ.get("MT_BB_UPDATE_SIGN_FALLBACK", "1"))

    # repo root on path
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    from bangcle.runtime_update import run_bb_runtime_update

    expect = args.expect_sign_version.strip() or None
    out = run_bb_runtime_update(
        mode,
        expect_sign_version=expect,
        notify_console=True,
        refresh_version_constants=mode != "check",
    )
    print(json.dumps(out.get("report") or out, ensure_ascii=False, indent=2))
    if out.get("signWarnings"):
        return 2 if out.get("ok") else 1
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
