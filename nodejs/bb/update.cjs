#!/usr/bin/env node
'use strict';

// 更新已改为 Python（bangcle/bb_update.py）。这里只把旧命令转到 Python。
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const root = path.resolve(__dirname, '..', '..');

function pythonBin() {
    const fromEnv = String(process.env.MT_PYTHON || process.env.PYTHON || process.env.PYTHON3 || '').trim();
    if (fromEnv) return fromEnv.split(/\s+/)[0];
    const venvPy = path.join(root, '.venv', 'bin', 'python');
    if (fs.existsSync(venvPy)) return venvPy;
    return 'python3';
}

const script = path.join(root, 'scripts', 'bb_fetch_latest.py');
const result = spawnSync(pythonBin(), [script, ...process.argv.slice(2)], {
    cwd: root,
    stdio: 'inherit',
});
if (result.error) {
    console.error(result.error.message || result.error);
    process.exit(1);
}
process.exit(result.status === null ? 1 : result.status);
