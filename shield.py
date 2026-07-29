#!/usr/bin/env python3
"""
茅台请求签名与 BotShield 头生成工具；整合 App 侧 MT-V 签名、AES 参数加密、H5 设备指纹、瑞数 BB 请求头、WASM 签名和抢购请求构建逻辑。
"""

import base64
import json
import time
import hashlib
import random
import string
import struct
import os
import sys
import threading
import uuid as _uuid
from typing import Dict, Any, List, Tuple, Optional, Union

from net_time import adjusted_unix, adjusted_unix_ms, format_mt_dtime_gmt08


# ====================== 运行时常量与抓包基线 ======================

RELEASE_VER = "1.9.10"
SHIELD_VER = "V3.5.0_20260721.2_imaotai"
WASM_TAG = "v1.1.3.2"
BUNDLE_ID = "com.moutai.mall"
TRACE_TOKEN = "a3f9c2b8471de05f9b6c4e1287d5a9c1"
RUSH_MT_INFO = "028e7f96f6369cafe1d105579c5b9377"
# mt1.9.6 test-full-flow.js 的 rushPurchase 抓包基线：RELEASE_VER 对应 App 版本，SHIELD_VER 对应 BB SDK 版本，WASM_TAG 对应 sign_wasm 版本。
DEFAULT_APPID = "10001"

# AES 参数加密密钥、XOR 初始种子、MT-V 随机字符集和可用签名 MD5 池。
_ENC_SECRET = b"3a79d$e4c2169a#fbd24c583ae71c0b9"[:32]
_ENC_NONCE = b"7392645081974362"
_XOR_SEED = 72
_SIGN_CHARS = "01234567890abcdefghijklmnopqrstuvwxyz"
_VALID_HASHES = [
    "2f5ae2348f427a666876d7da3563a7d2",
    "dd6c1787c17943e9db0c59c0da3d20e5",
]


# ====================== 基础哈希与 XOR 工具 ======================

def _md5(text: str) -> str:
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def _md5_raw(text: str) -> str:
    """生成不补零的 MD5 十六进制字符串，对齐 Java Integer.toHexString。"""
    digest = hashlib.md5(text.encode('utf-8')).digest()
    return ''.join(format(b, 'x') for b in digest)


def _stream_xor(data: str, seed: int = _XOR_SEED) -> str:
    """带累积种子的流式 XOR。"""
    buf = []
    for ch in data:
        seed ^= ord(ch)
        buf.append(chr(seed))
    return ''.join(buf)


# ====================== MurmurHash3 x64 128 位原语 ======================

def _add64(a, b):
    a = [a[0] >> 16, a[0] & 0xFFFF, a[1] >> 16, a[1] & 0xFFFF]
    b = [b[0] >> 16, b[0] & 0xFFFF, b[1] >> 16, b[1] & 0xFFFF]
    c = [0, 0, 0, 0]
    c[3] = a[3] + b[3]; c[2] = a[2] + b[2] + (c[3] >> 16); c[3] &= 0xFFFF
    c[1] = a[1] + b[1] + (c[2] >> 16); c[2] &= 0xFFFF
    c[0] = a[0] + b[0] + (c[1] >> 16); c[1] &= 0xFFFF; c[0] &= 0xFFFF
    return [(c[0] << 16) | c[1], (c[2] << 16) | c[3]]


def _mul64(a, b):
    a = [a[0] >> 16, a[0] & 0xFFFF, a[1] >> 16, a[1] & 0xFFFF]
    b = [b[0] >> 16, b[0] & 0xFFFF, b[1] >> 16, b[1] & 0xFFFF]
    c = [0, 0, 0, 0]
    c[3] += a[3]*b[3]; c[2] += c[3] >> 16; c[3] &= 0xFFFF
    c[2] += a[2]*b[3]; c[1] += c[2] >> 16; c[2] &= 0xFFFF
    c[2] += a[3]*b[2]; c[1] += c[2] >> 16; c[2] &= 0xFFFF
    c[1] += a[1]*b[3]; c[0] += c[1] >> 16; c[1] &= 0xFFFF
    c[1] += a[2]*b[2]; c[0] += c[1] >> 16; c[1] &= 0xFFFF
    c[1] += a[3]*b[1]; c[0] += c[1] >> 16; c[1] &= 0xFFFF
    c[0] += a[0]*b[3] + a[1]*b[2] + a[2]*b[1] + a[3]*b[0]; c[0] &= 0xFFFF
    return [(c[0] << 16) | c[1], (c[2] << 16) | c[3]]


def _rot64(a, b):
    b %= 64
    if b == 32: return [a[1], a[0]]
    if b < 32:
        return [(a[0] << b | a[1] >> (32-b)) & 0xFFFFFFFF,
                (a[1] << b | a[0] >> (32-b)) & 0xFFFFFFFF]
    b -= 32
    return [(a[1] << b | a[0] >> (32-b)) & 0xFFFFFFFF,
            (a[0] << b | a[1] >> (32-b)) & 0xFFFFFFFF]


def _xor64(a, b):
    return [(a[0] ^ b[0]) & 0xFFFFFFFF, (a[1] ^ b[1]) & 0xFFFFFFFF]


def _shl64(a, b):
    if b == 0: return a
    if b < 32: return [(a[1] << b >> 32) & 0xFFFFFFFF, (a[1] << b) & 0xFFFFFFFF]
    return [(a[1] << (b-32)) & 0xFFFFFFFF, 0]


def _mix64(a):
    a = _xor64(a, [0, a[0] >> 1])
    a = _mul64(a, [0xFF51AFD7, 0xED558CCD])
    a = _xor64(a, [0, a[0] >> 1])
    a = _mul64(a, [0xC4CEB9FE, 0x1A85EC53])
    a = _xor64(a, [0, a[0] >> 1])
    return a


def _u32(v): return v & 0xFFFFFFFF


def _to_utf8(s):
    out = []
    for ch in s:
        c = ord(ch)
        if c < 128: out.append(c)
        else: out.extend(ch.encode('utf-8'))
    return out


def fingerprint_hash(data, seed=0, cap=None, tail=None):
    """MurmurHash3 x64 128 位。"""
    t = _to_utf8(data) if isinstance(data, str) else list(data)
    if cap: t = t[:cap]
    if tail: t = t + _to_utf8(tail)

    rem = len(t) % 16
    body = len(t) - rem
    h1, h2 = [0, seed], [0, seed]
    c1, c2 = [0x87C37B91, 0x114253D5], [0x4CF5AD43, 0x2745937F]

    d = 0
    while d < body:
        k1 = [(255&t[d+4])|((255&t[d+5])<<8)|((255&t[d+6])<<16)|((255&t[d+7])<<24),
              (255&t[d])|((255&t[d+1])<<8)|((255&t[d+2])<<16)|((255&t[d+3])<<24)]
        k2 = [(255&t[d+12])|((255&t[d+13])<<8)|((255&t[d+14])<<16)|((255&t[d+15])<<24),
              (255&t[d+8])|((255&t[d+9])<<8)|((255&t[d+10])<<16)|((255&t[d+11])<<24)]
        k1 = _mul64(k1, c1); k1 = _rot64(k1, 31); k1 = _mul64(k1, c2)
        h1 = _xor64(h1, k1); h1 = _rot64(h1, 27); h1 = _add64(h1, h2)
        h1 = _add64(_mul64(h1, [0, 5]), [0, 0x52DCE729])
        k2 = _mul64(k2, c2); k2 = _rot64(k2, 33); k2 = _mul64(k2, c1)
        h2 = _xor64(h2, k2); h2 = _rot64(h2, 31); h2 = _add64(h2, h1)
        h2 = _add64(_mul64(h2, [0, 5]), [0, 0x38495AB5])
        d += 16

    k1, k2 = [0, 0], [0, 0]
    if rem >= 15: k2 = _xor64(k2, _shl64([0, t[d+14]], 48))
    if rem >= 14: k2 = _xor64(k2, _shl64([0, t[d+13]], 40))
    if rem >= 13: k2 = _xor64(k2, _shl64([0, t[d+12]], 32))
    if rem >= 12: k2 = _xor64(k2, _shl64([0, t[d+11]], 24))
    if rem >= 11: k2 = _xor64(k2, _shl64([0, t[d+10]], 16))
    if rem >= 10: k2 = _xor64(k2, _shl64([0, t[d+9]], 8))
    if rem >= 9:
        k2 = _xor64(k2, [0, t[d+8]])
        k2 = _mul64(k2, c2); k2 = _rot64(k2, 33); k2 = _mul64(k2, c1); h2 = _xor64(h2, k2)
    if rem >= 8: k1 = _xor64(k1, _shl64([0, t[d+7]], 56))
    if rem >= 7: k1 = _xor64(k1, _shl64([0, t[d+6]], 48))
    if rem >= 6: k1 = _xor64(k1, _shl64([0, t[d+5]], 40))
    if rem >= 5: k1 = _xor64(k1, _shl64([0, t[d+4]], 32))
    if rem >= 4: k1 = _xor64(k1, _shl64([0, t[d+3]], 24))
    if rem >= 3: k1 = _xor64(k1, _shl64([0, t[d+2]], 16))
    if rem >= 2: k1 = _xor64(k1, _shl64([0, t[d+1]], 8))
    if rem >= 1:
        k1 = _xor64(k1, [0, t[d]])
        k1 = _mul64(k1, c1); k1 = _rot64(k1, 31); k1 = _mul64(k1, c2); h1 = _xor64(h1, k1)

    h1 = _xor64(h1, [0, len(t)]); h2 = _xor64(h2, [0, len(t)])
    h1 = _add64(h1, h2); h2 = _add64(h2, h1)
    h1 = _mix64(h1); h2 = _mix64(h2)
    h1 = _add64(h1, h2); h2 = _add64(h2, h1)

    return (format(_u32(h1[0]),'08x') + format(_u32(h1[1]),'08x') +
            format(_u32(h2[0]),'08x') + format(_u32(h2[1]),'08x'))


# ====================== SM4-CBC 加密实现 ======================

_S = [
    214,144,233,254,204,225,61,183,22,182,20,194,40,251,44,5,
    43,103,154,118,42,190,4,195,170,68,19,38,73,134,6,153,
    156,66,80,244,145,239,152,122,51,84,11,67,237,207,172,98,
    228,179,28,169,201,8,232,149,128,223,148,250,117,143,63,166,
    71,7,167,252,243,115,23,186,131,89,60,25,230,133,79,168,
    104,107,129,178,113,100,218,139,248,235,15,75,112,86,157,53,
    30,36,14,94,99,88,209,162,37,34,124,59,1,33,120,135,
    212,0,70,87,159,211,39,82,76,54,2,231,160,196,200,158,
    234,191,138,210,64,199,56,181,163,247,242,206,249,97,21,161,
    224,174,93,164,155,52,26,85,173,147,50,48,245,140,177,227,
    29,246,226,46,130,102,202,96,192,41,35,171,13,83,78,111,
    213,219,55,69,222,253,142,47,3,255,106,114,109,108,91,81,
    141,27,175,146,187,221,188,127,17,217,92,65,31,16,90,216,
    10,193,49,136,165,205,123,189,45,116,208,18,184,229,180,176,
    137,105,151,74,12,150,119,126,101,185,241,9,197,110,198,132,
    24,240,125,236,58,220,77,32,121,238,95,62,215,203,57,72
]

_CK = [
    462357,472066609,943670861,1415275113,1886879365,2358483617,
    2830087869,3301692121,3773296373,4228057617,404694573,876298825,
    1347903077,1819507329,2291111581,2762715833,3234320085,3705924337,
    4177462797,337322537,808926789,1280531041,1752135293,2223739545,
    2695343797,3166948049,3638552301,4110090761,269950501,741554753,
    1213159005,1684763257
]

_FK = [0xA3B1BAC6, 0x56AA3350, 0x677D9197, 0xB27022DC]


def _rl32(x, n):
    n &= 31; return ((x << n) | (x >> (32-n))) & 0xFFFFFFFF

def _sb32(x):
    return ((_S[(x>>24)&0xFF]&0xFF)<<24)|((_S[(x>>16)&0xFF]&0xFF)<<16)|((_S[(x>>8)&0xFF]&0xFF)<<8)|(_S[x&0xFF]&0xFF)

def _tf(x):
    b = _sb32(x); return b ^ _rl32(b,2) ^ _rl32(b,10) ^ _rl32(b,18) ^ _rl32(b,24)

def _tp(x):
    b = _sb32(x); return b ^ _rl32(b,13) ^ _rl32(b,23)

def _rkeys(kb, enc=True):
    mk = [0]*4
    for i in range(4):
        mk[i] = (kb[4*i]<<24)|(kb[4*i+1]<<16)|(kb[4*i+2]<<8)|kb[4*i+3]
    k = [mk[i] ^ _FK[i] for i in range(4)]
    rk = [0]*32
    for i in range(0, 32, 4):
        rk[i] = k[0] ^ _tp(k[1]^k[2]^k[3]^_CK[i]); k[0] = rk[i]
        rk[i+1] = k[1] ^ _tp(k[2]^k[3]^k[0]^_CK[i+1]); k[1] = rk[i+1]
        rk[i+2] = k[2] ^ _tp(k[3]^k[0]^k[1]^_CK[i+2]); k[2] = rk[i+2]
        rk[i+3] = k[3] ^ _tp(k[0]^k[1]^k[2]^_CK[i+3]); k[3] = rk[i+3]
    if not enc:
        for i in range(16): rk[i], rk[31-i] = rk[31-i], rk[i]
    return rk

def _block_enc(blk, out, rk):
    x = [0]*4
    for i in range(4):
        x[i] = (blk[4*i]<<24)|(blk[4*i+1]<<16)|(blk[4*i+2]<<8)|blk[4*i+3]
    for i in range(0, 32, 4):
        x[0] ^= _tf(x[1]^x[2]^x[3]^rk[i])
        x[1] ^= _tf(x[2]^x[3]^x[0]^rk[i+1])
        x[2] ^= _tf(x[3]^x[0]^x[1]^rk[i+2])
        x[3] ^= _tf(x[0]^x[1]^x[2]^rk[i+3])
    for i in range(0, 16, 4):
        idx = 3 - i // 4
        out[i] = (x[idx]>>24)&0xFF; out[i+1] = (x[idx]>>16)&0xFF
        out[i+2] = (x[idx]>>8)&0xFF; out[i+3] = x[idx]&0xFF

def _hex2b(h): return [int(h[i:i+2], 16) for i in range(0, len(h), 2)]

def _str2b(s):
    r = []
    for ch in s:
        cp = ord(ch)
        if cp <= 127: r.append(cp)
        elif cp <= 2047: r.append(0xC0|(cp>>6)); r.append(0x80|(cp&0x3F))
        elif cp <= 55295 or (57344 <= cp <= 65535):
            r.append(0xE0|(cp>>12)); r.append(0x80|((cp>>6)&0x3F)); r.append(0x80|(cp&0x3F))
        elif 65536 <= cp <= 1114111:
            r.append(0xF0|((cp>>18)&0x1C)); r.append(0x80|((cp>>12)&0x3F))
            r.append(0x80|((cp>>6)&0x3F)); r.append(0x80|(cp&0x3F))
    return r

def cipher_enc(plain, key_hex, iv_hex):
    """返回 SM4-CBC 加密后的十六进制字符串。"""
    kb = _hex2b(key_hex); ib = _hex2b(iv_hex)
    data = _str2b(plain)
    pad = 16 - (len(data) % 16); data.extend([pad]*pad)
    rk = _rkeys(kb, True)
    out = []; prev = ib[:]
    for off in range(0, len(data), 16):
        blk = data[off:off+16]
        xb = [(blk[i]^prev[i]) for i in range(16)]
        o = [0]*16; _block_enc(xb, o, rk); out.extend(o); prev = o[:]
    return ''.join(format(b, '02x') for b in out)

def cipher_dec(ct_hex, key_hex, iv_hex):
    """返回 SM4-CBC 解密后的文本。"""
    kb = _hex2b(key_hex); ib = _hex2b(iv_hex); data = _hex2b(ct_hex)
    rk = _rkeys(kb, False)
    out = []; prev = ib[:]
    for off in range(0, len(data), 16):
        blk = data[off:off+16]; o = [0]*16; _block_enc(blk, o, rk)
        out.extend([(o[i]^prev[i]) for i in range(16)]); prev = blk[:]
    p = out[-1]
    if 1 <= p <= 16: out = out[:-p]
    return bytes(out).decode('utf-8')


# ====================== BotShield SM4 密钥派生 ======================

def _derive_key(seed_str):
    r = fingerprint_hash(seed_str + "_bsdk_", seed=27)
    return r.lower() if r else "9eqaw+jthssyswpl9eqaw+jthssyswpl"


# ====================== BotShield 随机 ID 与 GUID ======================

def _rand_id():
    # 对齐 py1.9.6/crypto.py:get_random_id，用毫秒时间戳和随机 nibble 生成 32 位随机串。
    parts = [str(int(time.time() * 1000)) for _ in range(3)]
    tpl = ''.join(parts).replace(".", "x")[:32].ljust(32, "x")
    ts = int(time.time() * 1000)
    buf = []
    for ch in tpl:
        r = (ts + random.randint(0,15)) % 16; ts //= 16
        buf.append(format(r if ch == 'x' else (r&0x3)|0x8, 'x'))
    return ''.join(buf)


def _make_guid():
    # 对齐 py1.9.6/crypto.py:_guid，生成类 UUID 字符串供 H5 指纹和 cookie 使用。
    ts = int(time.time() * 1000)
    tpl = "xxxxxxxx-xxxx-xxxx-yxxx-xxxxxxxxxxxx"
    buf = []
    for ch in tpl:
        if ch == '-': buf.append('-')
        elif ch in ('x','y'):
            r = (ts + int(random.random()*16)) % 16; ts //= 16
            buf.append(format(r if ch=='x' else (r&0x3)|0x8, 'x'))
        else: buf.append(ch)
    return ''.join(buf)


def _base36(n):
    if n == 0: return "0"
    c = "0123456789abcdefghijklmnopqrstuvwxyz"; r = []
    while n > 0: r.append(c[n%36]); n //= 36
    return ''.join(reversed(r))


# ====================== BotShield 对象序列化 ======================

def _serialize(obj, depth=0):
    depth = depth + 1 if isinstance(depth, int) else 1
    if depth == 1 and (obj is None or obj != obj): return ""
    if obj is None: return "|-|-|"
    if isinstance(obj, str): return obj
    if isinstance(obj, bool):
        if depth == 1: return ""
        return obj
    if isinstance(obj, (int, float)):
        return obj
    if isinstance(obj, list):
        if depth > 10: return "[]"
        items = [_serialize(x, depth) for x in obj]
        return json.dumps([x for x in items if x != "|-|-|"], separators=(',',':'), ensure_ascii=False)
    if isinstance(obj, dict):
        if depth > 10: return ""
        parts = []; keys = sorted(obj.keys())
        for k in keys:
            v = _serialize(obj[k], depth)
            if v != "|-|-|": parts.append(f"{k}={v};")
        return "".join(parts)
    return str(obj)


def _dict_to_arr(obj):
    keys = sorted(obj.keys(), key=str); r = []
    for k in keys:
        if k == "$1": continue
        v = obj[k]
        if v is None: r.append(None)
        elif isinstance(v, dict): r.append(_dict_to_arr(v))
        elif isinstance(v, list): r.append([_dict_to_arr(i) if isinstance(i, dict) else i for i in v])
        else: r.append(v)
    return r


# ====================== BotShield 数据封装与加密 ======================

def _wrap_data(data, sk=None, dk=None, iv=None):
    # 对齐 py1.9.6/crypto.py:process_data，SM4-CBC 的 key 和 iv 都取 dk。
    if not sk:
        sk = _rand_id()
    if not dk: dk = _derive_key(sk)
    if not iv: iv = ''.join(format(1^int(c,16),'x') for c in sk)
    pt = data if isinstance(data, str) else json.dumps(data, separators=(',',':'), ensure_ascii=False)
    enc = cipher_enc(pt, dk, dk)
    return iv + enc, sk, dk, iv


def _wrap_data_wasm(data, timestamp_ms=None) -> Optional[str]:
    """使用 WASM en() 生成 Content-Web-Bb；失败时返回 None 走 SM4 兜底。"""
    state = _acquire_shield_instance()
    if state is None:
        return None
    prev_now = None
    try:
        store, inst = state["store"], state["instance"]
        en_fn = inst.exports(store)["en"]
        free_memory_fn = inst.exports(store)["free_memory"]
        free_fn = inst.exports(store)["free"]
        pt = data if isinstance(data, str) else json.dumps(data, separators=(',', ':'), ensure_ascii=False)
        prev_now = getattr(_wasm_tls, "now_ms", None)
        if timestamp_ms not in (None, ""):
            _wasm_tls.now_ms = int(timestamp_ms)
        ptr = _wasm_wstr(store, inst, pt)
        rp = en_fn(store, ptr)
        rs = _wasm_rstr(store, inst, rp) if rp else None
        if rp:
            free_memory_fn(store, rp)
        free_fn(store, ptr)
        if rs and len(rs) > 32 and not any(token in rs.lower() for token in ("not initialized", "not loaded", "failed", "error")):
            return rs
        return None
    except Exception:
        return None
    finally:
        if timestamp_ms not in (None, ""):
            if prev_now is None:
                try:
                    delattr(_wasm_tls, "now_ms")
                except AttributeError:
                    pass
            else:
                _wasm_tls.now_ms = prev_now
        _release_shield_instance(state)


# ====================== WASM 运行时与实例池 ======================

try:
    import wasmtime as _wt
    _WT_OK = True
except ImportError:
    _WT_OK = False

_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
_WASM_BIN = os.path.join(_DIR, "runtime.wasm")
_SIGN_BIN = os.path.join(_DIR, "ext.bin")

_wasm_tls = threading.local()
_wasm_engine = None
_wasm_module = None
_wasm_store = None
_wasm_instance = None
_wasm_init_lock = threading.Lock()
_w_sign_bytes: Optional[bytes] = None
_w_sign_bytes_lock = threading.Lock()

_wasm_pool: list = []
_wasm_pool_lock = threading.Lock()
_wasm_pool_sem: Optional[threading.Semaphore] = None
_WASM_POOL_SIZE = int(os.environ.get("MT_WASM_POOL_SIZE", "128"))


def _wasm_sign_bytes() -> Optional[bytes]:
    global _w_sign_bytes
    if _w_sign_bytes is not None:
        return _w_sign_bytes
    with _w_sign_bytes_lock:
        if _w_sign_bytes is not None:
            return _w_sign_bytes
        if not os.path.exists(_SIGN_BIN):
            _w_sign_bytes = None
            return None
        with open(_SIGN_BIN, "rb") as f:
            _w_sign_bytes = f.read()
        return _w_sign_bytes


def _wasm_rstr(store, inst, ptr):
    mem = inst.exports(store)["memory"]
    buf = mem.data_ptr(store)
    sz = mem.data_len(store)
    r = bytearray()
    i = 0
    while ptr + i < sz and buf[ptr + i] != 0:
        r.append(buf[ptr + i])
        i += 1
    return r.decode("utf-8")


def _wasm_wstr(store, inst, s):
    fn = inst.exports(store)["malloc"]
    mem = inst.exports(store)["memory"]
    enc = s.encode("utf-8") + b"\x00"
    ptr = fn(store, len(enc))
    buf = mem.data_ptr(store)
    for i, b in enumerate(enc):
        buf[ptr + i] = b
    return ptr


def _load_shield_module():
    global _wasm_engine, _wasm_module
    if _wasm_module is not None:
        return _wasm_engine, _wasm_module
    with _wasm_init_lock:
        if _wasm_module is not None:
            return _wasm_engine, _wasm_module
        if not _WT_OK or not os.path.exists(_WASM_BIN):
            return None, None
        _wasm_engine = _wt.Engine()
        _wasm_module = _wt.Module.from_file(_wasm_engine, _WASM_BIN)
    return _wasm_engine, _wasm_module


def _create_shield_instance(engine, module):
    store = _wt.Store(engine)
    wasi_config = _wt.WasiConfig()
    store.set_wasi(wasi_config)
    linker = _wt.Linker(engine)
    linker.define_wasi()
    i32 = _wt.ValType.i32()
    f64 = _wt.ValType.f64()
    inst_ref = [None]

    def _js_invoke(tp, mp, ap):
        inst = inst_ref[0]
        if inst is None:
            return 0
        target = _wasm_rstr(store, inst, tp)
        method = _wasm_rstr(store, inst, mp)
        if target == "Date" and method == "now":
            result = int(getattr(_wasm_tls, "now_ms", 0) or int(time.time() * 1000))
        elif target == "window" and method == "eval":
            result = 0
        else:
            result = None
        return _wasm_wstr(store, inst, json.dumps(result))

    linker.define_func("env", "js_invoke", _wt.FuncType([i32, i32, i32], [i32]), _js_invoke)
    linker.define_func("env", "exit", _wt.FuncType([i32], []), lambda _code: None)
    linker.define_func("env", "__cxa_throw", _wt.FuncType([i32, i32, i32], []), lambda _a, _b, _c: None)
    linker.define_func(
        "env",
        "emscripten_date_now",
        _wt.FuncType([], [f64]),
        lambda: float(int(getattr(_wasm_tls, "now_ms", 0) or int(time.time() * 1000))),
    )
    linker.define_func("env", "emscripten_resize_heap", _wt.FuncType([i32], [i32]), lambda _size: 0)
    linker.define_func("env", "_abort_js", _wt.FuncType([], []), lambda: None)
    linker.define_func("env", "_tzset_js", _wt.FuncType([i32, i32, i32, i32], []), lambda _a, _b, _c, _d: None)
    instance = linker.instantiate(store, module)
    inst_ref[0] = instance
    try:
        instance.exports(store)["__wasm_call_ctors"](store)
    except Exception:
        pass
    return {"engine": engine, "store": store, "instance": instance}


def _ensure_shield_pool():
    """懒加载共享 WASM engine/module 和实例池信号量。"""
    global _wasm_pool_sem
    if _wasm_pool_sem is not None:
        return True
    engine, module = _load_shield_module()
    if engine is None:
        return False
    _wasm_sign_bytes()
    with _wasm_pool_lock:
        if _wasm_pool_sem is not None:
            return True
        _wasm_pool_sem = threading.Semaphore(_WASM_POOL_SIZE)
    return True


def _acquire_shield_instance():
    if not _ensure_shield_pool():
        return None
    _wasm_pool_sem.acquire()
    with _wasm_pool_lock:
        if _wasm_pool:
            return _wasm_pool.pop()
    try:
        engine, module = _load_shield_module()
        if engine is None:
            _wasm_pool_sem.release()
            return None
        return _create_shield_instance(engine, module)
    except Exception:
        _wasm_pool_sem.release()
        return None


def _release_shield_instance(state):
    if state is None:
        return
    with _wasm_pool_lock:
        _wasm_pool.append(state)
    _wasm_pool_sem.release()


def prewarm_pool(target: int = None, workers: int = 8) -> int:
    """预热 WASM 实例并返回当前池大小。"""
    if not _ensure_shield_pool():
        return 0
    if target is None:
        target = _WASM_POOL_SIZE
    target = max(0, min(_WASM_POOL_SIZE, int(target)))
    engine, module = _load_shield_module()
    if engine is None:
        return 0
    with _wasm_pool_lock:
        have = len(_wasm_pool)
    need = max(0, target - have)
    if need == 0:
        return have

    import concurrent.futures

    def _make_one():
        try:
            return _create_shield_instance(engine, module)
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, need))) as ex:
        for inst in ex.map(lambda _: _make_one(), range(need)):
            if inst is None:
                continue
            with _wasm_pool_lock:
                _wasm_pool.append(inst)
    with _wasm_pool_lock:
        return len(_wasm_pool)


def _init_wasm():
    return _ensure_shield_pool()


def compute_wasm_sig(did, sig, extra=None, timestamp_ms=None):
    """计算 WASM run_wac 签名。"""
    wb = extra or _wasm_sign_bytes()
    if not wb:
        return None
    state = _acquire_shield_instance()
    if state is None:
        return None
    prev_now = None
    try:
        prev_now = getattr(_wasm_tls, "now_ms", None)
        if timestamp_ms not in (None, ""):
            _wasm_tls.now_ms = int(timestamp_ms)
        store, inst = state["store"], state["instance"]
        ma = inst.exports(store)["malloc"]
        fr = inst.exports(store)["free"]
        rw = inst.exports(store)["run_wac"]
        fm = inst.exports(store)["free_memory"]
        mem = inst.exports(store)["memory"]
        dp = _wasm_wstr(store, inst, did)
        dl = len(did.encode("utf-8"))
        sp = _wasm_wstr(store, inst, sig)
        sl = len(sig.encode("utf-8"))
        wp = ma(store, len(wb))
        buf = mem.data_ptr(store)
        for i, b in enumerate(wb):
            buf[wp + i] = b
        rp = rw(store, dp, dl, sp, sl, wp, len(wb))
        rs = None
        if rp:
            rs = _wasm_rstr(store, inst, rp)
            fm(store, rp)
            if rs and any(token in rs.lower() for token in ("not initialized", "not loaded", "failed", "error")):
                rs = None
        fr(store, dp)
        fr(store, sp)
        fr(store, wp)
        return rs
    except Exception:
        return None
    finally:
        if timestamp_ms not in (None, ""):
            if prev_now is None:
                try:
                    delattr(_wasm_tls, "now_ms")
                except AttributeError:
                    pass
            else:
                _wasm_tls.now_ms = prev_now
        _release_shield_instance(state)


# ====================== Content-Web-Bb / Content-Hh-Bb 请求头生成 ======================

_SLOT = {
    "appid":0, "devid":1, "sessid":2, "ua":3, "devfp":4,
    "hp":5, "nonce":6, "sig":7, "ts":8, "ext":9,
    "uid":10, "wv":11, "ws":12,
}

def build_shield_headers(
    req_data, appid=DEFAULT_APPID, devid="", sessid="",
    uid="", ua="", devfp="0", hp="0",
    wasm_ver=WASM_TAG, wasm_sig="",
    sk=None, dk=None, iv=None,
    rush=False, wasm_bytes=None, timestamp_ms=None,
    wasm_outer=False,
):
    # 对齐 py1.9.6/crypto.py:generate_content_web_bb：先序列化请求数据计算 Content-Hh-Bb，再按字段顺序组装 BB 数组并加密。
    nonce = _rand_id()
    sig = fingerprint_hash(req_data, seed=27, cap=4096, tail=nonce)
    try:
        ts_ms = int(timestamp_ms) if timestamp_ms not in (None, "") else int(adjusted_unix_ms())
    except (TypeError, ValueError):
        ts_ms = int(adjusted_unix_ms())
    # ProxyPin 2026-05-20 rushPurchase 抓包使用 SM4 外层加密，141 为空。
    # 可选 wasm_outer 会保持字段和签名输入不变，再填充 141 并通过 WASM en() 加密。
    if wasm_outer and not wasm_sig:
        ws = compute_wasm_sig(devid, sig, wasm_bytes, timestamp_ms=ts_ms)
        if not ws:
            raise RuntimeError("shield.py: compute_wasm_sig returned empty while wasm_outer=True")
        wasm_sig = ws
    elif not rush and not wasm_sig:
        ws = compute_wasm_sig(devid, sig, wasm_bytes, timestamp_ms=ts_ms)
        if ws: wasm_sig = ws

    obj = {
        "101": appid, "103": devid, "104": sessid, "105": ua,
        "106": devfp, "108": hp, "109": nonce, "110": sig,
        "111": ts_ms, "120": {},
        "130": str(uid), "140": wasm_ver, "141": wasm_sig,
    }
    arr = _dict_to_arr(obj)
    # 默认对齐抓包到的 rushPurchase BotShield 形态：SM4-CBC 外层加密，sign_wasm 使用当前版本。
    method = "sm4"
    enc = None
    if wasm_outer:
        enc = _wrap_data_wasm(arr, timestamp_ms=ts_ms)
        if not enc:
            raise RuntimeError("shield.py: wasm en() returned empty while wasm_outer=True")
        method = "wasm"
    else:
        enc, sk, dk, iv = _wrap_data(arr, sk, dk, iv)
    return {
        "Content-Web-Bb": enc,
        "Sdk-Ver-Bb": SHIELD_VER,
        "Content-Hh-Bb": sig,
        "_dbg": {
            "encrypt_method": method,
            "sk": sk,
            "dk": dk,
            "iv": iv,
            "nonce": nonce,
            "sig": sig,
            "ws": wasm_sig,
            "raw": obj,
            "arr": arr,
        },
    }


def decrypt_shield(val):
    """解密 Content-Web-Bb。"""
    iv_part, ct = val[:32], val[32:]
    sk = ''.join(format(1^int(c,16),'x') for c in iv_part)
    dk = _derive_key(sk)
    raw = cipher_dec(ct, dk, dk)
    data = json.loads(raw)
    fields = {name: data[idx] if idx < len(data) else None for name, idx in _SLOT.items()}
    return {"raw": raw, "data": data, "fields": fields, "_key": {"iv":iv_part,"sk":sk,"dk":dk}}


def shield_for_post(body, **kw):
    if isinstance(body, str):
        try: body = json.loads(body)
        except: pass
    s = _serialize(body) if not isinstance(body, str) else body
    if not isinstance(s, str): s = str(s)
    return build_shield_headers(s, **kw)

def shield_for_get(url, **kw):
    q = ""
    if "?" in url: q = url[url.index("?")+1:]
    pairs = []
    if q:
        for p in q.split("&"):
            idx = p.find("=")
            pairs.append([p[:idx], p[idx+1:]] if idx > -1 else [p, ""])
    pairs.sort(key=lambda x: x[0])
    s = "&".join(f"{p[0]}={p[1]}" for p in pairs)
    return build_shield_headers(s, **kw)


# ====================== App 设备标识与环境标签 ======================

def make_raw_id(imei="", mac="", aid="", serial=""):
    if imei and mac: s = imei + "@" + mac
    elif mac: s = mac
    elif imei: s = imei
    elif aid: s = aid
    elif serial: s = serial
    else: s = str(_uuid.uuid4())
    return _md5_raw(s)

def make_device_token(raw_id):
    if not raw_id: return raw_id
    return "clips_" + base64.b64encode(_stream_xor(raw_id, _XOR_SEED).encode('utf-8')).decode('utf-8')


def _invert_stream_xor(buf: str, seed_init: int = _XOR_SEED) -> str:
    """把 raw_id 转为 clips_ 设备 ID。"""
    raw_chars = []
    for i, ch in enumerate(buf):
        out_ord = ord(ch)
        prev_seed = seed_init if i == 0 else ord(buf[i - 1])
        raw_chars.append(chr(prev_seed ^ out_ord))
    return "".join(raw_chars)


def raw_id_from_mt_device_token(device_token: str) -> Optional[str]:
    """把 MT clips_ 设备 ID 还原为 raw_id。"""
    if not device_token or not device_token.startswith("clips_"):
        return None
    try:
        xor_str = base64.b64decode(device_token[6:]).decode("utf-8")
    except Exception:
        return None
    return _invert_stream_xor(xor_str, _XOR_SEED)


def raw_id_from_mt_jwt(jwt_str: str) -> Optional[str]:
    """从 MT JWT payload 的 deviceId 字段还原 raw_id。"""
    if not (jwt_str or "").strip():
        return None
    parts = jwt_str.split(".")
    if len(parts) < 2:
        return None
    try:
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except Exception:
        return None
    did = payload.get("deviceId")
    if not did or not isinstance(did, str):
        return None
    rid = raw_id_from_mt_device_token(did)
    return rid.lower() if rid else None


def make_env_tag(root=False, debug=False, proxy=False, inject=False):
    pt = f"root/{int(root)};debug/{int(debug)};proxy/{int(proxy)};inject/{int(inject)}"
    return "clips_" + base64.b64encode(_stream_xor(pt, _XOR_SEED).encode('utf-8')).decode('utf-8')

def make_cert_tag(cert_md5="2f5ae2348f427a666876d7da3563a7d2"):
    return "clips_" + base64.b64encode(_stream_xor(cert_md5, _XOR_SEED).encode('utf-8')).decode('utf-8')


def make_env_tag_for_raw_id(raw_id: str) -> str:
    """根据 raw_id 派生 MT-R。"""
    rid = (raw_id or "").strip()
    if not rid:
        return make_env_tag()
    b = int(_md5(rid)[4:6], 16)
    return make_env_tag(root=False, debug=bool(b & 1), proxy=bool(b & 2), inject=False)


def make_cert_tag_for_raw_id(raw_id: str) -> str:
    """根据 raw_id 派生 MT-SN / MT-ACB 指纹片段。"""
    rid = (raw_id or "").strip()
    if not rid:
        return make_cert_tag()
    idx = int(_md5(rid)[:8], 16) % len(_VALID_HASHES)
    return make_cert_tag(_VALID_HASHES[idx])


def _xor_encrypt_md5_hex_sn(sn_plain: str, key: int = 72) -> bytes:
    """对齐 mt1.9.6 xorEncrypt(snPlain, 72)。"""
    out = bytearray()
    current_key = key
    for char in sn_plain:
        encrypted = ord(char) ^ current_key
        out.append(encrypted & 0xFF)
        current_key = encrypted
    return bytes(out)


def generate_mt_sn() -> str:
    """生成新的 MT-SN 值。"""
    session_data = f"{_uuid.uuid4()}{adjusted_unix()}{random.random()}"
    sn_plain = _md5(session_data)
    buf = _xor_encrypt_md5_hex_sn(sn_plain, 72)
    return "clips_" + base64.b64encode(buf).decode("ascii")


def format_d_time_mt_order_header() -> str:
    """格式化 MT-DTIME。"""
    return format_mt_dtime_gmt08()


# ====================== MT-K / MT-V 请求签名 ======================

def sign_request(ts, dev_id, extra="", ver=RELEASE_VER, plat="android"):
    combined = ("iOS" if plat.lower()=="ios" else "android") + ts + dev_id + extra + ver
    chars = list(combined)
    n = len(chars)
    for i in range(n-1):
        for j in range(n-1-i):
            if chars[j] > chars[j+1]: chars[j], chars[j+1] = chars[j+1], chars[j]
    sf = _SIGN_CHARS[random.randint(0,36)]
    h = _md5(''.join(chars) + sf)
    return h[:26] + sf

def make_kv_pair(dev_id, ver=RELEASE_VER, plat="android", timestamp_ms=None):
    try:
        k = str(int(timestamp_ms)) if timestamp_ms not in (None, "") else str(adjusted_unix_ms())
    except (TypeError, ValueError):
        k = str(adjusted_unix_ms())
    return k, sign_request(k, dev_id, "", ver, plat)


def sign_mtv_h5_rush(ts: str, device_info: str, ver: Optional[str] = None) -> str:
    """根据 MT-K 和 device_info 生成 H5 rushPurchase 的 MT-V。"""
    ver = ver or RELEASE_VER
    base_str = f"{ts}{device_info or ''}{ver}android"
    chars = list(base_str)
    n = len(chars)
    for i in range(n - 1):
        for j in range(n - 1 - i):
            if chars[j] > chars[j + 1]:
                chars[j], chars[j + 1] = chars[j + 1], chars[j]
    sorted_str = "".join(chars)
    suffix = _SIGN_CHARS[random.randint(0, 36)]
    h = _md5(sorted_str + suffix)
    return h[:26] + suffix


def make_h5_rush_kv_pair(device_info: str, ver: Optional[str] = None) -> Tuple[str, str]:
    """根据 MT-K 和 device_info 生成 H5 rushPurchase 的 MT-V。"""
    k = str(adjusted_unix_ms())
    return k, sign_mtv_h5_rush(k, device_info or "", ver)


# ====================== actParam AES 参数加密 ======================

def param_encrypt(plaintext):
    try:
        from Crypto.Cipher import AES; from Crypto.Util.Padding import pad
        c = AES.new(_ENC_SECRET, AES.MODE_CBC, _ENC_NONCE)
        ct = c.encrypt(pad(plaintext.encode('utf-8'), AES.block_size))
    except ImportError:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
        pd = padding.PKCS7(128).padder(); d = pd.update(plaintext.encode('utf-8')) + pd.finalize()
        c = Cipher(algorithms.AES(_ENC_SECRET), modes.CBC(_ENC_NONCE))
        ct = c.encryptor().update(d) + c.encryptor().finalize()
    return base64.b64encode(ct).decode('utf-8')

def param_decrypt(b64ct):
    ct = base64.b64decode(b64ct)
    try:
        from Crypto.Cipher import AES; from Crypto.Util.Padding import unpad
        c = AES.new(_ENC_SECRET, AES.MODE_CBC, _ENC_NONCE)
        return unpad(c.decrypt(ct), AES.block_size).decode('utf-8')
    except ImportError:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
        c = Cipher(algorithms.AES(_ENC_SECRET), modes.CBC(_ENC_NONCE))
        pd = c.decryptor().update(ct) + c.decryptor().finalize()
        up = padding.PKCS7(128).unpadder()
        return (up.update(pd) + up.finalize()).decode('utf-8')


def seal_param(data):
    return param_encrypt(json.dumps(data, separators=(',',':'), ensure_ascii=False))

def unseal_param(enc):
    return json.loads(param_decrypt(enc))


# ====================== 瑞数 H5 设备指纹与 cookie ======================

_MODELS = [
    {"ua":"mozilla/5.0 (linux; android 14; sm-g991b build/up1a.231005.007; wv) applewebkit/537.36 (khtml, like gecko) version/4.0 chrome/124.0.6367.179 mobile safari/537.36",
     "gpu":"Adreno (TM) 660","gpuv":"Qualcomm~Adreno (TM) 660","mem":8,"dpr":3,"cpu":8},
    {"ua":"mozilla/5.0 (linux; android 13; pixel 7 build/tq3a.230901.001; wv) applewebkit/537.36 (khtml, like gecko) version/4.0 chrome/120.0.6099.230 mobile safari/537.36",
     "gpu":"Mali-G78","gpuv":"ARM~Mali-G78","mem":8,"dpr":2.625,"cpu":8},
    {"ua":"mozilla/5.0 (linux; android 14; 22081212c build/ukq1.230917.001; wv) applewebkit/537.36 (khtml, like gecko) version/4.0 chrome/122.0.6261.64 mobile safari/537.36",
     "gpu":"Adreno (TM) 730","gpuv":"Qualcomm~Adreno (TM) 730","mem":12,"dpr":2.75,"cpu":8},
    {"ua":"mozilla/5.0 (linux; android 13; v2254a build/tp1a.220624.014; wv) applewebkit/537.36 (khtml, like gecko) version/4.0 chrome/119.0.6045.193 mobile safari/537.36",
     "gpu":"Adreno (TM) 642L","gpuv":"Qualcomm~Adreno (TM) 642L","mem":8,"dpr":2.75,"cpu":8},
    {"ua":"mozilla/5.0 (linux; android 14; oph2201 build/up1a.231005.007; wv) applewebkit/537.36 (khtml, like gecko) version/4.0 chrome/123.0.6312.118 mobile safari/537.36",
     "gpu":"Adreno (TM) 730","gpuv":"Qualcomm~Adreno (TM) 730","mem":12,"dpr":3,"cpu":8},
]

def _clean_ua(ua):
    lo = ua.lower()
    for mk in ["nett","nt:","wifi","4g","5g"]:
        if mk in lo:
            idx = lo.index(mk); p = lo[:idx].rstrip(); rest = lo[idx+len(mk):]
            sp = rest.split(" ",1)
            return p + sp[1] if len(sp) > 1 else p
    return lo


def make_h5_devid(**kw):
    # 生成 H5 设备指纹 ID。
    m = random.choice(_MODELS)
    ua = kw.get("ua") or m["ua"]
    feats = [
        _clean_ua(ua), "", "24", str(kw.get("mem") or m["mem"]),
        str(kw.get("dpr") or m["dpr"]), str(kw.get("cpu") or m["cpu"]),
        "", "Linux armv8l", "-480", "", "3;true;true",
        "1","1","0","1","0","0","0","",
        fingerprint_hash(str(random.random()) + str(adjusted_unix()), seed=27)[:16],
        fingerprint_hash(str(random.random()) + str(adjusted_unix()) + "t", seed=27)[:16],
        "", fingerprint_hash(str(random.random()) + str(adjusted_unix()) + "w", seed=27)[:16],
        "", kw.get("gpu") or m["gpu"], kw.get("gpuv") or m["gpuv"],
        "37","20030107","","","0","0","0","srgb",
    ]
    fs = "_".join(feats)
    gp = _make_guid()[25:]
    fp = fingerprint_hash(fs, seed=27)[3:19]
    base = "h" + gp + fp
    cs = fingerprint_hash(base, seed=27)[6:10]
    return base + cs

def make_h5_session():
    return f"{_base36(int(adjusted_unix_ms()))}_{_base36(int(random.random()*(36**10)))[2:]}_4"

def make_bs_cookie(did, timestamp_ms=None):
    try:
        ts_ms = int(timestamp_ms) if timestamp_ms not in (None, "") else int(adjusted_unix_ms())
    except (TypeError, ValueError):
        ts_ms = int(adjusted_unix_ms())
    tr = str(ts_ms)[::-1]
    return f"bid-{tr}-{did[-4:]}-{_make_guid()[9:13]}"

def make_du_cookie(did, sessid, ua=None, domain="h5.moutai519.com.cn", hp="", appid=DEFAULT_APPID, timestamp_ms=None):
    ua = ua or _MODELS[0]["ua"]
    ua_s = ua[:ua.index(")")] if ")" in ua else ua[:50]
    try:
        ts_ms = int(timestamp_ms) if timestamp_ms not in (None, "") else int(adjusted_unix_ms())
    except (TypeError, ValueError):
        ts_ms = int(adjusted_unix_ms())
    if not hp:
        hp = fingerprint_hash(did + str(ts_ms), seed=27)[:16]
    obj = {
        "101": appid,
        "103": did,
        "105": sessid,
        "107": ua_s,
        "109": domain,
        "111": hp,
        "113": ts_ms,
    }
    enc, _, _, _ = _wrap_data(_dict_to_arr(obj))
    return enc


# ====================== 完整抢购请求构建 ======================

def build_rush(item, act_id, dev_id, cookie, amount="1", ver=RELEASE_VER,
               ua="", h5did=None, h5sid=None, h5uid="", wv=WASM_TAG):
    k, v = make_kv_pair(dev_id, ver)
    dt = make_device_token(dev_id); rt = make_env_tag(); sn = make_cert_tag()
    ap = seal_param({"amount":amount,"itemCode":item,"itemPriorityActId":act_id,
         "userInfoBaseContext":{"addressLat":"","addressLng":"","appUserAgent":ua,"deviceId":dt,"mtr":rt},
         "ydLogId":"","ydToken":""})
    body = {"actParam": ap}
    if not h5did: h5did = make_h5_devid()
    if not h5sid: h5sid = make_h5_session()
    bb = shield_for_post(body, appid=DEFAULT_APPID, devid=h5did, sessid=h5sid,
                         uid=h5uid, wasm_ver=wv, rush=True)
    hdr = {
        "MT-V":v,"MT-K":k,"MT-Info":TRACE_TOKEN,"MT-Device-ID":dt,
        "MT-APP-Version":ver,"MT-R":rt,"MT-SN":sn,
        "Content-Web-Bb":bb["Content-Web-Bb"],"Sdk-Ver-Bb":bb["Sdk-Ver-Bb"],
        "Content-Hh-Bb":bb["Content-Hh-Bb"],
        "User-Agent":ua,"content-type":"application/json",
        "Accept":"application/json, text/javascript, */*; q=0.01",
        "X-Requested-With":"XMLHttpRequest",
        "Origin":"https://h5.moutai519.com.cn",
        "Sec-Fetch-Site":"same-origin","Sec-Fetch-Mode":"cors","Sec-Fetch-Dest":"empty",
        "Accept-Encoding":"gzip, deflate, br","Accept-Language":"zh-CN,zh;q=0.9",
    }
    ck = {
        "MT-Token-Wap":cookie,"MT-Device-ID-Wap":dev_id,
        "_d_u":make_du_cookie(h5did,h5sid),"_bs_device_id":make_bs_cookie(h5did),
        "_sdk_v_":SHIELD_VER,
    }
    return hdr, ck, body


if __name__ == "__main__":
    print("=== shield.py 自检 ===")
    rid = make_raw_id(aid="test_android_id")
    dt = make_device_token(rid)
    k, v = make_kv_pair(rid)
    did = make_h5_devid()
    sid = make_h5_session()
    print(f"  MT-Device-ID: {dt}")
    print(f"  MT-K / MT-V:  {k} / {v}")
    print(f"  H5 设备 ID:   {did} (len={len(did)})")
    print(f"  H5 会话:      {sid}")
    ap = seal_param({"test": True})
    assert unseal_param(ap) == {"test": True}
    print("  AES 加解密: 通过")
    bb = shield_for_post({"actParam": ap}, devid=did, sessid=sid, uid="123")
    dec = decrypt_shield(bb["Content-Web-Bb"])
    assert dec["fields"]["appid"] == DEFAULT_APPID
    print("  Content-Web-Bb 解密校验: 通过")
    print("=== 自检通过 ===")
