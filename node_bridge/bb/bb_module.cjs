'use strict';
/**
 * BB SDK 灏佽妯″潡锛屼緵 server.cjs 閫氳繃 HTTP 鏆撮湶缁?Python 绔€? *
 * 浠呮湇鍔℃姠璐?POST (rushPurchase) 鐨?bb 涓変欢濂楃敓鎴愶紱鍏朵粬闈炴姠璐?H5 璇锋眰鎸夌湡鏈烘姄鍖呬笉甯?bb 澶淬€? *
 * 鎺ュ彛锛? *   bbSignPostForRush(body, ctxInput)  鈫?{Content-Web-Bb, Content-Hh-Bb, Sdk-Ver-Bb, randomId, sign, signWac}
 *   bbBootstrap(input)                  鈫?{signWasmVersion, signWasmBase64, signWasmOk, expiredAt}
 *   bbHealth()                          鈫?{ok, wasmReady, sdkVersion, signWasmReady}
 */

const path = require('path');
const fs = require('fs');
const bb = require('./bb_sdk_1_9_6');

const WASM_PATH = path.join(__dirname, 'bs_stub_1_9_6.wasm');
const STATE_TEMPLATE_PATH = path.join(__dirname, 'live_bangcle_state_1_9_6.json');
const DEFAULT_SIGN_WASM_VERSION = 'v1.0.8';
const LOCAL_SIGN_WASM_CANDIDATES = [
    path.join(__dirname, 'sign_wasm.bin'),
    path.join(__dirname, 'ext.bin'),
    path.join(__dirname, '..', '..', 'sign_wasm.bin'),
    path.join(__dirname, '..', '..', 'ext.bin'),
];

const SDK_VERSION = 'V3.5.0_20260605.1_imaotai';

// 棰勭儹鍗曚緥锛氳繘绋嬪唴 WASM 杩愯鏃讹紙V8 native锛岀嚎绋嬪畨鍏ㄧ敱 V8 淇濊瘉锛?
let _runtimePromise = null;

async function ensureRuntime() {
    if (!_runtimePromise) {
        _runtimePromise = bb.getStubRuntime_1_9_6({
            wasmPath: WASM_PATH,
            quiet: true,
        });
        await _runtimePromise;
    }
    return _runtimePromise;
}

// 浠?live_bangcle_state_1_9_6.json 鎶介粯璁?sign_wasm bytes/version/ok
// 鐪熸鐢熶骇搴旇鐢?bbBootstrap 浠庢姊嗘湇鍔＄鎷夊彇锛涜繖閲屾槸棣栨鍚姩鐨勫厹搴曞€笺€?
let _signWasmCacheDefault = null;
function loadLocalSignWasmFile() {
    for (const filePath of LOCAL_SIGN_WASM_CANDIDATES) {
        try {
            if (!fs.existsSync(filePath)) continue;
            const bytes = fs.readFileSync(filePath);
            if (bytes && bytes.length > 0) {
                return { filePath, bytes };
            }
        } catch (_) {
            // Try the next candidate.
        }
    }
    return null;
}

function loadDefaultSignWasmFromState() {
    if (_signWasmCacheDefault !== null) return _signWasmCacheDefault;
    const local = loadLocalSignWasmFile();
    if (local) {
        _signWasmCacheDefault = {
            signWasmVersion: DEFAULT_SIGN_WASM_VERSION,
            signWasmBase64: local.bytes.toString('base64'),
            signWasmBytes: local.bytes,
            signWasmOk: true,
            signWasmOkValue: 'local_file',
            signWasmPath: local.filePath,
            source: 'local_sign_wasm_file',
        };
        return _signWasmCacheDefault;
    }
    try {
        const raw = fs.readFileSync(STATE_TEMPLATE_PATH, 'utf-8');
        const state = JSON.parse(raw);
        // 鐩存帴璇?decoded.local_wrappers锛圝SON 鑷甫瑙ｆ瀽缁撴灉锛夛紝鏍煎紡锛歿expiredAt, value}
        const wrappers = (state && state.decoded && state.decoded.local_wrappers) || {};
        const verW = wrappers.__wasm_sign_version__ || {};
        const wasmW = wrappers.__wasm_sign_wasm__ || {};
        const okW = wrappers.__wasm_sign_ok__ || {};
        const wasmBase64 = typeof wasmW.value === 'string' ? wasmW.value : '';
        _signWasmCacheDefault = {
            signWasmVersion: typeof verW.value === 'string' ? verW.value : '',
            signWasmBase64: wasmBase64,
            signWasmBytes: wasmBase64 ? Buffer.from(wasmBase64, 'base64') : null,
            signWasmOk: !!(typeof okW.value === 'string' && okW.value),
            signWasmOkValue: typeof okW.value === 'string' ? okW.value : '',
            signWasmPath: '',
            source: 'local_state_default',
        };
    } catch (e) {
        _signWasmCacheDefault = {
            signWasmVersion: '',
            signWasmBase64: '',
            signWasmBytes: null,
            signWasmOk: false,
            signWasmOkValue: '',
            signWasmPath: '',
            source: 'missing',
        };
    }
    return _signWasmCacheDefault;
}

function normalizeStr(v) {
    if (v === null || v === undefined) return '';
    return String(v);
}

function makeContextForRush(input) {
    const ci = input || {};
    const def = loadDefaultSignWasmFromState();
    let signWasmBytes = def.signWasmBytes || null;
    let usingDefaultSignWasm = !!signWasmBytes;
    if (!signWasmBytes && ci.wasmBytesBase64) {
        try {
            signWasmBytes = Buffer.from(ci.wasmBytesBase64, 'base64');
            usingDefaultSignWasm = false;
        } catch (_) {
            signWasmBytes = null;
        }
    }
    const signWasmVersion = usingDefaultSignWasm ? (def.signWasmVersion || '') : (ci.wasmVersion || def.signWasmVersion || '');
    const signWasmOk = usingDefaultSignWasm ? !!def.signWasmOk : (ci.wasmOk !== undefined ? !!ci.wasmOk : !!def.signWasmOk);

    return bb.createBbContext_1_9_6({
        sdkVersion: SDK_VERSION,
        appKey: normalizeStr(ci.appid || '10001'),
        did: normalizeStr(ci.devid || ''),
        startId: normalizeStr(ci.sessid || ''),
        userId: normalizeStr(ci.uid || ''),
        userAgent: normalizeStr(ci.ua || ''),
        hp: normalizeStr(ci.hp || ''),
        smk: normalizeStr(ci.smk || ''),
        smk1: normalizeStr(ci.smk1 || ''),
        smi: normalizeStr(ci.smi || ''),
        signWasmVersion: signWasmVersion,
        signWasmOk: signWasmOk,
        signWasmBytes: signWasmBytes,
        cookieSend: ci.cookieSend !== undefined ? !!ci.cookieSend : true,
    });
}

async function bbSignPostForRush(body, ctxInput, options = {}) {
    await ensureRuntime();
    const ctx = makeContextForRush(ctxInput || {});
    const canonical = bb.jsonSerialize_1_9_6(body);
    const now = options.now || Date.now();
    const headers = await bb.buildRushPurchaseBbHeaders_1_9_6(canonical, ctx, {
        now: now,
        randomId: options.randomId || undefined,
        runtimeOptions: {
            wasmPath: WASM_PATH,
            quiet: true,
            now: now,
            userAgent: normalizeStr((ctxInput || {}).ua || ''),
            noCache: !!options.noCacheRuntime,
        },
        forceSm4Outer: true,
    });
    return {
        'Content-Web-Bb': headers['Content-Web-Bb'],
        'Content-Hh-Bb': headers['Content-Hh-Bb'],
        'Sdk-Ver-Bb': headers['Sdk-Ver-Bb'],
        randomId: headers.randomId,
        sign: headers.sign,
        signWac: headers.signWac || '',
    };
}

async function bbDuCookie(ctxInput, options = {}) {
    // 鐢熸垚 _d_u cookie 鍊硷細SM4/WASM 鍔犲瘑鐨?cReport 椋庢帶鍩嬬偣銆?    // 蹇呴』涓庢姠璐?Cookie 閲?_bs_device_id 鍚屾簮 (context.hp = _bs_device_id 鍊?銆?    await ensureRuntime();
    const ctx = makeContextForRush(ctxInput || {});
    const now = options.now || (ctxInput && ctxInput.now) || Date.now();
    const pageDomain = (ctxInput && ctxInput.pageDomain) || 'https://h5.moutai519.com.cn/';
    const userAgent = (ctxInput && ctxInput.ua) || '';
    const value = bb.buildDUCookieValue_1_9_6(ctx, {
        now,
        userAgent,
        domain: pageDomain,
    });
    return { value, hp: ctx.hp };
}

async function bbBootstrap(input) {
    // M1: 鏆傜敤鏈湴 live_bangcle_state_1_9_6.json 鐨勯粯璁ゅ€硷紱
    // 鍚庣画鍙墿灞曚负浠?fk1.moutai519.com.cn/bangcle 鎷夊彇鐗堟湰鍒楄〃 + 涓嬭浇 wasm + 涓婃姤鎸囩汗銆?    await ensureRuntime();
    const def = loadDefaultSignWasmFromState();
    return {
        signWasmVersion: def.signWasmVersion,
        signWasmBase64: def.signWasmBase64,
        signWasmOk: def.signWasmOk,
        signWasmOkValue: def.signWasmOkValue,
        expiredAt: Date.now() + 86400000, // 24h
        source: def.source || 'local_state_default',
        signWasmBytesLength: def.signWasmBytes ? def.signWasmBytes.length : 0,
        signWasmPath: def.signWasmPath || '',
    };
}

async function bbHealth() {
    let wasmReady = false;
    try {
        const r = await ensureRuntime();
        wasmReady = !!(r && r.exports);
    } catch (_) {
        wasmReady = false;
    }
    const def = loadDefaultSignWasmFromState();
    return {
        ok: true,
        wasmReady: wasmReady,
        sdkVersion: SDK_VERSION,
        signWasmReady: !!def.signWasmBytes,
        signWasmVersion: def.signWasmVersion,
        signWasmBytesLength: def.signWasmBytes ? def.signWasmBytes.length : 0,
        signWasmSource: def.source || '',
        signWasmPath: def.signWasmPath || '',
    };
}

module.exports = {
    ensureRuntime,
    bbSignPostForRush,
    bbDuCookie,
    bbBootstrap,
    bbHealth,
    SDK_VERSION,
};
