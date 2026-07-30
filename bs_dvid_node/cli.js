/**
 * 供 Python subprocess 调用：stdout **仅**输出一行 JSON；流程日志全部走 stderr，
 * 避免与 json.loads(stdout) 冲突。
 * 用法: node cli.js [deviceId]
 */
const path = require('path');
const { appVersion: DEFAULT_APP_VERSION } = require('../node_bridge/bb/version.json');
// 必须在 require('bs-dvid.js') 之前挂钩，否则步骤日志会污染 stdout
for (const name of ['log', 'info', 'debug']) {
  console[name] = (...args) => {
    const line = args.map((x) => {
      if (x instanceof Error) return x.stack || x.message;
      if (typeof x === 'object' && x !== null) {
        try { return JSON.stringify(x); } catch (_) { return String(x); }
      }
      return String(x);
    }).join(' ');
    process.stderr.write(line + '\n');
  };
}
const bs = require(path.join(__dirname, 'bs-dvid.js'));

(async () => {
  const deviceId = process.argv[2] || null;
  try {
    const r = await bs.getBsDvidWithReport({
      appVersion: DEFAULT_APP_VERSION,
      deviceId: deviceId || undefined,
      fetchConfig: true,
      reportEnv: true,
      reportFullFingerprint: true,
      fetchFinalToken: true,
      proxyConfig: null,
    });
    process.stdout.write(JSON.stringify(r));
    process.exit(r.success ? 0 : 2);
  } catch (e) {
    process.stdout.write(JSON.stringify({ success: false, error: String(e && e.message || e) }));
    process.exit(1);
  }
})();
