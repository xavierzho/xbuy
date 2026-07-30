from __future__ import annotations

import base64
import json
import math
import os
import random
import threading
import time
from typing import Any


SDK_VERSION = "V3.5.0_20260721.2_imaotai"
DEFAULT_APPID = "10001"
DEFAULT_SIGN_WASM_VERSION = "v1.0.8"

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_BB_DIR = os.path.join(_BASE_DIR, "node_bridge", "bb")
_RUNTIME_WASM_PATH = os.path.join(_BB_DIR, "runtime.wasm")
_SIGN_WASM_PATH = os.path.join(_BB_DIR, "sign_wasm.bin")


def _add64(a: list[int], b: list[int]) -> list[int]:
    a = [a[0] >> 16, a[0] & 0xFFFF, a[1] >> 16, a[1] & 0xFFFF]
    b = [b[0] >> 16, b[0] & 0xFFFF, b[1] >> 16, b[1] & 0xFFFF]
    c = [0, 0, 0, 0]
    c[3] = a[3] + b[3]
    c[2] = a[2] + b[2] + (c[3] >> 16)
    c[3] &= 0xFFFF
    c[1] = a[1] + b[1] + (c[2] >> 16)
    c[2] &= 0xFFFF
    c[0] = a[0] + b[0] + (c[1] >> 16)
    c[1] &= 0xFFFF
    c[0] &= 0xFFFF
    return [(c[0] << 16) | c[1], (c[2] << 16) | c[3]]


def _mul64(a: list[int], b: list[int]) -> list[int]:
    a = [a[0] >> 16, a[0] & 0xFFFF, a[1] >> 16, a[1] & 0xFFFF]
    b = [b[0] >> 16, b[0] & 0xFFFF, b[1] >> 16, b[1] & 0xFFFF]
    c = [0, 0, 0, 0]
    c[3] += a[3] * b[3]
    c[2] += c[3] >> 16
    c[3] &= 0xFFFF
    c[2] += a[2] * b[3]
    c[1] += c[2] >> 16
    c[2] &= 0xFFFF
    c[2] += a[3] * b[2]
    c[1] += c[2] >> 16
    c[2] &= 0xFFFF
    c[1] += a[1] * b[3]
    c[0] += c[1] >> 16
    c[1] &= 0xFFFF
    c[1] += a[2] * b[2]
    c[0] += c[1] >> 16
    c[1] &= 0xFFFF
    c[1] += a[3] * b[1]
    c[0] += c[1] >> 16
    c[1] &= 0xFFFF
    c[0] += a[0] * b[3] + a[1] * b[2] + a[2] * b[1] + a[3] * b[0]
    c[0] &= 0xFFFF
    return [(c[0] << 16) | c[1], (c[2] << 16) | c[3]]


def _rot64(a: list[int], b: int) -> list[int]:
    b %= 64
    if b == 32:
        return [a[1], a[0]]
    if b < 32:
        return [
            ((a[0] << b) | (a[1] >> (32 - b))) & 0xFFFFFFFF,
            ((a[1] << b) | (a[0] >> (32 - b))) & 0xFFFFFFFF,
        ]
    b -= 32
    return [
        ((a[1] << b) | (a[0] >> (32 - b))) & 0xFFFFFFFF,
        ((a[0] << b) | (a[1] >> (32 - b))) & 0xFFFFFFFF,
    ]


def _xor64(a: list[int], b: list[int]) -> list[int]:
    return [(a[0] ^ b[0]) & 0xFFFFFFFF, (a[1] ^ b[1]) & 0xFFFFFFFF]


def _shl64(a: list[int], b: int) -> list[int]:
    if b == 0:
        return a
    if b < 32:
        return [((a[1] << b) >> 32) & 0xFFFFFFFF, (a[1] << b) & 0xFFFFFFFF]
    return [(a[1] << (b - 32)) & 0xFFFFFFFF, 0]


def _mix64(a: list[int]) -> list[int]:
    a = _xor64(a, [0, a[0] >> 1])
    a = _mul64(a, [0xFF51AFD7, 0xED558CCD])
    a = _xor64(a, [0, a[0] >> 1])
    a = _mul64(a, [0xC4CEB9FE, 0x1A85EC53])
    a = _xor64(a, [0, a[0] >> 1])
    return a


def _to_utf8(value: Any) -> list[int]:
    if isinstance(value, str):
        return list(value.encode("utf-8"))
    return list(value)


def fingerprint_hash(data: Any, seed: int = 0, cap: int | None = None, tail: str | None = None) -> str:
    t = _to_utf8(data)
    if cap:
        t = t[:cap]
    if tail:
        t += _to_utf8(tail)

    rem = len(t) % 16
    body = len(t) - rem
    h1, h2 = [0, seed], [0, seed]
    c1, c2 = [0x87C37B91, 0x114253D5], [0x4CF5AD43, 0x2745937F]
    d = 0
    while d < body:
        k1 = [
            (255 & t[d + 4])
            | ((255 & t[d + 5]) << 8)
            | ((255 & t[d + 6]) << 16)
            | ((255 & t[d + 7]) << 24),
            (255 & t[d])
            | ((255 & t[d + 1]) << 8)
            | ((255 & t[d + 2]) << 16)
            | ((255 & t[d + 3]) << 24),
        ]
        k2 = [
            (255 & t[d + 12])
            | ((255 & t[d + 13]) << 8)
            | ((255 & t[d + 14]) << 16)
            | ((255 & t[d + 15]) << 24),
            (255 & t[d + 8])
            | ((255 & t[d + 9]) << 8)
            | ((255 & t[d + 10]) << 16)
            | ((255 & t[d + 11]) << 24),
        ]
        k1 = _mul64(k1, c1)
        k1 = _rot64(k1, 31)
        k1 = _mul64(k1, c2)
        h1 = _xor64(h1, k1)
        h1 = _rot64(h1, 27)
        h1 = _add64(h1, h2)
        h1 = _add64(_mul64(h1, [0, 5]), [0, 0x52DCE729])
        k2 = _mul64(k2, c2)
        k2 = _rot64(k2, 33)
        k2 = _mul64(k2, c1)
        h2 = _xor64(h2, k2)
        h2 = _rot64(h2, 31)
        h2 = _add64(h2, h1)
        h2 = _add64(_mul64(h2, [0, 5]), [0, 0x38495AB5])
        d += 16

    k1, k2 = [0, 0], [0, 0]
    if rem >= 15:
        k2 = _xor64(k2, _shl64([0, t[d + 14]], 48))
    if rem >= 14:
        k2 = _xor64(k2, _shl64([0, t[d + 13]], 40))
    if rem >= 13:
        k2 = _xor64(k2, _shl64([0, t[d + 12]], 32))
    if rem >= 12:
        k2 = _xor64(k2, _shl64([0, t[d + 11]], 24))
    if rem >= 11:
        k2 = _xor64(k2, _shl64([0, t[d + 10]], 16))
    if rem >= 10:
        k2 = _xor64(k2, _shl64([0, t[d + 9]], 8))
    if rem >= 9:
        k2 = _xor64(k2, [0, t[d + 8]])
        k2 = _mul64(k2, c2)
        k2 = _rot64(k2, 33)
        k2 = _mul64(k2, c1)
        h2 = _xor64(h2, k2)
    if rem >= 8:
        k1 = _xor64(k1, _shl64([0, t[d + 7]], 56))
    if rem >= 7:
        k1 = _xor64(k1, _shl64([0, t[d + 6]], 48))
    if rem >= 6:
        k1 = _xor64(k1, _shl64([0, t[d + 5]], 40))
    if rem >= 5:
        k1 = _xor64(k1, _shl64([0, t[d + 4]], 32))
    if rem >= 4:
        k1 = _xor64(k1, _shl64([0, t[d + 3]], 24))
    if rem >= 3:
        k1 = _xor64(k1, _shl64([0, t[d + 2]], 16))
    if rem >= 2:
        k1 = _xor64(k1, _shl64([0, t[d + 1]], 8))
    if rem >= 1:
        k1 = _xor64(k1, [0, t[d]])
        k1 = _mul64(k1, c1)
        k1 = _rot64(k1, 31)
        k1 = _mul64(k1, c2)
        h1 = _xor64(h1, k1)

    h1 = _xor64(h1, [0, len(t)])
    h2 = _xor64(h2, [0, len(t)])
    h1 = _add64(h1, h2)
    h2 = _add64(h2, h1)
    h1 = _mix64(h1)
    h2 = _mix64(h2)
    h1 = _add64(h1, h2)
    h2 = _add64(h2, h1)
    return (
        format(h1[0] & 0xFFFFFFFF, "08x")
        + format(h1[1] & 0xFFFFFFFF, "08x")
        + format(h2[0] & 0xFFFFFFFF, "08x")
        + format(h2[1] & 0xFFFFFFFF, "08x")
    )


_S = [
    214, 144, 233, 254, 204, 225, 61, 183, 22, 182, 20, 194, 40, 251, 44, 5,
    43, 103, 154, 118, 42, 190, 4, 195, 170, 68, 19, 38, 73, 134, 6, 153,
    156, 66, 80, 244, 145, 239, 152, 122, 51, 84, 11, 67, 237, 207, 172, 98,
    228, 179, 28, 169, 201, 8, 232, 149, 128, 223, 148, 250, 117, 143, 63, 166,
    71, 7, 167, 252, 243, 115, 23, 186, 131, 89, 60, 25, 230, 133, 79, 168,
    104, 107, 129, 178, 113, 100, 218, 139, 248, 235, 15, 75, 112, 86, 157, 53,
    30, 36, 14, 94, 99, 88, 209, 162, 37, 34, 124, 59, 1, 33, 120, 135,
    212, 0, 70, 87, 159, 211, 39, 82, 76, 54, 2, 231, 160, 196, 200, 158,
    234, 191, 138, 210, 64, 199, 56, 181, 163, 247, 242, 206, 249, 97, 21, 161,
    224, 174, 93, 164, 155, 52, 26, 85, 173, 147, 50, 48, 245, 140, 177, 227,
    29, 246, 226, 46, 130, 102, 202, 96, 192, 41, 35, 171, 13, 83, 78, 111,
    213, 219, 55, 69, 222, 253, 142, 47, 3, 255, 106, 114, 109, 108, 91, 81,
    141, 27, 175, 146, 187, 221, 188, 127, 17, 217, 92, 65, 31, 16, 90, 216,
    10, 193, 49, 136, 165, 205, 123, 189, 45, 116, 208, 18, 184, 229, 180, 176,
    137, 105, 151, 74, 12, 150, 119, 126, 101, 185, 241, 9, 197, 110, 198, 132,
    24, 240, 125, 236, 58, 220, 77, 32, 121, 238, 95, 62, 215, 203, 57, 72,
]

_CK = [
    462357, 472066609, 943670861, 1415275113, 1886879365, 2358483617,
    2830087869, 3301692121, 3773296373, 4228057617, 404694573, 876298825,
    1347903077, 1819507329, 2291111581, 2762715833, 3234320085, 3705924337,
    4177462797, 337322537, 808926789, 1280531041, 1752135293, 2223739545,
    2695343797, 3166948049, 3638552301, 4110090761, 269950501, 741554753,
    1213159005, 1684763257,
]

_FK = [0xA3B1BAC6, 0x56AA3350, 0x677D9197, 0xB27022DC]


def _rl32(x: int, n: int) -> int:
    n &= 31
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _sb32(x: int) -> int:
    return (
        ((_S[(x >> 24) & 0xFF] & 0xFF) << 24)
        | ((_S[(x >> 16) & 0xFF] & 0xFF) << 16)
        | ((_S[(x >> 8) & 0xFF] & 0xFF) << 8)
        | (_S[x & 0xFF] & 0xFF)
    )


def _tf(x: int) -> int:
    b = _sb32(x)
    return b ^ _rl32(b, 2) ^ _rl32(b, 10) ^ _rl32(b, 18) ^ _rl32(b, 24)


def _tp(x: int) -> int:
    b = _sb32(x)
    return b ^ _rl32(b, 13) ^ _rl32(b, 23)


def _rkeys(kb: list[int], enc: bool = True) -> list[int]:
    mk = [0] * 4
    for i in range(4):
        mk[i] = (kb[4 * i] << 24) | (kb[4 * i + 1] << 16) | (kb[4 * i + 2] << 8) | kb[4 * i + 3]
    k = [mk[i] ^ _FK[i] for i in range(4)]
    rk = [0] * 32
    for i in range(0, 32, 4):
        rk[i] = k[0] ^ _tp(k[1] ^ k[2] ^ k[3] ^ _CK[i])
        k[0] = rk[i]
        rk[i + 1] = k[1] ^ _tp(k[2] ^ k[3] ^ k[0] ^ _CK[i + 1])
        k[1] = rk[i + 1]
        rk[i + 2] = k[2] ^ _tp(k[3] ^ k[0] ^ k[1] ^ _CK[i + 2])
        k[2] = rk[i + 2]
        rk[i + 3] = k[3] ^ _tp(k[0] ^ k[1] ^ k[2] ^ _CK[i + 3])
        k[3] = rk[i + 3]
    if not enc:
        rk = list(reversed(rk))
    return rk


def _block_enc(blk: list[int], rk: list[int]) -> list[int]:
    x = [0] * 4
    for i in range(4):
        x[i] = (blk[4 * i] << 24) | (blk[4 * i + 1] << 16) | (blk[4 * i + 2] << 8) | blk[4 * i + 3]
    for i in range(0, 32, 4):
        x[0] ^= _tf(x[1] ^ x[2] ^ x[3] ^ rk[i])
        x[1] ^= _tf(x[2] ^ x[3] ^ x[0] ^ rk[i + 1])
        x[2] ^= _tf(x[3] ^ x[0] ^ x[1] ^ rk[i + 2])
        x[3] ^= _tf(x[0] ^ x[1] ^ x[2] ^ rk[i + 3])
    out = [0] * 16
    for i in range(0, 16, 4):
        idx = 3 - i // 4
        out[i] = (x[idx] >> 24) & 0xFF
        out[i + 1] = (x[idx] >> 16) & 0xFF
        out[i + 2] = (x[idx] >> 8) & 0xFF
        out[i + 3] = x[idx] & 0xFF
    return out


def _hex_to_bytes(hex_text: str) -> list[int]:
    return [int(hex_text[i : i + 2], 16) for i in range(0, len(hex_text), 2)]


def sm4_encrypt_cbc_hex(plain: str, key_hex: str) -> str:
    kb = _hex_to_bytes(key_hex)
    prev = kb[:]
    data = list(plain.encode("utf-8"))
    pad = 16 - (len(data) % 16)
    data.extend([pad] * pad)
    rk = _rkeys(kb, True)
    out: list[int] = []
    for off in range(0, len(data), 16):
        block = data[off : off + 16]
        mixed = [block[i] ^ prev[i] for i in range(16)]
        enc = _block_enc(mixed, rk)
        out.extend(enc)
        prev = enc[:]
    return "".join(format(b, "02x") for b in out)


def sm4_decrypt_cbc_hex(cipher_hex: str, key_hex: str) -> str:
    kb = _hex_to_bytes(key_hex)
    prev = kb[:]
    data = _hex_to_bytes(cipher_hex)
    if len(data) % 16 != 0:
        raise ValueError("SM4-CBC cipher length must be multiple of 16 bytes")
    rk = _rkeys(kb, False)
    out: list[int] = []
    for off in range(0, len(data), 16):
        block = data[off : off + 16]
        dec = _block_enc(block, rk)
        out.extend([dec[i] ^ prev[i] for i in range(16)])
        prev = block[:]
    if not out:
        return ""
    pad = out[-1]
    if 1 <= pad <= 16 and out[-pad:] == [pad] * pad:
        out = out[:-pad]
    return bytes(out).decode("utf-8", errors="replace")


def decrypt_process_data_hex(cipher_text: str) -> Any:
    text = str(cipher_text or "").strip()
    if not text:
        return None
    wasm_plain = _wasm_decrypt_text(text)
    if wasm_plain:
        try:
            return json.loads(wasm_plain)
        except Exception:
            pass
    seed_len = 32
    if len(text) <= seed_len:
        return None
    mask = xor_hex_with_one(text[:seed_len])
    plain = sm4_decrypt_cbc_hex(text[seed_len:], sm4_key(mask))
    return json.loads(plain)


def sm4_key(seed: str) -> str:
    digest = fingerprint_hash(f"{seed}_bsdk_", seed=27)
    return digest.lower() if digest else "9EqAw+JtHSsyswpL9EqAw+JtHSsyswpL"


def xor_hex_with_one(hex_text: str) -> str:
    return "".join(format(1 ^ int(ch, 16), "x") for ch in str(hex_text))


def node_random_id(now_ms: int | None = None, random_func=None) -> str:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    rand = random_func or random.random
    cursor = now
    template = ("".join(str(now) for _ in range(3))).replace(".", "x")
    template = "x" * len(template)
    result = []
    for char in template[:32]:
        value = int((cursor + 16 * rand()) % 16)
        cursor = math.floor(cursor / 16)
        result.append(format(value if char == "x" else ((value & 3) | 8), "x"))
    return "".join(result)


def _js_string(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return str(value)


def json_serialize_1_9_6(value: Any, depth: int = 0) -> Any:
    next_depth = depth + 1 if isinstance(depth, int) else 1
    if next_depth == 1 and (value is None or (isinstance(value, float) and not math.isfinite(value))):
        return ""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "|-|-|"
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "" if next_depth == 1 else value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple)):
        if next_depth > 10:
            return "[]"
        items = [json_serialize_1_9_6(item, next_depth) for item in value]
        return json.dumps([item for item in items if item != "|-|-|"], ensure_ascii=False, separators=(",", ":"))
    if next_depth > 10:
        return ""
    if isinstance(value, dict):
        output = ""
        for key in sorted(value.keys(), key=str):
            entry = json_serialize_1_9_6(value[key], next_depth)
            if entry != "|-|-|":
                output += f"{key}={_js_string(entry)};"
        return output
    return str(value)


def object_to_array_1_9_6(obj: dict[str, Any]) -> list[Any]:
    result: list[Any] = []
    for key in sorted(obj.keys(), key=str):
        if key == "$1":
            continue
        value = obj[key]
        if isinstance(value, dict):
            result.append(object_to_array_1_9_6(value))
        elif isinstance(value, list):
            result.append([object_to_array_1_9_6(item) if isinstance(item, dict) else item for item in value])
        else:
            result.append(value)
    return result


try:
    import wasmtime as _wt
except ImportError:  # pragma: no cover - reported by the compare script.
    _wt = None

_wasm_lock = threading.Lock()
_wasm_engine = None
_wasm_module = None
_wasm_sign_cache: bytes | None = None
_wasm_tls = threading.local()

_bb_pool: list = []
_bb_pool_lock = threading.Lock()
_bb_pool_sem: threading.Semaphore | None = None
_BB_POOL_SIZE = int(os.environ.get("MT_WASM_POOL_SIZE", "32"))
_NODE_BRIDGE_DEFAULT_WAC_UA = (
    "Mozilla/5.0 (Linux; Android 14; wv) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/117.0.0.0 Mobile Safari/537.36"
)


def _load_wasm_module():
    global _wasm_engine, _wasm_module
    if _wt is None:
        raise RuntimeError("missing wasmtime: install project requirements first")
    if _wasm_module is not None:
        return _wasm_engine, _wasm_module
    with _wasm_lock:
        if _wasm_module is None:
            if not os.path.exists(_RUNTIME_WASM_PATH):
                raise FileNotFoundError(_RUNTIME_WASM_PATH)
            _wasm_engine = _wt.Engine()
            _wasm_module = _wt.Module.from_file(_wasm_engine, _RUNTIME_WASM_PATH)
    return _wasm_engine, _wasm_module


def _read_wasm_string(runtime: dict[str, Any], ptr: int) -> str:
    store = runtime["store"]
    inst = runtime["inst"]
    mem = inst.exports(store)["memory"]
    buf = mem.data_ptr(store)
    size = mem.data_len(store)
    out = bytearray()
    offset = 0
    while ptr + offset < size and buf[ptr + offset] != 0:
        out.append(buf[ptr + offset])
        offset += 1
    return out.decode("utf-8", errors="replace")


def _write_wasm_string(runtime: dict[str, Any], value: Any) -> int:
    store = runtime["store"]
    inst = runtime["inst"]
    raw = str(value).encode("utf-8") + b"\x00"
    ptr = inst.exports(store)["malloc"](store, len(raw))
    buf = inst.exports(store)["memory"].data_ptr(store)
    for i, b in enumerate(raw):
        buf[ptr + i] = b
    return ptr


def _json_for_wasm(value: Any) -> str:
    if value is _Undefined:
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class _UndefinedType:
    pass


_Undefined = _UndefinedType()


def _eval_window_code(code: str, runtime: dict[str, Any]) -> Any:
    text = str(code or "")
    user_agent = str(runtime.get("user_agent") or "")
    lowered = user_agent.lower()
    if "isAndroidWebView" in text and "process.versions?.node" in text and "navigator.userAgent.toLowerCase" in text:
        is_android_webview = "android" in lowered and ("wv" in lowered or ("version/" in lowered and "chrome" not in lowered))
        is_ios_webview = any(mark in lowered for mark in ("iphone", "ipad", "ipod")) and "safari" not in lowered
        return 0 if (is_android_webview or is_ios_webview) else 1
    if "has_env" in text:
        return 0
    if "getRendererUserAgent" in text or "navigator.userAgent" in text:
        return user_agent
    if "getLocalIPAddress" in text or "getConnectedWifi" in text:
        return ""
    if "getScreenBrightness" in text:
        return -1
    if "getHCEState" in text or "checkDeviceSupportHevc" in text:
        return False
    if "Date.now" in text:
        return int(runtime.get("now_ms") or time.time() * 1000)
    return 0


def _invoke_js(target: str, method: str, raw_args: str, runtime: dict[str, Any]) -> Any:
    args = []
    if raw_args:
        parsed = json.loads(raw_args)
        args = parsed if isinstance(parsed, list) else [parsed]
    if target == "Date" and method == "now":
        return int(runtime.get("now_ms") or time.time() * 1000)
    if target == "JSON":
        if method == "parse":
            return json.loads(args[0] if args else "")
        if method == "stringify":
            return json.dumps(args[0] if args else None, ensure_ascii=False, separators=(",", ":"))
    if target in ("window", "globalThis", "Module"):
        if method == "eval":
            return _eval_window_code(args[0] if args else "", runtime)
        if method == "has_env":
            return 0
        if method == "getRendererUserAgent":
            return runtime.get("user_agent") or ""
        if method in ("getLocalIPAddress", "getConnectedWifi"):
            return ""
        if method == "getScreenBrightness":
            return -1
        if method in ("getHCEState", "checkDeviceSupportHevc"):
            return False
    if target == "eval" and method == "call":
        return _eval_window_code(args[0] if args else "", runtime)
    return _Undefined


def _wac_request_context_enabled() -> bool:
    return (os.environ.get("MT_PYBB_WAC_REQUEST_CONTEXT") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _create_bb_instance(engine, module):
    store = _wt.Store(engine)
    wasi = _wt.WasiConfig()
    store.set_wasi(wasi)
    linker = _wt.Linker(engine)
    linker.define_wasi()
    runtime = {
        "store": store,
        "inst": None,
        "now_ms": None,
        "user_agent": "",
        "context_mode": "request",
    }
    i32 = _wt.ValType.i32()
    f64 = _wt.ValType.f64()

    def js_invoke(target_ptr, method_ptr, args_ptr):
        try:
            target = _read_wasm_string(runtime, target_ptr)
            method = _read_wasm_string(runtime, method_ptr)
            raw_args = _read_wasm_string(runtime, args_ptr) if args_ptr else ""
            result = _invoke_js(target, method, raw_args, runtime)
            return _write_wasm_string(runtime, _json_for_wasm(result))
        except Exception as exc:
            return _write_wasm_string(runtime, f"[invoke error] {exc}")

    linker.define_func("env", "js_invoke", _wt.FuncType([i32, i32, i32], [i32]), js_invoke)
    linker.define_func("env", "exit", _wt.FuncType([i32], []), lambda _code: None)
    linker.define_func("env", "__cxa_throw", _wt.FuncType([i32, i32, i32], []), lambda _a, _b, _c: None)
    linker.define_func(
        "env",
        "emscripten_date_now",
        _wt.FuncType([], [f64]),
        lambda: float(int(runtime.get("now_ms") or time.time() * 1000)),
    )
    linker.define_func("env", "emscripten_resize_heap", _wt.FuncType([i32], [i32]), lambda _size: 0)
    linker.define_func("env", "_abort_js", _wt.FuncType([], []), lambda: None)
    linker.define_func("env", "_tzset_js", _wt.FuncType([i32, i32, i32, i32], []), lambda _a, _b, _c, _d: None)

    inst = linker.instantiate(store, module)
    runtime["inst"] = inst
    try:
        inst.exports(store)["__wasm_call_ctors"](store)
    except Exception:
        pass
    return runtime


def _ensure_bb_pool():
    global _bb_pool_sem
    if _bb_pool_sem is not None:
        return True
    engine, module = _load_wasm_module()
    if engine is None or module is None:
        return False
    with _bb_pool_lock:
        if _bb_pool_sem is not None:
            return True
        for _ in range(_BB_POOL_SIZE):
            try:
                rt = _create_bb_instance(engine, module)
                _bb_pool.append(rt)
            except Exception:
                break
        if not _bb_pool:
            return False
        _bb_pool_sem = threading.Semaphore(len(_bb_pool))
    return True


def _acquire_bb_runtime(now_ms=None, user_agent="", context_mode="request"):
    if not _ensure_bb_pool():
        return None
    _bb_pool_sem.acquire()
    with _bb_pool_lock:
        runtime = _bb_pool.pop()
    runtime["now_ms"] = now_ms
    runtime["user_agent"] = user_agent or ""
    runtime["context_mode"] = context_mode
    return runtime


def _release_bb_runtime(runtime):
    if runtime is None:
        return
    with _bb_pool_lock:
        _bb_pool.append(runtime)
    _bb_pool_sem.release()


def _get_wasm_runtime(
    now_ms: int | None = None,
    user_agent: str = "",
    *,
    update_context: bool = True,
    context_mode: str = "request",
) -> dict[str, Any]:
    runtime = getattr(_wasm_tls, "runtime", None)
    if runtime is not None:
        if runtime.get("context_mode") != context_mode:
            runtime = None
        else:
            if update_context:
                runtime["now_ms"] = now_ms
                runtime["user_agent"] = user_agent or ""
            return runtime

    if runtime is not None:
        return runtime

    engine, module = _load_wasm_module()
    store = _wt.Store(engine)
    wasi = _wt.WasiConfig()
    store.set_wasi(wasi)
    linker = _wt.Linker(engine)
    linker.define_wasi()
    runtime = {
        "store": store,
        "inst": None,
        "now_ms": now_ms,
        "user_agent": user_agent or "",
        "context_mode": context_mode,
    }
    i32 = _wt.ValType.i32()
    f64 = _wt.ValType.f64()

    def js_invoke(target_ptr, method_ptr, args_ptr):
        try:
            target = _read_wasm_string(runtime, target_ptr)
            method = _read_wasm_string(runtime, method_ptr)
            raw_args = _read_wasm_string(runtime, args_ptr) if args_ptr else ""
            result = _invoke_js(target, method, raw_args, runtime)
            return _write_wasm_string(runtime, _json_for_wasm(result))
        except Exception as exc:
            return _write_wasm_string(runtime, f"[invoke error] {exc}")

    linker.define_func("env", "js_invoke", _wt.FuncType([i32, i32, i32], [i32]), js_invoke)
    linker.define_func("env", "exit", _wt.FuncType([i32], []), lambda _code: None)
    linker.define_func("env", "__cxa_throw", _wt.FuncType([i32, i32, i32], []), lambda _a, _b, _c: None)
    linker.define_func(
        "env",
        "emscripten_date_now",
        _wt.FuncType([], [f64]),
        lambda: float(int(runtime.get("now_ms") or time.time() * 1000)),
    )
    linker.define_func("env", "emscripten_resize_heap", _wt.FuncType([i32], [i32]), lambda _size: 0)
    linker.define_func("env", "_abort_js", _wt.FuncType([], []), lambda: None)
    linker.define_func("env", "_tzset_js", _wt.FuncType([i32, i32, i32, i32], []), lambda _a, _b, _c, _d: None)

    inst = linker.instantiate(store, module)
    runtime["inst"] = inst
    try:
        inst.exports(store)["__wasm_call_ctors"](store)
    except Exception:
        pass
    _wasm_tls.runtime = runtime
    return runtime


def load_sign_wasm_bytes() -> bytes:
    global _wasm_sign_cache
    if _wasm_sign_cache is not None:
        return _wasm_sign_cache
    with _wasm_lock:
        if _wasm_sign_cache is None:
            with open(_SIGN_WASM_PATH, "rb") as handle:
                _wasm_sign_cache = handle.read()
    return _wasm_sign_cache


def sign_wasm_bytes_from_base64(wasm_base64: str | None = None) -> bytes:
    text = str(wasm_base64 or "").strip()
    if text:
        return base64.b64decode(text)
    return load_sign_wasm_bytes()


def run_wac(did: str, sign: str, sign_wasm_bytes: bytes | None = None, *, now_ms: int | None = None, user_agent: str = "") -> str:
    runtime = _acquire_bb_runtime(now_ms=now_ms, user_agent=user_agent, context_mode="request")
    if runtime is None:
        runtime = _get_wasm_runtime(now_ms=now_ms, user_agent=user_agent, context_mode="request")
        return _run_wac_with_runtime(runtime, did, sign, sign_wasm_bytes)
    try:
        return _run_wac_with_runtime(runtime, did, sign, sign_wasm_bytes)
    finally:
        _release_bb_runtime(runtime)


def _run_wac_with_runtime(runtime, did, sign, sign_wasm_bytes):
    store = runtime["store"]
    inst = runtime["inst"]
    blob = sign_wasm_bytes if sign_wasm_bytes is not None else load_sign_wasm_bytes()
    malloc = inst.exports(store)["malloc"]
    free = inst.exports(store)["free"]
    free_memory = inst.exports(store)["free_memory"]
    memory = inst.exports(store)["memory"]
    run = inst.exports(store)["run_wac"]

    did_text = str(did or "")
    sign_text = str(sign or "")
    did_ptr = _write_wasm_string(runtime, did_text)
    sign_ptr = _write_wasm_string(runtime, sign_text)
    blob_ptr = malloc(store, len(blob))
    buf = memory.data_ptr(store)
    for i, b in enumerate(blob):
        buf[blob_ptr + i] = b
    out_ptr = run(store, did_ptr, len(did_text.encode("utf-8")), sign_ptr, len(sign_text.encode("utf-8")), blob_ptr, len(blob))
    output = _read_wasm_string(runtime, out_ptr) if out_ptr else ""
    if out_ptr:
        free_memory(store, out_ptr)
    free(store, did_ptr)
    free(store, sign_ptr)
    free(store, blob_ptr)
    return output or ""


def _is_hex_cipher_text(value: str) -> bool:
    text = str(value or "").strip()
    if len(text) <= 32 or len(text) % 2 != 0:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in text)


def _wasm_encrypt_text(plain_text: str, *, now_ms: int | None = None, user_agent: str = "") -> str | None:
    runtime = _acquire_bb_runtime(now_ms=now_ms, user_agent=user_agent, context_mode="outer_encrypt")
    if runtime is None:
        runtime = _get_wasm_runtime(now_ms=now_ms, user_agent=user_agent, context_mode="outer_encrypt")
        return _wasm_encrypt_with_runtime(runtime, plain_text)
    try:
        return _wasm_encrypt_with_runtime(runtime, plain_text)
    finally:
        _release_bb_runtime(runtime)


def _wasm_encrypt_with_runtime(runtime, plain_text):
    store = runtime["store"]
    inst = runtime["inst"]
    free = inst.exports(store)["free"]
    free_memory = inst.exports(store)["free_memory"]
    encrypt = inst.exports(store)["en"]
    ptr = _write_wasm_string(runtime, plain_text)
    out_ptr = 0
    try:
        out_ptr = encrypt(store, ptr)
        output = _read_wasm_string(runtime, out_ptr) if out_ptr else ""
    finally:
        if out_ptr:
            free_memory(store, out_ptr)
        free(store, ptr)
    return output if _is_hex_cipher_text(output) else None


def _wasm_decrypt_text(cipher_text: str, *, now_ms: int | None = None, user_agent: str = "") -> str | None:
    text = str(cipher_text or "").strip()
    if not _is_hex_cipher_text(text):
        return None
    runtime = _acquire_bb_runtime(now_ms=now_ms, user_agent=user_agent, context_mode="outer_decrypt")
    if runtime is None:
        runtime = _get_wasm_runtime(now_ms=now_ms, user_agent=user_agent, context_mode="outer_decrypt")
        return _wasm_decrypt_with_runtime(runtime, text)
    try:
        return _wasm_decrypt_with_runtime(runtime, text)
    finally:
        _release_bb_runtime(runtime)


def _wasm_decrypt_with_runtime(runtime, text):
    store = runtime["store"]
    inst = runtime["inst"]
    free = inst.exports(store)["free"]
    free_memory = inst.exports(store)["free_memory"]
    decrypt = inst.exports(store)["de"]
    ptr = _write_wasm_string(runtime, text)
    out_ptr = 0
    try:
        out_ptr = decrypt(store, ptr)
        output = _read_wasm_string(runtime, out_ptr) if out_ptr else ""
    finally:
        if out_ptr:
            free_memory(store, out_ptr)
        free(store, ptr)
    normalized = str(output or "").strip()
    return normalized or None


def build_bb_headers_py(
    body: Any,
    *,
    appid: str = DEFAULT_APPID,
    devid: str = "",
    sessid: str = "",
    uid: str = "",
    ua: str = "",
    wasm_version: str = DEFAULT_SIGN_WASM_VERSION,
    wasm_base64: str | None = None,
    wasm_ok: bool = True,
    now_ms: int | None = None,
    random_id: str | None = None,
    smk: str | None = None,
    sign_wasm_bytes: bytes | None = None,
) -> dict[str, Any]:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    canonical = json_serialize_1_9_6(body)
    nonce = random_id or node_random_id(now)
    sign = fingerprint_hash(canonical, seed=27, cap=4096, tail=nonce)
    active_wasm = sign_wasm_bytes
    if active_wasm is None and wasm_ok:
        active_wasm = sign_wasm_bytes_from_base64(wasm_base64)
    sign_wac = run_wac(devid, sign, active_wasm, now_ms=now, user_agent=ua) if wasm_ok else ""
    if wasm_ok and not sign_wac:
        raise RuntimeError("py_bb_new: run_wac returned empty signWac while wasm_ok=True")
    outer_smk = smk or node_random_id(now)
    outer_key = sm4_key(outer_smk)
    outer_iv = xor_hex_with_one(outer_smk)
    payload = {
        "101": str(appid or DEFAULT_APPID),
        "103": str(devid or ""),
        "104": str(sessid or ""),
        "105": "",
        "106": "0",
        "108": "0",
        "109": nonce,
        "110": sign,
        "111": now,
        "120": {},
        "130": str(uid or ""),
        "140": str(wasm_version or ""),
        "141": sign_wac or "",
    }
    payload_array = object_to_array_1_9_6(payload)
    plain = json.dumps(payload_array, ensure_ascii=False, separators=(",", ":"))
    encrypt_method = "sm4"
    encrypt_error = ""
    content_web = None
    if wasm_ok:
        try:
            content_web = _wasm_encrypt_text(plain, now_ms=now, user_agent=ua)
            if content_web:
                encrypt_method = "wasm"
        except Exception as exc:
            encrypt_error = str(exc)
            raise RuntimeError(f"py_bb_new: wasm Content-Web-Bb encrypt failed: {encrypt_error}") from exc
        if not content_web:
            raise RuntimeError("py_bb_new: wasm Content-Web-Bb encrypt returned empty")
    if not content_web:
        content_web = outer_iv + sm4_encrypt_cbc_hex(plain, outer_key)
        if wasm_ok:
            encrypt_method = "sm4_fallback"
    return {
        "Content-Web-Bb": content_web,
        "Content-Hh-Bb": sign,
        "Sdk-Ver-Bb": SDK_VERSION,
        "randomId": nonce,
        "sign": sign,
        "signWac": sign_wac or "",
        "_debug": {
            "canonical": canonical,
            "payload": payload,
            "payloadArray": payload_array,
            "plain": plain,
            "encrypt_method": encrypt_method,
            "encrypt_error": encrypt_error,
            "smk": outer_smk,
            "smk1": outer_key,
            "smi": outer_iv,
        },
    }


def bb_sign_post_for_rush(body: Any, **kwargs) -> dict[str, Any]:
    return build_bb_headers_py(body, **kwargs)


def _short_user_agent_for_cookie(user_agent: str) -> str:
    normalized = str(user_agent or "")
    if not normalized:
        return ""
    close_index = normalized.find(")")
    if close_index > 0:
        return normalized[:close_index]
    return normalized[:50]


def build_du_cookie_py(
    *,
    appid: str = DEFAULT_APPID,
    devid: str = "",
    sessid: str = "",
    uid: str = "",
    ua: str = "",
    hp: str = "",
    wasm_version: str | None = None,
    wasm_base64: str | None = None,
    wasm_ok: bool = True,
    page_domain: str = "https://h5.moutai519.com.cn/",
    now_ms: int | None = None,
) -> str:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    if not hp:
        tail = str(devid or "")[-4:]
        hp = f"bid-{str(now)[::-1]}-{tail}-{node_random_id(now)[9:13]}"
    payload = {
        "101": str(appid or DEFAULT_APPID),
        "103": str(devid or ""),
        "105": str(sessid or ""),
        "107": _short_user_agent_for_cookie(ua),
        "109": str(page_domain or ""),
        "111": str(hp or ""),
        "113": now,
    }
    payload_array = object_to_array_1_9_6(payload)
    smk = node_random_id(now)
    key = sm4_key(smk)
    iv = xor_hex_with_one(smk)
    plain = json.dumps(payload_array, ensure_ascii=False, separators=(",", ":"))
    return iv + sm4_encrypt_cbc_hex(plain, key)


def bb_du_cookie(**kwargs) -> str:
    return build_du_cookie_py(**kwargs)


# ============================================================
# 梆梆暖场上报（POST /bbprbdata/upload）
# 与 maotai-seckill-share2 2/maotai-seckill.js warmupBangcleRiskFlow_1_9_6 对齐
# 上报走 SM4-CBC（不走 wasm.en），body 即 processData_1_9_6(payload) 的输出
# ============================================================

WARMUP_UPLOAD_URL = "https://fk1.moutai519.com.cn/bangcle/bbprbdata/upload"

# 来自 maotai-seckill.js:245-295 LIVE_BB_DEVICE_INFO_TEMPLATE_1_9_6
# 真机 dump 出的浏览器/显示/环境/数学函数指纹
_DEVICE_INFO_TEMPLATE_BROWSER: list[Any] = [
    "f799d6fa", "", "", 8, 8,
    "Linux armv81", "Qualcomm~Adreno (TM) 740",
    "Asia/Shanghai(-480)", "UTF-8",
    [], [0, 0, 1, 0, 0, 0], [],
    ["zh-CN", "en-US"], ["sans-serif-thin"],
    [5, True, True], [0, 0, 0, 0],
    ["", "probably", "probably"],
    ["", 0, 1, 1, 1, 0, 0, 0, 0, 0, "a45c4277", 33, "20030107", 0],
]
_DEVICE_INFO_TEMPLATE_DISPLAY: list[Any] = [
    "2377X1081", 2.549999952316284, [424, 932, 424, 932], 24, ["srgb"], 2, 2, 2,
]
_DEVICE_INFO_TEMPLATE_ENV: list[Any] = [
    "", [[167840257], [], []], [-1, -1, -1], 1, 140500203110, "", [-1, -1],
    "124.08072766105033", 11,
]
_DEVICE_INFO_TEMPLATE_MATH: list[str] = [
    "1.4473588658278522", "709.889355822726", "355.291251501643",
    "0.12343746096704435", "0.881373587019543", "0.8813735870195429",
    "0.5493061443340548", "0.5493061443340548", "0.4636476090008061",
    "-0.8390715290095377", "1.5430806348152437", "1.5430806348152437",
    "2.718281828459045", "1.718281828459045", "1.718281828459045",
    "2.3978952727983707", "2.3978952727983707", "1.9275814160560204e-50",
    "0.8178819121159085", "1.1752011936438014", "2.534342107873324",
    "-1.4214488238747245", "0.7615941559557649", "0.7615941559557649",
]
_DEVICE_INFO_TEMPLATE_COOKIES: list[str] = ["_sdk_v_", "_bs_device_id", "_d_u"]


def _hex_account_seed(seed: str) -> str:
    """把账号 seed（如 raw_id）哈希成 32 字符 hex；用于派生稳定的 deviceInfo 字段。"""
    import hashlib
    return hashlib.sha256(str(seed or "").encode("utf-8")).hexdigest()


def build_device_info_for_account(
    account_seed: str,
    *,
    user_agent: str = "",
    page_url: str = "https://h5.moutai519.com.cn/",
    gpu: str = "",
    screen_resolution: str = "",
    jsid: str = "",
    uaid: str = "",
) -> dict:
    """按账号 seed 派生稳定 deviceInfo（每账号一份，固化后不变）。

    与 maotai-seckill.js:2532 buildBangcleDeviceInfo_1_9_6 结构一致。
    100=browser, 200=display, 300=env, 400=math, 500=cookies, 600=pageUrl, 700=jsid, 800=uaid

    UA 自洽：调用方传入 user_agent / gpu / screen_resolution，函数把这些值塞进对应位置；
    其余字段从模板派生（可哈希字段按 seed 扰动以避免账号间完全相同）。
    """
    h = _hex_account_seed(account_seed)
    # 浏览器指纹 ID（模板里是 'f799d6fa'）→ 取 seed hash 前 8 字符（每账号唯一）
    browser_fp = h[:8]

    # GPU：UA 自洽优先；否则用模板默认值
    gpu_value = (gpu or _DEVICE_INFO_TEMPLATE_BROWSER[6]).strip() or _DEVICE_INFO_TEMPLATE_BROWSER[6]

    # 分辨率：UA 自洽优先；否则用模板默认 '2377X1081'
    sr = (screen_resolution or "").strip()
    if sr:
        # 兼容 "1080*2215" / "1080x2215" / "1080X2215"
        for sep in ("x", "X", "*"):
            if sep in sr:
                parts = sr.split(sep, 1)
                w, hgt = parts[0].strip(), parts[1].strip()
                if w and hgt:
                    sr = f"{w}X{hgt}"
                    break
    display_size = sr or _DEVICE_INFO_TEMPLATE_DISPLAY[0]

    # 100/browser section（19 个字段）
    browser = list(_DEVICE_INFO_TEMPLATE_BROWSER)
    browser[0] = browser_fp
    browser[6] = gpu_value
    browser_section = {
        "101": str(user_agent or "").lower(),
        "102": browser[0],
        "103": browser[1],
        "104": browser[2],
        "105": browser[3],
        "106": browser[4],
        "107": browser[5],
        "108": browser[6],
        "109": browser[7],
        "110": browser[8],
        "111": browser[9],
        "112": browser[10],
        "113": browser[11],
        "114": browser[12],
        "115": browser[13],
        "116": browser[14],
        "117": browser[15],
        "118": browser[16],
        "119": browser[17],
    }

    # 200/display section（8 个字段）
    display = list(_DEVICE_INFO_TEMPLATE_DISPLAY)
    display[0] = display_size
    display_section = {
        "201": display[0],
        "202": display[1],
        "203": display[2],
        "204": display[3],
        "205": display[4],
        "206": display[5],
        "207": display[6],
        "208": display[7],
    }

    # 300/env section（9 个字段）
    env = list(_DEVICE_INFO_TEMPLATE_ENV)
    env_section = {
        "301": env[0],
        "302": env[1],
        "303": env[2],
        "304": env[3],
        "305": env[4],
        "306": env[5],
        "307": env[6],
        "308": env[7],
        "309": env[8],
    }

    # 400/math section（24 个字段，key 用 '00'..'23'）
    math_section = {}
    for index, value in enumerate(_DEVICE_INFO_TEMPLATE_MATH):
        key = f"0{index}" if index < 10 else str(index)
        math_section[key] = value

    # 去掉 page_url 的 query/hash（与 JS stripQueryAndHash_1_9_6 一致）
    page_clean = str(page_url or "https://h5.moutai519.com.cn/")
    for sep in ("#", "?"):
        if sep in page_clean:
            page_clean = page_clean.split(sep, 1)[0]
    if not page_clean:
        page_clean = "https://h5.moutai519.com.cn/"

    return {
        "100": browser_section,
        "200": display_section,
        "300": env_section,
        "400": math_section,
        "500": list(_DEVICE_INFO_TEMPLATE_COOKIES),
        "600": page_clean,
        "700": str(jsid or ""),
        "800": str(uaid or ""),
    }


def build_upload_info_payload_py(
    *,
    appid: str = DEFAULT_APPID,
    devid: str = "",
    sessid: str = "",
    uid: str = "",
    serial: int = 1,
    sdk_version: str = SDK_VERSION,
    tactics_version: int = 0,
    time_delta: int = 0,
    device_info: dict | None = None,
    token_prepetuity: str = "",
    now_ms: int | None = None,
    smk: str | None = None,
) -> tuple[str, dict]:
    """构造 bbprbdata/upload 的加密 body。

    对应 maotai-seckill.js buildBangcleUploadInfoPayloadObject_1_9_6 + processData_1_9_6。
    返回 (encrypted_body_str, debug_dict)。

    payload 顶层 12 个字段（按 key 字符串排序后 → object_to_array → SM4 加密）：
      001: userId, 002: appKey,        003: devid (did),       004: startId (sessid)
      005: tokenPrepetuity, 006: now,  007: serial,             008: sdk_version
      009: tactics_version, 010: time_delta,
      101: device_info, 102: events (空数组)

    encrypted_body = smi (32 字符 xor-hex) + sm4_encrypt_cbc_hex(JSON, smk1)
    """
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    payload_obj = {
        "001": str(uid or ""),
        "002": str(appid or DEFAULT_APPID),
        "003": str(devid or ""),
        "004": str(sessid or ""),
        "005": str(token_prepetuity or ""),
        "006": now,
        "007": int(serial or 1),
        "008": str(sdk_version or SDK_VERSION),
        "009": int(tactics_version or 0),
        "010": int(time_delta or 0),
        "101": device_info if isinstance(device_info, dict) else {},
        "102": [],
    }
    payload_array = object_to_array_1_9_6(payload_obj)
    plain = json.dumps(payload_array, ensure_ascii=False, separators=(",", ":"))

    outer_smk = smk or node_random_id(now)
    outer_key = sm4_key(outer_smk)
    outer_iv = xor_hex_with_one(outer_smk)
    body = outer_iv + sm4_encrypt_cbc_hex(plain, outer_key)
    return body, {
        "payload": payload_obj,
        "payload_array": payload_array,
        "plain": plain,
        "smk": outer_smk,
        "smk1": outer_key,
        "smi": outer_iv,
        "now_ms": now,
    }


__all__ = [
    "SDK_VERSION",
    "DEFAULT_APPID",
    "DEFAULT_SIGN_WASM_VERSION",
    "WARMUP_UPLOAD_URL",
    "build_bb_headers_py",
    "build_du_cookie_py",
    "bb_sign_post_for_rush",
    "bb_du_cookie",
    "build_device_info_for_account",
    "build_upload_info_payload_py",
    "fingerprint_hash",
    "json_serialize_1_9_6",
    "load_sign_wasm_bytes",
    "node_random_id",
    "object_to_array_1_9_6",
    "run_wac",
    "sm4_decrypt_cbc_hex",
    "sm4_encrypt_cbc_hex",
    "sm4_key",
    "decrypt_process_data_hex",
    "xor_hex_with_one",
]
