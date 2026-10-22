#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

const shared = require('../shared/paths.cjs');
const BB_DIR = __dirname;
const ROOT = shared.REPO_ROOT;
const RUNTIME_DIR = shared.RUNTIME_DIR;
const REVERSE_DIR = path.join(BB_DIR, 'reverse');
const VERSION_PATH = shared.versionPath();
const runtimeArtifactPath = (versionMeta) => shared.runtimeWasmPath(versionMeta);
const signArtifactPath = (versionMeta) => shared.signWasmPath(versionMeta);
const DEFAULT_PAGE = 'https://h5.moutai519.com.cn/mt/item/smsp-detail?appConfig=2_1_2';
const IOS_BUNDLE_ID = 'com.moutai.mall';
const ITUNES_LOOKUP_URL = `https://itunes.apple.com/cn/lookup?bundleId=${encodeURIComponent(IOS_BUNDLE_ID)}&limit=1`;

function usage(code = 0) {
    console.log(`用法:
  node nodejs/bb/update.cjs --check
  node nodejs/bb/update.cjs --apply
  node nodejs/bb/update.cjs --auto
  node nodejs/bb/update.cjs --sync

选项:
  --page URL          H5 入口页（默认当前商品详情页）
  --sign-version VER  覆盖 sign wasm 版本号写入 version.json
  --app-version VER   覆盖 App versionName
  --version-code NUM  覆盖 versionCode

从 H5 CDN 拉 runtime.wasm，从 Bangcle /api/v1/1/1（无 id）拉最新 sign_wasm；App 版本来自 App Store lookup。
--sync：校验通过后一律写入（推荐：scripts/bb_fetch_latest.sh）。
--auto：有变更才写入（定时任务可用 MT_BB_UPDATE_MODE=auto）。
MT_BB_UPDATE_SIGN_FALLBACK=0 可禁用 sign 下载失败时的本地 sign_wasm.bin 回退（--sync 始终要求线上成功）。`);
    process.exit(code);
}

function parseArgs(argv) {
    const out = {
        mode: '',
        page: DEFAULT_PAGE,
        signVersion: '',
        appVersion: '',
        versionCode: '',
    };
    for (let i = 0; i < argv.length; i += 1) {
        const arg = argv[i];
        if (arg === '--check' || arg === '--apply' || arg === '--auto' || arg === '--sync') {
            out.mode = arg.slice(2);
        } else if (arg === '--help' || arg === '-h') usage(0);
        else if (arg === '--transport' || arg === '--sign-source' || arg === '--ios-dump' || arg === '--sign-file') {
            i += 1;
        } else if (arg.startsWith('--')) {
            const key = arg.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
            if (i + 1 >= argv.length) usage(2);
            out[key] = argv[++i];
        } else usage(2);
    }
    if (!out.mode) usage(2);
    return out;
}

const sha256 = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');
const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

function localFetch(url, headers = {}) {
    const script = `const u=${JSON.stringify(url)};const h=${JSON.stringify({ 'user-agent': 'Mozilla/5.0', ...headers })};fetch(u,{redirect:'follow',headers:h}).then(async r=>{if(!r.ok)throw Error('HTTP '+r.status);process.stdout.write(Buffer.from(await r.arrayBuffer()))}).catch(e=>{console.error(e.message);process.exit(1)})`;
    const result = spawnSync(process.execPath, ['-e', script], { encoding: null, maxBuffer: 16 * 1024 * 1024 });
    if (result.status !== 0 || !result.stdout?.length) throw new Error(String(result.stderr || '本机下载失败'));
    return result.stdout;
}

function fetchBytes(url, headers = {}) {
    return { bytes: localFetch(url, headers), transport: 'local' };
}

function fetchPostBytes(url, headers = {}, body = '') {
    const bodyText = body === undefined || body === null ? '' : String(body);
    const script = `const u=${JSON.stringify(url)};const h=${JSON.stringify({ 'user-agent': 'Mozilla/5.0', 'content-type': 'text/plain;charset=UTF-8', ...headers })};const b=${JSON.stringify(bodyText)};fetch(u,{method:'POST',redirect:'follow',headers:h,body:b}).then(async r=>{if(!r.ok)throw Error('HTTP '+r.status);process.stdout.write(Buffer.from(await r.arrayBuffer()))}).catch(e=>{console.error(e.message);process.exit(1)})`;
    const result = spawnSync(process.execPath, ['-e', script], { encoding: null, maxBuffer: 16 * 1024 * 1024 });
    if (result.status !== 0 || !result.stdout?.length) {
        throw new Error(String(result.stderr || '本机 POST 失败'));
    }
    return result.stdout;
}

function postBytes(url, headers = {}, body = '') {
    return { bytes: fetchPostBytes(url, headers, body), transport: 'local' };
}

const LIVE_STATE_PATH = path.join(BB_DIR, 'live_bangcle_state.json');
const DEFAULT_REMOTE_TASK = [
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

function signFallbackAllowed() {
    const v = String(process.env.MT_BB_UPDATE_SIGN_FALLBACK || '').trim().toLowerCase();
    return v !== '0' && v !== 'false' && v !== 'no' && v !== 'off';
}

function envTruthy(name, defaultTrue = true) {
    const raw = process.env[name];
    if (raw === undefined || raw === null || String(raw).trim() === '') {
        return defaultTrue;
    }
    const v = String(raw).trim().toLowerCase();
    return v === '1' || v === 'true' || v === 'yes' || v === 'on';
}

/**
 * App 版本号默认与 iTunes lookup 的 results[0].version 对齐（可升可降）。
 * MT_BB_ITUNES_APP_VERSION=0 保持 version.json；MT_APP_VERSION 强制覆盖。
 */
function resolveAppVersion(current, storeInfo, args) {
    const currentVer = String(current.appVersion || '').trim();
    if (args.appVersion) {
        return { appVersion: String(args.appVersion).trim(), appVersionSource: 'cli:--app-version' };
    }
    const pinned = String(process.env.MT_APP_VERSION || '').trim();
    if (pinned) {
        return { appVersion: pinned, appVersionSource: 'env:MT_APP_VERSION' };
    }
    const storeVersion =
        storeInfo && storeInfo.appVersion && !storeInfo.error ? String(storeInfo.appVersion).trim() : '';
    if (!envTruthy('MT_BB_ITUNES_APP_VERSION', true)) {
        return {
            appVersion: currentVer,
            appVersionSource: 'pinned:MT_BB_ITUNES_APP_VERSION=0',
            storeLookupVersion: storeVersion || undefined,
        };
    }
    if (!storeVersion) {
        return { appVersion: currentVer, appVersionSource: 'unchanged:no_itunes' };
    }
    const source =
        !currentVer || storeVersion !== currentVer ? 'itunes_lookup' : 'itunes_lookup:unchanged';
    return {
        appVersion: storeVersion,
        appVersionSource: source,
        storeLookupVersion: storeVersion,
    };
}

function readLiveBangcleState() {
    if (!fs.existsSync(LIVE_STATE_PATH)) return null;
    try {
        return JSON.parse(fs.readFileSync(LIVE_STATE_PATH, 'utf8'));
    } catch (_) {
        return null;
    }
}

function createBangcleContextForUpdate(bb, { page, versionMeta }) {
    const live = readLiveBangcleState();
    const signPath = signArtifactPath(versionMeta);
    const seedSignBytes = fs.existsSync(signPath) ? fs.readFileSync(signPath) : null;
    const opts = {
        sdkVersion: versionMeta.sdkVersion,
        pageDomain: page,
        signWasmVersion: versionMeta.signWasmVersion,
        tokenPrepetuity: bb.getRandomId(Date.now()),
    };
    if (live?.ua) opts.userAgent = live.ua;
    if (live?.session?.__36_i__) opts.startId = live.session.__36_i__;
    const wrappers = live?.decoded?.local_wrappers;
    if (wrappers?.__ud?.value) opts.did = String(wrappers.__ud.value);
    if (wrappers?.__mg__?.value) opts.hp = String(wrappers.__mg__.value);
    if (live?.bb?.__SIGN_WASM_VERSION__) {
        opts.signWasmVersion = String(live.bb.__SIGN_WASM_VERSION__);
    }
    if (seedSignBytes?.length) {
        opts.signWasmOk = true;
        opts.signWasmBytes = seedSignBytes;
    }
    const context = bb.createBbContext(opts);
    if (live) {
        bb.applyWrappedBangcleState(context, {
            local: live.local || {},
            session: live.session || {},
            cookieState: live.decoded?.cookies || {},
        });
    }
    if (context.sdkConfig?.task) {
        context.sdkConfig.task[3] = 1;
    }
    return context;
}

function applyRemoteStrategy(bb, session, responsePayload, requestStartedAt, responseReceivedAt) {
    const decoded = Array.isArray(responsePayload)
        ? responsePayload
        : bb.unProcessData(String(responsePayload || ''), session);
    const fallback = [JSON.parse(JSON.stringify(DEFAULT_REMOTE_TASK)), Date.now(), 6];
    const data = Array.isArray(decoded) && Array.isArray(decoded[0]) ? decoded : fallback;
    const task = Array.isArray(data[0]) ? data[0] : fallback[0];
    const taskCfg = session.sdkConfig.task;
    taskCfg[16] = task[0] || 0;
    taskCfg[22] = task[1] || 0;
    taskCfg[17] = Array.isArray(task[2]) ? task[2].slice() : [];
    taskCfg[20] = typeof task[3] === 'number' && task[3] > 10 ? task[3] : 3600;
    taskCfg[21] = task[4] || 0;
    taskCfg[7] = task[5] !== undefined ? task[5] : 10;
    taskCfg[15] = task[6] || 10;
    taskCfg[23] = task[7] || 20;
    taskCfg[24] = task[8] || 3;
    if (!Array.isArray(taskCfg[25])) {
        taskCfg[25] = [0, '', '', '', '', '', '', '', ''];
    }
    if (Array.isArray(task[9])) {
        for (let i = 0; i < 9; i += 1) {
            taskCfg[25][i] = task[9][i] !== undefined ? task[9][i] : '';
        }
    }
    const requestAt = Number(requestStartedAt || Date.now());
    const responseAt = Number(responseReceivedAt || requestAt);
    const midpoint = requestAt + Math.round((responseAt - requestAt) / 2);
    const serverTime = Number(data[1] || midpoint);
    session.tacticsVersion = Number(data[2] || 6);
    session.timeDelta = serverTime - midpoint;
}

function signVersionDownloadIds(versionInfo, currentVersion, requestedVersion) {
    const ids = [];
    const add = (value) => {
        const v = String(value || '').trim();
        if (v && !ids.includes(v)) ids.push(v);
    };
    add(requestedVersion);
    add(currentVersion);
    const rawList = versionInfo.raw && Array.isArray(versionInfo.raw.v) ? versionInfo.raw.v : [];
    for (const entry of rawList) {
        if (typeof entry === 'string') {
            add(entry);
            continue;
        }
        if (entry && typeof entry === 'object') {
            add(entry.id || entry.hash || entry.i);
            add(entry.v || entry.version);
        }
    }
    for (const entry of versionInfo.versions || []) add(entry);
    const live = readLiveBangcleState();
    const hashId = live?.decoded?.local_wrappers?.__wasm_sign_version__?.value;
    add(hashId);
    return ids;
}

function loadBundledSignWasmFallback(versionMeta, requestedVersion, networkMeta) {
    const signPath = signArtifactPath(versionMeta);
    if (!fs.existsSync(signPath)) {
        throw new Error(
            `sign wasm 下载失败: ${networkMeta.reason}；且本地无 ${signPath}（请检查网络或勿设 MT_BB_UPDATE_SIGN_FALLBACK=0）`,
        );
    }
    return {
        bytes: fs.readFileSync(signPath),
        version: requestedVersion || versionMeta.signWasmVersion,
        versions: networkMeta.versions || [],
        sourceUrl: signPath,
        transport: 'bundled',
        networkFetch: networkMeta,
    };
}

function fetchSignWasm(bb, versionMeta, requestedVersion) {
    const currentVersion = versionMeta.signWasmVersion;
    const context = createBangcleContextForUpdate(bb, { page: DEFAULT_PAGE, versionMeta });

    try {
        const strategyUrl = bb.buildBangcleUploadUrl(context);
        const strategyBody = bb.processData(context.sdkConfig.app_key, context);
        const started = Date.now();
        const strategyResult = postBytes(strategyUrl, {}, strategyBody);
        applyRemoteStrategy(
            bb,
            context,
            strategyResult.bytes.toString('utf8'),
            started,
            Date.now(),
        );
    } catch (strategyErr) {
        applyRemoteStrategy(bb, context, null, Date.now(), Date.now());
        context.strategyError = String(strategyErr.message || strategyErr);
    }

    const versionReq = bb.buildBangcleVersionListRequest(context);
    const versionResult = fetchBytes(versionReq.url, versionReq.headers);
    const versionInfo = bb.extractBangcleWasmVersions(versionResult.bytes.toString('utf8'));
    const downloadIds = signVersionDownloadIds(versionInfo, currentVersion, requestedVersion);
    if (!downloadIds.length) throw new Error('版本列表中没有 sign wasm 版本');

    try {
        const uploadUrl = bb.buildBangcleUploadUrl(context);
        const uploadBody = bb.buildBangcleUploadInfoPayload(context, {
            now: Date.now(),
            serial: Number(context.serial || 1),
            tacticsVersion: context.tacticsVersion,
            timeDelta: context.timeDelta,
        });
        postBytes(uploadUrl, {}, uploadBody);
    } catch (_) {
        // 上报失败不阻断 wasm 拉取
    }

    const bootstrapHeaders = bb.buildBangcleBootstrapHeaders(context);
    let lastReason = '';

    function tryDownloadUrl(downloadUrl, downloadIdLabel) {
        const downloadResult = fetchBytes(downloadUrl, bootstrapHeaders);
        const payload = bb.extractBangcleWasmDownloadPayload(downloadResult.bytes.toString('utf8'));
        if (payload.ok && payload.codeBytes?.length) {
            const version = payload.version || downloadIdLabel || currentVersion;
            if (
                downloadIdLabel &&
                downloadIdLabel.startsWith('v') &&
                payload.version &&
                payload.version !== downloadIdLabel
            ) {
                throw new Error(`sign wasm 版本不匹配: 请求 ${downloadIdLabel}，返回 ${payload.version}`);
            }
            return {
                bytes: payload.codeBytes,
                version,
                versions: versionInfo.versions,
                sourceUrl: downloadUrl,
                transport: 'local',
            };
        }
        lastReason = payload.unavailableReason || 'empty payload';
        return null;
    }

    const latestUrl = bb.buildBangcleWasmDownloadUrl(context, { latest: true });
    const latestHit = tryDownloadUrl(latestUrl, '');
    if (latestHit) return latestHit;

    for (const downloadId of downloadIds) {
        const downloadUrl = bb.buildBangcleWasmDownloadUrl(context, { version: downloadId });
        const hit = tryDownloadUrl(downloadUrl, downloadId);
        if (hit) return hit;
    }

    const networkMeta = {
        ok: false,
        reason: lastReason || 'empty payload',
        versions: versionInfo.versions,
        attemptedIds: downloadIds,
        strategyError: context.strategyError || '',
    };
    if (!signFallbackAllowed()) {
        throw new Error(`sign wasm 下载失败: ${networkMeta.reason}（已禁用 bundled 回退）`);
    }
    return loadBundledSignWasmFallback(versionMeta, requestedVersion, networkMeta);
}

function extractSdkVersion(scriptText) {
    const text = String(scriptText || '');
    const patterns = [
        /sdkVersion[=:]\s*["'](V3\.5\.0[^"']+)["']/i,
        /["'](V3\.5\.0_\d{8}\.\d+_imaotai)["']/,
    ];
    for (const pattern of patterns) {
        const match = text.match(pattern);
        if (match && match[1]) return match[1].trim();
    }
    return '';
}

function parseSemverParts(version) {
    const core = String(version || '').trim().split('-')[0];
    const parts = core.split('.').map((p) => Number.parseInt(p, 10));
    while (parts.length < 3) parts.push(0);
    return parts.slice(0, 3);
}

function compareSemver(a, b) {
    const pa = parseSemverParts(a);
    const pb = parseSemverParts(b);
    for (let i = 0; i < 3; i += 1) {
        const da = Number.isFinite(pa[i]) ? pa[i] : 0;
        const db = Number.isFinite(pb[i]) ? pb[i] : 0;
        if (da > db) return 1;
        if (da < db) return -1;
    }
    return 0;
}

/** 与 Android versionCode 习惯一致：1.9.16 → 10916（见历史 1.9.10 → 10910）。 */
function appVersionToVersionCode(appVersion) {
    const [major, minor, patch] = parseSemverParts(appVersion);
    if (![major, minor, patch].every((n) => Number.isFinite(n))) return null;
    return major * 10000 + minor * 100 + patch;
}

function fetchAppStoreVersion() {
    try {
        const result = fetchBytes(ITUNES_LOOKUP_URL);
        const parsed = JSON.parse(result.bytes.toString('utf8'));
        const row = Array.isArray(parsed.results) && parsed.results.length ? parsed.results[0] : null;
        if (!row || !row.version) return null;
        return {
            appVersion: String(row.version).trim(),
            trackId: row.trackId,
            releaseDate: row.currentVersionReleaseDate || '',
            source: 'itunes_lookup',
        };
    } catch (err) {
        return { error: String(err.message || err), source: 'itunes_lookup' };
    }
}

function extractSdkUrl(html, pageUrl) {
    const patterns = [
        /<script[^>]+id=["']bb-sdk["'][^>]+src=["']([^"']+)["']/i,
        /<script[^>]+src=["']([^"']*bs_h5\.min\.js[^"']*)["']/i,
    ];
    for (const pattern of patterns) {
        const match = html.match(pattern);
        if (match) return new URL(match[1].replace(/&amp;/g, '&'), pageUrl).href;
    }
    throw new Error('入口页中未找到 bs_h5.min.js');
}

function extractRuntimeUrl(script, sdkUrl) {
    const names = [...script.matchAll(/["'](stub(?:\.[A-Za-z0-9_-]+)?\.wasm)["']/g)].map((m) => m[1]);
    if (!names.length) throw new Error('SDK 脚本中未找到 stub*.wasm');
    return { upstreamName: names[0], url: new URL(names[0], sdkUrl).href };
}

function validateRuntime(bytes) {
    const module = new WebAssembly.Module(bytes);
    const exports = WebAssembly.Module.exports(module).map((entry) => entry.name);
    const required = ['memory', 'malloc', 'free', 'run_wac', 'en', 'free_memory'];
    const missing = required.filter((name) => !exports.includes(name));
    if (missing.length) throw new Error(`runtime.wasm 缺少导出: ${missing.join(', ')}`);
    return exports;
}

/** Python shield（wasmtime）能否加载；与 Node WebAssembly 校验互补。 */
function checkRuntimeWasmtime(bytes) {
    const temporary = path.join(BB_DIR, `.runtime-wt.${process.pid}.${Date.now()}.wasm`);
    fs.writeFileSync(temporary, bytes);
    const script = path.join(ROOT, 'scripts', 'validate_runtime_wasm.py');
    const venvPy = path.join(ROOT, '.venv', 'bin', 'python');
    const py =
        process.env.PYTHON ||
        process.env.PYTHON3 ||
        (fs.existsSync(venvPy) ? venvPy : 'python3');
    try {
        const result = spawnSync(py, [script, temporary], {
            encoding: 'utf8',
            cwd: ROOT,
            maxBuffer: 1024 * 1024,
        });
        if (result.status === 0) {
            return { ok: true };
        }
        const detail = (result.stderr || result.stdout || '').trim();
        return { ok: false, detail: detail || 'wasmtime validate failed' };
    } finally {
        fs.rmSync(temporary, { force: true });
    }
}

function wasmtimeRequiredForRuntimeApply() {
    const v = String(process.env.MT_BB_UPDATE_REQUIRE_WASMTIME || '').trim().toLowerCase();
    return v === '1' || v === 'true' || v === 'yes' || v === 'on';
}

function isRuntimeWasmtimeLegacyBlock(detail) {
    const s = String(detail || '').toLowerCase();
    return s.includes('legacy_exceptions') || s.includes('legacy exception');
}

async function validatePair(runtimeBytes, signBytes) {
    const temporary = path.join(BB_DIR, `.runtime.${process.pid}.${Date.now()}.wasm`);
    fs.writeFileSync(temporary, runtimeBytes);
    try {
        const bb = require('./bb_sdk');
        const runtime = await bb.getStubRuntime({ noCache: true, wasmPath: temporary, now: 1785376800123, quiet: true });
        const output = await runtime.runWac(
            'DID_FIXTURE_0123456789',
            'af0269eeee2fff6eda1899799aec8c75',
            signBytes,
        );
        const parsed = JSON.parse(output);
        if (!parsed.uuid || !parsed.body || parsed.has_env === undefined) throw new Error('run_wac 返回字段不完整');
        return { outputLength: output.length, bodyLength: parsed.body.length, hasEnv: parsed.has_env };
    } finally {
        fs.rmSync(temporary, { force: true });
    }
}

function archiveCurrent(version) {
    const base = `v${String(version.appVersion || version.sdkVersion).replace(/^v/, '')}`;
    let destination = path.join(REVERSE_DIR, base);
    if (fs.existsSync(destination)) {
        const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
        destination = path.join(REVERSE_DIR, `${base}-${stamp}`);
    }
    const pairs = [
        ['version.json', VERSION_PATH],
        ['bb_sdk.js', path.join(BB_DIR, 'bb_sdk.js')],
        ['bb_module.cjs', path.join(BB_DIR, 'bb_module.cjs')],
        ['randk.js', path.join(BB_DIR, 'randk.js')],
        ['live_bangcle_state.json', path.join(BB_DIR, 'live_bangcle_state.json')],
        [version.runtime.file, runtimeArtifactPath(version)],
        [version.signModule.file, signArtifactPath(version)],
    ];
    for (const [name, src] of pairs) {
        if (!fs.existsSync(src)) throw new Error(`归档源文件缺失: ${name} (${src})`);
    }
    fs.mkdirSync(destination, { recursive: false });
    for (const [name, src] of pairs) fs.copyFileSync(src, path.join(destination, name));
    return destination;
}

function atomicWrite(file, bytes) {
    const temporary = `${file}.next-${process.pid}`;
    fs.writeFileSync(temporary, bytes);
    fs.renameSync(temporary, file);
}

(async () => {
    const args = parseArgs(process.argv.slice(2));
    const current = readJson(VERSION_PATH);
    const pageResult = fetchBytes(args.page);
    const sdkUrl = extractSdkUrl(pageResult.bytes.toString('utf8'), args.page);
    const sdkResult = fetchBytes(sdkUrl);
    const runtimeRef = extractRuntimeUrl(sdkResult.bytes.toString('utf8'), sdkUrl);
    const runtimeResult = fetchBytes(runtimeRef.url);
    const runtimeExports = validateRuntime(runtimeResult.bytes);
    const runtimeWasmtime = checkRuntimeWasmtime(runtimeResult.bytes);

    const bb = require('./bb_sdk');
    const currentRuntimePath = runtimeArtifactPath(current);
    const currentSignPath = signArtifactPath(current);
    const sign = fetchSignWasm(bb, current, args.signVersion);
    if (args.mode === 'sync' && sign.networkFetch) {
        throw new Error(
            `sync 必须从 Bangcle/H5 拉取 sign wasm，当前走了本地回退: ${sign.networkFetch.reason || 'unknown'}`,
        );
    }
    const signBytes = sign.bytes;
    const pair = await validatePair(runtimeResult.bytes, signBytes);
    const sdkFromH5 = extractSdkVersion(sdkResult.bytes.toString('utf8'));
    const storeInfo = ['auto', 'sync', 'check', 'apply'].includes(args.mode)
        ? fetchAppStoreVersion()
        : null;
    const appResolved = resolveAppVersion(current, storeInfo, args);
    const resolvedAppVersion = appResolved.appVersion;
    const derivedVersionCode = appVersionToVersionCode(resolvedAppVersion);
    const versionCodeFromApp =
        args.versionCode ||
        (appResolved.appVersionSource.startsWith('itunes_lookup') ||
        appResolved.appVersionSource === 'cli:--app-version' ||
        appResolved.appVersionSource === 'env:MT_APP_VERSION'
            ? derivedVersionCode
            : null);
    const next = {
        ...current,
        appVersion: resolvedAppVersion,
        appVersionSource: appResolved.appVersionSource,
        versionCode: Number(versionCodeFromApp != null ? versionCodeFromApp : current.versionCode),
        sdkVersion: sdkFromH5 || current.sdkVersion,
        signWasmVersion: args.signVersion || sign.version || current.signWasmVersion,
        runtime: {
            file: 'runtime.wasm',
            upstreamName: runtimeRef.upstreamName,
            sourceUrl: runtimeRef.url,
            size: runtimeResult.bytes.length,
            sha256: sha256(runtimeResult.bytes),
        },
        signModule: {
            file: 'sign_wasm.bin',
            sourceUrl: sign.sourceUrl,
            versions: sign.versions,
            size: signBytes.length,
            sha256: sha256(signBytes),
        },
        checkedAt: new Date().toISOString(),
        update: {
            ...(current.update || {}),
            script: 'update.cjs',
            syncCommand: 'node nodejs/bb/update.cjs --sync',
            autoCommand: 'node nodejs/bb/update.cjs --auto',
            checkCommand: 'node nodejs/bb/update.cjs --check',
            applyCommand: 'node nodejs/bb/update.cjs --apply',
        },
    };
    const changes = {
        appVersion: current.appVersion !== next.appVersion,
        versionCode: Number(current.versionCode) !== next.versionCode,
        sdkVersion: current.sdkVersion !== next.sdkVersion,
        runtime: !fs.existsSync(currentRuntimePath) || sha256(fs.readFileSync(currentRuntimePath)) !== next.runtime.sha256,
        signModule: !fs.existsSync(currentSignPath) || sha256(fs.readFileSync(currentSignPath)) !== next.signModule.sha256,
        signWasmVersion: current.signWasmVersion !== next.signWasmVersion,
    };
    const storeLookupVersion = appResolved.storeLookupVersion || '';
    const storeUpgrade =
        !!storeLookupVersion && compareSemver(storeLookupVersion, current.appVersion) > 0;
    const bbResourceChanged =
        changes.runtime || changes.signModule || changes.signWasmVersion || changes.sdkVersion;

    const report = {
        mode: args.mode,
        store: storeInfo,
        appVersionResolve: appResolved,
        storeUpgrade,
        transport: 'local',
        pageUrl: args.page,
        sdkUrl,
        next,
        changes,
        validation: {
            runtimeExportCount: runtimeExports.length,
            runtimeWasmtime,
            pair,
            signNetworkFetch: sign.networkFetch || null,
        },
    };
    if (args.mode === 'check') {
        console.log(JSON.stringify(report, null, 2));
        return;
    }

    const shouldApply =
        args.mode === 'sync'
            ? true
            : args.mode === 'apply'
              ? Object.values(changes).some(Boolean)
              : args.mode === 'auto'
                ? bbResourceChanged || storeUpgrade || changes.appVersion
                : false;

    if (!shouldApply) {
        console.log(JSON.stringify({ ...report, applied: false, reason: 'no_change' }, null, 2));
        return;
    }
    let runtimeSkipped = null;
    if (!runtimeWasmtime.ok) {
        if (!isRuntimeWasmtimeLegacyBlock(runtimeWasmtime.detail)) {
            throw new Error(
                runtimeWasmtime.detail || 'runtime.wasm Python wasmtime 校验失败',
            );
        }
        if (wasmtimeRequiredForRuntimeApply() || !fs.existsSync(currentRuntimePath)) {
            throw new Error(
                runtimeWasmtime.detail ||
                    'CDN runtime.wasm 含 legacy try，wasmtime 无法加载；无本地可保留的 runtime.wasm',
            );
        }
        const keptBytes = fs.readFileSync(currentRuntimePath);
        runtimeSkipped = {
            reason: 'wasmtime_legacy_exceptions',
            detail: runtimeWasmtime.detail,
            keptSha256: sha256(keptBytes),
            upstream: {
                upstreamName: runtimeRef.upstreamName,
                sourceUrl: runtimeRef.url,
                size: runtimeResult.bytes.length,
                sha256: sha256(runtimeResult.bytes),
            },
        };
        next.runtime = {
            ...(current.runtime || {}),
            file: 'runtime.wasm',
            size: keptBytes.length,
            sha256: runtimeSkipped.keptSha256,
            pendingUpstream: runtimeSkipped.upstream,
        };
        changes.runtime = false;
        console.warn(
            `[bb-update] 已跳过写入 runtime.wasm（wasmtime 不兼容 legacy try）；` +
                `保留本地文件，仍更新 sign/sdk/appVersion。H5 上游: ${runtimeRef.upstreamName}`,
        );
    }

    let archivedTo = null;
    const bytesChanged = changes.runtime || changes.signModule;
    if (bytesChanged) {
        archivedTo = archiveCurrent(current);
    }
    fs.mkdirSync(RUNTIME_DIR, { recursive: true });
    if (changes.runtime) {
        atomicWrite(currentRuntimePath, runtimeResult.bytes);
    }
    atomicWrite(currentSignPath, signBytes);
    if (runtimeSkipped) {
        next.runtimeWasmtimeSkip = runtimeSkipped;
    }
    atomicWrite(VERSION_PATH, Buffer.from(`${JSON.stringify(next, null, 2)}\n`));
    try {
        const bbModule = require('./bb_module.cjs');
        if (typeof bbModule.bbInvalidateSignWasmCache === 'function') {
            bbModule.bbInvalidateSignWasmCache();
        }
    } catch (_) {
        // ignore
    }
    console.log(JSON.stringify({ ...report, applied: true, archivedTo }, null, 2));
})().catch((error) => {
    console.error(`[bb-update] ${error.stack || error.message || error}`);
    process.exit(1);
});
