#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 ui_state.json 中「空列表」的 addressBook 项，按优先级补全后写回：

1. 各备份 ui_state 中同号且非空的列表（多备份时取条数更多的那份）；
2. 当前 addressBook 里任意账号下，首条地址的 mobile 与目标手机号一致的列表（深拷贝）。

不覆盖已有非空列表。写盘后顺带用 normalize_ui_state_address_book_keys 规整键名。
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accounts import _norm_phone
from grab_bot_ops import (
    extract_address_book_from_doc,
    load_ui_state_raw,
    normalize_ui_state_address_book_keys,
    save_ui_state_raw,
)


def _load_accounts_phones() -> list[str]:
    p = ROOT / "accounts.json"
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = raw if isinstance(raw, list) else raw.get("accounts")
    if not isinstance(rows, list):
        return []
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ph = _norm_phone(str(row.get("phone") or ""))
        if ph:
            out.append(ph)
    return out


def _merge_nonempty_from_backups(paths: list[Path]) -> dict[str, list]:
    best: dict[str, list] = {}
    for p in paths:
        if not p.is_file():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        book = extract_address_book_from_doc(raw)
        if not book:
            continue
        for ph, lst in book.items():
            if not isinstance(lst, list) or not lst:
                continue
            prev = best.get(ph)
            if not isinstance(prev, list) or len(lst) > len(prev):
                best[ph] = copy.deepcopy(lst)
    return best


def _fill_by_mobile_in_book(book: dict[str, list]) -> list[str]:
    """同一 book 内：若某号为空，但别的号下列表首条 mobile 等于该号，则拷贝该列表。"""
    filled: list[str] = []
    # 建立 mobile -> (donor_phone, list) 索引（只记第一个）
    by_mob: dict[str, tuple[str, list]] = {}
    for donor, lst in book.items():
        if not isinstance(lst, list) or not lst:
            continue
        a0 = lst[0]
        if not isinstance(a0, dict):
            continue
        mob = _norm_phone(str(a0.get("mobile") or ""))
        if mob and mob not in by_mob:
            by_mob[mob] = (donor, lst)

    for ph, lst in list(book.items()):
        nph = _norm_phone(str(ph))
        if not nph:
            continue
        if isinstance(lst, list) and len(lst) == 0:
            hit = by_mob.get(nph)
            if hit:
                _, src = hit
                book[ph] = copy.deepcopy(src)
                filled.append(nph)
    return filled


def main(argv: list[str]) -> int:
    default_backup = (
        ROOT.parent / "新建文件夹" / "4.8茅台" / "ui_state.json"
    ).resolve()
    extra = [Path(a).expanduser() for a in argv[1:] if a.strip()]
    paths = extra if extra else [default_backup]

    st = load_ui_state_raw()
    if not isinstance(st, dict):
        st = {}
    book = st.get("addressBook")
    if not isinstance(book, dict):
        book = {}

    backup_best = _merge_nonempty_from_backups(paths)
    from_backup: list[str] = []
    for ph, lst in list(book.items()):
        nph = _norm_phone(str(ph))
        if not nph:
            continue
        if isinstance(lst, list) and len(lst) == 0:
            b = backup_best.get(nph)
            if isinstance(b, list) and b:
                book[ph] = copy.deepcopy(b)
                from_backup.append(nph)

    # 账号在 accounts.json 但 addressBook 无键：若备份有则插入
    for nph in _load_accounts_phones():
        raw = book.get(nph)
        if raw is None:
            b = backup_best.get(nph)
            if isinstance(b, list) and b:
                book[nph] = copy.deepcopy(b)
                from_backup.append(nph + "(new_key)")
        elif isinstance(raw, list) and len(raw) == 0:
            b = backup_best.get(nph)
            if isinstance(b, list) and b:
                book[nph] = copy.deepcopy(b)
                from_backup.append(nph + "(dup_fill)")

    from_mobile = _fill_by_mobile_in_book(book)

    st["addressBook"] = book
    st["addressBookUpdatedAt"] = datetime.now().isoformat(timespec="seconds")
    st = normalize_ui_state_address_book_keys(st)
    save_ui_state_raw(st)

    print(
        "fill_empty_address_book:",
        "backup_files=",
        [str(p) for p in paths if p.is_file()],
        "from_backup_hits=",
        len(from_backup),
        "from_mobile_hits=",
        len(from_mobile),
    )
    if from_backup:
        print("  backup:", from_backup[:40], ("..." if len(from_backup) > 40 else ""))
    if from_mobile:
        print("  mobile:", from_mobile[:40], ("..." if len(from_mobile) > 40 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
