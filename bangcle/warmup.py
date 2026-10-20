from __future__ import annotations

import base64
import copy
import json
import time
from typing import Any
import requests

from bangcle.constants import (
    CONTENT_INFO_BB_HEADER,
    DEFAULT_BB_HOST,
    DEFAULT_UPLOAD_PATH,
    DEFAULT_WASM_DOWNLOAD_PATH,
    DEFAULT_WASM_RUNTIME_FALLBACK_HOST,
    DEFAULT_WASM_VERSION_LIST_PATH,
    H5_ORIGIN,
    LIVE_BB_REMOTE_TASK,
    LIVE_BB_STRATEGY_VERSION,
    LIVE_BB_WASM_VERSION,
    WARMUP_ALG_TAG,
)
from bangcle.context import (
    apply_remote_strategy,
    create_bb_context,
    generate_bs_device_id,
    get_start_id,
    strip_query_and_hash,
)
from bangcle.crypto_bb import process_data
from bangcle.wrapped_state import build_wrapped_state
from project_versions import BB_SIGN_WASM
from bangcle.sdk import SDK_VERSION, load_sign_wasm_bytes, node_random_id


def _requests_proxies(proxy_url: str | None) -> dict[str, str] | None:
    u = str(proxy_url or "").strip()
    if not u:
        return None
    return {"http": u, "https": u}


def _warmup_headers(user_agent: str, referer: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Connection": "keep-alive",
        "Accept-Encoding": "gzip, deflate",
        "Origin": H5_ORIGIN,
        "X-Requested-With": "com.moutai.mall",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": referer or f"{H5_ORIGIN}/",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }


def _post_headers(base: dict[str, str], url: str, body: str) -> dict[str, str]:
    from urllib.parse import urlparse

    headers = {**base, "Content-Type": "text/plain;charset=UTF-8"}
    try:
        headers["Host"] = urlparse(url).netloc
    except Exception:
        pass
    headers["Content-Length"] = str(len(body.encode("utf-8")))
    return headers


def _upload_url(ctx: dict) -> str:
    host = str(ctx.get("hostAd") or DEFAULT_BB_HOST).rstrip("/")
    return f"{host}{DEFAULT_UPLOAD_PATH}"


def _wasm_host(ctx: dict) -> str:
    host = str(ctx.get("hostWasm") or ctx.get("hostAd") or DEFAULT_WASM_RUNTIME_FALLBACK_HOST)
    return host.rstrip("/")


def _extract_wasm_versions(payload: Any) -> dict:
    parsed = payload
    if isinstance(payload, (bytes, bytearray)):
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except Exception:
            parsed = None
    elif isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = None
    if not isinstance(parsed, dict):
        return {"versions": [], "signWasmOk": False, "raw": parsed}
    versions = []
    if isinstance(parsed.get("v"), list):
        for entry in parsed["v"]:
            if isinstance(entry, str):
                versions.append(entry)
            elif isinstance(entry, dict):
                versions.append(str(entry.get("v") or entry.get("version") or ""))
    versions = [v for v in versions if v]
    return {"versions": versions, "signWasmOk": parsed.get("w") is not False, "raw": parsed}


def _extract_wasm_download(payload: Any) -> dict:
    parsed = payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = None
    if not isinstance(parsed, dict):
        return {"ok": False, "unavailableReason": "empty payload", "version": "", "codeBase64": ""}
    if parsed.get("code") == 0 and parsed.get("msg"):
        return {
            "ok": False,
            "unavailableReason": str(parsed.get("msg")),
            "version": str(parsed.get("v") or parsed.get("version") or ""),
            "codeBase64": "",
        }
    version = str(parsed.get("v") or parsed.get("version") or "")
    code_b64 = str(parsed.get("code") or "").strip() if isinstance(parsed.get("code"), str) else ""
    if not version or not code_b64:
        return {"ok": False, "unavailableReason": "code payload empty", "version": version, "codeBase64": code_b64}
    return {"ok": True, "version": version, "codeBase64": code_b64, "unavailableReason": ""}


def _create_session(input_data: dict, page_domain: str, now_ms: int) -> dict:
    state = input_data.get("bbState") if isinstance(input_data.get("bbState"), dict) else {}
    has_sign = BB_SIGN_WASM.exists()
    disk_b64 = ""
    if has_sign:
        try:
            disk_b64 = base64.b64encode(load_sign_wasm_bytes()).decode("ascii")
        except Exception:
            disk_b64 = ""
    state_ver = str(state.get("bbSignWasmVersion") or "")
    state_b64 = str(state.get("bbSignWasmBase64") or "") if state_ver == LIVE_BB_WASM_VERSION else ""
    sign_b64 = disk_b64 or state_b64
    requested_did = str(input_data.get("bbDid") or "").strip()
    state_did = str(state.get("bbDid") or "").strip()
    did = requested_did or state_did or f"h{node_random_id(now_ms)[1:]}"
    state_matches = bool(state_did) and state_did == did
    state_hp = (
        str(state.get("bbDeviceCookie") or state.get("bbCookieState", {}).get("_bs_device_id") or "")
        if state_matches
        else ""
    )
    return create_bb_context(
        {
            "did": did,
            "startId": state.get("bbStartId") or get_start_id(now_ms),
            "userId": str(input_data.get("uid") or state.get("userid") or ""),
            "userAgent": str(input_data.get("userAgent") or ""),
            "hp": state_hp or generate_bs_device_id(did, now_ms),
            "uaid": state.get("bbUaid") or "",
            "jsid": state.get("bbJsid") or "",
            "token": state.get("bbToken") or "",
            "tokenPrepetuity": state.get("bbRk") or node_random_id(now_ms + 1),
            "signWasmVersion": LIVE_BB_WASM_VERSION,
            "signWasmOk": bool(sign_b64),
            "signWasmBase64": sign_b64,
            "sdkVersion": state.get("bbSdkVersion") or SDK_VERSION,
            "pageDomain": page_domain,
            "serial": int(state.get("bbSerial") or state.get("bbTps") or 1),
            "tps": int(state.get("bbTps") or state.get("bbSerial") or 1),
            "tacticsVersion": int(state.get("bbTacticsVersion") or 0),
            "timeDelta": int(state.get("bbTimeDelta") or 0),
            "remoteTask": copy.deepcopy(state.get("bbRemoteTask")),
            "deviceInfo": copy.deepcopy(state.get("bbDeviceInfo")),
            "now": now_ms,
        }
    )


def _build_output_state(session: dict, input_data: dict, page_domain: str, now_ms: int, flags: dict) -> dict:
    wrapped = build_wrapped_state(
        session,
        now_ms=now_ms,
        page_domain=page_domain,
        user_agent=str(input_data.get("userAgent") or ""),
        h_c="1",
    )
    prev = input_data.get("bbState") if isinstance(input_data.get("bbState"), dict) else {}
    return {
        "bbDid": session.get("did") or "",
        "bbStartId": session.get("startId") or "",
        "bbDeviceCookie": session.get("hp") or "",
        "bbUaid": session.get("uaid") or "",
        "bbJsid": session.get("jsid") or "",
        "bbToken": session.get("token") or "",
        "bbRk": session.get("tokenPrepetuity") or "",
        "bbSignWasmBase64": session.get("signWasmBase64") or "",
        "bbSignWasmVersion": session.get("signWasmVersion") or LIVE_BB_WASM_VERSION,
        "bbSignWasmOk": bool(session.get("signWasmOk")),
        "bbSdkVersion": session.get("sdkVersion") or SDK_VERSION,
        "bbPageDomain": page_domain,
        "bbSerial": int(session.get("serial") or 1),
        "bbTps": int(session.get("tps") or session.get("serial") or 1),
        "bbTimeDelta": int(session.get("timeDelta") or 0),
        "bbTacticsVersion": int(session.get("tacticsVersion") or 0),
        "bbRemoteTask": copy.deepcopy(session.get("remoteTask")),
        "bbDeviceInfo": copy.deepcopy(session.get("deviceInfo")),
        "bbLocalStorage": wrapped["local"],
        "bbSessionStorage": wrapped["session"],
        "bbCookieState": wrapped["cookieState"],
        "__bbWarmupAlg": WARMUP_ALG_TAG,
        "__bbWarmupAt": now_ms,
        "__bbStrategyAt": now_ms if flags.get("strategyApplied") else int(prev.get("__bbStrategyAt") or 0),
        "__bbWasmFetched": bool(flags.get("wasmFetched") or flags.get("localWasm") or prev.get("__bbWasmFetched")),
        "__bbWasmFetchedAt": now_ms
        if (flags.get("wasmFetched") or flags.get("localWasm"))
        else int(prev.get("__bbWasmFetchedAt") or 0),
        "__bbWasmUnavailableReason": str(flags.get("wasmUnavailableReason") or ""),
    }


def run_bangcle_warmup(input_data: dict | None = None, *, timeout: float = 20.0) -> dict:
    input_data = dict(input_data or {})
    now_ms = int(input_data.get("now") or time.time() * 1000)
    page_domain = strip_query_and_hash(
        input_data.get("referer") or input_data.get("pageDomain") or f"{H5_ORIGIN}/"
    ) or f"{H5_ORIGIN}/"
    referer = str(input_data.get("referer") or page_domain or f"{H5_ORIGIN}/")
    timeout_ms = max(1000, min(30000, int(input_data.get("timeoutMs") or 12000)))
    req_timeout = min(timeout, timeout_ms / 1000.0 + 0.5)
    proxies = _requests_proxies(input_data.get("proxyUrl"))
    headers = _warmup_headers(str(input_data.get("userAgent") or ""), referer)
    session = _create_session(input_data, page_domain, now_ms)
    flags: dict[str, Any] = {
        "strategyApplied": False,
        "wasmFetched": False,
        "localWasm": BB_SIGN_WASM.exists(),
        "wasmUnavailableReason": "",
    }

    strategy_status = 0
    try:
        started = int(time.time() * 1000)
        strategy_url = _upload_url(session)
        strategy_body = process_data(session, session["sdkConfig"]["app_key"])
        resp = requests.post(
            strategy_url,
            data=strategy_body,
            headers=_post_headers(headers, strategy_url, strategy_body),
            timeout=req_timeout,
            proxies=proxies,
            verify=False,
        )
        strategy_status = resp.status_code
        if 200 <= resp.status_code < 300 and resp.content:
            try:
                strategy_data: Any = resp.json()
            except Exception:
                strategy_data = resp.text
            apply_remote_strategy(session, strategy_data, started, int(time.time() * 1000))
            flags["strategyApplied"] = True
        else:
            apply_remote_strategy(
                session,
                [copy.deepcopy(LIVE_BB_REMOTE_TASK), now_ms, LIVE_BB_STRATEGY_VERSION],
                now_ms,
                now_ms,
            )
    except Exception as ex:
        apply_remote_strategy(
            session,
            [copy.deepcopy(LIVE_BB_REMOTE_TASK), now_ms, LIVE_BB_STRATEGY_VERSION],
            now_ms,
            now_ms,
        )
        flags["strategyError"] = str(ex)

    version_url = f"{_wasm_host(session)}{DEFAULT_WASM_VERSION_LIST_PATH}"
    info_payload = process_data(session, [session.get("did") or "", int(time.time() * 1000)])
    version_resp = requests.get(
        version_url,
        headers={**headers, CONTENT_INFO_BB_HEADER: info_payload},
        timeout=req_timeout,
        proxies=proxies,
        verify=False,
    )
    version_info = _extract_wasm_versions(version_resp.content)
    session["signWasmVersion"] = LIVE_BB_WASM_VERSION
    if version_info.get("raw") and (
        not version_info.get("versions") or LIVE_BB_WASM_VERSION in version_info["versions"]
    ):
        session["signWasmOk"] = bool(session.get("signWasmBase64")) and version_info.get("signWasmOk") is not False

    prev_state = input_data.get("bbState") if isinstance(input_data.get("bbState"), dict) else {}
    if (not prev_state.get("__bbWasmFetched") and not input_data.get("forceWasm")) and not flags["localWasm"]:
        wasm_url = f"{_wasm_host(session)}{DEFAULT_WASM_DOWNLOAD_PATH}"
        dl_headers = {
            **headers,
            CONTENT_INFO_BB_HEADER: process_data(session, [session.get("did") or "", int(time.time() * 1000)]),
        }
        wasm_resp = requests.get(
            wasm_url,
            headers=dl_headers,
            timeout=req_timeout,
            proxies=proxies,
            verify=False,
        )
        if 200 <= wasm_resp.status_code < 300:
            wasm_info = _extract_wasm_download(wasm_resp.text)
            if wasm_info.get("ok") and wasm_info.get("codeBase64"):
                session["signWasmVersion"] = wasm_info.get("version") or LIVE_BB_WASM_VERSION
                session["signWasmBase64"] = wasm_info["codeBase64"]
                session["signWasmOk"] = True
                flags["wasmFetched"] = True
            else:
                flags["wasmUnavailableReason"] = wasm_info.get("unavailableReason") or "unexpected sign wasm"
        else:
            flags["wasmUnavailableReason"] = f"HTTP {wasm_resp.status_code}"

    serial = int(session.get("serial") or 1)
    upload_now = int(time.time() * 1000)
    dev_info = session.get("deviceInfo")
    upload_payload = {
        "001": str(input_data.get("uid") or session.get("userId") or ""),
        "002": session["sdkConfig"]["app_key"],
        "003": session.get("did") or "",
        "004": session.get("startId") or "",
        "005": session.get("tokenPrepetuity") or "",
        "006": upload_now,
        "007": serial,
        "008": session.get("sdkVersion") or SDK_VERSION,
        "009": int(session.get("tacticsVersion") or 0),
        "010": int(
            session["timeDelta"]
            if session.get("timeDelta") is not None
            else session["sdkConfig"].get("td", -1)
        ),
        "101": dev_info if isinstance(dev_info, dict) else None,
        "102": [],
    }
    from bangcle.sdk import object_to_array_1_9_6

    upload_body = process_data(session, object_to_array_1_9_6(upload_payload))
    upload_url = _upload_url(session)
    upload_resp = requests.post(
        upload_url,
        data=upload_body,
        headers=_post_headers(headers, upload_url, upload_body),
        timeout=req_timeout,
        proxies=proxies,
        verify=False,
    )
    if 200 <= upload_resp.status_code < 300:
        session["serial"] = serial + 1
        session["tps"] = serial + 1

    state = _build_output_state(session, input_data, page_domain, now_ms, flags)
    return {
        "success": True,
        "skipped": False,
        "state": state,
        "backend": "python",
        "summary": {
            "strategyStatus": strategy_status,
            "versionStatus": int(version_resp.status_code),
            "uploadStatus": int(upload_resp.status_code),
            "wasmFetched": bool(flags.get("wasmFetched")),
            "localWasm": bool(flags.get("localWasm")),
            "strategyApplied": bool(flags.get("strategyApplied")),
            "wasmUnavailableReason": str(flags.get("wasmUnavailableReason") or ""),
        },
    }
