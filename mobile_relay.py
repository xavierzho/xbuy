#!/usr/bin/env python3
"""In-memory relay for sending prepared rushPurchase requests from a phone.

The PC still builds actParam, BB headers, cookies and parses the response.
The phone only polls a prepared HTTP task, sends it, then posts the raw result
back to this process.
"""

from __future__ import annotations

import queue
import os
import random
import threading
import time
import uuid
from typing import Any


class MobileRelayError(RuntimeError):
    pass


class MobileRelayTimeout(MobileRelayError):
    pass


_LOCK = threading.RLock()
_QUEUES: dict[str, queue.Queue] = {}
_WAITERS: dict[str, dict[str, Any]] = {}
_DEVICES: dict[str, dict[str, Any]] = {}
_DEVICE_SEMAPHORES: dict[str, threading.BoundedSemaphore] = {}


def normalize_device_id(value: object) -> str:
    return str(value or "").strip()[:80]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _device_queue(device_id: str) -> queue.Queue:
    q = _QUEUES.get(device_id)
    if q is None:
        q = queue.Queue()
        _QUEUES[device_id] = q
    return q


def _device_parallel_cap() -> int:
    raw = (os.environ.get("MT_MOBILE_RELAY_DEVICE_CAP") or "1").strip()
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = 1
    return max(1, min(16, v))


def _device_semaphore(device_id: str) -> threading.BoundedSemaphore:
    sem = _DEVICE_SEMAPHORES.get(device_id)
    if sem is None:
        sem = threading.BoundedSemaphore(_device_parallel_cap())
        _DEVICE_SEMAPHORES[device_id] = sem
    return sem


def touch_device(device_id: object, info: dict | None = None) -> dict:
    did = normalize_device_id(device_id)
    if not did:
        raise MobileRelayError("deviceId 不能为空")
    with _LOCK:
        q = _device_queue(did)
        snap = _DEVICES.get(did) or {"deviceId": did, "firstSeenAt": _now_ms()}
        snap["lastSeenAt"] = _now_ms()
        snap["online"] = True
        snap["queueSize"] = int(q.qsize())
        if isinstance(info, dict):
            for key in ("name", "model", "ua", "client"):
                val = str(info.get(key) or "").strip()
                if val:
                    snap[key] = val[:240]
        _DEVICES[did] = snap
        return dict(snap)


def snapshot(max_age_ms: int = 30_000) -> dict:
    now = _now_ms()
    with _LOCK:
        devices = []
        for did, item in _DEVICES.items():
            row = dict(item)
            last = int(row.get("lastSeenAt") or 0)
            row["ageMs"] = max(0, now - last) if last else None
            row["online"] = bool(last and now - last <= int(max_age_ms))
            q = _QUEUES.get(did)
            row["queueSize"] = int(q.qsize()) if q is not None else 0
            devices.append(row)
        devices.sort(key=lambda x: int(x.get("lastSeenAt") or 0), reverse=True)
        return {"devices": devices, "deviceCount": len(devices), "nowMs": now}


def choose_online_device(max_age_ms: int = 30_000) -> str:
    now = _now_ms()
    with _LOCK:
        candidates: list[tuple[int, float, str]] = []
        for did, item in _DEVICES.items():
            last = int(item.get("lastSeenAt") or 0)
            if not last or now - last > int(max_age_ms):
                continue
            q = _QUEUES.get(did)
            qsize = int(q.qsize()) if q is not None else 0
            candidates.append((qsize, random.random(), did))
        if not candidates:
            raise MobileRelayError("没有在线手机发包设备")
        candidates.sort()
        best_queue_size = candidates[0][0]
        best = [did for qsize, _rnd, did in candidates if qsize == best_queue_size]
        return random.choice(best)


def _require_recent_device(device_id: str, max_age_ms: int = 30_000) -> None:
    with _LOCK:
        dev = _DEVICES.get(device_id)
        if not dev:
            raise MobileRelayError(f"手机发包设备未在线: {device_id}")
        age = _now_ms() - int(dev.get("lastSeenAt") or 0)
        if age > int(max_age_ms):
            raise MobileRelayError(f"手机发包设备离线 {age}ms: {device_id}")


def dispatch_http(
    device_id: object,
    attempt: dict,
    *,
    phone: str = "",
    timeout_sec: float = 45.0,
) -> dict:
    did = normalize_device_id(device_id)
    if not did:
        did = choose_online_device()
    if not isinstance(attempt, dict):
        raise MobileRelayError("手机发包任务格式错误")
    _require_recent_device(did)
    task_id = uuid.uuid4().hex
    ev = threading.Event()
    task = {
        "id": task_id,
        "kind": "rush_http",
        "phone": str(phone or "").strip(),
        "attempt": attempt,
        "createdAt": _now_ms(),
    }
    wait_total = max(1.0, float(timeout_sec or 45.0))
    deadline = time.monotonic() + wait_total
    sem = _device_semaphore(did)
    acquired = sem.acquire(timeout=min(wait_total, 30.0))
    if not acquired:
        raise MobileRelayTimeout(f"手机发包设备繁忙: {did}")
    try:
        with _LOCK:
            _WAITERS[task_id] = {
                "deviceId": did,
                "event": ev,
                "result": None,
                "createdAt": task["createdAt"],
            }
            _device_queue(did).put(task)
        ok = ev.wait(max(1.0, deadline - time.monotonic()))
        with _LOCK:
            waiter = _WAITERS.pop(task_id, None)
    finally:
        sem.release()
    if not ok or waiter is None:
        raise MobileRelayTimeout(f"手机发包等待响应超时: {did}")
    result = waiter.get("result")
    if not isinstance(result, dict):
        raise MobileRelayError("手机发包返回格式错误")
    if result.get("error"):
        raise MobileRelayError(str(result.get("error"))[:500])
    result.setdefault("deviceId", did)
    return result


def poll_task(device_id: object, wait_ms: int = 20_000, info: dict | None = None) -> dict:
    did = normalize_device_id(device_id)
    if not did:
        raise MobileRelayError("deviceId 不能为空")
    touch_device(did, info)
    wait_s = max(0.1, min(60.0, int(wait_ms or 20_000) / 1000.0))
    q = _device_queue(did)
    try:
        task = q.get(timeout=wait_s)
    except queue.Empty:
        return {}
    touch_device(did, info)
    return task if isinstance(task, dict) else {}


def complete_task(device_id: object, task_id: object, result: dict) -> bool:
    did = normalize_device_id(device_id)
    tid = str(task_id or "").strip()
    if not did or not tid:
        return False
    if not isinstance(result, dict):
        result = {"error": "result must be object"}
    touch_device(did)
    with _LOCK:
        waiter = _WAITERS.get(tid)
        if not waiter or waiter.get("deviceId") != did:
            return False
        waiter["result"] = result
        ev = waiter.get("event")
    if isinstance(ev, threading.Event):
        ev.set()
    return True
