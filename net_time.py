#!/usr/bin/env python3
"""
本机时钟与网络时间偏差校正（HTTP Date），供 MT-DTIME、毫秒时间戳等使用。
默认开启；离线或失败时回退本机 time.time()。关闭：环境变量 MT_TIME_SYNC=0
"""

from __future__ import annotations

import email.utils
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

_lock = threading.Lock()
_offset_sec: float = 0.0
_synced_ok: bool = False
_last_sync_mono: float = 0.0
_last_attempt_mono: float = 0.0

_MIN_RETRY_SEC = 45.0


def _time_sync_enabled() -> bool:
    return (os.environ.get("MT_TIME_SYNC", "1") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _urls() -> tuple[str, ...]:
    raw = (os.environ.get("MT_TIME_SYNC_URLS") or "").strip()
    if raw:
        parts = tuple(u.strip() for u in raw.split(",") if u.strip())
        if parts:
            return parts
    return (
        "https://www.baidu.com",
        "https://www.aliyun.com",
        "https://www.qq.com",
    )


def _timeout_total() -> float:
    try:
        return max(0.8, float((os.environ.get("MT_TIME_SYNC_TIMEOUT") or "2.5").strip()))
    except Exception:
        return 2.5


def _interval_sec() -> float:
    try:
        return max(120.0, float((os.environ.get("MT_TIME_SYNC_INTERVAL") or "3600").strip()))
    except Exception:
        return 3600.0


def _fetch_server_unix(url: str, timeout: float) -> float | None:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "Mozilla/5.0 (compatible; MT-TimeSync/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            ds = resp.headers.get("Date")
            if not ds:
                return None
            dt = email.utils.parsedate_to_datetime(ds)
            if dt is None:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return float(dt.timestamp())
    except (urllib.error.URLError, OSError, ValueError, TypeError):
        return None


def sync_time_offset(force: bool = False) -> bool:
    """
    用若干站点响应头 Date 估算 offset（server_unix - time.time()）。
    成功则写入 _offset_sec；失败则保留上次成功值或 0。
    """
    global _offset_sec, _synced_ok, _last_sync_mono, _last_attempt_mono

    if not _time_sync_enabled():
        with _lock:
            _offset_sec = 0.0
            _synced_ok = False
        return False

    now_m = time.monotonic()
    with _lock:
        if not force:
            if _synced_ok and (now_m - _last_sync_mono) < _interval_sec():
                return True
            if (
                (not _synced_ok)
                and _last_attempt_mono > 0
                and (now_m - _last_attempt_mono) < _MIN_RETRY_SEC
            ):
                return False

    to = _timeout_total()
    per = min(to, max(0.7, to / max(1, len(_urls()))))
    server_unix = None
    for url in _urls():
        server_unix = _fetch_server_unix(url, per)
        if server_unix is not None:
            break

    with _lock:
        _last_attempt_mono = time.monotonic()
        _last_sync_mono = _last_attempt_mono
        if server_unix is not None:
            _offset_sec = float(server_unix - time.time())
            _synced_ok = True
            return True
        if not _synced_ok:
            _offset_sec = 0.0
        return False


def adjusted_unix() -> float:
    if _time_sync_enabled():
        sync_time_offset(force=False)
    with _lock:
        return time.time() + _offset_sec


def adjusted_unix_ms() -> int:
    return int(adjusted_unix() * 1000)


def beijing_datetime() -> datetime:
    """东八区 wall clock（tz=UTC+8），基于校时后的 epoch。"""
    ts = adjusted_unix()
    utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    return utc.astimezone(timezone(timedelta(hours=8)))


def format_mt_dtime_gmt08() -> str:
    """与 mt1.9.6 formatDTime / 订单 MT-DTIME 一致。"""
    now = beijing_datetime()
    days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    w = now.weekday()
    js_w = (w + 1) % 7
    d = days[js_w]
    m = months[now.month - 1]
    return (
        f"{d} {m} {now.day} {now.hour:02d}:{now.minute:02d}:{now.second:02d} "
        f"GMT+08:00 {now.year}"
    )


def risk_log_timestamp() -> str:
    """YYYY-mm-dd HH:MM:SS（北京），用于本地 risk 记录等。"""
    return beijing_datetime().strftime("%Y-%m-%d %H:%M:%S")
