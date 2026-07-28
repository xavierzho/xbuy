from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any

import py_bb_new


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_PATH = os.path.join(BASE_DIR, "accounts.json")


def _first_account() -> dict[str, Any]:
    with open(ACCOUNTS_PATH, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    accounts = data if isinstance(data, list) else data.get("accounts", [])
    for account in accounts:
        did = account.get("h5_did") or account.get("devid") or account.get("did")
        sid = account.get("h5_sid") or account.get("sessid") or account.get("sid")
        uid = account.get("uid") or account.get("userId") or account.get("user_id")
        if did and sid and uid:
            return account
    raise RuntimeError("accounts.json has no account with h5_did/h5_sid/uid")


def _valid_accounts(limit: int) -> list[dict[str, Any]]:
    with open(ACCOUNTS_PATH, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    accounts = data if isinstance(data, list) else data.get("accounts", [])
    result = []
    for account in accounts:
        did = account.get("h5_did") or account.get("devid") or account.get("did")
        sid = account.get("h5_sid") or account.get("sessid") or account.get("sid")
        uid = account.get("uid") or account.get("userId") or account.get("user_id")
        if did and sid and uid:
            result.append(account)
            if len(result) >= limit:
                break
    if not result:
        raise RuntimeError("accounts.json has no account with h5_did/h5_sid/uid")
    return result


def _account_ctx(account: dict[str, Any], smk: str) -> dict[str, Any]:
    return {
        "appid": str(account.get("appid") or py_bb_new.DEFAULT_APPID),
        "devid": str(account.get("h5_did") or account.get("devid") or account.get("did") or ""),
        "sessid": str(account.get("h5_sid") or account.get("sessid") or account.get("sid") or ""),
        "uid": str(account.get("uid") or account.get("userId") or account.get("user_id") or ""),
        "ua": str(account.get("h5_user_agent") or account.get("user_agent") or account.get("ua") or ""),
        "hp": str(account.get("hp") or ""),
        "smk": smk,
        "smk1": "",
        "smi": "",
    }


def _run_node(node_bin: str, body: Any, ctx: dict[str, Any], now_ms: int, random_id: str, fixed_now: int) -> dict[str, Any]:
    script = r"""
const fs = require('fs');
const bbModule = require('./node_bridge/bb/bb_module.cjs');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
bbModule.bbSignPostForRush(payload.body, payload.ctx, payload.options)
  .then((result) => process.stdout.write(JSON.stringify(result)))
  .catch((error) => {
    process.stderr.write(error && error.stack ? error.stack : String(error));
    process.exit(1);
  });
"""
    prelude = f"Date.now=()=>{int(fixed_now)};\n"
    payload = {
        "body": body,
        "ctx": ctx,
        "options": {
            "now": now_ms,
            "randomId": random_id,
        },
    }
    proc = subprocess.run(
        [node_bin, "-e", prelude + script],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=BASE_DIR,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "node compare failed")
    return json.loads(proc.stdout)


def _run_node_batch(node_bin: str, cases: list[dict[str, Any]], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    script = r"""
const fs = require('fs');
const bbModule = require('./node_bridge/bb/bb_module.cjs');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
Promise.resolve()
  .then(() => bbModule.ensureRuntime())
  .then(async () => {
    const out = [];
    for (const item of payload.cases) {
      out.push(await bbModule.bbSignPostForRush(item.body, payload.ctx, {
        now: item.now,
        randomId: item.randomId,
      }));
    }
    process.stdout.write(JSON.stringify(out));
  })
  .catch((error) => {
    process.stderr.write(error && error.stack ? error.stack : String(error));
    process.exit(1);
  });
"""
    payload = {"cases": cases, "ctx": ctx}
    fixed_now = str(cases[0]["fixedNow"] if cases else 1888888888888)
    prelude = f"Date.now=()=>{fixed_now};\n"
    proc = subprocess.run(
        [node_bin, "-e", prelude + script],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=BASE_DIR,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "node batch compare failed")
    return json.loads(proc.stdout)


def _short(value: Any, length: int = 24) -> str:
    text = str(value or "")
    return text[:length] + ("..." if len(text) > length else "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare current node BB with the new Python BB implementation.")
    parser.add_argument("--node", default=shutil.which("node") or "node", help="node executable path")
    parser.add_argument("--now", type=int, default=1777777777777)
    parser.add_argument("--random-id", default="0123456789abcdef0123456789abcdef")
    parser.add_argument("--smk", default="fedcba9876543210fedcba9876543210")
    parser.add_argument("--cases", type=int, default=1, help="number of valid accounts to test")
    parser.add_argument("--node-prod-reuse", action="store_true", help="compare node_bridge production reused wasm runtime")
    args = parser.parse_args()

    accounts = _valid_accounts(max(1, args.cases))
    fixed_now = 1888888888888
    # Match node_bridge server runtime clock deterministically. Payload field
    # 111 still uses args.now; this only freezes Date.now seen inside run_wac.
    py_bb_new.time.time = lambda: fixed_now / 1000.0
    ok = True
    for index, account in enumerate(accounts, start=1):
        ctx = _account_ctx(account, args.smk)
        if args.node_prod_reuse:
            cases = [
                {
                    "body": {
                        "itemId": "10941",
                        "actId": f"{index:012d}-{shot:03d}",
                        "priorityRecordId": str(22235254 + index),
                        "count": shot,
                        "source": "py-node-bb-align-prod-reuse",
                    },
                    "now": args.now + index + shot,
                    "randomId": f"{shot:032x}"[-32:],
                    "fixedNow": fixed_now,
                }
                for shot in range(20)
            ]
            node_batch = _run_node_batch(args.node, cases, ctx)
        else:
            cases = [
                {
                    "body": {
                        "itemId": "10941",
                        "actId": f"{index:012d}",
                        "priorityRecordId": str(22235254 + index),
                        "count": index,
                        "source": "py-node-bb-align",
                    },
                    "now": args.now + index,
                    "randomId": args.random_id,
                }
            ]
            node_batch = [
                _run_node(
                    args.node,
                    cases[0]["body"],
                    ctx,
                    cases[0]["now"],
                    cases[0]["randomId"],
                    fixed_now,
                )
            ]

        print(f"case={index} account uid={ctx['uid']} did={_short(ctx['devid'], 12)} sid={_short(ctx['sessid'], 12)} shots={len(cases)}")
        for shot_index, case in enumerate(cases):
            py_result = py_bb_new.build_bb_headers_py(
                case["body"],
                appid=ctx["appid"],
                devid=ctx["devid"],
                sessid=ctx["sessid"],
                uid=ctx["uid"],
                ua=ctx["ua"],
                wasm_version=py_bb_new.DEFAULT_SIGN_WASM_VERSION,
                now_ms=case["now"],
                random_id=case["randomId"],
                smk=args.smk,
            )
            node_result = node_batch[shot_index]

            checks = [
                ("Content-Hh-Bb", py_result.get("Content-Hh-Bb"), node_result.get("Content-Hh-Bb")),
                ("randomId", py_result.get("randomId"), node_result.get("randomId")),
                ("sign", py_result.get("sign"), node_result.get("sign")),
                ("signWac", py_result.get("signWac"), node_result.get("signWac")),
                ("Sdk-Ver-Bb", py_result.get("Sdk-Ver-Bb"), node_result.get("Sdk-Ver-Bb")),
                ("Content-Web-Bb", py_result.get("Content-Web-Bb"), node_result.get("Content-Web-Bb")),
            ]
            print(f"shot={shot_index + 1} now={case['now']} randomId={case['randomId']} smk={args.smk}")
            for name, py_value, node_value in checks:
                same = py_value == node_value
                ok = ok and same
                print(
                    f"{'OK ' if same else 'BAD'} {name}: "
                    f"py_len={len(str(py_value or ''))} node_len={len(str(node_value or ''))} "
                    f"py={_short(py_value)} node={_short(node_value)}"
                )
            if not ok:
                print("PY_DEBUG:", json.dumps(py_result.get("_debug", {}), ensure_ascii=False, separators=(",", ":"))[:2000])
                return 1
    print(f"PASS py_bb_new matches current node BB for {len(accounts)} account input(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
