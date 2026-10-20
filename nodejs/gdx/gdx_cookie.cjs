'use strict';

const fs = require('fs');
const yidun = require('./yidun_gdx_sdk');

function readInput() {
    const raw = fs.readFileSync(0, 'utf8').trim();
    if (!raw) return {};
    return JSON.parse(raw);
}

function parseScreen(value) {
    const s = String(value || '').trim();
    const m = s.match(/(\d+)\s*[*xX]\s*(\d+)/);
    if (!m) return { width: 1080, height: 2215 };
    return { width: Number(m[1]) || 1080, height: Number(m[2]) || 2215 };
}

function buildProfile(input) {
    const screen = parseScreen(input.screen || input.scr);
    const extra = input.gdxInnerProfile && typeof input.gdxInnerProfile === 'object'
        ? input.gdxInnerProfile
        : {};
    return Object.assign({
        userAgent: String(input.userAgent || ''),
        screenWidth: screen.width,
        screenHeight: screen.height,
        platform: 'Linux armv8l',
        canvasFingerprint: input.gdxCanvasFingerprint || input.canvasFingerprint || undefined,
        systemColors: '',
        pluginString: input.gdxPluginString || input.pluginString || undefined,
        openDatabaseType: input.gdxOpenDatabaseType || input.openDatabaseType || 'undefined',
        timezoneOffset: Number(input.gdxTimezoneOffset ?? input.timezoneOffset ?? -480),
        language: input.gdxLanguage || input.language || 'zh-CN',
        colorDepth: Number(input.gdxColorDepth ?? input.colorDepth ?? 24),
    }, extra);
}

function main() {
    const input = readInput();
    if (!yidun || typeof yidun.buildGdxidpyhxdE !== 'function') {
        throw new Error('yidun_gdx_sdk 缺少 buildGdxidpyhxdE');
    }
    const fp = String(input.fp || input.gdxFp || '').trim();
    const icp = String(input.icp || input.gdxIcp || '').trim();
    const built = yidun.buildGdxidpyhxdE({
        host: input.host || 'h5.moutai519.com.cn',
        fp: fp || undefined,
        icp: icp || undefined,
        gdxInnerProfile: buildProfile(input),
        gdxInnerOptions: { b: false, a: false },
    });
    process.stdout.write(JSON.stringify({
        success: true,
        gdxidpyhxdE: built.encodedValue,
        value: built.value,
        timestamp: built.timestamp,
        expiresAt: built.expiresAt,
        fp: built.payload && built.payload.fp || '',
        icp: built.payload && built.payload.icp || '',
    }));
}

try {
    main();
} catch (err) {
    process.stdout.write(JSON.stringify({
        success: false,
        message: err && err.message || String(err),
    }));
    process.exitCode = 1;
}
