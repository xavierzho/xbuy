#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在覆盖写入 accounts.json / ui_state.json 之前，复制一份到 state_backups/，
避免限流、异常路径或误操作把 token、bsid、addressBook 等冲掉后无法找回。

环境变量：
  MT_DISABLE_STATE_BACKUP=1     关闭备份
  MT_STATE_BACKUP_KEEP=12       每个文件名最多保留的份数（默认 12，范围 3～200）
  MT_STATE_BACKUP_MIN_INTERVAL_SEC=0
      两次备份同一文件的最小间隔秒数；默认 0=每次写入前都备份。
      若写入极频繁可设为 30～120 减轻磁盘压力（间隔内跳过备份）。
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()
_last_mono: dict[str, float] = {}


def _disabled() -> bool:
    v = (os.environ.get("MT_DISABLE_STATE_BACKUP") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def backup_before_write(target: Path) -> None:
    """若 target 已存在则先复制到 state_backups/，再允许调用方覆盖写入。"""
    if _disabled():
        return
    p = Path(target).resolve()
    if not p.is_file():
        return
    try:
        gap = float(os.environ.get("MT_STATE_BACKUP_MIN_INTERVAL_SEC", "0") or "0")
    except (TypeError, ValueError):
        gap = 0.0
    gap = max(0.0, min(gap, 86_400.0))
    key = str(p)
    now = time.monotonic()
    if gap > 0:
        with _lock:
            prev = _last_mono.get(key, 0.0)
            if now - prev < gap:
                return
    try:
        keep = int(os.environ.get("MT_STATE_BACKUP_KEEP", "12") or "12")
    except (TypeError, ValueError):
        keep = 12
    keep = max(3, min(keep, 200))
    bdir = p.parent / "state_backups"
    try:
        bdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = bdir / f"{p.name}.{ts}.bak"
    try:
        shutil.copy2(p, dest)
    except OSError as ex:
        print(f"[state_backup] 复制 {p.name} 失败: {ex}", flush=True)
        return
    if gap > 0:
        with _lock:
            _last_mono[key] = time.monotonic()
    try:
        same = sorted(
            bdir.glob(f"{p.name}.*.bak"),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )
        for old in same[keep:]:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass
