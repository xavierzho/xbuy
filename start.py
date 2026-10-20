#!/usr/bin/env python3
"""
i茅科技 内部定制🚀
- 使用 QWebEngineView 直接加载 ui.html
- 内置启动 webui_server.py 提供 /api/*，按钮功能与网页完全一致

启动优化（环境变量）：
- MT_SYNC_GO_BRIDGE_BOOT=1：WebUI 绑定前同步等待 go_bridge（rush_bridge）就绪；兼容旧名 MT_SYNC_NODE_BRIDGE_BOOT
- MT_WEBENGINE_GPU_EXTRA：覆盖默认 GPU 附加 Chromium 参数；设为空字符串可关闭默认附加项
- MT_WEBENGINE_DISABLE_GPU=1 或 MT_WEBENGINE_SAFE_MODE=1：关闭 GPU 光栅/合成（部分集显/远程桌面下发码、批量时易白屏可试）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
import urllib.error
import urllib.request
import urllib.parse

# 抢购 BB 固定使用 shield.py 的 py 稳定版链路；无需设置老/新 BB 环境开关。

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtNetwork import QNetworkProxy, QNetworkProxyFactory
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox

try:
    from PyQt6.QtWebEngineCore import QWebEngineSettings
except ImportError:  # pragma: no cover
    QWebEngineSettings = None  # type: ignore[misc,assignment]

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except Exception as e:  # pragma: no cover
    print("缺少 PyQt6-WebEngine，请先安装: py -3 -m pip install PyQt6-WebEngine")
    raise

# 定制版默认：抢购 + 组单/下单/支付等 App 请求均走本机 go_bridge（rush_bridge，默认端口见 MT_GO_BRIDGE_PORT）；
# - MT_RUSH_HTTP_NODE：抢购 POST（Session.rush → /rush_loop）
# - MT_HTTP_BACKEND=node：Session._req_post/_req_get → /req（组单 compose/v2、下单 submit/v2、支付、网关等）
# 若需某类改回 Python curl：启动前设 MT_RUSH_HTTP_NODE=0 或 MT_HTTP_BACKEND=（非 node/1/true/yes）
os.environ.setdefault("MT_RUSH_HTTP_NODE", "1")
os.environ.setdefault("MT_BATCH_RUSH_NODE", "1")
os.environ.setdefault("MT_RUSH_SOFT429_ROTATE", "1")
os.environ.setdefault("MT_RUSH_SOFT429_RETRY", "auto")
os.environ.setdefault("MT_HTTP_BACKEND", "node")
# 写死：TLS worker 侧恒走 Python curl_cffi_worker（与 node_bridge/server.cjs 无混名关系）；环境变量仅作兼容。
os.environ["MT_NODE_USE_CURL_CFFI_TLS"] = "1"
os.environ["MT_NODE_TLS_FALLBACK_AXIOS"] = "0"
os.environ["MT_PREWARM_USE_CURL_CFFI"] = "1"
# 默认开启常驻 curl worker 池：降低高并发下频繁拉起子进程的排队与抖动。
# 如需优先降低内存占用可手动设 MT_CURL_WORKER_POOL=0。
os.environ.setdefault("MT_CURL_WORKER_POOL", "1")
# 默认开启：组单/下单与抢购同一出口（与 accounts 中 rush_success 钉线一致），避免组单日志与抢购 IP 不一致、支付网关 250。
# 若需组单阶段仍按池轮换：启动前设 MT_COMPOSE_USE_RUSH_PIN=0
os.environ.setdefault("MT_COMPOSE_USE_RUSH_PIN", "1")
# 内嵌 WebEngine 对「同一 http 源」并发连接很少；启用一账号一监听端口，与账号数同步扩容（accounts 保存时触发），
# 抢购/组单等按手机号走不同源，避免批量时大量号在浏览器侧排队。关闭：启动前设 MT_WEBUI_ONE_PORT_PER_ACCOUNT=0
os.environ.setdefault("MT_WEBUI_ONE_PORT_PER_ACCOUNT", "1")
# 与 mt test-full-flow 一致：发验证码前先跑浩天，把 BS-DVID + deviceConfig（sdk/厂商/机型）写入账号再发码。
# 关闭：MT_HAOTIAN_BEFORE_VCODE=0
os.environ.setdefault("MT_HAOTIAN_BEFORE_VCODE", "1")
# WebUI 启动后定时跑 node_bridge/bb/update.cjs --auto（App Store 版本 + sign_wasm/runtime）
os.environ.setdefault("MT_BB_AUTO_UPDATE", "1")
os.environ.setdefault("MT_BB_UPDATE_MODE", "sync")
os.environ.setdefault("MT_BB_AUTO_UPDATE_INTERVAL_SEC", "21600")
os.environ.setdefault("MT_BB_AUTO_UPDATE_ON_START", "1")
# 与 webui_server.PORT 一致（须在 import webui_server 之前设置）
os.environ.setdefault("MT_WEBUI_PORT", "8989")

from webui_server import (
    DIST_WS_HOST,
    DIST_WS_PORT,
    LISTEN_HOST,
    LOCAL_UI_HOST,
    PORT,
    bind_webui_servers,
    schedule_go_bridge_boot_if_configured,
    shutdown_all_webui_servers,
)

# _start_embedded_server 返回的 port 特殊值：负数为致命错误码
ERR_PORT_BIND = -1
ERR_STALE_WEBUI = -2


def _truthy(v: str | None, default: bool = False) -> bool:
    s = (v or "").strip().lower()
    if not s:
        return default
    return s in ("1", "true", "yes", "on")


def _resolve_cloudflared_bin() -> str | None:
    custom = (os.environ.get("MT_CLOUDFLARED_BIN") or "").strip()
    if custom:
        return custom
    p = os.path.join(os.path.dirname(__file__), "cloudflared.exe")
    if os.path.isfile(p):
        return p
    found = shutil.which("cloudflared")
    if found:
        return found
    return None


class _LegacyCloudflaredTunnel:
    """本地 WebUI -> trycloudflare 公网临时链接。"""

    def __init__(self, local_port: int):
        self.local_port = int(local_port)
        self.proc: subprocess.Popen[str] | None = None
        self.reader_thread: threading.Thread | None = None
        self.public_base_url = ""
        self._lock = threading.Lock()

    def start(self) -> None:
        if (os.environ.get("MT_PUBLIC_BASE_URL") or "").strip():
            print("[cloudflared] 已设置 MT_PUBLIC_BASE_URL，跳过自动穿透。", flush=True)
            return
        if not _truthy(os.environ.get("MT_CLOUDFLARED_AUTOSTART"), default=True):
            print("[cloudflared] 已禁用自动穿透（MT_CLOUDFLARED_AUTOSTART=0）。", flush=True)
            return
        bin_path = _resolve_cloudflared_bin()
        if not bin_path:
            print("[cloudflared] 未找到 cloudflared.exe，跳过自动穿透。", flush=True)
            return
        cmd = [
            bin_path,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{self.local_port}",
            "--no-autoupdate",
        ]
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                bufsize=1,
            )
        except Exception as ex:
            print(f"[cloudflared] 启动失败: {ex}", flush=True)
            self.proc = None
            return
        print(f"[cloudflared] 已启动: {' '.join(cmd)}", flush=True)
        self.reader_thread = threading.Thread(
            target=self._read_output_loop,
            daemon=True,
            name="cloudflared-reader",
        )
        self.reader_thread.start()

    def _read_output_loop(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        url_re = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
        for line in proc.stdout:
            s = str(line or "").strip()
            if not s:
                continue
            m = url_re.search(s)
            if m:
                base = m.group(0).rstrip("/")
                with self._lock:
                    if not self.public_base_url:
                        self.public_base_url = base
                        tail = ""
                        token = (os.environ.get("MT_REMOTE_LOGIN_SECRET") or "").strip()
                        if token:
                            tail = f"?token={urllib.parse.quote(token, safe='')}"
                        full = f"{base}/remote_login.html{tail}"
                        print(f"[cloudflared] 公网入口: {full}", flush=True)
                        try:
                            p = Path(__file__).resolve().parent / "remote_tunnel_url.txt"
                            p.write_text(
                                f"{full}\n"
                                f"# 说明：trycloudflare 为临时域名，软件重启或 cloudflared 退出后失效；"
                                f"请以本次启动控制台最新一行 [cloudflared] 公网入口 为准。\n",
                                encoding="utf-8",
                            )
                        except OSError:
                            pass

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


class _CloudflaredTunnel:
    """Start public whitelist gates and cloudflared quick tunnels for remote pages."""

    def __init__(self, local_port: int):
        self.local_port = int(local_port)
        self.login_port = int(os.environ.get("MT_PUBLIC_LOGIN_PORT") or "7979")
        self.order_port = int(os.environ.get("MT_PUBLIC_ORDER_PORT") or "6969")
        self.gate_procs: list[subprocess.Popen[str]] = []
        self.cf_procs: list[subprocess.Popen[str]] = []
        self.reader_threads: list[threading.Thread] = []
        self.urls: dict[str, str] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if not _truthy(os.environ.get("MT_CLOUDFLARED_AUTOSTART"), default=True):
            print("[cloudflared] 已禁用自动穿透（MT_CLOUDFLARED_AUTOSTART=0）。", flush=True)
            return
        if not _truthy(os.environ.get("MT_CLOUDFLARED_PUBLIC_PAGES"), default=True):
            print("[cloudflared] 已禁用公开页穿透（MT_CLOUDFLARED_PUBLIC_PAGES=0）。", flush=True)
            return

        gate_script = Path(__file__).resolve().parent / "public_gate.py"
        if not gate_script.exists():
            print(f"[public-gate] 缺少 {gate_script.name}，跳过公开页穿透。", flush=True)
            return

        entries = [
            ("login", "上号页", "login", self.login_port, "remote_login.html"),
            ("order", "查单页", "order", self.order_port, "mobile_order.html"),
        ]
        for label, title, mode, port, page in entries:
            self._start_gate(gate_script, label, title, mode, port, page)

        bin_path = _resolve_cloudflared_bin()
        if not bin_path:
            print("[cloudflared] 未找到 cloudflared.exe/cloudflared，已只启动本地白名单端口。", flush=True)
            return

        for label, title, _mode, port, page in entries:
            self._start_cloudflared(bin_path, label, title, port, page)

    def _start_gate(
        self,
        gate_script: Path,
        label: str,
        title: str,
        mode: str,
        port: int,
        page: str,
    ) -> None:
        env = os.environ.copy()
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PUBLIC_GATE_REQUIRE_TOKEN": "0",
                "PUBLIC_GATE_LISTEN_HOST": "127.0.0.1",
                "PUBLIC_GATE_MODE": mode,
                "PUBLIC_GATE_PORT": str(port),
                "PUBLIC_GATE_TARGET": f"http://127.0.0.1:{self.local_port}",
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
        print(
            f"[public-gate-{label}] {title} 本地入口: http://127.0.0.1:{port}/{page}",
            flush=True,
        )

    def _start_cloudflared(
        self,
        bin_path: str,
        label: str,
        title: str,
        port: int,
        page: str,
    ) -> None:
        cmd = [
            bin_path,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{port}",
            "--protocol",
            "http2",
            "--no-autoupdate",
        ]
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
            if m and label and title and page:
                self._record_public_url(label, title, m.group(0).rstrip("/"), page)
                continue
            low = s.lower()
            if "error" in low or "failed" in low or "forbidden" in low:
                print(f"[{prefix}] {s}", flush=True)

    def _record_public_url(self, label: str, title: str, base: str, page: str) -> None:
        full = f"{base}/{page}"
        with self._lock:
            if self.urls.get(label) == full:
                return
            self.urls[label] = full
            print(f"[cloudflared-{label}] {title}公网入口: {full}", flush=True)
            self._write_url_file()

    def _write_url_file(self) -> None:
        try:
            p = Path(__file__).resolve().parent / "remote_tunnel_url.txt"
            lines = [
                "# trycloudflare 为临时域名，软件重启或 cloudflared 退出后会失效。",
                "# 7979=上号白名单端口；6969=查单白名单端口；外网以 cloudflared 打印的 URL 为准。",
            ]
            if "login" in self.urls:
                lines.append(f"上号页={self.urls['login']}")
            if "order" in self.urls:
                lines.append(f"查单页={self.urls['order']}")
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
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


def _configure_chromium_flags() -> None:
    """在创建 QApplication 之前设置。含：本机 WebUI 不走系统/环境代理（避免 127.0.0.1 白屏）；可选 GPU 合成。"""
    # QNetworkProxy 无法约束 Chromium 内核；须用 proxy-bypass-list，否则 HTTP_PROXY 下常见「页面未加载」。
    bypass = "--proxy-bypass-list=127.0.0.1/8,localhost,::1"
    safe_gpu = _truthy(os.environ.get("MT_WEBENGINE_DISABLE_GPU")) or _truthy(
        os.environ.get("MT_WEBENGINE_SAFE_MODE")
    )
    if safe_gpu:
        # 发码/浩天/批量抢购时 Web 页重、并发 fetch 多，集显/远程桌面 + 默认 GPU 路径易触发渲染进程挂掉 → 整窗发白
        base_parts = [
            "--disable-gpu",
            "--disable-gpu-compositing",
            "--disable-backgrounding-occluded-windows",
        ]
        gpu_extra = ""
    else:
        # ignore-gpu-blocklist：部分集显/驱动被 Chromium 误判时仍可用 GPU 光栅化。
        # 不需要附加项时：启动前 set MT_WEBENGINE_GPU_EXTRA=（空字符串）
        if "MT_WEBENGINE_GPU_EXTRA" in os.environ:
            gpu_extra = (os.environ.get("MT_WEBENGINE_GPU_EXTRA") or "").strip()
        else:
            gpu_extra = "--ignore-gpu-blocklist --enable-gpu-compositing"
        base_parts = [
            "--enable-gpu-rasterization",
            "--enable-zero-copy",
            "--disable-backgrounding-occluded-windows",
        ]
        if gpu_extra:
            base_parts.append(gpu_extra)
    base_parts.append(bypass)
    base = " ".join(base_parts)
    cur = (os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS") or "").strip()
    if not cur:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = base.strip()
    elif "proxy-bypass-list" not in cur:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = cur + " " + bypass


def _connect_webengine_crash_recovery(view: QWebEngineView, url_supplier) -> None:
    """渲染进程崩溃（OOM/GPU/卡死被杀）后整窗会发白；自动拉回 ui.html。"""

    def _on_render_process_terminated(termination_status, exit_code: int) -> None:
        print(
            f"[webengine] 渲染进程结束 status={termination_status} exit={exit_code} "
            f"（多发码/批量时常见）；将重新加载 UI…",
            flush=True,
        )

        def _reload() -> None:
            try:
                u = url_supplier()
                if u:
                    view.setUrl(QUrl(u))
                else:
                    view.reload()
            except Exception as ex:
                print(f"[webengine] 自动 reload 失败: {ex}", flush=True)

        QTimer.singleShot(600, _reload)

    try:
        view.renderProcessTerminated.connect(_on_render_process_terminated)
    except Exception:
        pass


def _apply_webengine_settings(view: QWebEngineView) -> None:
    if QWebEngineSettings is None:
        return
    s = view.settings()
    attr = QWebEngineSettings.WebAttribute
    try:
        s.setAttribute(attr.ScrollAnimatorEnabled, True)
    except Exception:
        pass
    try:
        # 支付通知表「打开」用 window.open / 程序化 <a target=_blank>；默认关闭会导致点了没反应
        s.setAttribute(attr.JavascriptCanOpenWindows, True)
    except Exception:
        pass
    try:
        s.setAttribute(attr.WebGLEnabled, True)
        s.setAttribute(attr.Accelerated2dCanvasEnabled, True)
    except Exception:
        pass


def _disable_qt_network_proxy() -> None:
    """避免系统/环境 HTTP 代理把本机 loopback 走代理，导致 QWebEngine 白屏而 urllib 仍健康。"""
    QNetworkProxyFactory.setUseSystemConfiguration(False)
    no_proxy = QNetworkProxy()
    no_proxy.setType(QNetworkProxy.ProxyType.NoProxy)
    QNetworkProxy.setApplicationProxy(no_proxy)


def _is_port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_service_healthy(host: str, port: int, timeout: float = 1.0) -> bool:
    # 必须直连本机：若进程里加载了 HTTP_PROXY（如 local_proxy.env），默认 urlopen 会走代理，
    # 公有代理无法访问 127.0.0.1 → 超时 → 误判「启动失败」。
    url = f"http://{host}:{port}/api/state"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(url)
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(256).decode("utf-8", errors="ignore")
            return resp.status == 200 and '"ok": true' in body.lower()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
        return False


def _is_real_webui_ui_html(host: str, port: int, timeout: float = 2.5) -> bool:
    """
    确认 /ui.html 由本仓库 Python webui_server 提供，而不是误占用 WebUI 端口的 Node/Express
    （后者典型响应为纯文本「Cannot GET /ui.html」）。
    """
    url = f"http://{host}:{port}/ui.html"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(url)
        with opener.open(req, timeout=timeout) as resp:
            if int(resp.status) != 200:
                return False
            chunk = resp.read(12288).decode("utf-8", errors="ignore")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError):
        return False
    low = chunk.lower()
    if "cannot get" in low:
        return False
    # 正常 ui.html 含 doctype/html；缺失文件时 Python 仍返回带 <h1> 的 HTML
    return "<!doctype" in low or "<html" in low or "<h1" in low


def _api_route_refresh_device_params_available(host: str, port: int) -> bool:
    """
    探针：新 webui_server 对空 phone 返回 400 + phone 不能为空；
    旧进程无此路由 → 404 not found（与前端现象一致）。

    注意：urllib 对 4xx/5xx 会抛出 HTTPError，须从异常里读状态码与 body。
    """
    url = f"http://{host}:{port}/api/accounts/refresh_device_params"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = json.dumps({"phone": ""}).encode("utf-8")

    def _once() -> bool:
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with opener.open(req, timeout=3.0) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
                status = int(resp.status)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="ignore")
            status = int(e.code)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        msg = str(body.get("message") or "").strip().lower()
        if status == 400 and ("phone" in msg or "不能为空" in msg):
            return True
        if status == 404 and "not found" in msg:
            return False
        return status != 404

    for attempt in range(2):
        try:
            return _once()
        except (urllib.error.URLError, OSError, TimeoutError):
            if attempt == 0:
                time.sleep(0.2)
                continue
            return True
    return True


def _api_route_distributed_available(host: str, port: int) -> bool:
    """确认已运行的 WebUI 后端带分布式主控接口，避免复用旧进程后节点连不上。"""
    url = f"http://{host}:{port}/api/distributed/status"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(url)
        with opener.open(req, timeout=3.0) as resp:
            raw = resp.read(2048).decode("utf-8", errors="ignore")
            status = int(resp.status)
    except urllib.error.HTTPError as e:
        raw = e.read(2048).decode("utf-8", errors="ignore")
        status = int(e.code)
    except (urllib.error.URLError, OSError, TimeoutError):
        return True
    if status == 404:
        return False
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        body = {}
    if isinstance(body, dict) and body.get("ok") is True and isinstance(body.get("data"), dict):
        return "wsPort" in body["data"] or "nodes" in body["data"]
    return status != 404


def _wait_until_service_ready(host: str, port: int, total_sec: float = 8.0) -> bool:
    deadline = time.monotonic() + total_sec
    while time.monotonic() < deadline:
        if _is_port_open(host, port) and _is_service_healthy(host, port):
            return True
        time.sleep(0.1)
    return False


def _wait_until_embedded_ui_ready(host: str, port: int, total_sec: float = 12.0) -> bool:
    """本进程 bind 后：/api/state 与 /ui.html 均就绪再交给 WebEngine 加载。"""
    deadline = time.monotonic() + total_sec
    while time.monotonic() < deadline:
        if (
            _is_port_open(host, port)
            and _is_service_healthy(host, port)
            and _is_real_webui_ui_html(host, port)
        ):
            return True
        time.sleep(0.08)
    return False


def _wait_until_dist_ws_ready(total_sec: float = 4.0) -> bool:
    host = "127.0.0.1" if DIST_WS_HOST in ("0.0.0.0", "::", "") else DIST_WS_HOST
    deadline = time.monotonic() + total_sec
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, DIST_WS_PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _start_embedded_server() -> tuple[list | None, int]:
    """
    返回 (本进程绑定的服务列表, 主端口)。
    复用已存在服务时返回 (None, PORT)；失败返回 (None, ERR_PORT_BIND / ERR_STALE_WEBUI)。
    """
    # 优先复用健康的已运行服务（须带 refresh_device_params，否则界面会 404 not found）
    if _is_port_open(LOCAL_UI_HOST, PORT) and _is_service_healthy(LOCAL_UI_HOST, PORT):
        if not _api_route_refresh_device_params_available(LOCAL_UI_HOST, PORT):
            print(
                f"[webui] {LOCAL_UI_HOST}:{PORT} 上为旧版 webui_server（缺少 /api/accounts/refresh_device_params），"
                "请结束占用该端口的进程后重新启动本程序。",
                file=sys.stderr,
                flush=True,
            )
            return None, ERR_STALE_WEBUI
        if not _api_route_distributed_available(LOCAL_UI_HOST, PORT):
            print(
                f"[webui] {LOCAL_UI_HOST}:{PORT} 上为旧版 webui_server（缺少 /api/distributed/status），"
                "请结束占用该端口的进程后重新启动本程序。",
                file=sys.stderr,
                flush=True,
            )
            return None, ERR_STALE_WEBUI
        if not _is_real_webui_ui_html(LOCAL_UI_HOST, PORT):
            print(
                f"[webui] {LOCAL_UI_HOST}:{PORT} 虽可访问 /api/state，但 /ui.html 不是本程序页面"
                f"（常见为本端口被其它 Node/Express 占用，浏览器会显示 Cannot GET /ui.html）。"
                f"将尝试由本进程绑定 {PORT}…",
                file=sys.stderr,
                flush=True,
            )
            # 不复用，走下方 bind；若端口仍被占则返回 ERR_PORT_BIND
        else:
            print(
                f"[webui] 复用已运行的 http://{LOCAL_UI_HOST}:{PORT}/",
                file=sys.stderr,
                flush=True,
            )
            schedule_go_bridge_boot_if_configured()
            return None, PORT

    # 固定主端口（默认 8989，MT_WEBUI_PORT）：曾用「随机端口」会导致 localStorage 清空；可额外绑定 PORT+1… 突破浏览器同域并发连限制。
    try:
        servers = bind_webui_servers(for_embedded=True)
    except OSError:
        return None, ERR_PORT_BIND
    print(
        f"[webui] 已绑定 API 分片端口: {[s.server_address[1] for s in servers]} "
        f"（默认单端口 {PORT}；多端口见 MT_WEBUI_ONE_PORT_PER_ACCOUNT=1）",
        file=sys.stderr,
        flush=True,
    )
    if _wait_until_dist_ws_ready():
        print(f"[dist] WS 已就绪: ws://{DIST_WS_HOST}:{DIST_WS_PORT}", file=sys.stderr, flush=True)
    else:
        print(f"[dist] WS 未就绪: 请检查 {DIST_WS_HOST}:{DIST_WS_PORT}", file=sys.stderr, flush=True)
    return servers, PORT


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("i茅科技 内部定制")
        self.resize(1500, 920)
        self._ui_retry_deadline = 0.0
        self._ui_warned = False
        self._ui_boot_loaded_once = False
        self._ui_boot_loading = False
        self._ui_boot_retries = 0
        self._cf_tunnel = None

        self.webui_servers, self.port = _start_embedded_server()
        # 本进程刚 bind 时线程已起，仍偶发首帧早于 listen；短等可减小白屏概率
        if self.webui_servers is not None:
            if not _wait_until_embedded_ui_ready(LOCAL_UI_HOST, self.port, total_sec=12.0):
                QMessageBox.critical(
                    self,
                    "WebUI 未就绪",
                    f"本进程已绑定 {PORT}，但短时间内未检测到可用的 /ui.html（请确认 py_out 目录下存在 ui.html）。\n"
                    f"可手动访问：http://{LOCAL_UI_HOST}:{PORT}/ui.html",
                )
                sys.exit(1)
        if self.port == ERR_STALE_WEBUI:
            QMessageBox.critical(
                self,
                "需要重启后端服务",
                f"{LOCAL_UI_HOST}:{PORT} 上已在运行旧版 webui_server（无「刷新设备参数」等新版接口），"
                "界面会出现 not found。\n\n"
                "请先结束占用该端口的进程（任务管理器结束 python / 旧控制台窗口），"
                "再重新打开本程序；或单独用当前目录下的新版代码启动 webui_server。",
            )
            sys.exit(1)
        if self.port == ERR_PORT_BIND:
            QMessageBox.critical(
                self,
                f"无法占用 {PORT}",
                f"本程序无法在 {LISTEN_HOST}:{PORT} 启动 WebUI。\n\n"
                "常见原因：\n"
                f"· 端口 {PORT} 已被其它程序占用（例如误监听该端口的 Node/Express，会出现 Cannot GET /ui.html）；\n"
                "· 或已有一个本程序 / webui_server 在运行。\n\n"
                "请在任务管理器中结束占用该端口的进程（可搜索「python」「node」），或关闭多余实例后再开。\n\n"
                f"若你本意是连已有服务，请用浏览器打开：http://{LOCAL_UI_HOST}:{PORT}/ui.html",
            )
            sys.exit(1)

        self.web = QWebEngineView(self)
        _apply_webengine_settings(self.web)
        _connect_webengine_crash_recovery(self.web, self._ui_url)
        self.setCentralWidget(self.web)
        self.web.loadFinished.connect(self._on_ui_load_finished)
        # 不阻塞主线程等待后端就绪：先展示轻量占位页，避免反复 setUrl 造成闪屏。
        self._ui_retry_deadline = time.monotonic() + 8.0
        self.web.setHtml(
            "<html><body style='margin:0;background:#0b1220;color:#cfd8e3;"
            "font-family:Segoe UI,Microsoft YaHei,sans-serif;display:flex;"
            "align-items:center;justify-content:center;height:100vh;'>"
            "<div>WebUI 启动中，请稍候…</div></body></html>"
        )
        QTimer.singleShot(120, self._try_load_ui_once)

    def _ui_url(self) -> str:
        return f"http://{LOCAL_UI_HOST}:{self.port}/ui.html"

    def _try_load_ui_once(self) -> None:
        if self._ui_boot_loaded_once or self._ui_boot_loading:
            return
        if time.monotonic() >= self._ui_retry_deadline:
            if self._ui_warned:
                return
            self._ui_warned = True
            QMessageBox.warning(
                self,
                "页面未加载",
                f"浏览器内核未能打开 UI 页面（常见于系统代理、杀软拦截或 WebEngine 组件异常）。\n"
                f"请用本机浏览器访问: {self._ui_url()}\n"
                f"如浏览器正常而此处仍白屏，可检查是否设置了 HTTP_PROXY/HTTPS_PROXY。",
            )
            return
        if not (
            _is_port_open(LOCAL_UI_HOST, self.port)
            and _is_service_healthy(LOCAL_UI_HOST, self.port, timeout=0.8)
            and _is_real_webui_ui_html(LOCAL_UI_HOST, self.port, timeout=1.2)
        ):
            QTimer.singleShot(350, self._try_load_ui_once)
            return
        self._ui_boot_loading = True
        self.web.setUrl(QUrl(self._ui_url()))

    def _on_ui_load_finished(self, success: bool) -> None:
        if success:
            self._ui_boot_loaded_once = True
            self._ui_boot_loading = False
            return
        self._ui_boot_loading = False
        # 仅在首次开屏阶段自动重试，且降频，避免短时间反复整页重载导致闪屏。
        if (not self._ui_boot_loaded_once) and time.monotonic() < self._ui_retry_deadline:
            self._ui_boot_retries += 1
            delay_ms = min(1200, 300 + self._ui_boot_retries * 120)
            QTimer.singleShot(delay_ms, self._try_load_ui_once)
            return
        if self._ui_warned:
            return
        self._ui_warned = True
        QMessageBox.warning(
            self,
            "页面未加载",
            f"浏览器内核未能打开 UI 页面（常见于系统代理、杀软拦截或 WebEngine 组件异常）。\n"
            f"请用本机浏览器访问: {self._ui_url()}\n"
            f"如浏览器正常而此处仍白屏，可检查是否设置了 HTTP_PROXY/HTTPS_PROXY。",
        )

    def closeEvent(self, event):
        # 本进程启动的 WebUI（含动态增减的端口）全部写入 webui_server.API_SERVERS
        if self.webui_servers is not None:
            shutdown_all_webui_servers()
        super().closeEvent(event)


def main():
    # 子进程/部分库会读；与 WebEngine 的 Chromium 绕过互为补充
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,::1")
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost,::1")
    _configure_chromium_flags()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    except Exception:
        pass
    app = QApplication(sys.argv)
    _disable_qt_network_proxy()
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
