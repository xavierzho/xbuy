# Node 程序清单（rushbuy）

维护原则：**依赖只在仓库根目录执行一次 `npm install`**（npm workspaces）。源码仍留在原目录，避免改 Python 里的路径。

## 安装

```bash
cd rushbuy
npm install          # 或 bash scripts/bootstrap.sh（含 Python + Go）
```

根目录 [`package.json`](../package.json) 声明 workspaces：

| Workspace 包名 | 目录 | 用途 |
| --- | --- | --- |
| `moutai-http-bridge` | `node_bridge/` | HTTP 桥、BB 脚本、GDX、暖场 |
| `bs-dvid-moutai` | `bs_dvid_node/` | 浩天 BS-DVID |
| `rushbuy-yidun-local` | `local_yidun/service/` | 易盾本地 OCR HTTP |

## 入口一览

### 运行时（Python 会 subprocess / 自动拉起）

| 入口 | 路径 | 调用方 | 说明 |
| --- | --- | --- | --- |
| GDX Cookie | `node_bridge/gdx_cookie.cjs` | `app.py` `_build_gdx_cookie_via_node` | stdin JSON → stdout `gdxidpyhxdE`；依赖 `yidun_gdx_sdk.js` |
| Bangcle 暖场 | `node_bridge/bangcle_warmup.cjs` | `app.py` `_bangcle_warmup_via_node` | 可选；`MT_BANGCLE_WARMUP_ON_RUSH` |
| BS-DVID | `bs_dvid_node/cli.js` | `bs_dvid.py` | 发码/登录浩天；依赖 `bs-dvid.js` |
| 易盾服务 | `local_yidun/service/app_api.js` | `app.py` `_ensure_yidun_local_service_for_auto` | 默认 `:8887` |
| HTTP 桥（可选） | `node_bridge/server.cjs` | `app.py` `_ensure_node_bridge_running`（日常多不启） | `:19090`；审计 / `bb_client` |

### 运维 / 更新（人工或 WebUI 定时）

| 入口 | 路径 | 调用方 |
| --- | --- | --- |
| BB 资源更新 | `node_bridge/bb/update.cjs` | WebUI `MT_BB_AUTO_UPDATE`、`scripts/bb_fetch_latest.sh` |
| npm 快捷命令 | 根目录 `npm run bb:sync` 等 | 见根 `package.json` scripts |

### 库文件（不单独 `node` 启动）

| 文件 | 作用 |
| --- | --- |
| `node_bridge/bb/bb_sdk.js` / `bb_module.cjs` | BB WASM、sign、Bangcle processData |
| `node_bridge/yidun_gdx_sdk.js` | 易盾 GDX 加密 +（内含）打码相关逻辑 |
| `local_yidun/service/crypto_module.js` `fp.js` `ua_generator.js` | 易盾服务内部模块 |
| `node_bridge/curl_cffi_worker.py` | **Python**；被 `server.cjs` 调 TLS |
| `node_bridge/reverse/**` | `update.cjs` 归档，非运行时 |

### 非 rushbuy 主链路

| 路径 | 说明 |
| --- | --- |
| `tools/root_bypass.js` | 工具脚本，无 package.json |

## 根目录 npm scripts

```bash
npm run bridge:start      # node_bridge/server.cjs
npm run bb:sync           # 拉最新 runtime.wasm + sign_wasm.bin
npm run bb:check
npm run gdx:cookie        # 需自行 pipe JSON 到 stdin
npm run bangcle:warmup
npm run bs-dvid           # bs_dvid_node/cli.js
npm run yidun:local       # local_yidun 服务
```

## 与 Go 的区别

抢购/组单/支付 **HTTPS 默认走 `go_bridge`（19091）**，不是 `node_bridge/server.cjs`。Node 在本项目主要负责：**BB 制品与脚本、GDX/Bangcle 子进程、BS-DVID、易盾 HTTP 服务**。

## 环境变量（Node 相关摘录）

| 变量 | 作用 |
| --- | --- |
| `MT_NODE_EXE` | node 二进制路径 |
| `MT_NODE_HTTP_BRIDGE_PORT` | server.cjs 端口，默认 19090 |
| `MT_YIDUN_LOCAL_SCRIPT` | 覆盖易盾脚本路径 |
| `MT_BB_AUTO_UPDATE` | WebUI 定时跑 `update.cjs` |

完整列表见 [CONFIG_INVENTORY.md](CONFIG_INVENTORY.md)。
