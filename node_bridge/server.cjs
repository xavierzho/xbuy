/**
 * 本机 HTTP 桥：POST /req 单条；POST /batch 并行多条；POST /rush_loop 抢购多轮（限流换代理）。
 * 启动：npm install && node server.cjs
 */
// UV_THREADPOOL_SIZE 必须在所有 require 之前设置，否则 libuv 已初始化，设置无效
// 高频并发 SOCKS5 握手依赖此值（默认 4 会导致并发连接串行排队）
if (!process.env.UV_THREADPOOL_SIZE) {
  process.env.UV_THREADPOOL_SIZE = "512";
}
"use strict";

const http = require("http");
const https = require("https");
const fs = require("fs");
const os = require("os");
const path = require("path");
const events = require("events");
const { Readable } = require("stream");
const { pipeline } = require("stream/promises");
const { spawn } = require("child_process");
const axios = require("axios");
const { HttpsProxyAgent } = require("https-proxy-agent");
const { SocksProxyAgent } = require("socks-proxy-agent");
const bbModule = require("./bb/bb_module.cjs");

const PORT = parseInt(process.env.MT_NODE_HTTP_BRIDGE_PORT || "19090", 10);
const BIND = process.env.MT_NODE_HTTP_BRIDGE_BIND || "127.0.0.1";
const NODE_MAX_LISTENERS = Math.max(
  32,
  Math.min(2048, parseInt(process.env.MT_NODE_MAX_LISTENERS || "256", 10) || 256),
);
// 高频 keep-alive 并发下同一 Socket 会被临时挂载较多监听器，默认 10 易误报泄漏告警。
events.defaultMaxListeners = Math.max(events.defaultMaxListeners || 10, NODE_MAX_LISTENERS);
/** 与 Python MT_BATCH_RUSH_PARALLEL_CAP 一致：批量抢购 /batch 同时 worker 上限 */
const BATCH_CONC_CAP = Math.max(
  64,
  Math.min(
    2048,
    parseInt(
      process.env.MT_BATCH_RUSH_PARALLEL_CAP ||
        process.env.MT_NODE_BATCH_MAX_CONCURRENCY ||
        "512",
      10,
    ) || 512,
  ),
);

/** 抢购每枪：默认可不写内存环/不打终端，明细以 WebUI 各账号「操作记录」为准（接口 rushLoopTrace）。恢复旧调试：MT_BRIDGE_RUSH_TAP_MEMORY=1、MT_BRIDGE_RUSH_QUIET=0 */
const RUSH_TAP_MAX = 500;
const rushTapEntries = [];
const _rq = String(process.env.MT_BRIDGE_RUSH_QUIET ?? "1").trim().toLowerCase();
/** 默认安静：不显式设 MT_BRIDGE_RUSH_QUIET=0 则不打 [rush_tap] 到终端 */
const bridgeRushQuiet = !(
  _rq === "0" ||
  _rq === "false" ||
  _rq === "no" ||
  _rq === "off"
);
const _rtm = String(process.env.MT_BRIDGE_RUSH_TAP_MEMORY ?? "0").trim().toLowerCase();
/** 默认 0：不向内存环写入，GET /rush_tap 为空（本页仅说明）；=1 时恢复逐枪面板/轮询 */
const rushTapMemoryOn =
  _rtm === "1" || _rtm === "true" || _rtm === "yes" || _rtm === "on";
const KEEPALIVE_MAX_SOCKETS = Math.max(
  16,
  Math.min(2048, parseInt(process.env.MT_NODE_KEEPALIVE_MAX_SOCKETS || "512", 10) || 512),
);
const KEEPALIVE_FREE_SOCKETS = Math.max(
  8,
  Math.min(1024, parseInt(process.env.MT_NODE_KEEPALIVE_FREE_SOCKETS || "128", 10) || 128),
);
const directHttpAgent = new http.Agent({
  keepAlive: true,
  maxSockets: KEEPALIVE_MAX_SOCKETS,
  maxFreeSockets: KEEPALIVE_FREE_SOCKETS,
});
const directHttpsAgent = new https.Agent({
  keepAlive: true,
  maxSockets: KEEPALIVE_MAX_SOCKETS,
  maxFreeSockets: KEEPALIVE_FREE_SOCKETS,
});
directHttpAgent.setMaxListeners(NODE_MAX_LISTENERS);
directHttpsAgent.setMaxListeners(NODE_MAX_LISTENERS);
const proxyAgentCache = new Map();

/** 代理 keepAlive：HTTPS 业务经 curl worker；axios 代理池仍可用于少数回退路径。设 MT_NODE_PROXY_KEEPALIVE=0 可关闭。 */
function proxyAgentKeepAliveEnabled() {
  const v = String(process.env.MT_NODE_PROXY_KEEPALIVE || "1").trim().toLowerCase();
  return v !== "0" && v !== "false" && v !== "no" && v !== "off";
}

function rushPathHint(urlStr) {
  try {
    const u = new URL(String(urlStr));
    return u.pathname || "";
  } catch (_) {
    return String(urlStr || "").slice(0, 80);
  }
}

function pushRushTap(row) {
  if (rushTapMemoryOn) {
    rushTapEntries.push({ ...row, ts: Date.now() });
    if (rushTapEntries.length > RUSH_TAP_MAX) {
      rushTapEntries.splice(0, rushTapEntries.length - RUSH_TAP_MAX);
    }
  }
  if (!bridgeRushQuiet) {
    const w = row.warn ? " !" : "";
    console.log(`[rush_tap] ${row.line}${w}`);
  }
}

const RUSH_PANEL_HTML = `<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>Node 桥 · 抢购明细说明</title>
<style>
 body{font-family:system-ui,Segoe UI,sans-serif;background:#1a1a1e;color:#e0e0e0;margin:16px;font-size:14px;line-height:1.55;max-width:720px;}
 h1{font-size:16px;margin:0 0 12px;color:#f90;}
 p{margin:10px 0;opacity:.92;}
 code{background:#2a2a32;padding:2px 6px;border-radius:4px;font-size:12px;}
 .box{background:#111;border:1px solid #333;border-radius:8px;padding:14px;margin-top:14px;font-size:13px;color:#ccc;}
</style></head><body>
<h1>moutai-http-bridge</h1>
<p>每枪 HTTP 明细（含 <code>http=429</code>、业务提示等）已<strong>默认不再</strong>写入本页与终端刷屏，请在 <strong>Web 控制台 → 批量抢购 → 各账号卡片 →「本账号操作记录」</strong> 查看（数据来自接口返回的 <code>rushLoopTrace</code>，与 Node 实际请求一致）。</p>
<p class="box">若需恢复本页逐枪轮询 + 终端 <code>[rush_tap]</code> 调试：重启 Node 桥并设置环境变量 <code>MT_BRIDGE_RUSH_TAP_MEMORY=1</code>、<code>MT_BRIDGE_RUSH_QUIET=0</code>。</p>
</body></html>`;

/** Node 原始 path，去掉 ?query，避免异常 query 导致路由不走 /rush_loop */
function pathnameOnly(req) {
  const raw = req.url || "/";
  const q = raw.indexOf("?");
  return q >= 0 ? raw.slice(0, q) : raw;
}

function proxyAgent(proxyUrl) {
  const key = String(proxyUrl || "").trim();
  if (!key) return null;
  const ka = proxyAgentKeepAliveEnabled();
  const cacheKey = ka ? key : key + "\0noka";
  if (proxyAgentCache.has(cacheKey)) {
    return proxyAgentCache.get(cacheKey);
  }
  const low = key.toLowerCase();
  const opt = ka
    ? {
        keepAlive: true,
        maxSockets: KEEPALIVE_MAX_SOCKETS,
        maxFreeSockets: KEEPALIVE_FREE_SOCKETS,
      }
    : {
        keepAlive: false,
        maxSockets: Math.min(256, KEEPALIVE_MAX_SOCKETS),
      };
  let ag;
  if (low.startsWith("socks5") || low.startsWith("socks4") || low.startsWith("socks5h")) {
    ag = new SocksProxyAgent(key, opt);
  } else {
    ag = new HttpsProxyAgent(key, opt);
  }
  if (ag && typeof ag.setMaxListeners === "function") {
    ag.setMaxListeners(NODE_MAX_LISTENERS);
  }
  proxyAgentCache.set(cacheKey, ag);
  return ag;
}

function pickAgents(proxyUrl) {
  const ag = proxyAgent(proxyUrl);
  if (ag) {
    return { httpAgent: ag, httpsAgent: ag, disableAxiosProxy: true };
  }
  return { httpAgent: directHttpAgent, httpsAgent: directHttpsAgent, disableAxiosProxy: false };
}

/** 恒 true：所有 /req、/rush_loop、/batch 子请求经 Python curl_cffi_worker，TLS 与 app 一致。 */
function useCurlCffiTls() {
  return true;
}

/** 恒 false：禁止 axios 原生 TLS 兜底（与伪装指纹不一致）。 */
function tlsFallbackAxios() {
  return false;
}

function tlsImpersonateFrom(_obj) {
  if (!_obj || typeof _obj !== "object") {
    const fb = String(process.env.MT_NODE_TLS_IMPERSONATE || "").trim();
    return fb || "safari17_0";
  }
  const v =
    _obj.tlsImpersonate ||
    _obj.impersonate ||
    process.env.MT_NODE_TLS_IMPERSONATE ||
    "safari17_0";
  return String(v || "").trim() || "safari17_0";
}

/** 恒 true：/prewarm 与业务相同走 curl_cffi_worker。 */
function prewarmUseCurlCffiWorker() {
  return true;
}

function pythonExecArgv() {
  const e = process.env.MT_PYTHON;
  if (e && String(e).trim()) {
    return String(e).trim().split(/\s+/).filter(Boolean);
  }
  return process.platform === "win32" ? ["py", "-3"] : ["python3"];
}

function curlCffiWorkerScript() {
  return path.join(__dirname, "curl_cffi_worker.py");
}

const MAX_CURL_WORKER_STDOUT = 80 * 1024 * 1024;

/** 设为 0/false/off 则恢复「每枪 spawn 新子进程」；默认启用常驻池（仅少 spawn，不改变每枪 TLS 行为）。 */
function curlWorkerPoolDisabled() {
  const v = String(process.env.MT_CURL_WORKER_POOL || "1").trim().toLowerCase();
  return v === "0" || v === "false" || v === "no" || v === "off";
}

function curlWorkerPoolSize() {
  const raw = String(process.env.MT_CURL_WORKER_POOL_SIZE || "").trim();
  if (raw) {
    const n = parseInt(raw, 10);
    if (Number.isFinite(n) && n >= 1) return Math.min(256, n);
  }
  const c = os.cpus().length || 12;
  return Math.min(48, Math.max(8, c + 8));
}

/**
 * 单次模式：每枪 spawn 新 Python 进程（与历史行为一致）。
 * @returns {Promise<{ ok: boolean, status?: number, bodyB64?: string, error?: string }>}
 */
function runCurlCffiWorkerSpawnOnceAsync(job) {
  const script = curlCffiWorkerScript();
  if (!fs.existsSync(script)) {
    return Promise.resolve({ ok: false, error: "missing curl_cffi_worker.py" });
  }
  const argv = [...pythonExecArgv(), script];
  const tmo = Math.max(2000, Math.min(620000, parseInt(job.timeoutMs || "30000", 10) + 20000));
  return new Promise((resolve) => {
    let settled = false;
    const finish = (out) => {
      if (settled) return;
      settled = true;
      resolve(out);
    };
    const child = spawn(argv[0], argv.slice(1), {
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      try {
        child.kill();
      } catch (_) {}
    }, tmo);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (d) => {
      if (stdout.length >= MAX_CURL_WORKER_STDOUT) {
        try {
          child.kill();
        } catch (_) {}
        return;
      }
      stdout += d;
    });
    child.stderr.on("data", (d) => {
      stderr += d;
    });
    child.on("error", (err) => {
      clearTimeout(timer);
      finish({ ok: false, error: String(err.message || err).slice(0, 500) });
    });
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      if (settled) return;
      if (signal) {
        return finish({ ok: false, error: "worker signal " + String(signal) });
      }
      const raw = (stdout || "").trim();
      if (!raw) {
        const err = (stderr || "").trim() || "exit " + String(code);
        return finish({ ok: false, error: err.slice(0, 600) });
      }
      try {
        finish(JSON.parse(raw));
      } catch (_) {
        finish({ ok: false, error: "bad json from worker: " + raw.slice(0, 240) });
      }
    });
    const jobText = JSON.stringify(job);
    (async () => {
      try {
        await pipeline(Readable.from([jobText]), child.stdin);
      } catch (e) {
        clearTimeout(timer);
        try {
          child.kill();
        } catch (_) {}
        if (!settled) {
          finish({ ok: false, error: String(e.message || e).slice(0, 500) });
        }
      }
    })();
  });
}

function _dbgCurlPoolNDJSON(hypothesisId, location, message, data) {
  // #region agent log
  try {
    const lp = path.join(__dirname, "..", "debug-eb180b.log");
    fs.appendFileSync(
      lp,
      JSON.stringify({
        sessionId: "eb180b",
        timestamp: Date.now(),
        location,
        message,
        hypothesisId,
        data: data && typeof data === "object" ? data : {},
        runId: "curl-pool",
      }) + "\n",
      "utf8",
    );
  } catch (_) {}
  // #endregion
}

/** 常驻 curl_cffi_worker：stdin 一行 JSON / stdout 一行 JSON；每枪仍独立 Session，不跨枪复用连接。 */
class CurlCffiWorkerPool {
  constructor(size) {
    this.size = size;
    /** @type {{ busy: boolean, buf: string, child: import("child_process").ChildProcess | null, respawnLock: Promise<void> }[]} */
    this.slots = [];
    this.waitQueue = [];
    this.started = false;
  }

  _spawnDaemonChild() {
    const script = curlCffiWorkerScript();
    const argv = [...pythonExecArgv(), script, "--daemon"];
    return new Promise((resolve, reject) => {
      const child = spawn(argv[0], argv.slice(1), {
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      });
      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");
      child.stderr.on("data", () => {});
      let settled = false;
      child.on("error", (e) => {
        if (settled) return;
        settled = true;
        reject(e);
      });
      setImmediate(() => {
        if (settled) return;
        settled = true;
        resolve(child);
      });
    });
  }

  async start() {
    const script = curlCffiWorkerScript();
    if (!fs.existsSync(script)) {
      throw new Error("missing curl_cffi_worker.py");
    }
    for (let i = 0; i < this.size; i++) {
      const ch = await this._spawnDaemonChild();
      this.slots.push({ busy: false, buf: "", child: ch, respawnLock: Promise.resolve() });
    }
    this.started = true;
  }

  async _ensureSlot(slot) {
    slot.respawnLock = slot.respawnLock.then(async () => {
      const ch = slot.child;
      if (ch && ch.exitCode === null && ch.signalCode === null) {
        return;
      }
      // #region agent log
      _dbgCurlPoolNDJSON("H3", "server.cjs:pool._ensureSlot", "respawn_child", {});
      // #endregion
      try {
        if (slot.child) slot.child.kill();
      } catch (_) {}
      slot.buf = "";
      slot.child = await this._spawnDaemonChild();
    });
    return slot.respawnLock;
  }

  _acquire() {
    const idle = this.slots.find((s) => !s.busy);
    if (idle) {
      idle.busy = true;
      return Promise.resolve(idle);
    }
    return new Promise((resolve) => {
      this.waitQueue.push(resolve);
    });
  }

  _release(slot) {
    slot.busy = false;
    if (this.waitQueue.length > 0) {
      const next = this.waitQueue.shift();
      slot.busy = true;
      next(slot);
    }
  }

  _runOnSlot(slot, job) {
    const child = slot.child;
    if (!child) {
      return Promise.resolve({ ok: false, error: "no worker child" });
    }
    const tmo = Math.max(2000, Math.min(620000, parseInt(job.timeoutMs || "30000", 10) + 20000));
    return new Promise((resolve) => {
      let settled = false;
      /** @type {ReturnType<typeof setTimeout> | null} */
      let timer = null;
      const finish = (out) => {
        if (settled) return;
        settled = true;
        if (timer) {
          clearTimeout(timer);
          timer = null;
        }
        try {
          child.stdout.removeListener("data", onData);
        } catch (_) {}
        try {
          child.removeListener("close", onClose);
        } catch (_) {}
        try {
          child.stdin.removeListener("error", onStdinErr);
        } catch (_) {}
        resolve(out);
      };
      const onStdinErr = (err) => {
        finish({ ok: false, error: String(err.message || err).slice(0, 500) });
      };
      const tryParseBuf = () => {
        const nl = slot.buf.indexOf("\n");
        if (nl < 0) return false;
        const line = slot.buf.slice(0, nl).trim();
        slot.buf = slot.buf.slice(nl + 1);
        try {
          finish(JSON.parse(line));
        } catch (_) {
          finish({ ok: false, error: "bad json: " + line.slice(0, 200) });
        }
        return true;
      };
      const onData = (chunk) => {
        if (settled) return;
        if (slot.buf.length + chunk.length > MAX_CURL_WORKER_STDOUT) {
          try {
            child.kill();
          } catch (_) {}
          finish({ ok: false, error: "stdout too large" });
          return;
        }
        slot.buf += chunk;
        tryParseBuf();
      };
      const onClose = () => {
        finish({ ok: false, error: "worker closed" });
      };
      timer = setTimeout(() => {
        try {
          child.kill();
        } catch (_) {}
        finish({ ok: false, error: "timeout" });
      }, tmo);
      child.stdout.on("data", onData);
      child.once("close", onClose);
      child.stdin.once("error", onStdinErr);
      if (tryParseBuf()) {
        return;
      } /* 行缓冲：仅当 slot.buf 已有整行时立即结束（正常应为空） */
      const payload = JSON.stringify(job) + "\n";
      // 禁止 write 返回 false 后在 drain 里再次写整段 payload（会重复已写入字节 → Python 侧 JSON 损坏）
      (async () => {
        try {
          await pipeline(Readable.from([Buffer.from(payload, "utf8")]), child.stdin, { end: false });
        } catch (e) {
          if (!settled) {
            finish({ ok: false, error: String(e.message || e).slice(0, 500) });
          }
        }
      })();
    });
  }

  async run(job) {
    const t0 = Date.now();
    let hostHint;
    try {
      hostHint = new URL(String((job && job.url) || "http://local/")).host;
    } catch (_) {
      hostHint = "";
    }
    // #region agent log
    _dbgCurlPoolNDJSON("H2", "server.cjs:CurlCffiWorkerPool.run", "enter", { host: hostHint });
    // #endregion
    const slot = await this._acquire();
    try {
      await this._ensureSlot(slot);
      const out = await this._runOnSlot(slot, job);
      // #region agent log
      _dbgCurlPoolNDJSON("H1", "server.cjs:CurlCffiWorkerPool.run", "exit", {
        ms: Date.now() - t0,
        ok: !!out.ok,
        st: out.status,
      });
      // #endregion
      return out;
    } finally {
      this._release(slot);
    }
  }

  destroy() {
    for (const s of this.slots) {
      try {
        if (s.child) s.child.kill();
      } catch (_) {}
      s.child = null;
    }
  }
}

let _curlWorkerPoolSingleton = null;
/** @type {Promise<CurlCffiWorkerPool> | null} */
let _curlWorkerPoolInitPromise = null;

function getCurlWorkerPool() {
  if (_curlWorkerPoolSingleton) {
    return Promise.resolve(_curlWorkerPoolSingleton);
  }
  if (!_curlWorkerPoolInitPromise) {
    const size = curlWorkerPoolSize();
    const pool = new CurlCffiWorkerPool(size);
    _curlWorkerPoolInitPromise = pool
      .start()
      .then(() => {
        _curlWorkerPoolSingleton = pool;
        return pool;
      })
      .catch((e) => {
        _curlWorkerPoolInitPromise = null;
        throw e;
      });
  }
  return _curlWorkerPoolInitPromise;
}

/**
 * curl_cffi：默认走常驻 worker 池（少反复 spawn）；MT_CURL_WORKER_POOL=0 时与旧版一致每枪新进程。
 * @returns {Promise<{ ok: boolean, status?: number, bodyB64?: string, error?: string }>}
 */
function runCurlCffiWorkerAsync(job) {
  // #region agent log
  fetch('http://127.0.0.1:7656/ingest/e40b9442-f311-48ae-beb7-831142f9836b',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'67b662'},body:JSON.stringify({sessionId:'67b662',runId:'pre-fix-route-check',hypothesisId:'H1',location:'node_bridge/server.cjs:runCurlCffiWorkerAsync',message:'enter_python_worker_path',data:{method:String((job&&job.method)||''),url:String((job&&job.url)||'').slice(0,180),hasProxy:!!String((job&&job.proxyUrl)||'').trim()},timestamp:Date.now()})}).catch(()=>{});
  // #endregion
  const script = curlCffiWorkerScript();
  if (!fs.existsSync(script)) {
    return Promise.resolve({ ok: false, error: "missing curl_cffi_worker.py" });
  }
  if (curlWorkerPoolDisabled()) {
    return runCurlCffiWorkerSpawnOnceAsync(job);
  }
  return getCurlWorkerPool()
    .then((pool) => pool.run(job))
    .catch(() => runCurlCffiWorkerSpawnOnceAsync(job));
}

function normalizeWarmTask(input) {
  const src = input && typeof input === "object" ? input : {};
  const url = typeof src.url === "string" && src.url.trim() ? src.url.trim() : "https://h5.moutai519.com.cn/";
  const method = String(src.method || "GET").toUpperCase();
  const timeoutMs = Math.max(1000, Math.min(600000, parseInt(src.timeoutMs || "10000", 10) || 10000));
  const proxyUrl = String(src.proxyUrl || "").trim();
  const headers = src.headers && typeof src.headers === "object" ? src.headers : {};
  return { url, method, timeoutMs, proxyUrl, headers };
}

async function execWarmTask(input) {
  const t = normalizeWarmTask(input);
  const tls = tlsImpersonateFrom(input);
  const t0 = Date.now();
  const tryWorker = useCurlCffiTls() && prewarmUseCurlCffiWorker();
  if (tryWorker) {
    const wj = {
      method: t.method,
      url: t.url,
      headers: t.headers,
      timeoutMs: t.timeoutMs,
      proxyUrl: t.proxyUrl,
      tlsImpersonate: tls,
    };
    const out = await runCurlCffiWorkerAsync(wj);
    const ms = Math.max(0, Date.now() - t0);
    if (out.ok) {
      return { ok: true, status: out.status, ms, proxyUrl: t.proxyUrl, url: t.url };
    }
    if (!tlsFallbackAxios()) {
      return {
        ok: false,
        error: String(out.error || "curl_cffi warm").slice(0, 400),
        ms,
        proxyUrl: t.proxyUrl,
        url: t.url,
      };
    }
  }
  const ag = pickAgents(t.proxyUrl);
  const cfg = {
    method: t.method,
    url: t.url,
    headers: t.headers,
    timeout: t.timeoutMs,
    validateStatus: () => true,
    responseType: "arraybuffer",
    maxRedirects: 0,
    decompress: false,
    httpAgent: ag.httpAgent,
    httpsAgent: ag.httpsAgent,
  };
  if (ag.disableAxiosProxy) cfg.proxy = false;
  const t1 = Date.now();
  try {
    const r = await axios(cfg);
    return {
      ok: true,
      status: r.status,
      ms: Math.max(0, Date.now() - t1),
      proxyUrl: t.proxyUrl,
      url: t.url,
    };
  } catch (e) {
    return {
      ok: false,
      error: (e && e.message ? String(e.message) : String(e)).slice(0, 400),
      ms: Math.max(0, Date.now() - t1),
      proxyUrl: t.proxyUrl,
      url: t.url,
    };
  }
}

async function handlePrewarm(raw, res) {
  let payload;
  try {
    payload = JSON.parse(raw || "{}");
  } catch (_) {
    res.statusCode = 400;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.end(JSON.stringify({ ok: false, error: "invalid JSON" }));
    return;
  }
  const targets = Array.isArray(payload.targets) ? payload.targets : [];
  if (!targets.length) {
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.end(JSON.stringify({ ok: false, error: "targets must be non-empty array" }));
    return;
  }
  let conc = parseInt(payload.concurrency || "32", 10) || 32;
  conc = Math.max(1, Math.min(BATCH_CONC_CAP, conc, targets.length));
  const tlsG = tlsImpersonateFrom(payload);
  const results = new Array(targets.length);
  let next = 0;
  async function worker() {
    for (;;) {
      const i = next;
      next += 1;
      if (i >= targets.length) break;
      const ti = targets[i] && typeof targets[i] === "object" ? targets[i] : {};
      results[i] = await execWarmTask({
        ...ti,
        tlsImpersonate: ti.tlsImpersonate || tlsG,
      });
    }
  }
  const workers = [];
  for (let i = 0; i < conc; i++) workers.push(worker());
  await Promise.all(workers);
  const okCount = results.filter((x) => x && x.ok).length;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify({ ok: true, results, okCount, total: results.length }));
}

function normalizeRushHttpResponse(statusCode, text) {
  let _res;
  try {
    _res = JSON.parse(text);
  } catch (_) {
    return {
      code: statusCode,
      message: "响应非 JSON",
      raw: (text || "").slice(0, 800),
    };
  }
  if (Array.isArray(_res)) {
    return {
      code: -1,
      message: "抢购接口返回为数组，非预期",
      raw: String(_res).slice(0, 500),
    };
  }
  if (typeof _res !== "object" || _res === null) {
    if (_res === 1 || _res === 2 || _res === "1" || _res === "2") {
      const rs = String(_res).trim();
      return {
        code: -1,
        message:
          "抢购接口仅返回简码 1/2（多为限流、排队或服务端短时非标准体，与部分场景下 HTTP 429 响应体类似）；" +
          "请加大重试间隔、确认开售期与活动 ID，勿当作「黑号」唯一依据。",
        rushBodyCode: rs === "1" || rs === "2" ? parseInt(rs, 10) : _res,
        raw: String(_res),
      };
    }
    return {
      code: -1,
      message: "抢购接口返回类型异常（期望 JSON 对象，实际为非对象）",
      raw: String(_res).slice(0, 500),
    };
  }
  if (!Object.prototype.hasOwnProperty.call(_res, "code")) {
    return {
      code: -1,
      message: "响应无 code 字段",
      rawPreview: JSON.stringify(_res).slice(0, 600),
    };
  }
  return _res;
}

function rushIsSoftLimitLike(_res) {
  if (!_res || typeof _res !== "object") return false;
  const rb = String(_res.rushBodyCode || "");
  if (rb === "1" || rb === "2") return true;
  const c = _res.code;
  const cs = String(c == null ? "" : c).trim();
  const msg = String(_res.message || "").trim();
  if (cs === "429" || cs === "4293") return true;
  const low = msg.toLowerCase();
  if (low.includes("429")) return true;
  return msg.includes("人数较多") || msg.includes("稍后再试") || msg.includes("排队");
}

function sleepMs(ms) {
  const n = Math.max(0, Math.min(300000, parseInt(ms, 10) || 0));
  if (n <= 0) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, n));
}

/** 与 app.py MT_RUSH_JITTER_MS_MAX 一致：每枪 POST 前 [0,N]ms 均匀随机，N 最大 100 */
function rushJitterMsMaxFrom(task) {
  let raw = task.rushJitterMsMax ?? task.rush_jitter_ms_max;
  if (raw == null || raw === "") raw = process.env.MT_RUSH_JITTER_MS_MAX;
  if (raw == null || raw === "") return 100;
  const n = parseInt(String(raw), 10);
  if (!Number.isFinite(n)) return 100;
  return Math.max(0, Math.min(100, n));
}

async function sleepShotJitter(maxMs) {
  const m = Math.max(0, Math.min(100, parseInt(maxMs, 10) || 0));
  if (m <= 0) return;
  const j = Math.floor(Math.random() * (m + 1));
  await sleepMs(j);
}

/** 轮与轮之间 sleep：base ± jitterMax 均匀随机（毫秒） */
function rushRoundGapMsWithJitter(baseMs, jitterMax) {
  const b = Math.max(0, Math.min(300000, parseInt(baseMs, 10) || 0));
  const j = Math.max(0, Math.min(60000, parseInt(jitterMax, 10) || 0));
  if (j <= 0) return b;
  const lo = Math.max(0, b - j);
  const hi = Math.min(300000, b + j);
  return Math.round(lo + Math.random() * (hi - lo));
}

function rushTraceRowBizOk(row) {
  if (!row || row.code === undefined || row.code === null) return false;
  return row.code === 2000 || String(row.code) === "2000";
}

function rushTraceAnyBizOk(traceArr) {
  return Array.isArray(traceArr) && traceArr.some(rushTraceRowBizOk);
}

/** 与 WebUI maybeLock 口径接近：出现则不再发射后续枪（高频 fireAndForget 内逐枪 await 后检测） */
function rushTraceRowStopRush(row) {
  if (!row || typeof row !== "object") return false;
  const msg = String(row.message || "");
  if (
    /限购|限購|购买已达上限|已达购买上限|不可再购|超出每人|每人仅限|超过限购|已达购买|重复购买|每人每天|请勿重复/.test(
      msg,
    )
  ) {
    return true;
  }
  const cs = row.code != null && row.code !== "" ? String(row.code).trim() : "";
  if (cs === "4030") {
    return /限购|限購|购买已达上限|已达购买上限|不可再购|超出每人|每人仅限|超过限购|已达购买|重复购买|每人每天|请勿重复/.test(msg);
  }
  return false;
}

function rushTraceAnyStopRush(traceArr) {
  return Array.isArray(traceArr) && traceArr.some((x) => x && rushTraceRowStopRush(x));
}

async function execOneTask(payload) {
  const method = (payload.method || "GET").toUpperCase();
  const url = payload.url;
  const headers = payload.headers && typeof payload.headers === "object" ? payload.headers : {};
  const timeoutMs = Math.max(1000, Math.min(600000, parseInt(payload.timeoutMs || "30000", 10) || 30000));
  const proxyUrl = payload.proxyUrl || "";
  if (!url || typeof url !== "string") {
    return { ok: false, error: "missing url" };
  }
  let data;
  if (payload.bodyB64 != null && payload.bodyB64 !== "") {
    data = Buffer.from(String(payload.bodyB64), "base64");
  } else if (payload.body != null) {
    data = typeof payload.body === "string" ? payload.body : JSON.stringify(payload.body);
  }
  const tls = tlsImpersonateFrom(payload);
  // #region agent log
  fetch('http://127.0.0.1:7656/ingest/e40b9442-f311-48ae-beb7-831142f9836b',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'67b662'},body:JSON.stringify({sessionId:'67b662',runId:'pre-fix-route-check',hypothesisId:'H2',location:'node_bridge/server.cjs:execOneTask',message:'dispatch_req_path',data:{method,url:String(url||'').slice(0,180),useCurl:!!useCurlCffiTls(),hasBody:payload.body!=null||payload.bodyB64!=null},timestamp:Date.now()})}).catch(()=>{});
  // #endregion
  if (useCurlCffiTls()) {
    const job = {
      method,
      url,
      headers,
      timeoutMs,
      proxyUrl,
      tlsImpersonate: tls,
    };
    // 实名/地址等 POST 体较大时，用 UTF-8 原文塞进 job.body 再 JSON.stringify，
    // 整包 stdin 会膨胀且 Windows 管道偶发截断 → Python json.loads 报 column 3xxx。
    // 子进程侧始终走 bodyB64，外层 JSON 短、二进制安全。
    if (payload.bodyB64 != null && payload.bodyB64 !== "") {
      job.bodyB64 = String(payload.bodyB64);
    } else if (data !== undefined) {
      const buf = Buffer.isBuffer(data) ? data : Buffer.from(String(data), "utf8");
      job.bodyB64 = buf.toString("base64");
    }
    const out = await runCurlCffiWorkerAsync(job);
    if (out.ok) {
      return {
        ok: true,
        status: out.status,
        headers: {},
        bodyB64: out.bodyB64 || "",
      };
    }
    if (!tlsFallbackAxios()) {
      return { ok: false, error: String(out.error || "curl_cffi").slice(0, 800) };
    }
  }
  const ag = pickAgents(proxyUrl);
  const cfg = {
    method,
    url,
    headers,
    data,
    timeout: timeoutMs,
    validateStatus: () => true,
    responseType: "arraybuffer",
    maxRedirects: 5,
    decompress: true,
  };
  cfg.httpAgent = ag.httpAgent;
  cfg.httpsAgent = ag.httpsAgent;
  if (ag.disableAxiosProxy) {
    cfg.proxy = false;
  }
  try {
    const r = await axios(cfg);
    const buf = Buffer.isBuffer(r.data) ? r.data : Buffer.from(r.data || "");
    return {
      ok: true,
      status: r.status,
      headers: r.headers,
      bodyB64: buf.toString("base64"),
    };
  } catch (e) {
    const msg = e && e.message ? String(e.message) : String(e);
    return { ok: false, error: msg.slice(0, 800) };
  }
}

/**
 * 与 app.py Session.rush 循环一致：连接失败继续下一档；业务上非软限流则停；软限流则换 attempts 中下一出口。
 * attempts[i].proxyUrl 每档可不同；Python 发满池顺序时每线一枪时，这里仍是 await execOneTask 串行：上一枪收完再下一枪。
 */
async function execRushLoop(task) {
  const attempts = task.attempts;
  if (!Array.isArray(attempts) || !attempts.length) {
    return { ok: false, error: "rushLoop: attempts required", rushLoop: true };
  }
  const gapMs = Math.max(
    0,
    Math.min(
      300000,
      parseInt(task.rushLoopDelayMs ?? task.delayMs ?? task.rushDelayMs ?? "0", 10) || 0,
    ),
  );
  const maxTry = attempts.length;
  const tlsLoop = tlsImpersonateFrom(task);
  const loopT0 = Date.now();
  const jitM = rushJitterMsMaxFrom(task);
  // #region agent log
  fetch('http://127.0.0.1:7656/ingest/e40b9442-f311-48ae-beb7-831142f9836b',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'67b662'},body:JSON.stringify({sessionId:'67b662',runId:'pre-fix-route-check',hypothesisId:'H3',location:'node_bridge/server.cjs:execRushLoop',message:'rush_loop_enter',data:{attempts:Array.isArray(attempts)?attempts.length:0,fireAndForget:!!task.fireAndForget,useCurl:!!useCurlCffiTls()},timestamp:Date.now()})}).catch(()=>{});
  // #endregion

  // ── fire-and-forget 模式：不等上一枪响应，按 gapMs 间隔连发；可选 rushRounds>1 时分多轮，轮间 sleep ──
  // 启用条件：task.fireAndForget === true
  if (task.fireAndForget) {
    const rounds = Math.max(1, Math.min(100, parseInt(task.rushRounds ?? task.rushRoundCount ?? "1", 10) || 1));
    const roundGapBase = Math.max(
      0,
      Math.min(300000, parseInt(task.rushRoundGapMs ?? task.rush_round_gap_ms ?? "0", 10) || 0),
    );
    const roundJit = Math.max(
      0,
      Math.min(60000, parseInt(task.rushRoundGapJitterMs ?? task.rush_round_gap_jitter_ms ?? "0", 10) || 0),
    );
    let shotsPerRound = maxTry;
    if (rounds > 1) {
      if (maxTry % rounds !== 0) {
        return {
          ok: false,
          error: `rush_loop: attempts=${maxTry} 不能整除 rushRounds=${rounds}（请检查 Python 组包）`,
          rushLoop: true,
        };
      }
      shotsPerRound = Math.floor(maxTry / rounds);
    }
    const trace = new Array(maxTry).fill(null);
    /** 每枪完整 normalize 结果（含 data.priorityRecordId）；last 须用此对象，勿仅用 trace 行，否则高频成功无记录号 */
    const fullResByIdx = new Array(maxTry).fill(null);
    const t0All = Date.now();
    const launchDelayMs = Math.max(0, gapMs);
    const waitMs = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    let globalIdx = 0;
    for (let round = 0; round < rounds; round++) {
      if (rushTraceAnyBizOk(trace)) {
        pushRushTap({
          line: `[faf] 第${round + 1}/${rounds} 轮前检测到业务成功，不再发射后续轮`,
          warn: false,
        });
        break;
      }
      if (rushTraceAnyStopRush(trace)) {
        pushRushTap({
          line: `[faf] 第${round + 1}/${rounds} 轮前检测到限购/停抢类响应，不再发射后续轮`,
          warn: true,
        });
        break;
      }
      for (let i = 0; i < shotsPerRound; i++) {
        if (rushTraceAnyStopRush(trace)) {
          pushRushTap({
            line: `[faf] 已检测到限购/每人每天等响应，停止发射本轮剩余枪（已发 ${globalIdx}/${maxTry}）`,
            warn: true,
          });
          break;
        }
        await sleepShotJitter(jitM);
        const a = attempts[globalIdx];
        const idx = globalIdx;
        globalIdx += 1;
        const proxyUrl = String(a.proxyUrl || "").trim();
        const one = {
          method: "POST",
          url: a.url,
          headers: a.headers || {},
          body: a.body,
          timeoutMs: a.timeoutMs || 30000,
          proxyUrl,
          tlsImpersonate: tlsLoop,
        };
        const t0 = Date.now();
        const rp = `${round + 1}/${rounds}轮`;
        let r;
        try {
          r = await execOneTask(one);
        } catch (e) {
          const ms = Math.max(0, Math.round(Date.now() - t0));
          const em = String((e && e.message) || e).slice(0, 400);
          fullResByIdx[idx] = { code: -1, message: em };
          trace[idx] = {
            attempt: idx + 1,
            httpStatus: 0,
            code: -1,
            message: em,
            ms,
            rushRound: round + 1,
            rushRounds: rounds,
          };
          if (launchDelayMs > 0 && i < shotsPerRound - 1) {
            await waitMs(launchDelayMs);
          }
          continue;
        }
        const ms = Math.max(0, Math.round(Date.now() - t0));
        if (!r.ok) {
          const em = ("抢购请求异常: " + (r.error || "unknown")).slice(0, 400);
          fullResByIdx[idx] = { code: -1, message: em };
          trace[idx] = {
            attempt: idx + 1,
            httpStatus: 0,
            code: -1,
            message: em,
            ms,
            rushRound: round + 1,
            rushRounds: rounds,
          };
          pushRushTap({
            line: `[faf ${rp}] ${idx + 1}/${maxTry} ${ms}ms http=0 ${rushPathHint(one.url)} ${String(r.error || "").slice(0, 72)}`,
            warn: true,
          });
        } else {
          const buf = Buffer.from(String(r.bodyB64 || ""), "base64");
          const res = normalizeRushHttpResponse(r.status, buf.toString("utf8"));
          fullResByIdx[idx] = res;
          const bizOk = res.code === 2000 || String(res.code) === "2000";
          const prid =
            res &&
            res.data &&
            res.data.priorityRecordId != null &&
            String(res.data.priorityRecordId).trim() !== ""
              ? String(res.data.priorityRecordId).trim()
              : "";
          trace[idx] = {
            attempt: idx + 1,
            httpStatus: r.status,
            code: res.code,
            message: String(res.message || "").slice(0, 400),
            ms,
            rushRound: round + 1,
            rushRounds: rounds,
          };
          if (prid) {
            trace[idx].priorityRecordId = prid;
          }
          pushRushTap({
            line: `[faf ${rp}] ${idx + 1}/${maxTry} ${ms}ms http=${r.status} code=${res.code} ${rushPathHint(one.url)} ${String(res.message || "").replace(/\s+/g, " ").slice(0, 72)}`.trim(),
            warn: !bizOk,
          });
        }
        if (rushTraceRowStopRush(trace[idx])) {
          pushRushTap({
            line: `[faf] 本枪返回限购/停抢类业务提示，不再发射后续枪`,
            warn: true,
          });
          break;
        }
        if (launchDelayMs > 0 && i < shotsPerRound - 1) {
          await waitMs(launchDelayMs);
        }
      }
      if (round < rounds - 1) {
        if (rushTraceAnyBizOk(trace) || rushTraceAnyStopRush(trace)) break;
        const rg = rushRoundGapMsWithJitter(roundGapBase, roundJit);
        pushRushTap({
          line: `    → 轮次间隔 ${rg}ms（已发完第 ${round + 1}/${rounds} 轮共 ${shotsPerRound} 枪）`,
          warn: false,
        });
        await waitMs(rg);
      }
    }
    const filled = trace.filter(Boolean);
    const okIdx = trace.findIndex((row) => row && rushTraceRowBizOk(row));
    let last = null;
    if (okIdx >= 0 && fullResByIdx[okIdx] && typeof fullResByIdx[okIdx] === "object") {
      last = fullResByIdx[okIdx];
    } else {
      let li = maxTry - 1;
      while (li >= 0 && !trace[li]) li -= 1;
      if (li >= 0) {
        last =
          fullResByIdx[li] && typeof fullResByIdx[li] === "object"
            ? fullResByIdx[li]
            : trace[li];
      }
    }
    const lastProxyUrl = String(attempts[maxTry - 1].proxyUrl || "").trim();
    const wallMs = Math.max(0, Math.round(Date.now() - t0All));
    return {
      ok: true,
      rushLoop: true,
      fireAndForget: true,
      rushRounds: rounds,
      last,
      lastProxyUrl,
      trace: filled,
      wallMs,
    };
  }

  // ── 原串行模式：等上一枪响应后再发下一枪 ──
  let last = null;
  let lastProxyUrl = "";
  /** 每档一次请求的业务 code / 耗时（给 Python / WebUI 展示，不改变连发节奏） */
  const trace = [];
  for (let i = 0; i < maxTry; i++) {
    const a = attempts[i];
    const proxyUrl = String(a.proxyUrl || "").trim();
    lastProxyUrl = proxyUrl;
    const one = {
      method: "POST",
      url: a.url,
      headers: a.headers || {},
      body: a.body,
      timeoutMs: a.timeoutMs || 30000,
      proxyUrl,
      tlsImpersonate: tlsLoop,
    };
    const pathHint = rushPathHint(one.url);
    await sleepShotJitter(jitM);
    const t0 = Date.now();
    const r = await execOneTask(one);
    const rawMs = Date.now() - t0;
    const ms = Number.isFinite(rawMs) ? Math.max(0, Math.round(rawMs)) : 0;
    if (!r.ok) {
      last = { code: -1, message: "抢购请求异常: " + (r.error || "unknown") };
      trace.push({
        attempt: i + 1,
        httpStatus: 0,
        code: last.code,
        message: String(last.message || "").slice(0, 400),
        ms,
      });
      pushRushTap({
        line: `${i + 1}/${maxTry} ${ms}ms http=0 code=${last.code} ${pathHint} ${String(last.message || "").slice(0, 72)}`,
        warn: true,
      });
      if (i < maxTry - 1 && gapMs > 0) {
        pushRushTap({
          line: `    → 间隔 ${gapMs}ms（响应已收，待发第${i + 2}/${maxTry}枪）`,
          warn: false,
        });
        await sleepMs(gapMs);
      }
      continue;
    }
    const buf = Buffer.from(String(r.bodyB64 || ""), "base64");
    const text = buf.toString("utf8");
    last = normalizeRushHttpResponse(r.status, text);
    trace.push({
      attempt: i + 1,
      httpStatus: r.status,
      code: last.code,
      message: String(last.message || "").slice(0, 400),
      ms,
    });
    const msgShort = String(last.message || "")
      .replace(/\s+/g, " ")
      .slice(0, 72);
    const bizOk = last.code === 2000 || String(last.code) === "2000";
    pushRushTap({
      line: `${i + 1}/${maxTry} ${ms}ms http=${r.status} code=${last.code} ${pathHint} ${msgShort}`.trim(),
      warn: !bizOk,
    });
    const rowStop = trace[trace.length - 1];
    if (rushTraceRowStopRush(rowStop)) {
      pushRushTap({
        line: `[rush_loop] 本枪返回限购/停抢类业务提示，不再发射后续枪（已发 ${i + 1}/${maxTry}）`,
        warn: true,
      });
      break;
    }
    // 响应已记入上一行；非最后一枪：面板显式打出「间隔」再 sleep，与 WebUI「间隔(ms)」一致
    if (i < maxTry - 1 && gapMs > 0) {
      pushRushTap({
        line: `    → 间隔 ${gapMs}ms（响应已收，待发第${i + 2}/${maxTry}枪）`,
        warn: false,
      });
      await sleepMs(gapMs);
    }
  }
  const wallMs = Math.max(0, Math.round(Date.now() - loopT0));
  return {
    ok: true,
    rushLoop: true,
    last,
    lastProxyUrl,
    trace,
    wallMs,
  };
}

async function handleBatch(raw, res) {
  let payload;
  try {
    payload = JSON.parse(raw || "{}");
  } catch (_) {
    res.statusCode = 400;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.end(JSON.stringify({ ok: false, error: "invalid JSON" }));
    return;
  }
  const tasks = payload.tasks;
  if (!Array.isArray(tasks)) {
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.end(JSON.stringify({ ok: false, error: "tasks must be array" }));
    return;
  }
  let conc = parseInt(payload.concurrency || "64", 10) || 64;
  conc = Math.max(1, Math.min(BATCH_CONC_CAP, conc));
  const tlsB = tlsImpersonateFrom(payload);
  const results = new Array(tasks.length);
  let next = 0;
  async function worker() {
    for (;;) {
      const i = next;
      next += 1;
      if (i >= tasks.length) break;
      const t = tasks[i];
      const merged =
        t && typeof t === "object"
          ? { ...t, tlsImpersonate: t.tlsImpersonate || tlsB }
          : t;
      if (merged && merged.rushLoop) {
        results[i] = await execRushLoop(merged);
      } else {
        results[i] = await execOneTask(merged || {});
      }
    }
  }
  const nW = Math.min(conc, tasks.length);
  const workers = [];
  for (let w = 0; w < nW; w++) workers.push(worker());
  await Promise.all(workers);
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify({ ok: true, results }));
}

async function handleRushLoop(raw, res) {
  let payload;
  try {
    payload = JSON.parse(raw || "{}");
  } catch (_) {
    res.statusCode = 400;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.end(JSON.stringify({ ok: false, error: "invalid JSON" }));
    return;
  }
  const attempts = payload.attempts;
  if (!Array.isArray(attempts) || !attempts.length) {
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.end(JSON.stringify({ ok: false, error: "attempts must be non-empty array" }));
    return;
  }
  // 透传 Python 下发的 rushRounds / rushRoundGapMs / rushJitterMsMax 等，勿只挑 4 个字段（否则多轮与抖动在 Node 侧失效）
  const out = await execRushLoop({
    ...payload,
    attempts,
    rushLoopDelayMs:
      payload.rushLoopDelayMs ?? payload.delayMs ?? payload.rushDelayMs ?? 0,
    tlsImpersonate: tlsImpersonateFrom(payload),
    fireAndForget: !!payload.fireAndForget,
  });
  if (!out.ok) {
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.end(JSON.stringify({ ok: false, error: out.error || "rush_loop failed" }));
    return;
  }
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(
    JSON.stringify({
      ok: true,
      last: out.last,
      lastProxyUrl: out.lastProxyUrl,
      trace: out.trace,
      wallMs: out.wallMs,
      rushLoop: true,
    }),
  );
}

const server = http.createServer(async (req, res) => {
  const path = pathnameOnly(req);
  // #region agent log
  fetch('http://127.0.0.1:7656/ingest/e40b9442-f311-48ae-beb7-831142f9836b',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'67b662'},body:JSON.stringify({sessionId:'67b662',runId:'pre-fix-route-check',hypothesisId:'H4',location:'node_bridge/server.cjs:httpServer',message:'incoming_request',data:{method:String(req.method||''),path:String(path||'')},timestamp:Date.now()})}).catch(()=>{});
  // #endregion
  if (req.method === "GET" && path === "/rush_tap") {
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.end(JSON.stringify({ ok: true, entries: rushTapEntries.slice(-220) }));
    return;
  }
  if (req.method === "GET" && (path === "/rush_panel" || path === "/rush_tap.html")) {
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.end(RUSH_PANEL_HTML);
    return;
  }
  if (req.method === "GET" && path === "/bb/health") {
    const out = await bbModule.bbHealth();
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.end(JSON.stringify(out));
    return;
  }
  if (req.method !== "POST") {
    res.statusCode = 404;
    res.end();
    return;
  }
  let raw = "";
  for await (const ch of req) {
    raw += ch;
  }
  // ---- BB SDK endpoints (rush purchase only) ----
  if (path === "/bb/sign_post") {
    try {
      const p = JSON.parse(raw || "{}");
      const out = await bbModule.bbSignPostForRush(p.body || {}, p.ctx || {}, p.options || {});
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.end(JSON.stringify({ ok: true, headers: out }));
    } catch (e) {
      res.statusCode = 500;
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.end(JSON.stringify({ ok: false, error: String(e && e.message || e) }));
    }
    return;
  }
  if (path === "/bb/bootstrap") {
    try {
      const p = raw ? JSON.parse(raw) : {};
      const out = await bbModule.bbBootstrap(p);
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.end(JSON.stringify({ ok: true, state: out }));
    } catch (e) {
      res.statusCode = 500;
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.end(JSON.stringify({ ok: false, error: String(e && e.message || e) }));
    }
    return;
  }
  if (path === "/bb/du_cookie") {
    try {
      const p = JSON.parse(raw || "{}");
      const out = await bbModule.bbDuCookie(p.ctx || {}, p.options || {});
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.end(JSON.stringify({ ok: true, du: out.value, hp: out.hp }));
    } catch (e) {
      res.statusCode = 500;
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.end(JSON.stringify({ ok: false, error: String(e && e.message || e) }));
    }
    return;
  }
  if (path === "/batch") {
    await handleBatch(raw, res);
    return;
  }
  if (path === "/rush_loop") {
    await handleRushLoop(raw, res);
    return;
  }
  if (path === "/prewarm") {
    await handlePrewarm(raw, res);
    return;
  }
  if (path !== "/req") {
    res.statusCode = 404;
    res.end();
    return;
  }
  let payload;
  try {
    payload = JSON.parse(raw || "{}");
  } catch (_) {
    res.statusCode = 400;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.end(JSON.stringify({ ok: false, error: "invalid JSON body" }));
    return;
  }
  const out = await execOneTask(payload);
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  if (out.ok) {
    res.end(JSON.stringify(out));
  } else {
    res.statusCode = 200;
    res.end(JSON.stringify(out));
  }
});

const MT_WEBUI_PORT_HINT = String(process.env.MT_WEBUI_PORT || "8989").trim() || "8989";
server.listen(PORT, BIND, () => {
  console.log(
    `[moutai-http-bridge] http://${BIND}:${PORT}/req /batch /rush_loop /prewarm /bb/sign_post /bb/bootstrap /bb/health | 每枪明细默认在 WebUI 账号「操作记录」；终端 [rush_tap] 设 MT_BRIDGE_RUSH_QUIET=0`,
  );
  console.log(
    `[moutai-http-bridge] 说明页（固定 WebUI 口）: http://127.0.0.1:${MT_WEBUI_PORT_HINT}/node_rush_panel`,
  );
  if (!curlWorkerPoolDisabled()) {
    getCurlWorkerPool().catch((e) => {
      console.error("[moutai-http-bridge] curl worker pool 预热失败，将回退每枪 spawn:", e && e.message ? e.message : e);
    });
  }
  // BB SDK 预热：启动时加载 stub.wasm，避免抢购首次请求时的 ~25ms 冷启动。
  // 失败硬退出：禁止用未就绪的 WASM 发抢购请求（与「禁止 SM4 降级」同口径）。
  bbModule.ensureRuntime().then(() => {
    return bbModule.bbHealth();
  }).then((h) => {
    if (!h.wasmReady) {
      console.error("[moutai-http-bridge] FATAL: BB SDK WASM 未就绪");
      process.exit(1);
    }
    console.log(`[moutai-http-bridge] BB SDK ready: sdk=${h.sdkVersion} signWasm=${h.signWasmReady ? h.signWasmVersion : "(empty)"}`);
  }).catch((e) => {
    console.error("[moutai-http-bridge] FATAL: BB SDK 初始化失败:", e && e.message ? e.message : e);
    process.exit(1);
  });
});

process.on("exit", () => {
  try {
    if (_curlWorkerPoolSingleton) {
      _curlWorkerPoolSingleton.destroy();
    }
  } catch (_) {}
});
