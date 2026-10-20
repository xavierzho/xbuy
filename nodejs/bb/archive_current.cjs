'use strict';

const fs = require('fs');
const path = require('path');
const shared = require('../shared/paths.cjs');

const BB_DIR = __dirname;
const REVERSE_DIR = path.join(BB_DIR, 'reverse');
const VERSION_FILE = shared.versionPath();
const version = JSON.parse(fs.readFileSync(VERSION_FILE, 'utf8'));
const archiveName = String(version.appVersion || version.sdkVersion || '').trim();

if (!archiveName || !/^[A-Za-z0-9._-]+$/.test(archiveName)) {
    throw new Error('version.json 缺少有效的 appVersion/sdkVersion');
}

const destination = path.join(REVERSE_DIR, `v${archiveName.replace(/^v/, '')}`);
if (fs.existsSync(destination)) {
    throw new Error(`归档已存在: ${destination}`);
}

const pairs = [
    ['version.json', VERSION_FILE],
    ['bb_sdk.js', path.join(BB_DIR, 'bb_sdk.js')],
    ['bb_module.cjs', path.join(BB_DIR, 'bb_module.cjs')],
    ['randk.js', path.join(BB_DIR, 'randk.js')],
    ['live_bangcle_state.json', path.join(BB_DIR, 'live_bangcle_state.json')],
    [version.runtime.file, shared.runtimeWasmPath(version)],
    [version.signModule.file, shared.signWasmPath(version)],
];

for (const [name, source] of pairs) {
    if (!fs.existsSync(source)) {
        throw new Error(`实时文件缺失: ${name} (${source})`);
    }
}

fs.mkdirSync(destination, { recursive: false });
for (const [name, source] of pairs) {
    fs.copyFileSync(source, path.join(destination, name));
}

console.log(JSON.stringify({
    archivedVersion: archiveName,
    destination,
    files: pairs.map(([name]) => name),
}, null, 2));
