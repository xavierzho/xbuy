from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bangcle import sdk as py_bb_new


ACCOUNTS_PATH = os.path.join(str(BASE_DIR), "accounts.json")


def _accounts(limit: int) -> list[dict[str, Any]]:
    with open(ACCOUNTS_PATH, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    rows = data if isinstance(data, list) else data.get("accounts", [])
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("h5_did") and row.get("h5_sid") and row.get("uid"):
            out.append(row)
            if len(out) >= limit:
                break
    if not out:
        raise RuntimeError("no usable account in accounts.json")
    return out


def _body(i: int) -> dict[str, Any]:
    return {
        "actParam": f"bench-{i}",
        "itemId": "10941",
        "actId": f"{i:012d}",
    }


def _calc_one(row: dict[str, Any], i: int) -> int:
    out = py_bb_new.build_bb_headers_py(
        _body(i),
        appid=str(row.get("appid") or "10001"),
        devid=str(row.get("h5_did") or ""),
        sessid=str(row.get("h5_sid") or ""),
        uid=str(row.get("uid") or ""),
        ua=str(row.get("h5_user_agent") or ""),
        wasm_version=str(row.get("bb_sign_wasm_version") or py_bb_new.DEFAULT_SIGN_WASM_VERSION),
        wasm_base64=str(row.get("bb_sign_wasm_b64") or ""),
        wasm_ok=True,
    )
    return len(out.get("Content-Web-Bb") or "")


def _run_parallel(rows: list[dict[str, Any]], shots_per_account: int, workers: int) -> tuple[float, int]:
    tasks = []
    index = 0
    for row in rows:
        for _ in range(shots_per_account):
            index += 1
            tasks.append((row, index))
    t0 = time.perf_counter()
    total_len = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_calc_one, row, idx) for row, idx in tasks]
        for fut in concurrent.futures.as_completed(futures):
            total_len += int(fut.result())
    return (time.perf_counter() - t0) * 1000.0, total_len


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark py_bb_new parallel BB calculation.")
    parser.add_argument("--accounts", type=int, default=10)
    parser.add_argument("--shots", type=int, default=10)
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()

    rows = _accounts(max(1, args.accounts))
    py_bb_new.load_sign_wasm_bytes()
    warm_ms, _ = _run_parallel(rows[:1], 1, 1)
    ms, total_len = _run_parallel(rows, max(1, args.shots), max(1, args.workers))
    total = len(rows) * max(1, args.shots)
    print(
        json.dumps(
            {
                "ok": True,
                "accounts": len(rows),
                "shotsPerAccount": max(1, args.shots),
                "totalShots": total,
                "workers": max(1, args.workers),
                "warmMs": round(warm_ms, 2),
                "wallMs": round(ms, 2),
                "avgMsPerShot": round(ms / total, 2),
                "totalContentWebBbLen": total_len,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
