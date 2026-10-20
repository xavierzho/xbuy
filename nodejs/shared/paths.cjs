'use strict';

const fs = require('fs');
const path = require('path');

/** 仓库根目录（nodejs/shared → ../..） */
const REPO_ROOT = path.resolve(__dirname, '..', '..');

/** 统一 BB runtime / sign wasm / version.json（与 Python settings.paths 一致） */
const RUNTIME_DIR = process.env.RUSHBUY_RUNTIME_DIR
  ? path.resolve(process.env.RUSHBUY_RUNTIME_DIR)
  : path.join(REPO_ROOT, 'runtime');

function versionPath() {
  return path.join(RUNTIME_DIR, 'version.json');
}

function loadVersion() {
  const p = versionPath();
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

function runtimeWasmPath(version) {
  const v = version || loadVersion();
  const name = (v.runtime && v.runtime.file) || 'runtime.wasm';
  return path.join(RUNTIME_DIR, name);
}

function signWasmPath(version) {
  const v = version || loadVersion();
  const name = (v.signModule && v.signModule.file) || 'sign_wasm.bin';
  return path.join(RUNTIME_DIR, name);
}

module.exports = {
  REPO_ROOT,
  RUNTIME_DIR,
  versionPath,
  loadVersion,
  runtimeWasmPath,
  signWasmPath,
};
