/**
 * 离线 parity：stdin JSON fixture → stdout 一行 artifacts JSON。
 */
'use strict';

const fs = require('fs');
const path = require('path');

for (const name of ['log', 'info', 'debug']) {
    console[name] = (...args) => {
        process.stderr.write(`${args.join(' ')}\n`);
    };
}

const bs = require(path.join(__dirname, 'bs-dvid.js'));

function readStdin() {
    return new Promise((resolve) => {
        const chunks = [];
        process.stdin.on('data', (c) => chunks.push(c));
        process.stdin.on('end', () => {
            const raw = Buffer.concat(chunks).toString('utf8').trim();
            resolve(raw ? JSON.parse(raw) : {});
        });
    });
}

(async () => {
    try {
        const fixture = await readStdin();
        const out = bs.buildParityArtifacts(fixture);
        process.stdout.write(JSON.stringify(out));
        process.exit(0);
    } catch (e) {
        process.stdout.write(JSON.stringify({ success: false, error: String(e && e.message || e) }));
        process.exit(1);
    }
})();
