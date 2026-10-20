"""App / BB 版本号：读 runtime/version.json（与 update.cjs 写入位置一致）。"""

from __future__ import annotations

import json

from settings.paths import VERSION_JSON

_VERSION = json.loads(VERSION_JSON.read_text(encoding="utf-8"))

APP_VERSION = str(_VERSION["appVersion"])
APP_VERSION_CODE = int(_VERSION["versionCode"])
BB_SDK_VERSION = str(_VERSION["sdkVersion"])
BB_SIGN_WASM_VERSION = str(_VERSION["signWasmVersion"])
LEGACY_BB_SIGN_WASM_VERSIONS = frozenset(
    str(value) for value in _VERSION.get("legacySignWasmVersions", [])
)
