#!/usr/bin/env python3
"""
本地 UI 联动服务:
- 默认监听端口 8989（环境变量 MT_WEBUI_PORT 可覆盖）
- 提供静态页面 ui.html
- 将前端按钮映射到 app.py 的 Session 方法
- 多账号批量管理 API (/api/accounts/*)
- GET /api/address/pcas：全国省市区 JSON（内嵌浏览器优先同源拉取，避免直连 GitHub 失败）；可选同目录 pcas-code.json 离线兜底
- Wechaty /grab/*、企业微信与 QQ 文本指令的共用的落盘与业务逻辑见 grab_bot_ops.py
"""

import base64
import asyncio
import concurrent.futures
import hmac
import ipaddress
import json
import os
import queue

# 抢购 BB 固定使用 shield.py 的 py 稳定版链路；老/新切换入口仅保留兼容。
# 与启动器一致：发验证码前先跑浩天，把 BS-DVID + deviceConfig(sdk/mfr/mdl) 写入账号再发码。关闭：MT_HAOTIAN_BEFORE_VCODE=0
os.environ.setdefault("MT_HAOTIAN_BEFORE_VCODE", "1")
os.environ.setdefault("MT_BB_AUTO_UPDATE", "1")
os.environ.setdefault("MT_BB_UPDATE_MODE", "sync")
os.environ.setdefault("MT_BB_AUTO_UPDATE_INTERVAL_SEC", "21600")
os.environ.setdefault("MT_BB_AUTO_UPDATE_ON_START", "1")

import random
import re
import shutil
import sys
import socket
import subprocess
import threading
import time
import traceback
import http.client
import ssl
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape as html_escape
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from project_versions import BB_SIGN_WASM_VERSION

from app import (
    H5_HOST,
    TLS_PROFILE,
    Session,
    verify_status_realname_complete,
    _NodeHttpResponse,
    _batch_rush_parallel_cap,
    _ensure_go_bridge_running,
    _ensure_node_bridge_running,
    _node_bridge_port,
    _node_bridge_ports,
    _go_bridge_should_run,
    _go_bridge_port,
    _kill_node_on_port,
    _normalize_rush_loop_trace_rows,
    _proxy_hostport_for_log,
    _reset_go_bridge_boot_state,
    _rush_default_lane_count,
    _rush_jitter_ms_max,
    _rush_row_stop_rush,
    _wait_listen_port_free,
    _use_node_batch_rush,
    estimate_node_batch_outer_timeout_sec,
    go_bridge_batch_http_dispatch,
    go_bridge_prewarm_http_dispatch,
    mt_resolve_pay_url_from_sdk,
)
from accounts import (
    POOL,
    STATUS_OK,
    STATUS_PENDING,
    _norm_phone,
    enter_rush_hot_path,
    exit_rush_hot_path,
    global_force_local_ip,
    set_global_force_local_ip,
)
import mobile_relay
from grab_bot_ops import (
    GRAB_BOT_HTTP_PATHS,
    extract_address_book_from_doc,
    grab_apply_item_741,
    grab_batch_refresh_login,
    grab_list_account_addresses,
    grab_set_nickname,
    grab_refresh_all_addresses,
    grab_remove_account,
    grab_remove_accounts_batch,
    grab_set_all_amt,
    grab_set_all_delay,
    import_address_book_from_json_file,
    add_phones_to_account_group,
    load_ui_state_raw,
    merge_address_book_into_ui_state,
    merge_ui_state_from_post,
    normalize_ui_state_address_book_keys,
)
from product_catalog import (
    MT_NEED_CODE_BY_ITEM_CODE,
    catalog_item_by_code,
    detail_kind_from_referer,
    load_catalog_items,
)
import address_db

# 未显式设置时：组单/下单等与 app._post 同源走本机 go_bridge（rush_bridge；与 MT_RUSH_HTTP_NODE 独立）；可设 MT_HTTP_BACKEND= 空 关闭
os.environ.setdefault("MT_HTTP_BACKEND", "node")
# 抢购单独开关；与定制启动器默认一致
os.environ.setdefault("MT_RUSH_HTTP_NODE", "1")
# 批量抢购默认一次性下发 go_bridge /batch，由 Go 统一调度全部账号任务。
os.environ.setdefault("MT_BATCH_RUSH_NODE", "1")
# 大代理池：soft 429( body 1/2 ) 默认换 IP 再试；额外 hop 预算见 app._rush_soft429_retry_budget（MT_RUSH_SOFT429_RETRY=auto）
os.environ.setdefault("MT_RUSH_SOFT429_ROTATE", "1")
os.environ.setdefault("MT_RUSH_SOFT429_RETRY", "auto")
# 一账号一监听端口（8989、8990…），前端按手机号选不同源，突破浏览器对同源 HTTP/1.1 并发连接约 6 条的限制。关闭：MT_WEBUI_ONE_PORT_PER_ACCOUNT=0
os.environ.setdefault("MT_WEBUI_ONE_PORT_PER_ACCOUNT", "1")

ROOT = Path(__file__).resolve().parent
_go_bridge_boot_banner_printed = False
_rush_global_abort_flag = False
_rush_global_abort_event = threading.Event()
# 支付通知「导出」落盘目录（与 ui.html 导出一致）
PAY_RECORD_EXPORT_DIR = ROOT / "抢购记录"
# 账号勾选导出落盘目录（账号列表工具栏「导出勾选手机号」）
ACCOUNT_RECORD_EXPORT_DIR = ROOT / "账号记录"
WEB_DIR = ROOT / "web"
UI_FILE = WEB_DIR / "ui.html"
REMOTE_LOGIN_FILE = WEB_DIR / "remote_login.html"
MOBILE_ORDER_FILE = WEB_DIR / "mobile_order.html"
GO_BRIDGE_TLS_MODE_FILE = ROOT / "go_bridge_tls_mode.json"
# 监听地址：默认 0.0.0.0＝任意网卡、任意来源 IP 均可连（对方须能路由到你这台机；防火墙放行端口）。
# 若只想本机访问：MT_WEBUI_BIND=127.0.0.1
LISTEN_HOST = (os.environ.get("MT_WEBUI_BIND") or "0.0.0.0").strip() or "0.0.0.0"
# 内嵌浏览器与健康检查始终走回环（0.0.0.0 不能作为浏览器 URL）
LOCAL_UI_HOST = "127.0.0.1"
HOST = LISTEN_HOST  # 兼容旧代码：与监听地址相同
# 主 WebUI 端口：默认 8989；与 go_bridge 监听端口（默认 MT_GO_BRIDGE_PORT=19091）独立。覆盖：MT_WEBUI_PORT
try:
    _raw_webui_port = (os.environ.get("MT_WEBUI_PORT") or "8989").strip() or "8989"
    PORT = max(1, min(65535, int(_raw_webui_port)))
except (TypeError, ValueError):
    PORT = 8989
# 实际已成功绑定的端口（含主端口 PORT 与随后连续分片端口），供 /api/state 的 apiShardBases 使用
API_LISTEN_PORTS: list[int] = []
# 与 API_LISTEN_PORTS 一一对应，便于动态缩容时 shutdown
API_SERVERS: list[ThreadingHTTPServer] = []
_api_ports_lock = threading.Lock()
_PUBLIC_PAGE_TUNNEL: "_PublicPageTunnelManager | None" = None
_go_bridge_tls_mode_lock = threading.RLock()

# 内嵌 Qt WebEngine / Chromium 对「同一 http 源」的并发连接上限需由前端并发 fetch 实测；
# 此处仅统计服务端同时进入 hold 的请求数（ThreadingHTTPServer 每连接一线程，不会成为瓶颈）。
_conc_dbg_lock = threading.Lock()
_conc_dbg_inside = 0
_conc_dbg_peak = 0

DIST_WS_HOST = (os.environ.get("MT_DIST_WS_BIND") or "0.0.0.0").strip() or "0.0.0.0"
try:
    DIST_WS_PORT = max(1, min(65535, int((os.environ.get("MT_DIST_WS_PORT") or "8765").strip() or "8765")))
except (TypeError, ValueError):
    DIST_WS_PORT = 8765
try:
    DIST_NODE_STALE_SEC = max(10.0, float(os.environ.get("MT_DIST_NODE_STALE_SEC", "45") or 45))
except (TypeError, ValueError):
    DIST_NODE_STALE_SEC = 45.0
try:
    DIST_JOB_TIMEOUT_SEC = max(30.0, float(os.environ.get("MT_DIST_JOB_TIMEOUT_SEC", "1800") or 1800))
except (TypeError, ValueError):
    DIST_JOB_TIMEOUT_SEC = 1800.0
try:
    DIST_NODE_DEFAULT_CONCURRENCY = max(1, min(100000, int(os.environ.get("MT_DIST_NODE_DEFAULT_CONCURRENCY", "12") or 12)))
except (TypeError, ValueError):
    DIST_NODE_DEFAULT_CONCURRENCY = 12

_dist_lock = threading.RLock()
_dist_loop: asyncio.AbstractEventLoop | None = None
_dist_server_started = False
_dist_nodes: dict[str, dict] = {}
_dist_jobs: dict[str, "_DistJob"] = {}
DIST_NODE_CONFIG_FILE = ROOT / "dist_node_config.json"
_dist_node_config_cache: dict | None = None


class _DistJob:
    def __init__(self, job_id: str, node_count: int):
        self.job_id = job_id
        self.node_count = int(node_count)
        self.q: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.node_offsets: dict[str, int] = {}
        self.done_nodes: set[str] = set()
        self.started_at = time.monotonic()
        self.stop_requested = False

    def put(self, node_id: str, msg: dict) -> None:
        self.q.put((str(node_id or ""), msg if isinstance(msg, dict) else {}))


def _dist_load_node_config() -> dict:
    global _dist_node_config_cache
    if _dist_node_config_cache is not None:
        return _dist_node_config_cache
    try:
        data = json.loads(DIST_NODE_CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    nodes = data.get("nodes")
    if not isinstance(nodes, dict):
        nodes = {}
    _dist_node_config_cache = {"nodes": nodes}
    return _dist_node_config_cache


def _dist_save_node_config(data: dict) -> None:
    global _dist_node_config_cache
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("nodes"), dict):
        data["nodes"] = {}
    _dist_node_config_cache = data
    DIST_NODE_CONFIG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _dist_set_node_config(node_id: str, *, concurrency: int | None = None) -> dict:
    node_id = str(node_id or "").strip()
    if not node_id:
        raise ValueError("nodeId 不能为空")
    data = _dist_load_node_config()
    nodes = data.setdefault("nodes", {})
    cfg = nodes.setdefault(node_id, {})
    if concurrency is not None:
        cfg["concurrency"] = max(1, min(100000, int(concurrency)))
    _dist_save_node_config(data)
    return dict(cfg)


def _dist_get_node_config(node_id: str) -> dict:
    data = _dist_load_node_config()
    nodes = data.get("nodes") if isinstance(data.get("nodes"), dict) else {}
    cfg = nodes.get(str(node_id or "")) if isinstance(nodes, dict) else {}
    return dict(cfg) if isinstance(cfg, dict) else {}


def _dist_node_concurrency(node_id: str, fallback: int, task_count: int) -> int:
    cfg = _dist_get_node_config(node_id)
    raw = cfg.get("concurrency")
    if raw is None or str(raw).strip() == "":
        c = DIST_NODE_DEFAULT_CONCURRENCY
    else:
        try:
            c = int(raw)
        except (TypeError, ValueError):
            c = DIST_NODE_DEFAULT_CONCURRENCY
    return max(1, min(max(1, int(task_count or 1)), c))


def _dist_error_text(obj: object) -> str:
    if not isinstance(obj, dict):
        return str(obj or "").lower()
    parts: list[str] = []
    for k in ("message", "detail", "error", "code", "httpStatus"):
        v = obj.get(k)
        if v is not None:
            parts.append(str(v))
    row = obj.get("row")
    if isinstance(row, dict):
        parts.append(_dist_error_text(row))
    ev = obj.get("event")
    if isinstance(ev, dict):
        parts.append(_dist_error_text(ev))
    return " ".join(parts).lower()


def _dist_event_is_tls_fail(ev: dict) -> bool:
    s = _dist_error_text(ev)
    return any(k in s for k in ("tls", "handshake", "unexpected protocol", "protocol version"))


def _dist_event_is_proxy_fail(ev: dict) -> bool:
    s = _dist_error_text(ev)
    return any(
        k in s
        for k in (
            "socks",
            "proxy",
            "network unreachable",
            "connection refused",
            "connection reset",
            "forcibly closed",
            "wsarecv",
            "eof",
            "context deadline",
            "i/o timeout",
        )
    )


def _dist_event_hit_record(ev: dict) -> tuple[bool, str]:
    if not isinstance(ev, dict):
        return False, ""
    if ev.get("ok") is True and str(ev.get("type") or "") == "result":
        return True, str(ev.get("phone") or "")
    row = ev.get("row") if isinstance(ev.get("row"), dict) else ev
    if isinstance(row, dict):
        code = row.get("code")
        if str(code) == "2000" or code == 2000:
            rid = row.get("priorityRecordId") or (row.get("data") or {}).get("priorityRecordId") if isinstance(row.get("data"), dict) else ""
            return True, str(rid or ev.get("phone") or "")
    return False, ""


def _dist_update_node_event(node_id: str, ev: dict) -> None:
    now = time.monotonic()
    now_ts = int(time.time())
    with _dist_lock:
        node = _dist_nodes.get(node_id)
        if not node:
            return
        stats = node.setdefault(
            "stats",
            {
                "shots": 0,
                "success": 0,
                "failed": 0,
                "tlsFail": 0,
                "proxyFail": 0,
                "lastHitAt": 0,
                "lastHit": "",
            },
        )
        typ = str((ev or {}).get("type") or "")
        node["lastEventAt"] = now_ts
        node["lastEventMono"] = now
        if typ == "shot":
            stats["shots"] = int(stats.get("shots") or 0) + 1
            shot_times = node.setdefault("shotTimes", [])
            if isinstance(shot_times, list):
                shot_times.append(now)
                del shot_times[:-2000]
        if typ == "result":
            if (ev or {}).get("ok"):
                stats["success"] = int(stats.get("success") or 0) + 1
            else:
                stats["failed"] = int(stats.get("failed") or 0) + 1
        if _dist_event_is_tls_fail(ev or {}):
            stats["tlsFail"] = int(stats.get("tlsFail") or 0) + 1
        if _dist_event_is_proxy_fail(ev or {}):
            stats["proxyFail"] = int(stats.get("proxyFail") or 0) + 1
        hit, label = _dist_event_hit_record(ev or {})
        if hit:
            stats["lastHitAt"] = now_ts
            stats["lastHit"] = label


def _conc_dbg_reset() -> dict:
    """清零峰值计数；若仍有请求未结束，insideNow>0 时仍会清零 peak（见返回说明）。"""
    global _conc_dbg_peak, _conc_dbg_inside
    with _conc_dbg_lock:
        inside_now = _conc_dbg_inside
        _conc_dbg_peak = 0
    return {"insideNow": inside_now, "note": "已重置 peak；请立即发起一批并行 hold 请求"}


def _conc_dbg_hold(ms: int) -> dict:
    global _conc_dbg_inside, _conc_dbg_peak
    ms = max(200, min(8000, int(ms)))
    with _conc_dbg_lock:
        _conc_dbg_inside += 1
        if _conc_dbg_inside > _conc_dbg_peak:
            _conc_dbg_peak = _conc_dbg_inside
        depth_on_enter = _conc_dbg_inside
    try:
        time.sleep(ms / 1000.0)
    finally:
        with _conc_dbg_lock:
            _conc_dbg_inside -= 1
    with _conc_dbg_lock:
        peak = _conc_dbg_peak
    return {
        "holdMs": ms,
        "depthOnEnter": depth_on_enter,
        "peakAfterThisHold": peak,
    }


def _conc_dbg_result() -> dict:
    with _conc_dbg_lock:
        return {
            "peakConcurrent": _conc_dbg_peak,
            "insideNow": _conc_dbg_inside,
        }


def _json_truthy(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(int(v))
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _truthy(v: object) -> bool:
    """兼容旧调用点：等价于 _json_truthy。"""
    return _json_truthy(v)


def _json_false_explicit(v: object) -> bool:
    if isinstance(v, bool):
        return v is False
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return int(v) == 0
    return str(v).strip().lower() in ("0", "false", "no", "off")


def _resolve_cloudflared_bin() -> str | None:
    custom = (os.environ.get("MT_CLOUDFLARED_BIN") or "").strip()
    if custom:
        return custom
    local = ROOT / ("cloudflared.exe" if os.name == "nt" else "cloudflared")
    if local.is_file():
        return str(local)
    found = shutil.which("cloudflared")
    if found:
        return found
    return None


class _PublicPageTunnelManager:
    """WebUI lifecycle owned public access for public pages and the allowlisted console."""

    def __init__(self, webui_port: int):
        self.webui_port = int(webui_port)
        self.login_port = int(os.environ.get("MT_PUBLIC_LOGIN_PORT") or "7979")
        self.order_port = int(os.environ.get("MT_PUBLIC_ORDER_PORT") or "6969")
        self.console_enabled = _truthy(os.environ.get("MT_CLOUDFLARED_CONSOLE") or "1")
        self.gate_procs: list[subprocess.Popen[str]] = []
        self.cf_procs: list[subprocess.Popen[str]] = []
        self.reader_threads: list[threading.Thread] = []
        self.urls: dict[str, str] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if not _truthy(os.environ.get("MT_CLOUDFLARED_AUTOSTART") or "1"):
            print("[cloudflared] 已禁用自动穿透（MT_CLOUDFLARED_AUTOSTART=0）。", flush=True)
            return
        if not _truthy(os.environ.get("MT_CLOUDFLARED_PUBLIC_PAGES") or "1"):
            print("[cloudflared] 已禁用公开页穿透（MT_CLOUDFLARED_PUBLIC_PAGES=0）。", flush=True)
            return
        gate_script = ROOT / "public_gate.py"
        if not gate_script.exists():
            print(f"[public-gate] 缺少 {gate_script.name}，跳过公开页穿透。", flush=True)
            return

        entries = (
            ("login", "上号页", "login", self.login_port, "remote_login.html"),
            ("order", "查单页", "order", self.order_port, "mobile_order.html"),
        )
        for label, title, mode, port, page in entries:
            self._start_gate(gate_script, label, title, mode, port, page)

        bin_path = _resolve_cloudflared_bin()
        if not bin_path:
            print("[cloudflared] 未找到 cloudflared，已只启动本地白名单端口。", flush=True)
            return

        for label, title, _mode, port, page in entries:
            self._start_cloudflared(bin_path, label, title, port, page)
        if self.console_enabled:
            self._start_cloudflared(bin_path, "console", "控制台", self.webui_port, "")

    def _start_gate(self, gate_script: Path, label: str, title: str, mode: str, port: int, page: str) -> None:
        env = os.environ.copy()
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PUBLIC_GATE_REQUIRE_TOKEN": "0",
                "PUBLIC_GATE_LISTEN_HOST": "127.0.0.1",
                "PUBLIC_GATE_MODE": mode,
                "PUBLIC_GATE_PORT": str(port),
                "PUBLIC_GATE_TARGET": f"http://127.0.0.1:{self.webui_port}",
            }
        )
        cmd = [sys.executable, str(gate_script)]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
        except Exception as ex:
            print(f"[public-gate-{label}] 启动失败: {ex}", flush=True)
            return
        self.gate_procs.append(proc)
        self._spawn_reader(proc, f"public-gate-{label}")
        print(f"[public-gate-{label}] {title} 本地入口: http://127.0.0.1:{port}/{page}", flush=True)

    def _start_cloudflared(self, bin_path: str, label: str, title: str, port: int, page: str) -> None:
        cmd = [bin_path, "tunnel", "--protocol", "http2", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as ex:
            print(f"[cloudflared-{label}] 启动失败: {ex}", flush=True)
            return
        self.cf_procs.append(proc)
        self._spawn_reader(proc, f"cloudflared-{label}", label, title, page)
        print(f"[cloudflared-{label}] 已启动: {' '.join(cmd)}", flush=True)

    def _spawn_reader(
        self,
        proc: subprocess.Popen[str],
        prefix: str,
        label: str | None = None,
        title: str | None = None,
        page: str | None = None,
    ) -> None:
        t = threading.Thread(
            target=self._read_output_loop,
            args=(proc, prefix, label, title, page),
            daemon=True,
            name=f"{prefix}-reader",
        )
        self.reader_threads.append(t)
        t.start()

    def _read_output_loop(
        self,
        proc: subprocess.Popen[str],
        prefix: str,
        label: str | None,
        title: str | None,
        page: str | None,
    ) -> None:
        if proc.stdout is None:
            return
        url_re = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
        for line in proc.stdout:
            s = str(line or "").strip()
            if not s:
                continue
            m = url_re.search(s)
            if m and label and title and page is not None:
                self._record_public_url(label, title, m.group(0).rstrip("/"), page)
                continue
            low = s.lower()
            if "error" in low or "failed" in low or "forbidden" in low:
                print(f"[{prefix}] {s}", flush=True)

    def _record_public_url(self, label: str, title: str, base: str, page: str | None) -> None:
        full = f"{base}/{page}" if page else base
        with self._lock:
            if self.urls.get(label) == full:
                return
            self.urls[label] = full
            print(f"[cloudflared-{label}] {title}公网入口: {full}", flush=True)
            self._write_url_file()

    def _write_url_file(self) -> None:
        try:
            lines = [
                "# trycloudflare 为临时域名，软件重启或 cloudflared 退出后会失效。",
                "# 7979=上号白名单端口；6969=查单白名单端口；8989=控制台白名单入口。",
            ]
            if "login" in self.urls:
                lines.append(f"上号页={self.urls['login']}")
            if "order" in self.urls:
                lines.append(f"查单页={self.urls['order']}")
            if "console" in self.urls:
                lines.append(f"控制台={self.urls['console']}")
            (ROOT / "remote_tunnel_url.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    def stop(self) -> None:
        for proc in list(self.cf_procs) + list(self.gate_procs):
            try:
                proc.terminate()
                proc.wait(timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.cf_procs.clear()
        self.gate_procs.clear()


def _start_public_page_tunnels_if_needed() -> None:
    global _PUBLIC_PAGE_TUNNEL
    if _PUBLIC_PAGE_TUNNEL is not None:
        return
    mgr = _PublicPageTunnelManager(PORT)
    _PUBLIC_PAGE_TUNNEL = mgr
    mgr.start()


def _stop_public_page_tunnels() -> None:
    global _PUBLIC_PAGE_TUNNEL
    mgr = _PUBLIC_PAGE_TUNNEL
    _PUBLIC_PAGE_TUNNEL = None
    if mgr is not None:
        mgr.stop()


def _prefer_pool_rotate_from_payload(payload: dict) -> bool:
    """订单链路默认走代理池轮换；只有显式 false 才回到旧的中号出口钉线。"""
    if not isinstance(payload, dict):
        return True
    for key in ("preferPoolRotate", "autoChainPreferPoolRotate"):
        if key in payload and _json_false_explicit(payload.get(key)):
            return False
    return True


def _realname_submit_msg(out: object) -> str:
    if not isinstance(out, dict):
        return ""
    msg = str(out.get("message") or "").strip()
    if msg:
        return msg
    d = out.get("data")
    if isinstance(d, dict):
        return str(d.get("message") or d.get("msg") or "").strip()
    return ""


def _realname_submit_soft_retry(msg: str) -> bool:
    s = str(msg or "").strip().lower()
    if not s:
        return False
    kws = (
        "稍后重试",
        "稍后再试",
        "请稍后",
        "请稍候",
        "操作频繁",
        "系统繁忙",
        "繁忙",
        "限流",
        "too many",
        "try again",
    )
    return any(k in s for k in kws)


def _realname_submit_implies_verified(msg: str) -> bool:
    s = str(msg or "").strip()
    if not s:
        return False
    kws = (
        "已实名",
        "已认证",
        "已被认证",
        "证件已认证",
        "重复提交",
        "身份证已",
        "实名信息",
    )
    return any(k in s for k in kws)


def _mt_result_code(out: object) -> int | None:
    if not isinstance(out, dict):
        return None
    c = out.get("code")
    if c is None and isinstance(out.get("data"), dict):
        c = out["data"].get("code")
    try:
        return int(c) if c is not None and str(c).strip() != "" else None
    except Exception:
        return None


def _mt_result_message(out: object) -> str:
    if not isinstance(out, dict):
        return ""
    msg = str(out.get("message") or "").strip()
    if msg:
        return msg
    d = out.get("data")
    if isinstance(d, dict):
        return str(d.get("message") or d.get("msg") or "").strip()
    return ""


def _mt_should_rotate_retry(out: object) -> bool:
    code = _mt_result_code(out)
    if code == 2000:
        return False
    msg = _mt_result_message(out).lower()
    if code == 429:
        return True
    kws = (
        "429",
        "限流",
        "操作频繁",
        "系统繁忙",
        "繁忙",
        "稍后重试",
        "稍后再试",
        "请稍后",
        "请稍候",
        "too many",
        "try again",
        "timeout",
        "timed out",
        "network",
        "failed to fetch",
        "connection",
        "连接",
    )
    return any(k in msg for k in kws)


def _rotate_entry_proxy_for_retry(e: object, s: object | None = None, *, step: str = "") -> bool:
    rotated = False
    new_url = ""
    try:
        if s is not None and hasattr(s, "_rotate_next_proxy") and hasattr(s, "_set_proxy_from_rotation"):
            new_url = str(s._rotate_next_proxy() or "").strip()  # type: ignore[attr-defined]
            s._set_proxy_from_rotation(new_url)  # type: ignore[attr-defined]
            rotated = True
    except Exception:
        rotated = False
    if not rotated:
        try:
            import proxy_pool as _pp

            if _pp.pool_is_configured():
                new_url = str(_pp.rotate_next_url() or "").strip()
                rotated = True
        except Exception:
            rotated = False
    if not rotated:
        return False
    try:
        if hasattr(e, "proxy"):
            e.proxy = new_url  # type: ignore[attr-defined]
        if hasattr(e, "_session"):
            e._session = None  # type: ignore[attr-defined]
        if hasattr(e, "_session_proxy_src"):
            e._session_proxy_src = None  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        print(f"[remote-onboard] {step or 'step'} 失败后已轮换出口 IP", flush=True)
    except Exception:
        pass
    return True


def _remote_onboard_defer_teardown_after_send_code_fail(phone: str) -> None:
    """远程新户延迟落盘：发码失败/异常时解除屏蔽并落盘或移除仅内存中的空壳号。"""
    try:
        POOL.end_deferred_onboarding(phone)
    except Exception:
        pass
    try:
        e = POOL.get(phone)
        if e and e.status == STATUS_PENDING and not str(e.token or "").strip():
            POOL.remove(phone)
        else:
            POOL.save()
    except Exception:
        pass


def _one_port_per_account_enabled() -> bool:
    """
    True：监听端口数 = 当前账号数（上限见 MT_WEBUI_API_SHARD_MAX），每个号对应 PORT、PORT+1…，与 apiShardPhoneOrder（sorted 手机号）下标一致。
    未设置环境变量时默认 True（setdefault）；显式 MT_WEBUI_ONE_PORT_PER_ACCOUNT=0/false 可关回单端口。
    """
    v = (os.environ.get("MT_WEBUI_ONE_PORT_PER_ACCOUNT") or "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    return True


def _webui_api_shard_max() -> int:
    raw = (os.environ.get("MT_WEBUI_API_SHARD_MAX") or "512").strip()
    try:
        return max(1, min(1024, int(raw)))
    except (TypeError, ValueError):
        return 512


def _webui_api_shard_count() -> int:
    """端口数量：一账号一端口模式下与当前账号数对齐（ capped ）；否则由 MT_WEBUI_API_SHARD_COUNT 决定（默认 1，即仅主端口）。"""
    if _one_port_per_account_enabled():
        try:
            n_acct = len(getattr(POOL, "_phones", []) or [])
        except Exception:
            n_acct = 0
        cap = _webui_api_shard_max()
        return max(1, min(cap, max(1, n_acct)))
    raw = (os.environ.get("MT_WEBUI_API_SHARD_COUNT") or "1").strip()
    try:
        return max(1, min(32, int(raw)))
    except (TypeError, ValueError):
        return 1


def _prewarm_node_concurrency_for_targets(target_count: int) -> int:
    """
    单次 Node /prewarm 同时执行的预热子任务数上限（每条 target 一桩 HTTP）。
    预热 targets 已在服务端随机打乱，与抢购档序无关；默认较高并发以缩短总耗时。
    可用环境变量 MT_PREWARM_CONCURRENCY 覆盖（默认 28）。
    """
    tc = max(1, int(target_count or 1))
    raw = (os.environ.get("MT_PREWARM_CONCURRENCY") or "").strip()
    try:
        cap = int(raw) if raw else 36
    except (TypeError, ValueError):
        cap = 36
    cap = max(1, min(128, cap))
    cap = min(cap, max(1, _batch_rush_parallel_cap()), tc)
    return cap


def _host_header_hostname(host_header: str) -> str:
    """从 Host 头取出主机名（不含端口），IPv6 无方括号。"""
    h = (host_header or "").strip()
    if not h:
        return ""
    if h.startswith("["):
        end = h.find("]")
        if end > 1:
            return h[1:end].lower()
    if ":" in h:
        parts = h.rsplit(":", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0].strip("[]").lower()
    return h.strip("[]").lower()


def _http_origin_for_host_port(host: str, port: int) -> str:
    hn = (host or "").strip().strip("[]")
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", hn) or hn.lower() == "localhost":
        return f"http://{hn}:{port}"
    if ":" in hn:
        return f"http://[{hn}]:{port}"
    return f"http://{hn}:{port}"


def _both_loopback(a: str, b: str) -> bool:
    loop = {"127.0.0.1", "localhost", "::1"}
    return a.lower() in loop and b.lower() in loop


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower().strip("[]")
    return h in {"127.0.0.1", "localhost", "::1"}


def _go_bridge_http_get(path_on_bridge: str) -> tuple[bytes | None, str | None]:
    """
    GET 到 go_bridge（rush_bridge）。path_on_bridge 须以 / 开头；若当前 go 实现无该路径会返回错误。
    """
    np = _go_bridge_port()
    url = f"http://127.0.0.1:{np}{path_on_bridge}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            return resp.read(), None
    except urllib.error.HTTPError as ex:
        try:
            return ex.read(), None
        except Exception:
            return None, str(ex).strip() or "HTTPError"
    except Exception as ex:
        return None, str(ex).strip() or type(ex).__name__


def _go_bridge_health_snapshot(timeout: float = 3.0) -> dict:
    port = _go_bridge_port()
    url = f"http://127.0.0.1:{port}/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {"raw": data}
        data["reachable"] = True
        data["port"] = port
        return data
    except Exception as ex:
        return {
            "ok": False,
            "reachable": False,
            "port": port,
            "error": str(ex).strip() or type(ex).__name__,
        }


def _normalize_go_bridge_engine(value: object) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in ("", "dynamic", "reqrio", "reqrio_mt_random"):
        return "reqrio"
    if raw in ("ja3", "ja3_client", "tls_client", "tlsclient", "round_robin", "tls_client_round_robin_14"):
        return "ja3_client"
    raise ValueError("mode must be reqrio or ja3_client")


def _load_saved_go_bridge_engine() -> str | None:
    try:
        raw = json.loads(GO_BRIDGE_TLS_MODE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None
    try:
        if isinstance(raw, dict):
            value = raw.get("mode") or raw.get("engine")
        else:
            value = raw
        return _normalize_go_bridge_engine(value)
    except ValueError:
        return None


def _save_go_bridge_engine(engine: str) -> None:
    data = {
        "mode": _normalize_go_bridge_engine(engine),
        "updatedAt": int(time.time()),
    }
    GO_BRIDGE_TLS_MODE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _selected_go_bridge_engine() -> str:
    raw_env = (os.environ.get("MT_TLS_ENGINE") or "").strip()
    if raw_env:
        try:
            return _normalize_go_bridge_engine(raw_env)
        except ValueError:
            pass
    saved = _load_saved_go_bridge_engine()
    if saved:
        return saved
    return "reqrio"


def _apply_go_bridge_engine(engine: str) -> str:
    normalized = _normalize_go_bridge_engine(engine)
    os.environ["MT_GO_TLS_CLIENT"] = "1"
    os.environ["MT_TLS_ENGINE"] = normalized
    if normalized == "ja3_client":
        os.environ.setdefault("MT_GO_TLS_CLIENT_PROFILE", "chrome_138")
    return normalized


def _go_bridge_mode_snapshot() -> dict:
    health = _go_bridge_health_snapshot(timeout=1.8)
    env_engine = _selected_go_bridge_engine()
    running_mode = str(health.get("mode") or "").strip() or env_engine
    return {
        "envEngine": env_engine,
        "mode": running_mode,
        "tls": health.get("tls") or "",
        "port": health.get("port") or _go_bridge_port(),
        "reachable": bool(health.get("reachable")),
        "health": health,
        "modes": [
            {
                "id": "reqrio",
                "label": "动态 reqrio",
                "desc": "每次请求随机 custom_tls",
            },
            {
                "id": "ja3_client",
                "label": "tls-client 14套",
                "desc": "14套浏览器 TLS 指纹轮询",
            },
        ],
    }


def _kill_go_bridge_processes_for_switch() -> None:
    port = _go_bridge_port()
    try:
        _kill_node_on_port(port)
    except Exception:
        pass
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/IM", "rush_bridge.exe"],
                capture_output=True,
                timeout=5,
            )
        else:
            subprocess.run(["pkill", "-f", "rush_bridge"], capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        _wait_listen_port_free(port, max_wait=8.0)
    except Exception:
        time.sleep(0.8)


def _switch_go_bridge_mode(mode: object) -> dict:
    with _go_bridge_tls_mode_lock:
        engine = _apply_go_bridge_engine(_normalize_go_bridge_engine(mode))
        _save_go_bridge_engine(engine)
        _kill_go_bridge_processes_for_switch()
        try:
            _reset_go_bridge_boot_state()
        except Exception:
            pass
        ensure_go_bridge_if_configured()
        snap = _go_bridge_mode_snapshot()
        snap["requestedMode"] = engine
        return snap


def _rewrite_rush_panel_html_for_webui_proxy(raw: str) -> str:
    """经 WebUI 同源打开时，相对 /rush_tap 会打到 WebUI；改走本站 /node_rush_tap 代理。"""
    raw = raw.replace("fetch('/rush_tap", "fetch('/node_rush_tap")
    raw = raw.replace('fetch("/rush_tap', 'fetch("/node_rush_tap')
    return raw


def _cors_allow_origin(handler: BaseHTTPRequestHandler) -> str | None:
    """管理页在主端口、API 在后续分片端口时为跨源；仅允许与 Host 同主机或同属 loopback。"""
    origin = (handler.headers.get("Origin") or "").strip()
    if not origin:
        return None
    try:
        ou = urlparse(origin)
        o_host = (ou.hostname or "").lower().strip("[]")
    except Exception:
        return None
    # 浏览器同机跨端口（8989 -> 8990+）场景：优先放行 loopback origin，避免 Host 解析差异导致预检失败
    if _is_loopback_host(o_host):
        return origin
    req_h = _host_header_hostname(handler.headers.get("Host") or "")
    if not o_host:
        return None
    if not req_h:
        return origin if _is_loopback_host(o_host) else None
    if o_host == req_h:
        return origin
    if _both_loopback(o_host, req_h):
        return origin
    # 服务监听 0.0.0.0/:: 时，浏览器常用 127.0.0.1 或 localhost 访问，视作同机来源
    if req_h in {"0.0.0.0", "::"} and _is_loopback_host(o_host):
        return origin
    return None


def _api_listen_ports_snapshot() -> list[int]:
    with _api_ports_lock:
        return list(API_LISTEN_PORTS) if API_LISTEN_PORTS else [PORT]


def sync_api_listen_ports() -> None:
    """
    一账号一端口模式下：账号增删并已 save() 后调用，按当前账号数扩容/缩容监听（缩容仅关尾部端口，保留主端口 PORT）。
    """
    global API_LISTEN_PORTS, API_SERVERS
    if not _one_port_per_account_enabled():
        return
    desired = _webui_api_shard_count()
    with _api_ports_lock:
        if len(API_LISTEN_PORTS) == desired:
            return
        prev = list(API_LISTEN_PORTS)
        # 扩容
        while len(API_LISTEN_PORTS) < desired:
            if not API_LISTEN_PORTS:
                # 主端口 PORT 已由 bind_webui_servers 占用；勿重复 bind 导致 Address already in use
                API_LISTEN_PORTS.append(PORT)
                continue
            p = max(API_LISTEN_PORTS) + 1
            while p in API_LISTEN_PORTS:
                p += 1
            try:
                srv = ThreadingHTTPServer((LISTEN_HOST, p), Handler)
            except OSError as ex:
                print(f"[webui] 动态绑定端口 {p} 失败: {ex}", flush=True)
                break
            API_LISTEN_PORTS.append(p)
            API_SERVERS.append(srv)
            threading.Thread(
                target=srv.serve_forever,
                daemon=True,
                name=f"webui-{p}",
            ).start()
        # 缩容（永不移除主端口）
        while len(API_LISTEN_PORTS) > desired and len(API_LISTEN_PORTS) > 1:
            srv = API_SERVERS.pop()
            API_LISTEN_PORTS.pop()
            try:
                srv.shutdown()
                srv.server_close()
            except OSError as ex:
                print(f"[webui] 释放动态端口异常: {ex}", flush=True)
        if API_LISTEN_PORTS != prev:
            print(f"[webui] API 监听端口已随账号数调整: {API_LISTEN_PORTS}", flush=True)


def shutdown_all_webui_servers() -> None:
    """关闭本进程内全部 WebUI 监听（内嵌程序退出或 CLI 结束）。"""
    global API_LISTEN_PORTS, API_SERVERS
    _stop_public_page_tunnels()
    with _api_ports_lock:
        for srv in reversed(API_SERVERS):
            try:
                srv.shutdown()
                srv.server_close()
            except OSError:
                pass
        API_SERVERS.clear()
        API_LISTEN_PORTS.clear()


def _api_shard_bases_for_handler(handler: BaseHTTPRequestHandler) -> list[str]:
    ports = _api_listen_ports_snapshot()
    host = _host_header_hostname(handler.headers.get("Host") or "")
    if not host:
        host = LOCAL_UI_HOST
    return [_http_origin_for_host_port(host, p) for p in ports]


def _phone_key_for_api_shard(p: object) -> str:
    """与前端 _normPhoneShardKey 一致：去空白，避免 '138 0000 0000' 与 '13800000000' 对不上端口。"""
    return re.sub(r"\s+", "", str(p or "").strip())


def _api_shard_phone_order_for_snapshot() -> list[str] | None:
    """与 bind 时账号数顺序一致：sorted(phones)，供前端按下标选端口（一账号一端口）。"""
    if not _one_port_per_account_enabled():
        return None
    try:
        phones = getattr(POOL, "_phones", []) or []
        return sorted({_phone_key_for_api_shard(p) for p in phones if _phone_key_for_api_shard(p)})
    except Exception:
        return None


def _send_cors_headers(handler: BaseHTTPRequestHandler) -> None:
    aco = _cors_allow_origin(handler)
    if not aco:
        return
    handler.send_header("Access-Control-Allow-Origin", aco)
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type, Authorization, X-Remote-Login-Token",
    )
    handler.send_header("Access-Control-Max-Age", "86400")


def _try_bind_listen_port(port: int) -> tuple[int, ThreadingHTTPServer | None, str | None]:
    """在子线程中 bind+建 ThreadingHTTPServer，供多端口并行创建（缩短内嵌 Qt 冷启动前的阻塞）。"""
    try:
        srv = ThreadingHTTPServer((LISTEN_HOST, port), Handler)
        return (port, srv, None)
    except OSError as ex:
        return (port, None, str(ex))


def ensure_go_bridge_if_configured() -> None:
    """
    WebUI 启动时拉起本机 go_bridge（rush_bridge：req /batch /rush_loop）。
    与 MT_RUSH_HTTP_NODE / MT_BATCH_RUSH_NODE / MT_HTTP_BACKEND 一致；失败仅打印，不阻塞 WebUI。
    """
    global _go_bridge_boot_banner_printed
    if not _go_bridge_should_run():
        return
    with _go_bridge_tls_mode_lock:
        _apply_go_bridge_engine(_selected_go_bridge_engine())
        try:
            _ensure_go_bridge_running()
            p = _go_bridge_port()
            if not _go_bridge_boot_banner_printed:
                _go_bridge_boot_banner_printed = True
                print(
                    f"[go_bridge] 已随 WebUI 启动: http://127.0.0.1:{p}/req /batch /rush_loop | "
                    f"同源说明页: http://{LOCAL_UI_HOST}:{PORT}/node_rush_panel（每枪明细见各账号「操作记录」）",
                    flush=True,
                )
        except Exception as ex:
            print(
                f"[go_bridge] 随 WebUI 自动启动失败: {ex}\n"
                f"  请检查 go_bridge 目录、go_bridge/bridge.log；需本机 Go 时可重编 rush_bridge.exe。"
                f"（与 py_out/node_bridge 下 node server.cjs 的「旧 Node 桥 19090」不是同一进程。）",
                file=sys.stderr,
                flush=True,
            )


def schedule_go_bridge_boot_if_configured() -> None:
    """
    默认在后台线程拉起 go_bridge，避免阻塞 WebUI 绑定与内嵌首屏。
    抢购/组单首次请求时 app 仍会 _ensure_go_bridge_running()。
    同步等待就绪：MT_SYNC_GO_BRIDGE_BOOT=1；兼容旧变量名 MT_SYNC_NODE_BRIDGE_BOOT=1。
    """
    if not _go_bridge_should_run():
        return
    sync_raw = (os.environ.get("MT_SYNC_GO_BRIDGE_BOOT") or os.environ.get("MT_SYNC_NODE_BRIDGE_BOOT") or "")
    sync = sync_raw.strip().lower() in ("1", "true", "yes", "on")
    if sync:
        ensure_go_bridge_if_configured()
        return

    def _run() -> None:
        try:
            ensure_go_bridge_if_configured()
        except Exception:
            pass

    threading.Thread(
        target=_run,
        daemon=True,
        name="mt-go-bridge-boot",
    ).start()


# 兼容旧函数名：抢购 BB 已固定走 shield.py 的 py 稳定版链路，本函数只预热本机 wasm。
_node_bridge_boot_banner_printed = False


def ensure_node_bridge_if_configured() -> None:
    """Prewarm stable shield wasm for local rush BB calculation."""
    global _node_bridge_boot_banner_printed
    try:
        import shield
        shield.compute_wasm_sig("h_test", "test")
        pool_n = 0
        try:
            pool_n = shield.prewarm_pool()
        except Exception as ex_pool:
            print(f"[shield] 池子预填失败: {ex_pool}", file=sys.stderr, flush=True)
        if not _node_bridge_boot_banner_printed:
            _node_bridge_boot_banner_printed = True
            print(
                f"[shield] 已随 WebUI 预热：抢购 BB 固定使用 py 稳定版链路（池={pool_n}）",
                flush=True,
            )
    except Exception as ex:
        print(
            f"[shield] WebUI 预热失败: {ex}",
            file=sys.stderr,
            flush=True,
        )


_bb_auto_update_lock = threading.Lock()
_bb_auto_update_state: dict = {
    "enabled": False,
    "intervalSec": 0,
    "running": False,
    "lastStartedAt": "",
    "lastFinishedAt": "",
    "lastOk": None,
    "lastReport": None,
    "lastError": "",
}


def _bb_auto_update_enabled() -> bool:
    return (os.environ.get("MT_BB_AUTO_UPDATE") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _bb_auto_update_interval_sec() -> int:
    raw = (os.environ.get("MT_BB_AUTO_UPDATE_INTERVAL_SEC") or "21600").strip()
    try:
        return max(1800, min(7 * 86400, int(raw)))
    except (TypeError, ValueError):
        return 21600


def _bb_auto_update_on_start() -> bool:
    return (os.environ.get("MT_BB_AUTO_UPDATE_ON_START") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _nodejs_executable() -> str:
    for name in ("node", "nodejs"):
        found = shutil.which(name)
        if found:
            return found
    return "node"


def bb_auto_update_snapshot() -> dict:
    with _bb_auto_update_lock:
        return dict(_bb_auto_update_state)


def read_bb_resources_snapshot() -> dict:
    """Current node_bridge/bb/version.json + auto-update scheduler state (no device)."""
    path = ROOT / "node_bridge" / "bb" / "version.json"
    out: dict = {"autoUpdate": bb_auto_update_snapshot()}
    try:
        raw = path.read_text(encoding="utf-8")
        v = json.loads(raw)
        sign = v.get("signModule") if isinstance(v.get("signModule"), dict) else {}
        runtime = v.get("runtime") if isinstance(v.get("runtime"), dict) else {}
        out.update(
            {
                "appVersion": v.get("appVersion"),
                "versionCode": v.get("versionCode"),
                "sdkVersion": v.get("sdkVersion"),
                "signWasmVersion": v.get("signWasmVersion"),
                "signModuleSha256": sign.get("sha256"),
                "signModuleSize": sign.get("size"),
                "runtimeUpstream": runtime.get("upstreamName"),
                "runtimeSha256": runtime.get("sha256"),
                "checkedAt": v.get("checkedAt"),
                "headless": True,
            }
        )
    except Exception as ex:
        out["error"] = str(ex)
    return out


def _bb_update_cli_mode() -> str:
    mode = (os.environ.get("MT_BB_UPDATE_MODE") or "sync").strip().lower()
    return mode if mode in ("sync", "auto") else "sync"


def _run_bb_update_script(mode: str) -> dict:
    script = ROOT / "node_bridge" / "bb" / "update.cjs"
    node_bin = _nodejs_executable()
    started = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    with _bb_auto_update_lock:
        if _bb_auto_update_state.get("running"):
            return {"ok": False, "skipped": True, "reason": "already_running"}
        _bb_auto_update_state["running"] = True
        _bb_auto_update_state["lastStartedAt"] = started
        _bb_auto_update_state["lastError"] = ""

    try:
        cli_mode = mode if mode in ("sync", "auto", "check") else "sync"
        proc = subprocess.run(
            [node_bin, str(script), f"--{cli_mode}"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=600,
            env=os.environ.copy(),
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        report = None
        if stdout:
            try:
                report = json.loads(stdout)
            except json.JSONDecodeError:
                report = {"rawStdout": stdout[-4000:]}
        ok = proc.returncode == 0 and isinstance(report, dict)
        err = ""
        if not ok:
            err = stderr or stdout or f"exit {proc.returncode}"
        finished = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        with _bb_auto_update_lock:
            _bb_auto_update_state["lastFinishedAt"] = finished
            _bb_auto_update_state["lastOk"] = ok
            _bb_auto_update_state["lastReport"] = report
            _bb_auto_update_state["lastError"] = err
        if ok and isinstance(report, dict) and report.get("applied"):
            print(
                f"[bb-update] 自动已应用 app={report.get('next', {}).get('appVersion')} "
                f"sign={report.get('next', {}).get('signWasmVersion')}",
                flush=True,
            )
        elif ok:
            print("[bb-update] 自动检查：无变更", flush=True)
        else:
            print(f"[bb-update] 自动更新失败: {err[:500]}", file=sys.stderr, flush=True)
        return {"ok": ok, "report": report, "error": err}
    except Exception as ex:
        finished = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        with _bb_auto_update_lock:
            _bb_auto_update_state["lastFinishedAt"] = finished
            _bb_auto_update_state["lastOk"] = False
            _bb_auto_update_state["lastError"] = str(ex)
        print(f"[bb-update] 自动更新异常: {ex}", file=sys.stderr, flush=True)
        return {"ok": False, "error": str(ex)}
    finally:
        with _bb_auto_update_lock:
            _bb_auto_update_state["running"] = False


def run_bb_auto_update_once() -> dict:
    """Scheduled update: MT_BB_UPDATE_MODE=sync|auto (default sync = always pull latest)."""
    return _run_bb_update_script(_bb_update_cli_mode())


def run_bb_fetch_latest_once() -> dict:
    """Force headless pull of latest H5 runtime + Bangcle sign wasm (--sync)."""
    return _run_bb_update_script("sync")


def schedule_bb_auto_update() -> None:
    """Periodic MT_BB_AUTO_UPDATE: App Store version + sign_wasm/runtime refresh."""
    enabled = _bb_auto_update_enabled()
    interval = _bb_auto_update_interval_sec()
    with _bb_auto_update_lock:
        _bb_auto_update_state["enabled"] = enabled
        _bb_auto_update_state["intervalSec"] = interval
    if not enabled:
        return

    def _loop() -> None:
        if _bb_auto_update_on_start():
            try:
                run_bb_auto_update_once()
            except Exception:
                pass
        while True:
            time.sleep(interval)
            try:
                run_bb_auto_update_once()
            except Exception:
                pass

    threading.Thread(
        target=_loop,
        daemon=True,
        name="mt-bb-auto-update",
    ).start()


def schedule_node_bridge_boot() -> None:
    """后台线程预热 shield.py，不阻塞 WebUI 绑定。
    同步等待就绪：MT_SYNC_NODE_BRIDGE_BOOT=1
    """
    sync = (os.environ.get("MT_SYNC_NODE_BRIDGE_BOOT") or "").strip().lower() in ("1", "true", "yes", "on")
    if sync:
        ensure_node_bridge_if_configured()
        return

    def _run() -> None:
        try:
            ensure_node_bridge_if_configured()
        except Exception:
            pass

    threading.Thread(
        target=_run,
        daemon=True,
        name="mt-node-bridge-boot",
    ).start()


_bridge_rewarm_after_stop_lock = threading.Lock()
_bridge_rewarm_after_stop_running = False


def _bridge_rewarm_after_stop_delay_sec() -> float:
    raw = (os.environ.get("MT_BRIDGE_REWARM_AFTER_STOP_DELAY") or "0.5").strip()
    try:
        return max(0.0, min(10.0, float(raw)))
    except (TypeError, ValueError):
        return 0.5


def _bridge_rewarm_after_stop_enabled() -> bool:
    raw = (os.environ.get("MT_BRIDGE_REWARM_AFTER_STOP") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def schedule_bridge_rewarm_after_stop(reason: str = "stop") -> None:
    """After a rush stop kills the bridge, re-open helper bridges in background."""
    if not _bridge_rewarm_after_stop_enabled():
        return
    global _bridge_rewarm_after_stop_running
    with _bridge_rewarm_after_stop_lock:
        if _bridge_rewarm_after_stop_running:
            return
        _bridge_rewarm_after_stop_running = True

    def _run() -> None:
        global _bridge_rewarm_after_stop_running
        t0 = time.perf_counter()
        go_ok = not _go_bridge_should_run()
        node_ok = False
        try:
            delay = _bridge_rewarm_after_stop_delay_sec()
            if delay > 0:
                time.sleep(delay)
            try:
                _reset_go_bridge_boot_state()
            except Exception:
                pass
            if _go_bridge_should_run():
                try:
                    ensure_go_bridge_if_configured()
                    go_ok = True
                except Exception as ex:
                    print(f"[bridge] stop 后 go_bridge 预热失败({reason}): {ex}", file=sys.stderr, flush=True)
            try:
                ensure_node_bridge_if_configured()
                node_ok = True
            except Exception as ex:
                print(f"[bridge] stop 后 shield 预热失败({reason}): {ex}", file=sys.stderr, flush=True)
            ms = int((time.perf_counter() - t0) * 1000)
            print(f"[bridge] stop 后预热完成 reason={reason} go={go_ok} py_bb={node_ok} {ms}ms", flush=True)
        finally:
            with _bridge_rewarm_after_stop_lock:
                _bridge_rewarm_after_stop_running = False

    threading.Thread(
        target=_run,
        daemon=True,
        name="mt-bridge-rewarm-after-stop",
    ).start()


def bind_webui_servers(*, for_embedded: bool = False) -> list[ThreadingHTTPServer]:
    """
    绑定 PORT .. PORT+N-1。N：一账号一端口时≈当前账号数（见 MT_WEBUI_API_SHARD_MAX），
    否则由 MT_WEBUI_API_SHARD_COUNT（默认 1，单端口）决定。
    for_embedded=True 时全部在守护线程 serve_forever（供 Qt 内嵌）；
    否则仅主端口由调用方 serve_forever，其余端口在后台线程。
    """
    global API_LISTEN_PORTS, API_SERVERS
    schedule_go_bridge_boot_if_configured()
    schedule_node_bridge_boot()
    schedule_bb_auto_update()
    start_distributed_ws_server()
    n = _webui_api_shard_count()
    main_p = PORT
    try:
        main_srv = ThreadingHTTPServer((LISTEN_HOST, main_p), Handler)
    except OSError as ex:
        raise OSError(f"主端口 {main_p} 不可用: {ex}") from ex

    pairs: list[tuple[int, ThreadingHTTPServer]] = [(main_p, main_srv)]
    if n > 1:
        # 端口绑定失败时顺延到下一个端口，保证 pairs 数量 == n，phone→port 索引不错位
        needed = n - 1
        candidate = PORT + 1
        max_candidate = PORT + n + 64  # 最多多试 64 个端口
        while needed > 0 and candidate <= max_candidate:
            if candidate == main_p:
                candidate += 1
                continue
            p, srv, errmsg = _try_bind_listen_port(candidate)
            if srv is not None:
                pairs.append((p, srv))
                needed -= 1
            else:
                print(f"[webui] 端口 {candidate} 不可用({errmsg})，尝试下一个", flush=True)
            candidate += 1
        if needed > 0:
            print(f"[webui] 警告：仍有 {needed} 个端口未能绑定，phone→port 映射可能不完整", flush=True)
    pairs.sort(key=lambda x: x[0])

    servers: list[ThreadingHTTPServer] = []
    with _api_ports_lock:
        # 必须 clear() 勿整表赋值：否则 `from webui_server import API_LISTEN_PORTS` 仍指向旧空列表
        API_LISTEN_PORTS.clear()
        API_SERVERS.clear()
        for p, srv in pairs:
            API_LISTEN_PORTS.append(p)
            API_SERVERS.append(srv)
            servers.append(srv)
        if not servers:
            raise OSError("未能绑定任何 WebUI 端口（请检查端口占用或减小 MT_WEBUI_API_SHARD_COUNT）")

    for i, srv in enumerate(servers):
        p = srv.server_address[1]
        if for_embedded or i > 0:
            threading.Thread(
                target=srv.serve_forever,
                daemon=True,
                name=f"webui-{p}",
            ).start()
    _start_public_page_tunnels_if_needed()
    return servers
# 远程上号页与对应 API 可选口令（设 MT_REMOTE_LOGIN_SECRET 后须带 ?token= 或请求头/JSON）
REMOTE_LOGIN_SECRET = (os.environ.get("MT_REMOTE_LOGIN_SECRET") or "").strip()
# 内网穿透/域名（如 ngrok、frp）公网根 URL，不设则仅打印局域网 IP；设后控制台多印一条「发给异网」的链接
PUBLIC_BASE_URL = (os.environ.get("MT_PUBLIC_BASE_URL") or "").strip().rstrip("/")
CONTROL_CONSOLE_IP_ALLOWLIST_RAW = (
    os.environ.get("MT_WEBUI_CONSOLE_IP_ALLOWLIST") or "*"
).strip()

PUBLIC_REMOTE_GET_PATHS = {
    "/remote_login",
    "/remote_login.html",
    "/mobile_order",
    "/mobile_order.html",
    "/api/address/pcas",
}
PUBLIC_REMOTE_POST_PATHS = {
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


def _parse_ip_allowlist(raw: str) -> tuple[list[ipaddress._BaseNetwork], bool]:
    networks: list[ipaddress._BaseNetwork] = []
    allow_all = False
    for part in (raw or "").replace(";", ",").split(","):
        item = part.strip()
        if not item:
            continue
        if item in ("*", "0.0.0.0/0", "::/0"):
            allow_all = True
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            print(f"[webui] 忽略无效控制台白名单 IP: {item}", flush=True)
    return networks, allow_all


CONTROL_CONSOLE_IP_ALLOWLIST, CONTROL_CONSOLE_IP_ALLOW_ALL = _parse_ip_allowlist(
    CONTROL_CONSOLE_IP_ALLOWLIST_RAW
)
# 最近一次 rush/compose/submit 的茅台原始 JSON，便于排查（覆盖写入）
DEBUG_LAST_API = ROOT / "debug_last_api.json"
DEBUG_LAST_API_MIN_INTERVAL_MS = max(
    0,
    int(float(os.environ.get("MT_DEBUG_LAST_API_MIN_INTERVAL_MS", "1200") or 1200)),
)
_DEBUG_LAST_API_LAST_TS: dict[str, float] = {}
_DEBUG_LAST_API_TS_LOCK = threading.Lock()

try:
    address_db.init_schema()
    _addr_mig = address_db.migrate_missing_from_ui_state()
    if int(_addr_mig.get("inserted") or 0) > 0:
        print(
            f"[address_db] 已从 ui_state 补录 {_addr_mig.get('inserted')} 个账号的地址缓存",
            flush=True,
        )
except Exception as _addr_db_ex:
    print(f"[address_db] 初始化跳过: {_addr_db_ex}", flush=True)
PROXY_POOL_FILE = ROOT / "local_proxy_pool.txt"
PROXY_POOL_IMPORT_LOG = ROOT / "logs" / "proxy_api_import.jsonl"
_PROXY_POOL_FILE_LOCK = threading.RLock()
_PROXY_FILL_LOCK = threading.RLock()
_PROXY_FILL_STOP = threading.Event()
_PROXY_FILL_THREAD: threading.Thread | None = None
_PROXY_FILL_STATUS: dict = {
    "running": False,
    "startedAt": None,
    "stoppedAt": None,
    "targetCount": 0,
    "currentCount": 0,
    "rounds": 0,
    "totalPulled": 0,
    "totalAdded": 0,
    "totalInvalid": 0,
    "failures": 0,
    "message": "idle",
    "lastError": "",
    "lastSources": [],
    "lastRound": None,
}
# 浏览器镜像、机器人 /grab/*：grab_bot_ops.py


def _primary_lan_ipv4() -> str:
    """尽力取本机用于访问外网的 IPv4，便于打印「局域网入口」提示。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.25)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return str(ip or "").strip()
    except OSError:
        return ""


def _client_is_loopback(handler: BaseHTTPRequestHandler) -> bool:
    try:
        host = str(handler.client_address[0] or "").strip()
    except Exception:
        return False
    if host in ("127.0.0.1", "::1", "localhost"):
        return True
    if host.startswith("127."):
        return True
    # IPv4-mapped
    return host.endswith("127.0.0.1")


def _local_host_ip_set() -> set[str]:
    out: set[str] = {"127.0.0.1", "::1", "localhost"}
    try:
        lan = _primary_lan_ipv4()
        if lan:
            out.add(lan)
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        for item in socket.gethostbyname_ex(hostname)[2]:
            val = str(item or "").strip()
            if val:
                out.add(val)
    except Exception:
        pass
    return out


def _client_ip(handler: BaseHTTPRequestHandler) -> str:
    for name in ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For"):
        value = (handler.headers.get(name) or "").strip()
        if not value:
            continue
        if name.lower() == "x-forwarded-for":
            value = value.split(",", 1)[0].strip()
        if value:
            return value
    try:
        return str(handler.client_address[0] or "").strip()
    except Exception:
        return ""


def _control_console_ip_allowed(handler: BaseHTTPRequestHandler) -> bool:
    if CONTROL_CONSOLE_IP_ALLOW_ALL:
        return True
    if _client_is_loopback(handler):
        return True
    raw = _client_ip(handler)
    if raw in _local_host_ip_set():
        return True
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return any(ip in net for net in CONTROL_CONSOLE_IP_ALLOWLIST)


def _path_is_public_remote_get(path: str) -> bool:
    return path in PUBLIC_REMOTE_GET_PATHS


def _path_is_public_remote_post(path: str) -> bool:
    return path in PUBLIC_REMOTE_POST_PATHS


def _dist_node_public(node_id: str, node: dict) -> dict:
    stats = node.get("stats") if isinstance(node.get("stats"), dict) else {}
    shot_times = node.get("shotTimes") if isinstance(node.get("shotTimes"), list) else []
    now = time.monotonic()
    recent_shots = 0
    if isinstance(shot_times, list):
        recent_shots = sum(1 for t in shot_times if now - float(t or 0) <= 1.0)
    cfg = _dist_get_node_config(node_id)
    caps = node.get("caps") if isinstance(node.get("caps"), dict) else {}
    configured_conc = int(cfg.get("concurrency") or 0)
    return {
        "id": node_id,
        "name": node.get("name") or node_id,
        "addr": node.get("addr") or "",
        "connectedAt": node.get("connectedAt") or 0,
        "lastSeenAt": node.get("lastSeenAt") or 0,
        "busy": bool(node.get("busy")),
        "jobId": node.get("jobId") or "",
        "pid": node.get("pid") or 0,
        "cpuPercent": node.get("cpuPercent"),
        "currentTasks": int(node.get("currentTasks") or 0),
        "taskTotal": int(node.get("taskTotal") or 0),
        "accountCount": int(node.get("accountCount") or 0),
        "concurrency": configured_conc or DIST_NODE_DEFAULT_CONCURRENCY,
        "concurrencyConfigured": bool(configured_conc),
        "defaultConcurrency": DIST_NODE_DEFAULT_CONCURRENCY,
        "lastEventAt": node.get("lastEventAt") or 0,
        "shotsPerSec": recent_shots,
        "stats": {
            "shots": int(stats.get("shots") or 0),
            "success": int(stats.get("success") or 0),
            "failed": int(stats.get("failed") or 0),
            "tlsFail": int(stats.get("tlsFail") or 0),
            "proxyFail": int(stats.get("proxyFail") or 0),
            "lastHitAt": int(stats.get("lastHitAt") or 0),
            "lastHit": stats.get("lastHit") or "",
        },
        "caps": caps,
    }


def _dist_status_snapshot() -> dict:
    now = time.monotonic()
    with _dist_lock:
        nodes = [
            _dist_node_public(node_id, node)
            for node_id, node in _dist_nodes.items()
            if now - float(node.get("lastSeenMono") or 0) <= DIST_NODE_STALE_SEC
        ]
    return {
        "wsHost": DIST_WS_HOST,
        "wsPort": DIST_WS_PORT,
        "online": len(nodes),
        "nodes": nodes,
    }


def _dist_idle_node_ids() -> list[str]:
    now = time.monotonic()
    with _dist_lock:
        return [
            node_id
            for node_id, node in _dist_nodes.items()
            if not node.get("busy")
            and now - float(node.get("lastSeenMono") or 0) <= DIST_NODE_STALE_SEC
        ]


def _dist_mark_node(node_id: str, *, busy: bool | None = None, job_id: str | None = None) -> None:
    with _dist_lock:
        node = _dist_nodes.get(node_id)
        if not node:
            return
        if busy is not None:
            node["busy"] = bool(busy)
        if job_id is not None:
            node["jobId"] = str(job_id or "")


def _dist_job_for(job_id: str) -> _DistJob | None:
    with _dist_lock:
        return _dist_jobs.get(str(job_id or ""))


def _dist_handle_worker_message(node_id: str, msg: dict) -> None:
    typ = str((msg or {}).get("type") or "").strip()
    job_id = str((msg or {}).get("jobId") or "").strip()
    if not job_id:
        return
    job = _dist_job_for(job_id)
    if not job:
        return
    job.put(node_id, msg)


async def _dist_ws_handler(ws, path=None):
    node_id = ""
    remote = ""
    try:
        try:
            remote = str(getattr(ws, "remote_address", "") or "")
        except Exception:
            remote = ""
        async for raw in ws:
            try:
                msg = json.loads(str(raw))
            except Exception:
                await ws.send(json.dumps({"type": "error", "message": "bad json"}, ensure_ascii=False))
                continue
            typ = str(msg.get("type") or "").strip()
            if typ == "hello":
                node_id = str(msg.get("nodeId") or "").strip() or f"node-{uuid.uuid4().hex[:8]}"
                name = str(msg.get("name") or node_id).strip() or node_id
                caps = msg.get("caps") if isinstance(msg.get("caps"), dict) else {}
                pid = msg.get("pid") or 0
                with _dist_lock:
                    prev = _dist_nodes.get(node_id) if isinstance(_dist_nodes.get(node_id), dict) else {}
                    prev_stats = prev.get("stats") if isinstance(prev.get("stats"), dict) else {
                        "shots": 0,
                        "success": 0,
                        "failed": 0,
                        "tlsFail": 0,
                        "proxyFail": 0,
                        "lastHitAt": 0,
                        "lastHit": "",
                    }
                    _dist_nodes[node_id] = {
                        "ws": ws,
                        "name": name,
                        "addr": remote,
                        "pid": pid,
                        "caps": caps,
                        "connectedAt": int(time.time()),
                        "lastSeenAt": int(time.time()),
                        "lastSeenMono": time.monotonic(),
                        "busy": False,
                        "jobId": "",
                        "currentTasks": 0,
                        "taskTotal": 0,
                        "accountCount": 0,
                        "cpuPercent": None,
                        "stats": prev_stats,
                        "shotTimes": [],
                    }
                await ws.send(
                    json.dumps(
                        {
                            "type": "hello_ack",
                            "nodeId": node_id,
                            "serverTime": int(time.time()),
                            "message": "connected",
                        },
                        ensure_ascii=False,
                    )
                )
                print(f"[dist] 节点上线 {node_id} {remote}", flush=True)
                continue
            if not node_id:
                await ws.send(json.dumps({"type": "error", "message": "send hello first"}, ensure_ascii=False))
                continue
            with _dist_lock:
                node = _dist_nodes.get(node_id)
                if node:
                    node["lastSeenAt"] = int(time.time())
                    node["lastSeenMono"] = time.monotonic()
                    if "cpuPercent" in msg:
                        try:
                            node["cpuPercent"] = float(msg.get("cpuPercent"))
                        except (TypeError, ValueError):
                            node["cpuPercent"] = None
                    if "currentTasks" in msg:
                        try:
                            node["currentTasks"] = max(0, int(msg.get("currentTasks") or 0))
                        except (TypeError, ValueError):
                            pass
                    caps_update = msg.get("caps") if isinstance(msg.get("caps"), dict) else None
                    if caps_update:
                        caps = node.get("caps") if isinstance(node.get("caps"), dict) else {}
                        caps.update(caps_update)
                        node["caps"] = caps
            if typ == "heartbeat":
                continue
            if typ in ("event", "job_done", "job_error"):
                _dist_handle_worker_message(node_id, msg)
                if typ in ("job_done", "job_error"):
                    _dist_mark_node(node_id, busy=False, job_id="")
                continue
    except Exception as ex:
        print(f"[dist] 节点连接异常 {node_id or remote}: {ex}", flush=True)
    finally:
        old_job_id = ""
        with _dist_lock:
            node = _dist_nodes.get(node_id) if node_id else None
            if node and node.get("ws") is ws:
                old_job_id = str(node.get("jobId") or "")
                _dist_nodes.pop(node_id, None)
        if node_id:
            print(f"[dist] 节点下线 {node_id}", flush=True)
        if old_job_id:
            job = _dist_job_for(old_job_id)
            if job:
                job.put(node_id, {"type": "job_error", "jobId": old_job_id, "message": "节点连接断开"})


def start_distributed_ws_server() -> None:
    global _dist_server_started, _dist_loop
    with _dist_lock:
        if _dist_server_started:
            return
        _dist_server_started = True

    def _run() -> None:
        global _dist_server_started, _dist_loop
        server = None
        loop: asyncio.AbstractEventLoop | None = None
        try:
            import websockets

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _dist_loop = loop

            async def _start_server():
                return await websockets.serve(
                    _dist_ws_handler,
                    DIST_WS_HOST,
                    DIST_WS_PORT,
                    max_size=128 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                )

            server = loop.run_until_complete(_start_server())
            print(f"[dist] WebSocket 主控已监听 ws://{DIST_WS_HOST}:{DIST_WS_PORT}", flush=True)
            loop.run_forever()
        except Exception as ex:
            print(f"[dist] WebSocket 主控启动失败: {ex}", file=sys.stderr, flush=True)
        finally:
            if server is not None and loop is not None:
                try:
                    server.close()
                    loop.run_until_complete(server.wait_closed())
                except Exception:
                    pass
            with _dist_lock:
                _dist_server_started = False
                _dist_loop = None

    threading.Thread(target=_run, daemon=True, name="mt-dist-ws").start()


def _dist_send_to_node(node_id: str, msg: dict, timeout: float = 8.0) -> None:
    with _dist_lock:
        node = _dist_nodes.get(node_id)
        loop = _dist_loop
        ws = node.get("ws") if node else None
    if not loop or not ws:
        raise RuntimeError(f"节点 {node_id} 未连接")
    fut = asyncio.run_coroutine_threadsafe(
        ws.send(json.dumps(msg, ensure_ascii=False)),
        loop,
    )
    fut.result(timeout=timeout)


def _dist_proxy_shards_for_tasks(tasks: list[dict], proxy_pool_lines: list[str]) -> dict[str, list[str]]:
    phones: list[str] = []
    seen: set[str] = set()
    for t in tasks:
        if not isinstance(t, dict):
            continue
        ph = _norm_phone(t.get("phone", ""))
        if not ph or ph in seen:
            continue
        seen.add(ph)
        phones.append(ph)
    if not phones or not proxy_pool_lines:
        return {}
    n = len(phones)
    m = len(proxy_pool_lines)
    if m <= 1:
        return {ph: list(proxy_pool_lines) for ph in phones}
    out: dict[str, list[str]] = {}
    for j, ph in enumerate(phones):
        start = (j * m) // n
        end = ((j + 1) * m) // n
        out[ph] = list(proxy_pool_lines[start:end])
    return out


def _dist_accounts_for_tasks(tasks: list[dict], proxy_shards: dict[str, list[str]] | None = None) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    proxy_shards = proxy_shards or {}
    for t in tasks:
        if not isinstance(t, dict):
            continue
        phone = _norm_phone(t.get("phone", ""))
        if not phone or phone in seen:
            continue
        seen.add(phone)
        e = POOL.get(phone)
        if e:
            d = e.to_dict()
            shard = proxy_shards.get(phone)
            if shard is not None:
                d["proxy_shard_urls"] = list(shard)
                d["proxy_shard_mode"] = "runtime"
                d["proxy_shard_count"] = len(shard)
            out.append(d)
    return out


def _dist_remap_worker_event(node_id: str, job: _DistJob, ev: dict) -> dict | None:
    if not isinstance(ev, dict):
        return None
    typ = str(ev.get("type") or "").strip()
    if typ in ("start", "done"):
        return None
    out = dict(ev)
    out["distributed"] = True
    out["nodeId"] = node_id
    out["masterJobMs"] = int((time.monotonic() - job.started_at) * 1000)
    offset = int(job.node_offsets.get(node_id, 0) or 0)
    if "taskIndex" in out:
        try:
            out["taskIndex"] = offset + int(out.get("taskIndex") or 0)
        except (TypeError, ValueError):
            pass
    return out


def _dist_stop_all_jobs() -> dict:
    stopped_jobs: list[str] = []
    sent_nodes: list[str] = []
    with _dist_lock:
        jobs = list(_dist_jobs.values())
        nodes = [
            (node_id, dict(node))
            for node_id, node in _dist_nodes.items()
            if node.get("busy") or node.get("jobId")
        ]
        for job in jobs:
            job.stop_requested = True
            stopped_jobs.append(job.job_id)
    for node_id, node in nodes:
        node_job_id = str(node.get("jobId") or "")
        try:
            _dist_send_to_node(
                node_id,
                {"type": "stop_job", "jobId": node_job_id},
                timeout=2.0,
            )
            sent_nodes.append(node_id)
        except Exception:
            pass
        _dist_mark_node(node_id, busy=False, job_id="")
    return {"jobs": stopped_jobs, "nodes": sent_nodes}


def _run_distributed_batch_rush_stream_api(tasks: list[dict], concurrency: int, emit) -> None:
    n = len(tasks)
    emit({"type": "start", "ok": True, "total": n, "distributed": True})
    if not tasks:
        emit({"type": "done", "ok": True, "total": 0, "success": 0, "failed": 0, "distributed": True})
        return
    # 主控也进热路径：主控自己也会 ensure_session（分发前的本机预置等），避免写盘卡分发。
    try:
        enter_rush_hot_path()
    except Exception:
        pass
    try:
        _run_distributed_batch_rush_stream_api_inner(tasks, concurrency, emit)
    finally:
        try:
            exit_rush_hot_path()
        except Exception:
            pass


def _run_distributed_batch_rush_stream_api_inner(tasks: list[dict], concurrency: int, emit) -> None:
    n = len(tasks)
    nodes = _dist_idle_node_ids()
    if not nodes:
        emit({"type": "error", "ok": False, "message": "没有在线空闲分布式节点"})
        emit({"type": "done", "ok": False, "total": n, "success": 0, "failed": n, "distributed": True})
        return
    nodes = nodes[: min(len(nodes), n)]
    per = max(1, (n + len(nodes) - 1) // len(nodes))
    chunks: list[tuple[str, int, list[dict]]] = []
    for idx, node_id in enumerate(nodes):
        start = idx * per
        end = min(n, start + per)
        if start >= end:
            continue
        chunks.append((node_id, start, tasks[start:end]))
    if not chunks:
        emit({"type": "error", "ok": False, "message": "分布式任务拆分为空"})
        emit({"type": "done", "ok": False, "total": n, "success": 0, "failed": n, "distributed": True})
        return

    job_id = uuid.uuid4().hex
    job = _DistJob(job_id, len(chunks))
    success = 0
    failed = 0
    result_seen = 0
    active_nodes: set[str] = set()
    with _dist_lock:
        _dist_jobs[job_id] = job
    try:
        t_prep0 = time.perf_counter()
        emit({"type": "prep_start", "total": n, "mode": "distributed", "nodes": len(chunks)})
        proxy_pool_lines = _dist_proxy_pool_lines_for_job()
        proxy_shards: dict[str, list[str]] = {}
        act_id_count = sum(
            1 for t in tasks if isinstance(t, dict) and str(t.get("actId") or "").strip()
        )
        item_count = sum(
            1 for t in tasks if isinstance(t, dict) and str(t.get("itemCode") or "").strip()
        )
        spu_id_count = sum(
            1 for t in tasks if isinstance(t, dict) and str(t.get("spuId") or "").strip()
        )
        emit(
            {
                "type": "dist_plan_ready",
                "total": n,
                "nodes": len(chunks),
                "proxyPoolCount": len(proxy_pool_lines),
                "actIdCount": act_id_count,
                "itemCount": item_count,
                "spuIdCount": spu_id_count,
                "ms": int((time.perf_counter() - t_prep0) * 1000),
            }
        )
        for node_id, start, _chunk_tasks in chunks:
            job.node_offsets[node_id] = start

        def _send_dist_chunk(plan: tuple[str, int, list[dict]]) -> dict:
            node_id, start, chunk_tasks = plan
            t_send0 = time.perf_counter()
            accounts = _dist_accounts_for_tasks(chunk_tasks, proxy_shards)
            node_conc = _dist_node_concurrency(node_id, int(concurrency or 1), len(chunk_tasks))
            chunk_act_id_count = sum(
                1 for t in chunk_tasks if isinstance(t, dict) and str(t.get("actId") or "").strip()
            )
            chunk_item_count = sum(
                1 for t in chunk_tasks if isinstance(t, dict) and str(t.get("itemCode") or "").strip()
            )
            chunk_spu_id_count = sum(
                1 for t in chunk_tasks if isinstance(t, dict) and str(t.get("spuId") or "").strip()
            )
            chunk_highfreq_count = sum(
                1 for t in chunk_tasks if isinstance(t, dict) and _fire_and_forget_from_payload(t)
            )
            try:
                _dist_mark_node(node_id, busy=True, job_id=job_id)
                with _dist_lock:
                    node = _dist_nodes.get(node_id)
                    if node:
                        node["currentTasks"] = len(chunk_tasks)
                        node["taskTotal"] = len(chunk_tasks)
                        node["accountCount"] = len(accounts)
                _dist_send_to_node(
                    node_id,
                    {
                        "type": "run_job",
                        "jobId": job_id,
                        "taskOffset": start,
                        "tasks": chunk_tasks,
                        "accounts": accounts,
                        "concurrency": node_conc,
                        "proxyPoolLines": list(proxy_pool_lines),
                        "proxyPoolStartOffset": start,
                    },
                    timeout=10.0,
                )
                return {
                    "ok": True,
                    "nodeId": node_id,
                    "event": {
                        "type": "node_start_v2",
                        "jobId": job_id,
                        "nodeId": node_id,
                        "taskOffset": start,
                        "total": len(chunk_tasks),
                        "concurrency": node_conc,
                        "actIdCount": chunk_act_id_count,
                        "itemCount": chunk_item_count,
                        "spuIdCount": chunk_spu_id_count,
                        "highfreqCount": chunk_highfreq_count,
                        "proxyPoolCount": len(proxy_pool_lines),
                        "proxyPoolStartOffset": start % len(proxy_pool_lines) if proxy_pool_lines else 0,
                        "proxyMode": "global_pool",
                        "ms": int((time.perf_counter() - t_send0) * 1000),
                    },
                }
            except Exception as ex:
                _dist_mark_node(node_id, busy=False, job_id="")
                em = str(ex).strip() or type(ex).__name__
                return {
                    "ok": False,
                    "nodeId": node_id,
                    "start": start,
                    "tasks": chunk_tasks,
                    "message": em,
                }

        send_workers = max(1, min(len(chunks), 32))
        emit({"type": "dist_dispatch_start", "nodes": len(chunks), "sendWorkers": send_workers})
        with concurrent.futures.ThreadPoolExecutor(max_workers=send_workers) as ex:
            futs = [ex.submit(_send_dist_chunk, plan) for plan in chunks]
            for fu in concurrent.futures.as_completed(futs):
                try:
                    sent = fu.result()
                except Exception as ex:
                    sent = {"ok": False, "nodeId": "", "start": 0, "tasks": [], "message": str(ex).strip() or type(ex).__name__}
                if sent.get("ok"):
                    node_id = str(sent.get("nodeId") or "")
                    if node_id:
                        active_nodes.add(node_id)
                    ev = sent.get("event") if isinstance(sent.get("event"), dict) else None
                    if ev:
                        emit(ev)
                    continue
                node_id = str(sent.get("nodeId") or "")
                start = int(sent.get("start") or 0)
                chunk_tasks = sent.get("tasks") if isinstance(sent.get("tasks"), list) else []
                em = str(sent.get("message") or "unknown error")
                emit({"type": "error", "ok": False, "nodeId": node_id, "message": f"分布式节点下发失败: {em}"})
                for j, t in enumerate(chunk_tasks):
                    ph = str((t or {}).get("phone") or "").strip()
                    emit({"type": "result", "taskIndex": start + j, "phone": ph, "ok": False, "message": em})
                    failed += 1
                    result_seen += 1
        if not active_nodes:
            emit({"type": "done", "ok": False, "total": n, "success": success, "failed": max(failed, n), "distributed": True})
            return
        emit({"type": "go_start", "total": n, "concurrency": int(concurrency or 1), "mode": "distributed", "nodes": len(active_nodes)})
        while len(job.done_nodes) < len(active_nodes):
            if job.stop_requested:
                emit({"type": "error", "ok": False, "message": "分布式任务已停止"})
                break
            if time.monotonic() - job.started_at > DIST_JOB_TIMEOUT_SEC:
                emit({"type": "error", "ok": False, "message": f"分布式任务超时 {int(DIST_JOB_TIMEOUT_SEC)}s"})
                break
            try:
                node_id, msg = job.q.get(timeout=0.5)
            except queue.Empty:
                continue
            typ = str(msg.get("type") or "")
            if typ == "event":
                ev = msg.get("event") if isinstance(msg.get("event"), dict) else {}
                _dist_update_node_event(node_id, ev)
                out = _dist_remap_worker_event(node_id, job, ev)
                if not out:
                    continue
                if str(out.get("type") or "") == "result":
                    result_seen += 1
                    if out.get("ok"):
                        success += 1
                    else:
                        failed += 1
                emit(out)
                continue
            if typ in ("job_done", "job_error"):
                job.done_nodes.add(node_id)
                _dist_mark_node(node_id, busy=False, job_id="")
                if typ == "job_error":
                    emit(
                        {
                            "type": "error",
                            "ok": False,
                            "nodeId": node_id,
                            "message": str(msg.get("message") or "节点任务异常"),
                        }
                    )
                continue
        if result_seen < n:
            failed += n - result_seen
        emit({"type": "done", "ok": True, "total": n, "success": success, "failed": failed, "distributed": True})
    finally:
        with _dist_lock:
            _dist_jobs.pop(job_id, None)
        for node_id in active_nodes:
            _dist_mark_node(node_id, busy=False, job_id="")


def _remote_token_from_request(handler: BaseHTTPRequestHandler, payload: dict | None) -> str:
    h = (handler.headers.get("X-Remote-Login-Token") or "").strip()
    if h:
        return h
    auth = (handler.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if isinstance(payload, dict):
        return str(payload.get("remoteToken") or "").strip()
    return ""


def _remote_login_api_authorized(handler: BaseHTTPRequestHandler, payload: dict | None) -> bool:
    """局域网/外网访问须带对口令；本机 127.0.0.1 不受限，避免主界面发码失败。"""
    if not REMOTE_LOGIN_SECRET:
        return True
    if _client_is_loopback(handler):
        return True
    got = _remote_token_from_request(handler, payload).encode("utf-8")
    exp = REMOTE_LOGIN_SECRET.encode("utf-8")
    return hmac.compare_digest(got, exp)


def _remote_login_page_authorized(handler: BaseHTTPRequestHandler) -> bool:
    """外网/局域网须带 URL ?token=；本机 127.0.0.1 可免 token 打开，便于自测。"""
    if not REMOTE_LOGIN_SECRET:
        return True
    if _client_is_loopback(handler):
        return True
    parsed = urlparse(handler.path)
    qs = parse_qs(parsed.query or "")
    tok = (qs.get("token") or [""])[0].strip().encode("utf-8")
    return hmac.compare_digest(tok, REMOTE_LOGIN_SECRET.encode("utf-8"))


def _infer_http_proxy_scheme_from_api_urls(urls: list[str]) -> str:
    """部分厂商提取接口返回的是 HTTP 代理；未勾选格式时按域名推断，避免默认写成 socks5。"""
    for raw in urls:
        host = (urlparse(str(raw or "").strip()).hostname or "").lower()
        if not host:
            continue
        # 快代理私密/隧道等提取域名，出口默认 HTTP（非 SOCKS5）
        if host == "kdlapi.com" or host.endswith(".kdlapi.com"):
            return "http"
        if host == "kuaidaili.com" or host.endswith(".kuaidaili.com"):
            return "http"
    return ""


def _proxy_import_scheme(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    raw = (
        payload.get("proxyScheme")
        or payload.get("proxyType")
        or payload.get("scheme")
        or ""
    )
    scheme = str(raw or "").strip().lower()
    if scheme in ("http", "https", "socks5", "socks5h"):
        return scheme
    if payload.get("useHttps") is True:
        return "https"
    if payload.get("useHttp") is True:
        return "http"
    urls = _proxy_api_urls_from_payload(payload)
    return _infer_http_proxy_scheme_from_api_urls(urls)


def _coerce_proxy_scheme(proxy_url: str, scheme: str) -> str:
    scheme = str(scheme or "").strip().lower()
    if scheme not in ("http", "https", "socks5", "socks5h"):
        return proxy_url
    try:
        pr = urlparse(proxy_url)
        if not pr.netloc:
            return proxy_url
        return f"{scheme}://{pr.netloc}"
    except Exception:
        return proxy_url


def _normalize_proxy_line(raw: str, *, scheme: str = "") -> str | None:
    """与 proxy_pool.line_to_proxy_url 一致（裸 host:port 默认 socks5；显式 http/https/socks URL 透传）。"""
    s = str(raw or "").strip()
    if not s or s.startswith("#"):
        return None
    from proxy_pool import line_to_proxy_url

    norm = line_to_proxy_url(s)
    if norm and scheme:
        return _coerce_proxy_scheme(norm, scheme)
    return norm


def _looks_like_proxy_token(s: str) -> bool:
    s = str(s or "").strip()
    if not s:
        return False
    if "://" in s:
        return True
    if re.match(r"^(\[[^\]]+\]|[^:/|\s,，;；]+):\d{1,5}(?:[/|].*)?$", s):
        return True
    if re.match(r"^[^/|\s,，;；]+[/|]\d{1,5}(?:[/|].*)?$", s):
        return True
    return False


def _proxy_compact_credential_line(s: str) -> str | None:
    """识别单条紧凑代理：host:port,user,password 或 host,port,user,password。"""
    text = str(s or "").strip().strip("\"'`")
    if not text:
        return None
    m = re.match(r"^(\[[^\]]+\]|[^,\s]+):(\d{1,5}),([^,\s]+),([^,\s]+)$", text)
    if m:
        host, port_s, user, pwd = m.group(1), m.group(2), m.group(3), m.group(4)
        return f"{host}/{port_s}/{user}/{pwd}"
    m = re.match(r"^([^,\s]+),(\d{1,5}),([^,\s]+),([^,\s]+)$", text)
    if m:
        host, port_s, user, pwd = m.group(1), m.group(2), m.group(3), m.group(4)
        return f"{host}/{port_s}/{user}/{pwd}"
    return None


def _proxy_auth_from_api_url(url: str) -> tuple[str, str]:
    """从代理 API URL 查询参数中提取账号密码，例如 UserName/UserPwd。"""
    try:
        qs = parse_qs(urlparse(str(url or "").strip()).query or "")
    except Exception:
        return "", ""

    def pick(*names: str) -> str:
        for name in names:
            vals = qs.get(name)
            if vals:
                s = str(vals[0] or "").strip()
                if s:
                    return s
        return ""

    user = pick(
        "UserName",
        "Username",
        "username",
        "userName",
        "proxyUser",
        "proxy_user",
        "authUser",
        "user",
        "User",
        "account",
        "Account",
    )
    pwd = pick(
        "UserPwd",
        "Password",
        "password",
        "passWord",
        "proxyPwd",
        "proxy_pwd",
        "proxyPassword",
        "proxy_password",
        "authPass",
        "authPwd",
        "pwd",
        "Pwd",
        "pass",
        "Pass",
    )
    return user, pwd


def _proxy_auth_from_payload(payload: dict | None) -> tuple[str, str]:
    """优先用请求体里的代理账密，其次从提取 API URL 查询参数读取。"""
    if not isinstance(payload, dict):
        payload = {}
    user = str(
        payload.get("proxyUser")
        or payload.get("proxyUsername")
        or payload.get("authUser")
        or ""
    ).strip()
    pwd = str(
        payload.get("proxyPassword")
        or payload.get("proxyPwd")
        or payload.get("authPass")
        or payload.get("authPwd")
        or ""
    ).strip()
    if user and pwd:
        return user, pwd
    return _proxy_auth_from_api_url(str(payload.get("url") or ""))


def _attach_proxy_auth_if_needed(raw: str, *, user: str = "", pwd: str = "") -> str:
    s = str(raw or "").strip()
    user = str(user or "").strip()
    pwd = str(pwd or "").strip()
    if not s or not user or not pwd:
        return s
    if "@" in s:
        return s
    if not _looks_like_proxy_token(s):
        return s
    if "://" in s:
        try:
            pr = urlparse(s)
            if pr.username or not pr.hostname or pr.port is None:
                return s
            return f"{pr.scheme}://{quote(user, safe='')}:{quote(pwd, safe='')}@{pr.hostname}:{pr.port}"
        except Exception:
            return s
    m = re.match(r"^(\[[^\]]+\]|[^:/|\s]+):(\d{1,5})$", s)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{user}/{pwd}"
    m = re.match(r"^([^/|\s]+)[/|](\d{1,5})$", s)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{user}/{pwd}"
    return s


def _proxy_text_candidates(text: str) -> list[str]:
    """从文本/API字符串中拆出代理候选，兼容逗号、空格、分号和换行混排。"""
    raw = str(text or "")
    if not raw.strip():
        return []
    out: list[str] = []
    for part in re.split(r"[\r\n\t ,，;；]+", raw):
        s = part.strip().strip("\"'`[]{}()<>")
        s = s.rstrip(".。")
        if not s:
            continue
        if not _looks_like_proxy_token(s):
            continue
        out.append(s)
    return out


def _read_proxy_pool_payload() -> dict:
    lines: list[dict] = []
    text = ""
    with _PROXY_POOL_FILE_LOCK:
        if PROXY_POOL_FILE.exists():
            text = PROXY_POOL_FILE.read_text(encoding="utf-8")
    for idx, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        norm = _normalize_proxy_line(stripped)
        lines.append(
            {
                "lineNo": idx,
                "raw": stripped,
                "normalized": norm or "",
                "valid": bool(norm),
            }
        )
    return {"items": lines, "count": len(lines), "file": str(PROXY_POOL_FILE)}


def _dist_proxy_pool_lines_for_job() -> list[str]:
    """分布式任务随包携带主控当前代理池，避免节点读取本地旧 local_proxy_pool.txt。"""
    out: list[str] = []
    with _PROXY_POOL_FILE_LOCK:
        if not PROXY_POOL_FILE.exists():
            return []
        try:
            text = PROXY_POOL_FILE.read_text(encoding="utf-8")
        except Exception:
            return []
    for raw in text.splitlines():
        norm = _normalize_proxy_line(raw)
        if not norm:
            continue
        out.append(norm)
    return out


def _proxy_pool_shards_preview(only_logged: bool = True, phones: list[str] | None = None) -> dict:
    """按当前账号集合预览代理池均分分片（每号在其分片内按重试轮询）。"""
    from proxy_pool import active_urls, pool_is_configured, shard_urls_by_index

    rows = []
    au = active_urls()
    accts = POOL.snapshot()
    targeted = phones is not None
    requested = []
    seen_req = set()
    if targeted:
        for p in phones or []:
            ph = _norm_phone(str(p or "").strip())
            if ph and ph not in seen_req:
                seen_req.add(ph)
                requested.append(ph)
    acct_by_phone = {}
    for a in accts:
        ph = _norm_phone(str(a.get("phone", "")).strip())
        if not ph:
            continue
        acct_by_phone[ph] = a
    phones = []
    row_accts = []
    source_phones = requested if targeted else [
        _norm_phone(str(a.get("phone", "")).strip()) for a in accts
    ]
    for ph in source_phones:
        if not ph:
            continue
        a = acct_by_phone.get(ph)
        if not a:
            continue
        if only_logged and str(a.get("status", "")) != STATUS_OK:
            continue
        phones.append(ph)
        row_accts.append(a)
    # 保留传入/账号列表顺序，不再按手机号排序
    seen = set()
    phones_ordered = []
    for p in phones:
        if p not in seen:
            seen.add(p)
            phones_ordered.append(p)
    phones = phones_ordered
    row_accts = [acct_by_phone[p] for p in phones if p in acct_by_phone]
    for a in row_accts:
        ph = _norm_phone(str(a.get("phone", "")).strip())
        if not ph or ph not in seen:
            continue
        mode = str(a.get("proxy_shard_mode", "auto") or "auto").strip().lower()
        try:
            cnt = max(0, int(a.get("proxy_shard_count", 0) or 0))
        except Exception:
            cnt = 0
        # 勾选分组预览时以当前目标账号为分片基准；普通预览再读取账号已保存的分组
        psg = [] if targeted else list(a.get("proxy_shard_group") or [])
        shard_phones = [p for p in psg if p] if psg else phones
        total = max(1, len(shard_phones))
        try:
            acct_index = shard_phones.index(ph)
        except ValueError:
            acct_index = 0
        if not (shard_phones and pool_is_configured()):
            shard = []
        elif mode == "all":
            shard = list(au)
        elif mode == "count" and cnt > 0:
            base = shard_urls_by_index(acct_index, total)
            if not base:
                base = []
            shard = list(base[:cnt])
        else:
            shard = shard_urls_by_index(acct_index, total)
        rows.append(
            {
                "phone": ph,
                "nickname": str(a.get("nickname", "") or ph),
                "status": str(a.get("status", "") or ""),
                "mode": mode,
                "proxyCount": len(shard),
                "proxies": shard,
            }
        )
    return {
        "poolCount": len(au),
        "accountCount": len(phones),
        "onlyLogged": bool(only_logged),
        "targeted": bool(targeted),
        "rows": rows,
    }


def _save_proxy_pool_lines(raw_lines: list[str]) -> dict:
    """落盘代理池：默认与现有池累积合并；重复代理也保留为独立池条目。"""
    ok: list[str] = []
    bad: list[str] = []
    for raw in raw_lines:
        norm = _normalize_proxy_line(raw)
        if norm:
            ok.append(norm)
        elif str(raw or "").strip():
            bad.append(str(raw).strip())
    with _PROXY_POOL_FILE_LOCK:
        existing: list[str] = []
        if PROXY_POOL_FILE.exists():
            try:
                old_text = PROXY_POOL_FILE.read_text(encoding="utf-8")
                for line in old_text.splitlines():
                    norm = _normalize_proxy_line(line)
                    if norm:
                        existing.append(norm)
            except Exception:
                existing = []

        merged = list(existing) + list(ok)

        def _write_file(urls: list[str]) -> None:
            content = "\n".join(urls).strip()
            if content:
                content += "\n"
            PROXY_POOL_FILE.write_text(content, encoding="utf-8")

        if not merged:
            return {
                "saved": True,
                "count": len(existing),
                "existingCount": len(existing),
                "addedCount": 0,
                "invalidCount": len(bad),
                "invalid": bad[:30],
                "items": existing,
                "verify": {"allOk": True, "skipped": True, "results": []},
            }

        _write_file(merged)
        added_count = len(ok)
    return {
        "saved": True,
        "count": len(merged),
        "existingCount": len(existing),
        "addedCount": added_count,
        "invalidCount": len(bad),
        "invalid": bad[:30],
        "items": merged,
        "verify": {"allOk": True, "skipped": True, "results": []},
    }


def _clear_proxy_pool() -> dict:
    """清空代理池文件。"""
    with _PROXY_POOL_FILE_LOCK:
        PROXY_POOL_FILE.write_text("", encoding="utf-8")
    return {"saved": True, "count": 0, "items": []}


def _apply_proxy_pool_auto_assign(
    *,
    only_logged: bool = True,
    phones: list[str] | None = None,
) -> dict:
    """Reload proxy pool and make target accounts use automatic pool sharding."""
    try:
        from proxy_pool import reload_now

        urls = reload_now()
    except Exception as ex:
        return {
            "applied": False,
            "error": str(ex)[:240],
            "poolCount": 0,
        }
    out = POOL.apply_proxy_pool_auto_shards(only_logged=only_logged, phones=phones)
    out["poolCount"] = len(urls)
    return out


def _proxy_fill_now_ms() -> int:
    return int(time.time() * 1000)


def _proxy_fill_pool_count() -> int:
    try:
        return int(_read_proxy_pool_payload().get("count") or 0)
    except Exception:
        return 0


def _proxy_fill_snapshot() -> dict:
    with _PROXY_FILL_LOCK:
        out = dict(_PROXY_FILL_STATUS)
        out["running"] = bool(_PROXY_FILL_THREAD and _PROXY_FILL_THREAD.is_alive() and out.get("running"))
    out["currentCount"] = _proxy_fill_pool_count()
    return out


def _proxy_fill_set(**kwargs) -> None:
    with _PROXY_FILL_LOCK:
        _PROXY_FILL_STATUS.update(kwargs)


def _proxy_api_log_record(record: dict) -> None:
    try:
        PROXY_POOL_IMPORT_LOG.parent.mkdir(parents=True, exist_ok=True)
        safe = dict(record)
        safe["ts"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with PROXY_POOL_IMPORT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(safe, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _proxy_fill_clean_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        payload = {}
    raw_urls = payload.get("urls")
    if isinstance(raw_urls, str):
        raw_urls = raw_urls.splitlines()
    if isinstance(raw_urls, list):
        urls = [str(u or "").strip() for u in raw_urls if str(u or "").strip()]
    else:
        urls = []
    one = str(payload.get("url") or "").strip()
    if one and one not in urls:
        urls.insert(0, one)
    seen: set[str] = set()
    urls = [u for u in urls if not (u in seen or seen.add(u))]
    if not urls:
        raise ValueError("url 不能为空")

    def _int_in_range(name: str, default: int, lo: int, hi: int) -> int:
        try:
            v = int(float(payload.get(name, default)))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    def _float_in_range(name: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(payload.get(name, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    target_count = _int_in_range("targetCount", 10000, 1, 200000)
    parallel = _int_in_range("parallel", min(3, len(urls)), 1, 24)
    interval_ms = _int_in_range("intervalMs", 300, 0, 60000)
    max_rounds = _int_in_range("maxRounds", 0, 0, 200000)
    timeout_sec = _float_in_range("timeoutSec", 12.0, 2.0, 60.0)
    json_path = str(payload.get("jsonPath") or "data.list").strip() or "data.list"
    scheme = str(payload.get("proxyScheme") or "").strip().lower()
    use_https = payload.get("useHttps") is True or scheme == "https"
    use_http = (not use_https) and (payload.get("useHttp") is True or scheme == "http")
    proxy_scheme = "https" if use_https else ("http" if use_http else "")
    if not proxy_scheme:
        proxy_scheme = _infer_http_proxy_scheme_from_api_urls(urls)
        use_http = proxy_scheme == "http"
    proxy_user, proxy_pwd = _proxy_auth_from_payload({**payload, "url": urls[0], "urls": urls})
    target_phones = payload.get("phones")
    if target_phones is not None and not isinstance(target_phones, list):
        target_phones = None
    return {
        "url": urls[0],
        "urls": urls,
        "method": "GET",
        "jsonPath": json_path,
        "proxyScheme": proxy_scheme,
        "useHttp": bool(use_http),
        "useHttps": bool(use_https),
        "proxyUser": proxy_user,
        "proxyPassword": proxy_pwd,
        "timeoutSec": timeout_sec,
        "targetCount": target_count,
        "parallel": parallel,
        "intervalMs": interval_ms,
        "maxRounds": max_rounds,
        "phones": target_phones,
    }


def _proxy_api_urls_from_payload(payload: dict) -> list[str]:
    if not isinstance(payload, dict):
        payload = {}
    urls = [str(u or "").strip() for u in list(payload.get("urls") or []) if str(u or "").strip()]
    seen_urls: set[str] = set()
    urls = [u for u in urls if not (u in seen_urls or seen_urls.add(u))]
    if not urls:
        one = str(payload.get("url") or "").strip()
        if one:
            urls = [one]
    return urls


def _proxy_parallel_job_urls(payload: dict, parallel: int) -> tuple[list[str], list[str]]:
    urls = _proxy_api_urls_from_payload(payload)
    if not urls:
        return [], []

    jobs: list[str] = []
    repeat = max(1, int(parallel or 1))
    for i in range(repeat):
        jobs.append(urls[i % len(urls)])
    return urls, jobs


def _fetch_proxy_lines_from_apis_parallel(payload: dict, parallel: int) -> dict:
    urls, jobs = _proxy_parallel_job_urls(payload, parallel)
    if not jobs:
        return {"ok": False, "error": "url 不能为空"}
    rows: list[dict | None] = [None] * len(jobs)

    def _one(u: str) -> dict:
        res = _fetch_proxy_lines_from_api({**payload, "url": u})
        return {"url": u, "result": res}

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = {ex.submit(_one, u): i for i, u in enumerate(jobs)}
        for fu in concurrent.futures.as_completed(futs):
            idx = futs[fu]
            try:
                rows[idx] = fu.result()
            except Exception as ex2:
                rows[idx] = {"url": jobs[idx], "result": {"ok": False, "error": str(ex2)}}
    out = _merge_proxy_api_rows(jobs, rows)
    out["sourceCount"] = len(urls)
    out["requestCount"] = len(jobs)
    return out


def _iter_fetch_proxy_lines_from_apis_parallel(payload: dict, parallel: int):
    """Yield one parsed proxy API result as soon as each parallel request finishes."""
    urls, jobs = _proxy_parallel_job_urls(payload, parallel)
    if not jobs:
        yield {"ok": False, "error": "url 不能为空", "sourceCount": 0, "requestCount": 0, "sources": []}
        return

    def _one(u: str) -> dict:
        res = _fetch_proxy_lines_from_api({**payload, "url": u})
        return {"url": u, "result": res}

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs))
    futs = {ex.submit(_one, u): i for i, u in enumerate(jobs)}
    try:
        for fu in concurrent.futures.as_completed(futs):
            idx = futs[fu]
            try:
                row = fu.result()
            except Exception as ex2:
                row = {"url": jobs[idx], "result": {"ok": False, "error": str(ex2)}}
            out = _merge_proxy_api_rows([jobs[idx]], [row])
            out["sourceCount"] = len(urls)
            out["requestCount"] = 1
            out["jobIndex"] = idx
            yield out
    finally:
        for fu in futs:
            fu.cancel()
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            ex.shutdown(wait=False)


def _proxy_fill_worker(cfg: dict) -> None:
    target_count = int(cfg.get("targetCount") or 10000)
    interval_ms = int(cfg.get("intervalMs") or 0)
    parallel = int(cfg.get("parallel") or 1)
    max_rounds = int(cfg.get("maxRounds") or 0)
    target_phones = cfg.get("phones") if isinstance(cfg.get("phones"), list) else None
    current = _proxy_fill_pool_count()
    _proxy_fill_set(
        running=True,
        startedAt=_proxy_fill_now_ms(),
        stoppedAt=None,
        targetCount=target_count,
        currentCount=current,
        rounds=0,
        totalPulled=0,
        totalAdded=0,
        totalInvalid=0,
        apiCalls=0,
        lastAdded=0,
        failures=0,
        message="running",
        lastError="",
        lastSources=[],
        lastRound=None,
    )
    try:
        while not _PROXY_FILL_STOP.is_set():
            current = _proxy_fill_pool_count()
            with _PROXY_FILL_LOCK:
                rounds_done = int(_PROXY_FILL_STATUS.get("rounds") or 0)
            if current >= target_count:
                _proxy_fill_set(message="target_reached", currentCount=current)
                break
            if max_rounds and rounds_done >= max_rounds:
                _proxy_fill_set(message="max_rounds_reached", currentCount=current)
                break

            round_sources: list[dict] = []
            round_pulled = 0
            round_added = 0
            round_invalid = 0
            round_failures = 0
            round_api_calls = 0

            for pulled in _iter_fetch_proxy_lines_from_apis_parallel(cfg, parallel):
                if _PROXY_FILL_STOP.is_set():
                    break
                round_api_calls += int(pulled.get("requestCount") or 0)
                sources = list(pulled.get("sources") or [])
                round_sources.extend(sources)
                if not pulled.get("ok"):
                    err = str(pulled.get("error") or "API 拉取失败")
                    round_failures += 1
                    with _PROXY_FILL_LOCK:
                        failures = int(_PROXY_FILL_STATUS.get("failures") or 0) + 1
                        total_api_calls = int(_PROXY_FILL_STATUS.get("apiCalls") or 0) + int(pulled.get("requestCount") or 0)
                    _proxy_fill_set(
                        apiCalls=total_api_calls,
                        failures=failures,
                        lastError=err[:500],
                        message="pull_failed",
                        lastSources=sources,
                        lastRound={"ok": False, "error": err, "sources": sources},
                    )
                    _proxy_api_log_record({"event": "proxy_fill_api", "ok": False, "error": err, "sources": sources})
                    continue

                lines = list(pulled.get("items") or [])
                saved = _save_proxy_pool_lines(lines) if lines else {
                    "saved": True,
                    "count": _proxy_fill_pool_count(),
                    "addedCount": 0,
                    "invalidCount": 0,
                    "items": [],
                }
                current = int(saved.get("count") or _proxy_fill_pool_count())
                added = int(saved.get("addedCount") or 0)
                invalid = int(pulled.get("invalidCount") or 0) + int(saved.get("invalidCount") or 0)
                pulled_n = int(pulled.get("validCount") or len(lines))
                round_pulled += pulled_n
                round_added += added
                round_invalid += invalid
                with _PROXY_FILL_LOCK:
                    total_pulled = int(_PROXY_FILL_STATUS.get("totalPulled") or 0) + pulled_n
                    total_added = int(_PROXY_FILL_STATUS.get("totalAdded") or 0) + added
                    total_invalid = int(_PROXY_FILL_STATUS.get("totalInvalid") or 0) + invalid
                    api_calls = int(_PROXY_FILL_STATUS.get("apiCalls") or 0) + int(pulled.get("requestCount") or 0)
                api_info = {
                    "ok": True,
                    "pulled": pulled_n,
                    "added": added,
                    "poolCount": current,
                    "invalid": invalid,
                    "sources": sources,
                }
                _proxy_fill_set(
                    currentCount=current,
                    totalPulled=total_pulled,
                    totalAdded=total_added,
                    totalInvalid=total_invalid,
                    apiCalls=api_calls,
                    lastAdded=added,
                    lastError="",
                    message="running" if current < target_count else "target_reached",
                    lastSources=sources,
                    lastRound=api_info,
                )
                _proxy_api_log_record({"event": "proxy_fill_api", **api_info})
                if added > 0:
                    _apply_proxy_pool_auto_assign(only_logged=True, phones=target_phones)
                if current >= target_count:
                    break

            with _PROXY_FILL_LOCK:
                rounds = int(_PROXY_FILL_STATUS.get("rounds") or 0) + 1
                failures_total = int(_PROXY_FILL_STATUS.get("failures") or 0)
            current = _proxy_fill_pool_count()
            round_info = {
                "ok": round_failures == 0 or round_added > 0,
                "round": rounds,
                "pulled": round_pulled,
                "added": round_added,
                "poolCount": current,
                "invalid": round_invalid,
                "apiCalls": round_api_calls,
                "failures": round_failures,
                "sources": round_sources,
            }
            _proxy_fill_set(
                rounds=rounds,
                currentCount=current,
                failures=failures_total,
                message="target_reached" if current >= target_count else ("pull_failed" if round_failures and round_added <= 0 else "running"),
                lastSources=round_sources[-8:],
                lastRound=round_info,
            )
            _proxy_api_log_record({"event": "proxy_fill_round", **round_info})

            if _PROXY_FILL_STOP.is_set():
                break
            if current >= target_count:
                break
            if interval_ms > 0:
                _PROXY_FILL_STOP.wait(interval_ms / 1000.0)
    except Exception as ex:
        traceback.print_exc()
        _proxy_fill_set(message="error", lastError=str(ex)[:500])
    finally:
        current = _proxy_fill_pool_count()
        stopped = "stopped" if _PROXY_FILL_STOP.is_set() else str(_proxy_fill_snapshot().get("message") or "idle")
        _proxy_fill_set(running=False, stoppedAt=_proxy_fill_now_ms(), currentCount=current, message=stopped)


def _proxy_fill_start(payload: dict) -> dict:
    global _PROXY_FILL_THREAD
    cfg = _proxy_fill_clean_payload(payload)
    with _PROXY_FILL_LOCK:
        if _PROXY_FILL_THREAD and _PROXY_FILL_THREAD.is_alive():
            return {"ok": False, "error": "补池任务已在运行", "status": _proxy_fill_snapshot()}
        _PROXY_FILL_STOP.clear()
        _PROXY_FILL_STATUS.update(
            {
                "running": True,
                "startedAt": _proxy_fill_now_ms(),
                "stoppedAt": None,
                "targetCount": int(cfg.get("targetCount") or 0),
                "currentCount": _proxy_fill_pool_count(),
                "rounds": 0,
                "totalPulled": 0,
                "totalAdded": 0,
                "totalInvalid": 0,
                "apiCalls": 0,
                "lastAdded": 0,
                "failures": 0,
                "message": "starting",
                "lastError": "",
                "lastSources": [],
                "lastRound": None,
            }
        )
        _PROXY_FILL_THREAD = threading.Thread(target=_proxy_fill_worker, args=(cfg,), daemon=True, name="proxy-api-fill")
        _PROXY_FILL_THREAD.start()
    return {"ok": True, "status": _proxy_fill_snapshot()}


def _proxy_fill_stop() -> dict:
    _PROXY_FILL_STOP.set()
    th = _PROXY_FILL_THREAD
    if th and th.is_alive():
        th.join(timeout=2.0)
    return _proxy_fill_snapshot()


def _pick_json_path(obj, path_expr: str):
    """简易 JSON 路径：a.b.0.c；路径不存在时返回 None。"""
    cur = obj
    path = str(path_expr or "").strip()
    if not path:
        return cur
    for part in path.split("."):
        key = part.strip()
        if key == "":
            continue
        if isinstance(cur, list):
            try:
                idx = int(key)
            except (TypeError, ValueError):
                return None
            if idx < 0 or idx >= len(cur):
                return None
            cur = cur[idx]
            continue
        if isinstance(cur, dict):
            if key not in cur:
                return None
            cur = cur.get(key)
            continue
        return None
    return cur


def _collect_proxy_lines_from_obj(obj) -> list[str]:
    """从任意 JSON 结构提取代理候选行（URL/四段格式/字典字段组装）。"""
    out: list[str] = []

    def push(raw: str):
        s = str(raw or "").strip()
        if not s:
            return
        out.append(s)

    def push_split_record(s: str) -> bool:
        parts = [p.strip() for p in re.split(r"[\r\n]+", str(s or "")) if p.strip()]
        if len(parts) < 4:
            return False
        if not re.fullmatch(r"\d{1,5}", parts[1] or ""):
            return False
        # Some proxy APIs return one proxy as:
        # host \r\n port \r\n username \r\n password \r\n expire_time
        push("/".join(parts[:5] if len(parts) >= 5 else parts[:4]))
        return True

    def walk(node):
        if isinstance(node, str):
            s = node.strip()
            if not s:
                return
            compact = _proxy_compact_credential_line(s)
            if compact:
                push(compact)
                return
            if push_split_record(s):
                return
            parts = _proxy_text_candidates(s)
            if parts:
                for part in parts:
                    push(part)
                return
            if _looks_like_proxy_token(s):
                push(s)
            return
        if isinstance(node, list):
            for it in node:
                walk(it)
            return
        if isinstance(node, dict):
            # 常见字段：proxy/url/server+port/ip+port/user+password
            for k in ("proxy", "proxyUrl", "url", "proxy_url"):
                v = node.get(k)
                if isinstance(v, str) and v.strip():
                    push(v.strip())
            host = str(
                node.get("server")
                or node.get("proxyHost")
                or node.get("proxy_host")
                or node.get("host")
                or node.get("ip")
                or ""
            ).strip()
            port = str(node.get("port") or node.get("proxyPort") or node.get("proxy_port") or "").strip()
            user = str(
                node.get("user")
                or node.get("username")
                or node.get("userName")
                or node.get("account")
                or node.get("authUser")
                or node.get("proxyUser")
                or node.get("proxy_user")
                or ""
            ).strip()
            pwd = str(
                node.get("password")
                or node.get("pass")
                or node.get("passWord")
                or node.get("passwd")
                or node.get("pwd")
                or node.get("proxyPwd")
                or node.get("proxyPassword")
                or node.get("proxy_password")
                or node.get("authPass")
                or node.get("authPwd")
                or node.get("proxyPass")
                or node.get("proxy_pass")
                or ""
            ).strip()
            if host and port and not (user and pwd):
                push(f"{host}:{port}")
            if host and port and user and pwd:
                push(f"{host}/{port}/{user}/{pwd}")
            for v in node.values():
                walk(v)

    walk(obj)
    return out


def _proxy_api_https_opener(*, insecure: bool = False):
    """直连外部代理提取 API；绕过本机 HTTP(S)_PROXY，可选关闭证书校验。"""
    handlers: list = [urllib.request.ProxyHandler({})]
    if insecure:
        handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    else:
        try:
            import certifi  # type: ignore

            ctx = ssl.create_default_context(cafile=certifi.where())
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        except Exception:
            pass
    return urllib.request.build_opener(*handlers)


def _is_ssl_cert_verify_error(ex: BaseException) -> bool:
    msg = str(ex or "")
    if "CERTIFICATE_VERIFY_FAILED" in msg:
        return True
    if "unable to get local issuer certificate" in msg.lower():
        return True
    cur: BaseException | None = ex
    seen = 0
    while cur is not None and seen < 6:
        if isinstance(cur, ssl.SSLCertVerificationError):
            return True
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return False


def _fetch_proxy_lines_from_api(payload: dict) -> dict:
    """调用外部 API，抽取代理行并返回（不直接落盘）。"""
    url = str(payload.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "url 不能为空"}
    method = str(payload.get("method") or "GET").strip().upper()
    if method not in ("GET", "POST"):
        method = "GET"
    timeout_sec = payload.get("timeoutSec", 8.0)
    try:
        timeout = max(2.0, min(60.0, float(timeout_sec)))
    except (TypeError, ValueError):
        timeout = 8.0
    headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
    body = payload.get("body")
    data = None
    req_headers = {str(k): str(v) for k, v in headers.items()}
    if method == "POST":
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json; charset=utf-8")
        elif body is not None:
            data = str(body).encode("utf-8")
            req_headers.setdefault("Content-Type", "text/plain; charset=utf-8")
        else:
            data = b""
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        opener = _proxy_api_https_opener(insecure=False)
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = int(getattr(resp, "status", 200))
    except Exception as ex:
        if url.lower().startswith("https://") and _is_ssl_cert_verify_error(ex):
            try:
                opener = _proxy_api_https_opener(insecure=True)
                with opener.open(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                    status = int(getattr(resp, "status", 200))
            except Exception as ex2:
                return {"ok": False, "error": f"API 请求失败: {ex2}"}
        else:
            return {"ok": False, "error": f"API 请求失败: {ex}"}

    return _parse_proxy_api_response(payload, raw, status)


def _parse_proxy_api_response(payload: dict, raw: str, status: int) -> dict:
    """抽取外部代理 API 返回体里的代理行，供单 API/多 API 路径共用。"""
    path_expr = str(payload.get("jsonPath") or "").strip()
    preferred_scheme = _proxy_import_scheme(payload)
    api_user, api_pwd = _proxy_auth_from_payload(payload)
    candidates: list[str] = []
    fmt = "text"
    try:
        obj = json.loads(raw)
        fmt = "json"
        target = _pick_json_path(obj, path_expr) if path_expr else obj
        candidates = _collect_proxy_lines_from_obj(target)
        if not candidates and path_expr:
            candidates = _collect_proxy_lines_from_obj(obj)
    except Exception:
        # 文本兜底：兼容按行、逗号、空格、分号混排的代理 API。
        compact = _proxy_compact_credential_line(raw)
        candidates = [compact] if compact else _proxy_text_candidates(raw)
    # 归一化 + 剔除非法；重复代理保留为独立池条目。
    valid: list[str] = []
    bad: list[str] = []
    for c in candidates:
        c2 = _attach_proxy_auth_if_needed(c, user=api_user, pwd=api_pwd)
        n = _normalize_proxy_line(c2, scheme=preferred_scheme)
        if n:
            valid.append(n)
        elif str(c or "").strip():
            bad.append(str(c).strip())
    return {
        "ok": True,
        "status": status,
        "responseType": fmt,
        "candidateCount": len(candidates),
        "validCount": len(valid),
        "invalidCount": len(bad),
        "invalid": bad[:30],
        "items": valid,
    }


def _merge_proxy_api_rows(urls: list[str], rows: list[dict | None]) -> dict:
    valid: list[str] = []
    invalid: list[str] = []
    sources: list[dict] = []
    failed_count = 0
    status = None
    candidate_count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        u = str(row.get("url") or "").strip()
        res = row.get("result") if isinstance(row.get("result"), dict) else {}
        ok = bool(res.get("ok"))
        if not ok:
            failed_count += 1
        if status is None and res.get("status") is not None:
            status = res.get("status")
        candidate_count += int(res.get("candidateCount") or 0)
        for item in list(res.get("items") or []):
            s = str(item or "").strip()
            if s:
                valid.append(s)
        invalid.extend([str(x).strip() for x in list(res.get("invalid") or []) if str(x).strip()])
        sources.append(
            {
                "url": u,
                "ok": ok,
                "status": res.get("status"),
                "responseType": res.get("responseType"),
                "candidateCount": int(res.get("candidateCount") or 0),
                "validCount": int(res.get("validCount") or 0),
                "invalidCount": int(res.get("invalidCount") or 0),
                "error": res.get("error"),
            }
        )
    if not valid and failed_count:
        first_err = next((s.get("error") for s in sources if s.get("error")), None)
        return {
            "ok": False,
            "error": first_err or "API 拉取失败",
            "sourceCount": len(urls),
            "failedCount": failed_count,
            "sources": sources,
        }
    return {
        "ok": True,
        "status": status,
        "responseType": "multi",
        "candidateCount": candidate_count,
        "validCount": len(valid),
        "invalidCount": len(invalid),
        "invalid": invalid[:30],
        "items": valid,
        "sourceCount": len(urls),
        "failedCount": failed_count,
        "sources": sources,
    }


def _fetch_proxy_lines_from_apis(payload: dict) -> dict:
    """调用一个或多个代理 API，合并抽取结果（不直接落盘）。"""
    if not isinstance(payload, dict):
        payload = {}
    raw_urls = payload.get("urls")
    if isinstance(raw_urls, str):
        raw_urls = raw_urls.splitlines()
    if isinstance(raw_urls, list):
        urls = [str(u or "").strip() for u in raw_urls if str(u or "").strip()]
    else:
        urls = []
    if not urls:
        one = str(payload.get("url") or "").strip()
        if one:
            urls = [one]
    seen_urls: set[str] = set()
    urls = [u for u in urls if not (u in seen_urls or seen_urls.add(u))]
    if not urls:
        return {"ok": False, "error": "url 不能为空"}
    if len(urls) == 1:
        out = _fetch_proxy_lines_from_api({**payload, "url": urls[0]})
        out["sourceCount"] = 1
        out["failedCount"] = 0 if out.get("ok") else 1
        out["sources"] = [
            {
                "url": urls[0],
                "ok": bool(out.get("ok")),
                "validCount": int(out.get("validCount") or 0),
                "invalidCount": int(out.get("invalidCount") or 0),
                "status": out.get("status"),
                "error": out.get("error"),
            }
        ]
        return out

    rows: list[dict | None] = [None] * len(urls)

    def _one(u: str) -> dict:
        res = _fetch_proxy_lines_from_api({**payload, "url": u})
        return {"url": u, "result": res}

    max_workers = min(3, len(urls))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_one, u): i for i, u in enumerate(urls)}
        for fu in concurrent.futures.as_completed(futs):
            idx = futs[fu]
            try:
                rows[idx] = fu.result()
            except Exception as ex2:
                rows[idx] = {"url": urls[idx], "result": {"ok": False, "error": str(ex2)}}
    return _merge_proxy_api_rows(urls, rows)


def _grab_bot_authorized(handler: BaseHTTPRequestHandler) -> bool:
    sec = (os.environ.get("MT_GRAB_BOT_SECRET") or "").strip()
    if not sec:
        return True
    auth = (handler.headers.get("Authorization") or "").strip()
    return auth == f"Bearer {sec}"


def _debug_dump_mt(path: str, payload: dict, mt_response: object) -> None:
    """
    把本次请求的摘要 + 茅台接口返回体写入 debug_last_api.json（失败时静默）。注：摘要仅含业务参数；Token/Cookie、加密的 actParam、完整 H5 头由服务端 Session 注入，有意不落盘。rush 时可带 resolvedActId、branch、referer 等并入 payload，便于对照「实际用过的抢购参数」。    """
    try:
        from datetime import datetime
        now_mono = time.monotonic()
        if DEBUG_LAST_API_MIN_INTERVAL_MS > 0:
            min_gap_sec = DEBUG_LAST_API_MIN_INTERVAL_MS / 1000.0
            with _DEBUG_LAST_API_TS_LOCK:
                last = float(_DEBUG_LAST_API_LAST_TS.get(path, 0.0) or 0.0)
                if (now_mono - last) < min_gap_sec:
                    return
                _DEBUG_LAST_API_LAST_TS[path] = now_mono

        p = payload or {}
        safe_addr = None
        addr = p.get("address")
        if isinstance(addr, dict):
            safe_addr = {
                "shipAddressId": addr.get("shipAddressId"),
                "name": addr.get("name"),
                "fullAddress": (addr.get("fullAddress") or addr.get("address") or "")[:80],
            }
        # 最终参与 rush 的活动 ID：优先服务端解析结果 resolvedActId
        act_merged = (p.get("resolvedActId") or p.get("actId") or "").strip() or None
        req = {
            "phone": p.get("phone"),
            "itemCode": p.get("itemCode"),
            "actId": act_merged,
            "spuId": p.get("spuId"),
            "branch": (p.get("branch") or "").strip() or None,
            "referer": (p.get("referer") or "").strip() or None,
            "amount": p.get("amount"),
            "sourceId": (p.get("sourceId") or "").strip() or None,
            "hot": p.get("hot"),
            "needCode": p.get("needCode"),
            "priorityRecordId": p.get("priorityRecordId"),
            "count": p.get("count"),
            "address": safe_addr,
        }
        req = {k: v for k, v in req.items() if v is not None}
        out = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "api_path": path,
            "request": req,
            "mt_response": mt_response,
        }
        DEBUG_LAST_API.write_text(
            json.dumps(out, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[debug] 已写入 {DEBUG_LAST_API.name} ← {path}")
    except Exception:
        pass


def _tn_from_pay_data(data: object, _depth: int = 0) -> str:
    """
    支付接口 /order/pay 成功后的 TN，用于 requestPay 网关。常见字段 channelTradeSn / tn；部分版本会套一层 payResult、payInfo 等。    """
    if not isinstance(data, dict) or _depth > 6:
        return ""
    for k in (
        "channelTradeSn",
        "tn",
        "channel_trade_sn",
        "tradeSn",
        "channelTradeNo",
    ):
        v = data.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    for nest in (
        "data",
        "payResult",
        "payInfo",
        "result",
        "bizData",
        "orderPayInfo",
    ):
        sub = data.get(nest)
        if isinstance(sub, dict):
            hit = _tn_from_pay_data(sub, _depth + 1)
            if hit:
                return hit
    return ""


def _pay_extinfo_msg429(pay_data: object) -> str:
    """
    从 /order/pay 的 data.extInfo 提取 MSG_429 文案；无则返回空串。extInfo 常见为 JSON 字符串。    """
    if not isinstance(pay_data, dict):
        return ""
    raw = pay_data.get("extInfo")
    if raw is None:
        return ""
    txt = str(raw).strip()
    if not txt:
        return ""
    try:
        j = json.loads(txt)
    except Exception:
        return ""
    if not isinstance(j, dict):
        return ""
    msg = j.get("MSG_429")
    return str(msg).strip() if msg is not None else ""


def _compose_rotate_max_for_account(_entry, payload: dict) -> int:
    """配置了代理池时按环境变量/请求体换线组单上限（与 Session.compose 一致）。"""
    try:
        from proxy_pool import active_urls, pool_is_configured

        if not pool_is_configured():
            return 1
        au = active_urls()
        if not au:
            return 1
        n = len(au)
        raw = payload.get("composeRotateMax") if isinstance(payload, dict) else None
        if raw is not None and str(raw).strip() != "":
            try:
                cap = int(raw)
            except (TypeError, ValueError):
                cap = n
        else:
            try:
                cap = int(os.environ.get("MT_COMPOSE_POOL_ROTATE_MAX", str(n)) or n)
            except (TypeError, ValueError):
                cap = n
        return max(1, min(cap, 500))
    except Exception:
        return 1


def _mt_business_ok(code: object) -> bool:
    """茅台业务接口常见 code=2000（部分序列化为字符串）。"""
    if code == 2000:
        return True
    try:
        return int(code) == 2000
    except (TypeError, ValueError):
        return str(code).strip() == "2000"


def _record_id_to_str(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        s = str(value)
    elif isinstance(value, float):
        s = str(int(value)) if value.is_integer() else str(value)
    else:
        s = str(value).strip()
        if s.endswith(".0") and s[:-2].isdigit():
            s = s[:-2]
    if not s or s == "0" or s.lower() in ("none", "null", "nan"):
        return ""
    return s


def _extract_priority_record_id(obj: object) -> str:
    if not isinstance(obj, dict):
        return ""
    for key in ("priorityRecordId", "priorityUserRecordId", "recordId"):
        rid = _record_id_to_str(obj.get(key))
        if rid:
            return rid
    data = obj.get("data")
    if isinstance(data, dict):
        for key in ("priorityRecordId", "priorityUserRecordId", "recordId"):
            rid = _record_id_to_str(data.get(key))
            if rid:
                return rid
    return ""


def _normalize_priority_record_id_payload(res: object, trace: object = None) -> object:
    if not isinstance(res, dict):
        return res
    out = dict(res)
    rid = _extract_priority_record_id(out)
    if not rid and isinstance(trace, list):
        for row in trace:
            rid = _extract_priority_record_id(row)
            if rid:
                break
    if rid:
        data = out.get("data")
        data = dict(data) if isinstance(data, dict) else {}
        data["priorityRecordId"] = rid
        data.setdefault("priorityUserRecordId", rid)
        out["data"] = data
        out["priorityRecordId"] = rid
    return out


_VERIFY_CACHE: dict[str, dict] = {}
_VERIFY_CACHE_TS: dict[str, float] = {}
_VERIFY_CACHE_LOCK = threading.Lock()
_VERIFY_CACHE_TTL = 55


def _verify_code_business_ok(vr: dict | None) -> bool:
    if not isinstance(vr, dict) or vr.get("code") != 2000:
        return False
    data = vr.get("data")
    if isinstance(data, dict) and "userVerifyStatus" in data:
        try:
            return int(data.get("userVerifyStatus") or 0) == 2
        except (TypeError, ValueError):
            return False
    return True


def _verify_code_result_message(vr: dict | None) -> str:
    if not isinstance(vr, dict):
        return "验证返回为空"
    data = vr.get("data")
    if isinstance(data, dict) and "userVerifyStatus" in data:
        return f"code={vr.get('code')} userVerifyStatus={data.get('userVerifyStatus')}"
    return str(vr.get("message") or f"code={vr.get('code')}")


def _verify_cache_set(prid: int, verify_res: dict) -> None:
    key = str(prid)
    with _VERIFY_CACHE_LOCK:
        _VERIFY_CACHE[key] = verify_res
        _VERIFY_CACHE_TS[key] = time.time()


def _verify_cache_get(prid: int) -> dict | None:
    key = str(prid)
    with _VERIFY_CACHE_LOCK:
        if key not in _VERIFY_CACHE:
            return None
        if time.time() - _VERIFY_CACHE_TS.get(key, 0) > _VERIFY_CACHE_TTL:
            _VERIFY_CACHE.pop(key, None)
            _VERIFY_CACHE_TS.pop(key, None)
            return None
        return _VERIFY_CACHE[key]


def _verify_cache_success(prid: int) -> dict | None:
    cached = _verify_cache_get(prid)
    if _verify_code_business_ok(cached):
        return cached
    return None


def _gateway_code_ok(code: object) -> bool:
    if code == 200 or code == 2000:
        return True
    s = str(code).strip()
    return s in ("200", "2000")


def _gateway_should_retry_pay(code: object, gw: dict) -> bool:
    """
    requestPay 瞬时失败（Worth 重试）：50 参数异常、29、或回包/嵌套里出现拥堵提示。TN 本身无效时，多次仍失败，由调用方最多 3 次后放弃。    """
    if _gateway_code_ok(code):
        return False
    try:
        ic = int(str(code).strip())
    except (TypeError, ValueError):
        ic = None
    if ic in (250, 429):
        return True
    try:
        blob = json.dumps(gw, ensure_ascii=False)
    except Exception:
        blob = str(gw)
    if any(
        x in blob
        for x in ("拥堵", "MSG_429", "稍后重试", "网络拥堵", "系统繁忙", "请稍后")
    ):
        return True
    return False


def _sdk_string_from_gateway_data(gw_data: object) -> str:
    def _scan(obj: object, depth: int = 0) -> str:
        if depth > 6 or obj is None:
            return ""
        if isinstance(obj, str):
            s = obj.strip()
            if not s:
                return ""
            if s.startswith(("https://", "http://", "alipays://", "weixin://")):
                return s
            # 支付宝 SDK 串常见特征
            low = s.lower()
            if (
                "trade_no=" in low
                or "out_trade_no=" in low
                or "biz_content" in low
                or "total_amount" in low
                or "app_id=" in low
            ):
                return s
            # 微信支付：H5/JSAPI 常见 JSON 或 key=value
            if (
                "prepay_id" in low
                or "mweb_url" in low
                or '"mweb_url"' in s
                or '"prepay_id"' in s
                or ("package" in low and "prepay" in low.replace("_", ""))
                or ("noncestr" in low and "timestamp" in low and "sign" in low)
            ):
                return s
            return ""
        if isinstance(obj, dict):
            # 优先常见字段名（支付宝 + 微信）
            for k in (
                "payInfo",
                "alipay_sdk",
                "orderInfo",
                "AUTH_CODE",
                "mweb_url",
                "h5_url",
                "code_url",
                "mwebUrl",
                "payUrl",
                "pay_data",
                "payData",
                "wechat_sdk",
                "wxPayInfo",
                "wxpay",
                "appPayRequest",
                "jsapi_params",
            ):
                v = obj.get(k)
                if v:
                    hit = _scan(v, depth + 1)
                    if hit:
                        return hit
            # 再扫其余字段
            for v in obj.values():
                hit = _scan(v, depth + 1)
                if hit:
                    return hit
            return ""
        if isinstance(obj, list):
            for v in obj:
                hit = _scan(v, depth + 1)
                if hit:
                    return hit
            return ""
        return ""

    if gw_data is None:
        return ""
    return _scan(gw_data)


def _pay_url_from_sdk(
    sdk: str,
    *,
    proxy_kw: dict | None = None,
    impersonate: str | None = None,
) -> str:
    """与 app.mt_resolve_pay_url_from_sdk 一致（与 mt1.9.5/mt/test-direct-order.js convertToH5 + trade_no 兜底）。"""
    return mt_resolve_pay_url_from_sdk(
        sdk, proxy_kw=proxy_kw, impersonate=impersonate
    )


# ── 支付链接兜底：网关 JSON 里已有支付宝 H5，但 SDK 串解析失败时仍能推个微 ──

_ALIPAY_HTTPS_IN_TEXT = re.compile(r"https://[^\s\"'<>]+", re.I)
_ALIPAYS_IN_TEXT = re.compile(r"alipays://[^\s\"'<>]+", re.I)
_WEIXIN_SCHEME_IN_TEXT = re.compile(r"weixin://[^\s\"'<>]+", re.I)
_UNIONPAY_SCHEME_IN_TEXT = re.compile(r"(?:uppay|upwrp|uppaywallet|unionpay|cloudpay)://[^\s\"'<>]+", re.I)
_MOUTAI_NATIVE_SCHEME_IN_TEXT = re.compile(r"moutaiapp://[^\s\"'<>]+", re.I)

# 云闪付原生支付路由常量（与 demo.py 一致）
_MTPAY_NATIVE_ROUTE_BASE = "moutaiapp://mtpaysdk"
_MTPAY_RESULT_URL = "https://h5.moutai519.com.cn/mt/payResult?appConfig=1_1_1"

# 云闪付直跳链接构建（对应 yunsf.go GetAndroidPayUrl / GetIosPayUrl）
def _build_ysf_android_url(tn: str) -> str:
    """安卓云闪付直跳：upwrp://uppayservice?style=token&paydata=<base64(UTF-8)>。
    paydata 明文里的 tn 只使用 requestPay 返回的 AUTH_CODE；没有 AUTH_CODE 不生成安卓直跳。    """
    import base64 as _b64
    tn = _extract_ysf_android_tn(tn)
    if not tn or not tn.isdigit():
        return ""
    paydata = f"tn={tn},resultURL=exit,usetestmode=false"
    return "upwrp://uppayservice/?style=token&paydata=" + _b64.b64encode(paydata.encode("utf-8")).decode("ascii")


def _extract_ysf_android_tn(raw: str) -> str:
    """提取安卓 upwrp 可用的纯数字 AUTH_CODE，拒绝混入二进制或杂字符的值。"""
    import re as _re

    s = str(raw or "").strip()
    if not s:
        return ""
    if _re.fullmatch(r"\d{16,24}", s):
        return s
    m = _re.search(r"(?<!\d)(\d{16,24})(?!\d)", s)
    return m.group(1) if m else ""


def _build_ysf_ios_url(tn: str) -> str:
    """iOS 云闪付直跳：uppaywallet://uppay?paydata=<3des-ecb-hex>&s=26732007520004206700。
    入参 tn 只使用 requestPay 返回的 AUTH_CODE；没有 AUTH_CODE 不生成 iOS 直跳。    """
    tn = (tn or "").strip()
    if not tn:
        return ""
    try:
        import json as _json
        from Crypto.Cipher import DES3
        key = b"002023102420002800202310"
        data = _json.dumps({"scheme": None, "tn": tn, "mode": "00", "merchantMode": "01"},
                           separators=(",", ":")).encode()
        bs = DES3.block_size  # 8
        pad = bs - len(data) % bs
        data += bytes([pad] * pad)
        cipher = DES3.new(key, DES3.MODE_ECB)
        enc = cipher.encrypt(data).hex()
        return f"uppaywallet://uppay?paydata={enc}&s=26732007520004206700"
    except ImportError:
        return ""


def _strip_url_trail(u: str) -> str:
    return (u or "").strip().rstrip(".,;\"'）)]}〕】")


def _urls_from_text_blob(s: str) -> list[str]:
    if not s or not isinstance(s, str):
        return []
    out: list[str] = []
    for rx in (
        _ALIPAY_HTTPS_IN_TEXT,
        _ALIPAYS_IN_TEXT,
        _WEIXIN_SCHEME_IN_TEXT,
        _UNIONPAY_SCHEME_IN_TEXT,
        _MOUTAI_NATIVE_SCHEME_IN_TEXT,
    ):
        for m in rx.finditer(s):
            out.append(_strip_url_trail(m.group(0)))
    return out


def _is_alipay_related_url(u: str) -> bool:
    low = (u or "").lower()
    return (
        "alipay" in low
        or "alipays://" in low
        or "mclient.alipay" in low
        or "excashier" in low
        or "openapi.alipay" in low
    )


def _is_wechat_pay_url(u: str) -> bool:
    low = (u or "").lower()
    if low.startswith("weixin://"):
        return "pay" in low or "wap" in low or "wxpay" in low
    return (
        "wx.tenpay.com" in low
        or "weixin.qq.com" in low
        or "pay.weixin.qq.com" in low
        or "wxaurl.cn" in low
        or "tenpay.com" in low
    )


def _is_actionable_pay_url(u: str) -> bool:
    """支付宝 / 微信 / 云闪付 / 茅台原生收银台等可直接使用的支付链接。"""
    u = (u or "").strip()
    if not u:
        return False
    low = u.lower()
    if u.startswith("http://"):
        return "alipay" in low or _is_wechat_pay_url(u)
    if u.startswith("https://"):
        return _is_alipay_related_url(u) or _is_wechat_pay_url(u)
    if low.startswith("alipays://"):
        return True
    if low.startswith(("uppay://", "upwrp://", "uppaywallet://", "unionpay://", "cloudpay://")):
        return True
    if low.startswith("moutaiapp://mtpaysdk"):
        return True
    return _is_wechat_pay_url(u)


def _deep_scan_alipay_urls_in_object(obj: object, depth: int = 0) -> list[str]:
    """兼容旧名：等价于支付宝 + 微信可支付 URL 扫描。"""
    return _deep_scan_pay_urls_in_object(obj, depth)


def _deep_scan_pay_urls_in_object(obj: object, depth: int = 0) -> list[str]:
    acc: list[str] = []
    if depth > 10 or obj is None:
        return acc
    if isinstance(obj, str):
        for u in _urls_from_text_blob(obj):
            if _is_actionable_pay_url(u):
                acc.append(u)
        return acc
    if isinstance(obj, dict):
        for v in obj.values():
            acc.extend(_deep_scan_pay_urls_in_object(v, depth + 1))
        return acc
    if isinstance(obj, list):
        for v in obj:
            acc.extend(_deep_scan_pay_urls_in_object(v, depth + 1))
        return acc
    return acc


def _prefer_https_alipay_pay_url(candidates: list[str]) -> str:
    """兼容旧名：在支付宝 / 微信候选里择优返回一条。"""
    return _prefer_https_pay_url(candidates)


def _prefer_https_pay_url(candidates: list[str]) -> str:
    if not candidates:
        return ""
    seen: set[str] = set()
    uniq: list[str] = []
    for u in candidates:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        uniq.append(u)

    def sort_key(u: str) -> tuple:
        low = u.lower()
        if u.startswith("https://"):
            if "mclient.alipay.com" in low or (
                "cashier" in low and "alipay" in low
            ) or "excashier" in low:
                return (0, -len(u))
            if "wx.tenpay.com" in low or "pay.weixin.qq.com" in low:
                return (1, -len(u))
            if "weixin.qq.com" in low and "pay" in low:
                return (2, -len(u))
            if "alipay" in low or "openapi.alipay" in low:
                return (3, -len(u))
            if _is_wechat_pay_url(u):
                return (4, -len(u))
            return (5, -len(u))
        if u.startswith("alipays://"):
            return (6, -len(u))
        if u.startswith("http://") and ("alipay" in low or _is_wechat_pay_url(u)):
            return (7, -len(u))
        if low.startswith("weixin://"):
            return (8, -len(u))
        return (9, -len(u))

    uniq.sort(key=sort_key)
    return uniq[0]


def _sdk_looks_alipay(s: str) -> bool:
    low = (s or "").lower()
    return (
        "alipays://" in low
        or "alipay.trade" in low
        or ("app_id=" in low and "biz_content" in low)
        or "mclient.alipay.com" in low
    )


def _build_unionpay_native_route(tn: str, order_id: str, gw: object, pdata: object) -> str:
    """
    云闪付：当 requestPay 网关未返回可直接跳转的 uppay:// 链接时，
    用 TN + orderId 构建 moutaiapp://mtpaysdk?... 原生路由，
    供用户在 i茅台 App 内打开完成银联支付（与 demo.py build_moutai_native_pay_route 一致）。
    """
    tn = str(tn or "").strip()
    order_id = str(order_id or "").strip()
    if not tn or not order_id:
        return ""
    out_trade_no = ""
    total_price = ""
    for blob in (gw, pdata):
        if not isinstance(blob, dict):
            continue
        data = blob.get("data") if isinstance(blob.get("data"), dict) else blob
        if not out_trade_no:
            out_trade_no = str(
                data.get("outTradeNo") or data.get("payOrderId") or
                data.get("out_trade_no") or data.get("tradeNo") or ""
            ).strip()
        if not total_price:
            total_price = str(
                data.get("totalPrice") or data.get("totalAmount") or
                data.get("total_amount") or data.get("amount") or ""
            ).strip()
    params = urlencode({
        "orderId": order_id,
        "channelTradeSn": tn,
        "outTradeNo": out_trade_no or order_id,
        "totalPrice": total_price,
        "extInfo": "",
        "payResultQueryTimeout": "10",
        "payResultURL": _MTPAY_RESULT_URL,
        "orderType": "1",
    })
    url = f"{_MTPAY_NATIVE_ROUTE_BASE}?{params}"
    print(f"[云闪付] 构建原生路由: {url[:120]}", flush=True)
    return url


def _resolve_pay_url_for_notify(
    sdk: str,
    pay_res: dict | None,
    gw: dict | None,
    pdata: dict | None,
    *,
    proxy_kw: dict | None = None,
    impersonate: str | None = None,
) -> str:
    """SDK 解析（仅支付宝串走 mcgw）+ 网关/支付 JSON 全量扫描，支持微信 H5（wx.tenpay / weixin.qq 等）。"""
    merged: list[str] = []
    s = (sdk or "").strip()
    if s:
        merged.extend(_deep_scan_pay_urls_in_object(s))
        if _sdk_looks_alipay(s):
            primary = (
                _pay_url_from_sdk(s, proxy_kw=proxy_kw, impersonate=impersonate) or ""
            ).strip()
            if primary:
                merged.append(primary)
    for blob in (gw, pay_res, pdata):
        if blob:
            merged.extend(_deep_scan_pay_urls_in_object(blob))
    out_url = _prefer_https_pay_url(merged)
    # #region agent log
    try:
        _dbg_p = Path(__file__).resolve().parent / "debug-67b662.log"
        _dbg_p.open("a", encoding="utf-8").write(
            json.dumps(
                {
                    "sessionId": "67b662",
                    "runId": "pay-link-debug-v1",
                    "hypothesisId": "H3",
                    "location": "webui_server.py:_resolve_pay_url_for_notify",
                    "message": "resolve_candidates",
                    "data": {
                        "sdkLen": len(str(s or "")),
                        "candidateCount": len(merged),
                        "resolved": bool(out_url),
                        "resolvedPrefix": str(out_url or "")[:28],
                    },
                    "timestamp": int(time.time() * 1000),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    except Exception:
        pass
    # #endregion
    return out_url


def _resolve_order_sku_imtp(payload: dict) -> tuple[str, str]:
    """
    App compose/submit(v2) 口径：订单 spuId 使用数字商品编码（itemCode）；
    IMTP 仅作为 sourceId 回退值保留。返回 (sku, imtp_fallback)；sku 为空表示参数不足。    """
    p = payload or {}
    ic = str(p.get("itemCode", "")).strip()
    spu = str(p.get("spuId", "")).strip()
    imtp_extra = str(p.get("imtp", "")).strip()
    sku = ic
    imtp = imtp_extra
    if not imtp and spu.upper().startswith("IMTP"):
        imtp = spu
    if not imtp and ic.upper().startswith("IMTP"):
        imtp = ic
    if not sku and spu.isdigit():
        sku = spu
    return sku, imtp


def _order_sku_digit_error(sku: str, action: str = "组单/下单") -> str | None:
    """App 订单 spuId 须为纯数字；否则返回说明文案。"""
    s = (sku or "").strip()
    if not s:
        return None
    if s.isdigit():
        return None
    return (
        f"{action} 的商品 ID 须为纯数字编码（与抢购 itemCode 一致），当前值 {s!r} 非法。"
        "请勿单独把 IMTP 编号当作 itemCode；请填写卡片「商品 ID」中的数字。"
    )


def _pick_default_ship_address(lst: object) -> dict | None:
    if not isinstance(lst, list) or not lst:
        return None
    cands = [a for a in lst if isinstance(a, dict) and a.get("shipAddressId")]
    if not cands:
        return None
    for a in cands:
        if a.get("dft"):
            return a
    return cands[0]


def _resolve_ship_address_for_order(
    phone: str,
    addr_payload: object,
    s: Session,
) -> tuple[dict | None, str]:
    """
    启用本地库时：组单/下单一律优先使用库中默认收货地址（与卡片展示同源）。库无记录时：再用请求体中的 shipAddressId 或 address；仍无则 ship/query。    """
    addr: dict = (
        dict(addr_payload) if isinstance(addr_payload, dict) else {}
    )
    if address_db.enabled():
        cached = address_db.get_default_address_dict(phone)
        if cached:
            merged = dict(cached)
            for k, v in addr.items():
                if v is not None and v != "" and k not in merged:
                    merged[k] = v
            return merged, "已使用本地地址库默认收货地址（组单/下单）"
    if addr.get("shipAddressId"):
        return addr, ""
    lst, raw = s.addresses_detail()
    if not isinstance(raw, dict):
        return None, "拉取收货地址失败（接口异常）"
    if raw.get("code") != 2000:
        msg = str(raw.get("message") or raw.get("code") or "地址接口未成功")
        return None, msg
    picked = _pick_default_ship_address(lst)
    if not picked:
        return None, "账号无有效收货地址（请先在 i 茅台 APP 添加）"
    merged = dict(picked)
    for k, v in addr.items():
        if v is not None and v != "" and k not in merged:
            merged[k] = v
    return (
        merged,
        "已自动选用接口返回的默认/首条收货地址",
    )


def _ui_state_with_address_db_overlay(st: object) -> dict:
    """
    将账号池中各号的 addressBook 条目替换为本地库列表（库有非空时），供卡片默认展示。    """
    if not isinstance(st, dict):
        return {}
    if not address_db.enabled():
        return st
    out = dict(st)
    ab0 = out.get("addressBook")
    ab: dict[str, list] = dict(ab0) if isinstance(ab0, dict) else {}
    for row in POOL.snapshot():
        if not isinstance(row, dict):
            continue
        ph = _norm_phone(row.get("phone") or "")
        if not ph:
            continue
        lst = address_db.get_cached_address_list(ph)
        if lst:
            ab[ph] = lst
    out["addressBook"] = ab
    return out


# 与 product_list.json jumpUrlV2 解码一致：仅当条目无 referer 时的兜底（与 ui.html MT_ITEM_CODE_REFERER 宜同步）
MT_H5_REFERER_BY_ITEM_CODE = {
    "11942": "https://h5.moutai519.com.cn/mt/item/mm-485-detail-group?appConfig=2_1_2",
    "11947": "https://h5.moutai519.com.cn/mt/item/mm-485-detail-group?appConfig=2_1_2",
    "741": "https://h5.moutai519.com.cn/mt/item/smsp-detail?appConfig=2_1_2",
    "1741": "https://h5.moutai519.com.cn/mt/item/jpmt-detail?appConfig=2_1_2",
    "11942111": "https://h5.moutai519.com.cn/mt/item/mm-485-detail-group?appConfig=2_1_2",
    "483": "https://h5.moutai519.com.cn/mt/item/1000ml-detail?appConfig=2_1_2",
    "10193": "https://h5.moutai519.com.cn/mt/item/xft-detail?appConfig=2_1_2&sourceId=IMTP1000006",
    "425": "https://h5.moutai519.com.cn/mt/item/grad-detail?gt=ft_43&appConfig=2_1_2",
    "11894": "https://h5.moutai519.com.cn/mt/item/gyft-8392-detail?appConfig=2_1_2",
    "10220": "https://h5.moutai519.com.cn/mt/item/grad-detail?appConfig=2_1_2&gt=mt_53_15th",
    "11652": "https://h5.moutai519.com.cn/mt/item/hxx-detail?appConfig=2_1_2&sourceId=IMTP1000260",
    "11945": "https://h5.moutai519.com.cn/mt/item/mm-zx-271-detail?appConfig=2_1_2",
    "2181": "https://h5.moutai519.com.cn/mt/item/detailV2?appConfig=2_1_2&id=IMTP1000002",
}

# 与 ui.html MT_DETAIL_KIND_BRANCH 一致：详情 path -> 抢购分支（空=走默认 rushPurchase）
_MT_DETAIL_KIND_TO_BRANCH = {
    "smsp-detail": "one",
    "jpmt-detail": "three",
    "1000ml-detail": "",
    "grad-detail": "",
    "xft-detail": "",
    "gyft-8392-detail": "",
    "mm-485-detail-group": "",
    "mm-zx-271-detail": "",
    "hxx-detail": "",
    "zj-detail": "",
    "detailV2": "",
}

# 与 ui.html MT_ITEM_CODE_BRANCH 一致：特定编码强制分支（优先于 URL 推导）
_MT_ITEM_CODE_TO_BRANCH = {
    "741": "one",
    "1741": "three",
    "11942": "two",
    "11947": "two",
    "11945": "two",
    "11894": "three",
}


def _imtp_from_referer_query(ref: str) -> str:
    """从 H5 Referer 的查询参数提取 IMTP（sourceId / id / spuId）。"""
    if not ref:
        return ""
    try:
        u = urlparse(ref)
        qs = parse_qs(u.query)
        for key in ("sourceId", "id", "spuId"):
            vals = qs.get(key)
            if not vals:
                continue
            v = (vals[0] or "").strip()
            if v.upper().startswith("IMTP"):
                return v if v.startswith("IMTP") else v
            if key == "id" and "IMTP" in v.upper():
                return v
    except Exception:
        pass
    return ""


def _normalize_h5_referer(ref: str) -> str:
    r = (ref or "").strip()
    if not r:
        return ""
    if not re.match(r"^https?://", r, re.I):
        r = "https://" + r.lstrip("/")
    return r


def _referer_for_verify_context(referer: str, item_code: str = "") -> str:
    ref = _normalize_h5_referer(referer)
    if ref:
        return ref
    code = str(item_code or "").strip()
    if code:
        return _normalize_h5_referer(MT_H5_REFERER_BY_ITEM_CODE.get(code, ""))
    return ""


def _resolve_mt_item_route(item_code: str, referer: str) -> dict:
    """
    根据商品 ID（itemCode）和 H5 详情页（referer）推导编号（spuId）、抢购分支、是否需 code。商品库：mt_item_list.json 为主，product_list 长码兜底。    """
    code = (item_code or "").strip()
    ref = _normalize_h5_referer(referer)
    if not code:
        return {"ok": False, "message": "商品 ID（itemCode）不能为空"}
    if not ref:
        ref = (MT_H5_REFERER_BY_ITEM_CODE.get(code) or "").strip()
        ref = _normalize_h5_referer(ref)
    if not ref:
        return {
            "ok": False,
            "message": "请填写 H5 详情页完整链接，或维护 mt_item_list.json / product_list.json。",
        }

    dk = detail_kind_from_referer(ref)
    match = None
    try:
        match = catalog_item_by_code(code)
    except Exception:
        pass
    matches: list[dict] = [match] if match else []

    branch = ""
    if match and (match.get("branchHint") or "").strip():
        branch = str(match.get("branchHint")).strip()
    elif code in _MT_ITEM_CODE_TO_BRANCH:
        branch = str(_MT_ITEM_CODE_TO_BRANCH[code])
    elif dk in _MT_DETAIL_KIND_TO_BRANCH:
        branch = str(_MT_DETAIL_KIND_TO_BRANCH[dk] or "")

    spu_id = ""
    if match:
        spu_id = str(match.get("spuId") or "").strip()
    if not spu_id:
        spu_id = _imtp_from_referer_query(ref)
    need_code = MT_NEED_CODE_BY_ITEM_CODE.get(code)
    if need_code is None and matches:
        nc = matches[0].get("needCodeHint")
        if nc is not None:
            need_code = bool(nc)
    if need_code is None:
        need_code = False

    if not spu_id:
        return {
            "ok": False,
            "message": (
                f"未解析到编号（spuId）：商品 ID {code} 不在本地库中，且 H5 链接参数无 sourceId/id。"
                "请核对商品 ID、详情页 URL，或勾选「显示全部分类」后重试。"
            ),
        }

    act_hint = ""
    if matches:
        act_hint = str(matches[0].get("actId") or "").strip()

    return {
        "ok": True,
        "itemCode": code,
        "spuId": spu_id,
        "referer": ref,
        "detailKind": dk,
        "branch": branch,
        "needCodeHint": bool(need_code),
        "fromCatalog": bool(matches),
        "catalogMatches": len(matches),
        "actId": act_hint,
    }


def _purchase_info_auth_error_message(pinfo: object) -> str | None:
    """
    purchaseInfoV2 / 解析 actId 失败时，若实为鉴权问题则返回明确提示（避免误判为「活动 ID 解析」）。典型：HTTP 401 体或业务 code=4011、[ianus-token-auth] invalid userId。    """
    if not isinstance(pinfo, dict):
        return None
    msg = str(pinfo.get("message") or "")
    low = msg.lower()
    c = pinfo.get("code")
    try:
        ci = int(c) if c is not None and str(c).strip() not in ("", "none") else None
    except (TypeError, ValueError):
        ci = None
    if ci == 4011 or "4011" in msg or "invalid userid" in low or "ianus-token" in low:
        return (
            "当前账号登录态无效（Token 与 userId 不一致或已失效，网关常见 invalid userId / 4011）。"
            "请对本号执行「刷新登录」或重新验证码登录后再抢购、测黑；与 Node 桥 POST 参数传递无关。"
        )
    if ci == 401 and ("4011" in msg or "ianus" in low or "invalid" in low):
        return (
            "商品信息接口返回 HTTP 401：登录态无效。请重新登录后再试。"
        )
    return None


def _call_with_session_local_ip(s: Session, fn, *args, **kwargs):
    """
    purchaseInfoV2 / 解析 actId 失败时，若实为鉴权问题则返回明确提示（避免误判为「活动 ID 解析」）。典型：HTTP 401 体或业务 code=4011、[ianus-token-auth] invalid userId。    """
    names = (
        "_proxy_url",
        "_use_proxy_pool",
        "_proxy_direct_hop",
        "_account_explicit_direct",
        "_proxy_per_account_chain",
        "_proxy_rotation_chain",
        "_proxy_rotation_idx",
        "_proxy_prewarm_ok_for",
    )
    saved = {name: getattr(s, name, None) for name in names}
    try:
        s._proxy_url = ""
        s._use_proxy_pool = False
        s._proxy_direct_hop = True
        s._account_explicit_direct = True
        s._proxy_per_account_chain = False
        s._proxy_rotation_chain = []
        s._proxy_rotation_idx = 0
        s._proxy_prewarm_ok_for = ""
        return fn(*args, **kwargs)
    finally:
        for name, value in saved.items():
            try:
                setattr(s, name, value)
            except Exception:
                pass


def _rush_prepare_act(
    s: Session,
    item: str,
    act_id: str,
    spu_imtp: str,
    referer: str,
    hot: bool,
) -> dict:
    """
    拉 actId 时先用 spuId(IMTP) 向 purchaseInfo 拉活动 ID。若 actId、spuId 皆空，则用 itemCode + referer（或商品库默认详情页）自动解析编号。成功返回 ok=True 及 act_id、spu_imtp、referer、route_info、pinfo。    """
    pinfo = None
    route_info = None
    act_id = (act_id or "").strip()
    spu_imtp = (spu_imtp or "").strip()
    referer = (referer or "").strip()
    if act_id:
        return {
            "ok": True,
            "act_id": act_id,
            "spu_imtp": spu_imtp,
            "referer": referer,
            "route_info": None,
            "pinfo": None,
        }
    if not spu_imtp:
        route = _resolve_mt_item_route(item, referer)
        if not route.get("ok"):
            return {
                "ok": False,
                "error": (
                    (route.get("message") or "无法自动匹配编号（spuId）")
                    + "；请填写编号或活动 ID，或在抢购页点「匹配路由」、从商品库一键配置。"
                ),
                "detail": route,
            }
        spu_imtp = str(route.get("spuId") or "").strip()
        if not spu_imtp:
            return {
                "ok": False,
                "error": "自动匹配后仍无编号（spuId）",
                "detail": route,
            }
        route_info = route
        referer = str(route.get("referer") or referer).strip()
    act_id, pinfo = s.fetch_priority_act_id(
        spu_imtp, hot=bool(hot), referer=referer
    )
    if not act_id:
        auth_msg = _purchase_info_auth_error_message(pinfo)
        if auth_msg:
            return {"ok": False, "error": auth_msg, "detail": pinfo}
        hint = ""
        if isinstance(pinfo, dict):
            c = pinfo.get("code")
            m = pinfo.get("message") or ""
            if c is not None or m:
                hint = f"（接口返回 code={c} {m}）".strip()
        return {
            "ok": False,
            "error": (
                "无法从商品信息接口解析活动 ID。"
                "code=2000 只表示接口成功，部分商品当下无优先购/返回里无活动字段、或编号与 Referer、抢购分支不一致。"
                "请对齐详情页与编号，或改用手动填写活动 ID（actId）。"
                + (hint if hint else "")
            ),
            "detail": pinfo,
        }
    return {
        "ok": True,
        "act_id": act_id,
        "spu_imtp": spu_imtp,
        "referer": referer,
        "route_info": route_info,
        "pinfo": pinfo,
    }


def _verify_bs_dvid_and_device(e, s: Session) -> tuple[list[str], list[str], dict]:
    """开抢前核实 BS-DVID / 浩天三元组 / JWT 与 aid 对齐。"""
    issues: list[str] = []
    warnings: list[str] = []
    facts: dict = {}
    bs = (e.bsid or "").strip()
    facts["hasBsid"] = bool(bs)
    facts["bsidLen"] = len(bs)
    if not bs:
        issues.append("缺少 BS-DVID（bsid）：请走「发码→登录」（发码前浩天）或批量「补全浩天」")
    else:
        if len(bs) < 16:
            warnings.append(f"BS-DVID 长度仅 {len(bs)}，可能无效")
        sess_bs = (getattr(s, "_bsid", "") or "").strip()
        facts["sessionBsidMatch"] = (not sess_bs) or sess_bs == bs
        if sess_bs and sess_bs != bs:
            issues.append("Session 内 bsid 与账号文件不一致，请刷新登录")
        wv_ua = (getattr(s, "_wv_ua", "") or "").strip()
        facts["uaHasBsDvid"] = (not bs) or (f"BS-DVID/{bs}" in wv_ua)
        if bs and wv_ua and f"BS-DVID/{bs}" not in wv_ua:
            warnings.append("抢购 User-Agent 可能未正确嵌入 BS-DVID")
    for key, label in (("sdk", "sdk"), ("mfr", "厂商 mfr"), ("mdl", "机型 mdl")):
        v = (getattr(e, key, "") or "").strip()
        facts[key] = bool(v)
        if not v:
            warnings.append(f"浩天 deviceConfig 缺少 {label}")
    aid = (e.aid or "").strip()
    facts["hasAid"] = bool(aid)
    if not aid:
        issues.append("缺少 device-id（aid）")
    tok = (e.token or "").strip()
    uid = (e.uid or "").strip()
    facts["hasToken"] = bool(tok)
    facts["hasUid"] = bool(uid)
    if not tok or not uid:
        issues.append("登录态不完整（token 或 uid 为空）")
    else:
        try:
            from shield import raw_id_from_mt_jwt

            jwt_rid = (raw_id_from_mt_jwt(tok) or "").strip()
            if jwt_rid:
                facts["jwtDeviceIdPrefix"] = jwt_rid[:12]
            if jwt_rid and aid and jwt_rid != aid:
                issues.append(
                    "JWT 内 deviceId 与 aid 不一致，易 4011；请「刷新登录」或重新验证码登录"
                )
        except Exception:
            pass
    return issues, warnings, facts


def _verify_rush_product_params(
    s: Session, task: dict
) -> tuple[list[str], list[str], dict]:
    """与真实抢购相同的 _rush_prepare_act / 商品库路由，不打 rushPurchase。"""
    issues: list[str] = []
    warnings: list[str] = []
    resolved: dict = {}
    item = str(task.get("itemCode") or "").strip()
    if not item:
        warnings.append("未填商品 ID，跳过活动 ID / 编号核实")
        return issues, warnings, resolved
    act_id = str(task.get("actId") or "").strip()
    spu = str(task.get("spuId") or "").strip()
    referer = str(task.get("referer") or "").strip()
    branch = str(task.get("branch") or "").strip().lower()
    hot = bool(task.get("hot", True))
    need_code = bool(task.get("needCode", False))

    route = _resolve_mt_item_route(item, referer)
    if route.get("ok"):
        resolved["catalogRoute"] = {
            "spuId": route.get("spuId"),
            "branch": route.get("branch"),
            "referer": route.get("referer"),
            "needCodeHint": route.get("needCodeHint"),
            "detailKind": route.get("detailKind"),
        }
        rec_branch = str(route.get("branch") or "").strip().lower()
        if branch and rec_branch and branch != rec_branch:
            warnings.append(
                f"分支「{branch}」与商品库推荐「{rec_branch}」不一致"
            )
        rec_spu = str(route.get("spuId") or "").strip()
        if spu and rec_spu and spu != rec_spu:
            warnings.append(f"编号 {spu} 与库推荐 {rec_spu} 不一致")
        if not spu and rec_spu:
            resolved["suggestedSpuId"] = rec_spu
        if not referer and route.get("referer"):
            resolved["suggestedReferer"] = route.get("referer")
    elif not spu and not act_id:
        issues.append(str(route.get("message") or "无法解析编号（spuId）"))

    prep = _rush_prepare_act(s, item, act_id, spu, referer, hot)
    if not prep.get("ok"):
        issues.append(str(prep.get("error") or "活动 ID 解析失败"))
        if prep.get("detail") is not None:
            resolved["purchaseInfoDetail"] = prep.get("detail")
    else:
        resolved["actId"] = prep.get("act_id")
        resolved["spuId"] = prep.get("spu_imtp")
        resolved["referer"] = prep.get("referer")
        if prep.get("route_info"):
            resolved["autoResolvedRoute"] = prep.get("route_info")
        pinfo = prep.get("pinfo")
        if isinstance(pinfo, dict):
            resolved["purchaseInfoCode"] = pinfo.get("code")
            resolved["purchaseInfoMessage"] = pinfo.get("message")
    if route.get("ok") and route.get("needCodeHint") and not need_code:
        warnings.append("该商品通常需 needCode，当前未勾选")
    return issues, warnings, resolved


def _verify_one_account_rush_readiness(task: dict) -> dict:
    phone = str(task.get("phone") or "").strip()
    e = POOL.get(phone)
    if not e:
        return {"phone": phone, "ok": False, "issues": ["账号不存在"], "warnings": []}
    if e.status != STATUS_OK:
        return {
            "phone": phone,
            "ok": False,
            "issues": ["账号未登录"],
            "warnings": [],
        }
    try:
        s = e.ensure_session()
    except Exception as ex:
        return {
            "phone": phone,
            "ok": False,
            "issues": [f"Session 失败: {ex}"],
            "warnings": [],
        }
    iss, warn, dev = _verify_bs_dvid_and_device(e, s)
    p_iss, p_warn, resolved = _verify_rush_product_params(s, task)
    iss.extend(p_iss)
    warn.extend(p_warn)
    return {
        "phone": phone,
        "ok": len(iss) == 0,
        "issues": iss,
        "warnings": warn,
        "device": dev,
        "resolved": resolved,
    }


def _batch_shared_act_group_key(task: dict) -> tuple | None:
    """
    purchaseInfoV2 / 解析 actId 失败时，若实为鉴权问题则返回明确提示（避免误判为「活动 ID 解析」）。典型：HTTP 401 体或业务 code=4011、[ianus-token-auth] invalid userId。    """
    if not isinstance(task, dict):
        return None
    item = str(task.get("itemCode", "")).strip()
    act_id = str(task.get("actId", "")).strip()
    spu_imtp = str(task.get("spuId", "")).strip()
    referer = str(task.get("referer", "")).strip()
    hot = bool(task.get("hot", True))
    if act_id or not item:
        return None
    if not spu_imtp:
        route = _resolve_mt_item_route(item, referer)
        if not route.get("ok"):
            return None
        spu_imtp = str(route.get("spuId") or "").strip()
        referer = str(route.get("referer") or referer).strip()
    if not spu_imtp:
        return None
    return (spu_imtp, referer, hot)


def _batch_act_reuse_group_info(task: dict) -> tuple[tuple, str, str] | None:
    if not isinstance(task, dict):
        return None
    item = str(task.get("itemCode", "")).strip()
    if not item:
        return None
    spu_imtp = str(task.get("spuId", "")).strip()
    referer = str(task.get("referer", "")).strip()
    hot = bool(task.get("hot", True))
    if not spu_imtp:
        route = _resolve_mt_item_route(item, referer)
        if not route.get("ok"):
            return None
        spu_imtp = str(route.get("spuId") or "").strip()
        referer = str(route.get("referer") or referer).strip()
    if not spu_imtp:
        return None
    return ((item, spu_imtp, referer, hot), spu_imtp, referer)


def _batch_rush_reuse_known_act_ids(tasks: list[dict]) -> int:
    if len(tasks) < 2:
        return 0
    known: dict[tuple, dict[str, int]] = {}
    infos: list[tuple[int, tuple, str, str]] = []
    for i, raw in enumerate(tasks):
        info = _batch_act_reuse_group_info(raw)
        if info is None:
            continue
        key, spu_imtp, referer = info
        aid = str(raw.get("actId", "")).strip()
        if aid:
            by_act = known.setdefault(key, {})
            by_act[aid] = by_act.get(aid, 0) + 1
        else:
            infos.append((i, key, spu_imtp, referer))
    if not known or not infos:
        return 0
    filled = 0
    for i, key, spu_imtp, referer in infos:
        by_act = known.get(key)
        if not by_act:
            continue
        aid = sorted(by_act.items(), key=lambda kv: kv[1], reverse=True)[0][0]
        tasks[i]["actId"] = aid
        if spu_imtp and not str(tasks[i].get("spuId", "")).strip():
            tasks[i]["spuId"] = spu_imtp
        if referer and not str(tasks[i].get("referer", "")).strip():
            tasks[i]["referer"] = referer
        filled += 1
    return filled


def _batch_rush_prefill_shared_act_id(tasks: list[dict]) -> None:
    """
    批量抢购：多任务均未填 actId 且归一化后属同一 (spu,referer,hot) 时，
    只打一次 purchaseInfoV2，将 actId（及归一化后的 spuId/referer）写回同组任务，
    避免并行准备阶段重复请求。    """
    if len(tasks) < 2:
        return
    groups: dict[tuple, list[int]] = {}
    for i, raw in enumerate(tasks):
        k = _batch_shared_act_group_key(raw)
        if k is None:
            continue
        groups.setdefault(k, []).append(i)

    for _k, idxs in groups.items():
        if len(idxs) < 2:
            continue
        e = None
        leader_idx = idxs[0]
        for j in idxs:
            ph = str(tasks[j].get("phone", "")).strip()
            ex = POOL.get(ph)
            if ex and ex.status == STATUS_OK:
                e = ex
                leader_idx = j
                break
        if e is None:
            continue
        t0 = tasks[leader_idx]
        try:
            s = e.ensure_session()
            s._rush_full_pool_sequence = bool(getattr(e, "full_pool_rush", False))
            item = str(t0.get("itemCode", "")).strip()
            prep = _rush_prepare_act(
                s,
                item,
                str(t0.get("actId", "")).strip(),
                str(t0.get("spuId", "")).strip(),
                str(t0.get("referer", "")).strip(),
                bool(t0.get("hot", True)),
            )
        except Exception:
            continue
        if not prep.get("ok"):
            continue
        aid = str(prep.get("act_id") or "").strip()
        if not aid:
            continue
        spu_out = str(prep.get("spu_imtp") or "").strip()
        ref_out = str(prep.get("referer") or "").strip()
        for j in idxs:
            tasks[j]["actId"] = aid
            if spu_out:
                tasks[j]["spuId"] = spu_out
            if ref_out:
                tasks[j]["referer"] = ref_out
        spu_hint = (spu_out[:28] + "…") if len(spu_out) > 28 else spu_out
        print(
            f"[batch_rush] 活动 ID 合并拉取：{len(idxs)} 个任务共用 actId={aid}"
            + (f"（spu={spu_hint}）" if spu_hint else ""),
            flush=True,
        )


def _batch_rush_pay_payload(
    phone: str,
    e,
    s: Session,
    payload: dict,
    prep: dict,
    res: dict,
    rush_only: bool = False,
):
    act_id = prep["act_id"]
    spu_imtp = prep["spu_imtp"]
    referer = prep["referer"]
    pinfo = prep.get("pinfo")
    route_info = prep.get("route_info")
    pay = {
        "rush": res,
        "resolvedActId": act_id,
        "resolvedSpuId": spu_imtp or None,
        "purchaseInfo": pinfo,
        "autoResolvedRoute": route_info,
    }
    if rush_only:
        pay["distributedRushOnly"] = True
    _debug_dump_mt(
        "/api/accounts/batch_rush",
        {
            **payload,
            "resolvedActId": act_id,
            "resolvedSpuId": spu_imtp or None,
            "branch": payload.get("branch") or None,
            "referer": referer or None,
            "amount": payload.get("amount"),
            "sourceId": payload.get("sourceId") or None,
            "hot": payload.get("hot", True),
            "needCode": payload.get("needCode", False),
            "autoResolvedRoute": route_info,
        },
        res,
    )
    if not rush_only and isinstance(res, dict) and _mt_business_ok(res.get("code")):
        try:
            pay["rushIdentityPinned"] = bool(
                POOL.pin_h5_device_after_rush_ok(phone, session=s, rush_response=res)
            )
            if e and (getattr(e, "rush_success_egress_ip", "") or "").strip():
                pay["rushSuccessEgressIp"] = e.rush_success_egress_ip.strip()
            if e and (getattr(e, "rush_success_proxy_url", "") or "").strip():
                pay["rushSuccessProxyHost"] = _proxy_hostport_for_log(e.rush_success_proxy_url)
        except Exception as pin_ex:
            pay["rushIdentityPinned"] = False
            pay["rushPinError"] = str(pin_ex)
            print(f"[batch_rush] 抢购成功但写死设备/代理失败: {pin_ex}")
    if not (e.bsid or "").strip():
        pay["deviceHint"] = (
            "当前账号暂无 BS-DVID：当前策略仅在发码前获取浩天并复用。"
            "请重新走「发码 -> 登录」流程。"
        )
    return pay


def _batch_rush_finalize_from_node(s: Session, nr: dict) -> dict:
    if nr.get("rushLoop"):
        if not nr.get("ok"):
            return {
                "code": -1,
                "message": str(nr.get("error") or "Node rush_loop 失败"),
            }
        last = nr.get("last")
        if "lastProxyUrl" in nr:
            try:
                s._set_proxy_from_rotation(nr["lastProxyUrl"])
            except Exception:
                pass
        if isinstance(last, dict):
            out = dict(last)
            tr = _normalize_rush_loop_trace_rows(nr.get("trace"))
            if isinstance(tr, list) and tr:
                out["rushLoopTrace"] = tr
            return _normalize_priority_record_id_payload(out, tr)
        return {"code": -1, "message": "rush_loop 无有效 last"}
    if not nr.get("ok"):
        return {
            "code": -1,
            "message": str(nr.get("error") or "Node 请求失败"),
        }
    try:
        status = int(nr.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    b64 = nr.get("bodyB64") or ""
    try:
        content = base64.b64decode(b64) if b64 else b""
    except Exception:
        content = b""
    rh = nr.get("headers")
    rsp = _NodeHttpResponse(status, content, rh if isinstance(rh, dict) else {})
    return s._normalize_rush_http_response(rsp)


def _distributed_rush_only_mode() -> bool:
    return (os.environ.get("MT_DIST_RUSH_ONLY") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _parse_rush_loop_attempts_from_payload(payload: dict):
    """解析前端 / 任务里的连发次数；有效范围 >=1，未传则返回 None（沿用 Session._rush_max_attempts）。"""
    for key in ("rushLoopAttempts", "retry", "rushRetries"):
        raw = payload.get(key)
        if raw is None or raw == "":
            continue
        try:
            n = int(raw)
            return max(1, n)
        except (TypeError, ValueError):
            continue
    return None


def _parse_positive_int_from_payload(payload: dict, keys: tuple[str, ...]) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        raw = payload.get(key)
        if raw is None or raw == "":
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return None


def _parse_rush_shot_index_from_payload(payload: dict) -> int | None:
    return _parse_positive_int_from_payload(
        payload,
        ("rushShotIndex", "shotIndex", "attemptIndex", "rushAttemptIndex"),
    )


def _parse_rush_lane_count_from_payload(payload: dict) -> int | None:
    return _parse_positive_int_from_payload(
        payload,
        ("rushLaneCount", "laneCount", "rushAttemptsPerRound", "attemptsPerRound"),
    )


def _rush_lane_kwargs_from_payload(payload: dict) -> dict:
    kw: dict = {}
    shot_index = _parse_rush_shot_index_from_payload(payload)
    lane_count = _parse_rush_lane_count_from_payload(payload)
    lane_parallel = _rush_lane_parallel_from_payload(payload)
    if shot_index is not None:
        kw["rush_shot_index"] = shot_index
    if lane_parallel:
        kw["rush_lane_parallel"] = True
        if lane_count is None:
            lane_count = _rush_default_lane_count()
    if lane_count is not None:
        kw["rush_lane_count"] = lane_count
    return kw


def _parse_rush_loop_delay_ms_from_payload(payload: dict) -> int:
    """与 WebUI「间隔(ms)」一致：rushLoopDelayMs / delayMs / rushDelayMs，0～300000。"""
    for key in ("rushLoopDelayMs", "delayMs", "rushDelayMs"):
        raw = payload.get(key)
        if raw is None or raw == "":
            continue
        try:
            return max(0, min(300_000, int(raw)))
        except (TypeError, ValueError):
            continue
    return 0


def _rush_loop_burst_in_node_from_payload(payload: dict) -> bool:
    """
    WebUI「MT模式」：单次 Session.rush 内整轮走 Node /rush_loop（枪间间隔在 Node 内 sleep），
    避免间隔>0 时拆成多次 Python↔Node。    """
    for key in ("rushLoopBurstInNode", "mtRushMode", "rushMtMode"):
        raw = payload.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(int(raw))
        s = str(raw).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
    return False


def _rush_lane_parallel_from_payload(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in ("rushLaneParallel", "laneParallel", "mtLaneParallel"):
        raw = payload.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(int(raw))
        s = str(raw).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
    return _rush_loop_burst_in_node_from_payload(payload) and not _fire_and_forget_from_payload(payload)


def _parse_rush_rounds_from_payload(payload: dict) -> int | None:
    """⚡高频多轮：每轮枪数=重试次数，总枪数=重试×轮次。未传则由 Session.rush 默认 1。"""
    for key in ("rushRounds", "rushRoundCount", "rounds"):
        raw = payload.get(key)
        if raw is None or raw == "":
            continue
        try:
            return max(1, min(100, int(raw)))
        except (TypeError, ValueError):
            continue
    return None


def _parse_rush_round_gap_ms_from_payload(payload: dict) -> int | None:
    for key in ("rushRoundGapMs", "rush_round_gap_ms", "roundGapMs"):
        raw = payload.get(key)
        if raw is None or raw == "":
            continue
        try:
            return max(0, min(300_000, int(raw)))
        except (TypeError, ValueError):
            continue
    return None


def _parse_rush_round_gap_jitter_ms_from_payload(payload: dict) -> int | None:
    for key in ("rushRoundGapJitterMs", "rush_round_gap_jitter_ms", "roundGapJitterMs"):
        raw = payload.get(key)
        if raw is None or raw == "":
            continue
        try:
            return max(0, min(60_000, int(raw)))
        except (TypeError, ValueError):
            continue
    return None


def _fire_and_forget_from_payload(payload: dict) -> bool:
    """WebUI「高频」模式：不等响应连发所有枪，对应 Node /rush_loop fireAndForget=true。

    兼容不同入口/历史前端的字段命名，避免「勾了高频但字段名不一致」导致回落串行。
    """

    def _truthy(v) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(int(v))
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    # 主字段（新版）
    if "fireAndForget" in payload and payload.get("fireAndForget") is not None:
        return _truthy(payload.get("fireAndForget"))

    # 兼容字段（旧版/不同页面）
    alias_keys = (
        "rushFireAndForget",
        "rushHighFreq",
        "highFreq",
        "codeHighFreq",
        "rushFastLoop",
        "rushfast",
    )
    for k in alias_keys:
        if k in payload and payload.get(k) is not None:
            return _truthy(payload.get(k))

    # 兼容「模式枚举」写法
    mode_keys = ("rushMode", "mode", "rushStrategy")
    for mk in mode_keys:
        mv = payload.get(mk)
        if mv is None or mv == "":
            continue
        s = str(mv).strip().lower()
        if s in ("highfreq", "high_freq", "fire_and_forget", "faf"):
            return True
        if s in ("normal", "serial"):
            return False
    return False


def _batch_mt_realtime_from_payload(payload: dict) -> bool:
    """Batch MT uses per-shot build/send; high frequency keeps the existing prebuilt burst path."""
    if not isinstance(payload, dict):
        return False
    if _fire_and_forget_from_payload(payload):
        return False
    return _json_truthy(payload.get("mtMode"))


def _batch_has_mt_realtime(tasks: list[dict]) -> bool:
    return any(isinstance(t, dict) and _batch_mt_realtime_from_payload(t) for t in tasks)


def _batch_all_mt_realtime(tasks: list[dict]) -> bool:
    return bool(tasks) and all(isinstance(t, dict) and _batch_mt_realtime_from_payload(t) for t in tasks)


def _batch_account_stagger_ms() -> int:
    """错峰启动各账号，减轻同刻打满代理/CDN。与 Go 侧 MT_HF_ACCOUNT_STAGGER_MS 共用。"""
    raw = (
        os.environ.get("MT_HF_ACCOUNT_STAGGER_MS")
        or os.environ.get("MT_ACCOUNT_STAGGER_MS")
        or "80"
    ).strip().lower()
    if raw in ("0", "off", "false", "no"):
        return 0
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 80
    return max(0, min(5000, v))


def _batch_rush_one_mt_realtime_staggered(payload: dict, emit=None, task_index: int | None = None) -> dict:
    stagger = _batch_account_stagger_ms()
    if stagger > 0 and task_index is not None and int(task_index) > 0:
        time.sleep(int(task_index) * stagger / 1000.0)
    return _batch_rush_one_mt_realtime(payload, emit=emit, task_index=task_index)


def _batch_all_fire_and_forget(tasks: list[dict]) -> bool:
    return bool(tasks) and all(isinstance(t, dict) and _fire_and_forget_from_payload(t) for t in tasks)


def _realtime_shot_from_payload(payload: dict) -> bool:
    """高频逐枪实时模式：每账号现场组当前枪，组完立即 POST /rush_shot。"""
    if not isinstance(payload, dict):
        return False
    for key in ("realtimeShot", "rushRealtimeShot", "hfRealtimeShot", "shotRealtime"):
        if key in payload and payload.get(key) is not None:
            return _json_truthy(payload.get(key))
    raw = (os.environ.get("MT_HF_REALTIME_SHOT") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return False


def _batch_all_realtime_shot(tasks: list[dict]) -> bool:
    return bool(tasks) and all(
        isinstance(t, dict) and _fire_and_forget_from_payload(t) and _realtime_shot_from_payload(t)
        for t in tasks
    )


def _batch_has_mobile_relay_account(tasks: list[dict]) -> bool:
    for t in tasks:
        if not isinstance(t, dict):
            continue
        e = POOL.get(_norm_phone(t.get("phone")))
        if e is not None and bool(getattr(e, "use_mobile_relay", False)):
            return True
    return False


def _batch_rush_use_session_roundtrip_instead(tasks: list[dict]) -> bool:
    """
    是否在批量抢购里回退到 Session.rush（每枪 Python↔Node 往返）。
    默认关闭回退：即使间隔(ms)>0 也走 Node /batch，由 Node 侧负责枪间节奏。如需旧行为（任一号间隔>0 即回退），可设 MT_BATCH_RUSH_DELAY_ROUNDTRIP=1。    """
    if _batch_has_mobile_relay_account(tasks):
        return True
    sw = (os.environ.get("MT_BATCH_RUSH_DELAY_ROUNDTRIP") or "").strip().lower()
    if sw not in ("1", "true", "yes", "on"):
        return False
    for t in tasks:
        if isinstance(t, dict) and _parse_rush_loop_delay_ms_from_payload(t) > 0:
            return True
    return False


def _prewarm_http_method() -> str:
    """默认 POST 空 JSON（与抢购动词、_probe_current_proxy 一致）；HEAD/GET 可省上行体。"""
    m = (os.environ.get("MT_PREWARM_HTTP_METHOD") or "POST").strip().upper()
    return m if m in ("GET", "HEAD", "POST") else "POST"


def _prewarm_target_timeout_ms() -> int:
    """单条预热超时（毫秒），默认 8000；可调 MT_PREWARM_TIMEOUT_MS（2000～120000）。"""
    raw = (os.environ.get("MT_PREWARM_TIMEOUT_MS") or "").strip()
    try:
        v = int(raw) if raw else 8000
    except (TypeError, ValueError):
        v = 8000
    return max(2000, min(120000, v))


def _agent_dbg_prewarm(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # #region agent log
    try:
        lp = Path(__file__).resolve().parent / "debug-eb180b.log"
        lp.open("a", encoding="utf-8").write(
            json.dumps(
                {
                    "sessionId": "eb180b",
                    "timestamp": int(time.time() * 1000),
                    "location": location,
                    "message": message,
                    "hypothesisId": hypothesis_id,
                    "data": data,
                    "runId": "prewarm-debug",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    except Exception:
        pass
    # #endregion


def _build_prewarm_targets_for_account(phone: str, payload: dict) -> dict:
    """按与抢购相同的 proxy 序列生成 N 条 target（N=重试次数），再随机打乱后送 Node /prewarm。
    条数与出口集合仍与 _rush_proxy_url_sequence 一致；但**连接顺序**随机。
    预热 URL 仅为 **H5 根路径**（如 https://h5.moutai519.com.cn/），不请求抢购 xhr 接口，避免多余业务路径。
    说明：Node 侧每桩预热与每枪抢购均经 curl_cffi_worker **子进程单次请求**，进程结束即释放连接；**预热成功≠与后续抢购共用 TCP 长连**，对抢购首包时延通常只有边际帮助（验证出口/DNS 等）。不会抵消你在 WebUI 里设的「间隔(ms)」或抖动。    """
    item = str(payload.get("itemCode", "")).strip()
    branch = str(payload.get("branch", "")).strip().lower()
    if not item:
        return {"ok": False, "error": "商品 ID（itemCode）不能为空"}
    e = POOL.get(phone)
    if not e:
        return {"ok": False, "error": "账号不存在"}
    if e.status != STATUS_OK:
        return {"ok": False, "error": "账号未登录"}
    s = e.ensure_session()
    rla = _parse_rush_loop_attempts_from_payload(payload)
    attempts_n = s._rush_max_attempts()
    if rla is not None:
        attempts_n = rla
    attempts_n = s._rush_apply_full_pool_max_try(attempts_n)
    proxy_seq = s._rush_proxy_url_sequence(attempts_n, advance=False)
    if len(proxy_seq) < attempts_n and proxy_seq:
        proxy_seq = proxy_seq + [proxy_seq[-1]] * (attempts_n - len(proxy_seq))
    attempts_n_full = len(proxy_seq)
    h5_raw = str(H5_HOST or "").strip()
    h5_norm = h5_raw if h5_raw.startswith(("http://", "https://")) else f"https://{h5_raw}"
    warm_url = f"{h5_norm.rstrip('/')}/"
    pw_method = _prewarm_http_method()
    pw_tmo = _prewarm_target_timeout_ms()
    targets: list[dict] = []
    for pu in proxy_seq:
        proxy_url = str(pu or "").strip()
        targets.append(
            {
                "method": pw_method,
                "url": warm_url,
                "proxyUrl": proxy_url,
                "timeoutMs": pw_tmo,
            }
        )
    if not targets:
        return {"ok": False, "error": "未生成可预热的目标连接"}
    random.shuffle(targets)
    return {
        "ok": True,
        "targets": targets,
        "warm_url": warm_url,
        "rush_url": warm_url,
        "branch": branch,
        "attemptsN": attempts_n_full,
    }


def _rush_prepare_bs_dvid(phone: str) -> tuple[bool, str | None, object | None]:
    """
    抢购前 BS-DVID：若已设 MT_RUSH_BLOCK_WITHOUT_BS，则禁止在抢购阶段自动补浩天（保持旧行为）；否则已登录但缺 bsid 时自动跑一次 ensure_bs_dvid_for_rush。返回 (可继续, HTTP 错误文案, detail)；可继续时后两项为 None。    """
    e = POOL.get(phone)
    if not e:
        return False, "账号不存在", None
    block = os.environ.get("MT_RUSH_BLOCK_WITHOUT_BS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    has_bs = bool((e.bsid or "").strip())
    if not has_bs and block:
        return (
            False,
            (
                "未配置 BS-DVID：已开启 MT_RUSH_BLOCK_WITHOUT_BS，不会在抢购阶段自动补浩天。"
                "请重新走「发码 -> 登录」或关闭该环境变量后重试。"
            ),
            None,
        )
    if has_bs:
        return True, None, None
    st = POOL.ensure_bs_dvid_for_rush(phone)
    if st.get("ok"):
        if not st.get("skipped"):
            print(f"[抢购] 已自动补全 BS-DVID（浩天）{phone}", flush=True)
        return True, None, None
    return False, str(st.get("message") or "浩天补全 BS-DVID 失败"), st.get("detail")


def _batch_rush_prepare_one(payload: dict) -> dict:
    """单条组 Node 批量抢购任务（rushLoop + attempts）。成功返回 ok + node_task + ctx；失败返回 ok=False。"""
    phone = str(payload.get("phone", "")).strip()
    item = str(payload.get("itemCode", "")).strip()
    act_id = str(payload.get("actId", "")).strip()
    spu_imtp = str(payload.get("spuId", "")).strip()
    hot = payload.get("hot", True)
    referer = str(payload.get("referer", "")).strip()
    amount = str(payload.get("amount", "1")).strip() or "1"
    source = str(payload.get("sourceId", "")).strip()
    branch = str(payload.get("branch", "")).strip().lower()
    need_code = bool(payload.get("needCode", False))
    if not phone:
        return {"ok": False, "phone": phone, "error": "phone 不能为空"}
    if not item:
        return {"ok": False, "phone": phone, "error": "商品 ID（itemCode）不能为空"}
    e = POOL.get(phone)
    if not e:
        return {"ok": False, "phone": phone, "error": "账号不存在"}
    if e.status != STATUS_OK:
        return {"ok": False, "phone": phone, "error": "账号未登录"}
    cont, err_bs, det_bs = _rush_prepare_bs_dvid(phone)
    if not cont:
        return {"ok": False, "phone": phone, "error": err_bs or "BS-DVID 不可用", "detail": det_bs}
    try:
        s = e.ensure_session()
        # 与 Session 建号时一致：仅当账号勾选「全池顺序」时按 active_urls 整池轮询；否则走分片/默认池。
        s._rush_full_pool_sequence = bool(getattr(e, "full_pool_rush", False))
        prep = _rush_prepare_act(s, item, act_id, spu_imtp, referer, bool(hot))
        if not prep["ok"]:
            return {
                "ok": False,
                "phone": phone,
                "error": prep["error"],
                "detail": prep.get("detail"),
            }
        act_id = prep["act_id"]
        spu_imtp = prep["spu_imtp"]
        referer = prep["referer"]
        route_info = prep.get("route_info")
        pinfo = prep.get("pinfo")
        if route_info:
            if not branch:
                branch = str(route_info.get("branch", "") or "").strip().lower()
            if route_info.get("needCodeHint"):
                need_code = bool(need_code or route_info.get("needCodeHint"))
        try:
            s._rush_guard_auth_proxy_for_single_hop()
        except RuntimeError as re:
            return {
                "ok": False,
                "phone": phone,
                "error": str(re),
            }
        rla = _parse_rush_loop_attempts_from_payload(payload)
        attempts_n = s._rush_max_attempts()
        if rla is not None:
            attempts_n = rla
        attempts_n = s._rush_apply_full_pool_max_try(attempts_n)
        faf_b = _fire_and_forget_from_payload(payload)
        lane_parallel_b = _rush_lane_parallel_from_payload(payload)
        lane_count_b = _parse_rush_lane_count_from_payload(payload)
        if lane_count_b is None:
            lane_count_b = _rush_default_lane_count() if lane_parallel_b else attempts_n
        rr_b = _parse_rush_rounds_from_payload(payload)
        rounds_b = max(1, min(100, int(rr_b))) if rr_b is not None else 1
        build_n = attempts_n
        if rounds_b > 1:
            build_n = min(500, max(1, attempts_n * rounds_b))
        attempts = s._rush_build_node_attempt_packages(
            build_n,
            item,
            act_id,
            amount,
            source,
            branch,
            referer,
            need_code,
            lane_count=lane_count_b,
        )
        node_task: dict = {
            "rushLoop": True,
            "attempts": attempts,
            "rushLoopDelayMs": _parse_rush_loop_delay_ms_from_payload(payload),
            "tlsImpersonate": TLS_PROFILE,
            "accountKey": phone,
            # WebUI「高频」模式：透传给 Node /rush_loop，启用并发连发（fire-and-forget）
            "fireAndForget": faf_b,
        }
        if lane_parallel_b and not faf_b:
            node_task["laneParallel"] = True
        proxy_pool_lines = s._rush_runtime_proxy_pool()
        if proxy_pool_lines:
            node_task["runtimeProxyPool"] = True
            node_task["proxyPool"] = proxy_pool_lines
        if rounds_b > 1:
            node_task["rushRounds"] = rounds_b
        _rg = _parse_rush_round_gap_ms_from_payload(payload)
        if _rg is not None:
            node_task["rushRoundGapMs"] = _rg
        elif rounds_b > 1:
            node_task["rushRoundGapMs"] = 1000
        _rj = _parse_rush_round_gap_jitter_ms_from_payload(payload)
        if _rj is not None:
            node_task["rushRoundGapJitterMs"] = _rj
        elif rounds_b > 1:
            node_task["rushRoundGapJitterMs"] = 100
        ctx = {
            "item": item,
            "act_id": act_id,
            "spu_imtp": spu_imtp,
            "referer": referer,
            "amount": amount,
            "source": source,
            "branch": branch,
            "need_code": need_code,
            "hot": hot,
            "route_info": route_info,
            "pinfo": pinfo,
            "prep": prep,
        }
        return {
            "ok": True,
            "phone": phone,
            "e": e,
            "s": s,
            "node_task": node_task,
            "payload": dict(payload),
            "ctx": ctx,
        }
    except Exception as ex:
        traceback.print_exc()
        return {"ok": False, "phone": phone, "error": str(ex).strip() or type(ex).__name__}


def _batch_rush_prepare_live_context(payload: dict) -> dict:
    """Prepare one account for live per-shot high-frequency mode without prebuilding attempts."""
    phone = str(payload.get("phone", "")).strip()
    item = str(payload.get("itemCode", "")).strip()
    act_id = str(payload.get("actId", "")).strip()
    act_id_from_payload = bool(act_id)
    spu_imtp = str(payload.get("spuId", "")).strip()
    hot = payload.get("hot", True)
    referer = str(payload.get("referer", "")).strip()
    amount = str(payload.get("amount", "1")).strip() or "1"
    source = str(payload.get("sourceId", "")).strip()
    branch = str(payload.get("branch", "")).strip().lower()
    need_code = bool(payload.get("needCode", False))
    prep_breakdown = {"actSource": "payload" if act_id_from_payload else "auto"}
    if not phone:
        return {"ok": False, "phone": phone, "error": "phone 不能为空"}
    if not item:
        return {"ok": False, "phone": phone, "error": "商品 ID（itemCode）不能为空"}
    step_t0 = time.perf_counter()
    e = POOL.get(phone)
    prep_breakdown["poolMs"] = int((time.perf_counter() - step_t0) * 1000)
    if not e:
        return {"ok": False, "phone": phone, "error": "账号不存在", "prepBreakdown": prep_breakdown}
    if e.status != STATUS_OK:
        return {"ok": False, "phone": phone, "error": "账号未登录", "prepBreakdown": prep_breakdown}
    step_t0 = time.perf_counter()
    cont, err_bs, det_bs = _rush_prepare_bs_dvid(phone)
    prep_breakdown["bsMs"] = int((time.perf_counter() - step_t0) * 1000)
    if not cont:
        return {"ok": False, "phone": phone, "error": err_bs or "BS-DVID 不可用", "detail": det_bs, "prepBreakdown": prep_breakdown}
    try:
        step_t0 = time.perf_counter()
        s = e.ensure_session()
        prep_breakdown["sessionMs"] = int((time.perf_counter() - step_t0) * 1000)
        s._rush_full_pool_sequence = bool(getattr(e, "full_pool_rush", False))
        step_t0 = time.perf_counter()
        prep = _rush_prepare_act(s, item, act_id, spu_imtp, referer, bool(hot))
        prep_breakdown["actMs"] = int((time.perf_counter() - step_t0) * 1000)
        if prep.get("pinfo"):
            prep_breakdown["actSource"] = "fetched"
        if not prep["ok"]:
            return {
                "ok": False,
                "phone": phone,
                "error": prep["error"],
                "detail": prep.get("detail"),
                "prepBreakdown": prep_breakdown,
            }
        act_id = prep["act_id"]
        spu_imtp = prep["spu_imtp"]
        referer = prep["referer"]
        route_info = prep.get("route_info")
        pinfo = prep.get("pinfo")
        if route_info:
            if not branch:
                branch = str(route_info.get("branch", "") or "").strip().lower()
            if route_info.get("needCodeHint"):
                need_code = bool(need_code or route_info.get("needCodeHint"))
        try:
            step_t0 = time.perf_counter()
            s._rush_guard_auth_proxy_for_single_hop()
            prep_breakdown["proxyMs"] = int((time.perf_counter() - step_t0) * 1000)
        except RuntimeError as re:
            prep_breakdown["proxyMs"] = int((time.perf_counter() - step_t0) * 1000)
            return {"ok": False, "phone": phone, "error": str(re), "prepBreakdown": prep_breakdown}
        ctx = {
            "item": item,
            "act_id": act_id,
            "spu_imtp": spu_imtp,
            "referer": referer,
            "amount": amount,
            "source": source,
            "branch": branch,
            "need_code": need_code,
            "hot": hot,
            "route_info": route_info,
            "pinfo": pinfo,
            "prep": prep,
            "prepBreakdown": prep_breakdown,
        }
        return {
            "ok": True,
            "phone": phone,
            "e": e,
            "s": s,
            "payload": dict(payload),
            "ctx": ctx,
            "prepBreakdown": prep_breakdown,
        }
    except Exception as ex:
        traceback.print_exc()
        return {"ok": False, "phone": phone, "error": str(ex).strip() or type(ex).__name__, "prepBreakdown": prep_breakdown}


def _batch_rush_one_blocking(payload: dict, shot_emit=None) -> dict:
    """与 /api/accounts/rush 相同链路（Session.rush），用于未启用 Node 批量时的并行线程。"""
    phone = str(payload.get("phone", "")).strip()
    item = str(payload.get("itemCode", "")).strip()
    act_id = str(payload.get("actId", "")).strip()
    spu_imtp = str(payload.get("spuId", "")).strip()
    hot = payload.get("hot", True)
    referer = str(payload.get("referer", "")).strip()
    amount = str(payload.get("amount", "1")).strip() or "1"
    source = str(payload.get("sourceId", "")).strip()
    branch = str(payload.get("branch", "")).strip().lower()
    need_code = bool(payload.get("needCode", False))
    try:
        if not phone:
            return {"ok": False, "phone": phone, "message": "phone 不能为空"}
        if not item:
            return {"ok": False, "phone": phone, "message": "商品 ID（itemCode）不能为空"}
        e = POOL.get(phone)
        if not e:
            return {"ok": False, "phone": phone, "message": "账号不存在"}
        if e.status != STATUS_OK:
            return {"ok": False, "phone": phone, "message": "账号未登录"}
        cont, err_bs, det_bs = _rush_prepare_bs_dvid(phone)
        if not cont:
            return {
                "ok": False,
                "phone": phone,
                "message": err_bs or "BS-DVID 不可用",
                "detail": det_bs,
            }
        s = e.ensure_session()
        s._rush_full_pool_sequence = bool(getattr(e, "full_pool_rush", False))
        prep = _rush_prepare_act(s, item, act_id, spu_imtp, referer, bool(hot))
        if not prep["ok"]:
            return {
                "ok": False,
                "phone": phone,
                "message": prep["error"],
                "detail": prep.get("detail"),
            }
        act_id = prep["act_id"]
        spu_imtp = prep["spu_imtp"]
        referer = prep["referer"]
        pinfo = prep.get("pinfo")
        route_info = prep.get("route_info")
        if route_info:
            if not branch:
                branch = str(route_info.get("branch", "") or "").strip().lower()
            if route_info.get("needCodeHint"):
                need_code = bool(need_code or route_info.get("needCodeHint"))
        rla = _parse_rush_loop_attempts_from_payload(payload)
        kw = {}
        if rla is not None:
            kw["rush_loop_attempts"] = rla
        kw["rush_loop_delay_ms"] = _parse_rush_loop_delay_ms_from_payload(payload)
        if _rush_loop_burst_in_node_from_payload(payload):
            kw["rush_loop_burst_in_node"] = True
        if _fire_and_forget_from_payload(payload):
            kw["fire_and_forget"] = True
        _rr2 = _parse_rush_rounds_from_payload(payload)
        if _rr2 is not None:
            kw["rush_rounds"] = _rr2
        _rg2 = _parse_rush_round_gap_ms_from_payload(payload)
        if _rg2 is not None:
            kw["rush_round_gap_ms"] = _rg2
        _rj2 = _parse_rush_round_gap_jitter_ms_from_payload(payload)
        if _rj2 is not None:
            kw["rush_round_gap_jitter_ms"] = _rj2
        kw.update(_rush_lane_kwargs_from_payload(payload))
        res = s.rush(
            item=item,
            act_id=act_id,
            amount=amount,
            src=source,
            branch=branch,
            referer=referer,
            need_code=need_code,
            shot_callback=shot_emit,
            **kw,
        )
        rush_only = _distributed_rush_only_mode()
        if not rush_only:
            res = _handle_verify_if_needed(s, res, referer=referer)
        pay = _batch_rush_pay_payload(phone, e, s, payload, prep, res, rush_only=rush_only)
        return {"ok": True, "phone": phone, "pay": pay}
    except Exception as ex:
        traceback.print_exc()
        em = str(ex).strip() or type(ex).__name__
        msg = "抢购链路异常（多为网络超时、代理不可用或 TLS/连接失败，不是茅台返回的业务提示）"
        low = em.lower()
        if "407" in em or "proxy authentication" in low:
            msg = (
                "抢购失败：代理返回 HTTP 407（需要认证或白名单未命中）。"
                "当前代理地址没有携带 user:pass；如果用的是白名单代理，请核对本机出口 IP 是否已加入供应商白名单，"
                "以及这批 host:port 是否属于白名单端口；如果供应商给的是账密端口，才需要填写 user:pass@host:port。"
            )
        return {"ok": False, "phone": phone, "message": msg, "detail": em}


class _MtNoWaitBizOk(Exception):
    """抢到 code=2000，用异常中断 stream 读取，后续正常走组单链路。"""
    def __init__(self, last_row: dict):
        super().__init__("biz_ok")
        self.last_row = last_row


def _mt_no_wait_stream_handler(ev: dict, phone: str, trace: list, emit, task_index):
    """处理 go_bridge /batch_stream 的 ndjson 事件，实时 emit 给前端；收到 code=2000 则用 _MtNoWaitBizOk 中断 stream（立刻停枪走组单链路）。"""
    typ = str(ev.get("type") or "")
    if typ == "shot":
        row = ev.get("row") or ev
        row_dict = {
            "attempt": row.get("attempt"),
            "httpStatus": row.get("httpStatus"),
            "code": row.get("code"),
            "message": str(row.get("message") or "")[:400],
            "ms": row.get("ms"),
            "receivedAtMs": row.get("receivedAtMs") or int(time.time() * 1000),
            "proxy": row.get("proxy"),
        }
        prid = _extract_priority_record_id(row)
        if prid:
            row_dict["priorityRecordId"] = prid
        trace.append(row_dict)
        if emit is not None:
            emit({"type": "shot", "taskIndex": task_index, "phone": phone, "row": row_dict})
        # 抢到立刻中断 stream，跟等响应 MT 模式行为一致
        if _mt_business_ok(row_dict.get("code")):
            raise _MtNoWaitBizOk(row_dict)
    elif typ == "result":
        # result 事件：检查是否整体成功（batch_stream 最后汇总）
        res = ev.get("result") or {}
        last = res.get("last") if isinstance(res, dict) else None
        if isinstance(last, dict) and _mt_business_ok(last.get("code")):
            raise _MtNoWaitBizOk(last)


def _rush_result_from_mt_no_wait_row(row: dict) -> dict:
    out = {
        "code": row.get("code", 2000),
        "message": str(row.get("message") or ""),
    }
    data = row.get("data")
    if isinstance(data, dict):
        out["data"] = dict(data)
    return _normalize_priority_record_id_payload(out, [row])


def _batch_rush_one_mt_realtime(payload: dict, emit=None, task_index: int | None = None) -> dict:
    """Batch MT path: build only the next shot, send it, then build the following shot."""
    phone = str(payload.get("phone", "")).strip()
    item = str(payload.get("itemCode", "")).strip()
    try:
        if not phone:
            return {"ok": False, "phone": phone, "message": "phone cannot be empty"}
        if not item:
            return {"ok": False, "phone": phone, "message": "itemCode cannot be empty"}
        e = POOL.get(phone)
        if not e:
            return {"ok": False, "phone": phone, "message": "account not found"}
        if e.status != STATUS_OK:
            return {"ok": False, "phone": phone, "message": "account not logged in"}
        return _run_one_mt_realtime_lanes(payload, emit=emit, task_index=task_index)
    except Exception as ex:
        traceback.print_exc()
        em = str(ex).strip() or type(ex).__name__
        msg = "rush pipeline exception; usually network timeout, proxy failure, TLS, or connection failure"
        low = em.lower()
        if "407" in em or "proxy authentication" in low:
            msg = "rush failed: proxy requires authentication (HTTP 407)"
        return {"ok": False, "phone": phone, "message": msg, "detail": em}


def _handle_verify_if_needed(s, rush_res: dict, referer: str = "") -> dict:
    """
    When rushPurchase returns userVerifyStatus=1, run Yidun OCR + /verify/code
    immediately, then cache the successful verification for the later compose step.
    """
    if not isinstance(rush_res, dict):
        return rush_res
    data = rush_res.get("data")
    if not isinstance(data, dict):
        return rush_res
    if data.get("userVerifyStatus") != 1:
        return rush_res
    prid = data.get("priorityRecordId")
    if not prid:
        return rush_res
    try:
        prid = int(prid)
    except (TypeError, ValueError):
        return rush_res

    verify_ref = (referer or "").strip() or str(rush_res.get("_rushReferer") or "").strip()
    print(f"[验证] rushPurchase 触发滑块验证 (priorityRecordId={prid})", flush=True)
    s.force_order_chain_direct()
    verify_res = s.verify_captcha(priority_record_id=prid, referer=verify_ref)
    rush_res = dict(rush_res)
    rush_res["verifyResult"] = verify_res

    verify_ok = _verify_code_business_ok(verify_res)
    verify_msg = (
        "易盾滑块验证通过"
        if verify_ok
        else f"易盾滑块验证失败: {(verify_res or {}).get('message') or '未知'}"
    )
    verify_trace_row = {
        "attempt": 0,
        "httpStatus": 200 if verify_ok else 0,
        "code": verify_res.get("code") if isinstance(verify_res, dict) else -1,
        "message": verify_msg,
        "ms": 0,
    }
    existing_trace = rush_res.get("rushLoopTrace")
    if isinstance(existing_trace, list):
        rush_res["rushLoopTrace"] = list(existing_trace) + [verify_trace_row]
    else:
        rush_res["rushLoopTrace"] = [verify_trace_row]

    if verify_ok:
        rush_res["data"] = dict(data)
        rush_res["data"]["userVerifyStatus"] = 2
        _verify_cache_set(prid, verify_res)
        print(f"[验证] 验证通过，已缓存 priorityRecordId={prid}", flush=True)
    else:
        msg = _verify_code_result_message(verify_res)
        print(f"[验证] 验证未通过: {msg}", flush=True)
    return rush_res


def _batch_rush_finalize_verify_pay_payload(p: dict, nr: object) -> tuple[bool, dict | None, str | None]:
    res = _batch_rush_finalize_from_node(p["s"], nr)
    ctx = p["ctx"]
    prep = ctx["prep"]
    rush_only = _distributed_rush_only_mode()
    if not rush_only:
        res = _handle_verify_if_needed(p["s"], res, referer=str(prep.get("referer") or ""))
    pay = _batch_rush_pay_payload(
        p["phone"], p["e"], p["s"], p["payload"], prep, res, rush_only=rush_only,
    )
    biz_ok = _mt_business_ok(res.get("code") if isinstance(res, dict) else None)
    msg = res.get("message") if not biz_ok and isinstance(res, dict) else None
    return biz_ok, pay, msg


def _run_batch_rush_api(tasks: list[dict], concurrency: int) -> list[dict]:
    """按序返回与 tasks 等长的 {phone, ok, pay?, message?, detail?}。"""
    n = len(tasks)
    out: list[dict | None] = [None] * n
    if not tasks:
        return []
    has_mobile_relay = _batch_has_mobile_relay_account(tasks)
    highfreq_realtime = (not has_mobile_relay) and _batch_all_fire_and_forget(tasks)
    mt_realtime = (not has_mobile_relay) and not highfreq_realtime
    if not highfreq_realtime and not mt_realtime:
        _batch_rush_prefill_shared_act_id(tasks)
    use_fat_node_batch = (
        _use_node_batch_rush()
        and not _batch_rush_use_session_roundtrip_instead(tasks)
        and not mt_realtime
        and not highfreq_realtime
    )
    if use_fat_node_batch:
        preps: list[dict] = [None] * n  # type: ignore
        max_workers = min(32, max(4, n))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {
                ex.submit(_batch_rush_prepare_one, tasks[i]): i for i in range(n)
            }
            for fu in concurrent.futures.as_completed(futs):
                i = futs[fu]
                try:
                    preps[i] = fu.result()
                except Exception as ex2:
                    preps[i] = {
                        "ok": False,
                        "phone": str(tasks[i].get("phone", "")).strip(),
                        "error": str(ex2).strip() or type(ex2).__name__,
                    }
        node_tasks: list[dict] = []
        node_indices: list[int] = []
        for i, p in enumerate(preps):
            if not p.get("ok"):
                err = p.get("error") or "准备失败"
                out[i] = {
                    "phone": p.get("phone") or str(tasks[i].get("phone", "")).strip(),
                    "ok": False,
                    "message": err,
                    "detail": p.get("detail"),
                }
            else:
                node_tasks.append(p["node_task"])
                node_indices.append(i)
        if node_tasks:
            try:
                cap = _batch_rush_parallel_cap()
                eff = min(cap, max(len(node_tasks), max(1, int(concurrency))))
                print(
                    f"[batch_rush] Node /batch 并行 worker={eff}（任务数={len(node_tasks)}，"
                    f"间隔=0 时多枪在单次 rush_loop 内连发；首枪全号同发至上限 {cap}）",
                    flush=True,
                )
                ot = estimate_node_batch_outer_timeout_sec(node_tasks)
                print(
                    f"[batch_rush] Node /batch HTTP 等待超时 {ot:.0f}s（多档 rush_loop 自动估算，"
                    f"可设 MT_NODE_BATCH_OUTER_TIMEOUT 覆盖）",
                    flush=True,
                )
                node_results = go_bridge_batch_http_dispatch(
                    node_tasks,
                    concurrency=eff,
                    outer_timeout=ot,
                )
            except Exception as ex3:
                traceback.print_exc()
                em = str(ex3).strip() or type(ex3).__name__
                hint = ""
                if "timed out" in em.lower() or "time out" in em.lower():
                    hint = (
                        "（若为多档 rush_loop：已自动拉长 /batch 等待；仍超时请设环境变量 "
                        "MT_NODE_BATCH_OUTER_TIMEOUT=秒数 或提高 MT_NODE_BATCH_OUTER_TIMEOUT_MAX）"
                    )
                for i in node_indices:
                    p = preps[i]
                    out[i] = {
                        "phone": p["phone"],
                        "ok": False,
                        "message": "Node 批量发包失败: " + em + hint,
                        "detail": em,
                    }
                for k in range(n):
                    if out[k] is None:
                        out[k] = {
                            "phone": str(tasks[k].get("phone", "")).strip(),
                            "ok": False,
                            "message": "内部状态异常",
                        }
                return out  # type: ignore
            for j, i in enumerate(node_indices):
                p = preps[i]
                nr = node_results[j]
                biz_ok, pay, msg = _batch_rush_finalize_verify_pay_payload(p, nr)
                out[i] = {
                    "phone": p["phone"],
                    "ok": biz_ok,
                    "pay": pay if biz_ok else None,
                    **({"message": msg} if not biz_ok and msg else {}),
                }
        for k in range(n):
            if out[k] is None:
                out[k] = {
                    "phone": str(tasks[k].get("phone", "")).strip(),
                    "ok": False,
                    "message": "内部状态异常",
                }
        return out  # type: ignore

    if mt_realtime:
        print(
            "[batch_rush] MT realtime mode: each account builds one shot after the previous response",
            flush=True,
        )
    elif has_mobile_relay:
        print(
            "[batch_rush] 本批含手机发包账号：改为 Session.rush 路径，电脑组包，手机只发 rushPurchase POST。",
            flush=True,
        )
    elif _use_node_batch_rush() and _batch_rush_use_session_roundtrip_instead(tasks):
        print(
            "[batch_rush] MT_BATCH_RUSH_DELAY_ROUNDTRIP=1 且本批含「间隔(ms)>0」："
            "改为每号 Session.rush（每枪 Python↔Node 往返）。默认可关闭该回退，让间隔由 Node /batch 处理。",
            flush=True,
        )

    cap = _batch_rush_parallel_cap()
    if mt_realtime:
        max_workers = min(cap, n)
    else:
        max_workers = min(cap, max(1, int(concurrency)), n)
    print(
        f"[batch_rush] Python ThreadPool max_workers={max_workers}（本批 {n} 号）",
        flush=True,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        if mt_realtime:
            stagger = _batch_account_stagger_ms()
            if stagger > 0:
                print(
                    f"[batch_rush] MT 账号错峰启动：taskIndex×{stagger}ms（MT_HF_ACCOUNT_STAGGER_MS）",
                    flush=True,
                )
            worker_fn = _batch_rush_one_mt_realtime_staggered
            futs = {ex.submit(worker_fn, tasks[i], None, i): i for i in range(n)}
        else:
            worker_fn = _batch_rush_one_blocking
            futs = {ex.submit(worker_fn, tasks[i]): i for i in range(n)}
        for fu in concurrent.futures.as_completed(futs):
            i = futs[fu]
            try:
                r = fu.result()
            except Exception as ex2:
                r = {
                    "ok": False,
                    "phone": str(tasks[i].get("phone", "")).strip(),
                    "message": str(ex2).strip() or type(ex2).__name__,
                }
            if r.get("ok"):
                pay = r.get("pay")
                if mt_realtime:
                    rush = pay.get("rush") if isinstance(pay, dict) else None
                    biz_ok = _mt_business_ok(rush.get("code") if isinstance(rush, dict) else None)
                    msg = rush.get("message") if not biz_ok and isinstance(rush, dict) else None
                    out[i] = {
                        "phone": r["phone"],
                        "ok": biz_ok,
                        "pay": pay if biz_ok else None,
                        **({"message": msg} if not biz_ok and msg else {}),
                    }
                else:
                    out[i] = {"phone": r["phone"], "ok": True, "pay": r["pay"]}
            else:
                out[i] = {
                    "phone": r.get("phone") or str(tasks[i].get("phone", "")).strip(),
                    "ok": False,
                    "message": r.get("message") or "失败",
                    "detail": r.get("detail"),
                }
    return out  # type: ignore


def _go_bridge_batch_stream_dispatch(
    tasks: list[dict],
    *,
    concurrency: int,
    outer_timeout: float | None,
    on_event,
) -> None:
    """POST go_bridge /batch_stream and call on_event for every NDJSON event."""
    if not tasks:
        return
    _ensure_go_bridge_running()
    if outer_timeout is None:
        outer_timeout = estimate_node_batch_outer_timeout_sec(tasks)
    else:
        try:
            outer_timeout = float(outer_timeout)
        except (TypeError, ValueError):
            outer_timeout = estimate_node_batch_outer_timeout_sec(tasks)
        outer_timeout = max(60.0, min(86400.0, outer_timeout))
    cap = _batch_rush_parallel_cap()
    try:
        conc = max(1, min(cap, int(concurrency)))
    except (TypeError, ValueError):
        conc = min(cap, 64)
    envelope = {
        "tasks": tasks,
        "concurrency": conc,
        "tlsImpersonate": TLS_PROFILE,
    }
    bridge_url = f"http://127.0.0.1:{_go_bridge_port()}/batch_stream"
    req = urllib.request.Request(
        bridge_url,
        data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=outer_timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    ev = {"type": "raw", "line": line[:1000]}
                on_event(ev)  # _MtNoWaitBizOk 等业务异常直接穿透；with 退出后 TCP 自动关闭，go_bridge stopped
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"go_bridge /batch_stream 不可用: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"go_bridge /batch_stream 不可用: {e}") from e


def _go_bridge_rush_shot_dispatch(
    attempt: dict,
    *,
    attempt_index: int,
    rush_round: int = 1,
    rush_rounds: int = 1,
    timeout: float = 180.0,
) -> dict:
    """POST one already-built rush attempt to go_bridge /rush_shot."""
    _ensure_go_bridge_running()
    envelope = {
        "attempt": attempt,
        "attemptIndex": max(1, int(attempt_index or 1)),
        "rushRound": max(1, int(rush_round or 1)),
        "rushRounds": max(1, int(rush_rounds or 1)),
        "tlsImpersonate": TLS_PROFILE,
    }
    bridge_url = f"http://127.0.0.1:{_go_bridge_port()}/rush_shot"
    req = urllib.request.Request(
        bridge_url,
        data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=max(5.0, float(timeout or 180.0))) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"go_bridge /rush_shot 不可用: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"go_bridge /rush_shot 不可用: {e}") from e
    try:
        j = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"go_bridge /rush_shot 非 JSON: {raw[:300]!r}") from e
    if not j.get("ok"):
        raise RuntimeError("go_bridge /rush_shot: " + str(j.get("error") or "?"))
    return j


def _go_bridge_rush_async_start(job_id: str, *, ensure_bridge: bool = True) -> str:
    if ensure_bridge:
        _ensure_go_bridge_running()
    jid = str(job_id or "").strip() or uuid.uuid4().hex
    bridge_url = f"http://127.0.0.1:{_go_bridge_port()}/rush_async_start"
    req = urllib.request.Request(
        bridge_url,
        data=json.dumps({"jobId": jid}, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"go_bridge /rush_async_start 不可用: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"go_bridge /rush_async_start 不可用: {e}") from e
    try:
        j = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"go_bridge /rush_async_start 非 JSON: {raw[:300]!r}") from e
    if not j.get("ok"):
        raise RuntimeError("go_bridge /rush_async_start: " + str(j.get("error") or "?"))
    return str(j.get("jobId") or jid)


def _go_bridge_rush_async_shot(
    job_id: str,
    account_key: str,
    attempt: dict,
    *,
    attempt_index: int,
    rush_round: int = 1,
    rush_rounds: int = 1,
    cancel_job_on_stop: bool = False,
    ensure_bridge: bool = True,
    timeout: float = 10.0,
    proxy_pool: list[str] | None = None,
) -> dict:
    if ensure_bridge:
        _ensure_go_bridge_running()
    envelope = {
        "jobId": str(job_id or ""),
        "accountKey": str(account_key or ""),
        "attempt": attempt,
        "attemptIndex": max(1, int(attempt_index or 1)),
        "rushRound": max(1, int(rush_round or 1)),
        "rushRounds": max(1, int(rush_rounds or 1)),
        "tlsImpersonate": TLS_PROFILE,
        "cancelJobOnStop": bool(cancel_job_on_stop),
    }
    proxy_pool_lines = [str(u or "").strip() for u in (proxy_pool or []) if str(u or "").strip()]
    if proxy_pool_lines:
        envelope["runtimeProxyPool"] = True
        envelope["proxyPool"] = proxy_pool_lines
    bridge_url = f"http://127.0.0.1:{_go_bridge_port()}/rush_async_shot"
    req = urllib.request.Request(
        bridge_url,
        data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=max(2.0, float(timeout or 10.0))) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"go_bridge /rush_async_shot 不可用: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"go_bridge /rush_async_shot 不可用: {e}") from e
    try:
        j = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"go_bridge /rush_async_shot 非 JSON: {raw[:300]!r}") from e
    if not j.get("ok"):
        raise RuntimeError("go_bridge /rush_async_shot: " + str(j.get("error") or "?"))
    return j


def _go_bridge_rush_async_stop(job_id: str, account_key: str = "", *, ensure_bridge: bool = True) -> dict:
    if ensure_bridge:
        _ensure_go_bridge_running()
    bridge_url = f"http://127.0.0.1:{_go_bridge_port()}/rush_async_stop"
    req = urllib.request.Request(
        bridge_url,
        data=json.dumps({"jobId": str(job_id or ""), "accountKey": str(account_key or "")}, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": False, "raw": raw[:300]}


def _go_bridge_rush_async_events(job_id: str, *, timeout: float = 7200.0, ensure_bridge: bool = True):
    if ensure_bridge:
        _ensure_go_bridge_running()
    qs = urlencode({"jobId": str(job_id or "")})
    bridge_url = f"http://127.0.0.1:{_go_bridge_port()}/rush_async_events?{qs}"
    req = urllib.request.Request(bridge_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=max(30.0, float(timeout or 7200.0))) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    yield {"type": "raw", "line": line[:1000]}
    except urllib.error.HTTPError as e:
        yield {"type": "error", "ok": False, "message": f"go_bridge /rush_async_events HTTP {e.code}"}
    except urllib.error.URLError as e:
        yield {"type": "error", "ok": False, "message": f"go_bridge /rush_async_events 不可用: {e}"}
    except (ConnectionResetError, BrokenPipeError, http.client.IncompleteRead, http.client.RemoteDisconnected):
        return


def _rush_result_from_one_shot_result(nr: dict) -> dict:
    if not isinstance(nr, dict):
        return {"code": -1, "message": "rush_shot 无返回"}
    row = nr.get("row") if isinstance(nr.get("row"), dict) else {}
    last = nr.get("last")
    if isinstance(last, dict):
        out = dict(last)
        tr = _normalize_rush_loop_trace_rows([row] if row else [])
        if tr:
            out["rushLoopTrace"] = tr
        return _normalize_priority_record_id_payload(out, tr)
    return {
        "code": row.get("code", -1),
        "message": str(row.get("message") or "rush_shot 无有效 last"),
        "rushLoopTrace": [row] if row else [],
    }


def _shot_row_from_rush_shot_result(nr: dict, fallback_attempt: int) -> dict:
    row = nr.get("row") if isinstance(nr, dict) and isinstance(nr.get("row"), dict) else {}
    out = {
        "attempt": row.get("attempt") or fallback_attempt,
        "httpStatus": row.get("httpStatus"),
        "code": row.get("code"),
        "message": str(row.get("message") or "")[:400],
        "ms": row.get("ms"),
        "receivedAtMs": row.get("receivedAtMs") or int(time.time() * 1000),
        "rushRound": row.get("rushRound"),
        "rushRounds": row.get("rushRounds"),
        "laneId": row.get("laneId"),
        "priorityRecordId": row.get("priorityRecordId"),
        "proxy": row.get("proxy"),
        "tlsProfile": row.get("tlsProfile"),
    }
    return {k: v for k, v in out.items() if v is not None and v != ""}


def _rush_shot_row_http_status(row: dict) -> int:
    if not isinstance(row, dict):
        return 0
    for key in ("httpStatus", "HTTPStatus", "status", "statusCode"):
        try:
            return int(row.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return 0


def _rush_shot_result_to_loop_result(nr: dict) -> dict:
    """Adapt /rush_shot JSON to the existing rushLoop finalizer shape."""
    return {
        "ok": bool(isinstance(nr, dict) and nr.get("ok")),
        "rushLoop": True,
        "last": nr.get("last") if isinstance(nr, dict) else None,
        "lastProxyUrl": nr.get("lastProxyUrl") if isinstance(nr, dict) else "",
        "trace": [nr.get("row")] if isinstance(nr, dict) and isinstance(nr.get("row"), dict) else [],
        "rushShot": True,
    }


def _mt_result_from_realtime_shot(nr: dict, trace: list[dict]) -> dict:
    row = _shot_row_from_rush_shot_result(nr, 0)
    last = nr.get("last") if isinstance(nr, dict) else None
    if isinstance(last, dict):
        out = dict(last)
    else:
        out = {
            "code": row.get("code", -1),
            "message": str(row.get("message") or "rush_shot 无有效 last"),
        }
    tr = _normalize_rush_loop_trace_rows(trace)
    if tr:
        out["rushLoopTrace"] = tr
    return _normalize_priority_record_id_payload(out, tr)


def _run_one_mt_realtime_lanes(payload: dict, emit=None, task_index: int | None = None) -> dict:
    """Batch MT: start Go first, then 5 lane workers build one shot at a time and wait for lane response."""
    ph_hint = str(payload.get("phone", "")).strip() if isinstance(payload, dict) else ""
    prep_t0 = time.perf_counter()
    try:
        p = _batch_rush_prepare_live_context(payload)
    except Exception as ex:
        traceback.print_exc()
        p = {"ok": False, "phone": ph_hint, "error": str(ex).strip() or type(ex).__name__}
    prep_ms = int((time.perf_counter() - prep_t0) * 1000)
    phone = str(p.get("phone") or ph_hint).strip()
    if not p.get("ok"):
        return {
            "ok": False,
            "phone": phone,
            "message": p.get("error") or "准备失败",
            "detail": p.get("detail"),
            "prepMs": prep_ms,
            "prepBreakdown": p.get("prepBreakdown"),
        }

    s = p["s"]
    ctx = p["ctx"]
    prep = ctx["prep"]
    prep_breakdown = p.get("prepBreakdown") or ctx.get("prepBreakdown")
    attempts_n = _parse_rush_loop_attempts_from_payload(payload)
    if attempts_n is None:
        attempts_n = s._rush_max_attempts()
    attempts_n = max(1, int(attempts_n))
    lane_count = _parse_rush_lane_count_from_payload(payload) or _rush_default_lane_count()
    lane_count = max(1, min(64, int(lane_count)))
    gap_ms = max(0, int(_parse_rush_loop_delay_ms_from_payload(payload) or 0))
    rush_only = _distributed_rush_only_mode()

    if emit is not None:
        emit({
            "type": "prep",
            "taskIndex": task_index,
            "phone": phone,
            "ok": True,
            "ms": prep_ms,
            "mode": "mt_realtime_lanes",
            "laneCount": lane_count,
            "totalShots": attempts_n,
            "prepBreakdown": prep_breakdown,
        })

    bridge_ready = bool(isinstance(payload, dict) and payload.get("_bridgeReady"))
    bridge_ms = 0
    if not bridge_ready:
        bridge_t0 = time.perf_counter()
        try:
            _ensure_go_bridge_running()
            bridge_ready = True
        except Exception as ex:
            return {
                "ok": False,
                "phone": phone,
                "message": "Go bridge 预热失败: " + (str(ex).strip() or type(ex).__name__),
                "prepMs": prep_ms,
                "prepBreakdown": prep_breakdown,
            }
        finally:
            bridge_ms = int((time.perf_counter() - bridge_t0) * 1000)
    job_id = f"mt-lane-{uuid.uuid4().hex}"
    account_key = phone or f"idx-{task_index}"
    stop_event = threading.Event()
    done_event = threading.Event()
    reader_done = threading.Event()
    state_lock = threading.Lock()
    first_go_event_seen = False
    accepted_event_seen = False
    trace: list[dict] = []
    seen_attempts: set[int] = set()
    submitted_payloads: list[tuple[int, dict]] = []
    lane_waiters: dict[int, queue.Queue] = {lane: queue.Queue(maxsize=1) for lane in range(lane_count)}
    submitted_count = 0
    received_count = 0
    finished_lanes = 0
    last_nr: dict | None = None
    best_nr: dict | None = None
    go_post_t0 = time.perf_counter()

    def _global_aborted() -> bool:
        return bool(_rush_global_abort_flag or _rush_global_abort_event.is_set())

    def _maybe_done_locked() -> None:
        if stop_event.is_set():
            done_event.set()
            return
        if finished_lanes >= lane_count and received_count >= submitted_count:
            done_event.set()

    def _emit_first_event(go_type: str) -> None:
        nonlocal first_go_event_seen
        should_emit = False
        with state_lock:
            if not first_go_event_seen:
                first_go_event_seen = True
                should_emit = True
        if should_emit and emit is not None:
            emit({
                "type": "go_first_event",
                "taskIndex": task_index,
                "phone": phone,
                "waitMs": int((time.perf_counter() - go_post_t0) * 1000),
                "goType": go_type,
                "mode": "mt_realtime_lanes",
            })

    def _emit_accepted_event(accepted: int, in_flight: int, submit_ms: int | None = None) -> None:
        nonlocal accepted_event_seen
        if emit is None:
            return
        should_emit = False
        with state_lock:
            if not accepted_event_seen:
                accepted_event_seen = True
                should_emit = True
        if not should_emit:
            return
        ev = {
            "type": "go_shot_accepted",
            "taskIndex": task_index,
            "phone": phone,
            "accepted": int(accepted or 0),
            "inFlight": int(in_flight or 0),
            "mode": "mt_realtime_lanes",
        }
        if submit_ms is not None:
            ev["submitMs"] = int(submit_ms)
        emit(ev)

    try:
        go_start_t0 = time.perf_counter()
        job_id = _go_bridge_rush_async_start(job_id, ensure_bridge=not bridge_ready)
        go_start_ms = int((time.perf_counter() - go_start_t0) * 1000)
        go_post_t0 = time.perf_counter()
    except Exception as ex:
        return {
            "ok": False,
            "phone": phone,
            "message": "Go async 启动失败: " + (str(ex).strip() or type(ex).__name__),
            "prepMs": prep_ms,
            "prepBreakdown": prep_breakdown,
        }

    if emit is not None:
        emit({
            "type": "go_account_start",
            "taskIndex": task_index,
            "phone": phone,
            "prepMs": prep_ms,
            "mode": "mt_realtime_lanes",
            "laneCount": lane_count,
            "bridgeMs": bridge_ms,
            "goStartMs": go_start_ms,
            "prepBreakdown": prep_breakdown,
        })

    def _signal_lane(lane_id: int, item: object) -> None:
        q = lane_waiters.get(lane_id)
        if q is None:
            return
        try:
            q.put_nowait(item)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(item)
            except queue.Full:
                pass

    def _submit_lane_attempt(attempt_no: int, lane_id: int) -> bool:
        nonlocal submitted_count
        counted = False
        try:
            attempt = s.rush_build_one_node_attempt(
                attempt_no,
                attempts_n,
                ctx["item"],
                ctx["act_id"],
                ctx["amount"],
                ctx["source"],
                ctx["branch"],
                ctx["referer"],
                ctx["need_code"],
                lane_count=lane_count,
            )
            attempt["laneId"] = lane_id
            with state_lock:
                submitted_count += 1
                counted = True
                submitted_payloads.append((attempt_no, dict(attempt or {})))
            shot_t0 = time.perf_counter()
            ack = _go_bridge_rush_async_shot(
                job_id,
                account_key,
                attempt,
                attempt_index=attempt_no,
                rush_round=1,
                rush_rounds=1,
                cancel_job_on_stop=False,
                ensure_bridge=False,
                timeout=10.0,
                proxy_pool=s._rush_runtime_proxy_pool(),
            )
            submit_ms = int((time.perf_counter() - shot_t0) * 1000)
            if isinstance(ack, dict):
                try:
                    accepted = int(ack.get("accepted") or 0)
                except (TypeError, ValueError):
                    accepted = 0
                try:
                    in_flight = int(ack.get("inFlight") or 0)
                except (TypeError, ValueError):
                    in_flight = 0
                _emit_accepted_event(accepted, in_flight, submit_ms)
            return True
        except Exception as ex:
            row = {
                "attempt": attempt_no,
                "code": -1,
                "message": ("mt lane submit error: " + (str(ex).strip() or type(ex).__name__))[:400],
                "laneId": lane_id,
                "receivedAtMs": int(time.time() * 1000),
            }
            _emit_first_event("submit_error")
            if emit is not None:
                emit({"type": "shot", "taskIndex": task_index, "phone": phone, "row": row})
            with state_lock:
                if counted:
                    submitted_count = max(0, submitted_count - 1)
                trace.append(row)
                _maybe_done_locked()
            return False

    def _process_shot_event(ev: dict) -> None:
        nonlocal last_nr, best_nr, received_count
        nr = ev.get("result") if isinstance(ev.get("result"), dict) else {}
        if not nr:
            nr = {
                "ok": bool(ev.get("ok", True)),
                "rushShot": True,
                "row": ev.get("row") if isinstance(ev.get("row"), dict) else {},
                "last": None,
                "lastProxyUrl": "",
            }
        row0 = ev.get("row") if isinstance(ev.get("row"), dict) else {}
        try:
            fallback = int(row0.get("attempt") or 0)
        except (TypeError, ValueError):
            fallback = 0
        row = _shot_row_from_rush_shot_result(nr, fallback)
        try:
            attempt_seen_key = int(row.get("attempt") or fallback or 0)
        except (TypeError, ValueError):
            attempt_seen_key = 0
        if attempt_seen_key:
            with state_lock:
                if attempt_seen_key in seen_attempts:
                    return
                seen_attempts.add(attempt_seen_key)
        if "laneId" not in row and row0.get("laneId") is not None:
            row["laneId"] = row0.get("laneId")
        lane_id = 0
        try:
            lane_id = int(row.get("laneId") or 0)
        except (TypeError, ValueError):
            lane_id = 0
        if lane_id < 0 or lane_id >= lane_count:
            lane_id = 0
        _emit_first_event("shot")
        if emit is not None:
            emit({"type": "shot", "taskIndex": task_index, "phone": phone, "row": row})
        with state_lock:
            last_nr = nr
            trace.append(row)
            received_count += 1
            if _mt_business_ok(row.get("code")) and best_nr is None:
                best_nr = nr
        _signal_lane(lane_id, nr)
        if _mt_business_ok(row.get("code")) or _rush_row_stop_rush(row):
            stop_event.set()
            _go_bridge_rush_async_stop(job_id, account_key, ensure_bridge=False)
            for lane in range(lane_count):
                if lane != lane_id:
                    _signal_lane(lane, None)
            with state_lock:
                _maybe_done_locked()

    def _process_accepted_event(ev: dict) -> None:
        if emit is None:
            return
        try:
            accepted = int(ev.get("accepted") or 0)
        except (TypeError, ValueError):
            accepted = 0
        try:
            in_flight = int(ev.get("inFlight") or 0)
        except (TypeError, ValueError):
            in_flight = 0
        _emit_accepted_event(accepted, in_flight)

    def _read_go_events() -> None:
        try:
            for ev in _go_bridge_rush_async_events(job_id, timeout=7200.0, ensure_bridge=False):
                if not isinstance(ev, dict):
                    continue
                typ = str(ev.get("type") or "")
                if typ in ("events_start", "start"):
                    continue
                if typ == "accepted":
                    _process_accepted_event(ev)
                    continue
                if typ == "shot":
                    _process_shot_event(ev)
                    continue
                if typ == "account_stop":
                    stop_event.set()
                    nr = ev.get("result") if isinstance(ev.get("result"), dict) else {}
                    row0 = ev.get("row") if isinstance(ev.get("row"), dict) else {}
                    if nr and row0:
                        _process_shot_event({"type": "shot", "result": nr, "row": row0})
                    break
                if typ == "error":
                    stop_event.set()
                    break
                if typ == "done":
                    break
        finally:
            reader_done.set()
            for lane in range(lane_count):
                _signal_lane(lane, None)

    def _lane_worker(lane_id: int, start_attempt: int | None = None, first_already_submitted: bool = False) -> None:
        nonlocal finished_lanes
        attempt_no = int(start_attempt or (lane_id + 1))
        already_submitted = bool(first_already_submitted)
        try:
            while attempt_no <= attempts_n:
                if stop_event.is_set() or _global_aborted():
                    break
                if not already_submitted and not _submit_lane_attempt(attempt_no, lane_id):
                    break
                already_submitted = False
                wait_sec = max(30.0, min(180.0, 30.0 + gap_ms / 1000.0))
                try:
                    nr = lane_waiters[lane_id].get(timeout=wait_sec)
                except queue.Empty:
                    stop_event.set()
                    break
                if nr is None:
                    break
                if stop_event.is_set() or _global_aborted():
                    break
                if attempt_no + lane_count <= attempts_n and gap_ms > 0:
                    if stop_event.wait(gap_ms / 1000.0):
                        break
                attempt_no += lane_count
        finally:
            with state_lock:
                finished_lanes += 1
                _maybe_done_locked()

    first_lane_started = False
    if attempts_n >= 1 and not stop_event.is_set() and not _global_aborted():
        first_lane_started = _submit_lane_attempt(1, 0)
    reader = threading.Thread(target=_read_go_events, name=f"mt-go-events-{task_index}", daemon=True)
    reader.start()
    lane_threads = [
        threading.Thread(
            target=_lane_worker,
            args=(lane, 1, True) if lane == 0 and first_lane_started else (
                (lane, attempts_n + lane_count + 1, False) if lane == 0 else (lane, None, False)
            ),
            name=f"mt-lane-{task_index}-{lane}",
            daemon=True,
        )
        for lane in range(lane_count)
    ]
    for th in lane_threads:
        th.start()
    for th in lane_threads:
        th.join()
    with state_lock:
        _maybe_done_locked()
    done_event.wait(max(30.0, min(7200.0, max(1, submitted_count) * 30.0)))
    _go_bridge_rush_async_stop(job_id, ensure_bridge=False)
    reader_done.wait(5.0)

    with state_lock:
        nr_final = best_nr or last_nr
        tr_final = list(trace)
        final_attempts = [att for _, att in sorted(submitted_payloads, key=lambda x: x[0])]
    if nr_final is None:
        return {"ok": False, "phone": phone, "message": "Go MT 实时逐枪无返回", "prepMs": prep_ms}
    last = _mt_result_from_realtime_shot(nr_final, tr_final)
    if not rush_only:
        last = _handle_verify_if_needed(s, last, referer=str(prep.get("referer") or ""))
    if isinstance(last, dict):
        try:
            s._rush_attach_proxy_meta(last, final_attempts)
        except Exception:
            pass
    pay = _batch_rush_pay_payload(phone, p["e"], s, p["payload"], prep, last, rush_only=rush_only)
    return {"ok": True, "phone": phone, "pay": pay}


def _run_one_highfreq_realtime_shot(task: dict, task_index: int, emit_hf) -> bool:
    """One account: build each high-frequency shot live, submit it to Go, and do not wait before building the next."""
    ph_hint = str(task.get("phone", "")).strip() if isinstance(task, dict) else ""
    prep_t0 = time.perf_counter()
    try:
        p = _batch_rush_prepare_live_context(task)
    except Exception as ex:
        traceback.print_exc()
        p = {"ok": False, "phone": ph_hint, "error": str(ex).strip() or type(ex).__name__}
    prep_ms = int((time.perf_counter() - prep_t0) * 1000)
    phone = str(p.get("phone") or ph_hint).strip()
    if not p.get("ok"):
        emit_hf({
            "type": "result",
            "taskIndex": task_index,
            "phone": phone,
            "ok": False,
            "message": p.get("error") or "准备失败",
            "detail": p.get("detail"),
            "prepMs": prep_ms,
            "prepBreakdown": p.get("prepBreakdown"),
        })
        return False

    s = p["s"]
    ctx = p["ctx"]
    prep_breakdown = p.get("prepBreakdown") or ctx.get("prepBreakdown")
    attempts_n = _parse_rush_loop_attempts_from_payload(task)
    if attempts_n is None:
        attempts_n = s._rush_max_attempts()
    attempts_n = s._rush_apply_full_pool_max_try(max(1, int(attempts_n)))
    rounds = _parse_rush_rounds_from_payload(task)
    rounds = max(1, min(100, int(rounds))) if rounds is not None else 1
    total_shots = max(1, min(500, attempts_n * rounds))
    gap_ms = max(0, int(_parse_rush_loop_delay_ms_from_payload(task) or 0))
    round_gap_ms = _parse_rush_round_gap_ms_from_payload(task)
    if round_gap_ms is None:
        round_gap_ms = 1000 if rounds > 1 else 0
    round_jitter_ms = _parse_rush_round_gap_jitter_ms_from_payload(task)
    if round_jitter_ms is None:
        round_jitter_ms = 100 if rounds > 1 else 0

    emit_hf({
        "type": "prep",
        "taskIndex": task_index,
        "phone": phone,
        "ok": True,
        "ms": prep_ms,
        "realtimeShot": True,
        "totalShots": total_shots,
        "prepBreakdown": prep_breakdown,
    })
    emit_hf({"type": "go_account_start", "taskIndex": task_index, "phone": phone, "prepMs": prep_ms, "realtimeShot": True, "prepBreakdown": prep_breakdown})

    _ensure_go_bridge_running()
    job_id = f"hf-shot-{uuid.uuid4().hex}"
    account_key = phone or f"idx-{task_index}"
    stop_event = threading.Event()
    done_event = threading.Event()
    reader_done = threading.Event()
    state_lock = threading.Lock()
    first_go_event_seen = False
    trace: list[dict] = []
    last_nr: dict | None = None
    best_nr: dict | None = None
    submitted_done = False
    submitted_count = 0
    received_count = 0
    submitted_payloads: list[tuple[int, dict]] = []
    go_post_t0 = time.perf_counter()

    def _global_aborted() -> bool:
        return bool(_rush_global_abort_flag or _rush_global_abort_event.is_set())

    try:
        job_id = _go_bridge_rush_async_start(job_id)
    except Exception as ex:
        emit_hf({
            "type": "result",
            "taskIndex": task_index,
            "phone": phone,
            "ok": False,
            "message": "Go async 启动失败: " + (str(ex).strip() or type(ex).__name__),
        })
        return False

    def _maybe_done_locked() -> None:
        if stop_event.is_set() or _global_aborted():
            done_event.set()
            return
        if submitted_done and submitted_count <= 0:
            done_event.set()
            return
        if submitted_done and received_count >= submitted_count:
            done_event.set()

    def _emit_first_event(go_type: str) -> None:
        nonlocal first_go_event_seen
        should_emit = False
        with state_lock:
            if not first_go_event_seen:
                first_go_event_seen = True
                should_emit = True
        if should_emit:
            emit_hf({
                "type": "go_first_event",
                "taskIndex": task_index,
                "phone": phone,
                "waitMs": int((time.perf_counter() - go_post_t0) * 1000),
                "goType": go_type,
                "realtimeShot": True,
            })

    def _submit_async_attempt(attempt_no: int, attempt: dict, round_no: int) -> bool:
        nonlocal submitted_count
        with state_lock:
            submitted_count += 1
            submitted_payloads.append((attempt_no, dict(attempt or {})))
        try:
            _go_bridge_rush_async_shot(
                job_id,
                account_key,
                attempt,
                attempt_index=attempt_no,
                rush_round=round_no,
                rush_rounds=rounds,
                cancel_job_on_stop=True,
                ensure_bridge=False,
                timeout=10.0,
                proxy_pool=s._rush_runtime_proxy_pool(),
            )
            return True
        except Exception as ex:
            with state_lock:
                submitted_count = max(0, submitted_count - 1)
                _maybe_done_locked()
            row = {
                "attempt": attempt_no,
                "code": -1,
                "message": ("go_async_shot submit error: " + (str(ex).strip() or type(ex).__name__))[:400],
                "rushRound": round_no,
                "rushRounds": rounds,
                "laneId": (attempt or {}).get("laneId") if isinstance(attempt, dict) else None,
                "receivedAtMs": int(time.time() * 1000),
            }
            _emit_first_event("submit_error")
            emit_hf({"type": "shot", "taskIndex": task_index, "phone": phone, "row": row, "realtimeShot": True})
            with state_lock:
                trace.append(row)
            return False

    def _process_async_shot(nr: dict, fallback_attempt: int) -> None:
        nonlocal last_nr, best_nr, received_count
        row = _shot_row_from_rush_shot_result(nr, fallback_attempt)
        if "laneId" not in row and row.get("attempt"):
            try:
                att_no = int(row.get("attempt") or 0)
            except (TypeError, ValueError):
                att_no = 0
            if att_no > 0:
                with state_lock:
                    for submitted_no, submitted_att in submitted_payloads:
                        if submitted_no == att_no and isinstance(submitted_att, dict) and submitted_att.get("laneId") is not None:
                            row["laneId"] = submitted_att.get("laneId")
                            break

        _emit_first_event("shot")
        emit_hf({"type": "shot", "taskIndex": task_index, "phone": phone, "row": row, "realtimeShot": True})

        should_stop = _mt_business_ok(row.get("code")) or _rush_row_stop_rush(row)

        with state_lock:
            last_nr = nr
            trace.append(row)
            received_count += 1
            if _mt_business_ok(row.get("code")) and best_nr is None:
                best_nr = nr

        if should_stop:
            stop_event.set()
            _go_bridge_rush_async_stop(job_id, ensure_bridge=False)

        with state_lock:
            _maybe_done_locked()

    def _process_async_error(message: str, attempt_no: int = 0) -> None:
        nonlocal received_count
        row = {
            "attempt": attempt_no,
            "code": -1,
            "message": ("go_async event error: " + str(message or ""))[:400],
            "receivedAtMs": int(time.time() * 1000),
        }
        _emit_first_event("shot_error")
        emit_hf({"type": "shot", "taskIndex": task_index, "phone": phone, "row": row, "realtimeShot": True})
        with state_lock:
            trace.append(row)
            if attempt_no:
                received_count += 1
            _maybe_done_locked()

    def _bridge_lost(message: str) -> None:
        stop_event.set()
        _process_async_error(message or "go_bridge async event stream closed")
        with state_lock:
            _maybe_done_locked()

    def _read_go_events() -> None:
        try:
            saw_done = False
            for ev in _go_bridge_rush_async_events(job_id, timeout=7200.0, ensure_bridge=False):
                if not isinstance(ev, dict):
                    continue
                typ = str(ev.get("type") or "")
                if typ in ("events_start", "start", "accepted"):
                    continue
                if typ == "shot":
                    nr = ev.get("result") if isinstance(ev.get("result"), dict) else {}
                    if not nr:
                        nr = {
                            "ok": bool(ev.get("ok", True)),
                            "rushShot": True,
                            "row": ev.get("row") if isinstance(ev.get("row"), dict) else {},
                            "last": None,
                            "lastProxyUrl": "",
                        }
                    row0 = ev.get("row") if isinstance(ev.get("row"), dict) else {}
                    try:
                        fallback = int(row0.get("attempt") or 0)
                    except (TypeError, ValueError):
                        fallback = 0
                    _process_async_shot(nr, fallback)
                    continue
                if typ == "account_stop":
                    stop_event.set()
                    if not done_event.is_set():
                        nr = ev.get("result") if isinstance(ev.get("result"), dict) else {}
                        row0 = ev.get("row") if isinstance(ev.get("row"), dict) else {}
                        if nr and not last_nr:
                            try:
                                fallback = int(row0.get("attempt") or 0)
                            except (TypeError, ValueError):
                                fallback = 0
                            _process_async_shot(nr, fallback)
                        with state_lock:
                            _maybe_done_locked()
                    continue
                if typ == "done":
                    saw_done = True
                    with state_lock:
                        _maybe_done_locked()
                    break
                if typ == "error":
                    _process_async_error(str(ev.get("message") or ev.get("error") or "unknown"))
                    stop_event.set()
                    break
            if not saw_done and not stop_event.is_set() and not _global_aborted():
                _bridge_lost("go_bridge async event stream closed before done")
        except (ConnectionResetError, BrokenPipeError, http.client.IncompleteRead, http.client.RemoteDisconnected):
            if not stop_event.is_set() and not _global_aborted():
                _bridge_lost("go_bridge async event stream reset")
        finally:
            reader_done.set()

    reader = threading.Thread(target=_read_go_events, name=f"hf-go-events-{task_index}", daemon=True)
    reader.start()

    def _round_gap_sleep() -> None:
        try:
            base = int(round_gap_ms or 0)
            jit = int(round_jitter_ms or 0)
        except (TypeError, ValueError):
            base, jit = 0, 0
        wait_ms = max(0, base)
        if jit > 0:
            wait_ms += random.randint(0, max(0, min(60000, jit)))
        if wait_ms > 0:
            stop_event.wait(wait_ms / 1000.0)

    try:
        for shot in range(1, total_shots + 1):
            if stop_event.is_set() or _global_aborted():
                break
            round_no = ((shot - 1) // attempts_n) + 1
            if shot > 1 and attempts_n > 0 and (shot - 1) % attempts_n == 0:
                _round_gap_sleep()
                if stop_event.is_set() or _global_aborted():
                    break
            attempt = s.rush_build_one_node_attempt(
                shot,
                total_shots,
                ctx["item"],
                ctx["act_id"],
                ctx["amount"],
                ctx["source"],
                ctx["branch"],
                ctx["referer"],
                ctx["need_code"],
                lane_count=attempts_n,
            )
            if not _submit_async_attempt(shot, attempt, round_no):
                stop_event.set()
                break
            if gap_ms > 0 and shot < total_shots:
                stop_event.wait(gap_ms / 1000.0)
        with state_lock:
            submitted_done = True
            _maybe_done_locked()
        wait_sec = max(30.0, min(7200.0, max(1, submitted_count) * 30.0))
        done_event.wait(wait_sec)
    except Exception as ex:
        traceback.print_exc()
        stop_event.set()
        _go_bridge_rush_async_stop(job_id, ensure_bridge=False)
        emit_hf({
            "type": "result",
            "taskIndex": task_index,
            "phone": phone,
            "ok": False,
            "message": "实时逐枪高频失败: " + (str(ex).strip() or type(ex).__name__),
        })
        return False
    finally:
        _go_bridge_rush_async_stop(job_id, ensure_bridge=False)
        reader_done.wait(5.0)

    with state_lock:
        nr_final = best_nr or last_nr
        tr_final = list(trace)
        final_attempts = [
            att for _, att in sorted(submitted_payloads, key=lambda x: x[0])
        ]
    if nr_final is None:
        emit_hf({
            "type": "result",
            "taskIndex": task_index,
            "phone": phone,
            "ok": False,
            "message": "Go 实时逐枪无返回",
        })
        return False
    loop_nr = _rush_shot_result_to_loop_result(nr_final)
    if tr_final:
        loop_nr["trace"] = tr_final
    try:
        biz_ok, pay, msg = _batch_rush_finalize_verify_pay_payload(p, loop_nr)
        rush = pay.get("rush") if isinstance(pay, dict) else None
        if isinstance(rush, dict):
            try:
                s._rush_attach_proxy_meta(rush, final_attempts)
            except Exception:
                pass
    except Exception as ex:
        traceback.print_exc()
        biz_ok, pay, msg = False, None, str(ex).strip() or type(ex).__name__
    emit_hf({
        "type": "result",
        "taskIndex": task_index,
        "phone": phone,
        "ok": bool(biz_ok),
        "pay": pay,
        "realtimeShot": True,
        **({"message": msg} if not biz_ok and msg else {}),
    })
    return bool(biz_ok)


def _run_batch_rush_stream_api(tasks: list[dict], concurrency: int, emit) -> None:
    """Stream batch rush progress: prepare in Python, dispatch via Go /batch_stream, finalize per account."""
    n = len(tasks)
    emit({"type": "start", "ok": True, "total": n})
    if not tasks:
        emit({"type": "done", "ok": True, "total": 0, "success": 0, "failed": 0})
        return
    # 进入抢购热路径：ensure_session 期间所有写盘改为标记 dirty，结束统一 flush。
    # 把 30+ 号串行写盘（每次 50-200ms）从抢购前置耗时里挪走。
    try:
        enter_rush_hot_path()
    except Exception:
        pass
    try:
        _run_batch_rush_stream_api_inner(tasks, concurrency, emit)
    finally:
        try:
            exit_rush_hot_path()  # 自动 flush 累计 dirty
        except Exception:
            pass


def _run_batch_rush_stream_api_inner(tasks: list[dict], concurrency: int, emit) -> None:
    n = len(tasks)
    has_mobile_relay = _batch_has_mobile_relay_account(tasks)
    highfreq_realtime = (not has_mobile_relay) and _batch_all_fire_and_forget(tasks)
    highfreq_shot_realtime = _batch_all_realtime_shot(tasks)
    mt_realtime = (not has_mobile_relay) and not highfreq_realtime
    if has_mobile_relay:
        highfreq_shot_realtime = False
    if not highfreq_realtime and not mt_realtime:
        _batch_rush_reuse_known_act_ids(tasks)
        _batch_rush_prefill_shared_act_id(tasks)
    if mt_realtime:
        emit_lock = threading.Lock()

        def emit_mt(ev: dict) -> None:
            with emit_lock:
                emit(ev)

        emit_mt({"type": "prep_start", "total": n, "mode": "mt_realtime"})
        reused_act = _batch_rush_reuse_known_act_ids(tasks)
        if reused_act:
            act_count = sum(
                1 for t in tasks if isinstance(t, dict) and str(t.get("actId") or "").strip()
            )
            emit_mt({
                "type": "prep_known_act",
                "total": n,
                "filled": reused_act,
                "actIdCount": act_count,
                "mode": "mt_realtime",
            })
        if _json_truthy(os.environ.get("MT_BATCH_MT_PREFILL_ACT")):
            prefill_t0 = time.perf_counter()
            prefill_act_before = sum(
                1 for t in tasks if isinstance(t, dict) and str(t.get("actId") or "").strip()
            )
            _batch_rush_prefill_shared_act_id(tasks)
            prefill_ms = int((time.perf_counter() - prefill_t0) * 1000)
            prefill_act_after = sum(
                1 for t in tasks if isinstance(t, dict) and str(t.get("actId") or "").strip()
            )
            filled = max(0, prefill_act_after - prefill_act_before)
            if filled or prefill_ms >= 300:
                emit_mt({
                    "type": "prep_shared_act",
                    "total": n,
                    "filled": filled,
                    "actIdCount": prefill_act_after,
                    "ms": prefill_ms,
                    "mode": "mt_realtime",
                })
        bridge_t0 = time.perf_counter()
        try:
            _ensure_go_bridge_running()
            bridge_ms = int((time.perf_counter() - bridge_t0) * 1000)
            for t in tasks:
                if isinstance(t, dict):
                    t["_bridgeReady"] = True
            emit_mt({
                "type": "go_bridge_ready",
                "total": n,
                "ms": bridge_ms,
                "mode": "mt_realtime",
            })
        except Exception as ex:
            bridge_ms = int((time.perf_counter() - bridge_t0) * 1000)
            emit_mt({
                "type": "error",
                "message": "Go bridge 预热失败: " + (str(ex).strip() or type(ex).__name__),
                "ms": bridge_ms,
                "mode": "mt_realtime",
            })
            emit_mt({"type": "done", "ok": False, "total": n, "success": 0, "failed": n})
            return
        cap = _batch_rush_parallel_cap()
        max_workers = min(cap, n)
        emit_mt({"type": "go_start", "total": n, "concurrency": max_workers, "mode": "mt_realtime"})
        success = 0
        failed = 0
        stagger = _batch_account_stagger_ms()
        if stagger > 0:
            print(
                f"[batch_rush] MT 账号错峰启动：taskIndex×{stagger}ms（MT_HF_ACCOUNT_STAGGER_MS）",
                flush=True,
            )
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {
                ex.submit(_batch_rush_one_mt_realtime_staggered, tasks[i], emit_mt, i): i
                for i in range(n)
            }
            for fu in concurrent.futures.as_completed(futs):
                i = futs[fu]
                try:
                    r = fu.result()
                except Exception as ex2:
                    r = {
                        "ok": False,
                        "phone": str(tasks[i].get("phone", "")).strip(),
                        "message": str(ex2).strip() or type(ex2).__name__,
                    }
                ok = False
                pay = None
                msg = None
                if r.get("ok"):
                    pay = r.get("pay")
                    rush = pay.get("rush") if isinstance(pay, dict) else None
                    ok = _mt_business_ok(rush.get("code") if isinstance(rush, dict) else None)
                    msg = rush.get("message") if not ok and isinstance(rush, dict) else None
                else:
                    msg = r.get("message") or "failed"
                success += 1 if ok else 0
                failed += 0 if ok else 1
                emit_mt({
                    "type": "result",
                    "taskIndex": i,
                    "phone": r.get("phone") or str(tasks[i].get("phone", "")).strip(),
                    "ok": bool(ok),
                    "pay": pay,
                    **({"message": msg} if not ok and msg else {}),
                })
        emit_mt({"type": "done", "ok": True, "total": n, "success": success, "failed": failed})
        return
    if highfreq_realtime:
        emit_lock = threading.Lock()

        def emit_hf(ev: dict) -> None:
            with emit_lock:
                emit(ev)

        hf_mode = "highfreq_shot_realtime" if highfreq_shot_realtime else "highfreq_realtime"
        emit_hf({"type": "prep_start", "total": n, "mode": hf_mode})
        cap = _batch_rush_parallel_cap()
        if highfreq_shot_realtime:
            # 高频逐枪实时模式：每账号内部逐枪现场组包，适合调试/特殊实时签名场景。
            max_workers = min(cap, n)
        else:
            # 默认高频要快：所有账号并发预组 attempts，交给 Go 本地高速连发，不受页面 concurrency 排队。
            max_workers = min(cap, n)
        emit_hf({"type": "go_start", "total": n, "concurrency": max_workers, "mode": hf_mode})
        counters = [0, 0]

        def add_counter(ok: bool) -> None:
            with emit_lock:
                counters[0] += 1 if ok else 0
                counters[1] += 0 if ok else 1

        if highfreq_shot_realtime:
            def _run_one_highfreq_shot(i: int) -> None:
                ok = _run_one_highfreq_realtime_shot(tasks[i], i, emit_hf)
                add_counter(bool(ok))

            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = [ex.submit(_run_one_highfreq_shot, i) for i in range(n)]
                for fu in concurrent.futures.as_completed(futs):
                    try:
                        fu.result()
                    except Exception as ex:
                        traceback.print_exc()
                        add_counter(False)
                        emit_hf({"type": "error", "ok": False, "message": str(ex).strip() or type(ex).__name__})
            emit_hf({"type": "done", "ok": True, "total": n, "success": counters[0], "failed": counters[1], "mode": hf_mode})
            return

        def _run_one_highfreq(i: int) -> None:
            ph_hint = str(tasks[i].get("phone", "")).strip() if isinstance(tasks[i], dict) else ""
            prep_t0 = time.perf_counter()
            try:
                p = _batch_rush_prepare_one(tasks[i])
            except Exception as ex:
                traceback.print_exc()
                p = {
                    "ok": False,
                    "phone": ph_hint,
                    "error": str(ex).strip() or type(ex).__name__,
                }
            prep_ms = int((time.perf_counter() - prep_t0) * 1000)
            phone = str(p.get("phone") or ph_hint).strip()
            if not p.get("ok"):
                emit_hf({
                    "type": "result",
                    "taskIndex": i,
                    "phone": phone,
                    "ok": False,
                    "message": p.get("error") or "准备失败",
                    "detail": p.get("detail"),
                    "prepMs": prep_ms,
                })
                add_counter(False)
                return
            emit_hf({"type": "prep", "taskIndex": i, "phone": phone, "ok": True, "ms": prep_ms})
            go_post_t0 = time.perf_counter()
            first_go_event_seen = False
            emit_hf({"type": "go_account_start", "taskIndex": i, "phone": phone, "prepMs": prep_ms})

            def _on_one_event(ev: dict):
                nonlocal first_go_event_seen
                if not first_go_event_seen:
                    first_go_event_seen = True
                    emit_hf({
                        "type": "go_first_event",
                        "taskIndex": i,
                        "phone": phone,
                        "waitMs": int((time.perf_counter() - go_post_t0) * 1000),
                        "goType": str(ev.get("type") or ""),
                    })
                typ = str(ev.get("type") or "")
                if typ in ("start", "done", "task_start"):
                    return
                if typ == "shot":
                    row = ev.get("row") if isinstance(ev.get("row"), dict) else {
                        "attempt": ev.get("attempt"),
                        "httpStatus": ev.get("httpStatus"),
                        "code": ev.get("code"),
                        "message": ev.get("message"),
                        "ms": ev.get("ms"),
                        "rushRound": ev.get("rushRound"),
                        "rushRounds": ev.get("rushRounds"),
                        "priorityRecordId": ev.get("priorityRecordId"),
                    }
                    emit_hf({"type": "shot", "taskIndex": i, "phone": phone, "row": row})
                    return
                if typ == "result":
                    nr = ev.get("result") if isinstance(ev.get("result"), dict) else {}
                    try:
                        biz_ok, pay, msg = _batch_rush_finalize_verify_pay_payload(p, nr)
                    except Exception as ex:
                        traceback.print_exc()
                        biz_ok, pay, msg = False, None, str(ex).strip() or type(ex).__name__
                    add_counter(bool(biz_ok))
                    emit_hf({
                        "type": "result",
                        "taskIndex": i,
                        "phone": phone,
                        "ok": bool(biz_ok),
                        "pay": pay,
                        **({"message": msg} if not biz_ok and msg else {}),
                    })
                    return
                emit_hf({"type": "go_event", "taskIndex": i, "phone": phone, "event": ev})

            try:
                _go_bridge_batch_stream_dispatch(
                    [p["node_task"]],
                    concurrency=1,
                    outer_timeout=estimate_node_batch_outer_timeout_sec([p["node_task"]]),
                    on_event=_on_one_event,
                )
            except Exception as ex:
                em = str(ex).strip() or type(ex).__name__
                traceback.print_exc()
                add_counter(False)
                emit_hf({
                    "type": "result",
                    "taskIndex": i,
                    "phone": phone,
                    "ok": False,
                    "message": "Go 高频流式发包失败: " + em,
                })

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_run_one_highfreq, i) for i in range(n)]
            for fu in concurrent.futures.as_completed(futs):
                try:
                    fu.result()
                except Exception as ex:
                    traceback.print_exc()
                    add_counter(False)
                    emit_hf({"type": "error", "ok": False, "message": str(ex).strip() or type(ex).__name__})
        emit_hf({"type": "done", "ok": True, "total": n, "success": counters[0], "failed": counters[1]})
        return
    emit({"type": "prep_start", "total": n})
    if has_mobile_relay:
        cap = _batch_rush_parallel_cap()
        max_workers = min(cap, max(1, int(concurrency)), n)
        emit({"type": "go_start", "total": n, "concurrency": max_workers, "mode": "mobile_relay"})
        counters = [0, 0]

        def _run_mobile_one(i: int) -> None:
            phone_hint = str(tasks[i].get("phone", "")).strip()

            def _emit_mobile_shot(row: dict) -> None:
                emit({
                    "type": "shot",
                    "taskIndex": i,
                    "phone": phone_hint,
                    "row": row,
                })

            try:
                r = _batch_rush_one_blocking(tasks[i], shot_emit=_emit_mobile_shot)
            except Exception as ex:
                traceback.print_exc()
                r = {
                    "ok": False,
                    "phone": phone_hint,
                    "message": str(ex).strip() or type(ex).__name__,
                }
            pay = r.get("pay") if isinstance(r, dict) else None
            ok = bool(r.get("ok")) if isinstance(r, dict) else False
            if ok:
                counters[0] += 1
            else:
                counters[1] += 1
            emit({
                "type": "result",
                "taskIndex": i,
                "phone": r.get("phone") or str(tasks[i].get("phone", "")).strip() if isinstance(r, dict) else str(tasks[i].get("phone", "")).strip(),
                "ok": ok,
                "pay": pay if ok else None,
                **({"message": r.get("message") or "失败"} if not ok and isinstance(r, dict) else {}),
            })

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_run_mobile_one, i) for i in range(n)]
            for fu in concurrent.futures.as_completed(futs):
                try:
                    fu.result()
                except Exception as ex:
                    traceback.print_exc()
                    counters[1] += 1
                    emit({"type": "error", "ok": False, "message": str(ex).strip() or type(ex).__name__})
        emit({"type": "done", "ok": True, "total": n, "success": counters[0], "failed": counters[1], "mode": "mobile_relay"})
        return

    emit_lock = threading.Lock()

    def emit_live(ev: dict) -> None:
        with emit_lock:
            emit(ev)

    cap = _batch_rush_parallel_cap()
    try:
        req_dispatch = max(1, int(concurrency))
    except (TypeError, ValueError):
        req_dispatch = min(cap, n)
    dispatch_workers = min(cap, req_dispatch, n)
    prep_workers = min(32, max(4, n))
    counters = [0, 0]

    def add_counter(ok: bool) -> None:
        with emit_lock:
            counters[0] += 1 if ok else 0
            counters[1] += 0 if ok else 1

    emit_live({
        "type": "go_start",
        "total": n,
        "concurrency": dispatch_workers,
        "mode": "prepare_stream",
        "prepConcurrency": prep_workers,
    })

    def _dispatch_prepared_one(orig_idx: int, p: dict) -> None:
        phone = str(p.get("phone") or "").strip()
        result_seen = False

        def _on_one_go_event(ev: dict):
            nonlocal result_seen
            typ = str(ev.get("type") or "")
            if typ in ("start", "done"):
                return
            if typ == "task_start":
                emit_live({"type": "task_start", "taskIndex": orig_idx, "phone": phone})
                return
            if typ == "shot":
                row = ev.get("row") if isinstance(ev.get("row"), dict) else {
                    "attempt": ev.get("attempt"),
                    "httpStatus": ev.get("httpStatus"),
                    "code": ev.get("code"),
                    "message": ev.get("message"),
                    "ms": ev.get("ms"),
                    "rushRound": ev.get("rushRound"),
                    "rushRounds": ev.get("rushRounds"),
                }
                emit_live({"type": "shot", "taskIndex": orig_idx, "phone": phone, "row": row})
                return
            if typ == "result":
                result_seen = True
                nr = ev.get("result") if isinstance(ev.get("result"), dict) else {}
                try:
                    biz_ok, pay, msg = _batch_rush_finalize_verify_pay_payload(p, nr)
                except Exception as ex:
                    traceback.print_exc()
                    biz_ok, pay, msg = False, None, str(ex).strip() or type(ex).__name__
                add_counter(bool(biz_ok))
                emit_live({
                    "type": "result",
                    "taskIndex": orig_idx,
                    "phone": phone,
                    "ok": bool(biz_ok),
                    "pay": pay,
                    **({"message": msg} if not biz_ok and msg else {}),
                })
                return
            emit_live({"type": "go_event", "taskIndex": orig_idx, "phone": phone, "event": ev})

        try:
            _go_bridge_batch_stream_dispatch(
                [p["node_task"]],
                concurrency=1,
                outer_timeout=estimate_node_batch_outer_timeout_sec([p["node_task"]]),
                on_event=_on_one_go_event,
            )
        except Exception as ex:
            em = str(ex).strip() or type(ex).__name__
            traceback.print_exc()
            if not result_seen:
                add_counter(False)
                emit_live({
                    "type": "result",
                    "taskIndex": orig_idx,
                    "phone": phone,
                    "ok": False,
                    "message": "Go 实时发包失败: " + em,
                })

    dispatch_futs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=dispatch_workers) as dispatch_ex:
        with concurrent.futures.ThreadPoolExecutor(max_workers=prep_workers) as prep_ex:
            futs = {prep_ex.submit(_batch_rush_prepare_one, tasks[i]): i for i in range(n)}
            for fu in concurrent.futures.as_completed(futs):
                i = futs[fu]
                try:
                    p = fu.result()
                except Exception as ex2:
                    p = {
                        "ok": False,
                        "phone": str(tasks[i].get("phone", "")).strip(),
                        "error": str(ex2).strip() or type(ex2).__name__,
                    }
                ph = str(p.get("phone") or tasks[i].get("phone") or "").strip()
                if not p.get("ok"):
                    add_counter(False)
                    emit_live({
                        "type": "result",
                        "taskIndex": i,
                        "phone": ph,
                        "ok": False,
                        "message": p.get("error") or "准备失败",
                        "detail": p.get("detail"),
                    })
                    continue
                emit_live({"type": "prep", "taskIndex": i, "phone": ph, "ok": True})
                dispatch_futs.append(dispatch_ex.submit(_dispatch_prepared_one, i, p))

        for fu in concurrent.futures.as_completed(dispatch_futs):
            try:
                fu.result()
            except Exception as ex:
                traceback.print_exc()
                add_counter(False)
                emit_live({"type": "error", "ok": False, "message": str(ex).strip() or type(ex).__name__})

    emit_live({
        "type": "done",
        "ok": True,
        "total": n,
        "success": counters[0],
        "failed": counters[1],
        "mode": "prepare_stream",
    })


class RuntimeState:
    def __init__(self):
        self.s = None
        self.addresses = []
        self.addr_index = 0
        self.last_rush = None
        self.last_compose = None
        self.last_submit = None
        self.last_pay = None
        self.last_gateway = None

    def ensure_session(self):
        if self.s is None:
            self.s = Session()
        return self.s

    def current_addr(self):
        if not self.addresses:
            return None
        if self.addr_index < 0 or self.addr_index >= len(self.addresses):
            self.addr_index = 0
        return self.addresses[self.addr_index]

    def snapshot(self):
        uid = self.s._uid if self.s else ""
        try:
            gfl = global_force_local_ip()
        except Exception:
            gfl = False
        return {
            "hasSession": self.s is not None,
            "hasLogin": bool(uid),
            "uid": uid,
            "addressCount": len(self.addresses),
            "selectedAddressIndex": self.addr_index if self.addresses else -1,
            "priorityRecordId": ((self.last_rush or {}).get("data") or {}).get("priorityRecordId", ""),
            "orderId": ((self.last_submit or {}).get("data") or {}).get("orderId", ""),
            "tn": ((self.last_pay or {}).get("data") or {}).get("tn", ""),
            "globalForceLocalIp": gfl,
        }


STATE = RuntimeState()


def json_ok(data=None, message="ok"):
    return {"ok": True, "message": message, "data": data}


def json_err(message="error", detail=None):
    out = {"ok": False, "message": message}
    if detail is not None:
        out["detail"] = detail
    return out


def _mobile_text_phones(payload: dict | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    raw_items: list[object] = []
    phones = payload.get("phones")
    if isinstance(phones, list):
        raw_items.extend(phones)
    elif phones is not None:
        raw_items.append(phones)
    text = payload.get("text")
    if text is None:
        text = payload.get("phonesText")
    if text is not None:
        raw_items.extend(re.split(r"[\s,，;；]+", str(text)))
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        ph = _norm_phone(raw)
        if not ph or ph in seen:
            continue
        seen.add(ph)
        out.append(ph)
    return out


def _mobile_order_pick_rows(j: object) -> list[dict]:
    if not isinstance(j, dict):
        return []
    data = j.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("list", "records", "rows", "orderList", "items", "orderVoList", "dataList"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    for key in ("list", "records", "rows", "orderList", "items", "orderVoList", "dataList"):
        value = j.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _mobile_first_item(row: dict) -> dict:
    for key in ("orderCartItemList", "itemList", "orderItemList", "goodsList", "skuList"):
        value = row.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
    return {}


def _mobile_first_text(obj: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = obj.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def _mobile_order_id(row: dict) -> str:
    return _mobile_first_text(row, ("orderId", "orderNo", "id", "orderCode", "tradeOrderId"))


def _mobile_status_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    if isinstance(value, dict):
        text = _mobile_first_text(
            value,
            ("statusDesc", "statusName", "desc", "name", "label", "text", "title", "statusStr", "orderStatusName"),
        )
        if text:
            return text
        code = value.get("code")
        msg = value.get("message", value.get("msg"))
        if code is not None and msg is not None:
            return f"{code}:{msg}"
        try:
            return json.dumps(value, ensure_ascii=False)[:120]
        except Exception:
            return ""
    return ""


def _mobile_format_time(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 100000000000:
            ts = ts / 1000.0
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        except Exception:
            return str(value)
    text = str(value).strip()
    if re.fullmatch(r"\d{13}", text):
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(text) / 1000.0))
        except Exception:
            return text
    if re.fullmatch(r"\d{10}", text):
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(text)))
        except Exception:
            return text
    return text


def _mobile_time_sort_key(value: object) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 100000000000:
            ts = ts / 1000.0
        return ts
    text = str(value).strip()
    if not text:
        return 0.0
    if re.fullmatch(r"\d{13}", text):
        try:
            return int(text) / 1000.0
        except Exception:
            return 0.0
    if re.fullmatch(r"\d{10}", text):
        try:
            return float(int(text))
        except Exception:
            return 0.0
    text2 = text.replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(text2, fmt))
        except Exception:
            continue
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", text2)
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        try:
            return time.mktime(
                (
                    int(y),
                    int(mo),
                    int(d),
                    int(hh),
                    int(mm),
                    int(ss or 0),
                    0,
                    0,
                    -1,
                )
            )
        except Exception:
            return 0.0
    return 0.0


def _mobile_order_sort_key(row: dict) -> float:
    if not isinstance(row, dict):
        return 0.0
    for key in ("t", "createTime", "createdAt", "orderTime", "payTime", "submitTime", "gmtCreate", "time"):
        if key in row:
            ts = _mobile_time_sort_key(row.get(key))
            if ts:
                return ts
    return 0.0


def _mobile_order_status_full_text(row: dict) -> str:
    if not isinstance(row, dict):
        return ""
    keys = (
        "statusDesc",
        "statusName",
        "orderStatusName",
        "orderStatusDesc",
        "statusStr",
        "stateName",
        "stateDesc",
        "tradeStatusName",
        "payStatusName",
        "deliveryStatusName",
        "receiptStatusName",
        "status",
        "orderStatus",
        "state",
        "tradeStatus",
        "payStatus",
        "deliveryStatus",
        "receiptStatus",
    )
    texts: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        text = _mobile_status_text(value)
        if text and text not in seen:
            seen.add(text)
            texts.append(text)

    for key in keys:
        if key in row:
            add(row.get(key))
    item = _mobile_first_item(row)
    if item:
        for key in keys:
            if key in item:
                add(item.get(key))
    for key, value in row.items():
        kl = str(key).lower()
        if ("status" in kl or "state" in kl) and key not in keys:
            add(value)
    return " / ".join(texts)


def _mobile_pickup_status_label(status_text: str, row: dict | None = None) -> str:
    text = str(status_text or "")
    if row:
        try:
            text += " " + json.dumps(row, ensure_ascii=False)[:4000]
        except Exception:
            pass
    if any(x in text for x in ("未支付", "待支付", "待付款", "去支付", "支付中", "付款中")):
        return "未支付"
    if any(x in text for x in ("待提货", "待取货", "待自提", "待收货", "已支付", "支付成功", "已付款", "备货", "待发货")):
        return "待提货"
    if any(x in text for x in ("已提货", "已取货", "已收货", "已完成", "交易完成")):
        return "已完成"
    if any(x in text for x in ("已取消", "已关闭", "已退款", "超时取消", "取消")):
        return "已取消"
    return status_text or "未知"


def _mobile_order_title(row: dict) -> str:
    text = _mobile_first_text(
        row,
        (
            "itemName",
            "spuName",
            "goodsName",
            "productName",
            "skuName",
            "itemTitle",
            "itemDesc",
            "commodityName",
            "orderItemName",
            "name",
        ),
    )
    if text:
        return text
    item = _mobile_first_item(row)
    return _mobile_first_text(item, ("itemSpuName", "spuName", "itemName", "title", "name"))


def _mobile_order_count(row: dict) -> str:
    text = _mobile_first_text(row, ("count", "quantity", "qty", "num", "itemCount", "goodsCount", "bottleCount"))
    if text:
        return text
    item = _mobile_first_item(row)
    return _mobile_first_text(item, ("count", "quantity", "qty", "num", "itemCount"))


def _mobile_order_amount(row: dict) -> str:
    text = _mobile_first_text(
        row,
        ("showActualPrice", "totalAmount", "amount", "payAmount", "orderAmount", "totalPrice", "price", "money", "realAmount"),
    )
    if text:
        return text
    item = _mobile_first_item(row)
    return _mobile_first_text(item, ("showActualPrice", "totalAmount", "amount", "payAmount", "price"))


def _mobile_order_time(row: dict) -> str:
    raw = None
    for key in ("createTime", "createdAt", "orderTime", "payTime", "submitTime", "gmtCreate", "time"):
        if key in row:
            raw = row.get(key)
            break
    return _mobile_format_time(raw)


def _mobile_short_asset(title: str, count: str) -> str:
    title = str(title or "").strip()
    count = str(count or "").strip()
    base = "订单"
    if "飞天" in title:
        base = "飞天"
    elif "茅台" in title:
        base = "茅台"
    elif title:
        base = title[:8]
    return f"{base}[{count}]" if count else base


def _mobile_order_summary(phone: str, row: dict, index: int = 0) -> dict:
    title = _mobile_order_title(row)
    count = _mobile_order_count(row)
    status_raw = _mobile_order_status_full_text(row) or row.get("status", row.get("orderStatus", row.get("state", row.get("statusDesc"))))
    order_id = _mobile_order_id(row)
    order_time = _mobile_order_time(row)
    sort_ts = _mobile_order_sort_key(row)
    return {
        "phone": phone,
        "orderId": order_id,
        "index": int(index),
        "title": title,
        "assetType": _mobile_short_asset(title, count),
        "count": count,
        "amount": _mobile_order_amount(row),
        "status": _mobile_status_text(status_raw),
        "time": order_time,
        "sortTs": sort_ts,
    }


def _mobile_pickup_status_one(phone: str, order_id: str, page_size: int = 20) -> dict:
    ph = _norm_phone(phone)
    oid = str(order_id or "").strip()
    if not ph or not oid:
        return {"ok": False, "phone": ph, "orderId": oid, "message": "phone 和 orderId 不能为空"}
    e = POOL.get(ph)
    if not e:
        return {"ok": False, "phone": ph, "orderId": oid, "message": "账号不存在"}
    if e.status != STATUS_OK:
        return {"ok": False, "phone": ph, "orderId": oid, "message": "账号未登录"}
    try:
        s = e.ensure_session()
        s.force_order_chain_direct()
        res = s.order_list_h5(page_no=1, page_size=max(1, min(int(page_size or 20), 50)), referer="", extra_query=None)
        j = res.get("json") if isinstance(res, dict) else None
        rows = _mobile_order_pick_rows(j)
        parsed: list[tuple[dict, dict]] = []
        for i, raw in enumerate(rows):
            summary = _mobile_order_summary(ph, raw, i)
            if summary.get("orderId"):
                parsed.append((raw, summary))
        parsed.sort(key=lambda x: float(x[1].get("sortTs") or 0), reverse=True)
        matched: tuple[dict, dict] | None = None
        for raw, summary in parsed:
            if str(summary.get("orderId") or "").strip() == oid:
                matched = (raw, summary)
                break
        if matched is None:
            top_orders = [summary for _, summary in parsed[:5]]
            return {
                "ok": False,
                "phone": ph,
                "orderId": oid,
                "message": "订单列表未找到该订单",
                "orders": top_orders,
                "httpStatus": res.get("httpStatus") if isinstance(res, dict) else None,
                "code": res.get("code") if isinstance(res, dict) else None,
            }
        raw, summary = matched
        status_text = str(summary.get("status") or _mobile_order_status_full_text(raw) or "").strip()
        pickup_status = _mobile_pickup_status_label(status_text, raw)
        return {
            "ok": True,
            "phone": ph,
            "orderId": oid,
            "pickupStatus": pickup_status,
            "statusText": status_text,
            "order": summary,
            "httpStatus": res.get("httpStatus") if isinstance(res, dict) else None,
            "code": res.get("code") if isinstance(res, dict) else None,
            "message": res.get("message") if isinstance(res, dict) else "",
        }
    except Exception as ex:
        return {"ok": False, "phone": ph, "orderId": oid, "message": str(ex).strip() or type(ex).__name__}


def _mobile_scan_one_phone(phone: str, page_size: int = 20) -> dict:
    ph = _norm_phone(phone)
    if not ph:
        return {"ok": False, "phone": "", "message": "手机号为空"}
    e = POOL.get(ph)
    if not e:
        return {"ok": False, "phone": ph, "message": "账号不存在"}
    if e.status != STATUS_OK:
        return {"ok": False, "phone": ph, "message": "账号未登录"}
    try:
        s = e.ensure_session()
        s.force_order_chain_direct()
        res = s.order_list_h5(page_no=1, page_size=max(1, min(int(page_size or 20), 50)), referer="", extra_query=None)
        j = res.get("json") if isinstance(res, dict) else None
        rows = _mobile_order_pick_rows(j)
        orders = [_mobile_order_summary(ph, row, i) for i, row in enumerate(rows)]
        orders = [x for x in orders if x.get("orderId")]
        orders.sort(key=lambda x: float(x.get("sortTs") or 0), reverse=True)
        latest = orders[0] if orders else None
        msg = ""
        if isinstance(res, dict):
            msg = str(res.get("message") or res.get("msg") or "")
        return {
            "ok": bool(latest),
            "phone": ph,
            "message": msg or ("已查到订单" if latest else "无订单"),
            "latest": latest,
            "orders": orders[:5],
            "httpStatus": res.get("httpStatus") if isinstance(res, dict) else None,
            "code": res.get("code") if isinstance(res, dict) else None,
        }
    except Exception as ex:
        return {"ok": False, "phone": ph, "message": str(ex).strip() or type(ex).__name__}


def _mobile_local_api_post(path: str, payload: dict, timeout: float = 90.0) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"http://{LOCAL_UI_HOST}:{PORT}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as ex:
        raw = ex.read()
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as ex:
        return json_err("本机接口返回异常", detail=str(ex))


def _mobile_pick_pay_url(data: dict, channel: str) -> str:
    if not isinstance(data, dict):
        return ""
    channel = str(channel or "").strip()
    if channel in ("80_ios", "ysf_ios", "ios"):
        keys = ("ysfIosUrl",)
    elif channel in ("80_android", "ysf_android", "android", "80", "20", "2"):
        keys = ("ysfAndroidUrl", "ysfUrl", "unionpayUrl", "payUrl")
    elif channel in ("60", "wechat", "wx"):
        keys = ("wechatUrl", "weixinUrl", "payUrlHttps", "payUrl", "alipayH5PayUrl")
    else:
        keys = ("alipayUrl", "alipayH5PayUrl", "payUrlHttps", "payUrl")
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _mobile_load_pay_notices() -> list[dict]:
    try:
        st = load_ui_state_raw()
    except Exception:
        st = {}
    notices = st.get("payNotices") if isinstance(st, dict) else None
    if not isinstance(notices, list):
        return []
    return [x for x in notices if isinstance(x, dict)]


def _mobile_notice_time_ms(row: dict) -> int:
    raw = row.get("t")
    try:
        return int(float(raw))
    except Exception:
        return 0


def _mobile_notice_public(row: dict) -> dict:
    if not isinstance(row, dict):
        return {}
    t_ms = _mobile_notice_time_ms(row)
    ysf_auth = str(row.get("ysfAuthCode") or "").strip()
    ysf_android = str(row.get("ysfAndroidUrl") or row.get("ysfUrl") or "").strip()
    ysf_ios = str(row.get("ysfIosUrl") or "").strip()
    if ysf_auth:
        if not ysf_android:
            try:
                ysf_android = _build_ysf_android_url(ysf_auth)
            except Exception:
                ysf_android = ""
        if not ysf_ios:
            try:
                ysf_ios = _build_ysf_ios_url(ysf_auth)
            except Exception:
                ysf_ios = ""
    out = {
        "phone": _norm_phone(row.get("phone")),
        "orderId": str(row.get("orderId") or row.get("order_id") or "").strip(),
        "payUrl": str(row.get("payUrl") or row.get("pay_url") or row.get("PayUrl") or "").strip(),
        "alipayUrl": str(row.get("alipayUrl") or row.get("alipayH5PayUrl") or "").strip(),
        "alipayH5PayUrl": str(row.get("alipayH5PayUrl") or row.get("alipayUrl") or "").strip(),
        "payUrlHttps": str(row.get("payUrlHttps") or "").strip(),
        "wechatUrl": str(row.get("wechatUrl") or row.get("weixinUrl") or "").strip(),
        "weixinUrl": str(row.get("weixinUrl") or row.get("wechatUrl") or "").strip(),
        "ysfAuthCode": ysf_auth,
        "ysfAndroidUrl": ysf_android,
        "ysfUrl": ysf_android,
        "ysfIosUrl": ysf_ios,
        "productName": str(row.get("productName") or "").strip(),
        "spec": str(row.get("spec") or "").strip(),
        "bottles": row.get("bottles", ""),
        "amountStr": str(row.get("amountStr") or "").strip(),
        "t": t_ms,
        "time": _mobile_format_time(t_ms) if t_ms else "",
    }
    return out


def _mobile_pay_notice_index(notices: list[dict] | None = None) -> dict:
    notices = notices if isinstance(notices, list) else _mobile_load_pay_notices()
    exact: dict[tuple[str, str], dict] = {}
    latest_phone: dict[str, dict] = {}
    for row in notices:
        pub = _mobile_notice_public(row)
        ph = pub.get("phone") or ""
        oid = pub.get("orderId") or ""
        if not ph:
            continue
        key = (ph, oid)
        if oid and (key not in exact or _mobile_notice_time_ms(row) >= _mobile_notice_time_ms(exact[key])):
            exact[key] = row
        if ph not in latest_phone or _mobile_notice_time_ms(row) >= _mobile_notice_time_ms(latest_phone[ph]):
            latest_phone[ph] = row
    return {"exact": exact, "latestPhone": latest_phone}


def _mobile_find_pay_notice(phone: str, order_id: str = "", *, index: dict | None = None, allow_phone_fallback: bool = False) -> tuple[dict | None, str]:
    ph = _norm_phone(phone)
    oid = str(order_id or "").strip()
    idx = index if isinstance(index, dict) else _mobile_pay_notice_index()
    exact = idx.get("exact") if isinstance(idx.get("exact"), dict) else {}
    latest_phone = idx.get("latestPhone") if isinstance(idx.get("latestPhone"), dict) else {}
    if ph and oid:
        row = exact.get((ph, oid))
        if isinstance(row, dict):
            return row, "phone_order"
    if allow_phone_fallback and ph:
        row = latest_phone.get(ph)
        if isinstance(row, dict):
            return row, "phone_latest"
    return None, ""


def _mobile_attach_notice_to_row(row: dict, index: dict) -> dict:
    if not isinstance(row, dict):
        return row
    notice, matched_by = _mobile_find_pay_notice(
        str(row.get("phone") or ""),
        str(row.get("orderId") or ""),
        index=index,
        allow_phone_fallback=True,
    )
    if not notice:
        row["payNotice"] = None
        row["hasPayNotice"] = False
        return row
    pub = _mobile_notice_public(notice)
    row["payNotice"] = pub
    row["payNoticeMatchedBy"] = matched_by
    row["hasPayNotice"] = True
    row["hasAlipay"] = bool(_mobile_pick_pay_url(pub, "70"))
    row["hasWechat"] = bool(_mobile_pick_pay_url(pub, "60"))
    row["hasYsf"] = bool(_mobile_pick_pay_url(pub, "80"))
    return row


def _mobile_row_from_pay_notice(phone: str, notice: dict) -> dict:
    pub = _mobile_notice_public(notice)
    title = str(pub.get("productName") or pub.get("spec") or "电脑端支付记录").strip()
    count = str(pub.get("bottles") or "").strip()
    row = {
        "ok": True,
        "phone": _norm_phone(phone) or pub.get("phone") or "",
        "orderId": str(pub.get("orderId") or "").strip(),
        "title": title,
        "assetType": _mobile_short_asset(title, count),
        "count": count,
        "amount": pub.get("amountStr") or "",
        "status": "电脑端已提取链接",
        "time": pub.get("time") or "",
        "sortTs": _mobile_time_sort_key(pub.get("t") or pub.get("time")),
        "message": "电脑端支付记录",
        "payNotice": pub,
        "payNoticeMatchedBy": "phone_latest",
        "hasPayNotice": True,
        "hasAlipay": bool(_mobile_pick_pay_url(pub, "70")),
        "hasWechat": bool(_mobile_pick_pay_url(pub, "60")),
        "hasYsf": bool(_mobile_pick_pay_url(pub, "80")),
    }
    return row


# 省市区全国库：供 ui.html 同源拉取（避免内嵌 WebView 直连 GitHub 失败）
_PCAS_BYTES_CACHE: bytes | None = None
_PCAS_BYTES_LOCK = threading.Lock()
_PCAS_LOCAL_FILE = ROOT / "pcas-code.json"
_PCAS_REMOTE_URLS = (
    "https://cdn.jsdelivr.net/gh/modood/Administrative-divisions-of-China@master/dist/pcas-code.json",
    "https://fastly.jsdelivr.net/gh/modood/Administrative-divisions-of-China@master/dist/pcas-code.json",
    "https://raw.githubusercontent.com/modood/Administrative-divisions-of-China/master/dist/pcas-code.json",
)


def _get_pcas_json_bytes() -> bytes:
    """返回 modood pcas-code.json 原始 UTF-8 字节；优先内存缓存，其次本地文件，再次多镜像 HTTP。"""
    global _PCAS_BYTES_CACHE
    with _PCAS_BYTES_LOCK:
        if _PCAS_BYTES_CACHE is not None:
            return _PCAS_BYTES_CACHE
    if _PCAS_LOCAL_FILE.is_file():
        data = _PCAS_LOCAL_FILE.read_bytes()
        if len(data) < 5000:
            raise OSError(f"本地 {_PCAS_LOCAL_FILE.name} 过小或损坏")
        json.loads(data.decode("utf-8"))
        with _PCAS_BYTES_LOCK:
            _PCAS_BYTES_CACHE = data
        return data
    last: Exception | None = None
    for url in _PCAS_REMOTE_URLS:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 MT-WebUI/pcas-fetch"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if len(data) < 5000:
                raise ValueError("响应过小")
            json.loads(data.decode("utf-8"))
            with _PCAS_BYTES_LOCK:
                _PCAS_BYTES_CACHE = data
            return data
        except Exception as ex:
            last = ex
            continue
    raise RuntimeError(str(last or "无法下载 pcas-code.json"))


def _charset_from_content_type(ct: str) -> str:
    if not ct:
        return "utf-8"
    m = re.search(r"charset\s*=\s*([a-zA-Z0-9._-]+)", ct, re.I)
    if not m:
        return "utf-8"
    enc = m.group(1).strip().strip('"').strip("'").lower()
    if enc in ("utf8", "utf_8"):
        return "utf-8"
    return enc


def parse_json(handler):
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    ct = handler.headers.get("Content-Type", "") or ""
    enc = _charset_from_content_type(ct)
    try:
        text = raw.decode(enc)
    except LookupError:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return json.loads(text)


def _extract_item_pairs(obj, out):
    if isinstance(obj, dict):
        code = obj.get("itemCode") or obj.get("spuId")
        act = obj.get("itemPriorityActId") or obj.get("actId") or obj.get("priorityActId")
        if code and act:
            key = (str(code), str(act))
            if key not in out:
                out.add(key)
        for v in obj.values():
            _extract_item_pairs(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _extract_item_pairs(v, out)


def _extract_from_text(text, out):
    if not text:
        return
    code_keys = r"(?:itemCode|spuId)"
    act_keys = r"(?:itemPriorityActId|actId|priorityActId)"
    code_vals = re.findall(code_keys + r'"\s*:\s*"([^"]+)"', text)
    act_vals = re.findall(act_keys + r'"\s*:\s*"([^"]+)"', text)
    for c in code_vals:
        for a in act_vals:
            out.add((str(c), str(a)))


class Handler(BaseHTTPRequestHandler):
    server_version = "AppUIBridge/1.0"
    # 访问日志开关：默认静默轮询噪音；设 MT_WEBUI_ACCESS_LOG=1 可恢复全部请求打印
    _verbose_access_log = str(os.environ.get("MT_WEBUI_ACCESS_LOG", "") or "").strip().lower() in ("1", "true", "yes", "on")

    def log_message(self, fmt, *args):
        if self._verbose_access_log:
            return super().log_message(fmt, *args)
        try:
            code = int(args[1]) if len(args) > 1 else 0
        except Exception:
            code = 0
        p = self._request_path()
        # 默认仅打印异常请求；高频轮询接口彻底静默
        if code >= 400:
            return super().log_message(fmt, *args)
        if p in ("/api/state", "/api/ui/state", "/api/ui/address_book", "/api/accounts"):
            return
        if isinstance(p, str) and p.startswith("/api/debug/concurrency_hold"):
            return
        # 其余 2xx/3xx 默认不打印，避免启动后刷屏
        return

    def _send_json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        _send_cors_headers(self)
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, html, code=200):
        raw = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        _send_cors_headers(self)
        self.end_headers()
        self.wfile.write(raw)

    def _start_ndjson(self, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        _send_cors_headers(self)
        self.end_headers()

    def _write_ndjson(self, obj) -> bool:
        try:
            raw = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
            self.wfile.write(raw)
            try:
                self.wfile.flush()
            except Exception:
                pass
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False
        except Exception:
            traceback.print_exc()
            return False

    def do_OPTIONS(self):
        self.send_response(204)
        _send_cors_headers(self)
        self.end_headers()

    def _request_path(self) -> str:
        p = urlparse(self.path).path or "/"
        if len(p) > 1 and p.endswith("/"):
            return p[:-1]
        return p

    def do_GET(self):
        path = self._request_path()
        if not _path_is_public_remote_get(path) and not _control_console_ip_allowed(self):
            return self._send_json(
                json_err(
                    "控制台 IP 未授权",
                    detail={
                        "clientIp": _client_ip(self),
                        "allowlist": CONTROL_CONSOLE_IP_ALLOWLIST_RAW or "",
                    },
                ),
                403,
            )
        if path in ("/", "/ui.html"):
            if not UI_FILE.exists():
                return self._send_html("<h1>ui.html 不存在</h1>", 404)
            return self._send_html(UI_FILE.read_text(encoding="utf-8"))
        if path in ("/mobile_order", "/mobile_order.html"):
            if not _remote_login_page_authorized(self):
                return self._send_html(
                    "<!DOCTYPE html><html><head><meta charset=\"utf-8\"/><title>无权访问</title></head>"
                    "<body><p>缺少或错误的访问口令。请使用完整链接（含 <code>?token=</code>）。</p></body></html>",
                    403,
                )
            if not MOBILE_ORDER_FILE.exists():
                return self._send_html("<h1>mobile_order.html 不存在</h1>", 404)
            return self._send_html(MOBILE_ORDER_FILE.read_text(encoding="utf-8"))
        if path == "/node_rush_panel":
            body, err = _go_bridge_http_get("/rush_panel")
            if err or body is None:
                detail = html_escape(err or "无响应")
                np = _go_bridge_port()
                return self._send_html(
                    f"<!DOCTYPE html><html><head><meta charset=\"utf-8\"/><title>go_bridge 不可达</title></head>"
                    f"<body><h1>无法从 go_bridge 拉取 rush 面板</h1><p>{detail}</p>"
                    f"<p>请确认 rush_bridge 已在 <code>127.0.0.1:{np}</code> 监听（环境变量 <code>MT_GO_BRIDGE_PORT</code>）。"
                    f"当前实现可能无 <code>/rush_panel</code>，属正常。</p></body></html>",
                    502,
                )
            text = _rewrite_rush_panel_html_for_webui_proxy(body.decode("utf-8", errors="replace"))
            return self._send_html(text)
        if path == "/node_rush_tap":
            parsed = urlparse(self.path)
            q = ("?" + parsed.query) if parsed.query else ""
            body, err = _go_bridge_http_get("/rush_tap" + q)
            if err or body is None:
                return self._send_json(json_err("go_bridge /rush_tap 不可达", detail=err or "无响应"), 502)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            _send_cors_headers(self)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/address/pcas":
            try:
                data = _get_pcas_json_bytes()
            except Exception as ex:
                return self._send_json(
                    json_err("全国行政区划数据不可用", detail=str(ex)[:800]),
                    502,
                )
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(data)))
            _send_cors_headers(self)
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/state":
            snap = STATE.snapshot()
            snap["apiShardBases"] = _api_shard_bases_for_handler(self)
            snap["apiShardPhoneOrder"] = _api_shard_phone_order_for_snapshot()
            # go_bridge 端口（MT_GO_BRIDGE_PORT）可能因占坑/自动切换而变；看板同源入口：主 WebUI + /node_rush_panel
            np = _go_bridge_port()
            snap["goBridgePort"] = np
            snap["nodeHttpBridgePort"] = np  # 兼容旧字段名，实为 go_bridge 监听端口
            snap["nodeRushPanelUrl"] = f"http://{LOCAL_UI_HOST}:{np}/rush_panel"
            snap["nodeRushPanelUrlWebUi"] = f"http://{LOCAL_UI_HOST}:{PORT}/node_rush_panel"
            snap["goBridgeMode"] = _go_bridge_mode_snapshot()
            snap["bbResources"] = read_bb_resources_snapshot()
            return self._send_json(json_ok(snap))
        if path == "/api/bb/resources":
            return self._send_json(json_ok(read_bb_resources_snapshot()))
        if path == "/api/bb/update/status":
            payload = bb_auto_update_snapshot()
            payload["updateMode"] = _bb_update_cli_mode()
            return self._send_json(json_ok(payload))
        if path == "/api/go_bridge/mode":
            return self._send_json(json_ok(_go_bridge_mode_snapshot()))
        if path == "/api/distributed/status":
            return self._send_json(json_ok(_dist_status_snapshot()))
        if path == "/api/distributed/config":
            return self._send_json(json_ok(_dist_load_node_config()))
        if path == "/remote_login.html":
            if not _remote_login_page_authorized(self):
                return self._send_html(
                    "<!DOCTYPE html><html><head><meta charset=\"utf-8\"/><title>无权访问</title></head>"
                    "<body><p>缺少或错误的上号口令。请向管理员索取完整链接（含 <code>?token=</code>）。</p></body></html>",
                    403,
                )
            if not REMOTE_LOGIN_FILE.exists():
                return self._send_html("<h1>remote_login.html 不存在</h1>", 404)
            return self._send_html(REMOTE_LOGIN_FILE.read_text(encoding="utf-8"))
        if path == "/api/proxy_pool":
            return self._send_json(json_ok(_read_proxy_pool_payload()))
        if path == "/api/proxy_pool/fill_status":
            return self._send_json(json_ok(_proxy_fill_snapshot()))
        if path == "/api/proxy_pool/shards_preview":
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query or "")
            only_logged = (qs.get("onlyLogged") or ["1"])[0].strip().lower() not in (
                "0",
                "false",
                "no",
            )
            qs_phones = []
            for v in qs.get("phones") or []:
                qs_phones.extend([x.strip() for x in str(v).split(",") if x.strip()])
            return self._send_json(json_ok(_proxy_pool_shards_preview(only_logged, qs_phones or None)))
        if path == "/api/accounts":
            # #region agent log
            try:
                _snap = POOL.snapshot()
                _log_path = Path(__file__).resolve().parent / "debug-3de74e.log"
                with open(_log_path, "a", encoding="utf-8") as _lf:
                    _lf.write(
                        json.dumps(
                            {
                                "sessionId": "3de74e",
                                "hypothesisId": "H2",
                                "location": "webui_server.py:GET /api/accounts",
                                "message": "snapshot counts",
                                "data": {
                                    "accountCount": len(_snap),
                                    "stats": POOL.stats(),
                                },
                                "timestamp": int(time.time() * 1000),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # #endregion
            return self._send_json(json_ok({
                "accounts": POOL.snapshot(),
                "stats": POOL.stats(),
            }))
        if path == "/api/ui/state":
            st = normalize_ui_state_address_book_keys(load_ui_state_raw())
            st = _ui_state_with_address_db_overlay(st)
            return self._send_json(json_ok(st))
        if path == "/api/ui/address_book":
            st = normalize_ui_state_address_book_keys(load_ui_state_raw())
            st = _ui_state_with_address_db_overlay(st)
            ab = st.get("addressBook") if isinstance(st, dict) else {}
            if not isinstance(ab, dict):
                ab = {}
            return self._send_json(
                json_ok(
                    {
                        "addressBook": ab,
                        "addressBookUpdatedAt": str(
                            (st.get("addressBookUpdatedAt") if isinstance(st, dict) else "") or ""
                        ),
                    }
                )
            )
        if path == "/api/items/load_default_txt":
            return self._send_json(
                json_err("已废弃：请使用 product_list.json（与 mt 目录同步），见 /api/items/catalog"),
                410,
            )
        if path == "/api/items/catalog":
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query or "")
            full = (qs.get("full") or ["0"])[0].strip().lower() in ("1", "true", "yes")
            items = load_catalog_items(include_full_product_list=full)
            for it in items:
                c = str(it.get("code") or "").strip()
                if c and not (it.get("referer") or "").strip():
                    fb = (MT_H5_REFERER_BY_ITEM_CODE.get(c) or "").strip()
                    if fb:
                        it["referer"] = fb
            src = ["mt_item_list.json", "product_list.json"] if full else ["mt_item_list.json"]
            return self._send_json(json_ok({
                "items": items,
                "count": len(items),
                "sources": src,
                "fullProductList": full,
            }))
        if path == "/api/wechat/status":
            from wechat_notify import get_status as wx_status
            return self._send_json(json_ok(wx_status()))
        if path == "/api/wechat/login":
            from wechat_notify import connect as wx_connect
            return self._send_json(json_ok(wx_connect()))
        if path == "/api/wechat/logout":
            from wechat_notify import disconnect as wx_disconnect
            return self._send_json(json_ok(wx_disconnect()))
        if path == "/api/wechat/contacts":
            from wechat_notify import get_contacts_preview as wx_contacts
            return self._send_json(json_ok({"contacts": wx_contacts()}))
        if path == "/api/debug/concurrency_probe_reset":
            return self._send_json(json_ok(_conc_dbg_reset()))
        if path == "/api/debug/concurrency_probe_result":
            return self._send_json(json_ok(_conc_dbg_result()))
        if path == "/api/debug/concurrency_hold":
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query or "")
            raw_ms = (qs.get("ms") or ["1600"])[0].strip()
            try:
                ms = int(raw_ms)
            except (TypeError, ValueError):
                ms = 1600
            return self._send_json(json_ok(_conc_dbg_hold(ms)))
        # 抢单机器人须 POST + JSON；用 GET 会误认为 404，此处单独提示
        if path in GRAB_BOT_HTTP_PATHS or (
            isinstance(path, str) and path.startswith("/grab/")
        ):
            self.send_response(405)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Allow", "POST, OPTIONS")
            _send_cors_headers(self)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    json_err(
                        "抢单接口仅支持 POST（application/json），"
                        "路径示例：/grab/request-code、/grab/submit-code；"
                        "GET 会返回本错误而非 404。",
                    ),
                    ensure_ascii=False,
                ).encode("utf-8")
            )
            return
        self._send_json(json_err("not found"), 404)

    def do_POST(self):
        path = self._request_path()
        try:
            payload = parse_json(self)
        except Exception:
            return self._send_json(json_err("JSON 解析失败"), 400)

        if not _path_is_public_remote_post(path) and not _control_console_ip_allowed(self):
            return self._send_json(
                json_err(
                    "控制台 IP 未授权",
                    detail={
                        "clientIp": _client_ip(self),
                        "allowlist": CONTROL_CONSOLE_IP_ALLOWLIST_RAW or "",
                    },
                ),
                403,
            )

        try:
            if path == "/api/go_bridge/mode":
                try:
                    snap = _switch_go_bridge_mode(payload.get("mode", payload.get("engine", "")))
                except ValueError as ex:
                    return self._send_json(json_err(str(ex)), 400)
                except Exception as ex:
                    traceback.print_exc()
                    return self._send_json(json_err("go_bridge 模式切换失败", detail=str(ex)), 500)
                return self._send_json(json_ok(snap, f"go_bridge 已切换为 {snap.get('mode') or snap.get('requestedMode')}"))

            if path == "/api/mobile_relay/heartbeat":
                if not _remote_login_api_authorized(self, payload):
                    return self._send_json(json_err("访问口令无效或缺失"), 403)
                try:
                    dev = mobile_relay.touch_device(payload.get("deviceId"), payload)
                except Exception as ex:
                    return self._send_json(json_err(str(ex)), 400)
                return self._send_json(json_ok({"device": dev, "snapshot": mobile_relay.snapshot()}))

            if path == "/api/mobile_relay/poll":
                if not _remote_login_api_authorized(self, payload):
                    return self._send_json(json_err("访问口令无效或缺失"), 403)
                try:
                    wait_ms = int(payload.get("waitMs") or payload.get("wait_ms") or 20000)
                except (TypeError, ValueError):
                    wait_ms = 20000
                try:
                    task = mobile_relay.poll_task(payload.get("deviceId"), wait_ms=wait_ms, info=payload)
                except Exception as ex:
                    return self._send_json(json_err(str(ex)), 400)
                return self._send_json(json_ok({"task": task, "snapshot": mobile_relay.snapshot()}))

            if path == "/api/mobile_relay/result":
                if not _remote_login_api_authorized(self, payload):
                    return self._send_json(json_err("访问口令无效或缺失"), 403)
                result = payload.get("result")
                if not isinstance(result, dict):
                    return self._send_json(json_err("result 须为对象"), 400)
                ok = mobile_relay.complete_task(
                    payload.get("deviceId"),
                    payload.get("taskId") or payload.get("task_id") or payload.get("id"),
                    result,
                )
                if not ok:
                    return self._send_json(json_err("任务不存在或已超时"), 404)
                return self._send_json(json_ok({"accepted": True, "snapshot": mobile_relay.snapshot()}))

            if path == "/api/mobile_relay/status":
                if not _remote_login_api_authorized(self, payload):
                    return self._send_json(json_err("访问口令无效或缺失"), 403)
                return self._send_json(json_ok(mobile_relay.snapshot()))

            if path in GRAB_BOT_HTTP_PATHS and not _grab_bot_authorized(self):
                return self._send_json(
                    json_err(
                        "未授权：请在抢单端设置 MT_GRAB_BOT_SECRET，"
                        "与微信机器人 config.grabOrder.secret 相同"
                    ),
                    401,
                )

            if path == "/api/mobile/orders_scan":
                if not _remote_login_api_authorized(self, payload):
                    return self._send_json(json_err("访问口令无效或缺失"), 403)
                phones = _mobile_text_phones(payload)
                if not phones:
                    return self._send_json(json_err("请输入要查询的手机号"), 400)
                if len(phones) > 300:
                    return self._send_json(json_err("单次最多查询 300 个手机号"), 400)
                started = time.time()
                pay_notice_index = _mobile_pay_notice_index()
                latest_phone = pay_notice_index.get("latestPhone") if isinstance(pay_notice_index.get("latestPhone"), dict) else {}
                rows = []
                ordered = []
                for ph_raw in phones:
                    ph = _norm_phone(ph_raw)
                    notice = latest_phone.get(ph) if ph else None
                    if isinstance(notice, dict):
                        row = _mobile_row_from_pay_notice(ph, notice)
                        rows.append(row)
                        ordered.append({"ok": True, "phone": ph, "latest": row, "message": "电脑端支付通知"})
                    else:
                        ordered.append({"ok": False, "phone": ph, "message": "本机暂无支付通知"})
                rows = [x for x in rows if x.get("ok") and str(x.get("orderId") or "").strip()]
                rows.sort(key=lambda x: float(x.get("sortTs") or 0), reverse=True)
                ok_count = sum(1 for x in rows if x.get("ok"))
                return self._send_json(
                    json_ok(
                        {
                            "rows": rows,
                            "results": ordered,
                            "total": len(phones),
                            "okCount": ok_count,
                            "failedCount": len(phones) - ok_count,
                            "elapsedMs": int((time.time() - started) * 1000),
                        }
                    )
                )

            if path == "/api/mobile/order_detail":
                if not _remote_login_api_authorized(self, payload):
                    return self._send_json(json_err("访问口令无效或缺失"), 403)
                phone = _norm_phone(payload.get("phone"))
                order_id = str(payload.get("orderId") or payload.get("order_id") or "").strip()
                if not phone or not order_id:
                    return self._send_json(json_err("phone 和 orderId 不能为空"), 400)
                notice, matched_by = _mobile_find_pay_notice(phone, order_id, allow_phone_fallback=False)
                if not notice:
                    notice, matched_by = _mobile_find_pay_notice(phone, order_id, allow_phone_fallback=True)
                if not notice:
                    return self._send_json(json_err("本机暂无这个账号的支付通知记录"), 404)
                data = _mobile_notice_public(notice)
                return self._send_json(
                    json_ok(
                        {
                            "phone": phone,
                            "orderId": order_id,
                            "source": "ui_state.payNotices",
                            "matchedBy": matched_by,
                            "detail": data,
                            "raw": data,
                        }
                    )
                )

            if path == "/api/mobile/pickup_status":
                if not _remote_login_api_authorized(self, payload):
                    return self._send_json(json_err("访问口令无效或缺失"), 403)
                phone = _norm_phone(payload.get("phone"))
                order_id = str(payload.get("orderId") or payload.get("order_id") or "").strip()
                if not phone or not order_id:
                    return self._send_json(json_err("phone 和 orderId 不能为空"), 400)
                try:
                    page_size = int(payload.get("pageSize") or payload.get("size") or 20)
                except (TypeError, ValueError):
                    page_size = 20
                out = _mobile_pickup_status_one(phone, order_id, page_size=page_size)
                return self._send_json(json_ok(out) if out.get("ok") else json_err(out.get("message") or "提货查询失败", detail=out))

            if path == "/api/mobile/pay_link":
                if not _remote_login_api_authorized(self, payload):
                    return self._send_json(json_err("访问口令无效或缺失"), 403)
                phone = _norm_phone(payload.get("phone"))
                order_id = str(payload.get("orderId") or payload.get("order_id") or "").strip()
                channel = str(payload.get("channel") or "70").strip() or "70"
                if not phone or not order_id:
                    return self._send_json(json_err("phone 和 orderId 不能为空"), 400)
                notice, matched_by = _mobile_find_pay_notice(phone, order_id, allow_phone_fallback=False)
                if not notice:
                    notice, matched_by = _mobile_find_pay_notice(phone, order_id, allow_phone_fallback=True)
                if not notice:
                    return self._send_json(json_err("电脑端还没有这个账号的支付链接记录"), 404)
                data = _mobile_notice_public(notice)
                pay_url = _mobile_pick_pay_url(data, channel)
                if not pay_url:
                    return self._send_json(json_err(f"电脑端记录里没有 {channel} 通道链接", detail=data), 404)
                return self._send_json(
                    json_ok(
                        {
                            "phone": phone,
                            "orderId": order_id,
                            "channel": channel,
                            "payUrl": pay_url,
                            "payUrlFrom": "ui_state.payNotices",
                            "matchedBy": matched_by,
                            "alipayH5PayUrl": data.get("alipayH5PayUrl") or "",
                            "alipayUrl": data.get("alipayUrl") or "",
                            "payUrlHttps": data.get("payUrlHttps") or "",
                            "wechatUrl": data.get("wechatUrl") or "",
                            "ysfAuthCode": data.get("ysfAuthCode") or "",
                            "ysfAndroidUrl": data.get("ysfAndroidUrl") or "",
                            "ysfUrl": data.get("ysfUrl") or data.get("ysfAndroidUrl") or "",
                            "ysfIosUrl": data.get("ysfIosUrl") or "",
                            "productName": data.get("productName") or "",
                            "spec": data.get("spec") or "",
                            "bottles": data.get("bottles") or "",
                            "noticeTime": data.get("time") or "",
                            "raw": data,
                        }
                    )
                )

            if path == "/api/bb/update/run":
                out = run_bb_auto_update_once()
                if out.get("skipped"):
                    return self._send_json(json_ok(out, "已有自动更新任务在运行"))
                if out.get("ok"):
                    return self._send_json(json_ok(out, "BB 自动更新已完成"))
                return self._send_json(json_err(out.get("error") or "BB 自动更新失败", detail=out), 500)

            if path == "/api/bb/update/sync":
                out = run_bb_fetch_latest_once()
                if out.get("skipped"):
                    return self._send_json(json_ok(out, "已有 BB 更新任务在运行"))
                if out.get("ok"):
                    return self._send_json(json_ok(out, "已拉取并写入最新 BB 资源"))
                return self._send_json(json_err(out.get("error") or "拉取最新 BB 资源失败", detail=out), 500)

            if path == "/api/ui/state":
                merge_ui_state_from_post(payload)
                return self._send_json(json_ok({"saved": True}, "ui 状态已保存"))

            if path == "/api/ui/address_book/merge":
                raw_book = payload.get("addressBook")
                if not isinstance(raw_book, dict):
                    full = payload.get("uiStateSnapshot")
                    if isinstance(full, dict):
                        raw_book = extract_address_book_from_doc(full)
                    else:
                        raw_book = None
                if not isinstance(raw_book, dict) or not raw_book:
                    return self._send_json(json_err("缺少 addressBook 对象，或传 uiStateSnapshot 整份备份"), 400)
                book_norm = extract_address_book_from_doc({"addressBook": raw_book})
                if not book_norm:
                    return self._send_json(json_err("addressBook 中无有效「手机号→数组」项"), 400)
                ra = payload.get("replaceAll", payload.get("replace_all", False))
                if isinstance(ra, str):
                    replace_all = ra.strip().lower() in ("1", "true", "yes", "on")
                else:
                    replace_all = bool(ra)
                out = merge_address_book_into_ui_state(book_norm, replace_all=replace_all)
                return self._send_json(json_ok(out, "收货地址缓存已合并"))

            if path == "/api/ui/address_book/import_file":
                pth = str(payload.get("path", "") or "").strip()
                if not pth:
                    return self._send_json(json_err("path 必填：服务器本机上的备份 json 路径"), 400)
                ra = payload.get("replaceAll", False)
                if isinstance(ra, str):
                    replace_all = ra.strip().lower() in ("1", "true", "yes", "on")
                else:
                    replace_all = bool(ra)
                out = import_address_book_from_json_file(pth, replace_all=replace_all)
                if not out.get("ok"):
                    return self._send_json(json_err(str(out.get("error") or "导入失败"), detail=out), 400)
                return self._send_json(json_ok(out, "已从文件合并 addressBook"))

            if path == "/api/global/force_local_ip":
                raw = payload.get("enabled", True)
                if isinstance(raw, str):
                    en = raw.strip().lower() in ("1", "true", "yes", "on")
                else:
                    en = bool(raw)
                out = set_global_force_local_ip(en)
                if not out.get("ok"):
                    return self._send_json(
                        json_err(str(out.get("error") or "写入失败"), detail=out),
                        500,
                    )
                en2 = bool(out.get("enabled"))
                nclr = int(out.get("clearedProxyDirectMarkers") or 0)
                return self._send_json(
                    json_ok(
                        {
                            "enabled": en2,
                            "globalForceLocalIp": en2,
                            "clearedProxyDirectMarkers": nclr,
                        },
                        "已更新全局本机 IP",
                    )
                )

            if path == "/api/proxy_pool/save":
                lines = payload.get("lines")
                if isinstance(lines, str):
                    lines = lines.splitlines()
                if not isinstance(lines, list):
                    return self._send_json(json_err("lines 须为数组或多行字符串"), 400)
                target_phones = payload.get("phones")
                if target_phones is not None and not isinstance(target_phones, list):
                    return self._send_json(json_err("phones 须为数组或省略"), 400)
                out = _save_proxy_pool_lines([str(x) for x in lines])
                if not out.get("saved"):
                    return self._send_json(
                        json_err(out.get("message", "保存失败"), detail=out),
                        400,
                    )
                out["autoAssign"] = _apply_proxy_pool_auto_assign(
                    only_logged=True,
                    phones=target_phones if isinstance(target_phones, list) else None,
                )
                return self._send_json(json_ok(out, "代理池已保存"))

            if path == "/api/proxy_pool/shards_preview":
                only_logged_v = payload.get("onlyLogged", True) if isinstance(payload, dict) else True
                if isinstance(only_logged_v, str):
                    only_logged = only_logged_v.strip().lower() not in ("0", "false", "no", "off")
                else:
                    only_logged = bool(only_logged_v)
                target_phones = payload.get("phones") if isinstance(payload, dict) else None
                if target_phones is not None and not isinstance(target_phones, list):
                    return self._send_json(json_err("phones 须为数组或省略"), 400)
                return self._send_json(
                    json_ok(
                        _proxy_pool_shards_preview(
                            only_logged,
                            target_phones if isinstance(target_phones, list) else None,
                        )
                    )
                )

            if path == "/api/proxy_pool/clear":
                out = _clear_proxy_pool()
                target_phones = payload.get("phones")
                if target_phones is not None and not isinstance(target_phones, list):
                    return self._send_json(json_err("phones 须为数组或省略"), 400)
                out["autoAssign"] = _apply_proxy_pool_auto_assign(
                    only_logged=True,
                    phones=target_phones if isinstance(target_phones, list) else None,
                )
                return self._send_json(json_ok(out, "代理池已清空"))

            if path == "/api/proxy_pool/fill_start":
                try:
                    out = _proxy_fill_start(payload if isinstance(payload, dict) else {})
                except ValueError as ex:
                    return self._send_json(json_err(str(ex), detail=_proxy_fill_snapshot()), 400)
                if not out.get("ok"):
                    return self._send_json(json_err(str(out.get("error") or "补池任务启动失败"), detail=out), 409)
                return self._send_json(json_ok(out.get("status") or _proxy_fill_snapshot(), "代理池后台补池已启动"))

            if path == "/api/proxy_pool/fill_stop":
                return self._send_json(json_ok(_proxy_fill_stop(), "代理池后台补池已停止"))

            if path == "/api/pay_notices/export_disk":
                date_key = str(payload.get("dateKey") or "").strip()
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_key):
                    return self._send_json(json_err("dateKey 须为 YYYY-MM-DD（北京时间）"), 400)
                rows = payload.get("rows")
                if not isinstance(rows, list) or not rows:
                    return self._send_json(json_err("rows 须为非空数组"), 400)
                PAY_RECORD_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
                fn = f"pay_export_{date_key}.txt"
                if not re.fullmatch(r"pay_export_\d{4}-\d{2}-\d{2}\.txt", fn):
                    return self._send_json(json_err("文件名不合法"), 400)
                out_lines: list[str] = []
                for it in rows:
                    if not isinstance(it, dict):
                        continue
                    ph = str(it.get("phone") or "").strip()
                    oid = str(it.get("orderId") or "").strip()
                    res = str(it.get("result") or "").strip() or "未核对"
                    res = res.replace("\r", " ").replace("\n", " ").replace(";", "，").replace("；", "，")
                    ph = ph.replace(";", "").replace("；", "")
                    oid = oid.replace(";", "").replace("；", "")
                    if not ph or not oid:
                        continue
                    bottles = str(it.get("bottles") or "").strip().replace(";", "").replace("；", "")
                    addr_name = str(it.get("addrName") or "").strip().replace(";", "，").replace("；", "，")
                    addr_phone = str(it.get("addrPhone") or "").strip().replace(";", "").replace("；", "")
                    addr_full = str(it.get("addrFull") or "").strip().replace(";", "，").replace("；", "，")
                    # 与用户示例一致：手机号；订单号；数量；支付结果；值（英文分号+空格）
                    line = f"手机号; {ph}; 订单号; {oid}"
                    if bottles:
                        line += f"; 数量; {bottles}"
                    line += f"; 支付结果; {res}"
                    if addr_name or addr_full:
                        line += f"; 收货人; {addr_name}; 收货电话; {addr_phone}; 收货地址; {addr_full}"
                    out_lines.append(line)
                if not out_lines:
                    return self._send_json(json_err("无有效行（需手机号+订单号）"), 400)
                out_path = (PAY_RECORD_EXPORT_DIR / fn).resolve()
                try:
                    out_path.relative_to(PAY_RECORD_EXPORT_DIR.resolve())
                except ValueError:
                    return self._send_json(json_err("路径异常"), 400)
                text = "\ufeff" + "\n".join(out_lines) + "\n"
                out_path.write_text(text, encoding="utf-8", newline="\n")
                return self._send_json(
                    json_ok(
                        {
                            "saved": True,
                            "path": str(out_path),
                            "count": len(out_lines),
                            "dir": str(PAY_RECORD_EXPORT_DIR.resolve()),
                        },
                        "支付导出已写入抢购记录目录",
                    )
                )

            if path == "/api/operation_log/export_disk":
                date_key = str(payload.get("dateKey") or "").strip()
                time_key = str(payload.get("timeKey") or "").strip()
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_key):
                    return self._send_json(json_err("dateKey 须为 YYYY-MM-DD（北京时间）"), 400)
                if not re.fullmatch(r"\d{6}", time_key):
                    return self._send_json(json_err("timeKey 须为 HHMMSS（北京时间）"), 400)
                raw_lines = payload.get("lines")
                if not isinstance(raw_lines, list) or not raw_lines:
                    return self._send_json(json_err("lines 须为非空数组"), 400)
                if len(raw_lines) > 12000:
                    return self._send_json(json_err("lines 过多（上限 12000 行）"), 400)
                out_lines: list[str] = []
                for it in raw_lines:
                    s = str(it if it is not None else "")
                    s = s.replace("\r", " ").replace("\x00", " ")
                    if len(s) > 4000:
                        s = s[:4000] + "…"
                    out_lines.append(s)
                if not out_lines:
                    return self._send_json(json_err("无有效日志行"), 400)
                PAY_RECORD_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
                fn = f"op_log_{date_key}_{time_key}.txt"
                if not re.fullmatch(r"op_log_\d{4}-\d{2}-\d{2}_\d{6}\.txt", fn):
                    return self._send_json(json_err("文件名不合法"), 400)
                out_path = (PAY_RECORD_EXPORT_DIR / fn).resolve()
                try:
                    out_path.relative_to(PAY_RECORD_EXPORT_DIR.resolve())
                except ValueError:
                    return self._send_json(json_err("路径异常"), 400)
                text = "\ufeff" + "\n".join(out_lines) + "\n"
                out_path.write_text(text, encoding="utf-8", newline="\n")
                return self._send_json(
                    json_ok(
                        {
                            "saved": True,
                            "path": str(out_path),
                            "count": len(out_lines),
                            "dir": str(PAY_RECORD_EXPORT_DIR.resolve()),
                            "filename": fn,
                        },
                        "操作日志已写入抢购记录目录",
                    )
                )

            if path == "/api/accounts/export_disk":
                date_key = str(payload.get("dateKey") or "").strip()
                if not re.fullmatch(r"\d{8}", date_key):
                    return self._send_json(json_err("dateKey 须为 YYYYMMDD（北京时间）"), 400)
                phones = payload.get("phones")
                if not isinstance(phones, list) or not phones:
                    return self._send_json(json_err("phones 须为非空数组"), 400)
                ACCOUNT_RECORD_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
                fn = f"{date_key}账号记录.txt"
                if not re.fullmatch(r"\d{8}账号记录\.txt", fn):
                    return self._send_json(json_err("文件名不合法"), 400)
                out_lines: list[str] = []
                seen: set[str] = set()
                for it in phones:
                    ph = str(it or "").strip()
                    ph = ph.replace("\r", "").replace("\n", "").replace(";", "").replace("；", "")
                    if not ph or ph in seen:
                        continue
                    seen.add(ph)
                    out_lines.append(ph)
                if not out_lines:
                    return self._send_json(json_err("无有效手机号"), 400)
                out_path = (ACCOUNT_RECORD_EXPORT_DIR / fn).resolve()
                try:
                    out_path.relative_to(ACCOUNT_RECORD_EXPORT_DIR.resolve())
                except ValueError:
                    return self._send_json(json_err("路径异常"), 400)
                text = "\ufeff" + "\n".join(out_lines) + "\n"
                out_path.write_text(text, encoding="utf-8", newline="\n")
                return self._send_json(
                    json_ok(
                        {
                            "saved": True,
                            "path": str(out_path),
                            "count": len(out_lines),
                            "dir": str(ACCOUNT_RECORD_EXPORT_DIR.resolve()),
                            "filename": fn,
                        },
                        "账号导出已写入账号记录目录",
                    )
                )

            if path == "/api/proxy_pool/import_api":
                pulled = _fetch_proxy_lines_from_apis(payload if isinstance(payload, dict) else {})
                if not pulled.get("ok"):
                    return self._send_json(json_err(str(pulled.get("error") or "API 拉取失败"), detail=pulled), 400)
                lines = list(pulled.get("items") or [])
                if not lines:
                    return self._send_json(
                        json_err("API 未提取到有效代理行", detail=pulled),
                        400,
                    )
                pv = (payload or {}).get("previewOnly") if isinstance(payload, dict) else None
                if isinstance(pv, str):
                    preview_only = pv.strip().lower() in ("1", "true", "yes", "on")
                else:
                    preview_only = bool(pv)
                if preview_only:
                    return self._send_json(
                        json_ok(
                            {
                                "items": lines[:500],
                                "count": len(lines),
                                "import": {
                                    "sourceStatus": pulled.get("status"),
                                    "responseType": pulled.get("responseType"),
                                    "candidateCount": pulled.get("candidateCount"),
                                    "validCount": pulled.get("validCount"),
                                    "invalidCount": pulled.get("invalidCount"),
                                    "invalid": pulled.get("invalid"),
                                    "sourceCount": pulled.get("sourceCount"),
                                    "failedCount": pulled.get("failedCount"),
                                    "sources": pulled.get("sources"),
                                    "previewOnly": True,
                                },
                            },
                            "API 提取预览成功",
                        )
                    )
                out = _save_proxy_pool_lines(lines)
                if not out.get("saved"):
                    return self._send_json(
                        json_err(str(out.get("message") or "代理池保存失败"), detail={"pull": pulled, "save": out}),
                        400,
                    )
                target_phones = payload.get("phones") if isinstance(payload, dict) else None
                if target_phones is not None and not isinstance(target_phones, list):
                    return self._send_json(json_err("phones 须为数组或省略"), 400)
                merged = {
                    **out,
                    "import": {
                        "sourceStatus": pulled.get("status"),
                        "responseType": pulled.get("responseType"),
                        "candidateCount": pulled.get("candidateCount"),
                        "validCount": pulled.get("validCount"),
                        "invalidCount": pulled.get("invalidCount"),
                        "invalid": pulled.get("invalid"),
                        "sourceCount": pulled.get("sourceCount"),
                        "failedCount": pulled.get("failedCount"),
                        "sources": pulled.get("sources"),
                    },
                }
                merged["autoAssign"] = _apply_proxy_pool_auto_assign(
                    only_logged=True,
                    phones=target_phones if isinstance(target_phones, list) else None,
                )
                return self._send_json(json_ok(merged, "已从 API 导入并保存代理池"))

            if path == "/api/proxy_pool/verify":
                from proxy_pool import active_urls, line_to_proxy_url, verify_pool_urls

                lines = payload.get("lines")
                if isinstance(lines, str):
                    lines = lines.splitlines()
                urls: list[str] = []
                submitted_nonempty = 0
                if isinstance(lines, list) and lines:
                    seen: set[str] = set()
                    for raw in lines:
                        sraw = str(raw).strip()
                        if not sraw:
                            continue
                        submitted_nonempty += 1
                        try:
                            u = line_to_proxy_url(sraw)
                        except Exception:
                            u = None
                        if u and u not in seen:
                            seen.add(u)
                            urls.append(u)
                else:
                    urls = list(active_urls())
                if not urls:
                    if submitted_nonempty:
                        return self._send_json(
                            json_err(
                                "无有效代理行可检测：已提交 "
                                f"{submitted_nonempty} 行，但均无法解析为代理。"
                                "请用英文半角 `host:port`、`host/port/user/pass`、"
                                "或 `socks5://` / `socks5h://` / `http://` 完整 URL；"
                                "若刚升级程序，请完全退出后重启 WebUI 再试。",
                            ),
                            400,
                        )
                    return self._send_json(
                        json_err(
                            "无有效代理行可检测：当前文本框无内容且 "
                            f"{PROXY_POOL_FILE.name} 中也没有可解析行；"
                            "请填写后点「保存」或直接在框内编辑后点「自检连通」。",
                        ),
                        400,
                    )
                vres = verify_pool_urls(urls)
                all_ok = all(r.get("ok") for r in vres)
                return self._send_json(
                    json_ok(
                        {"allOk": all_ok, "results": vres, "count": len(urls)},
                        "自检完成" if all_ok else "部分线路未通过",
                    )
                )

            if path == "/grab/request-code":
                phone = str(payload.get("phone", "")).strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                POOL.add(phone, "")
                try:
                    res = POOL.send_code(phone)
                except Exception as ex:
                    traceback.print_exc()
                    return self._send_json(json_err(f"发码异常：{ex}"), 500)
                if not isinstance(res, dict):
                    return self._send_json(json_err("发码返回异常"), 500)
                if res.get("ok") is False and res.get("message"):
                    return self._send_json(json_err(str(res.get("message")), detail=res), 400)
                if _mt_business_ok(res.get("code")):
                    return self._send_json(json_ok(res, "验证码已请求"))
                msg = str(res.get("message") or "发码失败").strip()
                return self._send_json(json_err(msg, detail=res), 400)

            if path == "/grab/submit-code":
                phone = str(payload.get("phone", "")).strip()
                code = str(payload.get("code", "")).strip()
                nickname = str(
                    payload.get("nickname", "") or payload.get("remark", "") or ""
                ).strip()
                if not phone or not code:
                    return self._send_json(json_err("phone/code 不能为空"), 400)
                if not POOL.get(phone):
                    POOL.add(phone, "")
                res = POOL.login(phone, code, nickname=nickname or None)
                if not isinstance(res, dict):
                    return self._send_json(json_err("登录返回异常"), 500)
                if _mt_business_ok(res.get("code")):
                    e = POOL.get(phone)
                    return self._send_json(json_ok(
                        {
                            "result": res,
                            "account": e.to_dict() if e else {},
                        },
                        "登录成功",
                    ))
                msg = str(res.get("message") or "登录失败").strip()
                return self._send_json(json_err(msg, detail=res), 400)

            if path == "/grab/remove-account":
                phone = str(payload.get("phone", "")).strip()
                phone = _norm_phone(phone)
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                data = grab_remove_account(POOL, phone)
                return self._send_json(json_ok(
                    data,
                    "已删除" if data.get("removed") else "池中无此号（已清理抢购配置）",
                ))

            if path == "/grab/set-nickname":
                phone = str(payload.get("phone", "")).strip()
                nickname = str(
                    payload.get("nickname", "") or payload.get("remark", "")
                ).strip()
                data = grab_set_nickname(POOL, phone, nickname)
                if not data.get("ok"):
                    return self._send_json(
                        json_err(str(data.get("message") or "失败"), detail=data),
                        404,
                    )
                return self._send_json(json_ok(data, "备注已保存"))

            if path == "/grab/list-account-addresses":
                data = grab_list_account_addresses(POOL)
                return self._send_json(json_ok(data, "ok"))

            if path == "/grab/batch-refresh-login":
                phones = payload.get("phones")
                if phones is not None and not isinstance(phones, list):
                    return self._send_json(json_err("phones 须为数组或省略"), 400)
                try:
                    delay = float(payload.get("delay", 4.0))
                except (TypeError, ValueError):
                    delay = 4.0
                delay = max(2.0, delay)
                data = grab_batch_refresh_login(
                    POOL,
                    phones=phones,
                    delay=delay,
                )
                return self._send_json(json_ok(data))

            if path == "/grab/refresh-all-addresses":
                try:
                    gap_ms = float(payload.get("gapMs", 3000))
                except (TypeError, ValueError):
                    gap_ms = 3000.0
                data = grab_refresh_all_addresses(POOL, gap_ms=gap_ms)
                return self._send_json(json_ok(data))

            if path == "/grab/apply-item-741":
                raw_amt = payload.get("amount")
                amt = None
                if raw_amt is not None and raw_amt != "":
                    try:
                        amt = int(raw_amt)
                    except (TypeError, ValueError):
                        return self._send_json(json_err("amount 须为整数"), 400)
                data = grab_apply_item_741(POOL, amt)
                return self._send_json(json_ok(data))

            if path == "/grab/set-all-amt":
                try:
                    amount = int(payload.get("amount", 0))
                except (TypeError, ValueError):
                    return self._send_json(json_err("amount 必填且为整数"), 400)
                data = grab_set_all_amt(POOL, amount)
                return self._send_json(json_ok(data))

            if path == "/grab/set-all-delay":
                try:
                    delay_ms = int(payload.get("delayMs", payload.get("delay", -1)))
                except (TypeError, ValueError):
                    return self._send_json(json_err("delayMs 必填且为整数（毫秒）"), 400)
                data = grab_set_all_delay(POOL, delay_ms)
                return self._send_json(json_ok(data))

            if path == "/api/session/init":
                STATE.s = Session()
                STATE.addresses = []
                STATE.addr_index = 0
                return self._send_json(json_ok(STATE.snapshot(), "会话初始化成功"))

            if path == "/api/send_code":
                phone = str(payload.get("phone", "")).strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                s = STATE.ensure_session()
                res = s.send_code(phone)
                return self._send_json(json_ok(res))

            if path == "/api/login":
                phone = str(payload.get("phone", "")).strip()
                code = str(payload.get("code", "")).strip()
                if not phone or not code:
                    return self._send_json(json_err("phone/code 不能为空"), 400)
                s = STATE.ensure_session()
                res = s.login(phone, code)
                return self._send_json(json_ok({"result": res, "state": STATE.snapshot()}))

            if path == "/api/addresses":
                s = STATE.ensure_session()
                lst = s.addresses()
                STATE.addresses = lst or []
                if STATE.addr_index >= len(STATE.addresses):
                    STATE.addr_index = 0
                return self._send_json(json_ok({
                    "list": STATE.addresses,
                    "selectedIndex": STATE.addr_index,
                }))

            if path == "/api/address/select":
                idx = int(payload.get("index", 0))
                if not STATE.addresses:
                    return self._send_json(json_err("地址列表为空，请先获取地址"), 400)
                if idx < 0 or idx >= len(STATE.addresses):
                    return self._send_json(json_err("index 越界"), 400)
                STATE.addr_index = idx
                return self._send_json(json_ok({
                    "selectedIndex": idx,
                    "selectedAddress": STATE.current_addr(),
                }))

            if path == "/api/rush":
                s = STATE.ensure_session()
                item = str(payload.get("itemCode", "")).strip()
                act_id = str(payload.get("actId", "")).strip()
                spu_imtp = str(payload.get("spuId", "")).strip()
                hot = payload.get("hot", True)
                referer = str(payload.get("referer", "")).strip()
                amount = str(payload.get("amount", "1")).strip() or "1"
                source = str(payload.get("sourceId", "")).strip()
                if not item:
                    return self._send_json(json_err("商品 ID（itemCode）不能为空"), 400)
                branch = str(payload.get("branch", "")).strip().lower()
                need_code = bool(payload.get("needCode", False))
                prep = _rush_prepare_act(s, item, act_id, spu_imtp, referer, bool(hot))
                if not prep["ok"]:
                    return self._send_json(
                        json_err(prep["error"], detail=prep.get("detail")), 400
                    )
                act_id = prep["act_id"]
                spu_imtp = prep["spu_imtp"]
                referer = prep["referer"]
                pinfo = prep.get("pinfo")
                route_info = prep.get("route_info")
                if route_info:
                    if not branch:
                        branch = str(route_info.get("branch", "") or "").strip().lower()
                    if route_info.get("needCodeHint"):
                        need_code = bool(need_code or route_info.get("needCodeHint"))
                rla = _parse_rush_loop_attempts_from_payload(payload)
                kw = {}
                if rla is not None:
                    kw["rush_loop_attempts"] = rla
                kw["rush_loop_delay_ms"] = _parse_rush_loop_delay_ms_from_payload(
                    payload
                )
                if _rush_loop_burst_in_node_from_payload(payload):
                    kw["rush_loop_burst_in_node"] = True
                if _fire_and_forget_from_payload(payload):
                    kw["fire_and_forget"] = True
                _rr4 = _parse_rush_rounds_from_payload(payload)
                if _rr4 is not None:
                    kw["rush_rounds"] = _rr4
                _rg4 = _parse_rush_round_gap_ms_from_payload(payload)
                if _rg4 is not None:
                    kw["rush_round_gap_ms"] = _rg4
                _rj4 = _parse_rush_round_gap_jitter_ms_from_payload(payload)
                if _rj4 is not None:
                    kw["rush_round_gap_jitter_ms"] = _rj4
                kw.update(_rush_lane_kwargs_from_payload(payload))
                res = s.rush(
                    item=item,
                    act_id=act_id,
                    amount=amount,
                    src=source,
                    branch=branch,
                    referer=referer,
                    need_code=need_code,
                    **kw,
                )
                STATE.last_rush = res
                return self._send_json(json_ok({
                    "rush": res,
                    "resolvedActId": act_id,
                    "resolvedSpuId": spu_imtp or None,
                    "purchaseInfo": pinfo,
                    "autoResolvedRoute": route_info,
                }))

            if path == "/api/accounts/purchase_info":
                phone = str(payload.get("phone", "")).strip()
                spu_imtp = str(payload.get("spuId", "")).strip()
                hot = payload.get("hot", True)
                referer = str(payload.get("referer", "")).strip()
                if not phone or not spu_imtp:
                    return self._send_json(json_err("phone/spuId 不能为空"), 400)
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                s = e.ensure_session()
                aid, raw = _call_with_session_local_ip(
                    s,
                    s.fetch_priority_act_id,
                    spu_imtp,
                    hot=bool(hot),
                    referer=referer,
                )
                return self._send_json(json_ok({
                    "resolvedActId": aid,
                    "purchaseInfo": raw,
                    "localIpOnly": True,
                }))

            # 一键解析活动 ID（与抢购前 _rush_prepare_act 同源，仅 purchaseInfo 链路，不组包不发抢购）
            if path == "/api/accounts/resolve_act_id":
                phone = str(payload.get("phone", "")).strip()
                item = str(payload.get("itemCode", "")).strip()
                act_id = str(payload.get("actId", "")).strip()
                spu_imtp = str(payload.get("spuId", "")).strip()
                hot = payload.get("hot", True)
                referer = str(payload.get("referer", "")).strip()
                force = bool(payload.get("force", True))
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                if not item:
                    return self._send_json(json_err("商品 ID（itemCode）不能为空"), 400)
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                if force:
                    act_id = ""
                elif act_id:
                    return self._send_json(
                        json_ok(
                            {
                                "actId": act_id,
                                "spuId": spu_imtp or None,
                                "referer": referer or None,
                                "skipped": True,
                                "message": "已填写活动 ID 且 force=false，未请求 purchaseInfo",
                            },
                            "已存在活动 ID，跳过拉取",
                        )
                    )
                try:
                    s = e.ensure_session()
                    s._rush_full_pool_sequence = bool(getattr(e, "full_pool_rush", False))
                    prep = _call_with_session_local_ip(
                        s,
                        _rush_prepare_act,
                        s,
                        item,
                        act_id,
                        spu_imtp,
                        referer,
                        bool(hot),
                    )
                    if not prep.get("ok"):
                        return self._send_json(
                            json_err(str(prep.get("error") or "解析活动 ID 失败"), detail=prep.get("detail")),
                            400,
                        )
                    return self._send_json(
                        json_ok(
                            {
                                "actId": prep["act_id"],
                                "spuId": prep.get("spu_imtp") or None,
                                "referer": prep.get("referer") or None,
                                "purchaseInfo": prep.get("pinfo"),
                                "routeInfo": prep.get("route_info"),
                                "localIpOnly": True,
                            },
                            "活动 ID 已解析",
                        )
                    )
                except Exception as ex:
                    traceback.print_exc()
                    return self._send_json(
                        json_err("解析活动 ID 异常", detail=str(ex).strip() or type(ex).__name__),
                        500,
                    )

            if path == "/api/compose":
                phone = str(payload.get("phone", "")).strip()
                rec_id = str(payload.get("priorityRecordId", "")).strip()
                cnt = int(payload.get("count", 1) or 1)
                sku, _imtp = _resolve_order_sku_imtp(payload)
                if not phone:
                    return self._send_json(json_err("缺少 phone 参数"), 400)
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                s = e.ensure_session()
                addr = STATE.current_addr()
                if not rec_id or not sku:
                    return self._send_json(json_err(
                        "组单需要 priorityRecordId 与商品 ID：请传 itemCode（数字），"
                        "或 spuId 传数字；纯 IMTP 编号不能直接作为 compose 的 spuId"
                    ), 400)
                if not addr:
                    return self._send_json(json_err("尚未选择地址"), 400)
                s.force_order_chain_direct()
                res = s.compose(spu=sku, cnt=cnt, rec_id=rec_id, addr=addr)
                STATE.last_compose = res
                return self._send_json(json_ok(res))

            if path == "/api/submit":
                phone = str(payload.get("phone", "")).strip()
                rec_id = str(payload.get("priorityRecordId", "")).strip()
                cnt = int(payload.get("count", 1) or 1)
                sku, imtp_fb = _resolve_order_sku_imtp(payload)
                cdata = payload.get("composeData")
                if not isinstance(cdata, dict):
                    cdata = None
                shop_submit = str(
                    payload.get("composeShopId") or payload.get("shopId") or ""
                ).strip()
                if not phone:
                    return self._send_json(json_err("缺少 phone 参数"), 400)
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                s = e.ensure_session()
                addr = STATE.current_addr()
                if not rec_id or not sku:
                    return self._send_json(json_err(
                        "下单需要 priorityRecordId 与 itemCode（或数字 spuId）"
                    ), 400)
                if not addr:
                    return self._send_json(json_err("尚未选择地址"), 400)
                s.force_order_chain_direct()
                res = s.submit(
                    spu=sku,
                    cnt=cnt,
                    rec_id=rec_id,
                    addr=addr,
                    compose_data=cdata,
                    imtp_fallback=imtp_fb,
                    shop=shop_submit,
                )
                STATE.last_submit = res
                return self._send_json(json_ok(res))

            if path == "/api/pay":
                s = STATE.ensure_session()
                order_id = str(payload.get("orderId", "")).strip()
                pay_method = int(payload.get("payMethod", 0) or 0)
                channel = str(payload.get("channel", "70")).strip() or "70"
                if not order_id:
                    return self._send_json(json_err("orderId 不能为空"), 400)
                s.force_order_chain_direct()
                res = s.pay(order_id, method=pay_method, channel=channel)
                STATE.last_pay = res
                return self._send_json(json_ok(res))

            if path == "/api/gateway":
                s = STATE.ensure_session()
                tn = str(payload.get("tn", "")).strip()
                ch = str(payload.get("channel", "70")).strip() or "70"
                if not tn:
                    return self._send_json(json_err("tn 不能为空"), 400)
                s.force_order_chain_direct()
                res = s.gateway(tn, ch=ch)
                STATE.last_gateway = res
                try:
                    s._order_pay_egress_for_gateway = None
                except Exception:
                    pass
                return self._send_json(json_ok(res))

            if path == "/api/items/resolve_route":
                code = str(payload.get("itemCode", "")).strip()
                referer = str(payload.get("referer", "")).strip()
                out = _resolve_mt_item_route(code, referer)
                if not out.get("ok"):
                    return self._send_json(
                        json_err(out.get("message", "解析失败"), detail=out),
                        400,
                    )
                return self._send_json(json_ok(out))

            if path == "/api/items/collect":
                s = STATE.ensure_session()
                found = set()
                raw_text = str(payload.get("rawText", "")).strip()
                _extract_from_text(raw_text, found)

                # 优先尝试从已知热门分支接口中采集 itemCode/actId
                for ep in Session.RUSH_ENDPOINTS:
                    try:
                        url = f"{H5_HOST}{ep}"
                        body = {"pageNum": 1, "pageSize": 30}
                        hdr = s._h5_hdr(body, rush=False)
                        resp = s._req_post(url, headers=hdr, json=body)
                        data = resp.json() if resp and resp.text else {}
                        _extract_item_pairs(data, found)
                    except Exception:
                        continue

                # 从最近一次抢购结果里再补采一次
                _extract_item_pairs(STATE.last_rush, found)

                items = [{"itemCode": c, "actId": a, "title": f"{c} / {a}"} for c, a in sorted(found)]
                return self._send_json(json_ok({"items": items, "count": len(items)}))

            # ══════════════════════════════════════════
            #  多账号管理 API
            # ══════════════════════════════════════════

            # 获取全部账号列表 + 统计
            if path == "/api/accounts/list":
                return self._send_json(json_ok({
                    "accounts": POOL.snapshot(),
                    "stats": POOL.stats(),
                }))

            # 从磁盘重新读取 accounts.json（外部机器人上号后同步列表）
            if path == "/api/accounts/reload_disk":
                out = POOL.reload_from_disk()
                if not out.get("ok"):
                    return self._send_json(
                        json_err(str(out.get("error") or "重载失败"), detail=out),
                        500,
                    )
                return self._send_json(
                    json_ok(
                        {"count": out.get("count"), "stats": out.get("stats")},
                        "已从磁盘重新加载账号",
                    ),
                )

            # 添加单个账号  {phone, nickname?}
            if path == "/api/accounts/add":
                phone = str(payload.get("phone", "")).strip()
                nickname = str(payload.get("nickname", "")).strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                e = POOL.add(phone, nickname)
                return self._send_json(json_ok(e.to_dict(), "添加成功"))

            if path == "/api/accounts/set_nickname":
                phone = str(payload.get("phone", "")).strip()
                nickname = str(payload.get("nickname", "") or "").strip()
                pn = _norm_phone(phone)
                if not pn:
                    return self._send_json(json_err("phone 不能为空"), 400)
                if "remark" in payload:
                    ok = POOL.set_nickname(
                        phone,
                        nickname,
                        remark=str(payload.get("remark", "") or "").strip(),
                    )
                else:
                    ok = POOL.set_nickname(phone, nickname)
                if not ok:
                    return self._send_json(json_err("账号不存在"), 404)
                e = POOL.get(pn)
                return self._send_json(
                    json_ok(e.to_dict() if e else {}, "备注已保存"),
                )

            # 旧 BB 模式接口保留给前端兼容；运行时已固定使用 shield.py 的 py 稳定版链路。
            if path == "/api/accounts/bb_mode":
                phones = payload.get("phones")
                if phones is None:
                    phones = [payload.get("phone", "")]
                if not isinstance(phones, list):
                    return self._send_json(json_err("phones 须为数组或传 phone"), 400)
                updated = []
                missing = []
                for p in phones:
                    pn = _norm_phone(p)
                    if not pn:
                        continue
                    e = POOL.get(pn)
                    if not e:
                        missing.append(pn)
                        continue
                    e.use_legacy_bb = False
                    updated.append(pn)
                POOL.save()
                return self._send_json(
                    json_ok(
                        {
                            "updated": updated,
                            "missing": missing,
                            "use_legacy_bb": False,
                            "bb_path": f"stable_py_{BB_SIGN_WASM_VERSION}",
                            "count": len(updated),
                        },
                        f"BB 已固定为 py 稳定版链路，影响 {len(updated)} 个账号",
                    )
                )

            # 旧一键切换接口保留给前端兼容；只清理旧字段，不再切分支。
            if path == "/api/accounts/bb_mode/all":
                count = 0
                for e in POOL.all():
                    e.use_legacy_bb = False
                    count += 1
                POOL.save()
                return self._send_json(
                    json_ok(
                        {"use_legacy_bb": False, "bb_path": f"stable_py_{BB_SIGN_WASM_VERSION}", "count": count},
                        f"全部 {count} 个账号已固定为 py 稳定版 BB 链路",
                    )
                )

            if path == "/api/accounts/wasm_bb_mode":
                phones = payload.get("phones")
                if phones is None:
                    phones = [payload.get("phone", "")]
                if not isinstance(phones, list):
                    return self._send_json(json_err("phones 须为数组或传 phone"), 400)
                enabled = bool(payload.get("use_wasm_bb", payload.get("useWasmBb", payload.get("enabled", False))))
                updated = []
                missing = []
                for p in phones:
                    pn = _norm_phone(p)
                    if not pn:
                        continue
                    e = POOL.get(pn)
                    if not e:
                        missing.append(pn)
                        continue
                    e.use_wasm_bb = enabled
                    e.use_legacy_bb = False
                    e._session = None
                    e._session_proxy_src = None
                    updated.append(pn)
                POOL.save()
                bb_path = (
                    f"shield_wasm_en_{BB_SIGN_WASM_VERSION}"
                    if enabled
                    else f"shield_sm4_{BB_SIGN_WASM_VERSION}"
                )
                return self._send_json(
                    json_ok(
                        {
                            "updated": updated,
                            "missing": missing,
                            "use_wasm_bb": enabled,
                            "use_legacy_bb": False,
                            "bb_path": bb_path,
                            "count": len(updated),
                        },
                        f"wasm BB 已{'开启' if enabled else '关闭'}，影响 {len(updated)} 个账号",
                    )
                )

            if path == "/api/accounts/wasm_bb_mode/all":
                enabled = bool(payload.get("use_wasm_bb", payload.get("useWasmBb", payload.get("enabled", False))))
                count = 0
                for e in POOL.all():
                    e.use_wasm_bb = enabled
                    e.use_legacy_bb = False
                    e._session = None
                    e._session_proxy_src = None
                    count += 1
                POOL.save()
                bb_path = (
                    f"shield_wasm_en_{BB_SIGN_WASM_VERSION}"
                    if enabled
                    else f"shield_sm4_{BB_SIGN_WASM_VERSION}"
                )
                return self._send_json(
                    json_ok(
                        {
                            "use_wasm_bb": enabled,
                            "use_legacy_bb": False,
                            "bb_path": bb_path,
                            "count": count,
                        },
                        f"全部 {count} 个账号已{'开启' if enabled else '关闭'} wasm BB",
                    )
                )

            if path == "/api/accounts/mobile_relay_mode":
                phones = payload.get("phones")
                if phones is None:
                    phones = [payload.get("phone", "")]
                if not isinstance(phones, list):
                    return self._send_json(json_err("phones 须为数组或传 phone"), 400)
                enabled = bool(payload.get("use_mobile_relay", payload.get("useMobileRelay", payload.get("enabled", False))))
                device_id = str(
                    payload.get("mobile_relay_device", payload.get("mobileRelayDevice", payload.get("deviceId", ""))) or ""
                ).strip()
                has_device_field = any(k in payload for k in ("mobile_relay_device", "mobileRelayDevice", "deviceId"))
                updated = []
                missing = []
                for p in phones:
                    pn = _norm_phone(p)
                    if not pn:
                        continue
                    e = POOL.get(pn)
                    if not e:
                        missing.append(pn)
                        continue
                    e.use_mobile_relay = enabled
                    if has_device_field:
                        e.mobile_relay_device = device_id
                    e._session = None
                    e._session_proxy_src = None
                    updated.append(pn)
                POOL.save()
                return self._send_json(
                    json_ok(
                        {
                            "updated": updated,
                            "missing": missing,
                            "use_mobile_relay": enabled,
                            "mobile_relay_device": device_id,
                            "count": len(updated),
                            "relay": mobile_relay.snapshot(),
                        },
                        f"手机发包已{'开启' if enabled else '关闭'}，影响 {len(updated)} 个账号",
                    )
                )

            # 停止所有抢购：设全局 abort 标志 + kill go_bridge 连接
            if path == "/api/accounts/rush_abort":
                global _rush_global_abort_flag
                _rush_global_abort_flag = True
                _rush_global_abort_event.set()
                # kill 当前 rush_bridge 进程（断开所有 TCP，go_bridge 内部 goroutine 自动停）
                try:
                    import subprocess as _sp
                    if os.name == "nt":
                        _sp.run(["taskkill", "/F", "/IM", "rush_bridge.exe"],
                                capture_output=True, timeout=5)
                    else:
                        _sp.run(["pkill", "-f", "rush_bridge"], capture_output=True, timeout=5)
                except Exception:
                    pass
                try:
                    _reset_go_bridge_boot_state()
                except Exception:
                    pass
                try:
                    dist_stop = _dist_stop_all_jobs()
                except Exception as ex:
                    dist_stop = {"error": str(ex)}
                schedule_bridge_rewarm_after_stop("local_rush_abort")
                # 短暂延迟后重置 flag（让正在跑的 Python 循环检测到 abort 后退出）
                def _reset_abort():
                    import time as _t
                    _t.sleep(10)
                    global _rush_global_abort_flag
                    _rush_global_abort_flag = False
                    _rush_global_abort_event.clear()
                threading.Thread(target=_reset_abort, daemon=True).start()
                return self._send_json(json_ok({"aborted": True, "distributed": dist_stop}, "已发送停止信号"))

            if path == "/api/distributed/stop":
                out = _dist_stop_all_jobs()
                schedule_bridge_rewarm_after_stop("distributed_stop_master")
                return self._send_json(json_ok(out, "已通知分布式节点停止"))

            if path == "/api/distributed/config":
                node_id = str(payload.get("nodeId") or "").strip()
                if not node_id:
                    return self._send_json(json_err("nodeId 不能为空"), 400)
                raw_c = payload.get("concurrency")
                try:
                    concurrency = int(raw_c) if raw_c is not None and str(raw_c).strip() != "" else None
                except (TypeError, ValueError):
                    return self._send_json(json_err("concurrency 必须是数字"), 400)
                try:
                    cfg = _dist_set_node_config(node_id, concurrency=concurrency)
                except Exception as ex:
                    return self._send_json(json_err("保存节点配置失败", detail=str(ex)), 500)
                return self._send_json(json_ok({"nodeId": node_id, "config": cfg}))

            # 批量添加账号  {phones: ["138...", ...]}
            if path == "/api/accounts/add_batch":
                phones = payload.get("phones", [])
                if not isinstance(phones, list):
                    return self._send_json(json_err("phones 须为数组"), 400)
                entries = POOL.add_batch(phones)
                return self._send_json(json_ok({
                    "added": len(entries),
                    "stats": POOL.stats(),
                }))

            # 智能导入 MTM 账号包 {text, overwrite?}
            if path == "/api/accounts/import_mtm":
                text = payload.get("text", payload.get("mtm", ""))
                if not str(text or "").strip():
                    return self._send_json(json_err("MTM 内容不能为空"), 400)
                try:
                    data = POOL.import_mtm_text(text, overwrite=_json_truthy(payload.get("overwrite")))
                except Exception as ex:
                    return self._send_json(json_err("MTM 导入失败", detail=str(ex)), 400)
                if not data.get("valid"):
                    return self._send_json(json_err("未识别到可导入的 MTM 账号", detail=data), 400)
                msg = (
                    f"MTM导入：新增 {data.get('added', 0)}，覆盖 {data.get('updated', 0)}，"
                    f"跳过 {data.get('skipped', 0)}，无效 {data.get('invalid', 0)}"
                )
                return self._send_json(json_ok(data, msg))

            # 删除账号 {phone}（与 /grab/remove-account 一致：同步 ui_state.json）
            if path == "/api/accounts/remove":
                phone = str(payload.get("phone", "")).strip()
                data = grab_remove_account(POOL, phone)
                return self._send_json(
                    json_ok(
                        {
                            "removed": bool(data.get("removed")),
                            "stats": data.get("stats"),
                            "phone": data.get("phone") or phone,
                        }
                    )
                )

            # 批量删除  {phones: ["138...", ...]} 单次落盘 + 清 ui_state
            if path == "/api/accounts/remove_batch":
                phones = payload.get("phones", [])
                if not isinstance(phones, list):
                    return self._send_json(json_err("phones 须为数组"), 400)
                data = grab_remove_accounts_batch(POOL, phones)
                return self._send_json(json_ok(data))

            # 注销账号（茅台 /xhr/front/user/register/cancel）{phone}
            if path == "/api/accounts/cancel":
                phone = str(payload.get("phone", "")).strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                acct = POOL.get(phone)
                if not acct:
                    return self._send_json(json_err("账号不存在"), 404)
                sess = acct.ensure_session()
                res = sess.cancel_account()
                return self._send_json(json_ok(res) if res.get("code") in (2000, 200) else json_err(res.get("message") or "注销失败", detail=res))

            # 重置设备指纹与登录态（修复 4011 device id inconsistency）{phone}
            if path == "/api/accounts/reset_device":
                phone = str(payload.get("phone", "")).strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                ok = POOL.reset_device_identity(phone)
                if not ok:
                    return self._send_json(json_err("账号不存在"), 404)
                return self._send_json(json_ok({"reset": True, "stats": POOL.stats()}))

            # 换新模拟 App 全量指纹并清除登录态（与下次登录 Token 对齐）；保留备注、代理 {phone}
            if path == "/api/accounts/refresh_device_params":
                phone = str(payload.get("phone", "")).strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                ok = POOL.refresh_simulated_device_params_only(phone)
                if not ok:
                    return self._send_json(json_err("账号不存在"), 404)
                return self._send_json(json_ok(
                    {"ok": True, "stats": POOL.stats()},
                    "已换新设备指纹并清除登录态，请对该号发码重新登录",
                ))

            # 批量：换新模拟指纹并清除登录态 {phones?: [...] 省略=全部账号, delay?: 秒}
            if path == "/api/accounts/batch_refresh_device_params":
                phones = payload.get("phones")
                if phones is not None and not isinstance(phones, list):
                    return self._send_json(json_err("phones 须为数组或省略"), 400)
                try:
                    delay = float(payload.get("delay", 0.05) or 0)
                except (TypeError, ValueError):
                    delay = 0.05
                results = POOL.batch_refresh_simulated_device_params_only(
                    phones=[str(p).strip() for p in phones] if phones else None,
                    delay=max(0.0, delay),
                )
                return self._send_json(json_ok({"results": results, "stats": POOL.stats()}))

            # 兼容旧入口：单账号代理已禁用，统一走代理池轮换（仅用于清理遗留配置）{phone, proxy}
            if path == "/api/accounts/set_proxy":
                phone = str(payload.get("phone", "")).strip()
                proxy = str(payload.get("proxy", "")).strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                if not POOL.set_proxy(phone, proxy):
                    return self._send_json(json_err("账号不存在"), 404)
                return self._send_json(json_ok({
                    "phone": phone,
                    "proxy": "",
                    "proxyMode": "pool",
                    "note": "单账号代理已禁用，统一使用 local_proxy_pool.txt 轮换",
                    "stats": POOL.stats(),
                }))

            # 单账号代理分片策略（自动/手动条数/全池）{phone, mode:auto|count|all, count?}
            if path == "/api/accounts/set_proxy_shard":
                phone = str(payload.get("phone", "")).strip()
                mode = str(payload.get("mode", "auto") or "auto").strip().lower()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                if mode not in ("auto", "count", "all"):
                    return self._send_json(json_err("mode 仅支持 auto/count/all"), 400)
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                cnt = 0
                if mode == "count":
                    try:
                        cnt = max(1, int(payload.get("count", 1)))
                    except (TypeError, ValueError):
                        cnt = 1
                # group_phones: 分组内账号列表，用于分片计算；不传则用全量
                raw_gp = payload.get("group_phones")
                if isinstance(raw_gp, list) and raw_gp:
                    from accounts import _norm_phone as _np
                    e.proxy_shard_group = [_np(p) for p in raw_gp if _np(p)]
                else:
                    e.proxy_shard_group = []
                e.proxy_shard_mode = mode
                e.proxy_shard_count = cnt
                e._session = None
                e._session_proxy_src = None
                POOL.save()
                return self._send_json(
                    json_ok(
                        {
                            "phone": e.phone,
                            "proxy_shard_mode": e.proxy_shard_mode,
                            "proxy_shard_count": e.proxy_shard_count,
                            "account": e.to_dict(),
                            "stats": POOL.stats(),
                        },
                        "代理分片策略已保存",
                    )
                )

            # 单账号强制本机直连开关（优先级高于代理池分片）
            if path == "/api/accounts/force_direct":
                phone = str(payload.get("phone", "")).strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                enabled = bool(payload.get("enabled", True))
                e.proxy = "direct" if enabled else ""
                e._session = None
                e._session_proxy_src = None
                POOL.save()
                return self._send_json(
                    json_ok(
                        {
                            "phone": e.phone,
                            "directForced": enabled,
                            "proxy": e.proxy,
                            "account": e.to_dict(),
                            "stats": POOL.stats(),
                        },
                        "已切换本机直连开关",
                    )
                )

            # 探测当前账号 Session 的公网出口 IP（验证代理是否生效）{phone?: 省略=仅环境变量/直连}
            if path == "/api/accounts/egress_probe":
                phone = str(payload.get("phone", "")).strip()
                if phone:
                    e = POOL.get(phone)
                    if not e:
                        return self._send_json(json_err("账号不存在"), 404)
                    s = e.ensure_session()
                else:
                    s = Session()
                probe = s.fetch_public_ip()
                return self._send_json(json_ok({
                    "probe": probe,
                    "proxyUsed": bool(s._proxy_url),
                }))

            # 浩天 BS-DVID 完整流程（Node bs-dvid.js）{phone}
            if path == "/api/accounts/fetch_bs_dvid":
                return self._send_json(
                    json_err("已禁用手动刷新浩天：当前仅在发码前自动获取并在后续流程复用"),
                    400,
                )

            # 批量浩天 BS-DVID：{phones?: [...] 省略=全部已登录, delay?: 秒}
            if path == "/api/accounts/batch_fetch_bs_dvid":
                return self._send_json(
                    json_err("已禁用批量刷新浩天：当前仅在发码前自动获取并在后续流程复用"),
                    400,
                )

            # 仅补全「已登录但缺 bsid」的号（不刷新已有 BS-DVID）
            if path == "/api/accounts/batch_ensure_bs_dvid_missing":
                phones = payload.get("phones")
                if phones is not None and not isinstance(phones, list):
                    return self._send_json(json_err("phones 须为数组或省略"), 400)
                try:
                    delay = float(payload.get("delay", 2.0))
                except (TypeError, ValueError):
                    delay = 2.0
                data = POOL.batch_ensure_bs_dvid_missing(
                    phones=phones if isinstance(phones, list) else None,
                    delay=delay,
                )
                return self._send_json(
                    json_ok(data, "已处理缺少 BS-DVID 的账号（详见 picked / results）")
                )

            # 一键梆梆暖场上报：每账号用各自 _proxy_url 并发 POST /bbprbdata/upload
            # 与 maotai-seckill-share2 2/maotai-seckill.js warmupBangcleRiskFlow_1_9_6 对齐
            if path == "/api/accounts/batch_warmup_bb":
                phones = payload.get("phones")
                if phones is not None and not isinstance(phones, list):
                    return self._send_json(json_err("phones 须为数组或省略"), 400)
                try:
                    force = bool(payload.get("force", False))
                except Exception:
                    force = False
                try:
                    ttl_ms = int(payload.get("ttl_ms", 60_000))
                except (TypeError, ValueError):
                    ttl_ms = 60_000
                try:
                    max_workers = int(payload.get("max_workers", 64))
                except (TypeError, ValueError):
                    max_workers = 64
                page_url = str(payload.get("page_url") or "https://h5.moutai519.com.cn/").strip()
                data = POOL.batch_warmup_bb(
                    phones=phones if isinstance(phones, list) else None,
                    force=force,
                    ttl_ms=ttl_ms,
                    max_workers=max_workers,
                    page_url=page_url,
                )
                return self._send_json(json_ok(data, "暖场已完成（详见 results）"))

            # 暖场状态轻量查询（前端倒计时用）
            if path == "/api/accounts/warmup_bb_status":
                now_ms = int(time.time() * 1000)
                rows = []
                for ph in POOL._phones:
                    e = POOL._map.get(ph)
                    if not e:
                        continue
                    warm_at = int(getattr(e, "bb_warmup_at", 0) or 0)
                    age = max(0, now_ms - warm_at) if warm_at else None
                    remaining = max(0, 60_000 - age) if age is not None else 0
                    rows.append({
                        "phone": ph,
                        "logged_in": (e.status == STATUS_OK),
                        "age_ms": age,
                        "ttl_remaining_ms": remaining,
                        "fresh": (age is not None and age < 60_000),
                        "serial": int(getattr(e, "bb_serial", 1) or 1),
                    })
                return self._send_json(json_ok({"rows": rows, "now_ms": now_ms}))

            # 全部已登录账号「刷新登录」：仅 ship/query 校验（JWT 与设备 ID 对齐，不换机模）
            if path == "/api/accounts/batch_refresh_login":
                phones = payload.get("phones")
                if phones is not None and not isinstance(phones, list):
                    return self._send_json(json_err("phones 须为数组或省略"), 400)
                try:
                    delay = float(payload.get("delay", 4.0))
                except (TypeError, ValueError):
                    delay = 4.0
                delay = max(2.0, delay)
                results = POOL.batch_refresh_login(phones=phones, delay=delay)
                return self._send_json(json_ok({"results": results, "stats": POOL.stats()}))

            # 单账号发验证码  {phone}
            if path == "/api/accounts/send_code":
                if not _remote_login_api_authorized(self, payload):
                    return self._send_json(json_err("远程上号口令无效或缺失"), 403)
                phone = str(payload.get("phone", "")).strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                defer_ob = _json_truthy(payload.get("deferPoolPersistUntilOnboard"))
                if defer_ob:
                    POOL.begin_deferred_onboarding(phone)
                try:
                    if not POOL.get(phone):
                        nick0 = str(payload.get("nickname", "") or "").strip()
                        remark0 = str(payload.get("remark", "") or "").strip()
                        POOL.add(phone, nick0 or phone, remark0)
                    # ── 远程上号 IP 一致性：发码时锁定一条代理 IP，后续 login/实名/地址/测黑均沿用同一出口 ──
                    try:
                        import proxy_pool as _pp

                        if _pp.pool_is_configured():
                            _locked_proxy = _pp.pick_proxy_url_for_key(phone)
                            _e = POOL.get(phone)
                            if _e and _locked_proxy:
                                _e.proxy = _locked_proxy
                                POOL.save()
                    except Exception:
                        pass
                    res = POOL.send_code(phone)
                except Exception as ex:
                    traceback.print_exc()
                    if defer_ob:
                        _remote_onboard_defer_teardown_after_send_code_fail(phone)
                    # 与 i茅台业务 JSON 同形，避免落在外层「服务异常」且前端可展示 message
                    return self._send_json(
                        json_ok(
                            {
                                "code": -1,
                                "message": f"发码异常：{ex}",
                            }
                        )
                    )
                if defer_ob and isinstance(res, dict):
                    try:
                        _oc = int(res.get("code", -1))
                    except (TypeError, ValueError):
                        _oc = -1
                    if _oc != 2000:
                        _remote_onboard_defer_teardown_after_send_code_fail(phone)
                return self._send_json(json_ok(res))

            # 远程新户：异常中断时解除「延迟落盘」以免 accounts.json 长时间无法写入
            if path == "/api/accounts/remote_onboard_deferred_flush":
                if not _remote_login_api_authorized(self, payload):
                    return self._send_json(json_err("远程上号口令无效或缺失"), 403)
                phone = str(payload.get("phone", "")).strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                try:
                    POOL.end_deferred_onboarding(phone)
                    POOL.save()
                except Exception as ex:
                    traceback.print_exc()
                    return self._send_json(json_err("flush 失败", detail=str(ex)), 500)
                return self._send_json(json_ok({"flushed": True}))

            # 企业微信：webhook 群机器人 或 aibot 智能机器人(Bot ID + Secret 长连接)
            if path == "/api/wechat/config":
                st = wechat_notify.update_config(
                    enabled=bool(payload.get("enabled", False)),
                    mode=str(payload.get("mode", "webhook") or "webhook").strip(),
                    webhook=str(payload.get("webhook", "")).strip(),
                    bot_id=str(payload.get("bot_id", "")).strip(),
                    secret=payload.get("secret"),
                    notify_chatid=str(payload.get("notify_chatid", "")).strip(),
                )
                return self._send_json(json_ok(st))

            if path == "/api/wechat/status":
                return self._send_json(json_ok(wechat_notify.get_status()))

            if path == "/api/wechat/test":
                return self._send_json(json_ok(wechat_notify.send_test_msg()))

            # 企业微信机器人回调入口（把群消息转为发码/登录命令）
            if path == "/api/wechat/callback":
                out = wechat_notify.handle_callback(payload, POOL)
                return self._send_json(json_ok(out))

            # 批量发验证码  {phones?: [...], delay?: 0.5}
            if path == "/api/accounts/batch_send_code":
                phones = payload.get("phones") or None   # None = 全部
                delay  = float(payload.get("delay", 0.5))
                results = POOL.batch_send_code(phones, delay=delay)
                return self._send_json(json_ok({
                    "results": results,
                    "stats": POOL.stats(),
                }))

            # 单账号登录  {phone, code}
            if path == "/api/accounts/login":
                phone = str(payload.get("phone", "") or "").strip()
                defer_login_ob = _json_truthy(payload.get("deferPoolPersistUntilOnboard"))
                if not _remote_login_api_authorized(self, payload):
                    if defer_login_ob and phone:
                        try:
                            POOL.end_deferred_onboarding(phone)
                            POOL.save()
                        except Exception:
                            pass
                    return self._send_json(json_err("远程上号口令无效或缺失"), 403)
                code = str(payload.get("code", "") or "").strip()
                nickname = str(payload.get("nickname", "") or "").strip()
                remark = str(payload.get("remark", "") or "").strip()
                if not phone or not code:
                    if defer_login_ob:
                        try:
                            POOL.end_deferred_onboarding(phone)
                            POOL.save()
                        except Exception:
                            pass
                    return self._send_json(json_err("phone/code 不能为空"), 400)
                if not POOL.get(phone):
                    POOL.add(phone, nickname or phone, remark)
                res = POOL.login(
                    phone,
                    code,
                    nickname=nickname or None,
                    remark=remark or None,
                )
                e = POOL.get(phone)
                # 同备注 → 同标签分组：远程上号成功且写了备注时，并入 ui_state 同名分组
                try:
                    rem_tag = str((e.remark if e else "") or remark or "").strip()
                    login_ok = False
                    if isinstance(res, dict):
                        try:
                            login_ok = int(res.get("code", -1)) == 2000
                        except (TypeError, ValueError):
                            login_ok = str(res.get("code", "")) == "2000"
                    if login_ok and rem_tag and phone:
                        add_phones_to_account_group(rem_tag, phone)
                except Exception:
                    traceback.print_exc()
                # ── 远程上号 IP 一致性：登录成功后不清除 e.proxy ──
                # send_code 时已将 e.proxy 锁定为某条代理 IP。
                # 登录后前端还会依次请求 realname/address/remote_black_check。
                # 这些步骤调用 ensure_session() 时仍需沿用同一 IP，所以此处不清除。
                # 等 remote_black_check 完成后再清除 e.proxy，恢复抢购的代理池分片逻辑。
                return self._send_json(json_ok({
                    "result": res,
                    "account": e.to_dict() if e else {},
                    "haotianScheduled": False,
                }))

            # 批量登录  {code_map: {"138...": "123456", ...}}
            if path == "/api/accounts/batch_login":
                code_map = payload.get("code_map", {})
                if not isinstance(code_map, dict):
                    return self._send_json(json_err("code_map 须为对象"), 400)
                results = POOL.batch_login(code_map)
                return self._send_json(json_ok({
                    "results": results,
                    "stats": POOL.stats(),
                }))

            # 批量黑号检测  {phones?: [], maxRetries?, delay?} 不传 phones 则检测全部已登录
            if path == "/api/accounts/batch_black_check":
                phones_raw = payload.get("phones")
                phones = None
                if phones_raw is not None:
                    if not isinstance(phones_raw, list):
                        return self._send_json(json_err("phones 须为数组"), 400)
                    phones = [str(p).strip() for p in phones_raw if str(p).strip()]
                max_retries = payload.get("maxRetries", 50)
                delay = payload.get("delay", 2.0)
                try:
                    results = POOL.batch_black_check(
                        phones=phones,
                        max_retries=max_retries,
                        delay=delay,
                    )
                except Exception as ex:
                    traceback.print_exc()
                    return self._send_json(
                        json_err("批量黑号检测异常", detail=str(ex)),
                        500,
                    )
                ok_n = sum(
                    1
                    for r in results
                    if isinstance(r, dict) and not r.get("skipped") and r.get("risk_tag") == "normal"
                )
                bl_n = sum(
                    1
                    for r in results
                    if isinstance(r, dict)
                    and not r.get("skipped")
                    and r.get("risk_tag") in ("blacklisted", "possibly_blacklisted",
                                               "token_expired", "probe_error")
                )
                return self._send_json(json_ok({
                    "results": results,
                    "stats": POOL.stats(),
                    "summary": {"total": len(results), "normal": ok_n, "issue": bl_n},
                }))

            # 开抢前核实 BS-DVID + 商品参数（与 rush 同源解析，不发抢购 POST）
            if path == "/api/accounts/verify_rush_readiness":
                tasks_raw = payload.get("tasks")
                tasks: list[dict] = []
                if tasks_raw is not None:
                    if not isinstance(tasks_raw, list):
                        return self._send_json(json_err("tasks 须为数组"), 400)
                    for row in tasks_raw:
                        if not isinstance(row, dict):
                            continue
                        ph = str(row.get("phone") or "").strip()
                        if not ph:
                            continue
                        tasks.append(dict(row))
                else:
                    phones_raw = payload.get("phones")
                    if phones_raw is not None:
                        if not isinstance(phones_raw, list):
                            return self._send_json(json_err("phones 须为数组"), 400)
                        phone_list = [str(p).strip() for p in phones_raw if str(p).strip()]
                    else:
                        phone_list = [
                            p
                            for p in POOL._phones
                            if POOL.get(p) and POOL.get(p).status == STATUS_OK
                        ]
                    base = {
                        k: payload.get(k)
                        for k in (
                            "itemCode",
                            "spuId",
                            "actId",
                            "referer",
                            "branch",
                            "hot",
                            "needCode",
                        )
                        if k in payload
                    }
                    for ph in phone_list:
                        tasks.append({"phone": ph, **base})
                if not tasks:
                    return self._send_json(json_err("无待核实任务（需已登录账号或 tasks）"), 400)
                try:
                    delay = float(payload.get("delay", 0.35))
                except (TypeError, ValueError):
                    delay = 0.35
                delay = max(0.0, min(10.0, delay))
                results: list[dict] = []
                for i, task in enumerate(tasks):
                    results.append(_verify_one_account_rush_readiness(task))
                    if i < len(tasks) - 1 and delay > 0:
                        time.sleep(delay)
                ok_n = sum(1 for r in results if r.get("ok"))
                warn_n = sum(
                    1
                    for r in results
                    if r.get("ok") and (r.get("warnings") or [])
                )
                return self._send_json(
                    json_ok(
                        {
                            "results": results,
                            "summary": {
                                "total": len(results),
                                "ok": ok_n,
                                "failed": len(results) - ok_n,
                                "okWithWarnings": warn_n,
                            },
                        },
                        "核实完成",
                    )
                )

            # 远程上号页专用：单账号黑号检测（须远程口令；与 batch_black_check 规则相同）
            if path == "/api/accounts/remote_black_check":
                if not _remote_login_api_authorized(self, payload):
                    return self._send_json(json_err("远程上号口令无效或缺失"), 403)
                phone = str(payload.get("phone", "")).strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                max_retries = payload.get("maxRetries", 50)
                try:
                    results = POOL.batch_black_check(
                        phones=[phone],
                        max_retries=max_retries,
                        delay=0.0,
                    )
                except Exception as ex:
                    traceback.print_exc()
                    return self._send_json(
                        json_err("黑号检测异常", detail=str(ex)),
                        500,
                    )
                r = results[0] if results else {}
                # ── 远程上号 IP 一致性：测黑完成后释放锁定 IP，恢复抢购代理池分片逻辑 ──
                try:
                    _e = POOL.get(phone)
                    if _e:
                        _e.proxy = ""
                        _e._session = None          # 销毁用于上号的 Session，下次 ensure_session 按池分片重建
                        _e._session_proxy_src = None
                        POOL.save()
                except Exception:
                    pass
                return self._send_json(json_ok({
                    "result": r,
                    "stats": POOL.stats(),
                }))

            # 获取某账号地址列表  {phone}
            if path == "/api/accounts/addresses":
                phone = str(payload.get("phone", "")).strip()
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                try:
                    s = e.ensure_session()
                    if _json_truthy(payload.get("batchOrderAddrPoolRotate")):
                        s.advance_active_pool_line_for_batch_order_addr()
                    lst, addr_raw = s.addresses_detail()
                except Exception as ex:
                    traceback.print_exc()
                    if address_db.enabled():
                        cached = address_db.get_cached_address_list(phone)
                        if cached:
                            return self._send_json(
                                json_ok(
                                    {
                                        "list": cached,
                                        "addressApi": {
                                            "code": 2000,
                                            "message": "ok（本地地址库回退，网络恢复后请刷新）",
                                            "_fromLocalDb": True,
                                        },
                                    }
                                )
                            )
                    return self._send_json(
                        json_err("拉取地址异常", detail=str(ex)),
                        500,
                    )
                code = addr_raw.get("code") if isinstance(addr_raw, dict) else None
                if code == 2000 and isinstance(lst, list) and lst:
                    if address_db.enabled():
                        address_db.save_address_list_for_phone(phone, lst)
                elif address_db.enabled():
                    need_fallback = (code != 2000) or not (
                        isinstance(lst, list) and lst
                    )
                    if need_fallback:
                        cached = address_db.get_cached_address_list(phone)
                        if cached:
                            return self._send_json(
                                json_ok(
                                    {
                                        "list": cached,
                                        "addressApi": {
                                            "code": 2000,
                                            "message": "ok（本地地址库回退）",
                                            "_fromLocalDb": True,
                                            "_liveCode": code,
                                            "_liveMessage": (
                                                str(addr_raw.get("message") or "")
                                                if isinstance(addr_raw, dict)
                                                else ""
                                            )[:200],
                                        },
                                    }
                                )
                            )
                return self._send_json(json_ok({"list": lst, "addressApi": addr_raw}))

            # 从本地SQLite缓存读取地址(无网络请求，自动链路专用)
            if path == "/api/accounts/addresses_cached":
                phone = str(payload.get("phone", "")).strip()
                if not phone:
                    return self._send_json(json_err("missing phone"))
                cached_list = address_db.get_cached_address_list(phone) if address_db.enabled() else []
                default_addr = address_db.get_default_address_dict(phone) if address_db.enabled() else None
                return self._send_json(json_ok({"list": cached_list, "default": default_addr}))

            # 新增/更新某账号地址 {phone, address}
            if path == "/api/accounts/address_upsert":
                phone = str(payload.get("phone", "")).strip()
                addr = payload.get("address")
                try:
                    rotate_retries = int(payload.get("rotateRetries", 3) or 3)
                except (TypeError, ValueError):
                    rotate_retries = 3
                rotate_retries = max(1, min(6, rotate_retries))
                try:
                    rotate_delay_ms = int(payload.get("rotateRetryDelayMs", 1000) or 1000)
                except (TypeError, ValueError):
                    rotate_delay_ms = 1000
                rotate_delay_ms = max(200, min(5000, rotate_delay_ms))

                def _addr_end_defer_and_save(_ph: str) -> None:
                    try:
                        POOL.end_deferred_onboarding(_ph)
                        POOL.save()
                    except Exception:
                        pass

                e = POOL.get(phone)
                if not e:
                    _addr_end_defer_and_save(phone)
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    _addr_end_defer_and_save(phone)
                    return self._send_json(json_err("账号未登录"), 400)
                if not isinstance(addr, dict):
                    _addr_end_defer_and_save(phone)
                    return self._send_json(json_err("address 参数无效"), 400)
                try:
                    out = {}
                    attempts: list[dict] = []
                    s = None
                    for idx in range(rotate_retries):
                        s = e.ensure_session()
                        if _json_truthy(payload.get("batchOrderAddrPoolRotate")):
                            s.advance_active_pool_line_for_batch_order_addr()
                        out = s.address_upsert(addr)
                        c_now = _mt_result_code(out)
                        m_now = _mt_result_message(out)
                        attempts.append(
                            {
                                "attempt": idx + 1,
                                "code": c_now,
                                "message": m_now[:120],
                            }
                        )
                        if c_now == 2000:
                            break
                        if idx + 1 < rotate_retries and _mt_should_rotate_retry(out):
                            _rotate_entry_proxy_for_retry(e, s, step="address_upsert")
                            time.sleep(rotate_delay_ms / 1000.0)
                            continue
                        break
                    if isinstance(out, dict):
                        out["submitAttempts"] = attempts
                except Exception as ex:
                    traceback.print_exc()
                    _addr_end_defer_and_save(phone)
                    return self._send_json(
                        json_err("地址提交异常", detail=str(ex)),
                        500,
                    )
                try:
                    if address_db.enabled() and isinstance(out, dict):
                        oc = out.get("code")
                        if oc == 2000 or str(oc) == "2000":
                            if _json_truthy(payload.get("batchOrderAddrPoolRotate")):
                                s.advance_active_pool_line_for_batch_order_addr()
                            lst_u, raw_u = s.addresses_detail()
                            if (
                                isinstance(raw_u, dict)
                                and raw_u.get("code") == 2000
                                and isinstance(lst_u, list)
                                and lst_u
                            ):
                                address_db.save_address_list_for_phone(phone, lst_u)
                except Exception:
                    pass
                # ── 远程上号 IP 一致性：地址提交完成即释放锁定 IP ──
                # 上号流程最后的必走步骤；无论用户是否继续点测黑，到这里都释放，
                # 保证抢购不会一直卡在上号时的固定 IP 上。
                # remote_black_check 里也有同样的释放（幂等双保险）。
                try:
                    # 兜底：地址提交成功后，强制回写账号为已登录，避免旧失败状态残留在主控页。
                    e.status = STATUS_OK
                    e._sync_from_session()
                    e.proxy = ""
                    e._session = None
                    e._session_proxy_src = None
                    _addr_end_defer_and_save(phone)
                except Exception:
                    pass
                return self._send_json(json_ok({"result": out}))

            # H5 修改订单地址 {phone, orderId, shipAddressId, deliverMethod?, orderLevel?, item{count,spuId,storeId,provinceId,promType}?}
            if path == "/api/accounts/order_ops_update":
                phone = str(payload.get("phone", "")).strip()
                order_id = str(payload.get("orderId", "")).strip()
                try:
                    ship_sid = int(payload.get("shipAddressId") or 0)
                except (TypeError, ValueError):
                    ship_sid = 0
                try:
                    deliver_method = int(payload.get("deliverMethod", 1) or 1)
                except (TypeError, ValueError):
                    deliver_method = 1
                order_level = str(payload.get("orderLevel", "2") or "2").strip() or "2"
                item = payload.get("item")
                if not isinstance(item, dict):
                    item = {
                        "count": payload.get("count"),
                        "spuId": payload.get("spuId", payload.get("itemCode")),
                        "storeId": payload.get("storeId"),
                        "provinceId": payload.get("provinceId"),
                        "promType": payload.get("promType"),
                    }
                referer = str(payload.get("referer", "")).strip()
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                if not order_id:
                    return self._send_json(json_err("orderId 不能为空"), 400)
                if ship_sid <= 0:
                    return self._send_json(json_err("shipAddressId 不合法"), 400)
                try:
                    s = e.ensure_session()
                    if _json_truthy(payload.get("batchOrderAddrPoolRotate")):
                        s.advance_active_pool_line_for_batch_order_addr()
                    out = s.order_ops_update_h5(
                        order_id=order_id,
                        ship_address_id=ship_sid,
                        deliver_method=deliver_method,
                        order_level=order_level,
                        item=item,
                        referer=referer,
                    )
                except Exception as ex:
                    traceback.print_exc()
                    return self._send_json(json_err("订单地址修改异常", detail=str(ex)), 500)
                _debug_dump_mt("/api/accounts/order_ops_update", payload, out)
                return self._send_json(json_ok({"result": out}))

            # 查询指定账号实名认证状态 {phone}
            if path == "/api/accounts/realname/query":
                phone = str(payload.get("phone", "")).strip()
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                try:
                    s = e.ensure_session()
                    out = s.realname_query()
                except Exception as ex:
                    traceback.print_exc()
                    return self._send_json(json_err("实名查询异常", detail=str(ex)), 500)
                return self._send_json(json_ok({"result": out}))

            # 提交指定账号实名认证 {phone, name, idCard, force?}
            if path == "/api/accounts/realname/submit":
                phone = str(payload.get("phone", "")).strip()
                name = str(payload.get("name", "")).strip()
                id_card = str(payload.get("idCard", payload.get("idNo", ""))).strip()
                force_submit = bool(payload.get("force", False))
                try:
                    submit_retries = int(payload.get("submitRetries", 3) or 3)
                except (TypeError, ValueError):
                    submit_retries = 3
                submit_retries = max(1, min(6, submit_retries))
                try:
                    retry_delay_ms = int(payload.get("retryDelayMs", 1200) or 1200)
                except (TypeError, ValueError):
                    retry_delay_ms = 1200
                retry_delay_ms = max(200, min(5000, retry_delay_ms))
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                try:
                    s = e.ensure_session()
                    q = s.realname_query()
                    qv = None
                    if isinstance(q, dict):
                        if q.get("verifyStatus") is not None:
                            qv = q.get("verifyStatus")
                        elif isinstance(q.get("data"), dict) and q["data"].get("verifyStatus") is not None:
                            qv = q["data"].get("verifyStatus")
                    try:
                        if (
                            not force_submit
                            and qv is not None
                            and verify_status_realname_complete(qv)
                        ):
                            return self._send_json(
                                json_ok(
                                    {
                                        "result": {
                                            "code": 2000,
                                            "message": "该账号已实名，无需重复提交",
                                            "verifyStatus": 1,
                                            "alreadyVerified": True,
                                        }
                                    }
                                )
                            )
                    except Exception:
                        pass
                    out = {}
                    attempts: list[dict] = []
                    for idx in range(submit_retries):
                        out = s.realname_submit(name, id_card)
                        msg_now = _realname_submit_msg(out)
                        code_now = out.get("code") if isinstance(out, dict) else None
                        attempts.append(
                            {
                                "attempt": idx + 1,
                                "code": code_now,
                                "message": msg_now[:120],
                            }
                        )
                        ok_now = False
                        try:
                            ok_now = int(code_now) == 2000
                        except Exception:
                            ok_now = False
                        if (not ok_now) and _realname_submit_implies_verified(msg_now):
                            ok_now = True
                        if ok_now:
                            break
                        if idx + 1 < submit_retries and (
                            _realname_submit_soft_retry(msg_now) or _mt_should_rotate_retry(out)
                        ):
                            _rotate_entry_proxy_for_retry(e, s, step="realname_submit")
                            s = e.ensure_session()
                            time.sleep(retry_delay_ms / 1000.0)
                            continue
                        break
                    if isinstance(out, dict):
                        out["submitAttempts"] = attempts
                    _pool_synced = False
                    try:
                        _ok_submit = False
                        if isinstance(out, dict):
                            try:
                                _ok_submit = int(out.get("code", -1)) == 2000
                            except Exception:
                                _ok_submit = False
                            if (not _ok_submit) and _realname_submit_implies_verified(_realname_submit_msg(out)):
                                _ok_submit = True
                                out["alreadyVerifiedByMessage"] = True
                        if _ok_submit:
                            e.status = STATUS_OK
                            try:
                                q_after = s.realname_query()
                                qvs = None
                                if isinstance(q_after, dict):
                                    qvs = q_after.get("verifyStatus")
                                    if qvs is None and isinstance(q_after.get("data"), dict):
                                        qvs = q_after["data"].get("verifyStatus")
                                if qvs is not None:
                                    s._verify_status = int(qvs)
                                elif verify_status_realname_complete(getattr(s, "_verify_status", None)):
                                    pass
                                else:
                                    s._verify_status = 2
                            except Exception:
                                pass
                            e._sync_from_session()
                            try:
                                if e.verify_status is None and getattr(s, "_verify_status", None) is not None:
                                    e.verify_status = int(s._verify_status)
                            except Exception:
                                pass
                            POOL.save()
                            _pool_synced = True
                    except Exception:
                        pass
                    # #region agent log
                    try:
                        _mtc = out.get("code") if isinstance(out, dict) else None
                        _payload = {
                            "sessionId": "eb180b",
                            "hypothesisId": "H1",
                            "location": "webui_server.py:realname_submit",
                            "message": "after realname_submit",
                            "data": {
                                "mt_code": _mtc,
                                "mt_code_type": type(_mtc).__name__ if _mtc is not None else None,
                                "pool_synced_only_2000": _pool_synced,
                                "entry_verify_status": getattr(e, "verify_status", None),
                                "session__verify_status": getattr(s, "_verify_status", None),
                            },
                            "timestamp": int(time.time() * 1000),
                        }
                        with open(Path(__file__).resolve().parent / "debug-eb180b.log", "a", encoding="utf-8") as _af:
                            _af.write(json.dumps(_payload, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
                    # #endregion
                except Exception as ex:
                    traceback.print_exc()
                    return self._send_json(json_err("实名认证提交异常", detail=str(ex)), 500)
                return self._send_json(json_ok({"result": out}))

            # H5 找回身份 {phone, name, idCard}
            if path == "/api/accounts/find_identity":
                phone = str(payload.get("phone", "")).strip()
                name = str(payload.get("name", "")).strip()
                id_card = str(payload.get("idCard", payload.get("idNo", ""))).strip()
                try:
                    rotate_retries = int(payload.get("rotateRetries", 3) or 3)
                except (TypeError, ValueError):
                    rotate_retries = 3
                rotate_retries = max(1, min(6, rotate_retries))
                try:
                    rotate_delay_ms = int(payload.get("rotateRetryDelayMs", 1000) or 1000)
                except (TypeError, ValueError):
                    rotate_delay_ms = 1000
                rotate_delay_ms = max(200, min(5000, rotate_delay_ms))
                if str(os.environ.get("MT_LOG_FIND_IDENTITY_PAYLOAD", "") or "").strip().lower() in (
                    "1",
                    "true",
                    "yes",
                ):
                    try:
                        cps = ",".join(f"U+{ord(c):04X}" for c in name)
                        print(
                            f"[找回身份:WebUI] phone={phone} name_repr={name!r} codepoints={cps}",
                            flush=True,
                        )
                    except Exception:
                        pass
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                try:
                    out = {}
                    attempts: list[dict] = []
                    for idx in range(rotate_retries):
                        s = e.ensure_session()
                        out = s.find_identity(name, id_card)
                        c_now = _mt_result_code(out)
                        m_now = _mt_result_message(out)
                        attempts.append(
                            {
                                "attempt": idx + 1,
                                "code": c_now,
                                "message": m_now[:120],
                            }
                        )
                        if c_now == 2000:
                            break
                        if idx + 1 < rotate_retries and _mt_should_rotate_retry(out):
                            _rotate_entry_proxy_for_retry(e, s, step="find_identity")
                            time.sleep(rotate_delay_ms / 1000.0)
                            continue
                        break
                    if isinstance(out, dict):
                        out["submitAttempts"] = attempts
                except Exception as ex:
                    traceback.print_exc()
                    return self._send_json(json_err("找回身份异常", detail=str(ex)), 500)
                return self._send_json(json_ok({"result": out}))

            # H5 下单前置 beforeinit（首号测试按钮）；明文 JSON 与抓包路径一致
            if path == "/api/accounts/beforeinit_test":
                phone = str(payload.get("phone", "")).strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                spu_id = str(payload.get("spuId", "741") or "741").strip()
                store_id = str(payload.get("storeId", "MAOTAI1002") or "MAOTAI1002").strip()
                try:
                    cnt = int(payload.get("count", 1) or 1)
                except (TypeError, ValueError):
                    cnt = 1
                referer = str(payload.get("referer", "") or "").strip()
                ship_address_id = str(payload.get("shipAddressId", "") or "").strip()
                address_lat = str(payload.get("addressLat", "") or "").strip()
                address_lng = str(payload.get("addressLng", "") or "").strip()
                try:
                    s = e.ensure_session()
                    out = s.beforeinit_order_h5(
                        spu_id=spu_id,
                        store_id=store_id,
                        count=cnt,
                        referer=referer,
                        ship_address_id=ship_address_id,
                        address_lat=address_lat,
                        address_lng=address_lng,
                    )
                except Exception as ex:
                    traceback.print_exc()
                    return self._send_json(
                        json_err("beforeinit 异常", detail=str(ex).strip() or type(ex).__name__),
                        500,
                    )
                return self._send_json(json_ok({"beforeinit": out}))

            # 用指定账号抢购  {phone, itemCode, actId?, spuId(IMTP)?, hot?, referer?, amount?, sourceId?}
            # actId 可空：若提供 spuId(IMTP)，先调 purchaseInfoV2 动态取 itemPriorityActId
            if path == "/api/accounts/rush":
                phone   = str(payload.get("phone", "")).strip()
                item    = str(payload.get("itemCode", "")).strip()
                act_id  = str(payload.get("actId", "")).strip()
                spu_imtp = str(payload.get("spuId", "")).strip()
                hot     = payload.get("hot", True)
                referer = str(payload.get("referer", "")).strip()
                amount  = str(payload.get("amount", "1")).strip() or "1"
                source  = str(payload.get("sourceId", "")).strip()
                branch = str(payload.get("branch", "")).strip().lower()
                need_code = bool(payload.get("needCode", False))
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                if not item:
                    return self._send_json(json_err("商品 ID（itemCode）不能为空"), 400)
                cont, err_bs, det_bs = _rush_prepare_bs_dvid(phone)
                if not cont:
                    return self._send_json(json_err(err_bs or "BS-DVID 不可用", detail=det_bs), 400)
                try:
                    s = e.ensure_session()
                    # 勿强制 full_pool：否则 MT 模式每枪 rushLoopAttempts=1 时 _rush_proxy_url_sequence 每枪都取 au[0]
                    s._rush_full_pool_sequence = bool(getattr(e, "full_pool_rush", False))
                    prep = _rush_prepare_act(s, item, act_id, spu_imtp, referer, bool(hot))
                    if not prep["ok"]:
                        return self._send_json(
                            json_err(prep["error"], detail=prep.get("detail")), 400
                        )
                    act_id = prep["act_id"]
                    spu_imtp = prep["spu_imtp"]
                    referer = prep["referer"]
                    pinfo = prep.get("pinfo")
                    route_info = prep.get("route_info")
                    if route_info:
                        if not branch:
                            branch = str(route_info.get("branch", "") or "").strip().lower()
                        if route_info.get("needCodeHint"):
                            need_code = bool(need_code or route_info.get("needCodeHint"))
                    rla = _parse_rush_loop_attempts_from_payload(payload)
                    kw = {}
                    if rla is not None:
                        kw["rush_loop_attempts"] = rla
                    kw["rush_loop_delay_ms"] = _parse_rush_loop_delay_ms_from_payload(
                        payload
                    )
                    if _rush_loop_burst_in_node_from_payload(payload):
                        kw["rush_loop_burst_in_node"] = True
                    if _fire_and_forget_from_payload(payload):
                        kw["fire_and_forget"] = True
                    _rr3 = _parse_rush_rounds_from_payload(payload)
                    if _rr3 is not None:
                        kw["rush_rounds"] = _rr3
                    _rg3 = _parse_rush_round_gap_ms_from_payload(payload)
                    if _rg3 is not None:
                        kw["rush_round_gap_ms"] = _rg3
                    _rj3 = _parse_rush_round_gap_jitter_ms_from_payload(payload)
                    if _rj3 is not None:
                        kw["rush_round_gap_jitter_ms"] = _rj3
                    kw.update(_rush_lane_kwargs_from_payload(payload))
                    res = s.rush(
                        item=item,
                        act_id=act_id,
                        amount=amount,
                        src=source,
                        branch=branch,
                        referer=referer,
                        need_code=need_code,
                        **kw,
                    )
                    _debug_dump_mt(
                        "/api/accounts/rush",
                        {
                            **payload,
                            "resolvedActId": act_id,
                            "resolvedSpuId": spu_imtp or None,
                            "branch": branch or None,
                            "referer": referer or None,
                            "amount": amount,
                            "sourceId": source or None,
                            "hot": hot,
                            "needCode": need_code,
                            "autoResolvedRoute": route_info,
                        },
                        res,
                    )
                    pay = {
                        "rush": res,
                        "resolvedActId": act_id,
                        "resolvedSpuId": spu_imtp or None,
                        "purchaseInfo": pinfo,
                        "autoResolvedRoute": route_info,
                    }
                    # 写死：抢购成功当即落盘 H5 + App 设备 + 出口代理，组单/付款必须同源（不可静默失败）
                    if isinstance(res, dict) and _mt_business_ok(res.get("code")):
                        try:
                            pay["rushIdentityPinned"] = bool(
                                POOL.pin_h5_device_after_rush_ok(phone, session=s, rush_response=res)
                            )
                            if e and (getattr(e, "rush_success_egress_ip", "") or "").strip():
                                pay["rushSuccessEgressIp"] = e.rush_success_egress_ip.strip()
                            if e and (getattr(e, "rush_success_proxy_url", "") or "").strip():
                                pay["rushSuccessProxyHost"] = _proxy_hostport_for_log(
                                    e.rush_success_proxy_url
                                )
                        except Exception as pin_ex:
                            pay["rushIdentityPinned"] = False
                            pay["rushPinError"] = str(pin_ex)
                            print(f"[rush] 抢购成功但写死设备/代理失败: {pin_ex}")
                    if not (e.bsid or "").strip():
                        pay["deviceHint"] = (
                            "当前账号暂无 BS-DVID：当前策略仅在发码前获取浩天并复用。"
                            "请重新走「发码 -> 登录」流程。"
                        )
                    return self._send_json(json_ok(pay))
                except Exception as ex:
                    traceback.print_exc()
                    em = str(ex).strip() or type(ex).__name__
                    msg = "抢购链路异常（多为网络超时、代理不可用或 TLS/连接失败，不是茅台返回的业务提示）"
                    low = em.lower()
                    if "407" in em or "proxy authentication" in low:
                        msg = (
                            "抢购失败：代理返回 HTTP 407（需要认证或白名单未命中）。"
                            "当前代理地址没有携带 user:pass；如果用的是白名单代理，请核对本机出口 IP 是否已加入供应商白名单，"
                            "以及这批 host:port 是否属于白名单端口；如果供应商给的是账密端口，才需要填写 user:pass@host:port。"
                        )
                    return self._send_json(json_err(msg, detail=em), 500)

            if path == "/api/accounts/batch_rush":
                tasks = payload.get("tasks")
                if not isinstance(tasks, list) or not tasks:
                    return self._send_json(json_err("tasks 须为非空数组"), 400)
                cap = _batch_rush_parallel_cap()
                n_task = len(tasks)
                
                # 检查是否启用了高频模式（fireAndForget）
                has_high_freq = False
                for t in tasks:
                    if isinstance(t, dict) and _fire_and_forget_from_payload(t):
                        has_high_freq = True
                        break
                
                # 高频模式默认使用最大并发，普通模式使用用户设置或默认12
                default_conc = 12
                raw_conc = payload.get("concurrency", default_conc)
                try:
                    user_c = max(1, int(raw_conc))
                except (TypeError, ValueError):
                    user_c = default_conc
                user_c = min(cap, user_c)
                
                # 高频模式下强制使用尽可能高的并发（不小于任务数）
                conc = min(cap, n_task, user_c)
                try:
                    results = _run_batch_rush_api(tasks, conc)
                except Exception as ex:
                    traceback.print_exc()
                    return self._send_json(
                        json_err("批量抢购异常", detail=str(ex).strip() or type(ex).__name__),
                        500,
                    )
                return self._send_json(json_ok({"results": results}))

            if path == "/api/accounts/batch_rush_stream":
                tasks = payload.get("tasks")
                if not isinstance(tasks, list) or not tasks:
                    self._start_ndjson()
                    self._write_ndjson({"type": "error", "ok": False, "message": "tasks 须为非空数组"})
                    self._write_ndjson({"type": "done", "ok": False, "total": 0, "success": 0, "failed": 0})
                    return
                cap = _batch_rush_parallel_cap()
                n_task = len(tasks)
                has_high_freq = any(isinstance(t, dict) and _fire_and_forget_from_payload(t) for t in tasks)
                default_conc = 12
                raw_conc = payload.get("concurrency", default_conc)
                try:
                    user_c = max(1, int(raw_conc))
                except (TypeError, ValueError):
                    user_c = default_conc
                user_c = min(cap, user_c)
                conc = min(cap, n_task, user_c)
                self._start_ndjson()

                alive = True

                def emit(ev):
                    nonlocal alive
                    if not alive:
                        return
                    alive = self._write_ndjson(ev)

                try:
                    _run_batch_rush_stream_api(tasks, conc, emit)
                except Exception as ex:
                    traceback.print_exc()
                    em = str(ex).strip() or type(ex).__name__
                    emit({"type": "error", "ok": False, "message": "批量抢购流式异常: " + em})
                    emit({"type": "done", "ok": False, "total": n_task, "success": 0, "failed": n_task})
                return

            if path == "/api/distributed/batch_rush_stream":
                tasks = payload.get("tasks")
                if not isinstance(tasks, list) or not tasks:
                    self._start_ndjson()
                    self._write_ndjson({"type": "error", "ok": False, "message": "tasks 须为非空数组"})
                    self._write_ndjson({"type": "done", "ok": False, "total": 0, "success": 0, "failed": 0, "distributed": True})
                    return
                cap = _batch_rush_parallel_cap()
                n_task = len(tasks)
                default_conc = 12
                raw_conc = payload.get("concurrency", default_conc)
                try:
                    user_c = max(1, int(raw_conc))
                except (TypeError, ValueError):
                    user_c = default_conc
                conc = min(cap, n_task, user_c)
                self._start_ndjson()

                alive = True

                def emit(ev):
                    nonlocal alive
                    if not alive:
                        return
                    alive = self._write_ndjson(ev)

                try:
                    _run_distributed_batch_rush_stream_api(tasks, conc, emit)
                except Exception as ex:
                    traceback.print_exc()
                    em = str(ex).strip() or type(ex).__name__
                    emit({"type": "error", "ok": False, "message": "分布式批量抢购异常: " + em})
                    emit({"type": "done", "ok": False, "total": n_task, "success": 0, "failed": n_task, "distributed": True})
                return

            # 单账号预热 HTTP：仅 GET H5 根域名；targets 条数与抢购出口集合一致，顺序随机打乱后由 Node 并发执行
            if path == "/api/accounts/prewarm_http":
                phone = str(payload.get("phone", "")).strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                try:
                    # #region agent log
                    _agent_dbg_prewarm(
                        "H2",
                        "webui_server.py:prewarm_http",
                        "api_entry",
                        {
                            "item_code_len": len(str(payload.get("itemCode") or "").strip()),
                            "has_branch": bool(str(payload.get("branch") or "").strip()),
                        },
                    )
                    # #endregion
                    built = _build_prewarm_targets_for_account(phone, payload)
                    if not built.get("ok"):
                        # #region agent log
                        _agent_dbg_prewarm(
                            "H2",
                            "webui_server.py:prewarm_http",
                            "build_failed",
                            {"err": str(built.get("error") or "")[:120]},
                        )
                        # #endregion
                        return self._send_json(json_err(str(built.get("error") or "预热目标生成失败")), 400)
                    targets = built.get("targets") if isinstance(built.get("targets"), list) else []
                    e_pw = POOL.get(phone)
                    s_pw = e_pw.ensure_session() if e_pw else None
                    tls_pw = TLS_PROFILE
                    out = go_bridge_prewarm_http_dispatch(
                        targets,
                        concurrency=_prewarm_node_concurrency_for_targets(len(targets)),
                        outer_timeout=max(20.0, min(180.0, 8.0 + len(targets) * 5.0)),
                        tls_impersonate=str(tls_pw).strip(),
                    )
                    # #region agent log
                    _agent_dbg_prewarm(
                        "H3",
                        "webui_server.py:prewarm_http",
                        "dispatch_done",
                        {
                            "n_targets": len(targets),
                            "ok_count": int((out or {}).get("okCount") or 0),
                            "total": int((out or {}).get("total") or len(targets)),
                        },
                    )
                    # #endregion
                    data = {
                        "phone": phone,
                        "targetCount": len(targets),
                        "attemptsN": built.get("attemptsN") or len(targets),
                        "warmUrl": built.get("warm_url") or built.get("rush_url"),
                        "rushUrl": built.get("rush_url"),
                        "branch": built.get("branch") or "",
                        "prewarm": out,
                    }
                    return self._send_json(json_ok(data))
                except Exception as ex:
                    traceback.print_exc()
                    # #region agent log
                    _agent_dbg_prewarm(
                        "H5",
                        "webui_server.py:prewarm_http",
                        "exception",
                        {"exc_type": type(ex).__name__, "detail": str(ex).strip()[:200]},
                    )
                    # #endregion
                    return self._send_json(
                        json_err("HTTP 预热失败", detail=str(ex).strip() or type(ex).__name__),
                        500,
                    )

            # 全部已登录账号一键预热：逐账号按其分片 IP 仅连 H5 根域
            if path == "/api/accounts/prewarm_http_all":
                req_phones = payload.get("phones")
                phones: list[str] = []
                if isinstance(req_phones, list) and req_phones:
                    phones = [str(x).strip() for x in req_phones if str(x).strip()]
                else:
                    phones = [
                        str(a.get("phone", "")).strip()
                        for a in (POOL.snapshot() or [])
                        if str(a.get("status", "")).strip() == STATUS_OK
                    ]
                phones = [p for p in phones if p]
                if not phones:
                    return self._send_json(json_err("无可预热账号（请先登录）"), 400)
                out_rows: list[dict] = []
                max_workers = min(16, max(1, len(phones)))
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                    futs = {}
                    for ph in phones:
                        futs[ex.submit(_build_prewarm_targets_for_account, ph, payload)] = ph
                    built_rows: list[dict] = []
                    for fu in concurrent.futures.as_completed(futs):
                        ph = futs[fu]
                        try:
                            row = fu.result()
                            row["phone"] = ph
                        except Exception as ex2:
                            row = {"ok": False, "phone": ph, "error": str(ex2).strip() or type(ex2).__name__}
                        built_rows.append(row)
                for row in built_rows:
                    ph = str(row.get("phone", "")).strip()
                    if not row.get("ok"):
                        out_rows.append({"phone": ph, "ok": False, "message": str(row.get("error") or "预热目标生成失败")})
                        continue
                    targets = row.get("targets") if isinstance(row.get("targets"), list) else []
                    try:
                        e_a = POOL.get(ph)
                        s_a = e_a.ensure_session() if e_a else None
                        tls_a = TLS_PROFILE
                        out = go_bridge_prewarm_http_dispatch(
                            targets,
                            concurrency=_prewarm_node_concurrency_for_targets(len(targets)),
                            outer_timeout=max(20.0, min(180.0, 8.0 + len(targets) * 5.0)),
                            tls_impersonate=str(tls_a).strip(),
                        )
                        p_ok = int(out.get("okCount") or 0)
                        p_total = int(out.get("total") or len(targets))
                        out_rows.append(
                            {
                                "phone": ph,
                                "ok": True,
                                "targetCount": len(targets),
                                "okCount": p_ok,
                                "total": p_total,
                                "warmUrl": row.get("warm_url") or row.get("rush_url"),
                                "rushUrl": row.get("rush_url"),
                            }
                        )
                    except Exception as ex3:
                        out_rows.append({"phone": ph, "ok": False, "message": str(ex3).strip() or type(ex3).__name__})
                return self._send_json(json_ok({"results": out_rows}))

            # 用指定账号组单  {phone, priorityRecordId, itemCode|spuId, count?, address?}
            # 业务协议：app.py Session.compose 走 App compose/v2（本处仅参数与地址补全）
            if path == "/api/accounts/compose_stream":
                phone = str(payload.get("phone", "")).strip()
                rec_id = str(payload.get("priorityRecordId", "")).strip()
                cnt = int(payload.get("count", 1) or 1)
                addr = payload.get("address") or {}
                referer = str(payload.get("referer", "")).strip()
                prefer_pool_rotate = _prefer_pool_rotate_from_payload(payload)
                sku, _imtp = _resolve_order_sku_imtp(payload)
                verify_ref = _referer_for_verify_context(referer, sku)

                self._start_ndjson()
                alive = True

                def emit(obj) -> bool:
                    nonlocal alive
                    if not alive:
                        return False
                    alive = self._write_ndjson(obj)
                    return alive

                def log_line(line: str) -> None:
                    emit({"type": "log", "phone": phone, "line": str(line or "")})

                def fail(message: str, code: int = -1, data=None) -> None:
                    out = data if isinstance(data, dict) else {"code": code, "message": str(message or "")}
                    emit({"type": "error", "phone": phone, "ok": False, "message": str(message or ""), "data": out})
                    emit({"type": "done", "phone": phone, "ok": False, "message": str(message or ""), "data": out})

                try:
                    e = POOL.get(phone)
                    if not e:
                        fail("账号不存在")
                        return
                    if e.status != STATUS_OK:
                        fail("账号未登录")
                        return
                    if not rec_id or not sku:
                        fail("组单需要 priorityRecordId 与商品 ID（itemCode，卡片「商品 ID」），或与抢购一致的数字 ID；勿单独填编号(IMTP)")
                        return
                    err_sku = _order_sku_digit_error(sku, "组单")
                    if err_sku:
                        fail(err_sku)
                        return
                    if _truthy(payload.get("mockAutoChain")):
                        mock_compose = {
                            "code": 2000,
                            "message": "模拟组单成功",
                            "data": {
                                "transactionId": f"mock_{int(time.time() * 1000)}",
                                "sourceId": str(_imtp or sku),
                                "deliverMethodList": [
                                    {"type": 1, "desc": "快递配送", "notes": "", "checked": True}
                                ],
                                "promIdList": [],
                                "userPriorityComposeResult": {
                                    "priorityStoreInfo": {
                                        "storeId": "MOCK_STORE",
                                        "shopId": "MOCK_STORE",
                                    }
                                },
                            },
                            "_composeTrace": [
                                f"[模拟] 节点回传 priorityRecordId={rec_id}",
                                "[模拟] 已跳过易盾与真实组单请求",
                            ],
                            "_mockAutoChain": True,
                        }
                        for line in mock_compose["_composeTrace"]:
                            log_line(line)
                        emit({"type": "result", "phone": phone, "ok": True, "data": mock_compose})
                        emit({"type": "done", "phone": phone, "ok": True, "data": mock_compose})
                        return

                    s = e.ensure_session()
                    s.force_order_chain_direct()
                    prefer_pool_rotate = False
                    if prefer_pool_rotate:
                        try:
                            s._compose_pin_proxy_url = ""
                            s._rush_success_proxy_url_norm = ""
                            s.advance_active_pool_line_for_batch_order_addr()
                        except Exception:
                            pass
                    try:
                        rid_i = int(rec_id)
                    except (TypeError, ValueError):
                        fail("priorityRecordId 必须是数字")
                        return

                    verify_trace: list[str] = []
                    vr = _verify_cache_success(rid_i)
                    if vr is not None:
                        vr_ok = True
                        line = "[验证] 缓存命中，跳过重复验证"
                        verify_trace.append(line)
                        log_line(line)
                        print(f"[组单] 易盾验证缓存命中 priorityRecordId={rec_id}，跳过重复验证", flush=True)
                    else:
                        log_line(f"[验证] 开始易盾验证 priorityRecordId={rec_id}")
                        try:
                            vr = s.verify_captcha(priority_record_id=rid_i, referer=verify_ref, item_code=sku)
                            vr_code = vr.get("code") if isinstance(vr, dict) else -1
                            vr_ok = _verify_code_business_ok(vr)
                            line = (
                                f"[验证] /verify/code code={vr_code} {'通过' if vr_ok else '未通过'}: "
                                f"{_verify_code_result_message(vr)}"
                            )
                            verify_trace.append(line)
                            log_line(line)
                            print(f"[组单] 易盾验证结果: {vr}", flush=True)
                        except Exception as ex:
                            vr_ok = False
                            vr_msg = str(ex).strip() or type(ex).__name__
                            line = f"[验证] 异常: {vr_msg}"
                            verify_trace.append(line)
                            log_line(line)
                            print(f"[组单] 易盾验证异常(阻断组单): {vr_msg}", flush=True)
                            fail(f"易盾验证异常，组单已阻断: {vr_msg}")
                            return
                    if not vr_ok:
                        vr_msg = _verify_code_result_message(vr)
                        print(f"[组单] 易盾验证未通过，阻断组单: {vr_msg}", flush=True)
                        fail(f"易盾验证未通过，组单已阻断: {vr_msg}")
                        return
                    _verify_cache_set(rid_i, vr)

                    addr, addr_hint = _resolve_ship_address_for_order(phone, addr, s)
                    if not addr:
                        fail(addr_hint or "address 无效")
                        return
                    if addr_hint:
                        log_line("[地址] " + str(addr_hint))

                    res = s.compose(
                        spu=sku,
                        cnt=cnt,
                        rec_id=rec_id,
                        addr=addr,
                        rotate_max=_compose_rotate_max_for_account(e, payload),
                        use_rush_pin=not prefer_pool_rotate,
                        trace_emit=log_line,
                    )
                    if isinstance(res, dict):
                        existing = res.get("_composeTrace") or []
                        res = dict(res)
                        res["_composeTrace"] = verify_trace + list(existing)
                    else:
                        res = {"code": -1, "message": "组单无有效响应", "raw": str(res)}
                    if addr_hint and isinstance(res, dict):
                        res = dict(res)
                        res["_addressHint"] = addr_hint
                    _debug_dump_mt("/api/accounts/compose_stream", payload, res)
                    ok = isinstance(res, dict) and int(res.get("code") or -1) == 2000
                    emit({"type": "result", "phone": phone, "ok": bool(ok), "data": res})
                    emit({"type": "done", "phone": phone, "ok": bool(ok), "data": res})
                    return
                except Exception as ex:
                    traceback.print_exc()
                    fail(str(ex).strip() or type(ex).__name__)
                    return

            if path == "/api/accounts/compose":
                phone = str(payload.get("phone", "")).strip()
                rec_id = str(payload.get("priorityRecordId", "")).strip()
                cnt = int(payload.get("count", 1) or 1)
                addr = payload.get("address") or {}
                referer = str(payload.get("referer", "")).strip()
                prefer_pool_rotate = _prefer_pool_rotate_from_payload(payload)
                sku, _imtp = _resolve_order_sku_imtp(payload)
                verify_ref = _referer_for_verify_context(referer, sku)
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                if not rec_id or not sku:
                    return self._send_json(json_err(
                        "组单需要 priorityRecordId 与商品 ID（itemCode，卡片「商品 ID」），"
                        "或与抢购一致的数字 ID；勿单独填编号(IMTP)"
                    ), 400)
                err_sku = _order_sku_digit_error(sku, "组单")
                if err_sku:
                    return self._send_json(json_err(err_sku), 400)
                if _truthy(payload.get("mockAutoChain")):
                    mock_compose = {
                        "code": 2000,
                        "message": "模拟组单成功",
                        "data": {
                            "transactionId": f"mock_{int(time.time() * 1000)}",
                            "sourceId": str(_imtp or sku),
                            "deliverMethodList": [
                                {"type": 1, "desc": "快递配送", "notes": "", "checked": True}
                            ],
                            "promIdList": [],
                            "userPriorityComposeResult": {
                                "priorityStoreInfo": {
                                    "storeId": "MOCK_STORE",
                                    "shopId": "MOCK_STORE",
                                }
                            },
                        },
                        "_composeTrace": [
                            f"[模拟] 节点回传 priorityRecordId={rec_id}",
                            "[模拟] 已跳过易盾与真实组单请求",
                        ],
                        "_mockAutoChain": True,
                    }
                    return self._send_json(json_ok(mock_compose))
                s = e.ensure_session()
                s.force_order_chain_direct()
                prefer_pool_rotate = False
                if prefer_pool_rotate:
                    try:
                        s._compose_pin_proxy_url = ""
                        s._rush_success_proxy_url_norm = ""
                        s.advance_active_pool_line_for_batch_order_addr()
                    except Exception:
                        pass
                try:
                    rid_i = int(rec_id)
                except (TypeError, ValueError):
                    return self._send_json(json_err("priorityRecordId 必须是数字"), 400)
                verify_trace = []
                vr = _verify_cache_success(rid_i)
                if vr is not None:
                    vr_ok = True
                    verify_trace.append("[验证] 缓存命中，跳过重复验证")
                    print(f"[组单] 易盾验证缓存命中 priorityRecordId={rec_id}，跳过重复验证", flush=True)
                else:
                    try:
                        vr = s.verify_captcha(priority_record_id=rid_i, referer=verify_ref, item_code=sku)
                        vr_code = vr.get("code") if isinstance(vr, dict) else -1
                        vr_ok = _verify_code_business_ok(vr)
                        verify_trace.append(
                            f"[验证] /verify/code code={vr_code} {'通过' if vr_ok else '未通过'}: "
                            f"{_verify_code_result_message(vr)}"
                        )
                        print(f"[组单] 易盾验证结果: {vr}", flush=True)
                    except Exception as ex:
                        vr_ok = False
                        vr_msg = str(ex).strip() or type(ex).__name__
                        verify_trace.append(f"[验证] 异常: {vr_msg}")
                        print(f"[组单] 易盾验证异常(阻断组单): {vr_msg}", flush=True)
                        return self._send_json(json_err(f"易盾验证异常，组单已阻断: {vr_msg}"), 400)
                if not vr_ok:
                    vr_msg = _verify_code_result_message(vr)
                    print(f"[组单] 易盾验证未通过，阻断组单: {vr_msg}", flush=True)
                    return self._send_json(json_err(f"易盾验证未通过，组单已阻断: {vr_msg}"), 400)
                _verify_cache_set(rid_i, vr)
                addr, addr_hint = _resolve_ship_address_for_order(phone, addr, s)
                if not addr:
                    return self._send_json(json_err(addr_hint or "address 无效"), 400)
                res = s.compose(
                    spu=sku,
                    cnt=cnt,
                    rec_id=rec_id,
                    addr=addr,
                    rotate_max=_compose_rotate_max_for_account(e, payload),
                    use_rush_pin=not prefer_pool_rotate,
                )
                if isinstance(res, dict):
                    existing = res.get("_composeTrace") or []
                    res = dict(res)
                    res["_composeTrace"] = verify_trace + list(existing)
                if addr_hint and isinstance(res, dict):
                    res = dict(res)
                    res["_addressHint"] = addr_hint
                _debug_dump_mt("/api/accounts/compose", payload, res)
                return self._send_json(json_ok(res))

            # 单独测试易盾验证 {phone, priorityRecordId}
            if path == "/api/accounts/verify":
                phone = str(payload.get("phone", "")).strip()
                rec_id = str(payload.get("priorityRecordId", "")).strip()
                referer = str(payload.get("referer", "")).strip()
                verify_item_code = str(payload.get("itemCode") or payload.get("spuId") or "").strip()
                verify_ref = _referer_for_verify_context(referer, verify_item_code)
                if not phone or not rec_id:
                    return self._send_json(json_err("需要 phone 和 priorityRecordId"), 400)
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                try:
                    rid_i = int(rec_id)
                except (TypeError, ValueError):
                    return self._send_json(json_err("priorityRecordId 必须是数字"), 400)
                try:
                    cached = _verify_cache_success(rid_i)
                    if cached is not None:
                        return self._send_json(
                            json_ok({"phone": phone, "verifyResult": cached, "cached": True})
                        )
                    s = e.ensure_session()
                    s.force_order_chain_direct()
                    vr = s.verify_captcha(priority_record_id=rid_i, referer=verify_ref, item_code=verify_item_code)
                    if _verify_code_business_ok(vr):
                        _verify_cache_set(rid_i, vr)
                    return self._send_json(json_ok({"phone": phone, "verifyResult": vr, "cached": False}))
                except Exception as ex:
                    return self._send_json(json_err(f"验证异常: {ex}"), 500)

            # 用指定账号下单  {phone, priorityRecordId, itemCode|spuId, composeData?, composeShopId?, count?, address?}
            # 业务协议：app.py Session.submit 走 App submit/v2（本处仅参数与地址补全）
            if path == "/api/accounts/submit":
                phone = str(payload.get("phone", "")).strip()
                rec_id = str(payload.get("priorityRecordId", "")).strip()
                cnt = int(payload.get("count", 1) or 1)
                addr = payload.get("address") or {}
                prefer_pool_rotate = _prefer_pool_rotate_from_payload(payload)
                sku, imtp_fb = _resolve_order_sku_imtp(payload)
                cdata = payload.get("composeData")
                if not isinstance(cdata, dict):
                    cdata = None
                shop_submit = str(
                    payload.get("composeShopId") or payload.get("shopId") or ""
                ).strip()
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                if not rec_id or not sku:
                    return self._send_json(json_err(
                        "下单需要 priorityRecordId 与商品 ID（itemCode，数字）"
                    ), 400)
                err_sku = _order_sku_digit_error(sku, "下单")
                if err_sku:
                    return self._send_json(json_err(err_sku), 400)
                if _truthy(payload.get("mockAutoChain")):
                    mock_order_id = f"{int(time.time() * 1000)}{str(phone)[-4:]}"
                    mock_submit = {
                        "code": 2000,
                        "message": "模拟下单成功",
                        "data": {
                            "orderId": mock_order_id,
                            "priorityRecordId": rec_id,
                            "transactionId": (
                                str(cdata.get("transactionId") or "")
                                if isinstance(cdata, dict)
                                else ""
                            ),
                        },
                        "_mockAutoChain": True,
                    }
                    return self._send_json(json_ok(mock_submit))
                s = e.ensure_session()
                s.force_order_chain_direct()
                prefer_pool_rotate = False
                if prefer_pool_rotate:
                    try:
                        s._compose_pin_proxy_url = ""
                        s._rush_success_proxy_url_norm = ""
                        s.advance_active_pool_line_for_batch_order_addr()
                    except Exception:
                        pass
                addr, addr_hint = _resolve_ship_address_for_order(phone, addr, s)
                if not addr:
                    return self._send_json(json_err(addr_hint or "address 无效"), 400)
                res = s.submit(
                    spu=sku,
                    cnt=cnt,
                    rec_id=rec_id,
                    addr=addr,
                    compose_data=cdata,
                    imtp_fallback=imtp_fb,
                    shop=shop_submit,
                    use_rush_pin=not prefer_pool_rotate,
                )
                if addr_hint and isinstance(res, dict):
                    res = dict(res)
                    res["_addressHint"] = addr_hint
                _debug_dump_mt("/api/accounts/submit", payload, res)
                return self._send_json(json_ok(res))

            # 用指定账号支付并提取支付链接 {phone, orderId, payMethod?, channel?}
            if path == "/api/accounts/pay_link":
                phone = str(payload.get("phone", "")).strip()
                order_id = str(payload.get("orderId", "")).strip()
                pay_method = int(payload.get("payMethod", 0) or 0)
                channel = str(payload.get("channel", "70")).strip() or "70"
                # #region agent log
                try:
                    _dbg_p = Path(__file__).resolve().parent / "debug-67b662.log"
                    _dbg_p.open("a", encoding="utf-8").write(
                        json.dumps(
                            {
                                "sessionId": "67b662",
                                "runId": "pay-link-debug-v1",
                                "hypothesisId": "H1",
                                "location": "webui_server.py:/api/accounts/pay_link",
                                "message": "request_enter",
                                "data": {
                                    "channel": channel,
                                    "payMethod": pay_method,
                                    "orderIdLen": len(order_id),
                                },
                                "timestamp": int(time.time() * 1000),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                except Exception:
                    pass
                # #endregion
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                if not order_id:
                    return self._send_json(json_err("orderId 不能为空"), 400)
                prefer_pool_rotate = _prefer_pool_rotate_from_payload(payload)
                if _truthy(payload.get("mockAutoChain")):
                    mock_url = (
                        "https://mock-pay.local/maotai?"
                        + urlencode({"phone": phone, "orderId": order_id, "channel": channel})
                    )
                    out = {
                        "phone": phone,
                        "orderId": order_id,
                        "payMethod": pay_method,
                        "channel": channel,
                        "pay": {
                            "code": 2000,
                            "message": "模拟支付下单成功",
                            "data": {
                                "tn": f"MOCKTN{order_id[-10:]}{channel}",
                                "channelTradeSn": f"MOCKTN{order_id[-10:]}{channel}",
                            },
                        },
                        "gateway": {
                            "code": 200,
                            "message": "模拟网关成功",
                            "data": {"AUTH_CODE": f"888888{order_id[-10:]}"},
                        },
                        "tn": f"MOCKTN{order_id[-10:]}{channel}",
                        "payUrl": mock_url,
                        "payUrlHttps": mock_url,
                        "alipayH5PayUrl": mock_url if channel == "70" else "",
                        "_mockAutoChain": True,
                        "_payLinkProxyDiag": {
                            "preferPoolRotate": bool(prefer_pool_rotate),
                            "mock": True,
                        },
                    }
                    if channel in ("80", "20", "2"):
                        out["ysfAuthCode"] = f"888888{order_id[-10:]}"
                        out["ysfAndroidUrl"] = _build_ysf_android_url(out["ysfAuthCode"])
                        out["ysfIosUrl"] = _build_ysf_ios_url(out["ysfAuthCode"])
                    return self._send_json(json_ok(out))
                s = e.ensure_session()
                s.force_order_chain_direct()
                prefer_pool_rotate = False
                old_pay_prefer_pool = getattr(s, "_pay_prefer_pool_rotate", False)
                s._pay_prefer_pool_rotate = bool(prefer_pool_rotate)
                if prefer_pool_rotate:
                    try:
                        s._compose_pin_proxy_url = ""
                        s._rush_success_proxy_url_norm = ""
                        s.advance_active_pool_line_for_batch_order_addr()
                    except Exception:
                        pass
                # 与 ensure_session 双写：内存里的 rush_success 若刚写入，避免 Session 的 _rush_success_proxy_url_norm 仍为空
                _ru = (getattr(e, "rush_success_proxy_url", "") or "").strip()
                if _ru and not prefer_pool_rotate:
                    try:
                        from app import _normalize_proxy_url as _nru

                        s._rush_success_proxy_url_norm = _nru(_ru)
                    except Exception:
                        s._rush_success_proxy_url_norm = _ru
                else:
                    s._rush_success_proxy_url_norm = ""
                # 100% demo 模式（C:\Users\Administrator\Desktop\快手短句\py1.9.6\demo.py）：
                # pay_order(多 body 轮试) -> request_pay -> SDK 解析 -> H5
                out = {
                    "phone": phone,
                    "orderId": order_id,
                    "payMethod": pay_method,
                    "channel": channel,
                }
                out["_payLinkProxyDiag"] = {
                    "rushSuccessHost": _proxy_hostport_for_log(_ru) if _ru else "",
                    "sessionRushNormHost": _proxy_hostport_for_log(
                        getattr(s, "_rush_success_proxy_url_norm", "") or ""
                    ),
                    "payOrderUseRushProxy": (
                        (os.environ.get("MT_PAY_ORDER_USE_RUSH_PROXY", "1") or "1").strip().lower()
                        not in ("0", "false", "no", "off")
                    ),
                    "preferPoolRotate": bool(prefer_pool_rotate),
                }
                try:
                    pay_res = s.pay(order_id, method=pay_method, channel=channel)
                    out["pay"] = pay_res
                    pdata = pay_res.get("data") if isinstance(pay_res, dict) else None
                    if isinstance(pay_res, dict) and _mt_business_ok(pay_res.get("code")):
                        # demo.py 优先 tn；兼容现网 channelTradeSn 回包
                        tn = ""
                        if isinstance(pdata, dict):
                            tn = str(
                                pdata.get("tn")
                                or pdata.get("channelTradeSn")
                                or pdata.get("tradeNo")
                                or pdata.get("payTn")
                                or ""
                            ).strip()
                        if not tn:
                            tn = _tn_from_pay_data(pdata)
                        out["tn"] = tn
                        if tn:
                            gw = None
                            gw_attempts = []
                            for attempt_i in range(3):
                                gw = s.gateway(tn, ch=channel)
                                c = gw.get("code") if isinstance(gw, dict) else None
                                gw_attempts.append({"attempt": attempt_i + 1, "code": c})
                                if isinstance(gw, dict) and _gateway_code_ok(c):
                                    break
                                if (
                                    attempt_i < 2
                                    and isinstance(gw, dict)
                                    and _gateway_should_retry_pay(c, gw)
                                ):
                                    time.sleep(0.85)
                                    continue
                                break
                            out["gateway"] = gw
                            # #region agent log
                            try:
                                _dbg_p = Path(__file__).resolve().parent / "debug-67b662.log"
                                _dbg_p.open("a", encoding="utf-8").write(
                                    json.dumps(
                                        {
                                            "sessionId": "67b662",
                                            "runId": "pay-link-debug-v1",
                                            "hypothesisId": "H2",
                                            "location": "webui_server.py:/api/accounts/pay_link",
                                            "message": "gateway_result",
                                            "data": {
                                                "channel": channel,
                                                "tnPresent": bool(tn),
                                                "payCode": pay_res.get("code") if isinstance(pay_res, dict) else None,
                                                "gatewayCode": gw.get("code") if isinstance(gw, dict) else None,
                                                "attempts": len(gw_attempts),
                                            },
                                            "timestamp": int(time.time() * 1000),
                                        },
                                        ensure_ascii=False,
                                    )
                                    + "\n"
                                )
                            except Exception:
                                pass
                            # #endregion
                            if len(gw_attempts) > 1:
                                out["gatewayRetryMeta"] = gw_attempts
                            if isinstance(gw, dict):
                                sdk = ""
                                if _gateway_code_ok(gw.get("code")):
                                    sdk = _sdk_string_from_gateway_data(gw.get("data"))
                                if not sdk:
                                    sdk = _sdk_string_from_gateway_data(gw)
                                pay_url = _resolve_pay_url_for_notify(
                                    sdk,
                                    pay_res if isinstance(pay_res, dict) else None,
                                    gw,
                                    pdata if isinstance(pdata, dict) else None,
                                    proxy_kw=s.proxy_kw_for_pay_link_resolve(),
                                    impersonate=TLS_PROFILE,
                                )
                                # 云闪付兜底：网关未返回 uppay:// 等可直接跳转的链接时，
                                # 用 TN + orderId 构建 moutaiapp://mtpaysdk?... 原生路由
                                if not pay_url and channel in ("80", "20", "2"):
                                    pay_url = _build_unionpay_native_route(
                                        tn, order_id, gw, pdata if isinstance(pdata, dict) else None
                                    )
                                    if pay_url:
                                        out["unionpayNativeRoute"] = True
                                # 云闪付直跳：安卓/iOS 对齐 py版，只认网关 AUTH_CODE。
                                if channel in ("80", "20", "2"):
                                    _gw_data = gw.get("data") if isinstance(gw, dict) else None
                                    _auth_code = ""
                                    if isinstance(_gw_data, dict):
                                        _auth_code = _extract_ysf_android_tn(
                                            _gw_data.get("AUTH_CODE") or _gw_data.get("authCode") or ""
                                        )
                                    if _auth_code:
                                        out["ysfAuthCode"] = _auth_code
                                        _ysf_android = _build_ysf_android_url(_auth_code)
                                        if _ysf_android:
                                            out["ysfAndroidUrl"] = _ysf_android
                                            if channel == "80":
                                                pay_url = _ysf_android
                                        _ysf_ios = _build_ysf_ios_url(_auth_code)
                                        if _ysf_ios:
                                            out["ysfIosUrl"] = _ysf_ios
                                if pay_url:
                                    out["payUrl"] = pay_url
                                    if str(pay_url).lower().startswith("https://"):
                                        out["payUrlHttps"] = pay_url
                                        out["alipayH5PayUrl"] = pay_url
                                    try:
                                        pn = _norm_phone(phone)
                                        nick = str(e.nickname or "").strip() or pn
                                        remark_l = "未备注" if nick == pn else nick
                                        wechat_notify.send_pay_notify(pn, pay_url, remark_l)
                                    except Exception as wx_ex:
                                        out["wechatNotifyError"] = str(wx_ex)
                                # #region agent log
                                try:
                                    _dbg_p = Path(__file__).resolve().parent / "debug-67b662.log"
                                    _dbg_p.open("a", encoding="utf-8").write(
                                        json.dumps(
                                            {
                                                "sessionId": "67b662",
                                                "runId": "pay-link-debug-v1",
                                                "hypothesisId": "H4",
                                                "location": "webui_server.py:/api/accounts/pay_link",
                                                "message": "pay_url_output",
                                                "data": {
                                                    "channel": channel,
                                                    "hasPayUrl": bool(pay_url),
                                                    "payUrlPrefix": str(pay_url or "")[:28],
                                                },
                                                "timestamp": int(time.time() * 1000),
                                            },
                                            ensure_ascii=False,
                                        )
                                        + "\n"
                                    )
                                except Exception:
                                    pass
                                # #endregion
                            try:
                                s._order_pay_egress_for_gateway = None
                            except Exception:
                                pass
                    _debug_dump_mt("/api/accounts/pay_link", payload, out)
                    return self._send_json(json_ok(out))
                finally:
                    try:
                        s._pay_prefer_pool_rotate = old_pay_prefer_pool
                    except Exception:
                        pass

            # H5 订单列表 GET（与抓包 /xhr/front/trade/order/list/get 一致；query 参与 shield_for_get）
            if path == "/api/accounts/order_list":
                phone = str(payload.get("phone", "")).strip()
                referer = str(payload.get("referer", "")).strip()
                try:
                    page_no = int(payload.get("pageNo") or payload.get("page") or 1)
                except (TypeError, ValueError):
                    page_no = 1
                try:
                    page_size = int(payload.get("pageSize") or payload.get("size") or 20)
                except (TypeError, ValueError):
                    page_size = 20
                eq = payload.get("extraQuery")
                extra = eq if isinstance(eq, dict) else None
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                s = e.ensure_session()
                if _json_truthy(payload.get("batchOrderAddrPoolRotate")):
                    s.advance_active_pool_line_for_batch_order_addr()
                elif _json_truthy(payload.get("forceDirect")) or _json_truthy(payload.get("orderQueryForceDirect")):
                    s.force_order_chain_direct()
                res = s.order_list_h5(
                    page_no=page_no,
                    page_size=page_size,
                    referer=referer,
                    extra_query=extra,
                )
                _debug_dump_mt("/api/accounts/order_list", payload, res)
                return self._send_json(json_ok({"phone": phone, "orderList": res}))

            # H5 订单详情 GET：/xhr/front/trade/order/detail/get?__timestamp=...&orderId=...
            if path == "/api/accounts/order_detail":
                phone = str(payload.get("phone", "")).strip()
                order_id = str(payload.get("orderId", "") or payload.get("order_id", "")).strip()
                order_level = str(payload.get("orderLevel", "2") or "2").strip()
                referer = str(payload.get("referer", "")).strip()
                ts = payload.get("__timestamp", payload.get("timestampMs"))
                if not phone or not order_id:
                    return self._send_json(json_err("phone 和 orderId 不能为空"), 400)
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                s = e.ensure_session()
                if _json_truthy(payload.get("forceDirect")) or _json_truthy(payload.get("orderQueryForceDirect")):
                    s.force_order_chain_direct()
                res = s.order_detail_h5(
                    order_id=order_id,
                    order_level=order_level,
                    referer=referer,
                    timestamp_ms=ts,
                )
                _debug_dump_mt("/api/accounts/order_detail", payload, res)
                return self._send_json(json_ok({"phone": phone, "orderDetail": res}))

            # 取消最新订单：先查该账号最新 orderId，再固定 reason=10 / orderStatus=1 调 cancel
            if path == "/api/accounts/order_cancel_latest":
                phone = str(payload.get("phone", "")).strip()
                order_level = str(payload.get("orderLevel", "2") or "2").strip()
                if not phone:
                    return self._send_json(json_err("phone 不能为空"), 400)
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                s = e.ensure_session()
                s.force_order_chain_direct()
                list_res = s.order_list_h5(page_no=1, page_size=20, referer="", extra_query=None)
                rows = _mobile_order_pick_rows(list_res.get("json") if isinstance(list_res, dict) else None)
                parsed: list[tuple[dict, dict]] = []
                for i, raw in enumerate(rows):
                    summary = _mobile_order_summary(phone, raw, i)
                    if summary.get("orderId"):
                        parsed.append((raw, summary))
                parsed.sort(key=lambda x: float(x[1].get("sortTs") or 0), reverse=True)
                if not parsed:
                    out = {
                        "phone": phone,
                        "orderList": list_res,
                        "message": "未查到可用订单号",
                    }
                    _debug_dump_mt("/api/accounts/order_cancel_latest", payload, out)
                    return self._send_json({"ok": False, "message": "未查到可用订单号", "data": out}, 400)
                _raw, latest = parsed[0]
                order_id = str(latest.get("orderId") or "").strip()
                referer = f"{H5_HOST}/mt/order/detail?appConfig=1_0_2&orderId={quote(order_id)}&orderLevel={order_level or '2'}"
                cancel_res = s.order_ops_cancel_h5(
                    order_id=order_id,
                    order_status=1,
                    reason=10,
                    order_level=order_level,
                    referer=referer,
                )
                out = {
                    "phone": phone,
                    "orderId": order_id,
                    "orderStatus": 1,
                    "reason": 10,
                    "latest": latest,
                    "orderList": list_res,
                    "orderCancel": cancel_res,
                }
                _debug_dump_mt("/api/accounts/order_cancel_latest", payload, out)
                return self._send_json(json_ok(out))

            # 确认收货 {phone, orderId, orderLevel?}
            if path == "/api/accounts/confirm_receipt":
                phone = str(payload.get("phone", "")).strip()
                order_id = str(payload.get("orderId", "")).strip()
                order_level = str(payload.get("orderLevel", "2") or "2").strip()
                if not phone or not order_id:
                    return self._send_json(json_err("phone 和 orderId 不能为空"), 400)
                e = POOL.get(phone)
                if not e:
                    return self._send_json(json_err("账号不存在"), 404)
                if e.status != STATUS_OK:
                    return self._send_json(json_err("账号未登录"), 400)
                s = e.ensure_session()
                res = s.confirm_receipt_h5(order_id=order_id, order_level=order_level)
                _debug_dump_mt("/api/accounts/confirm_receipt", payload, res)
                return self._send_json(json_ok(res))

            # ══════════════════════════════════════════

            return self._send_json(json_err("not found"), 404)
        except Exception as e:
            traceback.print_exc()
            detail = f"{e}\n{traceback.format_exc()}"
            head = f"{type(e).__name__}: {e}"
            if len(head) > 280:
                head = head[:280] + "…"
            return self._send_json(
                json_err(f"服务异常：{head}", detail=detail[:8000]),
                500,
            )


def _print_remote_login_links() -> None:
    """同网 / 异网可复制链接（异网须能访问到本机主端口或穿透地址）。"""
    tail = ""
    if REMOTE_LOGIN_SECRET:
        tail = f"?token={quote(REMOTE_LOGIN_SECRET, safe='')}"
    elif LISTEN_HOST in ("0.0.0.0", "::"):
        print(
            "  [安全] 未设置 MT_REMOTE_LOGIN_SECRET：外网/同网谁都能对上号，建议尽快设置口令。",
            flush=True,
        )

    lip = _primary_lan_ipv4()
    if lip:
        print(
            f"  上号页·局域网/同一路由: http://{lip}:{PORT}/remote_login.html{tail}",
            flush=True,
        )
    if PUBLIC_BASE_URL:
        print(
            f"  上号页·穿透/域名（异网）: {PUBLIC_BASE_URL}/remote_login.html{tail}",
            flush=True,
        )
    elif LISTEN_HOST == "0.0.0.0":
        print(
            "  异网（如手机 4G）访问：须在路由器做端口映射到本机 "
            f"{PORT}，或使用 ngrok/frp 等穿透；设 MT_PUBLIC_BASE_URL=https://xxx 可自动打印对外链接。",
            flush=True,
        )


def main():
    try:
        servers = bind_webui_servers(for_embedded=False)
    except OSError as ex:
        print(f"[webui] 启动失败: {ex}", flush=True)
        raise SystemExit(1) from ex
    print(
        f"UI 服务已启动: http://{LOCAL_UI_HOST}:{PORT}/ （本机管理页） API 分片端口: {API_LISTEN_PORTS}",
        flush=True,
    )
    print(
        f"[安全] 控制台 IP 白名单: {CONTROL_CONSOLE_IP_ALLOWLIST_RAW or '*'} "
        f"（remote_login/mobile_order 公开页及其接口不受此限制）",
        flush=True,
    )
    print(
        f"  端口策略：默认仅主端口 {PORT}；多端口（突破浏览器同域并发）请设 MT_WEBUI_ONE_PORT_PER_ACCOUNT=1，"
        "或设 MT_WEBUI_API_SHARD_COUNT>1（固定条数 + 前端哈希）。",
        flush=True,
    )
    if LISTEN_HOST not in ("127.0.0.1", "::1", "localhost"):
        print(f"  监听: {LISTEN_HOST}（任意来源 IP 可连，取决于网络是否通）", flush=True)
    if LISTEN_HOST == "0.0.0.0":
        _print_remote_login_links()
        if len(API_LISTEN_PORTS) > 1:
            print(
                f"  [防火墙] 多 API 端口：局域网访问时除主端口 {PORT} 外还须放行 "
                f"{', '.join(str(p) for p in API_LISTEN_PORTS[1:])}（或改 MT_WEBUI_API_SHARD_COUNT=1）。",
                flush=True,
            )
    print(
        "说明: 设备ID/指纹/签名参数都由 app.py Session 内部自动处理。",
        flush=True,
    )
    print(
        "代理: 429/失败轮换在池内 URL 之后默认追加本机直连兜底；只要代理不要直连可设 MT_PROXY_POOL_NO_DIRECT=1。"
        " 详调可设 MT_PROXY_LOG=1。",
        flush=True,
    )
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        shutdown_all_webui_servers()


if __name__ == "__main__":
    main()
