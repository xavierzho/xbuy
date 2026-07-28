#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每账号收货地址本地库（SQLite）：成功从茅台拉到列表后落盘，并记录默认地址。

- 组单/下单：启用时一律优先使用库中「默认」那条（与卡片下拉默认一致），不再依赖请求体里的 shipAddressId。
- GET /api/ui/state（及 address_book）：对账号池中的号，若库有非空列表则覆盖返回里的 addressBook，卡片默认展示库。

关闭：环境变量 MT_DISABLE_ADDRESS_DB=1
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from accounts import _norm_phone

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "account_addresses.sqlite3"
UI_STATE_PATH = ROOT / "ui_state.json"

_lock = threading.Lock()
_schema_ok = False


def _enabled() -> bool:
    v = (os.environ.get("MT_DISABLE_ADDRESS_DB") or "").strip().lower()
    return v not in ("1", "true", "yes", "on")


def enabled() -> bool:
    """是否启用本地地址库（与 MT_DISABLE_ADDRESS_DB 相反）。"""
    return _enabled()


def _ensure_schema() -> None:
    global _schema_ok
    if _schema_ok or not _enabled():
        return
    with _lock:
        if _schema_ok:
            return
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS account_address_cache (
                    phone TEXT PRIMARY KEY NOT NULL,
                    list_json TEXT NOT NULL,
                    default_ship_address_id INTEGER,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()
        _schema_ok = True


def _pick_default_ship_address(lst: object) -> dict | None:
    if not isinstance(lst, list) or not lst:
        return None
    cands = [a for a in lst if isinstance(a, dict) and a.get("shipAddressId")]
    if not cands:
        return None
    for a in cands:
        if a.get("dft"):
            return a
    return cands[0]


def save_address_list_for_phone(phone: str, lst: list[Any]) -> None:
    """成功拿到非空地址列表时调用；空列表不写库，避免覆盖掉已有缓存。"""
    if not _enabled():
        return
    ph = _norm_phone(phone)
    if not ph or not isinstance(lst, list) or not lst:
        return
    _ensure_schema()
    picked = _pick_default_ship_address(lst)
    default_sid: int | None = None
    if picked and picked.get("shipAddressId") is not None:
        try:
            default_sid = int(picked.get("shipAddressId"))
        except (TypeError, ValueError):
            default_sid = None
    now = datetime.now().isoformat(timespec="seconds")
    blob = json.dumps(lst, ensure_ascii=False)
    with _lock:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        try:
            conn.execute(
                """
                INSERT INTO account_address_cache (phone, list_json, default_ship_address_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(phone) DO UPDATE SET
                    list_json = excluded.list_json,
                    default_ship_address_id = excluded.default_ship_address_id,
                    updated_at = excluded.updated_at
                """,
                (ph, blob, default_sid, now),
            )
            conn.commit()
        finally:
            conn.close()


def sync_address_book_dict(book: object) -> None:
    """把 ui_state.addressBook 中非空列表批量写入库（导入/合并时用）。"""
    if not _enabled() or not isinstance(book, dict):
        return
    for ph, lst in book.items():
        if isinstance(lst, list) and lst:
            save_address_list_for_phone(str(ph), lst)


def get_cached_address_list(phone: str) -> list[dict] | None:
    if not _enabled():
        return None
    ph = _norm_phone(phone)
    if not ph:
        return None
    _ensure_schema()
    with _lock:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        try:
            row = conn.execute(
                "SELECT list_json FROM account_address_cache WHERE phone = ?",
                (ph,),
            ).fetchone()
        finally:
            conn.close()
    if not row or not row[0]:
        return None
    try:
        raw = json.loads(row[0])
    except Exception:
        return None
    if not isinstance(raw, list):
        return None
    out: list[dict] = []
    for x in raw:
        if isinstance(x, dict):
            out.append(dict(x))
    return out if out else None


def get_default_address_dict(phone: str) -> dict | None:
    """返回当前应作为组单/下单默认使用的一条地址（深拷贝）。"""
    lst = get_cached_address_list(phone)
    if not lst:
        return None
    picked = _pick_default_ship_address(lst)
    return dict(picked) if isinstance(picked, dict) else None


def init_schema() -> None:
    """创建 SQLite 表（幂等）。"""
    if _enabled():
        _ensure_schema()


def migrate_missing_from_ui_state() -> dict[str, Any]:
    """启动时：库中无记录但 ui_state.addressBook 有非空的号，补进库。"""
    if not _enabled() or not UI_STATE_PATH.is_file():
        return {"inserted": 0, "skipped": True}
    _ensure_schema()
    try:
        raw = json.loads(UI_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"inserted": 0, "error": "ui_state.json 读取失败"}
    book = raw.get("addressBook")
    if not isinstance(book, dict):
        return {"inserted": 0}
    _ensure_schema()
    inserted = 0
    for ph, lst in book.items():
        nph = _norm_phone(str(ph))
        if not nph or not isinstance(lst, list) or not lst:
            continue
        with _lock:
            conn = sqlite3.connect(str(DB_PATH), timeout=30)
            try:
                row = conn.execute(
                    "SELECT 1 FROM account_address_cache WHERE phone = ?",
                    (nph,),
                ).fetchone()
            finally:
                conn.close()
        if row:
            continue
        save_address_list_for_phone(nph, lst)
        inserted += 1
    return {"inserted": inserted}
