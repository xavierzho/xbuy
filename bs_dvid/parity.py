from __future__ import annotations

import copy
from typing import Any

from bs_dvid.constants import APP_KEY, DEFAULT_HAOTIAN_SDK, DEFAULT_HAOTIAN_X0
from bs_dvid.crypto_util import md5_hash
from bs_dvid.device import (
    device_config_by_index,
    generate_device_fingerprint_from_config,
    generate_local_id,
)
from bs_dvid.http_client import build_encrypted_request_artifacts
from bs_dvid.json_compat import dumps_like_node
from bs_dvid.payloads import (
    build_device_env_report,
    build_full_device_fingerprint,
    build_report_events,
    build_request_body,
    build_sig_request_body,
    build_token_request_body,
)
from parity.context import parity_context_from_fixture


def _resolve_device_config(fixture: dict) -> dict:
    if isinstance(fixture.get("deviceConfig"), dict):
        return copy.deepcopy(fixture["deviceConfig"])
    idx = int(fixture.get("deviceConfigIndex") or fixture.get("device_config_index") or 0)
    return device_config_by_index(idx)


def build_bs_dvid_parity_artifacts(fixture: dict | None = None) -> dict:
    fixture = dict(fixture or {})
    ctx = parity_context_from_fixture(fixture)
    app_version = str(fixture.get("appVersion") or fixture.get("app_version") or "1.9.10")
    device_id = str(fixture.get("deviceId") or fixture.get("device_id") or "353456789012345")
    device_config = _resolve_device_config(fixture)
    overrides = fixture.get("fingerprintOverrides") or fixture.get("fingerprint_overrides") or {}
    gateway_ip = str(fixture.get("gatewayIp") or fixture.get("gateway_ip") or overrides.get("gateway_ip") or "192.168.1.1")

    base_sec = int(fixture.get("requestTimestampBase") or fixture.get("request_timestamp_base") or 1_700_000_000)
    base_ms = base_sec * 1000

    local_id = generate_local_id(device_id)
    local_id_md5 = md5_hash(local_id)

    fp_data = generate_device_fingerprint_from_config(
        device_config,
        gateway_ip=gateway_ip,
        rng=ctx,
        overrides=overrides,
    )

    steps: list[dict[str, Any]] = []

    def add_step(name: str, path: str, body: Any, headers: dict[str, str]) -> None:
        enc = build_encrypted_request_artifacts(
            path,
            body,
            local_id_md5,
            timestamp=ctx.next_timestamp_sec(base_sec),
            random_key=ctx.generate_random_key(16),
        )
        steps.append({"name": name, "headers": headers, **enc})

    add_step(
        "step1_fetch",
        "/p/5/aio/210",
        build_request_body(local_id, app_version),
        {"User-Agent": f"haotian/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_SDK}"},
    )
    add_step(
        "step2_report_events",
        "/p/1/r/210",
        build_report_events(local_id, app_version, timestamp_ms=base_ms + 100),
        {"User-Agent": f"haotian/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_SDK}"},
    )
    add_step(
        "step3_config",
        "/s/5/aio/210",
        build_request_body(local_id, app_version),
        {
            "User-Agent": f"haotian_x0/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_X0}",
            "x-sdk-ver": f"haotian/{DEFAULT_HAOTIAN_SDK}",
            "x-plu-ver": f"haotian_x0/{DEFAULT_HAOTIAN_X0}",
            "x-app-ver": f"com.moutai.mall/{app_version}",
            "x-api-ver": "34",
        },
    )
    add_step(
        "step4_env",
        "/r/5/c/210",
        build_device_env_report(local_id, base_ms + 200, app_version, gateway_ip=gateway_ip),
        {
            "User-Agent": f"haotian_x0/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_X0}",
            "x-sdk-ver": f"haotian/{DEFAULT_HAOTIAN_SDK}",
            "x-plu-ver": f"haotian_x0/{DEFAULT_HAOTIAN_X0}",
            "x-app-ver": f"com.moutai.mall/{app_version}",
            "x-api-ver": "34",
        },
    )
    add_step(
        "step5_sig_rules",
        "/f/2/sig/210",
        build_sig_request_body(local_id),
        {
            "User-Agent": f"haotian_x6/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_X0}",
            "x-client-src": "jar",
            "x-sdk-ver": f"haotian/{DEFAULT_HAOTIAN_SDK}",
            "x-plu-ver": f"haotian_x6/{DEFAULT_HAOTIAN_X0}",
            "x-app-ver": f"com.moutai.mall/{app_version}",
            "x-api-ver": "34",
        },
    )
    fp_body = build_full_device_fingerprint(
        local_id,
        base_ms + 300,
        app_version,
        gateway_ip=gateway_ip,
        fp_data=fp_data,
    )
    add_step(
        "step6_fingerprint",
        "/r/5/c/210",
        fp_body,
        {
            "User-Agent": f"haotian_x0/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_X0}",
            "x-sdk-ver": f"haotian/{DEFAULT_HAOTIAN_SDK}",
            "x-plu-ver": f"haotian_x0/{DEFAULT_HAOTIAN_X0}",
            "x-app-ver": f"com.moutai.mall/{app_version}",
            "x-api-ver": "34",
        },
    )
    add_step(
        "step7_token",
        "/c/11/z/210",
        build_token_request_body(local_id, base_ms + 400, None),
        {
            "User-Agent": f"haotian_x6/{APP_KEY}/{app_version}/{DEFAULT_HAOTIAN_X0}",
            "x-client-src": "jar",
            "x-sdk-ver": f"haotian/{DEFAULT_HAOTIAN_SDK}",
            "x-plu-ver": f"haotian_x6/{DEFAULT_HAOTIAN_X0}",
            "x-app-ver": f"com.moutai.mall/{app_version}",
            "x-api-ver": "34",
        },
    )

    return {
        "schema": "bs_dvid_parity_v1",
        "deviceId": device_id,
        "localId": local_id,
        "localIdMd5": local_id_md5,
        "deviceConfig": device_config,
        "fingerprintPlainJson": dumps_like_node(fp_body),
        "steps": steps,
    }
