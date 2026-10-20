# Node 程序清单（rushbuy）

维护原则：**依赖只在仓库根目录执行一次 `npm install`**（npm workspaces）。**全部实现在 `nodejs/`**。

## 安装

```bash
cd rushbuy
npm install          # 或 bash scripts/bootstrap.sh（含 Python + Go）
```

根目录 [`package.json`](../package.json) workspaces：

| Workspace 包名 | 目录 | 用途 |
| --- | --- | --- |
| `@rushbuy/shared` | `nodejs/shared/` | 仓库根、`runtime/` 路径解析 |
| `@rushbuy/bb` | `nodejs/bb/` | BB SDK、update.cjs、归档 |
| `@rushbuy/gdx` | `nodejs/gdx/` | GDX cookie、yidun_gdx_sdk |
| `@rushbuy/bangcle` | `nodejs/bangcle/` | Bangcle 暖场 |
| `moutai-http-bridge` | `nodejs/http-bridge/` | HTTP 桥 + curl_cffi worker 调度 |
| `@rushbuy/bs-dvid` | `nodejs/bs-dvid/` | 浩天 BS-DVID |
| `@rushbuy/yidun-local` | `nodejs/yidun-local/` | 易盾本地 OCR HTTP |

**WASM 制品**（与 Python `settings.paths` 一致）：仓库根 [`runtime/`](../runtime/)（`runtime.wasm`、`sign_wasm.bin`、`version.json`）。

## 入口一览

### 运行时（Python subprocess）

| 入口 | 路径 | 调用方 |
| --- | --- | --- |
| GDX Cookie | `nodejs/gdx/gdx_cookie.cjs` | `app.py` |
| Bangcle 暖场 | `bangcle/` Python 包（默认）；`nodejs/bangcle/bangcle_warmup.cjs`（`MT_BANGCLE_BACKEND=node` 对照） | `app.py` |
| BS-DVID | `nodejs/bs-dvid/cli.js`（对照 / `MT_BS_DVID_BACKEND=node`） | `bs_dvid/` Python 包（默认） |
| 易盾服务 | `nodejs/yidun-local/app_api.js` | `app.py` |
| HTTP 桥 | `nodejs/http-bridge/server.cjs` | `app.py`（GDX/curl 等）；`bangcle.bridge_client` **已废弃** |

### 运维

| 入口 | 路径 |
| --- | --- |
| BB 更新 | `nodejs/bb/update.cjs` |
| 快捷命令 | 根目录 `npm run bb:sync` 等 |

Python 路径统一见 [`settings/paths.py`](../settings/paths.py)。

## 根目录 npm scripts

```bash
npm run bridge:start
npm run bb:sync
npm run bb:check
npm run gdx:cookie
npm run bangcle:warmup
npm run bs-dvid
npm run yidun:local
```

抢购 HTTPS 默认走 **go_bridge（19091）**；Node 负责 BB 制品、GDX/Bangcle、BS-DVID、易盾服务。环境变量见 [CONFIG_INVENTORY.md](CONFIG_INVENTORY.md)。

## Python OCR（非 Node workspace）

| 目录 | 说明 |
| --- | --- |
| [`services/ddddocr/`](../services/ddddocr/) | 滑块 OCR 模块；`app.py` 拉起 `uvicorn services.ddddocr.main:app :8000`（`pip install -e ".[ddddocr]"`） |
