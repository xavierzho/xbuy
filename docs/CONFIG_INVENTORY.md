# 配置项清单

本清单只记录可配置入口，不记录当前机器上的密钥值。

## 第三方平台与敏感配置

| 配置 | 位置 | 用途 | 敏感 | 建议 |
| --- | --- | --- | --- | --- |
| FRP 服务端地址 | `frp_mt_ws/frpc.toml` 的 `serverAddr` | worker 或家里主控连接公网 FRP 服务器 | 否 | 部署到新服务器时替换为公网 IP/域名 |
| FRP 服务端口 | `frp_mt_ws/frpc.toml` 的 `serverPort`、`frp_mt_ws/frps.toml` 的 `bindPort`、`MT_FRPS_PORT` | FRP 控制端口 | 否 | 保持服务端/客户端一致 |
| FRP token | `frp_mt_ws/frpc.toml`、`frp_mt_ws/frps.toml`、`MT_FRP_TOKEN` | FRP 认证 | 是 | 真实 token 不应提交到 Git，部署时通过环境变量注入 |
| FRP 远程 WS 端口 | `frp_mt_ws/frpc.toml` 的 `remotePort`、`MT_REMOTE_WS_PORT` | 把主控 WebSocket 映射到公网服务器 | 否 | 默认 `18765`，改动后 worker 连接地址要同步 |
| Cloudflared 程序路径 | `MT_CLOUDFLARED_BIN` | 指定 `cloudflared` 二进制 | 否 | Linux/macOS 用系统版 `cloudflared`，不要依赖 Windows `.exe` |
| Cloudflared 自动启动 | `MT_CLOUDFLARED_AUTOSTART` | 是否自动启动公网临时隧道 | 否 | 设 `0` 可关闭 |
| Cloudflared 公开页 | `MT_CLOUDFLARED_PUBLIC_PAGES` | 是否公开上号页/查单页白名单入口 | 否 | 设 `0` 可关闭公开页 |
| Cloudflared 控制台 | `MT_CLOUDFLARED_CONSOLE` | 是否额外公开 WebUI 控制台入口 | 高 | 服务器部署建议设 `0`，除非有额外访问控制 |
| Cloudflared 本地白名单端口 | `MT_PUBLIC_LOGIN_PORT`、`MT_PUBLIC_ORDER_PORT` | 上号页、查单页本地转发端口 | 否 | 默认 `7979`、`6969` |
| 固定公网域名 | `MT_PUBLIC_BASE_URL` | 使用自有域名/固定隧道时打印远程入口 | 否 | 当前代码主要是 quick tunnel；固定 named tunnel 需要另行配置 cloudflared |
| 远程上号口令 | `MT_REMOTE_LOGIN_SECRET` | `/remote_login.html` 和相关 API 的可选口令 | 是 | 外网开放时必须设置 |
| Public gate 口令 | `MT_PUBLIC_GATE_TOKEN` | `public_gate.py` 独立运行时的访问口令 | 是 | 可复用 `MT_REMOTE_LOGIN_SECRET` |
| 全局代理 | `HTTPS_PROXY`、`HTTP_PROXY`、`ALL_PROXY` | Python/curl 请求默认出口 | 是 | 不建议写入仓库；当前 `local_proxy.env` 已被 Git 忽略，且程序不再自动加载 |
| 代理池 | `local_proxy_pool.txt` | 账号/抢购代理池 | 是 | 已被 Git 忽略；部署时单独下发 |
| Bingtop 账号 | `BINGTOP_USERNAME`、`BINGTOP_PASSWORD` | 易盾点击验证码三方识别 | 是 | 放环境变量或本机私有配置 |
| Bingtop 接口 | `BINGTOP_ENDPOINT`、`BINGTOP_CAPTCHA_TYPE`、`BINGTOP_CLICK_CAPTCHA_TYPE`、`BINGTOP_TIMEOUT_MS` | Bingtop 请求参数 | 部分 | 平台变更时可改 |
| TTOCR appkey | `TTOCR_APPKEY` | 备用验证码平台 appkey | 是 | 不建议使用代码里的默认值作为部署密钥 |

## 本地服务与端口

| 配置 | 位置 | 默认 | 用途 |
| --- | --- | --- | --- |
| WebUI 监听地址 | `MT_WEBUI_BIND` | `0.0.0.0` | WebUI/API 绑定地址 |
| WebUI 主端口 | `MT_WEBUI_PORT` | `8989` | WebUI/API 主端口 |
| 分布式 WS 监听地址 | `MT_DIST_WS_BIND` | `0.0.0.0` | 主控 WebSocket 绑定地址 |
| 分布式 WS 端口 | `MT_DIST_WS_PORT` | `8765` | worker 连接主控 |
| Go bridge 端口 | `MT_GO_BRIDGE_PORT` | `19091` | `go_bridge/rush_bridge` HTTP 入口 |
| HTTP 桥起始端口 | `MT_NODE_HTTP_BRIDGE_PORT` | `19090` | `nodejs/http-bridge` 起始端口 |
| HTTP 桥数量 | `MT_NODE_BRIDGE_COUNT` | `2` | 启动多少个 HTTP 桥实例 |
| HTTP 桥端口列表 | `MT_BB_NODE_PORTS` / `MT_NODE_BRIDGE_PORTS` | 自动推导 | 显式指定 BB bridge 端口 |
| 本地易盾服务 | `MT_YIDUN_LOCAL_URL` | `http://127.0.0.1:8887` | 默认 `nodejs/yidun-local/app_api.js` |
| 本地易盾 API key | `MT_YIDUN_LOCAL_API_KEY` | `default-key` | 本地易盾服务鉴权字段 |
| 本地 OCR 地址 | `MT_YIDUN_LOCAL_OCR_URL` | `http://127.0.0.1:8000/slide_match` 或空 | ddddocr FastAPI |
| 本地易盾脚本路径 | `MT_YIDUN_LOCAL_SCRIPT` | `nodejs/yidun-local/app_api.js` | 自定义服务脚本 |
| 本地 OCR 目录 | `MT_YIDUN_TUNED_OCR_DIR` | `services/ddddocr/` | 自定义 OCR 模块目录（兼容旧独立目录） |
| BB 自动更新 | `MT_BB_AUTO_UPDATE` | `1`（`start.py`/WebUI） | 定时跑 `update.cjs`（见 `MT_BB_UPDATE_MODE`） |
| BB 更新模式 | `MT_BB_UPDATE_MODE` | `sync` | `sync`=每次拉最新并写入；`auto`=有变更才 apply |
| BB 自动更新间隔秒 | `MT_BB_AUTO_UPDATE_INTERVAL_SEC` | `21600`（6h） | 两次自动检查最小 1800 |
| BB 启动即检查 | `MT_BB_AUTO_UPDATE_ON_START` | `1` | WebUI 绑定后是否立即跑一次 `--auto` |
| BB sign 下载回退 | `MT_BB_UPDATE_SIGN_FALLBACK` | `1` | Bangcle 带 `id` 失败时是否用本地 `runtime/sign_wasm.bin` |
| Runtime 制品目录 | `RUSHBUY_RUNTIME_DIR` | 仓库根 `runtime/` | 覆盖 wasm/version.json 位置（Node/Python 一致） |
| BS-DVID 后端 | `MT_BS_DVID_BACKEND` | `python` | `python`=包 `bs_dvid/`；`node`=`nodejs/bs-dvid/cli.js`（对照） |
| Bangcle 暖场后端 | `MT_BANGCLE_BACKEND` | `python` | `python`=包 `bangcle/`；`node`=`nodejs/bangcle/bangcle_warmup.cjs`（对照） |
| Py/Node 离线对照 | `MT_PARITY_MODE` | 未设 | 设为 `1` 时部分路径启用确定性 PRNG；**CI/本地应用 `pytest tests/test_parity_*.py`**（固定 fixture，无 Bangcle/浩天外网） |
| 浩天常量同步 | `scripts/sync_haotian_constants.sh` | — | 改 JS 设备池后刷新 `bs_dvid/constants.py` 内联块 |

## 抢购链路与性能开关

| 配置 | 默认 | 用途 |
| --- | --- | --- |
| `MT_HTTP_BACKEND` | `node` | 组单、下单、支付等走 go bridge |
| `MT_RUSH_HTTP_NODE` | `1` | 抢购 POST 走 go bridge |
| `MT_BATCH_RUSH_NODE` | `1` | 批量抢购走 go bridge batch |
| `MT_CURL_WORKER_POOL` | `1` | node bridge 使用常驻 curl worker |
| `MT_CURL_WORKER_POOL_SIZE` | 自动 | curl worker 池大小 |
| `MT_BATCH_RUSH_PARALLEL_CAP` | `512` | 批量抢购并发上限 |
| `MT_NODE_BATCH_OUTER_TIMEOUT` | 空 | 强制 batch 外层超时秒数 |
| `MT_NODE_BATCH_OUTER_TIMEOUT_MAX` | `7200` | 自动估算超时上限 |
| `MT_RUSH_JITTER_MS_MAX` | `100` | 每枪随机抖动上限毫秒 |
| `MT_RUSH_SOFT429_RETRY` | `auto` | soft 429 额外 hop；`auto` 按代理池规模（万级池最多 +12 hop） |
| `MT_RUSH_SOFT429_ROTATE` | `1` | Go 遇 HTTP 429 且 body 为 `1`/`2` 时，池≥50 条则换 IP 再试 |
| `MT_RUSH_SOFT429_ROTATE_MIN_POOL` | `50` | 上项生效所需最小代理池条数 |
| `MT_COMPOSE_USE_RUSH_PIN` | `1` | 组单/下单沿用抢购成功出口 |
| `MT_COMPOSE_TWO_STEP` | `1` | 优先购 compose 先 `deliverMethod=-1` 无地址，再带地址组单（对齐 App grad 链路） |
| `MT_RUSH_ALLOW_BRANCH_MISMATCH` | 空 | 设为 `1` 时允许手动 branch 与商品库不一致（默认阻断，减少 branch/one 误打 4293） |
| `MT_RUSH_ALLOW_ACT_MISMATCH` | 空 | 设为 `1` 时允许手动 actId 与 purchaseInfoV2 不一致 |
| `MT_PAY_CHANNEL_ALIPAY` | `71` | `requestPay` 的 `PAY_CHANNEL`（WebUI `/api/pay`、`/api/gateway`、`/api/accounts/pay_link` 默认；Android 可设 `70`） |
| `MT_PAY_ORDER_USE_RUSH_PROXY` | `1` | 支付请求沿用抢购成功出口 |
| `MT_PAY_LINK_USE_RUSH_SUCCESS` | `1` | 转支付链接沿用抢购成功出口 |
| `MT_PROXY_POOL_NO_DIRECT` | 空 | 代理池失败后是否禁止直连兜底 |
| `MT_PROXY_LOG` | 空 | 打印代理轮换调试日志 |
| `MT_PROXY_SHARD_ROUNDS` | `2` | 分片代理轮询圈数 |
| `MT_PROXY_ROTATE_PER_REQUEST` | 空 | 每次请求后轮换代理 |
| `MT_TLS_ENGINE` | `ja3_client` | go bridge TLS 引擎；macOS/Linux 用 `ja3_client`，Windows 可用 `reqrio` |
| `MT_GO_TLS_CLIENT_PROFILE` | `chrome_138` | TLS 指纹 profile |
| `go_bridge_tls_mode.json` | `ja3_client` | UI 保存的 TLS 模式 |
| `mt_global_force_local.json` | `enabled` | UI 保存的全局强制本机直连开关 |

## 分布式部署

| 配置 | 位置 | 默认 | 用途 |
| --- | --- | --- | --- |
| 主控 WS 地址 | `MT_DIST_MASTER_WS` 或 `scripts/run_worker.sh ws://...` | `ws://127.0.0.1:8765` | worker 连接主控 |
| Worker 节点 ID | `MT_DIST_NODE_ID` | 自动生成 | 分布式节点唯一标识 |
| Worker 节点名 | `MT_DIST_NODE_NAME` | 主机名 | WebUI 显示名称 |
| Worker 内存模式 | `MT_DIST_MEMORY_ONLY` | `1` | worker 不落账号状态 |
| Worker 只抢购 | `MT_DIST_RUSH_ONLY` | `1` | compose/verify/submit/pay 留在主控 |
| Worker 依赖检查 | `MT_DIST_SKIP_DEP_CHECK` | `0` | 跳过自动依赖检查 |
| 节点并发 | `dist_node_config.json` | 每节点配置 | WebUI 保存的节点并发 |

## 账号与状态文件

| 文件 | 用途 | Git 状态 |
| --- | --- | --- |
| `accounts.json` | 账号、登录态、设备信息 | 已忽略 |
| `account_addresses.sqlite3` | 地址缓存 | 已忽略 |
| `ui_state.json` | UI 状态、通知等 | 已忽略 |
| `remote_tunnel_url.txt` | cloudflared 临时 URL 输出 | 已忽略 |
| `local_proxy.env` | 本机代理/三方平台私有变量 | 已忽略 |
| `local_proxy_pool.txt` | 代理池 | 已忽略 |

## 当前需要优先处理

1. `frp_mt_ws/frpc.toml` 和 `frp_mt_ws/frps.toml` 被 Git 跟踪，但包含 FRP token。建议改成 `.example` 样例，真实 token 通过 `MT_FRP_TOKEN` 或部署环境写入。
2. `MT_REMOTE_LOGIN_SECRET` 当前是外网开放上号页时最关键的口令；Cloudflared quick tunnel 开启时应显式设置。
3. `MT_CLOUDFLARED_CONSOLE` 默认会公开控制台 quick tunnel；服务器部署或给外部人员上号时建议关闭。
4. `TTOCR_APPKEY`、`BINGTOP_USERNAME`、`BINGTOP_PASSWORD`、代理账号密码都应只放环境变量或本机忽略文件。
