from __future__ import annotations

import json
from typing import Any

from bangcle.sdk import (
    node_random_id,
    sm4_decrypt_cbc_hex,
    sm4_encrypt_cbc_hex,
    sm4_key,
    xor_hex_with_one,
)


def ensure_process_session(ctx: dict, now_ms: int | None = None) -> None:
    if ctx.get("smk") and ctx.get("smk1") and ctx.get("smi"):
        return
    now = int(now_ms if now_ms is not None else ctx.get("now") or 0)
    rand = ctx.get("_parity_random")
    smk = str(ctx.get("smk") or node_random_id(now, random_func=rand))
    ctx["smk"] = smk
    ctx["smk1"] = sm4_key(smk)
    ctx["smi"] = xor_hex_with_one(smk)


def process_data(ctx: dict, data: Any) -> str:
    task = ctx["sdkConfig"]["task"]
    if not task[3]:
        if isinstance(data, str):
            return data
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    ensure_process_session(ctx)
    plain = (
        data
        if isinstance(data, str)
        else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    )
    enc = sm4_encrypt_cbc_hex(plain, ctx["smk1"])
    return str(ctx["smi"]) + enc


def un_process_data(ctx: dict, cipher_text: str) -> Any:
    task = ctx["sdkConfig"]["task"]
    text = str(cipher_text or "")
    if not task[3]:
        return json.loads(text)
    ensure_process_session(ctx)
    smk1_len = len(str(ctx["smk1"]))
    mask = xor_hex_with_one(text[:smk1_len])
    plain = sm4_decrypt_cbc_hex(text[smk1_len:], sm4_key(mask))
    return json.loads(plain)


def sm4_encrypt_json_value(value: Any, app_key: str) -> str:
    plain = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return sm4_encrypt_cbc_hex(plain, sm4_key(app_key))


def sm4_decrypt_json_value(cipher_hex: str, app_key: str) -> Any:
    plain = sm4_decrypt_cbc_hex(cipher_hex, sm4_key(app_key))
    return json.loads(plain)
