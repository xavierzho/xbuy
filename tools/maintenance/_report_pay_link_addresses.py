# -*- coding: utf-8 -*-
"""一次性：payNotices 里出现过支付链接的号 → 当前本地库/addressBook 下的「默认下单地址」。"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "pay_link_phones_addresses.txt"


def norm_phone(p: object) -> str:
    s = str(p or "").strip()
    return "".join(c for c in s if c.isdigit()) or s


def pick_default(lst: object) -> dict | None:
    if not isinstance(lst, list) or not lst:
        return None
    cands = [a for a in lst if isinstance(a, dict) and a.get("shipAddressId")]
    if not cands:
        return None
    for a in cands:
        if a.get("dft"):
            return a
    return cands[0]


def addr_lines(a: dict | None) -> tuple[str, str, str, str, str]:
    if not isinstance(a, dict):
        return ("", "", "", "", "")
    name = str(a.get("name") or "").strip()
    mobile = str(a.get("mobile") or "").strip()
    addr = str(a.get("fullAddress") or a.get("address") or "").strip()
    sid = str(a.get("shipAddressId") or "").strip()
    dft = "是" if a.get("dft") else "否"
    return (name, mobile, addr, sid, dft)


def main() -> None:
    st = json.loads((ROOT / "ui_state.json").read_text(encoding="utf-8"))
    notices = st.get("payNotices") or []
    by_phone: dict[str, tuple[int, object]] = {}
    for n in notices:
        if not isinstance(n, dict):
            continue
        ph = norm_phone(n.get("phone"))
        url = str(n.get("payUrl") or "").strip()
        if not ph or not url.startswith("http"):
            continue
        t = int(n.get("t") or 0)
        oid = n.get("orderId")
        prev = by_phone.get(ph)
        if prev is None or t >= prev[0]:
            by_phone[ph] = (t, oid)

    phones = sorted(by_phone.keys(), key=lambda x: by_phone[x][0], reverse=True)

    db_path = ROOT / "account_addresses.sqlite3"
    book = st.get("addressBook") if isinstance(st.get("addressBook"), dict) else {}
    cache: dict[str, str] = {}
    if db_path.is_file():
        conn = sqlite3.connect(str(db_path))
        try:
            for r in conn.execute("select phone, list_json from account_address_cache"):
                cache[norm_phone(r[0])] = r[1]
        finally:
            conn.close()

    lines: list[str] = []
    lines.append(
        "说明：号码来自 ui_state.json 的 payNotices（payUrl 为 http 开头的记录，"
        "同一号取时间最新的一条订单号）。地址按与组单/下单相同的规则："
        "优先 SQLite 缓存列表里 dft=真 的条目，否则第一条带 shipAddressId 的；"
        "库无则回退 ui_state.addressBook。\n"
    )
    lines.append("手机号\t订单号\t地址来源\t收件人\t收货手机\t详细地址(截断)\tshipAddressId\t接口默认dft\n")

    for ph in phones:
        oid = by_phone[ph][1]
        picked = None
        src = ""
        js = cache.get(ph)
        if js:
            try:
                lst = json.loads(js)
            except Exception:
                lst = None
            picked = pick_default(lst)
            src = "本地地址库"
        if picked is None:
            lst = book.get(ph)
            if not isinstance(lst, list):
                for k, v in book.items():
                    if norm_phone(k) == ph and isinstance(v, list):
                        lst = v
                        break
            else:
                v = None
            if isinstance(lst, list) and lst:
                picked = pick_default(lst)
                src = "addressBook"
        if picked is None:
            src = "无地址缓存"
        name, mobile, addr, sid, dft = addr_lines(picked)
        addr_short = addr[:120] + ("…" if len(addr) > 120 else "")
        lines.append(
            f"{ph}\t{oid}\t{src}\t{name}\t{mobile}\t{addr_short}\t{sid}\t{dft}\n"
        )

    OUT.write_text("".join(lines), encoding="utf-8")
    print("wrote", OUT, "rows", len(phones))


if __name__ == "__main__":
    main()
