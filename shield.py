#!/usr/bin/env python3
"""
闂傚倸鍊峰ù鍥敋瑜嶉湁闁绘垼妫勭粻鐘绘煙閹冩闁搞儺鍓﹂弫宥夋煟閹邦垰鐨洪柣鎾村灴濡懘顢曢姀鈥愁槱缂備礁顑嗙敮锟犲箖閻愮數纾兼俊顖濐嚙瀵灝鈹戦绛嬫當婵☆偅顨嗙粋宥呂旈崘顏嗭紲闁荤姴娲ゅ鍫曀夐悩缁樼厓鐟滄粓宕滃棰濇晩闁哄稁鍘肩粣妤佷繆閵堝倸浜鹃柣鎾卞€栭幈銊ヮ渻鐠囪弓澹曟俊?- 缂傚倸鍊搁崐鎼佸磹閻戣姤鍊块柨鏂垮⒔閻瑩鏌熼悜姗嗘當缁炬儳娼￠弻鐔煎箚閻楀牜妫勭紓?闂傚倸鍊搁崐椋庣矆娓氣偓楠炲鍨鹃幇浣圭稁缂傚倷鐒﹁摫闁告瑥绻橀弻鐔虹磼閵忕姵鐏嶉梺?闂傚倸鍊搁崐椋庣矆娴ｉ潻鑰块梺顒€绉埀顒婄畵瀹曠厧顭垮┑鍥ㄣ仢濠碘剝鎮傛俊?婵犵數濮烽。顔炬閺囥垹纾绘繛鎴欏焺閺佸嫰鏌涘☉鍗炵仚闁稿鎸搁埥澶愬箳閹惧褰囬梻浣芥〃缁讹繝宕抽敐鍛殾婵犲﹤瀚刊鎾煙缂佹ê绗傚瑙勬礃缁绘稒娼忛崜褍鍩岄梺鍝ュУ閼归箖鈥﹂崶顒€绠涙い鎴ｅГ閺傗偓?
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


# ====================== 闂傚倸鍊搁崐鐑芥嚄閸撲礁鍨濇い鏍亼閳ь剙鍟村畷銊р偓娑櫭禍杈ㄧ節閻㈤潧孝闁稿锕幊鎾诲锤濡や胶鍘藉┑掳鍊愰崑鎾绘煟濡も偓缁绘﹢宕洪姀銈呯睄闁割偅绻勯鎰攽閻戝洨绉甸柛鎾寸懄娣囧﹥绺介崨濠備缓濡炪倖鐗楀銊╂晬瀹ュ洨纾?======================

RELEASE_VER = "1.9.10"
SHIELD_VER = "V3.5.0_20260605.1_imaotai"
WASM_TAG = "v1.0.8"
BUNDLE_ID = "com.moutai.mall"
TRACE_TOKEN = "a3f9c2b8471de05f9b6c4e1287d5a9c1"
RUSH_MT_INFO = "028e7f96f6369cafe1d105579c5b9377"
# mt1.9.6 test-full-flow.js rushPurchase 婵犵數濮烽弫鎼佸磻閻樿绠垫い蹇撴缁€濠囨煃瑜滈崜姘辨崲濞戞瑥绶為悗锝庡亞椤︿即鎮楀▓鍨珮闁稿锕ユ穱濠囨嚋闂堟稓绐為柣搴秵娴滄粍绔熼崟顖涒拻濞达絽鎲￠崯鐐寸箾鐠囇呯暤鐎规洘鍨垮畷銊╊敍濠婂懐鍔稿┑鐘绘涧閸婃悂骞夐敓鐘茬９?MT-Info闂傚倸鍊搁崐鐑芥倿閿旈敮鍋撶粭娑樻噽閻瑩鏌熸潏楣冩闁稿顑夐弻娑樷槈閸楃偟浠紓浣哄У濮婂鍩€椤掆偓閸樻粓宕戦幘缁樼厱闁哄洢鍔屾禍鐐淬亜锜婚崶銊у幗闂佺粯顭堝畷鐢垫閻楀牏绠鹃柛婊冨暟缁夘喚鈧?H5 闂?TRACE_TOKEN 闂傚倸鍊搁崐椋庣矆娓氣偓楠炲鏁撻悩鍐蹭罕闂佸搫娲㈤崹鍦不鐟欏嫨浜滈柟鏉垮閻ｉ亶鏌涢妸銉モ偓鍧楃嵁閺嶎灔搴敆閳ь剚淇婃禒瀣厸?RUSH_MT_INFO = "028e7f96f6369cafe1d105579c5b9377"
DEFAULT_APPID = "10001"

# 闂傚倸鍊搁崐椋庣矆娓氣偓楠炲鍨鹃幇浣圭稁缂傚倷鐒﹁摫闁告瑥绻橀弻鐔虹磼閵忕姵鐏嶉梺绋块閿曨亪寮婚悢鍛婄秶闁告挆鍛闂備焦妞块崢浠嬨€冩繝鍥ц摕婵炴垶鐭▽顏堟煕閹炬せ鍋撳┑顔兼搐閳?
_ENC_SECRET = b"3a79d$e4c2169a#fbd24c583ae71c0b9"[:32]
_ENC_NONCE = b"7392645081974362"
_XOR_SEED = 72
_SIGN_CHARS = "01234567890abcdefghijklmnopqrstuvwxyz"
_VALID_HASHES = [
    "2f5ae2348f427a666876d7da3563a7d2",
    "dd6c1787c17943e9db0c59c0da3d20e5",
]


# ====================== 闂傚倸鍊搁崐鐑芥嚄閸撲焦鍏滈柛顐ｆ礀閻ら箖鏌ｉ幇顓犮偞闁哄绉归弻銊モ攽閸♀晜肖闂侀€炲苯鍘哥紒鑸佃壘閻ｇ兘濡搁埡濠冩櫍閻熸粌绉瑰鏌ユ偐缂佹ǚ鎷洪梻鍌氱墛娓氭危閸洘鐓曢幖绮光偓瀹犲煘濡?======================

def _md5(text: str) -> str:
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def _md5_raw(text: str) -> str:
    """MD5 without zero padding for Java Integer.toHexString compatibility."""
    digest = hashlib.md5(text.encode('utf-8')).digest()
    return ''.join(format(b, 'x') for b in digest)


def _stream_xor(data: str, seed: int = _XOR_SEED) -> str:
    """Streaming XOR with cumulative seed."""
    buf = []
    for ch in data:
        seed ^= ord(ch)
        buf.append(chr(seed))
    return ''.join(buf)


# ====================== 64婵犵數濮烽弫鎼佸磻閻樿绠垫い蹇撴缁躲倝鏌ｉ敐鍛伇闁活厽鎹囬弻鐔虹磼閵忕姵鐏堢紓浣哄Т缂嶅﹤顕ｉ崼鏇為唶婵﹩鍘介悵鏇㈡⒑閸濆嫭顥炵紒顔奸叄閸┾偓妞ゆ巻鍋撻柛妯荤矒瀹曟垿骞樼紒妯煎幈闂佽婢橀崥鈧紒銊ㄥГ閵囧嫰骞橀悷棰佹睏缂?======================

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
    """MurmurHash3 x64 128-bit"""
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


# ====================== SM4 闂傚倸鍊搁崐椋庣矆娓氣偓楠炲鏁嶉崟顒佹闂佸湱鍎ら崵锕€鈽夊Ο閿嬵潔濠殿喗锕╅崜娆撴倶婵犲洦鍊垫鐐茬仢閸旀碍绻涢懠顒€鈻堢€规洘鍨块獮妯肩磼濮楀棙顥堟繝鐢靛仦閸ㄥ爼鎮烽姣兼盯濡舵径瀣ф嫼?======================

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
    """Return SM4-CBC encrypted hex."""
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
    """Return SM4-CBC decrypted text."""
    kb = _hex2b(key_hex); ib = _hex2b(iv_hex); data = _hex2b(ct_hex)
    rk = _rkeys(kb, False)
    out = []; prev = ib[:]
    for off in range(0, len(data), 16):
        blk = data[off:off+16]; o = [0]*16; _block_enc(blk, o, rk)
        out.extend([(o[i]^prev[i]) for i in range(16)]); prev = blk[:]
    p = out[-1]
    if 1 <= p <= 16: out = out[:-p]
    return bytes(out).decode('utf-8')


# ====================== 闂傚倸鍊峰ù鍥敋瑜旈弻濠囨晲閸滀胶鍔烽悷婊冪Х閻忓鈹戦悩缁樻锭妞ゆ垵鎳愮划濠氭偋閸垻鐦堟繝鐢靛Т閸婃悂鐛Ο鑽ょ鐎瑰壊鍠曠花鑽ょ磼閳ь剟宕奸姀鈥虫瀾闁瑰吋鐣崝宀勬偪?======================

def _derive_key(seed_str):
    r = fingerprint_hash(seed_str + "_bsdk_", seed=27)
    return r.lower() if r else "9eqaw+jthssyswpl9eqaw+jthssyswpl"


# ====================== 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸缁犵娀骞栨潏鍓хɑ鐎规洘鐓￠弻鐔衡偓娑欘焽婵″洭鏌￠埀顒勬晝閸屾稓鍘撻悷婊勭矒瀹曟粓鎮㈤悡搴ゆ憰闂佺偨鍎查崜姘跺触鐎ｎ喗鐓曟繛鎴濆船閺嬨倝鏌嶇憴鍕创婵?======================

def _rand_id():
    # 闂傚倸鍊峰ù鍥敋瑜旈弻濠囨晲閸滀胶鍔烽悷婊冪箳濡叉劙鎮欑€靛摜鐦堥梺鍛婃处閸橀箖藝?py1.9.6\crypto.py:get_random_id闂傚倸鍊搁崐鐑芥倿閿旈敮鍋撶粭娑樻噽閻瑩鏌熸潏楣冩闁稿顑夐弻娑樷槈閸楃偞鐏撶紓浣哄█缁犳牠寮诲☉銏犖ㄦい鏃傚帶椤亪姊虹粙娆惧剭闁告梹鍨垮濠氬即閻旇櫣顔曢悷婊冪Ф閳ь剚鍑归崜鐔煎箖閻愬搫绠瑰ù锝呮贡閸樹粙鏌熼懖鈺勊夐柛鎾寸箘閹峰綊鏁撻悩宕囧幈闁诲函缍嗛崜娆撳几濞戙垺鐓涢悘鐐垫櫕婢с垽鏌嶈閸撶喎顭囪楠炴顭ㄩ崨顓ф婵犻潧鍊搁幉锟犳偂閻斿吋鐓涢柛灞剧☉椤曟粓鏌涢悢鍝勪沪闁靛洤瀚悢渚€鏌涢妷锝呭Ψ闁瑰嘲顭峰铏圭矙閹稿孩鎷辩紓浣筋嚙閸熸娊濡甸幇顖ｆЬ缂?
    parts = [str(int(time.time() * 1000)) for _ in range(3)]
    tpl = ''.join(parts).replace(".", "x")[:32].ljust(32, "x")
    ts = int(time.time() * 1000)
    buf = []
    for ch in tpl:
        r = (ts + random.randint(0,15)) % 16; ts //= 16
        buf.append(format(r if ch == 'x' else (r&0x3)|0x8, 'x'))
    return ''.join(buf)


def _make_guid():
    # 闂傚倸鍊峰ù鍥敋瑜旈弻濠囨晲閸滀胶鍔烽悷婊冪箳濡叉劙鎮欑€靛摜鐦堥梺鍛婃处閸橀箖藝?py1.9.6\crypto.py:_guid闂傚倸鍊搁崐鐑芥倿閿旈敮鍋撶粭娑樻噽閻瑩鏌熸潏楣冩闁稿顑夐弻娑樷槈閸楃偞鐏撶紓浣哄█缁犳牠寮诲☉銏犖ㄦい鏃傚帶椤亪姊虹粙娆惧剭闁告梹鍨垮濠氬即閻旇櫣顔曢悷婊冪Ф閳ь剚鍑归崜鐔煎箖閻愬搫绠瑰ù锝呮贡閸樹粙鏌熼懖鈺勊夐柛鎾寸箘閹峰綊鏁撻悩宕囧幈闁诲函缍嗛崜娆撳几濞戙垺鐓涢悘鐐垫櫕婢с垽鏌嶈閸撶喎顭囪楠炴顭ㄩ崨顓ф婵犻潧鍊搁幉锟犳偂閻斿吋鐓涢柛灞剧☉椤曟粓鏌涢悢鍝勪沪闁靛洤瀚悢渚€鏌涢妷锝呭Ψ闁瑰嘲顭峰铏圭矙閹稿孩鎷辩紓浣筋嚙閸熸娊濡甸幇顖ｆЬ缂?
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


# ====================== 闂傚倸鍊烽懗鍫曞箠閹捐瑙﹂悗锝庝憾閻掕姤绻涢崱妯虹仸鐎规洘鐓￠弻鐔兼偋閸喓鍑￠梺鎼炲妼閸婂綊濡甸崟顔剧杸闁规崘娉涢。铏圭磽娴ｆ彃浜?======================

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


# ====================== 闂傚倸鍊搁崐宄懊归崶褜娴栭柕濞炬櫆閸ゅ嫰鏌ょ粙璺ㄤ粵婵炲懐濮垫穱濠囧Χ閸屾矮澹曢梻浣风串缁蹭粙鎮樺璺虹闁告侗鍠楁慨婊堟煙濞堝灝鏋涘褎鍎抽埞鎴︽倷閼搁潧娑х紓浣瑰絻濞硷繝骞冨ú顏勬閻?======================

def _wrap_data(data, sk=None, dk=None, iv=None):
    # Match py1.9.6 crypto.py process_data: SM4-CBC, key=dk, iv=dk.
    if not sk:
        sk = _rand_id()
    if not dk: dk = _derive_key(sk)
    if not iv: iv = ''.join(format(1^int(c,16),'x') for c in sk)
    pt = data if isinstance(data, str) else json.dumps(data, separators=(',',':'), ensure_ascii=False)
    enc = cipher_enc(pt, dk, dk)
    return iv + enc, sk, dk, iv


def _wrap_data_wasm(data, timestamp_ms=None) -> Optional[str]:
    """Use WASM en() for Content-Web-Bb; return None to fall back to SM4."""
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


# ====================== WASM 闂傚倷娴囬褏鈧稈鏅犻、娆撳冀椤撶偟鐛ュ┑掳鍊愰崑鎾绘偂閵堝棙鍙忔俊鐐额嚙娴滄儳螖閻橀潧浠﹂柣妤冨█閻涱噣寮介妸锕€顎撻柣鐔哥懃鐎氼參鎮?.0.8闂?=====================

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
    """Lazy initialize shared WASM engine/module and pool semaphore."""
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
    """Prewarm WASM instances and return the current pool size."""
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
    """Compute the WASM run_wac signature."""
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


# ====================== 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸閻ゎ喗銇勯幇鈺佺労闁搞倖娲熼弻銈囩矙鐠恒劋绮甸梺鍛婄懃缁绘﹢寮婚弴锛勭杸濠电偞鍎虫禍鍓р偓瑙勬礀濞层劑鎮伴鈧铏规嫚閺屻儱寮板┑鐐板尃閸パ呯枀闂佽法鍠撴慨鎾偪妤ｅ啯鐓欓梻鍌氼嚟閸斿秹鏌?======================

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
    # 闂傚倸鍊峰ù鍥敋瑜旈弻濠囨晲閸滀胶鍔烽悷婊冪箳濡叉劙鎮欑€靛摜鐦堥梺鍛婃处閸橀箖藝?py1.9.6\crypto.py:generate_content_web_bb
    nonce = _rand_id()
    sig = fingerprint_hash(req_data, seed=27, cap=4096, tail=nonce)
    try:
        ts_ms = int(timestamp_ms) if timestamp_ms not in (None, "") else int(adjusted_unix_ms())
    except (TypeError, ValueError):
        ts_ms = int(adjusted_unix_ms())
    # ProxyPin 2026-05-20 rushPurchase capture uses SM4 outer and empty 141.
    # Optional wasm_outer keeps all shield.py field/sign inputs unchanged, then fills 141 and encrypts via wasm en().
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
    # Match captured rushPurchase BotShield shape by default: SM4-CBC outer with v1.0.8.
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
    """Decrypt Content-Web-Bb."""
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


# ====================== 闂傚倸鍊峰ù鍥х暦閸偅鍙忛柟鎯板Г閸婂潡鏌ㄩ弴鐐测偓鍝ュ閸ф鐓欓悹鍝勬惈椤ョ偤鏌涢妷顔煎⒒闁轰礁妫濋弻娑㈠即閵娿儰绨婚梺鍝ュ亼閸旀垵顫忓ú顏勭闁绘劖褰冩慨鑸电箾鐎涙鐭岄柛瀣☉瀹?======================

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
    """Convert raw_id to the clips_ device id."""
    raw_chars = []
    for i, ch in enumerate(buf):
        out_ord = ord(ch)
        prev_seed = seed_init if i == 0 else ord(buf[i - 1])
        raw_chars.append(chr(prev_seed ^ out_ord))
    return "".join(raw_chars)


def raw_id_from_mt_device_token(device_token: str) -> Optional[str]:
    """Convert the MT clips_ device id back to raw_id."""
    if not device_token or not device_token.startswith("clips_"):
        return None
    try:
        xor_str = base64.b64decode(device_token[6:]).decode("utf-8")
    except Exception:
        return None
    return _invert_stream_xor(xor_str, _XOR_SEED)


def raw_id_from_mt_jwt(jwt_str: str) -> Optional[str]:
    """Restore raw_id from the deviceId field in an MT JWT payload."""
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
    """Derive MT-R from raw_id."""
    rid = (raw_id or "").strip()
    if not rid:
        return make_env_tag()
    b = int(_md5(rid)[4:6], 16)
    return make_env_tag(root=False, debug=bool(b & 1), proxy=bool(b & 2), inject=False)


def make_cert_tag_for_raw_id(raw_id: str) -> str:
    """Derive the MT-SN / MT-ACB fingerprint segment from raw_id."""
    rid = (raw_id or "").strip()
    if not rid:
        return make_cert_tag()
    idx = int(_md5(rid)[:8], 16) % len(_VALID_HASHES)
    return make_cert_tag(_VALID_HASHES[idx])


def _xor_encrypt_md5_hex_sn(sn_plain: str, key: int = 72) -> bytes:
    """Match mt1.9.6 xorEncrypt(snPlain, 72)."""
    out = bytearray()
    current_key = key
    for char in sn_plain:
        encrypted = ord(char) ^ current_key
        out.append(encrypted & 0xFF)
        current_key = encrypted
    return bytes(out)


def generate_mt_sn() -> str:
    """Generate a fresh MT-SN value."""
    session_data = f"{_uuid.uuid4()}{adjusted_unix()}{random.random()}"
    sn_plain = _md5(session_data)
    buf = _xor_encrypt_md5_hex_sn(sn_plain, 72)
    return "clips_" + base64.b64encode(buf).decode("ascii")


def format_d_time_mt_order_header() -> str:
    """Format MT-DTIME."""
    return format_mt_dtime_gmt08()


# ====================== 闂傚倸鍊峰ù鍥х暦閸偅鍙忛柡澶嬪殮濞差亶鏁囬柕蹇曞Х閸濇姊绘笟鍥у缂佸鏁诲畷鏇㈠箣閿旂晫鍘靛┑鐐茬墕閻忔繈寮搁妶鍜佹富閻熸瑥瀚埢鏇㈡煛瀹€鈧崰鏍€佸☉銏犲耿婵°倐鍋撴い蹇婃櫊閺?======================

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
    """Sign H5 rushPurchase MT-V from MT-K and device info."""
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
    """Sign H5 rushPurchase MT-V from MT-K and device info."""
    k = str(adjusted_unix_ms())
    return k, sign_mtv_h5_rush(k, device_info or "", ver)


# ====================== AES 闂傚倸鍊搁崐椋庣矆娓氣偓楠炲鏁撻悩鍐蹭画闂侀潧顦弲娑氬閸︻厽鍠愰柣妤€鐗嗙粭鎺撴叏鐟欏嫮鍙€闁哄矉缍佸顕€宕掑顑跨帛缂傚倷璁查崑鎾愁熆閼搁潧濮堥柣鎾跺枑娣囧﹪顢涘鍙樿檸闂佹眹鍔庨崕銈嗙┍?======================

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


# ====================== H5 闂傚倸鍊搁崐椋庣矆娴ｉ潻鑰块梺顒€绉埀顒婄畵瀹曠厧顭垮┑鍥ㄣ仢濠碘剝鎮傛俊鐑藉箛鐏炶棄鎮╅柡鍐ㄧ墕瀹告繃銇勯弮鍫燂紵缂傚倹鎹囧缁樻媴閾忕懓绗″┑顔硷工椤兘鍨鹃敃鍌氶唶闁靛绠戦埀?======================

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
    # Generate an H5 device fingerprint id.
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


# ====================== 闂傚倸鍊峰ù鍥敋瑜嶉湁闁绘垼妫勭粻鐘绘煙閹规劦鍤欓悗姘槹閵囧嫰骞掗幋婵愪患濠殿喛顫夐悡锟犲蓟閵娿儮鏀介柛鈩兠ˇ鈺呮煟鎼淬垻顣茬€光偓缁嬫娼栨繛宸簻閸ㄥ倹銇勯弴鐐村櫣闁告挸鍟块埞鎴︽偐椤愵澀澹曢梺璇插缁嬫帞婀佺紓浣筋嚙濡瑩濡甸崟顖氬唨妞ゎ厽鍨堕悾鑸电節閳封偓閸愩劎楠囩紓?======================

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
    print("=== 缂傚倸鍊搁崐鎼佸磹閻戣姤鍊块柨鏂垮⒔閻瑩鏌熼悜姗嗘當缁炬儳娼￠弻鐔煎箚閻楀牜妫勭紓浣哄У閻楃娀寮婚敓鐘茬倞闁宠桨绀佹竟澶愭⒑閹肩偛鈧牕煤閻旂厧钃熼柨鐔哄Т閻愬﹪鏌嶆潪鎵妽闁诲酣绠栧铏规嫚閳ュ磭浠梺鐑╂櫓閸ㄨ泛顕ｆ繝姘ч柛鈩冪懇濡绢喗绻濋姀锝嗙【妞わ箓浜跺畷娆撴偡闁附瀵?===")
    rid = make_raw_id(aid="test_android_id")
    dt = make_device_token(rid)
    k, v = make_kv_pair(rid)
    did = make_h5_devid()
    sid = make_h5_session()
    print(f"  闂傚倸鍊峰ù鍥х暦閸偅鍙忛柟鎯板Г閸婂潡鏌ㄩ弴鐐测偓鍝ュ閸ф鐓欓悹鍝勬惈椤ョ偤鏌涢妷顔煎闁稿濮撮埥?   {dt}")
    print(f"  缂傚倸鍊搁崐鎼佸磹閻戣姤鍊块柨鏂垮⒔閻瑩鏌熼悜姗嗘當缁炬儳娼￠弻鐔煎箚閻楀牜妫勭紓浣哄У閻楃娀寮?V:  {k} / {v}")
    print(f"  H5闂傚倸鍊搁崐椋庣矆娴ｉ潻鑰块梺顒€绉埀顒婄畵瀹曠厧顭垮┑鍥ㄣ仢濠碘剝鎮傛俊?   {did} (len={len(did)})")
    print(f"  婵犵數濮烽弫鎼佸磻閻愬樊鐒芥繛鍡樻尭鐟欙箓鎮楅敐搴′簽闁崇懓绉电换娑橆啅椤旇崵鍑归梺绋块閿曪妇妲愰幒鎳ㄥ湱鈧綆浜欐竟鏃?   {sid}")
    ap = seal_param({"test": True})
    assert unseal_param(ap) == {"test": True}
    print("  闂傚倸鍊搁崐椋庣矆娓氣偓楠炲鏁撻悩鍐蹭画闂侀潧顦弲娑氬閸︻厽鍠愰柣妤€鐗嗙粭鎺撴叏鐟欏嫮鍙€闁哄矉缍佸顕€宕掑顑跨帛缂傚倷璁查崑鎾愁熆閼搁潧濮堥柣鎾跺枑娣囧﹪顢涘顒佸€紒鍓ц檸娴滎亪寮婚悢鍏煎€绘俊顖濆吹閸樻悂姊? OK")
    bb = shield_for_post({"actParam": ap}, devid=did, sessid=sid, uid="123")
    dec = decrypt_shield(bb["Content-Web-Bb"])
    assert dec["fields"]["appid"] == DEFAULT_APPID
    print(f"  闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸閻ゎ喗銇勯幇鈺佺労闁搞倖娲熼弻銈囩矙鐠恒劋绮甸梺鍛婄懃缁绘﹢寮婚弴锛勭杸濠电偞鍎虫禍鍓р偓瑙勬礀濞层劑鎮伴鈧铏规嫚閺屻儱寮板┑鐐板尃閸パ呯枀闂佽法鍠撴慨鎾偪妤ｅ啯鐓欓梻鍌氼嚟閸斿秹鏌?闂傚倸鍊峰ù鍥х暦閻㈢绐楅柟鎵閸嬶繝寮堕崼姘珔缂佽翰鍊曢湁闁绘ê妯婇崕鎰版煕? OK")
    print("=== 闂傚倸鍊搁崐鐑芥嚄閸洍鈧箓宕奸姀鈥冲簥闂佸壊鍋侀崕杈╃矆婢舵劖鐓欓弶鍫濆⒔閻ｈ京鐥幆褎鍋ラ柡宀€鍠撶槐鎺楀閻樺磭浜堕梻浣虹帛鐢偟鍒掗鐐茬疄闁靛鍎欓悢鍏兼優闁谎冩憸缁€濠勭磽?===")
