#!/usr/bin/env python3
"""Verify runtime/runtime.wasm loads under wasmtime (Python shield path)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from settings.paths import PATHS
from wasmtime_bb import validate_runtime_wasm_file


def main() -> int:
    p = argparse.ArgumentParser(description="Validate BB runtime.wasm for Python wasmtime.")
    p.add_argument(
        "wasm",
        nargs="?",
        default=str(PATHS.runtime_wasm),
        help=f"Path to runtime.wasm (default: {PATHS.runtime_wasm})",
    )
    args = p.parse_args()
    try:
        validate_runtime_wasm_file(args.wasm)
    except Exception as ex:
        print(f"validate_runtime_wasm: FAIL: {ex}", file=sys.stderr)
        return 1
    print("validate_runtime_wasm: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
