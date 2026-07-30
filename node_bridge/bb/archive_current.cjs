'use strict';

const fs = require('fs');
const path = require('path');

const BB_DIR = __dirname;
const REVERSE_DIR = path.resolve(BB_DIR, '..', 'reverse');
const VERSION_FILE = path.join(BB_DIR, 'version.json');
const version = JSON.parse(fs.readFileSync(VERSION_FILE, 'utf8'));
const archiveName = String(version.appVersion || version.sdkVersion || '').trim();

if (!archiveName || !/^[A-Za-z0-9._-]+$/.test(archiveName)) {
    throw new Error('version.json 缺少有效的 appVersion/sdkVersion');
}

const destination = path.join(REVERSE_DIR, `v${archiveName.replace(/^v/, '')}`);
if (fs.existsSync(destination)) {
    throw new Error(`归档已存在: ${destination}`);
}

const files = [
    'version.json',
    'bb_sdk.js',
    'bb_module.cjs',
    'randk.js',
    'live_bangcle_state.json',
    version.runtime.file,
    version.signModule.file,
];

for (const name of files) {
    const source = path.join(BB_DIR, name);
    if (!fs.existsSync(source)) {
        throw new Error(`实时文件缺失: ${source}`);
    }
}

fs.mkdirSync(destination, { recursive: false });
for (const name of files) {
    const source = path.join(BB_DIR, name);
    fs.copyFileSync(source, path.join(destination, name));
}

console.log(JSON.stringify({
    archivedVersion: archiveName,
    destination,
    files,
}, null, 2));
