'use strict';

const crypto = require('crypto');
const fs = require('fs');

const RANDC_1_9_6 = '01234567890abcdefghijklmnopqrstuvwxyz';
const ERROR_TOKEN_1_9_6 = 'error';
const PACKAGE_NAME_1_9_6 = 'com.moutai.mall';
const SIGNATURE_WHITELIST_1_9_6 = [
    'dd6c1787c17943e9db0c59c0da3d20e5',
    '2f5ae2348f427a666876d7da3563a7d2',
];
const IV_PREFIX_1_9_6 = '2018534749';
const IV_SUFFIX_1_9_6 = '963515';
const IV_1_9_6 = IV_PREFIX_1_9_6 + IV_SUFFIX_1_9_6;
const MIXM_KEY_PREFIX_1_9_6 = '7115c$ffbb5745#bb6';
const MIXM_KEY_SUFFIX_1_9_6 = '58415f3d89dae8';
const FALLBACK_CODE_1_9_6 = 'qbhajinldepmucsonaaaccgypwuvcjaa';
const FALLBACK_MD5_1_9_6 = '028e7f96f6369cafe1d105579c5b9377';

function normalizeText(value) {
    if (value === null || value === undefined) {
        return '';
    }
    return String(value);
}

function isAsciiAlnumByte_1_9_6(value) {
    return (
        (value >= 48 && value <= 57) ||
        (value >= 65 && value <= 90) ||
        (value >= 97 && value <= 122)
    );
}

function maybeParseJson_1_9_6(value) {
    if (typeof value !== 'string') {
        return value;
    }
    try {
        return JSON.parse(value);
    } catch (error) {
        return value;
    }
}

function compactJson_1_9_6(value) {
    if (typeof value === 'string') {
        return value;
    }
    return JSON.stringify(value);
}

function extractCodeFromBmpBuffer_1_9_6(buffer) {
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
            if (!isAsciiAlnumByte_1_9_6(component)) {
                throw new Error(`non-alnum component at x=${x}: ${component}`);
            }
            chars.push(String.fromCharCode(component));
        }
    }
    return chars.join('') + 'aa';
}

function extractCodeFromBmpPath_1_9_6(path) {
    return extractCodeFromBmpBuffer_1_9_6(fs.readFileSync(path));
}

function androidBase64Default_1_9_6(buffer) {
    return Buffer.from(buffer).toString('base64');
}

function encryptAUEc_1_9_6(plainText, key, iv = IV_1_9_6) {
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
    return androidBase64Default_1_9_6(encrypted);
}

function md5Hex_1_9_6(text) {
    return crypto
        .createHash('md5')
        .update(Buffer.from(normalizeText(text), 'utf8'))
        .digest('hex');
}

function sortChars_1_9_6(text) {
    return normalizeText(text).split('').sort().join('');
}

function pickRandCharFromValue_1_9_6(randValue) {
    const value = Number(randValue);
    if (!Number.isFinite(value)) {
        throw new Error(`invalid rand value: ${randValue}`);
    }
    const index =
        ((Math.trunc(value) % RANDC_1_9_6.length) + RANDC_1_9_6.length) %
        RANDC_1_9_6.length;
    return RANDC_1_9_6[index];
}

function randomRandChar_1_9_6() {
    return RANDC_1_9_6[Math.floor(Math.random() * RANDC_1_9_6.length)];
}

function buildMixKBase_1_9_6(arg1, arg2, arg3, versionName = '') {
    return `android${normalizeText(arg1)}${normalizeText(arg2)}${normalizeText(arg3)}${normalizeText(versionName)}`;
}

function finalizeMixK_1_9_6(base, randChar) {
    const digest = md5Hex_1_9_6(sortChars_1_9_6(base) + randChar);
    if (digest.length >= 27) {
        return digest.slice(0, 26) + randChar;
    }
    return digest;
}

function mixK_1_9_6(arg1, arg2, arg3, options = {}) {
    const randChar = options.randChar || randomRandChar_1_9_6();
    return finalizeMixK_1_9_6(
        buildMixKBase_1_9_6(arg1, arg2, arg3, options.versionName || ''),
        randChar
    );
}

function mixKWithGuards_1_9_6(arg1, arg2, arg3, options = {}) {
    const randChar = options.randChar || randomRandChar_1_9_6();
    let base = buildMixKBase_1_9_6(arg1, arg2, arg3, options.versionName || '');
    const firstArg = normalizeText(arg1);

    if (!options.packageMatches) {
        base += ERROR_TOKEN_1_9_6 + randChar + firstArg;
    }
    if (!options.signatureAllowed) {
        base += ERROR_TOKEN_1_9_6 + randChar + firstArg;
    }

    return finalizeMixK_1_9_6(base, randChar);
}

function mixM_1_9_6(input, options = {}) {
    if (options.packageMatches === false || options.signatureAllowed === false) {
        return mixMFallback_1_9_6(options);
    }
    return encryptAUEc_1_9_6(
        normalizeText(input),
        MIXM_KEY_PREFIX_1_9_6 + MIXM_KEY_SUFFIX_1_9_6,
        options.iv || IV_1_9_6
    );
}

function mixMFallback_1_9_6(options = {}) {
    const randChar = options.randChar || randomRandChar_1_9_6();
    return md5Hex_1_9_6(normalizeText(options.versionName) + ERROR_TOKEN_1_9_6 + randChar);
}

function mixMP_1_9_6(code, requestJson, options = {}) {
    if (options.packageMatches === false || options.signatureAllowed === false) {
        return mixMPFallback_1_9_6(options);
    }
    return encryptAUEc_1_9_6(
        compactJson_1_9_6(requestJson),
        normalizeText(code),
        options.iv || IV_1_9_6
    );
}

function mixMPFallback_1_9_6(options = {}) {
    const randChar = options.randChar || randomRandChar_1_9_6();
    return md5Hex_1_9_6(normalizeText(options.versionName) + ERROR_TOKEN_1_9_6 + randChar);
}

module.exports = {
    ERROR_TOKEN_1_9_6,
    FALLBACK_CODE_1_9_6,
    FALLBACK_MD5_1_9_6,
    IV_1_9_6,
    IV_PREFIX_1_9_6,
    IV_SUFFIX_1_9_6,
    MIXM_KEY_PREFIX_1_9_6,
    MIXM_KEY_SUFFIX_1_9_6,
    PACKAGE_NAME_1_9_6,
    RANDC_1_9_6,
    SIGNATURE_WHITELIST_1_9_6,
    androidBase64Default_1_9_6,
    buildMixKBase_1_9_6,
    compactJson_1_9_6,
    encryptAUEc_1_9_6,
    extractCodeFromBmpBuffer_1_9_6,
    extractCodeFromBmpPath_1_9_6,
    isAsciiAlnumByte_1_9_6,
    md5Hex_1_9_6,
    maybeParseJson_1_9_6,
    mixK_1_9_6,
    mixKWithGuards_1_9_6,
    mixM_1_9_6,
    mixMFallback_1_9_6,
    mixMP_1_9_6,
    mixMPFallback_1_9_6,
    pickRandCharFromValue_1_9_6,
    randomRandChar_1_9_6,
    sortChars_1_9_6,
};

if (require.main === module) {
    const [mode, ...args] = process.argv.slice(2);
    if (!mode) {
        console.error('usage: node randk_1_9_6.js <mixk|mixm|mixmp> ...');
        process.exit(1);
    }

    if (mode === 'mixk') {
        const [arg1, arg2, arg3, versionName = '', randChar = '0'] = args;
        process.stdout.write(
            mixK_1_9_6(arg1, arg2, arg3, { versionName, randChar })
        );
        process.stdout.write('\n');
        process.exit(0);
    }

    if (mode === 'mixm') {
        const [input] = args;
        process.stdout.write(mixM_1_9_6(input));
        process.exit(0);
    }

    if (mode === 'mixmp') {
        const [code, json] = args;
        if (!code || !json) {
            console.error('usage: node randk_1_9_6.js mixmp <code> <json-or-string>');
            process.exit(1);
        }
        let value = json;
        try {
            value = JSON.parse(json);
        } catch (error) { }
        process.stdout.write(mixMP_1_9_6(code, value));
        process.exit(0);
    }

    if (mode === 'bmp') {
        const [path] = args;
        if (!path) {
            console.error('usage: node randk_1_9_6.js bmp <path-to-bmp>');
            process.exit(1);
        }
        process.stdout.write(extractCodeFromBmpPath_1_9_6(path));
        process.stdout.write('\n');
        process.exit(0);
    }

    console.error(`unknown mode: ${mode}`);
    process.exit(1);
}
