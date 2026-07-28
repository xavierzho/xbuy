#!/usr/bin/env python3
"""Local whitelist proxy for cloudflared public access.

Expose only remote_login.html, mobile_order.html, and the APIs required by those
two pages. Do not expose the main WebUI console.
"""

from __future__ import annotations

import hmac
import http.client
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


LISTEN_HOST = os.environ.get("PUBLIC_GATE_LISTEN_HOST", "127.0.0.1").strip() or "127.0.0.1"
LISTEN_PORT = int(os.environ.get("PUBLIC_GATE_PORT", "18089") or "18089")
TARGET = os.environ.get("PUBLIC_GATE_TARGET", "http://127.0.0.1:8989").strip().rstrip("/")
MODE = (os.environ.get("PUBLIC_GATE_MODE") or "both").strip().lower()
TOKEN = (
    os.environ.get("MT_PUBLIC_GATE_TOKEN")
    or os.environ.get("MT_REMOTE_LOGIN_SECRET")
    or ""
).strip()
REQUIRE_TOKEN = (os.environ.get("PUBLIC_GATE_REQUIRE_TOKEN") or "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

ALLOWED_GET = {
    "/remote_login",
    "/remote_login.html",
    "/mobile_order",
    "/mobile_order.html",
    "/api/address/pcas",
}

ALLOWED_POST = {
    "/api/accounts/list",
    "/api/accounts/send_code",
    "/api/accounts/login",
    "/api/accounts/remote_onboard_deferred_flush",
    "/api/accounts/realname/query",
    "/api/accounts/realname/submit",
    "/api/accounts/find_identity",
    "/api/accounts/address_upsert",
    "/api/accounts/remote_black_check",
    "/api/mobile/orders_scan",
    "/api/mobile/pickup_status",
    "/api/mobile/pay_link",
    "/api/mobile_relay/heartbeat",
    "/api/mobile_relay/poll",
    "/api/mobile_relay/result",
    "/api/mobile_relay/status",
}

LOGIN_GET = {"/remote_login", "/remote_login.html", "/api/address/pcas"}
LOGIN_POST = {
    "/api/accounts/list",
    "/api/accounts/send_code",
    "/api/accounts/login",
    "/api/accounts/remote_onboard_deferred_flush",
    "/api/accounts/realname/query",
    "/api/accounts/realname/submit",
    "/api/accounts/find_identity",
    "/api/accounts/address_upsert",
    "/api/accounts/remote_black_check",
}
ORDER_GET = {"/mobile_order", "/mobile_order.html"}
ORDER_POST = {
    "/api/mobile/orders_scan",
    "/api/mobile/pickup_status",
    "/api/mobile/pay_link",
    "/api/mobile_relay/heartbeat",
    "/api/mobile_relay/poll",
    "/api/mobile_relay/result",
    "/api/mobile_relay/status",
}


def _allowed_get() -> set[str]:
    if MODE in ("login", "remote_login", "onboard"):
        return LOGIN_GET
    if MODE in ("order", "mobile_order", "query"):
        return ORDER_GET
    return ALLOWED_GET


def _allowed_post() -> set[str]:
    if MODE in ("login", "remote_login", "onboard"):
        return LOGIN_POST
    if MODE in ("order", "mobile_order", "query"):
        return ORDER_POST
    return ALLOWED_POST


def _relay_path(path: str) -> bool:
    return str(path or "").startswith("/api/mobile_relay/")


def _target_parts() -> tuple[str, int]:
    u = urlparse(TARGET)
    if u.scheme not in ("http", ""):
        raise RuntimeError(f"unsupported target scheme: {u.scheme}")
    return (u.hostname or "127.0.0.1", int(u.port or 80))


TARGET_HOST, TARGET_PORT = _target_parts()


def _consteq(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


class GateHandler(BaseHTTPRequestHandler):
    server_version = "MTPublicGate/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print("[public-gate] " + fmt % args, flush=True)

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _deny(self, msg: str = "forbidden") -> None:
        self._json(403, {"ok": False, "message": msg})

    def _path(self) -> str:
        return urlparse(self.path).path or "/"

    def _token_from_query(self) -> str:
        qs = parse_qs(urlparse(self.path).query or "")
        return str((qs.get("token") or qs.get("remoteToken") or [""])[0] or "").strip()

    def _token_from_headers(self) -> str:
        got = (self.headers.get("X-Remote-Login-Token") or "").strip()
        if got:
            return got
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def _token_from_json_body(self, raw: bytes) -> str:
        try:
            data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        return str(data.get("remoteToken") or data.get("token") or "").strip()

    def _authorized(self, raw_body: bytes | None = None, *, force_token: bool = False) -> bool:
        if not force_token and not REQUIRE_TOKEN:
            return True
        if not TOKEN:
            return False
        candidates = [self._token_from_query(), self._token_from_headers()]
        if raw_body is not None:
            candidates.append(self._token_from_json_body(raw_body))
        return any(_consteq(x, TOKEN) for x in candidates if x)

    def _proxy(self, method: str, raw_body: bytes = b"") -> None:
        headers = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in ("host", "connection", "proxy-connection", "keep-alive", "transfer-encoding", "upgrade"):
                continue
            if lk == "content-length":
                continue
            headers[k] = v
        if raw_body:
            headers["Content-Length"] = str(len(raw_body))
        headers["Host"] = f"{TARGET_HOST}:{TARGET_PORT}"

        conn = http.client.HTTPConnection(TARGET_HOST, TARGET_PORT, timeout=120)
        try:
            conn.request(method, self.path, body=raw_body if raw_body else None, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            status = resp.status
            resp_headers = resp.getheaders()
        except (OSError, http.client.HTTPException, socket.timeout) as ex:
            self._json(502, {"ok": False, "message": f"backend unavailable: {ex}"})
            return
        finally:
            try:
                conn.close()
            except Exception:
                pass

        self.send_response(status)
        for k, v in resp_headers:
            lk = k.lower()
            if lk in ("connection", "transfer-encoding", "content-encoding", "content-length"):
                continue
            self.send_header(k, v)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Remote-Login-Token, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        path = self._path()
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        allowed = _allowed_get()
        if path not in allowed:
            self._deny("path not exposed on this public port")
            return
        if path != "/api/address/pcas" and not self._authorized():
            self._deny("missing or invalid token")
            return
        self._proxy("GET")

    def do_POST(self) -> None:
        path = self._path()
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(max(0, n)) if n else b""
        if path not in _allowed_post():
            self._deny("api not exposed")
            return
        if (REQUIRE_TOKEN or _relay_path(path)) and not self._authorized(raw, force_token=_relay_path(path)):
            self._deny("missing or invalid token")
            return
        self._proxy("POST", raw)


def main() -> None:
    if REQUIRE_TOKEN and not TOKEN:
        raise SystemExit("MT_PUBLIC_GATE_TOKEN is required")
    print(f"[public-gate] listen http://{LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    print(f"[public-gate] target {TARGET}", flush=True)
    print(f"[public-gate] mode={MODE} require_token={int(REQUIRE_TOKEN)}", flush=True)
    print("[public-gate] exposed limited page/APIs only", flush=True)
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), GateHandler).serve_forever()


if __name__ == "__main__":
    main()
