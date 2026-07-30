"""项目实时版本的唯一 Python 数据源。"""

from __future__ import annotations

import json
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent / "node_bridge" / "bb" / "version.json"
_VERSION = json.loads(_VERSION_FILE.read_text(encoding="utf-8"))

APP_VERSION = str(_VERSION["appVersion"])
APP_VERSION_CODE = int(_VERSION["versionCode"])
BB_SDK_VERSION = str(_VERSION["sdkVersion"])
BB_SIGN_WASM_VERSION = str(_VERSION["signWasmVersion"])
LEGACY_BB_SIGN_WASM_VERSIONS = frozenset(
    str(value) for value in _VERSION.get("legacySignWasmVersions", [])
)
