"""
商品目录（字段约定与界面一致）：
- code：商品 ID（抢购/接口 itemCode）
- spuId：编号（IMTP 等）
- referer：H5 商品详情页 URL
- 抢购分支、是否带 code 字段：由 referer 路径 + 内置映射自动推断（见 ui / webui_server）
- 默认数据：mt_item_list.json（与 test-full-flow.js ITEM_LIST 一致，8 条）
- 可选：product_list.json 全量（与 ITEM_LIST 的 spuId 去重后附加）
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
MT_ITEM_LIST_FILE = ROOT / "mt_item_list.json"

# 打包/迁移环境兜底：当 mt_item_list.json 缺失或损坏时，仍返回基础 8 条，避免 UI 商品库全空。
_BUILTIN_MT_ITEM_LIST: list[dict] = [
    {
        "code": "11942",
        "name": "53%vol 500ml贵州外卖酒（丙午马年）经典版",
        "spuId": "IMTP1000320",
        "itemPriorityActId": 72343,
        "amount": 6,
        "referer": "https://h5.moutai519.com.cn/mt/item/mm-485-detail-group?appConfig=2_1_2",
        "branch": "two",
        "needCodeField": True,
    },
    {
        "code": "741",
        "name": "飞天53%vol 500ml贵州外卖酒（带杯）",
        "spuId": "IMTP1000313",
        "itemPriorityActId": 91008,
        "amount": 2,
        "referer": "https://h5.moutai519.com.cn/mt/item/smsp-detail?appConfig=2_1_2",
        "branch": "one",
    },
    {
        "code": "11894",
        "name": "53%vol 500ml贵州外卖酒（鼓乐飞天）",
        "spuId": "IMTP1000321",
        "itemPriorityActId": 72342,
        "amount": 1,
        "referer": "https://h5.moutai519.com.cn/mt/item/gyft-8392-detail?appConfig=2_1_2",
        "branch": "three",
    },
    {
        "code": "11947",
        "name": "53%vol 500ml×2贵州外卖酒（丙午马年）礼盒装",
        "spuId": "IMTP1000319",
        "itemPriorityActId": 72341,
        "amount": 1,
        "referer": "https://h5.moutai519.com.cn/mt/item/mm-485-detail-group?appConfig=2_1_2",
        "branch": "two",
        "needCodeField": True,
    },
    {
        "code": "11945",
        "name": "53%vol 500ml贵州茅台酒（丙午马年）珍享版",
        "spuId": "IMTP1000318",
        "itemPriorityActId": 72343,
        "amount": 1,
        "referer": "https://h5.moutai519.com.cn/mt/item/mm-zx-271-detail?appConfig=2_1_2",
        "branch": "two",
        "needCodeField": True,
    },
    {
        "code": "483",
        "name": "飞天53%vol 1L贵州茅台酒",
        "spuId": "IMTP1000229",
        "itemPriorityActId": 76036,
        "amount": 6,
        "referer": "https://h5.moutai519.com.cn/mt/item/1000ml-detail?appConfig=2_1_2",
        "branch": "",
    },
    {
        "code": "10220",
        "name": "陈年贵州茅台酒(15年) 53%vol 500ml",
        "spuId": "IMTP1000262",
        "itemPriorityActId": 76090,
        "amount": 6,
        "referer": "https://h5.moutai519.com.cn/mt/item/grad-detail?appConfig=2_1_2&gt=mt_53_15th",
        "branch": "",
    },
    {
        "code": "1741",
        "name": "53%vol 500ml贵州茅台酒(精品)",
        "spuId": "IMTP1000264",
        "itemPriorityActId": 76161,
        "amount": 6,
        "referer": "https://h5.moutai519.com.cn/mt/item/jpmt-detail?appConfig=2_1_2",
        "branch": "three",
    },
    {
        "code": "5",
        "name": "飞天53%vol 200ml贵州茅台酒",
        "spuId": "IMTP1000265",
        "itemPriorityActId": 0,
        "amount": 2,
        "referer": "https://h5.moutai519.com.cn/mt/item/grad-detail?gt=ft_200&appConfig=2_1_2",
        "branch": "",
    },
]

DETAIL_KIND_BRANCH_MAP = {
    "smsp-detail": "one",
    "jpmt-detail": "three",
}

# 兼容：手动填接口长码时的 needCode（与 ITEM_LIST 短码并列）
MT_NEED_CODE_BY_ITEM_CODE = {
    "11942": True,
    "11942111": True,
    "11947": True,
    "11945": True,
    "741": False,
    "11894": False,
    "483": False,
    "10220": False,
    "1741": False,
}


def referer_from_jump_url_v2(jump: str) -> str:
    if not jump or "url=" not in jump:
        return ""
    try:
        if jump.startswith("moutaiapp://"):
            q = urlparse(jump).query
            qs = parse_qs(q)
            if qs.get("url"):
                return unquote(qs["url"][0])
    except Exception:
        pass
    return ""


def detail_kind_from_referer(ref: str) -> str:
    if not ref:
        return ""
    m = re.search(r"/mt/item/([^/?]+)", ref)
    return m.group(1) if m else ""


def _load_mt_item_list_raw() -> list[dict]:
    if not MT_ITEM_LIST_FILE.is_file():
        return [dict(x) for x in _BUILTIN_MT_ITEM_LIST]
    try:
        root = json.loads(MT_ITEM_LIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return [dict(x) for x in _BUILTIN_MT_ITEM_LIST]
    items = root.get("items")
    if not isinstance(items, list):
        return [dict(x) for x in _BUILTIN_MT_ITEM_LIST]
    return [x for x in items if isinstance(x, dict)]


def _item_list_order_codes() -> list[str]:
    return [str(x.get("code") or "").strip() for x in _load_mt_item_list_raw() if str(x.get("code") or "").strip()]


def _product_list_spu_map() -> dict[str, dict]:
    """spuId -> {itemCode, itemName, outPrice, jumpUrlV2, pictureUrl}"""
    p = ROOT / "product_list.json"
    if not p.is_file():
        return {}
    try:
        root = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    data = root.get("data") or {}
    out: dict[str, dict] = {}
    for cat in data.get("categoryItemList") or []:
        for it in cat.get("itemList") or []:
            li = (it.get("listItem") or {})
            spu = str(li.get("spuId") or "").strip()
            if not spu:
                continue
            out[spu] = {
                "itemCode": str(li.get("itemCode") or "").strip(),
                "itemName": str(li.get("itemName") or ""),
                "outPrice": str(li.get("outPrice") or ""),
                "jumpUrlV2": str(li.get("jumpUrlV2") or ""),
                "pictureUrl": str(li.get("pictureUrl") or "").strip(),
            }
    return out


def _row_from_item_list_entry(entry: dict, pl_by_spu: dict[str, dict]) -> dict:
    code = str(entry.get("code") or "").strip()
    spu = str(entry.get("spuId") or "").strip()
    name = str(entry.get("name") or "")
    ref = str(entry.get("referer") or "").strip()
    br = entry.get("branch")
    branch_hint = str(br).strip() if br is not None and str(br).strip() != "" else ""
    act_id = entry.get("itemPriorityActId")
    row: dict = {
        "name": name,
        "code": code,
        "price": "",
        "spuId": spu,
        "referer": ref,
        "source": "ITEM_LIST",
    }
    if act_id is not None:
        row["actId"] = str(act_id)
    if branch_hint:
        row["branchHint"] = branch_hint
    if "needCodeField" in entry:
        row["needCodeHint"] = bool(entry["needCodeField"])
    if entry.get("amount") is not None:
        try:
            row["defaultAmount"] = int(entry["amount"])
        except (TypeError, ValueError):
            pass
    dk = detail_kind_from_referer(ref)
    if dk:
        row["detailKind"] = dk
        if not row.get("branchHint"):
            br_m = DETAIL_KIND_BRANCH_MAP.get(dk)
            if br_m:
                row["branchHint"] = br_m
    pl = pl_by_spu.get(spu) if spu else None
    if pl:
        if pl.get("outPrice"):
            row["price"] = pl["outPrice"]
        if pl.get("pictureUrl"):
            row["pictureUrl"] = pl["pictureUrl"]
        if pl.get("jumpUrlV2"):
            row["jumpUrlV2"] = pl["jumpUrlV2"]
        api_code = (pl.get("itemCode") or "").strip()
        if api_code and api_code != code:
            row["catalogItemCode"] = api_code
    return row


def _flatten_product_list_rows() -> list[dict]:
    """纯 product_list.json 扁平行（与旧逻辑一致），用于 full 模式附加与路由兜底。"""
    p = ROOT / "product_list.json"
    if not p.is_file():
        return []
    try:
        root = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    data = root.get("data") or {}
    out: list[dict] = []
    for cat in data.get("categoryItemList") or []:
        for it in cat.get("itemList") or []:
            li = (it.get("listItem") or {})
            code = str(li.get("itemCode") or "").strip()
            spu = str(li.get("spuId") or "").strip()
            if not code or not spu:
                continue
            name = str(li.get("itemName") or "")
            price = str(li.get("outPrice") or "")
            jump = str(li.get("jumpUrlV2") or "")
            ref = referer_from_jump_url_v2(jump)
            pic = str(li.get("pictureUrl") or "").strip()
            dk = detail_kind_from_referer(ref)
            row: dict = {
                "name": name,
                "code": code,
                "price": price,
                "spuId": spu,
                "source": "product_list",
            }
            if jump:
                row["jumpUrlV2"] = jump
            if pic:
                row["pictureUrl"] = pic
            if ref:
                row["referer"] = ref
            if dk:
                row["detailKind"] = dk
                br = DETAIL_KIND_BRANCH_MAP.get(dk)
                if br:
                    row["branchHint"] = br
            nc = MT_NEED_CODE_BY_ITEM_CODE.get(code)
            if nc is not None:
                row["needCodeHint"] = nc
            out.append(row)
    return out


def load_catalog_items(*, include_full_product_list: bool = False) -> list[dict]:
    """
    include_full_product_list=False：仅 ITEM_LIST（8 条），与 test-full-flow.js 一致。
    True：先 8 条，再追加 product_list 中与 ITEM_LIST spuId 不重复的商品。
    """
    pl_by_spu = _product_list_spu_map()
    raw_items = _load_mt_item_list_raw()
    order = _item_list_order_codes()
    rush: list[dict] = []
    item_spus: set[str] = set()
    for entry in raw_items:
        row = _row_from_item_list_entry(entry, pl_by_spu)
        if row.get("code") and row.get("spuId"):
            rush.append(row)
            item_spus.add(str(row["spuId"]))

    def _rush_sort_key(it: dict):
        c = str(it.get("code") or "")
        if c in order:
            return (0, order.index(c))
        return (1, c)

    rush.sort(key=_rush_sort_key)

    if not include_full_product_list:
        return rush

    extra: list[dict] = []
    seen_code_spu: set[tuple[str, str]] = set()
    for it in rush:
        seen_code_spu.add((str(it.get("code") or ""), str(it.get("spuId") or "")))

    for row in _flatten_product_list_rows():
        spu = str(row.get("spuId") or "")
        if spu in item_spus:
            continue
        k = (str(row.get("code") or ""), spu)
        if k in seen_code_spu:
            continue
        seen_code_spu.add(k)
        extra.append(row)

    extra.sort(key=lambda x: (str(x.get("code") or "")))
    return rush + extra


def catalog_item_by_code(item_code: str) -> dict | None:
    """按 itemCode 查找：先 ITEM_LIST，再整表 product_list（兼容长码）。"""
    c = (item_code or "").strip()
    if not c:
        return None
    for it in load_catalog_items(include_full_product_list=False):
        if str(it.get("code") or "").strip() == c:
            return it
    for it in _flatten_product_list_rows():
        if str(it.get("code") or "").strip() == c:
            return it
    return None


def need_code_hint_for_item(code: str, li: dict | None = None) -> bool | None:
    if li:
        for k in ("needCodeField", "needCode", "need_code"):
            if k in li:
                return bool(li[k])
    c = (code or "").strip()
    if c in MT_NEED_CODE_BY_ITEM_CODE:
        return MT_NEED_CODE_BY_ITEM_CODE[c]
    return None
