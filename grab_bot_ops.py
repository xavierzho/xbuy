#!/usr/bin/env python3
"""
微信 Wechaty / 企业微信 / QQ 机器人 与 HTTP /grab/* 共用的抢单侧逻辑。

- 写盘：accounts.json（经 pool）、ui_state.json（rushCfg / addressBook / batchDefaultAmt）
- 指令文案与项目根 server.js 中 parseGrabMgmt 保持一致，便于双端适配。
"""

from __future__ import annotations

import json
import re
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from accounts import STATUS_OK, _norm_phone

import address_db

ROOT = Path(__file__).resolve().parent
UI_STATE_FILE = ROOT / "ui_state.json"

# 默认串行刷新收货地址间隔（毫秒）；略大以降低 ship/query 429（与 webui 默认一致）
DEFAULT_ADDRESS_REFRESH_GAP_MS = 3000

# 与 ui.html DEFAULT_ITEM_741 一致
GRAB_BOT_ITEM_741_PATCH = {
    "item": "741",
    "imtp": "IMTP1000313",
    "branch": "one",
    "ref": "https://h5.moutai519.com.cn/mt/item/smsp-detail?appConfig=2_1_2",
    "needcode": False,
    "act": "",
}

GRAB_BOT_HTTP_PATHS = frozenset({
    "/grab/request-code",
    "/grab/submit-code",
    "/grab/remove-account",
    "/grab/set-nickname",
    "/grab/batch-refresh-login",
    "/grab/refresh-all-addresses",
    "/grab/list-account-addresses",
    "/grab/apply-item-741",
    "/grab/set-all-amt",
    "/grab/set-all-delay",
})


def load_ui_state_raw() -> dict:
    if not UI_STATE_FILE.exists():
        return {}
    try:
        o = json.loads(UI_STATE_FILE.read_text(encoding="utf-8"))
        return o if isinstance(o, dict) else {}
    except Exception:
        return {}


def save_ui_state_raw(data: dict) -> None:
    try:
        from state_file_backup import backup_before_write

        backup_before_write(UI_STATE_FILE)
    except Exception:
        pass
    UI_STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def normalize_ui_state_address_book_keys(st: object) -> dict:
    """
    供 GET /api/ui/state 等返回给前端：addressBook 键统一为 _norm_phone；
    同号多键（历史空格等）时保留条数更多的列表，避免下拉对不上账号手机号。
    """
    if not isinstance(st, dict):
        return {}
    out: dict[str, Any] = dict(st)
    ab = out.get("addressBook")
    if not isinstance(ab, dict):
        return out
    merged: dict[str, list] = {}
    for k, v in ab.items():
        nk = _norm_phone(str(k))
        if not nk or not isinstance(v, list):
            continue
        prev = merged.get(nk)
        if not isinstance(prev, list) or len(v) > len(prev):
            merged[nk] = list(v)
    out["addressBook"] = merged
    return out


def extract_address_book_from_doc(raw: object) -> dict[str, list] | None:
    """
    从整份 ui_state.json 或仅含 addressBook 的片段中取出地址表。
    返回 { 规范化手机号: [ 地址对象, ... ], ... }；无法解析时返回 None。
    """
    if not isinstance(raw, dict):
        return None
    book = raw.get("addressBook")
    if not isinstance(book, dict):
        # 允许直接传「以手机号为键、值为数组」的根对象
        if raw and all(isinstance(k, str) for k in raw.keys()):
            sample = next(iter(raw.values()), None)
            if isinstance(sample, list):
                book = raw
            else:
                return None
        else:
            return None
    out: dict[str, list] = {}
    for ph, lst in book.items():
        phone = _norm_phone(str(ph))
        if not phone or not isinstance(lst, list):
            continue
        out[phone] = lst
    return out


def merge_address_book_into_ui_state(
    book_in: dict[str, list],
    *,
    replace_all: bool = False,
) -> dict[str, Any]:
    """
    将地址缓存写入 ui_state.json（与网页「推送 ui 状态」、机器人刷新一致）。
    - replace_all=False：按手机号合并，新数据覆盖同号已有列表。
    - replace_all=True：整表替换为 book_in（仅保留 book_in 中出现的号）。
    """
    st = load_ui_state_raw()
    if not isinstance(st, dict):
        st = {}
    if replace_all:
        st["addressBook"] = dict(book_in)
    else:
        book = st.get("addressBook")
        if not isinstance(book, dict):
            book = {}
        for phone, lst in book_in.items():
            if not phone:
                continue
            if isinstance(lst, list):
                book[phone] = lst
        st["addressBook"] = book
    st["addressBookUpdatedAt"] = datetime.now().isoformat(timespec="seconds")
    st.setdefault("v", 1)
    save_ui_state_raw(st)
    try:
        if address_db.enabled():
            ab2 = st.get("addressBook")
            if isinstance(ab2, dict):
                address_db.sync_address_book_dict(ab2)
    except Exception:
        pass
    ab = st.get("addressBook")
    n = len(ab) if isinstance(ab, dict) else 0
    return {"ok": True, "replaceAll": replace_all, "phoneCount": n}


def import_address_book_from_json_file(
    path: str | Path,
    *,
    replace_all: bool = False,
) -> dict[str, Any]:
    """从磁盘读取备份（整份 ui_state 或任意含 addressBook 的 JSON）并合并到当前 ui_state。"""
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"文件不存在: {p}"}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as ex:
        return {"ok": False, "error": f"JSON 解析失败: {ex}"}
    book = extract_address_book_from_doc(raw)
    if not book:
        return {"ok": False, "error": "文件中未找到 addressBook 或可识别的地址表"}
    return merge_address_book_into_ui_state(book, replace_all=replace_all)


def merge_rush_patches(patches: dict) -> dict:
    st = load_ui_state_raw()
    rc = st.get("rushCfg")
    if not isinstance(rc, dict):
        rc = {}
    for ph, patch in (patches or {}).items():
        phone = _norm_phone(ph)
        if not phone or not isinstance(patch, dict):
            continue
        base = rc.get(phone)
        if not isinstance(base, dict):
            base = {}
        merged = dict(base)
        merged.update(patch)
        rc[phone] = merged
    st["rushCfg"] = rc
    st.setdefault("v", 1)
    save_ui_state_raw(st)
    return st


def remove_phones_from_ui_state(phones: list) -> None:
    st = load_ui_state_raw()
    if not isinstance(st, dict):
        st = {}
    changed = False
    rc = st.get("rushCfg")
    if isinstance(rc, dict):
        for ph in phones or []:
            phone = _norm_phone(ph)
            if phone and phone in rc:
                del rc[phone]
                changed = True
        st["rushCfg"] = rc
    ab = st.get("addressBook")
    if isinstance(ab, dict):
        for ph in phones or []:
            phone = _norm_phone(ph)
            if phone and phone in ab:
                del ab[phone]
                changed = True
        st["addressBook"] = ab
    if changed:
        save_ui_state_raw(st)


def normalize_mgmt_text(s: str) -> str:
    if not s:
        return ""
    t = str(s).strip()
    t = re.sub(r"[\u2010-\u2015\u2212\uFE58\uFE63\uFF0D]", "-", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse_grab_login_text(t_raw: str) -> Optional[tuple[str, str, Optional[str]]]:
    """
    解析「手机号-验证码」及可选备注，供企业微信/QQ 等与页面一致。
    支持：验证码与备注间有空格/逗号；可无空格直接接汉字或英文（常见误发格式）；
    「备注」「備註」关键词；unicode 横线经 normalize_mgmt_text 已统一为 ASCII「-」。
    返回 (phone, code, nickname_or_none)；非登录句式返回 None。
    """
    t = normalize_mgmt_text(t_raw)
    if not t:
        return None

    def _clean_nick(phone: str, raw: str) -> Optional[str]:
        s = (raw or "").strip()
        if not s:
            return None
        nick = re.sub(r"^(?:昵称|叫|name\s*[:：]\s*)", "", s, flags=re.I).strip()
        if not nick or nick == phone or (len(nick) == 11 and nick.isdigit()):
            return None
        return nick

    m = re.match(r"^(\d{11})-(\d{4,8})\s*(?:备注|備註)\s*(.+)$", t)
    if m:
        return m.group(1), m.group(2), _clean_nick(m.group(1), m.group(3))

    m = re.match(r"^(\d{11})-(\d{4,8})\s*[,，]\s*(.+)$", t)
    if m:
        return m.group(1), m.group(2), _clean_nick(m.group(1), m.group(3))

    m = re.match(r"^(\d{11})-(\d{4,8})\s+(.+)$", t)
    if m:
        return m.group(1), m.group(2), _clean_nick(m.group(1), m.group(3))

    m = re.match(r"^(\d{11})-(\d{4,8})([^\d\s].*)$", t)
    if m:
        return m.group(1), m.group(2), _clean_nick(m.group(1), m.group(3))

    m = re.match(r"^(\d{11})-(\d{4,8})$", t)
    if m:
        return m.group(1), m.group(2), None
    return None


def try_parse_mgmt(t_raw: str) -> Optional[dict]:
    """与 server.js parseGrabMgmt 同序；命中返回 {kind, ...}，否则 None。"""
    t = normalize_mgmt_text(t_raw)
    if not t:
        return None

    m = re.match(r"^(?:删除|删掉)(?:账号)?\s*(\d{11})\s*$", t)
    if m:
        return {"kind": "remove", "phone": m.group(1)}

    m = re.match(r"^备注(\d{11})(.*)$", t)
    if m:
        return {
            "kind": "setNickname",
            "phone": m.group(1),
            "nickname": m.group(2).strip(),
        }

    m = re.match(r"^设置备注\s*(\d{11})(?:\s+(.*))?$", t)
    if m:
        return {
            "kind": "setNickname",
            "phone": m.group(1),
            "nickname": (m.group(2) or "").strip(),
        }

    m = re.match(
        r"^(?:列出所有账号地址|列出账号地址|查看所有收货地址|所有账号地址|账号地址列表)\s*$",
        t,
    )
    if m:
        return {"kind": "listAccountAddresses"}

    m = re.match(
        r"^(?:刷新所有账号地址|一键刷新所有账号地址|全部刷新地址|批量刷新地址)\s*$",
        t,
    )
    if m:
        return {"kind": "refreshAllAddresses"}

    m = re.match(r"^(?:全员刷新登录)\s*$", t)
    if m:
        return {"kind": "batchRefresh"}

    m = re.match(
        r"^全部设置商品\s*741(?:\s*(?:抢购)?数量\s*(\d{1,4}))?\s*$",
        t,
    )
    if m:
        return {
            "kind": "apply741",
            "amount": int(m.group(1)) if m.group(1) else None,
        }

    m = re.match(
        r"^(?:一键配置\s*741|一键741|配置\s*741|商品\s*741|配置741|商品741)"
        r"(?:\s+数量\s*(\d{1,4}))?\s*$",
        t,
    )
    if m:
        return {
            "kind": "apply741",
            "amount": int(m.group(1)) if m.group(1) else None,
        }

    m = re.match(r"^全部设置抢购数量\s*(\d{1,4})\s*$", t)
    if m:
        n = int(m.group(1))
        if n >= 1:
            return {"kind": "setAllAmt", "amount": n}

    m = re.match(r"^(?:抢购数量|全员数量)\s*(\d{1,4})\s*$", t)
    if m:
        n = int(m.group(1))
        if n >= 1:
            return {"kind": "setAllAmt", "amount": n}

    m = re.match(r"^全部设置间隔\s*(\d+)\s*(?:ms|毫秒)?\s*$", t, re.I)
    if m:
        n = int(m.group(1))
        if n >= 0:
            return {"kind": "setAllDelay", "delayMs": n}

    m = re.match(r"^(?:抢购间隔|全员间隔|间隔)\s*(\d{1,7})\s*(?:ms|毫秒)?\s*$", t, re.I)
    if m:
        n = int(m.group(1))
        if n >= 0:
            return {"kind": "setAllDelay", "delayMs": n}

    return None


def grab_remove_account(pool: Any, phone: str) -> dict:
    phone = _norm_phone(phone)
    ok = bool(pool.remove(phone)) if phone else False
    remove_phones_from_ui_state([phone] if phone else [])
    return {"removed": ok, "phone": phone, "stats": pool.stats()}


def grab_remove_accounts_batch(pool: Any, phones: list) -> dict:
    """与 Web 批量删号一致：内存池 + accounts.json + ui_state（rushCfg/addressBook）。"""
    norm = [_norm_phone(str(p).strip()) for p in (phones or [])]
    norm = [p for p in norm if p]
    if not norm:
        return {"removed": 0, "stats": pool.stats(), "phones": []}
    n = pool.remove_batch(norm)
    remove_phones_from_ui_state(norm)
    return {"removed": n, "stats": pool.stats(), "phones": norm}


def grab_set_nickname(pool: Any, phone: str, nickname: str) -> dict:
    phone = _norm_phone(phone)
    if not phone or not pool.get(phone):
        return {"ok": False, "message": "账号不在池中", "phone": phone or ""}
    pool.set_nickname(phone, nickname)
    e = pool.get(phone)
    return {
        "ok": True,
        "phone": phone,
        "nickname": e.nickname if e else phone,
        "stats": pool.stats(),
    }


def grab_list_account_addresses(pool: Any) -> dict:
    """汇总 accounts 中的手机号/备注与 ui_state.addressBook 中的收货地址缓存（只读）。"""
    st = load_ui_state_raw()
    if not isinstance(st, dict):
        st = {}
    book = st.get("addressBook")
    if not isinstance(book, dict):
        book = {}
    updated_at = str(st.get("addressBookUpdatedAt") or "").strip()

    accounts_out: list[dict] = []
    for e in pool.all():
        if not e:
            continue
        phone = _norm_phone(e.phone)
        if not phone:
            continue
        nick = str(getattr(e, "nickname", "") or "").strip() or phone
        remark_label = "未备注" if nick == phone else nick
        raw_list = book.get(phone)
        if not isinstance(raw_list, list):
            raw_list = []
        addresses: list[dict] = []
        for a in raw_list:
            if not isinstance(a, dict):
                continue
            name = str(a.get("name") or "").strip()
            mob = str(a.get("mobile") or "").strip()
            addr_line = str(a.get("fullAddress") or a.get("address") or "").strip()
            addresses.append({
                "name": name,
                "mobile": mob,
                "fullAddress": addr_line,
            })
        accounts_out.append({
            "phone": phone,
            "remark": remark_label,
            "status": str(getattr(e, "status", "") or ""),
            "addressCount": len(addresses),
            "addresses": addresses,
        })
    return {
        "addressBookUpdatedAt": updated_at,
        "accounts": accounts_out,
        "stats": pool.stats(),
    }


def format_list_addresses_for_chat(data: dict, max_chars: int = 2000) -> str:
    """拼成一条微信文本；过长则截断（与 server.js buildListAddressesReply 保持同结构）。"""
    lines: list[str] = []
    ts = str(data.get("addressBookUpdatedAt") or "").strip()
    if ts:
        lines.append(f"地址缓存更新时间：{ts}")
    else:
        lines.append(
            "（暂无地址缓存时间；若无列表可先网页刷新或发「刷新所有账号地址」）"
        )
    accs = data.get("accounts") or []
    lines.append(f"共 {len(accs)} 个账号（手机号｜备注｜收货地址来自本地缓存）")
    for r in accs:
        phone = str(r.get("phone") or "")
        rem = str(r.get("remark") or "未备注")
        st = str(r.get("status") or "").strip()
        cnt = int(r.get("addressCount") or 0)
        lines.append(f"【{phone}】备注：{rem}｜{st}｜地址 {cnt} 条")
        for i, ad in enumerate(r.get("addresses") or [], 1):
            name = str(ad.get("name") or "").strip()
            mob = str(ad.get("mobile") or "").strip()
            fa = str(ad.get("fullAddress") or "").strip()
            one = f"  {i}. {name} {mob} {fa}".strip()
            if len(one) > 220:
                one = one[:217] + "…"
            lines.append(one)
        if cnt == 0 and st == STATUS_OK:
            lines.append("  （已登录但本地无地址缓存，请刷新）")
    body = "\n".join(lines)
    if len(body) <= max_chars:
        return body
    cut = max(0, max_chars - 48)
    return body[:cut].rstrip() + f"\n…已截断（共 {len(accs)} 个账号，可网页查看）"


def grab_refresh_all_addresses(pool: Any, gap_ms: Optional[float] = None) -> dict:
    if gap_ms is None:
        gap_ms = float(DEFAULT_ADDRESS_REFRESH_GAP_MS)
    try:
        gap_ms = float(gap_ms)
    except (TypeError, ValueError):
        gap_ms = float(DEFAULT_ADDRESS_REFRESH_GAP_MS)
    gap_ms = max(200.0, min(gap_ms, 120_000.0))

    targets = [
        _norm_phone(e.phone)
        for e in pool.all()
        if e and e.status == STATUS_OK
    ]
    targets = [p for p in targets if p]
    results = []
    st = load_ui_state_raw()
    if not isinstance(st, dict):
        st = {}
    book = st.get("addressBook")
    if not isinstance(book, dict):
        book = {}

    for i, phone in enumerate(targets):
        e = pool.get(phone)
        if not e or e.status != STATUS_OK:
            results.append({"phone": phone, "ok": False, "message": "跳过"})
            continue
        prev = book.get(phone)
        prev_ok = isinstance(prev, list) and len(prev) > 0
        try:
            s = e.ensure_session()
            lst, addr_raw = s.addresses_detail()
            code = addr_raw.get("code") if isinstance(addr_raw, dict) else None
            ok = code == 2000
            addr_list = lst if isinstance(lst, list) else []
            if ok and addr_list:
                book[phone] = addr_list
                try:
                    if address_db.enabled():
                        address_db.save_address_list_for_phone(phone, addr_list)
                except Exception:
                    pass
            elif ok and not addr_list:
                if not prev_ok:
                    book[phone] = []
            else:
                if not prev_ok:
                    book[phone] = addr_list
            msg = ""
            if isinstance(addr_raw, dict):
                msg = str(addr_raw.get("message") or "")
            results.append({
                "phone": phone,
                "ok": ok,
                "count": len(book.get(phone) or []) if isinstance(book.get(phone), list) else 0,
                "code": code,
                "message": (msg[:200] if msg else ("ok" if ok else "失败")),
            })
        except Exception as ex:
            traceback.print_exc()
            if not prev_ok:
                book[phone] = []
            results.append({
                "phone": phone,
                "ok": False,
                "message": str(ex)[:200],
            })
        if i < len(targets) - 1:
            time.sleep(gap_ms / 1000.0)

    st["addressBook"] = book
    st["addressBookUpdatedAt"] = datetime.now().isoformat(timespec="seconds")
    st.setdefault("v", 1)
    save_ui_state_raw(st)
    okc = sum(1 for r in results if r.get("ok"))
    return {
        "results": results,
        "stats": pool.stats(),
        "addressesOk": okc,
        "total": len(targets),
    }


def grab_batch_refresh_login(
    pool: Any,
    phones: Optional[list] = None,
    delay: float = 4.0,
) -> dict:
    delay = max(2.0, float(delay))
    results = pool.batch_refresh_login(phones=phones, delay=delay)
    return {"results": results, "stats": pool.stats()}


def _resolve_apply_741_amount(pool: Any, amount: Optional[int]) -> int:
    if amount is not None:
        return max(1, min(int(amount), 9999))
    st0 = load_ui_state_raw()
    ba = st0.get("batchDefaultAmt")
    try:
        return max(1, min(int(ba) if ba not in (None, "") else 2, 9999))
    except (TypeError, ValueError):
        return 2


def grab_apply_item_741(pool: Any, amount: Optional[int] = None) -> dict:
    amount = _resolve_apply_741_amount(pool, amount)
    phones = [_norm_phone(e.phone) for e in pool.all()]
    phones = [p for p in phones if p]
    patch = dict(GRAB_BOT_ITEM_741_PATCH)
    patch["amt"] = amount
    patch["ccnt"] = amount
    mut = {p: dict(patch) for p in phones}
    merge_rush_patches(mut)
    return {"updated": len(phones), "amount": amount, "stats": pool.stats()}


def grab_set_all_amt(pool: Any, amount: int) -> dict:
    amount = max(1, min(int(amount), 9999))
    phones = [_norm_phone(e.phone) for e in pool.all()]
    phones = [p for p in phones if p]
    mut = {p: {"amt": amount, "ccnt": amount} for p in phones}
    merge_rush_patches(mut)
    st = load_ui_state_raw()
    st["batchDefaultAmt"] = str(amount)
    save_ui_state_raw(st)
    return {"updated": len(phones), "amount": amount, "stats": pool.stats()}


def grab_set_all_delay(pool: Any, delay_ms: int) -> dict:
    delay_ms = max(0, min(int(delay_ms), 3_600_000))
    phones = [_norm_phone(e.phone) for e in pool.all()]
    phones = [p for p in phones if p]
    mut = {p: {"delay": delay_ms} for p in phones}
    merge_rush_patches(mut)
    return {"updated": len(phones), "delayMs": delay_ms, "stats": pool.stats()}


def execute_mgmt_chat(pool: Any, text: str) -> Optional[str]:
    """
    企业微信/QQ 等文本通道：识别管理指令并执行，返回回复文案；非管理指令返回 None。
    """
    mg = try_parse_mgmt(text)
    if mg is None:
        return None
    kind = mg["kind"]
    try:
        if kind == "remove":
            data = grab_remove_account(pool, mg["phone"])
            if data["removed"]:
                return f"已删除账号 {data['phone']}（accounts.json 与 ui_state 已更新）"
            return f"账号 {data['phone']} 不在池中，已清理其抢购/收货地址缓存"

        if kind == "setNickname":
            data = grab_set_nickname(pool, mg["phone"], mg.get("nickname", ""))
            if not data.get("ok"):
                return str(data.get("message") or "失败")
            phone = data["phone"]
            nick = data.get("nickname") or phone
            if nick == phone:
                return f"{phone} 备注已清空（网页侧栏显示未备注）"
            return f"{phone} 备注已设为：{nick}"

        if kind == "listAccountAddresses":
            data = grab_list_account_addresses(pool)
            return format_list_addresses_for_chat(data)

        if kind == "refreshAllAddresses":
            data = grab_refresh_all_addresses(pool)
            okc = data.get("addressesOk", 0)
            total = data.get("total", 0)
            return (
                f"已刷新 {total} 个已登录账号的收货地址，成功 {okc}/{total}；"
                f"已写入 {UI_STATE_FILE.name}"
            )

        if kind == "batchRefresh":
            data = grab_batch_refresh_login(pool, phones=None, delay=4.0)
            results = data.get("results") or []
            okc = sum(1 for r in results if r and r.get("ok"))
            return f"全员刷新登录完成：{okc}/{len(results)} 个账号会话校验通过"

        if kind == "apply741":
            data = grab_apply_item_741(pool, mg.get("amount"))
            return (
                f"已为 {data['updated']} 个账号写入商品 741 参数（数量={data['amount']}），"
                f"已保存"
            )

        if kind == "setAllAmt":
            data = grab_set_all_amt(pool, mg["amount"])
            return f"已将 {data['updated']} 个账号抢购/组单数量设为 {data['amount']}（已保存）"

        if kind == "setAllDelay":
            data = grab_set_all_delay(pool, mg["delayMs"])
            return (
                f"已将 {data['updated']} 个账号抢购间隔设为 {data['delayMs']} 毫秒（已保存）"
            )
    except Exception as ex:
        traceback.print_exc()
        return f"执行失败：{ex}"
    return "未知指令"


def merge_ui_state_from_post(payload: dict) -> None:
    """POST /api/ui/state：与 webui_server 一致地合并前端快照。"""
    st = load_ui_state_raw()
    if not isinstance(st, dict):
        st = {}
    for key in (
        "rushCfg",
        "uiPrefs",
        "batchDefaultAmt",
        "batchRushRetry",
        "batchRushDelayMs",
        "batchConcurrency",
        "autoChain",
        "showDeviceRefresh",
        "accountGroups",
        "proxyPoolApi",
    ):
        if key in payload:
            st[key] = payload[key]
    if "payNotices" in payload and isinstance(payload["payNotices"], list):
        st["payNotices"] = payload["payNotices"][:120]
    if "addressBook" in payload and isinstance(payload["addressBook"], dict):
        book_norm = extract_address_book_from_doc({"addressBook": payload["addressBook"]})
        if book_norm:
            book = st.get("addressBook")
            if not isinstance(book, dict):
                book = {}
            for phone, lst in book_norm.items():
                if isinstance(lst, list):
                    book[phone] = lst
            st["addressBook"] = book
            st["addressBookUpdatedAt"] = datetime.now().isoformat(timespec="seconds")
            try:
                if address_db.enabled():
                    for p2, lst2 in book_norm.items():
                        if isinstance(lst2, list) and lst2:
                            address_db.save_address_list_for_phone(p2, lst2)
            except Exception:
                pass
    if "v" in payload:
        st["v"] = payload["v"]
    save_ui_state_raw(st)
