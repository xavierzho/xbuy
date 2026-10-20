"""常用环境变量（默认值与 docs/CONFIG_INVENTORY.md 对齐）。"""

from __future__ import annotations

import os


def _truthy(name: str, default: str = "") -> bool:
    v = (os.environ.get(name) or default).strip().lower()
    return v in ("1", "true", "yes", "on")


def go_bridge_port() -> int:
    raw = (os.environ.get("MT_GO_BRIDGE_PORT") or "19091").strip()
    try:
        return max(1, min(65535, int(raw)))
    except (TypeError, ValueError):
        return 19091


def node_http_bridge_port() -> int:
    raw = (os.environ.get("MT_NODE_HTTP_BRIDGE_PORT") or "19090").strip()
    try:
        return max(1, min(65535, int(raw)))
    except (TypeError, ValueError):
        return 19090


def dist_ws_port() -> int:
    raw = (os.environ.get("MT_DIST_WS_PORT") or "8765").strip()
    try:
        return max(1, min(65535, int(raw)))
    except (TypeError, ValueError):
        return 8765


def webui_port() -> int:
    raw = (os.environ.get("MT_WEBUI_PORT") or "8989").strip()
    try:
        return max(1, min(65535, int(raw)))
    except (TypeError, ValueError):
        return 8989


def runtime_dir_override() -> str:
    return (os.environ.get("RUSHBUY_RUNTIME_DIR") or "").strip()
