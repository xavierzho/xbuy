from __future__ import annotations

import copy
import json
import time
from typing import Any

from bangcle.constants import (
    DEFAULT_BB_APP_KEY,
    DEVICE_INFO_STORAGE_TTL_MS,
    GEN_F_TOKEN_TTL_MS,
    REMOTE_CONFIG_STORAGE_TTL_MS,
    SIGN_WASM_OK_STORAGE_KEY,
    SIGN_WASM_STORAGE_KEY,
    SIGN_WASM_STORAGE_TTL_MS,
    TOKEN_STORAGE_TTL_MS,
    WASM_SIGN_VERSION_KEY,
)
from bangcle.crypto_bb import process_data, sm4_decrypt_json_value, sm4_encrypt_json_value
from bangcle.sdk import object_to_array

DEFAULT_STORAGE_TTL_MS = 86_400_000
PERSISTENT_STORAGE_TTL_MS = 259_200_000_000


def _normalize_ttl(ttl_ms: int | None) -> int:
    if ttl_ms is None:
        return DEFAULT_STORAGE_TTL_MS
    try:
        parsed = int(ttl_ms)
    except (TypeError, ValueError):
        return DEFAULT_STORAGE_TTL_MS
    return parsed if parsed else PERSISTENT_STORAGE_TTL_MS


def _wrap(
    storage_key: str, value: Any, ttl_ms: int, app_key: str, *, now_ms: int | None = None
) -> str:
    base = int(now_ms if now_ms is not None else time.time() * 1000)
    expired_at = base + _normalize_ttl(ttl_ms)
    if storage_key in ("h_five_event", SIGN_WASM_STORAGE_KEY):
        inner = value
    else:
        inner = sm4_encrypt_json_value(value, app_key)
    return json.dumps({"expiredAt": expired_at, "value": inner}, ensure_ascii=False, separators=(",", ":"))


def _short_ua(ua: str) -> str:
    s = str(ua or "")
    idx = s.find(")")
    if idx > 0:
        return s[:idx]
    return s[:50]


def build_du_cookie_value(ctx: dict, *, now_ms: int, user_agent: str, page_domain: str) -> str:
    payload = {
        "101": ctx["sdkConfig"]["app_key"],
        "103": ctx.get("did") or "",
        "105": ctx.get("startId") or "",
        "107": _short_ua(user_agent or ctx.get("userAgent") or ""),
        "109": page_domain or ctx.get("pageDomain") or "",
        "111": ctx.get("hp") or "",
        "113": now_ms,
    }
    return process_data(ctx, object_to_array(payload))


def build_wrapped_state(ctx: dict, *, now_ms: int, page_domain: str, user_agent: str, h_c: str = "1") -> dict:
    app_key = str(ctx["sdkConfig"].get("app_key") or DEFAULT_BB_APP_KEY)
    local: dict[str, str] = {}

    if ctx.get("did"):
        local["__ud"] = _wrap("__ud", ctx["did"], 0, app_key, now_ms=now_ms)
    if ctx.get("hp"):
        local["__mg__"] = _wrap("__mg__", ctx["hp"], GEN_F_TOKEN_TTL_MS, app_key, now_ms=now_ms)
    if ctx.get("tokenPrepetuity"):
        local["__rk"] = _wrap("__rk", ctx["tokenPrepetuity"], 0, app_key, now_ms=now_ms)
    if ctx.get("uaid"):
        local["__idua_"] = _wrap("__idua_", ctx["uaid"], 0, app_key, now_ms=now_ms)
    if ctx.get("jsid"):
        local["__idjs_"] = _wrap("__idjs_", ctx["jsid"], 0, app_key, now_ms=now_ms)
    if ctx.get("token"):
        local["__tok_"] = _wrap("__tok_", ctx["token"], TOKEN_STORAGE_TTL_MS, app_key, now_ms=now_ms)
    if ctx.get("signWasmVersion"):
        local[WASM_SIGN_VERSION_KEY] = _wrap(
            WASM_SIGN_VERSION_KEY,
            ctx["signWasmVersion"],
            SIGN_WASM_STORAGE_TTL_MS,
            app_key,
            now_ms=now_ms,
        )
    if ctx.get("signWasmOk") is not None:
        local[SIGN_WASM_OK_STORAGE_KEY] = _wrap(
            SIGN_WASM_OK_STORAGE_KEY,
            1 if ctx.get("signWasmOk") else 0,
            SIGN_WASM_STORAGE_TTL_MS,
            app_key,
            now_ms=now_ms,
        )
    b64 = str(ctx.get("signWasmBase64") or "").strip()
    if b64:
        local[SIGN_WASM_STORAGE_KEY] = _wrap(
            SIGN_WASM_STORAGE_KEY, b64, SIGN_WASM_STORAGE_TTL_MS, app_key, now_ms=now_ms
        )
    if ctx.get("sdkVersion"):
        local["_bb_v_"] = _wrap("_bb_v_", ctx["sdkVersion"], None, app_key, now_ms=now_ms)
    if ctx.get("serial") is not None:
        local["h_five_serial"] = _wrap(
            "h_five_serial", int(ctx["serial"]), 0, app_key, now_ms=now_ms
        )
    if isinstance(ctx.get("deviceInfo"), dict) and ctx["deviceInfo"]:
        local["h_five_cp"] = _wrap(
            "h_five_cp", ctx["deviceInfo"], DEVICE_INFO_STORAGE_TTL_MS, app_key, now_ms=now_ms
        )
    if isinstance(ctx.get("remoteTask"), list):
        local["h_five_co"] = _wrap(
            "h_five_co",
            [
                copy.deepcopy(ctx["remoteTask"]),
                int(ctx.get("timeDelta") or ctx["sdkConfig"].get("td") or 0),
                int(ctx.get("tacticsVersion") or 0),
            ],
            REMOTE_CONFIG_STORAGE_TTL_MS,
            app_key,
            now_ms=now_ms,
        )
    if ctx.get("tps") is not None:
        local["__tps"] = _wrap("__tps", int(ctx["tps"]), 0, app_key, now_ms=now_ms)

    session: dict[str, Any] = {}
    if ctx.get("startId"):
        session["__36_i__"] = ctx["startId"]
    rk = str(ctx.get("tokenPrepetuity") or "")
    if rk:
        session["__st__"] = rk[12:18] if len(rk) >= 18 else ""
    session["h_c"] = h_c

    cookie_state = {
        "_sdk_v_": ctx.get("sdkVersion") or "",
        "_bs_device_id": ctx.get("hp") or "",
        "_d_u": build_du_cookie_value(ctx, now_ms=now_ms, user_agent=user_agent, page_domain=page_domain),
    }
    return {"local": local, "session": session, "cookieState": cookie_state}
