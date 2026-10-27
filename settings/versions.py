"""App / BB 版本号：读 runtime/version.json（与 update.cjs 写入位置一致）。"""

from __future__ import annotations

import json
from typing import Any

from settings.paths import VERSION_JSON


def _apply_version_meta(meta: dict[str, Any]) -> None:
    global _VERSION, APP_VERSION, APP_VERSION_CODE, BB_SDK_VERSION
    global BB_SIGN_WASM_VERSION, LEGACY_BB_SIGN_WASM_VERSIONS
    _VERSION = meta
    APP_VERSION = str(_VERSION["appVersion"])
    APP_VERSION_CODE = int(_VERSION["versionCode"])
    BB_SDK_VERSION = str(_VERSION["sdkVersion"])
    BB_SIGN_WASM_VERSION = str(_VERSION["signWasmVersion"])
    LEGACY_BB_SIGN_WASM_VERSIONS = frozenset(
        str(value) for value in _VERSION.get("legacySignWasmVersions", [])
    )
    try:
        import project_versions as pv

        pv.APP_VERSION = APP_VERSION
        pv.APP_VERSION_CODE = APP_VERSION_CODE
        pv.BB_SDK_VERSION = BB_SDK_VERSION
        pv.BB_SIGN_WASM_VERSION = BB_SIGN_WASM_VERSION
        pv.LEGACY_BB_SIGN_WASM_VERSIONS = LEGACY_BB_SIGN_WASM_VERSIONS
    except ImportError:
        pass


_VERSION = json.loads(VERSION_JSON.read_text(encoding="utf-8"))
_apply_version_meta(_VERSION)


def reload_from_disk() -> dict[str, Any]:
    """Re-read version.json after update.cjs writes runtime/sign_wasm.bin."""
    meta = json.loads(VERSION_JSON.read_text(encoding="utf-8"))
    _apply_version_meta(meta)
    return meta
