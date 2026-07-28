#!/usr/bin/env python3
"""Distributed rush worker.

Run on a secondary machine that has the full project installed:
    python distributed_worker.py ws://MASTER_IP:8765
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

os.environ.setdefault("MT_DIST_MEMORY_ONLY", "1")
os.environ.setdefault("MT_DIST_RUSH_ONLY", "1")
print(f"[dist-worker] boot file={__file__}", flush=True)

PIP_INDEX_URLS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.org/simple",
]
WORKER_DEPENDENCIES: list[tuple[str, str]] = [
    ("websockets", "websockets>=13.0"),
    ("curl_cffi", "curl_cffi>=0.7.0"),
    ("requests", "requests>=2.28.0"),
    ("socks", "PySocks>=1.7.1"),
    ("gmssl", "gmssl>=3.2.1"),
    ("Crypto", "pycryptodome>=3.20.0"),
    ("wasmtime", "wasmtime>=24.0.0"),
    ("fastapi", "fastapi==0.68.0"),
    ("uvicorn", "uvicorn==0.15.0"),
    ("ddddocr", "ddddocr==1.5.5"),
    ("cv2", "opencv-python-headless"),
    ("numpy", "numpy"),
    ("PIL", "Pillow"),
    ("multipart", "python-multipart==0.0.5"),
]


def _dep_check_disabled() -> bool:
    return (os.environ.get("MT_DIST_SKIP_DEP_CHECK") or "").strip().lower() in ("1", "true", "yes", "on")


def _module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _run_pip_install(spec: str, index_url: str) -> bool:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-U",
        spec,
        "-i",
        index_url,
        "--trusted-host",
        index_url.replace("https://", "").replace("http://", "").split("/")[0],
    ]
    print(f"[dist-worker] 安装依赖 {spec}，镜像 {index_url}", flush=True)
    try:
        r = subprocess.run(cmd, text=True, capture_output=True, timeout=300)
    except Exception as ex:
        print(f"[dist-worker] pip 启动失败: {ex}", flush=True)
        return False
    if r.returncode == 0:
        return True
    tail = (r.stderr or r.stdout or "").strip().splitlines()[-6:]
    if tail:
        print("[dist-worker] pip 失败摘要:\n" + "\n".join(tail), flush=True)
    return False


def ensure_worker_dependencies() -> None:
    if _dep_check_disabled():
        print("[dist-worker] 已跳过依赖检查 MT_DIST_SKIP_DEP_CHECK=1", flush=True)
        return
    missing = [(mod, spec) for mod, spec in WORKER_DEPENDENCIES if not _module_available(mod)]
    if not missing:
        return
    print(
        "[dist-worker] 缺少依赖: " + ", ".join(spec for _, spec in missing) + "，开始自动安装",
        flush=True,
    )
    for mod, spec in missing:
        ok = False
        for index_url in PIP_INDEX_URLS:
            if _run_pip_install(spec, index_url):
                ok = _module_available(mod)
                if ok:
                    break
        if not ok:
            raise RuntimeError(f"依赖 {spec} 安装失败，请手动执行: {sys.executable} -m pip install -U {spec}")


ensure_worker_dependencies()

from accounts import POOL
import webui_server as webui

print(f"[dist-worker] webui_server file={getattr(webui, '__file__', '')}", flush=True)


def _dist_rush_only_mode() -> bool:
    return (os.environ.get("MT_DIST_RUSH_ONLY") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _local_yidun_status() -> dict:
    if _dist_rush_only_mode():
        return {
            "ok": True,
            "disabled": True,
            "reason": "rush-only node; yidun/compose runs on master",
            "localUrl": "",
            "localReady": False,
            "localScript": "",
            "localScriptExists": False,
            "ocrUrl": "",
            "ocrReady": False,
            "ocrDir": "",
            "ocrDirExists": False,
        }
    try:
        import app as mt_app
    except Exception as ex:
        return {"ok": False, "reason": f"app import failed: {ex}"}
    script = Path(str(getattr(mt_app, "_YIDUN_LOCAL_SCRIPT", "") or ""))
    ocr_dir = Path(str(getattr(mt_app, "_YIDUN_TUNED_OCR_DIR", "") or ""))
    local_url = str(getattr(mt_app, "_YIDUN_LOCAL_URL", "") or "")
    ocr_url = str(getattr(mt_app, "_YIDUN_LOCAL_OCR_URL", "") or "")
    script_exists = script.is_file()
    ocr_dir_exists = ocr_dir.is_dir()
    try:
        local_ready = bool(mt_app._yidun_local_ready(timeout=0.35))
    except Exception:
        local_ready = False
    try:
        ocr_ready = bool(mt_app._yidun_tuned_ocr_ready(timeout=0.35))
    except Exception:
        ocr_ready = False
    if not script_exists:
        reason = f"local yidun script missing: {script}"
    elif ocr_url == "http://127.0.0.1:8000/slide_match" and not ocr_dir_exists:
        reason = f"5.10 OCR directory missing: {ocr_dir}"
    elif local_url.endswith(":3000"):
        reason = "using fallback 127.0.0.1:3000, not bundled legacy service"
    elif not local_ready:
        reason = "local yidun service not listening yet"
    elif ocr_url and not ocr_ready:
        reason = "local OCR service not listening yet"
    else:
        reason = "ok"
    ok = script_exists and local_url.endswith(":8887") and (
        not ocr_url or ocr_url != "http://127.0.0.1:8000/slide_match" or ocr_dir_exists
    )
    return {
        "ok": bool(ok),
        "reason": reason,
        "localUrl": local_url,
        "localReady": bool(local_ready),
        "localScript": str(script),
        "localScriptExists": bool(script_exists),
        "ocrUrl": ocr_url,
        "ocrReady": bool(ocr_ready),
        "ocrDir": str(ocr_dir),
        "ocrDirExists": bool(ocr_dir_exists),
    }


def _prewarm_local_bridges() -> None:
    """Start local helper bridges before the first distributed job arrives."""

    def _run() -> None:
        t0 = time.perf_counter()
        try:
            webui.ensure_go_bridge_if_configured()
        except Exception as ex:
            print(f"[dist-worker] go_bridge 预热失败: {ex}", flush=True)
        try:
            import shield
            shield.compute_wasm_sig("h_test", "test")
            print("[dist-worker] stable shield prewarm done", flush=True)
        except Exception as ex:
            print(f"[dist-worker] stable shield prewarm failed: {ex}", flush=True)
        if _dist_rush_only_mode():
            print("[dist-worker] rush-only: skip local yidun/OCR prewarm", flush=True)
            ms = int((time.perf_counter() - t0) * 1000)
            print(f"[dist-worker] bridge prewarm done {ms}ms", flush=True)
            return
        try:
            import app as mt_app

            ok, msg = mt_app._ensure_yidun_local_service_for_auto()
            if ok:
                print("[dist-worker] local yidun/OCR 预热完成", flush=True)
            else:
                print(f"[dist-worker] local yidun/OCR 预热失败: {msg}", flush=True)
        except Exception as ex:
            print(f"[dist-worker] local yidun/OCR 预热异常: {ex}", flush=True)
        ms = int((time.perf_counter() - t0) * 1000)
        print(f"[dist-worker] 本机 bridge 预热完成 {ms}ms", flush=True)

    threading.Thread(target=_run, daemon=True, name="dist-worker-bridge-prewarm").start()


NODE_ID = (
    os.environ.get("MT_DIST_NODE_ID")
    or socket.gethostname()
    or f"node-{uuid.uuid4().hex[:8]}"
)
NODE_NAME = os.environ.get("MT_DIST_NODE_NAME") or socket.gethostname() or NODE_ID

_job_lock = threading.Lock()
_current_job_id = ""
_current_task_total = 0


def _apply_job_proxy_pool(lines: object, start_offset: object = 0) -> dict:
    """Use the master-sent proxy pool for this worker run."""
    if not isinstance(lines, list):
        lines = []
    try:
        offset = int(start_offset or 0)
    except (TypeError, ValueError):
        offset = 0
    clean: list[str] = []
    for raw in lines:
        s = str(raw or "").strip()
        if not s or s.startswith("#"):
            continue
        clean.append(s)
    try:
        import proxy_pool

        clean = proxy_pool.set_runtime_urls(clean, start_offset=offset)
    except Exception:
        pass
    return {
        "count": len(clean),
        "updated": True,
        "startOffset": offset % len(clean) if clean else 0,
    }


def _task_highfreq_count(tasks: object) -> int:
    if not isinstance(tasks, list):
        return 0
    fn = getattr(webui, "_fire_and_forget_from_payload", None)
    count = 0
    for t in tasks:
        if not isinstance(t, dict):
            continue
        try:
            ok = bool(fn(t)) if callable(fn) else bool(t.get("fireAndForget"))
        except Exception:
            ok = bool(t.get("fireAndForget"))
        count += 1 if ok else 0
    return count


def _cpu_percent_snapshot() -> float | None:
    try:
        if os.name == "nt":
            cmd = [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average",
            ]
            r = subprocess.run(cmd, text=True, capture_output=True, timeout=2)
            if r.returncode == 0:
                s = (r.stdout or "").strip()
                if s:
                    return round(float(s), 1)
        return None
    except Exception:
        return None


def _json_dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _stop_local_rush() -> None:
    try:
        webui._rush_global_abort_flag = True
    except Exception:
        pass
    try:
        webui._rush_global_abort_event.set()
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
        webui._reset_go_bridge_boot_state()
    except Exception:
        pass
    try:
        webui.schedule_bridge_rewarm_after_stop("distributed_worker_stop")
    except Exception:
        _prewarm_local_bridges()


async def _heartbeat(out_q: asyncio.Queue) -> None:
    while True:
        await asyncio.sleep(5)
        with _job_lock:
            task_total = int(_current_task_total or 0)
        await out_q.put(
            {
                "type": "heartbeat",
                "nodeId": NODE_ID,
                "ts": int(time.time()),
                "cpuPercent": _cpu_percent_snapshot(),
                "currentTasks": task_total,
                "caps": {"yidun": _local_yidun_status()},
            }
        )


async def _sender(ws, out_q: asyncio.Queue) -> None:
    while True:
        msg = await out_q.get()
        await ws.send(_json_dumps(msg))


async def _run_job(msg: dict, out_q: asyncio.Queue) -> None:
    global _current_job_id, _current_task_total
    job_id = str(msg.get("jobId") or "").strip()
    if not job_id:
        await out_q.put({"type": "job_error", "nodeId": NODE_ID, "jobId": "", "message": "missing jobId"})
        return
    with _job_lock:
        if _current_job_id:
            await out_q.put(
                {
                    "type": "job_error",
                    "nodeId": NODE_ID,
                    "jobId": job_id,
                    "message": f"节点正忙: {_current_job_id}",
                }
            )
            return
        _current_job_id = job_id

    loop = asyncio.get_running_loop()
    try:
        t_job0 = time.perf_counter()
        accounts = msg.get("accounts") if isinstance(msg.get("accounts"), list) else []
        tasks = msg.get("tasks") if isinstance(msg.get("tasks"), list) else []
        with _job_lock:
            _current_task_total = len(tasks)
        try:
            concurrency = max(1, int(msg.get("concurrency") or 1))
        except (TypeError, ValueError):
            concurrency = 1
        runtime_proxy_lines: list[str] = []
        raw_proxy_pool = msg.get("proxyPoolLines")
        if isinstance(raw_proxy_pool, list):
            runtime_proxy_lines = [
                str(u or "").strip()
                for u in raw_proxy_pool
                if str(u or "").strip()
            ]
        else:
            for a in accounts:
                if not isinstance(a, dict):
                    continue
                for u in a.get("proxy_shard_urls") or []:
                    s = str(u or "").strip()
                    if s:
                        runtime_proxy_lines.append(s)
        proxy_pool_sync = _apply_job_proxy_pool(
            runtime_proxy_lines,
            msg.get("proxyPoolStartOffset"),
        )
        loaded = POOL.load_memory_accounts(accounts)
        ready_ms = int((time.perf_counter() - t_job0) * 1000)
        highfreq_count = _task_highfreq_count(tasks)
        wasm_bb_count = sum(
            1
            for a in accounts
            if isinstance(a, dict) and bool(a.get("use_wasm_bb") or a.get("useWasmBb"))
        )
        if wasm_bb_count <= 0:
            bb_path = "shield_sm4_v1.0.8"
        elif wasm_bb_count >= len(accounts):
            bb_path = "shield_wasm_en_v1.0.8"
        else:
            bb_path = "mixed_shield_sm4_wasm_en_v1.0.8"
        print(
            f"[dist-worker] job {job_id} tasks={len(tasks)} highfreq={highfreq_count}/{len(tasks)} "
            f"concurrency={concurrency}",
            flush=True,
        )
        print(
            f"[dist-worker] bb_path={bb_path} accounts={len(accounts)}",
            flush=True,
        )
        await out_q.put(
            {
                "type": "event",
                "nodeId": NODE_ID,
                "jobId": job_id,
                "event": {
                    "type": "node_ready",
                    "ok": True,
                    "accounts": loaded.get("count"),
                    "tasks": len(tasks),
                    "concurrency": concurrency,
                    "actIdCount": sum(
                        1
                        for t in tasks
                        if isinstance(t, dict) and str(t.get("actId") or "").strip()
                    ),
                    "itemCount": sum(
                        1
                        for t in tasks
                        if isinstance(t, dict) and str(t.get("itemCode") or "").strip()
                    ),
                    "highfreqCount": highfreq_count,
                    "legacyBbCount": 0,
                    "wasmBbCount": wasm_bb_count,
                    "legacyBbEnvForced": False,
                    "bbPath": bb_path,
                    "spuIdCount": sum(
                        1
                        for t in tasks
                        if isinstance(t, dict) and str(t.get("spuId") or "").strip()
                    ),
                    "proxyPoolCount": proxy_pool_sync.get("count"),
                    "proxyPoolSynced": proxy_pool_sync.get("updated"),
                    "proxyPoolStartOffset": proxy_pool_sync.get("startOffset"),
                    "ms": ready_ms,
                },
            }
        )

        def emit(ev: dict) -> None:
            loop.call_soon_threadsafe(
                out_q.put_nowait,
                {
                    "type": "event",
                    "nodeId": NODE_ID,
                    "jobId": job_id,
                    "event": ev if isinstance(ev, dict) else {"type": "raw", "data": ev},
                },
            )

        def blocking_run() -> None:
            try:
                webui._rush_global_abort_flag = False
                try:
                    webui._rush_global_abort_event.clear()
                except Exception:
                    pass
                webui._run_batch_rush_stream_api(tasks, concurrency, emit)
            except Exception as ex:
                emit(
                    {
                        "type": "error",
                        "ok": False,
                        "message": str(ex).strip() or type(ex).__name__,
                    }
                )

        await asyncio.to_thread(blocking_run)
        await out_q.put({"type": "job_done", "nodeId": NODE_ID, "jobId": job_id, "ok": True})
    finally:
        try:
            webui.schedule_bridge_rewarm_after_stop("distributed_worker_job_end")
        except Exception:
            pass
        with _job_lock:
            if _current_job_id == job_id:
                _current_job_id = ""
                _current_task_total = 0


async def _connect_once(master_ws: str) -> None:
    global _current_job_id, _current_task_total
    import websockets

    out_q: asyncio.Queue = asyncio.Queue()
    async with websockets.connect(
        master_ws,
        max_size=128 * 1024 * 1024,
        ping_interval=20,
        ping_timeout=20,
    ) as ws:
        yidun_status = _local_yidun_status()
        yidun_status_for_log = {
            "localUrl": "",
            "localScriptExists": False,
            "ocrDirExists": False,
            "localReady": False,
            "ocrReady": False,
            "reason": "",
            **(yidun_status if isinstance(yidun_status, dict) else {}),
        }
        print(
            "[dist-worker] yidun localUrl={localUrl} scriptExists={localScriptExists} "
            "ocrDirExists={ocrDirExists} localReady={localReady} ocrReady={ocrReady} reason={reason}".format(
                **yidun_status_for_log
            ),
            flush=True,
        )
        await ws.send(
            _json_dumps(
                {
                    "type": "hello",
                    "nodeId": NODE_ID,
                    "name": NODE_NAME,
                    "pid": os.getpid(),
                    "caps": {
                        "python": sys.version.split()[0],
                        "platform": sys.platform,
                        "memoryOnlyAccounts": True,
                        "yidun": yidun_status,
                    },
                }
            )
        )
        sender_task = asyncio.create_task(_sender(ws, out_q))
        heartbeat_task = asyncio.create_task(_heartbeat(out_q))
        print(f"[dist-worker] 已连接主控 {master_ws} nodeId={NODE_ID}", flush=True)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(str(raw))
                except Exception:
                    continue
                typ = str(msg.get("type") or "").strip()
                if typ == "run_job":
                    asyncio.create_task(_run_job(msg, out_q))
                elif typ == "stop_job":
                    _stop_local_rush()
                    with _job_lock:
                        stopped_job_id = str(_current_job_id or msg.get("jobId") or "")
                        _current_job_id = ""
                        _current_task_total = 0
                    await out_q.put(
                        {
                            "type": "job_error",
                            "nodeId": NODE_ID,
                            "jobId": str(msg.get("jobId") or stopped_job_id),
                            "message": "节点已执行停止",
                        }
                    )
                elif typ == "hello_ack":
                    pass
        finally:
            sender_task.cancel()
            heartbeat_task.cancel()


async def main() -> None:
    master_ws = (
        sys.argv[1].strip()
        if len(sys.argv) > 1 and sys.argv[1].strip()
        else os.environ.get("MT_DIST_MASTER_WS", "ws://192.168.31.105:8765")
    )
    print(f"[dist-worker] master={master_ws} nodeId={NODE_ID}", flush=True)
    _prewarm_local_bridges()
    while True:
        try:
            await _connect_once(master_ws)
        except KeyboardInterrupt:
            raise
        except Exception as ex:
            print(f"[dist-worker] 连接断开，3秒后重连: {ex}", flush=True)
            await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[dist-worker] 已退出", flush=True)
