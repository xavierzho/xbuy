#!/usr/bin/env python3
"""Phone-side relay client.

Run on the phone:
  python mobile_relay_client.py --server http://PC_LAN_IP:8989 --device phone1

The PC prepares rushPurchase requests. This client only polls, sends the POST
from the phone network, and returns the raw response.
"""

from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.error
import urllib.request


def _json_post(url: str, data: dict, timeout: float = 30.0, token: str = "") -> dict:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["X-Remote-Login-Token"] = token
    req = urllib.request.Request(
        url,
        data=raw,
        headers=headers,
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body or "{}")


def _header_pairs(attempt: dict) -> list[tuple[str, str]]:
    ordered = attempt.get("headersOrdered")
    pairs: list[tuple[str, str]] = []
    if isinstance(ordered, list):
        for item in ordered:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                value = str(item.get("value") or "")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                name = str(item[0] or "").strip()
                value = str(item[1] or "")
            else:
                continue
            if name:
                pairs.append((name, value))
    if pairs:
        return pairs
    headers = attempt.get("headers")
    if isinstance(headers, dict):
        return [(str(k), str(v)) for k, v in headers.items()]
    return []


def _send_attempt(attempt: dict) -> dict:
    url = str(attempt.get("url") or "").strip()
    if not url:
        return {"error": "attempt.url empty"}
    body_b64 = str(attempt.get("bodyB64") or "")
    body = base64.b64decode(body_b64) if body_b64 else b""
    headers = {}
    for name, value in _header_pairs(attempt):
        lname = name.lower()
        if lname in ("host", "content-length", "connection", "proxy-connection"):
            continue
        headers[name] = value
    req = urllib.request.Request(url, data=body, headers=headers, method=str(attempt.get("method") or "POST").upper())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.perf_counter()
    status = 0
    resp_headers = {}
    resp_body = b""
    try:
        with opener.open(req, timeout=45.0) as resp:
            status = int(getattr(resp, "status", 0) or resp.getcode() or 0)
            resp_headers = dict(resp.headers.items())
            resp_body = resp.read()
    except urllib.error.HTTPError as ex:
        status = int(ex.code or 0)
        resp_headers = dict(ex.headers.items()) if ex.headers else {}
        try:
            resp_body = ex.read()
        except Exception:
            resp_body = b""
    except Exception as ex:
        return {"error": f"{type(ex).__name__}: {ex}", "elapsedMs": int((time.perf_counter() - started) * 1000)}
    return {
        "statusCode": status,
        "headers": resp_headers,
        "bodyB64": base64.b64encode(resp_body).decode("ascii"),
        "elapsedMs": int((time.perf_counter() - started) * 1000),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True, help="PC WebUI root, e.g. http://192.168.1.10:8989")
    ap.add_argument("--device", required=True, help="Device id configured on account card, e.g. phone1")
    ap.add_argument("--name", default="", help="Optional display name")
    ap.add_argument("--token", default="", help="Optional public-gate token")
    ns = ap.parse_args()
    server = str(ns.server).strip().rstrip("/")
    device_id = str(ns.device).strip()
    info = {"name": ns.name or device_id, "client": "python-urllib-mobile-relay"}
    print(f"[mobile-relay] server={server} device={device_id}", flush=True)
    while True:
        try:
            poll = _json_post(
                server + "/api/mobile_relay/poll",
                {"deviceId": device_id, "waitMs": 20000, **info},
                timeout=30.0,
                token=ns.token,
            )
            if not poll.get("ok"):
                print("[mobile-relay] poll failed:", poll.get("message") or poll, flush=True)
                time.sleep(2)
                continue
            task = (poll.get("data") or {}).get("task") or {}
            if not task:
                continue
            tid = str(task.get("id") or "")
            print(f"[mobile-relay] send task={tid} phone={task.get('phone') or ''}", flush=True)
            result = _send_attempt(task.get("attempt") or {})
            ack = _json_post(
                server + "/api/mobile_relay/result",
                {"deviceId": device_id, "taskId": tid, "result": result},
                timeout=20.0,
                token=ns.token,
            )
            if not ack.get("ok"):
                print("[mobile-relay] result ack failed:", ack.get("message") or ack, flush=True)
            else:
                print(
                    f"[mobile-relay] done task={tid} status={result.get('statusCode')} "
                    f"ms={result.get('elapsedMs')} err={result.get('error') or ''}",
                    flush=True,
                )
        except KeyboardInterrupt:
            print("\n[mobile-relay] stopped", flush=True)
            return
        except Exception as ex:
            print(f"[mobile-relay] loop error: {type(ex).__name__}: {ex}", flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
