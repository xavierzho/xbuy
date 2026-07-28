'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { sm4 } = require('sm-crypto');

const SDK_VERSION_1_9_6 = 'V3.5.0_20260605.1_imaotai';
const DEFAULT_BB_APP_KEY_1_9_6 = '10001';
const DEFAULT_BB_HOST_1_9_6 = 'https://fk1.moutai519.com.cn/bangcle';
const DEFAULT_WASM_RUNTIME_FALLBACK_HOST_1_9_6 = 'http://172.16.202.6:9101';
const DEFAULT_UPLOAD_PATH_1_9_6 = '/bbprbdata/upload';
const DEFAULT_WASM_VERSION_LIST_PATH_1_9_6 = '/api/v1/1/2';
const DEFAULT_WASM_DOWNLOAD_PATH_1_9_6 = '/api/v1/1/1';
const CONTENT_WEB_BB_HEADER_1_9_6 = 'Content-Web-Bb';
const CONTENT_HH_BB_HEADER_1_9_6 = 'Content-Hh-Bb';
const CONTENT_INFO_BB_HEADER_1_9_6 = 'Content-Info-Bb';
const SDK_VER_BB_HEADER_1_9_6 = 'Sdk-Ver-Bb';
const WASM_SIGN_STORAGE_KEY_1_9_6 = '__wasm_sign_wasm__';
const WASM_SIGN_VERSION_KEY_1_9_6 = '__wasm_sign_version__';
const WASM_SIGN_OK_STORAGE_KEY_1_9_6 = '__wasm_sign_ok__';
const GLOBAL_SIGN_VERSION_1_9_6 = '__SIGN_WASM_VERSION__';
const GLOBAL_SIGN_WASM_OK_1_9_6 = '__SIGN_WASM_OK__';
const DEFAULT_STORAGE_TTL_MS_1_9_6 = 86400000;
const PERSISTENT_STORAGE_TTL_MS_1_9_6 = 259200000000;
const SIGN_WASM_STORAGE_TTL_MS_1_9_6 = 2592000000;
const TOKEN_STORAGE_TTL_MS_1_9_6 = 30000;
const GEN_F_TOKEN_TTL_MS_1_9_6 = 5316758292;
const DEVICE_INFO_STORAGE_TTL_MS_1_9_6 = 433296000;
const REMOTE_CONFIG_STORAGE_TTL_MS_1_9_6 = 28809082;
const DEFAULT_BB_TASK_1_9_6 = [
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

let defaultStubRuntimePromise_1_9_6 = null;

function cloneDefaultBbTask_1_9_6() {
    return JSON.parse(JSON.stringify(DEFAULT_BB_TASK_1_9_6));
}

function cloneJsonValue_1_9_6(value) {
    if (value === undefined || value === null) {
        return value;
    }
    return JSON.parse(JSON.stringify(value));
}

function normalizeString_1_9_6(value) {
    if (value === null || value === undefined) {
        return '';
    }
    return String(value);
}

function trimTrailingSlashes_1_9_6(value) {
    return normalizeString_1_9_6(value).replace(/\/+$/, '');
}

function getOriginForBangcleHost_1_9_6(pageDomain = 'https://h5.moutai519.com.cn/') {
    const fallbackOrigin = 'https://h5.moutai519.com.cn';
    try {
        return new URL(normalizeString_1_9_6(pageDomain) || `${fallbackOrigin}/`).origin;
    } catch (error) {
        return fallbackOrigin;
    }
}

function normalizeBangcleHost_1_9_6(host, pageDomain = 'https://h5.moutai519.com.cn/') {
    const normalized = normalizeString_1_9_6(host).trim();
    if (!normalized) {
        return '';
    }
    if (/^https?:\/\//i.test(normalized)) {
        return trimTrailingSlashes_1_9_6(normalized);
    }
    if (normalized.startsWith('/')) {
        return `${getOriginForBangcleHost_1_9_6(pageDomain)}${trimTrailingSlashes_1_9_6(normalized)}`;
    }
    const protocol = getOriginForBangcleHost_1_9_6(pageDomain).split(':')[0] || 'https';
    return `${protocol}://${trimTrailingSlashes_1_9_6(normalized)}`;
}

function guid_1_9_6(now = Date.now()) {
    let cursor = now;
    return 'xxxxxxxx-xxxx-xxxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
        const random = ((cursor + 16 * Math.random()) % 16) | 0;
        cursor = Math.floor(cursor / 16);
        return (char === 'x' ? random : ((random & 3) | 8)).toString(16);
    });
}

function getRandomId_1_9_6(now = Date.now()) {
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

function getStartId_1_9_6(now = Date.now(), randomSource = Math.random()) {
    return `${now.toString(36)}_${randomSource.toString(36).slice(2)}_4`;
}

function str2utf8_1_9_6(text) {
    const input = normalizeString_1_9_6(text);
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

function x64Add_1_9_6(left, right) {
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

function x64Multiply_1_9_6(left, right) {
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

function x64Rotl_1_9_6(value, shift) {
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

function x64LeftShift_1_9_6(value, shift) {
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

function x64Xor_1_9_6(left, right) {
    return [left[0] ^ right[0], left[1] ^ right[1]];
}

function x64Fmix_1_9_6(value) {
    let mixed = x64Xor_1_9_6(value, [0, value[0] >>> 1]);
    mixed = x64Multiply_1_9_6(mixed, [0xff51afd7, 0xed558ccd]);
    mixed = x64Xor_1_9_6(mixed, [0, mixed[0] >>> 1]);
    mixed = x64Multiply_1_9_6(mixed, [0xc4ceb9fe, 0x1a85ec53]);
    mixed = x64Xor_1_9_6(mixed, [0, mixed[0] >>> 1]);
    return mixed;
}

function bsHash_1_9_6(input, seed = 0, limit, suffix) {
    let bytes = str2utf8_1_9_6(input || []);
    let actualSeed = seed || 0;
    if (limit) {
        bytes = bytes.slice(0, limit);
    }
    if (suffix) {
        bytes = bytes.concat(str2utf8_1_9_6(suffix));
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
        k1 = x64Multiply_1_9_6(k1, c1);
        k1 = x64Rotl_1_9_6(k1, 31);
        k1 = x64Multiply_1_9_6(k1, c2);
        hash1 = x64Xor_1_9_6(hash1, k1);
        hash1 = x64Rotl_1_9_6(hash1, 27);
        hash1 = x64Add_1_9_6(hash1, hash2);
        hash1 = x64Add_1_9_6(x64Multiply_1_9_6(hash1, [0, 5]), [0, 0x52dce729]);
        k2 = x64Multiply_1_9_6(k2, c2);
        k2 = x64Rotl_1_9_6(k2, 33);
        k2 = x64Multiply_1_9_6(k2, c1);
        hash2 = x64Xor_1_9_6(hash2, k2);
        hash2 = x64Rotl_1_9_6(hash2, 31);
        hash2 = x64Add_1_9_6(hash2, hash1);
        hash2 = x64Add_1_9_6(x64Multiply_1_9_6(hash2, [0, 5]), [0, 0x38495ab5]);
    }
    k1 = [0, 0];
    k2 = [0, 0];
    const tail = bodyLength;
    switch (remainder) {
        case 15:
            k2 = x64Xor_1_9_6(k2, x64LeftShift_1_9_6([0, bytes[tail + 14]], 48));
        case 14:
            k2 = x64Xor_1_9_6(k2, x64LeftShift_1_9_6([0, bytes[tail + 13]], 40));
        case 13:
            k2 = x64Xor_1_9_6(k2, x64LeftShift_1_9_6([0, bytes[tail + 12]], 32));
        case 12:
            k2 = x64Xor_1_9_6(k2, x64LeftShift_1_9_6([0, bytes[tail + 11]], 24));
        case 11:
            k2 = x64Xor_1_9_6(k2, x64LeftShift_1_9_6([0, bytes[tail + 10]], 16));
        case 10:
            k2 = x64Xor_1_9_6(k2, x64LeftShift_1_9_6([0, bytes[tail + 9]], 8));
        case 9:
            k2 = x64Xor_1_9_6(k2, [0, bytes[tail + 8]]);
            k2 = x64Multiply_1_9_6(k2, c2);
            k2 = x64Rotl_1_9_6(k2, 33);
            k2 = x64Multiply_1_9_6(k2, c1);
            hash2 = x64Xor_1_9_6(hash2, k2);
        case 8:
            k1 = x64Xor_1_9_6(k1, x64LeftShift_1_9_6([0, bytes[tail + 7]], 56));
        case 7:
            k1 = x64Xor_1_9_6(k1, x64LeftShift_1_9_6([0, bytes[tail + 6]], 48));
        case 6:
            k1 = x64Xor_1_9_6(k1, x64LeftShift_1_9_6([0, bytes[tail + 5]], 40));
        case 5:
            k1 = x64Xor_1_9_6(k1, x64LeftShift_1_9_6([0, bytes[tail + 4]], 32));
        case 4:
            k1 = x64Xor_1_9_6(k1, x64LeftShift_1_9_6([0, bytes[tail + 3]], 24));
        case 3:
            k1 = x64Xor_1_9_6(k1, x64LeftShift_1_9_6([0, bytes[tail + 2]], 16));
        case 2:
            k1 = x64Xor_1_9_6(k1, x64LeftShift_1_9_6([0, bytes[tail + 1]], 8));
        case 1:
            k1 = x64Xor_1_9_6(k1, [0, bytes[tail]]);
            k1 = x64Multiply_1_9_6(k1, c1);
            k1 = x64Rotl_1_9_6(k1, 31);
            k1 = x64Multiply_1_9_6(k1, c2);
            hash1 = x64Xor_1_9_6(hash1, k1);
            break;
        default:
            break;
    }
    hash1 = x64Xor_1_9_6(hash1, [0, bytes.length]);
    hash2 = x64Xor_1_9_6(hash2, [0, bytes.length]);
    hash1 = x64Add_1_9_6(hash1, hash2);
    hash2 = x64Add_1_9_6(hash2, hash1);
    hash1 = x64Fmix_1_9_6(hash1);
    hash2 = x64Fmix_1_9_6(hash2);
    hash1 = x64Add_1_9_6(hash1, hash2);
    hash2 = x64Add_1_9_6(hash2, hash1);
    return (
        `00000000${(hash1[0] >>> 0).toString(16)}`.slice(-8) +
        `00000000${(hash1[1] >>> 0).toString(16)}`.slice(-8) +
        `00000000${(hash2[0] >>> 0).toString(16)}`.slice(-8) +
        `00000000${(hash2[1] >>> 0).toString(16)}`.slice(-8)
    );
}

function sm4Key_1_9_6(seed) {
    const digest = bsHash_1_9_6(`${normalizeString_1_9_6(seed)}_bsdk_`, 27);
    return digest ? digest.toLowerCase() : '9EqAw+JtHSsyswpL9EqAw+JtHSsyswpL';
}

function sm4Encrypt_1_9_6(plainText, keyHex) {
    return sm4.encrypt(plainText, keyHex, {
        mode: 'cbc',
        iv: keyHex,
        padding: 'pkcs#7',
    });
}

function sm4Decrypt_1_9_6(cipherHex, keyHex) {
    return sm4.decrypt(cipherHex, keyHex, {
        mode: 'cbc',
        iv: keyHex,
        padding: 'pkcs#7',
    });
}

function isRawStoredValue_1_9_6(storageKey) {
    return storageKey === 'h_five_event' || storageKey === WASM_SIGN_STORAGE_KEY_1_9_6;
}

function normalizeStorageTtlMs_1_9_6(ttlMs) {
    if (ttlMs === undefined || ttlMs === null) {
        return DEFAULT_STORAGE_TTL_MS_1_9_6;
    }
    const parsed = Number(ttlMs);
    if (!Number.isFinite(parsed)) {
        return DEFAULT_STORAGE_TTL_MS_1_9_6;
    }
    return parsed || PERSISTENT_STORAGE_TTL_MS_1_9_6;
}

function wrapStoredValue_1_9_6(
    storageKey,
    value,
    ttlMs = undefined,
    appKey = DEFAULT_BB_APP_KEY_1_9_6
) {
    return JSON.stringify({
        expiredAt: Date.now() + normalizeStorageTtlMs_1_9_6(ttlMs),
        value: isRawStoredValue_1_9_6(storageKey)
            ? value
            : sm4Encrypt_1_9_6(JSON.stringify(value), sm4Key_1_9_6(appKey)),
    });
}

function unwrapStoredValue_1_9_6(storageKey, wrappedValue, appKey = DEFAULT_BB_APP_KEY_1_9_6) {
    if (!wrappedValue) {
        return null;
    }
    const parsed = typeof wrappedValue === 'string' ? JSON.parse(wrappedValue) : wrappedValue;
    if (!parsed.expiredAt || Number(parsed.expiredAt) < Number(Date.now())) {
        return null;
    }
    if (isRawStoredValue_1_9_6(storageKey)) {
        return parsed.value;
    }
    return JSON.parse(sm4Decrypt_1_9_6(parsed.value, sm4Key_1_9_6(appKey)));
}

function normalizeWrappedStorageInput_1_9_6(state) {
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

function deriveBangcleSessionTokenShard_1_9_6(tokenPrepetuity) {
    const normalized = normalizeString_1_9_6(tokenPrepetuity);
    return normalized ? normalized.slice(12, 18) : '';
}

function applyCachedRemoteConfig_1_9_6(context, cachedConfig) {
    if (!Array.isArray(cachedConfig) || !Array.isArray(cachedConfig[0])) {
        return false;
    }
    const task = cachedConfig[0];
    const timeDelta = Number(cachedConfig[1] || 0);
    const tacticsVersion = Number(cachedConfig[2] || 0);
    const sdkTask = context.sdkConfig.task;

    sdkTask[16] = task[0] || 0;
    sdkTask[22] = task[1] || 0;
    sdkTask[17] = Array.isArray(task[2]) ? cloneJsonValue_1_9_6(task[2]) : [];
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
    context.remoteTask = cloneJsonValue_1_9_6(task);
    return true;
}

function applyWrappedBangcleState_1_9_6(context, state, options = {}) {
    const { local, session, cookieState } = normalizeWrappedStorageInput_1_9_6(state);
    const preferState = options.preferState !== false;
    const appKey = options.appKey || context.sdkConfig?.app_key || DEFAULT_BB_APP_KEY_1_9_6;
    const unwrap = (storageKey) => unwrapStoredValue_1_9_6(storageKey, local[storageKey], appKey);
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
    assign('signWasmVersion', unwrap(WASM_SIGN_VERSION_KEY_1_9_6));

    const wrappedSignWasmOk = unwrap(WASM_SIGN_OK_STORAGE_KEY_1_9_6);
    if (wrappedSignWasmOk !== null && wrappedSignWasmOk !== undefined) {
        assign('signWasmOk', !!wrappedSignWasmOk);
    }

    const wrappedSignWasmBase64 = unwrap(WASM_SIGN_STORAGE_KEY_1_9_6);
    if (wrappedSignWasmBase64) {
        assign('signWasmBase64', wrappedSignWasmBase64);
        if (!context.signWasmBytes) {
            context.signWasmBytes = decodeBangcleSignWasmBase64_1_9_6(wrappedSignWasmBase64);
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
        assign('deviceInfo', cloneJsonValue_1_9_6(deviceInfo));
    }
    const cachedConfig = unwrap('h_five_co');
    if (Array.isArray(cachedConfig)) {
        applyCachedRemoteConfig_1_9_6(context, cachedConfig);
    }

    assign('startId', normalizeString_1_9_6(session.__36_i__ || ''));
    if (!context.sessionTokenShard && typeof session.__st__ === 'string') {
        context.sessionTokenShard = session.__st__;
    }

    if (cookieState && typeof cookieState === 'object') {
        assign('sdkVersion', normalizeString_1_9_6(cookieState._sdk_v_ || ''));
        assign('hp', normalizeString_1_9_6(cookieState._bs_device_id || ''));
    }

    return context;
}

function xorHexStringWithOne_1_9_6(hexText) {
    return normalizeString_1_9_6(hexText)
        .split('')
        .map((char) => (1 ^ parseInt(char, 16)).toString(16))
        .join('');
}

function objectToArray_1_9_6(object) {
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
                result[index] = objectToArray_1_9_6(value);
            } else if (value && value.constructor === Array) {
                result[index] = value.map((item) =>
                    item && item.constructor === Object ? objectToArray_1_9_6(item) : item
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

function jsonSerialize_1_9_6(value, depth = 0) {
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
                .map((item) => jsonSerialize_1_9_6(item, nextDepth))
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
        const entry = jsonSerialize_1_9_6(value[key], nextDepth);
        if (entry !== '|-|-|') {
            output += `${key}=${entry};`;
        }
    }
    return output;
}

function urlParamsConcat_1_9_6(queryString, extraValue) {
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

function ensureProcessDataSession_1_9_6(context) {
    if (context.smk && context.smk1 && context.smi) {
        return context;
    }
    context.smk = context.smk || getRandomId_1_9_6(context.now || Date.now());
    context.smk1 = sm4Key_1_9_6(context.smk);
    context.smi = xorHexStringWithOne_1_9_6(context.smk);
    return context;
}

function processData_1_9_6(data, context) {
    try {
        if (context.sdkConfig.task[3]) {
            ensureProcessDataSession_1_9_6(context);
            const plainText = typeof data === 'string' ? data : JSON.stringify(data);
            return [sm4Encrypt_1_9_6(plainText, context.smk1), context.smi].reverse().join('');
        }
        return JSON.stringify(data);
    } catch (error) {
        console.error(error);
        return '';
    }
}

async function processDataForSign_1_9_6(data, context, options = {}) {
    try {
        if (!context.sdkConfig.task[3]) {
            return JSON.stringify(data);
        }
        ensureProcessDataSession_1_9_6(context);
        const plainText = typeof data === 'string' ? data : JSON.stringify(data);
        if (context.signWasmOk) {
            const runtime = await getStubRuntime_1_9_6(options.runtimeOptions);
            const encrypted = await runtime.encrypt(plainText);
            if (
                encrypted &&
                typeof encrypted === 'string' &&
                !encrypted.includes('未初始化') &&
                !encrypted.includes('失败')
            ) {
                return encrypted;
            }
        }
        return [sm4Encrypt_1_9_6(plainText, context.smk1), context.smi].reverse().join('');
    } catch (error) {
        console.error(error);
        return '';
    }
}

function unProcessData_1_9_6(cipherText, context) {
    try {
        if (context.sdkConfig.task[3]) {
            ensureProcessDataSession_1_9_6(context);
            const mask = xorHexStringWithOne_1_9_6(cipherText.slice(0, context.smk1.length));
            return JSON.parse(
                sm4Decrypt_1_9_6(cipherText.slice(context.smk1.length), sm4Key_1_9_6(mask))
            );
        }
        return JSON.parse(cipherText);
    } catch (error) {
        console.error(error);
        return null;
    }
}

function generateBsDeviceId_1_9_6(did, now = Date.now()) {
    const tail = normalizeString_1_9_6(did).slice(-4);
    return `bid-${String(now).split('').reverse().join('')}-${tail}-${guid_1_9_6(now).slice(9, 13)}`;
}

function ensureHp_1_9_6(context, now = Date.now()) {
    if (!context.hp) {
        context.hp = generateBsDeviceId_1_9_6(context.did, now);
    }
    return context.hp;
}

function serializeSignWasmBase64_1_9_6(context) {
    if (context.signWasmBase64) {
        return normalizeString_1_9_6(context.signWasmBase64);
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

function shortUserAgentForCookie_1_9_6(userAgent) {
    const normalized = normalizeString_1_9_6(userAgent);
    if (!normalized) {
        return '';
    }
    const closeIndex = normalized.indexOf(')');
    if (closeIndex > 0) {
        return normalized.slice(0, closeIndex);
    }
    return normalized.slice(0, 50);
}

function buildContentInfoPayload_1_9_6(context, now = Date.now()) {
    return processData_1_9_6([context.did || '', now], context);
}

function resolveBangcleUploadHost_1_9_6(context, options = {}) {
    const pageDomain = options.pageDomain || context.pageDomain || 'https://h5.moutai519.com.cn/';
    const resolved =
        normalizeBangcleHost_1_9_6(options.hostAd || context.hostAd || DEFAULT_BB_HOST_1_9_6, pageDomain) ||
        DEFAULT_BB_HOST_1_9_6;
    return trimTrailingSlashes_1_9_6(resolved);
}

function resolveBangcleWasmHost_1_9_6(context, options = {}) {
    const pageDomain = options.pageDomain || context.pageDomain || 'https://h5.moutai519.com.cn/';
    const candidate =
        options.hostWasm ||
        context.hostWasm ||
        context.hostAd ||
        DEFAULT_WASM_RUNTIME_FALLBACK_HOST_1_9_6;
    const resolved = normalizeBangcleHost_1_9_6(candidate, pageDomain);
    return trimTrailingSlashes_1_9_6(resolved || DEFAULT_WASM_RUNTIME_FALLBACK_HOST_1_9_6);
}

function buildBangcleUploadUrl_1_9_6(context, options = {}) {
    return `${resolveBangcleUploadHost_1_9_6(context, options)}${DEFAULT_UPLOAD_PATH_1_9_6}`;
}

function buildBangcleWasmVersionListUrl_1_9_6(context, options = {}) {
    return `${resolveBangcleWasmHost_1_9_6(context, options)}${DEFAULT_WASM_VERSION_LIST_PATH_1_9_6}`;
}

function buildBangcleWasmDownloadUrl_1_9_6(context, options = {}) {
    const baseUrl = `${resolveBangcleWasmHost_1_9_6(context, options)}${DEFAULT_WASM_DOWNLOAD_PATH_1_9_6}`;
    const version = normalizeString_1_9_6(options.version || context.signWasmVersion || '');
    return version ? `${baseUrl}?id=${encodeURIComponent(version)}` : baseUrl;
}

function buildBangcleVersionListRequest_1_9_6(context, options = {}) {
    const now = options.now || Date.now();
    return {
        method: 'GET',
        url: buildBangcleWasmVersionListUrl_1_9_6(context, options),
        headers: {
            [CONTENT_INFO_BB_HEADER_1_9_6]: buildContentInfoPayload_1_9_6(context, now),
        },
    };
}

function parseBangcleJsonPayload_1_9_6(payload) {
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

function extractBangcleWasmVersions_1_9_6(payload) {
    const parsed = parseBangcleJsonPayload_1_9_6(payload);
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

function decodeBangcleSignWasmBase64_1_9_6(value) {
    const base64 = normalizeString_1_9_6(value).trim();
    if (!base64) {
        return Buffer.alloc(0);
    }
    return Buffer.from(base64, 'base64');
}

function extractBangcleWasmDownloadPayload_1_9_6(payload) {
    const parsed = parseBangcleJsonPayload_1_9_6(payload);
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
            name: normalizeString_1_9_6(parsed.name || ''),
            version: normalizeString_1_9_6(parsed.v || parsed.version || ''),
            codeBase64: '',
            codeBytes: null,
            unavailableReason: normalizeString_1_9_6(parsed.msg),
            raw: parsed,
        };
    }
    const version = normalizeString_1_9_6(parsed.v || parsed.version || '');
    const codeBase64 =
        typeof parsed.code === 'string' ? normalizeString_1_9_6(parsed.code).trim() : '';
    if (!version || !codeBase64) {
        return {
            ok: false,
            name: normalizeString_1_9_6(parsed.name || ''),
            version,
            codeBase64,
            codeBytes: null,
            unavailableReason: 'code payload empty',
            raw: parsed,
        };
    }
    const codeBytes = decodeBangcleSignWasmBase64_1_9_6(codeBase64);
    if (!codeBytes.length) {
        return {
            ok: false,
            name: normalizeString_1_9_6(parsed.name || ''),
            version,
            codeBase64,
            codeBytes: null,
            unavailableReason: 'decoded code payload empty',
            raw: parsed,
        };
    }
    return {
        ok: true,
        name: normalizeString_1_9_6(parsed.name || ''),
        version,
        codeBase64,
        codeBytes,
        unavailableReason: '',
        raw: parsed,
    };
}

function shouldSkipBangcleHook_1_9_6(requestUrl, context, options = {}) {
    const target = normalizeString_1_9_6(requestUrl);
    if (!target) {
        return false;
    }
    if (target === buildBangcleUploadUrl_1_9_6(context, options)) {
        return true;
    }
    return (
        target.endsWith(DEFAULT_WASM_VERSION_LIST_PATH_1_9_6) ||
        target.endsWith(DEFAULT_WASM_DOWNLOAD_PATH_1_9_6)
    );
}

function buildCReportPayloadObject_1_9_6(context, options = {}) {
    const now = options.now || Date.now();
    ensureHp_1_9_6(context, now);
    return {
        101: context.sdkConfig.app_key,
        103: context.did,
        105: context.startId || '',
        107: shortUserAgentForCookie_1_9_6(options.userAgent || context.userAgent || ''),
        109: options.domain || context.pageDomain || '',
        111: context.hp || '',
        113: now,
    };
}

function buildDUCookieValue_1_9_6(context, options = {}) {
    const payload = objectToArray_1_9_6(buildCReportPayloadObject_1_9_6(context, options));
    return processData_1_9_6(payload, context);
}

function buildBangcleUploadInfoPayloadObject_1_9_6(context, options = {}) {
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

function buildBangcleUploadEventPayloadObject_1_9_6(context, options = {}) {
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

function buildBangcleUploadPayload_1_9_6(payloadObject, context) {
    return processData_1_9_6(objectToArray_1_9_6(payloadObject), context);
}

function buildBangcleUploadInfoPayload_1_9_6(context, options = {}) {
    return buildBangcleUploadPayload_1_9_6(
        buildBangcleUploadInfoPayloadObject_1_9_6(context, options),
        context
    );
}

function buildBangcleUploadEventPayload_1_9_6(context, options = {}) {
    return buildBangcleUploadPayload_1_9_6(
        buildBangcleUploadEventPayloadObject_1_9_6(context, options),
        context
    );
}

function normalizeSignWasmBytes_1_9_6(input) {
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

function createBbContext_1_9_6(options = {}) {
    const task = cloneDefaultBbTask_1_9_6();
    const pageDomain = normalizeString_1_9_6(options.pageDomain || 'https://h5.moutai519.com.cn/');
    if (Array.isArray(options.taskOverride)) {
        options.taskOverride.forEach((value, index) => {
            task[index] = value;
        });
    }
    const context = {
        sdkVersion: options.sdkVersion || SDK_VERSION_1_9_6,
        sdkConfig: {
            app_key: normalizeString_1_9_6(options.appKey || DEFAULT_BB_APP_KEY_1_9_6),
            task,
        },
        hostAd:
            normalizeBangcleHost_1_9_6(options.hostAd || DEFAULT_BB_HOST_1_9_6, pageDomain) ||
            DEFAULT_BB_HOST_1_9_6,
        hostWasm:
            normalizeBangcleHost_1_9_6(
                options.hostWasm || options.hostAd || DEFAULT_BB_HOST_1_9_6,
                pageDomain
            ) || DEFAULT_WASM_RUNTIME_FALLBACK_HOST_1_9_6,
        domain: Array.isArray(options.domain)
            ? options.domain
            : [normalizeString_1_9_6(options.domain || 'moutai519.com.cn')],
        pageDomain,
        did: normalizeString_1_9_6(options.did),
        startId: normalizeString_1_9_6(options.startId),
        userId: normalizeString_1_9_6(options.userId),
        userAgent: normalizeString_1_9_6(options.userAgent),
        hp: normalizeString_1_9_6(options.hp),
        uaid: normalizeString_1_9_6(options.uaid),
        jsid: normalizeString_1_9_6(options.jsid),
        token: normalizeString_1_9_6(options.token),
        tokenPrepetuity: normalizeString_1_9_6(options.tokenPrepetuity || options.rk),
        cookieSend: options.cookieSend !== undefined ? !!options.cookieSend : true,
        signWasmVersion: normalizeString_1_9_6(options.signWasmVersion || ''),
        signWasmOk: options.signWasmOk !== undefined ? !!options.signWasmOk : false,
        signWasmBytes: normalizeSignWasmBytes_1_9_6(options.signWasmBytes || null),
        signWasmBase64: normalizeString_1_9_6(options.signWasmBase64 || ''),
        deviceInfo: options.deviceInfo || null,
        smk: normalizeString_1_9_6(options.smk),
        smk1: normalizeString_1_9_6(options.smk1),
        smi: normalizeString_1_9_6(options.smi),
        serial: Number.isFinite(Number(options.serial)) ? Number(options.serial) : undefined,
        tps: Number.isFinite(Number(options.tps)) ? Number(options.tps) : undefined,
        tacticsVersion: Number.isFinite(Number(options.tacticsVersion))
            ? Number(options.tacticsVersion)
            : undefined,
        timeDelta: Number.isFinite(Number(options.timeDelta)) ? Number(options.timeDelta) : undefined,
        remoteTask: options.remoteTask || null,
    };
    if (!context.startId) {
        context.startId = getStartId_1_9_6();
    }
    if (!context.hp && context.did) {
        ensureHp_1_9_6(context);
    }
    return context;
}

function resolveBundledFile_1_9_6(candidates) {
    for (const candidate of candidates) {
        if (candidate && fs.existsSync(candidate)) {
            return candidate;
        }
    }
    return null;
}

class StubWasmRuntime_1_9_6 {
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
            resolveBundledFile_1_9_6([
                path.join(__dirname, 'bs_stub_1_9_6.wasm'),
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
            env: this.buildImports_1_9_6(),
            wasi_snapshot_preview1: this.buildImports_1_9_6(),
        };
        const { instance } = await WebAssembly.instantiate(wasmBinary, imports);
        this.instance = instance;
        this.exports = instance.exports;
        this.memory = this.exports.memory;
        this.syncMemoryViews_1_9_6();
        return this;
    }

    buildImports_1_9_6() {
        return {
            __cxa_throw: () => {
                throw new Error('wasm __cxa_throw');
            },
            _abort_js: () => {
                throw new Error('wasm abort');
            },
            _tzset_js: (timezonePtr, daylightPtr, stdPtr, dstPtr) =>
                this.tzset_1_9_6(timezonePtr, daylightPtr, stdPtr, dstPtr),
            emscripten_date_now: () => this.dateNowFn(),
            emscripten_resize_heap: (requestedSize) => this.resizeHeap_1_9_6(requestedSize),
            environ_get: (envPtr, bufPtr) => this.environGet_1_9_6(envPtr, bufPtr),
            environ_sizes_get: (countPtr, sizePtr) => this.environSizesGet_1_9_6(countPtr, sizePtr),
            exit: () => 0,
            fd_close: () => 52,
            fd_seek: () => 70,
            fd_write: (fd, iovPtr, iovCount, writtenPtr) =>
                this.fdWrite_1_9_6(fd, iovPtr, iovCount, writtenPtr),
            js_invoke: (targetPtr, methodPtr, argsPtr) =>
                this.jsInvoke_1_9_6(targetPtr, methodPtr, argsPtr),
        };
    }

    syncMemoryViews_1_9_6() {
        const buffer = this.memory.buffer;
        this.HEAP8 = new Int8Array(buffer);
        this.HEAPU8 = new Uint8Array(buffer);
        this.HEAP32 = new Int32Array(buffer);
        this.HEAPU32 = new Uint32Array(buffer);
    }

    resizeHeap_1_9_6(requestedSize) {
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
                this.syncMemoryViews_1_9_6();
                return true;
            } catch (error) {}
        }
        return false;
    }

    getEnvironmentStrings_1_9_6() {
        return ['USER=web_user', 'LOGNAME=web_user', 'PATH=/', 'PWD=/', 'HOME=/home/web_user', 'LANG=C.UTF-8'];
    }

    environSizesGet_1_9_6(countPtr, sizePtr) {
        const strings = this.getEnvironmentStrings_1_9_6();
        this.HEAP32[countPtr >> 2] = strings.length;
        let totalBytes = 0;
        strings.forEach((entry) => {
            totalBytes += this.lengthBytesUTF8_1_9_6(entry) + 1;
        });
        this.HEAP32[sizePtr >> 2] = totalBytes;
        return 0;
    }

    environGet_1_9_6(envPtr, bufPtr) {
        const strings = this.getEnvironmentStrings_1_9_6();
        let offset = 0;
        strings.forEach((entry, index) => {
            const ptr = bufPtr + offset;
            this.HEAP32[(envPtr >> 2) + index] = ptr;
            offset += this.stringToUTF8_1_9_6(entry, ptr, Infinity) + 1;
        });
        return 0;
    }

    fdWrite_1_9_6(fd, iovPtr, iovCount, writtenPtr) {
        let written = 0;
        for (let index = 0; index < iovCount; index += 1) {
            const base = this.HEAPU32[(iovPtr >> 2) + index * 2];
            const length = this.HEAPU32[(iovPtr >> 2) + index * 2 + 1];
            const text = this.UTF8ToString_1_9_6(base, length, true);
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

    tzset_1_9_6(timezonePtr, daylightPtr, stdPtr, dstPtr) {
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
            this.stringToUTF8_1_9_6(standard, stdPtr, 17);
            this.stringToUTF8_1_9_6(daylight, dstPtr, 17);
        } else {
            this.stringToUTF8_1_9_6(standard, dstPtr, 17);
            this.stringToUTF8_1_9_6(daylight, stdPtr, 17);
        }
    }

    lengthBytesUTF8_1_9_6(text) {
        return Buffer.byteLength(normalizeString_1_9_6(text), 'utf8');
    }

    stringToUTF8_1_9_6(text, ptr, maxBytes) {
        const buffer = Buffer.from(`${normalizeString_1_9_6(text)}\0`, 'utf8');
        const length = maxBytes === Infinity ? buffer.length : Math.min(buffer.length, maxBytes);
        this.HEAPU8.set(buffer.subarray(0, length), ptr);
        if (length < maxBytes && this.HEAPU8[ptr + length - 1] !== 0) {
            this.HEAPU8[ptr + length] = 0;
        }
        return length - 1;
    }

    UTF8ToString_1_9_6(ptr, length, noStopAtNull = false) {
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

    mallocBytes_1_9_6(bytes) {
        const ptr = this.exports.malloc(bytes.length);
        this.HEAPU8.set(bytes, ptr);
        return ptr;
    }

    mallocString_1_9_6(text) {
        const length = this.lengthBytesUTF8_1_9_6(text) + 1;
        const ptr = this.exports.malloc(length);
        this.stringToUTF8_1_9_6(text, ptr, length);
        return ptr;
    }

    free_1_9_6(ptr) {
        if (ptr) {
            this.exports.free(ptr);
        }
    }

    freeReturnedString_1_9_6(ptr) {
        if (ptr) {
            this.exports.free_memory(ptr);
        }
    }

    resolveTarget_1_9_6(name) {
        const target = normalizeString_1_9_6(name);
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
            return this.getWindowTarget_1_9_6();
        }
        if (target === 'eval') {
            return { call: (code) => this.evaluateWindowCode_1_9_6(code) };
        }
        return this.getWindowTarget_1_9_6();
    }

    getWindowTarget_1_9_6() {
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
                return self.evaluateWindowCode_1_9_6(code);
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

    evaluateWindowCode_1_9_6(code) {
        return vm.runInNewContext(normalizeString_1_9_6(code), {
            window: this.getWindowTarget_1_9_6(),
            navigator: { userAgent: this.userAgent },
            Date: {
                now: () => this.dateNowFn(),
            },
            console,
        });
    }

    parseInvokeArgs_1_9_6(rawArgs) {
        if (!rawArgs) {
            return [];
        }
        const parsed = JSON.parse(rawArgs);
        return Array.isArray(parsed) ? parsed : [parsed];
    }

    jsInvoke_1_9_6(targetPtr, methodPtr, argsPtr) {
        try {
            const targetName = this.UTF8ToString_1_9_6(targetPtr);
            const methodName = this.UTF8ToString_1_9_6(methodPtr);
            const rawArgs = argsPtr ? this.UTF8ToString_1_9_6(argsPtr) : '';
            const target = this.resolveTarget_1_9_6(targetName);
            if (!target) {
                throw new Error(`target not found: ${targetName}`);
            }
            const method = target[methodName];
            if (typeof method !== 'function') {
                throw new Error(`method not found: ${targetName}.${methodName}`);
            }
            const result = method.apply(target, this.parseInvokeArgs_1_9_6(rawArgs));
            const serialized = JSON.stringify(result);
            const ptr = this.mallocString_1_9_6(serialized);
            return ptr;
        } catch (error) {
            const ptr = this.mallocString_1_9_6(`[invoke error] ${error.message}`);
            return ptr;
        }
    }

    async encrypt(text) {
        await this.init();
        const inputPtr = this.mallocString_1_9_6(text);
        const outputPtr = this.exports.en(inputPtr);
        const output = this.UTF8ToString_1_9_6(outputPtr);
        this.free_1_9_6(inputPtr);
        this.freeReturnedString_1_9_6(outputPtr);
        return output;
    }

    async decrypt(text) {
        await this.init();
        const inputPtr = this.mallocString_1_9_6(text);
        const outputPtr = this.exports.de(inputPtr);
        const output = this.UTF8ToString_1_9_6(outputPtr);
        this.free_1_9_6(inputPtr);
        this.freeReturnedString_1_9_6(outputPtr);
        return output;
    }

    async runWac(did, sign, wasmBytes) {
        await this.init();
        const blob = normalizeSignWasmBytes_1_9_6(wasmBytes);
        if (!blob) {
            return '';
        }
        const didText = normalizeString_1_9_6(did);
        const signText = normalizeString_1_9_6(sign);
        const didPtr = this.mallocString_1_9_6(didText);
        const signPtr = this.mallocString_1_9_6(signText);
        const blobPtr = this.mallocBytes_1_9_6(blob);
        const outputPtr = this.exports.run_wac(
            didPtr,
            this.lengthBytesUTF8_1_9_6(didText),
            signPtr,
            this.lengthBytesUTF8_1_9_6(signText),
            blobPtr,
            blob.length
        );
        const output = this.UTF8ToString_1_9_6(outputPtr);
        this.free_1_9_6(didPtr);
        this.free_1_9_6(signPtr);
        this.free_1_9_6(blobPtr);
        this.freeReturnedString_1_9_6(outputPtr);
        return output;
    }
}

async function getStubRuntime_1_9_6(options = {}) {
    if (!defaultStubRuntimePromise_1_9_6 || options.noCache) {
        defaultStubRuntimePromise_1_9_6 = new StubWasmRuntime_1_9_6(options).init();
    }
    return defaultStubRuntimePromise_1_9_6;
}

async function buildRushPurchaseBbHeaders_1_9_6(bodyCanonical, context, options = {}) {
    const randomId = options.randomId || getRandomId_1_9_6(options.now || Date.now());
    const sign = bsHash_1_9_6(bodyCanonical, 27, 4096, randomId);
    let signWac = '';
    if (context.signWasmOk && context.signWasmBytes) {
        try {
            signWac = await (await getStubRuntime_1_9_6(options.runtimeOptions)).runWac(
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
    const gUd = context.signWasmOk
        ? await processDataForSign_1_9_6(objectToArray_1_9_6(payload), context, options)
        : processData_1_9_6(objectToArray_1_9_6(payload), context);
    return {
        [CONTENT_WEB_BB_HEADER_1_9_6]: gUd,
        [CONTENT_HH_BB_HEADER_1_9_6]: sign,
        [SDK_VER_BB_HEADER_1_9_6]: context.sdkVersion,
        randomId,
        sign,
        signWac,
        gUd,
    };
}

function buildBangcleBootstrapHeaders_1_9_6(context, options = {}) {
    return {
        [CONTENT_INFO_BB_HEADER_1_9_6]: buildContentInfoPayload_1_9_6(context, options.now || Date.now()),
    };
}

function buildBangcleCookieState_1_9_6(context, options = {}) {
    const now = options.now || Date.now();
    ensureHp_1_9_6(context, now);
    return {
        _sdk_v_: context.sdkVersion,
        _bs_device_id: context.hp,
        _d_u: buildDUCookieValue_1_9_6(context, options),
    };
}

function buildBangcleWrappedState_1_9_6(context, options = {}) {
    const appKey = options.appKey || context.sdkConfig?.app_key || DEFAULT_BB_APP_KEY_1_9_6;
    const now = options.now || Date.now();
    const signWasmTtlMs =
        options.signWasmTtlMs !== undefined ? options.signWasmTtlMs : SIGN_WASM_STORAGE_TTL_MS_1_9_6;
    const local = {};

    if (context.did) {
        local.__ud = wrapStoredValue_1_9_6('__ud', context.did, options.didTtlMs ?? 0, appKey);
    }
    if (context.hp) {
        local.__mg__ = wrapStoredValue_1_9_6(
            '__mg__',
            context.hp,
            options.hpTtlMs !== undefined ? options.hpTtlMs : GEN_F_TOKEN_TTL_MS_1_9_6,
            appKey
        );
    }
    if (context.tokenPrepetuity) {
        local.__rk = wrapStoredValue_1_9_6(
            '__rk',
            context.tokenPrepetuity,
            options.rkTtlMs ?? 0,
            appKey
        );
    }
    if (context.uaid) {
        local.__idua_ = wrapStoredValue_1_9_6(
            '__idua_',
            context.uaid,
            options.idTtlMs ?? 0,
            appKey
        );
    }
    if (context.jsid) {
        local.__idjs_ = wrapStoredValue_1_9_6(
            '__idjs_',
            context.jsid,
            options.idTtlMs ?? 0,
            appKey
        );
    }
    if (context.token) {
        local.__tok_ = wrapStoredValue_1_9_6(
            '__tok_',
            context.token,
            options.tokenTtlMs !== undefined ? options.tokenTtlMs : TOKEN_STORAGE_TTL_MS_1_9_6,
            appKey
        );
    }
    if (context.signWasmVersion) {
        local[WASM_SIGN_VERSION_KEY_1_9_6] = wrapStoredValue_1_9_6(
            WASM_SIGN_VERSION_KEY_1_9_6,
            context.signWasmVersion,
            signWasmTtlMs,
            appKey
        );
    }
    if (context.signWasmOk !== undefined) {
        local[WASM_SIGN_OK_STORAGE_KEY_1_9_6] = wrapStoredValue_1_9_6(
            WASM_SIGN_OK_STORAGE_KEY_1_9_6,
            context.signWasmOk ? 1 : 0,
            signWasmTtlMs,
            appKey
        );
    }
    const signWasmBase64 = serializeSignWasmBase64_1_9_6(context);
    if (signWasmBase64) {
        local[WASM_SIGN_STORAGE_KEY_1_9_6] = wrapStoredValue_1_9_6(
            WASM_SIGN_STORAGE_KEY_1_9_6,
            signWasmBase64,
            signWasmTtlMs,
            appKey
        );
    }
    if (context.sdkVersion) {
        local._bb_v_ = wrapStoredValue_1_9_6(
            '_bb_v_',
            context.sdkVersion,
            options.sdkVersionTtlMs,
            appKey
        );
    }
    if (context.serial !== undefined) {
        local.h_five_serial = wrapStoredValue_1_9_6(
            'h_five_serial',
            Number(context.serial),
            options.serialTtlMs ?? 0,
            appKey
        );
    }
    if (context.deviceInfo && typeof context.deviceInfo === 'object') {
        local.h_five_cp = wrapStoredValue_1_9_6(
            'h_five_cp',
            context.deviceInfo,
            options.deviceInfoTtlMs ?? DEVICE_INFO_STORAGE_TTL_MS_1_9_6,
            appKey
        );
    }
    if (Array.isArray(context.remoteTask)) {
        local.h_five_co = wrapStoredValue_1_9_6(
            'h_five_co',
            [
                cloneJsonValue_1_9_6(context.remoteTask),
                Number(context.timeDelta || context.sdkConfig?.td || 0),
                Number(context.tacticsVersion || 0),
            ],
            options.remoteConfigTtlMs ?? REMOTE_CONFIG_STORAGE_TTL_MS_1_9_6,
            appKey
        );
    }
    if (context.tps !== undefined) {
        local.__tps = wrapStoredValue_1_9_6(
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
        context.sessionTokenShard || deriveBangcleSessionTokenShard_1_9_6(context.tokenPrepetuity);
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
        cookieState: buildBangcleCookieState_1_9_6(context, { ...options, now }),
        globals: {
            [GLOBAL_SIGN_VERSION_1_9_6]: context.signWasmVersion || '',
            [GLOBAL_SIGN_WASM_OK_1_9_6]: !!context.signWasmOk,
        },
    };
}

module.exports = {
    SDK_VERSION_1_9_6,
    DEFAULT_BB_APP_KEY_1_9_6,
    DEFAULT_BB_HOST_1_9_6,
    DEFAULT_WASM_RUNTIME_FALLBACK_HOST_1_9_6,
    DEFAULT_UPLOAD_PATH_1_9_6,
    DEFAULT_WASM_VERSION_LIST_PATH_1_9_6,
    DEFAULT_WASM_DOWNLOAD_PATH_1_9_6,
    CONTENT_WEB_BB_HEADER_1_9_6,
    CONTENT_HH_BB_HEADER_1_9_6,
    CONTENT_INFO_BB_HEADER_1_9_6,
    SDK_VER_BB_HEADER_1_9_6,
    WASM_SIGN_STORAGE_KEY_1_9_6,
    WASM_SIGN_VERSION_KEY_1_9_6,
    WASM_SIGN_OK_STORAGE_KEY_1_9_6,
    GLOBAL_SIGN_VERSION_1_9_6,
    GLOBAL_SIGN_WASM_OK_1_9_6,
    DEFAULT_STORAGE_TTL_MS_1_9_6,
    PERSISTENT_STORAGE_TTL_MS_1_9_6,
    SIGN_WASM_STORAGE_TTL_MS_1_9_6,
    TOKEN_STORAGE_TTL_MS_1_9_6,
    GEN_F_TOKEN_TTL_MS_1_9_6,
    DEVICE_INFO_STORAGE_TTL_MS_1_9_6,
    REMOTE_CONFIG_STORAGE_TTL_MS_1_9_6,
    DEFAULT_BB_TASK_1_9_6,
    guid_1_9_6,
    getRandomId_1_9_6,
    getStartId_1_9_6,
    bsHash_1_9_6,
    sm4Key_1_9_6,
    sm4Encrypt_1_9_6,
    sm4Decrypt_1_9_6,
    wrapStoredValue_1_9_6,
    unwrapStoredValue_1_9_6,
    applyWrappedBangcleState_1_9_6,
    buildBangcleWrappedState_1_9_6,
    deriveBangcleSessionTokenShard_1_9_6,
    xorHexStringWithOne_1_9_6,
    objectToArray_1_9_6,
    jsonSerialize_1_9_6,
    urlParamsConcat_1_9_6,
    processData_1_9_6,
    processDataForSign_1_9_6,
    unProcessData_1_9_6,
    generateBsDeviceId_1_9_6,
    createBbContext_1_9_6,
    buildContentInfoPayload_1_9_6,
    buildCReportPayloadObject_1_9_6,
    buildDUCookieValue_1_9_6,
    normalizeBangcleHost_1_9_6,
    buildBangcleUploadUrl_1_9_6,
    buildBangcleWasmVersionListUrl_1_9_6,
    buildBangcleWasmDownloadUrl_1_9_6,
    buildBangcleVersionListRequest_1_9_6,
    parseBangcleJsonPayload_1_9_6,
    extractBangcleWasmVersions_1_9_6,
    decodeBangcleSignWasmBase64_1_9_6,
    extractBangcleWasmDownloadPayload_1_9_6,
    shouldSkipBangcleHook_1_9_6,
    buildBangcleUploadInfoPayloadObject_1_9_6,
    buildBangcleUploadEventPayloadObject_1_9_6,
    buildBangcleUploadPayload_1_9_6,
    buildBangcleUploadInfoPayload_1_9_6,
    buildBangcleUploadEventPayload_1_9_6,
    buildBangcleCookieState_1_9_6,
    buildBangcleBootstrapHeaders_1_9_6,
    buildRushPurchaseBbHeaders_1_9_6,
    normalizeSignWasmBytes_1_9_6,
    StubWasmRuntime_1_9_6,
    getStubRuntime_1_9_6,
};
