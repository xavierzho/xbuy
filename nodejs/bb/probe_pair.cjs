#!/usr/bin/env node
'use strict';

const fs = require('fs');
const bb = require('./bb_sdk');

const wasmPath = process.argv[2];
const signPath = process.argv[3];
if (!wasmPath || !signPath) {
    console.error('用法: node nodejs/bb/probe_pair.cjs <runtime.wasm> <sign_wasm.bin>');
    process.exit(2);
}

(async () => {
    const wasmBytes = fs.readFileSync(wasmPath);
    const signBytes = fs.readFileSync(signPath);
    const wasmModule = new WebAssembly.Module(wasmBytes);
    const exports = WebAssembly.Module.exports(wasmModule).map((entry) => entry.name);
    const required = ['memory', 'malloc', 'free', 'run_wac', 'en', 'free_memory'];
    const missing = required.filter((name) => !exports.includes(name));
    if (missing.length) {
        throw new Error(`runtime.wasm 缺少导出: ${missing.join(', ')}`);
    }
    const runtime = await bb.getStubRuntime({
        noCache: true,
        wasmPath,
        now: 1785376800123,
        quiet: true,
    });
    const output = await runtime.runWac(
        'DID_FIXTURE_0123456789',
        'af0269eeee2fff6eda1899799aec8c75',
        signBytes,
    );
    const parsed = JSON.parse(output);
    if (!parsed.uuid || !parsed.body || parsed.has_env === undefined) {
        throw new Error('run_wac 返回字段不完整');
    }
    process.stdout.write(JSON.stringify({
        runtimeExportCount: exports.length,
        pair: {
            outputLength: output.length,
            bodyLength: parsed.body.length,
            hasEnv: parsed.has_env,
        },
    }));
})().catch((error) => {
    console.error(error && (error.stack || error.message) || error);
    process.exit(1);
});
