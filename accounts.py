#!/usr/bin/env python3
"""
多账号管理模块
- 每个账号绑定独立 Session（不同设备伪装）
- 支持持久化：token/cookie/设备信息存入 accounts.json
- 提供批量发码、批量登录接口
- 浩天（百度 SDK）：固定为发码前获取一次 BS-DVID 并复用，不再在登录后或业务中刷新
"""

import hashlib
import json
import os
import random
import re
import threading
import time as _time
import uuid
import base64
import binascii
from datetime import datetime
from pathlib import Path
from typing import Optional

from app import (
    H5_FIXED_CHROME_VERSION,
    Session,
    _normalize_h5_ua_model,
    _normalize_h5_ua_model_in_user_agent,
    _replace_chrome_version_in_ua,
)
from net_time import risk_log_timestamp
from shield import raw_id_from_mt_jwt

ACCOUNTS_FILE = Path(__file__).resolve().parent / "accounts.json"
_GLOBAL_FORCE_LOCAL_FILE = Path(__file__).resolve().parent / "mt_global_force_local.json"


def _norm_phone(phone: object) -> str:
    """与前端、接口入参一致：统一 strip，避免 json 里带空格导致删不掉。"""
    return str(phone or "").strip()


_MTM_TOKEN_RE = re.compile(r"MTM-[A-Za-z0-9+/=_-]+")


def _b64_json_part_decode(part: str) -> dict:
    pad = "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part + pad).decode("utf-8"))


def _raw_id_from_clips_device_token(device_token: object) -> str:
    val = str(device_token or "").strip()
    if not val.startswith("clips_"):
        return ""
    try:
        buf = base64.b64decode(val[6:]).decode("utf-8")
    except Exception:
        return ""
    out: list[str] = []
    for idx, ch in enumerate(buf):
        prev = 72 if idx == 0 else ord(buf[idx - 1])
        out.append(chr(prev ^ ord(ch)))
    rid = "".join(out).strip().lower()
    if all(c in "0123456789abcdef" for c in rid) and 16 <= len(rid) <= 32:
        return rid
    return ""


def _jwt_payload(token: object) -> dict:
    tok = str(token or "").strip()
    parts = tok.split(".")
    if len(parts) < 2:
        return {}
    try:
        data = _b64_json_part_decode(parts[1])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _decode_mtm_token(token: str) -> object:
    raw = str(token or "").strip().strip("\"'`")
    if raw.startswith("MTM-"):
        raw = raw[4:]
    raw = re.sub(r"\s+", "", raw)
    if not raw:
        raise ValueError("MTM 串为空")
    pad = "=" * (-len(raw) % 4)
    try:
        blob = base64.urlsafe_b64decode(raw[::-1] + pad)
    except (binascii.Error, ValueError) as ex:
        raise ValueError("MTM Base64 解码失败") from ex
    try:
        txt = blob.decode("utf-8")
    except UnicodeDecodeError as ex:
        raise ValueError("MTM 明文不是 UTF-8") from ex
    try:
        return json.loads(txt)
    except json.JSONDecodeError as ex:
        raise ValueError("MTM 明文不是 JSON") from ex


def _walk_mtm_objects(obj: object) -> list[object]:
    found: list[object] = []
    if isinstance(obj, str):
        text = obj.strip()
        if "MTM-" in text:
            for m in _MTM_TOKEN_RE.finditer(text):
                found.append(_decode_mtm_token(m.group(0)))
        return found
    if isinstance(obj, list):
        for item in obj:
            found.extend(_walk_mtm_objects(item))
        return found
    if isinstance(obj, dict):
        for val in obj.values():
            found.extend(_walk_mtm_objects(val))
        return found
    return found


def _extract_mtm_payloads(raw_text: object) -> list[object]:
    text = str(raw_text or "").strip()
    if not text:
        return []
    out: list[object] = []
    for m in _MTM_TOKEN_RE.finditer(text):
        try:
            out.append(_decode_mtm_token(m.group(0)))
        except ValueError:
            continue
    if out:
        return out
    try:
        obj = json.loads(text)
    except Exception:
        return []
    nested = _walk_mtm_objects(obj)
    return nested or [obj]


def _iter_mtm_account_candidates(obj: object) -> list[dict]:
    out: list[dict] = []
    if isinstance(obj, list):
        for item in obj:
            out.extend(_iter_mtm_account_candidates(item))
        return out
    if not isinstance(obj, dict):
        return out
    if isinstance(obj.get("data"), (dict, list)):
        out.extend(_iter_mtm_account_candidates(obj.get("data")))
    if isinstance(obj.get("accounts"), list):
        out.extend(_iter_mtm_account_candidates(obj.get("accounts")))
    if obj.get("token") and (obj.get("mobile") or obj.get("phone") or obj.get("userId") or obj.get("uid")):
        out.append(obj)
    return out


def _mtm_extract_app_profile(device_headers: dict) -> tuple[str, str, str]:
    ua = str(device_headers.get("simpleUserAgent") or "").strip()
    m = re.search(r"^android;([^;]+);([^;]+);(.+)$", ua, re.I)
    if m:
        return m.group(1).strip(), m.group(2).strip(), _normalize_h5_ua_model(m.group(3).strip())
    return "", "", ""


def _mtm_to_account_dict(src: dict) -> tuple[dict, list[dict]]:
    if not isinstance(src, dict):
        raise ValueError("账号记录不是对象")
    dh = src.get("deviceHeaders") if isinstance(src.get("deviceHeaders"), dict) else {}
    token = str(src.get("token") or "").strip()
    cookie = str(src.get("cookie") or token).strip()
    payload = _jwt_payload(token)
    jwt_device = str(payload.get("deviceId") or "").strip()
    raw_id = (
        raw_id_from_mt_jwt(token)
        or _raw_id_from_clips_device_token(jwt_device)
        or str(dh.get("deviceIdPlain") or "").strip().lower()
        or _raw_id_from_clips_device_token(dh.get("mtDeviceId"))
    )
    sdk, mfr, mdl = _mtm_extract_app_profile(dh)
    phone = _norm_phone(src.get("phone", src.get("mobile", "")))
    uid = str(src.get("uid", src.get("userId", payload.get("userId", ""))) or "").strip()
    addr0 = src.get("shippingAddress") if isinstance(src.get("shippingAddress"), dict) else {}
    ship_name = str(addr0.get("name") or "").strip()
    nick = str(ship_name or src.get("nickname") or src.get("userName") or phone).strip()
    verify_status = src.get("verify_status", src.get("verifyStatus", None))
    h5_ua = str(dh.get("userAgent") or src.get("h5_user_agent") or "").strip()
    entry = {
        "phone": phone,
        "nickname": nick or phone,
        "status": STATUS_OK if token else str(src.get("status") or STATUS_PENDING),
        "uid": uid,
        "verify_status": verify_status,
        "token": token,
        "cookie": cookie,
        "aid": raw_id,
        "sdk": sdk,
        "mfr": mfr,
        "mdl": mdl,
        "scr": str(dh.get("mtRs") or src.get("scr") or "").strip(),
        "btime": str(dh.get("mtDtime") or src.get("btime") or "").strip(),
        "bsid": str(dh.get("bsDvid") or src.get("bsid") or "").strip(),
        "wv": str(src.get("wv") or "").strip(),
        "h5_user_agent": h5_ua,
        "h5_device_id": raw_id,
        "haotian_device_id": str(dh.get("deviceInfo") or src.get("haotian_device_id") or "").strip(),
        "h5_android_version": sdk,
        "h5_device_model": mdl,
        "h5_build_id": "",
        "h5_chrome_version": "",
        "gdxidpyhxdE": str(src.get("gdxidpyhxdE") or "").strip(),
        "yidun_gdx_fp": str(src.get("yidun_gdx_fp") or "").strip(),
        "gdx_icp": str(src.get("gdx_icp") or "").strip(),
        "remark": str(src.get("remark") or "MTM导入").strip(),
    }
    if src.get("isBlacklisted") is True:
        entry["risk_tag"] = "blacklisted"
        entry["risk_message"] = "源数据 isBlacklisted=true"
    if not entry["phone"]:
        raise ValueError("缺少手机号")
    if not entry["token"]:
        raise ValueError("缺少 token")
    addresses: list[dict] = []
    addr = addr0
    if isinstance(addr, dict) and addr.get("shipAddressId"):
        addresses.append(dict(addr))
    return entry, addresses


_BS_DVID_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _valid_bs_dvid_token(v: object) -> bool:
    s = str(v or "").strip()
    return bool(s and len(s) >= 40 and not _BS_DVID_IP_RE.match(s) and "/" not in s and ":" not in s)


def _strip_invalid_bs_dvid_from_ua(ua: object, bsid: object = "") -> str:
    s = str(ua or "").strip()
    bs = str(bsid or "").strip()
    if not s:
        return ""
    if _valid_bs_dvid_token(bs):
        return re.sub(r"BS-DVID/[^\s;]+", f"BS-DVID/{bs}", s)
    return re.sub(r"\s*BS-DVID/[^\s;]+", "", s).strip()


def _ordered_unique_phones(seq: object) -> list[str]:
    """保留传入顺序去重，用于勾选/分组范围内的代理分片。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(seq or []):
        ph = _norm_phone(raw)
        if not ph or ph in seen:
            continue
        seen.add(ph)
        out.append(ph)
    return out


def _derive_h5_did_from_haotian(entry) -> str:
    """
    用浩天 H5 画像稳定派生 BB/H5 did。
    不把 did 粗暴等同于 UA 里的 device-id，而是固定映射到同一来源画像，
    避免当前 make_h5_devid() 的随机成分导致同账号跨重建漂移。
    """
    h5_device_id = str(getattr(entry, "h5_device_id", "") or "").strip().lower()
    if not h5_device_id:
        ua = str(getattr(entry, "h5_user_agent", "") or "").strip()
        m = re.search(r"device-id/([^\s;]+)", ua)
        if m:
            h5_device_id = m.group(1).strip().lower()
    if not h5_device_id:
        return ""
    model = str(getattr(entry, "h5_device_model", "") or "").strip()
    android = str(getattr(entry, "h5_android_version", "") or "").strip()
    build_id = str(getattr(entry, "h5_build_id", "") or "").strip()
    chrome = str(getattr(entry, "h5_chrome_version", "") or "").strip()
    ua = str(getattr(entry, "h5_user_agent", "") or "").strip().lower()
    seed = "|".join(
        [
            h5_device_id,
            model,
            android,
            build_id,
            chrome,
            ua,
        ]
    )
    return "h" + hashlib.md5(seed.encode("utf-8")).hexdigest()


# 账号状态常量
STATUS_PENDING   = "待登录"
STATUS_CODE_SENT = "已发码"
STATUS_OK        = "已登录"
STATUS_FAIL      = "登录失败"

# set_nickname(..., remark=...) 未传 remark 时不改 e.remark
_REMARK_ARG_OMIT = object()

_BB_STATE_KEYS = (
    "bbDid",
    "bbStartId",
    "bbDeviceCookie",
    "bbUaid",
    "bbJsid",
    "bbToken",
    "bbRk",
    "bbSignWasmBase64",
    "bbSignWasmVersion",
    "bbSignWasmOk",
    "bbSdkVersion",
    "bbPageDomain",
    "bbSerial",
    "bbTps",
    "bbTimeDelta",
    "bbTacticsVersion",
    "bbRemoteTask",
    "bbDeviceInfo",
    "bbLocalStorage",
    "bbSessionStorage",
    "bbCookieState",
    "__bbWarmupAt",
    "__bbWarmupAlg",
    "__bbStrategyAt",
    "__bbWasmFetched",
    "__bbWasmFetchedAt",
    "__bbWasmUnavailableReason",
)

_BB_WARMUP_PORTABLE_KEYS = (
    "bbSignWasmBase64",
    "bbSignWasmVersion",
    "bbSignWasmOk",
    "bbSdkVersion",
    "__bbWasmFetched",
    "__bbWasmFetchedAt",
    "__bbWasmUnavailableReason",
)

# 须可重入：ensure_session / add 等在已持锁路径上会调用 POOL.save()，save() 再次取同一把锁。
_lock = threading.RLock()
# 抢购热路径标志：webui_server 在批量抢购入口 set，结束 clear。
# 期间 ensure_session 路径里的写盘改为标记 dirty 跳过，由 exit_rush_hot_path 统一 flush。
# 仅影响 ensure_session 里第 643 行的 proxy 漂移写盘 和 第 822 行的 POOL.save()；
# 其他路径（登录、发码、加号等）不受影响。
_rush_hot_path_event = threading.Event()
_hot_dirty_lock = threading.Lock()
_hot_dirty_pending = False


def enter_rush_hot_path() -> None:
    _rush_hot_path_event.set()


def exit_rush_hot_path(*, flush: bool = True) -> None:
    _rush_hot_path_event.clear()
    if flush:
        flush_hot_dirty_accounts()


def _hot_path_active() -> bool:
    return _rush_hot_path_event.is_set()


def _mark_hot_dirty() -> None:
    global _hot_dirty_pending
    with _hot_dirty_lock:
        _hot_dirty_pending = True


def flush_hot_dirty_accounts() -> bool:
    """把热路径累计的 dirty 一次性写盘。返回是否实际写了。"""
    global _hot_dirty_pending
    with _hot_dirty_lock:
        if not _hot_dirty_pending:
            return False
        _hot_dirty_pending = False
    try:
        with _lock:
            _save_raw([POOL._map[p].to_dict() for p in POOL._phones])
        return True
    except Exception:
        # flush 失败：还原 dirty 标志，下次重试
        with _hot_dirty_lock:
            _hot_dirty_pending = True
        raise


# 浩天 BS-DVID（Node）较重，单进程串行，避免并发打满 CPU/触发风控
_haotian_lock = threading.Lock()
# 同一号码两次发码的最小间隔（秒），防止恢复期连点加重限流；0=关闭。例：MT_VCODE_MIN_INTERVAL=120
_vcode_gap_lock = threading.Lock()
_vcode_last_attempt_mono: dict[str, float] = {}


def _min_vcode_interval_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("MT_VCODE_MIN_INTERVAL", "0") or 0))
    except (TypeError, ValueError):
        return 0.0


def _read_global_force_local_disk() -> bool:
    try:
        if _GLOBAL_FORCE_LOCAL_FILE.is_file():
            d = json.loads(_GLOBAL_FORCE_LOCAL_FILE.read_text(encoding="utf-8"))
            return bool(d.get("enabled"))
    except Exception:
        pass
    return False


_global_force_all_local_ip: bool = _read_global_force_local_disk()
_global_force_local_lock = threading.Lock()


def global_force_local_ip() -> bool:
    """全局「本机 IP」：为 True 时所有账号 ensure_session 强制直连，忽略代理池分片。"""
    with _global_force_local_lock:
        return bool(_global_force_all_local_ip)


def set_global_force_local_ip(enabled: bool) -> dict:
    """切换全局本机 IP 并落盘。

    - 开启/关闭均会清除所有账号的「单号强制本机」proxy 标记（本机/direct/直连等），
      出口仅以全局开关为准：开=全直连，关=全走代理池/分片，避免关全局后仍有漏网 direct。
    - 关闭：成功时从磁盘重载后再清标记；全局开启期间 ensure_session 不会把 proxy 写成 direct。
    """
    global _global_force_all_local_ip
    with _global_force_local_lock:
        _global_force_all_local_ip = bool(enabled)
        try:
            _GLOBAL_FORCE_LOCAL_FILE.write_text(
                json.dumps({"enabled": _global_force_all_local_ip}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
        except OSError as ex:
            return {"ok": False, "error": str(ex), "enabled": _global_force_all_local_ip}
    nclr = 0
    try:
        if not _global_force_all_local_ip:
            out = POOL.reload_from_disk()
            if not out.get("ok"):
                print(
                    f"[全局] 本机IP（全局）已关闭，accounts.json 重载失败: {out.get('error')}；"
                    f"仍尝试清除单号直连标记并保存。",
                    flush=True,
                )
            nclr = POOL.clear_all_proxy_direct_markers()
            POOL.invalidate_all_sessions()
        else:
            nclr = POOL.clear_all_proxy_direct_markers()
            POOL.invalidate_all_sessions()
    except Exception:
        try:
            POOL.invalidate_all_sessions()
        except Exception:
            pass
    if nclr:
        print(
            f"[全局] 已清空 {nclr} 个账号的 proxy 直连标记（与本机IP全局联动，无漏网）",
            flush=True,
        )
    print(
        f"[全局] 本机IP（全局）={'开启' if _global_force_all_local_ip else '关闭'} → "
        f"{'已从磁盘同步并统一代理策略' if not _global_force_all_local_ip else '全部账号按协议走本机出口'}，下次请求按新策略生效",
        flush=True,
    )
    return {
        "ok": True,
        "enabled": _global_force_all_local_ip,
        "clearedProxyDirectMarkers": int(nclr),
    }


def _is_direct_marker(cur: str) -> bool:
    p = (cur or "").strip()
    if p in ("本机", "直连", "-"):
        return True
    return p.lower() in ("direct", "local", "none", "0")


def _resolve_account_proxy_for_session(current: str) -> str:
    """
    账号层代理策略：
    - 全局「本机IP」开启：所有账号强制 direct（优先级最高）。
    - 填「本机/direct」等标记：强制 Session 走本机直连（不与代理池分片混用）。
    - 否则不绑定单账号固定 URL（返回空），由 Session 按代理池/分片轮换（组单/下单/支付与抢购一致）。
    """
    if global_force_local_ip():
        return "direct"
    cur = (current or "").strip()
    if _is_direct_marker(cur):
        return cur
    return ""


def _maybe_lock_account_proxy_for_login(entry: "AccountEntry", *, persist: bool = True) -> None:
    """
    发码/登录/浩天：代理池可用且账号未指定出口时，按手机号稳定映射一条池内 IP 并写入账号。
    与远程上号 send_code 行为一致；批量发码、主控发码均走 POOL.send_code 时也会生效。
    """
    if global_force_local_ip():
        return
    cur = (entry.proxy or "").strip()
    if cur and not _is_direct_marker(cur):
        return
    try:
        import proxy_pool as _pp

        if not _pp.pool_is_configured():
            return
        locked = _pp.pick_proxy_url_for_key(entry.phone)
        if not locked:
            return
        entry.proxy = locked
        if not persist:
            return
        if _hot_path_active():
            _mark_hot_dirty()
        else:
            with _lock:
                _save_raw([POOL._map[p].to_dict() for p in POOL._phones])
    except Exception:
        pass


# ──────────────────────────────────────────────
#  持久化
# ──────────────────────────────────────────────

def _load_raw() -> list:
    if not ACCOUNTS_FILE.exists():
        return []
    try:
        return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_raw(data: list):
    if (os.environ.get("MT_DIST_MEMORY_ONLY") or "").strip().lower() in ("1", "true", "yes", "on"):
        return
    try:
        from state_file_backup import backup_before_write

        backup_before_write(ACCOUNTS_FILE)
    except Exception:
        pass
    try:
        ACCOUNTS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as ex:
        print(f"[accounts] 写入 {ACCOUNTS_FILE} 失败: {ex}", file=__import__("sys").stderr)
        raise


# ──────────────────────────────────────────────
#  内存中的账号池  {phone: AccountEntry}
# ──────────────────────────────────────────────

class AccountEntry:
    """单个账号的全部状态"""

    def __init__(self, phone: str, nickname: str = ""):
        self.phone    = _norm_phone(phone)
        nick = str(nickname or "").strip()
        self.nickname = nick or self.phone
        self.status   = STATUS_PENDING
        self.uid      = ""
        self.verify_status = None
        self.token    = ""
        self.cookie   = ""
        # 设备信息（用于重建 Session）
        self.aid      = ""
        self.sdk      = ""
        self.mfr      = ""
        self.mdl      = ""
        self.scr      = ""
        self.btime    = ""
        self.bsid     = ""
        self.wv       = ""
        self.h5_user_agent = ""
        self.h5_device_id = ""
        self.haotian_device_id = ""
        self.h5_android_version = ""
        self.h5_device_model = ""
        self.h5_build_id = ""
        self.h5_chrome_version = ""
        self.gdxidpyhxdE = ""
        self.yidun_gdx_fp = ""
        self.gdx_icp = ""
        # HTTP 代理（可选，如 http://127.0.0.1:7890，分散出口 IP）
        self.proxy    = ""
        # True：本轮 Node rush_loop 按 local_proxy_pool 顺序每档一线（每 IP 只打一枪，不循环补位）；档数=min(重试,池条数)
        self.full_pool_rush = False
        # True：每次 _req_post/_req_get 调用结束后拨到下一出口（与 MT_PROXY_ROTATE_PER_REQUEST 合并生效）
        self.proxy_rotate_per_request = False
        # 代理分配模式：auto=自动均分；count=按手动条数分配；all=该号使用全池
        self.proxy_shard_mode: str = "auto"
        # mode=count 时生效：按手机号位置切出 count 条（不足则有几条用几条）
        self.proxy_shard_count: int = 0
        # 分布式主控下发的运行时独占代理段；不落盘长期使用，仅随任务进内存
        self.proxy_shard_urls: list = []
        # 分组分配时传入的分组内账号列表，用于分片计算；空则用全池账号
        self.proxy_shard_group: list = []
        # 历史字段：曾用于抢购后锁单出口；现始终按池轮换，仅兼容旧 accounts.json
        self.proxy_locked_after_rush = False
        # 抢到 priorityRecordId 当刻的出口：组单 compose 固定走此代理（与当次公网 IP 一致）
        self.rush_success_proxy_url = ""
        self.rush_success_egress_ip = ""
        # 抢购成功后固定 H5 设备指纹（与当次成功抢购一致，重建 Session 时不再换 _h5id/_h5sid）
        self.h5_did              = ""
        self.h5_sid              = ""
        self.rush_device_pinned  = False
        # 全部刷新登录轮换设备后：优先用磁盘 aid 建 Session，勿用 JWT 内旧 deviceId 盖掉新指纹（重新登录后自动关）
        self.prefer_persisted_device = False
        # 管理员附加说明（可与 nickname 并列；列表短标题仍主要用 nickname）
        self.remark: str = ""
        # 黑号检测（batch_black_check / mt test-full-flow 规则）
        self.risk_tag: str = ""  # normal | blacklisted | possibly_blacklisted | token_expired | probe_error
        self.risk_message: str = ""
        self.risk_checked_at: str = ""
        # BB SDK 远程协商状态（由 node_bridge /bb/bootstrap 拿到，登录时填充，按 expiredAt 刷新）
        # 仅抢购 POST (rushPurchase) 使用；非抢购 H5 请求不带 bb 头
        self.bb_sign_wasm_b64: str = ""        # sign_wasm bytes 的 base64
        self.bb_sign_wasm_version: str = ""    # 服务端协商版本号（如 "a2abd765..."）
        self.bb_sign_wasm_ok: bool = False     # 服务端 OK 标志
        self.bb_state_expired_at: int = 0      # 毫秒时间戳，<= now 时刷新
        # 兼容旧配置字段；运行时抢购 BB 已固定走 shield.py 的 py 稳定版链路。
        self.use_legacy_bb: bool = False
        self.use_wasm_bb: bool = False
        self.use_mobile_relay: bool = False
        self.mobile_relay_device: str = ""
        # 梆梆暖场上报状态（POST /bbprbdata/upload）：服务端建档/刷新设备画像
        # 60 秒缓存窗口，与对标项目 maotai-seckill.js warmupBangcleRiskFlow_1_9_6 对齐
        self.bb_warmup_at: int = 0          # 上次 upload 成功毫秒时间戳
        self.bb_serial: int = 1             # serial 计数（每次 upload 成功 +1）
        self.bb_device_info: dict = {}      # 派生的 deviceInfo（首次生成后固化，不再变）
        self.bb_tactics_version: int = 0    # 服务端策略版本（v1 默认 0）
        self.bb_time_delta: int = 0         # 服务端时间差（v1 默认 0）
        self.bb_state: dict = {}
        # 运行时 Session（不序列化）
        self._session: Session | None = None
        # 创建/复用 _session 的判定键：("", 全账号手机号元组) 或单账号代理串 用于分片变化时重建
        self._session_proxy_src: object | None = None
        # 单账号 Session 构造串行锁：允许 ensure_session 把 30ms CPU 工作挪到 _lock 外并行执行，
        # 但同账号同一时刻只允许一个线程在构造，避免锁外重复造成数据撕裂。
        self._build_lock = threading.Lock()

    def clear_h5_bb_context(self, *, clear_profile: bool = False) -> None:
        self.h5_did = ""
        self.h5_sid = ""
        self.rush_device_pinned = False
        self.gdxidpyhxdE = ""
        self.yidun_gdx_fp = ""
        self.gdx_icp = ""
        self.bb_sign_wasm_b64 = ""
        self.bb_sign_wasm_version = ""
        self.bb_sign_wasm_ok = False
        self.bb_state_expired_at = 0
        self.bb_warmup_at = 0
        self.bb_serial = 1
        self.bb_device_info = {}
        self.bb_tactics_version = 0
        self.bb_time_delta = 0
        self.bb_state = {}
        if clear_profile:
            self.h5_user_agent = ""
            self.h5_device_id = ""
            self.haotian_device_id = ""
            self.h5_android_version = ""
            self.h5_device_model = ""
            self.h5_build_id = ""
            self.h5_chrome_version = ""

    # ── 序列化 ──

    def to_dict(self) -> dict:
        d = {
            "phone":    _norm_phone(self.phone),
            "nickname": self.nickname,
            "status":   self.status,
            "uid":      self.uid,
            "verify_status": self.verify_status,
            "token":    self.token,
            "cookie":   self.cookie,
            "aid":      self.aid,
            "sdk":      self.sdk,
            "mfr":      self.mfr,
            "mdl":      self.mdl,
            "scr":      self.scr,
            "btime":    self.btime,
            "bsid":     self.bsid,
            "wv":       self.wv,
            "h5_user_agent": self.h5_user_agent,
            "h5_device_id": self.h5_device_id,
            "haotian_device_id": self.haotian_device_id,
            "h5_android_version": self.h5_android_version,
            "h5_device_model": self.h5_device_model,
            "h5_build_id": self.h5_build_id,
            "h5_chrome_version": self.h5_chrome_version,
            "gdxidpyhxdE": self.gdxidpyhxdE,
            "yidun_gdx_fp": self.yidun_gdx_fp,
            "gdx_icp": self.gdx_icp,
            "proxy":    self.proxy,
            "proxy_shard_mode": self.proxy_shard_mode,
            "proxy_shard_count": int(self.proxy_shard_count or 0),
            "proxy_shard_urls": list(getattr(self, "proxy_shard_urls", None) or []),
            "proxy_shard_group": list(getattr(self, "proxy_shard_group", None) or []),
            "proxy_locked_after_rush": self.proxy_locked_after_rush,
            "rush_success_proxy_url": self.rush_success_proxy_url,
            "rush_success_egress_ip": self.rush_success_egress_ip,
            "h5_did":   self.h5_did,
            "h5_sid":   self.h5_sid,
            "rush_device_pinned": self.rush_device_pinned,
            "prefer_persisted_device": self.prefer_persisted_device,
            "remark": self.remark,
            "risk_tag": self.risk_tag,
            "risk_message": self.risk_message,
            "risk_checked_at": self.risk_checked_at,
            "bb_sign_wasm_b64": self.bb_sign_wasm_b64,
            "bb_sign_wasm_version": self.bb_sign_wasm_version,
            "bb_sign_wasm_ok": bool(self.bb_sign_wasm_ok),
            "bb_state_expired_at": int(self.bb_state_expired_at or 0),
            "use_legacy_bb": bool(self.use_legacy_bb),
            "use_wasm_bb": bool(self.use_wasm_bb),
            "use_mobile_relay": bool(self.use_mobile_relay),
            "mobile_relay_device": str(self.mobile_relay_device or ""),
            "bb_warmup_at": int(self.bb_warmup_at or 0),
            "bb_serial": int(self.bb_serial or 1),
            "bb_device_info": dict(self.bb_device_info or {}),
            "bb_tactics_version": int(self.bb_tactics_version or 0),
            "bb_time_delta": int(self.bb_time_delta or 0),
        }
        if isinstance(getattr(self, "bb_state", None), dict):
            for key in _BB_STATE_KEYS:
                if key in self.bb_state:
                    d[key] = self.bb_state[key]
        if self.full_pool_rush:
            d["full_pool_rush"] = True
        if self.proxy_rotate_per_request:
            d["proxy_rotate_per_request"] = True
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AccountEntry":
        e = cls(
            _norm_phone(d.get("phone", "")),
            str(d.get("nickname", "") or "").strip(),
        )
        e.status  = d.get("status",  STATUS_PENDING)
        e.uid     = d.get("uid",     "")
        _vs = d.get("verify_status", d.get("verifyStatus", None))
        try:
            e.verify_status = int(_vs) if _vs is not None and str(_vs) != "" else None
        except Exception:
            e.verify_status = None
        e.token   = d.get("token",   "")
        e.cookie  = d.get("cookie",  "")
        e.aid     = d.get("aid",     "")
        e.sdk     = d.get("sdk",     "")
        e.mfr     = d.get("mfr",     "")
        e.mdl     = d.get("mdl",     "")
        e.scr     = d.get("scr",     "")
        e.btime   = d.get("btime",   "")
        raw_bsid = str(d.get("bsid", "") or "").strip()
        e.bsid    = raw_bsid if _valid_bs_dvid_token(raw_bsid) else ""
        e.wv      = d.get("wv",      "")
        e.h5_user_agent = _strip_invalid_bs_dvid_from_ua(
            d.get("h5_user_agent", d.get("webviewUa", d.get("wv_ua", ""))),
            e.bsid,
        )
        e.h5_device_id = d.get("h5_device_id", d.get("deviceId", ""))
        e.haotian_device_id = d.get("haotian_device_id", "")
        e.h5_android_version = d.get("h5_android_version", d.get("androidVersion", d.get("android_version", "")))
        e.h5_device_model = d.get("h5_device_model", "")
        e.h5_build_id = d.get("h5_build_id", d.get("buildId", d.get("build", "")))
        e.h5_chrome_version = d.get("h5_chrome_version", d.get("chromeVersion", d.get("chrome", "")))
        bb_state = d.get("bbCookieState") if isinstance(d.get("bbCookieState"), dict) else {}
        e.gdxidpyhxdE = str(d.get("gdxidpyhxdE", bb_state.get("gdxidpyhxdE", "")) or "").strip()
        e.yidun_gdx_fp = str(
            d.get("yidun_gdx_fp", d.get("yidunGdxFp", d.get("gdxFp", bb_state.get("gdxFp", "")))) or ""
        ).strip()
        e.gdx_icp = str(d.get("gdx_icp", d.get("gdxIcp", bb_state.get("gdxIcp", ""))) or "").strip()
        e.bb_state = {key: d.get(key) for key in _BB_STATE_KEYS if key in d}
        e.proxy   = d.get("proxy",   "")
        e.proxy_shard_mode = str(d.get("proxy_shard_mode", "auto") or "auto").strip().lower()
        if e.proxy_shard_mode not in ("auto", "count", "all", "runtime"):
            e.proxy_shard_mode = "auto"
        try:
            e.proxy_shard_count = max(0, int(d.get("proxy_shard_count", 0) or 0))
        except Exception:
            e.proxy_shard_count = 0
        raw_shard = d.get("proxy_shard_urls")
        if isinstance(raw_shard, list):
            e.proxy_shard_urls = [str(x).strip() for x in raw_shard if str(x or "").strip()]
        else:
            e.proxy_shard_urls = []
        e.full_pool_rush = bool(d.get("full_pool_rush", False))
        e.proxy_rotate_per_request = bool(d.get("proxy_rotate_per_request", False))
        e.proxy_locked_after_rush = bool(d.get("proxy_locked_after_rush", False))
        e.rush_success_proxy_url = str(d.get("rush_success_proxy_url", "") or "").strip()
        e.rush_success_egress_ip = str(d.get("rush_success_egress_ip", "") or "").strip()
        e.h5_did = str(d.get("h5_did", "") or "").strip()
        e.h5_sid = str(d.get("h5_sid", "") or "").strip()
        e.rush_device_pinned = bool(d.get("rush_device_pinned", False))
        e.prefer_persisted_device = bool(d.get("prefer_persisted_device", False))
        e.remark = str(d.get("remark", "") or "")
        e.risk_tag = str(d.get("risk_tag", "") or "")
        e.risk_message = str(d.get("risk_message", "") or "")
        e.risk_checked_at = str(d.get("risk_checked_at", "") or "")
        e.bb_sign_wasm_b64 = str(d.get("bb_sign_wasm_b64", "") or "")
        e.bb_sign_wasm_version = str(d.get("bb_sign_wasm_version", "") or "")
        e.bb_sign_wasm_ok = bool(d.get("bb_sign_wasm_ok", False))
        try:
            e.bb_state_expired_at = int(d.get("bb_state_expired_at", 0) or 0)
        except (TypeError, ValueError):
            e.bb_state_expired_at = 0
        e.use_legacy_bb = False
        e.use_wasm_bb = bool(d.get("use_wasm_bb", d.get("useWasmBb", False)))
        e.use_mobile_relay = bool(d.get("use_mobile_relay", d.get("useMobileRelay", False)))
        e.mobile_relay_device = str(
            d.get("mobile_relay_device", d.get("mobileRelayDevice", "")) or ""
        ).strip()
        try:
            e.bb_warmup_at = int(d.get("bb_warmup_at", 0) or 0)
        except (TypeError, ValueError):
            e.bb_warmup_at = 0
        try:
            e.bb_serial = int(d.get("bb_serial", 1) or 1)
        except (TypeError, ValueError):
            e.bb_serial = 1
        bdi = d.get("bb_device_info", {})
        e.bb_device_info = bdi if isinstance(bdi, dict) else {}
        try:
            e.bb_tactics_version = int(d.get("bb_tactics_version", 0) or 0)
        except (TypeError, ValueError):
            e.bb_tactics_version = 0
        try:
            e.bb_time_delta = int(d.get("bb_time_delta", 0) or 0)
        except (TypeError, ValueError):
            e.bb_time_delta = 0
        return e

    def _persisted_raw_id(self) -> str | None:
        """accounts.json 里 aid 存的是 Session._raw_id；Java 风格 MD5 可能为 16~32 位 hex，不能要求必须 32 位。"""
        a = (self.aid or "").strip()
        if not a:
            return None
        if all(c in "0123456789abcdefABCDEF" for c in a) and 16 <= len(a) <= 32:
            return a.lower()
        return None

    # ── BB SDK 状态管理 ──────────────────────────────

    def bb_state_is_fresh(self, safety_margin_ms: int = 300_000) -> bool:
        """bb_state 是否新鲜（未过期且有内容）。

        默认提前 5 分钟视为过期，避免抢购瞬间触发刷新增加延迟。
        """
        if not self.bb_sign_wasm_b64 or not self.bb_sign_wasm_version:
            return False
        import time as _t
        now_ms = int(_t.time() * 1000)
        return int(self.bb_state_expired_at or 0) > now_ms + int(safety_margin_ms or 0)

    def apply_bb_bootstrap(self, state: dict) -> None:
        """兼容旧 bb_state 字段；抢购 BB 已固定走 shield.py py 稳定版链路。"""
        if not isinstance(state, dict):
            return
        b64 = state.get("signWasmBase64") or ""
        ver = state.get("signWasmVersion") or ""
        ok = bool(state.get("signWasmOk", False))
        exp = state.get("expiredAt") or 0
        if not b64 or not ver:
            return
        self.bb_sign_wasm_b64 = str(b64)
        # 强制使用 Python 原版的字面值，与设备指纹历史一致
        from shield import WASM_TAG as _WASM_TAG
        self.bb_sign_wasm_version = _WASM_TAG
        self.bb_sign_wasm_ok = bool(ok)
        try:
            self.bb_state_expired_at = int(exp or 0)
        except (TypeError, ValueError):
            self.bb_state_expired_at = 0

    def ensure_bb_state(self, force: bool = False) -> None:
        """兼容旧调用；抢购 BB 不再依赖外部 bootstrap。"""
        return None

    # ── 梆梆暖场上报（POST /bbprbdata/upload） ──────────────────────────────

    def warmup_bb_is_fresh(self, ttl_ms: int = 60_000) -> bool:
        """暖场是否仍在 60 秒缓存窗口内。"""
        warm_at = int(self.bb_warmup_at or 0)
        if not warm_at:
            return False
        import time as _t
        now_ms = int(_t.time() * 1000)
        return (now_ms - warm_at) < int(ttl_ms or 60_000)

    def warmup_bb_if_stale(
        self,
        *,
        force: bool = False,
        ttl_ms: int = 60_000,
        page_url: str = "https://h5.moutai519.com.cn/",
    ) -> dict:
        """旧暖场入口保留兼容；当前抢购 BB 固定使用 shield.py py 稳定版链路。

        返回 {ok, skipped, age_ms, message, serial, status, phone}。
        失败不 raise（暖场失败不阻断后续抢购）。
        """
        phone_n = _norm_phone(self.phone) or ""
        if not force and self.warmup_bb_is_fresh(ttl_ms):
            import time as _t

            warm_at = int(self.bb_warmup_at or 0)
            now_ms = int(_t.time() * 1000)
            return {
                "ok": True,
                "skipped": True,
                "phone": phone_n,
                "age_ms": now_ms - warm_at,
                "serial": int(self.bb_serial or 1),
                "message": "warmup cache fresh",
                "status": 200,
            }

        s = self.ensure_session()
        try:
            result = s.warmup_bb(
                account_seed=phone_n,
                force=force,
                ttl_ms=ttl_ms,
                page_url=page_url,
            )
        except Exception as ex:
            return {
                "ok": False,
                "skipped": False,
                "phone": phone_n,
                "age_ms": None,
                "serial": int(self.bb_serial or 1),
                "message": str(ex),
                "status": -1,
            }
        self._sync_bb_warmup_from_session(s)
        if isinstance(result, dict):
            result = dict(result)
            result["phone"] = phone_n
            return result
        return {
            "ok": False,
            "skipped": False,
            "phone": phone_n,
            "age_ms": None,
            "serial": int(self.bb_serial or 1),
            "message": "warmup returned non-object",
            "status": -1,
        }

    # ── Session 管理 ──

    def ensure_session(self) -> Session:
        """获取（或重建）此账号专属的 Session（出口由代理池/分片决定，与抢购一致轮询）。"""
        raw_proxy = (self.proxy or "").strip()
        want_proxy = _resolve_account_proxy_for_session(raw_proxy)
        # 全局本机开启时 want_proxy 恒为 direct，但绝不能写回 accounts.json，
        # 否则关闭全局后 proxy 仍停留在 direct，无法恢复代理池/分片。
        if not global_force_local_ip() and want_proxy != raw_proxy:
            self.proxy = want_proxy
            if _hot_path_active():
                _mark_hot_dirty()
            else:
                with _lock:
                    _save_raw([POOL._map[p].to_dict() for p in POOL._phones])
        psg = _ordered_unique_phones(getattr(self, "proxy_shard_group", None) or [])
        fp = bool(getattr(self, "full_pool_rush", False))
        prr = bool(getattr(self, "proxy_rotate_per_request", False))
        psm = str(getattr(self, "proxy_shard_mode", "auto") or "auto").strip().lower()
        psc = int(getattr(self, "proxy_shard_count", 0) or 0)
        desired_wv = str(self.wv or "").strip()
        from project_versions import LEGACY_BB_SIGN_WASM_VERSIONS

        if not desired_wv or desired_wv in LEGACY_BB_SIGN_WASM_VERSIONS:
            from shield import WASM_TAG as _WASM_TAG

            desired_wv = _WASM_TAG
        _ph = _norm_phone(self.phone)
        if psg:
            _acct_phones = psg
        else:
            _acct_phones = _ordered_unique_phones(POOL._phones)
        try:
            _acct_index = _acct_phones.index(_ph)
        except ValueError:
            _acct_index = 0
        _total_accts = max(1, len(_acct_phones))
        _acct_idx_sig = (_acct_index, _total_accts)
        use_wasm_bb = bool(getattr(self, "use_wasm_bb", False))
        use_mobile_relay = bool(getattr(self, "use_mobile_relay", False))
        mobile_relay_device = str(getattr(self, "mobile_relay_device", "") or "").strip()
        relay_sig = (use_mobile_relay, mobile_relay_device)
        if (want_proxy or "").strip():
            session_src: object = (want_proxy, fp, prr, psm, psc, use_wasm_bb, relay_sig)
        else:
            session_src = ("", _acct_idx_sig, fp, prr, psm, psc, use_wasm_bb, relay_sig)
        if self._session is not None:
            prev = getattr(self, "_session_proxy_src", None)
            sid_mismatch = prev is None or prev != session_src
            # 磁盘/发码后已写入 bsid，但旧 Session 仍为「无浩天」时须重建，否则抢购一直报无 BS-DVID
            s_bs = str(getattr(self._session, "_bsid", "") or "").strip()
            e_bs = str(self.bsid or "").strip()
            bs_mismatch = s_bs != e_bs
            s_wv = str(getattr(self._session, "_wv", "") or "").strip()
            wv_mismatch = s_wv != desired_wv
            if sid_mismatch or bs_mismatch or wv_mismatch:
                self._session = None
                self._session_proxy_src = None
            else:
                pass
        if self._session is None:
            # 单账号构造串行（多账号并行）：拿到 _build_lock 后才有资格走构造链路
            with self._build_lock:
                if self._session is None:
                    # ── 阶段 1：锁内快照 + 计算 kw（μs 级）──
                    should_build = False
                    kw: dict = {}
                    old_aid = old_sdk = old_mfr = old_mdl = ""
                    old_h5_did = old_h5_sid = old_h5_user_agent = ""
                    old_h5_device_id = old_h5_android_version = old_h5_build_id = old_h5_chrome_version = ""
                    old_rush_device_pinned = False
                    with _lock:
                        if self._session is None:
                            old_aid = (self.aid or "").strip()
                            old_sdk = (self.sdk or "").strip()
                            old_mfr = (self.mfr or "").strip()
                            old_mdl = (self.mdl or "").strip()
                            old_h5_did = (self.h5_did or "").strip()
                            old_h5_sid = (self.h5_sid or "").strip()
                            old_h5_user_agent = (self.h5_user_agent or "").strip()
                            old_h5_device_id = (self.h5_device_id or "").strip()
                            old_h5_android_version = (self.h5_android_version or "").strip()
                            old_h5_build_id = (self.h5_build_id or "").strip()
                            old_h5_chrome_version = (self.h5_chrome_version or "").strip()
                            old_rush_device_pinned = bool(self.rush_device_pinned)
                            pr = self._persisted_raw_id()
                            # 已登录：默认以 JWT 内 deviceId 为准，避免 aid 与 token 不一致（4011）；「刷新登录·轮换设备」时 prefer_persisted_device 暂用磁盘新 aid
                            if self.token and not getattr(self, "prefer_persisted_device", False):
                                pr_jwt = raw_id_from_mt_jwt(self.token)
                                if pr_jwt:
                                    pr = pr_jwt
                            kw = dict(
                                sdk=(
                                    str(self.sdk).strip()
                                    if (self.sdk is not None and str(self.sdk).strip() != "")
                                    else None
                                ),
                                mfr=self.mfr, mdl=self.mdl, scr=self.scr,
                                btime=self.btime, bsid=self.bsid, wv=desired_wv,
                                raw_id=pr,
                                proxy_url=want_proxy or None,
                                h5_user_agent=self.h5_user_agent,
                                h5_device_id=self.h5_device_id,
                                haotian_device_id=self.haotian_device_id,
                                h5_android_version=self.h5_android_version,
                                h5_device_model=self.h5_device_model,
                                h5_build_id=self.h5_build_id,
                                h5_chrome_version=self.h5_chrome_version,
                                gdxidpyhxdE=self.gdxidpyhxdE,
                                yidun_gdx_fp=self.yidun_gdx_fp,
                                gdx_icp=self.gdx_icp,
                                bb_state=self.bb_state,
                                use_wasm_bb=bool(getattr(self, "use_wasm_bb", False)),
                                use_mobile_relay=bool(getattr(self, "use_mobile_relay", False)),
                                mobile_relay_device=str(getattr(self, "mobile_relay_device", "") or "").strip(),
                            )
                            if (self.h5_did or "").strip() and (self.h5_sid or "").strip():
                                kw["h5_did"] = self.h5_did.strip()
                                kw["h5_sid"] = self.h5_sid.strip()
                            if not pr:
                                kw["aid"] = self.aid or ""
                            # 空代理账号不再按账号切独占分片；统一交给 proxy_pool 的整体池全局游标。
                            # 旧的 proxy_shard_* 字段保留为账号 JSON/UI 兼容，但不参与 Session 出口选择。
                            proxy_shard_kw = None
                            kw["proxy_shard"] = proxy_shard_kw
                            kw["proxy_account_index"] = _acct_index
                            kw["proxy_account_total"] = _total_accts
                            kw["full_pool_rush"] = bool(getattr(self, "full_pool_rush", False))
                            kw["proxy_rotate_per_request"] = bool(getattr(self, "proxy_rotate_per_request", False))
                            should_build = True
                    if should_build:
                        # ── 阶段 2：锁外构造（30ms CPU/proxy_pool 查询，多账号此处并行）──
                        created_session = Session(**kw)
                        try:
                            created_session._account_phone = _norm_phone(self.phone)
                        except Exception:
                            pass
                        # ── 阶段 3：锁内写回 + 字段同步（μs 级）──
                        with _lock:
                            self._session = created_session
                            try:
                                self._session._bb_warmup_persist_cb = self._sync_bb_warmup_from_session
                            except Exception:
                                pass
                            self._session._gdx_persist_cb = self._sync_gdx_from_session
                            # 记录生成的设备参数，方便下次重建
                            s = self._session
                            self.aid = s._raw_id
                            segs_ua = (s._ua or "").split(";")
                            if len(segs_ua) >= 4:
                                self.sdk = self.sdk or segs_ua[1]
                                self.mfr = self.mfr or segs_ua[2]
                                self.mdl = self.mdl or segs_ua[3]
                            self.scr   = s._scr
                            self.btime = s._dtime
                            self.wv = str(getattr(s, "_wv", "") or desired_wv)
                            self.h5_user_agent = str(getattr(s, "_h5_user_agent", "") or self.h5_user_agent or "").strip()
                            self.h5_device_id = str(getattr(s, "_h5_device_id", "") or self.h5_device_id or "").strip()
                            self.haotian_device_id = str(getattr(s, "_haotian_device_id", "") or self.haotian_device_id or "").strip()
                            self.h5_android_version = str(getattr(s, "_h5_android_version", "") or self.h5_android_version or "").strip()
                            self.h5_device_model = str(getattr(s, "_h5_device_model", "") or self.h5_device_model or "").strip()
                            self.h5_build_id = str(getattr(s, "_h5_build_id", "") or self.h5_build_id or "").strip()
                            self.h5_chrome_version = str(getattr(s, "_h5_chrome_version", "") or self.h5_chrome_version or "").strip()
                            self.gdxidpyhxdE = str(getattr(s, "_gdxidpyhxdE", "") or self.gdxidpyhxdE or "").strip()
                            self.yidun_gdx_fp = str(getattr(s, "_yidun_gdx_fp", "") or self.yidun_gdx_fp or "").strip()
                            self.gdx_icp = str(getattr(s, "_gdx_icp", "") or self.gdx_icp or "").strip()
                            # Pin H5 DID/SID like py版: reuse it on later Session rebuilds.
                            hid = str(getattr(s, "_h5id", "") or "").strip()
                            sid = str(getattr(s, "_h5sid", "") or "").strip()
                            if hid and sid:
                                self.h5_did = hid
                                self.h5_sid = sid
                                self.rush_device_pinned = True
                            else:
                                self.h5_did = ""
                                self.h5_sid = ""
                                self.rush_device_pinned = False
                            # 若已有 token/cookie 则直接注入
                            if self.token:
                                s._token  = self.token
                                s._cookie = self.cookie
                                s._uid    = self.uid
                                s._verify_status = self.verify_status
                            self._session_proxy_src = session_src
                            # aid 变化或首次从 Session 回填 MT App 行（浩天前多为 34/samsung/S9280）须落盘
                            need_save = (self.aid or "").strip() != old_aid
                            if not need_save:
                                need_save = (
                                    (not old_sdk and (self.sdk or "").strip())
                                    or (not old_mfr and (self.mfr or "").strip())
                                    or (not old_mdl and (self.mdl or "").strip())
                                    or ((self.h5_did or "").strip() != old_h5_did)
                                    or ((self.h5_sid or "").strip() != old_h5_sid)
                                    or ((self.h5_user_agent or "").strip() != old_h5_user_agent)
                                    or ((self.h5_device_id or "").strip() != old_h5_device_id)
                                    or ((self.h5_android_version or "").strip() != old_h5_android_version)
                                    or ((self.h5_build_id or "").strip() != old_h5_build_id)
                                    or ((self.h5_chrome_version or "").strip() != old_h5_chrome_version)
                                    or (bool(self.rush_device_pinned) != old_rush_device_pinned)
                                )
                            if need_save:
                                if _hot_path_active():
                                    _mark_hot_dirty()
                                else:
                                    POOL.save()
        # 抢购中号后写入的出口：每次取 Session 时注入，供 compose 固定同线
        s = self._session
        if s is not None:
            s._gdx_persist_cb = self._sync_gdx_from_session
            try:
                s._bb_warmup_persist_cb = self._sync_bb_warmup_from_session
            except Exception:
                pass
            _pin_off = (os.environ.get("MT_COMPOSE_USE_RUSH_PIN", "1") or "1").strip().lower() in (
                "0",
                "false",
                "no",
                "off",
            )
            if _pin_off:
                s._compose_pin_proxy_url = ""
            else:
                psu = (getattr(self, "rush_success_proxy_url", "") or "").strip()
                if psu:
                    try:
                        from app import _normalize_proxy_url as _npu

                        s._compose_pin_proxy_url = _npu(psu)
                    except Exception:
                        s._compose_pin_proxy_url = psu
                else:
                    s._compose_pin_proxy_url = ""
            # 支付链路与 compose 钉线解耦：中号出口写入 rush_success_proxy_url 时，供 pay/requestPay 使用。
            psu_pay = (getattr(self, "rush_success_proxy_url", "") or "").strip()
            if psu_pay:
                try:
                    from app import _normalize_proxy_url as _npu_pay

                    s._rush_success_proxy_url_norm = _npu_pay(psu_pay)
                except Exception:
                    s._rush_success_proxy_url_norm = psu_pay
            else:
                s._rush_success_proxy_url_norm = ""
            # 把暖场状态从 AccountEntry 同步到 Session（便于 _h5_hdr 抢购路径兜底命中 60s 缓存）
            s._bb_warmup_at = int(self.bb_warmup_at or 0)
            s._bb_serial = int(self.bb_serial or 1)
            s._bb_device_info = dict(self.bb_device_info or {})
            s._bb_tactics_version = int(self.bb_tactics_version or 0)
            s._bb_time_delta = int(self.bb_time_delta or 0)
            if not isinstance(getattr(s, "_bb_state", None), dict):
                s._bb_state = {}
            expected_h5_did = str(getattr(s, "_h5id", "") or "").strip()
            if isinstance(getattr(self, "bb_state", None), dict) and self.bb_state:
                bb_state = dict(self.bb_state)
                state_did = str(bb_state.get("bbDid") or "").strip()
                has_cookie_state = isinstance(bb_state.get("bbCookieState"), dict)
                if expected_h5_did and (state_did != expected_h5_did) and (state_did or has_cookie_state):
                    bb_state = {
                        key: bb_state.get(key)
                        for key in _BB_WARMUP_PORTABLE_KEYS
                        if key in bb_state
                    }
                s._bb_state.update(bb_state)
            if expected_h5_did:
                s._bb_state["bbDid"] = expected_h5_did
            s._bb_state.setdefault("bbSerial", int(self.bb_serial or 1))
            if self.bb_device_info:
                s._bb_state.setdefault("bbDeviceInfo", dict(self.bb_device_info or {}))
            s._bb_state.setdefault("bbTacticsVersion", int(self.bb_tactics_version or 0))
            s._bb_state.setdefault("bbTimeDelta", int(self.bb_time_delta or 0))
        return self._session

    def _sync_from_session(self):
        """将 session 的登录态同步回 entry"""
        if self._session is None:
            return
        s = self._session
        self.token  = s._token
        self.cookie = s._cookie
        self.uid    = s._uid
        self.verify_status = getattr(s, "_verify_status", None)
        self.aid    = s._raw_id
        segs = (s._ua or "").split(";")
        if len(segs) >= 4:
            self.sdk = self.sdk or segs[1]
            self.mfr = self.mfr or segs[2]
            self.mdl = self.mdl or segs[3]
        self.scr    = s._scr
        self.btime  = s._dtime
        self.h5_user_agent = str(getattr(s, "_h5_user_agent", "") or self.h5_user_agent or "").strip()
        self.h5_device_id = str(getattr(s, "_h5_device_id", "") or self.h5_device_id or "").strip()
        self.haotian_device_id = str(getattr(s, "_haotian_device_id", "") or self.haotian_device_id or "").strip()
        self.h5_android_version = str(getattr(s, "_h5_android_version", "") or self.h5_android_version or "").strip()
        self.h5_device_model = str(getattr(s, "_h5_device_model", "") or self.h5_device_model or "").strip()
        self.h5_build_id = str(getattr(s, "_h5_build_id", "") or self.h5_build_id or "").strip()
        self.h5_chrome_version = str(getattr(s, "_h5_chrome_version", "") or self.h5_chrome_version or "").strip()
        self.gdxidpyhxdE = str(getattr(s, "_gdxidpyhxdE", "") or self.gdxidpyhxdE or "").strip()
        self.yidun_gdx_fp = str(getattr(s, "_yidun_gdx_fp", "") or self.yidun_gdx_fp or "").strip()
        self.gdx_icp = str(getattr(s, "_gdx_icp", "") or self.gdx_icp or "").strip()
        hid = str(getattr(s, "_h5id", "") or "").strip()
        sid = str(getattr(s, "_h5sid", "") or "").strip()
        if hid and sid:
            self.h5_did = hid
            self.h5_sid = sid
            self.rush_device_pinned = True
        if getattr(s, "_bsid", ""):
            self.bsid = s._bsid
        self._sync_bb_warmup_from_session(s)

    def _sync_gdx_from_session(self, session=None):
        s = session or self._session
        if s is None:
            return
        changed = False
        for attr, session_attr in (
            ("gdxidpyhxdE", "_gdxidpyhxdE"),
            ("yidun_gdx_fp", "_yidun_gdx_fp"),
            ("gdx_icp", "_gdx_icp"),
        ):
            val = str(getattr(s, session_attr, "") or "").strip()
            if val and getattr(self, attr, "") != val:
                setattr(self, attr, val)
                changed = True
        if changed:
            try:
                POOL.save()
            except Exception:
                pass

    def _sync_bb_warmup_from_session(self, session=None):
        s = session or self._session
        if s is None:
            return False
        changed = False
        state = getattr(s, "_bb_state", None)
        if isinstance(state, dict) and state:
            new_state = {key: state.get(key) for key in _BB_STATE_KEYS if key in state}
            expected_h5_did = str(getattr(s, "_h5id", "") or "").strip()
            state_did = str(new_state.get("bbDid") or "").strip()
            has_cookie_state = isinstance(new_state.get("bbCookieState"), dict)
            if expected_h5_did and (state_did != expected_h5_did) and (state_did or has_cookie_state):
                new_state = {
                    key: new_state.get(key)
                    for key in _BB_WARMUP_PORTABLE_KEYS
                    if key in new_state
                }
                new_state["bbDid"] = expected_h5_did
            if new_state and new_state != getattr(self, "bb_state", {}):
                self.bb_state = new_state
                changed = True
        for dst, src, cast, default in (
            ("bb_warmup_at", "_bb_warmup_at", int, 0),
            ("bb_serial", "_bb_serial", int, 1),
            ("bb_tactics_version", "_bb_tactics_version", int, 0),
            ("bb_time_delta", "_bb_time_delta", int, 0),
        ):
            try:
                val = cast(getattr(s, src, default) or default)
            except (TypeError, ValueError):
                val = default
            if getattr(self, dst, default) != val:
                setattr(self, dst, val)
                changed = True
        bdi = getattr(s, "_bb_device_info", None)
        if isinstance(bdi, dict) and bdi != self.bb_device_info:
            self.bb_device_info = dict(bdi)
            changed = True
        if changed:
            try:
                POOL.save()
            except Exception:
                pass
        return changed


# ──────────────────────────────────────────────
#  账号池
# ──────────────────────────────────────────────

class AccountPool:
    def __init__(self):
        # 保持插入顺序：用 list + dict 双索引
        self._phones: list[str] = []
        self._map: dict[str, AccountEntry] = {}
        # 远程新户「整段上号」完成前不落 accounts.json（避免主控先看到已登录但无实名/地址）
        self._deferred_onboarding_phone: Optional[str] = None
        self._load()

    def begin_deferred_onboarding(self, phone: str) -> None:
        """与 webui 远程新户 deferPoolPersistUntilOnboard 配合；发码前调用。"""
        self._deferred_onboarding_phone = _norm_phone(phone)

    def end_deferred_onboarding(self, phone: Optional[str] = None) -> None:
        """结束延迟落盘；phone 省略或与当前一致时清除。"""
        cur = self._deferred_onboarding_phone
        if not cur:
            return
        p = _norm_phone(phone or "")
        if not p or p == cur:
            self._deferred_onboarding_phone = None

    def deferred_onboarding_phone(self) -> Optional[str]:
        return self._deferred_onboarding_phone

    # ── 加载 / 保存 ──

    def _ingest_raw_list(self, raw: list) -> None:
        """由磁盘解析结果填充内存（须已确认为 list）。"""
        self._phones = []
        self._map = {}
        for d in raw:
            if not isinstance(d, dict):
                continue
            phone = _norm_phone(d.get("phone", ""))
            if not phone:
                continue
            e = AccountEntry.from_dict(d)
            if phone not in self._map:
                self._phones.append(phone)
            self._map[phone] = e

    def _load(self):
        self._ingest_raw_list(_load_raw())

    def load_memory_accounts(self, raw: list[dict]) -> dict:
        if not isinstance(raw, list):
            raw = []
        with _lock:
            self._map.clear()
            self._phones.clear()
            self._ingest_raw_list(raw)
            for e in self._map.values():
                e._session = None
                e._session_proxy_src = None
            return {"count": len(self._phones), "phones": list(self._phones)}

    def save(self, *, force: bool = False) -> None:
        if not force and getattr(self, "_deferred_onboarding_phone", None):
            return
        if (os.environ.get("MT_DIST_MEMORY_ONLY") or "").strip().lower() in ("1", "true", "yes", "on"):
            return
        with _lock:
            _save_raw([self._map[p].to_dict() for p in self._phones])
        try:
            from webui_server import sync_api_listen_ports

            sync_api_listen_ports()
        except Exception:
            pass

    def invalidate_all_sessions(self) -> None:
        """清空池内所有账号内存 Session，下次 ensure_session 按当前代理策略重建。"""
        with _lock:
            for e in self._map.values():
                e._session = None
                e._session_proxy_src = None

    def clear_all_proxy_direct_markers(self) -> int:
        """
        将所有账号 proxy 字段中的「强制本机」类标记清空为 '' 并落盘。
        与全局本机 IP 联动：避免关全局后仍有 direct/本机 残留在 accounts.json。
        """
        n = 0
        with _lock:
            for e in self._map.values():
                raw = (e.proxy or "").strip()
                if raw and _is_direct_marker(raw):
                    e.proxy = ""
                    e._session = None
                    e._session_proxy_src = None
                    n += 1
            if n:
                _save_raw([self._map[p].to_dict() for p in self._phones])
        if n:
            try:
                from webui_server import sync_api_listen_ports

                sync_api_listen_ports()
            except Exception:
                pass
        return n

    def apply_proxy_pool_auto_shards(
        self,
        *,
        only_logged: bool = True,
        phones: list[str] | None = None,
    ) -> dict:
        """
        代理池保存/API提取/清空后，把目标账号重置为自动分片并清 Session。
        传入 phones 时只作用于当前勾选/当前分组范围；分片也按该范围计算。
        """
        changed = 0
        touched = 0
        skipped = 0
        targeted = phones is not None
        requested = _ordered_unique_phones(phones or []) if targeted else list(self._phones)
        eligible: list[str] = []
        with _lock:
            for phone in requested:
                e = self._map.get(phone)
                if not e:
                    skipped += 1
                    continue
                if only_logged and e.status != STATUS_OK:
                    skipped += 1
                    continue
                eligible.append(phone)
            shard_group = list(eligible) if targeted else []
            for phone in eligible:
                e = self._map.get(phone)
                if not e:
                    skipped += 1
                    continue
                touched += 1
                before = (
                    e.proxy,
                    e.proxy_shard_mode,
                    int(e.proxy_shard_count or 0),
                    tuple(getattr(e, "proxy_shard_group", None) or []),
                    bool(e.proxy_locked_after_rush),
                )
                e.proxy = ""
                e.proxy_shard_mode = "auto"
                e.proxy_shard_count = 0
                e.proxy_shard_group = list(shard_group)
                e.proxy_locked_after_rush = False
                e._session = None
                e._session_proxy_src = None
                after = (
                    e.proxy,
                    e.proxy_shard_mode,
                    int(e.proxy_shard_count or 0),
                    tuple(getattr(e, "proxy_shard_group", None) or []),
                    bool(e.proxy_locked_after_rush),
                )
                changed += 1 if before != after else 0
            if changed:
                _save_raw([self._map[p].to_dict() for p in self._phones])
        if changed:
            try:
                from webui_server import sync_api_listen_ports

                sync_api_listen_ports()
            except Exception:
                pass
        return {
            "applied": True,
            "onlyLogged": bool(only_logged),
            "targeted": bool(targeted),
            "requestedAccounts": len(requested),
            "touchedAccounts": touched,
            "changedAccounts": changed,
            "skippedAccounts": skipped,
            "groupAccounts": len(shard_group),
        }

    def reload_from_disk(self) -> dict:
        """从 accounts.json 重新载入内存池（机器人等外部改盘后同步，无需重启软件）。"""
        prev_n = len(self._phones)
        if not ACCOUNTS_FILE.exists():
            raw: list = []
        else:
            try:
                raw = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
            except Exception as ex:
                return {
                    "ok": False,
                    "error": f"accounts.json 解析失败: {ex}",
                    "count": prev_n,
                    "stats": self.stats(),
                }
        if not isinstance(raw, list):
            return {
                "ok": False,
                "error": "accounts.json 须为 JSON 数组",
                "count": prev_n,
                "stats": self.stats(),
            }
        with _lock:
            self._ingest_raw_list(raw)
        try:
            from webui_server import sync_api_listen_ports

            sync_api_listen_ports()
        except Exception:
            pass
        return {
            "ok": True,
            "count": len(self._phones),
            "stats": self.stats(),
        }

    # ── CRUD ──

    def add(self, phone: str, nickname: str = "", remark: str = "") -> AccountEntry:
        """添加账号；已存在时若传入非空昵称/备注则更新并保存"""
        phone = _norm_phone(phone)
        rem = str(remark or "").strip()
        with _lock:
            if phone in self._map:
                nick = str(nickname or "").strip()
                if nick:
                    self._map[phone].nickname = nick or phone
                if rem:
                    self._map[phone].remark = rem
                if nick or rem:
                    self.save()
                return self._map[phone]
            e = AccountEntry(phone, nickname or phone)
            if rem:
                e.remark = rem
            self._phones.append(phone)
            self._map[phone] = e
            self.save()
            return e

    def add_batch(self, phones: list[str]) -> list[AccountEntry]:
        """批量添加，跳过重复"""
        result = []
        changed = False
        with _lock:
            for phone in phones:
                phone = _norm_phone(phone)
                if not phone:
                    continue
                if phone not in self._map:
                    e = AccountEntry(phone)
                    self._phones.append(phone)
                    self._map[phone] = e
                    changed = True
                result.append(self._map[phone])
        if changed:
            self.save()
        return result

    def import_mtm_text(self, text: object, *, overwrite: bool = False) -> dict:
        """导入 MTM 账号包；支持一段文本里混贴多个 MTM 串或已解开的 JSON。"""
        payloads = _extract_mtm_payloads(text)
        candidates: list[dict] = []
        for payload in payloads:
            candidates.extend(_iter_mtm_account_candidates(payload))

        seen: set[str] = set()
        prepared: list[tuple[str, dict, list[dict]]] = []
        invalid: list[dict] = []
        for idx, src in enumerate(candidates, start=1):
            try:
                entry_dict, addresses = _mtm_to_account_dict(src)
                phone = _norm_phone(entry_dict.get("phone"))
                if not phone:
                    raise ValueError("缺少手机号")
                if phone in seen:
                    invalid.append({"index": idx, "phone": phone, "reason": "同批重复"})
                    continue
                seen.add(phone)
                prepared.append((phone, entry_dict, addresses))
            except Exception as ex:
                invalid.append({"index": idx, "reason": str(ex) or type(ex).__name__})

        added = 0
        updated = 0
        skipped = 0
        imported_phones: list[str] = []
        skipped_phones: list[str] = []
        changed = False
        with _lock:
            for phone, entry_dict, _addresses in prepared:
                if phone in self._map and not overwrite:
                    skipped += 1
                    skipped_phones.append(phone)
                    continue
                if phone in self._map:
                    old = self._map[phone].to_dict()
                    keep_keys = (
                        "proxy",
                        "proxy_shard_mode",
                        "proxy_shard_count",
                        "proxy_shard_urls",
                        "proxy_shard_group",
                        "proxy_locked_after_rush",
                        "full_pool_rush",
                        "proxy_rotate_per_request",
                        "remark",
                    )
                    merged = dict(entry_dict)
                    for key in keep_keys:
                        if key in old and key not in merged:
                            merged[key] = old[key]
                    if old.get("remark") and entry_dict.get("remark") == "MTM导入":
                        merged["remark"] = old.get("remark")
                    e = AccountEntry.from_dict(merged)
                    updated += 1
                else:
                    e = AccountEntry.from_dict(entry_dict)
                    self._phones.append(phone)
                    added += 1
                e._session = None
                e._session_proxy_src = None
                self._map[phone] = e
                imported_phones.append(phone)
                changed = True
            if changed:
                _save_raw([self._map[p].to_dict() for p in self._phones])

        address_saved = 0
        if changed:
            try:
                import address_db

                if address_db.enabled():
                    for phone, _entry_dict, addresses in prepared:
                        if phone in imported_phones and addresses:
                            address_db.save_address_list_for_phone(phone, addresses)
                            address_saved += 1
            except Exception:
                address_saved = 0
            try:
                from webui_server import sync_api_listen_ports

                sync_api_listen_ports()
            except Exception:
                pass

        return {
            "ok": bool(prepared),
            "detected": len(candidates),
            "valid": len(prepared),
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "invalid": len(invalid),
            "addressSaved": address_saved,
            "phones": imported_phones,
            "skippedPhones": skipped_phones,
            "invalidItems": invalid[:20],
            "overwrite": bool(overwrite),
            "stats": self.stats(),
        }

    def remove(self, phone: str) -> bool:
        phone = _norm_phone(phone)
        with _lock:
            if phone not in self._map:
                return False
            self._phones.remove(phone)
            del self._map[phone]
            _save_raw([self._map[p].to_dict() for p in self._phones])
        try:
            from webui_server import sync_api_listen_ports

            sync_api_listen_ports()
        except Exception:
            pass
        return True

    def remove_batch(self, phones: list[str]) -> int:
        """批量删除账号；返回实际删除条数。单锁内落盘，只触发一次端口同步。"""
        norm: list[str] = []
        for p in phones or []:
            q = _norm_phone(str(p).strip())
            if q:
                norm.append(q)
        if not norm:
            return 0
        removed = 0
        with _lock:
            for phone in norm:
                if phone not in self._map:
                    continue
                self._phones.remove(phone)
                del self._map[phone]
                removed += 1
            if removed:
                _save_raw([self._map[p].to_dict() for p in self._phones])
        if removed:
            try:
                from webui_server import sync_api_listen_ports

                sync_api_listen_ports()
            except Exception:
                pass
        return removed

    def get(self, phone: str) -> AccountEntry | None:
        return self._map.get(_norm_phone(phone))

    def set_nickname(self, phone: str, nickname: str, remark: object = _REMARK_ARG_OMIT) -> bool:
        """修改列表显示名；remark 传入 str 时同步附加备注（空串则清空备注）"""
        phone = _norm_phone(phone)
        e = self._map.get(phone)
        if not e:
            return False
        nick = str(nickname or "").strip()
        e.nickname = nick or phone
        if remark is not _REMARK_ARG_OMIT:
            e.remark = str(remark or "").strip()
        self.save()
        return True

    def set_proxy(self, phone: str, proxy: str) -> bool:
        """兼容旧接口：禁用单账号代理，统一改由代理池控制。"""
        phone = _norm_phone(phone)
        e = self._map.get(phone)
        if not e:
            return False
        if (proxy or "").strip():
            print(f"[代理] 已忽略单账号代理设置 {phone}，统一使用代理池轮换")
        e.proxy = ""
        e.proxy_locked_after_rush = False
        e.prefer_persisted_device = False
        e._session = None
        e._session_proxy_src = None
        self.save()
        return True

    def unlock_proxy_lock_to_pool(self, phone: str, reason: str = "") -> bool:
        """
        取消「抢购后锁定出口」，回到代理池分片轮询。
        用于订单已失效/不存在或已付款后，释放账号固定代理。
        """
        phone = _norm_phone(phone)
        e = self._map.get(phone)
        if not e:
            return False
        e.proxy_locked_after_rush = False
        # 清空固定代理，确保后续 ensure_session 直接走池子分片而非单号 URL
        e.proxy = ""
        e._session = None
        e._session_proxy_src = None
        self.save()
        if reason:
            print(f"[代理] 账号 {phone} 已取消锁出口：{reason}")
        else:
            print(f"[代理] 账号 {phone} 已取消锁出口")
        return True

    def reset_device_identity(self, phone: str) -> bool:
        """
        清空登录态与设备指纹（aid/token 等），用于修复 4011 device id inconsistency。
        之后请重新「发码 → 登录」，会生成与 Token 一致的新设备对。
        """
        phone = _norm_phone(phone)
        e = self._map.get(phone)
        if not e:
            return False
        e.token = ""
        e.cookie = ""
        e.uid = ""
        e.aid = ""
        e.sdk = ""
        e.mfr = ""
        e.mdl = ""
        e.scr = ""
        e.btime = ""
        e.bsid = ""
        e.wv = ""
        e.clear_h5_bb_context(clear_profile=True)
        e.proxy_locked_after_rush = False
        e.prefer_persisted_device = False
        e.risk_tag = ""
        e.risk_message = ""
        e.risk_checked_at = ""
        e._session = None
        e._session_proxy_src = None
        e.status = STATUS_PENDING
        self.save()
        return True

    def refresh_simulated_device_params_only(self, phone: str) -> bool:
        """
        轮换完整模拟 App 指纹（aid / sdk,mfr,mdl / scr / btime / wv），与登录后请求头、MT-K/V 同源字段一致。

        同时**清除登录态**（token / cookie / uid）并置为待登录，使新设备与**下一次发码→登录**拉到的新 Token/JWT 一一对应，
        避免 4011 device id inconsistency。保留：手机号、备注、代理(proxy)；清空：bsid、h5、抢购锁出口、风控探测缓存。
        """
        phone = _norm_phone(phone)
        e = self._map.get(phone)
        if not e:
            return False
        aid_seed = uuid.uuid4().hex[:16]
        s_new = Session(aid=aid_seed, proxy_url=None)
        e.aid = s_new._raw_id
        segs = (s_new._ua or ";;;").split(";")
        e.sdk = segs[1] if len(segs) > 1 else ""
        e.mfr = segs[2] if len(segs) > 2 else ""
        e.mdl = segs[3] if len(segs) > 3 else ""
        e.scr = s_new._scr
        e.btime = s_new._dtime
        e.wv = str(getattr(s_new, "_wv", "") or "")
        e.bsid = ""
        e.clear_h5_bb_context(clear_profile=True)
        e.proxy_locked_after_rush = False
        e.prefer_persisted_device = False
        e.token = ""
        e.cookie = ""
        e.uid = ""
        e.status = STATUS_PENDING
        e.risk_tag = ""
        e.risk_message = ""
        e.risk_checked_at = ""
        e._session = None
        e._session_proxy_src = None
        self.save()
        return True

    def batch_refresh_simulated_device_params_only(
        self,
        *,
        phones: list[str] | None = None,
        delay: float = 0.0,
    ) -> list[dict]:
        """批量换新模拟指纹并清除各账号登录态；phones 为空则处理池中全部账号。成功后需各自重新发码登录。"""
        targets = phones if phones is not None else list(self._phones)
        out: list[dict] = []
        for i, ph in enumerate(targets):
            ph = _norm_phone(ph)
            if i and delay > 0:
                _time.sleep(delay)
            if not ph or ph not in self._map:
                out.append({"phone": ph or "", "ok": False, "message": "账号不存在"})
                continue
            ok = self.refresh_simulated_device_params_only(ph)
            out.append({
                "phone": ph,
                "ok": ok,
                "message": (
                    "已换新指纹并清除登录态，请发码重新登录"
                    if ok
                    else "失败"
                ),
            })
        return out

    def pin_h5_device_after_rush_ok(
        self,
        phone: str,
        session: Session | None = None,
        rush_response: dict | None = None,
    ) -> bool:
        """
        抢购业务成功后立即写死设备（强制落盘）：
        - App 模拟设备：aid / sdk,mfr,mdl / scr / btime / wv / bsid（与当次 rush 的 Session 一致）
        - H5：_h5id/_h5sid，有则 rush_device_pinned=True
        - 若 rush_response.data 含 priorityRecordId：记录当次 Session 的出口代理与公网 IP，供后续 compose 固定同线（见 MT_COMPOSE_USE_RUSH_PIN）
        - 关闭 prefer_persisted_device，避免下次建 Session 绕过已写死的 aid

        session：若传入，须为本轮 ensure_session() 上执行 rush 的同一 Session，避免 e._session 与当次请求不一致导致写死失败。
        rush_response：茅台 rush 原始 JSON，用于判断是否中优先购记录号并锁出口。
        """
        phone = _norm_phone(phone)
        e = self._map.get(phone)
        s = session if session is not None else (e._session if e else None)
        if not e or s is None:
            return False
        e.aid = s._raw_id
        segs = (s._ua or ";;;").split(";")
        if len(segs) > 3:
            e.sdk = segs[1]
            e.mfr = segs[2]
            e.mdl = segs[3]
        elif len(segs) > 2:
            e.sdk = segs[1]
            e.mfr = segs[2]
        e.scr = s._scr
        e.btime = s._dtime
        e.wv = str(getattr(s, "_wv", "") or "")
        sb = str(getattr(s, "_bsid", "") or "").strip()
        if sb:
            e.bsid = sb
        e.prefer_persisted_device = False

        hid = str(getattr(s, "_h5id", "") or "")
        sid = str(getattr(s, "_h5sid", "") or "")
        if hid and sid:
            e.h5_did = hid
            e.h5_sid = sid
            e.rush_device_pinned = True
        else:
            e.rush_device_pinned = False

        e.proxy_locked_after_rush = False

        rid = None
        if isinstance(rush_response, dict):
            for k in ("priorityRecordId", "priorityUserRecordId", "recordId"):
                rid = rush_response.get(k)
                if rid is not None and str(rid).strip() not in ("", "0"):
                    break
            else:
                d = rush_response.get("data")
                if isinstance(d, dict):
                    for k in ("priorityRecordId", "priorityUserRecordId", "recordId"):
                        rid = d.get(k)
                        if rid is not None and str(rid).strip() not in ("", "0"):
                            break
        if rid is not None and str(rid).strip() not in ("", "0"):
            from app import _normalize_proxy_url, _proxy_hostport_for_log

            pu = (getattr(s, "_proxy_url", "") or "").strip()
            e.rush_success_proxy_url = _normalize_proxy_url(pu) if pu else ""
            e.rush_success_egress_ip = ""
            # 默认不把出口公网 IP 写入 accounts（避免「写死」旧 IP、支付旁路展示误导）；
            # 需要落盘时设 MT_RUSH_OK_PERSIST_EGRESS_IP=1；仍可用 MT_RUSH_OK_SKIP_IP_PROBE=1 跳过探测。
            persist_eg = (os.environ.get("MT_RUSH_OK_PERSIST_EGRESS_IP", "").strip().lower() in (
                "1", "true", "yes", "on",
            ))
            skip_probe = (os.environ.get("MT_RUSH_OK_SKIP_IP_PROBE", "").strip().lower() in ("1", "true", "yes", "on"))
            if persist_eg and not skip_probe:
                try:
                    probe = s.fetch_public_ip()
                    if isinstance(probe, dict) and probe.get("ok") and (probe.get("ip") or "").strip():
                        e.rush_success_egress_ip = str(probe.get("ip")).strip()
                except Exception:
                    pass
            print(
                f"[抢购出口锁定] phone={phone} priorityRecordId={rid} "
                f"proxy={_proxy_hostport_for_log(pu)} egress_ip={e.rush_success_egress_ip or '(未持久化/探测跳过)'}",
                flush=True,
            )
        e._session = None
        e._session_proxy_src = None
        self.save()
        return True

    def all(self) -> list[AccountEntry]:
        return [self._map[p] for p in self._phones]

    def snapshot(self) -> list[dict]:
        return [e.to_dict() for e in self.all()]

    @staticmethod
    def _triple_key(sdk: str, mfr: str, mdl: str) -> str:
        s = str(sdk or "").strip().lower()
        m = str(mfr or "").strip().lower()
        d = str(mdl or "").strip().lower()
        if not (s and m and d):
            return ""
        return f"{s}|{m}|{d}"

    def _release_reusable_device_slots(
        self, exclude_phone: str = "", max_release: int = 1
    ) -> int:
        """
        设备三元组池耗尽时，优先回收「非已登录账号」占用的 sdk/mfr/mdl，给当前号腾位。
        说明：仅回收待登录/已发码/登录失败账号，避免影响已登录可用号。
        """
        ex = _norm_phone(exclude_phone)
        cap = max(0, int(max_release or 0))
        if cap <= 0:
            return 0
        n = 0
        for p in self._phones:
            if p == ex:
                continue
            e = self._map.get(p)
            if not e:
                continue
            if e.status == STATUS_OK:
                continue
            if not self._triple_key(e.sdk, e.mfr, e.mdl):
                continue
            e.sdk = ""
            e.mfr = ""
            e.mdl = ""
            e.bsid = ""
            e.h5_did = ""
            e.h5_sid = ""
            e.gdxidpyhxdE = ""
            e.yidun_gdx_fp = ""
            e.gdx_icp = ""
            e.rush_device_pinned = False
            e.prefer_persisted_device = False
            e.clear_h5_bb_context(clear_profile=False)
            e._session = None
            e._session_proxy_src = None
            n += 1
            if n >= cap:
                break
        if n > 0:
            self.save()
            print(
                f"[浩天] 设备池占满，已自动释放 {n} 个非登录账号设备占用（exclude={ex or '-' }）",
                flush=True,
            )
        return n

    # ── 单账号操作 ──

    def _maybe_haotian_before_vcode(self, phone: str) -> None:
        """
        MT_HAOTIAN_BEFORE_VCODE=1 时：发码前跑浩天（与 mt test-full-flow initDeviceForLogin 同源），
        写入 bsid 及 deviceConfig→sdk/mfr/mdl；已具备四项则跳过，避免重复 7 步。
        """
        v = (os.environ.get("MT_HAOTIAN_BEFORE_VCODE", "") or "").strip().lower()
        if v not in ("1", "true", "yes", "on"):
            return
        e = self._map.get(phone)
        if not e or e.status == STATUS_OK:
            return
        if (
            (e.bsid or "").strip()
            and (e.sdk or "").strip()
            and (e.mfr or "").strip()
            and (e.mdl or "").strip()
            and (e.h5_user_agent or "").strip()
        ):
            return
        # 浩天 Node 用 手机号|aid 派生 15 位 device_id；新号尚未建 Session 时先保证 aid 已生成
        if not (e.aid or "").strip():
            e.ensure_session()
        try:
            r = self.fetch_bs_dvid_haotian(phone)
            if r.get("code") == 2000:
                print(f"[浩天] 发码前已获取 BS-DVID 与设备三元组 {phone}", flush=True)
            else:
                print(
                    f"[浩天] 发码前刷新未成功 {phone}: {r.get('message', r)}（仍尝试发码）",
                    flush=True,
                )
        except Exception as ex:
            print(f"[浩天] 发码前刷新异常 {phone}: {ex}（仍尝试发码）", flush=True)
        e._session = None
        e._session_proxy_src = None

    def send_code(self, phone: str) -> dict:
        phone = _norm_phone(phone)
        e = self._map.get(phone)
        if not e:
            return {"ok": False, "message": "账号不存在"}
        gap = _min_vcode_interval_sec()
        if gap > 0:
            with _vcode_gap_lock:
                now = _time.monotonic()
                prev = _vcode_last_attempt_mono.get(phone)
                if prev is not None and (now - prev) < gap:
                    w = int(gap - (now - prev)) + 1
                    return {
                        "code": 429,
                        "message": (
                            f"本程序保护：同一号码需间隔 ≥{int(gap)}s 再发码，请再等约 {w}s "
                            f"（加重试可设环境变量 MT_VCODE_MIN_INTERVAL=0 关闭）"
                        ),
                    }
        _maybe_lock_account_proxy_for_login(e)
        self._maybe_haotian_before_vcode(phone)
        s = e.ensure_session()
        res = s.send_code(phone)
        if gap > 0:
            with _vcode_gap_lock:
                _vcode_last_attempt_mono[phone] = _time.monotonic()
        if res.get("code") == 2000:
            e.status = STATUS_CODE_SENT
            self.save()
        return res

    def login(
        self,
        phone: str,
        code: str,
        nickname: Optional[str] = None,
        remark: Optional[str] = None,
    ) -> dict:
        phone = _norm_phone(phone)
        e = self._map.get(phone)
        if not e:
            return {"ok": False, "message": "账号不存在"}
        _maybe_lock_account_proxy_for_login(e)
        s = e.ensure_session()
        res = s.login(phone, code)
        if res.get("code") == 2000:
            e.status = STATUS_OK
            e.prefer_persisted_device = False
            e._sync_from_session()
            nick = str(nickname or "").strip()
            if nick:
                e.nickname = nick
            rem = str(remark or "").strip()
            if rem:
                e.remark = rem
            self.save()
        else:
            # 兜底：若本次/之前请求已在 Session 中拿到 uid+token（常见于网关二次返回 4010 验证码过期），
            # 仍按已登录回写到账号池，避免前端提示成功但主控页卡在“失败”不可操作。
            tok = str(getattr(s, "_token", "") or "").strip()
            ck = str(getattr(s, "_cookie", "") or "").strip()
            uid = str(getattr(s, "_uid", "") or "").strip()
            if tok and uid:
                if not ck:
                    ck = tok
                    try:
                        s._cookie = ck
                    except Exception:
                        pass
                e.status = STATUS_OK
                e.prefer_persisted_device = False
                e._sync_from_session()
                nick = str(nickname or "").strip()
                if nick:
                    e.nickname = nick
                rem = str(remark or "").strip()
                if rem:
                    e.remark = rem
                self.save()
                return {
                    "code": 2000,
                    "message": "登录已回写（检测到有效会话，忽略验证码过期回包）",
                    "recoveredFromSession": True,
                    "data": {
                        "token": tok,
                        "cookie": ck,
                        "userId": uid,
                    },
                }
            e.status = STATUS_FAIL
            e.prefer_persisted_device = False
            self.end_deferred_onboarding(phone)
            self.save()
        return res

    def fetch_bs_dvid_haotian(self, phone: str) -> dict:
        """
        调用 mt 同款浩天（百度 SDK）7 步流程（Node bs-dvid.js），写入本账号 bsid 并持久化。
        bsid 会出现在 H5 请求头 BS-DVID 与 WebView User-Agent 中，用于设备/风控侧标识。
        需安装 Node.js，并在 py_out/bs_dvid_node 执行过 npm install。
        """
        from bs_dvid import (
            app_ua_triple_from_haotian_result,
            fetch_bs_dvid_full,
            final_bs_dvid_token,
            h5_profile_from_haotian_result,
        )

        phone = _norm_phone(phone)
        with _haotian_lock:
            e = self._map.get(phone)
            if not e:
                return {"code": -1, "message": "账号不存在"}
            # 15 位数字设备号：手机号 + 本账号 raw_id(aid) 哈希，账号间互不相同
            digits = "".join(c for c in phone if c.isdigit())
            if not digits:
                digits = "13800138000"
            aid = (e.aid or "").strip()
            mix = hashlib.sha256(f"{digits}|{aid}".encode("utf-8")).hexdigest()
            # 写死：不再做「全库 sdk/mfr/mdl 唯一」校验，浩天 DEVICE_CONFIGS 可对多账号无限复用；
            # 仅对单次 Node 失败 / 缺 token / 缺三元组做有限次重试（与占坑无关）。
            max_try = 24
            last_res = None
            last_msg = ""
            token = ""
            picked = ("", "", "")
            for j in range(max_try):
                seed = hashlib.sha256(f"{mix}|{j}".encode("utf-8")).hexdigest()
                dev_id = str(int(seed[:15], 16) % (10**15)).zfill(15)
                res = fetch_bs_dvid_full(device_id=dev_id)
                last_res = res
                token = final_bs_dvid_token(res)
                if not token:
                    raw_token = ""
                    if isinstance(res, dict):
                        raw_token = str(
                            res.get("finalBsDvid")
                            or res.get("token")
                            or res.get("bsDvid")
                            or ""
                        ).strip()
                    last_msg = (
                        f"浩天返回的 BS-DVID 形态异常: {raw_token[:30]}"
                        if raw_token
                        else ((res or {}).get("error") or "未获取到 BS-DVID")
                    )
                    _time.sleep(0.08)
                    continue
                sv, mf, md = app_ua_triple_from_haotian_result(res)
                k = self._triple_key(sv, mf, md)
                if not k:
                    last_msg = "浩天未返回完整设备三元组(sdk/mfr/mdl)"
                    _time.sleep(0.08)
                    continue
                picked = (sv, mf, md)
                break

            if not token:
                return {"code": -1, "message": last_msg or "未获取到 BS-DVID", "haotian": last_res}
            if not all(picked):
                return {
                    "code": -1,
                    "message": (
                        f"浩天多次未返回有效结果（已重试 {max_try} 次）；"
                        "请检查 bs_dvid_node、网络或 DEVICE_CONFIGS。"
                    ),
                    "haotian": last_res,
                }
            e.sdk, e.mfr, e.mdl = picked[0], picked[1], _normalize_h5_ua_model(picked[2])
            e.bsid = token
            prof = h5_profile_from_haotian_result(last_res)
            h5_ua = _normalize_h5_ua_model_in_user_agent(prof.get("h5_user_agent") or "")
            if H5_FIXED_CHROME_VERSION:
                h5_ua = _replace_chrome_version_in_ua(h5_ua, H5_FIXED_CHROME_VERSION)
                prof["h5_user_agent"] = h5_ua
                prof["h5_chrome_version"] = H5_FIXED_CHROME_VERSION
            e.h5_user_agent = h5_ua
            e.h5_device_id = str(prof.get("h5_device_id") or "").strip()
            e.haotian_device_id = str(prof.get("haotian_device_id") or "").strip()
            e.h5_android_version = str(prof.get("h5_android_version") or "").strip()
            e.h5_device_model = _normalize_h5_ua_model(prof.get("h5_device_model") or "")
            e.h5_build_id = str(prof.get("h5_build_id") or "").strip()
            e.h5_chrome_version = str(prof.get("h5_chrome_version") or "").strip()
            # 发码前浩天若刷新了设备三元组，清空旧 H5 指纹，下一次 ensure_session 将按新三元组重建并写死。
            e.clear_h5_bb_context(clear_profile=False)
            e._session = None
            e._session_proxy_src = None
            self.save()
            return {
                "code": 2000,
                "message": "ok",
                "bsid": token,
                "deviceTriple": {"sdk": e.sdk, "mfr": e.mfr, "mdl": e.mdl},
                "h5Profile": prof,
                "haotian": last_res,
            }

    def ensure_bs_dvid_for_rush(self, phone: str) -> dict:
        """
        抢购前补全 BS-DVID：仅处理已登录账号。

        与「浩天设备池」关系：DEVICE_CONFIGS 可对多号复用，但 **BS-DVID token（bsid）仍按号独立**
        （由手机号+aid 派生 device_id 再跑 Node 7 步得到）。旧号导入、发码前浩天失败等会导致
        已登录但无 bsid，此前只能手动「发码→登录」；此处供 WebUI 在抢购前自动补一次。
        """
        phone = _norm_phone(phone)
        e = self._map.get(phone)
        if not e:
            return {"ok": False, "message": "账号不存在"}
        if e.status != STATUS_OK:
            return {"ok": False, "message": "账号未登录"}
        if (e.bsid or "").strip() and (e.h5_user_agent or "").strip():
            return {"ok": True, "skipped": True}
        r = self.fetch_bs_dvid_haotian(phone)
        if r.get("code") == 2000:
            return {"ok": True, "skipped": False, "haotian": r}
        return {
            "ok": False,
            "message": str(r.get("message") or "浩天补全 BS-DVID 失败"),
            "detail": r,
        }

    def batch_ensure_bs_dvid_missing(
        self, phones: list[str] | None = None, delay: float = 2.0
    ) -> dict:
        """
        一键补全：仅对已登录且当前无 bsid 的号依次跑浩天并落盘；已有 BS-DVID 的不动。
        phones 省略=扫描账号池全部；可传手机号列表只处理子集。
        """
        import time

        if phones is None:
            cand = list(self._phones)
        else:
            cand = [_norm_phone(p) for p in phones if _norm_phone(p)]
        try:
            delay = float(delay)
        except (TypeError, ValueError):
            delay = 2.0
        delay = max(0.0, min(120.0, delay))

        skipped_had_bsid = 0
        skipped_not_logged = 0
        skipped_unknown = 0
        missing: list[str] = []
        for ph in cand:
            e = self._map.get(ph)
            if not e:
                skipped_unknown += 1
                continue
            if e.status != STATUS_OK:
                skipped_not_logged += 1
                continue
            if (e.bsid or "").strip() and (e.h5_user_agent or "").strip():
                skipped_had_bsid += 1
                continue
            missing.append(ph)

        out: list[dict] = []
        for i, phone in enumerate(missing):
            r = self.fetch_bs_dvid_haotian(phone)
            out.append({"phone": phone, **r})
            if i < len(missing) - 1 and delay > 0:
                time.sleep(delay)
        ok_n = sum(1 for row in out if row.get("code") == 2000)
        return {
            "results": out,
            "picked": len(missing),
            "filledOk": ok_n,
            "filledFail": len(out) - ok_n,
            "skippedHadBsid": skipped_had_bsid,
            "skippedNotLogged": skipped_not_logged,
            "skippedUnknownPhone": skipped_unknown,
            "stats": self.stats(),
        }

    def batch_warmup_bb(
        self,
        phones: list[str] | None = None,
        *,
        max_workers: int = 64,
        force: bool = False,
        ttl_ms: int = 60_000,
        page_url: str = "https://h5.moutai519.com.cn/",
    ) -> dict:
        """一键梆梆暖场上报：每账号用各自 _proxy_url 并发 POST /bbprbdata/upload。

        ip 已按账号隔离（ensure_session 内部分配），可放心并发。
        失败不阻塞其他账号。返回逐账号结果摘要。
        """
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if phones is None:
            cand = [p for p in self._phones if self._map[p].status == STATUS_OK]
        else:
            cand = [_norm_phone(p) for p in phones if _norm_phone(p)]
            cand = [p for p in cand if p in self._map and self._map[p].status == STATUS_OK]

        started_at = int(time.time() * 1000)
        results: list[dict] = []
        if not cand:
            return {
                "results": [],
                "picked": 0,
                "ok_count": 0,
                "fail_count": 0,
                "skipped_count": 0,
                "started_at": started_at,
                "finished_at": started_at,
                "elapsed_ms": 0,
            }

        max_workers = max(1, min(int(max_workers or 64), max(1, len(cand))))

        def _one(phone: str) -> dict:
            e = self._map.get(phone)
            if not e:
                return {"phone": phone, "ok": False, "skipped": False, "message": "账号不存在"}
            try:
                return e.warmup_bb_if_stale(force=force, ttl_ms=ttl_ms, page_url=page_url)
            except Exception as ex:
                return {"phone": phone, "ok": False, "skipped": False, "message": f"线程异常：{ex}"}

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_one, ph): ph for ph in cand}
            for f in as_completed(futs):
                try:
                    results.append(f.result())
                except Exception as e2:
                    results.append({
                        "phone": futs[f],
                        "ok": False,
                        "skipped": False,
                        "message": f"future 异常：{e2}",
                    })

        finished_at = int(time.time() * 1000)
        ok_n = sum(1 for r in results if r.get("ok") and not r.get("skipped"))
        skip_n = sum(1 for r in results if r.get("skipped"))
        fail_n = sum(1 for r in results if not r.get("ok"))
        return {
            "results": results,
            "picked": len(cand),
            "ok_count": ok_n,
            "fail_count": fail_n,
            "skipped_count": skip_n,
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_ms": finished_at - started_at,
        }

    def batch_fetch_bs_dvid_haotian(
        self, phones: list[str] | None = None, delay: float = 2.0
    ) -> list[dict]:
        """对已登录账号依次刷新浩天（避免并发 Node）。phones 为空则处理全部已登录。"""
        import time

        if phones is None:
            targets = [p for p in self._phones if self._map[p].status == STATUS_OK]
        else:
            targets = [_norm_phone(p) for p in phones if _norm_phone(p)]
        out: list[dict] = []
        for i, phone in enumerate(targets):
            e = self._map.get(phone)
            if not e or e.status != STATUS_OK:
                out.append({"phone": phone, "code": -1, "message": "跳过（未登录或不存在）"})
                continue
            r = self.fetch_bs_dvid_haotian(phone)
            out.append({"phone": phone, **r})
            if i < len(targets) - 1 and delay > 0:
                time.sleep(delay)
        return out

    def batch_refresh_login(
        self,
        phones: list[str] | None = None,
        delay: float = 4.0,
    ) -> list[dict]:
        """
        对已登录账号：用 ship/query 校验收货地址与会话。
        不换机模：prefer_persisted_device 关闭，Session 按 JWT 内 deviceId 与 Token 对齐，
        与茅台 ianus-token-auth 一致，避免 4011 device id inconsistency。

        delay：号间间隔，建议 ≥4s，减轻 429。
        """
        import time

        if phones is None:
            targets = [p for p in self._phones if self._map[p].status == STATUS_OK]
        else:
            targets = [_norm_phone(p) for p in phones if _norm_phone(p)]
        out: list[dict] = []
        for i, phone in enumerate(targets):
            e = self._map.get(phone)
            if not e:
                out.append({"phone": phone, "ok": False, "message": "账号不存在"})
                continue
            if e.status != STATUS_OK or not (e.token or "").strip():
                out.append({"phone": phone, "ok": False, "message": "跳过（未登录或无 token）"})
                continue
            try:
                e.prefer_persisted_device = False
                e = self._map.get(phone)
                e._session = None
                e._session_proxy_src = None
                s = e.ensure_session()
                _lst, raw = s.addresses_detail()
                code = raw.get("code") if isinstance(raw, dict) else None
                ok = code == 2000
                msg = ""
                if isinstance(raw, dict):
                    msg = str(raw.get("message") or "")
                if ok:
                    e._sync_from_session()
                    self.save()
                    try:
                        import address_db

                        if (
                            address_db.enabled()
                            and isinstance(_lst, list)
                            and _lst
                        ):
                            address_db.save_address_list_for_phone(phone, _lst)
                    except Exception:
                        pass
                else:
                    e.prefer_persisted_device = False
                    e._session = None
                    e._session_proxy_src = None
                    self.save()
                    msg = msg or (str(raw)[:200] if raw else "校验失败")
                ok_msg = "会话校验通过（设备与 Token/JWT 一致；未换机模）"
                out.append({
                    "phone": phone,
                    "ok": ok,
                    "code": code,
                    "message": (
                        (msg[:300] if msg else ("ok" if ok else "校验失败"))
                        if not ok
                        else ok_msg
                    ),
                })
            except Exception as ex:
                try:
                    e2 = self._map.get(phone)
                    if e2:
                        e2.prefer_persisted_device = False
                        e2._session = None
                        e2._session_proxy_src = None
                        self.save()
                except Exception:
                    pass
                out.append({"phone": phone, "ok": False, "message": str(ex)[:300]})
            if i < len(targets) - 1 and delay > 0:
                time.sleep(delay + random.uniform(0, 0.75))
        return out

    # ── 批量操作 ──

    def batch_send_code(self, phones: list[str] | None = None,
                        delay: float = 0.5) -> list[dict]:
        """
        对多个（或全部）账号发送验证码。
        delay: 每次请求间隔（秒），避免请求过快
        """
        import time
        targets = phones or self._phones
        results = []
        for phone in targets:
            res = self.send_code(phone)
            results.append({"phone": phone, "result": res})
            time.sleep(delay)
        return results

    def batch_login(self, code_map: dict[str, str]) -> list[dict]:
        """
        批量登录。code_map = {phone: sms_code}
        """
        results = []
        for phone, code in code_map.items():
            ph = _norm_phone(phone)
            res = self.login(ph, code)
            results.append({"phone": ph, "result": res,
                             "status": self._map[ph].status if ph in self._map else "unknown"})
        return results

    def batch_black_check(
        self,
        phones: list[str] | None = None,
        max_retries: int = 50,
        delay: float = 2.0,
    ) -> list[dict]:
        """
        批量黑号检测：仅处理已登录账号；结果写入 risk_tag / risk_message / risk_checked_at 并落盘。
        规则与 black_account_probe 一致（741 + branch/one；活动 ID 由 purchaseInfoV2 动态拉取）。
        """
        if phones is None:
            targets = [p for p in self._phones if self._map[p].status == STATUS_OK]
        else:
            targets = [_norm_phone(p) for p in phones if _norm_phone(p)]
        try:
            mr = max(1, int(max_retries))
        except (TypeError, ValueError):
            mr = 50
        try:
            dl = max(0.0, float(delay))
        except (TypeError, ValueError):
            dl = 2.0
        out: list[dict] = []
        for i, phone in enumerate(targets):
            e = self._map.get(phone)
            if not e:
                out.append({"phone": phone, "skipped": True, "message": "账号不存在"})
                continue
            if e.status != STATUS_OK:
                out.append({
                    "phone": phone,
                    "skipped": True,
                    "message": f"非已登录状态: {e.status}",
                })
                continue
            try:
                s = e.ensure_session()
                probe = s.black_account_probe(max_retries=mr)
            except Exception as ex:
                probe = {
                    "success": False,
                    "status": "probe_error",
                    "message": str(ex),
                }
            tag = str(probe.get("status") or "unknown")
            e.risk_tag = tag
            e.risk_message = str(probe.get("message") or "")
            e.risk_checked_at = risk_log_timestamp()
            self.save()
            out.append({"phone": phone, "risk_tag": tag, **probe})
            if i < len(targets) - 1 and dl > 0:
                _time.sleep(dl)
        return out

    # ── 统计 ──

    def stats(self) -> dict:
        total  = len(self._phones)
        ok     = sum(1 for p in self._phones if self._map[p].status == STATUS_OK)
        fail   = sum(1 for p in self._phones if self._map[p].status == STATUS_FAIL)
        sent   = sum(1 for p in self._phones if self._map[p].status == STATUS_CODE_SENT)
        pend   = total - ok - fail - sent
        return {"total": total, "logged_in": ok, "code_sent": sent,
                "failed": fail, "pending": pend}


# 全局单例
POOL = AccountPool()
