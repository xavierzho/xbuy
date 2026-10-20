#!/usr/bin/env bash
# 从 nodejs/bs-dvid/bs-dvid.js 刷新 bs_dvid/constants.py 里的 APP_KEY / 设备池等块
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export ROOT
python3 << 'PY'
import json
import os
import pprint
import subprocess
from pathlib import Path

ROOT = Path(os.environ["ROOT"])
proc = subprocess.run(
    [
        "node",
        "-e",
        f"const b=require({json.dumps(str(ROOT / 'nodejs/bs-dvid/bs-dvid.js'))});"
        "console.log(JSON.stringify({"
        "APP_KEY:b.APP_KEY,SEC_KEY:b.SEC_KEY,APP_SIGNATURE:b.APP_SIGNATURE,"
        "DEVICE_CONFIGS:b.DEVICE_CONFIGS,IME_SERVICES:b.IME_SERVICES,"
        "DNS_SERVERS:b.DNS_SERVERS,GATEWAY_IPS:b.GATEWAY_IPS}));",
    ],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
    check=True,
)
data = json.loads(proc.stdout)
pp = pprint.PrettyPrinter(width=100, sort_dicts=False, compact=True)
path = ROOT / "bs_dvid/constants.py"
text = path.read_text(encoding="utf-8")
start = text.index("APP_KEY = ")
end = text.index("\nBASE_URL = ")
block = f"""APP_KEY = {data['APP_KEY']!r}
SEC_KEY = {data['SEC_KEY']!r}
APP_SIGNATURE = {data['APP_SIGNATURE']!r}

DEVICE_CONFIGS: list[dict[str, str]] = {pp.pformat(data['DEVICE_CONFIGS'])}

IME_SERVICES: list[str] = {pp.pformat(data['IME_SERVICES'])}

DNS_SERVERS: list[str] = {pp.pformat(data['DNS_SERVERS'])}

GATEWAY_IPS: list[str] = {pp.pformat(data['GATEWAY_IPS'])}
"""
path.write_text(text[:start] + block + text[end:], encoding="utf-8")
print("updated", path, "devices", len(data["DEVICE_CONFIGS"]))
PY
