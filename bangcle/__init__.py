"""Bangcle / BB SDK Python 包：暖场、进程内签名（``sdk``）、Node 桥对照（``bridge_client``）。"""

from __future__ import annotations

import os

from bangcle.warmup import run_bangcle_warmup


def _backend() -> str:
    return (os.environ.get("MT_BANGCLE_BACKEND") or "python").strip().lower()


def bangcle_warmup(payload: dict | None = None, *, timeout: float = 20.0) -> dict:
    if _backend() in ("node", "js", "nodejs"):
        from bangcle._node import warmup_via_node

        return warmup_via_node(payload or {}, timeout)
    try:
        return run_bangcle_warmup(payload, timeout=timeout)
    except Exception as ex:
        return {"success": False, "message": str(ex), "backend": "python"}


__all__ = ["bangcle_warmup", "run_bangcle_warmup"]
