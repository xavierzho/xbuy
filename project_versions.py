"""项目实时版本的唯一 Python 数据源（兼容层 → settings）。"""

from __future__ import annotations

from settings.paths import RUNTIME_DIR, RUNTIME_WASM, SIGN_WASM, VERSION_JSON
from settings.versions import (
    APP_VERSION,
    APP_VERSION_CODE,
    BB_SDK_VERSION,
    BB_SIGN_WASM_VERSION,
    LEGACY_BB_SIGN_WASM_VERSIONS,
)

# 历史名称：制品目录现为仓库根 runtime/
BB_DIR = RUNTIME_DIR
BB_RUNTIME_WASM = RUNTIME_WASM
BB_SIGN_WASM = SIGN_WASM
_VERSION_FILE = VERSION_JSON
