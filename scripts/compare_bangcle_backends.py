#!/usr/bin/env python3
"""
Bangcle warmup 后端对照。

默认（--offline）：固定 fixture，Python build_bangcle_warmup_parity_artifacts 与 parity_warmup.cjs 深比较，无网络。
--live：同一暖场入参分别跑 Python / Node 全流程（需网络，state 不必逐字节相同）。

用法:
  python scripts/compare_bangcle_backends.py
  python scripts/compare_bangcle_backends.py --live
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests/fixtures/parity/bangcle_warmup.json"


def _offline_parity() -> int:
    from bangcle.parity import build_bangcle_warmup_parity_artifacts

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    py_out = build_bangcle_warmup_parity_artifacts(fixture)
    proc = subprocess.run(
        ["node", "nodejs/bangcle/parity_warmup.cjs"],
        input=json.dumps(fixture).encode("utf-8"),
        capture_output=True,
        cwd=str(ROOT),
        check=True,
    )
    node_out = json.loads(proc.stdout.decode("utf-8"))
    if py_out != node_out:
        print("OFFLINE parity: mismatch (see pytest tests/test_parity_bangcle.py)")
        return 1
    print("OFFLINE parity: OK (Py artifacts == Node parity_warmup.cjs)")
    return 0


def _payload() -> dict:
    now = int(time.time() * 1000)
    return {
        "uid": "",
        "bbDid": f"h{now % 10**12}",
        "userAgent": (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        ),
        "referer": "https://h5.moutai519.com.cn/mt/item/hxxxxx",
        "proxyUrl": os.environ.get("MT_PROXY_URL") or "",
        "timeoutMs": int(os.environ.get("MT_BANGCLE_WARMUP_TIMEOUT_MS") or "12000"),
        "now": now,
    }


def _run(backend: str, payload: dict) -> dict:
    from bangcle import bangcle_warmup

    os.environ["MT_BANGCLE_BACKEND"] = backend
    return bangcle_warmup(dict(payload), timeout=25.0)


def _summary(label: str, r: dict) -> None:
    print(f"\n=== {label} (backend={r.get('backend')}) ===")
    print("success:", r.get("success"), "skipped:", r.get("skipped"))
    if not r.get("success"):
        print("message:", r.get("message"))
        return
    s = r.get("summary") if isinstance(r.get("summary"), dict) else {}
    print("summary:", json.dumps(s, ensure_ascii=False))
    st = r.get("state") if isinstance(r.get("state"), dict) else {}
    keys = (
        "bbDid",
        "bbStartId",
        "__bbWarmupAlg",
        "bbSignWasmVersion",
        "bbSerial",
        "bbTacticsVersion",
    )
    print("state:", {k: st.get(k) for k in keys})


def main() -> int:
    args = [a for a in sys.argv[1:] if a]
    if not args or args[0] != "--live":
        return _offline_parity()
    payload = _payload()
    py = _run("python", payload)
    js = _run("node", payload)
    _summary("Python", py)
    _summary("Node", js)
    def http_ok(r: dict) -> bool:
        s = r.get("summary") if isinstance(r.get("summary"), dict) else {}
        return all(
            200 <= int(s.get(k) or 0) < 300
            for k in ("strategyStatus", "versionStatus", "uploadStatus")
        )

    ok = all(bool(r.get("success")) and http_ok(r) for r in (py, js))
    if ok:
        print("\nOK: 两端 strategy/version/upload 均 2xx。did/startId 随机，state 不必逐字节相同。")
    else:
        print("\nFAIL: warmup 的 success 不看 HTTP 状态；以上 summary 中有非 2xx（如 403 多为 WAF 拦截）。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
