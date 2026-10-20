#!/usr/bin/env python3
"""对照 Python / Node 的 RC4 与 AES 封装（固定向量，不访问浩天网络）。"""

from __future__ import annotations

import base64
import gzip
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bs_dvid.crypto_util import aes_decrypt_cbc, aes_encrypt_cbc, md5_hash, rc4_encrypt


def _node_op(op: str, key: str, data_b64: str) -> bytes:
    js = f"""
const b = require({json.dumps(str(ROOT / "nodejs/bs-dvid/bs-dvid.js"))});
const key = Buffer.from({json.dumps(key)}, 'utf8');
const data = Buffer.from({json.dumps(data_b64)}, 'base64');
let out;
if ({json.dumps(op)} === 'rc4') out = b.rc4Encrypt(data, key);
else out = b.aesEncryptCbc(data, key);
process.stdout.write(JSON.stringify({{ b64: out.toString('base64') }}));
"""
    proc = subprocess.run(
        ["node", "-e", js],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return base64.b64decode(json.loads(proc.stdout)["b64"])


def main() -> int:
    sample = gzip.compress(b'{"hello":"world"}')
    key = b"abcdefghijklmnop"
    py_aes = aes_encrypt_cbc(sample, key)
    node_aes = _node_op("aes", key.decode(), base64.b64encode(sample).decode())
    if py_aes != node_aes:
        print("AES ciphertext mismatch")
        return 1

    rnd = key
    local_md5 = md5_hash("ABC123|0")
    py_rc4 = rc4_encrypt(rnd, local_md5.encode())
    node_rc4 = _node_op("rc4", local_md5, base64.b64encode(rnd).decode())
    if py_rc4 != node_rc4:
        print("RC4 ciphertext mismatch")
        return 1

    if aes_decrypt_cbc(py_aes, key) != sample:
        print("AES roundtrip failed")
        return 1

    print("OK: AES + RC4 match Node for sample vectors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
