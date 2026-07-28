"""
BB SDK 客户端：Python 端通过 HTTP 调用 node_bridge 的 /bb/* endpoint。

仅服务抢购 POST (rushPurchase) 的 bb 三件套生成；其他非抢购 H5 请求按真机抓包不带 bb 头。

设计要点（与项目「禁止 SM4 降级」口径一致）：
- node_bridge 失败硬抛 RuntimeError，**绝不降级**到 Python WASM/SM4。
- bootstrap 返回 signWasm bytes/version/ok 持久化到 accounts.json（账号级）。
- 与 node_bridge 通讯用 urllib（避免循环依赖 curl_cffi）。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

_DEFAULT_PORT = int((os.environ.get("MT_NODE_HTTP_BRIDGE_PORT") or "19090").strip() or "19090")
_DEFAULT_BIND = (os.environ.get("MT_NODE_HTTP_BRIDGE_BIND") or "127.0.0.1").strip() or "127.0.0.1"
_DEFAULT_BASE = f"http://{_DEFAULT_BIND}:{_DEFAULT_PORT}"
_SIGN_RR_LOCK = threading.Lock()
_SIGN_RR_INDEX = 0
_SIGN_BAD_UNTIL: dict[int, float] = {}
_SIGN_RESTARTING: set[int] = set()


def _base_url() -> str:
    # 允许运行时覆盖（测试/调试）
    return (os.environ.get("MT_BB_CLIENT_BASE") or _DEFAULT_BASE).rstrip("/")


def _base_url_for_port(port: int) -> str:
    return f"http://{_DEFAULT_BIND}:{int(port)}"


def _parse_ports(raw: str) -> list[int]:
    ports: list[int] = []
    for token in (raw or "").replace(";", ",").replace("|", ",").replace("\n", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            port = max(1, min(65535, int(token)))
        except (TypeError, ValueError):
            continue
        if port not in ports:
            ports.append(port)
    return ports


def _bridge_ports() -> list[int]:
    raw = os.environ.get("MT_BB_NODE_PORTS") or os.environ.get("MT_NODE_BRIDGE_PORTS") or ""
    ports = _parse_ports(raw)
    if ports:
        return ports
    try:
        count = max(1, min(16, int((os.environ.get("MT_NODE_BRIDGE_COUNT") or "2").strip() or "2")))
    except (TypeError, ValueError):
        count = 2
    return [_DEFAULT_PORT + i * 2 for i in range(count)]


def _bad_ttl_sec() -> float:
    try:
        return max(0.2, min(30.0, float((os.environ.get("MT_BB_NODE_BAD_TTL_SEC") or "3").strip() or "3")))
    except (TypeError, ValueError):
        return 3.0


def _sign_candidate_ports() -> list[int]:
    global _SIGN_RR_INDEX
    ports = _bridge_ports()
    if not ports:
        return [_DEFAULT_PORT]
    now = time.time()
    with _SIGN_RR_LOCK:
        start = _SIGN_RR_INDEX % len(ports)
        _SIGN_RR_INDEX += 1
        ordered = ports[start:] + ports[:start]
        live = [p for p in ordered if _SIGN_BAD_UNTIL.get(p, 0.0) <= now]
    return live or ordered


def _mark_sign_port_bad(port: int) -> None:
    with _SIGN_RR_LOCK:
        _SIGN_BAD_UNTIL[int(port)] = time.time() + _bad_ttl_sec()


def _schedule_node_bridge_restart(port: int) -> None:
    port = int(port)
    with _SIGN_RR_LOCK:
        if port in _SIGN_RESTARTING:
            return
        _SIGN_RESTARTING.add(port)

    def _run() -> None:
        try:
            import app as _app
            fn = getattr(_app, "restart_node_bridge_port", None)
            if callable(fn):
                fn(port)
        except Exception:
            pass
        finally:
            with _SIGN_RR_LOCK:
                _SIGN_RESTARTING.discard(port)

    threading.Thread(target=_run, daemon=True, name=f"bb-node-restart-{port}").start()


def _http_request_to_base(
    base: str,
    method: str,
    path: str,
    payload: Optional[dict] = None,
    timeout: float = 10.0,
) -> dict:
    base = base.rstrip("/")
    url = base + path
    data: Optional[bytes] = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"bb_client {path} HTTP {e.code}: {body[:300]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"bb_client {path} unreachable ({base}): {e}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"bb_client {path} bad JSON: {e}") from e


def _http_request(method: str, path: str, payload: Optional[dict] = None, timeout: float = 10.0) -> dict:
    return _http_request_to_base(_base_url(), method, path, payload, timeout=timeout)


def bb_health(timeout: float = 5.0) -> dict:
    """
    GET /bb/health → {ok, wasmReady, sdkVersion, signWasmReady, signWasmVersion}
    """
    return _http_request("GET", "/bb/health", None, timeout=timeout)


def bb_bootstrap(
    phone: str = "",
    raw_id: str = "",
    ua: str = "",
    timeout: float = 30.0,
) -> dict:
    """
    POST /bb/bootstrap → 拿到（或刷新）某账号的 sign_wasm 三件套。

    返回 dict:
      signWasmVersion : str  (如 "a2abd765daa2ec438a96fc2d97209be4")
      signWasmBase64  : str  (sign_wasm bytes 的 base64)
      signWasmOk      : bool
      signWasmOkValue : str
      expiredAt       : int  (毫秒时间戳)
      source          : str  ("local_state_default" / "remote_negotiated" 等)
    """
    payload = {"phone": phone or "", "raw_id": raw_id or "", "ua": ua or ""}
    resp = _http_request("POST", "/bb/bootstrap", payload, timeout=timeout)
    if not resp.get("ok"):
        raise RuntimeError(f"bb_client.bb_bootstrap failed: {resp.get('error') or resp}")
    state = resp.get("state") or {}
    if not state.get("signWasmBase64"):
        raise RuntimeError("bb_client.bb_bootstrap: empty signWasmBase64 (node_bridge state 不可用)")
    return state


def bb_sign_post_for_rush(
    body: Any,
    *,
    appid: str = "10001",
    devid: str,
    sessid: str,
    uid: str,
    ua: str = "",
    hp: str = "",
    wasm_version: str,
    wasm_base64: str,
    wasm_ok: bool = True,
    cookie_send: bool = True,
    now_ms: Optional[int] = None,
    timeout: float = 10.0,
) -> dict:
    """
    POST /bb/sign_post → 抢购请求生成 bb 三件套 Header。

    入参：
      body         : 抢购请求 body (dict 或 string)
      devid        : H5 设备 ID（32 位 hex，"h"+...）
      sessid       : H5 会话 ID
      uid          : 用户 ID
      ua           : WebView UA
      wasm_version : 协商版本号（如 "a2abd765..."）
      wasm_base64  : sign_wasm bytes 的 base64（从 bb_bootstrap 拿）
      wasm_ok      : True 时启用 WASM 签名（141 字段），抢购必须 True

    返回 dict:
      Content-Web-Bb : str
      Content-Hh-Bb  : str
      Sdk-Ver-Bb     : str (如 "V3.5.0_20260605.1_imaotai")
      randomId       : str
      sign           : str
      signWac        : str  (JSON {"uuid","body","has_env"})
    """
    if not wasm_base64 or not wasm_version:
        raise RuntimeError(
            "bb_client.bb_sign_post_for_rush: wasm_base64/wasm_version 必传（应先跑 bb_bootstrap）"
        )
    payload = {
        "body": body,
        "ctx": {
            "appid": appid,
            "devid": devid,
            "sessid": sessid,
            "uid": uid,
            "ua": ua,
            "hp": hp,
            "wasmVersion": wasm_version,
            "wasmBytesBase64": wasm_base64,
            "wasmOk": bool(wasm_ok),
            "cookieSend": bool(cookie_send),
        },
        "options": {"now": int(now_ms)} if now_ms is not None else {},
    }
    if os.environ.get("MT_BB_CLIENT_BASE"):
        resp = _http_request("POST", "/bb/sign_post", payload, timeout=timeout)
    else:
        resp = None
        last_err: Optional[BaseException] = None
        tried: list[int] = []
        for port in _sign_candidate_ports():
            tried.append(port)
            try:
                resp = _http_request_to_base(
                    _base_url_for_port(port),
                    "POST",
                    "/bb/sign_post",
                    payload,
                    timeout=timeout,
                )
                break
            except RuntimeError as ex:
                last_err = ex
                _mark_sign_port_bad(port)
                _schedule_node_bridge_restart(port)
                continue
        if resp is None:
            raise RuntimeError(
                f"bb_client.bb_sign_post failed on node_bridge ports {tried}: {last_err}"
            ) from last_err
    if not resp.get("ok"):
        raise RuntimeError(f"bb_client.bb_sign_post failed: {resp.get('error') or resp}")
    headers = resp.get("headers") or {}
    if not headers.get("Content-Web-Bb") or not headers.get("Content-Hh-Bb"):
        raise RuntimeError(f"bb_client.bb_sign_post: missing bb headers in response: {resp}")
    return headers


def bb_du_cookie(
    *,
    appid: str = "10001",
    devid: str,
    sessid: str,
    uid: str,
    ua: str = "",
    hp: str,                                # 必传：与 cookie 里 _bs_device_id 同源
    wasm_version: str,
    wasm_base64: str,
    wasm_ok: bool = True,
    page_domain: str = "https://h5.moutai519.com.cn/",
    now_ms: Optional[int] = None,
    timeout: float = 10.0,
) -> str:
    """
    POST /bb/du_cookie → 生成 _d_u cookie 值（SM4/WASM 加密的 cReport 风控埋点）。

    注意：hp 必须传入与抢购 Cookie 里 _bs_device_id 完全一致的值，
    否则服务端解密 _d_u 后字段 111(hp) 与 cookie _bs_device_id 不一致 → 命中风控。

    返回：_d_u cookie 字符串（直接用于 Cookie header）。
    """
    if not wasm_base64 or not wasm_version:
        raise RuntimeError(
            "bb_client.bb_du_cookie: wasm_base64/wasm_version 必传（应先跑 bb_bootstrap）"
        )
    if not hp:
        raise RuntimeError("bb_client.bb_du_cookie: hp 必传（= _bs_device_id 值，否则与 cookie 不一致）")
    payload = {
        "ctx": {
            "appid": appid,
            "devid": devid,
            "sessid": sessid,
            "uid": uid,
            "ua": ua,
            "hp": hp,
            "wasmVersion": wasm_version,
            "wasmBytesBase64": wasm_base64,
            "wasmOk": bool(wasm_ok),
            "cookieSend": True,
            "pageDomain": page_domain,
            "now": int(now_ms) if now_ms is not None else None,
        },
        "options": {"now": int(now_ms)} if now_ms is not None else {},
    }
    resp = _http_request("POST", "/bb/du_cookie", payload, timeout=timeout)
    if not resp.get("ok"):
        raise RuntimeError(f"bb_client.bb_du_cookie failed: {resp.get('error') or resp}")
    du = resp.get("du") or ""
    if not du:
        raise RuntimeError(f"bb_client.bb_du_cookie: empty _d_u in response: {resp}")
    return du


def ensure_node_bridge_ready(min_signwasm_required: bool = True) -> dict:
    """启动期/抢购前调一次：node_bridge 不可用 → 立即硬抛错（禁止降级）。"""
    try:
        h = bb_health(timeout=3.0)
    except RuntimeError as e:
        raise RuntimeError(
            f"node_bridge BB SDK 不可达 ({_base_url()}/bb/health): {e}. "
            "禁止降级，请先启动 node_bridge (cd node_bridge && node server.cjs)。"
        ) from e
    if not h.get("ok") or not h.get("wasmReady"):
        raise RuntimeError(f"node_bridge BB SDK 未就绪: {h}")
    if min_signwasm_required and not h.get("signWasmReady"):
        # health 阶段只看默认 state，账号级 sign_wasm 由 bb_bootstrap 加载；这里只是兜底
        pass
    return h


if __name__ == "__main__":
    print("[bb_client] base =", _base_url())
    print("[bb_client] health:", json.dumps(bb_health(), ensure_ascii=False))
    boot = bb_bootstrap(phone="13800000000", raw_id="a" * 32, ua="test_ua")
    print("[bb_client] bootstrap version:", boot.get("signWasmVersion"))
    print("[bb_client] bootstrap b64 len:", len(boot.get("signWasmBase64") or ""))
    t0 = time.perf_counter()
    headers = bb_sign_post_for_rush(
        {"actParam": "x_demo"},
        devid="h" + "a" * 31,
        sessid="sid_demo",
        uid="1196233237",
        ua="mozilla/5.0 test",
        wasm_version=boot["signWasmVersion"],
        wasm_base64=boot["signWasmBase64"],
        wasm_ok=boot["signWasmOk"],
    )
    dt = (time.perf_counter() - t0) * 1000
    print(f"[bb_client] sign_post {dt:.1f}ms:")
    print("  Content-Web-Bb len:", len(headers["Content-Web-Bb"]))
    print("  Content-Hh-Bb     :", headers["Content-Hh-Bb"])
    print("  Sdk-Ver-Bb        :", headers["Sdk-Ver-Bb"])
    print("  signWac present   :", bool(headers.get("signWac")))
