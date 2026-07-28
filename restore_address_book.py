#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从备份 JSON 合并收货地址缓存到本目录 ui_state.json（与 WebUI「合并地址备份」同源）。

用法:
  py restore_address_book.py <备份.json>           # 按手机号合并
  py restore_address_book.py <备份.json> --replace-all   # 整表替换为备份中的 addressBook

备份可为整份 ui_state.json，或仅含顶层字段 "addressBook" 的对象。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from grab_bot_ops import import_address_book_from_json_file


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    path = Path(sys.argv[1]).expanduser()
    replace_all = "--replace-all" in sys.argv[2:] or "--replace_all" in sys.argv[2:]
    out = import_address_book_from_json_file(path, replace_all=replace_all)
    if not out.get("ok"):
        print(out.get("error") or "失败", file=sys.stderr)
        return 1
    print(
        f"已写入 {ROOT / 'ui_state.json'}："
        f"phoneCount={out.get('phoneCount')} replaceAll={out.get('replaceAll')}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
