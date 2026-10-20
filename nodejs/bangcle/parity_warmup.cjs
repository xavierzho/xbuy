'use strict';

const fs = require('fs');
const path = require('path');
const { installGlobalParityRng } = require('../shared/parity_rng.cjs');
const { runtimeWasmPath, signWasmPath } = require('../shared/paths.cjs');

const {
    SDK_VERSION,
    DEFAULT_BB_APP_KEY,
    DEFAULT_BB_HOST,
    buildBangcleUploadUrl,
    buildBangcleVersionListRequest,
    buildBangcleUploadInfoPayload,
    buildBangcleWrappedState,
    createBbContext,
    decodeBangcleSignWasmBase64,
    extractBangcleWasmVersions,
    generateBsDeviceId,
    getRandomId,
    getStartId,
    processData,
    unProcessData,
} = require('@rushbuy/bb/bb_sdk');

const H5_ORIGIN = 'https://h5.moutai519.com.cn';
const LIVE_BB_WASM_VERSION = 'v1.1.3.2';
const BB_SIGN_WASM_PATH = signWasmPath();
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
    [0, '7a382327999a48569b105dcc7e937e03', '300013070715', '14C9C8F90EDBA243F0D0CDCB936E1770', 'https://msg.cmpassport.com/h5/getMobile', 'https://ua.cmpassport.com/api/h5/uaidGetCUAddress', 'https://ua.cmpassport.com/api/h5/uaidGetCTAddress', 'https://ua.cmpassport.com/api/h5/uaidGetCTCUToken', 'https://cloudcenter.bangcle.com/saas/third/device/uaid'],
];

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

function signWasmBase64FromDisk() {
    if (!fs.existsSync(BB_SIGN_WASM_PATH)) return '';
    return fs.readFileSync(BB_SIGN_WASM_PATH).toString('base64');
}

function applyRemoteStrategy(session, strategyResponse, requestStartedAt, responseReceivedAt) {
    const decoded = Array.isArray(strategyResponse)
        ? strategyResponse
        : unProcessData(String(strategyResponse || ''), session);
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
        ? decodeBangcleSignWasmBase64(signWasmBase64)
        : (hasSignWasm ? BB_SIGN_WASM_PATH : null);
    const requestedDid = String(input.bbDid || '').trim();
    const stateDid = String(state.bbDid || '').trim();
    const did = requestedDid || stateDid || `h${getRandomId(now).slice(1)}`;
    const stateMatchesDid = !!stateDid && stateDid === did;
    const stateHp = stateMatchesDid
        ? (state.bbDeviceCookie || state.bbCookieState?._bs_device_id || '')
        : '';
    return createBbContext({
        appKey: DEFAULT_BB_APP_KEY,
        hostAd: DEFAULT_BB_HOST,
        sdkVersion: state.bbSdkVersion || SDK_VERSION,
        did,
        startId: state.bbStartId || input.bbStartId || getStartId(now),
        userId: String(input.uid || state.userid || ''),
        userAgent: String(input.userAgent || ''),
        hp: stateHp || input.bbDeviceCookie || generateBsDeviceId(did, now),
        uaid: state.bbUaid || '',
        jsid: state.bbJsid || '',
        token: state.bbToken || '',
        tokenPrepetuity: state.bbRk || input.bbRk || getRandomId(now + 1),
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
    const wrapped = buildBangcleWrappedState(session, {
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
        bbSdkVersion: session.sdkVersion || SDK_VERSION,
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
        __bbWarmupAlg: 'py-v1.1.3.2',
        __bbWarmupAt: now,
        __bbStrategyAt: flags.strategyApplied ? now : Number(input.bbState?.__bbStrategyAt || 0),
        __bbWasmFetched: !!(flags.wasmFetched || flags.localWasm || input.bbState?.__bbWasmFetched),
        __bbWasmFetchedAt: (flags.wasmFetched || flags.localWasm) ? now : Number(input.bbState?.__bbWasmFetchedAt || 0),
        __bbWasmUnavailableReason: flags.wasmUnavailableReason || '',
    };
}

function buildParityArtifacts(fixture = {}) {
    const seed = Number(fixture.deterministicSeed || fixture.deterministic_seed || 42);
    installGlobalParityRng(seed);
    const baseNow = Number(fixture.now ?? fixture.nowMs ?? Date.now());
    const offsets = fixture.clockOffsetsMs || {};
    let clockMs = baseNow;
    const realNow = Date.now;
    Date.now = () => clockMs;

    try {
        const input = { ...fixture, now: baseNow };
        const pageDomain = pageDomainFromInput(input);
        const session = createSession(input, pageDomain);
        const flags = {
            strategyApplied: false,
            wasmFetched: false,
            localWasm: fs.existsSync(BB_SIGN_WASM_PATH),
            wasmUnavailableReason: '',
        };

        clockMs = baseNow + Number(offsets.strategyStarted || 0);
        const strategyStarted = clockMs;
        clockMs = baseNow + Number(offsets.strategyResponse || 50);
        const strategyResponseAt = clockMs;
        clockMs = baseNow;

        const strategyUrl = buildBangcleUploadUrl(session);
        const strategyBody = processData(session.sdkConfig.app_key, session);
        applyRemoteStrategy(
            session,
            fixture.strategyResponse ?? [cloneJson(LIVE_BB_REMOTE_TASK), baseNow, LIVE_BB_STRATEGY_VERSION],
            strategyStarted,
            strategyResponseAt,
        );
        flags.strategyApplied = true;

        clockMs = baseNow + Number(offsets.versionInfo || 100);
        const versionNow = clockMs;
        const versionReq = buildBangcleVersionListRequest(session, { now: versionNow });
        const versionPayload = fixture.versionResponse || { v: [LIVE_BB_WASM_VERSION], w: true };
        const versionInfo = extractBangcleWasmVersions(versionPayload);
        session.signWasmVersion = LIVE_BB_WASM_VERSION;
        if (versionInfo.raw && (!versionInfo.versions?.length || versionInfo.versions.includes(LIVE_BB_WASM_VERSION))) {
            session.signWasmOk = !!session.signWasmBytes && versionInfo.signWasmOk !== false;
        }

        if (!fixture.skipWasmDownload && (!input.bbState?.__bbWasmFetched || input.forceWasm) && !flags.localWasm) {
            flags.wasmUnavailableReason = 'parity: wasm download not mocked';
        }

        clockMs = baseNow + Number(offsets.upload || 300);
        const uploadNow = clockMs;
        const serial = Number(session.serial || 1);
        const uploadBody = buildBangcleUploadInfoPayload(session, {
            now: uploadNow,
            userId: String(input.uid || ''),
            tokenPrepetuity: session.tokenPrepetuity,
            serial,
            tacticsVersion: session.tacticsVersion,
            timeDelta: session.timeDelta,
            deviceInfo: session.deviceInfo,
        });
        const uploadUrl = buildBangcleUploadUrl(session);
        const uploadStatus = Number(fixture.uploadStatus ?? 200);
        if (uploadStatus >= 200 && uploadStatus < 300) {
            session.serial = serial + 1;
            session.tps = serial + 1;
        }

        clockMs = baseNow;
        const state = buildState(session, input, pageDomain, baseNow, flags);

        return {
            schema: 'bangcle_warmup_parity_v1',
            strategyUrl,
            strategyBody,
            versionRequest: {
                url: versionReq.url,
                contentInfoHeader: versionReq.headers['Content-Info-Bb'],
            },
            uploadUrl,
            uploadBody,
            state,
            summary: {
                strategyStatus: 200,
                versionStatus: 200,
                uploadStatus,
                wasmFetched: !!flags.wasmFetched,
                localWasm: !!flags.localWasm,
                strategyApplied: !!flags.strategyApplied,
                wasmUnavailableReason: flags.wasmUnavailableReason || '',
            },
        };
    } finally {
        Date.now = realNow;
    }
}

function readInput() {
    const raw = fs.readFileSync(0, 'utf8').trim();
    if (!raw) return {};
    return JSON.parse(raw);
}

if (require.main === module) {
    const out = buildParityArtifacts(readInput());
    process.stdout.write(JSON.stringify(out));
}

module.exports = { buildParityArtifacts };
