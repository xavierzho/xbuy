from __future__ import annotations

import base64
import gzip
import json
import logging
import time
from typing import Any

from bs_dvid.constants import APP_KEY, DEFAULT_HAOTIAN_SDK, DEFAULT_HAOTIAN_X0
from bs_dvid.crypto_util import md5_hash
from bs_dvid.device import (
    generate_imei,
    generate_local_id,
    generate_moutai_user_agent,
    get_random_device_config,
)
from bs_dvid.http_client import send_encrypted_request
from bs_dvid.payloads import (
    build_device_env_report,
    build_full_device_fingerprint,
    build_report_events,
    build_request_body,
    build_sig_request_body,
    build_token_request_body,
)

_log = logging.getLogger(__name__)


def _extract_report_result_data(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    for value in data.values():
        if isinstance(value, dict) and not isinstance(value, list):
            return value
    return {}


def fetch_bs_dvid(
    app_version: str,
    device_id: str | None = None,
    proxy_config: dict | None = None,
) -> dict:
    device_id = device_id or generate_imei()
    local_id = generate_local_id(device_id)
    local_id_md5 = md5_hash(local_id)
    _log.info("[BS-DVID] 步骤1: 获取初始BS-DVID...")
    body = build_request_body(local_id, app_version)
    headers = {"User-Agent": f"haotian/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_SDK}"}
    try:
        result = send_encrypted_request(
            "/p/5/aio/210", body, local_id_md5, headers, 30.0, proxy_config
        )
        if result.get("success") and result.get("data"):
            bs_dvid = result["data"].get("0") or ""
            return {
                "success": True,
                "bsDvid": bs_dvid,
                "localId": local_id,
                "localIdMd5": local_id_md5,
                "deviceId": device_id,
                "rawResponse": result["data"],
                "requestId": result.get("request_id"),
            }
        return {
            "success": False,
            "error": result.get("error") or "获取失败",
            "localId": local_id,
            "deviceId": device_id,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "localId": local_id, "deviceId": device_id}


def report_sdk_events(local_id: str, app_version: str, proxy_config: dict | None = None) -> dict:
    local_id_md5 = md5_hash(local_id)
    _log.info("[BS-DVID] 步骤2: 上报SDK初始化事件...")
    events = build_report_events(local_id, app_version)
    headers = {"User-Agent": f"haotian/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_SDK}"}
    try:
        result = send_encrypted_request(
            "/p/1/r/210", events, local_id_md5, headers, 30.0, proxy_config
        )
        return {
            "success": result.get("success"),
            "requestId": result.get("request_id"),
            "error": result.get("error"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def fetch_sdk_config(local_id: str, app_version: str, proxy_config: dict | None = None) -> dict:
    local_id_md5 = md5_hash(local_id)
    _log.info("[BS-DVID] 步骤3: 获取SDK配置...")
    body = build_request_body(local_id, app_version)
    headers = {
        "User-Agent": f"haotian_x0/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_X0}",
        "x-sdk-ver": f"haotian/{DEFAULT_HAOTIAN_SDK}",
        "x-plu-ver": f"haotian_x0/{DEFAULT_HAOTIAN_X0}",
        "x-app-ver": f"com.moutai.mall/{app_version}",
        "x-api-ver": "34",
    }
    try:
        result = send_encrypted_request(
            "/s/5/aio/210", body, local_id_md5, headers, 30.0, proxy_config
        )
        return {
            "success": result.get("success"),
            "config": result.get("data"),
            "requestId": result.get("request_id"),
            "error": result.get("error"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def report_device_env(local_id: str, app_version: str, proxy_config: dict | None = None) -> dict:
    local_id_md5 = md5_hash(local_id)
    timestamp_ms = int(time.time() * 1000)
    _log.info("[BS-DVID] 步骤4: 上报设备环境检测...")
    body = build_device_env_report(local_id, timestamp_ms, app_version)
    headers = {
        "User-Agent": f"haotian_x0/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_X0}",
        "x-sdk-ver": f"haotian/{DEFAULT_HAOTIAN_SDK}",
        "x-plu-ver": f"haotian_x0/{DEFAULT_HAOTIAN_X0}",
        "x-app-ver": f"com.moutai.mall/{app_version}",
        "x-api-ver": "34",
    }
    try:
        result = send_encrypted_request(
            "/r/5/c/210", body, local_id_md5, headers, 30.0, proxy_config
        )
        if result.get("success") and result.get("data"):
            result_data = _extract_report_result_data(result["data"])
            return {
                "success": True,
                "clientIp": result_data.get("7") or "",
                "detect": result_data.get("detect") or "-1",
                "nonce": result_data.get("nonce") or "",
                "requestId": result.get("request_id"),
            }
        return {"success": False, "error": result.get("error")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def fetch_sig_rules(local_id: str, app_version: str, proxy_config: dict | None = None) -> dict:
    local_id_md5 = md5_hash(local_id)
    _log.info("[BS-DVID] 步骤5: 获取签名/风控规则...")
    body = build_sig_request_body(local_id)
    headers = {
        "User-Agent": f"haotian_x6/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_X0}",
        "x-client-src": "jar",
        "x-sdk-ver": f"haotian/{DEFAULT_HAOTIAN_SDK}",
        "x-plu-ver": f"haotian_x6/{DEFAULT_HAOTIAN_X0}",
        "x-app-ver": f"com.moutai.mall/{app_version}",
        "x-api-ver": "34",
    }
    try:
        result = send_encrypted_request(
            "/f/2/sig/210", body, local_id_md5, headers, 30.0, proxy_config
        )
        if result.get("success") and result.get("data"):
            sig_rules: list = []
            sig_b64 = result["data"].get("sig")
            if sig_b64:
                try:
                    sig_compressed = base64.b64decode(sig_b64)
                    sig_decompressed = gzip.decompress(sig_compressed)
                    sig_rules = json.loads(sig_decompressed.decode("utf-8"))
                except (OSError, json.JSONDecodeError, ValueError):
                    pass
            return {
                "success": True,
                "sigRules": sig_rules,
                "sigRulesCount": len(sig_rules),
                "requestId": result.get("request_id"),
            }
        return {"success": False, "error": result.get("error")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def report_full_device_fingerprint(
    local_id: str,
    app_version: str,
    proxy_config: dict | None = None,
    *,
    device_config: dict | None = None,
) -> dict:
    local_id_md5 = md5_hash(local_id)
    timestamp_ms = int(time.time() * 1000)
    _log.info("[BS-DVID] 步骤6: 上报完整设备指纹...")
    body = build_full_device_fingerprint(
        local_id, timestamp_ms, app_version, device_config=device_config
    )
    headers = {
        "User-Agent": f"haotian_x0/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_X0}",
        "x-sdk-ver": f"haotian/{DEFAULT_HAOTIAN_SDK}",
        "x-plu-ver": f"haotian_x0/{DEFAULT_HAOTIAN_X0}",
        "x-app-ver": f"com.moutai.mall/{app_version}",
        "x-api-ver": "34",
    }
    try:
        result = send_encrypted_request(
            "/r/5/c/210", body, local_id_md5, headers, 30.0, proxy_config
        )
        if result.get("success") and result.get("data"):
            result_data = _extract_report_result_data(result["data"])
            return {
                "success": True,
                "clientIp": result_data.get("7") or "",
                "detect": result_data.get("detect") or "-1",
                "nonce": result_data.get("nonce") or "",
                "requestId": result.get("request_id"),
            }
        return {"success": False, "error": result.get("error")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def fetch_token(
    local_id: str,
    app_version: str,
    magic: str | None = None,
    proxy_config: dict | None = None,
) -> dict:
    local_id_md5 = md5_hash(local_id)
    timestamp_ms = int(time.time() * 1000)
    _log.info("[BS-DVID] 步骤7: 获取最终BS-DVID Token...")
    body = build_token_request_body(local_id, timestamp_ms, magic)
    headers = {
        "User-Agent": f"haotian_x6/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_X0}",
        "x-client-src": "jar",
        "x-sdk-ver": f"haotian/{DEFAULT_HAOTIAN_SDK}",
        "x-plu-ver": f"haotian_x6/{DEFAULT_HAOTIAN_X0}",
        "x-app-ver": f"com.moutai.mall/{app_version}",
        "x-api-ver": "34",
    }
    try:
        result = send_encrypted_request(
            "/c/11/z/210", body, local_id_md5, headers, 30.0, proxy_config
        )
        if result.get("success") and result.get("data"):
            data = result["data"]
            return {
                "success": True,
                "token": data.get("token") or "",
                "status": data.get("st") or "",
                "version": data.get("ver") or "",
                "nc": data.get("nc") or "",
                "requestId": result.get("request_id"),
            }
        return {"success": False, "error": result.get("error")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_bs_dvid_with_report(options: dict | None = None) -> dict:
    options = options or {}
    app_version = options.get("appVersion") or options.get("app_version")
    if not app_version:
        from settings.versions import APP_VERSION

        app_version = APP_VERSION

    device_id = options.get("deviceId") or options.get("device_id")
    fetch_config = options.get("fetchConfig", options.get("fetch_config", True))
    report_env = options.get("reportEnv", options.get("report_env", True))
    report_full_fingerprint = options.get(
        "reportFullFingerprint", options.get("report_full_fingerprint", True)
    )
    fetch_final_token = options.get(
        "fetchFinalToken", options.get("fetch_final_token", True)
    )
    proxy_config = options.get("proxyConfig") or options.get("proxy_config")

    _log.info("========================================")
    _log.info("[BS-DVID] 开始获取 BS-DVID (完整流程, Python)")
    _log.info("========================================")

    final_device_id = device_id or generate_imei()
    device_config = get_random_device_config()

    step1 = fetch_bs_dvid(app_version, final_device_id, proxy_config)
    if not step1.get("success"):
        _log.info("[BS-DVID] 步骤1失败: %s", step1.get("error"))
        return {"success": False, "error": step1.get("error"), "deviceId": final_device_id}

    result: dict[str, Any] = {
        "success": True,
        "bsDvid": step1["bsDvid"],
        "localId": step1["localId"],
        "localIdMd5": step1["localIdMd5"],
        "deviceId": final_device_id,
        "deviceIdMd5": md5_hash(final_device_id),
        "deviceIdDoubleMd5": md5_hash(md5_hash(final_device_id)),
        "deviceConfig": device_config,
        "manufacturer": device_config.get("manufacturer"),
        "device": device_config.get("device"),
        "androidVersion": device_config.get("android_version"),
        "sdkVersion": device_config.get("sdk_version"),
        "backend": "python",
    }

    local_id = step1["localId"]

    step2 = report_sdk_events(local_id, app_version, proxy_config)
    result["reportSuccess"] = step2.get("success")
    result["reportRequestId"] = step2.get("requestId")

    if fetch_config:
        step3 = fetch_sdk_config(local_id, app_version, proxy_config)
        result["configSuccess"] = step3.get("success")
        result["configRequestId"] = step3.get("requestId")
        if step3.get("success"):
            result["config"] = step3.get("config")

    if report_env:
        step4 = report_device_env(local_id, app_version, proxy_config)
        result["envReportSuccess"] = step4.get("success")
        result["envRequestId"] = step4.get("requestId")
        if step4.get("success"):
            result["clientIp"] = step4.get("clientIp")
            result["detectResult"] = step4.get("detect")
            result["nonce"] = step4.get("nonce")

    step5 = fetch_sig_rules(local_id, app_version, proxy_config)
    result["sigRulesSuccess"] = step5.get("success")
    result["sigRequestId"] = step5.get("requestId")
    if step5.get("success"):
        result["sigRulesCount"] = step5.get("sigRulesCount")

    if report_full_fingerprint:
        step6 = report_full_device_fingerprint(
            local_id, app_version, proxy_config, device_config=device_config
        )
        result["fingerprintSuccess"] = step6.get("success")
        result["fingerprintRequestId"] = step6.get("requestId")
        if step6.get("success"):
            result["fingerprintClientIp"] = step6.get("clientIp")
            result["fingerprintDetect"] = step6.get("detect")
            result["fingerprintNonce"] = step6.get("nonce")

    if fetch_final_token:
        step7 = fetch_token(local_id, app_version, None, proxy_config)
        result["tokenSuccess"] = step7.get("success")
        result["tokenRequestId"] = step7.get("requestId")
        if step7.get("success"):
            result["finalBsDvid"] = step7.get("token")
            result["token"] = step7.get("token")
            result["tokenStatus"] = step7.get("status")
            result["tokenVersion"] = step7.get("version")
            result["tokenNc"] = step7.get("nc")

    final_bs_dvid = result.get("finalBsDvid") or result.get("bsDvid")
    if final_bs_dvid and final_device_id:
        result["userAgent"] = generate_moutai_user_agent(
            app_version, final_device_id, final_bs_dvid, device_config
        )

    _log.info("[BS-DVID] 完成 final=%s...", (final_bs_dvid or "")[:30])
    return result
