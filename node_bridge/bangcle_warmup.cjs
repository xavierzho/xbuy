'use strict';

const fs = require('fs');
const path = require('path');
const axios = require('axios');
const { HttpsProxyAgent } = require('https-proxy-agent');
const { SocksProxyAgent } = require('socks-proxy-agent');

const {
    SDK_VERSION_1_9_6,
    DEFAULT_BB_APP_KEY_1_9_6,
    DEFAULT_BB_HOST_1_9_6,
    buildBangcleUploadInfoPayload_1_9_6,
    buildBangcleUploadUrl_1_9_6,
    buildBangcleBootstrapHeaders_1_9_6,
    buildBangcleCookieState_1_9_6,
    buildBangcleVersionListRequest_1_9_6,
    buildBangcleWasmDownloadUrl_1_9_6,
    buildBangcleWrappedState_1_9_6,
    createBbContext_1_9_6,
    decodeBangcleSignWasmBase64_1_9_6,
    extractBangcleWasmDownloadPayload_1_9_6,
    extractBangcleWasmVersions_1_9_6,
    generateBsDeviceId_1_9_6,
    getRandomId_1_9_6,
    getStartId_1_9_6,
    processData_1_9_6,
    unProcessData_1_9_6,
} = require('./reverse/v1.9.6/bb_sdk_1_9_6');

const H5_ORIGIN = 'https://h5.moutai519.com.cn';
const LIVE_BB_WASM_VERSION = 'v1.0.8';
const PY_RUNTIME_WASM_PATH = path.resolve(__dirname, '../runtime.wasm');
const PY_SIGN_WASM_PATH = path.resolve(__dirname, '../ext.bin');
const BB_STUB_WASM_PATH = fs.existsSync(PY_RUNTIME_WASM_PATH)
    ? PY_RUNTIME_WASM_PATH
    : path.join(__dirname, 'reverse/v1.9.6/bs_stub_1_9_6.wasm');
const BB_SIGN_WASM_PATH = fs.existsSync(PY_SIGN_WASM_PATH)
    ? PY_SIGN_WASM_PATH
    : path.join(__dirname, 'reverse/v1.9.6/sign_wasm_v1_0_8_1_9_6.bin');
const LIVE_BB_STRATEGY_VERSION = 6;
const LIVE_BB_REMOTE_TASK = [
    1,
    1,
    [],
    3600,
    0,
    0,
    10,
    20,
    3,
    [
        0,
        '7a382327999a48569b105dcc7e937e03',
        '300013070715',
        '14C9C8F90EDBA243F0D0CDCB936E1770',
        'https://msg.cmpassport.com/h5/getMobile',
        'https://ua.cmpassport.com/api/h5/uaidGetCUAddress',
        'https://ua.cmpassport.com/api/h5/uaidGetCTAddress',
        'https://ua.cmpassport.com/api/h5/uaidGetCTCUToken',
        'https://cloudcenter.bangcle.com/saas/third/device/uaid',
    ],
];

function readInput() {
    const raw = fs.readFileSync(0, 'utf8').trim();
    if (!raw) return {};
    return JSON.parse(raw);
}

function cloneJson(value) {
    if (value === undefined || value === null) return value;
    return JSON.parse(JSON.stringify(value));
}

function stripQueryAndHash(value) {
    const s = String(value || '').trim();
    if (!s) return '';
    try {
        const u = new URL(s);
        return `${u.origin}${u.pathname}`;
    } catch (_) {
        return s.split(/[?#]/)[0] || s;
    }
}

function pageDomainFromInput(input) {
    return stripQueryAndHash(input.referer || input.pageDomain || `${H5_ORIGIN}/`) || `${H5_ORIGIN}/`;
}

function proxyConfig(proxyUrl) {
    const u = String(proxyUrl || '').trim();
    if (!u) return {};
    const low = u.toLowerCase();
    if (low.startsWith('socks4://') || low.startsWith('socks5://') || low.startsWith('socks5h://')) {
        const agent = new SocksProxyAgent(u);
        return { proxy: false, httpAgent: agent, httpsAgent: agent };
    }
    const agent = new HttpsProxyAgent(u);
    return { proxy: false, httpAgent: agent, httpsAgent: agent };
}

function signWasmBase64FromDisk() {
    if (!fs.existsSync(BB_SIGN_WASM_PATH)) return '';
    return fs.readFileSync(BB_SIGN_WASM_PATH).toString('base64');
}

function asBool(value, fallback) {
    if (value === undefined || value === null || value === '') return fallback;
    return !!value;
}

function warmupHeaders(input, referer) {
    return {
        'User-Agent': String(input.userAgent || ''),
        'Accept': '*/*',
        'Connection': 'keep-alive',
        'Accept-Encoding': 'gzip, deflate',
        'Origin': H5_ORIGIN,
        'X-Requested-With': 'com.moutai.mall',
        'Sec-Fetch-Site': 'same-site',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Dest': 'empty',
        'Referer': referer || `${H5_ORIGIN}/`,
        'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    };
}

function postHeadersWithLength(baseHeaders, url, body) {
    const headers = { ...baseHeaders, 'Content-Type': 'text/plain;charset=UTF-8' };
    try {
        headers.Host = new URL(url).host;
    } catch (_) {}
    if (body !== undefined && body !== null) {
        headers['Content-Length'] = String(Buffer.byteLength(String(body)));
    }
    return headers;
}

function applyRemoteStrategy(session, strategyResponse, requestStartedAt, responseReceivedAt) {
    const decoded = Array.isArray(strategyResponse)
        ? strategyResponse
        : unProcessData_1_9_6(String(strategyResponse || ''), session);
    const fallback = [cloneJson(LIVE_BB_REMOTE_TASK), Date.now(), LIVE_BB_STRATEGY_VERSION];
    const data = Array.isArray(decoded) && Array.isArray(decoded[0]) ? decoded : fallback;
    const task = Array.isArray(data[0]) ? data[0] : cloneJson(LIVE_BB_REMOTE_TASK);
    const requestAt = Number(requestStartedAt || Date.now());
    const responseAt = Number(responseReceivedAt || requestAt);
    const midpoint = requestAt + Math.round((responseAt - requestAt) / 2);
    const serverTime = Number(data[1] || midpoint);
    const tacticsVersion = Number(data[2] || LIVE_BB_STRATEGY_VERSION);

    session.sdkConfig.task[16] = task[0] || 0;
    session.sdkConfig.task[22] = task[1] || 0;
    session.sdkConfig.task[17] = Array.isArray(task[2]) ? cloneJson(task[2]) : [];
    session.sdkConfig.task[20] = typeof task[3] === 'number' && task[3] > 10 ? task[3] : 3600;
    session.sdkConfig.task[21] = task[4] || 0;
    session.sdkConfig.task[7] = task[5] !== undefined ? task[5] : 10;
    session.sdkConfig.task[15] = task[6] || 10;
    session.sdkConfig.task[23] = task[7] || 20;
    session.sdkConfig.task[24] = task[8] || 3;
    if (!Array.isArray(session.sdkConfig.task[25])) {
        session.sdkConfig.task[25] = [0, '', '', '', '', '', '', '', ''];
    }
    if (Array.isArray(task[9])) {
        for (let i = 0; i < 9; i += 1) {
            session.sdkConfig.task[25][i] = task[9][i] !== undefined ? task[9][i] : '';
        }
    }
    if (!Array.isArray(session.sdkConfig.task[18])) {
        session.sdkConfig.task[18] = [0, 0];
    }
    session.sdkConfig.task[18][0] = midpoint;
    session.sdkConfig.task[18][1] = serverTime;
    session.sdkConfig.td = serverTime - midpoint;
    session.timeDelta = session.sdkConfig.td;
    session.tacticsVersion = tacticsVersion;
    session.remoteTask = cloneJson(task);
    return { task: cloneJson(task), serverTime, midpoint, timeDelta: session.timeDelta, tacticsVersion };
}

function createSession(input, pageDomain) {
    const state = input.bbState && typeof input.bbState === 'object' ? input.bbState : {};
    const now = Number(input.now || Date.now());
    const hasSignWasm = fs.existsSync(BB_SIGN_WASM_PATH);
    const diskSignWasmBase64 = signWasmBase64FromDisk();
    const stateSignVersion = String(state.bbSignWasmVersion || '');
    const stateSignWasmBase64 = stateSignVersion === LIVE_BB_WASM_VERSION
        ? String(state.bbSignWasmBase64 || '')
        : '';
    const signWasmBase64 = diskSignWasmBase64 || stateSignWasmBase64;
    const signWasmBytes = signWasmBase64
        ? decodeBangcleSignWasmBase64_1_9_6(signWasmBase64)
        : (hasSignWasm ? BB_SIGN_WASM_PATH : null);
    const requestedDid = String(input.bbDid || '').trim();
    const stateDid = String(state.bbDid || '').trim();
    const did = requestedDid || stateDid || `h${getRandomId_1_9_6(now).slice(1)}`;
    const stateMatchesDid = !!stateDid && stateDid === did;
    const stateHp = stateMatchesDid
        ? (state.bbDeviceCookie || state.bbCookieState?._bs_device_id || '')
        : '';
    return createBbContext_1_9_6({
        appKey: DEFAULT_BB_APP_KEY_1_9_6,
        hostAd: DEFAULT_BB_HOST_1_9_6,
        sdkVersion: state.bbSdkVersion || SDK_VERSION_1_9_6,
        did,
        startId: state.bbStartId || getStartId_1_9_6(now),
        userId: String(input.uid || state.userid || ''),
        userAgent: String(input.userAgent || ''),
        hp: stateHp || generateBsDeviceId_1_9_6(did, now),
        uaid: state.bbUaid || '',
        jsid: state.bbJsid || '',
        token: state.bbToken || '',
        tokenPrepetuity: state.bbRk || getRandomId_1_9_6(now + 1),
        signWasmVersion: LIVE_BB_WASM_VERSION,
        signWasmOk: !!signWasmBytes,
        signWasmBytes,
        signWasmBase64,
        pageDomain,
        serial: Number(state.bbSerial || 1),
        tps: Number(state.bbTps || state.bbSerial || 1),
        tacticsVersion: Number(state.bbTacticsVersion || 0),
        timeDelta: Number(state.bbTimeDelta || 0),
        remoteTask: cloneJson(state.bbRemoteTask || null),
        deviceInfo: cloneJson(state.bbDeviceInfo || null),
    });
}

function buildState(session, input, pageDomain, now, flags) {
    const wrapped = buildBangcleWrappedState_1_9_6(session, {
        now,
        pageDomain,
        userAgent: String(input.userAgent || ''),
        h_c: '1',
    });
    return {
        bbDid: session.did || '',
        bbStartId: session.startId || '',
        bbDeviceCookie: session.hp || '',
        bbUaid: session.uaid || '',
        bbJsid: session.jsid || '',
        bbToken: session.token || '',
        bbRk: session.tokenPrepetuity || '',
        bbSignWasmBase64: session.signWasmBase64 || '',
        bbSignWasmVersion: session.signWasmVersion || LIVE_BB_WASM_VERSION,
        bbSignWasmOk: !!session.signWasmOk,
        bbSdkVersion: session.sdkVersion || SDK_VERSION_1_9_6,
        bbPageDomain: pageDomain,
        bbSerial: Number(session.serial || 1),
        bbTps: Number(session.tps || session.serial || 1),
        bbTimeDelta: Number(session.timeDelta || 0),
        bbTacticsVersion: Number(session.tacticsVersion || 0),
        bbRemoteTask: cloneJson(session.remoteTask || null),
        bbDeviceInfo: cloneJson(session.deviceInfo || null),
        bbLocalStorage: wrapped.local,
        bbSessionStorage: wrapped.session,
        bbCookieState: wrapped.cookieState,
        __bbWarmupAlg: 'py-v1.0.8',
        __bbWarmupAt: now,
        __bbStrategyAt: flags.strategyApplied ? now : Number(input.bbState?.__bbStrategyAt || 0),
        __bbWasmFetched: !!(flags.wasmFetched || flags.localWasm || input.bbState?.__bbWasmFetched),
        __bbWasmFetchedAt: (flags.wasmFetched || flags.localWasm) ? now : Number(input.bbState?.__bbWasmFetchedAt || 0),
        __bbWasmUnavailableReason: flags.wasmUnavailableReason || '',
    };
}

async function main() {
    const input = readInput();
    const now = Number(input.now || Date.now());
    const pageDomain = pageDomainFromInput(input);
    const referer = String(input.referer || pageDomain || `${H5_ORIGIN}/`);
    const timeout = Math.max(1000, Math.min(30000, Number(input.timeoutMs || 12000)));
    const proxy = proxyConfig(input.proxyUrl);
    const headers = warmupHeaders(input, referer);
    const session = createSession(input, pageDomain);
    const flags = {
        strategyApplied: false,
        wasmFetched: false,
        localWasm: fs.existsSync(BB_SIGN_WASM_PATH),
        wasmUnavailableReason: '',
    };

    let strategyStatus = 0;
    try {
        const started = Date.now();
        const strategyUrl = buildBangcleUploadUrl_1_9_6(session);
        const strategyBody = processData_1_9_6(session.sdkConfig.app_key, session);
        const strategyResp = await axios.post(
            strategyUrl,
            strategyBody,
            {
                headers: postHeadersWithLength(headers, strategyUrl, strategyBody),
                timeout,
                validateStatus: () => true,
                ...proxy,
            }
        );
        strategyStatus = Number(strategyResp.status || 0);
        if (strategyResp.status >= 200 && strategyResp.status < 300 && strategyResp.data) {
            applyRemoteStrategy(session, strategyResp.data, started, Date.now());
            flags.strategyApplied = true;
        } else {
            applyRemoteStrategy(session, [cloneJson(LIVE_BB_REMOTE_TASK), now, LIVE_BB_STRATEGY_VERSION], now, now);
        }
    } catch (err) {
        applyRemoteStrategy(session, [cloneJson(LIVE_BB_REMOTE_TASK), now, LIVE_BB_STRATEGY_VERSION], now, now);
        flags.strategyError = err && err.message || String(err);
    }

    const versionReq = buildBangcleVersionListRequest_1_9_6(session, { now: Date.now() });
    const versionResp = await axios.get(versionReq.url, {
        headers: { ...headers, ...versionReq.headers },
        timeout,
        validateStatus: () => true,
        ...proxy,
    });
    const versionInfo = extractBangcleWasmVersions_1_9_6(versionResp.data);
    session.signWasmVersion = LIVE_BB_WASM_VERSION;
    if (versionInfo.raw && (!versionInfo.versions || versionInfo.versions.includes(LIVE_BB_WASM_VERSION))) {
        session.signWasmOk = !!session.signWasmBytes && versionInfo.signWasmOk !== false;
    }

    if ((!input.bbState?.__bbWasmFetched || input.forceWasm) && !flags.localWasm) {
        const wasmResp = await axios.get(buildBangcleWasmDownloadUrl_1_9_6(session, { version: LIVE_BB_WASM_VERSION }), {
            headers: { ...headers, ...buildBangcleBootstrapHeaders_1_9_6(session, { now: Date.now() }) },
            timeout,
            validateStatus: () => true,
            ...proxy,
        });
        if (wasmResp.status >= 200 && wasmResp.status < 300) {
            const wasmInfo = extractBangcleWasmDownloadPayload_1_9_6(wasmResp.data);
            if (wasmInfo.ok && wasmInfo.version === LIVE_BB_WASM_VERSION) {
                session.signWasmVersion = LIVE_BB_WASM_VERSION;
                session.signWasmBytes = wasmInfo.codeBytes;
                session.signWasmBase64 = wasmInfo.codeBase64;
                session.signWasmOk = true;
                flags.wasmFetched = true;
            } else {
                flags.wasmUnavailableReason = wasmInfo.unavailableReason || `unexpected sign wasm ${wasmInfo.version || ''}`.trim();
            }
        } else {
            flags.wasmUnavailableReason = `HTTP ${wasmResp.status}`;
        }
    }

    const serial = Number(session.serial || 1);
    const uploadBody = buildBangcleUploadInfoPayload_1_9_6(session, {
        now: Date.now(),
        userId: String(input.uid || ''),
        tokenPrepetuity: session.tokenPrepetuity,
        serial,
        tacticsVersion: session.tacticsVersion,
        timeDelta: session.timeDelta,
        deviceInfo: session.deviceInfo,
    });
    const uploadUrl = buildBangcleUploadUrl_1_9_6(session);
    const uploadResp = await axios.post(uploadUrl, uploadBody, {
        headers: postHeadersWithLength(headers, uploadUrl, uploadBody),
        timeout,
        validateStatus: () => true,
        ...proxy,
    });
    if (uploadResp.status >= 200 && uploadResp.status < 300) {
        session.serial = serial + 1;
        session.tps = serial + 1;
    }

    const state = buildState(session, input, pageDomain, now, flags);
    process.stdout.write(JSON.stringify({
        success: true,
        skipped: false,
        state,
        summary: {
            strategyStatus,
            versionStatus: Number(versionResp.status || 0),
            uploadStatus: Number(uploadResp.status || 0),
            wasmFetched: !!flags.wasmFetched,
            localWasm: !!flags.localWasm,
            strategyApplied: !!flags.strategyApplied,
            wasmUnavailableReason: flags.wasmUnavailableReason || '',
        },
    }));
}

main().catch((err) => {
    process.stdout.write(JSON.stringify({
        success: false,
        message: err && err.message || String(err),
        stack: err && err.stack || '',
    }));
    process.exitCode = 1;
});
