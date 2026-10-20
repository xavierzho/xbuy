from __future__ import annotations

import os


def warmup_timeout_ms() -> int:
    raw = os.environ.get("MT_BANGCLE_WARMUP_TIMEOUT_MS") or "3000"
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        n = 3000
    return max(500, min(30_000, n))


def warmup_on_rush() -> bool:
    v = (os.environ.get("MT_BANGCLE_WARMUP_ON_RUSH") or "0").strip().lower()
    return v not in ("0", "false", "no", "off")
