#!/usr/bin/env python3
"""
业务流程自动化引擎
"""

import time
import random
import uuid
import re
import hashlib
import json
import base64
import shutil
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import parse_qs, quote, unquote, urlparse, urlencode
from curl_cffi import requests as http

import os
import sys
from pathlib import Path

# 抢购 BB 固定使用 shield.py 的 py 稳定版链路，不再读取老/新 BB 环境开关。

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from net_time import adjusted_unix_ms, format_mt_dtime_gmt08

_go_bridge_lock = threading.Lock()
_go_bridge_boot_attempted = False
_go_bridge_started = False  # 本次进程内已成功拉起 go_bridge（rush_bridge），不再 kill


def _reset_go_bridge_boot_state() -> None:
    """rush_bridge 被外部停止后，清掉进程内启动标记，下一次请求才能重新拉起。"""
    global _go_bridge_boot_attempted, _go_bridge_started
    with _go_bridge_lock:
        _go_bridge_boot_attempted = False
        _go_bridge_started = False


_YIDUN_GDX_SCRIPT = str(_ROOT / "node_bridge" / "gdx_cookie.cjs")
_BANGCLE_WARMUP_SCRIPT = str(_ROOT / "node_bridge" / "bangcle_warmup.cjs")
_GDX_COOKIE_KEY = "gdxidpyhxdE"
_GDX_REFRESH_MIN_REMAINING_MS = 60_000
_BANGCLE_WARMUP_MIN_INTERVAL_MS = 60_000
_BB_WARMUP_PORTABLE_KEYS = (
    "bbSignWasmBase64",
    "bbSignWasmVersion",
    "bbSignWasmOk",
    "bbSdkVersion",
    "__bbWasmFetched",
    "__bbWasmFetchedAt",
    "__bbWasmUnavailableReason",
)
_TTOCR_APPKEY = os.environ.get("TTOCR_APPKEY", "22954d79c73a33975c039fd1048da34d")
_TTOCR_CAPTCHA_ID = os.environ.get(
    "MT_YIDUN_CAPTCHA_ID",
    "22912abb4c484691b71f6efaf778c252",
).strip() or "22912abb4c484691b71f6efaf778c252"
_TTOCR_REFERER = "https://h5.moutai519.com.cn/"
_YIDUN_CAPTCHA_ID_BY_DETAIL_KIND = {
    "smsp-detail": "1e1ea52963a94c21b0babc42b2b5b910",
    "jpmt-detail": "22912abb4c484691b71f6efaf778c252",
    "1000ml-detail": "3fde6da0bf4d41c39ef99de8ba977d50",
    "mm-485-detail-group": "c2657d2cc1f248a78c5a06980923dba5",
    "mm-zx-271-detail": "c2657d2cc1f248a78c5a06980923dba5",
    "hxx-detail": "c2657d2cc1f248a78c5a06980923dba5",
    "gyft-8392-detail": "5f1aab1567f54fdd8c137afed42af465",
}
_YIDUN_CAPTCHA_ID_BY_ITEM_CODE = {
    "741": "1e1ea52963a94c21b0babc42b2b5b910",
    "1741": "22912abb4c484691b71f6efaf778c252",
    "483": "3fde6da0bf4d41c39ef99de8ba977d50",
    "IMTP1000229": "3fde6da0bf4d41c39ef99de8ba977d50",
    "11942": "c2657d2cc1f248a78c5a06980923dba5",
    "11945": "c2657d2cc1f248a78c5a06980923dba5",
    "11947": "c2657d2cc1f248a78c5a06980923dba5",
    "11652": "c2657d2cc1f248a78c5a06980923dba5",
    "11654": "c2657d2cc1f248a78c5a06980923dba5",
    "11894": "5f1aab1567f54fdd8c137afed42af465",
}
_YIDUN_REFERER_BY_ITEM_CODE = {
    "741": "https://h5.moutai519.com.cn/mt/item/smsp-detail?appConfig=2_1_2",
    "1741": "https://h5.moutai519.com.cn/mt/item/jpmt-detail?appConfig=2_1_2",
    "483": "https://h5.moutai519.com.cn/mt/item/1000ml-detail?appConfig=2_1_2",
    "IMTP1000229": "https://h5.moutai519.com.cn/mt/item/1000ml-detail?appConfig=2_1_2",
    "11942": "https://h5.moutai519.com.cn/mt/item/mm-485-detail-group?appConfig=2_1_2",
    "11945": "https://h5.moutai519.com.cn/mt/item/mm-zx-271-detail?appConfig=2_1_2",
    "11947": "https://h5.moutai519.com.cn/mt/item/mm-485-detail-group?appConfig=2_1_2",
    "11652": "https://h5.moutai519.com.cn/mt/item/hxx-detail?appConfig=2_1_2&sourceId=IMTP1000260",
    "11654": "https://h5.moutai519.com.cn/mt/item/hxx-detail?appConfig=2_1_2&sourceId=IMTP1000260",
    "11894": "https://h5.moutai519.com.cn/mt/item/gyft-8392-detail?appConfig=2_1_2",
}
_YIDUN_BUNDLED_DIR = _ROOT / "local_yidun" / "service"
_YIDUN_EXTERNAL_DIR = Path(r"C:\Users\Administrator\Desktop\ok (5)\dist\2026年5月10日更新(2)")
_YIDUN_BUNDLED_OCR_DIR = _ROOT / "local_yidun" / "ddddocr-fastapi"
_YIDUN_EXTERNAL_OCR_DIR = Path(r"C:\Users\Administrator\Desktop\ok (5)\bin\ddddocr-fastapi")


def _pick_existing_path(env_name: str, bundled: Path, external: Path) -> Path:
    raw = os.environ.get(env_name)
    if raw:
        return Path(raw)
    try:
        if bundled.exists():
            return bundled
    except OSError:
        pass
    return external


_YIDUN_LEGACY_DIR = _pick_existing_path("MT_YIDUN_LEGACY_DIR", _YIDUN_BUNDLED_DIR, _YIDUN_EXTERNAL_DIR)
_YIDUN_LOCAL_SCRIPT = Path(
    os.environ.get("MT_YIDUN_LOCAL_SCRIPT") or (_YIDUN_LEGACY_DIR / "app_api.js")
)
_YIDUN_TUNED_OCR_DIR = _pick_existing_path("MT_YIDUN_TUNED_OCR_DIR", _YIDUN_BUNDLED_OCR_DIR, _YIDUN_EXTERNAL_OCR_DIR)


def _default_yidun_local_url() -> str:
    try:
        if _YIDUN_LOCAL_SCRIPT.is_file():
            return "http://127.0.0.1:8887"
    except OSError:
        pass
    return "http://127.0.0.1:3000"


def _default_yidun_local_ocr_url() -> str:
    try:
        if _YIDUN_LOCAL_SCRIPT.is_file() and _YIDUN_TUNED_OCR_DIR.is_dir():
            return "http://127.0.0.1:8000/slide_match"
    except OSError:
        pass
    return ""


_YIDUN_LOCAL_URL = (os.environ.get("MT_YIDUN_LOCAL_URL") or _default_yidun_local_url()).strip().rstrip("/")
_YIDUN_LOCAL_API_KEY = (os.environ.get("MT_YIDUN_LOCAL_API_KEY") or "default-key").strip() or "default-key"
_YIDUN_LOCAL_OCR_URL = (os.environ.get("MT_YIDUN_LOCAL_OCR_URL") or _default_yidun_local_ocr_url()).strip()
_yidun_local_proc: subprocess.Popen | None = None
_yidun_local_lock = threading.Lock()
_yidun_tuned_ocr_proc: subprocess.Popen | None = None
_yidun_tuned_ocr_lock = threading.Lock()


def _use_node_http_backend() -> bool:
    """设 MT_HTTP_BACKEND=node（或 1/true/yes）时由本机 go_bridge（rush_bridge）发 HTTPS，直连茅台域名（可带账号代理）。"""
    v = (os.environ.get("MT_HTTP_BACKEND") or "").strip().lower()
    return v in ("node", "1", "true", "yes")


def _use_node_batch_rush() -> bool:
    """批量抢购经 go_bridge（rush_bridge）/batch 并行发包（与 Python 编排配合）。"""
    v = (os.environ.get("MT_BATCH_RUSH_NODE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _use_node_rush_http() -> bool:
    """
    仅抢购 POST 走本机 go_bridge（rush_bridge）（与 MT_HTTP_BACKEND 解耦：登录/组单/下单等仍用 Python curl）。
    环境变量：MT_RUSH_HTTP_NODE=1 / true / yes / on
    """
    v = (os.environ.get("MT_RUSH_HTTP_NODE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _go_bridge_should_run() -> bool:
    return (
        _use_node_http_backend()
        or _use_node_batch_rush()
        or _use_node_rush_http()
    )


def _go_bridge_port() -> int:
    # MT_GO_BRIDGE_PORT 专用于 go_bridge（默认 19091），与 node_bridge 的 19090 隔离
    try:
        return max(1, min(65535, int((os.environ.get("MT_GO_BRIDGE_PORT") or "19091").strip())))
    except (TypeError, ValueError):
        return 19091


def _rush_jitter_ms_max() -> int:
    """
    抢购每枪 POST 发出前额外随机等待 [0, N] 毫秒（抖动，避免固定节拍）。
    MT_RUSH_JITTER_MS_MAX：默认 100，最大 100；设为 0 关闭。
    """
    try:
        n = int((os.environ.get("MT_RUSH_JITTER_MS_MAX") or "100").strip())
    except (TypeError, ValueError):
        n = 100
    return max(0, min(100, n))


def _rush_sleep_shot_jitter() -> None:
    """与 go_bridge 侧 sleepShotJitter 一致：整数毫秒 uniform [0, mx]，含端点。"""
    mx = _rush_jitter_ms_max()
    if mx <= 0:
        return
    ms = random.randint(0, mx)
    if ms > 0:
        time.sleep(ms / 1000.0)


def _resolve_node_executable() -> str | None:
    """
    定位 node.exe：MT_NODE_EXE → PATH(which) → Windows 常见安装路径。
    解决：桌面/快捷方式启动时子进程无 Node 的 PATH（与终端不一致），导致 shutil.which 失败。
    """
    raw = (os.environ.get("MT_NODE_EXE") or "").strip().strip('"')
    if raw:
        p = Path(raw)
        if p.is_file():
            return str(p.resolve())
    w = shutil.which("node")
    if w:
        return w
    if os.name != "nt":
        return None
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pfx86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    la = (os.environ.get("LOCALAPPDATA") or "").strip()
    candidates: list[Path] = [
        Path(pf) / "nodejs" / "node.exe",
        Path(pfx86) / "nodejs" / "node.exe",
        Path(la) / "Programs" / "nodejs" / "node.exe",
    ]
    for drive in ("D:", "C:", "E:"):
        candidates.append(Path(f"{drive}\\Aibote\\Environment\\Node\\node.exe"))
    for c in candidates:
        try:
            if c.is_file():
                return str(c.resolve())
        except OSError:
            continue
    return None


def _resolve_python_command() -> list[str]:
    raw = (os.environ.get("MT_PYTHON_EXE") or "").strip().strip('"')
    if raw:
        p = Path(raw)
        if p.is_file():
            return [str(p.resolve())]
        return [raw]
    try:
        p = Path(sys.executable)
        if p.is_file() and p.name.lower().startswith("python"):
            return [str(p.resolve())]
    except OSError:
        pass
    w = shutil.which("python")
    if w:
        return [w]
    if os.name == "nt":
        la = (os.environ.get("LOCALAPPDATA") or "").strip()
        candidates = [
            Path(la) / "Programs" / "Python" / "Python313" / "python.exe",
            Path(la) / "Programs" / "Python" / "Python312" / "python.exe",
            Path(la) / "Programs" / "Python" / "Python311" / "python.exe",
            Path(r"C:\Python313\python.exe"),
            Path(r"C:\Python312\python.exe"),
            Path(r"C:\Python311\python.exe"),
        ]
        for c in candidates:
            try:
                if c.is_file():
                    return [str(c.resolve())]
            except OSError:
                continue
    py_launcher = shutil.which("py")
    if py_launcher:
        return [py_launcher, "-3"]
    return [sys.executable]


def _probe_tcp_listening(host: str, port: int, *, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return True
    except OSError:
        return False


def _probe_go_bridge() -> bool:
    """仅表示端口上有 TCP 监听；非 rush_bridge 占坑时也为 True，不能代表支持 /rush_loop。"""
    return _probe_tcp_listening("127.0.0.1", _go_bridge_port())


def _kill_node_on_port(port: int) -> None:
    """
    杀掉占用指定端口的桥进程（node.exe / rush_bridge.exe），供启动时强制刷新。

    仅解析 **LISTENING** 行：若把 ESTABLISHED 里的 PID 当成监听方，可能误杀
    正在连接该端口的 python/WebUI 等客户端进程，表现为「强制重启」后整进程卡死。
    """
    needle = f":{int(port)}"
    try:
        import subprocess as _sp
        out = _sp.check_output(
            ["netstat", "-ano"],
            stderr=_sp.DEVNULL,
            text=True,
            timeout=5,
        )
        pids: set[int] = set()
        for line in out.splitlines():
            lu = line.upper()
            if needle not in line or "LISTENING" not in lu:
                continue
            parts = line.split()
            if not parts:
                continue
            try:
                pids.add(int(parts[-1]))
            except ValueError:
                pass
        for pid in pids:
            try:
                name_out = _sp.check_output(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    stderr=_sp.DEVNULL, text=True, timeout=3,
                )
                name_lower = name_out.lower()
                if "node" in name_lower or "rush_bridge" in name_lower:
                    _sp.call(["taskkill", "/F", "/PID", str(pid)],
                             stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            except Exception:
                pass
    except Exception:
        pass


def _wait_listen_port_free(port: int, max_wait: float = 6.0) -> None:
    """杀桥后等待端口释放，避免新 rush_bridge 因「端口仍被占用」秒退，主进程却空等就绪。"""
    end = time.monotonic() + max(0.25, float(max_wait))
    while time.monotonic() < end:
        if not _probe_tcp_listening("127.0.0.1", int(port), timeout=0.18):
            return
        time.sleep(0.1)


def _go_bridge_feature_ready(port: int) -> bool:
    """
    探测 go_bridge（rush_bridge）：必须同时具备 /rush_loop 与 /batch_stream。
    旧版纯 Node 桥仅 /req 时对 /rush_loop 返回 404。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        url = f"http://127.0.0.1:{port}/rush_loop"
        data = json.dumps({"attempts": []}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with opener.open(req, timeout=3.0) as resp:
            if int(resp.status or 0) == 404:
                return False
            raw = resp.read().decode("utf-8", errors="replace")
            if int(resp.status or 0) != 200:
                return False
            try:
                j = json.loads(raw)
            except json.JSONDecodeError:
                return False
            err = str(j.get("error") or "").lower()
            if not (j.get("ok") is False and ("attempt" in err or "non-empty" in err)):
                return False
        url2 = f"http://127.0.0.1:{port}/batch_stream"
        data2 = json.dumps({"tasks": []}, ensure_ascii=False).encode("utf-8")
        req2 = urllib.request.Request(
            url2,
            data=data2,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with opener.open(req2, timeout=3.0) as resp2:
            if int(resp2.status or 0) == 404:
                return False
            raw2 = resp2.read(512).decode("utf-8", errors="replace")
            if not ('"type":"done"' in raw2 or '"type": "done"' in raw2):
                return False
        url3 = f"http://127.0.0.1:{port}/rush_shot"
        data3 = json.dumps({"attempt": {}}, ensure_ascii=False).encode("utf-8")
        req3 = urllib.request.Request(
            url3,
            data=data3,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with opener.open(req3, timeout=3.0) as resp3:
            if int(resp3.status or 0) == 404:
                return False
            raw3 = resp3.read(512).decode("utf-8", errors="replace")
            if not ("attempt.url required" in raw3 or '"rushShot":true' in raw3 or '"rushShot": true' in raw3):
                return False
        url4 = f"http://127.0.0.1:{port}/health"
        req4 = urllib.request.Request(url4, method="GET")
        with opener.open(req4, timeout=3.0) as resp4:
            if int(resp4.status or 0) != 200:
                return False
            raw4 = resp4.read(1024).decode("utf-8", errors="replace")
            try:
                j4 = json.loads(raw4)
            except json.JSONDecodeError:
                return False
            return bool(j4.get("asyncRush"))
    except urllib.error.HTTPError as e:
        return False
    except Exception:
        return False


def _find_free_bridge_port_near(base: int, span: int = 64) -> int | None:
    """从 base+1 起找第一个当前无 TCP 占用的端口，供自动起新版桥。"""
    for alt in range(base + 1, min(65536, base + 1 + span)):
        if not _probe_tcp_listening("127.0.0.1", alt, timeout=0.12):
            return alt
    return None


def _find_go_binary() -> str | None:
    """查找系统 go 可执行文件，返回路径或 None。"""
    import shutil
    found = shutil.which("go")
    if found:
        return found
    candidates = [
        r"C:\Go\bin\go.exe",
        r"C:\Program Files\Go\bin\go.exe",
        os.path.expanduser(r"~\go\bin\go.exe"),
        "/usr/local/go/bin/go",
        "/usr/bin/go",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    return None


def _go_bridge_exe_path() -> Path:
    """预编译 exe 的路径（go_bridge/rush_bridge.exe 或 rush_bridge）。"""
    go_src = _ROOT / "go_bridge"
    name = "rush_bridge.exe" if os.name == "nt" else "rush_bridge"
    return go_src / name


def _ensure_go_bridge_compiled() -> str:
    """
    确保 go_bridge 已编译为 exe。
    - 若 exe 存在且比所有 .go 源文件新，直接返回 exe 路径。
    - 否则用 go build 编译（首次约 30-60s，之后增量编译很快）。
    返回 exe 路径字符串。
    """
    go_src = _ROOT / "go_bridge"
    exe = _go_bridge_exe_path()
    go_files = list(go_src.glob("*.go"))

    # 检查 exe 是否比所有源文件都新
    if exe.is_file() and go_files:
        exe_mtime = exe.stat().st_mtime
        if all(exe_mtime >= f.stat().st_mtime for f in go_files):
            return str(exe)

    go_bin = _find_go_binary()
    if not go_bin:
        raise RuntimeError("未找到 go 可执行文件，请安装 Go 或将其加入 PATH")

    print(f"[go_bridge] 编译中（首次约 30-60s）: go build -o {exe.name} .", flush=True)
    result = subprocess.run(
        [go_bin, "build", "-o", str(exe), "."],
        cwd=str(go_src),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"go build 失败:\n{result.stderr}")
    print(f"[go_bridge] 编译完成: {exe}", flush=True)
    return str(exe)


def _wait_for_bridge_ready() -> None:
    """在锁外轮询桥就绪，最多等 60s。所有线程共用，不持锁。"""
    port = _go_bridge_port()
    for i in range(1200):
        if _go_bridge_feature_ready(port):
            global _go_bridge_started
            if not _go_bridge_started:
                _go_bridge_started = True
                print(
                    f"[go_bridge] 已就绪（/rush_loop 探测成功）: http://127.0.0.1:{port}/health",
                    flush=True,
                )
            return
        if i > 0 and i % 100 == 0:
            print(
                f"[go_bridge] 仍在等待桥就绪… 已约 {i // 20}s，"
                f"请查看 go_bridge/bridge.log 是否有绑定失败；或手动结束占用 {port} 的进程。",
                flush=True,
            )
        time.sleep(0.05)
    # 超时不抛异常，只打印警告；请求线程会在 _go_bridge_http_dispatch 里快速失败
    print(
        f"[go_bridge] 警告：{port} 端口桥启动超时（约 60s），后续请求将直接报错。"
        f" 可检查 go_bridge/bridge.log、确认 rush_bridge.exe 未被拦截、或改 MT_GO_BRIDGE_PORT。",
        flush=True,
    )


def _ensure_go_bridge_running() -> None:
    global _go_bridge_boot_attempted, _go_bridge_started
    if not _go_bridge_should_run():
        return
    port0 = _go_bridge_port()
    # 已就绪且端口仍在监听：直接返回。避免发码/登录每条请求都 POST /rush_loop 探测 + 刷屏日志。
    if _go_bridge_started and _probe_tcp_listening("127.0.0.1", port0):
        if _go_bridge_feature_ready(port0):
            return
        _go_bridge_started = False
        _go_bridge_boot_attempted = False
    if _go_bridge_started and not _probe_tcp_listening("127.0.0.1", port0):
        _go_bridge_started = False
        _go_bridge_boot_attempted = False
    # 仅当端口被占且探测不是「带 /rush_loop 的新版桥」时才杀监听进程，避免误杀本机已在跑的 rush_bridge。
    if (
        not _go_bridge_started
        and _probe_tcp_listening("127.0.0.1", port0)
        and not _go_bridge_feature_ready(port0)
    ):
        print(f"[go_bridge] 检测到端口 {port0} 已有进程，强制重启以使用最新代码…", flush=True)
        _kill_node_on_port(port0)
        time.sleep(0.8)
        _wait_listen_port_free(port0, max_wait=8.0)
    if _go_bridge_feature_ready(port0):
        # 每个进程只打一次：发码/登录等会反复调用本函数，桥未变时不应刷屏
        if not _go_bridge_started:
            _go_bridge_started = True
            print(
                f"[go_bridge] 已就绪（沿用现有监听）: http://127.0.0.1:{port0}/health",
                flush=True,
            )
        return
    with _go_bridge_lock:
        port = _go_bridge_port()
        if _go_bridge_feature_ready(port):
            if not _go_bridge_started:
                _go_bridge_started = True
                print(
                    f"[go_bridge] 已就绪（锁内检测到实例）: http://127.0.0.1:{port}/health",
                    flush=True,
                )
            return

        if _probe_tcp_listening("127.0.0.1", port) and not _go_bridge_feature_ready(port):
            alt = _find_free_bridge_port_near(port)
            if alt is None:
                raise RuntimeError(
                    f"127.0.0.1:{port} 上进程不是新版 go_bridge（无 /rush_loop），"
                    f"且近邻端口均被占用。请结束占用 {port} 的进程后重试。"
                )
            os.environ["MT_GO_BRIDGE_PORT"] = str(alt)
            _go_bridge_boot_attempted = False
            print(f"[go_bridge] 127.0.0.1:{port} 非新版桥，已自动改用端口 {alt}", flush=True)
            port = alt

        if _go_bridge_boot_attempted:
            if _go_bridge_feature_ready(_go_bridge_port()):
                if not _go_bridge_started:
                    _go_bridge_started = True
                    print(
                        f"[go_bridge] 已就绪（其他线程已拉起）: http://127.0.0.1:{_go_bridge_port()}/health",
                        flush=True,
                    )
                return
            # 另一个线程已触发 go run，退出锁后一起等就绪

        else:
            go_src = str(_ROOT / "go_bridge")
            if not Path(go_src).is_dir():
                raise RuntimeError(f"缺少 Go 桥源码目录: {go_src}")

            # 编译（有缓存 exe 时几乎瞬间返回）
            exe = _ensure_go_bridge_compiled()

            _go_bridge_boot_attempted = True
            env = os.environ.copy()
            env["MT_GO_BRIDGE_PORT"] = str(_go_bridge_port())
            env.setdefault("MT_GO_TLS_CLIENT", "1")
            env.setdefault("MT_GO_TLS_CLIENT_PROFILE", "chrome_138")
            env.setdefault("MT_TLS_ENGINE", "reqrio")
            log_path = _ROOT / "go_bridge" / "bridge.log"
            try:
                log_f = open(log_path, "a", encoding="utf-8")
            except OSError:
                log_f = subprocess.DEVNULL
            try:
                subprocess.Popen(
                    [exe],
                    cwd=go_src,
                    env=env,
                    stdout=log_f,
                    stderr=log_f,
                    stdin=subprocess.DEVNULL,
                    close_fds=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                print(
                    f"[go_bridge] 子进程已启动，等待 http://127.0.0.1:{env['MT_GO_BRIDGE_PORT']}/rush_loop 探测成功…",
                    flush=True,
                )
            except OSError as ex:
                _go_bridge_boot_attempted = False
                raise RuntimeError(f"启动 Go 桥失败: {ex}") from ex
    # 锁已释放，所有线程在锁外等待桥就绪，互不阻塞
    _wait_for_bridge_ready()


class _NodeHttpResponse:
    """模拟 curl_cffi 响应对象：status_code、text、json()、headers。"""

    __slots__ = ("status_code", "_content", "headers")

    def __init__(self, status_code: int, content: bytes, headers: dict | None = None):
        self.status_code = int(status_code)
        self._content = content
        self.headers = headers if isinstance(headers, dict) else {}

    @property
    def text(self) -> str:
        if not self._content:
            return ""
        # 网关偶发 GBK/GB18030 文本；优先按响应头 charset，其次 UTF-8，再兜底 GB18030。
        ct = str(self.headers.get("content-type") or self.headers.get("Content-Type") or "").lower()
        m = re.search(r"charset\s*=\s*([a-z0-9_\-]+)", ct)
        if m:
            enc = m.group(1).strip()
            try:
                return self._content.decode(enc, errors="strict")
            except Exception:
                pass
        try:
            return self._content.decode("utf-8", errors="strict")
        except Exception:
            pass
        try:
            return self._content.decode("gb18030", errors="strict")
        except Exception:
            return self._content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


def _ordered_header_pairs(headers) -> list[dict]:
    pairs: list[dict] = []
    if not isinstance(headers, dict):
        return pairs
    for k, v in headers.items():
        if k is None:
            continue
        pairs.append({"name": str(k), "value": "" if v is None else str(v)})
    return pairs


def _bridge_json_body_b64_len(body_obj) -> tuple[str, int]:
    if body_obj is None:
        return "", 0
    if isinstance(body_obj, bytes):
        raw = body_obj
    elif isinstance(body_obj, (dict, list)):
        raw = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    else:
        raw = str(body_obj).encode("utf-8")
    return base64.b64encode(raw).decode("ascii"), len(raw)


def _bridge_host_from_url(url: str) -> str:
    try:
        parsed = urlparse(str(url or ""))
        if not parsed.hostname:
            return ""
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return host
    except Exception:
        return ""


def _go_bridge_account_key_from_headers(headers: dict) -> str:
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    for key in ("x-account-key", "x-mt-account-key", "mt-device-id"):
        val = (h.get(key) or "").strip()
        if val:
            return val
    cookie = (h.get("cookie") or "").strip()
    if cookie:
        parts = {}
        for chunk in cookie.split(";"):
            if "=" not in chunk:
                continue
            name, value = chunk.split("=", 1)
            parts[name.strip().lower()] = value.strip()
        for key in ("mt-device-id-wap", "_bs_device_id", "_d_u", "mt-token-wap", "mt-token"):
            val = (parts.get(key) or "").strip()
            if val:
                return val
    return ""


def _bridge_prepare_attempt(method: str, url: str, headers: dict, body_obj, proxy_url: str, timeout_ms: int, lane_id: int | None = None) -> dict:
    hdrs = {str(k): str(v) for k, v in (headers or {}).items()}
    body_b64, body_len = _bridge_json_body_b64_len(body_obj)
    host = ""
    for hk, hv in hdrs.items():
        if str(hk).lower() == "host":
            host = str(hv).strip()
            break
    if not host:
        host = _bridge_host_from_url(url)
        if host:
            hdrs["Host"] = host
    if body_len > 0 and not any(str(k).lower() == "content-length" for k in hdrs):
        hdrs["Content-Length"] = str(body_len)
    account_key = _go_bridge_account_key_from_headers(hdrs)
    payload = {
        "method": str(method or "POST").upper(),
        "url": url,
        "headers": hdrs,
        "headersOrdered": _ordered_header_pairs(hdrs),
        "bodyB64": body_b64,
        "host": host,
        "contentLength": body_len,
        "proxyUrl": str(proxy_url or "").strip(),
        "timeoutMs": int(timeout_ms or 0),
    }
    if account_key:
        payload["accountKey"] = account_key
    if lane_id is not None:
        try:
            payload["laneId"] = max(0, int(lane_id))
        except (TypeError, ValueError):
            payload["laneId"] = 0
    return payload


def _rush_lane_id_for_shot(shot_index: int, lane_count: int | None) -> int:
    try:
        lanes = int(lane_count or 0)
    except (TypeError, ValueError):
        lanes = 0
    if lanes <= 1:
        return 0
    try:
        shot = int(shot_index or 1)
    except (TypeError, ValueError):
        shot = 1
    return max(0, (max(1, shot) - 1) % lanes)


def _rush_default_lane_count() -> int:
    raw = (os.environ.get("MT_RUSH_LANE_COUNT") or "5").strip() or "5"
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 5
    return max(1, min(64, n))


def _truthy_env_payload_value(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(int(v))
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "on")


def _go_bridge_http_dispatch(method: str, url: str, **kw) -> _NodeHttpResponse:
    """经 127.0.0.1 go_bridge（rush_bridge）/req 发往真实 url；签名与头仍由 Python 组装。"""
    _ensure_go_bridge_running()
    # 桥没就绪时快速失败，不卡住请求线程
    if not _go_bridge_started and not _go_bridge_feature_ready(_go_bridge_port()):
        raise RuntimeError("go_bridge（rush_bridge）未就绪，请稍后重试")
    kw.pop("tls_impersonate", None)
    _raw_to = kw.get("timeout")
    if _raw_to is None:
        timeout = 30.0
    else:
        try:
            timeout = float(_raw_to)
        except (TypeError, ValueError):
            timeout = 30.0
    headers = {str(k): str(v) for k, v in (kw.get("headers") or {}).items()}
    proxies = kw.get("proxies")
    proxy_url = ""
    if isinstance(proxies, dict):
        proxy_url = (proxies.get("https") or proxies.get("http") or "").strip()
    # 兼容 curl 分离账密（proxy_auth）场景：Node 侧需完整代理 URL 才能认证
    proxy_auth = kw.get("proxy_auth")
    if (
        proxy_url
        and isinstance(proxy_auth, (tuple, list))
        and len(proxy_auth) >= 2
        and "@" not in proxy_url
    ):
        try:
            pr = urlparse(proxy_url)
            if pr.scheme in ("http", "https") and pr.hostname:
                user = quote(str(proxy_auth[0] or ""), safe="")
                pwd = quote(str(proxy_auth[1] or ""), safe="")
                host = pr.hostname
                port = f":{pr.port}" if pr.port else ""
                auth = f"{user}:{pwd}@" if user else ""
                proxy_url = f"{pr.scheme}://{auth}{host}{port}"
        except Exception:
            pass

    if timeout <= 0:
        _t_ms = 0
        _local_urlopen_timeout = None
    else:
        _t_ms = int(max(1000, min(600_000, timeout * 1000)))
        _local_urlopen_timeout = timeout + 20.0
    payload: dict = {
        "method": method,
        "url": url,
        "headers": headers,
        "timeoutMs": _t_ms,
        "proxyUrl": proxy_url,
        "tlsImpersonate": TLS_PROFILE,
    }

    if "json" in kw and kw["json"] is not None:
        if not any(str(k).lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/json; charset=UTF-8"
        body_obj = kw["json"]
        # 整段 JSON 放进 payload["body"] 会使 Python→Node 的 /req 包极大，且 Node 再转给 curl 子进程时
        # 曾出现 Windows 管道截断 → json.loads 失败。dict/list 一律走 bodyB64。
        if isinstance(body_obj, (dict, list)):
            _raw = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False)
            payload["bodyB64"] = base64.b64encode(_raw.encode("utf-8")).decode("ascii")
        else:
            _s = str(body_obj)
            if _s:
                payload["bodyB64"] = base64.b64encode(_s.encode("utf-8")).decode("ascii")
    elif "data" in kw and kw["data"] is not None:
        d = kw["data"]
        if isinstance(d, bytes):
            payload["bodyB64"] = base64.b64encode(d).decode("ascii")
        elif isinstance(d, str):
            if d:
                payload["bodyB64"] = base64.b64encode(d.encode("utf-8")).decode("ascii")
        elif isinstance(d, dict):
            # 与 curl_cffi requests.post(data=dict) 一致：表单须 x-www-form-urlencoded，勿用 str(dict)。
            form_body = urlencode(d, doseq=True)
            payload["bodyB64"] = base64.b64encode(form_body.encode("utf-8")).decode("ascii")
        else:
            payload["bodyB64"] = base64.b64encode(str(d).encode("utf-8")).decode("ascii")

    body_b64 = payload.get("bodyB64") or ""
    body_len = 0
    if body_b64:
        try:
            body_len = len(base64.b64decode(body_b64))
        except Exception:
            body_len = 0
    host = ""
    for hk, hv in list(headers.items()):
        if str(hk).lower() == "host":
            host = str(hv).strip()
            break
    if not host:
        host = _bridge_host_from_url(url)
        if host:
            headers["Host"] = host
    if body_len > 0 and not any(str(k).lower() == "content-length" for k in headers):
        headers["Content-Length"] = str(body_len)
    for hk, hv in list(headers.items()):
        lk = str(hk).lower()
        if lk == "host":
            payload["host"] = str(hv)
        elif lk == "content-length":
            try:
                payload["contentLength"] = int(str(hv).strip())
            except (TypeError, ValueError):
                payload["contentLength"] = body_len

    payload["headers"] = headers
    payload["headersOrdered"] = _ordered_header_pairs(headers)
    account_key = _go_bridge_account_key_from_headers(headers)
    if account_key:
        payload["accountKey"] = account_key

    bridge_url = f"http://127.0.0.1:{_go_bridge_port()}/req"
    req = urllib.request.Request(
        bridge_url,
        data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_local_urlopen_timeout) as resp:
            raw_resp = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise RuntimeError(f"go_bridge /req 不可达: {e}") from e
    try:
        j = json.loads(raw_resp)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"go_bridge /req 返回非 JSON: {raw_resp[:240]!r}") from e
    if not j.get("ok"):
        err = str(j.get("error") or "unknown").strip()
        raise RuntimeError("go_bridge /req: " + err)
    status = int(j.get("status") or 0)
    b64 = j.get("bodyB64") or ""
    try:
        content = base64.b64decode(b64) if b64 else b""
    except Exception:
        content = b""
    rh = j.get("headers")
    return _NodeHttpResponse(status, content, rh if isinstance(rh, dict) else {})


def _batch_rush_parallel_cap() -> int:
    """
    批量抢购：同时「起第一枪」的账号数上限（Node /batch worker 与 Python ThreadPool 共用）。
    环境变量 MT_BATCH_RUSH_PARALLEL_CAP，默认 512，范围 64～2048。
    """
    raw = (os.environ.get("MT_BATCH_RUSH_PARALLEL_CAP") or "512").strip()
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = 512
    return max(64, min(2048, v))


def _mobile_relay_parallel_cap() -> int:
    raw = (os.environ.get("MT_MOBILE_RELAY_PARALLEL_CAP") or "1").strip()
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = 1
    return max(1, min(16, v))


def estimate_node_batch_outer_timeout_sec(tasks: list[dict]) -> float:
    """
    估算 Python 侧等 Node POST /batch 整包返回的最少超时秒数。
    批量并行时壁钟时间 ≈ 最慢一条 rush_loop；原固定 180s 易在多档×长单档超时时整批误判失败。
    环境变量：MT_NODE_BATCH_OUTER_TIMEOUT 强制秒数；MT_NODE_BATCH_OUTER_TIMEOUT_MAX 估算结果上限（默认 7200）。
    """
    floor = 180.0
    raw_fix = (os.environ.get("MT_NODE_BATCH_OUTER_TIMEOUT") or "").strip()
    if raw_fix:
        try:
            v = float(raw_fix)
            if v > 0:
                return max(60.0, min(86400.0, v))
        except (TypeError, ValueError):
            pass
    try:
        cap = float(os.environ.get("MT_NODE_BATCH_OUTER_TIMEOUT_MAX") or "7200") or 7200.0
    except (TypeError, ValueError):
        cap = 7200.0
    cap = max(300.0, min(86400.0, cap))

    max_task = floor
    for t in tasks:
        if not isinstance(t, dict):
            continue
        if not t.get("rushLoop"):
            try:
                ms = int(t.get("timeoutMs") or 30000)
            except (TypeError, ValueError):
                ms = 30000
            ms = max(1000, min(600_000, ms))
            max_task = max(max_task, ms / 1000.0 + 30.0)
            continue
        att = t.get("attempts")
        if not isinstance(att, list) or not att:
            max_task = max(max_task, 300.0)
            continue
        n = len(att)
        tmo = 30000
        try:
            a0 = att[0] if isinstance(att[0], dict) else {}
            tmo = int(a0.get("timeoutMs") or 30000)
        except Exception:
            pass
        if tmo <= 0:
            tmo_eff = 120_000
        else:
            tmo = max(1000, min(600_000, tmo))
            tmo_eff = tmo
        gap_ms = 0
        for key in ("rushLoopDelayMs", "delayMs", "rushDelayMs"):
            if key in t:
                try:
                    gap_ms = max(gap_ms, int(t.get(key) or 0))
                except (TypeError, ValueError):
                    pass
        gap_ms = max(0, min(300_000, gap_ms))
        # 每枪最多等 tmo；枪间 gap（最后一枪后无 gap）
        per = n * (tmo_eff / 1000.0) + max(0, n - 1) * (gap_ms / 1000.0)
        max_task = max(max_task, per + 60.0)
        # ⚡高频多轮：轮次间隔（含最大抖动）计入整段墙钟
        if t.get("fireAndForget"):
            try:
                rr = int(t.get("rushRounds") or t.get("rushRoundCount") or 1)
            except (TypeError, ValueError):
                rr = 1
            rr = max(1, min(100, rr))
            if rr > 1:
                try:
                    rgap = int(t.get("rushRoundGapMs") or 0)
                except (TypeError, ValueError):
                    rgap = 0
                rgap = max(0, min(300_000, rgap))
                try:
                    rjit = int(t.get("rushRoundGapJitterMs") or 0)
                except (TypeError, ValueError):
                    rjit = 0
                rjit = max(0, min(60_000, rjit))
                max_task += (rr - 1) * (rgap + rjit) / 1000.0

    out = min(cap, max(floor, max_task * 1.1))
    return out


def go_bridge_batch_http_dispatch(
    tasks: list[dict],
    *,
    concurrency: int = 64,
    outer_timeout: float | None = None,
) -> list[dict]:
    """
    go_bridge /batch：多条 HTTPS 并行（用于批量抢购等）。
    每条 task：常规同 /req；或 rushLoop 任务 { rushLoop: true, attempts: [...] }，由 rush_bridge 内按限流规则连发。
    返回与 tasks 等长的结果列表（rushLoop 项含 last / lastProxyUrl）。
    outer_timeout：默认 None 时按任务自动估算，避免多档 rush_loop 超过 180s 被 Python 掐断。
    """
    if not tasks:
        return []
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
    bridge_url = f"http://127.0.0.1:{_go_bridge_port()}/batch"
    req = urllib.request.Request(
        bridge_url,
        data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=outer_timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        port = _go_bridge_port()
        hint404 = ""
        if int(e.code or 0) == 404:
            hint404 = (
                f" 当前 127.0.0.1:{port} 上 go_bridge（rush_bridge）不可用（无 /batch 或返回异常）。"
                f"请检查 go_bridge/rush_bridge.exe、go_bridge/bridge.log；"
                f"若你自行占用该端口的不是 rush_bridge，请结束进程或改 MT_GO_BRIDGE_PORT。"
            )
        raise RuntimeError(f"go_bridge /batch 不可达: {e}.{hint404}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"go_bridge /batch 不可达: {e}") from e
    try:
        j = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"go_bridge /batch 非 JSON: {raw[:300]!r}") from e
    if not j.get("ok"):
        raise RuntimeError("go_bridge /batch: " + str(j.get("error") or "?"))
    results = j.get("results")
    if not isinstance(results, list) or len(results) != len(tasks):
        raise RuntimeError("go_bridge /batch 返回 results 条数异常")
    return results


def go_bridge_prewarm_http_dispatch(
    targets: list[dict],
    *,
    concurrency: int = 32,
    outer_timeout: float = 60.0,
    tls_impersonate: str | None = None,
) -> dict:
    """go_bridge /prewarm：按 target 列表并发执行 HTTP GET（通常为 H5 根域名，仅建连/探路）。"""
    if not targets:
        return {"ok": True, "results": [], "okCount": 0, "total": 0}
    _ensure_go_bridge_running()
    cap = _batch_rush_parallel_cap()
    try:
        conc = max(1, min(cap, int(concurrency)))
    except (TypeError, ValueError):
        conc = min(cap, 32)
    try:
        timeout_sec = float(outer_timeout)
    except (TypeError, ValueError):
        timeout_sec = 60.0
    timeout_sec = max(10.0, min(600.0, timeout_sec))
    envelope = {"targets": targets, "concurrency": conc, "tlsImpersonate": TLS_PROFILE}
    bridge_url = f"http://127.0.0.1:{_go_bridge_port()}/prewarm"
    req = urllib.request.Request(
        bridge_url,
        data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        port = _go_bridge_port()
        hint404 = ""
        if int(e.code or 0) == 404:
            hint404 = (
                f" 当前 127.0.0.1:{port} 上 go_bridge（rush_bridge）无 /prewarm 或返回 404。"
                f"请升级/重编 go_bridge，或检查 go_bridge/bridge.log；勿与 node_bridge(19090) 混淆。"
            )
        raise RuntimeError(f"go_bridge /prewarm 不可达: {e}.{hint404}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"go_bridge /prewarm 不可达: {e}") from e
    try:
        j = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"go_bridge /prewarm 非 JSON: {raw[:300]!r}") from e
    if not j.get("ok"):
        raise RuntimeError("go_bridge /prewarm: " + str(j.get("error") or "?"))
    # #region agent log
    try:
        (_ROOT / "debug-eb180b.log").open("a", encoding="utf-8").write(
            json.dumps(
                {
                    "sessionId": "eb180b",
                    "timestamp": int(time.time() * 1000),
                    "location": "app.py:go_bridge_prewarm_http_dispatch",
                    "message": "go_bridge_prewarm_response",
                    "hypothesisId": "H3",
                    "data": {
                        "bridge_port": int(_go_bridge_port()),
                        "n_targets": len(targets),
                        "ok_count": int(j.get("okCount") or 0),
                        "total": int(j.get("total") or len(targets)),
                    },
                    "runId": "prewarm-debug",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    except Exception:
        pass
    # #endregion
    return j if isinstance(j, dict) else {"ok": True, "results": []}


# 兼容旧代码/外部脚本对函数名与桥入口的引用（实际均为 go_bridge）
node_batch_http_dispatch = go_bridge_batch_http_dispatch
node_prewarm_http_dispatch = go_bridge_prewarm_http_dispatch
_node_http_bridge_port = _go_bridge_port


# ---- node_bridge (server.cjs, BB SDK + curl_cffi_worker) 启动管理 ----
# 注意：node_bridge 与 go_bridge 是两个独立进程
#   - go_bridge   : rush_bridge.exe，端口默认 19091（抢购发包 / ja3-client）
#   - node_bridge : server.cjs，端口默认 19090（BB SDK 加密 / GDX 验证）

_node_bridge_lock = threading.Lock()
_node_bridge_started = False
_node_bridge_boot_attempted = False
_node_bridge_started_ports: set[int] = set()
_node_bridge_boot_attempted_ports: set[int] = set()
_node_bridge_starting_ports: set[int] = set()
_node_bridge_procs: dict[int, subprocess.Popen] = {}


def _node_bridge_port() -> int:
    try:
        return max(1, min(65535, int((os.environ.get("MT_NODE_HTTP_BRIDGE_PORT") or "19090").strip())))
    except (TypeError, ValueError):
        return 19090


def _node_bridge_count() -> int:
    try:
        return max(1, min(16, int((os.environ.get("MT_NODE_BRIDGE_COUNT") or "2").strip() or "2")))
    except (TypeError, ValueError):
        return 2


def _parse_node_bridge_ports(raw: str) -> list[int]:
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


def _node_bridge_ports() -> list[int]:
    raw = os.environ.get("MT_BB_NODE_PORTS") or os.environ.get("MT_NODE_BRIDGE_PORTS") or ""
    ports = _parse_node_bridge_ports(raw)
    if ports:
        return ports
    base = _node_bridge_port()
    count = _node_bridge_count()
    try:
        go_port = _go_bridge_port()
    except Exception:
        go_port = -1
    ports = []
    for i in range(count):
        port = base + i * 2
        while port == go_port or port in ports or port > 65535:
            port = 1 if port >= 65535 else port + 1
        ports.append(port)
    return ports


def _publish_node_bridge_ports_env(ports: list[int]) -> None:
    if ports:
        os.environ["MT_BB_NODE_PORTS"] = ",".join(str(p) for p in ports)


def _node_bridge_bb_health_ok(port: int, timeout: float = 1.5) -> bool:
    """探测 node_bridge 是否带 BB SDK：GET /bb/health 返回 wasmReady=True 才算就绪。"""
    try:
        import urllib.request as _ur
        with _ur.urlopen(f"http://127.0.0.1:{port}/bb/health", timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
        j = json.loads(raw)
        return bool(j.get("ok") and j.get("wasmReady"))
    except Exception:
        return False


def _wait_for_node_bridge_ready(port: int, max_wait: float = 30.0) -> None:
    """等待 node_bridge BB SDK 就绪（启动 + WASM 加载约 1-3s）。"""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if _node_bridge_bb_health_ok(port):
            return
        time.sleep(0.2)
    raise RuntimeError(
        f"node_bridge BB SDK 在 {max_wait}s 内未就绪 (127.0.0.1:{port}/bb/health)。"
        f"请检查 node_bridge/server.cjs 启动日志（终端窗口或控制台）。"
    )


def _terminate_node_bridge_proc_locked(port: int) -> None:
    proc = _node_bridge_procs.get(port)
    if not proc or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=2.0)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _spawn_node_bridge_port_locked(port: int, *, force: bool = False) -> None:
    global _node_bridge_boot_attempted
    if force:
        _terminate_node_bridge_proc_locked(port)
        _node_bridge_started_ports.discard(port)
        _node_bridge_boot_attempted_ports.discard(port)
        _node_bridge_starting_ports.discard(port)
    elif _node_bridge_bb_health_ok(port):
        _node_bridge_started_ports.add(port)
        _node_bridge_starting_ports.discard(port)
        return
    elif port in _node_bridge_boot_attempted_ports:
        proc = _node_bridge_procs.get(port)
        if (proc and proc.poll() is None) or _probe_tcp_listening("127.0.0.1", port):
            _node_bridge_starting_ports.add(port)
            return
        _node_bridge_boot_attempted_ports.discard(port)

    node_exe = _resolve_node_executable()
    if not node_exe:
        raise RuntimeError(
            "未找到 node.exe。请安装 Node.js (https://nodejs.org/) 或设 MT_NODE_EXE 指向 node.exe。"
        )
    node_src = _ROOT / "node_bridge"
    if not node_src.is_dir():
        raise RuntimeError(f"缺少 node_bridge 目录: {node_src}")
    server_cjs = node_src / "server.cjs"
    if not server_cjs.is_file():
        raise RuntimeError(f"缺少 node_bridge/server.cjs: {server_cjs}")
    if (not force) and port in _node_bridge_boot_attempted_ports and _node_bridge_bb_health_ok(port):
        _node_bridge_started_ports.add(port)
        return

    _node_bridge_boot_attempted = True
    _node_bridge_boot_attempted_ports.add(port)
    _node_bridge_starting_ports.add(port)
    env = os.environ.copy()
    env["MT_NODE_HTTP_BRIDGE_PORT"] = str(port)
    env.pop("MT_BB_CLIENT_BASE", None)
    log_path = node_src / f"bridge-{port}.log"
    try:
        log_f = open(log_path, "a", encoding="utf-8")
    except OSError:
        log_f = subprocess.DEVNULL
    popen_kwargs = {
        "cwd": str(node_src),
        "env": env,
        "stdout": log_f,
        "stderr": log_f,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        popen_kwargs["startupinfo"] = si
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | detached
    try:
        _node_bridge_procs[port] = subprocess.Popen([node_exe, "server.cjs"], **popen_kwargs)
        print(
            f"[node_bridge] 子进程已启动 port={port}，等待 http://127.0.0.1:{port}/bb/health 探测成功…",
            flush=True,
        )
    except OSError as ex:
        _node_bridge_boot_attempted_ports.discard(port)
        _node_bridge_starting_ports.discard(port)
        raise RuntimeError(f"启动 node_bridge({port}) 失败: {ex}") from ex


def _ensure_node_bridge_port_running(port: int, *, force: bool = False, max_wait: float = 30.0) -> None:
    port = max(1, min(65535, int(port)))
    if (not force) and port in _node_bridge_started_ports and _node_bridge_bb_health_ok(port):
        return
    if (not force) and _node_bridge_bb_health_ok(port):
        _node_bridge_started_ports.add(port)
        _node_bridge_starting_ports.discard(port)
        return
    with _node_bridge_lock:
        if (not force) and _node_bridge_bb_health_ok(port):
            _node_bridge_started_ports.add(port)
            _node_bridge_starting_ports.discard(port)
            return
        if (not force) and _probe_tcp_listening("127.0.0.1", port):
            _node_bridge_boot_attempted_ports.add(port)
            _node_bridge_starting_ports.add(port)
        else:
            _spawn_node_bridge_port_locked(port, force=force)
    try:
        _wait_for_node_bridge_ready(port, max_wait=max_wait)
    except Exception:
        with _node_bridge_lock:
            _node_bridge_started_ports.discard(port)
            _node_bridge_boot_attempted_ports.discard(port)
            _node_bridge_starting_ports.discard(port)
        raise
    with _node_bridge_lock:
        _node_bridge_started_ports.add(port)
        _node_bridge_starting_ports.discard(port)
    print(f"[node_bridge] 已就绪: http://127.0.0.1:{port}/bb/health", flush=True)


def _ensure_node_bridge_running() -> None:
    """启动并保活 node_bridge (server.cjs) 端口池。

    默认启动 2 个端口：19090/19092，用于分摊 /bb/sign_post。
    BB SDK 加载失败硬抛错（禁止任何降级）。
    """
    global _node_bridge_started
    ports = _node_bridge_ports()
    _publish_node_bridge_ports_env(ports)
    if _node_bridge_started and all(_node_bridge_bb_health_ok(p) for p in ports):
        return
    for port in ports:
        _ensure_node_bridge_port_running(port)
    _node_bridge_started = True
    print(f"[node_bridge] 端口池已就绪: {','.join(str(p) for p in ports)}", flush=True)


def restart_node_bridge_port(port: int, *, max_wait: float = 12.0) -> dict:
    """Restart one node_bridge port after a local helper detects it is unreachable."""
    try:
        _ensure_node_bridge_port_running(int(port), force=True, max_wait=max_wait)
        return {"ok": True, "port": int(port)}
    except Exception as ex:
        return {"ok": False, "port": int(port) if str(port).isdigit() else port, "error": str(ex)}


def _runtime_rush_proxy_pool_snapshot() -> list[str]:
    """Return the active proxy pool; Go rush paths pick the concrete proxy at send time."""
    try:
        from proxy_pool import active_urls

        return [str(u or "").strip() for u in active_urls() if str(u or "").strip()]
    except Exception:
        return []


def _go_bridge_rush_loop_request(
    attempts: list[dict],
    *,
    outer_timeout: float = 180.0,
    rush_loop_delay_ms: int = 0,
    tls_impersonate: str | None = None,
    fire_and_forget: bool = False,
    lane_parallel: bool = False,
    rush_jitter_ms_max: int | None = None,
    rush_rounds: int = 1,
    rush_round_gap_ms: int = 0,
    rush_round_gap_jitter_ms: int = 0,
    runtime_proxy_pool: bool = False,
    proxy_pool: list[str] | None = None,
) -> dict:
    """
    go_bridge POST /rush_loop：在 rush_bridge 内按 Python rush() 同款规则连续发包（软限流/连接失败则换下一档 attempts）。
    attempts 由 Session._rush_build_node_attempt_packages 预组（每轮新签名）；走代理池时 Go 发包前即时取线。
    rush_loop_delay_ms：上一枪收完响应后、下一枪 POST 发出前在桥内 sleep 的毫秒数（0=紧连，与 WebUI「间隔(ms)」一致）。
    rushJitterMsMax：每枪 POST 发出前额外 [0,N]ms 均匀随机（默认取 MT_RUSH_JITTER_MS_MAX，与 curl 路径一致）。
    返回 JSON：ok, last（已规范化的抢购 dict）, lastProxyUrl（最后一跳出口，供 Python 写回会话）。
    """
    if not attempts:
        raise RuntimeError("rush_loop: attempts 为空")
    _ensure_go_bridge_running()
    envelope = {
        "attempts": attempts,
        "proxySequence": [
            str(a.get("proxyUrl") or "").strip() for a in attempts if isinstance(a, dict)
        ],
    }
    proxy_pool_lines = [str(u or "").strip() for u in (proxy_pool or []) if str(u or "").strip()]
    if runtime_proxy_pool and proxy_pool_lines:
        envelope["runtimeProxyPool"] = True
        envelope["proxyPool"] = proxy_pool_lines
    try:
        dms = int(rush_loop_delay_ms)
    except (TypeError, ValueError):
        dms = 0
    dms = max(0, min(300_000, dms))
    # 始终下发，rush_bridge 在「上一枪返回后、下一枪 POST 前」严格 sleep（与 WebUI 间隔 ms 一致）
    envelope["rushLoopDelayMs"] = dms
    try:
        jm = int(_rush_jitter_ms_max() if rush_jitter_ms_max is None else rush_jitter_ms_max)
    except (TypeError, ValueError):
        jm = _rush_jitter_ms_max()
    envelope["rushJitterMsMax"] = max(0, min(100, jm))
    if fire_and_forget:
        envelope["fireAndForget"] = True
    if lane_parallel:
        envelope["laneParallel"] = True
    try:
        rr = int(rush_rounds)
    except (TypeError, ValueError):
        rr = 1
    rr = max(1, min(100, rr))
    if rr > 1:
        envelope["rushRounds"] = rr
        try:
            envelope["rushRoundGapMs"] = max(0, min(300_000, int(rush_round_gap_ms)))
        except (TypeError, ValueError):
            envelope["rushRoundGapMs"] = 0
        try:
            envelope["rushRoundGapJitterMs"] = max(0, min(60_000, int(rush_round_gap_jitter_ms)))
        except (TypeError, ValueError):
            envelope["rushRoundGapJitterMs"] = 0
    envelope["tlsImpersonate"] = TLS_PROFILE
    bridge_url = f"http://127.0.0.1:{_go_bridge_port()}/rush_loop"
    req = urllib.request.Request(
        bridge_url,
        data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=outer_timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        port = _go_bridge_port()
        hint404 = ""
        if int(e.code or 0) == 404:
            hint404 = (
                f" 当前 127.0.0.1:{port} 不是带 /rush_loop 的 go_bridge（rush_bridge），"
                f"或端口被其它程序占用。请结束冲突进程、检查 go_bridge/bridge.log，或改 MT_GO_BRIDGE_PORT。"
            )
        raise RuntimeError(f"go_bridge /rush_loop 不可达: {e}.{hint404}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"go_bridge /rush_loop 不可达: {e}") from e
    try:
        j = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"go_bridge /rush_loop 非 JSON: {raw[:300]!r}") from e
    if not j.get("ok"):
        raise RuntimeError("go_bridge /rush_loop: " + str(j.get("error") or "?"))
    return j


def _normalize_rush_loop_trace_rows(tr):
    """
    rush_bridge execRushLoop 每条 trace 含 ms（发起到收完响应）。
    统一为 int，避免 JSON null/字符串导致前端不显示。
    """
    if not isinstance(tr, list):
        return tr
    out = []
    for row in tr:
        if not isinstance(row, dict):
            out.append(row)
            continue
        d = dict(row)
        v = d.get("ms")
        if v is None:
            v = d.get("elapsedMs")
        try:
            if v is not None and v != "":
                d["ms"] = int(round(float(v)))
            else:
                d["ms"] = 0
        except (TypeError, ValueError):
            d["ms"] = 0
        out.append(d)
    return out


def _strip_dead_loopback_proxy_env() -> None:
    """清除误设的 127.0.0.1:1（无服务）等，避免 curl 全程连错；仅改当前进程 os.environ。"""
    keys = (
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
        "ALL_PROXY", "all_proxy",
    )
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        if not v:
            continue
        compact = v.replace(" ", "").lower()
        if "127.0.0.1:1" in compact or "localhost:1" in compact:
            os.environ.pop(k, None)


_strip_dead_loopback_proxy_env()
# 不再自动加载 local_proxy.env；出口默认本机直连。若需代理：在账号「HTTP 代理」中填写，
# 或在运行环境中自行设置 HTTPS_PROXY/HTTP_PROXY（系统/终端），不由项目目录内文件注入。

from shield import (
    make_kv_pair, make_h5_rush_kv_pair, make_device_token, make_raw_id,
    make_env_tag_for_raw_id, make_cert_tag_for_raw_id,
    format_d_time_mt_order_header,
    seal_param, unseal_param, param_encrypt,
    make_h5_devid, make_h5_session, make_du_cookie, shield_for_post, shield_for_get,
    make_bs_cookie,
    _md5, _stream_xor,
    RELEASE_VER, TRACE_TOKEN, RUSH_MT_INFO, DEFAULT_APPID, SHIELD_VER, WASM_TAG,
)

API_HOST = "https://app.moutai519.com.cn"
H5_HOST = "https://h5.moutai519.com.cn"
PAY_HOST = "https://payapi.moutai519.com.cn"
CODE_SALT = "2af72f100c356273d46284f6fd1dfc08"

# 支付相关
_PAY_APPID = "MT519ANDROID"
_PAY_KEY = "8C79446361034bd2aE98b27E153a2eA8"
_PAY_SM4 = "e881E52D7932Cf00"

TLS_PROFILE = "chrome_138"
RUSH_DEFAULT_AB_PROFILE = "chrome138"
# 全局固定：抢购链路统一使用 ja3-client chrome_138 + Android Chrome 138 UA，不按账号轮换。
H5_FIXED_CHROME_VERSION = "138.0.7204.168"
RUSH_AB_PROFILES = {
    "chrome138": {
        "label": "chrome138:chrome_138/ua138_android",
        "tls": "chrome_138",
        "ua_chrome": H5_FIXED_CHROME_VERSION,
        "sec_ch_major": "138",
        "header_profile": "android_wv",
    },
}


def rush_ab_profile_config(name: str | None) -> dict:
    key = RUSH_DEFAULT_AB_PROFILE
    cfg = RUSH_AB_PROFILES[key]
    return {**cfg, "key": key}


def _android_webview_sec_ch_ua(major: str | int) -> str:
    m = str(major or "").strip() or "138"
    return f'"Not)A;Brand";v="99", "Android WebView";v="{m}", "Chromium";v="{m}"'

def _is_direct_proxy_marker(s: str) -> bool:
    """账号「代理」填本机直连时的标记（有代理池时单靠空串仍会走分片池，须显式标记）。"""
    p = (s or "").strip()
    if p in ("本机", "直连", "-"):
        return True
    return p.lower() in ("direct", "local", "none", "0")


def _normalize_proxy_url(url: str) -> str:
    """补全代理 URL（如 127.0.0.1:7890 → http://127.0.0.1:7890），供 curl_cffi 使用。"""
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        return "http://" + u
    low = u.lower()
    if low.startswith("ks5h://"):
        return "socks5h://" + u[7:]
    if low.startswith("ks5://"):
        return "socks5://" + u[6:]
    if low.startswith("socks5s://"):
        return "socks5h://" + u[10:]
    return u


def _proxy_from_environ() -> str:
    """系统/终端里配置的代理（Clash 勾选「系统代理」时常会写入）。账号未单独填代理时使用。"""
    for key in (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        v = (os.environ.get(key) or "").strip()
        if v:
            return _normalize_proxy_url(v)
    return ""


def _proxy_netloc_no_auth(proxy_url: str) -> str | None:
    """从代理 URL 得到 `host:port` 或 `[v6]:port`，不含账密（供 curl PROXY 与日志）。"""
    pr = urlparse((proxy_url or "").strip())
    host = pr.hostname
    if not host:
        return None
    port = pr.port
    if ":" in host and "." not in host:
        hp = f"[{host}]"
    else:
        hp = host
    return f"{hp}:{port}" if port else hp


def _proxy_hostport_for_log(proxy_url: str) -> str:
    """日志：有用户名时打 `user@host:port`，否则 `host:port`；永不打印密码。"""
    p = (proxy_url or "").strip()
    if not p:
        return "本机直连"
    try:
        u = urlparse(p)
        host = u.hostname or ""
        port = u.port or ""
        hp = f"{host}:{port}" if (host and port) else (host or "")
        user = (u.username or "").strip()
        if user and hp:
            return f"{user}@{hp}"
        if hp:
            return hp
    except Exception:
        pass
    return "已配置"


def _extract_gdx_cookie_value(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    m = re.search(r"(?:^|;\s*)gdxidpyhxdE=([^;]+)", raw, re.I)
    if m:
        raw = m.group(1).strip()
    return raw


def _decode_gdx_cookie_value(value: str) -> str:
    raw = _extract_gdx_cookie_value(value)
    if not raw:
        return ""
    try:
        return unquote(raw)
    except Exception:
        return raw


def _gdx_cookie_timestamp_ms(value: str) -> int | None:
    raw = _decode_gdx_cookie_value(value)
    if not raw:
        return None
    idx = raw.rfind(":")
    if idx <= 0:
        return None
    try:
        return int(raw[idx + 1 :])
    except (TypeError, ValueError):
        return None


def _gdx_cookie_has_room(value: str, now_ms: int | None = None) -> bool:
    ts = _gdx_cookie_timestamp_ms(value)
    if not ts:
        return False
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    return (ts - now_ms) > _GDX_REFRESH_MIN_REMAINING_MS


def _build_gdx_cookie_via_node(payload_obj: dict, timeout: float = 5.0) -> dict:
    node_exe = _resolve_node_executable()
    if not node_exe:
        return {"success": False, "message": "node executable not found"}
    if not os.path.exists(_YIDUN_GDX_SCRIPT):
        return {"success": False, "message": f"missing {_YIDUN_GDX_SCRIPT}"}
    payload = json.dumps(payload_obj or {}, ensure_ascii=False).encode("utf-8")
    try:
        proc = subprocess.run(
            [node_exe, _YIDUN_GDX_SCRIPT],
            input=payload,
            capture_output=True,
            timeout=timeout,
            cwd=os.path.dirname(_YIDUN_GDX_SCRIPT),
        )
        stdout = proc.stdout.decode("utf-8", errors="replace").strip()
        if not stdout:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            return {"success": False, "message": f"node gdx no output; stderr={stderr[:300]}"}
        data = json.loads(stdout)
        if not isinstance(data, dict):
            return {"success": False, "message": "node gdx returned invalid format"}
        return data
    except subprocess.TimeoutExpired:
        return {"success": False, "message": f"node gdx timeout ({timeout}s)"}
    except Exception as ex:
        return {"success": False, "message": f"node gdx failed: {ex}"}


def _bangcle_warmup_via_node(payload_obj: dict, timeout: float = 20.0) -> dict:
    node_exe = _resolve_node_executable()
    if not node_exe:
        return {"success": False, "message": "node executable not found"}
    if not os.path.exists(_BANGCLE_WARMUP_SCRIPT):
        return {"success": False, "message": f"missing {_BANGCLE_WARMUP_SCRIPT}"}
    payload = json.dumps(payload_obj or {}, ensure_ascii=False).encode("utf-8")
    try:
        proc = subprocess.run(
            [node_exe, _BANGCLE_WARMUP_SCRIPT],
            input=payload,
            capture_output=True,
            timeout=timeout,
            cwd=os.path.dirname(_BANGCLE_WARMUP_SCRIPT),
        )
        stdout = proc.stdout.decode("utf-8", errors="replace").strip()
        if not stdout:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            return {"success": False, "message": f"node bangcle warmup no output; stderr={stderr[:300]}"}
        data = json.loads(stdout)
        if not isinstance(data, dict):
            return {"success": False, "message": "node bangcle warmup returned non-object"}
        return data
    except subprocess.TimeoutExpired:
        return {"success": False, "message": f"node bangcle warmup timeout ({timeout}s)"}
    except Exception as ex:
        return {"success": False, "message": f"node bangcle warmup failed: {ex}"}


def _bangcle_warmup_timeout_ms() -> int:
    raw = os.environ.get("MT_BANGCLE_WARMUP_TIMEOUT_MS") or "3000"
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        n = 3000
    return max(500, min(30_000, n))


def _bangcle_warmup_on_rush() -> bool:
    v = (os.environ.get("MT_BANGCLE_WARMUP_ON_RUSH") or "0").strip().lower()
    return v not in ("0", "false", "no", "off")


def _yidun_local_attempts() -> int:
    raw = os.environ.get("MT_YIDUN_LOCAL_ATTEMPTS", "1")
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        n = 1
    return max(1, min(5, n))


def _ttocr_solve_yidun(captcha_id: str = _TTOCR_CAPTCHA_ID, referer: str = _TTOCR_REFERER) -> dict:
    import urllib.parse as _up
    import urllib.request as _ur

    appkey = _TTOCR_APPKEY
    if not appkey:
        return {"success": False, "message": "TTOCR_APPKEY not configured"}
    post_data = _up.urlencode({
        "appkey": appkey,
        "id": captcha_id,
        "referer": referer,
        "itemid": 500,
    }).encode()
    req = _ur.Request("http://api.ttocr.com/api/recognize2", data=post_data, method="POST")
    try:
        with _ur.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
    except Exception as ex:
        return {"success": False, "message": f"ttocr network error: {ex}"}
    if result.get("status") == 1:
        validate = result.get("data", "")
        if validate:
            return {"success": True, "validate": validate, "raw": result}
        return {"success": False, "message": "ttocr returned empty data"}
    return {"success": False, "message": f"ttocr failed status={result.get('status')} msg={result.get('msg', '')}"}


def _yidun_detail_kind_from_referer(referer: str) -> str:
    ref = str(referer or "").strip()
    if not ref:
        return ""
    try:
        path = urlparse(ref).path or ""
    except Exception:
        path = ref
    m = re.search(r"/mt/item/([^/?#]+)", path)
    return (m.group(1) if m else "").strip()


def _yidun_captcha_id_for_context(
    *,
    captcha_id: str = "",
    referer: str = "",
    item_code: str = "",
) -> str:
    explicit = str(captcha_id or "").strip()
    code = str(item_code or "").strip()
    by_item = _YIDUN_CAPTCHA_ID_BY_ITEM_CODE.get(code)
    kind = _yidun_detail_kind_from_referer(referer)
    by_kind = _YIDUN_CAPTCHA_ID_BY_DETAIL_KIND.get(kind)
    resolved = by_item or by_kind or explicit or _TTOCR_CAPTCHA_ID
    if explicit and explicit != resolved:
        print(
            f"[验证] 修正易盾 captchaId {explicit[:8]}... -> {resolved[:8]}... "
            f"(item={code or '-'} refererKind={kind or '-'})",
            flush=True,
        )
    return resolved


def _yidun_referer_for_context(referer: str = "", item_code: str = "") -> str:
    ref = str(referer or "").strip()
    code = str(item_code or "").strip()
    if code and _YIDUN_REFERER_BY_ITEM_CODE.get(code):
        mapped = _YIDUN_REFERER_BY_ITEM_CODE[code]
        if ref and _yidun_detail_kind_from_referer(ref) != _yidun_detail_kind_from_referer(mapped):
            print(
                f"[验证] 修正易盾 referer {ref} -> {mapped} (item={code})",
                flush=True,
            )
        return mapped
    return ref


def _yidun_solve_via_local_ocr(
    *,
    captcha_id: str,
    referer: str,
    priority_record_id: int | str,
    item_code: str = "",
) -> dict:
    if not _YIDUN_LOCAL_URL:
        return {"success": False, "message": "MT_YIDUN_LOCAL_URL not configured"}
    ref = str(referer or _TTOCR_REFERER).strip() or _TTOCR_REFERER
    resolved_captcha_id = _yidun_captcha_id_for_context(
        captcha_id=captcha_id,
        referer=ref,
        item_code=item_code,
    )
    body_base = {
        "api_key": _YIDUN_LOCAL_API_KEY,
        "captchaId": resolved_captcha_id,
        "referer": ref,
        "pageUrl": ref,
        "itemCode": str(item_code or "").strip(),
        "priorityRecordId": str(priority_record_id or "").strip(),
        "priorityUserRecordId": str(priority_record_id or "").strip(),
        "extraData": {
            "priorityRecordId": int(priority_record_id)
            if str(priority_record_id or "").strip().isdigit()
            else str(priority_record_id or "").strip()
        },
        "proxyUrl": "",
    }
    if _YIDUN_LOCAL_OCR_URL:
        body_base["ocrApiUrl"] = _YIDUN_LOCAL_OCR_URL

    local_attempts = _yidun_local_attempts()
    last_error: dict | None = None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt_no in range(1, local_attempts + 1):
        mode_label = f"direct#{attempt_no}/{local_attempts}"
        data = json.dumps(body_base, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            _YIDUN_LOCAL_URL + "/api/verify",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with opener.open(req, timeout=90) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as ex:
            raw = ""
            try:
                raw = ex.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            last_error = {"success": False, "message": f"local OCR yidun HTTP {ex.code} ({mode_label}): {raw[:300]}", "raw": raw}
            print(f"[验证] 本地OCR直连请求失败({attempt_no}/{local_attempts}): HTTP {ex.code} {raw[:160]}", flush=True)
            continue
        except Exception as ex:
            last_error = {"success": False, "message": f"local OCR yidun error ({mode_label}): {ex}"}
            print(f"[验证] 本地OCR直连请求异常({attempt_no}/{local_attempts}): {ex}", flush=True)
            continue
        try:
            result = json.loads(raw)
        except Exception:
            last_error = {"success": False, "message": f"local OCR yidun returned non-JSON ({mode_label}): {raw[:300]}", "raw": raw}
            print(f"[验证] 本地OCR返回非JSON({attempt_no}/{local_attempts}): {raw[:160]}", flush=True)
            continue

        data_obj = result.get("data") if isinstance(result, dict) else None
        payload = data_obj if isinstance(data_obj, dict) else result
        if isinstance(payload, dict) and payload.get("result") and (payload.get("finalValidate") or payload.get("validate")):
            if attempt_no > 1:
                print(f"[验证] 本地OCR第{attempt_no}/{local_attempts}次通过", flush=True)
            return {
                "success": True,
                "validate": str(payload.get("finalValidate") or payload.get("v") or payload.get("validate") or ""),
                "v": str(payload.get("v") or payload.get("finalValidate") or payload.get("validate") or ""),
                "rawValidate": str(payload.get("rawValidate") or payload.get("validate") or ""),
                "fp": str(payload.get("fp") or ""),
                "token": str(payload.get("token") or ""),
                "zoneId": str(payload.get("zoneId") or ""),
                "captchaId": str(payload.get("captchaId") or resolved_captcha_id),
                "raw": result,
            }
        last_error = {"success": False, "message": f"local OCR yidun failed ({mode_label}): {result}", "raw": result}
        print(f"[验证] 本地OCR未通过({attempt_no}/{local_attempts}) validate为空", flush=True)
    return last_error or {"success": False, "message": "local OCR yidun failed: not executed"}


def _yidun_local_is_legacy_url() -> bool:
    try:
        p = urlparse(_YIDUN_LOCAL_URL)
        host = (p.hostname or "").lower()
        return host in ("127.0.0.1", "localhost", "::1") and int(p.port or 80) == 8887
    except Exception:
        return False


def _yidun_local_probe_verify(timeout: float = 0.8) -> bool:
    body = json.dumps({"captchaId": _TTOCR_CAPTCHA_ID}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _YIDUN_LOCAL_URL + "/api/verify",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            resp.read(200)
        return True
    except urllib.error.HTTPError as ex:
        try:
            raw = ex.read(300).decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return int(getattr(ex, "code", 0) or 0) in (400, 401, 405) and "/api/verify" not in raw.lower()
    except Exception:
        return False


def _yidun_local_ready(timeout: float = 0.5) -> bool:
    if not _YIDUN_LOCAL_URL:
        return False
    if _yidun_local_probe_verify(timeout=min(max(timeout, 0.2), 1.5)):
        return True
    try:
        req = urllib.request.Request(_YIDUN_LOCAL_URL + "/api/health", method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return isinstance(data, dict) and data.get("status") == "ok"
    except Exception:
        return False


def _yidun_tuned_ocr_ready(timeout: float = 0.6) -> bool:
    if _YIDUN_LOCAL_OCR_URL != "http://127.0.0.1:8000/slide_match":
        return True
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/docs", method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            resp.read(80)
        return True
    except Exception:
        return False


def _ensure_yidun_tuned_ocr_service_for_auto() -> tuple[bool, str]:
    global _yidun_tuned_ocr_proc
    if _YIDUN_LOCAL_OCR_URL != "http://127.0.0.1:8000/slide_match":
        return True, ""
    if _yidun_tuned_ocr_ready(timeout=0.3):
        return True, ""
    if not _YIDUN_TUNED_OCR_DIR.is_dir():
        return False, f"5.10 OCR directory missing: {_YIDUN_TUNED_OCR_DIR}"
    python_cmd = _resolve_python_command()
    with _yidun_tuned_ocr_lock:
        if _yidun_tuned_ocr_ready(timeout=0.3):
            return True, ""
        try:
            _yidun_tuned_ocr_proc = subprocess.Popen(
                [*python_cmd, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                cwd=str(_YIDUN_TUNED_OCR_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as ex:
            return False, f"failed to start 5.10 OCR: {ex}"
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if _yidun_tuned_ocr_ready(timeout=0.5):
            return True, ""
        time.sleep(0.2)
    rc = _yidun_tuned_ocr_proc.poll() if _yidun_tuned_ocr_proc is not None else None
    return False, f"5.10 OCR not ready processCode={rc} dir={_YIDUN_TUNED_OCR_DIR}"


def _ensure_yidun_local_service_for_auto() -> tuple[bool, str]:
    global _yidun_local_proc
    ocr_ok, ocr_err = _ensure_yidun_tuned_ocr_service_for_auto()
    if not ocr_ok:
        return False, ocr_err
    if _yidun_local_ready(timeout=0.3):
        return True, ""
    if _yidun_local_is_legacy_url() and _YIDUN_LOCAL_SCRIPT.is_file():
        with _yidun_local_lock:
            if _yidun_local_ready(timeout=0.3):
                return True, ""
            node_bin = _resolve_node_executable() or ("node.exe" if os.name == "nt" else "node")
            env = os.environ.copy()
            if _YIDUN_LOCAL_OCR_URL:
                env.setdefault("OCR_API_URL", _YIDUN_LOCAL_OCR_URL)
            try:
                _yidun_local_proc = subprocess.Popen(
                    [node_bin, str(_YIDUN_LOCAL_SCRIPT)],
                    cwd=str(_YIDUN_LOCAL_SCRIPT.parent),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except Exception as ex:
                return False, f"failed to start local yidun service: {ex}"
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if _yidun_local_ready(timeout=0.5):
                return True, ""
            time.sleep(0.2)
        rc = _yidun_local_proc.poll() if _yidun_local_proc is not None else None
        return False, f"local yidun service not ready processCode={rc} script={_YIDUN_LOCAL_SCRIPT}"
    return False, f"local yidun service unavailable: {_YIDUN_LOCAL_URL}"


def humanize_requests_error(exc: BaseException) -> str:
    """
    将 curl_cffi / 代理层异常转成可操作的说明（常见于 407 代理需认证、隧道失败等）。
    业务「失效」多为出口/代理配置问题而非 MT 接口逻辑变更。
    """
    msg = str(exc)
    low = msg.lower()
    if "407" in msg or "proxy authentication required" in low:
        return (
            "代理要求身份认证（HTTP 407）：当前地址未带上代理账号密码，连接被拒绝。"
            "请在账号「HTTP 代理」填写完整 URL：`socks5://用户名:密码@IP:端口` 或 `socks5h://用户名:密码@IP:端口`；"
            "若用 local_proxy_pool.txt，四段格式 `host/port/user/password` 会存为 socks5h（SK5H）。"
            "仅填 `http://IP:端口` 对多数付费代理无效。"
        )
    if "connect tunnel failed" in low or "proxyerror" in low:
        return (
            "代理隧道失败（无法通过代理访问目标）：" + msg + "。"
            "请检查代理是否需认证、是否过期、端口是否正确，或暂时清空代理改直连排查。"
        )
    if "ssl" in low and ("wrong version" in low or "certificate" in low):
        return "TLS/证书异常：" + msg + "。可尝试更换节点或更新 curl_cffi。"
    if "timed out" in low or "timeout" in low or "connection timed out" in low:
        return "连接超时：" + msg + "。请检查代理/网络或增大超时。"
    return "网络请求异常：" + msg


_UA_POOL = [
    (14, "SM-G991B", "UP1A.231005.007", "124.0.6367.179"),
    (13, "Pixel 7",  "TQ3A.230901.001", "120.0.6099.230"),
    (14, "22081212C","UKQ1.230917.001", "122.0.6261.64"),
    (13, "V2254A",   "TP1A.220624.014", "119.0.6045.193"),
    (14, "OPH2201",  "UP1A.231005.007", "123.0.6312.118"),
]

_H5_FP_PROFILE_BY_MFR = {
    "samsung": {"mem": 12, "dpr": 3.0, "cpu": 8, "gpu": "Adreno (TM) 730", "gpuv": "Qualcomm~Adreno (TM) 730"},
    "xiaomi": {"mem": 12, "dpr": 2.75, "cpu": 8, "gpu": "Adreno (TM) 730", "gpuv": "Qualcomm~Adreno (TM) 730"},
    "vivo": {"mem": 8, "dpr": 2.75, "cpu": 8, "gpu": "Adreno (TM) 642L", "gpuv": "Qualcomm~Adreno (TM) 642L"},
    "oppo": {"mem": 12, "dpr": 3.0, "cpu": 8, "gpu": "Adreno (TM) 730", "gpuv": "Qualcomm~Adreno (TM) 730"},
    "oneplus": {"mem": 12, "dpr": 3.0, "cpu": 8, "gpu": "Adreno (TM) 730", "gpuv": "Qualcomm~Adreno (TM) 730"},
    "google": {"mem": 8, "dpr": 2.625, "cpu": 8, "gpu": "Mali-G78", "gpuv": "ARM~Mali-G78"},
    "huawei": {"mem": 8, "dpr": 2.75, "cpu": 8, "gpu": "Mali-G78", "gpuv": "ARM~Mali-G78"},
    "honor": {"mem": 8, "dpr": 2.75, "cpu": 8, "gpu": "Adreno (TM) 730", "gpuv": "Qualcomm~Adreno (TM) 730"},
    "realme": {"mem": 8, "dpr": 2.75, "cpu": 8, "gpu": "Adreno (TM) 642L", "gpuv": "Qualcomm~Adreno (TM) 642L"},
    "nothing": {"mem": 8, "dpr": 2.75, "cpu": 8, "gpu": "Adreno (TM) 642L", "gpuv": "Qualcomm~Adreno (TM) 642L"},
}


def _replace_chrome_version_in_ua(ua: str, chrome_version: str) -> str:
    cv = str(chrome_version or "").strip()
    if not cv:
        return ua
    if re.search(r"Chrome/[\d.]+", ua or ""):
        return re.sub(r"Chrome/[\d.]+", f"Chrome/{cv}", ua or "")
    return ua


def _replace_moutaiapp_version_in_ua(ua: str, version: str) -> str:
    mv = str(version or "").strip()
    if not mv:
        return ua
    if re.search(r"moutaiapp/[\d.]+", ua or "", flags=re.I):
        return re.sub(r"moutaiapp/[\d.]+", f"moutaiapp/{mv}", ua or "", flags=re.I)
    return ua


_H5_UA_MODEL_BY_CODENAME = {
    "houji": "23127PN0CC",
    "shennong": "23116PN5BC",
    "fuxi": "2211133C",
    "ishtar": "2304FPN6DC",
    "corot": "23117RK66C",
    "dijia": "24129PN74C",
    "haotian": "2410DPN6CC",
    "xuanyuan": "25010PN30C",
    "chenfeng": "24053PY09C",
    "husky": "Pixel 8 Pro",
    "shiba": "Pixel 8",
    "cheetah": "Pixel 7 Pro",
    "caiman": "Pixel 9 Pro",
    "komodo": "Pixel 9 Pro XL",
    "tokay": "Pixel 9",
    "comet": "Pixel 9 Pro Fold",
}


def _normalize_h5_ua_model(model: str) -> str:
    s = str(model or "").strip()
    return _H5_UA_MODEL_BY_CODENAME.get(s.lower(), s)


def _normalize_h5_ua_model_in_user_agent(ua: str) -> str:
    s = str(ua or "").strip()
    if not s:
        return ""

    def _repl(m: re.Match) -> str:
        return f"{m.group(1)}{_normalize_h5_ua_model(m.group(2))}{m.group(3)}"

    return re.sub(r"(Android\s+[^;]+;\s*)([^;]+?)(\s+Build/)", _repl, s, count=1)


def _sec_ch_ua_for_wv_ua(wv_ua: str) -> str:
    m = re.search(r"Chrome/([\d.]+)", wv_ua or "")
    maj = (m.group(1).split(".")[0] if m else "120")
    return f'"Chromium";v="{maj}", "Android WebView";v="{maj}", "Not=A?Brand";v="99"'


# 默认超时：代理或高峰时 15s 易触发 curl 28；可按需改大（单请求仍可传 timeout= 覆盖）
_DEFAULT_HTTP_TIMEOUT = 30


def _post(url, **kw):
    kw.setdefault("impersonate", TLS_PROFILE)
    kw.setdefault("timeout", _DEFAULT_HTTP_TIMEOUT)
    if _use_node_http_backend():
        imp = kw.get("impersonate", TLS_PROFILE)
        kw = dict(kw)
        kw.pop("impersonate", None)
        return _go_bridge_http_dispatch("POST", url, tls_impersonate=imp, **kw)
    return http.post(url, **kw)


def _get(url, **kw):
    kw.setdefault("impersonate", TLS_PROFILE)
    kw.setdefault("timeout", _DEFAULT_HTTP_TIMEOUT)
    if _use_node_http_backend():
        imp = kw.get("impersonate", TLS_PROFILE)
        kw = dict(kw)
        kw.pop("impersonate", None)
        return _go_bridge_http_dispatch("GET", url, tls_impersonate=imp, **kw)
    return http.get(url, **kw)

def _sm4_ecb_enc(plain, key):
    """
    requestPay / payResultNotice 的 cipherText：SM4-ECB + Base64。
    与快手短句 demo(2).py::sm4_encrypt 一致：明文 UTF-8 字节直接 crypt_ecb，
    不显式 PKCS7（gmssl 对非 16 整数倍长度自行处理；若先 PKCS7 再加密会与现网验签不一致，易触发网关 250 参数异常）。
    """
    raw = plain.encode("utf-8") if isinstance(plain, str) else bytes(plain)
    try:
        from gmssl import sm4 as m
    except ImportError as e:
        raise RuntimeError(
            "缺少 gmssl：请执行 pip install gmssl（支付拉链 requestPay 依赖 SM4）"
        ) from e
    c = m.CryptSM4()
    c.set_key(key.encode("utf-8"), m.SM4_ENCRYPT)
    return base64.b64encode(c.crypt_ecb(raw)).decode("utf-8")


# ── mt1.9.5/mt/test-direct-order.js：requestPay 之后 SDK 串 → H5（mcgw 转链 + trade_no 兜底，与 JS 同源）──

_ALIPAY_3DES_KEY_STR = "23h4fhdilenbs741kogue1tl"

_ALIPAY_RSA_PUBLIC_PEM = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDENksAVqDoz5SMCZq0bsZwE+I3
NjrANyTTwUVSf1+ec1PfPB4tiocEpYJFCYju9MIbawR8ivECbUWjpffZq5QllJg+
19CB7V5rYGcEnb/M7CS3lFF2sNcRFJUtXUUAqyR3/l7PmpxTwObZ4DLG258dhE2v
FlVGXjnuLs+FI2hg4QIDAQAB
-----END PUBLIC KEY-----"""

_MT_MSP_USER_AGENT = (
    "Msp/9.1.5 (Android 12;Linux 4.4.146;zh_CN;http;540*960;21.0;WIFI;87699552;32617;1;"
    "000000000000000;000000000000000;8efce46e85;GOOGLE;H002;false;00:00:00:00:00:00;-1.0;-1.0;"
    "sdk-and-lite;65r7u2pfruicqrn;r2agza5c56pzmev;<unknown ssid>;02:00:00:00:00:00)"
)


def _des3_ecb_encrypt(plain: bytes, key: bytes) -> bytes:
    try:
        from Crypto.Cipher import DES3
        from Crypto.Util.Padding import pad

        return DES3.new(key, DES3.MODE_ECB).encrypt(pad(plain, 8))
    except ImportError:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.backends import default_backend

        pd = padding.PKCS7(64).padder()
        d = pd.update(plain) + pd.finalize()
        c = Cipher(algorithms.TripleDES(key), modes.ECB(), backend=default_backend())
        return c.encryptor().update(d) + c.encryptor().finalize()


def _des3_ecb_decrypt(ct: bytes, key: bytes) -> bytes:
    try:
        from Crypto.Cipher import DES3
        from Crypto.Util.Padding import unpad

        return unpad(DES3.new(key, DES3.MODE_ECB).decrypt(ct), 8)
    except ImportError:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.backends import default_backend

        c = Cipher(algorithms.TripleDES(key), modes.ECB(), backend=default_backend())
        dec = c.decryptor()
        d = dec.update(ct) + dec.finalize()
        up = padding.PKCS7(64).unpadder()
        return up.update(d) + up.finalize()


def _rsa_pkcs1_encrypt_public(plain: bytes, pem: bytes) -> bytes:
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    from cryptography.hazmat.backends import default_backend

    pub = load_pem_public_key(pem, backend=default_backend())
    return pub.encrypt(plain, asym_padding.PKCS1v15())


def _mt_build_msp_json_for_sdk(sdk_str: str) -> str:
    obj = {
        "tid": "qwertyuiopasdfghjklzxcvbnm",
        "user_agent": _MT_MSP_USER_AGENT,
        "has_alipay": False,
        "has_msp_app": False,
        "external_info": sdk_str,
        "app_key": "2021002145675770",
        "utdid": "z1x2c3v4v5v6v78v9",
        "new_client_key": "8efcf8b134",
        "action": {"type": "cashier", "method": "main"},
        "gzip": True,
    }
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _agent_debug_pay_resolve_ndjson(
    hypothesis_id: str, message: str, data: dict, *, location: str = "app.py:mt_resolve_pay_url_from_sdk"
) -> None:
    # #region agent log
    try:
        line = json.dumps(
            {
                "sessionId": "eb180b",
                "hypothesisId": hypothesis_id,
                "location": location,
                "message": message,
                "data": data,
                "timestamp": int(time.time() * 1000),
            },
            ensure_ascii=False,
        )
        with open(_ROOT / "debug-eb180b.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    # #endregion


def _pay_sdk_diag(s: str) -> dict:
    """仅诊断形态，不落库订单/签名等敏感全文。"""
    s = (s or "").strip()
    scheme = s.split(":", 1)[0] if ":" in s else ""
    return {
        "in_len": len(s),
        "scheme": scheme,
        "is_https": s.startswith("https://"),
        "is_alipays": s.startswith("alipays://"),
        "has_biz_content_q": "biz_content=" in s,
    }


def mt_alipay_mcgw_sdk_to_h5_url(
    sdk_str: str,
    *,
    proxy_kw: dict | None = None,
    impersonate: str | None = None,
) -> str:
    """
    与 mt1.9.5/mt/test-direct-order.js 内 async function convertToH5：POST http://mcgw.alipay.com/gateway.do，
    解密 res_data 后取 jsonData.form.onload.name 中 https://…（与 JS 同结构、同密钥与网关体）。
    mt 脚本对 mcgw 为直连；若调用方传入代理导致空响应/无 res_data，则自动再试一次直连（与 mt 行为对齐）。
    """
    sdk_str = (sdk_str or "").strip()
    if not sdk_str:
        return ""
    key = _ALIPAY_3DES_KEY_STR.encode("utf-8")
    try:
        json_request = _mt_build_msp_json_for_sdk(sdk_str)
        encrypted_data = base64.b64encode(_des3_ecb_encrypt(json_request.encode("utf-8"), key)).decode("ascii")
        rsa_blob = _rsa_pkcs1_encrypt_public(key, _ALIPAY_RSA_PUBLIC_PEM)
        parameter1 = base64.b64encode(rsa_blob).decode("ascii")
        parameter2 = format(len(parameter1), "X").zfill(8)
        parameter3 = format(len(encrypted_data), "X").zfill(8)
        req_data = parameter2 + parameter1 + parameter3 + encrypted_data
        gateway_payload = json.dumps(
            {
                "data": {
                    "device": "GOOGLE-H002",
                    "namespace": "com.alipay.mobilecashier",
                    "api_name": "com.alipay.mcpay",
                    "api_version": "4.0.2",
                    "params": {"req_data": req_data},
                }
            },
            separators=(",", ":"),
        )
        hdrs = {
            "Accept-Charset": "UTF-8",
            "Connection": "Keep-Alive",
            "Content-Type": "application/octet-stream;binary/octet-stream",
            "Cookie": "zone=RZ43A",
            "Cookie2": "$Version=1",
            "Host": "mcgw.alipay.com",
            "Keep-Alive": "timeout=180, max=100",
            "User-Agent": "msp",
        }
        imp = impersonate or TLS_PROFILE
        post_kw_seq: list[tuple[str, dict | None]] = (
            [("proxied", proxy_kw), ("direct", None)]
            if proxy_kw is not None
            else [("direct", None)]
        )
        for tag, pkw in post_kw_seq:
            try:
                if pkw:
                    r = http.post(
                        "http://mcgw.alipay.com/gateway.do",
                        data=gateway_payload.encode("utf-8"),
                        headers=hdrs,
                        timeout=25,
                        impersonate=imp,
                        **pkw,
                    )
                else:
                    r = http.post(
                        "http://mcgw.alipay.com/gateway.do",
                        data=gateway_payload.encode("utf-8"),
                        headers=hdrs,
                        timeout=25,
                        impersonate=imp,
                    )
                raw = (r.text or "").strip()
                st = getattr(r, "status_code", None)
            except Exception as ex_post:
                _agent_debug_pay_resolve_ndjson(
                    "M1",
                    "mcgw_http_exc",
                    {"attempt": tag, "exc_type": type(ex_post).__name__},
                    location="app.py:mt_alipay_mcgw_sdk_to_h5_url",
                )
                continue
            _agent_debug_pay_resolve_ndjson(
                "M1",
                "mcgw_post",
                {
                    "attempt": tag,
                    "http_status": st,
                    "raw_len": len(raw),
                    "looks_json": bool(raw) and raw.lstrip().startswith("{"),
                },
                location="app.py:mt_alipay_mcgw_sdk_to_h5_url",
            )
            if not raw:
                continue
            try:
                response = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[支付宝转链] 网关非 JSON: {raw[:200]}...")
                _agent_debug_pay_resolve_ndjson(
                    "M2",
                    "mcgw_bad_json",
                    {"attempt": tag, "raw_len": len(raw)},
                    location="app.py:mt_alipay_mcgw_sdk_to_h5_url",
                )
                continue
            res_b64 = ((response.get("data") or {}).get("params") or {}).get("res_data")
            if not res_b64:
                _agent_debug_pay_resolve_ndjson(
                    "M2",
                    "mcgw_no_res_data",
                    {"attempt": tag, "top_keys": list(response.keys())[:8]},
                    location="app.py:mt_alipay_mcgw_sdk_to_h5_url",
                )
                continue
            try:
                decrypted = _des3_ecb_decrypt(base64.b64decode(res_b64), key).decode("utf-8", errors="replace")
                jd = json.loads(decrypted)
            except Exception as ex_dec:
                _agent_debug_pay_resolve_ndjson(
                    "M3",
                    "mcgw_decrypt_json_fail",
                    {"attempt": tag, "exc_type": type(ex_dec).__name__},
                    location="app.py:mt_alipay_mcgw_sdk_to_h5_url",
                )
                continue
            form = jd.get("form") or {}
            onload = form.get("onload") or {}
            onload_name = onload.get("name") if isinstance(onload, dict) else ""
            if not onload_name and isinstance(form.get("onload"), str):
                onload_name = form["onload"]
            m = re.search(r"https://[^\s']+", str(onload_name or ""))
            if m:
                _agent_debug_pay_resolve_ndjson(
                    "M4",
                    "mcgw_h5_ok",
                    {"attempt": tag},
                    location="app.py:mt_alipay_mcgw_sdk_to_h5_url",
                )
                return m.group(0).strip()
            _agent_debug_pay_resolve_ndjson(
                "M3",
                "mcgw_no_https_in_onload",
                {"attempt": tag, "has_form": bool(form)},
                location="app.py:mt_alipay_mcgw_sdk_to_h5_url",
            )
    except Exception as ex:
        print(f"[支付宝转链] {ex}")
        _agent_debug_pay_resolve_ndjson(
            "M0",
            "mcgw_outer_exc",
            {"exc_type": type(ex).__name__},
            location="app.py:mt_alipay_mcgw_sdk_to_h5_url",
        )
    return ""


def _biz_content_from_alipays_startapp(s: str) -> str:
    """alipays://platformapi/startapp?...&biz_content= → URL 解码后的串（常为 k=v 或 JSON），供转 H5。"""
    s = (s or "").strip()
    if not s.startswith("alipays://"):
        return ""
    try:
        q = urlparse(s).query
        if not q:
            return ""
        d = parse_qs(q, keep_blank_values=True)
        bc = (d.get("biz_content") or [""])[0]
        if not bc:
            return ""
        return unquote(bc).strip()
    except Exception:
        return ""


def mt_alipay_trade_no_h5_from_sdk(sdk_str: str) -> str:
    """
    与 mt1.9.5/mt/test-direct-order.js 主流程兜底一致（约 1060–1064 行）：
    sdkStr.match(/trade_no=([^&"]+)/) →
    https://mclient.alipay.com/cashier/mobilepay.htm?alipay_trade_no=...
    """
    raw = sdk_str or ""
    m = re.search(r"trade_no=([^&\"]+)", raw)
    if m:
        return f"https://mclient.alipay.com/cashier/mobilepay.htm?alipay_trade_no={m.group(1).strip()}"
    try:
        dec = unquote(raw)
    except Exception:
        dec = raw
    if dec != raw:
        m = re.search(r"trade_no=([^&\"]+)", dec)
        if m:
            return f"https://mclient.alipay.com/cashier/mobilepay.htm?alipay_trade_no={m.group(1).strip()}"
    return ""


def mt_resolve_pay_url_from_sdk(
    sdk_str: str,
    *,
    proxy_kw: dict | None = None,
    impersonate: str | None = None,
) -> str:
    """
    与 mt1.9.5/mt/test-direct-order.js 一致（无分支、无环境变量）：
    Step5 async function convertToH5（约 847–957 行）→ 主流程 1053–1066 行。
    顺序：mcgw 转链成功则返回；否则 trade_no→mclient；再无则 alipays://platformapi/startapp?{sdk}。
    """
    s = (sdk_str or "").strip()
    if not s:
        return ""
    _agent_debug_pay_resolve_ndjson(
        "H1",
        "resolve_enter",
        {**_pay_sdk_diag(s), "proxy_kw_keys": sorted((proxy_kw or {}).keys()) if isinstance(proxy_kw, dict) else []},
    )
    if s.startswith("https://"):
        _agent_debug_pay_resolve_ndjson("H1", "resolve_exit_https_in", {"out_scheme": "https"})
        return s
    # 网关有时直接给 App 调起链：须从 biz_content 再跑 mcgw / trade_no，否则前端只能看到 alipays:// 而非 H5。
    if s.startswith("alipays://"):
        inner = _biz_content_from_alipays_startapp(s)
        _agent_debug_pay_resolve_ndjson(
            "H2",
            "alipays_branch",
            {
                "inner_len": len(inner or ""),
                "inner_has_app_pay": "alipay.trade.app.pay" in (inner or ""),
                "inner_has_trade_no": "trade_no=" in (inner or ""),
            },
        )
        for cand in ([inner] if inner else []) + [s]:
            c = (cand or "").strip()
            if not c:
                continue
            h5 = mt_alipay_mcgw_sdk_to_h5_url(c, proxy_kw=proxy_kw, impersonate=impersonate)
            if h5:
                print(f"[支付宝转链] H5(自 alipays 展开): {h5}")
                _agent_debug_pay_resolve_ndjson(
                    "H3",
                    "resolve_exit_mcgw_from_alipays",
                    {"out_scheme": "https", "mcgw_cand": "inner" if c == inner else "full"},
                )
                return h5
            fb = mt_alipay_trade_no_h5_from_sdk(c)
            if fb:
                print(f"[支付宝转链] trade_no 兜底(自 alipays 展开): {fb}")
                _agent_debug_pay_resolve_ndjson(
                    "H4",
                    "resolve_exit_trade_no_from_alipays",
                    {"out_scheme": "https", "trade_no_cand": "inner" if c == inner else "full"},
                )
                return fb
        _agent_debug_pay_resolve_ndjson("H5", "resolve_exit_alipays_raw", {"out_scheme": "alipays"})
        return s

    h5 = mt_alipay_mcgw_sdk_to_h5_url(s, proxy_kw=proxy_kw, impersonate=impersonate)
    if h5:
        print(f"[支付宝转链] H5 支付链接: {h5}")
        _agent_debug_pay_resolve_ndjson("H3", "resolve_exit_mcgw", {"out_scheme": "https"})
        return h5
    fb = mt_alipay_trade_no_h5_from_sdk(s)
    if fb:
        print(f"[支付宝转链] 转链失败，使用 trade_no 兜底: {fb}")
        _agent_debug_pay_resolve_ndjson("H4", "resolve_exit_trade_no", {"out_scheme": "https"})
        return fb
    wrapped = f"alipays://platformapi/startapp?{s}"
    _agent_debug_pay_resolve_ndjson("H5", "resolve_exit_wrap_startapp", {"out_scheme": "alipays"})
    return wrapped


def _api_ok(code) -> bool:
    return code == 2000 or str(code) == "2000"


def _mask_mobile(m: str) -> str:
    m = (m or "").strip()
    if len(m) < 7:
        return m
    return m[:3] + "****" + m[-4:]


def _json_min(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


# purchaseInfoV2 里可能出现的活动 ID 字段（不同品类/版本优先级不同）
_PRIORITY_ACT_ID_KEYS = (
    "itemPriorityActId",
    "priorityActId",
    "priorityPurchaseActId",
    "itemActId",
)


def _deep_scan_priority_act_id(obj) -> str | None:
    """在任意子树中查找优先购活动 ID（多字段名 + 嵌套）。"""
    if isinstance(obj, dict):
        for key in _PRIORITY_ACT_ID_KEYS:
            v = obj.get(key)
            if v is not None and str(v).strip() != "":
                return str(v).strip()
        for x in obj.values():
            r = _deep_scan_priority_act_id(x)
            if r:
                return r
    elif isinstance(obj, list):
        for x in obj:
            r = _deep_scan_priority_act_id(x)
            if r:
                return r
    return None


def _act_id_from_purchase_map_value(v: object) -> str | None:
    """从 purchaseInfoMap 的单条 value 里取活动 ID。"""
    if not isinstance(v, dict):
        return None
    for key in _PRIORITY_ACT_ID_KEYS:
        x = v.get(key)
        if x is not None and str(x).strip() != "":
            return str(x).strip()
    pi = v.get("purchaseInfo") or {}
    if isinstance(pi, dict):
        for key in _PRIORITY_ACT_ID_KEYS:
            x = pi.get(key)
            if x is not None and str(x).strip() != "":
                return str(x).strip()
    return _deep_scan_priority_act_id(v)


def priority_act_from_purchase_info(res: dict) -> str | None:
    """从 purchaseInfoV2 响应中解析优先购活动 ID（code=2000 仍可能无活动或结构异于常规）。"""
    if not isinstance(res, dict) or not _api_ok(res.get("code")):
        return None
    data = res.get("data")
    if isinstance(data, dict):
        pmap = data.get("purchaseInfoMap") or {}
        if isinstance(pmap, dict):
            for _k, v in pmap.items():
                aid = _act_id_from_purchase_map_value(v)
                if aid:
                    return aid
        for key in _PRIORITY_ACT_ID_KEYS:
            x = data.get(key)
            if x is not None and str(x).strip() != "":
                return str(x).strip()
        r = _deep_scan_priority_act_id(data)
        if r:
            return r
    elif isinstance(data, list):
        r = _deep_scan_priority_act_id(data)
        if r:
            return r
    return _deep_scan_priority_act_id(res)


def _black_probe_benign_activity_msg(code: object, message: str) -> bool:
    """
    活动未开售 / 非抢购窗口：说明账号与 Token 已抵达抢购接口，不应记为 probe_error 或黑号。
    （茅台常返回 HTTP 480 + code 4030「购买活动信息异常」，或文案含「当前活动未开启」。）
    """
    msg = str(message or "").strip()
    if "当前活动未开启" in msg or "活动未开启" in msg:
        return True
    try:
        c = int(code)
    except (TypeError, ValueError):
        c = None
    if c == 4030 and "购买活动信息异常" in msg:
        return True
    return False


_RUSH_STOP_MSG_RE = re.compile(
    r"限购|限購|购买已达上限|已达购买上限|不可再购|超出每人|每人仅限|超过限购|已达购买|重复购买|每人每天|请勿重复"
)


def _rush_row_stop_rush(row: object) -> bool:
    """
    与 node_bridge rushTraceRowStopRush 口径一致：出现限购/每人每天/4030（排除纯「购买活动信息异常」）则不再打后续枪。
    """
    if not isinstance(row, dict):
        return False
    msg = str(row.get("message") or "")
    if _RUSH_STOP_MSG_RE.search(msg):
        return True
    c = row.get("code")
    cs = str(c).strip() if c is not None and c != "" else ""
    if cs == "4030":
        return bool(_RUSH_STOP_MSG_RE.search(msg))
    return False


def _http_response_has_json_business_code(resp: object) -> bool:
    """
    网关/限流层可能标 HTTP 429，但响应体仍是 i 茅台业务 JSON（含 code）。
    此时应把 Response 交给上层解析，避免 _req_post/_req_get 误判为纯限流而扫遍代理分片
    （黑号检测、组单 compose 等均依赖带 code 的 JSON，与 HTTP 状态码解耦）。
    """
    if resp is None:
        return False
    try:
        j = resp.json()
    except Exception:
        return False
    return isinstance(j, dict) and "code" in j


def _http429_is_mt_rate_limit_json(resp: object) -> bool:
    """
    收货地址等接口常见 HTTP 429 + body 为 {\"code\":429,...}（频控）。
    此类应允许在代理池内换出口重试，而非当作「已定性的业务响应」立即返回。
    """
    if resp is None:
        return False
    try:
        j = resp.json()
    except Exception:
        return False
    if not isinstance(j, dict):
        return False
    c = j.get("code")
    if c == 429:
        return True
    try:
        return int(c) == 429
    except (TypeError, ValueError):
        return str(c).strip() == "429"


def _http_response_should_proxy_retry(resp: object) -> bool:
    """本机直连被限流/网关挡住时，允许临时切代理池补打一轮。"""
    if resp is None:
        return False
    try:
        status = int(getattr(resp, "status_code", 0) or 0)
    except Exception:
        status = 0
    if status == 429:
        return True
    try:
        j = resp.json()
    except Exception:
        j = None
    if isinstance(j, dict):
        c = j.get("code")
        try:
            if int(c) == 429:
                return True
        except (TypeError, ValueError):
            if str(c).strip() == "429":
                return True
        msg = str(j.get("message") or "").lower()
        if "限流" in msg or "频控" in msg or "too many" in msg:
            return True
    text = str(getattr(resp, "text", "") or "").lower()
    return "429" in text or "限流" in text or "频控" in text or "too many" in text


def _proxy_kw_dict_from_url(proxy_url: str) -> dict:
    """curl_cffi 用代理参数；空串表示显式不走代理（关环境变量 HTTPS_PROXY）。"""
    p = (proxy_url or "").strip()
    if not p:
        return {"proxies": {"http": None, "https": None}}
    try:
        pr = urlparse(p)
        scheme = (pr.scheme or "").lower()
        if scheme in ("socks5", "socks5h"):
            return {"proxies": {"http": p, "https": p}}
    except Exception:
        pass
    try:
        pr = urlparse(p)
        user = unquote(pr.username) if pr.username else ""
        pwd = unquote(pr.password) if pr.password else ""
        netloc = _proxy_netloc_no_auth(p)
        if user and netloc:
            base = f"http://{netloc}"
            return {
                "proxies": {"http": base, "https": base},
                "proxy_auth": (user, pwd),
            }
    except Exception:
        pass
    return {"proxies": {"http": p, "https": p}}


class Session:
    """业务会话管理器"""

    RUSH_ENDPOINTS = [
        "/xhr/front/trade/priority/rushPurchase/hot/branch/one",
        "/xhr/front/trade/priority/rushPurchase/hot/branch/two",
        "/xhr/front/trade/priority/rushPurchase/hot/branch/three",
        "/xhr/front/trade/priority/rushPurchase/hot/branch/four",
        "/xhr/front/trade/priority/rushPurchase",
    ]

    def __init__(self, aid="", sdk=None, mfr="", mdl="", scr="",
                 btime="", bsid="", wv="", raw_id=None, proxy_url=None,
                 h5_did=None, h5_sid=None, proxy_shard=None, proxy_account_index: int = 0,
                 proxy_account_total: int = 1, full_pool_rush=False,
                 proxy_rotate_per_request: bool = False, h5_user_agent="",
                 h5_device_id="", haotian_device_id="", h5_android_version="",
                 h5_device_model="", h5_build_id="", h5_chrome_version="",
                 gdxidpyhxdE="", yidun_gdx_fp="", gdx_icp="", bb_state=None,
                 use_wasm_bb: bool = False, use_mobile_relay: bool = False,
                 mobile_relay_device: str = ""):
        # raw_id: 从 accounts.json 恢复的已持久化设备指纹（与登录时 MT-Device-ID 对应），不可再经 make_raw_id 二次哈希
        if raw_id:
            self._raw_id = raw_id.strip()
        else:
            if not aid:
                aid = uuid.uuid4().hex[:16]
            self._raw_id = make_raw_id(aid=aid)
        # WebView UA 仍用池映射（与 App 行独立）；Chrome/Build 随 raw_id 稳定
        di = int(hashlib.md5(self._raw_id.encode("utf-8")).hexdigest(), 16) % len(_UA_POOL)
        av, dm, db, dc = _UA_POOL[di]
        # App 行：与 mt1.9.6 deviceHeaders.user-agent 同源
        # android;sdk_version;manufacturer;device（浩天成功后 accounts 写入 sdk/mfr/mdl）
        # 未配置时同 test-full-flow 简化版：android;34;samsung;SM-S9280
        _m_in = (mfr or "").strip()
        _d_in = _normalize_h5_ua_model(mdl or "")
        if _m_in and _d_in:
            _s = (
                str(sdk).strip()
                if sdk is not None and str(sdk).strip() != ""
                else "34"
            )
            _m, _d = _m_in, _d_in
        else:
            _s, _m, _d = "34", "samsung", "SM-S9280"
        _res_opts = ["1080*2340", "1080*2400", "1080*2176", "1440*3200"]
        _r = scr or _res_opts[int(hashlib.md5((self._raw_id + "#rs").encode("utf-8")).hexdigest(), 16) % len(_res_opts)]

        self._dev_token = make_device_token(self._raw_id)
        # 恒为 TLS_PROFILE；保留字段供外部读取，与全局 chrome_138 一致。
        self._tls_imp = TLS_PROFILE
        self._rush_ab_profile_key = RUSH_DEFAULT_AB_PROFILE
        self._rush_ab_profile_label = RUSH_AB_PROFILES[RUSH_DEFAULT_AB_PROFILE]["label"]
        self._env = make_env_tag_for_raw_id(self._raw_id)
        self._cert = make_cert_tag_for_raw_id(self._raw_id)
        # 注意：第一段为 API level（sdk_version），与 MT 一致，不要用 android_version 代替
        self._ua = f"android;{_s};{_m};{_d}"
        self._wv_ua = (
            f"Mozilla/5.0 (Linux; Android {_s}; {_d} Build/{db}; wv) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
            f"Chrome/{dc} Mobile Safari/537.36 "
            f"moutaiapp/{RELEASE_VER} device-id/{self._raw_id}"
        )
        if bsid:
            self._wv_ua += f" BS-DVID/{bsid}"
        self._h5_user_agent = str(h5_user_agent or "").strip()
        self._h5_device_id = str(h5_device_id or "").strip()
        self._haotian_device_id = str(haotian_device_id or "").strip()
        self._h5_android_version = str(h5_android_version or "").strip()
        self._h5_device_model = str(h5_device_model or "").strip()
        self._h5_build_id = str(h5_build_id or "").strip()
        self._h5_chrome_version = str(h5_chrome_version or "").strip()
        self._gdxidpyhxdE = _extract_gdx_cookie_value(gdxidpyhxdE)
        self._yidun_gdx_fp = str(yidun_gdx_fp or "").strip()
        self._gdx_icp = str(gdx_icp or "").strip()
        self._gdx_lock = threading.RLock()
        self._gdx_persist_cb = None
        if self._h5_user_agent:
            fixed_chrome = str(RUSH_AB_PROFILES[RUSH_DEFAULT_AB_PROFILE].get("ua_chrome") or "").strip()
            self._wv_ua = _replace_chrome_version_in_ua(
                _normalize_h5_ua_model_in_user_agent(self._h5_user_agent),
                fixed_chrome or self._h5_chrome_version or H5_FIXED_CHROME_VERSION,
            )
            self._wv_ua = _replace_moutaiapp_version_in_ua(self._wv_ua, RELEASE_VER)
            if bsid and re.search(r"BS-DVID/[^\s;]+", self._wv_ua):
                self._wv_ua = re.sub(r"BS-DVID/[^\s;]+", f"BS-DVID/{bsid}", self._wv_ua)
            elif bsid and "BS-DVID/" not in self._wv_ua:
                self._wv_ua = f"{self._wv_ua} BS-DVID/{bsid}"
        self._sec_ch_ua = _sec_ch_ua_for_wv_ua(self._wv_ua)
        self._scr = _r
        self._bsid = bsid
        self._token = ""
        self._cookie = ""
        self._uid = ""
        self._verify_status = None
        self._dtime = btime or format_mt_dtime_gmt08()
        _pin_h5 = (str(h5_did or "").strip(), str(h5_sid or "").strip())
        if _pin_h5[0] and _pin_h5[1]:
            self._h5id, self._h5sid = _pin_h5[0], _pin_h5[1]
        else:
            # H5 指纹语义对齐 App 三元组（sdk/mfr/mdl）：同账号会话内 App/H5 尽量同源。
            fp = _H5_FP_PROFILE_BY_MFR.get(str(_m).strip().lower(), _H5_FP_PROFILE_BY_MFR["samsung"])
            self._h5id = make_h5_devid(
                ua=self._wv_ua,
                mem=fp["mem"],
                dpr=fp["dpr"],
                cpu=fp["cpu"],
                gpu=fp["gpu"],
                gpuv=fp["gpuv"],
            )
            self._h5sid = make_h5_session()
        self._bs_ck = make_bs_cookie(self._h5id)
        self._wv = (wv or WASM_TAG)
        # 旧 BB SDK 状态字段仅保留账号 JSON 兼容；抢购发包不再读取外部状态。
        self._bb_sign_wasm_b64: str = ""
        self._bb_sign_wasm_version: str = ""
        self._bb_sign_wasm_ok: bool = False
        self._bb_state_expired_at: int = 0
        self._bb_state: dict = dict(bb_state or {}) if isinstance(bb_state, dict) else {}
        self._bb_warmup_lock = threading.RLock()
        self._bb_warmup_persist_cb = None
        # 旧暖场状态字段仅保留 UI/账号 JSON 兼容；抢购 BB 不依赖它。
        self._bb_warmup_at: int = 0
        self._bb_serial: int = 1
        self._bb_device_info: dict = {}
        self._bb_tactics_version: int = 0
        self._bb_time_delta: int = 0
        # 抢购 BB 默认使用 shield.py 的稳定 SM4 链路；账号开关仅切到 shield.py 的 wasm.en 外层。
        self._use_wasm_bb = bool(use_wasm_bb)
        self._use_mobile_relay = bool(use_mobile_relay)
        self._mobile_relay_device = str(mobile_relay_device or "").strip()
        self._use_proxy_pool = False
        # 抢购中号后由 AccountEntry 注入：仅 compose 使用，与当次出口一致（不换代理池）
        self._compose_pin_proxy_url = ""
        # 抢购成功落盘的出口（与 compose 钉线独立）；用于 /order/pay、requestPay，避免关 MT_COMPOSE_USE_RUSH_PIN 后支付仍走错线。
        self._rush_success_proxy_url_norm = ""
        self._proxy_prewarm_ok_for = ""
        self._proxy_per_account_chain = False
        self._proxy_rotation_chain: list[str] = []
        self._proxy_rotation_idx = 0
        # 账号填「本机/直连」等：强制不走代理池；_rush_guard_auth_proxy_for_single_hop 须放行
        self._account_explicit_direct = False
        explicit = (proxy_url or "").strip()
        if _is_direct_proxy_marker(explicit):
            self._account_explicit_direct = True
            self._proxy_url = ""
        elif explicit:
            self._proxy_url = _normalize_proxy_url(explicit)
        else:
            try:
                from proxy_pool import (
                    active_urls,
                    pool_is_configured,
                )

                # 空代理账号只标记走整体代理池；rush 热路径由账号内 lane 从全局池借线。
                if pool_is_configured() and active_urls():
                    self._proxy_url = ""
                    self._use_proxy_pool = True
                else:
                    pu = _proxy_from_environ()
                    self._proxy_url = _normalize_proxy_url(pu) if pu else ""
            except Exception:
                pu = _proxy_from_environ()
                self._proxy_url = _normalize_proxy_url(pu) if pu else ""
        # 优先购 App 组单 compose/v2 成功后缓存 data，submit/v2 可省略 composeData
        self._last_rush_compose_data = None
        # 轮换链落到本机直连（空 _proxy_url）时置 True，避免 _ensure_pool_proxy_bound 立刻用全局下标盖回代理
        self._proxy_direct_hop = False
        # 仅分片模式：每次 rush() 在本账号独占的池 IP 上顺序轮询（与 _rush_shard_cycle_i 同步）
        self._rush_shard_cycle_i = None
        # accounts.full_pool_rush：rush_loop 按 active_urls 顺序每档一条代理、每线只打一枪（与分片独占不同）
        self._rush_full_pool_sequence = bool(full_pool_rush)
        # 抢购串行：第 k 枪用 ordered[(start+k)%N]，跨 rush()/多档连续；与 _rush_proxy_url_sequence(advance=True) 同步
        self._rush_serial_idx = 0
        # 账号级：与 MT_PROXY_ROTATE_PER_REQUEST 环境变量合并（每次请求结束后再判一次 env，便于热开关）
        self._proxy_rotate_per_request = bool(proxy_rotate_per_request)
        # None=未记录；str=取 TN 时所用出口（空串表示当时为直连）。requestPay 在轮换开启时须钉回该出口。
        self._order_pay_egress_for_gateway = None
        self._force_order_chain_direct = False

    def set_rush_ab_profile(self, profile: str | None) -> dict:
        cfg = rush_ab_profile_config(profile)
        self._rush_ab_profile_key = cfg["key"]
        self._rush_ab_profile_label = cfg["label"]
        return cfg

    def _rush_ab_profile(self) -> dict:
        return rush_ab_profile_config(getattr(self, "_rush_ab_profile_key", RUSH_DEFAULT_AB_PROFILE))

    def _rush_tls_profile(self) -> str:
        return str(self._rush_ab_profile().get("tls") or TLS_PROFILE)

    def _proxy_round_robin_enabled(self) -> bool:
        v = (os.environ.get("MT_PROXY_ROTATE_PER_REQUEST") or "").strip().lower()
        env_on = v in ("1", "true", "yes", "on")
        return bool(getattr(self, "_proxy_rotate_per_request", False)) or env_on

    def force_order_chain_direct(self) -> None:
        """组单/下单/支付链路固定本机直连；不改抢购链路的代理状态。"""
        self._force_order_chain_direct = True
        self._order_pay_egress_for_gateway = None
        self._pay_prefer_pool_rotate = False

    def _apply_rush_success_proxy_for_payment(self) -> None:
        """
        抢购中号时写入账号的 rush_success_proxy_url：/order/pay 与 requestPay 应优先走该出口。
        与 MT_COMPOSE_USE_RUSH_PIN 无关（花椒等入口常关 compose 钉线，否则支付易与抢线不一致 → 网关 250）。
        关闭：MT_PAY_ORDER_USE_RUSH_PROXY=0；账号显式直连时不覆盖。
        """
        if getattr(self, "_force_order_chain_direct", False):
            return
        if getattr(self, "_pay_prefer_pool_rotate", False):
            return
        if (os.environ.get("MT_PAY_ORDER_USE_RUSH_PROXY", "1") or "1").strip().lower() in (
            "0",
            "false",
            "no",
            "off",
        ):
            return
        if getattr(self, "_account_explicit_direct", False):
            return
        u = (getattr(self, "_rush_success_proxy_url_norm", "") or "").strip()
        if u:
            self._proxy_url = u

    def _advance_proxy_after_request_round(self, use_proxy: bool) -> None:
        """
        每完成一次对外的 _req_post / _req_get 调用（含内部多轮重试）后，将出口拨到下一跳，实现按「发送轮次」轮询多 IP。
        与是否 200/429 无关；抢购中号后若锁定组单出口（_compose_pin_proxy_url）则跳过，以免破坏同线 compose。
        """
        if not self._proxy_round_robin_enabled():
            return
        if not use_proxy:
            return
        if getattr(self, "_account_explicit_direct", False):
            return
        # 不在此用 _compose_pin_proxy_url 挡轮换：否则中号后刷新地址/其它 GET 永远钉在同一出口，易 429。
        # 组单 compose 多轮内在循环头会重新套用抢购出口线。
        try:
            self._set_proxy_from_rotation(self._rotate_next_proxy())
        except Exception:
            pass

    def _tls_bump_after_429(self) -> None:
        """全局固定 TLS_PROFILE，收到 429 不再轮换 impersonate。"""
        return

    @staticmethod
    def _rush_trace_row_http_status(row: dict) -> int | None:
        hs = row.get("httpStatus")
        if hs is None:
            return None
        try:
            return int(hs)
        except (TypeError, ValueError):
            return None

    def _tls_bump_from_rush_trace(self, tr: object) -> None:
        """全局固定 TLS_PROFILE；保留调用点兼容，不再因 rush trace 轮换指纹。"""
        if not isinstance(tr, list):
            return
        for row in tr:
            if not isinstance(row, dict):
                continue
            if self._rush_trace_row_http_status(row) == 429:
                self._tls_bump_after_429()

    def _cycle_shard_proxy_for_rush(self) -> None:
        """
        多账号分片：首轮抢购保持 Session 按 raw_id 选中的起始线；之后每一轮 rush() 从「上一轮最后一枪」的下一跳开始，
        以便 Node /rush_loop 多档里 IP1→IP2→… 循环与跨轮连续（与 _rush_proxy_url_sequence 一致）。
        """
        if not getattr(self, "_proxy_per_account_chain", False):
            return
        ch = getattr(self, "_proxy_rotation_chain", None)
        if not ch:
            return
        paid_idxs = [i for i, u in enumerate(ch) if (u or "").strip()]
        if not paid_idxs:
            return
        if getattr(self, "_rush_shard_cycle_i", None) is None:
            try:
                pos = paid_idxs.index(int(self._proxy_rotation_idx))
            except ValueError:
                h = int(hashlib.md5(self._raw_id.encode("utf-8")).hexdigest(), 16)
                pos = h % len(paid_idxs)
            self._rush_shard_cycle_i = pos
            self._proxy_rotation_idx = paid_idxs[pos]
            self._proxy_url = ch[self._proxy_rotation_idx]
        else:
            self._set_proxy_from_rotation(self._rotate_next_proxy())
            try:
                self._rush_shard_cycle_i = paid_idxs.index(int(self._proxy_rotation_idx))
            except ValueError:
                self._rush_shard_cycle_i = 0
                self._proxy_rotation_idx = paid_idxs[self._rush_shard_cycle_i]
                self._proxy_url = ch[self._proxy_rotation_idx]
        self._proxy_direct_hop = self._proxy_url == ""

    def _sync_shard_state_from_proxy_url(self, proxy_url: str | None) -> None:
        """抢购多枪结束后：用最后一枪出口写回 Session；分片模式下对齐 _proxy_rotation_idx / _rush_shard_cycle_i（便于下一轮从下一跳继续）。"""
        u = (proxy_url or "").strip()
        self._set_proxy_from_rotation(u)
        if not getattr(self, "_proxy_per_account_chain", False):
            return
        ch = getattr(self, "_proxy_rotation_chain", None) or []
        paid_idxs = [i for i, u in enumerate(ch) if (u or "").strip()]
        if not paid_idxs:
            return
        for ci in paid_idxs:
            if (ch[ci] or "").strip() == u:
                self._proxy_rotation_idx = ci
                self._proxy_url = ch[ci]
                try:
                    self._rush_shard_cycle_i = paid_idxs.index(ci)
                except ValueError:
                    pass
                self._proxy_direct_hop = u == ""
                return

    def _rush_proxy_slot_for_url(self, proxy_url: str | None) -> tuple[int | None, str]:
        """
        分片：按链上「非空代理」顺序为 IP1、IP2…；全池/池模式：按 active_urls 下标+1；
        其它轮换链：按链上非空项顺序。无法归类时 slot 为 None，仍返回 host 摘要。
        """
        u = (proxy_url or "").strip()
        host = _proxy_hostport_for_log(u) if u else "本机直连"
        if not u:
            return None, host
        ch = getattr(self, "_proxy_rotation_chain", None) or []
        if getattr(self, "_proxy_per_account_chain", False) and ch:
            paid = [i for i, x in enumerate(ch) if (x or "").strip()]
            for si, ci in enumerate(paid, start=1):
                if (ch[ci] or "").strip() == u:
                    return si, host
        if getattr(self, "_rush_full_pool_sequence", False) or getattr(
            self, "_use_proxy_pool", False
        ):
            try:
                from proxy_pool import active_urls

                au = active_urls()
                if au:
                    for idx, x in enumerate(au):
                        if (x or "").strip() == u:
                            return idx + 1, host
            except Exception:
                pass
        try:
            from proxy_pool import rotation_chain

            chain = rotation_chain()
            nonempty = [c for c in chain if (c or "").strip()]
            for si, c in enumerate(nonempty, start=1):
                if (c or "").strip() == u:
                    return si, host
        except Exception:
            pass
        return None, host

    def _rush_attach_proxy_meta(
        self, last: object, attempts: list[dict] | None
    ) -> None:
        """为抢购返回体及 rushLoopTrace 各行附加 rushProxySlot / rushProxyHost，供 WebUI 卡片日志展示 IP几。"""
        if not isinstance(last, dict):
            return
        tr = last.get("rushLoopTrace")
        att = attempts if isinstance(attempts, list) else []
        if isinstance(tr, list) and att:
            for i, row in enumerate(tr):
                if not isinstance(row, dict):
                    continue
                pu = ""
                if i < len(att):
                    pu = str(att[i].get("proxyUrl") or "").strip()
                if not pu:
                    traced = str(row.get("proxy") or "").strip()
                    if traced and traced != "direct":
                        row["rushProxyHost"] = traced
                        continue
                slot, ph = self._rush_proxy_slot_for_url(pu)
                row["rushProxyHost"] = ph
                if slot is not None:
                    row["rushProxySlot"] = slot
        ref_u = ""
        if att:
            ref_u = str(att[-1].get("proxyUrl") or "").strip()
        if not ref_u:
            ref_u = (getattr(self, "_proxy_url", "") or "").strip()
        ts, th = self._rush_proxy_slot_for_url(ref_u)
        last["rushProxyHost"] = th
        if ts is not None:
            last["rushProxySlot"] = ts

    def _proxy_rotation_len(self) -> int:
        if getattr(self, "_proxy_per_account_chain", False) and self._proxy_rotation_chain:
            return len(self._proxy_rotation_chain)
        if getattr(self, "_use_proxy_pool", False):
            try:
                from proxy_pool import active_urls

                n = len(active_urls())
                if n:
                    return n
            except Exception:
                pass
        from proxy_pool import rotation_count

        return rotation_count()

    def _shard_paid_proxy_count(self) -> int:
        """账号代理池分片内认证代理条数（仅分片模式）；用于重试时须能轮到每一条，勿被 max_proxy_tries=1 压成 1。"""
        if not getattr(self, "_proxy_per_account_chain", False):
            return 0
        ch = getattr(self, "_proxy_rotation_chain", None) or []
        return len([u for u in ch if (u or "").strip()])

    @staticmethod
    def _shard_retry_rounds() -> int:
        """
        分片内每条线都失败后，再完整轮询几轮（如 2 条线 × 2 轮 = 最多 4 次尝试，覆盖 AB 再 AB）。
        环境变量 MT_PROXY_SHARD_ROUNDS，默认 2；范围 1～16。
        """
        raw = (os.environ.get("MT_PROXY_SHARD_ROUNDS") or "2").strip()
        try:
            r = int(raw)
        except (TypeError, ValueError):
            r = 2
        return max(1, min(16, r))

    def _rotate_next_proxy(self) -> str:
        """下一出口：分片/代理池仅在认证代理间轮换，不落本机直连；固定单 URL 会话不换线。"""
        if (getattr(self, "_proxy_url", "") or "").strip() and not getattr(
            self, "_use_proxy_pool", False
        ):
            return (self._proxy_url or "").strip()

        if getattr(self, "_proxy_per_account_chain", False) and self._proxy_rotation_chain:
            ch = self._proxy_rotation_chain
            paid_idxs = [i for i, u in enumerate(ch) if (u or "").strip()]
            if not paid_idxs:
                return (self._proxy_url or "").strip() or ""
            cur_idx = int(getattr(self, "_proxy_rotation_idx", 0))
            try:
                pos = paid_idxs.index(cur_idx)
            except ValueError:
                pos = -1
            npos = (pos + 1) % len(paid_idxs)
            self._proxy_rotation_idx = paid_idxs[npos]
            url = ch[self._proxy_rotation_idx]
            if (os.environ.get("MT_PROXY_LOG") or "").strip().lower() in (
                "1",
                "true",
                "yes",
                "verbose",
            ):
                print(
                    f"[代理池] 本账号分片轮换 → {self._proxy_rotation_idx}/{len(ch)} "
                    f"{_proxy_hostport_for_log(url)}",
                    flush=True,
                )
            return url

        if getattr(self, "_use_proxy_pool", False):
            try:
                from proxy_pool import next_active_pool_url

                u = (next_active_pool_url() or "").strip()
                if u:
                    if (os.environ.get("MT_PROXY_LOG") or "").strip().lower() in (
                        "1",
                        "true",
                        "yes",
                        "verbose",
                    ):
                        print(
                            f"[代理池] 池内轮换（无直连）→ {_proxy_hostport_for_log(u)}",
                            flush=True,
                        )
                    return u
            except Exception:
                pass

        from proxy_pool import rotate_next_url

        return rotate_next_url()

    def _rush_ordered_exit_urls(self) -> list[str]:
        """
        抢购用固定顺序出口表（长度 N）：第 1 条=IP1、第 2 条=IP2…
        与分片链非空项顺序、active_urls 顺序、全局 rotation_chain 非空顺序一致。
        """
        if getattr(self, "_account_explicit_direct", False):
            return []
        if getattr(self, "_rush_full_pool_sequence", False):
            try:
                from proxy_pool import active_urls

                au = active_urls()
                if au:
                    return [(x or "").strip() for x in au]
            except Exception:
                pass
        u0 = (getattr(self, "_proxy_url", "") or "").strip()
        if u0 and not getattr(self, "_use_proxy_pool", False) and not getattr(
            self, "_proxy_per_account_chain", False
        ):
            return [u0]
        if getattr(self, "_proxy_per_account_chain", False) and self._proxy_rotation_chain:
            ch = self._proxy_rotation_chain
            paid_idxs = [i for i, u in enumerate(ch) if (u or "").strip()]
            if paid_idxs:
                return [(ch[i] or "").strip() for i in paid_idxs]
        if getattr(self, "_use_proxy_pool", False):
            try:
                from proxy_pool import active_urls

                au = active_urls()
                if au:
                    return [(x or "").strip() for x in au]
            except Exception:
                pass
        try:
            from proxy_pool import rotation_chain

            chain = rotation_chain()
            nonempty = [(c or "").strip() for c in chain if (c or "").strip()]
            if nonempty:
                return nonempty
        except Exception:
            pass
        if u0:
            return [u0]
        return []

    def _rush_proxy_url_sequence(self, n: int, *, advance: bool = True) -> list[str]:
        """
        抢购：整体代理池模式下从全局池游标连续取 N 条；账号分片/固定代理按本账号链循环。
        advance=False 时只预览序列（不推进游标），用于预热等不应消耗档位的场景。
        """
        n = max(1, int(n))
        if getattr(self, "_account_explicit_direct", False):
            return [""] * n
        if getattr(self, "_use_proxy_pool", False) and not getattr(
            self, "_proxy_per_account_chain", False
        ):
            try:
                from proxy_pool import active_urls, take_active_pool_urls

                if advance:
                    out = [(x or "").strip() for x in take_active_pool_urls(n)]
                else:
                    au = [(x or "").strip() for x in active_urls()]
                    out = [au[i % len(au)] for i in range(n)] if au else []
                if out:
                    if len(out) < n:
                        out = out + [out[-1]] * (n - len(out))
                    return out
            except Exception:
                pass
        ordered = self._rush_ordered_exit_urls()
        if not ordered:
            return [""] * n
        N = len(ordered)
        start = int(getattr(self, "_rush_serial_idx", 0)) % N
        out = [ordered[(start + k) % N] for k in range(n)]
        if advance:
            self._rush_serial_idx = (start + n) % N
        return out

    def _set_proxy_from_rotation(self, url: str | None) -> None:
        """写入轮换结果；空串表示本机直连。"""
        u = (url or "").strip()
        self._proxy_url = u
        self._proxy_direct_hop = u == ""

    def advance_active_pool_line_for_batch_order_addr(self) -> None:
        """
        批量改订单地址：改用代理池「顺序下一条」出口，不按账号分片钉死独占 IP。

        依赖 local_proxy_pool.txt；直连账号或未配置池则 no-op。
        """
        if getattr(self, "_account_explicit_direct", False):
            return
        try:
            from proxy_pool import next_active_pool_url_for_batch_order_addr, pool_is_configured

            if not pool_is_configured():
                return
            u = next_active_pool_url_for_batch_order_addr()
            if not (u or "").strip():
                return
            self._use_proxy_pool = True
            self._proxy_per_account_chain = False
            self._proxy_rotation_chain = []
            self._set_proxy_from_rotation(u)
            if (os.environ.get("MT_PROXY_LOG") or "").strip().lower() in (
                "1",
                "true",
                "yes",
                "verbose",
            ):
                print(
                    f"[代理池] 批量改址用池线 → {_proxy_hostport_for_log(u)}",
                    flush=True,
                )
        except Exception:
            pass

    def _proxy_url_for_pay_link_resolve(self) -> str:
        """
        提 H5 支付链接（支付宝 mcgw / SDK→URL）时选用的代理 URL 字符串。

        默认 **不** 沿用抢购成功写入的 rush_success 固定出口（避免单号「写死 IP」）；
        在启用代理池时按 raw_id 从池里取线，与初次建会话时分片规则一致。

        若需与组单/网关同线（旧行为）：设 MT_PAY_LINK_USE_RUSH_PIN=1 / true。
        若账号有抢购中号出口 rush_success_proxy_url：默认转链也走该线（MT_PAY_LINK_USE_RUSH_SUCCESS=0 可关）。
        """
        if getattr(self, "_force_order_chain_direct", False):
            return ""
        use_rush_line = (os.environ.get("MT_PAY_LINK_USE_RUSH_PIN", "0") or "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        cur = (getattr(self, "_proxy_url", "") or "").strip()
        if use_rush_line:
            return cur
        if getattr(self, "_pay_prefer_pool_rotate", False):
            if getattr(self, "_use_proxy_pool", False):
                try:
                    from proxy_pool import pick_proxy_url_for_raw_id

                    p = (pick_proxy_url_for_raw_id(self._raw_id) or "").strip()
                    if p:
                        return p
                except Exception:
                    pass
            return cur
        if (os.environ.get("MT_PAY_LINK_USE_RUSH_SUCCESS", "1") or "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        ):
            rk = (getattr(self, "_rush_success_proxy_url_norm", "") or "").strip()
            if rk:
                return rk
        pin = (getattr(self, "_compose_pin_proxy_url", "") or "").strip()
        if not pin:
            return cur
        if getattr(self, "_use_proxy_pool", False):
            try:
                from proxy_pool import pick_proxy_url_for_raw_id

                p = (pick_proxy_url_for_raw_id(self._raw_id) or "").strip()
                if p:
                    return p
            except Exception:
                pass
        return cur

    def proxy_kw_for_pay_link_resolve(self) -> dict:
        """供 /api/accounts/pay_link 里 mcgw 转链使用，出口见 _proxy_url_for_pay_link_resolve。"""
        return _proxy_kw_dict_from_url(self._proxy_url_for_pay_link_resolve())

    def _proxy_kw(self):
        return _proxy_kw_dict_from_url((getattr(self, "_proxy_url", "") or "").strip())

    def _ensure_pool_proxy_bound(self) -> None:
        """
        若启用了代理池但当前会话代理为空，强制绑定一条池中认证代理。
        防止请求落到直连导致「刷新地址正常、抢购被拒」这类不一致。
        """
        if not getattr(self, "_use_proxy_pool", False):
            return
        if getattr(self, "_proxy_direct_hop", False):
            return
        if (getattr(self, "_proxy_url", "") or "").strip():
            return
        try:
            if getattr(self, "_proxy_per_account_chain", False) and self._proxy_rotation_chain:
                ch = self._proxy_rotation_chain
                self._proxy_url = ch[0]
                self._proxy_rotation_idx = 0
                self._proxy_direct_hop = False
                self._proxy_prewarm_ok_for = ""
                return
            from proxy_pool import get_session_proxy_url

            p = (get_session_proxy_url() or "").strip()
            if p:
                self._proxy_url = p
                self._proxy_direct_hop = False
                self._proxy_prewarm_ok_for = ""
        except Exception:
            pass

    def _probe_current_proxy(self, timeout: float = 6.0) -> tuple[bool, str]:
        """
        在发业务请求前先验证当前代理是否可用（账号密码/隧道）。
        仅对业务域根路径发 **POST**（空 JSON），与 App 侧 HTTPS/TLS 及「测出口」口径一致，不用根域 GET。
        返回 (ok, reason)。
        """
        p = (getattr(self, "_proxy_url", "") or "").strip()
        if not p:
            return True, "direct"
        # 仅预连 i 茅台业务域：付费代理常封/限百度等站点，测百度会误判「代理坏」并疯狂换线
        targets = (
            "https://h5.moutai519.com.cn",
            "https://app.moutai519.com.cn",
        )
        last_reason = ""
        probe_headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "User-Agent": self._ua,
            "Connection": "Keep-Alive",
            "Accept": "*/*",
        }
        for u in targets:
            try:
                base = u.rstrip("/")
                r = _post(
                    f"{base}/",
                    timeout=timeout,
                    impersonate=TLS_PROFILE,
                    **self._proxy_kw(),
                    data=b"{}",
                    headers=probe_headers,
                )
                sc = r.status_code
                if sc == 429:
                    self._tls_bump_after_429()
                if sc == 407:
                    last_reason = f"{u} HTTP 407"
                    continue
                if sc in (502, 503, 504):
                    last_reason = f"{u} HTTP {sc}"
                    continue
                if 200 <= sc < 600:
                    return True, f"{u} HTTP {sc} (POST)"
                last_reason = f"{u} HTTP {sc}"
            except Exception as ex:
                last_reason = f"{u} {ex}"
                continue
        return False, last_reason or "proxy precheck failed"

    def _req_post(
        self,
        url,
        *,
        tls_imp=None,
        use_proxy=True,
        require_auth_proxy=False,
        precheck_proxy=True,
        max_proxy_tries=None,
        **kw,
    ):
        """
        tls_imp: 显式指定 impersonate（默认全局为 TLS_PROFILE=chrome_138）。
        省略时使用 TLS_PROFILE。
        require_auth_proxy 且走池计数时 pool_n 可能为 0（例如账号显式直连、_use_proxy_pool=False），
        此时只要本会话已有 _proxy_url（带账密 URL），仍允许请求。
        max_proxy_tries=1 时与抢购/组单一致：单次调用内不因 HTTP 429 扫整条**全局**代理池；
        若本会话为**账号分片**（一账号独占多 IP），仍会在本分片内线之间轮换，不受此参数压成仅 1 条；
        分片下线全失败后按 MT_PROXY_SHARD_ROUNDS（默认 2）再轮完整圈（如 AB 均 curl 56 后重新 AB）。
        若 HTTP 429 且正文为带 code 的业务 JSON，则立即返回该 Response，不因 429 换出口（与组单 compose/v2 一致）。
        若开启 MT_PROXY_ROTATE_PER_REQUEST 或账号 proxy_rotate_per_request：本方法**整次调用结束**后（无论返回还是抛错）
        将出口拨到下一跳，便于多分片 IP 按「第1次请求→IP1、第2次→IP2…」循环；内部重试过程中的换线逻辑仍保留。
        """
        try:
            return self._req_post_inner(
                url,
                tls_imp=tls_imp,
                use_proxy=use_proxy,
                require_auth_proxy=require_auth_proxy,
                precheck_proxy=precheck_proxy,
                max_proxy_tries=max_proxy_tries,
                **kw,
            )
        finally:
            self._advance_proxy_after_request_round(use_proxy)

    def _req_post_inner(
        self,
        url,
        *,
        tls_imp=None,
        use_proxy=True,
        require_auth_proxy=False,
        precheck_proxy=True,
        max_proxy_tries=None,
        **kw,
    ):
        from proxy_pool import pool_is_configured

        if use_proxy:
            self._ensure_pool_proxy_bound()
        pool_n = 0
        if use_proxy and getattr(self, "_use_proxy_pool", False) and pool_is_configured():
            pool_n = self._proxy_rotation_len()
        shard_n = self._shard_paid_proxy_count()
        bound_proxy = (getattr(self, "_proxy_url", "") or "").strip()
        if use_proxy and require_auth_proxy and pool_n <= 0 and not bound_proxy:
            raise RuntimeError("抢购要求使用认证代理，但代理池为空或无可用认证代理")
        if max_proxy_tries is not None:
            try:
                cap = max(1, int(max_proxy_tries))
            except (TypeError, ValueError):
                cap = 1
            # 分片：须在本号独占的几条线之间轮换；不能用 max_proxy_tries=1 把 pool_n 压成 1 否则永远切不到第二条
            if shard_n > 0:
                pool_n = shard_n * self._shard_retry_rounds()
            else:
                pool_n = min(pool_n, cap)
        elif shard_n > 0:
            pool_n = shard_n * self._shard_retry_rounds()
        max_tries = max(1, pool_n) if pool_n else 1

        last = None
        last_ex = None
        base = dict(kw)
        for attempt in range(max_tries):
            k2 = dict(base)
            if tls_imp is not None:
                k2["impersonate"] = tls_imp
            else:
                k2.setdefault("impersonate", TLS_PROFILE)
            if use_proxy:
                k2.update(self._proxy_kw())
            else:
                k2.update({"proxies": {"http": None, "https": None}})
            cur_proxy = (getattr(self, "_proxy_url", "") or "").strip() if use_proxy else ""
            if use_proxy and precheck_proxy and cur_proxy and cur_proxy != self._proxy_prewarm_ok_for:
                ok_probe, reason = self._probe_current_proxy()
                if ok_probe:
                    self._proxy_prewarm_ok_for = cur_proxy
                else:
                    if not getattr(self, "_use_proxy_pool", False) or max_tries < 2 or attempt >= max_tries - 1:
                        raise RuntimeError(f"代理预连接失败: {reason}")
                    self._set_proxy_from_rotation(self._rotate_next_proxy())
                    self._proxy_prewarm_ok_for = ""
                    print(
                        f"[代理池] 预连接失败({reason[:80]}) → 切换出口 {_proxy_hostport_for_log(self._proxy_url)} "
                        f"({attempt + 1}/{max_tries})"
                    )
                    continue
            try:
                last = _post(url, **k2)
            except Exception as ex:
                last_ex = ex
                # 代理节点超时/不可达也立刻换线，不只盯 HTTP 429（分片模式 max_tries 已含多轮 AB）
                if (not use_proxy) or (not getattr(self, "_use_proxy_pool", False)) or max_tries < 2 or attempt >= max_tries - 1:
                    raise
                self._set_proxy_from_rotation(self._rotate_next_proxy())
                self._proxy_prewarm_ok_for = ""
                print(
                    f"[代理池] 请求异常({str(ex)[:80]}) → 切换出口 {_proxy_hostport_for_log(self._proxy_url)} "
                    f"({attempt + 1}/{max_tries})",
                    flush=True,
                )
                continue
            if getattr(last, "status_code", None) == 429:
                self._tls_bump_after_429()
            if last.status_code != 429:
                return last
            if _http_response_has_json_business_code(last):
                return last
            if (not use_proxy) or (not getattr(self, "_use_proxy_pool", False)) or max_tries < 2:
                return last
            self._set_proxy_from_rotation(self._rotate_next_proxy())
            self._proxy_prewarm_ok_for = ""
            print(
                f"[代理池] HTTP 429 → 切换出口 {_proxy_hostport_for_log(self._proxy_url)} "
                f"({attempt + 1}/{max_tries})",
                flush=True,
            )
        if last is not None:
            return last
        if last_ex is not None:
            raise last_ex
        raise RuntimeError("请求失败：未获得响应")

    def _req_get(
        self,
        url,
        *,
        tls_imp=None,
        use_proxy=True,
        require_auth_proxy=False,
        precheck_proxy=True,
        max_proxy_tries=None,
        ship_query_429_rotate: bool = False,
        **kw,
    ):
        """
        ship_query_429_rotate: 为 True 时，HTTP 429 且 JSON 业务码为 429（频控）不立即返回，
        仍在代理池/分片内轮换出口重试（用于 ship/query 刷新地址）。
        按次轮换出口：同 _req_post，见 MT_PROXY_ROTATE_PER_REQUEST / proxy_rotate_per_request。
        """
        try:
            return self._req_get_inner(
                url,
                tls_imp=tls_imp,
                use_proxy=use_proxy,
                require_auth_proxy=require_auth_proxy,
                precheck_proxy=precheck_proxy,
                max_proxy_tries=max_proxy_tries,
                ship_query_429_rotate=ship_query_429_rotate,
                **kw,
            )
        finally:
            self._advance_proxy_after_request_round(use_proxy)

    def _req_get_inner(
        self,
        url,
        *,
        tls_imp=None,
        use_proxy=True,
        require_auth_proxy=False,
        precheck_proxy=True,
        max_proxy_tries=None,
        ship_query_429_rotate: bool = False,
        **kw,
    ):
        from proxy_pool import pool_is_configured

        if use_proxy:
            self._ensure_pool_proxy_bound()
        pool_n = 0
        if use_proxy and getattr(self, "_use_proxy_pool", False) and pool_is_configured():
            pool_n = self._proxy_rotation_len()
        shard_n = self._shard_paid_proxy_count()
        bound_proxy = (getattr(self, "_proxy_url", "") or "").strip()
        if use_proxy and require_auth_proxy and pool_n <= 0 and not bound_proxy:
            raise RuntimeError("请求要求使用认证代理，但代理池为空或无可用认证代理")
        if max_proxy_tries is not None:
            try:
                cap = max(1, int(max_proxy_tries))
            except (TypeError, ValueError):
                cap = 1
            if shard_n > 0:
                pool_n = shard_n * self._shard_retry_rounds()
            else:
                pool_n = min(pool_n, cap)
        elif shard_n > 0:
            pool_n = shard_n * self._shard_retry_rounds()
        max_tries = max(1, pool_n) if pool_n else 1

        last = None
        last_ex = None
        base = dict(kw)
        for attempt in range(max_tries):
            k2 = dict(base)
            if tls_imp is not None:
                k2["impersonate"] = tls_imp
            else:
                k2.setdefault("impersonate", TLS_PROFILE)
            if use_proxy:
                k2.update(self._proxy_kw())
            else:
                k2.update({"proxies": {"http": None, "https": None}})
            cur_proxy = (getattr(self, "_proxy_url", "") or "").strip() if use_proxy else ""
            if use_proxy and precheck_proxy and cur_proxy and cur_proxy != self._proxy_prewarm_ok_for:
                ok_probe, reason = self._probe_current_proxy()
                if ok_probe:
                    self._proxy_prewarm_ok_for = cur_proxy
                else:
                    if not getattr(self, "_use_proxy_pool", False) or max_tries < 2 or attempt >= max_tries - 1:
                        raise RuntimeError(f"代理预连接失败: {reason}")
                    self._set_proxy_from_rotation(self._rotate_next_proxy())
                    self._proxy_prewarm_ok_for = ""
                    print(
                        f"[代理池] 预连接失败({reason[:80]}) → 切换出口 {_proxy_hostport_for_log(self._proxy_url)} "
                        f"({attempt + 1}/{max_tries})",
                        flush=True,
                    )
                    continue
            try:
                last = _get(url, **k2)
            except Exception as ex:
                last_ex = ex
                if (not use_proxy) or (not getattr(self, "_use_proxy_pool", False)) or max_tries < 2 or attempt >= max_tries - 1:
                    raise
                self._set_proxy_from_rotation(self._rotate_next_proxy())
                self._proxy_prewarm_ok_for = ""
                print(
                    f"[代理池] GET异常({str(ex)[:80]}) → 切换出口 {_proxy_hostport_for_log(self._proxy_url)} "
                    f"({attempt + 1}/{max_tries})",
                    flush=True,
                )
                continue
            if getattr(last, "status_code", None) == 429:
                self._tls_bump_after_429()
            if last.status_code != 429:
                return last
            if _http_response_has_json_business_code(last):
                if not (
                    ship_query_429_rotate
                    and _http429_is_mt_rate_limit_json(last)
                ):
                    return last
            if (not use_proxy) or (not getattr(self, "_use_proxy_pool", False)) or max_tries < 2:
                return last
            self._set_proxy_from_rotation(self._rotate_next_proxy())
            self._proxy_prewarm_ok_for = ""
            print(
                f"[代理池] HTTP 429 → 切换出口 {_proxy_hostport_for_log(self._proxy_url)} "
                f"({attempt + 1}/{max_tries})",
                flush=True,
            )
        if last is not None:
            return last
        if last_ex is not None:
            raise last_ex
        raise RuntimeError("请求失败：未获得响应")

    def fetch_public_ip(self) -> dict:
        """
        探测当前 Session 出公网 IP（用于确认「账号代理 / HTTPS_PROXY」是否生效）。
        与账号侧「测出口」一致：对公网 IP 服务 **POST**（空 JSON），不用 GET。
        ipify 若返回非 JSON，再尝试 httpbin POST（解析 origin）。
        """
        probe_headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json, */*",
            "User-Agent": self._ua,
        }
        json_endpoints = (
            "https://api.ipify.org?format=json",
            "https://api64.ipify.org?format=json",
        )
        last = ""
        for ep in json_endpoints:
            try:
                r = self._req_post(
                    ep,
                    tls_imp=TLS_PROFILE,
                    timeout=12,
                    data=b"{}",
                    headers=probe_headers,
                )
                if r.status_code != 200:
                    last = f"{ep} HTTP {r.status_code}"
                    continue
                j = r.json()
                if isinstance(j, dict) and j.get("ip"):
                    return {
                        "ok": True,
                        "ip": str(j.get("ip")),
                        "proxy": _proxy_hostport_for_log(self._proxy_url),
                    }
                last = f"{ep} 无 ip 字段"
            except Exception as ex:
                last = str(ex)
        try:
            r = self._req_post(
                "https://httpbin.org/post",
                tls_imp=TLS_PROFILE,
                timeout=12,
                json={},
                headers={
                    "Accept": "application/json, */*",
                    "User-Agent": self._ua,
                },
            )
            if r.status_code != 200:
                return {
                    "ok": False,
                    "message": last or f"httpbin HTTP {r.status_code}",
                    "proxy": _proxy_hostport_for_log(self._proxy_url),
                }
            j = r.json()
            if isinstance(j, dict):
                origin = j.get("origin")
                if origin:
                    ip = str(origin).split(",")[0].strip()
                    if ip:
                        return {
                            "ok": True,
                            "ip": ip,
                            "proxy": _proxy_hostport_for_log(self._proxy_url),
                        }
        except Exception as ex:
            last = str(ex)
        return {"ok": False, "message": last or "出口探测失败", "proxy": _proxy_hostport_for_log(self._proxy_url)}

    def _base_hdr(self, sign=False):
        h = {
            "MT-Token": self._token, "User-Agent": self._ua,
            "MT-Device-ID": self._dev_token, "MT-APP-Version": RELEASE_VER,
            "MT-Request-ID": str(uuid.uuid4()), "MT-Network-Type": "4G",
            "MT-R": self._env, "MT-Bundle-ID": "com.moutai.mall",
            "MT-USER-TAG": "0", "MT-SN": self._cert,
            "MT-DTIME": format_mt_dtime_gmt08(),
            "MT-RS": self._scr,
            "MT-DOUBLE": "0",
            "MT-SIM": "0", "MT-ACBE": "0", "MT-ACB": "0", "MT-ACBM": "0",
            "Content-Type": "application/json; charset=UTF-8",
            "Host": "app.moutai519.com.cn",
            "Connection": "Keep-Alive", "Accept-Encoding": "gzip",
        }
        # 无浩天 bsid 时不要带空 BS-DVID，部分网关/风控对空值不友好
        _bs = (getattr(self, "_bsid", "") or "").strip()
        if _bs:
            h["BS-DVID"] = _bs
        if sign:
            k, v = make_kv_pair(self._raw_id)
            h["MT-K"] = k; h["MT-V"] = v
        return h

    def _app_order_hdr(self):
        """
        与 mt1.9.6 test-direct-order.js buildAppHeaders 一致（compose/v2、submit/v2、pay）：
        MT-K = 毫秒时间戳字符串，MT-V = generateMTV(MT-K, deviceInfo 明文)，勿用 make_kv_pair（登录发码另一套算法）。
        会话级固定：MT-SN 使用登录会话内固定 self._cert，避免链路中设备证书标识频繁跳变。
        请求级动态：MT-DTIME 每请求刷新，保持时间戳语义。
        """
        h = self._base_hdr(sign=False)
        rid = (self._raw_id or "").strip()
        k, v = make_h5_rush_kv_pair(rid)
        h["MT-K"] = k
        h["MT-V"] = v
        h["MT-BIZID"] = ""
        h["MT-Info"] = TRACE_TOKEN
        h["MT-Network-Type"] = "4G"
        h["MT-SN"] = self._cert
        h["MT-DTIME"] = format_d_time_mt_order_header()
        return h

    def _act_param_aes(self, obj: dict) -> str:
        """抢购组单/下单 actParam：AES-CBC + Base64（与 Node aesEncrypt 同密钥，服务端兼容无换行 Base64）。"""
        return param_encrypt(_json_min(obj))

    def _rush_h5_user_agent(self) -> str:
        """
        与 mt1.9.5 test-full-flow.js buildWebviewUserAgent(account) 一致：
        ... moutaiapp/{ver} device-id/{明文} BS-DVID/{bsDvid}（无 token 时仍带 BS-DVID/ 空值）；
        rushPurchase 请求头里不设独立 BS-DVID 头，仅 User-Agent 携带。
        """
        saved_ua = str(getattr(self, "_h5_user_agent", "") or "").strip()
        if saved_ua:
            saved_ua = _normalize_h5_ua_model_in_user_agent(saved_ua)
            bs = (getattr(self, "_bsid", "") or "").strip()
            if bs and re.search(r"BS-DVID/[^\s;]+", saved_ua):
                saved_ua = re.sub(r"BS-DVID/[^\s;]+", f"BS-DVID/{bs}", saved_ua)
            elif bs and "BS-DVID/" not in saved_ua:
                saved_ua = f"{saved_ua} BS-DVID/{bs}"
            saved_ua = _replace_chrome_version_in_ua(saved_ua, H5_FIXED_CHROME_VERSION)
            saved_ua = _replace_moutaiapp_version_in_ua(saved_ua, RELEASE_VER)
            return saved_ua
        raw = (self._ua or "android;12;Samsung;SM-S9180").strip()
        segs = raw.split(";")
        os_name = (segs[0] or "android").lower()
        os_version = (getattr(self, "_h5_android_version", "") or (segs[1] if len(segs) > 1 else "12")).strip()
        model = _normalize_h5_ua_model(
            (getattr(self, "_h5_device_model", "") or (segs[3] if len(segs) > 3 else "SM-S9180")).strip()
        )
        build_id = (getattr(self, "_h5_build_id", "") or "W528JS").strip()
        chrome_version = (
            H5_FIXED_CHROME_VERSION
            or (getattr(self, "_h5_chrome_version", "") or "95.0.4638.74").strip()
        )
        dev_path = (self._raw_id or "").strip() or (self._dev_token or "").strip()
        bs = (getattr(self, "_bsid", "") or "").strip()
        if os_name == "ios":
            ios_v = os_version.replace(".", "_")
            ua_prefix = (
                f"Mozilla/5.0 (iPhone; CPU iPhone OS {ios_v} like Mac OS X) "
                f"AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
            )
        else:
            ua_prefix = (
                f"Mozilla/5.0 (Linux; Android {os_version}; {model} Build/{build_id}; wv) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
                f"Chrome/{chrome_version} Mobile Safari/537.36"
            )
        return f"{ua_prefix} moutaiapp/{RELEASE_VER} device-id/{dev_path} BS-DVID/{bs}"

    def _notify_gdx_updated(self) -> None:
        cb = getattr(self, "_gdx_persist_cb", None)
        if callable(cb):
            try:
                cb(self)
            except Exception:
                pass

    def _set_gdx_cookie_value(self, value: str, *, fp: str = "", icp: str = "") -> str:
        raw = _extract_gdx_cookie_value(value)
        changed = raw != (getattr(self, "_gdxidpyhxdE", "") or "")
        self._gdxidpyhxdE = raw
        if fp:
            fp_s = str(fp or "").strip()
            if fp_s and fp_s != getattr(self, "_yidun_gdx_fp", ""):
                self._yidun_gdx_fp = fp_s
                changed = True
        if icp:
            self._gdx_icp = str(icp or "").strip()
            changed = True
        if changed:
            self._notify_gdx_updated()
        return raw

    def _ensure_gdx_cookie_value(self) -> str:
        with self._gdx_lock:
            existing = _extract_gdx_cookie_value(getattr(self, "_gdxidpyhxdE", ""))
            if _gdx_cookie_has_room(existing):
                return existing
            payload = {
                "host": "h5.moutai519.com.cn",
                "userAgent": self._rush_h5_user_agent(),
                "screen": getattr(self, "_scr", "") or "",
                "h5Did": getattr(self, "_h5id", "") or "",
                "fp": getattr(self, "_yidun_gdx_fp", "") or "",
                "icp": getattr(self, "_gdx_icp", "") or "",
            }
            built = _build_gdx_cookie_via_node(payload, timeout=5.0)
            value = str((built or {}).get("gdxidpyhxdE") or "").strip()
            if value:
                return self._set_gdx_cookie_value(
                    value,
                    fp=str((built or {}).get("fp") or ""),
                    icp=str((built or {}).get("icp") or ""),
                )
            return existing

    def _rush_cookie_mt(self, page_domain: str = "", request_ts_ms=None) -> str:
        """Match py_client.build_h5_headers Cookie for rush H5."""
        ua = self._rush_h5_user_agent()
        du = make_du_cookie(
            self._h5id,
            self._h5sid,
            ua=ua,
            domain=f"{H5_HOST}/mt/item/smsp-detail",
            hp=self._bs_ck,
            timestamp_ms=request_ts_ms,
        )
        wap_cookie = (self._cookie or "").strip()
        app_token = (self._token or "").strip()
        wap_token = (wap_cookie or app_token).strip()
        return (
            f"MT-Token-Wap={wap_token}; "
            f"MT-Device-ID-Wap={self._dev_token}; "
            f"_sdk_v_={SHIELD_VER}; "
            f"_bs_device_id={self._bs_ck}; "
            f"_d_u={du}"
        )

    def _rush_h5_headers_mt(self, bb: dict, k: str, v: str, ref: str, request_ts_ms=None, body=None) -> dict:
        """Match py_client.build_h5_headers header shape for rush H5."""
        r = (ref or "").strip() or f"{H5_HOST}/mt/item/hxx-detail?appConfig=2_1_2"
        ck = self._rush_cookie_mt(request_ts_ms=request_ts_ms)
        ua = self._rush_h5_user_agent()
        content_length = ""
        if body is not None:
            try:
                content_length = str(len(_json_min(body).encode("utf-8")))
            except Exception:
                content_length = ""
        pairs = [
            ("Host", "h5.moutai519.com.cn"),
            ("Connection", "keep-alive"),
        ]
        if content_length:
            pairs.append(("Content-Length", content_length))
        pairs.extend([
            ("Content-Type", "application/json"),
            ("Upgrade-Insecure-Requests", "1"),
            ("User-Agent", ua),
            ("Accept", "application/json, text/javascript, */*; q=0.01"),
            ("Sec-Fetch-Site", "same-origin"),
            ("Sec-Fetch-Mode", "cors"),
            ("Sec-Fetch-Dest", "empty"),
            ("Referer", r),
            ("Accept-Encoding", "gzip, deflate"),
            ("Accept-Language", "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7"),
            ("Cookie", ck),
            ("Origin", H5_HOST),
            ("x-csrf-token", ""),
            ("X-Requested-With", "XMLHttpRequest"),
            ("MT-APP-Version", RELEASE_VER),
            ("MT-V", v),
            ("MT-Device-ID", self._dev_token),
            ("MT-K", k),
            ("Content-Web-Bb", bb["Content-Web-Bb"]),
            ("Sdk-Ver-Bb", bb["Sdk-Ver-Bb"]),
            ("Content-Hh-Bb", bb["Content-Hh-Bb"]),
            ("MT-Info", TRACE_TOKEN),
        ])
        return dict(pairs)

    def _ensure_bb_state(self) -> None:
        """兼容旧调用；抢购 BB 固定由 shield.py 加载 runtime.wasm/ext.bin。"""
        return None

    def _notify_bb_warmup_updated(self) -> None:
        cb = getattr(self, "_bb_warmup_persist_cb", None)
        if callable(cb):
            try:
                cb(self)
            except Exception:
                pass

    def _bangcle_warmup_proxy_url(self) -> str:
        if getattr(self, "_account_explicit_direct", False):
            return ""
        try:
            self._ensure_pool_proxy_bound()
        except Exception:
            pass
        return str(getattr(self, "_proxy_url", "") or "").strip()

    def _bangcle_warmup_if_needed(self, item=None, referer: str = "", proxy_url=None) -> dict:
        with self._bb_warmup_lock:
            now_ms = int(time.time() * 1000)
            state = self._bb_state if isinstance(self._bb_state, dict) else {}
            expected_did = str(getattr(self, "_h5id", "") or "").strip()
            state_did = str(state.get("bbDid") or "").strip()
            has_cookie_state = isinstance(state.get("bbCookieState"), dict)
            if expected_did and (state_did != expected_did) and (state_did or has_cookie_state):
                state = {
                    key: state.get(key)
                    for key in _BB_WARMUP_PORTABLE_KEYS
                    if key in state
                }
                self._bb_state = dict(state)
                state_did = ""
            state_wasm_ver = str(state.get("bbSignWasmVersion") or "").strip()
            state_alg = str(state.get("__bbWarmupAlg") or "").strip()
            try:
                last_ms = int(state.get("__bbWarmupAt") or 0)
            except (TypeError, ValueError):
                last_ms = 0
            state_matches_session = (
                (not expected_did or state_did == expected_did)
                and state_wasm_ver == "v1.0.8"
                and state_alg == "py-v1.0.8"
            )
            if last_ms and now_ms - last_ms < _BANGCLE_WARMUP_MIN_INTERVAL_MS and state_matches_session:
                return {"success": True, "skipped": True, "state": state}

            ref = (referer or "").strip() or f"{H5_HOST}/"
            warm_timeout_ms = _bangcle_warmup_timeout_ms()
            warm_proxy_url = getattr(self, "_proxy_url", "") if proxy_url is None else proxy_url
            payload = {
                "uid": str(getattr(self, "_uid", "") or ""),
                "bbDid": expected_did,
                "userAgent": self._rush_h5_user_agent(),
                "referer": ref,
                "pageDomain": ref,
                "proxyUrl": str(warm_proxy_url or ""),
                "bbState": state,
                "timeoutMs": warm_timeout_ms,
                "now": now_ms,
            }
            if (os.environ.get("MT_PROXY_LOG") or "").strip().lower() in ("1", "true", "yes", "verbose"):
                print(
                    f"[Bangcle] warmup proxy={_proxy_hostport_for_log(payload.get('proxyUrl') or '')}",
                    flush=True,
                )
            result = _bangcle_warmup_via_node(payload, timeout=max(1.0, warm_timeout_ms / 1000.0 + 1.0))
            if result.get("success") and isinstance(result.get("state"), dict):
                self._bb_state = dict(result["state"])
                self._bb_warmup_at = int(self._bb_state.get("__bbWarmupAt") or now_ms)
                try:
                    self._bb_serial = int(
                        self._bb_state.get("bbSerial")
                        or self._bb_state.get("bbTps")
                        or self._bb_serial
                        or 1
                    )
                except (TypeError, ValueError):
                    self._bb_serial = 1
                self._bb_device_info = dict(self._bb_state.get("bbDeviceInfo") or {})
                try:
                    self._bb_tactics_version = int(self._bb_state.get("bbTacticsVersion") or 0)
                except (TypeError, ValueError):
                    self._bb_tactics_version = 0
                try:
                    self._bb_time_delta = int(self._bb_state.get("bbTimeDelta") or 0)
                except (TypeError, ValueError):
                    self._bb_time_delta = 0
                self._notify_bb_warmup_updated()
                summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
                print(
                    "[Bangcle] 风控预热完成 "
                    f"strategy={summary.get('strategyStatus')} "
                    f"version={summary.get('versionStatus')} "
                    f"upload={summary.get('uploadStatus')} "
                    f"wasmFetched={summary.get('wasmFetched')}",
                    flush=True,
                )
            else:
                print(f"[Bangcle] 风控预热失败: {result.get('message') or result}", flush=True)
            return result

    def warmup_bb(
        self,
        *,
        account_seed: str = "",
        force: bool = False,
        ttl_ms: int = 60_000,
        page_url: str = "https://h5.moutai519.com.cn/",
    ) -> dict:
        """Run Bangcle warmup upload while keeping shield.py as the rush BB calculator."""
        now_ms = int(time.time() * 1000)
        warm_at = int(getattr(self, "_bb_warmup_at", 0) or 0)
        age_ms = (now_ms - warm_at) if warm_at else None
        if not force and warm_at and age_ms is not None and age_ms < int(ttl_ms or 60_000):
            return {
                "ok": True,
                "skipped": True,
                "status": 200,
                "age_ms": age_ms,
                "serial": int(getattr(self, "_bb_serial", 1) or 1),
                "warmup_at": warm_at,
                "device_info": dict(getattr(self, "_bb_device_info", {}) or {}),
                "message": "warmup cache fresh",
                "response": None,
            }

        result = self._bangcle_warmup_if_needed(
            item=None,
            referer=page_url,
            proxy_url=self._bangcle_warmup_proxy_url(),
        )
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        upload_status = summary.get("uploadStatus")
        try:
            status = int(upload_status if upload_status is not None else 0)
        except (TypeError, ValueError):
            status = 0
        ok = bool(result.get("success")) and (status == 0 or 200 <= status < 300)
        warm_at = int(getattr(self, "_bb_warmup_at", 0) or 0)
        return {
            "ok": ok,
            "skipped": bool(result.get("skipped", False)),
            "status": status,
            "age_ms": 0 if warm_at else age_ms,
            "serial": int(getattr(self, "_bb_serial", 1) or 1),
            "warmup_at": warm_at,
            "device_info": dict(getattr(self, "_bb_device_info", {}) or {}),
            "message": result.get("message") or f"upload={status}",
            "summary": summary,
            "response": result,
        }

    def _h5_hdr(self, body, ref="", rush=False):
        request_ts_ms = int(adjusted_unix_ms())
        if not rush:
            # Non-rush H5 POST follows py版: body participates in BotShield.
            k, v = make_kv_pair(self._raw_id, timestamp_ms=request_ts_ms)
            bb = shield_for_post(
                body,
                appid=DEFAULT_APPID,
                devid=self._h5id,
                sessid=self._h5sid,
                uid=self._uid,
                wasm_ver=self._wv or WASM_TAG,
                rush=False,
                timestamp_ms=request_ts_ms,
            )
            du = make_du_cookie(self._h5id, self._h5sid, ua=self._wv_ua, timestamp_ms=request_ts_ms)
            wap_cookie = (self._cookie or "").strip()
            app_token = (self._token or "").strip()
            wap_token = (wap_cookie or app_token).strip()
            ck_parts = [
                f"MT-Token-Wap={wap_token}",
                f"MT-Device-ID-Wap={self._dev_token}",
            ]
            if app_token:
                ck_parts.append(f"MT-Token={app_token}")
            ck_parts.extend([
                f"_sdk_v_={SHIELD_VER}",
                f"_bs_device_id={self._bs_ck}",
                f"_d_u={du}",
            ])
            ck = "; ".join(ck_parts)
            h = {
                "MT-V": v, "MT-K": k, "MT-Device-ID": self._dev_token,
                "x-csrf-token": "",
                "MT-APP-Version": RELEASE_VER, "MT-Info": TRACE_TOKEN,
                "Content-Web-Bb": bb["Content-Web-Bb"],
                "Sdk-Ver-Bb": bb["Sdk-Ver-Bb"],
                "Content-Hh-Bb": bb["Content-Hh-Bb"],
                "content-type": "application/json",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": H5_HOST, "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty",
                "Referer": ref or f"{H5_HOST}/mt/item/hxx-detail?appConfig=2_1_2",
                "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                "Cookie": ck,
                "MT-Token": app_token,
            }
            h["User-Agent"] = self._wv_ua
            h["sec-ch-ua"] = self._sec_ch_ua
            h["sec-ch-ua-mobile"] = "?1"
            h["sec-ch-ua-platform"] = '"Android"'
            h["Accept-Encoding"] = "gzip, deflate, br, zstd"
            return h
        # Rush H5 follows py_client.build_h5_headers: plain H5 MT-K/V.
        k, v = make_kv_pair(self._raw_id, timestamp_ms=request_ts_ms)
        bb = shield_for_post(
            body,
            appid=DEFAULT_APPID,
            devid=self._h5id,
            sessid=self._h5sid,
            uid=self._uid,
            wasm_ver=WASM_TAG,
            rush=True,
            timestamp_ms=request_ts_ms,
            wasm_outer=bool(getattr(self, "_use_wasm_bb", False)),
        )
        return self._rush_h5_headers_mt(bb, k, v, ref, request_ts_ms=request_ts_ms, body=body)

    def _h5_hdr_with_yidun_fp(self, body, ref="", yidun_fp=""):
        h = self._h5_hdr(body, ref=ref, rush=False)
        h["Host"] = "h5.moutai519.com.cn"
        fp = str(yidun_fp or "").strip()
        if fp:
            ck = str(h.get("Cookie") or "").strip()
            encoded_fp = quote(fp, safe="")
            self._set_gdx_cookie_value(encoded_fp, fp=fp)
            if ck:
                parts = [
                    p.strip()
                    for p in ck.split(";")
                    if p.strip() and not p.strip().lower().startswith("gdxidpyhxde=")
                ]
                parts.append(f"gdxidpyhxdE={encoded_fp}")
                h["Cookie"] = "; ".join(parts)
            else:
                h["Cookie"] = f"gdxidpyhxdE={encoded_fp}"
        return h

    def _rush_act_body_url_ref(
        self,
        item,
        act_id,
        amount="1",
        src="",
        branch="",
        referer="",
        need_code=False,
    ) -> tuple[str, dict, str]:
        """抢购明文 data → 单次 seal 的 body，以及 URL、Referer（与 _rush_build_http_core 一致，不含头）。"""
        data = {
            "amount": amount,
            "itemCode": item,
            "itemPriorityActId": act_id,
            "userInfoBaseContext": {
                "addressLat": "",
                "addressLng": "",
                "appUserAgent": self._ua,
                "deviceId": self._dev_token,
                "mtr": self._env,
            },
            "ydLogId": "",
            "ydToken": "",
        }
        if need_code:
            data["code"] = "undefined"
        body = {"actParam": seal_param(data)}
        ref = (referer or "").strip()
        if not ref:
            ref = f"{H5_HOST}/mt/item/hxx-detail?appConfig=2_1_2"
            if src:
                ref += f"&sourceId={src}"
        self._last_rush_referer = ref
        br = (branch or "").strip().lower()
        if br in self._RUSH_BRANCHES:
            path = f"/xhr/front/trade/priority/rushPurchase/hot/branch/{br}"
        else:
            path = "/xhr/front/trade/priority/rushPurchase"
        url = f"{H5_HOST}{path}"
        print(f"[抢购] {item} x{amount} path={path}")
        return url, body, ref

    def _rush_referer_for_warmup(self, src="", referer="") -> str:
        ref = (referer or "").strip()
        if ref:
            return ref
        ref = f"{H5_HOST}/mt/item/hxx-detail?appConfig=2_1_2"
        if src:
            ref += f"&sourceId={src}"
        return ref

    # ---- 登录 ----

    def send_code(self, phone):
        phone = (phone or "").strip()
        if not phone:
            return {"code": -1, "message": "手机号为空"}
        ts = str(adjusted_unix_ms())
        sig = _md5(CODE_SALT + phone + ts)
        # 与 /register/login 一致携带 MT-K/V，新版本服务端常校验
        h = self._base_hdr(sign=True)
        b = {"md5": sig, "mobile": phone, "timestamp": ts}
        if self._proxy_url:
            print(f"[验证码] → {phone}（经代理 {_proxy_hostport_for_log(self._proxy_url)}）")
        else:
            print(f"[验证码] → {phone}（直连：未配置账号代理且未设置 HTTPS_PROXY 等环境变量）")
        try:
            r = self._req_post(
                f"{API_HOST}/xhr/front/user/register/vcode",
                tls_imp=TLS_PROFILE,
                headers=h,
                json=b,
            )
        except Exception as ex:
            err = humanize_requests_error(ex)
            print(f"[验证码] 请求失败: {err}")
            return {"code": -1, "message": err}
        print(f"[验证码] ← {r.status_code}")
        if r.status_code != 200:
            raw0 = (r.text or "").strip()
            if raw0:
                print(f"[验证码] 响应体(截断): {raw0[:480]}", flush=True)
            elif r.status_code == 429:
                print("[验证码] 响应体为空（常见：网关/限流直接 429，无 JSON）", flush=True)
            msg = f"HTTP {r.status_code}"
            raw = raw0
            if raw:
                try:
                    j = r.json()
                    if isinstance(j, dict) and j.get("message") is not None:
                        return {
                            "code": j.get("code", r.status_code),
                            "message": str(j.get("message")),
                        }
                except Exception:
                    msg = f"{msg}: {raw[:400]}"
            if r.status_code == 429:
                ra = (r.headers.get("Retry-After") or "").strip()
                ra_hint = f"（服务端建议 {ra}s 后再试）" if ra.isdigit() else ""
                tunnel = (
                    "若仅开了系统 VPN/加速器：Python 请求默认仍走本地宽带，请在账号里填 "
                    "http://127.0.0.1:混合端口 或设置 HTTPS_PROXY；用「测出口IP」确认已换出口。"
                )
                if self._proxy_url:
                    tunnel = (
                        "已配置代理仍 429：可能是代理节点本身也被限流，请换节点或暂停其它高频请求后再试。"
                    )
                msg = (
                    "请求过于频繁(429)：多为出口 IP / 行为被风控；不等同于永久封号，但可能出现数小时～数天的「冷却」"
                    "（俗称软拉黑）。换代理若仍 429，说明惩罚多半挂在手机号、设备指纹或整条链路上。"
                    + tunnel
                    + " 建议：停所有自动化尝试 24～48 小时；用手机自带网络在官方 i 茅台 App 试发验证码作对照；"
                    "机房/数据中心 IP 易被重点限制，可试住宅宽带；必要时「重置设备」后冷启动再发码，或换手机号测试。"
                    + ra_hint
                )
            elif r.status_code == 403:
                msg = "HTTP 403（可能被网关拦截），请检查网络/代理后重试"
            return {"code": r.status_code, "message": msg}
        if not r.text:
            return {"code": -1, "message": "响应为空"}
        res = r.json(); print(f"[验证码] {res}"); return res

    def login(self, phone, code):
        h = self._base_hdr(sign=True)
        b = {"vCode": code, "mobile": phone, "ydToken": "", "ydLogId": ""}
        print(f"[登录] → {phone}")
        try:
            rlogin = self._req_post(
                f"{API_HOST}/xhr/front/user/register/login",
                tls_imp=TLS_PROFILE,
                headers=h,
                json=b,
            )
            res = rlogin.json()
        except Exception as ex:
            err = humanize_requests_error(ex)
            print(f"[登录] 请求失败: {err}")
            return {"code": -1, "message": err}
        if res.get("code") == 2000:
            d = res["data"]
            self._token = d.get("token",""); self._cookie = d.get("cookie","")
            # 某些登录返回不带 cookie，H5 口径依赖 MT-Token-Wap，回退到 token 兜底。
            if not (self._cookie or "").strip() and (self._token or "").strip():
                self._cookie = self._token
            self._uid = str(d.get("userId",""))
            try:
                vs = d.get("verifyStatus")
                self._verify_status = int(vs) if vs is not None and str(vs) != "" else None
            except Exception:
                self._verify_status = None
            print(f"[登录] 成功 uid={self._uid}")
        else:
            print(f"[登录] 失败: {res}")
        return res

    # ---- 实名 ----

    def realname_query(self) -> dict:
        """
        查询实名状态（对齐 mt：仅使用登录缓存 verifyStatus）。
        不发任何远端请求，避免 404/429/代理波动干扰。
        """
        try:
            vs = getattr(self, "_verify_status", None)
            # #region agent log
            try:
                _payload = {
                    "sessionId": "eb180b",
                    "hypothesisId": "H3",
                    "location": "app.py:realname_query",
                    "message": "cached verifyStatus",
                    "data": {"vs": vs},
                    "timestamp": int(time.time() * 1000),
                }
                with open(_ROOT / "debug-eb180b.log", "a", encoding="utf-8") as _af:
                    _af.write(json.dumps(_payload, ensure_ascii=False) + "\n")
            except Exception:
                pass
            # #endregion
            if vs is not None:
                return {
                    "code": 2000,
                    "message": "ok(cached_login_verify_status)",
                    "data": {"verifyStatus": int(vs)},
                    "verifyStatus": int(vs),
                    "cached": True,
                    "source": "login_data.verifyStatus",
                }
        except Exception:
            pass
        return {"code": -1, "message": "实名查询失败：当前账号无登录缓存 verifyStatus，请重新登录后再查"}

    def realname_submit(self, name: str, id_code: str) -> dict:
        """
        提交实名认证（姓名 + 身份证号）。
        对齐 mt1.9.6：仅调用 app 域名 /xhr/front/user/realNameAuth。
        """
        nm = str(name or "").strip()
        ic = str(id_code or "").strip().upper()
        if not nm:
            return {"code": -1, "message": "姓名不能为空"}
        if len(ic) != 18:
            return {"code": -1, "message": "身份证号应为18位"}
        body = {
            "cardType": 0,
            "idCardName": nm,
            "idCardNo": ic,
        }
        # 头部按 mt test-full-flow.js realNameAuth 对齐
        h = {
            "MT-Token": self._token,
            "User-Agent": self._ua,
            "MT-Device-ID": self._dev_token,
            "MT-APP-Version": RELEASE_VER,
            "MT-Request-ID": str(uuid.uuid4()),
            "MT-Network-Type": "4G",
            "MT-R": self._env,
            "MT-Bundle-ID": "com.moutai.mall",
            "MT-USER-TAG": "0",
            "MT-SN": self._cert,
            "MT-DTIME": format_d_time_mt_order_header(),
            "MT-RS": "1440*3088",
            "BS-DVID": getattr(self, "_bsid", "") or "",
            "MT-DOUBLE": "0",
            "MT-SIM": "0",
            "MT-ACBE": "0",
            "MT-ACB": "0",
            "MT-ACBM": "0",
            "Content-Type": "application/json; charset=UTF-8",
            "Host": "app.moutai519.com.cn",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
        }
        pkw = self._proxy_kw()
        try:
            r = _go_bridge_http_dispatch(
                "POST",
                f"{API_HOST}/xhr/front/user/realNameAuth",
                headers=h,
                json=body,
                timeout=10,
                tls_impersonate=TLS_PROFILE,
                proxies=pkw.get("proxies"),
                proxy_auth=pkw.get("proxy_auth"),
            )
        except Exception as ex:
            return {"code": -1, "message": f"实名认证提交失败: {humanize_requests_error(ex)}"}
        if r.status_code == 429:
            self._tls_bump_after_429()
        if r.status_code != 200:
            return {
                "code": -1,
                "message": f"实名认证提交失败: HTTP {r.status_code} {(r.text or '')[:200]}",
            }
        try:
            j = r.json()
        except Exception:
            return {"code": -1, "message": f"实名认证提交失败: 非 JSON {(r.text or '')[:200]}"}
        if not isinstance(j, dict):
            return {"code": -1, "message": f"实名认证提交失败: 返回非对象 {str(j)[:120]}"}
        out = dict(j)
        out.setdefault("endpoint", "/xhr/front/user/realNameAuth")
        try:
            c = int(out.get("code", -1))
            if c in (2000, 200):
                self._verify_status = 1
        except Exception:
            pass
        # #region agent log
        try:
            _dc = out.get("data") if isinstance(out.get("data"), dict) else None
            _payload = {
                "sessionId": "eb180b",
                "hypothesisId": "H2",
                "location": "app.py:realname_submit",
                "message": "submit MT response summary",
                "data": {
                    "top_code": out.get("code"),
                    "top_code_type": type(out.get("code")).__name__,
                    "nested_data_code": (_dc.get("code") if _dc else None),
                    "session_verify_after": getattr(self, "_verify_status", None),
                    "out_keys": list(out.keys())[:24],
                },
                "timestamp": int(time.time() * 1000),
            }
            with open(_ROOT / "debug-eb180b.log", "a", encoding="utf-8") as _af:
                _af.write(json.dumps(_payload, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # #endregion
        return out

    def cancel_account(self) -> dict:
        h = self._base_hdr(sign=False)
        pkw = self._proxy_kw()
        try:
            r = _go_bridge_http_dispatch(
                "POST",
                f"{API_HOST}/xhr/front/user/register/cancel",
                headers=h,
                json={},
                timeout=12,
                tls_impersonate=TLS_PROFILE,
                proxies=pkw.get("proxies"),
                proxy_auth=pkw.get("proxy_auth"),
            )
        except Exception as ex:
            return {"code": -1, "message": f"注销失败: {humanize_requests_error(ex)}"}
        if r.status_code == 429:
            self._tls_bump_after_429()
        if r.status_code != 200:
            return {"code": -1, "message": f"注销失败: HTTP {r.status_code} {(r.text or '')[:200]}"}
        try:
            return r.json()
        except Exception:
            return {"code": -1, "message": f"注销失败: 非 JSON {(r.text or '')[:200]}"}

    def find_identity(self, name: str, id_code: str) -> dict:
        """
        找回身份（H5）：POST /xhr/front/user/findIdentity
        入参：name, idCardNo
        """
        nm = str(name or "").strip()
        ic = str(id_code or "").strip().upper()
        if not nm:
            return {"code": -1, "message": "姓名不能为空"}
        if len(ic) != 18:
            return {"code": -1, "message": "身份证号应为18位"}
        body = {"name": nm, "idCardNo": ic}
        # 与早期定制一致：Referer 用 H5 根路径（用户抓包对比用 /mt/findIdentity 时可再改）
        ref = f"{H5_HOST}/"
        h = self._h5_hdr(body, ref=ref, rush=False)
        if str(os.environ.get("MT_LOG_FIND_IDENTITY_PAYLOAD", "") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            cps = ",".join(f"U+{ord(c):04X}" for c in nm)
            print(f"[找回身份→茅台] name_repr={nm!r} codepoints={cps}", flush=True)
        try:
            r = self._req_post(
                f"{H5_HOST}/xhr/front/user/findIdentity",
                headers=h,
                json=body,
                require_auth_proxy=False,
                precheck_proxy=False,
            )
        except Exception as ex:
            return {"code": -1, "message": f"找回身份请求失败: {humanize_requests_error(ex)}"}
        if r.status_code != 200:
            return {
                "code": r.status_code,
                "message": f"找回身份失败: HTTP {r.status_code} {(r.text or '')[:200]}",
            }
        try:
            j = r.json()
        except Exception:
            return {"code": -1, "message": f"找回身份失败: 非 JSON {(r.text or '')[:220]}"}
        if isinstance(j, dict):
            j.setdefault("endpoint", "/xhr/front/user/findIdentity")
            return j
        return {"code": -1, "message": f"找回身份失败: 返回非对象 {str(j)[:120]}"}

    # ---- 地址 ----

    def addresses_detail(self, pid="", atype=1):
        """返回 (地址列表, 接口完整 JSON)，便于前端展示失败原因。"""
        h = self._base_hdr()
        params = {"type": 1, "addressType": int(atype) if atype else 1}
        if (pid or "").strip():
            params["provinceId"] = str(pid).strip()
        try:
            resp = self._req_get(
                f"{API_HOST}/xhr/front/user/address/ship/query",
                headers=h,
                params=params,
                ship_query_429_rotate=True,
            )
        except Exception as ex:
            err = {"code": -1, "message": humanize_requests_error(ex)}
            print(f"[地址] {err}")
            return [], err
        if resp.status_code != 200:
            err = {"code": resp.status_code, "message": (resp.text or "")[:400]}
            print(f"[地址] HTTP {resp.status_code}")
            return [], err
        try:
            r = resp.json()
        except Exception:
            raw = (resp.text or "")[:500]
            err = {"code": -1, "message": "响应非 JSON", "raw": raw}
            print(f"[地址] JSON 解析失败 body[:200]={raw[:200]!r}")
            return [], err
        if not isinstance(r, dict):
            err = {"code": -1, "message": "接口返回非对象"}
            return [], err
        if r.get("code") == 2000:
            d = r.get("data", [])
            lst = d.get("list", d.get("addresses", [])) if isinstance(d, dict) else d if isinstance(d, list) else []
            print(f"[地址] 共{len(lst)}个")
            for i, a in enumerate(lst):
                df = " (默认)" if a.get("dft") else ""
                print(f"  [{i}] {a.get('provinceName','')}{a.get('cityName','')}"
                      f"{a.get('districtName','')} {a.get('address','')}"
                      f" {a.get('name','')} {a.get('mobile','')}{df}")
            return lst, r
        print(f"[地址] 失败: {r}")
        return [], r

    def addresses(self, pid="", atype=1):
        lst, _ = self.addresses_detail(pid=pid, atype=atype)
        return lst

    def address_upsert(self, payload: dict) -> dict:
        """
        新增/更新收货地址：POST /xhr/front/user/address/ship/upsert
        - shipAddressId<=0 视为新增
        - shipAddressId>0 视为更新
        """
        if not isinstance(payload, dict):
            return {"code": -1, "message": "地址参数无效"}
        body = dict(payload)
        try:
            sid = int(body.get("shipAddressId", 0) or 0)
        except (TypeError, ValueError):
            sid = 0
        body["shipAddressId"] = sid
        # 与抓包兼容：布尔字段按布尔值发送
        body["dft"] = bool(body.get("dft", False))
        for k in ("grey", "recent"):
            if k in body:
                body[k] = bool(body.get(k))
        for rk in ("name", "mobile", "address", "provinceId", "cityId", "districtId"):
            if not str(body.get(rk, "")).strip():
                return {"code": -1, "message": f"{rk} 不能为空"}
        h = self._base_hdr(sign=False)
        pkw = self._proxy_kw()
        try:
            r = _go_bridge_http_dispatch(
                "POST",
                f"{API_HOST}/xhr/front/user/address/ship/upsert",
                headers=h,
                json=body,
                timeout=12,
                tls_impersonate=TLS_PROFILE,
                proxies=pkw.get("proxies"),
                proxy_auth=pkw.get("proxy_auth"),
            )
        except Exception as ex:
            return {"code": -1, "message": f"地址提交失败: {humanize_requests_error(ex)}"}
        if r.status_code == 429:
            self._tls_bump_after_429()
        if r.status_code != 200:
            return {
                "code": -1,
                "message": f"地址提交失败: HTTP {r.status_code} {(r.text or '')[:220]}",
            }
        try:
            j = r.json()
        except Exception:
            return {"code": -1, "message": f"地址提交失败: 非 JSON {(r.text or '')[:220]}"}
        if isinstance(j, dict):
            return j
        return {"code": -1, "message": f"地址提交失败: 返回非对象 {str(j)[:160]}"}

    def beforeinit_order_h5(
        self,
        *,
        spu_id: str = "741",
        store_id: str = "MAOTAI1002",
        count: int = 1,
        referer: str = "",
        ship_address_id: str = "",
        address_lat: str = "",
        address_lng: str = "",
    ) -> dict:
        """
        H5 POST /xhr/front/trade/order/standard/beforeinit
        与真机抓包一致：明文 JSON + _h5_hdr（含 BotShield 三件套、Cookie、sec-ch-ua*、Referer 等）。
        默认经纬度与抓包示例一致，可传 address_lat/address_lng 覆盖。
        """
        spu_id = str(spu_id or "").strip() or "741"
        store_id = str(store_id or "").strip() or "MAOTAI1002"
        try:
            cnt = max(1, int(count))
        except (TypeError, ValueError):
            cnt = 1
        lat = str(address_lat or "").strip() or "28.137752"
        lng = str(address_lng or "").strip() or "112.906819"
        sid = str(ship_address_id if ship_address_id is not None else "").strip()
        body = {
            "itemList": [{"count": cnt, "spuId": spu_id, "storeId": store_id}],
            "userInfoBaseContext": {
                "addressLat": lat,
                "addressLng": lng,
                "mtr": self._env,
                "deviceId": self._dev_token,
                "appUserAgent": self._ua,
            },
            "ydToken": "",
            "ydLogId": "",
            "shipAddressId": sid,
        }
        ref = (referer or "").strip() or f"{H5_HOST}/"
        h = self._h5_hdr(body, ref=ref, rush=False)
        h["Host"] = "h5.moutai519.com.cn"
        h["Connection"] = "keep-alive"
        url = f"{H5_HOST}/xhr/front/trade/order/standard/beforeinit"
        try:
            r = self._req_post(
                url,
                headers=h,
                json=body,
                require_auth_proxy=False,
                precheck_proxy=False,
            )
        except Exception as ex:
            return {
                "ok": False,
                "httpStatus": 0,
                "message": humanize_requests_error(ex),
            }
        sc = int(getattr(r, "status_code", 0) or 0)
        raw_text = (r.text or "").strip()
        out: dict = {
            "ok": 200 <= sc < 300,
            "httpStatus": sc,
            "rawPreview": raw_text[:1200],
        }
        try:
            j = r.json()
            if isinstance(j, dict):
                out["json"] = j
                out["code"] = j.get("code")
                out["message"] = j.get("message")
        except Exception:
            out["jsonParseError"] = True
        return out

    def order_list_h5(
        self,
        *,
        page_no: int = 1,
        page_size: int = 20,
        referer: str = "",
        extra_query: dict | None = None,
    ) -> dict:
        """
        H5 GET /xhr/front/trade/order/list/get（与真机抓包一致：query 参与 BotShield，须用 shield_for_get）。
        默认 query：pageNo、pageSize；可通过 extra_query 覆盖或追加（如 status 等，以你抓包为准）。
        """
        ref = (referer or "").strip() or f"{H5_HOST}/"
        try:
            pn = max(1, int(page_no))
        except (TypeError, ValueError):
            pn = 1
        try:
            ps = max(1, min(100, int(page_size)))
        except (TypeError, ValueError):
            ps = 20
        qdict: dict[str, str] = {}
        if isinstance(extra_query, dict):
            for k, v in extra_query.items():
                if v is None:
                    continue
                ks = str(k).strip()
                if not ks:
                    continue
                qdict[ks] = str(v).strip()
        if "pageNo" not in qdict:
            qdict["pageNo"] = str(pn)
        if "pageSize" not in qdict:
            qdict["pageSize"] = str(ps)
        qs = urlencode(sorted(qdict.items(), key=lambda x: x[0]))
        path_q = f"/xhr/front/trade/order/list/get?{qs}"
        full_url = f"{H5_HOST}{path_q}"
        bb = shield_for_get(
            full_url,
            appid=DEFAULT_APPID,
            devid=self._h5id,
            sessid=self._h5sid,
            uid=self._uid,
            ua=self._wv_ua,
            wasm_ver=self._wv,
            rush=False,
        )
        k, v = make_kv_pair(self._raw_id)
        du = make_du_cookie(self._h5id, self._h5sid, ua=self._wv_ua)
        wap_cookie = (self._cookie or "").strip()
        app_token = (self._token or "").strip()
        wap_token = (wap_cookie or app_token).strip()
        ck_parts = [
            f"MT-Token-Wap={wap_token}",
            f"MT-Device-ID-Wap={self._dev_token}",
        ]
        if app_token:
            ck_parts.append(f"MT-Token={app_token}")
        ck_parts.extend(
            [
                f"_sdk_v_={SHIELD_VER}",
                f"_bs_device_id={self._bs_ck}",
                f"_d_u={du}",
            ]
        )
        ck = "; ".join(ck_parts)
        h = {
            "MT-V": v,
            "MT-K": k,
            "MT-Device-ID": self._dev_token,
            "x-csrf-token": "",
            "MT-APP-Version": RELEASE_VER,
            "MT-Info": TRACE_TOKEN,
            "Content-Web-Bb": bb["Content-Web-Bb"],
            "Sdk-Ver-Bb": bb["Sdk-Ver-Bb"],
            "Content-Hh-Bb": bb["Content-Hh-Bb"],
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": H5_HOST,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": ref,
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cookie": ck,
            "MT-Token": app_token,
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Connection": "keep-alive",
            "Host": "h5.moutai519.com.cn",
        }
        h["User-Agent"] = self._wv_ua
        h["sec-ch-ua"] = self._sec_ch_ua
        h["sec-ch-ua-mobile"] = "?1"
        h["sec-ch-ua-platform"] = '"Android"'
        direct = bool(getattr(self, "_force_order_chain_direct", False))
        print(
            f"[订单列表] 出口={_proxy_hostport_for_log('' if direct else (getattr(self, '_proxy_url', '') or ''))}",
            flush=True,
        )
        try:
            r = self._req_get(
                full_url,
                headers=h,
                use_proxy=not direct,
                require_auth_proxy=False,
                precheck_proxy=not direct,
            )
        except Exception as ex:
            return {
                "ok": False,
                "httpStatus": 0,
                "message": humanize_requests_error(ex),
            }
        sc = int(getattr(r, "status_code", 0) or 0)
        raw_text = (r.text or "").strip()
        out: dict = {
            "ok": 200 <= sc < 300,
            "httpStatus": sc,
            "rawPreview": raw_text[:2000],
            "requestPath": path_q.split("?")[0],
            "requestQuery": qs,
        }
        try:
            j = r.json()
            if isinstance(j, dict):
                out["json"] = j
                out["code"] = j.get("code")
                out["message"] = j.get("message")
        except Exception:
            out["jsonParseError"] = True
        return out

    def order_detail_h5(
        self,
        *,
        order_id: str,
        order_level: str = "2",
        referer: str = "",
        timestamp_ms: int | str | None = None,
    ) -> dict:
        """H5 GET /xhr/front/trade/order/detail/get"""
        oid = str(order_id or "").strip()
        if not oid:
            return {"ok": False, "httpStatus": 0, "code": -1, "message": "orderId 不能为空"}
        try:
            ts = str(int(timestamp_ms)) if timestamp_ms not in (None, "") else str(adjusted_unix_ms())
        except (TypeError, ValueError):
            ts = str(adjusted_unix_ms())
        ref = (referer or "").strip() or (
            f"{H5_HOST}/mt/order/detail?appConfig=1_0_2&orderId={quote(oid)}&orderLevel={order_level or '2'}"
        )
        qs = urlencode([("__timestamp", ts), ("orderId", oid)])
        path_q = f"/xhr/front/trade/order/detail/get?{qs}"
        full_url = f"{H5_HOST}{path_q}"
        bb = shield_for_get(
            full_url,
            appid=DEFAULT_APPID,
            devid=self._h5id,
            sessid=self._h5sid,
            uid=self._uid,
            ua=self._wv_ua,
            wasm_ver=self._wv,
            rush=False,
        )
        k, v = make_kv_pair(self._raw_id)
        du = make_du_cookie(self._h5id, self._h5sid, ua=self._wv_ua)
        wap_cookie = (self._cookie or "").strip()
        app_token = (self._token or "").strip()
        wap_token = (wap_cookie or app_token).strip()
        ck_parts = [
            f"MT-Token-Wap={wap_token}",
            f"MT-Device-ID-Wap={self._dev_token}",
        ]
        if app_token:
            ck_parts.append(f"MT-Token={app_token}")
        ck_parts.extend(
            [
                "YX_SUPPORT_WEBP=1",
                f"_sdk_v_={SHIELD_VER}",
                f"_bs_device_id={self._bs_ck}",
                f"_d_u={du}",
            ]
        )
        h = {
            "MT-V": v,
            "MT-K": k,
            "MT-Device-ID": self._dev_token,
            "x-csrf-token": "",
            "MT-APP-Version": RELEASE_VER,
            "MT-Info": TRACE_TOKEN,
            "Content-Web-Bb": bb["Content-Web-Bb"],
            "Sdk-Ver-Bb": bb["Sdk-Ver-Bb"],
            "Content-Hh-Bb": bb["Content-Hh-Bb"],
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "content-type": "application/json",
            "cache-control": "no-cache",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": H5_HOST,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": ref,
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cookie": "; ".join(ck_parts),
            "MT-Token": app_token,
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Connection": "keep-alive",
            "Host": "h5.moutai519.com.cn",
        }
        h["User-Agent"] = self._wv_ua
        h["sec-ch-ua"] = self._sec_ch_ua
        h["sec-ch-ua-mobile"] = "?1"
        h["sec-ch-ua-platform"] = '"Android"'
        direct = bool(getattr(self, "_force_order_chain_direct", False))
        print(
            f"[订单详情] 出口={_proxy_hostport_for_log('' if direct else (getattr(self, '_proxy_url', '') or ''))} orderId={oid}",
            flush=True,
        )
        try:
            r = self._req_get(
                full_url,
                headers=h,
                use_proxy=not direct,
                require_auth_proxy=False,
                precheck_proxy=not direct,
            )
        except Exception as ex:
            return {"ok": False, "httpStatus": 0, "code": -1, "message": humanize_requests_error(ex)}
        sc = int(getattr(r, "status_code", 0) or 0)
        raw_text = (r.text or "").strip()
        out: dict = {
            "ok": 200 <= sc < 300,
            "httpStatus": sc,
            "rawPreview": raw_text[:2000],
            "requestPath": path_q.split("?")[0],
            "requestQuery": qs,
        }
        try:
            j = r.json()
            if isinstance(j, dict):
                out["json"] = j
                out["code"] = j.get("code")
                out["message"] = j.get("message")
        except Exception:
            out["jsonParseError"] = True
        return out

    def order_ops_update_h5(
        self,
        *,
        order_id: str,
        ship_address_id: int,
        deliver_method: int = 1,
        order_level: str = "2",
        item: dict | None = None,
        referer: str = "",
    ) -> dict:
        """
        H5 POST /xhr/front/trade/order/ops/update
        按抓包口径提交：addressInfo + deliverMethod + orderId + orderLevel + itemList。
        """
        oid = str(order_id or "").strip()
        if not oid:
            return {"ok": False, "httpStatus": 0, "code": -1, "message": "orderId 不能为空"}
        try:
            sid = int(ship_address_id)
        except (TypeError, ValueError):
            sid = 0
        if sid <= 0:
            return {"ok": False, "httpStatus": 0, "code": -1, "message": "shipAddressId 不合法"}
        try:
            dm = int(deliver_method)
        except (TypeError, ValueError):
            dm = 1
        lv = str(order_level or "").strip() or "2"
        one = dict(item) if isinstance(item, dict) else {}
        spu_id = str(one.get("spuId") or one.get("itemCode") or "").strip()
        store_id = str(one.get("storeId") or "MAOTAI1002").strip() or "MAOTAI1002"
        province_id = str(one.get("provinceId") or "43000000").strip() or "43000000"
        try:
            cnt = max(1, int(one.get("count", 1) or 1))
        except (TypeError, ValueError):
            cnt = 1
        try:
            prom_type = int(one.get("promType", 0) or 0)
        except (TypeError, ValueError):
            prom_type = 0
        if not spu_id:
            return {"ok": False, "httpStatus": 0, "code": -1, "message": "item.spuId 不能为空"}
        body = {
            "addressInfo": {"shipAddressId": sid},
            "deliverMethod": dm,
            "orderId": oid,
            "orderLevel": lv,
            "itemList": [
                {
                    "count": cnt,
                    "spuId": spu_id,
                    "storeId": store_id,
                    "provinceId": province_id,
                    "promType": prom_type,
                }
            ],
        }
        # region agent log
        try:
            import json as _dbg_json
            import time as _dbg_time
            with open(_ROOT / "debug-eb180b.log", "a", encoding="utf-8") as _dbg_f:
                _dbg_f.write(_dbg_json.dumps({
                    "sessionId": "eb180b",
                    "runId": "ops-update-v1",
                    "hypothesisId": "H5",
                    "location": "app.py:order_ops_update_h5:payload",
                    "message": "payload_built",
                    "data": {
                        "path": "/xhr/front/trade/order/ops/update",
                        "hasAddressInfo": isinstance(body.get("addressInfo"), dict),
                        "hasShipAddressId": bool(body.get("addressInfo", {}).get("shipAddressId")),
                        "deliverMethod": body.get("deliverMethod"),
                        "orderLevelType": type(body.get("orderLevel")).__name__,
                        "itemListLen": len(body.get("itemList") or []),
                        "itemKeys": sorted(list((body.get("itemList") or [{}])[0].keys())),
                    },
                    "timestamp": int(_dbg_time.time() * 1000),
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # endregion
        ref = (referer or "").strip() or f"{H5_HOST}/"
        h = self._h5_hdr(body, ref=ref, rush=False)
        h["Host"] = "h5.moutai519.com.cn"
        h["Connection"] = "keep-alive"
        try:
            r = self._req_post(
                f"{H5_HOST}/xhr/front/trade/order/ops/update",
                headers=h,
                json=body,
                require_auth_proxy=False,
                precheck_proxy=False,
            )
        except Exception as ex:
            return {
                "ok": False,
                "httpStatus": 0,
                "code": -1,
                "message": humanize_requests_error(ex),
            }
        sc = int(getattr(r, "status_code", 0) or 0)
        raw_text = (r.text or "").strip()
        out: dict = {
            "ok": 200 <= sc < 300,
            "httpStatus": sc,
            "rawPreview": raw_text[:1200],
        }
        try:
            j = r.json()
            if isinstance(j, dict):
                out["json"] = j
                out["code"] = j.get("code")
                out["message"] = j.get("message")
        except Exception:
            out["jsonParseError"] = True
        # region agent log
        try:
            import json as _dbg_json
            import time as _dbg_time
            with open(_ROOT / "debug-eb180b.log", "a", encoding="utf-8") as _dbg_f:
                _dbg_f.write(_dbg_json.dumps({
                    "sessionId": "eb180b",
                    "runId": "ops-update-v1",
                    "hypothesisId": "H5",
                    "location": "app.py:order_ops_update_h5:response",
                    "message": "response_got",
                    "data": {
                        "httpStatus": out.get("httpStatus"),
                        "ok": out.get("ok"),
                        "code": out.get("code"),
                        "hasJson": bool(out.get("json")),
                    },
                    "timestamp": int(_dbg_time.time() * 1000),
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # endregion
        return out

    def order_ops_cancel_h5(
        self,
        *,
        order_id: str,
        order_status: int = 1,
        reason: int = 10,
        order_level: str = "2",
        referer: str = "",
    ) -> dict:
        """H5 POST /xhr/front/trade/order/ops/cancel."""
        oid = str(order_id or "").strip()
        if not oid:
            return {"ok": False, "httpStatus": 0, "code": -1, "message": "orderId 不能为空"}
        try:
            status_val = int(order_status)
        except Exception:
            status_val = 1
        try:
            reason_val = int(reason)
        except Exception:
            reason_val = 10
        body = {
            "orderId": int(oid) if oid.isdigit() else oid,
            "orderStatus": status_val,
            "reason": reason_val,
        }
        ref = (referer or "").strip() or (
            f"{H5_HOST}/mt/order/detail?appConfig=1_0_2&orderId={quote(oid)}&orderLevel={order_level or '2'}"
        )
        h = self._h5_hdr(body, ref=ref, rush=False)
        h["Host"] = "h5.moutai519.com.cn"
        h["Connection"] = "keep-alive"
        try:
            r = self._req_post(
                f"{H5_HOST}/xhr/front/trade/order/ops/cancel",
                headers=h,
                json=body,
                require_auth_proxy=False,
                precheck_proxy=False,
            )
        except Exception as ex:
            return {
                "ok": False,
                "httpStatus": 0,
                "code": -1,
                "message": humanize_requests_error(ex),
                "requestBody": body,
            }
        sc = int(getattr(r, "status_code", 0) or 0)
        raw_text = (r.text or "").strip()
        out: dict = {
            "ok": 200 <= sc < 300,
            "httpStatus": sc,
            "rawPreview": raw_text[:800],
            "requestBody": body,
        }
        try:
            j = r.json()
            if isinstance(j, dict):
                out["json"] = j
                out["code"] = j.get("code")
                out["message"] = j.get("message")
                data = j.get("data")
                if isinstance(data, dict):
                    out["opResult"] = data.get("opResult")
                    out["successDesc"] = data.get("successDesc")
        except Exception:
            out["jsonParseError"] = True
        return out

    def confirm_receipt_h5(self, *, order_id: str, order_level: str = "2", referer: str = "") -> dict:
        """H5 POST /xhr/front/trade/order/ops/confirm — 确认收货"""
        oid = str(order_id or "").strip()
        if not oid:
            return {"ok": False, "httpStatus": 0, "code": -1, "message": "orderId 不能为空"}
        body = {"orderId": oid}
        ref = (referer or "").strip() or f"{H5_HOST}/mt/order/detail?appConfig=1_0_2&orderId={oid}&orderLevel={order_level}"
        h = self._h5_hdr(body, ref=ref, rush=False)
        h["Host"] = "h5.moutai519.com.cn"
        h["Connection"] = "keep-alive"
        try:
            r = self._req_post(
                f"{H5_HOST}/xhr/front/trade/order/ops/confirm",
                headers=h,
                json=body,
                require_auth_proxy=False,
                precheck_proxy=False,
            )
        except Exception as ex:
            return {"ok": False, "httpStatus": 0, "code": -1, "message": humanize_requests_error(ex)}
        sc = int(getattr(r, "status_code", 0) or 0)
        raw_text = (r.text or "").strip()
        out: dict = {"ok": 200 <= sc < 300, "httpStatus": sc, "rawPreview": raw_text[:800]}
        try:
            j = r.json()
            if isinstance(j, dict):
                out["json"] = j
                out["code"] = j.get("code")
                out["message"] = j.get("message")
                if j.get("code") == 2000 and isinstance(j.get("data"), dict):
                    out["opResult"] = j["data"].get("opResult")
        except Exception:
            out["jsonParseError"] = True
        return out

    # ---- 活动/商品查询 ----

    def purchase_info_v2(self, spu_id: str, hot: bool = True, referer: str = "") -> dict:
        """
        H5 商品详情接口：根据 IMTP(spuId) 拉取 purchaseInfoMap，含当前 itemPriorityActId。
        与 mt1.9.6 test-full-flow / purchaseInfoV2 一致。
        """
        body = {"hot": bool(hot), "spuId": spu_id.strip()}
        ref = referer or (
            f"{H5_HOST}/mt/item/hxx-detail?appConfig=2_1_2&sourceId={quote(spu_id)}"
        )
        h = self._h5_hdr(body, ref=ref, rush=False)
        try:
            r = self._req_post(
                f"{H5_HOST}/xhr/front/mall/item/purchaseInfoV2",
                headers=h,
                json=body,
            )
        except Exception as ex:
            return {"code": -1, "message": humanize_requests_error(ex)}
        if r.status_code != 200:
            return {
                "code": r.status_code,
                "message": ((r.text or "")[:300] or f"HTTP {r.status_code}"),
            }
        try:
            return r.json()
        except Exception:
            return {"code": -1, "message": (r.text or "")[:300]}

    def fetch_priority_act_id(self, spu_id: str, hot: bool = True, referer: str = "") -> tuple[str | None, dict]:
        """
        返回 (itemPriorityActId 字符串, 最后一次 purchaseInfoV2 原始 JSON)。
        同一商品在不同详情页 Referer 下返回可能不同，依次尝试多种组合（含 hot 开关）。
        """
        spu_id = (spu_id or "").strip()
        if not spu_id:
            return None, {}
        r0 = (referer or "").strip()
        refs = []
        if r0:
            refs.append(r0)
        refs.extend(
            [
                f"{H5_HOST}/mt/item/hxx-detail?appConfig=2_1_2&sourceId={quote(spu_id)}",
                f"{H5_HOST}/mt/item/smsp-detail?appConfig=2_1_2",
                f"{H5_HOST}/mt/item/jpmt-detail?appConfig=2_1_2&sourceId={quote(spu_id)}",
                f"{H5_HOST}/mt/item/1000ml-detail?appConfig=2_1_2&sourceId={quote(spu_id)}",
                f"{H5_HOST}/mt/item/mm-485-detail-group?appConfig=2_1_2&sourceId={quote(spu_id)}",
                f"{H5_HOST}/mt/item/detailV2?appConfig=2_1_2&id={quote(spu_id)}",
                f"{H5_HOST}/mt/item/xft-detail?appConfig=2_1_2&sourceId={quote(spu_id)}",
            ]
        )
        seen = set()
        last_raw: dict = {}
        for ref in refs:
            if not ref or ref in seen:
                continue
            seen.add(ref)
            for try_hot in (hot, not hot):
                raw = self.purchase_info_v2(spu_id, hot=try_hot, referer=ref)
                last_raw = raw if isinstance(raw, dict) else {}
                aid = priority_act_from_purchase_info(raw)
                if aid:
                    return aid, raw
        return None, last_raw

    def fetch_act_items(self) -> list:
        """
        从多个接口采集当前可抢购的 itemCode + actId 列表。
        返回: [{"itemCode":..., "actId":..., "itemName":..., "price":...}, ...]
        """
        found = {}  # key=(itemCode,actId)

        def _pick(obj):
            if isinstance(obj, dict):
                code = (obj.get("itemCode") or obj.get("spuId") or
                        obj.get("itemId") or "")
                act  = (obj.get("itemPriorityActId") or obj.get("actId") or
                        obj.get("priorityActId") or "")
                name = (obj.get("itemName") or obj.get("name") or
                        obj.get("title") or "")
                price = str(obj.get("price") or obj.get("salePrice") or "")
                if code and act:
                    k = (str(code), str(act))
                    if k not in found:
                        found[k] = {"itemCode": str(code), "actId": str(act),
                                    "itemName": name, "price": price}
                for v in obj.values():
                    _pick(v)
            elif isinstance(obj, list):
                for v in obj:
                    _pick(v)

        # 1. 热门分支接口（H5签名）
        for ep in self.RUSH_ENDPOINTS:
            try:
                body = {"pageNum": 1, "pageSize": 50}
                h = self._h5_hdr(body, rush=False)
                r = self._req_post(f"{H5_HOST}{ep}", headers=h, json=body)
                if r and r.text:
                    _pick(r.json())
            except Exception:
                pass

        # 2. APP端活动列表接口
        _act_endpoints = [
            "/xhr/front/trade/priority/act/list",
            "/xhr/front/trade/priority/item/list",
            "/xhr/front/item/priority/list",
            "/xhr/front/trade/priority/actItem/list",
        ]
        for ep in _act_endpoints:
            try:
                h = self._base_hdr()
                r = self._req_get(f"{API_HOST}{ep}", headers=h,
                         params={"pageNum": 1, "pageSize": 50})
                if r and r.text:
                    _pick(r.json())
            except Exception:
                pass

        # 3. H5活动列表接口
        _h5_act_endpoints = [
            "/xhr/front/trade/priority/act/list",
            "/xhr/front/trade/priority/item/queryList",
            "/xhr/front/trade/priority/actItem/queryList",
        ]
        for ep in _h5_act_endpoints:
            try:
                body = {"pageNum": 1, "pageSize": 50}
                h = self._h5_hdr(body, rush=False)
                r = self._req_post(f"{H5_HOST}{ep}", headers=h, json=body)
                if r and r.text:
                    _pick(r.json())
            except Exception:
                pass

        result = list(found.values())
        print(f"[采集] 共找到 {len(result)} 个商品活动")
        return result

    def fetch_shop_items(self, shop_id: str) -> list:
        """
        查询指定门店的可购商品（用于到店自提场景）。
        shop_id: 门店ID
        """
        found = {}

        def _pick(obj):
            if isinstance(obj, dict):
                code = obj.get("itemCode") or obj.get("spuId") or ""
                act  = (obj.get("itemPriorityActId") or obj.get("actId") or
                        obj.get("priorityActId") or "")
                name = obj.get("itemName") or obj.get("name") or ""
                price = str(obj.get("price") or obj.get("salePrice") or "")
                if code and act:
                    k = (str(code), str(act))
                    if k not in found:
                        found[k] = {"itemCode": str(code), "actId": str(act),
                                    "itemName": name, "price": price,
                                    "shopId": shop_id}
                for v in obj.values():
                    _pick(v)
            elif isinstance(obj, list):
                for v in obj:
                    _pick(v)

        _shop_endpoints = [
            "/xhr/front/trade/priority/shop/item/list",
            "/xhr/front/trade/priority/shopItem/list",
            "/xhr/front/shop/item/list",
        ]
        for ep in _shop_endpoints:
            try:
                h = self._base_hdr()
                r = self._req_get(f"{API_HOST}{ep}", headers=h,
                         params={"shopId": shop_id, "pageNum": 1, "pageSize": 50})
                if r and r.text:
                    _pick(r.json())
            except Exception:
                pass

        result = list(found.values())
        print(f"[门店采集] shopId={shop_id} 找到 {len(result)} 个商品")
        return result

    # ---- 抢购 ----
    # mt test-full-flow ITEM_LIST「741 飞天带杯」：branch=one →
    #   .../priority/rushPurchase/hot/branch/one
    # Referer: https://h5.moutai519.com.cn/mt/item/smsp-detail?appConfig=2_1_2

    _RUSH_BRANCHES = frozenset({"one", "two", "three", "four"})

    def _normalize_rush_http_response(self, rsp):
        try:
            _res = rsp.json()
        except Exception:
            _res = {
                "code": rsp.status_code,
                "message": "响应非 JSON",
                "raw": (rsp.text or "")[:800],
            }
        if isinstance(_res, list):
            _res = {"code": -1, "message": "抢购接口返回为数组，非预期", "raw": str(_res)[:500]}
        elif not isinstance(_res, dict):
            if _res in (1, 2, "1", "2"):
                _res = {
                    "code": -1,
                    "message": (
                        "抢购接口仅返回简码 1/2（多为限流、排队或服务端短时非标准体，与部分场景下 HTTP 429 响应体类似）；"
                        "请加大重试间隔、确认开售期与活动 ID，勿当作「黑号」唯一依据。"
                    ),
                    "rushBodyCode": int(_res) if str(_res).strip() in ("1", "2") else _res,
                    "raw": str(_res),
                }
            else:
                _res = {
                    "code": -1,
                    "message": "抢购接口返回类型异常（期望 JSON 对象，实际为非对象）",
                    "raw": str(_res)[:500],
                }
        elif "code" not in _res:
            _res = {
                "code": -1,
                "message": "响应无 code 字段",
                "rawPreview": json.dumps(_res, ensure_ascii=False)[:600],
            }
        return _res

    def _rush_is_soft_limit_like(self, _res: dict) -> bool:
        if not isinstance(_res, dict):
            return False
        if str(_res.get("rushBodyCode") or "") in ("1", "2"):
            return True
        c = _res.get("code")
        cs = str(c).strip()
        msg = str(_res.get("message") or "").strip()
        if cs in ("429", "4293"):
            return True
        low = msg.lower()
        if "429" in low:
            return True
        if "人数较多" in msg or "稍后再试" in msg or "排队" in msg:
            return True
        return False

    def _rush_build_http_core(
        self,
        item,
        act_id,
        amount="1",
        src="",
        branch="",
        referer="",
        need_code=False,
    ):
        """仅组包，不切换分片出口（rush() 首跳前自行 cycle，重试轮只用 rotate）。单档与 mt 行为一致。"""
        url, body, ref = self._rush_act_body_url_ref(
            item, act_id, amount, src, branch, referer, need_code
        )
        h = self._h5_hdr(body, ref=ref, rush=True)
        return url, h, body

    def _mobile_relay_post(self, url: str, headers: dict, body: dict) -> _NodeHttpResponse:
        from mobile_relay import dispatch_http

        hdrs = {str(k): str(v) for k, v in (headers or {}).items()}
        attempt = _bridge_prepare_attempt("POST", url, hdrs, body, "", 0)
        attempt["headersOrdered"] = _ordered_header_pairs(attempt.get("headers") or {})
        result = dispatch_http(
            getattr(self, "_mobile_relay_device", "") or "",
            attempt,
            phone=getattr(self, "_account_phone", "") or getattr(self, "_uid", "") or "",
            timeout_sec=float(os.environ.get("MT_MOBILE_RELAY_TIMEOUT", "45") or 45),
        )
        used_device = str(result.get("deviceId") or getattr(self, "_mobile_relay_device", "") or "").strip()
        status = int(result.get("statusCode") or result.get("httpStatus") or 0)
        resp_headers = result.get("headers") if isinstance(result.get("headers"), dict) else {}
        body_b64 = str(result.get("bodyB64") or "")
        if body_b64:
            try:
                content = base64.b64decode(body_b64)
            except Exception:
                content = b""
        else:
            content = str(result.get("body") or result.get("text") or "").encode("utf-8", errors="replace")
        resp = _NodeHttpResponse(status, content, resp_headers)
        try:
            setattr(resp, "_mobile_relay_device_id", used_device)
        except Exception:
            pass
        return resp

    def _rush_mobile_relay_concurrent(
        self,
        *,
        total_try: int,
        item,
        act_id,
        amount,
        src,
        branch,
        referer,
        need_code,
        gap_ms: int,
        rr_n: int,
        max_try: int,
        rrgap: int,
        rrjit: int,
        shot_callback=None,
    ) -> dict:
        relay_device = str(getattr(self, "_mobile_relay_device", "") or "").strip()
        total_try = max(1, int(total_try or 1))
        cap = min(total_try, _mobile_relay_parallel_cap())
        sem = threading.BoundedSemaphore(cap)
        lock = threading.Lock()
        stop_event = threading.Event()
        rows = [None] * total_try
        results = [None] * total_try
        threads = []
        wall_t0 = time.perf_counter()

        def run_one(index: int, url: str, headers: dict, body: dict) -> None:
            try:
                r = None
                shot_t0 = time.perf_counter()
                try:
                    r = self._mobile_relay_post(url, headers, body)
                    last_one = self._normalize_rush_http_response(r)
                except Exception as ex:
                    last_one = {"code": -1, "message": f"mobile relay error: {ex}"}
                ms = int((time.perf_counter() - shot_t0) * 1000)
                row = {
                    "attempt": index + 1,
                    "httpStatus": getattr(r, "status_code", None) if r is not None else None,
                    "code": last_one.get("code") if isinstance(last_one, dict) else -1,
                    "message": str((last_one or {}).get("message") or "")[:500] if isinstance(last_one, dict) else "",
                    "ms": ms,
                    "transport": "mobile_relay_async",
                    "deviceId": str(getattr(r, "_mobile_relay_device_id", "") or relay_device),
                }
                with lock:
                    rows[index] = row
                    results[index] = last_one if isinstance(last_one, dict) else {"code": -1, "message": "invalid result"}
                    if isinstance(last_one, dict) and _rush_row_stop_rush(last_one):
                        stop_event.set()
                if shot_callback:
                    try:
                        shot_callback(dict(row))
                    except Exception as ex:
                        print(f"[rush] mobile relay shot callback error: {ex}", flush=True)
                print(
                    f"[rush] mobile_relay async shot {index + 1}/{total_try} {ms}ms "
                    f"device={row.get('deviceId') or '-'} http={row.get('httpStatus')} "
                    f"code={row.get('code')} {row.get('message')}",
                    flush=True,
                )
            finally:
                sem.release()

        print(
            f"[rush] mobile_relay async dispatch shots={total_try} concurrency={cap} device={relay_device or '-'}",
            flush=True,
        )
        scheduled = 0
        for i in range(total_try):
            if stop_event.is_set():
                break
            sem.acquire()
            if stop_event.is_set():
                sem.release()
                break
            try:
                url, h, body = self._rush_build_http_core(
                    item, act_id, amount, src, branch, referer, need_code
                )
                _rush_sleep_shot_jitter()
                t = threading.Thread(
                    target=run_one,
                    args=(i, url, h, body),
                    name=f"mobile-relay-shot-{i + 1}",
                    daemon=True,
                )
                threads.append(t)
                scheduled += 1
                t.start()
            except Exception:
                sem.release()
                raise
            if i < total_try - 1:
                wait_ms = int(gap_ms or 0)
                if rr_n > 1 and max_try > 0 and (i + 1) % max_try == 0:
                    wait_ms = int(rrgap or 0) + (random.randint(0, int(rrjit or 0)) if int(rrjit or 0) > 0 else 0)
                if wait_ms > 0:
                    time.sleep(wait_ms / 1000.0)

        for t in threads:
            t.join()

        trace = [row for row in rows if isinstance(row, dict)]
        last = None
        for result in reversed(results):
            if isinstance(result, dict):
                last = dict(result)
                break
        if not isinstance(last, dict):
            last = {"code": -1, "message": "mobile relay no result"}
        last["rushLoopTrace"] = trace
        last["rushLoopWallMs"] = int((time.perf_counter() - wall_t0) * 1000)
        last["rushTransport"] = "mobile_relay_async"
        last["mobileRelayDevice"] = str((trace[-1] if trace else {}).get("deviceId") or relay_device)
        last["mobileRelayScheduled"] = scheduled
        last["mobileRelayConcurrency"] = cap
        ref_for_verify = (referer or "").strip() or getattr(self, "_last_rush_referer", "")
        if ref_for_verify:
            last.setdefault("_rushReferer", ref_for_verify)
        print(f"[rush] mobile_relay async done -> {last}", flush=True)
        return last

    def rush_build_http(
        self,
        item,
        act_id,
        amount="1",
        src="",
        branch="",
        referer="",
        need_code=False,
    ):
        """批量 Node 抢购：组包前不再单独 cycle 分片；出口顺序由 _rush_serial_idx 与 _rush_proxy_url_sequence 统一。"""
        return self._rush_build_http_core(
            item, act_id, amount, src, branch, referer, need_code
        )

    def _rush_soft429_multi_hop_allowed(self) -> bool:
        """
        与 rush 内「软限流可换出口」一致：仅当存在多条可轮换出口时多档才有意义。
        - 全局代理池已配置且本会话走池；或
        - 账号分片代理链内有 2 条及以上认证代理。
        单账号固定一条代理时返回 False，避免 Node 连发同一出口无意义重复。
        """
        if getattr(self, "_proxy_per_account_chain", False):
            return self._shard_paid_proxy_count() > 1
        if getattr(self, "_use_proxy_pool", False):
            try:
                from proxy_pool import active_urls

                return len(active_urls()) > 1
            except Exception:
                return False
        return False

    def _rush_max_attempts(self) -> int:
        try:
            soft_retry = max(0, int(os.environ.get("MT_RUSH_SOFT429_RETRY", "1") or 1))
        except (TypeError, ValueError):
            soft_retry = 1
        can_rotate = self._rush_soft429_multi_hop_allowed()
        return 1 + (soft_retry if can_rotate else 0)

    def _rush_guard_auth_proxy_for_single_hop(self) -> None:
        """与 rush 单轮 _req_post(require_auth_proxy=True, max_proxy_tries=1) 相同的代理池门禁。"""
        from proxy_pool import pool_is_configured

        if getattr(self, "_account_explicit_direct", False):
            return
        self._ensure_pool_proxy_bound()
        pool_n = 0
        if getattr(self, "_use_proxy_pool", False) and pool_is_configured():
            pool_n = self._proxy_rotation_len()
        bound_proxy = (getattr(self, "_proxy_url", "") or "").strip()
        if pool_n <= 0 and not bound_proxy:
            raise RuntimeError("抢购要求使用认证代理，但代理池为空或无可用认证代理")

    def _rush_apply_full_pool_max_try(self, max_try: int) -> int:
        """
        full_pool_rush 不改动用户设定档数：
        - 极速模式按「重试」次数执行；
        - 代理池条数不足时在 _rush_proxy_url_sequence 中按池顺序循环。
        """
        return max_try

    def _rush_proxy_snapshot(self) -> dict:
        return {
            "_proxy_url": getattr(self, "_proxy_url", "") or "",
            "_proxy_rotation_idx": int(getattr(self, "_proxy_rotation_idx", 0)),
            "_proxy_direct_hop": bool(getattr(self, "_proxy_direct_hop", False)),
            "_proxy_prewarm_ok_for": getattr(self, "_proxy_prewarm_ok_for", "") or "",
        }

    def _rush_proxy_restore(self, snap: dict) -> None:
        self._proxy_url = snap.get("_proxy_url", "") or ""
        self._proxy_rotation_idx = int(snap.get("_proxy_rotation_idx", 0))
        self._proxy_direct_hop = bool(snap.get("_proxy_direct_hop", False))
        self._proxy_prewarm_ok_for = snap.get("_proxy_prewarm_ok_for", "") or ""

    def _rush_runtime_proxy_pool(self) -> list[str]:
        if getattr(self, "_account_explicit_direct", False):
            return []
        if not getattr(self, "_use_proxy_pool", False):
            return []
        return _runtime_rush_proxy_pool_snapshot()

    def _rush_build_node_attempt_packages(
        self,
        max_try: int,
        item,
        act_id,
        amount="1",
        src="",
        branch="",
        referer="",
        need_code=False,
        lane_count: int | None = None,
        shot_index_start: int = 1,
    ) -> list[dict]:
        """
        为 Node /rush_loop 预组多轮 HTTP：与 mt 同口径每枪全量刷新参数。
        每档都会重算 actParam + Content-Web-Bb/Content-Hh-Bb + MT-K/V/MT-SN；
        走代理池时，rush_loop 出口不再在这里提前写死；Go 侧按 lane 从全局代理池借线，业务正常保持。
        全部 attempts 一次 JSON 下发 Go 本地串行/高频发送。
        """
        snap = self._rush_proxy_snapshot()
        # 0：Go 桥不设 http.Client.Timeout（不再出现 Client.Timeout exceeded while awaiting headers）
        t_ms = 0
        n = max(1, int(max_try))
        try:
            first_shot_index = max(1, int(shot_index_start or 1))
        except (TypeError, ValueError):
            first_shot_index = 1
        try:
            out: list[dict] = []
            runtime_pool = self._rush_runtime_proxy_pool()
            fixed_proxy = "" if runtime_pool else (getattr(self, "_proxy_url", "") or "").strip()
            for i in range(n):
                shot_no = first_shot_index + i
                # 每枪按 mt 口径重建 body 与防护头，避免高频模式下复用同一 actParam/BB。
                url, body, ref = self._rush_act_body_url_ref(
                    item, act_id, amount, src, branch, referer, need_code
                )
                h = self._h5_hdr(body, ref=ref, rush=True)
                hdrs = {str(k): str(v) for k, v in (h or {}).items()}
                attempt = _bridge_prepare_attempt(
                    "POST",
                    url,
                    hdrs,
                    body,
                    fixed_proxy,
                    t_ms,
                    lane_id=_rush_lane_id_for_shot(shot_no, lane_count or n),
                )
                attempt["headersOrdered"] = _ordered_header_pairs(attempt.get("headers") or {})
                attempt["attemptIndex"] = shot_no
                attempt["totalShots"] = n
                out.append(attempt)
            return out
        finally:
            self._rush_proxy_restore(snap)

    def rush_build_one_node_attempt(
        self,
        shot_index: int,
        total_shots: int,
        item,
        act_id,
        amount="1",
        src="",
        branch="",
        referer="",
        need_code=False,
        lane_count: int | None = None,
    ) -> dict:
        """
        实时逐枪高频：只现场组当前这一枪，不预组整批。
        shot_index 为 1-based；走代理池时出口由 Go 侧发包前从全局代理池即时领取。
        """
        snap = self._rush_proxy_snapshot()
        try:
            runtime_pool = self._rush_runtime_proxy_pool()
            fixed_proxy = "" if runtime_pool else (getattr(self, "_proxy_url", "") or "").strip()
            url, body, ref = self._rush_act_body_url_ref(
                item, act_id, amount, src, branch, referer, need_code
            )
            h = self._h5_hdr(body, ref=ref, rush=True)
            hdrs = {str(k): str(v) for k, v in (h or {}).items()}
            attempt = _bridge_prepare_attempt(
                "POST",
                url,
                hdrs,
                body,
                fixed_proxy,
                0,
                lane_id=_rush_lane_id_for_shot(shot_index, lane_count or total_shots),
            )
            attempt["headersOrdered"] = _ordered_header_pairs(attempt.get("headers") or {})
            attempt["attemptIndex"] = max(1, int(shot_index or 1))
            attempt["totalShots"] = max(1, int(total_shots or 1))
            return attempt
        finally:
            self._rush_proxy_restore(snap)

    def rush(
        self,
        item,
        act_id,
        amount="1",
        src="",
        branch="",
        referer="",
        need_code=False,
        *,
        rush_loop_attempts=None,
        rush_loop_delay_ms=None,
        rush_loop_burst_in_node=None,
        fire_and_forget=False,
        rush_rounds: int | None = None,
        rush_round_gap_ms: int | None = None,
        rush_round_gap_jitter_ms: int | None = None,
        rush_shot_index: int | None = None,
        rush_lane_count: int | None = None,
        rush_lane_parallel: bool | None = None,
        shot_callback=None,
    ):
        """
        与 MT 一致：Referer（该商品 H5 详情 URL）与 branch（抢购接口路径后缀）须与 itemCode、
        itemPriorityActId、spu 来源同一快照成套；勿只用默认页却走 branch/one。
        branch: 空=默认 /rushPurchase；one/two/three/four → /rushPurchase/hot/branch/{branch}
        referer: 完整 H5 Referer；不传则退化为 hxx-detail（仅兜底，易与商品不一致）
        need_code: ITEM_LIST.needCodeField 为 True 时 body 增加 code:\"undefined\"
        BS-DVID：与 MT 一致依赖账号 bsid；当前策略仅在发码前获取并在后续流程复用，不再登录后异步刷新。
        若需强制与 MT 相同「无 bs 不准抢」，可设环境变量 MT_RUSH_BLOCK_WITHOUT_BS=1（由 Web 层校验）。
        rush_loop_attempts：显式指定本轮连发档数（>=1），与 WebUI「重试」对齐；不传则仍按 _rush_max_attempts()。
        rush_rounds + fire_and_forget：⚡高频多轮；总档数 = rush_loop_attempts × rush_rounds（每轮档数=重试），轮与轮之间在 Node sleep（rush_round_gap_ms ± 抖动）。
        若轮次>1：无论枪间间隔是否>0，均强制单次 Node 整包连发（否则拆枪后 Node 收不到 rushRounds，轮间停顿会丢失）。
        rush_loop_delay_ms：Node rush_loop 在 axios 收完上一枪响应后先 sleep 再发下一枪 POST（与 WebUI「间隔(ms)」）；0 或 None=紧连。
        rush_loop_burst_in_node=True：强制整轮在单次 Node /rush_loop 内连打（枪间仍 sleep）；否则间隔>0 时可能拆成多次 Python↔Node（见 MT_RUSH_NODE_BURST）。
        MT_RUSH_HTTP_NODE=1：抢购多轮在 Node /rush_loop 内**串行**执行（await execOneTask：上一枪收完响应再发下一枪，与极速/间隔无关）。
        MT_RUSH_JITTER_MS_MAX：每枪 POST 前额外随机等待 [0,N]ms（默认 100，上限 100；0=关闭）；Node 与 Python curl 路径均生效。
        走代理池时，Go rush_loop 每个 lane 首次发包从全局池取线；业务正常保持，HTTP 429 空/非业务体才让该 lane 下一枪换出口。
        账号**只填一条固定代理**时各档仍同一出口（要每枪换 IP 须空代理走池）。
        非 Go 路径仍按 _rush_proxy_url_sequence(max_try) 逐枪设代理。
        """
        if not (getattr(self, "_bsid", "") or "").strip():
            print("[抢购] 提示：当前无 BS-DVID，请先走「发码 -> 登录」获取浩天后再抢")
        if (os.environ.get("MT_RUSH_SERIAL_RESET") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            self._rush_serial_idx = 0
        max_try = self._rush_max_attempts()
        if rush_loop_attempts is not None:
            try:
                ut = int(rush_loop_attempts)
                max_try = max(1, ut)
            except (TypeError, ValueError):
                pass
        max_try = self._rush_apply_full_pool_max_try(max_try)
        try:
            node_shot_index_start = max(1, int(rush_shot_index or 1))
        except (TypeError, ValueError):
            node_shot_index_start = 1
        node_lane_count = _rush_default_lane_count() if rush_lane_parallel else max_try
        if rush_lane_count is not None:
            try:
                node_lane_count = max(1, int(rush_lane_count))
            except (TypeError, ValueError):
                node_lane_count = _rush_default_lane_count() if rush_lane_parallel else max_try
        try:
            rr_n = int(rush_rounds) if rush_rounds is not None else 1
        except (TypeError, ValueError):
            rr_n = 1
        rr_n = max(1, min(100, rr_n))
        try:
            rrgap = int(rush_round_gap_ms) if rush_round_gap_ms is not None else (1000 if rr_n > 1 else 0)
        except (TypeError, ValueError):
            rrgap = 1000 if rr_n > 1 else 0
        rrgap = max(0, min(300_000, rrgap))
        try:
            rrjit = int(rush_round_gap_jitter_ms) if rush_round_gap_jitter_ms is not None else (100 if rr_n > 1 else 0)
        except (TypeError, ValueError):
            rrjit = 100 if rr_n > 1 else 0
        rrjit = max(0, min(60_000, rrjit))
        build_try = max_try
        if rr_n > 1:
            build_try = min(500, max(1, max_try * rr_n))
        last = None
        gap_ms = 0
        if rush_loop_delay_ms is not None:
            try:
                gap_ms = max(0, min(300_000, int(rush_loop_delay_ms)))
            except (TypeError, ValueError):
                gap_ms = 0

        if _bangcle_warmup_on_rush():
            try:
                warm_proxy = self._bangcle_warmup_proxy_url()
                self._bangcle_warmup_if_needed(
                    item=item,
                    referer=self._rush_referer_for_warmup(src=src, referer=referer),
                    proxy_url=warm_proxy,
                )
            except Exception as ex:
                print(f"[Bangcle] 风控预热失败: {ex}", flush=True)

        if getattr(self, "_use_mobile_relay", False):
            return self._rush_mobile_relay_concurrent(
                total_try=build_try,
                item=item,
                act_id=act_id,
                amount=amount,
                src=src,
                branch=branch,
                referer=referer,
                need_code=need_code,
                gap_ms=gap_ms,
                rr_n=rr_n,
                max_try=max_try,
                rrgap=rrgap,
                rrjit=rrjit,
                shot_callback=shot_callback,
            )
            relay_device = str(getattr(self, "_mobile_relay_device", "") or "").strip()
            total_try = build_try
            tr: list[dict] = []
            wall_t0 = time.perf_counter()
            for i in range(total_try):
                url, h, body = self._rush_build_http_core(
                    item, act_id, amount, src, branch, referer, need_code
                )
                r = None
                shot_t0 = time.perf_counter()
                _rush_sleep_shot_jitter()
                try:
                    r = self._mobile_relay_post(url, h, body)
                    last = self._normalize_rush_http_response(r)
                except Exception as ex:
                    last = {
                        "code": -1,
                        "message": f"手机发包异常: {ex}",
                    }
                ms = int((time.perf_counter() - shot_t0) * 1000)
                row = {
                    "attempt": i + 1,
                    "httpStatus": getattr(r, "status_code", None) if r is not None else None,
                    "code": last.get("code") if isinstance(last, dict) else -1,
                    "message": str((last or {}).get("message") or "")[:500] if isinstance(last, dict) else "",
                    "ms": ms,
                    "transport": "mobile_relay",
                    "deviceId": str(getattr(r, "_mobile_relay_device_id", "") or relay_device),
                }
                tr.append(row)
                if shot_callback:
                    try:
                        shot_callback(dict(row))
                    except Exception as ex:
                        print(f"[抢购] 手机发包逐枪回调异常: {ex}", flush=True)
                print(
                    f"[抢购] 手机发包 第{i + 1}枪 {ms}ms "
                    f"device={row.get('deviceId') or '-'} "
                    f"http={row.get('httpStatus')} code={row.get('code')} "
                    f"{row.get('message')}",
                    flush=True,
                )
                if isinstance(last, dict) and _rush_row_stop_rush(last):
                    break
                if i < total_try - 1:
                    wait_ms = gap_ms
                    if rr_n > 1 and max_try > 0 and (i + 1) % max_try == 0:
                        wait_ms = rrgap + (random.randint(0, rrjit) if rrjit > 0 else 0)
                    if wait_ms > 0:
                        time.sleep(wait_ms / 1000.0)
            if isinstance(last, dict):
                last["rushLoopTrace"] = tr
                last["rushLoopWallMs"] = int((time.perf_counter() - wall_t0) * 1000)
                last["rushTransport"] = "mobile_relay"
                last["mobileRelayDevice"] = str((tr[-1] if tr else {}).get("deviceId") or relay_device)
                ref_for_verify = (referer or "").strip() or getattr(self, "_last_rush_referer", "")
                if ref_for_verify:
                    last.setdefault("_rushReferer", ref_for_verify)
            print(f"[抢购] → {last}")
            return last

        if _use_node_rush_http():
            attempts: list[dict] = []
            try:
                _ensure_go_bridge_running()
                self._rush_guard_auth_proxy_for_single_hop()
                attempts = self._rush_build_node_attempt_packages(
                    build_try,
                    item,
                    act_id,
                    amount,
                    src,
                    branch,
                    referer,
                    need_code,
                    lane_count=node_lane_count,
                    shot_index_start=node_shot_index_start,
                )
                if getattr(self, "_rush_full_pool_sequence", False):
                    print(
                        f"[抢购] full_pool_rush：本轮 {len(attempts)} 档（Go 发包前从全局 local_proxy_pool 即时取线；"
                        f"遇 429 的 lane 下一枪立即避开刚才出口）",
                        flush=True,
                    )
                runtime_pool = self._rush_runtime_proxy_pool()
                _runtime_pool_n = len(runtime_pool)
                _rr_log = f"；⚡多轮 {rr_n} 轮×每轮 {max_try} 档、轮间≈{rrgap}±{rrjit}ms" if rr_n > 1 else ""
                print(
                    f"[抢购] 经 Node rush_loop 连发 {len(attempts)} 档"
                    f"（MT_RUSH_HTTP_NODE=1；"
                    f"{'运行时全局代理池=' + str(_runtime_pool_n) + ' 条；lane 首次发包借 IP+TLS 会话，业务正常保持长连接，空 429/非业务 429 后该 lane 立刻换 IP+TLS；' if runtime_pool else '固定出口按 attempt.proxyUrl 发送；'}"
                    f"详细轮换日志可设 MT_PROXY_LOG=1）{_rr_log}",
                    flush=True,
                )
                n_att = len(attempts)
                if rr_n > 1:
                    extra_sec = rr_n * max(0, max_try - 1) * gap_ms / 1000.0
                    extra_sec += (rr_n - 1) * (rrgap + rrjit) / 1000.0
                else:
                    extra_sec = max(0, n_att - 1) * gap_ms / 1000.0
                rush_ot = min(7200.0, max(180.0, 90.0 + n_att * 25.0 + extra_sec))
                if any(
                    isinstance(a, dict) and int(a.get("timeoutMs") or 0) <= 0
                    for a in attempts
                ):
                    rush_ot = 7200.0
                # 间隔>0 且多枪：每枪单独 Python→Node→茅台 往返，枪间在 Python sleep（贴近不勾选极速时的浏览器逐次请求，减轻限流）。
                # 间隔=0：仍一次 POST /rush_loop 内连发（极速极限）。
                per_shot_ot = min(180.0, max(75.0, 50.0 + 25.0))
                j: dict | None = None
                last = None
                tr: list | None = None
                j = _go_bridge_rush_loop_request(
                    attempts,
                    outer_timeout=rush_ot,
                    rush_loop_delay_ms=gap_ms,
                    tls_impersonate=TLS_PROFILE,
                    fire_and_forget=bool(fire_and_forget),
                    rush_rounds=rr_n,
                    rush_round_gap_ms=rrgap,
                    rush_round_gap_jitter_ms=rrjit,
                    lane_parallel=bool(rush_lane_parallel) and not bool(fire_and_forget),
                    runtime_proxy_pool=bool(runtime_pool),
                    proxy_pool=runtime_pool,
                )
                last = j.get("last")
                tr = _normalize_rush_loop_trace_rows(j.get("trace"))
                self._tls_bump_from_rush_trace(tr)
                if not isinstance(last, dict):
                    last = {
                        "code": -1,
                        "message": "Node rush_loop 返回无效 last",
                    }
                elif isinstance(tr, list) and tr:
                    for row in tr:
                        if isinstance(row, dict):
                            print(
                                "[抢购] rush_loop "
                                f"第{row.get('attempt')}次 {row.get('ms')}ms "
                                f"proxy={row.get('proxy') or '-'} "
                                f"http={row.get('httpStatus')} "
                                f"code={row.get('code')} "
                                f"{str(row.get('message') or '')[:100]}",
                                flush=True,
                            )
                    last = {**last, "rushLoopTrace": tr}
                if isinstance(last, dict):
                    ref_for_verify = (referer or "").strip() or getattr(self, "_last_rush_referer", "")
                    if ref_for_verify:
                        last.setdefault("_rushReferer", ref_for_verify)
                if isinstance(last, dict) and isinstance(j, dict):
                    wmv = j.get("wallMs")
                    if wmv is not None:
                        try:
                            last["rushLoopWallMs"] = int(round(float(wmv)))
                        except (TypeError, ValueError):
                            pass
                if j is not None and "lastProxyUrl" in j:
                    self._sync_shard_state_from_proxy_url(j["lastProxyUrl"])
            except Exception as ex:
                last = {
                    "code": -1,
                    "message": f"抢购请求异常: {ex}",
                }
            self._rush_attach_proxy_meta(last, attempts)
            print(f"[抢购] ← {last}")
            return last

        proxy_seq = self._rush_proxy_url_sequence(max_try)
        if len(proxy_seq) < max_try and proxy_seq:
            proxy_seq = proxy_seq + [proxy_seq[-1]] * (max_try - len(proxy_seq))
        for i in range(max_try):
            self._set_proxy_from_rotation(proxy_seq[i])
            url, h, body = self._rush_build_http_core(
                item, act_id, amount, src, branch, referer, need_code
            )
            r = None
            _rush_sleep_shot_jitter()
            try:
                r = self._req_post(
                    url,
                    headers=h,
                    json=body,
                    require_auth_proxy=True,
                    precheck_proxy=False,
                    max_proxy_tries=1,
                    timeout=0,
                )
            except Exception as ex:
                last = {
                    "code": -1,
                    "message": f"抢购请求异常: {ex}",
                }
            # 与 Node execRushLoop 一致：完整收到本枪 HTTP 响应后先按间隔等待，再解析 JSON / 再打下一枪
            if i < max_try - 1 and gap_ms > 0:
                time.sleep(gap_ms / 1000.0)
            if r is not None:
                last = self._normalize_rush_http_response(r)
            if isinstance(last, dict) and _rush_row_stop_rush(last):
                print(
                    f"[抢购] 检测到限购/停抢类提示，不再发射后续枪（已发 {i + 1}/{max_try}）",
                    flush=True,
                )
                break
            if i < max_try - 1:
                try:
                    nxt = proxy_seq[i + 1]
                    print(
                        f"[抢购] 第{i + 1}枪返回(code={last.get('code')}, msg={str(last.get('message') or '')[:40]}) "
                        f"→ 下一枪出口 {_proxy_hostport_for_log(nxt)} ({i + 2}/{max_try})"
                    )
                except Exception:
                    pass
        if getattr(self, "_proxy_per_account_chain", False):
            self._sync_shard_state_from_proxy_url(self._proxy_url)
        if isinstance(last, dict):
            ref_for_verify = (referer or "").strip() or getattr(self, "_last_rush_referer", "")
            if ref_for_verify:
                last.setdefault("_rushReferer", ref_for_verify)
        self._rush_attach_proxy_meta(last, None)
        print(f"[抢购] ← {last}")
        return last

    def black_account_probe(self, max_retries: int = 50) -> dict:
        """
        黑号检测：与 mt/test-full-flow.js testAccount 同规则。
        使用 741 飞天带杯 + branch/one + 固定备用活动 ID，发真实抢购探测请求（不保证能抢到）。
        返回字段：success, status(normal|token_expired|blacklisted|possibly_blacklisted|probe_error), message, mt_code?, http_status?, retry_count
        说明：4293+「当前下单人数较多…」标为 possibly_blacklisted（疑似）：发售期外也常出现，不等同永久黑号；4099/4003 仍为 blacklisted。
        HTTP 480 + 4030「购买活动信息异常」或文案「当前活动未开启」等为活动未开售态，标为 normal（非黑号）。

        探测请求走 _req_post 时不限制 max_proxy_tries（与池内轮询一致）：连接中断、预检失败、HTTP 429
        时会在代理池内换线重试，便于单号检测尽快碰到可用出口；日志可能出现多次「切换出口」。
        """
        item = "741"
        act_id = "72346"
        referer = f"{H5_HOST}/mt/item/smsp-detail?appConfig=2_1_2"
        path = "/xhr/front/trade/priority/rushPurchase/hot/branch/one"
        url = f"{H5_HOST}{path}"
        data = {
            "amount": "1",
            "itemCode": item,
            "itemPriorityActId": act_id,
            "userInfoBaseContext": {
                "addressLat": "",
                "addressLng": "",
                "appUserAgent": self._ua,
                "deviceId": self._dev_token,
                "mtr": self._env,
            },
            "ydLogId": "",
            "ydToken": "",
        }

        retry_count = 0
        while True:
            retry_count += 1
            body = {"actParam": seal_param(data)}
            h = self._h5_hdr(body, ref=referer, rush=True)
            try:
                r = self._req_post(
                    url,
                    headers=h,
                    json=body,
                )
            except Exception as ex:
                if retry_count < max_retries and retry_count < 5:
                    time.sleep(0.5)
                    continue
                return {
                    "success": False,
                    "status": "probe_error",
                    "message": str(ex),
                    "retry_count": retry_count,
                }

            http_status = r.status_code
            raw_text = (r.text or "").strip()

            # HTTP 480：茅台仍可能返回 JSON 业务体（如活动未开启），与单纯代理错误区分
            if http_status == 480:
                try:
                    j480 = r.json()
                except Exception:
                    j480 = None
                if isinstance(j480, dict):
                    c480 = j480.get("code")
                    m480 = str(j480.get("message") or "")
                    if _black_probe_benign_activity_msg(c480, m480):
                        return {
                            "success": True,
                            "status": "normal",
                            "message": m480 or "活动未开启或非开售窗口（接口可达，非黑号）",
                            "mt_code": c480,
                            "http_status": 480,
                            "retry_count": retry_count,
                        }
                return {
                    "success": False,
                    "status": "probe_error",
                    "message": (
                        f"HTTP 480，且非「活动未开启」类业务响应，无法判断黑号/正常。"
                        f"body[:180]={raw_text[:180]!r}"
                    ),
                    "http_status": 480,
                    "retry_count": retry_count,
                }

            # 仅 200 / 429 可解析其余业务含义；407/403/5xx 等多为代理或网关
            if http_status not in (200, 429):
                return {
                    "success": False,
                    "status": "probe_error",
                    "message": (
                        f"HTTP {http_status}，探测未到达 i茅台抢购接口（常见于代理 407 需认证、403 拦截或网关错误），"
                        f"无法判断黑号/正常。body[:180]={raw_text[:180]!r}"
                    ),
                    "http_status": http_status,
                    "retry_count": retry_count,
                }

            if http_status == 429:
                if raw_text in ("1", "2"):
                    if retry_count >= max_retries:
                        return {
                            "success": False,
                            "status": "possibly_blacklisted",
                            "message": f"已重试{max_retries}次，持续 HTTP 429 + 响应体 {raw_text}，疑似限制",
                            "mt_code": int(raw_text),
                            "http_status": 429,
                            "retry_count": retry_count,
                        }
                    time.sleep(0.3 + random.random() * 0.3)
                    continue
                try:
                    j429 = r.json()
                except Exception:
                    j429 = None
                if isinstance(j429, dict):
                    c = j429.get("code")
                    m429 = str(j429.get("message") or "")
                    if _black_probe_benign_activity_msg(c, m429):
                        return {
                            "success": True,
                            "status": "normal",
                            "message": m429 or "活动未开启或非开售窗口（接口可达，非黑号）",
                            "mt_code": c,
                            "http_status": 429,
                            "retry_count": retry_count,
                        }
                    if c == 4011:
                        return {
                            "success": False,
                            "status": "token_expired",
                            "message": "Token 失效（4011），请重新登录",
                            "mt_code": c,
                            "http_status": 429,
                            "retry_count": retry_count,
                        }
                    if c == 4293 and str(j429.get("message") or "") == "当前下单人数较多，请您稍后再试":
                        return {
                            "success": False,
                            "status": "possibly_blacklisted",
                            "message": (
                                "4293「当前下单人数较多」：发售结束或非抢购时段也常见，仅作疑似限制/繁忙，"
                                "请在开售窗口内再测；不等同永久黑号。"
                            ),
                            "mt_code": 4293,
                            "http_status": 429,
                            "retry_count": retry_count,
                        }
                    if c in (4099, 4003):
                        return {
                            "success": False,
                            "status": "blacklisted",
                            "message": str(j429.get("message") or f"黑号相关 code={c}"),
                            "mt_code": c,
                            "http_status": 429,
                            "retry_count": retry_count,
                        }
                return {
                    "success": False,
                    "status": "probe_error",
                    "message": f"HTTP 429 且非 1/2 简码体: {raw_text[:200]}",
                    "http_status": 429,
                    "retry_count": retry_count,
                }

            try:
                resp = r.json()
            except Exception:
                return {
                    "success": False,
                    "status": "probe_error",
                    "message": f"响应非 JSON: {raw_text[:300]}",
                    "http_status": http_status,
                    "retry_count": retry_count,
                }

            if isinstance(resp, list):
                return {
                    "success": False,
                    "status": "probe_error",
                    "message": "抢购接口返回数组，非预期",
                    "http_status": http_status,
                    "retry_count": retry_count,
                }
            if not isinstance(resp, dict):
                return {
                    "success": False,
                    "status": "probe_error",
                    "message": f"业务响应非 JSON 对象（无法按黑号规则解析）: {raw_text[:280]!r}",
                    "http_status": http_status,
                    "retry_count": retry_count,
                }

            code = resp.get("code")
            msg = str(resp.get("message") or "")
            if _black_probe_benign_activity_msg(code, msg):
                return {
                    "success": True,
                    "status": "normal",
                    "message": msg or "活动未开启或非开售窗口（接口可达，非黑号）",
                    "mt_code": code,
                    "http_status": http_status,
                    "retry_count": retry_count,
                }
            if "code" not in resp:
                return {
                    "success": False,
                    "status": "probe_error",
                    "message": "业务 JSON 缺少 code 字段，无法判定",
                    "http_status": http_status,
                    "retry_count": retry_count,
                }

            if code == 4293 and msg == "当前下单人数较多，请您稍后再试":
                return {
                    "success": False,
                    "status": "possibly_blacklisted",
                    "message": (
                        "4293「当前下单人数较多」：发售结束或非抢购时段也常见，仅作疑似限制/繁忙，"
                        "请在开售窗口内再测；不等同永久黑号。"
                    ),
                    "mt_code": 4293,
                    "http_status": http_status,
                    "retry_count": retry_count,
                }
            if code == 4011:
                return {
                    "success": False,
                    "status": "token_expired",
                    "message": "Token 失效（4011），请重新登录",
                    "mt_code": code,
                    "http_status": http_status,
                    "retry_count": retry_count,
                }
            if code == 4099:
                return {
                    "success": False,
                    "status": "blacklisted",
                    "message": "请求人数过多（4099，黑号）",
                    "mt_code": code,
                    "http_status": http_status,
                    "retry_count": retry_count,
                }
            if code == 4003:
                return {
                    "success": False,
                    "status": "blacklisted",
                    "message": msg or "账号被限制（4003，黑号）",
                    "mt_code": code,
                    "http_status": http_status,
                    "retry_count": retry_count,
                }

            print(f"[黑号检测] ← HTTP {http_status} code={code} retry={retry_count}")
            return {
                "success": True,
                "status": "normal",
                "message": msg or "账号正常",
                "mt_code": code,
                "http_status": http_status,
                "retry_count": retry_count,
            }

    # ---- 组单（H5 composeOrder，使用与抢购一致的 rush 头）----

    def _session_order_pool_rotate_meta(
        self, env_cap_name: str = "MT_COMPOSE_POOL_ROTATE_MAX"
    ) -> tuple[int, bool]:
        """代理池可用时的换线尝试上限 + 是否允许枪间 _rotate_next（与 compose 原逻辑一致）。"""
        direct_first = bool(getattr(self, "_force_order_chain_direct", False))
        can_rot = bool(getattr(self, "_use_proxy_pool", False))
        if direct_first and not can_rot and not getattr(self, "_account_explicit_direct", False):
            try:
                from proxy_pool import active_urls, pool_is_configured

                can_rot = bool(pool_is_configured() and active_urls())
            except Exception:
                can_rot = False
        if can_rot:
            try:
                from proxy_pool import pool_is_configured

                can_rot = pool_is_configured()
            except Exception:
                can_rot = False
        rm = 1
        if can_rot:
            try:
                from proxy_pool import active_urls

                au = active_urls()
                if au:
                    n = len(au)
                    try:
                        cap = int(os.environ.get(env_cap_name, str(n)) or n)
                    except (TypeError, ValueError):
                        cap = n
                    rm = max(1, min(cap, 500))
            except Exception:
                rm = 1
        if direct_first and can_rot:
            rm = min(rm + 1, 501)
        return rm, can_rot

    def _enable_proxy_pool_retry_after_direct_limit(self, log_prefix: str = "订单链路") -> bool:
        """直连被 429/限流挡住时，临时切到代理池第一条可用 IP 补打。"""
        if getattr(self, "_account_explicit_direct", False):
            return False
        try:
            from proxy_pool import active_urls, pool_is_configured

            if not pool_is_configured():
                return False
            urls = active_urls()
            if not urls:
                return False
            cur = (getattr(self, "_proxy_url", "") or "").strip()
            if cur not in urls:
                self._set_proxy_from_rotation(urls[0])
            self._use_proxy_pool = True
            self._proxy_per_account_chain = False
            self._proxy_rotation_chain = []
            self._force_order_chain_direct = False
            self._proxy_prewarm_ok_for = ""
            print(
                f"[{log_prefix}] 本机直连限流 → 切代理池出口 {_proxy_hostport_for_log(self._proxy_url)} 重试",
                flush=True,
            )
            return True
        except Exception as ex:
            print(f"[{log_prefix}] 本机直连限流，但代理池不可用: {ex}", flush=True)
            return False

    def _app_post_order_with_pool_rotate(self, *, url: str, log_prefix: str, **req_kw) -> dict:
        """下单/支付/网关：多枪换出口，直至业务 JSON 含 code 或用尽上限（与 compose 轮询方式一致）。
        不强制认证代理，允许与会话直连一致。"""
        if getattr(self, "_force_order_chain_direct", False):
            self.force_order_chain_direct()
        rm, can_rot = self._session_order_pool_rotate_meta()
        last_out: dict | None = None
        for i in range(rm):
            direct = bool(getattr(self, "_force_order_chain_direct", False))
            eg = _proxy_hostport_for_log("" if direct else (getattr(self, "_proxy_url", "") or ""))
            print(f"[{log_prefix}] 第{i + 1}/{rm}枪 出口={eg}", flush=True)
            try:
                r = self._req_post(
                    url,
                    use_proxy=not direct,
                    require_auth_proxy=False,
                    precheck_proxy=False,
                    max_proxy_tries=1,
                    **req_kw,
                )
            except Exception as ex:
                last_out = {
                    "code": -1,
                    "message": humanize_requests_error(ex),
                    "detail": str(ex)[:500],
                }
                if can_rot and i < rm - 1:
                    self._set_proxy_from_rotation(self._rotate_next_proxy())
                    self._proxy_prewarm_ok_for = ""
                    continue
                print(
                    f"[{log_prefix}] ← 异常 {str(last_out.get('message', ''))[:120]}",
                    flush=True,
                )
                return last_out
            res_obj = None
            try:
                res_obj = r.json()
            except Exception:
                pass
            if isinstance(res_obj, dict) and ("code" in res_obj):
                print(f"[{log_prefix}] ← code={res_obj.get('code')}", flush=True)
                return res_obj
            last_out = (
                res_obj
                if isinstance(res_obj, dict)
                else {
                    "code": r.status_code,
                    "message": "响应非 JSON",
                    "raw": (r.text or "")[:800],
                }
            )
            if direct and _http_response_should_proxy_retry(r):
                if self._enable_proxy_pool_retry_after_direct_limit(log_prefix) and i < rm - 1:
                    can_rot = True
                    continue
            if can_rot and i < rm - 1:
                self._set_proxy_from_rotation(self._rotate_next_proxy())
                self._proxy_prewarm_ok_for = ""
                continue
            break
        out = dict(last_out or {"code": -1, "message": f"{log_prefix}无有效响应"})
        print(f"[{log_prefix}] ← code={out.get('code')}", flush=True)
        return out

    def compose(
        self,
        spu,
        cnt,
        rec_id,
        addr,
        dm=1,
        store="0",
        shop="",  # 保留入参兼容；actParam.userPriorityInfo.shopId 与 Node 一致固定为 ""
        inv=0,
        rotate_max=None,
        use_rush_pin=None,
        trace_emit=None,
    ):
        """
        组单仅切回 App v2：/xhr/front/trade/order/standard/compose/v2
        其余请求链路保持当前实现不变。
        """
        if getattr(self, "_force_order_chain_direct", False):
            self.force_order_chain_direct()
            use_rush_pin = False
        self._last_rush_compose_data = None
        try:
            prid = int(rec_id)
        except (TypeError, ValueError):
            return {"code": -1, "message": f"priorityRecordId 无效: {rec_id!r}"}
        ship_id = addr.get("shipAddressId", 0)
        act_param_obj = {
            "deliverMethod": int(dm),
            "itemList": [{"storeId": str(store), "spuId": str(spu), "count": int(cnt)}],
            "addressInfo": {"shipAddressId": ship_id},
            "userPriorityInfo": {
                "priorityRecordId": prid,
                "shopId": "",
                "inventorySource": int(inv),
            },
            "selfLatitude": "",
            "selfLongitude": "",
        }
        act_param = self._act_param_aes(act_param_obj)
        request_body_obj = {
            "deliverMethod": int(dm),
            "addressInfo": {"shipAddressId": ship_id},
            "itemList": [{"storeId": str(store), "spuId": str(spu), "count": int(cnt)}],
            "actParam": act_param,
        }
        body_str = _json_min(request_body_obj)
        if use_rush_pin is None:
            use_rush_pin = (os.environ.get("MT_COMPOSE_USE_RUSH_PIN", "1") or "1").strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )
        else:
            use_rush_pin = bool(use_rush_pin)
        pin_url = (
            (getattr(self, "_compose_pin_proxy_url", "") or "").strip()
            if use_rush_pin
            else ""
        )
        if pin_url:
            rm, can_rot = 3, True
        else:
            rm, can_rot = self._session_order_pool_rotate_meta()
        if rotate_max is not None:
            try:
                rm = max(1, min(int(rotate_max), 500))
            except (TypeError, ValueError):
                pass

        trace: list[str] = []

        def _clog(line: str) -> None:
            trace.append(line)
            if trace_emit:
                try:
                    trace_emit(line)
                except Exception:
                    pass
            print(f"[组单/v2] {line}", flush=True)

        rid_tail = str(rec_id)[-6:] if len(str(rec_id)) > 6 else str(rec_id)
        if pin_url:
            _clog(
                "组单固定为抢购中号时的出口 "
                + _proxy_hostport_for_log(pin_url)
                + "（每轮请求前重新套用；其它接口仍可按池轮换）"
            )
        attempt_limit = rm
        _clog(
            f"开始 spu={spu} cnt={cnt} recId…{rid_tail} 本轮最多 {attempt_limit} 次 "
            f"可换出口={'是' if can_rot else '否'}；有业务 code 即返回、不换线重打"
        )

        last_out: dict | None = None
        url_c = f"{API_HOST}/xhr/front/trade/order/standard/compose/v2"
        attempts_done = 0

        for i in range(attempt_limit):
            direct = bool(getattr(self, "_force_order_chain_direct", False))
            if pin_url and i == 0:  # 只有第一次使用固定代理
                try:
                    self._proxy_url = _normalize_proxy_url(pin_url)
                except Exception:
                    self._proxy_url = pin_url
            attempts_done = i + 1
            eg = _proxy_hostport_for_log("" if direct else (getattr(self, "_proxy_url", "") or ""))
            _clog(f"第{i + 1}/{attempt_limit} 次 请求前 出口={eg}")
            h = self._app_order_hdr()
            try:
                r = self._req_post(
                    url_c,
                    use_proxy=not direct,
                    headers=h,
                    data=body_str.encode("utf-8"),
                    require_auth_proxy=False,
                    precheck_proxy=False,
                    max_proxy_tries=1,
                )
            except Exception as ex:
                msg = humanize_requests_error(ex)
                last_out = {
                    "code": -2,
                    "message": msg,
                    "detail": str(ex)[:500],
                    "proxy": eg,
                }
                _clog(f"第{i + 1}/{attempt_limit} 次 请求异常: {msg}")
                if i < attempt_limit - 1:
                    if direct and self._enable_proxy_pool_retry_after_direct_limit("组单/v2"):
                        can_rot = True
                        _clog("本机直连异常 → 使用代理池出口重试")
                        continue
                    if (not direct) and can_rot:
                        self._set_proxy_from_rotation(self._rotate_next_proxy())
                        self._proxy_prewarm_ok_for = ""
                        nxt = _proxy_hostport_for_log(self._proxy_url or "")
                        _clog(f"第{i + 1}/{attempt_limit} 次 异常后切换下一出口 → {nxt}")
                        continue
                break
            is_429 = r.status_code == 429
            is_network_error = False
            if r.status_code == 0 or "EOF" in (r.text or ""):
                is_network_error = True
            res_obj = None
            try:
                res_obj = r.json()
            except Exception:
                pass
            # 业务 JSON：含 code 即返回（含 4099），不因拥塞换出口重复请求。
            if isinstance(res_obj, dict) and ("code" in res_obj):
                biz_code = res_obj.get("code")
                msg_b = str(res_obj.get("message") or "").strip().replace("\n", " ")
                if len(msg_b) > 100:
                    msg_b = msg_b[:100] + "…"
                line = (
                    f"第{i + 1}/{attempt_limit} 次 返回 HTTP {r.status_code} 业务 code={biz_code}"
                    + (f" msg={msg_b}" if msg_b else "")
                )
                _clog(line)
                if biz_code == 2000 and isinstance(res_obj.get("data"), dict):
                    self._last_rush_compose_data = res_obj["data"]
                out = dict(res_obj)
                out["_composeTrace"] = trace
                _clog(f"结束 共尝试 {attempts_done} 次 最终业务 code={out.get('code')}")
                return out
            # 网络错误：允许切换出口重试
            if is_network_error:
                _clog(f"第{i + 1}/{attempt_limit} 次 网络错误: {r.text or 'EOF/连接失败'}")
            last_out = (
                res_obj
                if isinstance(res_obj, dict)
                else {
                    "code": r.status_code if not is_network_error else -2,
                    "message": "网络错误" if is_network_error else "响应非 JSON",
                    "raw": (r.text or "")[:800],
                }
            )
            raw_brief = (r.text or "").replace("\n", " ").strip()
            if len(raw_brief) > 120:
                raw_brief = raw_brief[:120] + "…"
            _clog(
                f"第{i + 1}/{attempt_limit} 次 返回 HTTP {r.status_code} "
                + ("429 限流/网关" if is_429 else "非 JSON 或需重试")
                + (f" body预览={raw_brief!r}" if raw_brief else " body空")
            )
            if direct and _http_response_should_proxy_retry(r):
                if self._enable_proxy_pool_retry_after_direct_limit("组单/v2") and i < attempt_limit - 1:
                    can_rot = True
                    _clog("本机直连限流 → 使用代理池出口重试")
                    continue
            # 网络错误时强制允许切换出口
            if (can_rot or is_network_error) and i < attempt_limit - 1:
                self._set_proxy_from_rotation(self._rotate_next_proxy())
                self._proxy_prewarm_ok_for = ""
                nxt = _proxy_hostport_for_log(self._proxy_url or "")
                _clog(f"第{i + 1}/{attempt_limit} 次 后 切换下一出口 → {nxt}")
                continue
            break

        out = dict(last_out or {"code": -1, "message": "组单无响应"})
        out["_composeTrace"] = trace
        _clog(
            f"结束 共尝试 {attempts_done} 次 已用尽或不可再轮换 最终 code={out.get('code')}"
        )
        return out

    # ---- 下单（App submit/v2，对齐 mt1.9.5/mt）----

    def submit(
        self,
        spu,
        cnt,
        rec_id,
        addr,
        dm=1,
        store="0",
        shop="",
        inv=0,
        compose_data=None,
        imtp_fallback="",
        use_rush_pin=None,
    ):
        if getattr(self, "_force_order_chain_direct", False):
            self.force_order_chain_direct()
            use_rush_pin = False
        try:
            prid = int(rec_id)
        except (TypeError, ValueError):
            return {"code": -1, "message": f"priorityRecordId 无效: {rec_id!r}"}
        cdata = compose_data
        if cdata is None:
            cdata = self._last_rush_compose_data
        if cdata is not None and not isinstance(cdata, dict):
            cdata = None

        compose_store_id = None
        transaction_id = None
        deliver_method_list = [
            {"type": 1, "desc": "快递配送", "notes": "", "checked": False}
        ]
        prom_id_list: list = []
        source_from_compose = ""

        if isinstance(cdata, dict) and cdata:
            up = cdata.get("userPriorityComposeResult") or {}
            ps = (up.get("priorityStoreInfo") or {})
            compose_store_id = ps.get("storeId")
            transaction_id = cdata.get("transactionId")
            deliver_method_list = cdata.get("deliverMethodList") or deliver_method_list
            prom_id_list = cdata.get("promIdList") or []
            source_from_compose = (cdata.get("sourceId") or "").strip()

        # 对齐 mt1.9.5/mt：无 transactionId 时生成 1_xxx_ms_xxx_uid
        shop_fb = (shop or "").strip()
        if not compose_store_id and shop_fb:
            compose_store_id = shop_fb
        if not transaction_id:
            rnd9 = random.randint(100000000, 999999999)
            rnd3 = random.randint(100, 999)
            ms = adjusted_unix_ms()
            uid_s = str(self._uid or "0").strip() or "0"
            transaction_id = f"1_{rnd9}_{ms}_{rnd3}_{uid_s}"

        if not compose_store_id:
            return {
                "code": -1,
                "message": (
                    "缺少门店 storeId：请先组单成功并传入 composeData，"
                    "或在无组单下单时提供 composeShopId（与 compose 返回的 priorityStoreInfo.storeId 一致）"
                ),
                "debug": (
                    {"keys": list(cdata.keys())[:20]}
                    if isinstance(cdata, dict) and cdata
                    else None
                ),
            }
        mobile_raw = (addr.get("mobile") or "").strip()
        masked = _mask_mobile(mobile_raw)
        address_info = {
            "address": "",
            "cityId": "",
            "cityName": "",
            "dft": False,
            "districtId": "",
            "districtName": "",
            "fullAddress": (addr.get("fullAddress") or ""),
            "mobile": masked,
            "name": (addr.get("name") or ""),
            "provinceId": "",
            "provinceName": "",
            "shipAddressId": addr.get("shipAddressId", 0),
            "townId": "",
            "townName": "",
            "userId": int(self._uid) if str(self._uid or "").isdigit() else 0,
            "grey": False,
            "recent": False,
            "longitude": "",
            "latitude": "",
            "province": "",
            "city": "",
            "district": "",
        }
        imtp = (imtp_fallback or "").strip()
        source_id = (source_from_compose or imtp or "").strip()

        act_param_obj = {
            "transactionId": transaction_id,
            "deliverMethod": 1,
            "itemList": [
                {"storeId": compose_store_id, "spuId": str(spu), "count": int(cnt)}
            ],
            "addressInfo": address_info,
            "payChannel": 0,
            "userPriorityInfo": {
                "priorityRecordId": prid,
                "shopId": compose_store_id,
                "inventorySource": 0,
            },
            "deliverMethodList": deliver_method_list,
            "promIdList": prom_id_list,
            "source": "",
            "sourceId": source_id,
            "ignorePromInconsistent": False,
        }
        act_param = self._act_param_aes(act_param_obj)
        request_body_obj = {
            "transactionId": transaction_id,
            "deliverMethod": 1,
            "itemList": [
                {"storeId": compose_store_id, "spuId": str(spu), "count": int(cnt)}
            ],
            "actParam": act_param,
            "addressInfo": address_info,
            "payChannel": 0,
            "source": "",
            "sourceId": source_id,
            "ignorePromInconsistent": False,
        }
        body_str = _json_min(request_body_obj)
        if use_rush_pin is None:
            use_rush_pin = (os.environ.get("MT_COMPOSE_USE_RUSH_PIN", "1") or "1").strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )
        else:
            use_rush_pin = bool(use_rush_pin)
        pin_url = (
            (getattr(self, "_compose_pin_proxy_url", "") or "").strip()
            if use_rush_pin
            else ""
        )
        if pin_url:
            try:
                self._proxy_url = _normalize_proxy_url(pin_url)
            except Exception:
                self._proxy_url = pin_url
            rm, can_rot = 1, False
        else:
            rm, can_rot = self._session_order_pool_rotate_meta()

        h = self._app_order_hdr()
        print(f"[下单/v2] txn={transaction_id}", flush=True)
        return self._app_post_order_with_pool_rotate(
            url=f"{API_HOST}/xhr/front/trade/order/standard/submit/v2",
            log_prefix="下单/v2",
            headers=h,
            data=body_str.encode("utf-8"),
        )

    # ---- 支付 ----

    def pay(self, oid, method=0, channel="70"):
        if getattr(self, "_force_order_chain_direct", False):
            self.force_order_chain_direct()
        # 支付通道与 requestPay 保持一致：按传入 channel 取 TN，避免「TN 来源通道」与 requestPay 的 PAY_CHANNEL 不一致。
        oid_s = str(oid)
        m = int(method)
        ch = str(channel or "70").strip() or "70"
        attempts = [
            ("channel+method", {"orderId": oid_s, "payChannel": ch, "payMethod": m}),
            ("channel", {"orderId": oid_s, "payChannel": ch}),
            ("method", {"orderId": oid_s, "payMethod": m}),
        ]
        self._order_pay_egress_for_gateway = None
        last = None
        for i, (tag, body) in enumerate(attempts):
            # 每轮重试前再钉一次：_app_post_order_with_pool_rotate 内部可能换线，避免 TN 与出口错位。
            self._apply_rush_success_proxy_for_payment()
            egress_used = "" if getattr(self, "_force_order_chain_direct", False) else (self._proxy_url or "").strip()
            body_str = _json_min(body)
            print(
                f"[支付] oid={oid_s} channel={ch} attempt={tag} 出口={_proxy_hostport_for_log(egress_used)}",
                flush=True,
            )
            res = self._app_post_order_with_pool_rotate(
                url=f"{API_HOST}/xhr/front/trade/order/pay",
                log_prefix="支付",
                headers=self._base_hdr(),
                data=body_str.encode("utf-8"),
            )
            last = res
            pd = res.get("data") if isinstance(res, dict) else None
            if isinstance(pd, dict):
                tn = str(
                    pd.get("channelTradeSn")
                    or pd.get("tn")
                    or pd.get("tradeNo")
                    or pd.get("payTn")
                    or pd.get("channel_trade_sn")
                    or ""
                ).strip()
                if tn:
                    self._order_pay_egress_for_gateway = egress_used
                    out = dict(res)
                    out_d = dict(pd)
                    out_d["channelTradeSn"] = tn
                    out["data"] = out_d
                    return out
            if i < len(attempts) - 1:
                time.sleep(0.4)
        return last if isinstance(last, dict) else {"code": -1, "message": "支付无有效响应"}

    def gateway(self, tn, ch="70"):
        if getattr(self, "_force_order_chain_direct", False):
            self.force_order_chain_direct()
        self._apply_rush_success_proxy_for_payment()
        slot = getattr(self, "_order_pay_egress_for_gateway", None)
        if (
            slot is not None
            and self._proxy_round_robin_enabled()
            and not getattr(self, "_pay_prefer_pool_rotate", False)
        ):
            s = str(slot).strip()
            self._proxy_url = _normalize_proxy_url(s) if s else ""
        ts = str(adjusted_unix_ms())
        ij = json.dumps({"PAY_CHANNEL": ch, "TN": tn}, separators=(",", ":"))
        sid = _md5(tn + ts + _PAY_KEY)
        try:
            ct = _sm4_ecb_enc(ts, _PAY_SM4)
        except Exception as ex:
            err = str(ex)
            print(f"[网关] SM4/构造失败: {err[:200]}")
            return {
                "code": -1,
                "message": "网关参数构造失败（SM4）",
                "detail": err[:500],
            }
        eg = "" if getattr(self, "_force_order_chain_direct", False) else (self._proxy_url or "").strip()
        print(f"[网关] 出口={_proxy_hostport_for_log(eg)} tn={tn[:30]}...", flush=True)
        res = self._app_post_order_with_pool_rotate(
            url=f"{PAY_HOST}/settle-api-server/anon/pay/requestPay",
            log_prefix="网关",
            data={
                "inJson": ij,
                "appId": _PAY_APPID,
                "securityID": sid,
                "cipherText": ct,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "Host": "payapi.moutai519.com.cn",
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip",
                "User-Agent": "okhttp/4.9.2",
            },
        )
        if isinstance(res, dict):
            # 排查 code=250「参数异常」时可直接在 pay_link 回包与 debug_last_api.json 中看到构造摘要。
            # 不写入完整密文，仅保留长度与前缀，避免泄露敏感参数。
            res = dict(res)
            res["_gatewayReqMeta"] = {
                "channel": str(ch),
                "tnTail": str(tn or "")[-16:],
                "timestampMs": ts,
                "inJson": ij,
                "appId": _PAY_APPID,
                "securityID": sid,
                "cipherTextLen": len(ct or ""),
                "cipherTextHead": str(ct or "")[:18],
            }
            return res
        return {"code": -1, "message": "网关返回异常", "raw": str(res)[:800]}

    def pay_result_notice(self, tn, ext_info="", resp_code="99", resp_msg="打开收银台"):
        """
        对齐抓包：POST /settle-api-server/anon/pay/payResultNotice
        form: inJson + cipherText + appId + securityID
        """
        ts = str(adjusted_unix_ms())
        in_json = json.dumps(
            {
                "EXT_INFO": str(ext_info or ""),
                "RESP_CODE": str(resp_code or "99"),
                "RESP_MSG": str(resp_msg or "打开收银台"),
                "TN": str(tn or ""),
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        sid = _md5(str(tn or "") + ts + _PAY_KEY)
        try:
            ct = _sm4_ecb_enc(ts, _PAY_SM4)
        except Exception as ex:
            err = str(ex)
            print(f"[支付回执] SM4/构造失败: {err[:200]}")
            return {"code": -1, "message": "支付回执参数构造失败（SM4）", "detail": err[:500]}
        res = self._app_post_order_with_pool_rotate(
            url=f"{PAY_HOST}/settle-api-server/anon/pay/payResultNotice",
            log_prefix="支付回执",
            data={
                "inJson": in_json,
                "appId": _PAY_APPID,
                "securityID": sid,
                "cipherText": ct,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "Host": "payapi.moutai519.com.cn",
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip",
                "User-Agent": "okhttp/4.9.2",
            },
        )
        if isinstance(res, dict):
            res = dict(res)
            res["_noticeReqMeta"] = {
                "tnTail": str(tn or "")[-16:],
                "timestampMs": ts,
                "inJson": in_json,
                "appId": _PAY_APPID,
                "securityID": sid,
                "cipherTextLen": len(ct or ""),
                "cipherTextHead": str(ct or "")[:18],
            }
            return res
        return {"code": -1, "message": "支付回执返回异常", "raw": str(res)[:800]}


# ==================== 主入口 ====================

    def submit_verify_code(
        self,
        priority_record_id: int,
        validate: str,
        captcha_id: str = _TTOCR_CAPTCHA_ID,
        referer: str = "",
        yidun_fp: str = "",
        item_code: str = "",
    ) -> dict:
        validate = str(validate or "").strip()
        if not validate:
            return {"code": -1, "message": "validate 不能为空"}
        ref = _yidun_referer_for_context(referer, item_code) or f"{H5_HOST}/mt/item/jpmt-detail"
        captcha_id = _yidun_captcha_id_for_context(
            captcha_id=captcha_id,
            referer=ref,
            item_code=item_code,
        )
        direct = bool(getattr(self, "_force_order_chain_direct", False))
        print(
            f"[验证] 提交 /verify/code priorityRecordId={priority_record_id} "
            f"出口={_proxy_hostport_for_log('' if direct else (getattr(self, '_proxy_url', '') or ''))}",
            flush=True,
        )
        url = f"{H5_HOST}/xhr/front/trade/priority/verify/code"
        body = {
            "captchaId": captcha_id,
            "validate": validate,
            "priorityUserRecordId": priority_record_id,
        }
        headers = self._h5_hdr_with_yidun_fp(body, ref=ref, yidun_fp=yidun_fp)
        try:
            r = self._req_post(
                url,
                headers=headers,
                json=body,
                timeout=30,
                use_proxy=not direct,
                require_auth_proxy=False,
                precheck_proxy=False,
                max_proxy_tries=1,
            )
            raw_text = getattr(r, "text", "") or ""
            result = r.json() if hasattr(r, "json") else json.loads(raw_text)
            try:
                data = result.get("data") if isinstance(result, dict) else None
                cookie_for_verify = str(headers.get("Cookie") or "")
                verify_log = {
                    "time": datetime.now().isoformat(timespec="milliseconds"),
                    "api": "/xhr/front/trade/priority/verify/code",
                    "httpStatus": getattr(r, "status_code", None),
                    "priorityRecordId": priority_record_id,
                    "captchaId": captcha_id,
                    "hasYidunFp": bool(str(yidun_fp or "").strip()),
                    "cookieHasGdx": "gdxidpyhxdE=" in cookie_for_verify,
                    "request": body,
                    "response": result,
                    "userVerifyStatus": data.get("userVerifyStatus") if isinstance(data, dict) else None,
                    "invalidTime": data.get("invalidTime") if isinstance(data, dict) else None,
                    "userVerifyInvalidTime": data.get("userVerifyInvalidTime") if isinstance(data, dict) else None,
                }
                log_dir = _ROOT / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                with open(log_dir / "verify_code_full.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(verify_log, ensure_ascii=False, separators=(",", ":")) + "\n")
            except Exception as log_ex:
                print(f"[验证] 写入 verify_code_full.jsonl 失败: {log_ex}", flush=True)
            data = result.get("data") if isinstance(result, dict) else None
            user_verify_status = data.get("userVerifyStatus") if isinstance(data, dict) else None
            print(f"[验证] /verify/code userVerifyStatus={user_verify_status}", flush=True)
            return result
        except Exception as ex:
            return {"code": -1, "message": f"/verify/code 请求失败: {ex}"}

    def verify_captcha(
        self,
        priority_record_id: int,
        captcha_id: str = _TTOCR_CAPTCHA_ID,
        max_attempts: int = 10,
        referer: str = "",
        item_code: str = "",
    ) -> dict:
        print(f"[验证] 开始易盾验证 priorityRecordId={priority_record_id}", flush=True)
        ref = _yidun_referer_for_context(referer, item_code) or getattr(self, "_last_rush_referer", "") or f"{H5_HOST}/mt/item/mm-zx-271-detail?appConfig=2_1_2"
        captcha_id = _yidun_captcha_id_for_context(
            captcha_id=captcha_id,
            referer=ref,
            item_code=item_code,
        )

        print("[验证] 优先尝试本地 OCR 易盾服务（固定直连）", flush=True)
        service_ok, service_err = _ensure_yidun_local_service_for_auto()
        if service_ok:
            local_result = _yidun_solve_via_local_ocr(
                captcha_id=captcha_id,
                referer=ref,
                priority_record_id=priority_record_id,
                item_code=item_code,
            )
        else:
            local_result = {"success": False, "message": service_err}
        if local_result.get("success") and local_result.get("validate"):
            fp = str(local_result.get("fp") or "").strip()
            validate = str(local_result["validate"])
            print(f"[验证] 本地OCR成功 validate={validate[:20]}... fpLen={len(fp)}", flush=True)
            return self.submit_verify_code(
                priority_record_id=priority_record_id,
                validate=validate,
                captcha_id=captcha_id,
                referer=ref,
                yidun_fp=fp,
                item_code=item_code,
            )
        print(f"[验证] 本地OCR不可用/未通过，回退 ttocr: {local_result.get('message', local_result)}", flush=True)

        validates: list[str] = []
        for attempt in range(1, max_attempts + 1):
            try:
                print(f"[验证] ttocr 第{attempt}/{max_attempts}次", flush=True)
                result = _ttocr_solve_yidun(captcha_id=captcha_id, referer=ref)
                if result.get("success") and result.get("validate"):
                    validate = str(result["validate"])
                    validates.append(validate)
                    print(f"[验证] ttocr 成功 validate={validate[:20]}...", flush=True)
                    break
                print(f"[验证] ttocr 未通过: {result.get('message', result)} 重试", flush=True)
            except Exception as ex:
                print(f"[验证] ttocr 请求异常: {ex} 重试", flush=True)

        if not validates:
            msg = f"ttocr {max_attempts}次未拿到 validate"
            print(f"[验证] 失败: {msg}", flush=True)
            return {"code": -1, "message": msg}

        return self.submit_verify_code(
            priority_record_id=priority_record_id,
            validate=validates[-1],
            captcha_id=captcha_id,
            referer=ref,
            item_code=item_code,
        )


if __name__ == "__main__":
    s = Session()
    print("=" * 50)
    print("客户端就绪")
    print("=" * 50)
    print(f"设备令牌: {s._dev_token}")
    print(f"原始ID:   {s._raw_id}")
    print(f"H5指纹:   {s._h5id}")
    print(f"UA:       {s._ua}")
    print("=" * 50)

    phone = input("\n手机号: ").strip()
    if not phone: print("空"); exit(1)

    print()
    cr = s.send_code(phone)
    if cr.get("code") != 2000: print(f"验证码失败: {cr}"); exit(1)

    code = input("\n验证码: ").strip()
    lr = s.login(phone, code)
    if lr.get("code") != 2000: exit(1)

    print("\n" + "=" * 50)
    addrs = s.addresses()
    if not addrs: print("无地址"); exit(1)
    sel = addrs[0] if len(addrs)==1 else addrs[int(input(f"\n选择[0-{len(addrs)-1}]: ").strip() or "0")]
    print(f"使用: id={sel['shipAddressId']} {sel.get('fullAddress','')}")

    print("\n" + "=" * 50)
    ic = input("商品编码: ").strip()
    ai = input("活动ID: ").strip()
    am = input("数量: ").strip() or "1"

    print()
    rr = None
    for i in range(1, 11):
        print(f"--- 第{i}/10次 ---")
        rr = s.rush(item=ic, act_id=ai, amount=am)
        if isinstance(rr, dict) and rr.get("code") == 2000: break
        time.sleep(0.5)

    if rr.get("code") == 2000:
        rid = rr.get("data",{}).get("priorityRecordId",0)
        print(f"\n[OK] recordId={rid}")
        if input("\n下单? (y/n): ").strip().lower() == "y":
            cnt = int(am)
            cr = s.compose(spu=ic, cnt=cnt, rec_id=rid, addr=sel)
            if cr.get("code") == 2000:
                sr = s.submit(
                    spu=ic,
                    cnt=cnt,
                    rec_id=rid,
                    addr=sel,
                    compose_data=cr.get("data"),
                )
                if sr.get("code") == 2000:
                    oid = str(sr.get("data",{}).get("orderId",""))
                    print(f"\n[下单成功] oid={oid}")
                    if input("支付? (y/n): ").strip().lower() == "y" and oid:
                        pr = s.pay(oid)
                        if pr.get("code") == 2000:
                            pd = pr.get("data") or {}
                            tn = str(pd.get("channelTradeSn") or pd.get("tn") or "").strip()
                            if tn:
                                gr = s.gateway(tn)
                                gc = gr.get("code")
                                gw_ok = gc in (200, 2000) or str(gc) in ("200", "2000")
                                if gw_ok:
                                    gd = gr.get("data")
                                    sdk = ""
                                    if isinstance(gd, str):
                                        sdk = gd.strip()
                                    elif isinstance(gd, dict):
                                        for k in ("payInfo", "alipay_sdk", "orderInfo", "AUTH_CODE"):
                                            v = gd.get(k)
                                            if v:
                                                sdk = str(v).strip()
                                                break
                                    if sdk:
                                        pay_url = mt_resolve_pay_url_from_sdk(
                                            sdk,
                                            proxy_kw=s._proxy_kw(),
                                            impersonate=TLS_PROFILE,
                                        )
                                        print(f"\n支付链接（与 mt 脚本同链路）:\n{pay_url}")
                                    else:
                                        print(f"\n网关无 SDK 字段: {gr}")
                                else:
                                    print(f"\n网关失败: {gr}")
                            else:
                                print(f"\n无TN: {pd}")
                            s._order_pay_egress_for_gateway = None
                        else:
                            print(f"支付失败: {pr}")
                else: print(f"\n下单失败: {sr}")
            else: print(f"\n组单失败: {cr}")
    else:
        print(f"\n未成功: {rr}")
