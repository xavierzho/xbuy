from __future__ import annotations

import copy
import json
import math
import time
from typing import Any
from urllib.parse import urlparse

from bangcle.constants import (
    DEFAULT_BB_APP_KEY,
    DEFAULT_BB_HOST,
    DEFAULT_BB_TASK,
    DEFAULT_WASM_RUNTIME_FALLBACK_HOST,
    LIVE_BB_REMOTE_TASK,
    LIVE_BB_STRATEGY_VERSION,
    LIVE_BB_WASM_VERSION,
)
from bangcle.sdk import SDK_VERSION, node_random_id


def _clone_task() -> list:
    return json.loads(json.dumps(DEFAULT_BB_TASK))


def strip_query_and_hash(value: str) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    try:
        u = urlparse(s)
        if u.scheme:
            return f"{u.scheme}://{u.netloc}{u.path}"
    except Exception:
        pass
    return s.split("?")[0].split("#")[0] or s


_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _int_to_base36(n: int) -> str:
    n = int(n)
    if n == 0:
        return "0"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = _B36[r] + out
    return out


def _js_random_base36_suffix(rs: float) -> str:
    """``Math.random().toString(36).slice(2)``（与 V8 一致，优先 Node）。"""
    import shutil
    import subprocess

    y = float(rs)
    node = shutil.which("node")
    if node:
        code = f"process.stdout.write(Number({y}).toString(36).slice(2))"
        try:
            return subprocess.check_output([node, "-e", code], text=True, timeout=2).strip()
        except Exception:
            pass
    if not (0 <= y < 1):
        whole = _int_to_base36(int(y))
        return whole[2:] if len(whole) > 2 else whole
    out: list[str] = []
    for _ in range(11):
        y *= 36
        d = int(y)
        out.append(_B36[d])
        y -= d
    return "".join(out)


def get_start_id(now_ms: int, random_source: float | None = None) -> str:
    import random

    rs = random.random() if random_source is None else float(random_source)
    return f"{_int_to_base36(int(now_ms))}_{_js_random_base36_suffix(rs)}_4"


def generate_bs_device_id(did: str, now_ms: int) -> str:
    tail = str(did or "")[-4:]
    return f"bid-{str(now_ms)[::-1]}-{tail}-{node_random_id(now_ms)[9:13]}"


def create_bb_context(options: dict) -> dict:
    task = _clone_task()
    page_domain = str(options.get("pageDomain") or "https://h5.moutai519.com.cn/")
    ctx: dict[str, Any] = {
        "sdkVersion": str(options.get("sdkVersion") or SDK_VERSION),
        "sdkConfig": {"app_key": str(options.get("appKey") or DEFAULT_BB_APP_KEY), "task": task},
        "hostAd": str(options.get("hostAd") or DEFAULT_BB_HOST).rstrip("/"),
        "hostWasm": str(
            options.get("hostWasm") or options.get("hostAd") or DEFAULT_BB_HOST
        ).rstrip("/")
        or DEFAULT_WASM_RUNTIME_FALLBACK_HOST,
        "pageDomain": page_domain,
        "did": str(options.get("did") or ""),
        "startId": str(options.get("startId") or ""),
        "userId": str(options.get("userId") or ""),
        "userAgent": str(options.get("userAgent") or ""),
        "hp": str(options.get("hp") or ""),
        "uaid": str(options.get("uaid") or ""),
        "jsid": str(options.get("jsid") or ""),
        "token": str(options.get("token") or ""),
        "tokenPrepetuity": str(options.get("tokenPrepetuity") or options.get("rk") or ""),
        "signWasmVersion": str(options.get("signWasmVersion") or ""),
        "signWasmOk": bool(options.get("signWasmOk", False)),
        "signWasmBase64": str(options.get("signWasmBase64") or ""),
        "signWasmBytes": options.get("signWasmBytes"),
        "deviceInfo": options.get("deviceInfo"),
        "serial": int(options.get("serial") or 1),
        "tps": int(options.get("tps") or options.get("serial") or 1),
        "tacticsVersion": int(options.get("tacticsVersion") or 0),
        "timeDelta": int(options.get("timeDelta") or 0),
        "remoteTask": copy.deepcopy(options.get("remoteTask")),
        "now": int(options.get("now") or 0),
    }
    if not ctx["startId"]:
        ctx["startId"] = get_start_id(int(options.get("now") or 0))
    if not ctx["hp"] and ctx["did"]:
        ctx["hp"] = generate_bs_device_id(ctx["did"], int(options.get("now") or 0))
    return ctx


def apply_remote_strategy(
    ctx: dict, strategy_response: Any, request_started_at: int, response_received_at: int
) -> dict:
    from bangcle.crypto_bb import un_process_data

    if isinstance(strategy_response, list):
        decoded = strategy_response
    else:
        try:
            decoded = un_process_data(ctx, str(strategy_response or ""))
        except Exception:
            decoded = None
    fallback = [copy.deepcopy(LIVE_BB_REMOTE_TASK), int(time.time() * 1000), LIVE_BB_STRATEGY_VERSION]
    data = decoded if isinstance(decoded, list) and decoded and isinstance(decoded[0], list) else fallback
    task = data[0] if isinstance(data[0], list) else copy.deepcopy(LIVE_BB_REMOTE_TASK)
    request_at = int(request_started_at or time.time() * 1000)
    response_at = int(response_received_at or request_at)
    midpoint = request_at + math.floor((response_at - request_at) / 2 + 0.5)

    def at(seq: list, i: int) -> Any:
        return seq[i] if i < len(seq) else None

    server_time = int(at(data, 1) or midpoint)
    tactics_version = int(at(data, 2) or LIVE_BB_STRATEGY_VERSION)

    sdk_task = ctx["sdkConfig"]["task"]
    sdk_task[16] = at(task, 0) or 0
    sdk_task[22] = at(task, 1) or 0
    sdk_task[17] = copy.deepcopy(task[2]) if isinstance(at(task, 2), list) else []
    t3 = at(task, 3)
    sdk_task[20] = t3 if isinstance(t3, (int, float)) and not isinstance(t3, bool) and t3 > 10 else 3600
    sdk_task[21] = at(task, 4) or 0
    sdk_task[7] = task[5] if len(task) > 5 else 10
    sdk_task[15] = at(task, 6) or 10
    sdk_task[23] = at(task, 7) or 20
    sdk_task[24] = at(task, 8) or 3
    if not isinstance(sdk_task[25], list):
        sdk_task[25] = [0, "", "", "", "", "", "", "", ""]
    if len(task) > 9 and isinstance(task[9], list):
        for i in range(9):
            sdk_task[25][i] = task[9][i] if i < len(task[9]) else ""

    if not isinstance(sdk_task[18], list):
        sdk_task[18] = [0, 0]
    sdk_task[18][0] = midpoint
    sdk_task[18][1] = server_time
    ctx["sdkConfig"]["td"] = server_time - midpoint
    ctx["timeDelta"] = ctx["sdkConfig"]["td"]
    ctx["tacticsVersion"] = tactics_version
    ctx["remoteTask"] = copy.deepcopy(task)
    return {
        "task": copy.deepcopy(task),
        "serverTime": server_time,
        "midpoint": midpoint,
        "timeDelta": ctx["timeDelta"],
        "tacticsVersion": tactics_version,
    }
