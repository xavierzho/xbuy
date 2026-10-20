'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { sm4 } = require('sm-crypto');

const SDK_VERSION = 'V3.5.0_20260721.2_imaotai';
const DEFAULT_BB_APP_KEY = '10001';
const DEFAULT_BB_HOST = 'https://fk1.moutai519.com.cn/bangcle';
const DEFAULT_WASM_RUNTIME_FALLBACK_HOST = 'http://172.16.202.6:9101';
const DEFAULT_UPLOAD_PATH = '/bbprbdata/upload';
const DEFAULT_WASM_VERSION_LIST_PATH = '/api/v1/1/2';
const DEFAULT_WASM_DOWNLOAD_PATH = '/api/v1/1/1';
const CONTENT_WEB_BB_HEADER = 'Content-Web-Bb';
const CONTENT_HH_BB_HEADER = 'Content-Hh-Bb';
const CONTENT_INFO_BB_HEADER = 'Content-Info-Bb';
const SDK_VER_BB_HEADER = 'Sdk-Ver-Bb';
const WASM_SIGN_STORAGE_KEY = '__wasm_sign_wasm__';
const WASM_SIGN_VERSION_KEY = '__wasm_sign_version__';
const WASM_SIGN_OK_STORAGE_KEY = '__wasm_sign_ok__';
const GLOBAL_SIGN_VERSION = '__SIGN_WASM_VERSION__';
const GLOBAL_SIGN_WASM_OK = '__SIGN_WASM_OK__';
const DEFAULT_STORAGE_TTL_MS = 86400000;
const PERSISTENT_STORAGE_TTL_MS = 259200000000;
const SIGN_WASM_STORAGE_TTL_MS = 2592000000;
const TOKEN_STORAGE_TTL_MS = 30000;
const GEN_F_TOKEN_TTL_MS = 5316758292;
const DEVICE_INFO_STORAGE_TTL_MS = 433296000;
const REMOTE_CONFIG_STORAGE_TTL_MS = 28809082;
const DEFAULT_BB_TASK = [
    '',
    '',
    0,
    1,
    0,
    0,
    60,
    10,
    [0, 10],
    [
        'mousemove',
        'error',
        'click',
        'touchstart',
        'touchend',
        'pageshow',
        'pagehide',
        'mousedown',
        'mouseup',
        'a1',
        'a2',
        'a3',
        'a4',
        'c1',
        'c2',
        'c3',
        'c4',
        'c5',
        'c6',
        'c7',
        'h',
        'i',
        'j',
        'k',
        'l',
        'm',
        'n',
    ],
    '',
    '',
    1,
    0,
    100,
    10,
    1,
    [],
    [0, 0],
    [0, 10],
    3600,
    0,
    0,
    20,
    3,
    [0, '', '', '', '', '', '', '', ''],
];

let defaultStubRuntimePromise = null;

function cloneDefaultBbTask() {
    return JSON.parse(JSON.stringify(DEFAULT_BB_TASK));
}

function cloneJsonValue(value) {
    if (value === undefined || value === null) {
        return value;
    }
    return JSON.parse(JSON.stringify(value));
}

function normalizeString(value) {
    if (value === null || value === undefined) {
        return '';
    }
    return String(value);
}

function trimTrailingSlashes(value) {
    return normalizeString(value).replace(/\/+$/, '');
}

function getOriginForBangcleHost(pageDomain = 'https://h5.moutai519.com.cn/') {
    const fallbackOrigin = 'https://h5.moutai519.com.cn';
    try {
        return new URL(normalizeString(pageDomain) || `${fallbackOrigin}/`).origin;
    } catch (error) {
        return fallbackOrigin;
    }
}

function normalizeBangcleHost(host, pageDomain = 'https://h5.moutai519.com.cn/') {
    const normalized = normalizeString(host).trim();
    if (!normalized) {
        return '';
    }
    if (/^https?:\/\//i.test(normalized)) {
        return trimTrailingSlashes(normalized);
    }
    if (normalized.startsWith('/')) {
        return `${getOriginForBangcleHost(pageDomain)}${trimTrailingSlashes(normalized)}`;
    }
    const protocol = getOriginForBangcleHost(pageDomain).split(':')[0] || 'https';
    return `${protocol}://${trimTrailingSlashes(normalized)}`;
}

function guid(now = Date.now()) {
    let cursor = now;
    return 'xxxxxxxx-xxxx-xxxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
        const random = ((cursor + 16 * Math.random()) % 16) | 0;
        cursor = Math.floor(cursor / 16);
        return (char === 'x' ? random : ((random & 3) | 8)).toString(16);
    });
}

function getRandomId(now = Date.now()) {
    const seeds = [];
    for (let index = 0; index < 3; index += 1) {
        seeds.push(String(now));
    }
    let cursor = new Date(now).getTime();
    return seeds
        .join('')
        .replace(/./g, 'x')
        .slice(0, 32)
        .replace(/[xy]/g, (char) => {
            const random = ((cursor + 16 * Math.random()) % 16) | 0;
            cursor = Math.floor(cursor / 16);
            return (char === 'x' ? random : ((random & 3) | 8)).toString(16);
        });
}

function getStartId(now = Date.now(), randomSource = Math.random()) {
    return `${now.toString(36)}_${randomSource.toString(36).slice(2)}_4`;
}

function str2utf8(text) {
    const input = normalizeString(text);
    const result = [];
    for (let index = 0; index < input.length; index += 1) {
        const code = input.charCodeAt(index);
        if (code >= 0 && code < 128) {
            result.push(code);
            continue;
        }
        const encoded = encodeURIComponent(input[index]).split('%');
        for (let part = 1; part < encoded.length; part += 1) {
            result.push(parseInt(encoded[part], 16));
        }
    }
    return result;
}

function x64Add(left, right) {
    const a = [left[0] >>> 16, left[0] & 0xffff, left[1] >>> 16, left[1] & 0xffff];
    const b = [right[0] >>> 16, right[0] & 0xffff, right[1] >>> 16, right[1] & 0xffff];
    const result = [0, 0, 0, 0];
    result[3] += a[3] + b[3];
    result[2] += result[3] >>> 16;
    result[3] &= 0xffff;
    result[2] += a[2] + b[2];
    result[1] += result[2] >>> 16;
    result[2] &= 0xffff;
    result[1] += a[1] + b[1];
    result[0] += result[1] >>> 16;
    result[1] &= 0xffff;
    result[0] += a[0] + b[0];
    result[0] &= 0xffff;
    return [(result[0] << 16) | result[1], (result[2] << 16) | result[3]];
}

function x64Multiply(left, right) {
    const a = [left[0] >>> 16, left[0] & 0xffff, left[1] >>> 16, left[1] & 0xffff];
    const b = [right[0] >>> 16, right[0] & 0xffff, right[1] >>> 16, right[1] & 0xffff];
    const result = [0, 0, 0, 0];
    result[3] += a[3] * b[3];
    result[2] += result[3] >>> 16;
    result[3] &= 0xffff;
    result[2] += a[2] * b[3];
    result[1] += result[2] >>> 16;
    result[2] &= 0xffff;
    result[2] += a[3] * b[2];
    result[1] += result[2] >>> 16;
    result[2] &= 0xffff;
    result[1] += a[1] * b[3];
    result[0] += result[1] >>> 16;
    result[1] &= 0xffff;
    result[1] += a[2] * b[2];
    result[0] += result[1] >>> 16;
    result[1] &= 0xffff;
    result[1] += a[3] * b[1];
    result[0] += result[1] >>> 16;
    result[1] &= 0xffff;
    result[0] += a[0] * b[3] + a[1] * b[2] + a[2] * b[1] + a[3] * b[0];
    result[0] &= 0xffff;
    return [(result[0] << 16) | result[1], (result[2] << 16) | result[3]];
}

function x64Rotl(value, shift) {
    let amount = shift % 64;
    if (amount === 32) {
        return [value[1], value[0]];
    }
    if (amount < 32) {
        return [
            (value[0] << amount) | (value[1] >>> (32 - amount)),
            (value[1] << amount) | (value[0] >>> (32 - amount)),
        ];
    }
    amount -= 32;
    return [
        (value[1] << amount) | (value[0] >>> (32 - amount)),
        (value[0] << amount) | (value[1] >>> (32 - amount)),
    ];
}

function x64LeftShift(value, shift) {
    const amount = shift % 64;
    if (amount === 0) {
        return value;
    }
    if (amount < 32) {
        return [
            (value[0] << amount) | (value[1] >>> (32 - amount)),
            value[1] << amount,
        ];
    }
    return [value[1] << (amount - 32), 0];
}

function x64Xor(left, right) {
    return [left[0] ^ right[0], left[1] ^ right[1]];
}

function x64Fmix(value) {
    let mixed = x64Xor(value, [0, value[0] >>> 1]);
    mixed = x64Multiply(mixed, [0xff51afd7, 0xed558ccd]);
    mixed = x64Xor(mixed, [0, mixed[0] >>> 1]);
    mixed = x64Multiply(mixed, [0xc4ceb9fe, 0x1a85ec53]);
    mixed = x64Xor(mixed, [0, mixed[0] >>> 1]);
    return mixed;
}

function bsHash(input, seed = 0, limit, suffix) {
    let bytes = str2utf8(input || []);
    let actualSeed = seed || 0;
    if (limit) {
        bytes = bytes.slice(0, limit);
    }
    if (suffix) {
        bytes = bytes.concat(str2utf8(suffix));
    }
    const remainder = bytes.length % 16;
    const bodyLength = bytes.length - remainder;
    let hash1 = [0, actualSeed];
    let hash2 = [0, actualSeed];
    const c1 = [0x87c37b91, 0x114253d5];
    const c2 = [0x4cf5ad43, 0x2745937f];
    let k1 = [0, 0];
    let k2 = [0, 0];
    for (let offset = 0; offset < bodyLength; offset += 16) {
        k1 = [
            (bytes[offset + 4] & 0xff) |
                ((bytes[offset + 5] & 0xff) << 8) |
                ((bytes[offset + 6] & 0xff) << 16) |
                ((bytes[offset + 7] & 0xff) << 24),
            (bytes[offset] & 0xff) |
                ((bytes[offset + 1] & 0xff) << 8) |
                ((bytes[offset + 2] & 0xff) << 16) |
                ((bytes[offset + 3] & 0xff) << 24),
        ];
        k2 = [
            (bytes[offset + 12] & 0xff) |
                ((bytes[offset + 13] & 0xff) << 8) |
                ((bytes[offset + 14] & 0xff) << 16) |
                ((bytes[offset + 15] & 0xff) << 24),
            (bytes[offset + 8] & 0xff) |
                ((bytes[offset + 9] & 0xff) << 8) |
                ((bytes[offset + 10] & 0xff) << 16) |
                ((bytes[offset + 11] & 0xff) << 24),
        ];
        k1 = x64Multiply(k1, c1);
        k1 = x64Rotl(k1, 31);
        k1 = x64Multiply(k1, c2);
        hash1 = x64Xor(hash1, k1);
        hash1 = x64Rotl(hash1, 27);
        hash1 = x64Add(hash1, hash2);
        hash1 = x64Add(x64Multiply(hash1, [0, 5]), [0, 0x52dce729]);
        k2 = x64Multiply(k2, c2);
        k2 = x64Rotl(k2, 33);
        k2 = x64Multiply(k2, c1);
        hash2 = x64Xor(hash2, k2);
        hash2 = x64Rotl(hash2, 31);
        hash2 = x64Add(hash2, hash1);
        hash2 = x64Add(x64Multiply(hash2, [0, 5]), [0, 0x38495ab5]);
    }
    k1 = [0, 0];
    k2 = [0, 0];
    const tail = bodyLength;
    switch (remainder) {
        case 15:
            k2 = x64Xor(k2, x64LeftShift([0, bytes[tail + 14]], 48));
        case 14:
            k2 = x64Xor(k2, x64LeftShift([0, bytes[tail + 13]], 40));
        case 13:
            k2 = x64Xor(k2, x64LeftShift([0, bytes[tail + 12]], 32));
        case 12:
            k2 = x64Xor(k2, x64LeftShift([0, bytes[tail + 11]], 24));
        case 11:
            k2 = x64Xor(k2, x64LeftShift([0, bytes[tail + 10]], 16));
        case 10:
            k2 = x64Xor(k2, x64LeftShift([0, bytes[tail + 9]], 8));
        case 9:
            k2 = x64Xor(k2, [0, bytes[tail + 8]]);
            k2 = x64Multiply(k2, c2);
            k2 = x64Rotl(k2, 33);
            k2 = x64Multiply(k2, c1);
            hash2 = x64Xor(hash2, k2);
        case 8:
            k1 = x64Xor(k1, x64LeftShift([0, bytes[tail + 7]], 56));
        case 7:
            k1 = x64Xor(k1, x64LeftShift([0, bytes[tail + 6]], 48));
        case 6:
            k1 = x64Xor(k1, x64LeftShift([0, bytes[tail + 5]], 40));
        case 5:
            k1 = x64Xor(k1, x64LeftShift([0, bytes[tail + 4]], 32));
        case 4:
            k1 = x64Xor(k1, x64LeftShift([0, bytes[tail + 3]], 24));
        case 3:
            k1 = x64Xor(k1, x64LeftShift([0, bytes[tail + 2]], 16));
        case 2:
            k1 = x64Xor(k1, x64LeftShift([0, bytes[tail + 1]], 8));
        case 1:
            k1 = x64Xor(k1, [0, bytes[tail]]);
            k1 = x64Multiply(k1, c1);
            k1 = x64Rotl(k1, 31);
            k1 = x64Multiply(k1, c2);
            hash1 = x64Xor(hash1, k1);
            break;
        default:
            break;
    }
    hash1 = x64Xor(hash1, [0, bytes.length]);
    hash2 = x64Xor(hash2, [0, bytes.length]);
    hash1 = x64Add(hash1, hash2);
    hash2 = x64Add(hash2, hash1);
    hash1 = x64Fmix(hash1);
    hash2 = x64Fmix(hash2);
    hash1 = x64Add(hash1, hash2);
    hash2 = x64Add(hash2, hash1);
    return (
        `00000000${(hash1[0] >>> 0).toString(16)}`.slice(-8) +
        `00000000${(hash1[1] >>> 0).toString(16)}`.slice(-8) +
        `00000000${(hash2[0] >>> 0).toString(16)}`.slice(-8) +
        `00000000${(hash2[1] >>> 0).toString(16)}`.slice(-8)
    );
}

function sm4Key(seed) {
    const digest = bsHash(`${normalizeString(seed)}_bsdk_`, 27);
    return digest ? digest.toLowerCase() : '9EqAw+JtHSsyswpL9EqAw+JtHSsyswpL';
}

function sm4Encrypt(plainText, keyHex) {
    return sm4.encrypt(plainText, keyHex, {
        mode: 'cbc',
        iv: keyHex,
        padding: 'pkcs#7',
    });
}

function sm4Decrypt(cipherHex, keyHex) {
    return sm4.decrypt(cipherHex, keyHex, {
        mode: 'cbc',
        iv: keyHex,
        padding: 'pkcs#7',
    });
}

function isRawStoredValue(storageKey) {
    return storageKey === 'h_five_event' || storageKey === WASM_SIGN_STORAGE_KEY;
}

function normalizeStorageTtlMs(ttlMs) {
    if (ttlMs === undefined || ttlMs === null) {
        return DEFAULT_STORAGE_TTL_MS;
    }
    const parsed = Number(ttlMs);
    if (!Number.isFinite(parsed)) {
        return DEFAULT_STORAGE_TTL_MS;
    }
    return parsed || PERSISTENT_STORAGE_TTL_MS;
}

function wrapStoredValue(
    storageKey,
    value,
    ttlMs = undefined,
    appKey = DEFAULT_BB_APP_KEY
) {
    return JSON.stringify({
        expiredAt: Date.now() + normalizeStorageTtlMs(ttlMs),
        value: isRawStoredValue(storageKey)
            ? value
            : sm4Encrypt(JSON.stringify(value), sm4Key(appKey)),
    });
}

function unwrapStoredValue(storageKey, wrappedValue, appKey = DEFAULT_BB_APP_KEY) {
    if (!wrappedValue) {
        return null;
    }
    const parsed = typeof wrappedValue === 'string' ? JSON.parse(wrappedValue) : wrappedValue;
    if (!parsed.expiredAt || Number(parsed.expiredAt) < Number(Date.now())) {
        return null;
    }
    if (isRawStoredValue(storageKey)) {
        return parsed.value;
    }
    return JSON.parse(sm4Decrypt(parsed.value, sm4Key(appKey)));
}

function normalizeWrappedStorageInput(state) {
    if (!state || typeof state !== 'object') {
        return { local: {}, session: {}, cookieState: null };
    }
    const local =
        state.local && typeof state.local === 'object'
            ? state.local
            : state.localStorage && typeof state.localStorage === 'object'
              ? state.localStorage
              : state;
    const session =
        state.session && typeof state.session === 'object'
            ? state.session
            : state.sessionStorage && typeof state.sessionStorage === 'object'
              ? state.sessionStorage
              : {};
    const cookieState =
        state.cookieState && typeof state.cookieState === 'object'
            ? state.cookieState
            : state.cookies && typeof state.cookies === 'object'
              ? state.cookies
              : null;
    return { local, session, cookieState };
}

function deriveBangcleSessionTokenShard(tokenPrepetuity) {
    const normalized = normalizeString(tokenPrepetuity);
    return normalized ? normalized.slice(12, 18) : '';
}

function applyCachedRemoteConfig(context, cachedConfig) {
    if (!Array.isArray(cachedConfig) || !Array.isArray(cachedConfig[0])) {
        return false;
    }
    const task = cachedConfig[0];
    const timeDelta = Number(cachedConfig[1] || 0);
    const tacticsVersion = Number(cachedConfig[2] || 0);
    const sdkTask = context.sdkConfig.task;

    sdkTask[16] = task[0] || 0;
    sdkTask[22] = task[1] || 0;
    sdkTask[17] = Array.isArray(task[2]) ? cloneJsonValue(task[2]) : [];
    sdkTask[20] = typeof task[3] === 'number' && task[3] > 10 ? task[3] : 3600;
    sdkTask[21] = task[4] || 0;
    sdkTask[7] = task[5] !== undefined ? task[5] : 10;
    sdkTask[15] = task[6] || 10;
    sdkTask[23] = task[7] || 20;
    sdkTask[24] = task[8] || 3;
    if (!Array.isArray(sdkTask[25])) {
        sdkTask[25] = [0, '', '', '', '', '', '', '', ''];
    }
    if (Array.isArray(task[9])) {
        for (let index = 0; index < 9; index += 1) {
            sdkTask[25][index] = task[9][index] !== undefined ? task[9][index] : '';
        }
    }

    context.sdkConfig.td = timeDelta;
    context.timeDelta = timeDelta;
    context.tacticsVersion = tacticsVersion;
    context.remoteTask = cloneJsonValue(task);
    return true;
}

function applyWrappedBangcleState(context, state, options = {}) {
    const { local, session, cookieState } = normalizeWrappedStorageInput(state);
    const preferState = options.preferState !== false;
    const appKey = options.appKey || context.sdkConfig?.app_key || DEFAULT_BB_APP_KEY;
    const unwrap = (storageKey) => unwrapStoredValue(storageKey, local[storageKey], appKey);
    const assign = (field, value) => {
        if (value === undefined || value === null || value === '') {
            return;
        }
        if (
            !preferState &&
            context[field] !== undefined &&
            context[field] !== null &&
            context[field] !== ''
        ) {
            return;
        }
        context[field] = value;
    };

    assign('did', unwrap('__ud'));
    assign('hp', unwrap('__mg__'));
    assign('uaid', unwrap('__idua_'));
    assign('jsid', unwrap('__idjs_'));
    assign('token', unwrap('__tok_'));
    assign('tokenPrepetuity', unwrap('__rk'));
    assign('sdkVersion', unwrap('_bb_v_'));
    assign('signWasmVersion', unwrap(WASM_SIGN_VERSION_KEY));

    const wrappedSignWasmOk = unwrap(WASM_SIGN_OK_STORAGE_KEY);
    if (wrappedSignWasmOk !== null && wrappedSignWasmOk !== undefined) {
        assign('signWasmOk', !!wrappedSignWasmOk);
    }

    const wrappedSignWasmBase64 = unwrap(WASM_SIGN_STORAGE_KEY);
    if (wrappedSignWasmBase64) {
        assign('signWasmBase64', wrappedSignWasmBase64);
        if (!context.signWasmBytes) {
            context.signWasmBytes = decodeBangcleSignWasmBase64(wrappedSignWasmBase64);
        }
    }

    const serial = unwrap('h_five_serial');
    if (serial !== null && serial !== undefined) {
        assign('serial', Number(serial));
    }
    const tps = unwrap('__tps');
    if (tps !== null && tps !== undefined) {
        assign('tps', Number(tps));
    }
    const deviceInfo = unwrap('h_five_cp');
    if (deviceInfo && typeof deviceInfo === 'object') {
        assign('deviceInfo', cloneJsonValue(deviceInfo));
    }
    const cachedConfig = unwrap('h_five_co');
    if (Array.isArray(cachedConfig)) {
        applyCachedRemoteConfig(context, cachedConfig);
    }

    assign('startId', normalizeString(session.__36_i__ || ''));
    if (!context.sessionTokenShard && typeof session.__st__ === 'string') {
        context.sessionTokenShard = session.__st__;
    }

    if (cookieState && typeof cookieState === 'object') {
        assign('sdkVersion', normalizeString(cookieState._sdk_v_ || ''));
        assign('hp', normalizeString(cookieState._bs_device_id || ''));
    }

    return context;
}

function xorHexStringWithOne(hexText) {
    return normalizeString(hexText)
        .split('')
        .map((char) => (1 ^ parseInt(char, 16)).toString(16))
        .join('');
}

function objectToArray(object) {
    const result = [];
    const keys = Object.keys(object).sort();
    for (let index = 0; index < keys.length; index += 1) {
        if (keys[index] === '$1') {
            continue;
        }
        const value = object[keys[index]];
        try {
            if (value === undefined) {
                result[index] = '';
            } else if (value === null) {
                result[index] = null;
            } else if (value && value.constructor === Object) {
                result[index] = objectToArray(value);
            } else if (value && value.constructor === Array) {
                result[index] = value.map((item) =>
                    item && item.constructor === Object ? objectToArray(item) : item
                );
            } else {
                result[index] = value;
            }
        } catch (error) {
            result[index] = value;
        }
    }
    return result;
}

function jsonSerialize(value, depth = 0) {
    let nextDepth = depth;
    if (typeof nextDepth === 'number') {
        nextDepth += 1;
    }
    if (
        nextDepth === 1 &&
        (value == null ||
            Object.is(value, Number.NaN) ||
            Object.is(value, Number.POSITIVE_INFINITY) ||
            typeof value === 'function')
    ) {
        return '';
    }
    if (
        value == null ||
        Object.is(value, Number.NaN) ||
        Object.is(value, Number.POSITIVE_INFINITY) ||
        typeof value === 'function'
    ) {
        return '|-|-|';
    }
    if (typeof value === 'string') {
        return value.toString();
    }
    if (typeof value === 'number') {
        return value;
    }
    if (nextDepth === 1 && typeof value === 'boolean') {
        return '';
    }
    if (typeof value === 'boolean') {
        return value;
    }
    if (Array.isArray(value)) {
        if (nextDepth > 10) {
            return '[]';
        }
        return JSON.stringify(
            value
                .map((item) => jsonSerialize(item, nextDepth))
                .filter((item) => item !== '|-|-|')
        );
    }
    if (nextDepth === 1 && value instanceof Date) {
        return '';
    }
    if (value instanceof Date) {
        return value.toString();
    }
    if (typeof FormData !== 'undefined' && value instanceof FormData) {
        const raw = {};
        for (const [entryKey, entryValue] of value.entries()) {
            if (!(typeof File !== 'undefined' && entryValue instanceof File)) {
                raw[entryKey] = entryValue;
            }
        }
        const canonical = {};
        for (const key of Object.keys(raw).sort()) {
            canonical[key] = raw[key];
        }
        value = canonical;
    }
    if (nextDepth > 10) {
        return '';
    }
    let output = '';
    for (const key of Object.keys(value).sort()) {
        const entry = jsonSerialize(value[key], nextDepth);
        if (entry !== '|-|-|') {
            output += `${key}=${entry};`;
        }
    }
    return output;
}

function urlParamsConcat(queryString, extraValue) {
    const entries = [];
    if (queryString) {
        queryString.split('&').forEach((entry) => {
            entries.push([
                entry.slice(0, entry.indexOf('=')),
                entry.slice(entry.indexOf('=') + 1),
            ]);
        });
    }
    let output = '';
    entries
        .sort((left, right) => left[0].localeCompare(right[0]))
        .forEach((entry) => {
            output += `&${entry[0]}=${entry[1]}`;
        });
    return output;
}

function ensureProcessDataSession(context) {
    if (context.smk && context.smk1 && context.smi) {
        return context;
    }
    context.smk = context.smk || getRandomId(context.now || Date.now());
    context.smk1 = sm4Key(context.smk);
    context.smi = xorHexStringWithOne(context.smk);
    return context;
}

function processData(data, context) {
    try {
        if (context.sdkConfig.task[3]) {
            ensureProcessDataSession(context);
            const plainText = typeof data === 'string' ? data : JSON.stringify(data);
            return [sm4Encrypt(plainText, context.smk1), context.smi].reverse().join('');
        }
        return JSON.stringify(data);
    } catch (error) {
        console.error(error);
        return '';
    }
}

async function processDataForSign(data, context, options = {}) {
    try {
        if (!context.sdkConfig.task[3]) {
            return JSON.stringify(data);
        }
        ensureProcessDataSession(context);
        const plainText = typeof data === 'string' ? data : JSON.stringify(data);
        if (context.signWasmOk) {
            const runtime = await getStubRuntime(options.runtimeOptions);
            const encrypted = await runtime.encrypt(plainText);
            if (
                encrypted &&
                typeof encrypted === 'string' &&
                !encrypted.includes('鏈垵濮嬪寲') &&
                !encrypted.includes('澶辫触')
            ) {
                return encrypted;
            }
        }
        return [sm4Encrypt(plainText, context.smk1), context.smi].reverse().join('');
    } catch (error) {
        console.error(error);
        return '';
    }
}

function unProcessData(cipherText, context) {
    try {
        if (context.sdkConfig.task[3]) {
            ensureProcessDataSession(context);
            const mask = xorHexStringWithOne(cipherText.slice(0, context.smk1.length));
            return JSON.parse(
                sm4Decrypt(cipherText.slice(context.smk1.length), sm4Key(mask))
            );
        }
        return JSON.parse(cipherText);
    } catch (error) {
        console.error(error);
        return null;
    }
}

function generateBsDeviceId(did, now = Date.now()) {
    const tail = normalizeString(did).slice(-4);
    return `bid-${String(now).split('').reverse().join('')}-${tail}-${guid(now).slice(9, 13)}`;
}

function ensureHp(context, now = Date.now()) {
    if (!context.hp) {
        context.hp = generateBsDeviceId(context.did, now);
    }
    return context.hp;
}

function serializeSignWasmBase64(context) {
    if (context.signWasmBase64) {
        return normalizeString(context.signWasmBase64);
    }
    if (typeof context.signWasmBytes === 'string' && fs.existsSync(context.signWasmBytes)) {
        return fs.readFileSync(context.signWasmBytes).toString('base64');
    }
    if (context.signWasmBytes instanceof Uint8Array) {
        return Buffer.from(context.signWasmBytes).toString('base64');
    }
    if (Buffer.isBuffer(context.signWasmBytes)) {
        return context.signWasmBytes.toString('base64');
    }
    return '';
}

function shortUserAgentForCookie(userAgent) {
    const normalized = normalizeString(userAgent);
    if (!normalized) {
        return '';
    }
    const closeIndex = normalized.indexOf(')');
    if (closeIndex > 0) {
        return normalized.slice(0, closeIndex);
    }
    return normalized.slice(0, 50);
}

function buildContentInfoPayload(context, now = Date.now()) {
    return processData([context.did || '', now], context);
}

function resolveBangcleUploadHost(context, options = {}) {
    const pageDomain = options.pageDomain || context.pageDomain || 'https://h5.moutai519.com.cn/';
    const resolved =
        normalizeBangcleHost(options.hostAd || context.hostAd || DEFAULT_BB_HOST, pageDomain) ||
        DEFAULT_BB_HOST;
    return trimTrailingSlashes(resolved);
}

function resolveBangcleWasmHost(context, options = {}) {
    const pageDomain = options.pageDomain || context.pageDomain || 'https://h5.moutai519.com.cn/';
    const candidate =
        options.hostWasm ||
        context.hostWasm ||
        context.hostAd ||
        DEFAULT_WASM_RUNTIME_FALLBACK_HOST;
    const resolved = normalizeBangcleHost(candidate, pageDomain);
    return trimTrailingSlashes(resolved || DEFAULT_WASM_RUNTIME_FALLBACK_HOST);
}

function buildBangcleUploadUrl(context, options = {}) {
    return `${resolveBangcleUploadHost(context, options)}${DEFAULT_UPLOAD_PATH}`;
}

function buildBangcleWasmVersionListUrl(context, options = {}) {
    return `${resolveBangcleWasmHost(context, options)}${DEFAULT_WASM_VERSION_LIST_PATH}`;
}

function buildBangcleWasmDownloadUrl(context, options = {}) {
    const baseUrl = `${resolveBangcleWasmHost(context, options)}${DEFAULT_WASM_DOWNLOAD_PATH}`;
    const version = normalizeString(options.version || context.signWasmVersion || '');
    return version ? `${baseUrl}?id=${encodeURIComponent(version)}` : baseUrl;
}

function buildBangcleVersionListRequest(context, options = {}) {
    const now = options.now || Date.now();
    return {
        method: 'GET',
        url: buildBangcleWasmVersionListUrl(context, options),
        headers: {
            [CONTENT_INFO_BB_HEADER]: buildContentInfoPayload(context, now),
        },
    };
}

function parseBangcleJsonPayload(payload) {
    if (!payload) {
        return null;
    }
    if (Buffer.isBuffer(payload)) {
        try {
            return JSON.parse(payload.toString('utf8'));
        } catch (error) {
            return null;
        }
    }
    if (typeof payload === 'string') {
        try {
            return JSON.parse(payload);
        } catch (error) {
            return null;
        }
    }
    if (typeof payload === 'object') {
        return payload;
    }
    return null;
}

function extractBangcleWasmVersions(payload) {
    const parsed = parseBangcleJsonPayload(payload);
    if (!parsed) {
        return { versions: [], signWasmOk: false, raw: null };
    }
    const versions = Array.isArray(parsed.v)
        ? parsed.v
              .map((entry) =>
                  typeof entry === 'string' ? entry : entry?.v || entry?.version || ''
              )
              .filter(Boolean)
        : Array.isArray(parsed)
          ? parsed
                .map((entry) =>
                    typeof entry === 'string' ? entry : entry?.v || entry?.version || ''
                )
                .filter(Boolean)
          : [];
    return {
        versions,
        signWasmOk: parsed.w !== false,
        raw: parsed,
    };
}

function decodeBangcleSignWasmBase64(value) {
    const base64 = normalizeString(value).trim();
    if (!base64) {
        return Buffer.alloc(0);
    }
    return Buffer.from(base64, 'base64');
}

function extractBangcleWasmDownloadPayload(payload) {
    const parsed = parseBangcleJsonPayload(payload);
    if (!parsed) {
        return {
            ok: false,
            name: '',
            version: '',
            codeBase64: '',
            codeBytes: null,
            unavailableReason: 'empty payload',
            raw: null,
        };
    }
    if (typeof parsed.code === 'number' && parsed.code === 0 && parsed.msg) {
        return {
            ok: false,
            name: normalizeString(parsed.name || ''),
            version: normalizeString(parsed.v || parsed.version || ''),
            codeBase64: '',
            codeBytes: null,
            unavailableReason: normalizeString(parsed.msg),
            raw: parsed,
        };
    }
    const version = normalizeString(parsed.v || parsed.version || '');
    const codeBase64 =
        typeof parsed.code === 'string' ? normalizeString(parsed.code).trim() : '';
    if (!version || !codeBase64) {
        return {
            ok: false,
            name: normalizeString(parsed.name || ''),
            version,
            codeBase64,
            codeBytes: null,
            unavailableReason: 'code payload empty',
            raw: parsed,
        };
    }
    const codeBytes = decodeBangcleSignWasmBase64(codeBase64);
    if (!codeBytes.length) {
        return {
            ok: false,
            name: normalizeString(parsed.name || ''),
            version,
            codeBase64,
            codeBytes: null,
            unavailableReason: 'decoded code payload empty',
            raw: parsed,
        };
    }
    return {
        ok: true,
        name: normalizeString(parsed.name || ''),
        version,
        codeBase64,
        codeBytes,
        unavailableReason: '',
        raw: parsed,
    };
}

function shouldSkipBangcleHook(requestUrl, context, options = {}) {
    const target = normalizeString(requestUrl);
    if (!target) {
        return false;
    }
    if (target === buildBangcleUploadUrl(context, options)) {
        return true;
    }
    return (
        target.endsWith(DEFAULT_WASM_VERSION_LIST_PATH) ||
        target.endsWith(DEFAULT_WASM_DOWNLOAD_PATH)
    );
}

function buildCReportPayloadObject(context, options = {}) {
    const now = options.now || Date.now();
    ensureHp(context, now);
    return {
        101: context.sdkConfig.app_key,
        103: context.did,
        105: context.startId || '',
        107: shortUserAgentForCookie(options.userAgent || context.userAgent || ''),
        109: options.domain || context.pageDomain || '',
        111: context.hp || '',
        113: now,
    };
}

function buildDUCookieValue(context, options = {}) {
    const payload = objectToArray(buildCReportPayloadObject(context, options));
    return processData(payload, context);
}

function buildBangcleUploadInfoPayloadObject(context, options = {}) {
    const now = options.now || Date.now();
    return {
        '001': String(options.userId !== undefined ? options.userId : context.userId || ''),
        '002': context.sdkConfig.app_key,
        '003': context.did || '',
        '004': options.startId || context.startId || '',
        '005': options.tokenPrepetuity || context.tokenPrepetuity || options.rk || '',
        '006': now,
        '007': options.serial || context.serial || 1,
        '008': options.sdkVersion || context.sdkVersion,
        '009': options.tacticsVersion || context.tacticsVersion || 0,
        '010':
            options.timeDelta !== undefined
                ? options.timeDelta
                : context.timeDelta !== undefined
                  ? context.timeDelta
                  : context.sdkConfig.td !== undefined
                    ? context.sdkConfig.td
                    : -1,
        101: options.deviceInfo || context.deviceInfo || null,
        102: [],
    };
}

function buildBangcleUploadEventPayloadObject(context, options = {}) {
    const now = options.now || Date.now();
    return {
        '001': String(options.userId !== undefined ? options.userId : context.userId || ''),
        '002': context.sdkConfig.app_key,
        '003': context.did || '',
        '004': options.startId || context.startId || '',
        '005': options.tokenPrepetuity || context.tokenPrepetuity || options.rk || '',
        '006': now,
        '007': options.serial || context.serial || 1,
        '008': options.sdkVersion || context.sdkVersion,
        '009': options.tacticsVersion || context.tacticsVersion || 0,
        '010':
            options.timeDelta !== undefined
                ? options.timeDelta
                : context.timeDelta !== undefined
                  ? context.timeDelta
                  : context.sdkConfig.td !== undefined
                    ? context.sdkConfig.td
                    : -1,
        101: null,
        102: Array.isArray(options.events) ? options.events : [],
    };
}

function buildBangcleUploadPayload(payloadObject, context) {
    return processData(objectToArray(payloadObject), context);
}

function buildBangcleUploadInfoPayload(context, options = {}) {
    return buildBangcleUploadPayload(
        buildBangcleUploadInfoPayloadObject(context, options),
        context
    );
}

function buildBangcleUploadEventPayload(context, options = {}) {
    return buildBangcleUploadPayload(
        buildBangcleUploadEventPayloadObject(context, options),
        context
    );
}

function normalizeSignWasmBytes(input) {
    if (!input) {
        return null;
    }
    if (input instanceof Uint8Array) {
        return input;
    }
    if (Buffer.isBuffer(input)) {
        return new Uint8Array(input);
    }
    if (typeof input === 'string') {
        if (fs.existsSync(input)) {
            return new Uint8Array(fs.readFileSync(input));
        }
        return new Uint8Array(Buffer.from(input, 'base64'));
    }
    throw new TypeError('unsupported sign wasm input');
}

function createBbContext(options = {}) {
    const task = cloneDefaultBbTask();
    const pageDomain = normalizeString(options.pageDomain || 'https://h5.moutai519.com.cn/');
    if (Array.isArray(options.taskOverride)) {
        options.taskOverride.forEach((value, index) => {
            task[index] = value;
        });
    }
    const context = {
        sdkVersion: options.sdkVersion || SDK_VERSION,
        sdkConfig: {
            app_key: normalizeString(options.appKey || DEFAULT_BB_APP_KEY),
            task,
        },
        hostAd:
            normalizeBangcleHost(options.hostAd || DEFAULT_BB_HOST, pageDomain) ||
            DEFAULT_BB_HOST,
        hostWasm:
            normalizeBangcleHost(
                options.hostWasm || options.hostAd || DEFAULT_BB_HOST,
                pageDomain
            ) || DEFAULT_WASM_RUNTIME_FALLBACK_HOST,
        domain: Array.isArray(options.domain)
            ? options.domain
            : [normalizeString(options.domain || 'moutai519.com.cn')],
        pageDomain,
        did: normalizeString(options.did),
        startId: normalizeString(options.startId),
        userId: normalizeString(options.userId),
        userAgent: normalizeString(options.userAgent),
        hp: normalizeString(options.hp),
        uaid: normalizeString(options.uaid),
        jsid: normalizeString(options.jsid),
        token: normalizeString(options.token),
        tokenPrepetuity: normalizeString(options.tokenPrepetuity || options.rk),
        cookieSend: options.cookieSend !== undefined ? !!options.cookieSend : true,
        signWasmVersion: normalizeString(options.signWasmVersion || ''),
        signWasmOk: options.signWasmOk !== undefined ? !!options.signWasmOk : false,
        signWasmBytes: normalizeSignWasmBytes(options.signWasmBytes || null),
        signWasmBase64: normalizeString(options.signWasmBase64 || ''),
        deviceInfo: options.deviceInfo || null,
        smk: normalizeString(options.smk),
        smk1: normalizeString(options.smk1),
        smi: normalizeString(options.smi),
        serial: Number.isFinite(Number(options.serial)) ? Number(options.serial) : undefined,
        tps: Number.isFinite(Number(options.tps)) ? Number(options.tps) : undefined,
        tacticsVersion: Number.isFinite(Number(options.tacticsVersion))
            ? Number(options.tacticsVersion)
            : undefined,
        timeDelta: Number.isFinite(Number(options.timeDelta)) ? Number(options.timeDelta) : undefined,
        remoteTask: options.remoteTask || null,
    };
    if (!context.startId) {
        context.startId = getStartId();
    }
    if (!context.hp && context.did) {
        ensureHp(context);
    }
    return context;
}

function resolveBundledFile(candidates) {
    for (const candidate of candidates) {
        if (candidate && fs.existsSync(candidate)) {
            return candidate;
        }
    }
    return null;
}

class StubWasmRuntime {
    constructor(options = {}) {
        this.userAgent =
            options.userAgent ||
            'Mozilla/5.0 (Linux; Android 14; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/117.0.0.0 Mobile Safari/537.36';
        this.quiet = options.quiet !== undefined ? !!options.quiet : true;
        this.dateNowFn =
            typeof options.dateNowFn === 'function'
                ? options.dateNowFn
                : options.now !== undefined
                  ? () => options.now
                  : () => Date.now();
        this.wasmPath =
            options.wasmPath ||
            resolveBundledFile([
                path.join(__dirname, 'runtime.wasm'),
                path.join(__dirname, '..', '..', '.analysis', 'bs_stub.wasm'),
            ]);
        if (!this.wasmPath) {
            throw new Error('bs_stub.wasm not found');
        }
        this.instance = null;
        this.exports = null;
        this.memory = null;
        this.HEAP8 = null;
        this.HEAPU8 = null;
        this.HEAP32 = null;
        this.HEAPU32 = null;
    }

    async init() {
        if (this.instance) {
            return this;
        }
        const wasmBinary = fs.readFileSync(this.wasmPath);
        const imports = {
            env: this.buildImports(),
            wasi_snapshot_preview1: this.buildImports(),
        };
        const { instance } = await WebAssembly.instantiate(wasmBinary, imports);
        this.instance = instance;
        this.exports = instance.exports;
        this.memory = this.exports.memory;
        this.syncMemoryViews();
        return this;
    }

    buildImports() {
        return {
            __cxa_throw: () => {
                throw new Error('wasm __cxa_throw');
            },
            _abort_js: () => {
                throw new Error('wasm abort');
            },
            _tzset_js: (timezonePtr, daylightPtr, stdPtr, dstPtr) =>
                this.tzset(timezonePtr, daylightPtr, stdPtr, dstPtr),
            emscripten_date_now: () => this.dateNowFn(),
            emscripten_resize_heap: (requestedSize) => this.resizeHeap(requestedSize),
            environ_get: (envPtr, bufPtr) => this.environGet(envPtr, bufPtr),
            environ_sizes_get: (countPtr, sizePtr) => this.environSizesGet(countPtr, sizePtr),
            exit: () => 0,
            fd_close: () => 52,
            fd_seek: () => 70,
            fd_write: (fd, iovPtr, iovCount, writtenPtr) =>
                this.fdWrite(fd, iovPtr, iovCount, writtenPtr),
            js_invoke: (targetPtr, methodPtr, argsPtr) =>
                this.jsInvoke(targetPtr, methodPtr, argsPtr),
        };
    }

    syncMemoryViews() {
        const buffer = this.memory.buffer;
        this.HEAP8 = new Int8Array(buffer);
        this.HEAPU8 = new Uint8Array(buffer);
        this.HEAP32 = new Int32Array(buffer);
        this.HEAPU32 = new Uint32Array(buffer);
    }

    resizeHeap(requestedSize) {
        const oldSize = this.HEAPU8.length;
        const maxSize = 2147483648;
        if ((requestedSize >>>= 0) > maxSize) {
            return false;
        }
        for (let step = 1; step <= 4; step *= 2) {
            let target = oldSize * (1 + 0.2 / step);
            target = Math.min(target, requestedSize + 100663296);
            const grown = Math.min(maxSize, Math.ceil(Math.max(requestedSize, target) / 65536) * 65536);
            const deltaPages = (grown - this.memory.buffer.byteLength + 65535) / 65536 | 0;
            try {
                this.memory.grow(deltaPages);
                this.syncMemoryViews();
                return true;
            } catch (error) {}
        }
        return false;
    }

    getEnvironmentStrings() {
        return ['USER=web_user', 'LOGNAME=web_user', 'PATH=/', 'PWD=/', 'HOME=/home/web_user', 'LANG=C.UTF-8'];
    }

    environSizesGet(countPtr, sizePtr) {
        const strings = this.getEnvironmentStrings();
        this.HEAP32[countPtr >> 2] = strings.length;
        let totalBytes = 0;
        strings.forEach((entry) => {
            totalBytes += this.lengthBytesUTF8(entry) + 1;
        });
        this.HEAP32[sizePtr >> 2] = totalBytes;
        return 0;
    }

    environGet(envPtr, bufPtr) {
        const strings = this.getEnvironmentStrings();
        let offset = 0;
        strings.forEach((entry, index) => {
            const ptr = bufPtr + offset;
            this.HEAP32[(envPtr >> 2) + index] = ptr;
            offset += this.stringToUTF8(entry, ptr, Infinity) + 1;
        });
        return 0;
    }

    fdWrite(fd, iovPtr, iovCount, writtenPtr) {
        let written = 0;
        for (let index = 0; index < iovCount; index += 1) {
            const base = this.HEAPU32[(iovPtr >> 2) + index * 2];
            const length = this.HEAPU32[(iovPtr >> 2) + index * 2 + 1];
            const text = this.UTF8ToString(base, length, true);
            if (!this.quiet) {
                if (fd === 1) {
                    process.stdout.write(text);
                } else if (fd === 2) {
                    process.stderr.write(text);
                }
            }
            written += length;
        }
        this.HEAP32[writtenPtr >> 2] = written;
        return 0;
    }

    tzset(timezonePtr, daylightPtr, stdPtr, dstPtr) {
        const year = new Date().getFullYear();
        const january = new Date(year, 0, 1);
        const july = new Date(year, 6, 1);
        const januaryOffset = january.getTimezoneOffset();
        const julyOffset = july.getTimezoneOffset();
        const offset = Math.max(januaryOffset, julyOffset);
        this.HEAPU32[timezonePtr >> 2] = 60 * offset;
        this.HEAP32[daylightPtr >> 2] = Number(januaryOffset !== julyOffset);
        const formatOffset = (minutes) => {
            const sign = minutes >= 0 ? '-' : '+';
            const absolute = Math.abs(minutes);
            const hours = String(Math.floor(absolute / 60)).padStart(2, '0');
            const mins = String(absolute % 60).padStart(2, '0');
            return `UTC${sign}${hours}${mins}`;
        };
        const standard = formatOffset(januaryOffset);
        const daylight = formatOffset(julyOffset);
        if (julyOffset < januaryOffset) {
            this.stringToUTF8(standard, stdPtr, 17);
            this.stringToUTF8(daylight, dstPtr, 17);
        } else {
            this.stringToUTF8(standard, dstPtr, 17);
            this.stringToUTF8(daylight, stdPtr, 17);
        }
    }

    lengthBytesUTF8(text) {
        return Buffer.byteLength(normalizeString(text), 'utf8');
    }

    stringToUTF8(text, ptr, maxBytes) {
        const buffer = Buffer.from(`${normalizeString(text)}\0`, 'utf8');
        const length = maxBytes === Infinity ? buffer.length : Math.min(buffer.length, maxBytes);
        this.HEAPU8.set(buffer.subarray(0, length), ptr);
        if (length < maxBytes && this.HEAPU8[ptr + length - 1] !== 0) {
            this.HEAPU8[ptr + length] = 0;
        }
        return length - 1;
    }

    UTF8ToString(ptr, length, noStopAtNull = false) {
        if (!ptr) {
            return '';
        }
        let end = ptr + (length || 0);
        if (!length || !noStopAtNull) {
            end = ptr;
            while (this.HEAPU8[end] && (!length || end < ptr + length)) {
                end += 1;
            }
        }
        return Buffer.from(this.HEAPU8.subarray(ptr, end)).toString('utf8');
    }

    mallocBytes(bytes) {
        const ptr = this.exports.malloc(bytes.length);
        this.HEAPU8.set(bytes, ptr);
        return ptr;
    }

    mallocString(text) {
        const length = this.lengthBytesUTF8(text) + 1;
        const ptr = this.exports.malloc(length);
        this.stringToUTF8(text, ptr, length);
        return ptr;
    }

    free(ptr) {
        if (ptr) {
            this.exports.free(ptr);
        }
    }

    freeReturnedString(ptr) {
        if (ptr) {
            this.exports.free_memory(ptr);
        }
    }

    resolveTarget(name) {
        const target = normalizeString(name);
        if (!target) {
            return null;
        }
        if (target === 'Date') {
            return {
                now: () => this.dateNowFn(),
            };
        }
        if (target === 'JSON') {
            return JSON;
        }
        if (target === 'window' || target === 'globalThis' || target === 'Module') {
            return this.getWindowTarget();
        }
        if (target === 'eval') {
            return { call: (code) => this.evaluateWindowCode(code) };
        }
        return this.getWindowTarget();
    }

    getWindowTarget() {
        const self = this;
        return {
            navigator: { userAgent: self.userAgent },
            Date: {
                now() {
                    return self.dateNowFn();
                },
            },
            JSON,
            eval(code) {
                return self.evaluateWindowCode(code);
            },
            has_env() {
                return 0;
            },
            getRendererUserAgent() {
                return self.userAgent;
            },
            getLocalIPAddress() {
                return '';
            },
            getConnectedWifi() {
                return '';
            },
            getScreenBrightness() {
                return -1;
            },
            getHCEState() {
                return false;
            },
            checkDeviceSupportHevc() {
                return false;
            },
        };
    }

    evaluateWindowCode(code) {
        return vm.runInNewContext(normalizeString(code), {
            window: this.getWindowTarget(),
            navigator: { userAgent: this.userAgent },
            Date: {
                now: () => this.dateNowFn(),
            },
            console,
        });
    }

    parseInvokeArgs(rawArgs) {
        if (!rawArgs) {
            return [];
        }
        const parsed = JSON.parse(rawArgs);
        return Array.isArray(parsed) ? parsed : [parsed];
    }

    jsInvoke(targetPtr, methodPtr, argsPtr) {
        try {
            const targetName = this.UTF8ToString(targetPtr);
            const methodName = this.UTF8ToString(methodPtr);
            const rawArgs = argsPtr ? this.UTF8ToString(argsPtr) : '';
            const target = this.resolveTarget(targetName);
            if (!target) {
                throw new Error(`target not found: ${targetName}`);
            }
            const method = target[methodName];
            if (typeof method !== 'function') {
                throw new Error(`method not found: ${targetName}.${methodName}`);
            }
            const result = method.apply(target, this.parseInvokeArgs(rawArgs));
            const serialized = JSON.stringify(result);
            const ptr = this.mallocString(serialized);
            return ptr;
        } catch (error) {
            const ptr = this.mallocString(`[invoke error] ${error.message}`);
            return ptr;
        }
    }

    async encrypt(text) {
        await this.init();
        const inputPtr = this.mallocString(text);
        const outputPtr = this.exports.en(inputPtr);
        const output = this.UTF8ToString(outputPtr);
        this.free(inputPtr);
        this.freeReturnedString(outputPtr);
        return output;
    }

    async decrypt(text) {
        await this.init();
        const inputPtr = this.mallocString(text);
        const outputPtr = this.exports.de(inputPtr);
        const output = this.UTF8ToString(outputPtr);
        this.free(inputPtr);
        this.freeReturnedString(outputPtr);
        return output;
    }

    async runWac(did, sign, wasmBytes) {
        await this.init();
        const blob = normalizeSignWasmBytes(wasmBytes);
        if (!blob) {
            return '';
        }
        const didText = normalizeString(did);
        const signText = normalizeString(sign);
        const didPtr = this.mallocString(didText);
        const signPtr = this.mallocString(signText);
        const blobPtr = this.mallocBytes(blob);
        const outputPtr = this.exports.run_wac(
            didPtr,
            this.lengthBytesUTF8(didText),
            signPtr,
            this.lengthBytesUTF8(signText),
            blobPtr,
            blob.length
        );
        const output = this.UTF8ToString(outputPtr);
        this.free(didPtr);
        this.free(signPtr);
        this.free(blobPtr);
        this.freeReturnedString(outputPtr);
        return output;
    }
}

async function getStubRuntime(options = {}) {
    if (!defaultStubRuntimePromise || options.noCache) {
        defaultStubRuntimePromise = new StubWasmRuntime(options).init();
    }
    return defaultStubRuntimePromise;
}

async function buildRushPurchaseBbHeaders(bodyCanonical, context, options = {}) {
    const randomId = options.randomId || getRandomId(options.now || Date.now());
    const sign = bsHash(bodyCanonical, 27, 4096, randomId);
    let signWac = '';
    if (context.signWasmOk && context.signWasmBytes) {
        try {
            signWac = await (await getStubRuntime(options.runtimeOptions)).runWac(
                context.did || '',
                sign,
                context.signWasmBytes
            );
        } catch (error) {
            signWac = '';
        }
    }
    const payload = {
        101: context.sdkConfig.app_key,
        103: context.did,
        104: context.startId || '',
        105: '',
        106: '0',
        108: '0',
        109: randomId,
        110: sign,
        111: options.now || Date.now(),
        120: {},
        130: String(context.userId || ''),
        140: context.signWasmVersion || '',
        141: signWac || '',
    };
    const payloadArray = objectToArray(payload);
    const gUd = options.forceSm4Outer
        ? processData(payloadArray, context)
        : context.signWasmOk
            ? await processDataForSign(payloadArray, context, options)
            : processData(payloadArray, context);
    return {
        [CONTENT_WEB_BB_HEADER]: gUd,
        [CONTENT_HH_BB_HEADER]: sign,
        [SDK_VER_BB_HEADER]: context.sdkVersion,
        randomId,
        sign,
        signWac,
        gUd,
    };
}

function buildBangcleBootstrapHeaders(context, options = {}) {
    return {
        [CONTENT_INFO_BB_HEADER]: buildContentInfoPayload(context, options.now || Date.now()),
    };
}

function buildBangcleCookieState(context, options = {}) {
    const now = options.now || Date.now();
    ensureHp(context, now);
    return {
        _sdk_v_: context.sdkVersion,
        _bs_device_id: context.hp,
        _d_u: buildDUCookieValue(context, options),
    };
}

function buildBangcleWrappedState(context, options = {}) {
    const appKey = options.appKey || context.sdkConfig?.app_key || DEFAULT_BB_APP_KEY;
    const now = options.now || Date.now();
    const signWasmTtlMs =
        options.signWasmTtlMs !== undefined ? options.signWasmTtlMs : SIGN_WASM_STORAGE_TTL_MS;
    const local = {};

    if (context.did) {
        local.__ud = wrapStoredValue('__ud', context.did, options.didTtlMs ?? 0, appKey);
    }
    if (context.hp) {
        local.__mg__ = wrapStoredValue(
            '__mg__',
            context.hp,
            options.hpTtlMs !== undefined ? options.hpTtlMs : GEN_F_TOKEN_TTL_MS,
            appKey
        );
    }
    if (context.tokenPrepetuity) {
        local.__rk = wrapStoredValue(
            '__rk',
            context.tokenPrepetuity,
            options.rkTtlMs ?? 0,
            appKey
        );
    }
    if (context.uaid) {
        local.__idua_ = wrapStoredValue(
            '__idua_',
            context.uaid,
            options.idTtlMs ?? 0,
            appKey
        );
    }
    if (context.jsid) {
        local.__idjs_ = wrapStoredValue(
            '__idjs_',
            context.jsid,
            options.idTtlMs ?? 0,
            appKey
        );
    }
    if (context.token) {
        local.__tok_ = wrapStoredValue(
            '__tok_',
            context.token,
            options.tokenTtlMs !== undefined ? options.tokenTtlMs : TOKEN_STORAGE_TTL_MS,
            appKey
        );
    }
    if (context.signWasmVersion) {
        local[WASM_SIGN_VERSION_KEY] = wrapStoredValue(
            WASM_SIGN_VERSION_KEY,
            context.signWasmVersion,
            signWasmTtlMs,
            appKey
        );
    }
    if (context.signWasmOk !== undefined) {
        local[WASM_SIGN_OK_STORAGE_KEY] = wrapStoredValue(
            WASM_SIGN_OK_STORAGE_KEY,
            context.signWasmOk ? 1 : 0,
            signWasmTtlMs,
            appKey
        );
    }
    const signWasmBase64 = serializeSignWasmBase64(context);
    if (signWasmBase64) {
        local[WASM_SIGN_STORAGE_KEY] = wrapStoredValue(
            WASM_SIGN_STORAGE_KEY,
            signWasmBase64,
            signWasmTtlMs,
            appKey
        );
    }
    if (context.sdkVersion) {
        local._bb_v_ = wrapStoredValue(
            '_bb_v_',
            context.sdkVersion,
            options.sdkVersionTtlMs,
            appKey
        );
    }
    if (context.serial !== undefined) {
        local.h_five_serial = wrapStoredValue(
            'h_five_serial',
            Number(context.serial),
            options.serialTtlMs ?? 0,
            appKey
        );
    }
    if (context.deviceInfo && typeof context.deviceInfo === 'object') {
        local.h_five_cp = wrapStoredValue(
            'h_five_cp',
            context.deviceInfo,
            options.deviceInfoTtlMs ?? DEVICE_INFO_STORAGE_TTL_MS,
            appKey
        );
    }
    if (Array.isArray(context.remoteTask)) {
        local.h_five_co = wrapStoredValue(
            'h_five_co',
            [
                cloneJsonValue(context.remoteTask),
                Number(context.timeDelta || context.sdkConfig?.td || 0),
                Number(context.tacticsVersion || 0),
            ],
            options.remoteConfigTtlMs ?? REMOTE_CONFIG_STORAGE_TTL_MS,
            appKey
        );
    }
    if (context.tps !== undefined) {
        local.__tps = wrapStoredValue(
            '__tps',
            Number(context.tps),
            options.tpsTtlMs ?? 0,
            appKey
        );
    }

    const session = {};
    if (context.startId) {
        session.__36_i__ = context.startId;
    }
    const tokenShard =
        context.sessionTokenShard || deriveBangcleSessionTokenShard(context.tokenPrepetuity);
    if (tokenShard) {
        session.__st__ = tokenShard;
    }
    if (options.h_c !== undefined) {
        session.h_c = options.h_c;
    } else if (context.remoteTask) {
        session.h_c = '1';
    }

    return {
        local,
        session,
        cookieState: buildBangcleCookieState(context, { ...options, now }),
        globals: {
            [GLOBAL_SIGN_VERSION]: context.signWasmVersion || '',
            [GLOBAL_SIGN_WASM_OK]: !!context.signWasmOk,
        },
    };
}

module.exports = {
    SDK_VERSION,
    DEFAULT_BB_APP_KEY,
    DEFAULT_BB_HOST,
    DEFAULT_WASM_RUNTIME_FALLBACK_HOST,
    DEFAULT_UPLOAD_PATH,
    DEFAULT_WASM_VERSION_LIST_PATH,
    DEFAULT_WASM_DOWNLOAD_PATH,
    CONTENT_WEB_BB_HEADER,
    CONTENT_HH_BB_HEADER,
    CONTENT_INFO_BB_HEADER,
    SDK_VER_BB_HEADER,
    WASM_SIGN_STORAGE_KEY,
    WASM_SIGN_VERSION_KEY,
    WASM_SIGN_OK_STORAGE_KEY,
    GLOBAL_SIGN_VERSION,
    GLOBAL_SIGN_WASM_OK,
    DEFAULT_STORAGE_TTL_MS,
    PERSISTENT_STORAGE_TTL_MS,
    SIGN_WASM_STORAGE_TTL_MS,
    TOKEN_STORAGE_TTL_MS,
    GEN_F_TOKEN_TTL_MS,
    DEVICE_INFO_STORAGE_TTL_MS,
    REMOTE_CONFIG_STORAGE_TTL_MS,
    DEFAULT_BB_TASK,
    guid,
    getRandomId,
    getStartId,
    bsHash,
    sm4Key,
    sm4Encrypt,
    sm4Decrypt,
    wrapStoredValue,
    unwrapStoredValue,
    applyWrappedBangcleState,
    buildBangcleWrappedState,
    deriveBangcleSessionTokenShard,
    xorHexStringWithOne,
    objectToArray,
    jsonSerialize,
    urlParamsConcat,
    processData,
    processDataForSign,
    unProcessData,
    generateBsDeviceId,
    createBbContext,
    buildContentInfoPayload,
    buildCReportPayloadObject,
    buildDUCookieValue,
    normalizeBangcleHost,
    buildBangcleUploadUrl,
    buildBangcleWasmVersionListUrl,
    buildBangcleWasmDownloadUrl,
    buildBangcleVersionListRequest,
    parseBangcleJsonPayload,
    extractBangcleWasmVersions,
    decodeBangcleSignWasmBase64,
    extractBangcleWasmDownloadPayload,
    shouldSkipBangcleHook,
    buildBangcleUploadInfoPayloadObject,
    buildBangcleUploadEventPayloadObject,
    buildBangcleUploadPayload,
    buildBangcleUploadInfoPayload,
    buildBangcleUploadEventPayload,
    buildBangcleCookieState,
    buildBangcleBootstrapHeaders,
    buildRushPurchaseBbHeaders,
    normalizeSignWasmBytes,
    StubWasmRuntime,
    getStubRuntime,
};
