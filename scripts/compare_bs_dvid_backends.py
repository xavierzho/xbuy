#!/usr/bin/env python3
"""
BS-DVID 后端对照。

默认（--offline）：固定 fixture，Python build_bs_dvid_parity_artifacts 与 node parity_cli 深比较，无网络。
--live：同一 device_id 分别跑 Python / Node 全流程（需网络，token 字节不必相同）。

用法:
  python scripts/compare_bs_dvid_backends.py
  python scripts/compare_bs_dvid_backends.py --live [device_id]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests/fixtures/parity/bs_dvid_flow.json"


def _offline_parity() -> int:
    from bs_dvid.parity import build_bs_dvid_parity_artifacts

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    py_out = build_bs_dvid_parity_artifacts(fixture)
    proc = subprocess.run(
        ["node", "nodejs/bs-dvid/parity_cli.js"],
        input=json.dumps(fixture).encode("utf-8"),
        capture_output=True,
        cwd=str(ROOT),
        check=True,
    )
    node_out = json.loads(proc.stdout.decode("utf-8"))
    if py_out != node_out:
        print("OFFLINE parity: mismatch (see pytest tests/test_parity_bs_dvid.py)")
        return 1
    print("OFFLINE parity: OK (Py artifacts == Node parity_cli)")
    return 0


def _run(backend: str, device_id: str | None) -> dict:
    os.environ["MT_BS_DVID_BACKEND"] = backend
    # re-read backend on each call via env in fetch_bs_dvid_full
    return fetch_bs_dvid_full(device_id=device_id)


def main() -> int:
    args = [a for a in sys.argv[1:] if a]
    if not args or args[0] != "--live":
        return _offline_parity()
    device_id = args[1] if len(args) > 1 else "353456789012345"
    from bs_dvid import fetch_bs_dvid_full, final_bs_dvid_token
    py = _run("python", device_id)
    js = _run("node", device_id)

    def summary(label: str, r: dict) -> None:
        print(f"\n=== {label} (backend={r.get('backend')}) ===")
        print("success:", r.get("success"))
        if not r.get("success"):
            print("error:", r.get("error"))
            return
        tok = final_bs_dvid_token(r)
        print("token len:", len(tok))
        print("token prefix:", tok[:48] + "..." if len(tok) > 48 else tok)
        for k in (
            "reportSuccess",
            "configSuccess",
            "envReportSuccess",
            "sigRulesSuccess",
            "fingerprintSuccess",
            "tokenSuccess",
        ):
            if k in r:
                print(f"  {k}: {r[k]}")

    summary("Python", py)
    summary("Node", js)
    if py.get("success") and js.get("success"):
        print("\nNote: 随机指纹不同，token 字节不必相同；两者均应 tokenSuccess 且 len>=40。")
    return 0 if py.get("success") and js.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
