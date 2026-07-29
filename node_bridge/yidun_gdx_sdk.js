/**
 * 易盾 NECaptcha core 2.28.5 + bingtop 自动打码 — Node CJS 端口。
 *
 * 与 electron/services/yidun/{crypto,jsonp,track,solver}.ts、bingtopClient.ts 等价；
 * 仅依赖 Node stdlib (https/url/buffer)，无第三方依赖。
 *
 * 调用方（electron/services/yidunCaptcha.ts、maotai-seckill.js）会传入 challenge.captchaId；
 * 该值按茅台 H5 详情页硬编码在各页 bundle 中，映射如下（仅作资料，本文件不设默认值）：
 *   smsp-detail              -> 1e1ea52963a94c21b0babc42b2b5b910  // 生晒面 SMSP，code=741 等
 *   jpmt-detail              -> 22912abb4c484691b71f6efaf778c252  // 精品茅台 JPMT，code=1741
 *   mm-485-detail-group      -> c2657d2cc1f248a78c5a06980923dba5  // 丙午马年 mm-485，code=11942/11945/11947
 *   hxx-detail               -> c2657d2cc1f248a78c5a06980923dba5  // 黄小西 HXX，code=11652/11654
 *   gyft-8392-detail         -> 5f1aab1567f54fdd8c137afed42af465  // 贵州飞天 8392 1L 系列
 * 另外 c2657d2cc1f248a78c5a06980923dba5 在 smsp/jpmt bundle 中亦作为测试环境分支出现，不要用于线上两个页面。
 *
 * 用法：
 *   const { solveYidunCaptchaWithBingtop } = require('./reverse/yidun_sdk');
 *   const r = await solveYidunCaptchaWithBingtop({
 *       captchaId, extraData,
 *       // 二选一：bingtop 或 ddddocr（ddddocr 优先级更高）
 *       bingtop: { enabled, endpoint, username, password, captchaType, timeoutMs },
 *       ddddocr: { enabled, endpoint, simpleTarget, timeoutMs },
 *       // 可选：用 JFBYM type=66 根据识别出的距离生成滑动轨迹
 *       jfbymTrack: { enabled, token, beginPoint: '8,0' },
 *       maxAttempts: 5, logger: console.log,
 *   });
 *   if (r.success) use r.validate;
 *
 * ddddocr 用法说明：
 *   先在本机启动官方 HTTP 服务（https://github.com/sml2h3/ocr_api_server），
 *   默认监听 http://127.0.0.1:9898，提供 POST /slide_match 接口。
 */

'use strict';

const http = require('http');
const https = require('https');
const zlib = require('zlib');
const crypto = require('crypto');
const { URL } = require('url');
const { Buffer } = require('buffer');

// ============= 进程内稳定的设备指纹（用于 UA 中 device-id / BS-DVID 占位）=============
let _STABLE_DEVICE_ID = null;
let _STABLE_BS_DVID = null;
function getStableDeviceId() {
    if (!_STABLE_DEVICE_ID) {
        _STABLE_DEVICE_ID = process.env.YIDUN_DEVICE_ID || crypto.randomBytes(16).toString('hex');
    }
    return _STABLE_DEVICE_ID;
}
function getStableBsDvid() {
    if (!_STABLE_BS_DVID) {
        if (process.env.YIDUN_BS_DVID) {
            _STABLE_BS_DVID = process.env.YIDUN_BS_DVID;
        } else {
            // 真实样本长度 ~107，base64url 风格（无 +/=，含 -_）
            const buf = crypto.randomBytes(80);
            _STABLE_BS_DVID = buf.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
        }
    }
    return _STABLE_BS_DVID;
}

// ============= crypto =============

const NORMAL_BASE64 = 'i/x1XgU0z7k8N+lCpOnPrv6\\qu2Gj9HRcwTYZ4bfSJBhaWstAeoMIEQ5mDdVFLKy';
const NORMAL_PADDING = '3';
const PRIVATE_BASE64 = 'MB.CfHUzEeJpsuGkgNwhqiSaI4Fd9L6jYKZAxn1/Vml0c5rbXRP+8tD3QTO2vWyo';
const PRIVATE_PADDING = '7';
const SBOX = 'a7be3f3933fa8c5fcf86c4b6908b569ba1e26c1a6d7cfbf60ae4b00e074a194dac4b73e7f898541159a39d08183b76eedee3ed341e6685d2357440158394b1ff03a9004cbbb5ca7dcb7f41489a16e03dcc9c71eb3c9796685b1d01b4d56193a6e1f1a2470445c191ae49c5d82765dc82c350f263387a24a502fcbf442e2dddaad0e936d9ea22b89275307b42518fbc3a626ba806d4ecd6d725f50cc8c72fefa4551ccd6fc9b2b7ab954f815c7264c6e51f4eaf99885a79892b1b60a0b3526e57ba5d178d370958847eb9fd28f9ce0bc023f4148a2adfe632126769057043d3bd8eda0df7872629f3809ef05310e83113216afe202c460fc23e789f77d1addb5e';
const ROUND_KEY = '037606da0296055c';
const SEED_KEY = 'fd6a43ae25f74398b61c03c83be37449';

function toByte(v) { if (v < -128) return toByte(256 + v); if (v > 127) return toByte(v - 256); return v; }
function toUnsigned(v) { return toByte(v) & 0xff; }
function stringToBytes(s) { return Array.from(Buffer.from(String(s), 'utf8'), (b) => toByte(b)); }
function bytesToString(bs) { return Buffer.from(bs.map(toUnsigned)).toString('utf8'); }
function intToBytes(v) { return [toByte((v >>> 24) & 0xff), toByte((v >>> 16) & 0xff), toByte((v >>> 8) & 0xff), toByte(v & 0xff)]; }
function byteToHex(v) { const t = '0123456789abcdef'; return t[(v >>> 4) & 0xf] + t[v & 0xf]; }
function bytesToHex(bs) { return bs.map(byteToHex).join(''); }
function hexToByte(h) { return toByte(parseInt(h.slice(0, 2), 16)); }
function hexsToBytes(h) { const o = []; for (let i = 0; i < h.length; i += 2) o.push(hexToByte(h.slice(i, i + 2))); return o; }
function xorByte(a, b) { return toByte(toByte(a) ^ toByte(b)); }
function xors(d, k) { const o = []; for (let i = 0; i < d.length; i++) o[i] = xorByte(d[i], k[i % k.length]); return o; }
function shiftByte(a, b) { return toByte(a + b); }
function shifts(d, k) { const o = []; for (let i = 0; i < d.length; i++) o[i] = shiftByte(d[i], k[i % k.length]); return o; }
function copyToBytes(s, so, d, doff, l) { for (let i = 0; i < l; i++) if (so + i < s.length) d[doff + i] = s[so + i]; return d; }
function paddingArrayZero(n) { return Array.from({ length: n }, () => 0); }

const CRC32_TABLE = (() => { const t = []; for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
function crc32Hex(bs) { let c = 0xffffffff; for (const b of bs) c = (c >>> 8) ^ CRC32_TABLE[(c ^ toUnsigned(b)) & 0xff]; return bytesToHex(intToBytes((0xffffffff ^ c) >>> 0)); }

function _b64chunk(bytes, alpha, pad) {
    const b = bytes.map(toUnsigned);
    if (b.length === 1) return [alpha[(b[0] >>> 2) & 0x3f], alpha[(b[0] << 4) & 0x30], pad, pad].join('');
    if (b.length === 2) return [alpha[(b[0] >>> 2) & 0x3f], alpha[((b[0] << 4) & 0x30) + ((b[1] >>> 4) & 0xf)], alpha[(b[1] << 2) & 0x3c], pad].join('');
    if (b.length === 3) return [alpha[(b[0] >>> 2) & 0x3f], alpha[((b[0] << 4) & 0x30) + ((b[1] >>> 4) & 0xf)], alpha[((b[1] << 2) & 0x3c) + ((b[2] >>> 6) & 0x3)], alpha[b[2] & 0x3f]].join('');
    return '';
}
function _b64enc(bytes, alpha, pad) { if (!bytes || !bytes.length) return ''; const o = []; for (let i = 0; i < bytes.length; i += 3) o.push(_b64chunk(bytes.slice(i, i + 3), alpha, pad)); return o.join(''); }
function _b64dec(value, alpha, pad) {
    const clean = String(value).split(pad)[0].split('');
    const o = [];
    for (let i = 0; i < clean.length; i += 4) {
        const idx = clean.slice(i, i + 4).map((c) => alpha.indexOf(c));
        if (idx.length === 2) o.push(toByte(((idx[0] << 2) & 0xff) + ((idx[1] >>> 4) & 0x3)));
        else if (idx.length === 3) { o.push(toByte(((idx[0] << 2) & 0xff) + ((idx[1] >>> 4) & 0x3))); o.push(toByte(((idx[1] << 4) & 0xff) + ((idx[2] >>> 2) & 0xf))); }
        else if (idx.length === 4) { o.push(toByte(((idx[0] << 2) & 0xff) + ((idx[1] >>> 4) & 0x3))); o.push(toByte(((idx[1] << 4) & 0xff) + ((idx[2] >>> 2) & 0xf))); o.push(toByte(((idx[2] << 6) & 0xff) + (idx[3] & 0x3f))); }
    }
    return o;
}
const base64Encode = (b) => _b64enc(b, NORMAL_BASE64.split(''), NORMAL_PADDING);
const base64Decode = (v) => _b64dec(v, NORMAL_BASE64.split(''), NORMAL_PADDING);
const base64EncodePrivate = (b) => _b64enc(b, PRIVATE_BASE64.split(''), PRIVATE_PADDING);

function xorEncode(token, text) { return base64Encode(xors(stringToBytes(text), stringToBytes(token))); }
function xorDecode(token, encoded) { return bytesToString(xors(base64Decode(encoded), stringToBytes(token))); }

// 将 PRIVATE_BASE64 输出转成易盾 SDK 在最终 validate 中使用的 URL-safe 形态
// 对应 core_2.28.5.decoded.js 中的 _0x127d20 ：'\\'->'-', '/'->'_', '+'->'*'
function privateBase64ToUrlSafe(s) {
    return String(s).replace(/[\\/+]/g, (ch) => ({ '\\': '-', '/': '_', '+': '*' }[ch]));
}

// 还原 SDK 中的 _0xab267f：将 /v3/check 返回的 raw validate + fp 二次加密并拼上 zoneId / _v_i_1 后缀
// 输出形如 `${zoneId}_<urlSafePrivateB64(aes(raw+"::"+fp))>_v_i_1`
function wrapValidate(rawValidate, fp, zoneId, opts) {
    const enc = privateBase64ToUrlSafe(aesEncrypt(String(rawValidate) + '::' + String(fp || ''), opts));
    const body = zoneId ? `${zoneId}_${enc}` : enc;
    return `${body}_v_i_1`;
}

function repeatTo64(bs) { if (!bs.length) return paddingArrayZero(64); if (bs.length >= 64) return bs.slice(0, 64); const o = []; for (let i = 0; i < 64; i++) o[i] = bs[i % bs.length]; return o; }
function padWithZeroAndLength(bs) {
    if (!bs.length) return paddingArrayZero(64);
    const len = bs.length;
    const padLen = len % 64 <= 60 ? 64 - (len % 64) - 4 : 128 - (len % 64) - 4;
    const o = [];
    copyToBytes(bs, 0, o, 0, len);
    for (let i = 0; i < padLen; i++) o[len + i] = 0;
    copyToBytes(intToBytes(len), 0, o, len + padLen, 4);
    return o;
}
function split64(bs) { if (bs.length % 64) return []; const o = []; for (let i = 0; i < bs.length; i += 64) o.push(bs.slice(i, i + 64)); return o; }
function sboxFn(bs) { const t = hexsToBytes(SBOX); return bs.map((b) => t[16 * ((b >>> 4) & 0xf) + (b & 0xf)]); }
function roundTransform(block) {
    const ops = [
        (d) => d,
        (d, a) => d.map((b) => xorByte(b, toByte(a))),
        (d, a) => d.map((b) => shiftByte(b, toByte(a))),
        (d, a) => { let k = toByte(a); return d.map((b) => xorByte(b, k++)); },
        (d, a) => { let k = toByte(a); return d.map((b) => shiftByte(b, k++)); },
        (d, a) => { let k = toByte(a); return d.map((b) => xorByte(b, k--)); },
        (d, a) => { let k = toByte(a); return d.map((b) => shiftByte(b, k--)); },
    ];
    let o = block;
    for (let i = 0; i < ROUND_KEY.length; i += 4) {
        const opIdx = hexToByte(ROUND_KEY.slice(i, i + 2));
        const arg = hexToByte(ROUND_KEY.slice(i + 2, i + 4));
        o = ops[opIdx](o, arg);
    }
    return o;
}
function defaultRandomBytes4(rfn) { const r = rfn || Math.random; const o = []; for (let i = 0; i < 4; i++) o[i] = toByte(Math.floor(256 * r())); return o; }

function aesEncrypt(text, opts) {
    opts = opts || {};
    const input = stringToBytes(text);
    const random = opts.sessionRandom ? opts.sessionRandom.slice(0, 4).map(toByte) : defaultRandomBytes4(opts.randomFn);
    let key = repeatTo64(stringToBytes(SEED_KEY));
    key = xors(key, repeatTo64(random));
    key = repeatTo64(key);
    const crc = stringToBytes(crc32Hex(input));
    const blocks = split64(padWithZeroAndLength(input.concat(crc)));
    const out = random.slice();
    let prev = key;
    for (const block of blocks) {
        let cur = xors(roundTransform(block), key);
        cur = shifts(cur, prev);
        cur = xors(cur, prev);
        prev = sboxFn(sboxFn(cur));
        copyToBytes(prev, 0, out, out.length, 64);
    }
    return base64EncodePrivate(out);
}

function cb(opts) {
    opts = opts || {};
    const alpha = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';
    const chars = [];
    const r = opts.randomFn || Math.random;
    for (let i = 0; i < 32; i++) chars[i] = alpha[Math.floor(r() * alpha.length)];
    const code = 'vfnv46';
    const pos = [1, 10, 12, 13, 26, 31];
    for (let i = 0; i < pos.length; i++) chars[pos[i]] = code.charAt(i);
    return aesEncrypt(chars.join(''), opts);
}

// ============= gdxidpyhxdE / fp cookie =============

const GDX_COOKIE_NAME = 'gdxidpyhxdE';
const GDX_WINDOW_NAME = 'gdxidpyhxde';
const GDX_VERSION = 'v1.1';
const GDX_TTL_MS = 30 * 24 * 60 * 60 * 1000;
const GDX_REFRESH_DELAY_MS = 900000;
const GDX_RANDOM_ALPHABET = 'aZbY0cXdW1eVf2Ug3Th4SiR5jQk6PlO7mNn8MoL9pKqJrIsHtGuFvEwDxCyBzA';
const GDX_BASE64 = '240aYHiQxL\\ZufVlg8sPMR6dGkXvO/Cbw9WDj1ETyIScmeoJz37qthBrU+KNA5pn';
const GDX_PADDING = 'F';
const GDX_SEED_KEY = '14731255234d414cF91356d684E4E8F5F56c8f1bc';
const GDX_SBOX = [
    -9, -84, -50, 59, 115, 102, 57, 125, 94, -15, 15, 2, -72, -98, -79, 38,
    -56, -49, 76, -26, -117, 60, 90, 9, -107, -12, -71, -100, 63, 42, -18, 28,
    -120, -11, 33, 45, 79, 92, 37, 97, 4, 58, 98, 84, -97, -88, 95, -104,
    -13, -89, 78, -90, 119, -66, 13, -5, 29, -116, -4, -81, 27, 40, -59, -43,
    85, 48, -74, 109, -64, 26, 67, -33, -115, 0, -37, -102, 88, -48, 127, -86,
    41, 105, -2, 122, -42, 112, -94, 81, -31, -65, -101, -14, 65, 49, -67,
    -114, -103, -87, -19, 104, 66, -73, -34, -78, -45, -27, -109, -108, 47,
    61, 86, 43, -54, 25, 64, -35, -44, 53, -112, 36, 73, 89, -82, 51, -32, 39,
    -83, 80, -85, -111, 12, -58, 103, -76, -46, -127, 34, 1, -99, 14, -57,
    110, 106, 93, -52, 11, 113, 20, -106, 75, 62, -69, -39, -55, -119, 126,
    114, 123, 10, 77, -121, -8, 74, 21, -93, 17, -61, -21, -105, -126, 18,
    124, -17, 52, -10, -77, -24, -22, 120, -95, -25, 96, -110, 22, -23, 69,
    -125, -128, -47, -38, -1, 3, -20, 100, 68, 101, 5, 117, -122, 44, -51,
    -36, -41, 24, -80, 30, 82, -63, -40, -92, 91, -6, -53, -124, -62, -28,
    111, 19, 50, 108, 70, -68, -29, -75, 99, -91, -60, -70, 71, -118, -3, 83,
    87, -7, 32, 55, 31, -123, 121, 107, -113, 46, -30, 118, 54, 23, 116, -16,
    7, 6, 35, 16, -96, 56, 72, 8,
];
const GDX_INVERSE_SBOX = (() => {
    const inverse = new Map();
    GDX_SBOX.forEach((value, index) => inverse.set(toByte(value), toByte(index)));
    return inverse;
})();

function gdxRandomBytes(randomBytes, randomFn) {
    if (randomBytes) {
        const bytes = Array.from(randomBytes).slice(0, 4).map((byte) => toByte(Number(byte) || 0));
        while (bytes.length < 4) bytes.push(0);
        return bytes;
    }
    const r = randomFn || Math.random;
    return Array.from({ length: 4 }, () => toByte(Math.floor(256 * r())));
}
function gdxRandomChars(length, randomFn) {
    const r = randomFn || Math.random;
    let out = '';
    for (let i = 0; i < length; i++) out += GDX_RANDOM_ALPHABET.charAt(Math.floor(r() * GDX_RANDOM_ALPHABET.length));
    return out;
}
function gdxFingerprint(fp) {
    if (Array.isArray(fp)) return fp.join(',');
    if (fp == null) return '';
    return String(fp);
}
const GDX_HASH_SEED = 31;
const GDX_HASH_STAT_WIDTH = 2;
const GDX_SYSTEM_COLORS = Object.freeze([
    'ActiveBorder', 'ActiveCaption', 'AppWorkspace', 'Background', 'ButtonFace', 'ButtonHighlight',
    'ButtonShadow', 'ButtonText', 'CaptionText', 'GrayText', 'Highlight', 'HighlightText',
    'InactiveBorder', 'InactiveCaption', 'InactiveCaptionText', 'InfoBackground', 'InfoText',
    'Menu', 'MenuText', 'Scrollbar', 'ThreeDDarkShadow', 'ThreeDFace', 'ThreeDHighlight',
    'ThreeDLightShadow', 'ThreeDShadow', 'Window', 'WindowFrame', 'WindowText',
]);
const GDX_PLUGIN_PROBE_NAMES = Object.freeze([
    '4game', 'AdblockPlugin', 'AdobeExManCCDetect', 'AdobeExManDetect', 'Alawar NPAPI utils',
    'Aliedit Plug-In', 'Alipay Security Control 3', 'AliSSOLogin plugin', 'AmazonMP3DownloaderPlugin',
    'AOL Media Playback Plugin', 'AppUp', 'ArchiCAD', 'AVG SiteSafety plugin', 'Babylon ToolBar',
    'Battlelog Game Launcher', 'BitCometAgent', 'Bitdefender QuickScan', 'BlueStacks Install Detector',
    'CatalinaGroup Update', 'Citrix ICA Client', 'Citrix online plug-in', 'Citrix Receiver Plug-in',
    'Coowon Update', 'DealPlyLive Update', 'Default Browser Helper', 'DivX Browser Plug-In',
    'DivX Plus Web Player', 'DivX VOD Helper Plug-in', 'doubleTwist Web Plugin',
    'Downloaders plugin', 'downloadUpdater', 'eMusicPlugin DLM6', 'ESN Launch Mozilla Plugin',
    'ESN Sonar API', 'Exif Everywhere', 'Facebook Plugin', 'File Downloader Plug-in',
    'FileLab plugin', 'FlyOrDie Games Plugin', 'Folx 3 Browser Plugin', 'FUZEShare',
    'GDL Object Web Plug-in 16.00', 'GFACE Plugin', 'Ginger', 'Gnome Shell Integration',
    'Google Earth Plugin', 'Google Earth Plug-in', 'Google Gears 0.5.33.0',
    'Google Talk Effects Plugin', 'Google Update', 'Harmony Firefox Plugin', 'Harmony Plug-In',
    'Heroes & Generals live', 'HPDetect', 'Html5 location provider', 'IE Tab plugin',
    'iGetterScriptablePlugin', 'iMesh plugin', 'Kaspersky Password Manager', 'LastPass',
    'LogMeIn Plugin 1.0.0.935', 'LogMeIn Plugin 1.0.0.961', 'Ma-Config.com plugin',
    'Microsoft Office 2013', 'MinibarPlugin', 'Native Client', 'Nitro PDF Plug-In',
    'Nokia Suite Enabler Plugin', 'Norton Identity Safe', 'npAPI Plugin', 'NPLastPass',
    'NPPlayerShell', 'npTongbuAddin', 'NyxLauncher', 'Octoshape Streaming Services',
    'Online Storage plug-in', 'Orbit Downloader', 'Pando Web Plugin', 'Parom.TV player plugin',
    'PDF integrado do WebKit', 'PDF-XChange Viewer', 'PhotoCenterPlugin1.1.2.2',
    'Picasa', 'PlayOn Plug-in', 'QQ2013 Firefox Plugin', 'QQDownload Plugin', 'QQMiniDL Plugin',
    'QQMusic', 'RealDownloader Plugin', 'Roblox Launcher Plugin', 'RockMelt Update',
    'Safer Update', 'SafeSearch', 'Scripting.Dictionary', 'SefClient Plugin', 'Shell.UIHelper',
    'Silverlight Plug-In', 'Simple Pass', 'Skype Web Plugin', 'SumatraPDF Browser Plugin',
    'Symantec PKI Client', 'Tencent FTN plug-in', 'Thunder DapCtrl NPAPI Plugin',
    'TorchHelper', 'Unity Player', 'Uplay PC', 'VDownloader', 'Veetle TV Core',
    'VLC Multimedia Plugin', 'Web Components', 'WebKit-integrierte PDF', 'WEBZEN Browser Extension',
    'Wolfram Mathematica', 'WordCaptureX', 'WPI Detector 1.4', 'Yandex Media Plugin',
    'Yandex PDF Viewer', 'YouTube Plug-in', 'zako',
]);
const GDX_DEFAULT_INNER_PROFILE = Object.freeze({
    sessionStorage: true,
    localStorage: true,
    indexedDB: true,
    bodyAddBehaviorType: 'undefined',
    openDatabaseType: 'undefined',
    cpuClass: undefined,
    platform: 'Linux armv8l',
    canvasSupported: true,
    canvasFingerprint: 'canvas api exception',
    systemColors: '',
    userAgent: 'Mozilla/5.0 (Linux; Android 12; SM-S9180 Build/TP1A.220905.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/117.0.0.0 Mobile Safari/537.36 moutaiapp/1.9.10 device-id/ BS-DVID/',
    language: 'zh-CN',
    colorDepth: 24,
    screenHeight: 2215,
    screenWidth: 1080,
    timezoneOffset: -480,
    doNotTrack: undefined,
    plugins: [],
});
function gdxUnsignedMul(left, right) {
    return (((left & 0xffff) * right) + ((((left >>> 16) * right) & 0xffff) << 16)) >>> 0;
}
function gdxMurmurHash32(input, seed) {
    const key = String(input || '');
    const remainder = key.length & 3;
    const bytes = key.length - remainder;
    let h1 = seed == null ? GDX_HASH_SEED : Number(seed);
    const c1 = 0xcc9e2d51;
    const c2 = 0x1b873593;
    let i = 0;
    let k1 = 0;

    while (i < bytes) {
        k1 = (key.charCodeAt(i) & 0xff)
            | ((key.charCodeAt(++i) & 0xff) << 8)
            | ((key.charCodeAt(++i) & 0xff) << 16)
            | ((key.charCodeAt(++i) & 0xff) << 24);
        ++i;
        k1 = gdxUnsignedMul(k1, c1);
        k1 = ((k1 << 15) | (k1 >>> 17)) >>> 0;
        k1 = gdxUnsignedMul(k1, c2);
        h1 ^= k1;
        h1 = ((h1 << 13) | (h1 >>> 19)) >>> 0;
        h1 = gdxUnsignedMul(h1, 5);
        h1 = (((h1 & 0xffff) + 0x6b64) + ((((h1 >>> 16) + 0xe654) & 0xffff) << 16)) >>> 0;
    }

    k1 = 0;
    switch (remainder) {
        case 3:
            k1 ^= (key.charCodeAt(i + 2) & 0xff) << 16;
            // fallthrough
        case 2:
            k1 ^= (key.charCodeAt(i + 1) & 0xff) << 8;
            // fallthrough
        case 1:
            k1 ^= key.charCodeAt(i) & 0xff;
            k1 = gdxUnsignedMul(k1, c1);
            k1 = ((k1 << 15) | (k1 >>> 17)) >>> 0;
            k1 = gdxUnsignedMul(k1, c2);
            h1 ^= k1;
            break;
        default:
            break;
    }

    h1 ^= key.length;
    h1 ^= h1 >>> 16;
    h1 = gdxUnsignedMul(h1, 0x85ebca6b);
    h1 ^= h1 >>> 13;
    h1 = gdxUnsignedMul(h1, 0xc2b2ae35);
    h1 ^= h1 >>> 16;
    return h1 >>> 0;
}
function gdxDigitMetric(value, width) {
    const n = Number(value);
    if (n < 0 || n >= 10 || !Number.isFinite(n)) throw new Error('1110');
    const out = Array.from({ length: width }, () => '0');
    const raw = String(n);
    let cursor = 0;
    for (let i = 0; cursor < out.length && i < raw.length; i++) {
        if (raw.charAt(i) !== '.') out[cursor++] = raw.charAt(i);
    }
    return parseInt(out.join(''), 10);
}
function gdxPadMetric(value, width) {
    const raw = String(value);
    if (raw.length > width) throw new Error('1111');
    return raw.padStart(width, '0');
}
function hashGdxFingerprintBucket(input) {
    const rawHash = gdxMurmurHash32(input, GDX_HASH_SEED);
    const parts = [rawHash];
    try {
        const digits = String(rawHash);
        let total = 0;
        let count = 0;
        for (let i = 0; i < digits.length; i++) {
            const digit = parseInt(digits.charAt(i), 10);
            total += Number.isFinite(digit) ? digit : 1;
            count++;
        }
        const avg = gdxDigitMetric(total / (count || 1), GDX_HASH_STAT_WIDTH);
        const threshold = Math.floor(avg / Math.pow(10, GDX_HASH_STAT_WIDTH - 1));
        let lowTotal = 0;
        let lowCount = 0;
        let highTotal = 0;
        let highCount = 0;
        for (let i = 0; i < digits.length; i++) {
            const digit = parseInt(digits.charAt(i), 10);
            const value = Number.isFinite(digit) ? digit : threshold;
            if (value < threshold) {
                lowCount++;
                lowTotal += value;
            } else {
                highCount++;
                highTotal += value;
            }
        }
        const spread = gdxDigitMetric((highTotal / (highCount || 1)) - (lowTotal / (lowCount || 1)), GDX_HASH_STAT_WIDTH);
        parts.push(gdxPadMetric(avg, GDX_HASH_STAT_WIDTH));
        parts.push(gdxPadMetric(spread, GDX_HASH_STAT_WIDTH));
    } catch (_) {
        parts.length = 1;
        parts.push('-'.repeat(GDX_HASH_STAT_WIDTH));
        parts.push('-'.repeat(GDX_HASH_STAT_WIDTH));
    }
    return parts.join('');
}
function gdxJsJoinValue(value) {
    if (value === null || value === undefined) return '';
    return String(value);
}
function gdxJoin(values, sep) {
    return values.map(gdxJsJoinValue).join(sep);
}
function gdxNormalizeInnerProfile(profile) {
    let parsed = {};
    if (process.env.YIDUN_GDX_PROFILE) {
        try {
            parsed = JSON.parse(process.env.YIDUN_GDX_PROFILE);
        } catch (_) {
            parsed = {};
        }
    }
    return Object.assign({}, GDX_DEFAULT_INNER_PROFILE, parsed, profile || {});
}
function buildGdxSystemColorsString(colors) {
    if (typeof colors === 'string') return colors;
    if (!colors || typeof colors !== 'object') return '';
    const out = [];
    for (const name of GDX_SYSTEM_COLORS) {
        if (!Object.prototype.hasOwnProperty.call(colors, name)) continue;
        out.push(name);
        out.push(colors[name]);
    }
    return out.join(':');
}
function buildGdxPluginString(profile) {
    if (typeof profile.pluginString === 'string') return profile.pluginString;
    const plugins = Array.isArray(profile.plugins) ? profile.plugins : null;
    if (!plugins) return '';

    const byName = new Map();
    const primary = plugins.map((plugin) => {
        if (!plugin) return '';
        const name = String(plugin.name || '');
        byName.set(name, plugin);
        const mimeTypes = Array.isArray(plugin.mimeTypes) ? plugin.mimeTypes : Array.from(plugin || []);
        const types = mimeTypes.map((item) => [item?.type || '', item?.suffixes || ''].join('~')).join(',');
        return [name, plugin.description || '', types].join('::');
    }).join('$');
    const probed = GDX_PLUGIN_PROBE_NAMES.map((name) => {
        if (byName.has(name)) return '';
        const plugin = plugins[name];
        if (!plugin) return '';
        const mimeTypes = Array.isArray(plugin.mimeTypes) ? plugin.mimeTypes : Array.from(plugin || []);
        const types = mimeTypes.map((item) => [item?.type || '', item?.suffixes || ''].join('~')).join(',');
        return [plugin.name || '', plugin.description || '', types].join('::');
    }).join(';');
    return [primary, probed].join(';');
}
function buildGdxInnerFingerprintBuckets(profile, options) {
    const p = gdxNormalizeInnerProfile(profile);
    const opts = Object.assign({ b: false, a: false, o: true, l: true, j: true }, options || {});
    const first = [];
    const second = [];

    if (p.environmentOk !== false) {
        first.push(!!p.sessionStorage);
        first.push(!!p.localStorage);
        first.push(!!p.indexedDB);
        first.push(p.bodyAddBehaviorType || 'undefined');
        first.push(p.openDatabaseType || 'undefined');
        first.push(p.cpuClass);
        first.push(p.platform);
        if (opts.l !== false && p.canvasSupported !== false) {
            try {
                first.push(p.canvasFingerprint == null ? 'canvas api exception' : p.canvasFingerprint);
                if (opts.b) first.push(p.webglFingerprint || '');
            } catch (_) {
                first.push('canvas exception');
            }
        }
        first.push(buildGdxSystemColorsString(p.systemColors));
        if (opts.a && p.fonts) second.push(Array.isArray(p.fonts) ? p.fonts.join(';') : p.fonts);
        second.push(p.userAgent);
        second.push(p.language);
        second.push(p.colorDepth);
        if (opts.o !== false) {
            const height = p.screenHeight == null ? 0 : p.screenHeight;
            const width = p.screenWidth == null ? 0 : p.screenWidth;
            second.push([height, width].join('x'));
        }
        second.push(p.timezoneOffset);
        second.push(p.doNotTrack);
        if (opts.j !== false) second.push(buildGdxPluginString(p));
    }

    return [gdxJoin(first, '###'), gdxJoin(second, '###')];
}
function buildGdxInnerFingerprint(profile, options) {
    return buildGdxInnerFingerprintBuckets(profile, options).map(hashGdxFingerprintBucket);
}
function buildGdxInnerFingerprintValue(profile, options) {
    return buildGdxInnerFingerprint(profile, options).join(',');
}
function gdxXorBytes(left, right) {
    if (!left || !right || left.length !== right.length) return left;
    return left.map((byte, index) => toByte(toByte(byte) ^ toByte(right[index])));
}
function gdxSbox(bytes) {
    return bytes.map((byte) => GDX_SBOX[((byte >>> 4) & 0xf) * 16 + (byte & 0xf)]);
}
function gdxInverseSbox(bytes) {
    return bytes.map((byte) => {
        const mapped = GDX_INVERSE_SBOX.get(toByte(byte));
        if (mapped === undefined) throw new Error(`gdx inverse sbox miss: ${byte}`);
        return mapped;
    });
}
function serializeGdxPayload(payload) {
    const keys = ['v', 'fp', 'u', 'h', 'ec', 'em', 'icp'];
    const parts = [];
    for (const key of keys) {
        if (!Object.prototype.hasOwnProperty.call(payload, key)) continue;
        const value = String(payload[key]).replace(/'/g, "\\'").replace(/"/g, '"');
        parts.push(`'${key}':'${value}'`);
    }
    return `{${parts.join(',')}}`;
}
function gdxTimestamp(opts) {
    if (Number.isFinite(Number(opts.timestamp))) return Number(opts.timestamp);
    const nowValue = typeof opts.now === 'function' ? opts.now() : opts.now;
    if (Number.isFinite(Number(nowValue))) return Number(nowValue) + GDX_REFRESH_DELAY_MS;
    return Date.now() + GDX_REFRESH_DELAY_MS;
}
function buildGdxPayload(opts) {
    opts = opts || {};
    const randomFn = opts.randomFn || Math.random;
    const timestamp = gdxTimestamp(opts);
    const fp = gdxFingerprint(opts.fp || opts.gdxFp || (
        opts.gdxInnerProfile === false ? '' : buildGdxInnerFingerprintValue(opts.gdxInnerProfile, opts.gdxInnerOptions)
    ));
    const payload = {
        v: GDX_VERSION,
        fp: fp || '0000000000',
        u: `${opts.prefix || gdxRandomChars(3, randomFn)}${timestamp}${opts.suffix || gdxRandomChars(3, randomFn)}`,
        h: opts.host || 'not_exist_host',
    };
    if (!fp) payload.ec = opts.ec || '1';
    if (opts.em) payload.em = opts.em;
    if (opts.icp) payload.icp = opts.icp;
    return { payload, serializedPayload: serializeGdxPayload(payload), timestamp };
}
function encryptGdxPayload(serializedPayload, opts) {
    opts = opts || {};
    const randomFn = opts.randomFn || Math.random;
    const random = gdxRandomBytes(opts.randomBytes, randomFn);
    const payloadBytes = stringToBytes(serializedPayload);
    const input = stringToBytes(serializedPayload + crc32Hex(payloadBytes));
    const keySeed = repeatTo64(stringToBytes(GDX_SEED_KEY));
    const key = repeatTo64(gdxXorBytes(keySeed, repeatTo64(random)));
    const padded = padWithZeroAndLength(input);
    const out = random.slice();
    let previous = key;

    for (let offset = 0; offset < padded.length; offset += 64) {
        let block = padded.slice(offset, offset + 64);
        block = block.map((byte) => toByte(toByte(byte) ^ toByte(37)));
        let keyByte = toByte(35);
        block = block.map((byte) => toByte(toByte(byte) ^ toByte(keyByte--)));
        keyByte = toByte(-44);
        block = block.map((byte) => toByte(byte + keyByte++));
        block = gdxXorBytes(block, key);
        block = block.map((byte, index) => toByte(byte + previous[index % previous.length]));
        block = gdxXorBytes(block, previous);
        previous = gdxSbox(gdxSbox(block));
        out.push(...previous);
    }
    return _b64enc(out, GDX_BASE64.split(''), GDX_PADDING);
}
function extractGdxCookieValue(value) {
    let raw = String(value || '').trim();
    const match = raw.match(/(?:^|;\s*)gdxidpyhxdE=([^;]+)/);
    if (match) raw = match[1];
    try {
        raw = decodeURIComponent(raw);
    } catch (_) {
        // keep original
    }
    return raw;
}
function parseGdxSerializedPayload(serializedPayload) {
    const payload = {};
    const re = /'([^']+)':'((?:\\'|[^'])*)'/g;
    let match;
    while ((match = re.exec(String(serializedPayload)))) {
        payload[match[1]] = match[2].replace(/\\'/g, "'");
    }
    return payload;
}
function decodeGdxidpyhxdE(value) {
    const raw = extractGdxCookieValue(value);
    const colonIdx = raw.lastIndexOf(':');
    if (colonIdx <= 0) throw new Error('invalid gdxidpyhxdE value');
    const body = raw.slice(0, colonIdx);
    const timestampRaw = raw.slice(colonIdx + 1);
    const timestamp = Number(timestampRaw);
    const bytes = _b64dec(body, GDX_BASE64.split(''), GDX_PADDING);
    if (bytes.length < 68 || (bytes.length - 4) % 64 !== 0) {
        throw new Error(`invalid gdxidpyhxdE body length: ${bytes.length}`);
    }

    const random = bytes.slice(0, 4);
    const keySeed = repeatTo64(stringToBytes(GDX_SEED_KEY));
    const key = repeatTo64(gdxXorBytes(keySeed, repeatTo64(random)));
    const input = [];
    let previous = key;

    for (let offset = 4; offset < bytes.length; offset += 64) {
        const cipherBlock = bytes.slice(offset, offset + 64);
        let block = gdxInverseSbox(gdxInverseSbox(cipherBlock));
        block = gdxXorBytes(block, previous);
        block = block.map((byte, index) => toByte(byte - previous[index % previous.length]));
        block = gdxXorBytes(block, key);
        let keyByte = toByte(-44);
        block = block.map((byte) => toByte(byte - keyByte++));
        keyByte = toByte(35);
        block = block.map((byte) => toByte(toByte(byte) ^ toByte(keyByte--)));
        block = block.map((byte) => toByte(toByte(byte) ^ toByte(37)));
        input.push(...block);
        previous = cipherBlock;
    }

    const length = ((toUnsigned(input[input.length - 4]) << 24) >>> 0)
        + (toUnsigned(input[input.length - 3]) << 16)
        + (toUnsigned(input[input.length - 2]) << 8)
        + toUnsigned(input[input.length - 1]);
    const unpadded = input.slice(0, length);
    const decoded = bytesToString(unpadded);
    const serializedPayload = decoded.slice(0, -8);
    const checksum = decoded.slice(-8);
    const crcOk = crc32Hex(stringToBytes(serializedPayload)) === checksum;
    return {
        value: raw,
        body,
        timestamp,
        timestampRaw,
        randomBytes: random.map(toUnsigned),
        serializedPayload,
        checksum,
        crcOk,
        payload: parseGdxSerializedPayload(serializedPayload),
    };
}
function buildGdxidpyhxdE(opts) {
    opts = opts || {};
    const randomFn = opts.randomFn || Math.random;
    const built = buildGdxPayload(Object.assign({}, opts, { randomFn }));
    const body = encryptGdxPayload(built.serializedPayload, {
        randomBytes: opts.randomBytes,
        randomFn,
    });
    const value = `${body}:${built.timestamp}`;
    const encodedValue = encodeURIComponent(value);
    const expiresAt = built.timestamp + GDX_TTL_MS;
    const cookieParts = [`${GDX_COOKIE_NAME}=${encodedValue}`];
    if (opts.expires !== false) cookieParts.push(`expires=${new Date(expiresAt).toGMTString()}`);
    cookieParts.push('path=/');
    if (opts.domain) cookieParts.push(`domain=${opts.domain}`);
    return {
        name: GDX_COOKIE_NAME,
        windowName: GDX_WINDOW_NAME,
        value,
        encodedValue,
        cookie: cookieParts.join('; '),
        body,
        timestamp: built.timestamp,
        expiresAt,
        payload: built.payload,
        serializedPayload: built.serializedPayload,
    };
}
function buildGdxidpyhxdEValue(opts) {
    return buildGdxidpyhxdE(opts).value;
}

// ============= jsonp =============

function generateJsonpCallback(rfn) {
    const r = rfn || Math.random;
    const alpha = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    let s = '';
    for (let i = 0; i < 8; i++) s += alpha[Math.floor(r() * alpha.length)];
    return `__JSONP_${s}_${Math.floor(r() * 10)}`;
}
function parseJsonp(body, callback) {
    const t = String(body).trim();
    try { return JSON.parse(t); } catch (_) { /* fallthrough */ }
    const start = t.indexOf('(');
    const end = t.lastIndexOf(')');
    if (start >= 0 && end > start) return JSON.parse(t.slice(start + 1, end));
    throw new Error('JSONP 解析失败: ' + (callback || ''));
}

// ============= track wrap =============

function parsePointString(s) {
    return String(s)
        .split(/[|;\n]+/)
        .map((seg) => seg.trim())
        .filter(Boolean)
        .map((seg) => seg.split(',').map((v) => Number(String(v).trim())))
        .filter((arr) => arr.length >= 3 && arr.every((n) => Number.isFinite(n)))
        .map((arr) => [Math.round(arr[0]), Math.round(arr[1]), Math.round(arr[2]), arr[3] == null ? 1 : arr[3]]);
}

function sampleList(list, n) {
    if (list.length <= n) return list;
    const out = [];
    let cursor = 0;
    for (let i = 0; i < list.length; i++) {
        if (i >= (cursor * (list.length - 1)) / (n - 1)) {
            out.push(list[i]);
            cursor += 1;
        }
    }
    return out;
}

function unique2DArray(points, index) {
    const seen = new Set();
    const out = [];
    for (const point of points) {
        const key = point[index];
        if (key == null || seen.has(key)) continue;
        seen.add(key);
        out.push(point);
    }
    return out;
}

function toNumberSeries(points) {
    const xs = [];
    const ys = [];
    const ts = [];
    for (const point of points) {
        xs.push(point[0]);
        ys.push(point[1]);
        ts.push(point[2]);
    }
    return [xs, ys, ts];
}

function diffQuotient(base, values) {
    const out = [];
    for (let i = 0; i < base.length - 1; i++) {
        const dt = base[i + 1] - base[i];
        if (dt === 0) continue;
        out.push((values[i + 1] - values[i]) / dt);
    }
    return out;
}

function buildVelocities(xs, ys, ts) {
    const vx = diffQuotient(ts, xs);
    const vy = diffQuotient(ts, ys);
    const distance = xs.map((x, i) => Math.sqrt(Math.pow(x, 2) + Math.pow(ys[i], 2)));
    const v = diffQuotient(ts, distance);
    return [vx, vy, v];
}

function buildAccelerations(vx, vy, v, ts) {
    const velocityTimeBase = ts.slice(0, -1);
    return [
        diffQuotient(velocityTimeBase, vx),
        diffQuotient(velocityTimeBase, vy),
        diffQuotient(velocityTimeBase, v),
    ];
}

function mean(values) {
    if (!values.length) return 0;
    return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function stddev(values) {
    if (!values.length) return 0;
    const avg = mean(values);
    const variance = values.reduce((sum, value) => sum + Math.pow(value - avg, 2), 0) / values.length;
    return Math.sqrt(variance);
}

function round4(value) {
    if (!Number.isFinite(value)) return 0;
    return parseFloat(value.toFixed(4));
}

function uniqueCount(values) {
    return values.filter((value, index) => values.indexOf(value) === index).length;
}

function percentile(values, percent) {
    if (!values.length) return 0;
    const sorted = values.slice().sort((a, b) => a - b);
    if (percent <= 0) return sorted[0];
    if (percent >= 100) return sorted[sorted.length - 1];
    if (sorted.length === 1) return sorted[0];
    const rawIndex = (sorted.length - 1) * (percent / 100);
    const lowerIndex = Math.floor(rawIndex);
    const lower = sorted[lowerIndex];
    const upper = sorted[Math.min(lowerIndex + 1, sorted.length - 1)];
    return lower + (upper - lower) * (rawIndex - lowerIndex);
}

function seriesFeatures(values) {
    if (!values.length) return [0, 0, 0, 0, 0, 0, 0];
    return [
        round4(Math.min(...values)),
        round4(Math.max(...values)),
        round4(mean(values)),
        round4(stddev(values)),
        uniqueCount(values),
        round4(percentile(values, 25)),
        round4(percentile(values, 75)),
    ];
}

function buildMotionFeatures(points) {
    const uniq = unique2DArray(points, 2);
    if (!Array.isArray(uniq) || uniq.length <= 2) return [];

    const [xs, ys, ts] = toNumberSeries(uniq);
    const [vx, vy, v] = buildVelocities(xs, ys, ts);
    const [ax, ay, a] = buildAccelerations(vx, vy, v, ts);

    return [
        uniqueCount(xs),
        uniqueCount(ys),
        round4(mean(ys)),
        round4(stddev(ys)),
        xs.length,
        ...seriesFeatures(vx),
        ...seriesFeatures(vy),
        ...seriesFeatures(v),
        ...seriesFeatures(ax),
        ...seriesFeatures(ay),
        ...seriesFeatures(a),
    ];
}

function buildFTrace(points) {
    return buildMotionFeatures(points).join(',');
}

function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
}

function randomBetween(randomFn, min, max) {
    return min + (max - min) * randomFn();
}

function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
}

function smoothstep(t) {
    const x = clamp(t, 0, 1);
    return x * x * (3 - 2 * x);
}

// 真人样本模板（来自 验证码流程/未命名文件夹/[8679] /v3/check 抓包；captchaId=22912abb..., width=362, target=96px）。
// 已是 sample(50) 后形态，可直接用于 wrapTrack。形态：start=(8,0,t=47), peak=(118,22,t=756), settle=(111,25,t=1473)。
const REAL_TRACE_TEMPLATE = [
    [8, 0, 47, 1], [24, 2, 80, 1], [40, 5, 112, 1], [54, 8, 142, 1], [68, 11, 179, 1],
    [75, 13, 193, 1], [87, 15, 227, 1], [97, 16, 260, 1], [104, 17, 293, 1], [106, 18, 308, 1],
    [109, 19, 342, 1], [110, 20, 377, 1], [110, 19, 409, 1], [110, 19, 424, 1], [110, 19, 457, 1],
    [110, 20, 492, 1], [112, 20, 523, 1], [112, 20, 540, 1], [113, 20, 572, 1], [114, 20, 607, 1],
    [115, 20, 640, 1], [116, 21, 658, 1], [117, 21, 689, 1], [117, 21, 723, 1], [118, 22, 756, 1],
    [118, 22, 773, 1], [118, 22, 808, 1], [117, 22, 839, 1], [117, 22, 870, 1], [117, 22, 887, 1],
    [117, 22, 921, 1], [117, 23, 954, 1], [116, 23, 988, 1], [115, 23, 1005, 1], [114, 23, 1038, 1],
    [113, 24, 1070, 1], [112, 24, 1105, 1], [111, 24, 1120, 1], [111, 25, 1154, 1], [111, 25, 1189, 1],
    [111, 25, 1221, 1], [111, 25, 1236, 1], [111, 25, 1269, 1], [111, 25, 1304, 1], [111, 25, 1336, 1],
    [111, 25, 1352, 1], [111, 25, 1386, 1], [111, 25, 1419, 1], [111, 25, 1451, 1], [111, 25, 1473, 1],
];
const TEMPLATE_TARGET_X = 96;
const TEMPLATE_START_X = 8;

// 仅 targetX 时生成 WebView 风格轨迹。
// 默认使用真实抓包样本缩放；options.humanize=true 改用随机真人长轨迹；options.replay=false 保留旧合成。
function generateDenseTraceFromX(targetX, options) {
    options = options || {};
    if (options.replay === false) return generateSyntheticTraceFromX(targetX, options);
    if (options.humanize === true) return generateHumanTraceFromX(targetX, options);
    return generateReplayTraceFromX(targetX, options);
}

function generateHumanTraceFromX(targetX, options) {
    options = options || {};
    const randomFn = options.randomFn || Math.random;
    const rR = (lo, hi) => lo + randomFn() * (hi - lo);
    const target = Math.max(5, Math.round(targetX));

    if (options.shortTemplate === true || (options.shortTemplate !== false && target <= 130)) {
        return generateSyntheticTraceFromX(target, options);
    }

    const startX = Math.round(rR(7, 12));
    const startY = Math.round(rR(-5, -2));
    const overshoot = Math.round(clamp(target * 0.035 + rR(12, 20), 14, 28));
    const peakX = target + overshoot;
    const backoff = Math.round(clamp(rR(4, 8) + target * 0.006, 4, 10));
    const finalX = Math.max(target + Math.round(overshoot * 0.45), peakX - backoff);
    const peakY = Math.round(clamp(rR(14, 18) + target * 0.0015, 14, 19));

    const pointCount = Math.round(clamp(rR(136, 154) + target * 0.01, 136, 158));
    const duration = Math.round(clamp(rR(1950, 2450) + target * 2.1, 2200, 2850));
    const startT = Math.round(rR(75, 125));
    const dragStart = rR(0.004, 0.012);
    const dragEnd = rR(0.60, 0.69);
    const holdEnd = Math.min(0.86, dragEnd + rR(0.12, 0.18));
    const correctionEnd = Math.min(0.98, holdEnd + rR(0.16, 0.22));
    const yRiseEnd = rR(0.34, 0.48);

    const trace = [];
    let prevX = startX;
    let curT = startT;
    const baseDt = duration / Math.max(1, pointCount - 1);

    for (let i = 0; i < pointCount; i++) {
        const u = pointCount <= 1 ? 1 : i / (pointCount - 1);
        let x;
        if (u < dragStart) {
            x = startX + rR(-0.35, 0.35);
        } else if (u <= dragEnd) {
            const q = smoothstep((u - dragStart) / Math.max(0.001, dragEnd - dragStart));
            x = startX + (peakX - startX) * q + rR(-0.65, 0.65);
            if (x < prevX) x = prevX + rR(0, 0.45);
        } else if (u <= holdEnd) {
            x = peakX + rR(-0.45, 0.45);
        } else if (u <= correctionEnd) {
            const q = smoothstep((u - holdEnd) / Math.max(0.001, correctionEnd - holdEnd));
            x = peakX - (peakX - finalX) * q + rR(-0.75, 0.7);
        } else {
            x = finalX + rR(-0.25, 0.25);
        }

        const yProgress = u <= yRiseEnd ? smoothstep(u / Math.max(0.001, yRiseEnd)) : 1;
        const wobble = Math.sin(u * Math.PI * 2.3 + rR(-0.2, 0.2)) * rR(0.12, 0.32);
        const steadyNoise = u > yRiseEnd ? rR(-0.35, 0.35) : rR(-0.3, 0.3);
        const y = startY + (peakY - startY) * yProgress + wobble + steadyNoise;

        if (i > 0) {
            let dt = baseDt + rR(-3, 4);
            if (u >= dragStart && u <= dragEnd && i % 9 === 0) {
                dt = rR(7, 11);
            } else {
                if (i % 17 === 0) dt += rR(8, 16);
                if (i % 43 === 0) dt += rR(10, 22);
            }
            curT += Math.max(7, Math.round(dt));
        }

        const roundedX = Math.round(x);
        trace.push([roundedX, Math.round(y), curT, 1]);
        prevX = roundedX;
    }

    for (let i = 1; i < trace.length; i++) {
        if (trace[i][2] <= trace[i - 1][2]) trace[i][2] = trace[i - 1][2] + 1;
    }
    return trace;
}

function generateReplayTraceFromX(targetX, options) {
    options = options || {};
    const randomFn = options.randomFn || Math.random;
    const target = Math.max(5, Math.round(targetX));
    const startXNew = Math.round(randomFn() * 8 + 4); // [4,12]
    const xRatio = (target - startXNew) / (TEMPLATE_TARGET_X - TEMPLATE_START_X);
    const tScale = 0.85 + randomFn() * 0.30; // [0.85, 1.15]
    const tOffset = Math.round(30 + randomFn() * 20) - Math.round(REAL_TRACE_TEMPLATE[0][2] * tScale);
    const yOffset = Math.round(randomFn() * 4 - 2); // [-2,2]

    const out = REAL_TRACE_TEMPLATE.map((p, i) => {
        const [x, y, t] = p;
        const xNew = startXNew + (x - TEMPLATE_START_X) * xRatio + (randomFn() * 1.2 - 0.6);
        const yNew = i === 0 ? 0 : Math.max(0, y + yOffset + Math.round(randomFn() - 0.5));
        const tNew = Math.round(t * tScale + tOffset + (randomFn() * 4 - 2));
        return [Math.round(xNew), yNew, tNew, 1];
    });
    for (let i = 1; i < out.length; i++) {
        if (out[i][2] <= out[i - 1][2]) out[i][2] = out[i - 1][2] + 1;
    }
    return out;
}

// 旧合成版本（保留以备对比/降级）。基于真人样本统计：
//   匀速推进 vx≈0.48 → 减速 → 静止 settle → 缓慢回退 → 尾部静止
//   dt 主导 33ms（30Hz），每 ~5 帧夹一个 ~16ms（60Hz RAF）
function generateSyntheticTraceFromX(targetX, options) {
    options = options || {};
    const randomFn = options.randomFn || Math.random;
    const target = Math.max(5, Math.round(targetX));
    const expectedCount = options.expectedCount || Math.round(randomBetween(randomFn, 82, 94));
    const overshoot = options.overshoot == null
        ? Math.round(clamp(target * 0.08 + randomBetween(randomFn, 10, 18), 12, 28))
        : Math.round(options.overshoot);
    const peakX = target + overshoot;
    const settleX = Math.max(target, peakX - Math.round(randomBetween(randomFn, 4, 9)));
    const maxY = Math.round(clamp(target * 0.09 + randomBetween(randomFn, 12, 18), 14, 34));

    const trace = [];
    let curX = Math.round(randomBetween(randomFn, 6, 8));
    let curY = 0;
    let curTime = Math.round(randomBetween(randomFn, 30, 42));
    const add = (x, y, t) => trace.push([Math.round(x), Math.round(y), Math.round(t), 1]);

    const stickyCount = Math.max(2, Math.round(expectedCount * 0.035));
    const moveCount = Math.max(22, Math.round(expectedCount * 0.38));
    const settleCount = Math.max(10, Math.round(expectedCount * 0.16));
    const tailCount = Math.max(14, expectedCount - stickyCount - moveCount - settleCount);

    for (let i = 0; i < stickyCount; i++) {
        if (curX < 8 && (i % 2 === 0 || randomFn() > 0.75)) curX += 1;
        curTime += Math.floor(randomBetween(randomFn, 8, 14));
        add(curX, curY, curTime);
    }

    const startX = curX;
    for (let i = 1; i <= moveCount; i++) {
        const ratio = i / moveCount;
        const eased = easeOutCubic(ratio);
        const nextX = startX + (peakX - startX) * eased + randomBetween(randomFn, -0.7, 0.9);
        curX = Math.max(curX, Math.round(nextX));
        curY = Math.max(0, Math.round(maxY * eased + randomBetween(randomFn, -1.1, 1.1)));
        curTime += Math.floor(randomBetween(randomFn, 11, 21));
        add(curX, curY, curTime);
    }

    const correctionStartX = curX;
    for (let i = 1; i <= settleCount; i++) {
        const ratio = i / settleCount;
        curX = Math.round(correctionStartX - (correctionStartX - settleX) * easeOutCubic(ratio));
        curY = Math.round(clamp(maxY + randomBetween(randomFn, -1.2, 1.8), 0, 36));
        curTime += Math.floor(randomBetween(randomFn, 13, 28));
        add(curX, curY, curTime);
    }

    for (let i = 0; i < tailCount; i++) {
        curTime += Math.floor(randomBetween(randomFn, 13, 31));
        add(settleX, maxY, curTime);
    }

    // 时间严格单调
    for (let i = 1; i < trace.length; i++) {
        if (trace[i][2] <= trace[i - 1][2]) trace[i][2] = trace[i - 1][2] + 1;
    }
    return trace;
}

function getFirstFiniteNumber(value) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string') {
        const match = value.match(/-?\d+(?:\.\d+)?/);
        if (!match) return null;
        const n = Number(match[0]);
        return Number.isFinite(n) ? n : null;
    }
    return null;
}

function normalizeTrackObject(raw) {
    if ('d' in raw || 'p' in raw) return { bundle: raw, pixelX: null };
    for (const key of ['recognition', 'targetX', 'slideX', 'x', 'left']) {
        if (key in raw) {
            const n = getFirstFiniteNumber(raw[key]);
            if (n !== null) return { bundle: null, pixelX: n };
        }
    }
    return { bundle: raw, pixelX: null };
}

function pointsToEncodedSamples(points, token) {
    const sampled = sampleList(points, points.length > 50 ? 50 : points.length);
    return sampled.map((p) => {
        const flag = p[3] == null ? 1 : p[3];
        return xorEncode(token, `${p[0]},${p[1]},${p[2]},${flag}`);
    });
}

function normalizeRawTrack(raw) {
    if (raw == null) return { points: null, bundle: null, pixelX: null };
    if (typeof raw === 'number') return { points: null, bundle: null, pixelX: raw };

    if (typeof raw === 'string') {
        const trimmed = raw.trim();
        if (!trimmed) return { points: null, bundle: null, pixelX: null };
        if (trimmed.startsWith('{')) {
            try {
                const parsed = JSON.parse(trimmed);
                if (parsed && typeof parsed === 'object' && ('d' in parsed || 'p' in parsed)) {
                    return { points: null, bundle: parsed, pixelX: null };
                }
            } catch (_) { /* fallthrough */ }
        }
        if (trimmed.includes('|') || trimmed.includes(';') || trimmed.includes(',')) {
            const points = parsePointString(trimmed);
            if (points.length) return { points, bundle: null, pixelX: null };
            const coordX = getFirstFiniteNumber(trimmed);
            if (coordX !== null) return { points: null, bundle: null, pixelX: coordX };
        }
        const n = Number(trimmed);
        if (Number.isFinite(n)) return { points: null, bundle: null, pixelX: n };
        return { points: null, bundle: null, pixelX: null };
    }

    if (Array.isArray(raw)) {
        const points = raw
            .filter((p) => Array.isArray(p) && p.length >= 3 && p.every((v) => Number.isFinite(v)))
            .map((p) => [Math.round(p[0]), Math.round(p[1]), Math.round(p[2]), p[3] == null ? 1 : p[3]]);
        return { points, bundle: null, pixelX: null };
    }

    if (typeof raw === 'object') {
        const { bundle, pixelX } = normalizeTrackObject(raw);
        return { points: null, bundle, pixelX };
    }

    return { points: null, bundle: null, pixelX: null };
}

function scalePoint(point, scale) {
    const flag = point[3] == null ? 1 : point[3];
    return [Math.round(point[0] * scale), point[1], point[2], flag];
}

function scalePointString(value, scale) {
    const trimmed = String(value).trim();
    if (!trimmed) return value;
    if (trimmed.startsWith('{')) {
        try { return JSON.stringify(scaleRawTrackX(JSON.parse(trimmed), scale)); } catch (_) { return value; }
    }
    if (trimmed.includes('|') || trimmed.includes(';')) {
        const sep = trimmed.includes('|') ? '|' : ';';
        return trimmed.split(sep).map((seg) => {
            const parts = seg.split(',');
            const x = Number(parts[0]);
            if (Number.isFinite(x)) parts[0] = String(Math.round(x * scale));
            return parts.join(',');
        }).join(sep);
    }
    if (trimmed.includes(',')) {
        const parts = trimmed.split(',');
        const x = Number(parts[0]);
        if (Number.isFinite(x)) parts[0] = String(Math.round(x * scale));
        return parts.join(',');
    }
    const n = Number(trimmed);
    return Number.isFinite(n) ? String(Math.round(n * scale)) : value;
}

function scaleRawTrackX(rawTrack, scale) {
    if (!Number.isFinite(scale) || scale <= 0 || Math.abs(scale - 1) < 0.001) return rawTrack;
    if (typeof rawTrack === 'number') return Math.round(rawTrack * scale);
    if (typeof rawTrack === 'string') return scalePointString(rawTrack, scale);
    if (Array.isArray(rawTrack)) return rawTrack.map((point) => scalePoint(point, scale));
    if (rawTrack && typeof rawTrack === 'object') {
        if ('d' in rawTrack || 'p' in rawTrack) return rawTrack;
        const scaled = Object.assign({}, rawTrack);
        for (const key of ['recognition', 'targetX', 'slideX', 'x', 'left']) {
            if (key in scaled) scaled[key] = scaleRawTrackX(scaled[key], scale);
        }
        return scaled;
    }
    return rawTrack;
}

function wrapTrack(opts) {
    opts = opts || {};
    const { rawTrack, token, sessionRandom } = opts;
    const width = opts.width || 362;
    const aesOpts = sessionRandom ? { sessionRandom } : undefined;
    const normalized = normalizeRawTrack(rawTrack);

    if (normalized.bundle) {
        const bundle = normalized.bundle;
        const isEncrypted = bundle.encrypted !== false;
        const encode = (value) => {
            if (value == null) return '';
            if (isEncrypted) return String(value);
            return aesEncrypt(xorEncode(token, String(value)), aesOpts);
        };
        return {
            d: encode(bundle.d),
            m: bundle.m || '',
            p: encode(bundle.p),
            f: encode(bundle.f),
            ext: encode(bundle.ext),
        };
    }

    let points;
    if (normalized.points && normalized.points.length) {
        points = normalized.points;
    } else if (normalized.pixelX !== null) {
        const traceOptions = Object.assign({}, opts.traceOptions || {});
        traceOptions.randomFn = opts.randomFn;
        if (opts.replay !== undefined) traceOptions.replay = opts.replay;
        if (opts.humanize !== undefined) traceOptions.humanize = opts.humanize === true;
        if (traceOptions.humanize === undefined && traceOptions.replay === undefined) traceOptions.humanize = true;
        points = generateDenseTraceFromX(normalized.pixelX, traceOptions);
    } else {
        throw new Error('wrapTrack: rawTrack 无法解析为点序列或像素 X');
    }

    const targetX = opts.targetX == null ? (normalized.pixelX == null ? points[points.length - 1][0] : normalized.pixelX) : opts.targetX;
    const mouseDownCount = opts.mouseDownCount == null ? 1 : opts.mouseDownCount;
    const dEncoded = pointsToEncodedSamples(points, token);
    const fTrace = buildFTrace(points);

    return {
        d: aesEncrypt(dEncoded.join(':'), aesOpts),
        m: '',
        p: aesEncrypt(xorEncode(token, String((targetX / width) * 100)), aesOpts),
        f: aesEncrypt(xorEncode(token, fTrace), aesOpts),
        ext: aesEncrypt(xorEncode(token, `${mouseDownCount},${points.length}`), aesOpts),
    };
}

// 解析 PNG，返回 alpha 非透明区域的最小列 x（图像系）。失败返回 null。
function getPngAlphaMinX(body) {
    try {
        if (body.length < 24) return null;
        if (!body.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return null;
        const width = body.readUInt32BE(16);
        const height = body.readUInt32BE(20);
        const bitDepth = body[24];
        const colorType = body[25];
        if (bitDepth !== 8) return null;
        // 仅支持带 alpha 的 truecolor (6) / grayscale+alpha (4)
        const bpp = colorType === 6 ? 4 : colorType === 4 ? 2 : null;
        if (!bpp) return null;
        const idatChunks = [];
        let off = 8;
        while (off + 8 <= body.length) {
            const len = body.readUInt32BE(off);
            const type = body.subarray(off + 4, off + 8).toString('ascii');
            const dataOff = off + 8;
            if (type === 'IDAT') idatChunks.push(body.subarray(dataOff, dataOff + len));
            if (type === 'IEND') break;
            off = dataOff + len + 4;
        }
        if (!idatChunks.length) return null;
        const zlib = require('zlib');
        const raw = zlib.inflateSync(Buffer.concat(idatChunks));
        const stride = width * bpp;
        // 反过滤：每行第一个字节是 filter type
        const out = Buffer.alloc(stride * height);
        let prev = Buffer.alloc(stride);
        let pos = 0;
        for (let y = 0; y < height; y++) {
            const filter = raw[pos++];
            const row = Buffer.alloc(stride);
            for (let x = 0; x < stride; x++) {
                const cur = raw[pos++];
                const left = x >= bpp ? row[x - bpp] : 0;
                const up = prev[x];
                const upLeft = x >= bpp ? prev[x - bpp] : 0;
                let recon;
                switch (filter) {
                    case 0: recon = cur; break;
                    case 1: recon = (cur + left) & 0xff; break;
                    case 2: recon = (cur + up) & 0xff; break;
                    case 3: recon = (cur + ((left + up) >> 1)) & 0xff; break;
                    case 4: {
                        const p = left + up - upLeft;
                        const pa = Math.abs(p - left);
                        const pb = Math.abs(p - up);
                        const pc = Math.abs(p - upLeft);
                        const pred = pa <= pb && pa <= pc ? left : pb <= pc ? up : upLeft;
                        recon = (cur + pred) & 0xff;
                        break;
                    }
                    default: return null;
                }
                row[x] = recon;
            }
            row.copy(out, y * stride);
            prev = row;
        }
        // alpha 通道索引：truecolor+alpha → byte index 3, gray+alpha → byte index 1
        const alphaOffset = bpp - 1;
        for (let x = 0; x < width; x++) {
            for (let y = 0; y < height; y++) {
                if (out[y * stride + x * bpp + alphaOffset] > 10) return x;
            }
        }
        return null;
    } catch (_) {
        return null;
    }
}

function getImageDimensions(body) {
    if (body.length >= 24 && body.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
        return { width: body.readUInt32BE(16), height: body.readUInt32BE(20) };
    }
    if (body.length >= 4 && body[0] === 0xff && body[1] === 0xd8) {
        let offset = 2;
        while (offset + 9 < body.length) {
            if (body[offset] !== 0xff) {
                offset += 1;
                continue;
            }
            const marker = body[offset + 1];
            const length = body.readUInt16BE(offset + 2);
            if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker)) {
                return { width: body.readUInt16BE(offset + 7), height: body.readUInt16BE(offset + 5) };
            }
            offset += 2 + length;
        }
    }
    if (body.length >= 30 && body.subarray(0, 4).toString('ascii') === 'RIFF' && body.subarray(8, 12).toString('ascii') === 'WEBP') {
        const type = body.subarray(12, 16).toString('ascii');
        if (type === 'VP8X') return { width: 1 + body.readUIntLE(24, 3), height: 1 + body.readUIntLE(27, 3) };
    }
    return {};
}

// ============= bingtop client =============

const DEFAULT_BINGTOP_ENDPOINT = 'https://www.bingtop.com/ocr/upload/';
const DEFAULT_BINGTOP_CAPTCHA_TYPE = 1316;
const DEFAULT_BINGTOP_TIMEOUT_MS = 60000;

function postForm(url, formObj, timeoutMs) {
    return new Promise((resolve, reject) => {
        const u = new URL(url);
        const body = Object.entries(formObj)
            .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v == null ? '' : v))}`)
            .join('&');
        const req = https.request({
            method: 'POST',
            hostname: u.hostname,
            port: u.port || 443,
            path: u.pathname + (u.search || ''),
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Content-Length': Buffer.byteLength(body),
                'User-Agent': 'Mozilla/5.0',
            },
            timeout: timeoutMs,
        }, (res) => {
            const chunks = [];
            res.on('data', (c) => chunks.push(c));
            res.on('end', () => resolve({ status: res.statusCode, bodyText: Buffer.concat(chunks).toString('utf8') }));
        });
        req.on('error', reject);
        req.on('timeout', () => { req.destroy(new Error('bingtop timeout')); });
        req.write(body);
        req.end();
    });
}

function decodeHttpBody(res, raw) {
    const enc = String(res.headers && res.headers['content-encoding'] || '').toLowerCase();
    try {
        if (enc === 'gzip') return zlib.gunzipSync(raw);
        if (enc === 'deflate') return zlib.inflateSync(raw);
        if (enc === 'br' && typeof zlib.brotliDecompressSync === 'function') return zlib.brotliDecompressSync(raw);
    } catch (_) { /* fall through to raw */ }
    return raw;
}
function getBinary(url, timeoutMs, extraHeaders) {
    return new Promise((resolve, reject) => {
        const u = new URL(url);
        const headers = Object.assign({
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
            'Accept': '*/*',
        }, extraHeaders || {});
        const req = https.request({
            method: 'GET',
            hostname: u.hostname,
            port: u.port || 443,
            path: u.pathname + (u.search || ''),
            headers,
            timeout: timeoutMs,
        }, (res) => {
            const chunks = [];
            res.on('data', (c) => chunks.push(c));
            res.on('end', () => resolve({ status: res.statusCode, body: decodeHttpBody(res, Buffer.concat(chunks)) }));
        });
        req.on('error', reject);
        req.on('timeout', () => { req.destroy(new Error('getBinary timeout')); });
        req.end();
    });
}
function getText(url, timeoutMs, extraHeaders) {
    return getBinary(url, timeoutMs, extraHeaders).then((r) => ({ status: r.status, bodyText: r.body.toString('utf8') }));
}

function postText(url, body, timeoutMs, extraHeaders) {
    return new Promise((resolve, reject) => {
        const u = new URL(url);
        const headers = Object.assign({
            'Content-Type': 'text/plain',
            'Content-Length': Buffer.byteLength(body),
            'User-Agent': 'Mozilla/5.0 (Linux; Android 14; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/117.0.0.0 Mobile Safari/537.36 moutaiapp/1.9.10',
            'Accept': '*/*',
        }, extraHeaders || {});
        const req = https.request({
            method: 'POST',
            hostname: u.hostname,
            port: u.port || 443,
            path: u.pathname + (u.search || ''),
            headers,
            timeout: timeoutMs,
        }, (res) => {
            const chunks = [];
            res.on('data', (c) => chunks.push(c));
            res.on('end', () => resolve({ status: res.statusCode, bodyText: decodeHttpBody(res, Buffer.concat(chunks)).toString('utf8') }));
        });
        req.on('error', reject);
        req.on('timeout', () => { req.destroy(new Error('postText timeout')); });
        req.write(body);
        req.end();
    });
}

// ============= ddddocr client =============

const DEFAULT_DDDDOCR_ENDPOINT = 'http://127.0.0.1:9898';
const DEFAULT_DDDDOCR_TIMEOUT_MS = 30000;

function postJson(url, payload, timeoutMs, extraHeaders) {
    return new Promise((resolve, reject) => {
        const u = new URL(url);
        const isHttps = u.protocol === 'https:';
        const lib = isHttps ? https : http;
        const body = Buffer.from(JSON.stringify(payload), 'utf8');
        const headers = Object.assign({
            'Content-Type': 'application/json',
            'Content-Length': body.length,
            'User-Agent': 'yidun-sdk/ddddocr',
            'Accept': 'application/json',
        }, extraHeaders || {});
        const req = lib.request({
            method: 'POST',
            hostname: u.hostname,
            port: u.port || (isHttps ? 443 : 80),
            path: u.pathname + (u.search || ''),
            headers,
            timeout: timeoutMs,
        }, (res) => {
            const chunks = [];
            res.on('data', (c) => chunks.push(c));
            res.on('end', () => resolve({ status: res.statusCode, bodyText: Buffer.concat(chunks).toString('utf8') }));
        });
        req.on('error', reject);
        req.on('timeout', () => { req.destroy(new Error('ddddocr timeout')); });
        req.write(body);
        req.end();
    });
}

/**
 * 调用本地 ddddocr_api_server：
 *  - 首选 /detection（det=True 目标检测，适合复杂背景）
 *  - 备选 /slide_match（simple_target=True）
 * 返回结构对齐 bingtopSolve：{ code, recognition, raw }。
 * recognition = 缺口中心 X − 滑块半宽（滑块需要移动的像素距离，原图坐标系）。
 */
async function ddddocrSolve(input, config) {
    config = config || {};
    const endpoint = (config.endpoint || DEFAULT_DDDDOCR_ENDPOINT).trim().replace(/\/+$/, '') || DEFAULT_DDDDOCR_ENDPOINT;
    const timeoutMs = config.timeoutMs || DEFAULT_DDDDOCR_TIMEOUT_MS;
    const simpleTarget = config.simpleTarget !== false; // 默认 true
    const sliderWidth = Number(input.sliderWidth) || 60;
    const useDetection = config.useDetection !== false; // 默认 true
    let netRetryLeft = 1;

    async function postOnce(path, body) {
        const url = `${endpoint}${path}`;
        const r = await postJson(url, body, timeoutMs);
        if (r.status >= 500) throw new Error(`[ddddocr ${path}] HTTP ${r.status}`);
        let parsed;
        try { parsed = JSON.parse(r.bodyText); } catch (_) {
            throw new Error(`[ddddocr ${path}] 非 JSON 响应: ` + r.bodyText.slice(0, 200));
        }
        return parsed;
    }

    while (true) {
        try {
            let recognition = null;
            let raw = null;
            // 优先 detection
            if (useDetection) {
                const dResp = await postOnce('/detection', { image: input.bgBase64 });
                raw = dResp;
                const bboxes = Array.isArray(dResp.bboxes) ? dResp.bboxes : [];
                // 选择面积最接近滔块宽平方的候选（缺口与滔块大小接近）
                if (bboxes.length) {
                    const ideal = sliderWidth * sliderWidth;
                    let best = null; let bestDiff = Infinity;
                    for (const b of bboxes) {
                        if (!Array.isArray(b) || b.length < 4) continue;
                        const w = b[2] - b[0];
                        const h = b[3] - b[1];
                        if (w <= 0 || h <= 0) continue;
                        const diff = Math.abs(w * h - ideal) + Math.abs(w - sliderWidth) * 5;
                        if (diff < bestDiff) { bestDiff = diff; best = b; }
                    }
                    if (best) {
                        const gapCenterX = (best[0] + best[2]) / 2;
                        recognition = gapCenterX - sliderWidth / 2;
                    }
                }
            }
            // fallback: slide_match
            if (recognition == null || !Number.isFinite(recognition)) {
                const sResp = await postOnce('/slide_match', {
                    target_image: input.sliderBase64 || '',
                    background_image: input.bgBase64,
                    simple_target: simpleTarget,
                    flag: false,
                });
                raw = sResp;
                const result = sResp.result && typeof sResp.result === 'object' ? sResp.result : sResp;
                const target = result && Array.isArray(result.target) ? result.target : null;
                if (target && target.length >= 4) {
                    recognition = (Number(target[0]) + Number(target[2])) / 2 - sliderWidth / 2;
                } else if (Number.isFinite(Number(result && result.target_x))) {
                    recognition = Number(result.target_x);
                } else if (Number.isFinite(Number(sResp.target_x))) {
                    recognition = Number(sResp.target_x);
                }
            }
            if (recognition == null || !Number.isFinite(recognition)) {
                const e = new Error('[ddddocr biz] 无法解析缺口位置: ' + JSON.stringify(raw).slice(0, 200));
                e.ddddocrBiz = true;
                throw e;
            }
            recognition = Math.max(0, Math.round(recognition));
            return { code: 0, recognition, raw };
        } catch (err) {
            if (err && err.ddddocrBiz) throw err;
            if (netRetryLeft > 0) {
                netRetryLeft--;
                await new Promise((res) => setTimeout(res, 200 + Math.random() * 800));
                continue;
            }
            const e = new Error('[ddddocr net] ' + (err && err.message || err));
            e.ddddocrNet = true;
            throw e;
        }
    }
}

// ============= JFBYM track client =============

const DEFAULT_JFBYM_TRACK_ENDPOINT = 'https://api.jfbym.com/api/YmServer/customApi';
const DEFAULT_JFBYM_TRACK_TYPE = '66';
const DEFAULT_JFBYM_TRACK_TIMEOUT_MS = 10000;
const DEFAULT_JFBYM_TRACK_BEGIN_POINT = '8,0';
const DEFAULT_JFBYM_TRACK_INTERVAL_MS = 16;

function formatPointForJfbym(point) {
    if (Array.isArray(point) && point.length >= 2) return `${Math.round(Number(point[0]))},${Math.round(Number(point[1]))}`;
    const s = String(point == null ? '' : point).trim();
    if (!s) return '';
    const parts = s.split(',').map((v) => Number(String(v).trim()));
    if (parts.length >= 2 && Number.isFinite(parts[0]) && Number.isFinite(parts[1])) {
        return `${Math.round(parts[0])},${Math.round(parts[1])}`;
    }
    return s;
}

function numberToParam(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value);
    return Number.isInteger(n) ? String(n) : String(Number(n.toFixed(3)));
}

function normalizeJfbymTrackData(rawData, config) {
    const intervalMs = Number(config && config.intervalMs) > 0 ? Number(config.intervalMs) : DEFAULT_JFBYM_TRACK_INTERVAL_MS;
    const startTimeMs = Number(config && config.startTimeMs) >= 0 ? Number(config.startTimeMs) : intervalMs;
    let raw = rawData;
    if (typeof raw === 'string') {
        const trimmed = raw.trim();
        if (!trimmed) return [];
        if (trimmed.startsWith('[')) {
            try { raw = JSON.parse(trimmed); } catch (_) { raw = trimmed; }
        }
        if (typeof raw === 'string') {
            raw = trimmed.split(/[|;\n]+/).map((seg) => seg.split(',').map((v) => Number(String(v).trim())));
        }
    }
    if (!Array.isArray(raw)) return [];
    return raw
        .map((point, index) => {
            if (!Array.isArray(point) || point.length < 2) return null;
            const x = Number(point[0]);
            const y = Number(point[1]);
            if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
            const tRaw = point.length >= 3 ? Number(point[2]) : startTimeMs + index * intervalMs;
            const flagRaw = point.length >= 4 ? Number(point[3]) : 1;
            const t = Number.isFinite(tRaw) ? tRaw : startTimeMs + index * intervalMs;
            const flag = Number.isFinite(flagRaw) ? flagRaw : 1;
            return [Math.round(x), Math.round(y), Math.round(t), flag];
        })
        .filter(Boolean);
}

function fitJfbymTrackForYidun(track, config) {
    config = config || {};
    if (config.fitForYidun !== true) return track;
    if (!Array.isArray(track) || track.length === 0) return track;
    const timeScale = Number(config.timeScale || config.yidunTimeScale || 2.5);
    const minDurationMs = Number(config.minDurationMs || config.yidunMinDurationMs || 1200);
    const tailIntervalMs = Number(config.tailIntervalMs || config.yidunTailIntervalMs || 32);
    const startTimeMs = Number(config.startTimeMs || config.yidunStartTimeMs || 47);
    const maxTailPoints = Number(config.maxTailPoints || config.yidunMaxTailPoints || 40);
    const safeScale = Number.isFinite(timeScale) && timeScale > 0 ? timeScale : 2.5;
    const safeMinDuration = Number.isFinite(minDurationMs) && minDurationMs > 0 ? minDurationMs : 1200;
    const safeTailInterval = Number.isFinite(tailIntervalMs) && tailIntervalMs > 0 ? tailIntervalMs : 32;
    const safeStartTime = Number.isFinite(startTimeMs) && startTimeMs >= 0 ? startTimeMs : 47;
    const safeMaxTailPoints = Number.isFinite(maxTailPoints) && maxTailPoints >= 0 ? Math.floor(maxTailPoints) : 40;

    const first = track[0];
    const firstY = Number(first && first[1]) || 0;
    const firstT = Number(first && first[2]);
    const baseT = Number.isFinite(firstT) ? firstT : DEFAULT_JFBYM_TRACK_INTERVAL_MS;
    const fitted = track.map((point, index) => {
        const x = Number(point[0]);
        const y = Number(point[1]);
        const t = Number(point[2]);
        const flag = point.length >= 4 ? point[3] : 1;
        const relT = Number.isFinite(t) ? t - baseT : index * DEFAULT_JFBYM_TRACK_INTERVAL_MS;
        return [
            Math.round(Number.isFinite(x) ? x : 0),
            Math.max(0, Math.round((Number.isFinite(y) ? y : firstY) - firstY)),
            Math.round(safeStartTime + relT * safeScale),
            flag == null ? 1 : flag,
        ];
    });
    for (let i = 1; i < fitted.length; i++) {
        if (fitted[i][2] <= fitted[i - 1][2]) fitted[i][2] = fitted[i - 1][2] + 1;
    }
    let tailAdded = 0;
    while (fitted[fitted.length - 1][2] < safeMinDuration && tailAdded < safeMaxTailPoints) {
        const last = fitted[fitted.length - 1];
        fitted.push([last[0], last[1], last[2] + safeTailInterval, last[3] == null ? 1 : last[3]]);
        tailAdded += 1;
    }
    return fitted;
}

function extractDistanceX(value) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string') {
        const m = value.match(/-?\d+(?:\.\d+)?/);
        if (!m) return null;
        const n = Number(m[0]);
        return Number.isFinite(n) ? n : null;
    }
    if (value && typeof value === 'object' && !Array.isArray(value)) {
        for (const key of ['targetX', 'x', 'slideX', 'recognition', 'left']) {
            if (key in value) {
                const n = extractDistanceX(value[key]);
                if (n !== null) return n;
            }
        }
    }
    return null;
}

const RECOGNITION_X_KEYS = ['recognition', 'targetX', 'slideX', 'x', 'left'];

function adjustXNumber(value, offset) {
    const n = Number(value);
    if (!Number.isFinite(n)) return value;
    const adjusted = Math.max(0, Math.round(n - offset));
    return adjusted;
}

function adjustRecognitionStringForSliderOffset(value, offset) {
    const trimmed = String(value).trim();
    if (!trimmed || !Number.isFinite(offset) || offset <= 0) return value;
    if (trimmed.startsWith('{')) {
        try { return JSON.stringify(adjustRecognitionForSliderOffset(JSON.parse(trimmed), offset)); } catch (_) { return value; }
    }
    const sep = trimmed.includes('|') ? '|' : (trimmed.includes(';') ? ';' : null);
    const adjustSegment = (seg) => {
        const parts = seg.split(',');
        const x = Number(String(parts[0]).trim());
        if (!Number.isFinite(x)) return seg;
        parts[0] = String(adjustXNumber(x, offset));
        return parts.join(',');
    };
    if (sep) return trimmed.split(sep).map(adjustSegment).join(sep);
    if (trimmed.includes(',')) return adjustSegment(trimmed);
    const n = Number(trimmed);
    return Number.isFinite(n) ? String(adjustXNumber(n, offset)) : value;
}

function adjustRecognitionForSliderOffset(raw, offset) {
    if (!Number.isFinite(offset) || offset <= 0 || raw == null) return raw;
    if (typeof raw === 'number') return adjustXNumber(raw, offset);
    if (typeof raw === 'string') return adjustRecognitionStringForSliderOffset(raw, offset);
    if (Array.isArray(raw)) {
        return raw.map((point) => {
            if (!Array.isArray(point) || point.length < 1) return point;
            const out = point.slice();
            out[0] = adjustXNumber(out[0], offset);
            return out;
        });
    }
    if (raw && typeof raw === 'object') {
        if ('d' in raw || 'p' in raw) return raw;
        const out = Object.assign({}, raw);
        let target = null;
        for (const key of RECOGNITION_X_KEYS) {
            if (!(key in out)) continue;
            out[key] = adjustRecognitionForSliderOffset(out[key], offset);
            const n = extractDistanceX(out[key]);
            if (target == null && n != null) target = n;
        }
        if (!('targetX' in out) && target != null) out.targetX = target;
        return out;
    }
    return raw;
}

async function jfbymTrackSolve(input, config) {
    config = config || {};
    if (!config.enabled) throw new Error('[jfbym track] 未启用');
    const token = String(config.token || '').trim();
    if (!token) throw new Error('[jfbym track] 缺少 token');
    const endpoint = (config.endpoint || DEFAULT_JFBYM_TRACK_ENDPOINT).trim() || DEFAULT_JFBYM_TRACK_ENDPOINT;
    const timeoutMs = config.timeoutMs || DEFAULT_JFBYM_TRACK_TIMEOUT_MS;
    const beginPoint = formatPointForJfbym(input.beginPoint || input.begin_point || config.beginPoint || config.begin_point || DEFAULT_JFBYM_TRACK_BEGIN_POINT);
    const endPoint = formatPointForJfbym(input.endPoint || input.end_point || config.endPoint || config.end_point || '');
    const distanceX = input.distanceX != null ? input.distanceX : input.distance_x;
    if (!beginPoint) throw new Error('[jfbym track] 缺少 begin_point');
    if (!endPoint && distanceX == null) throw new Error('[jfbym track] end_point 和 distance_x 必须二选一');

    const payload = {
        token,
        type: String(config.type || DEFAULT_JFBYM_TRACK_TYPE),
        begin_point: beginPoint,
    };
    if (endPoint) payload.end_point = endPoint;
    else payload.distance_x = numberToParam(distanceX);

    let netRetryLeft = 1;
    while (true) {
        try {
            const r = await postJson(endpoint, payload, timeoutMs, { 'User-Agent': 'yidun-sdk/jfbym-track' });
            if (r.status >= 500) throw new Error(`[jfbym track] HTTP ${r.status}`);
            let parsed;
            try { parsed = JSON.parse(r.bodyText); } catch (_) { throw new Error('[jfbym track] 非 JSON 响应: ' + r.bodyText.slice(0, 200)); }
            const nested = parsed && parsed.data && typeof parsed.data === 'object' ? parsed.data : {};
            if (parsed.code !== 10000 || (nested.code != null && nested.code !== 0)) {
                const e = new Error(`[jfbym track biz code=${parsed.code} subCode=${nested.code}] ${parsed.msg || nested.msg || ''}`);
                e.jfbymTrackBiz = true;
                e.bizCode = parsed.code;
                e.subCode = nested.code;
                throw e;
            }
            const track = normalizeJfbymTrackData(nested.data, config);
            if (!track.length) {
                const e = new Error('[jfbym track biz] 无法解析轨迹: ' + JSON.stringify(nested.data).slice(0, 200));
                e.jfbymTrackBiz = true;
                throw e;
            }
            return {
                code: parsed.code,
                recognition: distanceX == null ? null : Number(distanceX),
                track,
                uniqueCode: nested.unique_code || nested.uniqueCode,
                raw: parsed,
            };
        } catch (err) {
            if (err && err.jfbymTrackBiz) throw err;
            if (netRetryLeft > 0) {
                netRetryLeft--;
                await new Promise((res) => setTimeout(res, 200 + Math.random() * 800));
                continue;
            }
            const e = new Error('[jfbym track net] ' + (err && err.message || err));
            e.jfbymTrackNet = true;
            throw e;
        }
    }
}

// ============= 点选验证码数据封装 =============

const CLICK_SELECT_TYPES = new Set([7, '7', 10, '10']);
const DEFAULT_BINGTOP_CLICK_CAPTCHA_TYPE = 2316;

function generateClickTrace(clickPoints) {
    const trace = [];
    let t = 0;
    let cx = Math.floor(Math.random() * 50) + 10;
    let cy = Math.floor(Math.random() * 50) + 10;

    for (let i = 0; i < clickPoints.length; i++) {
        const target = clickPoints[i];
        const dx = target.x - cx;
        const dy = target.y - cy;
        const moveSteps = 20 + Math.floor(Math.random() * 20);
        for (let s = 1; s <= moveSteps; s++) {
            const progress = s / moveSteps;
            const ease = 1 - Math.pow(1 - progress, 2);
            const mx = Math.round(cx + dx * ease + (Math.random() - 0.5) * 6);
            const my = Math.round(cy + dy * ease + (Math.random() - 0.5) * 6);
            t += 8 + Math.floor(Math.random() * 20);
            trace.push([mx, my, t]);
        }
        t += 30 + Math.floor(Math.random() * 60);
        trace.push([target.x, target.y, t]);
        t += 40 + Math.floor(Math.random() * 60);
        trace.push([target.x, target.y, t]);
        cx = target.x;
        cy = target.y;
        if (i < clickPoints.length - 1) {
            t += 200 + Math.floor(Math.random() * 400);
        }
    }
    return trace;
}

function wrapClickData(opts) {
    const { clickPoints, token, sampleSize = 50 } = opts;
    const aesOpts = opts.sessionRandom ? { sessionRandom: opts.sessionRandom } : undefined;
    const trace = generateClickTrace(clickPoints);
    const sampled = sampleList(trace, Math.min(sampleSize, trace.length));
    const mRaw = sampled.map(p => xorEncode(token, `${p[0]},${p[1]},${p[2]},1`)).join(':');

    const clickTimestamps = [];
    let traceIdx = 0;
    for (let i = 0; i < clickPoints.length; i++) {
        let clickCount = 0;
        while (traceIdx < trace.length) {
            const pt = trace[traceIdx];
            traceIdx++;
            if (pt[0] === clickPoints[i].x && pt[1] === clickPoints[i].y) {
                clickCount++;
                if (clickCount === 2) {
                    clickTimestamps.push(pt[2]);
                    break;
                }
            }
        }
    }
    while (clickTimestamps.length < clickPoints.length) {
        const last = clickTimestamps.length ? clickTimestamps[clickTimestamps.length - 1] : 0;
        clickTimestamps.push(last + 500 + Math.floor(Math.random() * 300));
    }

    const pValue = clickPoints.map((p, i) => xorEncode(token, `${p.x},${p.y},${clickTimestamps[i]}`)).join(':');
    return {
        d: '',
        m: aesEncrypt(mRaw, aesOpts),
        p: aesEncrypt(pValue, aesOpts),
        ext: aesEncrypt(xorEncode(token, `${clickPoints.length},${trace.length}`), aesOpts),
    };
}

function parseClickRecognition(recognition, scale) {
    scale = scale || 1;
    const raw = String(recognition || '').trim();
    if (!raw) return [];
    return raw.split('|').map(pair => {
        const [x, y] = pair.split(',').map(Number);
        return { x: Math.round(x * scale), y: Math.round(y * scale) };
    }).filter(p => Number.isFinite(p.x) && Number.isFinite(p.y));
}

// ============= bingtop client =============

async function bingtopSolve(input, config) {
    const username = (config.username || '').trim();
    const password = (config.password || '').trim();
    if (!username || !password) throw new Error('[bingtop] 缺少账号密码');
    const endpoint = (config.endpoint || DEFAULT_BINGTOP_ENDPOINT).trim() || DEFAULT_BINGTOP_ENDPOINT;
    const captchaType = config.captchaType || DEFAULT_BINGTOP_CAPTCHA_TYPE;
    const timeoutMs = config.timeoutMs || DEFAULT_BINGTOP_TIMEOUT_MS;
    let netRetryLeft = 1;
    while (true) {
        try {
            const r = await postForm(endpoint, {
                username, password, captchaType,
                captchaData: input.bgBase64,
                subCaptchaData: input.sliderBase64 || '',
            }, timeoutMs);
            if (r.status >= 500 && netRetryLeft > 0) { netRetryLeft--; await new Promise((res) => setTimeout(res, 200 + Math.random() * 1300)); continue; }
            let parsed;
            try { parsed = JSON.parse(r.bodyText); } catch (_) { throw new Error('[bingtop] 非 JSON 响应: ' + r.bodyText.slice(0, 200)); }
            if (parsed.code !== 0) {
                const e = new Error(`[bingtop biz code=${parsed.code}] ${parsed.message || ''}`);
                e.bizCode = parsed.code; e.bingtopBiz = true;
                throw e;
            }
            return { code: parsed.code, captchaId: parsed.data && parsed.data.captchaId, recognition: parsed.data && parsed.data.recognition, raw: parsed };
        } catch (err) {
            if (err && err.bingtopBiz) throw err;
            if (netRetryLeft > 0) { netRetryLeft--; await new Promise((res) => setTimeout(res, 200 + Math.random() * 1300)); continue; }
            const e = new Error('[bingtop net] ' + (err && err.message || err));
            e.bingtopNet = true;
            throw e;
        }
    }
}

// ============= solver =============

const DEFAULT_API_HOST = 'c.dun.163.com';
const DEFAULT_IR_HOST = 'ir-sdk.dun.163.com';
const DEFAULT_VERSION = '2.28.5';
const DEFAULT_LOAD_VERSION = '2.5.4';
const DEFAULT_RUN_ENV = 10;
const DEFAULT_IV = 4;
const DEFAULT_GETCONF_IV = 5;
const DEFAULT_WIDTH = 362;
const DEFAULT_DPR = 2.549999952316284;
const DEFAULT_DEV = 2;
const DEFAULT_IR_VERSION = '2.0.13_yanzhengma';
const DEFAULT_IR_VERSION_KEY = 'd44593ca';
const DEFAULT_IR_PN = 'YD00192283058223';
const MIN_RETRY_INTERVAL_MS = 3000;
const DEFAULT_IR_DATA = [
    'aEBhQ.5eZMu4AO52Wh00jEDChUSKoiMxlyD3+Z6gf014pfoOwMIN1i80TGnRS+kf9WR9rE36.8VmoUHwxz1So4LLqeSvHFCiqkQR',
    'SQsXIkQ48GKOrSqJkbA+kiX0C9xIDIdHqEd/UNXarYisfYBGOfj8K95knkAeZiAT64D5v3KX+lpJCTqQ6EZuYiuFB6q+5M1ptYXw',
    'GRjegVEEAKJHJ+49Qs3B1UWRh5hoFuNdlTFNWqIvvhbs1QqBgivwXDgkvigIR6ao6d1dyGyTKAIIMc/va/FocEsd5lL09T0PG1k4',
    'GnD.yHY2cJL+gbjcSKS/cy.LqPn5jZkXMB1x1a9b8sQHxWG/xmggC5Hrx4/iJCp+RkCBAUfwQFp8ABpVJQQM.blVBSf9idMKyu1.',
    'kDKTzj+UjVHloSAeQuQno+WvsTVcTSj55KQEFrOD8MhK4pk.aMgzPr5B0sqQvlpevjiIc/brXjo+XgdV1yal2iWpHUcDbCitcKb+',
    'OprU8WT2Z1tD/PCzL8QO1GGg.aIer3lrY8ClbQKOS.cOYKOopNkDcPmvGV1EKQgfJSzGwtZ/bU10MxwMwRv.quODnhgk+xUFFdc/',
    'iNTzOHl.nC46g.rbAmfL1mKIU2Lz6ISUWLXQi/yR3SfbJ4wiejUzAZEXBH5BtsL+KH.nSf8I0TiNegHAoSqYPt.bXAtCmOi+uRZV',
    '5e2IVT4Wc/2xjEHkiggEnCdyDp+vmJGXyI6DKoKvp/UZBx1Ju9T0DL2vLYSEz.QGZYQKRdAreNoweGhyucc+.Yl6XxA+yCJuiZEJ',
    'BrZmMBjp9.lUfHr1vtklnF+1BQM8umnUavwzkpdOKs3GKNRHMa+EMCBFhoCAPf.aLxDftlf8/0RXcFLAvAK5DdVdVnLkPTP+/fSl',
    'n/JJFiDDtd2awTLMD4QllwT0AF9I9tXvP3qT8wRjIQGSquDUN0cF/6PhVWjqVPwXvwU5MdK/SdLO8rqhxg.FB3cdBuaRF+1uWhz5',
    'W03t5TZBeEk6G0GKW5wX+3hfuTid46Z5OVVYgWC9Jo/rhaCZO4g26E6ckqq1kQ0OJPb/NejAOBhib5+/l0.vv.Xx2ACteSXEUpRu',
    'BUy1Cg0SezN++Ecrt9y/JdZN5bkonS4GRJ6s22K5fLZP1xZlQxonYTeK66Akkh13e5/vKYKEblLyC/XYfCccXQzKF+MynHoaW9Mv',
    '/laUuwnCFgw9S.sE+ILHoxYwmXl/ysgMQnxf63rshD3bLk092+tZ.e1oRTmf.p1pXST50mLzIQCbjdVfXkR.+jX55k8mqIiv0UQ0',
    'DcqvY8Ml40d4dLcAdOtfRwm2JwSXrG9KI6cOHpgL/pwXgfoWkHmoaF+v6XohjL5ccIovKUso08Tddovl3hP0M0RlRncaJcCTgW5n',
    'aGdouLNe2yVj8.DgRHEvZFh985mElWX0+qLI9WFCz6dEXIsJOCatJiXYxtRPWZqo9zQYK4qkITdB6pvXYxBUYNsosKdlowbrj36P',
    'DTzlwjTAduvYYFbTcyBP1bRRb8tKG9yf4hrl0lFeMF+LWqadxksP3nDsEOhdYUy3XAYsPzSCjkZTWKfJQGXxAwQlhMO+q9IPmgo4',
    'rPznbu9fAdGiy1M40E6AZC9BKus.MCovmHtbpFlp9MbGpKghBpozQDs3w1jb3F8Atf0AO2LbosXiIjlW1NunnbSsSdZt0ZlfK9cL',
    'Eu51FxY8Te3MT2Eax0uAdc0DBp8cW/5WHWh5KTNKdPM1WnknKmCvBUcXaqd1NoaMasOg11iRKUg/Noe4n8..O+If4j2MeP.kM06F',
    'h4BmakxcyCIMEzbp5xrpjJLqnTIU1Wu+2EWrLbXr5JIH2r1Y.ZFvCxrjBCpKDdCLsBk/E+zBl4mna4ZhywML3DPIRmMnX0g.+frf',
    'F2QECHuIe3/8jpdgNptK5AxKkl9CnU0gRLxs9XCuU.YaV/plUMUW6+.hgFJmxc8T2CborpdporCtKN8y4r4jEk+BCXgghoS/DQ3U',
    'y3ZMsHCeM1/4KEeYrFQruchFD/wD+OGjQT/WJVIAcBe+4gvnqZx8qlqGRZza/ESGkxJ6EvZq2RCu51uz6ITPCcax5kmwQHkWCWkf',
    'Mv4MyEF.vVV88ZrA8XsOC5omeZy0FMWRNi5hE+N2steC.dtdLCuOy9I5gKlh9Ys9VyMxvFZmY46Tq/UnDDwBCYxXGSC2SGIFLO6l',
    'VWGxgHEI5q3EuA5A+wN82Z+M8WX/WZ/ofQVJarxSwDCoaru29BxFo.xzcOCdJby6XY0VHiqTxlp4.Md9XbIl5EN2tUegYIb6WScc',
    '2oQSY28YIDdbxjImBS6mYHQhgs8jtuclYeg4SvqP9ov5M4X6ATmj9VZE6x1./NszvNhC0KYPVCfGBX+ncR+gEXiVDiXXPFk.raoD',
    '6i.8ZNMa1zQQHb0KaEBg8r6P1MMRUW0eiUL2eBXQC/zKjPjpW64pWb4VV8HnlCxBJSAZzvESOB/ayoE8'
].join('');

function buildQuery(obj) {
    return Object.entries(obj).filter(([, v]) => v !== undefined && v !== null)
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join('&');
}
function randomHex(n, rfn) {
    rfn = rfn || Math.random; const a = '0123456789abcdef'; let o = '';
    for (let i = 0; i < n; i++) o += a[Math.floor(rfn() * 16)];
    return o;
}

async function solveYidunCaptchaWithBingtop(opts) {
    const challenge = opts.challenge || opts;
    const bingtopConfig = opts.bingtop || {};
    const ddddocrConfig = opts.ddddocr || {};
    const jfbymTrackConfig = opts.jfbymTrack || opts.jfbym_track || opts.jfbym || {};
    const useDdddocr = !!ddddocrConfig.enabled;
    const useBingtop = !useDdddocr && !!bingtopConfig.enabled;
    const useJfbymTrack = !!jfbymTrackConfig.enabled;
    const logger = opts.logger || (() => undefined);
    const maxAttempts = Math.max(1, opts.maxAttempts || 5);
    const fallbackDt = opts.dt || '';
    const httpTimeout = opts.httpTimeoutMs || 8000;
    const refererQuery = (challenge.referer || 'https://h5.moutai519.com.cn/').trim();
    // Header `Referer` 在真实抓包中固定为 origin 根，query 中的 `referer` 才是当前页面 URL
    const refererOrigin = (() => { try { const u = new URL(refererQuery); return `${u.protocol}//${u.host}`; } catch (_) { return 'https://h5.moutai519.com.cn'; } })();
    const refererHost = (() => { try { return new URL(refererQuery).host || 'h5.moutai519.com.cn'; } catch (_) { return 'h5.moutai519.com.cn'; } })();
    const refererHeader = (challenge.refererHeader || `${refererOrigin}/`).trim();
    // 默认带具体机型 + 一对稳定的 device-id / BS-DVID 占位（同一进程内复用，避免风控发现 UA 频繁变化）
    const defaultUA = 'Mozilla/5.0 (Linux; Android 14; PJE110 Build/TP1A.220905.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/117.0.0.0 Mobile Safari/537.36 moutaiapp/1.9.10'
        + ` device-id/${getStableDeviceId()} BS-DVID/${getStableBsDvid()}`;
    const userAgent = opts.userAgent || challenge.userAgent || defaultUA;
    const gdxInnerProfile = opts.gdxInnerProfile === false
        ? false
        : Object.assign({ userAgent }, opts.gdxInnerProfile || {});
    const innerFp = opts.gdxFp || opts.gdxFingerprint || process.env.YIDUN_GDX_FP || (
        gdxInnerProfile === false ? '' : buildGdxInnerFingerprintValue(gdxInnerProfile, opts.gdxInnerOptions)
    );
    const fp = opts.fingerprint || buildGdxidpyhxdEValue({
        host: refererHost,
        fp: innerFp,
        icp: opts.gdxIcp || opts.icp || process.env.YIDUN_GDX_ICP,
    });
    const yidunHeaders = Object.assign({
        Referer: refererHeader,
        Origin: refererOrigin,
        Accept: '*/*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'sec-ch-ua': '"Android WebView";v="117", "Not;A=Brand";v="8", "Chromium";v="117"',
        'sec-ch-ua-mobile': '?1',
        'sec-ch-ua-platform': '"Android"',
        'User-Agent': userAgent,
        'X-Requested-With': 'com.moutai.mall',
        'Sec-Fetch-Site': 'cross-site',
        'Sec-Fetch-Mode': 'no-cors',
        'Sec-Fetch-Dest': 'script',
    }, opts.headers || {});
    // 同样向 referer query 字段提供完整 URL（默认与 Header 一致）
    const referer = refererQuery;
    const useManual = typeof opts.manualSolve === 'function';
    if (!useDdddocr && !useBingtop && !useManual) return { success: false, message: '未启用任何打码 provider（bingtop / ddddocr / manualSolve）', attempts: 0 };

    let conf = null;
    let lastError = null;
    let lastStart = null;
    let attempts = 0;

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        attempts = attempt;
        if (lastStart !== null) {
            const sinceLast = Date.now() - lastStart;
            if (sinceLast < MIN_RETRY_INTERVAL_MS) {
                const wait = MIN_RETRY_INTERVAL_MS - sinceLast;
                logger('info', `[yidun] 距上次尝试不足 3s，等待 ${wait}ms`);
                await new Promise((r) => setTimeout(r, wait));
            }
        }
        lastStart = Date.now();
        logger('info', `[yidun] attempt ${attempt}/${maxAttempts} captchaId=${challenge.captchaId}`);

        try {
            if (!conf) {
                const cbName = generateJsonpCallback();
                const url = `https://${DEFAULT_API_HOST}/api/v2/getconf?${buildQuery({
                    referer,
                    zoneId: '',
                    dt: fallbackDt,
                    id: challenge.captchaId,
                    ipv6: false,
                    loadVersion: challenge.loadVersion || DEFAULT_LOAD_VERSION,
                    runEnv: challenge.runEnv || DEFAULT_RUN_ENV,
                    iv: DEFAULT_GETCONF_IV,
                    callback: cbName,
                })}`;
                const r = await getText(url, httpTimeout, yidunHeaders);
                if (r.status >= 400) throw new Error(`getconf status=${r.status}`);
                const parsed = parseJsonp(r.bodyText, cbName);
                if (parsed.error !== 0) throw new Error(`getconf error=${parsed.error} ${parsed.msg || ''}`);
                const data = parsed.data || {};
                conf = {
                    apiServer: Array.isArray(data.apiServer) ? data.apiServer[0] : data.apiServer,
                    zoneId: data.zoneId,
                    ac: data.ac, ir: data.ir,
                    dt: data.dt || '',
                };
            }
            const apiServer = conf.apiServer || DEFAULT_API_HOST;
            const zoneId = conf.zoneId || '';
            const dtFromConf = conf.dt || fallbackDt;
            // getconf 响应中的 ac.bid 实际上是入参 id 的回显（watchman 模块绑定参数），
            // 正常与 challenge.captchaId 一致。保留覆盖逻辑仅作防御性兼底：
            // 如果服务器下发不同值说明传入 id 是错的，遇到时警告，后续请求使用 ac.bid。
            const effectiveCaptchaId = (conf.ac && conf.ac.bid) || challenge.captchaId;
            if (effectiveCaptchaId !== challenge.captchaId) {
                logger('warn', `[yidun] getconf ac.bid=${effectiveCaptchaId} 与传入 captchaId=${challenge.captchaId} 不一致，可能传入了错误的 captchaId；正在用 ac.bid 覆盖。`);
            }
            let irToken = opts.irToken || '';
            if (!irToken && conf.ir && conf.ir.enable && !opts.skipIrisk) {
                const irServer = Array.isArray(conf.ir.apiServer) ? conf.ir.apiServer[0] : conf.ir.apiServer || DEFAULT_IR_HOST;
                const irBody = JSON.stringify({
                    p: conf.ir.pn || DEFAULT_IR_PN,
                    v: DEFAULT_IR_VERSION,
                    vk: DEFAULT_IR_VERSION_KEY,
                    n: randomHex(32),
                    d: opts.irData || DEFAULT_IR_DATA,
                });
                const irRes = await postText(`https://${irServer}/v4/j/up`, irBody, httpTimeout, Object.assign({}, yidunHeaders, {
                    'Content-Type': 'text/plain',
                    'Content-Length': Buffer.byteLength(irBody),
                }));
                if (irRes.status >= 400) throw new Error(`ir-sdk /v4/j/up status=${irRes.status}`);
                const irParsed = JSON.parse(irRes.bodyText);
                if (irParsed.code !== 200 || !irParsed.data || !irParsed.data.tk) {
                    throw new Error(`ir-sdk /v4/j/up error=${irParsed.code} ${irParsed.msg || ''}`);
                }
                irToken = irParsed.data.tk;
            }

            // /v3/get
            const cbGet = generateJsonpCallback();
            const getUrl = `https://${apiServer}/api/v3/get?${buildQuery({
                referer,
                zoneId,
                dt: dtFromConf,
                irToken,
                id: effectiveCaptchaId, fp, https: true,
                type: challenge.captchaType || '',
                version: challenge.version || DEFAULT_VERSION,
                dpr: challenge.dpr || DEFAULT_DPR, dev: challenge.dev || DEFAULT_DEV, cb: cb(), ipv6: false,
                runEnv: challenge.runEnv || DEFAULT_RUN_ENV,
                group: '', scene: '',
                lang: 'zh-CN',
                sdkVersion: '',
                loadVersion: challenge.loadVersion || DEFAULT_LOAD_VERSION,
                iv: challenge.iv || DEFAULT_IV,
                user: challenge.user || '',
                width: challenge.width || DEFAULT_WIDTH,
                audio: false,
                sizeType: challenge.sizeType || 10,
                token: '',
                callback: cbGet,
            })}`;
            const gr = await getText(getUrl, httpTimeout, yidunHeaders);
            if (gr.status >= 400) throw new Error(`/v3/get status=${gr.status}`);
            const got = parseJsonp(gr.bodyText, cbGet);
            if (got.error !== 0) throw new Error(`/v3/get error=${got.error} ${got.msg || ''}`);
            const gd = got.data || {};
            if (!gd.token || !Array.isArray(gd.bg) || !gd.bg.length) throw new Error('/v3/get 缺少 token/bg');
            const width = gd.width || challenge.width || DEFAULT_WIDTH;
            const ctype = gd.type || challenge.captchaType || '';
            const token = gd.token;

            const isClickType = CLICK_SELECT_TYPES.has(ctype);
            if (isClickType) {
                logger('info', `[yidun] /v3/get click-select type=${ctype} keys=${Object.keys(gd).join(',')} bg=${(gd.bg || []).length} front=${(gd.front || []).length} width=${width}`);
            }

            const [bg, slider] = await Promise.all([
                getBinary(gd.bg[0], httpTimeout, yidunHeaders),
                isClickType
                    ? Promise.resolve(null)
                    : gd.front && gd.front[0] ? getBinary(gd.front[0], httpTimeout, yidunHeaders) : Promise.resolve(null),
            ]);
            const bgBase64 = bg.body.toString('base64');
            const sliderBase64 = slider ? slider.body.toString('base64') : undefined;
            const bgDimensions = getImageDimensions(bg.body);
            const sliderDimensions = slider ? getImageDimensions(slider.body) : {};

            let dataStr;

            if (isClickType) {
                const clickBingtopConfig = Object.assign({}, bingtopConfig, {
                    captchaType: bingtopConfig.clickCaptchaType || DEFAULT_BINGTOP_CLICK_CAPTCHA_TYPE,
                });
                const clickSolved = typeof opts.clickSolve === 'function'
                    ? await opts.clickSolve({
                        attempt,
                        bg: bg.body,
                        bgBase64,
                        bgWidth: bgDimensions.width || null,
                        bgHeight: bgDimensions.height || null,
                        renderWidth: width,
                        token,
                    })
                    : await bingtopSolve({ bgBase64, sliderBase64: '' }, clickBingtopConfig);
                const imageToRenderScale = bgDimensions.width ? width / bgDimensions.width : 1;
                const clickPoints = Array.isArray(clickSolved && clickSolved.points)
                    ? clickSolved.points
                    : parseClickRecognition(clickSolved && clickSolved.recognition, imageToRenderScale);
                logger('info', `[yidun] click-select recognition=${JSON.stringify(clickSolved && clickSolved.recognition)} points=${clickPoints.length} scale=${imageToRenderScale.toFixed(3)} renderWidth=${width} imageWidth=${bgDimensions.width || 'unknown'}`);
                if (!clickPoints.length) throw new Error('[yidun click] 点选识别结果为空: ' + JSON.stringify(clickSolved && clickSolved.recognition));
                const wrapped = wrapClickData({ clickPoints, token });
                dataStr = JSON.stringify(wrapped);
                logger('info', `[yidun] click-select wrapped d.len=${wrapped.d.length} m.len=${wrapped.m.length} p.len=${wrapped.p.length} ext.len=${wrapped.ext.length}`);
            } else {
                const providerName = useDdddocr ? 'ddddocr' : (typeof opts.manualSolve === 'function' ? 'manual' : 'bingtop');
                let solved;
                if (typeof opts.manualSolve === 'function') {
                    solved = await opts.manualSolve({
                        attempt,
                        bg: bg.body,
                        slider: slider ? slider.body : null,
                        bgBase64,
                        sliderBase64,
                        bgWidth: bgDimensions.width || null,
                        bgHeight: bgDimensions.height || null,
                        sliderWidth: sliderDimensions.width || null,
                        sliderHeight: sliderDimensions.height || null,
                        renderWidth: width,
                        token,
                    });
                    if (!solved || !solved.recognition) throw new Error('[manual] 未返回 recognition');
                } else {
                    solved = useDdddocr
                        ? await ddddocrSolve({ bgBase64, sliderBase64, sliderWidth: sliderDimensions.width || 60 }, ddddocrConfig)
                        : await bingtopSolve({ bgBase64, sliderBase64 }, bingtopConfig);
                }
                const manualMode = providerName === 'manual';
                const providerConfig = useDdddocr ? ddddocrConfig : (providerName === 'bingtop' ? bingtopConfig : {});
                const imageToRenderScale = manualMode ? 1 : (bgDimensions.width ? width / bgDimensions.width : 1);
                const scaledRecognition = manualMode ? solved.recognition : scaleRawTrackX(solved.recognition, imageToRenderScale);
                // Bingtop 1316 返回的是滑块图应放置在背景图上的坐标；默认不要再扣 slider PNG 透明边。
                // 少数 provider 若返回的是缺口实体坐标，可显式传 adjustSliderOffset=true 启用旧修正。
                let sx0Render = 0;
                if (slider && !manualMode) {
                    const sx0Img = getPngAlphaMinX(slider.body);
                    if (sx0Img != null) sx0Render = sx0Img * imageToRenderScale;
                }
                const shouldAdjustSliderOffset = !manualMode
                    && (opts.adjustSliderOffset === true || (providerConfig && providerConfig.adjustSliderOffset === true));
                const adjustedRecognition = shouldAdjustSliderOffset
                    ? adjustRecognitionForSliderOffset(scaledRecognition, sx0Render)
                    : scaledRecognition;
                logger('info', `[yidun] ${providerName} recognition=${JSON.stringify(solved.recognition)} scaled=${JSON.stringify(scaledRecognition)} sx0Render=${sx0Render.toFixed(2)} adjustOffset=${shouldAdjustSliderOffset} adjusted=${JSON.stringify(adjustedRecognition)} imageWidth=${bgDimensions.width || 'unknown'} sliderWidth=${sliderDimensions.width || 'unknown'} renderWidth=${width}`);
                if (typeof opts.onCaptchaSample === 'function') {
                    try {
                        await opts.onCaptchaSample({
                            attempt,
                            provider: providerName,
                            bg: bg.body,
                            slider: slider ? slider.body : null,
                            bgUrl: gd.bg[0],
                            sliderUrl: gd.front && gd.front[0] || null,
                            bgWidth: bgDimensions.width || null,
                            bgHeight: bgDimensions.height || null,
                            sliderWidth: sliderDimensions.width || null,
                            sliderHeight: sliderDimensions.height || null,
                            renderWidth: width,
                            recognition: solved.recognition,
                            scaledRecognition,
                        });
                    } catch (_) { /* ignore sample hook errors */ }
                }
                let rawTrackForWrap = adjustedRecognition;
                let targetXForWrap = null;
                if (useJfbymTrack) {
                    const distanceX = extractDistanceX(adjustedRecognition);
                    if (distanceX == null || !Number.isFinite(distanceX)) {
                        const e = new Error('[jfbym track biz] 无法从识别结果提取 distance_x: ' + JSON.stringify(adjustedRecognition));
                        e.jfbymTrackBiz = true;
                        throw e;
                    }
                    const trackResult = await jfbymTrackSolve({ distanceX }, jfbymTrackConfig);
                    rawTrackForWrap = fitJfbymTrackForYidun(trackResult.track, jfbymTrackConfig);
                    targetXForWrap = distanceX;
                    logger('info', `[yidun] jfbym track points=${trackResult.track.length}->${rawTrackForWrap.length} distanceX=${distanceX} uniqueCode=${trackResult.uniqueCode || ''}`);
                }
                const wrapped = wrapTrack({
                    rawTrack: rawTrackForWrap,
                    token,
                    width,
                    targetX: targetXForWrap == null ? undefined : targetXForWrap,
                    humanize: opts.humanize !== undefined
                        ? opts.humanize === true
                        : !(opts.replay !== undefined || (opts.traceOptions && opts.traceOptions.replay !== undefined)),
                    traceOptions: opts.traceOptions,
                    replay: opts.replay,
                });
                dataStr = JSON.stringify(wrapped);
                logger('info', `[yidun] wrapped d.len=${wrapped.d.length} p.len=${wrapped.p.length} f.len=${wrapped.f.length} ext.len=${wrapped.ext.length}`);
            }

            // /v3/check
            const cbChk = generateJsonpCallback();
            let extraDataStr = '';
            if (challenge.extraData != null) {
                extraDataStr = typeof challenge.extraData === 'string'
                    ? challenge.extraData
                    : JSON.stringify(challenge.extraData);
            }
            const chkUrl = `https://${apiServer}/api/v3/check?${buildQuery({
                referer, zoneId, dt: dtFromConf,
                id: effectiveCaptchaId, token, data: dataStr, width,
                type: ctype, version: challenge.version || DEFAULT_VERSION,
                cb: cb(), user: challenge.user || '',
                extraData: extraDataStr,
                bf: 0, runEnv: challenge.runEnv || DEFAULT_RUN_ENV,
                sdkVersion: '',
                loadVersion: challenge.loadVersion || DEFAULT_LOAD_VERSION,
                iv: challenge.iv || DEFAULT_IV,
                callback: cbChk,
            })}`;
            const cr = await getText(chkUrl, httpTimeout, yidunHeaders);
            if (cr.status >= 400) throw new Error(`/v3/check status=${cr.status}`);
            logger('info', `[yidun] /v3/check raw body: ${cr.bodyText}`);
            const chk = parseJsonp(cr.bodyText, cbChk);
            if (chk.error !== 0) throw new Error(`/v3/check error=${chk.error} ${chk.msg || ''}`);
            if (!chk.data || chk.data.result !== true || !chk.data.validate) {
                throw new Error('/v3/check result=false ' + JSON.stringify(chk.data || {}));
            }
            logger('info', `[yidun] attempt ${attempt} 验证成功 validateLen=${chk.data.validate.length}`);
            const rawValidate = chk.data.validate;
            const finalToken = chk.data.token || token;
            const finalValidate = wrapValidate(rawValidate, fp, zoneId);
            return {
                success: true,
                validate: finalValidate,
                rawValidate,
                token: finalToken,
                zoneId,
                attempts: attempt,
                raw: chk,
                captchaId: effectiveCaptchaId,
                fingerprint: fp,
            };
        } catch (err) {
            lastError = err;
            let tag = 'yidun';
            if (err) {
                if (err.bingtopBiz) tag = 'bingtop-biz';
                else if (err.bingtopNet) tag = 'bingtop-net';
                else if (err.ddddocrBiz) tag = 'ddddocr-biz';
                else if (err.ddddocrNet) tag = 'ddddocr-net';
                else if (err.jfbymTrackBiz) tag = 'jfbym-track-biz';
                else if (err.jfbymTrackNet) tag = 'jfbym-track-net';
            }
            logger('warn', `[yidun] attempt ${attempt} 失败 [${tag}]: ${err && err.message || err}`);
            if (tag !== 'yidun-policy' && tag !== 'bingtop-biz' && tag !== 'ddddocr-biz' && tag !== 'jfbym-track-biz') conf = null;
        }
    }
    return { success: false, message: `易盾验证失败（${attempts} 次尝试）：${lastError && lastError.message || 'unknown'}`, attempts, fingerprint: fp };
}

module.exports = {
    aesEncrypt, xorEncode, xorDecode, base64Encode, base64Decode, base64EncodePrivate,
    crc32Hex, cb, generateJsonpCallback, parseJsonp, wrapTrack, bingtopSolve, ddddocrSolve, jfbymTrackSolve,
    privateBase64ToUrlSafe, wrapValidate,
    buildGdxidpyhxdE, buildGdxidpyhxdEValue, buildGdxPayload, encryptGdxPayload, serializeGdxPayload, decodeGdxidpyhxdE,
    hashGdxFingerprintBucket, buildGdxInnerFingerprintBuckets, buildGdxInnerFingerprint, buildGdxInnerFingerprintValue,
    solveYidunCaptchaWithBingtop,
    solveYidunCaptcha: solveYidunCaptchaWithBingtop,
    getPngAlphaMinX, getImageDimensions,
    DEFAULT_BINGTOP_ENDPOINT, DEFAULT_BINGTOP_CAPTCHA_TYPE, DEFAULT_BINGTOP_CLICK_CAPTCHA_TYPE, DEFAULT_BINGTOP_TIMEOUT_MS,
    DEFAULT_DDDDOCR_ENDPOINT, DEFAULT_DDDDOCR_TIMEOUT_MS,
    DEFAULT_JFBYM_TRACK_ENDPOINT, DEFAULT_JFBYM_TRACK_TYPE, DEFAULT_JFBYM_TRACK_TIMEOUT_MS,
    wrapClickData, parseClickRecognition, CLICK_SELECT_TYPES,
    __test__: { buildMotionFeatures, generateDenseTraceFromX, scaleRawTrackX, getImageDimensions, normalizeJfbymTrackData, fitJfbymTrackForYidun, extractDistanceX, adjustRecognitionForSliderOffset },
};
