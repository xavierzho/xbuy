'use strict';

const crypto = require('crypto');
const fs = require('fs');

const RANDC = '01234567890abcdefghijklmnopqrstuvwxyz';
const ERROR_TOKEN = 'error';
const PACKAGE_NAME = 'com.moutai.mall';
const SIGNATURE_WHITELIST = [
    'dd6c1787c17943e9db0c59c0da3d20e5',
    '2f5ae2348f427a666876d7da3563a7d2',
];
const IV_PREFIX = '2018534749';
const IV_SUFFIX = '963515';
const IV = IV_PREFIX + IV_SUFFIX;
const MIXM_KEY_PREFIX = '7115c$ffbb5745#bb6';
const MIXM_KEY_SUFFIX = '58415f3d89dae8';
const FALLBACK_CODE = 'qbhajinldepmucsonaaaccgypwuvcjaa';
const FALLBACK_MD5 = '028e7f96f6369cafe1d105579c5b9377';

function normalizeText(value) {
    if (value === null || value === undefined) {
        return '';
    }
    return String(value);
}

function isAsciiAlnumByte(value) {
    return (
        (value >= 48 && value <= 57) ||
        (value >= 65 && value <= 90) ||
        (value >= 97 && value <= 122)
    );
}

function maybeParseJson(value) {
    if (typeof value !== 'string') {
        return value;
    }
    try {
        return JSON.parse(value);
    } catch (error) {
        return value;
    }
}

function compactJson(value) {
    if (typeof value === 'string') {
        return value;
    }
    return JSON.stringify(value);
}

function extractCodeFromBmpBuffer(buffer) {
    if (!Buffer.isBuffer(buffer)) {
        throw new TypeError('expected Buffer');
    }
    if (buffer.length < 54 || buffer.slice(0, 2).toString('ascii') !== 'BM') {
        throw new Error('not a BMP file');
    }

    const pixelOffset = buffer.readUInt32LE(10);
    const width = buffer.readInt32LE(18);
    const height = buffer.readInt32LE(22);
    const bitsPerPixel = buffer.readUInt16LE(28);
    const compression = buffer.readUInt32LE(30);

    if (compression !== 0) {
        throw new Error(`unsupported BMP compression: ${compression}`);
    }
    if (bitsPerPixel !== 24 && bitsPerPixel !== 32) {
        throw new Error(`unsupported BMP bpp: ${bitsPerPixel}`);
    }
    if (width < 10 || height === 0) {
        throw new Error(`invalid BMP size: ${width}x${height}`);
    }

    const absHeight = Math.abs(height);
    const bytesPerPixel = bitsPerPixel / 8;
    const rowSize = Math.floor((bitsPerPixel * width + 31) / 32) * 4;
    const storedRowIndex = height > 0 ? 0 : absHeight - 1;
    const rowStart = pixelOffset + storedRowIndex * rowSize;
    const rowEnd = rowStart + rowSize;

    if (rowEnd > buffer.length) {
        throw new Error('BMP pixel data truncated');
    }

    const chars = [];
    for (let x = width - 10; x < width; x += 1) {
        const pixelOffsetInRow = x * bytesPerPixel;
        const blue = buffer[rowStart + pixelOffsetInRow];
        const green = buffer[rowStart + pixelOffsetInRow + 1];
        const red = buffer[rowStart + pixelOffsetInRow + 2];
        for (const component of [red, green, blue]) {
            if (!isAsciiAlnumByte(component)) {
                throw new Error(`non-alnum component at x=${x}: ${component}`);
            }
            chars.push(String.fromCharCode(component));
        }
    }
    return chars.join('') + 'aa';
}

function extractCodeFromBmpPath(path) {
    return extractCodeFromBmpBuffer(fs.readFileSync(path));
}

function androidBase64Default(buffer) {
    return Buffer.from(buffer).toString('base64');
}

function encryptAUEc(plainText, key, iv = IV) {
    const keyBuffer = Buffer.from(normalizeText(key), 'utf8');
    const algorithmByLength = {
        16: 'aes-128-cbc',
        24: 'aes-192-cbc',
        32: 'aes-256-cbc',
    };
    const algorithm = algorithmByLength[keyBuffer.length];
    if (!algorithm) {
        throw new Error(`unsupported AES key length: ${keyBuffer.length}`);
    }
    const cipher = crypto.createCipheriv(
        algorithm,
        keyBuffer,
        Buffer.from(normalizeText(iv), 'utf8')
    );
    const encrypted = Buffer.concat([
        cipher.update(Buffer.from(normalizeText(plainText), 'utf8')),
        cipher.final(),
    ]);
    return androidBase64Default(encrypted);
}

function md5Hex(text) {
    return crypto
        .createHash('md5')
        .update(Buffer.from(normalizeText(text), 'utf8'))
        .digest('hex');
}

function sortChars(text) {
    return normalizeText(text).split('').sort().join('');
}

function pickRandCharFromValue(randValue) {
    const value = Number(randValue);
    if (!Number.isFinite(value)) {
        throw new Error(`invalid rand value: ${randValue}`);
    }
    const index =
        ((Math.trunc(value) % RANDC.length) + RANDC.length) %
        RANDC.length;
    return RANDC[index];
}

function randomRandChar() {
    return RANDC[Math.floor(Math.random() * RANDC.length)];
}

function buildMixKBase(arg1, arg2, arg3, versionName = '') {
    return `android${normalizeText(arg1)}${normalizeText(arg2)}${normalizeText(arg3)}${normalizeText(versionName)}`;
}

function finalizeMixK(base, randChar) {
    const digest = md5Hex(sortChars(base) + randChar);
    if (digest.length >= 27) {
        return digest.slice(0, 26) + randChar;
    }
    return digest;
}

function mixK(arg1, arg2, arg3, options = {}) {
    const randChar = options.randChar || randomRandChar();
    return finalizeMixK(
        buildMixKBase(arg1, arg2, arg3, options.versionName || ''),
        randChar
    );
}

function mixKWithGuards(arg1, arg2, arg3, options = {}) {
    const randChar = options.randChar || randomRandChar();
    let base = buildMixKBase(arg1, arg2, arg3, options.versionName || '');
    const firstArg = normalizeText(arg1);

    if (!options.packageMatches) {
        base += ERROR_TOKEN + randChar + firstArg;
    }
    if (!options.signatureAllowed) {
        base += ERROR_TOKEN + randChar + firstArg;
    }

    return finalizeMixK(base, randChar);
}

function mixM(input, options = {}) {
    if (options.packageMatches === false || options.signatureAllowed === false) {
        return mixMFallback(options);
    }
    return encryptAUEc(
        normalizeText(input),
        MIXM_KEY_PREFIX + MIXM_KEY_SUFFIX,
        options.iv || IV
    );
}

function mixMFallback(options = {}) {
    const randChar = options.randChar || randomRandChar();
    return md5Hex(normalizeText(options.versionName) + ERROR_TOKEN + randChar);
}

function mixMP(code, requestJson, options = {}) {
    if (options.packageMatches === false || options.signatureAllowed === false) {
        return mixMPFallback(options);
    }
    return encryptAUEc(
        compactJson(requestJson),
        normalizeText(code),
        options.iv || IV
    );
}

function mixMPFallback(options = {}) {
    const randChar = options.randChar || randomRandChar();
    return md5Hex(normalizeText(options.versionName) + ERROR_TOKEN + randChar);
}

module.exports = {
    ERROR_TOKEN,
    FALLBACK_CODE,
    FALLBACK_MD5,
    IV,
    IV_PREFIX,
    IV_SUFFIX,
    MIXM_KEY_PREFIX,
    MIXM_KEY_SUFFIX,
    PACKAGE_NAME,
    RANDC,
    SIGNATURE_WHITELIST,
    androidBase64Default,
    buildMixKBase,
    compactJson,
    encryptAUEc,
    extractCodeFromBmpBuffer,
    extractCodeFromBmpPath,
    isAsciiAlnumByte,
    md5Hex,
    maybeParseJson,
    mixK,
    mixKWithGuards,
    mixM,
    mixMFallback,
    mixMP,
    mixMPFallback,
    pickRandCharFromValue,
    randomRandChar,
    sortChars,
};

if (require.main === module) {
    const [mode, ...args] = process.argv.slice(2);
    if (!mode) {
        console.error('usage: node randk.js <mixk|mixm|mixmp> ...');
        process.exit(1);
    }

    if (mode === 'mixk') {
        const [arg1, arg2, arg3, versionName = '', randChar = '0'] = args;
        process.stdout.write(
            mixK(arg1, arg2, arg3, { versionName, randChar })
        );
        process.stdout.write('\n');
        process.exit(0);
    }

    if (mode === 'mixm') {
        const [input] = args;
        process.stdout.write(mixM(input));
        process.exit(0);
    }

    if (mode === 'mixmp') {
        const [code, json] = args;
        if (!code || !json) {
            console.error('usage: node randk.js mixmp <code> <json-or-string>');
            process.exit(1);
        }
        let value = json;
        try {
            value = JSON.parse(json);
        } catch (error) { }
        process.stdout.write(mixMP(code, value));
        process.exit(0);
    }

    if (mode === 'bmp') {
        const [path] = args;
        if (!path) {
            console.error('usage: node randk.js bmp <path-to-bmp>');
            process.exit(1);
        }
        process.stdout.write(extractCodeFromBmpPath(path));
        process.stdout.write('\n');
        process.exit(0);
    }

    console.error(`unknown mode: ${mode}`);
    process.exit(1);
}
