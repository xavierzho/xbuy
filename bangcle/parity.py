from __future__ import annotations

import base64
import copy
from typing import Any

from bangcle.constants import (
    DEFAULT_BB_HOST,
    DEFAULT_UPLOAD_PATH,
    DEFAULT_WASM_VERSION_LIST_PATH,
    H5_ORIGIN,
    LIVE_BB_REMOTE_TASK,
    LIVE_BB_STRATEGY_VERSION,
    LIVE_BB_WASM_VERSION,
    WARMUP_ALG_TAG,
)
from bangcle.context import apply_remote_strategy, create_bb_context, strip_query_and_hash
from bangcle.crypto_bb import process_data
from bangcle.sdk import SDK_VERSION, load_sign_wasm_bytes, object_to_array
from bangcle.warmup import extract_wasm_versions_parsed
from bangcle.wrapped_state import build_wrapped_state
from parity.context import parity_context_from_fixture
from project_versions import BB_SIGN_WASM


def _wasm_host(ctx: dict) -> str:
    host = str(ctx.get("hostWasm") or ctx.get("hostAd") or DEFAULT_BB_HOST)
    return host.rstrip("/")


def _upload_url(ctx: dict) -> str:
    host = str(ctx.get("hostAd") or DEFAULT_BB_HOST).rstrip("/")
    return f"{host}{DEFAULT_UPLOAD_PATH}"


def _create_parity_session(fixture: dict, page_domain: str, base_now: int, rng) -> dict:
    state = fixture.get("bbState") if isinstance(fixture.get("bbState"), dict) else {}
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
    requested_did = str(fixture.get("bbDid") or "").strip()
    state_did = str(state.get("bbDid") or "").strip()
    from bangcle.sdk import node_random_id

    did = requested_did or state_did or f"h{node_random_id(base_now, random_func=rng.random)[1:]}"
    state_matches = bool(state_did) and state_did == did
    state_hp = (
        str(state.get("bbDeviceCookie") or state.get("bbCookieState", {}).get("_bs_device_id") or "")
        if state_matches
        else ""
    )
    from bangcle.context import generate_bs_device_id, get_start_id

    start_id = (
        str(state.get("bbStartId") or fixture.get("bbStartId") or "")
        or get_start_id(base_now, random_source=fixture.get("randomSource"))
    )
    hp = state_hp or str(fixture.get("bbDeviceCookie") or "") or generate_bs_device_id(did, base_now)
    rk = str(state.get("bbRk") or fixture.get("bbRk") or node_random_id(base_now + 1, random_func=rng.random))
    ctx = create_bb_context(
        {
            "did": did,
            "startId": start_id,
            "userId": str(fixture.get("uid") or state.get("userid") or ""),
            "userAgent": str(fixture.get("userAgent") or ""),
            "hp": hp,
            "uaid": state.get("bbUaid") or "",
            "jsid": state.get("bbJsid") or "",
            "token": state.get("bbToken") or "",
            "tokenPrepetuity": rk,
            "signWasmVersion": LIVE_BB_WASM_VERSION,
            "signWasmOk": bool(sign_b64),
            "signWasmBase64": sign_b64,
            "sdkVersion": state.get("bbSdkVersion") or SDK_VERSION,
            "pageDomain": page_domain,
            "serial": int(state.get("bbSerial") or 1),
            "tps": int(state.get("bbTps") or state.get("bbSerial") or 1),
            "tacticsVersion": int(state.get("bbTacticsVersion") or 0),
            "timeDelta": int(state.get("bbTimeDelta") or 0),
            "remoteTask": copy.deepcopy(state.get("bbRemoteTask")),
            "deviceInfo": copy.deepcopy(state.get("bbDeviceInfo")),
            "now": base_now,
        }
    )
    ctx["_parity_random"] = rng.random
    return ctx


def _build_output_state(
    session: dict, fixture: dict, page_domain: str, base_now: int, flags: dict
) -> dict:
    wrapped = build_wrapped_state(
        session,
        now_ms=base_now,
        page_domain=page_domain,
        user_agent=str(fixture.get("userAgent") or ""),
        h_c="1",
    )
    prev = fixture.get("bbState") if isinstance(fixture.get("bbState"), dict) else {}
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
        "__bbWarmupAt": base_now,
        "__bbStrategyAt": base_now if flags.get("strategyApplied") else int(prev.get("__bbStrategyAt") or 0),
        "__bbWasmFetched": bool(
            flags.get("wasmFetched") or flags.get("localWasm") or prev.get("__bbWasmFetched")
        ),
        "__bbWasmFetchedAt": base_now
        if (flags.get("wasmFetched") or flags.get("localWasm"))
        else int(prev.get("__bbWasmFetchedAt") or 0),
        "__bbWasmUnavailableReason": str(flags.get("wasmUnavailableReason") or ""),
    }


def build_bangcle_warmup_parity_artifacts(fixture: dict | None = None) -> dict:
    fixture = dict(fixture or {})
    rng = parity_context_from_fixture(fixture)
    base_now = int(fixture.get("now") or fixture.get("nowMs") or 1_700_000_000_123)
    offsets = fixture.get("clockOffsetsMs") if isinstance(fixture.get("clockOffsetsMs"), dict) else {}

    page_domain = (
        strip_query_and_hash(
            fixture.get("referer") or fixture.get("pageDomain") or f"{H5_ORIGIN}/"
        )
        or f"{H5_ORIGIN}/"
    )
    session = _create_parity_session(fixture, page_domain, base_now, rng)
    flags: dict[str, Any] = {
        "strategyApplied": False,
        "wasmFetched": False,
        "localWasm": BB_SIGN_WASM.exists(),
        "wasmUnavailableReason": "",
    }

    strategy_started = base_now + int(offsets.get("strategyStarted") or 0)
    strategy_response_at = base_now + int(offsets.get("strategyResponse") or 50)
    strategy_url = _upload_url(session)
    strategy_body = process_data(session, session["sdkConfig"]["app_key"])
    strategy_payload = fixture.get("strategyResponse")
    if strategy_payload is None:
        strategy_payload = [copy.deepcopy(LIVE_BB_REMOTE_TASK), base_now, LIVE_BB_STRATEGY_VERSION]
    apply_remote_strategy(session, strategy_payload, strategy_started, strategy_response_at)
    flags["strategyApplied"] = True

    version_now = base_now + int(offsets.get("versionInfo") or 100)
    version_url = f"{_wasm_host(session)}{DEFAULT_WASM_VERSION_LIST_PATH}"
    version_header = process_data(session, [session.get("did") or "", version_now])
    version_payload = fixture.get("versionResponse") or {"v": [LIVE_BB_WASM_VERSION], "w": True}
    version_info = extract_wasm_versions_parsed(version_payload)
    session["signWasmVersion"] = LIVE_BB_WASM_VERSION
    if version_info.get("raw") and LIVE_BB_WASM_VERSION in version_info["versions"]:
        session["signWasmOk"] = bool(session.get("signWasmBase64")) and version_info.get("signWasmOk") is not False

    prev_state = fixture.get("bbState") if isinstance(fixture.get("bbState"), dict) else {}
    if (
        not fixture.get("skipWasmDownload")
        and (not prev_state.get("__bbWasmFetched") or fixture.get("forceWasm"))
        and not flags["localWasm"]
    ):
        flags["wasmUnavailableReason"] = "parity: wasm download not mocked"

    upload_now = base_now + int(offsets.get("upload") or 300)
    serial = int(session.get("serial") or 1)
    dev_info = session.get("deviceInfo")
    upload_payload = {
        "001": str(fixture.get("uid") or session.get("userId") or ""),
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
    upload_body = process_data(session, object_to_array(upload_payload))
    upload_url = _upload_url(session)
    upload_status = int(fixture.get("uploadStatus") or 200)
    if 200 <= upload_status < 300:
        session["serial"] = serial + 1
        session["tps"] = serial + 1

    state = _build_output_state(session, fixture, page_domain, base_now, flags)

    return {
        "schema": "bangcle_warmup_parity_v1",
        "strategyUrl": strategy_url,
        "strategyBody": strategy_body,
        "versionRequest": {
            "url": version_url,
            "contentInfoHeader": version_header,
        },
        "uploadUrl": upload_url,
        "uploadBody": upload_body,
        "state": state,
        "summary": {
            "strategyStatus": 200,
            "versionStatus": 200,
            "uploadStatus": upload_status,
            "wasmFetched": bool(flags.get("wasmFetched")),
            "localWasm": bool(flags.get("localWasm")),
            "strategyApplied": bool(flags.get("strategyApplied")),
            "wasmUnavailableReason": str(flags.get("wasmUnavailableReason") or ""),
        },
    }
