"""runtime.wasm must compile under wasmtime (Python shield)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wasmtime_bb import validate_runtime_wasm_file

REPO = Path(__file__).resolve().parents[1]
KNOWN_GOOD = REPO / "nodejs/bb/reverse/v1.9.10/runtime.wasm"


def test_reverse_runtime_wasm_loads_in_wasmtime():
    assert KNOWN_GOOD.is_file(), "fixture runtime.wasm missing"
    validate_runtime_wasm_file(str(KNOWN_GOOD))
