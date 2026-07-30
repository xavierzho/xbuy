#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawnSync } = require('child_process');
const { pathToFileURL } = require('url');

const BB_DIR = __dirname;
const ROOT = path.resolve(BB_DIR, '..', '..');
const REVERSE_DIR = path.resolve(BB_DIR, '..', 'reverse');
const VERSION_PATH = path.join(BB_DIR, 'version.json');
const DEFAULT_PAGE = 'https://h5.moutai519.com.cn/mt/item/smsp-detail?appConfig=2_1_2';
const PACKAGE_NAME = 'com.moutai.mall';

function usage(code = 0) {
    console.log(`用法:
  node node_bridge/bb/update.cjs --check
  node node_bridge/bb/update.cjs --apply

选项:
  --page URL          H5 入口页（默认当前商品详情页）
  --transport MODE    auto、local 或 adb（默认 auto）
  --sign-file PATH    同时验证并更新 sign_wasm.bin
  --sign-version VER  配合 --sign-file 写入版本号
  --app-version VER   覆盖设备读取的 App versionName
  --version-code NUM  覆盖设备读取的 versionCode

脚本不会被服务自动调用；只有 --apply 才会归档并替换实时文件。`);
    process.exit(code);
}

function parseArgs(argv) {
    const out = { mode: '', page: DEFAULT_PAGE, transport: 'auto' };
    for (let i = 0; i < argv.length; i += 1) {
        const arg = argv[i];
        if (arg === '--check' || arg === '--apply') out.mode = arg.slice(2);
        else if (arg === '--help' || arg === '-h') usage(0);
        else if (arg.startsWith('--')) {
            const key = arg.slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
            if (i + 1 >= argv.length) usage(2);
            out[key] = argv[++i];
        } else usage(2);
    }
    if (!out.mode || !['auto', 'local', 'adb'].includes(out.transport)) usage(2);
    return out;
}

const sha256 = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');
const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

function localFetch(url) {
    const script = `const u=${JSON.stringify(url)};fetch(u,{redirect:'follow',headers:{'user-agent':'Mozilla/5.0'}}).then(async r=>{if(!r.ok)throw Error('HTTP '+r.status);process.stdout.write(Buffer.from(await r.arrayBuffer()))}).catch(e=>{console.error(e.message);process.exit(1)})`;
    const result = spawnSync(process.execPath, ['-e', script], { encoding: null, maxBuffer: 16 * 1024 * 1024 });
    if (result.status !== 0 || !result.stdout?.length) throw new Error(String(result.stderr || '本机下载失败'));
    return result.stdout;
}

function adbFetch(url) {
    const result = spawnSync('adb', ['exec-out', 'curl', '-fsSL', '--compressed', '--max-time', '30', '-A', 'Mozilla/5.0', url], {
        encoding: null,
        maxBuffer: 16 * 1024 * 1024,
    });
    if (result.status !== 0 || !result.stdout?.length) throw new Error(String(result.stderr || 'ADB 下载失败'));
    return result.stdout;
}

function fetchBytes(url, transport) {
    if (transport === 'local') return { bytes: localFetch(url), transport: 'local' };
    if (transport === 'adb') return { bytes: adbFetch(url), transport: 'adb' };
    try {
        return { bytes: localFetch(url), transport: 'local' };
    } catch (_) {
        return { bytes: adbFetch(url), transport: 'adb' };
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

function readDeviceVersion() {
    const result = spawnSync('adb', ['shell', 'dumpsys', 'package', PACKAGE_NAME], { encoding: 'utf8' });
    if (result.status !== 0) return {};
    const versionName = result.stdout.match(/versionName=([^\s]+)/)?.[1] || '';
    const versionCode = Number(result.stdout.match(/versionCode=(\d+)/)?.[1] || 0);
    return { appVersion: versionName, versionCode };
}

function validateRuntime(bytes) {
    const module = new WebAssembly.Module(bytes);
    const exports = WebAssembly.Module.exports(module).map((entry) => entry.name);
    const required = ['memory', 'malloc', 'free', 'run_wac', 'en', 'free_memory'];
    const missing = required.filter((name) => !exports.includes(name));
    if (missing.length) throw new Error(`runtime.wasm 缺少导出: ${missing.join(', ')}`);
    return exports;
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
            signBytes
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
    const files = ['version.json', 'bb_sdk.js', 'bb_module.cjs', 'randk.js', 'live_bangcle_state.json', version.runtime.file, version.signModule.file];
    for (const name of files) {
        if (!fs.existsSync(path.join(BB_DIR, name))) throw new Error(`归档源文件缺失: ${name}`);
    }
    fs.mkdirSync(destination, { recursive: false });
    for (const name of files) fs.copyFileSync(path.join(BB_DIR, name), path.join(destination, name));
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
    const device = readDeviceVersion();
    const pageResult = fetchBytes(args.page, args.transport);
    const sdkUrl = extractSdkUrl(pageResult.bytes.toString('utf8'), args.page);
    const sdkResult = fetchBytes(sdkUrl, args.transport === 'auto' ? pageResult.transport : args.transport);
    const runtimeRef = extractRuntimeUrl(sdkResult.bytes.toString('utf8'), sdkUrl);
    const runtimeResult = fetchBytes(runtimeRef.url, args.transport === 'auto' ? sdkResult.transport : args.transport);
    const runtimeExports = validateRuntime(runtimeResult.bytes);

    const currentRuntimePath = path.join(BB_DIR, current.runtime.file);
    const currentSignPath = path.join(BB_DIR, current.signModule.file);
    const signBytes = args.signFile ? fs.readFileSync(path.resolve(ROOT, args.signFile)) : fs.readFileSync(currentSignPath);
    const pair = await validatePair(runtimeResult.bytes, signBytes);
    const next = {
        ...current,
        appVersion: args.appVersion || device.appVersion || current.appVersion,
        versionCode: Number(args.versionCode || device.versionCode || current.versionCode),
        signWasmVersion: args.signVersion || current.signWasmVersion,
        runtime: {
            file: 'runtime.wasm',
            upstreamName: runtimeRef.upstreamName,
            sourceUrl: runtimeRef.url,
            size: runtimeResult.bytes.length,
            sha256: sha256(runtimeResult.bytes),
        },
        signModule: {
            file: 'sign_wasm.bin',
            size: signBytes.length,
            sha256: sha256(signBytes),
        },
        checkedAt: new Date().toISOString(),
    };
    const changes = {
        appVersion: current.appVersion !== next.appVersion,
        versionCode: Number(current.versionCode) !== next.versionCode,
        runtime: !fs.existsSync(currentRuntimePath) || sha256(fs.readFileSync(currentRuntimePath)) !== next.runtime.sha256,
        signModule: !fs.existsSync(currentSignPath) || sha256(fs.readFileSync(currentSignPath)) !== next.signModule.sha256,
        signWasmVersion: current.signWasmVersion !== next.signWasmVersion,
    };

    const report = { mode: args.mode, transport: runtimeResult.transport, pageUrl: args.page, sdkUrl, next, changes, validation: { runtimeExportCount: runtimeExports.length, pair } };
    if (args.mode === 'check') {
        console.log(JSON.stringify(report, null, 2));
        return;
    }

    if (!Object.values(changes).some(Boolean)) {
        console.log(JSON.stringify({ ...report, applied: false, reason: 'no_change' }, null, 2));
        return;
    }
    const archivedTo = archiveCurrent(current);
    atomicWrite(currentRuntimePath, runtimeResult.bytes);
    if (args.signFile) atomicWrite(currentSignPath, signBytes);
    atomicWrite(VERSION_PATH, Buffer.from(`${JSON.stringify(next, null, 2)}\n`));
    console.log(JSON.stringify({ ...report, applied: true, archivedTo }, null, 2));
})().catch((error) => {
    console.error(`[bb-update] ${error.stack || error.message || error}`);
    process.exit(1);
});
