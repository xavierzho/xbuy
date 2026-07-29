'use strict';
/**
 * BB SDK 封装模块，供 server.cjs 通过 HTTP 暴露给 Python 端。
 * 仅生成 rushPurchase POST 需要的 BB 三件套请求头；其它非抢购 H5 请求按真机抓包通常不带 BB 头。
 * 接口：bbSignPostForRush / bbBootstrap / bbHealth。
 *   bbSignPostForRush(body, ctxInput)  => {Content-Web-Bb, Content-Hh-Bb, Sdk-Ver-Bb, randomId, sign, signWac}
 *   bbBootstrap(input)                 => {signWasmVersion, signWasmBase64, signWasmOk, expiredAt}
 *   bbHealth()                         => {ok, wasmReady, sdkVersion, signWasmReady}
 */

const path = require('path');
const fs = require('fs');
const bb = require('./bb_sdk_1_9_6');

const WASM_PATH = path.join(__dirname, 'bs_stub_1_9_6.wasm');
const STATE_TEMPLATE_PATH = path.join(__dirname, 'live_bangcle_state_1_9_6.json');
const DEFAULT_SIGN_WASM_VERSION = 'v1.1.3.2';
const LOCAL_SIGN_WASM_CANDIDATES = [
    path.join(__dirname, 'sign_wasm.bin'),
    path.join(__dirname, 'ext.bin'),
    path.join(__dirname, '..', '..', 'sign_wasm.bin'),
    path.join(__dirname, '..', '..', 'ext.bin'),
];

const SDK_VERSION = 'V3.5.0_20260721.2_imaotai';

// 预热单例：进程内 WASM 运行时，线程安全由 V8 保证。
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

// 默认 sign_wasm 状态优先读取本地文件，再读取 live_bangcle_state_1_9_6.json。
// 生产环境可由 bbBootstrap 或运行时更新器拉取版本列表并刷新本地缓存。
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
            // 继续尝试下一个候选文件。
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
        // decoded.local_wrappers 已经是解析后的 {expiredAt, value} 包装格式。
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
    // 生成 _d_u cookie 值：SM4/WASM 加密后的 cReport 风控基点。
    await ensureRuntime();
    // 必须与抢购 Cookie 里的 _bs_device_id 同源，context.hp 即 _bs_device_id 值。
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
    // M1: 暂用本地 live_bangcle_state_1_9_6.json 的默认值；
    // 后续可扩展为从 fk1.moutai519.com.cn/bangcle 拉取版本列表、下载 wasm、上报指纹。
    await ensureRuntime();
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
