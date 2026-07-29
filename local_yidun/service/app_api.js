const express = require("express");
const fs = require("fs");
const path = require("path");
const { randomUUID } = require("crypto");
const { Blob } = require("buffer");
const { fetch, FormData, ProxyAgent } = require("undici");
const cryptoModule = require("./crypto_module.js");
const buildPdDe3Value = require("./fp.js");
const { generateUA } = require("./ua_generator.js");
const { solveYidunCaptchaWithBingtop } = require("../../node_bridge/yidun_gdx_sdk.js");
const app = express();

function loadLocalEnvFile() {
  const envPath = path.resolve(__dirname, "..", "..", "local_proxy.env");
  if (!fs.existsSync(envPath)) return;
  const text = fs.readFileSync(envPath, "utf8");
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim();
    const value = trimmed.slice(eq + 1).trim();
    if (key && process.env[key] === undefined) process.env[key] = value;
  }
}

loadLocalEnvFile();

// const OCR_API_URL = "http://446699.xyz:9898/slide/match/file";
const OCR_API_URL = "http://192.168.8.59:9898/slide/match/file";
const CAPTCHA_CHECK_URL = "http://c.dun.163.com/api/v3/check";
const GETCONF_URL = "http://c.dun.163.com/api/v2/getconf";
const IR_UP_URL = "http://ir-sdk.dun.163.com/v4/j/up";
const CAPTCHA_GET_URL = "http://c.dun.163.com/api/v3/get";

const CAPTCHA_VERSION = "2.28.5";
const LOAD_VERSION = "2.5.4";
const CAPTCHA_RENDER_WIDTH = 328;
const CAPTCHA_DPR = "3.75";
const CAPTCHA_DEV = "2";
const IR_VERSION = "2.0.13_yanzhengma";
const IR_VK = "d44593ca";
const BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36";
const DEVICE_TOKEN_STORAGE_PATH = path.join(__dirname, ".captcha_device_token.txt");
const KEPUCHINA_FP_ARRAY = ["38974258425244", "39631003042944"];
const CAPTCHA_ID_BY_DETAIL_KIND = {
  "smsp-detail": "1e1ea52963a94c21b0babc42b2b5b910",
  "jpmt-detail": "22912abb4c484691b71f6efaf778c252",
  "1000ml-detail": "3fde6da0bf4d41c39ef99de8ba977d50",
  "mm-485-detail-group": "c2657d2cc1f248a78c5a06980923dba5",
  "mm-zx-271-detail": "c2657d2cc1f248a78c5a06980923dba5",
  "hxx-detail": "c2657d2cc1f248a78c5a06980923dba5",
  "gyft-8392-detail": "5f1aab1567f54fdd8c137afed42af465",
};
const CAPTCHA_ID_BY_ITEM_CODE = {
  "741": "1e1ea52963a94c21b0babc42b2b5b910",
  "1741": "22912abb4c484691b71f6efaf778c252",
  "483": "3fde6da0bf4d41c39ef99de8ba977d50",
  "IMTP1000229": "3fde6da0bf4d41c39ef99de8ba977d50",
  "11942": "c2657d2cc1f248a78c5a06980923dba5",
  "11945": "c2657d2cc1f248a78c5a06980923dba5",
  "11947": "c2657d2cc1f248a78c5a06980923dba5",
  "11652": "c2657d2cc1f248a78c5a06980923dba5",
  "11654": "c2657d2cc1f248a78c5a06980923dba5",
  "11894": "5f1aab1567f54fdd8c137afed42af465",
};
const IR_UP_D = `JgaYPDePT/o9DatgdURG.pvHtMK0ZPS1JSgJcuDq9ZdGz9zA+KVZfU1GmXYhHj2q6iDJhcc6ew5rFDLxRJEjYbce9XTym8r6Yce2tufO50gmxf3UQcTu6h3iKHhHumUqkC6bcdmcHKJa0zRWIBIlh0AhLMvXk5w.UPeKw+GAY4VLheJzIx1wdtRM2waimMdP4e16EEAG9XEdCbup.pd+FW8rcBpJl/VgUsKislTBnye1sci1Eo6+sN31zCg1yukyvqnv+cHK9.fzlHL0t+mGKYxzQGz6CyUiOmt4L0RVDb+UsMl5PKVoIHo0zDwqzBkpE9BaMnbojh30j.58z0b+JbCmJW.eN.1mBpuwZyR+0F2jNL2UWoTyirDfIL0Cw2v9bpwaKF3kpWAhUDEvOJjP9J8Ea/PixmyRUJSbHSHQqQV55XZQUH3KnVJNNWN1Kn9x9f.HJg239yIT+OU/.eIIGNEs4AJuozj5SmJbvJhk3.ZQptEHhCZCkggQBbDERylXdwVgOsgvj/v2kxo.I5V.mXxlNyXHDD9kU+DtyLDfwkiccSi/eZt3cW+0+VpOrucLjzh34ZsvvQgH4a+/ylp.BTCAn68113YBCRaHOnQw6JaKZTk/YMN/aRcF0nYV.qAtJBt.NrOhVXezDLBaG6hr8vYqd9YIjpP8UbUsoEj1C9RO1kMWEjMmo9lRZy/MMGdaclm4U99N+6gZDEgkRpEhKXtLBew5EAUoMILmulUVYl5pt+WPdlz1uteIcGLR9C/lz9PKy.kUD1DBKiEFtasHPrh.VxqDeJZQmhCBygKKRDm.R.QbU0I36rwqEr53Lm92E1TWHkqkwUYeAdXsYzlu6EU8ulNvdRkAY5OoH6RftU8wYrMwweEOmmsJe4ALmPJs.sISqzalyfNkUc4/Ua5df9M.0BL/uBi9Jh6F3c.gfBVvjNc+ASt6QNhk62Gy9kRPDSyg8EF82ymG1xI24rnMigBYBjBcUKvOCq0YDuud5WEQpWkLFkTZ3kZo9p3WcsXaAAgVe2ecMlYacVP4RR66aoyvWph3QFmvM/4finNu+hhUc46H5bsKda4K0jUsKr12aI9ws6.AjEod4NfZGCCnm9sLGBOdPQObCNcuDukACnvY3usLFurwBKo5iXzANq6Xz3FRTYzc+QUsSFTOu9rVIB5mLkjcL8YTo0.t9KUTKhQV8UCxOqv3n9sYi3Hzeq1QqipprZPDmh6yjDoTkO6SFBXL2PvC3fi2YLpSf+KTTag44iIyoMpuCcqwBJdgI0.gXeHFv2SI109ix65ds0FH6qA/ZawFFJUUh4YyF8/Np4wu5Wo+5JpQf/oCzXiJVqym9rNcRbIjGRkV4chEfwlsu/MEMDK6xiO6WKgoIHohS9bgex9xFNHV64hn5T8Vm33dK5X4U1wOmBkwDgftuAXJGQQ8CIG6uztotlFkUdUehTm1BXVFlLqii3Ev/hVoHU3Etiwh5UGgG1H0vfNwfnL48YTXDjzr6CLypwnh2K5Bt5a5HZ9E9ddAKD5vvSm1BCzq6by9QZ.2Y/RlxtuH6+mxMwMP9653nBx+C8P2iYxnLivdCu/.txUGgxXQwtYH1Mibt3gSAws8XKzFIrLp9lelFquLnjVY.mPMoKQ1WrhEK5p0unBTT4h08zvIPMDe+9btiosoajwNkrHBajSm+8EAPe42c4yckztwd4kWbvw3LKh3.gc/b6qxGQzABoqQ0WbFhzO690C1AMWijVAGY1uzayIdhJF+1Ureei/QVAs8ZiI5BF33Zwe4SiNd8h5om1zN4xXvL8EyBtSVw0vXCvvmniq+VYKA+aGzLREAi61dnJm4S5XgBKCkscYiRyi6ictGXwpMhKrDwgplYqdrXML2pBK6QZLyRSgMvsFiJpATdp8poMJfVSDB2DgsvEjcy3aoXSHBU2Q4fLGAJ./v5NFvQVH2jsN901EO0FFAMqiUQPTiOICrgbUnPRIz1MIkdTm2jIeO5UtLLxHD+vETYAmTRaKyFv9MYe05n9lI0FDtT4sBIzr4Mrrb2OUuqHhk8nbO5SIJdm9Ul81jy/DBsfdSBeSr/5M9Pq.2YYSrxj/TIw1ldtoVlHY4MLrNRYAYeo+lmiQaJe2ojgONrEo/RV/lADIVOJg7`;

// 代理配置：
// 1) POST /api/verify 传 proxyUrl 时优先使用；
// 2) 未传 proxyUrl 时读取环境变量 YIDUN_PROXY_URL 或 PROXY_URL；
// 3) proxyUrl 传空字符串 "" 表示本次请求强制直连。
// 注意：本机/内网地址（127.0.0.1、localhost、192.168.*、10.*、172.16-31.*）会自动直连，避免 OCR 内网接口被错误发到代理里。
const PROXY_URL = normalizeProxyUrl(process.env.YIDUN_PROXY_URL || process.env.PROXY_URL || "");
const PROXY_DEBUG = String(process.env.YIDUN_PROXY_DEBUG || "1") !== "0";
const _proxyDispatcherCache = new Map();
const _proxyFetchLogCache = new Set();

function buildNewSession() {
  const { ua, deviceId, bsDvid, parts } = generateUA();
  return { ua, deviceId, bsDvid, parts, dt: "" };
}

function acquireSession() {
  const session = buildNewSession();
  console.log(`[UA会话] 每次新会话 | device=${session.parts.device} android=${session.parts.androidVersion} chrome=${session.parts.chromeVersion}`);
  return session;
}

// 动态生成 /v4/j/up 接口的 d 参数
function generateIrUpD(appId, version, nonce, session) {
  const fpUa = session && session.ua ? session.ua : BROWSER_USER_AGENT;
  const fpUaTrimmed = fpUa.replace(/^Mozilla\//, "");
  const chromeVersion = session && session.parts ? session.parts.chromeVersion : "114.0.0.0";
  const device = session && session.parts ? session.parts.device : "ANA-AN00";
  const androidVersion = session && session.parts ? session.parts.androidVersion : "12.0.0";
  // 1. 模拟移动端浏览器底层特征 (对应你 curl 中的 Android Chrome 114 环境)
  const features = [
    [218, 1, 0], [225, 1, 1], [252, 100, "44100,2,2,2,2,max,speakers"], [254, 1, 1],
    [253, 30, chromeVersion], [261, 1, 2], [262, 30, "0.0.0.0"],
    [263, 8, 2, 3, 0, 1, 2, 2, 2, 2], // matchMedia 特征
    [265, 400, ""], [279, 5, "22222"], [280, 1, 33], [283, 500, ""],
    [501, 1, 0], [503, 32, "22222222"], [505, 3, "222"],
    [509, 30, "function(){return[nativecode]}"], [508, 4, 0, 0], 
    [510, 15, "stun:stun.l.google.com:19302"], [511, 32, ""], [512, 1, 0], [513, 100, "茅台"],
    [700, 200, "https://h5.moutai519.com.cn/"], [713, 4, 393, 851], // Client Size
    [800, 8, "5bf1626b"], [801, 8, ""], [802, 8, "e5cd4de6"],
    [803, 8, "a4c622e1"], [804, 8, "89e7c489"], [902, 16, "5b8f6c40049ed4e0"], 
    [904, 16, "7dfd6ff2168393de"], // Audio/Math Hash
    [200, 400, fpUa],
    [201, 20, "zh-CN"], [202, 1, 24], [203, 1, 24], [206, 1, -8], // Timezone
    [207, 1, 1], [208, 1, 1], [209, 1, 1], [210, 1, 0], [211, 1, 1],
    [213, 10, "Linux arm8l"], [214, 15, "unknown"],
    [216, 16, "7320f78dfeb22a46"], [217, 16, "5a27867205423871"], // Canvas & WebGL Hash
    [223, 1, 1], [228, 1, 0], [229, 1, 0],
    [233, 400, fpUaTrimmed],
    [234, 64, "zh-CN,zh,en-US,en"], [238, 40, ""], [239, 20, ""],
    [242, 2, 393, 851, 393, 851], [243, 1, 8], [250, 1, 0], [251, 1, -1], [258, 1, 8], 
    [260, 4, 4096], [264, 1, 5], [267, 1, 24], [273, 16, "e3b0c44298fc1c14"],
    [901, 200, "Google Inc. (ARM):Mali-G77"], [506, 1, 0], [502, 4, 100, 2, -1, -1], 
    [255, 20, "UTF-8"], [257, 20, ""], [900, 16, "8a5cfdb0bb72fa19"], 
    [500, 100, "1111111111111111111"],
    [284, 400, `arm,HUAWEI_${chromeVersion},HUAWEI_${chromeVersion},true,64,${device},Android,${androidVersion}`],
    [911, 1, 0], [912, 4, 110], [913, 4, 0], [914, 100, "1,1"],
    [922, 100, ""], [963, 400, "https://h5.moutai519.com.cn/"], [964, 1, 2]
  ];

  // 2. 运行时上下文信息
  const contextInfo = [
    [2, 32, appId],
    [3, 32, ""], // trackId，初始为空
    [4, 20, version],
    [5, 32, nonce],
    [6, 16, Date.now().toString()],
    [515, 4, 105], [516, 4, 120],
    [121, 32, "init:1-gts:1"],
    [910, 400, ""], [278, 4, 110],
    [3006, 400, ""], [3007, 400, nonce],
    [971, 4, 200], [972, 4, 0]
  ];

  // 3. 事件监听统计 (防机器人的点击、移动次数，全部置0表示一加载就发包)
  const listenerInfo = [
    [110, 2, 0], [111, 2, 0], [112, 2, 0], [113, 2, 0], [114, 2, 0], [115, 2, 0],
    [116, 2, 0], [117, 2, 0], [118, 2, 0], [119, 2, 0], [120, 2, 0], [967, 2, 0], [968, 2, 0]
  ];

  const chunks = [...features, ...contextInfo, ...listenerInfo];

  // 4. 洗牌算法：打乱所有指纹模块的顺序 (彻底防御静态重放，保证每次密文不重样)
  for (let i = chunks.length; i;) {
    const r = Math.floor(Math.random() * i--);
    const temp = chunks[i];
    chunks[i] = chunks[r];
    chunks[r] = temp;
  }

  // 5. 拍平数组并转为逗号拼接的字符串
  const plainText = chunks.reduce((acc, val) => acc.concat(val), []).join(",");

  // 6. 调用现成的加密逻辑，一键生成 D
  return cryptoModule.encryptWithAes(plainText);
}

function normalizeProxyUrl(proxyUrl) {
  if (proxyUrl === undefined || proxyUrl === null) return "";
  let value = String(proxyUrl).trim();
  if (!value) return "";
  // 兼容 127.0.0.1:7897 这种写法，默认补 http://
  if (!/^[a-zA-Z][a-zA-Z\d+.-]*:\/\//.test(value)) value = `http://${value}`;
  return value;
}

function maskProxyUrl(proxyUrl) {
  const normalized = normalizeProxyUrl(proxyUrl);
  if (!normalized) return "未启用";
  try {
    const urlObj = new URL(normalized);
    if (urlObj.username) urlObj.username = "***";
    if (urlObj.password) urlObj.password = "***";
    return urlObj.toString();
  } catch (e) {
    return normalized.replace(/:\/\/([^:@/]+):([^@/]+)@/, "://***:***@");
  }
}

function getProxyUsageInfo(proxyUrlOverride) {
  if (proxyUrlOverride !== undefined) {
    const requestProxyUrl = normalizeProxyUrl(proxyUrlOverride);
    return {
      enabled: Boolean(requestProxyUrl),
      url: requestProxyUrl,
      source: requestProxyUrl ? "请求参数proxyUrl" : "请求参数proxyUrl为空，强制直连",
      label: requestProxyUrl ? maskProxyUrl(requestProxyUrl) : "未启用",
    };
  }
  return {
    enabled: Boolean(PROXY_URL),
    url: PROXY_URL,
    source: PROXY_URL ? "环境变量YIDUN_PROXY_URL/PROXY_URL" : "未配置",
    label: PROXY_URL ? maskProxyUrl(PROXY_URL) : "未启用",
  };
}

function getProxyDispatcher(proxyUrl) {
  const normalizedProxyUrl = normalizeProxyUrl(proxyUrl);
  if (!normalizedProxyUrl) return undefined;

  if (_proxyDispatcherCache.has(normalizedProxyUrl)) return _proxyDispatcherCache.get(normalizedProxyUrl);

  try {
    const dispatcher = new ProxyAgent(normalizedProxyUrl);
    _proxyDispatcherCache.set(normalizedProxyUrl, dispatcher);
    if (PROXY_DEBUG) console.log(`[代理] ProxyAgent创建成功: ${maskProxyUrl(normalizedProxyUrl)}`);
    return dispatcher;
  } catch (e) {
    const message = `代理创建失败: ${maskProxyUrl(normalizedProxyUrl)} | ${e.message}`;
    console.error(`[代理] ${message}`);
    throw new Error(message);
  }
}

function isPrivateOrLocalUrl(requestUrl) {
  try {
    const { hostname } = new URL(requestUrl);
    const host = hostname.replace(/^\[|\]$/g, "").toLowerCase();
    if (host === "localhost" || host === "::1") return true;
    const parts = host.split(".").map(Number);
    if (parts.length !== 4 || parts.some(n => !Number.isInteger(n) || n < 0 || n > 255)) return false;
    return parts[0] === 10
      || parts[0] === 127
      || (parts[0] === 192 && parts[1] === 168)
      || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31)
      || (parts[0] === 169 && parts[1] === 254);
  } catch (e) {
    return false;
  }
}

function fetchWithProxy(url, options = {}, proxyUrlOverride) {
  const bypassProxy = isPrivateOrLocalUrl(url);
  const proxyInfo = bypassProxy
    ? { enabled: false, url: "", source: "本机/内网地址自动直连", label: "未启用" }
    : getProxyUsageInfo(proxyUrlOverride);
  const dispatcher = proxyInfo.enabled ? getProxyDispatcher(proxyInfo.url) : undefined;

  if (PROXY_DEBUG) {
    try {
      const target = new URL(url).host;
      const logKey = `${target}|${proxyInfo.enabled ? proxyInfo.url : "DIRECT"}`;
      if (!_proxyFetchLogCache.has(logKey)) {
        _proxyFetchLogCache.add(logKey);
        console.log(`[代理] 出站请求 ${target} | ${proxyInfo.enabled ? "走代理" : "直连"} | ${proxyInfo.label} | 来源: ${proxyInfo.source}`);
      }
    } catch (e) {}
  }

  const requestPromise = dispatcher ? fetch(url, { ...options, dispatcher }) : fetch(url, options);
  return requestPromise.catch((e) => {
    let target = String(url);
    try { target = new URL(url).host; } catch (err) {}
    const prefix = proxyInfo.enabled
      ? `代理请求失败 | 目标: ${target} | 代理: ${proxyInfo.label}`
      : `直连请求失败 | 目标: ${target}`;
    const wrappedError = new Error(`${prefix} | ${e.message}`);
    wrappedError.cause = e;
    throw wrappedError;
  });
}


// --- 工具函数保持不变 ---
function getFilenameFromUrl(url, fallbackName) {
  try {
    return new URL(url).pathname.split("/").pop() || fallbackName;
  } catch (error) {
    return fallbackName;
  }
}

async function fetchImageAsBlob(imageUrl, fallbackName, proxyUrl) {
  if (!imageUrl || typeof imageUrl !== "string") {
    throw new Error(`图片地址为空: ${fallbackName}`);
  }
  const response = await fetchWithProxy(imageUrl, {}, proxyUrl);
  if (!response.ok) throw new Error(`图片下载失败 (${response.status}): ${imageUrl}`);
  const contentType = response.headers.get("content-type") || "application/octet-stream";
  const bytes = Buffer.from(await response.arrayBuffer());
  return {
    blob: new Blob([bytes], { type: contentType }),
    filename: getFilenameFromUrl(imageUrl, fallbackName),
  };
}

function parseOcrResponseText(responseText) {
  try { return JSON.parse(responseText); } catch (e) {}
  try { return JSON.parse(responseText.replace(/'/g, '"')); } catch (e) {}
  try { return Function(`"use strict"; return (${responseText});`)(); } catch (e) {
    throw new Error(`OCR 接口返回内容无法解析`);
  }
}

function randomString(length) {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  return Array.from({ length }, () => chars[Math.floor(Math.random() * chars.length)]).join('');
}

function generateJsonpCallback(suffix) {
  return `__JSONP_${randomString(7)}_${suffix}`;
}

function parseJsonpResponse(responseText) {
  const trimmedText = responseText.trim();
  try { return JSON.parse(trimmedText); } catch (e) {}
  const match = trimmedText.match(/^[^(]+\(([\s\S]+)\)\s*;?\s*$/);
  if (!match) throw new Error(`无法解析 JSONP 响应`);
  return JSON.parse(match[1]);
}

function buildUrlWithParams(baseUrl, params) {
  const url = new URL(baseUrl);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
  });
  return url.toString();
}

function normalizeCaptchaDataPayload(dataPayload) {
  if (dataPayload == null) return "";
  if (typeof dataPayload === "string") {
    try {
      return JSON.stringify(JSON.parse(dataPayload.trim()));
    } catch (e) {
      return dataPayload.trim().split(/\r?\n/).map(line => line.trim()).filter(Boolean).join("");
    }
  }
  return JSON.stringify(dataPayload);
}

function createUuid32() {
  return randomUUID().replace(/-/g, "");
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, Math.max(0, Number(ms) || 0)));
}

function detailKindFromPageUrl(pageUrl) {
  const raw = String(pageUrl || "").trim();
  if (!raw) return "";
  try {
    const urlObj = new URL(raw);
    const match = urlObj.pathname.match(/\/mt\/item\/([^/?#]+)/);
    return match ? match[1] : "";
  } catch (e) {
    const match = raw.match(/\/mt\/item\/([^/?#]+)/);
    return match ? match[1] : "";
  }
}

function resolveCaptchaIdForContext(captchaId, pageUrl, itemCode) {
  const input = String(captchaId || "").trim();
  const code = String(itemCode || "").trim();
  const kind = detailKindFromPageUrl(pageUrl);
  const resolved = CAPTCHA_ID_BY_ITEM_CODE[code] || CAPTCHA_ID_BY_DETAIL_KIND[kind] || input;
  if (input && resolved && input !== resolved) {
    console.warn(`[WARN] 修正 captchaId ${input.slice(0, 8)}... -> ${resolved.slice(0, 8)}... | item=${code || "-"} detail=${kind || "-"}`);
  }
  return resolved;
}

function getCommonHeaders(session, extraHeaders) {
  return { "User-Agent": session && session.ua ? session.ua : BROWSER_USER_AGENT, ...extraHeaders };
}

function buildBingtopConfig(input = {}) {
  const config = input && typeof input === "object" ? input : {};
  const username = config.username || config.user || process.env.BINGTOP_USERNAME || process.env.BINGTOP_USER || "";
  const password = config.password || config.pass || process.env.BINGTOP_PASSWORD || process.env.BINGTOP_PASS || "";
  const endpoint = config.endpoint || process.env.BINGTOP_ENDPOINT || undefined;
  const captchaType = config.captchaType || process.env.BINGTOP_CAPTCHA_TYPE || undefined;
  const clickCaptchaType = config.clickCaptchaType || process.env.BINGTOP_CLICK_CAPTCHA_TYPE || 2316;
  const timeoutMs = Number(config.timeoutMs || process.env.BINGTOP_TIMEOUT_MS || 60000);
  return {
    enabled: Boolean(username && password),
    username,
    password,
    endpoint,
    captchaType,
    clickCaptchaType,
    timeoutMs: Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 60000,
  };
}

async function fetchJsonp(url, headers, proxyUrl) {
  const response = await fetchWithProxy(url, { headers }, proxyUrl);
  const responseText = await response.text();
  if (!response.ok) throw new Error(`请求失败 (${response.status})`);
  return parseJsonpResponse(responseText);
}

// --- 核心业务逻辑修改（接收参数） ---

function firstNonEmptyUrl(value) {
  if (Array.isArray(value)) return value.find(item => typeof item === "string" && item.trim());
  return typeof value === "string" && value.trim() ? value : "";
}

function getCaptchaImageSet(captchaData) {
  const data = captchaData && typeof captchaData === "object" ? captchaData : {};
  return {
    targetImageUrl: firstNonEmptyUrl(data.front || data.target || data.slider || data.jigsaw || data.subCaptchaData),
    backgroundImageUrl: firstNonEmptyUrl(data.bg || data.background || data.backgroundImage || data.captchaData),
    token: data.token == null ? "" : String(data.token),
    width: data.width || CAPTCHA_RENDER_WIDTH,
    zoneId: data.zoneId || "CN31",
    type: data.type,
  };
}

function summarizeCaptchaData(captchaData) {
  const data = captchaData && typeof captchaData === "object" ? captchaData : {};
  const keys = Object.keys(data).slice(0, 30).join(",");
  const frontLen = Array.isArray(data.front) ? data.front.length : (data.front ? 1 : 0);
  const bgLen = Array.isArray(data.bg) ? data.bg.length : (data.bg ? 1 : 0);
  return `keys=${keys} token=${data.token ? "yes" : "no"} frontLen=${frontLen} bgLen=${bgLen} error=${data.error ?? ""} msg=${data.msg ?? ""}`;
}

async function getSlideTargetFromImageUrls(targetImageUrl, backgroundImageUrl, ocrApiUrl = OCR_API_URL, proxyUrl) {
  if (!backgroundImageUrl) {
    throw new Error("易盾返回缺少背景图 bg，无法识别滑块");
  }
  const backgroundFile = await fetchImageAsBlob(backgroundImageUrl, "background.jpg", proxyUrl);
  const targetFile = targetImageUrl ? await fetchImageAsBlob(targetImageUrl, "target.png", proxyUrl) : null;

  const formData = new FormData();
  formData.append("bg_img", backgroundFile.blob, backgroundFile.filename);
  formData.append("background_file", backgroundFile.blob, backgroundFile.filename);
  if (targetFile) {
    formData.append("target_img", targetFile.blob, targetFile.filename);
    formData.append("target_file", targetFile.blob, targetFile.filename);
  } else {
    formData.append("simple_target", "true");
  }

  const response = await fetchWithProxy(ocrApiUrl, { method: "POST", body: formData }, proxyUrl);
  const responseText = await response.text();
  if (!response.ok) throw new Error(`OCR 接口请求失败`);

  const rawData = parseOcrResponseText(responseText);
  const data = rawData && rawData.data && typeof rawData.data === "object" ? rawData.data : rawData;
  let target = data && Array.isArray(data.target) ? data.target : null;
  if (!target && data && Number.isFinite(Number(data.target_x))) {
    target = [Number(data.target_x), Number(data.target_y || 0)];
  }
  if (!target && data && Array.isArray(data.result)) {
    target = data.result;
  }
  if (!target || target.length === 0) {
    throw new Error(`OCR 返回缺少 target: ${responseText.slice(0, 200)}`);
  }
  return { targetX: target[0] };
}

async function solveWithLocalOcrFromBuffers(sample, ocrApiUrl, proxyUrl) {
  const bgBytes = sample.bg || Buffer.from(sample.bgBase64 || "", "base64");
  const sliderBytes = sample.slider || (sample.sliderBase64 ? Buffer.from(sample.sliderBase64, "base64") : null);
  const bgBlob = new Blob([bgBytes], { type: "image/png" });
  const formData = new FormData();
  formData.append("background_file", bgBlob, "background.png");
  formData.append("bg_img", bgBlob, "background.png");
  if (sliderBytes && sliderBytes.length) {
    const sliderBlob = new Blob([sliderBytes], { type: "image/png" });
    formData.append("target_file", sliderBlob, "target.png");
    formData.append("target_img", sliderBlob, "target.png");
  } else {
    formData.append("simple_target", "true");
  }

  const response = await fetchWithProxy(ocrApiUrl || OCR_API_URL, { method: "POST", body: formData }, proxyUrl);
  const responseText = await response.text();
  if (!response.ok) throw new Error(`OCR 接口请求失败 (${response.status}): ${responseText.slice(0, 200)}`);
  const rawData = parseOcrResponseText(responseText);
  const data = rawData && rawData.data && typeof rawData.data === "object" ? rawData.data : rawData;
  let target = data && Array.isArray(data.target) ? data.target : null;
  if (!target && data && Number.isFinite(Number(data.target_x))) {
    target = [Number(data.target_x), Number(data.target_y || 0)];
  }
  if (!target && data && Array.isArray(data.result)) {
    target = data.result;
  }
  if (!target || target.length === 0) {
    throw new Error(`OCR 返回缺少 target: ${responseText.slice(0, 200)}`);
  }
  return { recognition: Number(target[0]), raw: rawData };
}

async function runCaptchaFlowViaNodeBridge(captchaId, pageUrl, ocrApiUrl, proxyUrl, priorityRecordId, bingtopConfigInput) {
  const bingtopConfig = buildBingtopConfig(bingtopConfigInput);
  const result = await solveYidunCaptchaWithBingtop({
    challenge: {
      captchaId,
      referer: pageUrl,
      refererHeader: pageUrl,
      extraData: priorityRecordId ? { priorityRecordId: Number(priorityRecordId) || priorityRecordId } : undefined,
      width: CAPTCHA_RENDER_WIDTH,
    },
    bingtop: bingtopConfig,
    maxAttempts: _safeInt(process.env.YIDUN_NODE_BRIDGE_ATTEMPTS, 3),
    manualSolve: sample => solveWithLocalOcrFromBuffers(sample, ocrApiUrl, proxyUrl),
    logger: (level, message) => console.log(`[node_bridge:${level}] ${message}`),
  });
  if (result && result.success && result.validate) {
    return {
      result: true,
      validate: result.validate,
      v: result.validate,
      rawValidate: result.rawValidate || "",
      fp: result.fingerprint || "",
      token: result.token || "",
      zoneId: result.zoneId || "",
      attempts: result.attempts || 0,
      captchaId: result.captchaId || captchaId,
      raw: result.raw || result,
    };
  }
  throw new Error(result && result.message ? result.message : `node_bridge yidun failed: ${JSON.stringify(result)}`);
}

function _safeInt(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : fallback;
}

async function getConf(captchaId, pageUrl, session, proxyUrl) {
  const params = {
    referer: pageUrl,
    zoneId: "",
    dt: session.dt || undefined,
    id: captchaId,
    ipv6: "false",
    runEnv: "10",
    iv: "5",
    loadVersion: LOAD_VERSION,
    lang: "zh-CN",
    callback: generateJsonpCallback(0),
  };

  const result = await fetchJsonp(buildUrlWithParams(GETCONF_URL, params), getCommonHeaders(session, { Accept: "*/*", Referer: pageUrl }), proxyUrl);
  if (!result || !result.data || !result.data.dt || !result.data.ir || !result.data.ir.pn) {
    throw new Error(`getconf 异常`);
  }
  session.dt = result.data.dt;
  return result.data;
}

async function postIrUp(irConfig, pageUrl, session, proxyUrl) {
  // 1. 生成本次唯一的会话 n 值
  const nonce = createUuid32();
  // 2. 动态生成加密的 d 值！！
  const dynamicD = generateIrUpD(irConfig.pn, IR_VERSION, nonce, session);

  // 3. 组装动态 Payload
  const payload = { 
    p: irConfig.pn, 
    v: IR_VERSION, 
    vk: IR_VK, 
    n: nonce, 
    d: dynamicD 
  };

  const response = await fetchWithProxy(IR_UP_URL, {
    method: "POST",
    headers: getCommonHeaders(session, {
      Accept: "*/*",
      Origin: pageUrl.replace(/\/$/, ""),
      Referer: pageUrl,
      "content-type": "text/plain",
    }),
    body: JSON.stringify(payload),
  }, proxyUrl);
  
  const responseText = await response.text();
  if (!response.ok) throw new Error(`ir up 请求失败`);
  const result = JSON.parse(responseText);
  return result.data;
}

// async function postIrUp(irConfig, pageUrl, proxyUrl) {
//   const payload = { p: irConfig.pn, v: IR_VERSION, vk: IR_VK, n: createUuid32(), d: IR_UP_D };
//   const response = await fetchWithProxy(IR_UP_URL, {
//     method: "POST",
//     headers: getCommonHeaders({
//       Accept: "*/*",
//       Origin: pageUrl.replace(/\/$/, ""),
//       Referer: pageUrl,
//       "content-type": "text/plain",
//     }),
//     body: JSON.stringify(payload),
//   }, proxyUrl);
//   const responseText = await response.text();
//   if (!response.ok) throw new Error(`ir up 请求失败`);
//   const result = JSON.parse(responseText);
//   return result.data;
// }

function generateFp(pageHost) {
  return buildPdDe3Value({ host: pageHost, fpArray: KEPUCHINA_FP_ARRAY });
}

async function getCaptchaData(confData, irUpData, fpValue, captchaId, pageUrl, session, proxyUrl) {
  const cbValue = cryptoModule.generateCbValue();
  const params = {
    referer: pageUrl, 
    zoneId: confData.zoneId || "CN31", 
    dt: confData.dt, 
    irToken: irUpData.tk, 
    id: captchaId, 
    fp: fpValue,
    https: "true", 
    type: "",
    version: CAPTCHA_VERSION, 
    dpr: CAPTCHA_DPR, 
    dev: CAPTCHA_DEV, 
    cb: cbValue, 
    ipv6: "false", 
    runEnv: "10",
    group: "", 
    scene: "", 
    lang: "zh-CN", 
    sdkVersion: "", 
    loadVersion: LOAD_VERSION, 
    iv: "4", 
    user: "", 
    width: String(CAPTCHA_RENDER_WIDTH),
    audio: "false", 
    sizeType: "10", 
    smsVersion: "v3", 
    token: "", 
    callback: generateJsonpCallback(0),
  };
  return fetchJsonp(buildUrlWithParams(CAPTCHA_GET_URL, params), getCommonHeaders(session, { Accept: "*/*", Referer: pageUrl }), proxyUrl);
}

async function n1(captchaId, pageUrl, pageHost, session, proxyUrl) {
  const confData = await getConf(captchaId, pageUrl, session, proxyUrl);
  const effectiveCaptchaId = (confData.ac && confData.ac.bid) || captchaId;
  if (effectiveCaptchaId !== captchaId) {
    console.warn(`[WARN] getconf ac.bid=${effectiveCaptchaId} 与传入 captchaId=${captchaId} 不一致，后续请求改用 ac.bid`);
  }
  const irUpData = await postIrUp(confData.ir, pageUrl, session, proxyUrl);
  const fpValue = generateFp(pageHost);
  const captchaResponse = await getCaptchaData(confData, irUpData, fpValue, effectiveCaptchaId, pageUrl, session, proxyUrl);
  return {
    fp: fpValue, fpArray: KEPUCHINA_FP_ARRAY, dt: confData.dt,
    irPn: confData.ir.pn, irToken: irUpData.tk, response: captchaResponse,
    captchaId: effectiveCaptchaId, zoneId: confData.zoneId || "",
  };
}

async function checkCaptcha(dt, id, token, dataPayload, pageUrl, captchaOptions = {}, session, proxyUrl) {
  const cbValue = cryptoModule.generateCbValue();
  const zoneId = captchaOptions.zoneId || "CN31";
  const params = {
    referer: pageUrl, zoneId: zoneId, dt: dt, id: id, token: token, data: normalizeCaptchaDataPayload(dataPayload),
    width: String(captchaOptions.width || CAPTCHA_RENDER_WIDTH), type: String(captchaOptions.type == null ? 2 : captchaOptions.type),
    version: CAPTCHA_VERSION, cb: cbValue, user: "", extraData: captchaOptions.priorityRecordId == null ? "" : JSON.stringify({ priorityRecordId: Number(captchaOptions.priorityRecordId) }), bf: String(captchaOptions.bf == null ? 0 : captchaOptions.bf),
    runEnv: "10", sdkVersion: "", loadVersion: LOAD_VERSION, iv: "4", callback: generateJsonpCallback(1),
  };

  const response = await fetchWithProxy(buildUrlWithParams(CAPTCHA_CHECK_URL, params), {
    headers: getCommonHeaders(session, { Accept: "*/*", Referer: pageUrl }),
  }, proxyUrl);
  const responseText = await response.text();
  const parsed = parseJsonpResponse(responseText);
  console.log("[DEBUG] check响应:", JSON.stringify(parsed));
  if (parsed && parsed.data && parsed.data.validate && captchaOptions.fingerprint) {
    parsed.data.finalValidate = cryptoModule.buildFinalValidate(parsed.data.validate, captchaOptions.fingerprint, zoneId);
  }
  return parsed;
}

async function res_data(token, drag, trackOptions) {
  const { d, p, f, ext } = cryptoModule.buildTraceData(token, drag, trackOptions);
  return { d, m: "", p, f, ext };
}

async function runCaptchaFlow(captchaId, pageUrl, ocrApiUrl, proxyUrl, priorityRecordId, maxRetries = 2) {
  let pageHost;
  try {
    pageHost = new URL(pageUrl).hostname;
  } catch (e) {
    throw new Error("提供的 PAGE_URL 格式不合法");
  }

  const effectiveProxyUrl = proxyUrl !== undefined ? proxyUrl : (PROXY_URL || undefined);
  const effectiveOcrApiUrl = ocrApiUrl || OCR_API_URL;
  const session = acquireSession();

  let lastResult = null;
  for (let retry = 0; retry <= maxRetries; retry += 1) {
    if (retry > 0) {
      console.log(`[RETRY] 第${retry}次重试，重新取图...`);
      await sleep(1000 + Math.random() * 2000);
    }

    const get_res = await n1(captchaId, pageUrl, pageHost, session, effectiveProxyUrl);
    const captchaData = get_res.response && get_res.response.data && typeof get_res.response.data === "object"
      ? get_res.response.data
      : {};
    const imageSet = getCaptchaImageSet(captchaData);
    if (!imageSet.token || !imageSet.backgroundImageUrl) {
      throw new Error(`/v3/get 缺少 token/bg：${summarizeCaptchaData(captchaData)}`);
    }
    if (!imageSet.targetImageUrl) {
      console.warn(`[WARN] /v3/get 未返回 front，改用背景缺口识别 | ${summarizeCaptchaData(captchaData)}`);
    }
    const juli = await getSlideTargetFromImageUrls(imageSet.targetImageUrl, imageSet.backgroundImageUrl, effectiveOcrApiUrl, effectiveProxyUrl);
    console.log("[DEBUG] OCR targetX:", juli.targetX, "| token:", imageSet.token.substring(0, 20));
    const body = await res_data(imageSet.token, juli.targetX, { width: imageSet.width || CAPTCHA_RENDER_WIDTH });
    await sleep(captchaData.waitTime || 300);

    const checkRes = await checkCaptcha(get_res.dt, get_res.captchaId || captchaId, imageSet.token, body, pageUrl, {
      zoneId: get_res.zoneId || imageSet.zoneId,
      type: imageSet.type,
      width: imageSet.width || CAPTCHA_RENDER_WIDTH,
      bf: 0,
      fingerprint: get_res.fp,
      priorityRecordId,
    }, session, effectiveProxyUrl);
    const checkData = checkRes && checkRes.data && typeof checkRes.data === "object" ? checkRes.data : {};
    lastResult = { ...checkData, fp: get_res.fp, deviceId: session.deviceId, userAgent: session.ua };
    if (checkData.result === true && (checkData.finalValidate || checkData.validate)) {
      console.log(`[RETRY] 成功，尝试次数=${retry + 1}`);
      return lastResult;
    }
    console.log(`[RETRY] 未通过，尝试次数=${retry + 1}`);
  }

  return lastResult || { result: false, fp: "" };
}

// 1. 必须开启 JSON 解析中间件，否则无法接收 Python 发来的 JSON body
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// --- Express API 路由 ---
app.post("/api/verify", async (req, res) => {
  // 2. 从请求体中提取参数
  const { captchaId, pageUrl, ocrApiUrl, proxyUrl, priorityRecordId, itemCode, bingtop } = req.body;
  
  // 3. 校验参数是否存在
  if (!captchaId || !pageUrl) {
    return res.status(400).json({ success: false, error: "缺少必要参数：captchaId 或 pageUrl" });
  }

  try {
    // 4. 将参数正确传入执行函数中
    const proxyInfo = getProxyUsageInfo(proxyUrl);
    console.log(`[代理] 本次代理: ${proxyInfo.enabled ? "启用" : "未启用"} | ${proxyInfo.label} | 来源: ${proxyInfo.source}`);
    const effectiveCaptchaId = resolveCaptchaIdForContext(captchaId, pageUrl, itemCode);
    const result = await runCaptchaFlowViaNodeBridge(effectiveCaptchaId, pageUrl, ocrApiUrl, proxyUrl, priorityRecordId, bingtop);
    res.json({ success: true, data: result, message: "接口调用成功" });
  } catch (error) {
    // 将错误打印到黑窗口，方便排查
    console.error("【接口内部报错】:", error); 
    res.status(500).json({ success: false, error: error.message });
  }
});

const PORT = 8887;
app.listen(PORT, () => {
  console.log(`本地滑块 API 服务已启动！请勿关闭此窗口。`);
  console.log(`调用地址: http://127.0.0.1:${PORT}/api/verify`);
  console.log(`默认代理: ${PROXY_URL ? maskProxyUrl(PROXY_URL) : "未配置"}`);
  console.log(`proxyUrl示例: http://127.0.0.1:7897；传空字符串""表示本次强制直连`);
});
