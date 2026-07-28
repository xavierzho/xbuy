#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Node 桥 TLS 伪装出口：stdin 一行 JSON → stdout 一行 JSON。
与 app.py curl_cffi impersonate 一致，避免 Node axios 使用默认 TLS 指纹。

无参数：单次模式（stdin 读满一段 JSON，处理一次后退出，兼容旧行为）。
--daemon：常驻模式，stdin 每行一条 JSON 任务，stdout 每行一条 JSON 结果；
          仅减少反复启动解释器/进程的开销；每枪仍新建 Session，由 curl_cffi 自行建连/TLS。
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _run_curl_job_with_http(http, req: dict) -> dict:
    """执行一条 HTTP 任务；http 为 curl_cffi.requests 模块。"""
    method = (req.get("method") or "GET").upper()
    url = (req.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "missing url"}

    headers = req.get("headers") if isinstance(req.get("headers"), dict) else {}
    timeout_ms = int(req.get("timeoutMs") or 30000)
    timeout = float(max(1000, min(600_000, timeout_ms))) / 1000.0
    impersonate = (
        req.get("tlsImpersonate") or req.get("impersonate") or "safari17_0"
    )
    impersonate = str(impersonate).strip() or "safari17_0"
    proxy_url = str(req.get("proxyUrl") or "").strip()
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    data = None
    if req.get("bodyB64"):
        try:
            data = base64.b64decode(req["bodyB64"])
        except Exception:
            data = None
    elif req.get("body") is not None:
        b = req["body"]
        if isinstance(b, str):
            data = b.encode("utf-8")
        elif isinstance(b, (dict, list)):
            data = json.dumps(b, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        else:
            data = str(b).encode("utf-8")

    kw: dict = {
        "headers": headers,
        "timeout": timeout,
        "impersonate": impersonate,
        "allow_redirects": True,
    }
    if data is not None:
        kw["data"] = data
    if proxies:
        kw["proxies"] = proxies

    try:
        # 每枪新建 Session，不做跨请求连接池复用（与单次子进程版语义一致）
        s = http.Session()
        r = s.request(method, url, **kw)
        buf = r.content if r.content else b""
        return {
            "ok": True,
            "status": int(r.status_code),
            "bodyB64": base64.b64encode(buf).decode("ascii"),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:800]}


def _main_once() -> None:
    raw = sys.stdin.read()
    raw = raw.strip()
    if raw.startswith("\ufeff"):
        raw = raw.lstrip("\ufeff")
    if not raw:
        print(json.dumps({"ok": False, "error": "empty stdin"}, ensure_ascii=False))
        return
    try:
        req = json.loads(raw)
    except json.JSONDecodeError as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"json: {e} (stdin_chars={len(raw)})",
                },
                ensure_ascii=False,
            )
        )
        return

    from curl_cffi import requests as http

    out = _run_curl_job_with_http(http, req)
    print(json.dumps(out, ensure_ascii=False))


def _main_daemon() -> None:
    from curl_cffi import requests as http

    for line in sys.stdin:
        line = line.rstrip("\r\n")
        if line.startswith("\ufeff"):
            line = line.lstrip("\ufeff")
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            print(
                json.dumps(
                    {"ok": False, "error": f"json: {e}"},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            continue
        out = _run_curl_job_with_http(http, req)
        print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        _main_daemon()
    else:
        _main_once()
