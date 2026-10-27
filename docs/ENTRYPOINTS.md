# 项目入口清单

本文件只回答一个问题：日常运行该从哪里进，哪些文件不是启动入口。

## 正式启动入口

| 场景 | 命令 | 说明 |
| --- | --- | --- |
| 初始化依赖 | `bash scripts/bootstrap.sh` | 创建/使用 `.venv`，要求 Python 3.12，安装 Python/Node 依赖并编译 Go bridge |
| 启动主控 WebUI | `bash scripts/run_webui.sh` | 服务器、macOS、本机浏览器测试都用这个入口 |
| 启动分布式 worker | `scripts/run_worker.sh ws://主控IP:8765` | worker 只负责抢购发包，默认 rush-only |
| 健康检查 | `bash scripts/healthcheck.sh` | 检查 WebUI 和 Go bridge 是否可用 |
| BB 零真机更新 | `bash scripts/bb_headless_update.sh` | App Store + H5/Bangcle 自动 `--auto`（见 `docs/HEADLESS_RESOURCES.md`） |
| Makefile 启动 | `make bootstrap` / `make run` / `make worker` / `make health` | 对上面脚本的快捷封装 |

## 本地桌面入口

| 文件 | 作用 | 什么时候用 |
| --- | --- | --- |
| `start.py` | PyQt6/WebEngine 桌面壳，内嵌 WebUI | 只在本机图形界面使用 |
| `main.py` | 直接拉起 `webui_server.py` | 简单本地调试；服务部署优先用 `scripts/run_webui.sh` |

## 后端组件入口

这些文件通常由 WebUI 自动拉起，不建议手动乱跑。

| 文件/目录 | 作用 | 默认端口 |
| --- | --- | --- |
| `webui_server.py` | WebUI HTTP 服务、主控 API、分布式 master WS | `8989` / `8765` |
| `go_bridge/rush_bridge` | Go 发包桥，抢购 `/rush_loop`、批量 `/batch`、预热 `/prewarm` | `19091` |
| `nodejs/http-bridge/server.cjs` | Node HTTP 桥，BB SDK、curl worker | `19090` 起 |
| `public_gate.py` | cloudflared 公开页白名单代理，只暴露上号/查单相关页面 | `18089` 或配置值 |
| `nodejs/yidun-local/app_api.js` | 本地易盾服务 | `8887` |
| 根目录 `package.json` | **npm workspaces**：一次 `npm install` 安装上述 Node 依赖 | — |

Node 入口与脚本对照见 [NODE_APPS.md](NODE_APPS.md)。
| `services/ddddocr/main.py`（`rushbuy-ddddocr` / uvicorn） | 本地 OCR FastAPI | `8000` |

## 前端页面

| 文件 | 对外路径 | 说明 |
| --- | --- | --- |
| `web/ui.html` | `/ui.html`、`/` | 主控 WebUI |
| `web/ui-shell.css` | `/ui-shell.css` | 主控布局与组件结构 |
| `web/ui-theme.css` | `/ui-theme.css` | 主控视觉主题（覆盖 shell） |

主控样式分工与校验见 [`web/README.md`](../web/README.md)。
| `web/remote_login.html` | `/remote_login.html`、`/remote_login` | 远程上号页 |
| `web/mobile_order.html` | `/mobile_order.html`、`/mobile_order` | 移动查单/支付页 |

## 核心库文件

这些是被入口调用的业务模块，不是直接启动入口。

| 文件 | 作用 |
| --- | --- |
| `app.py` | 茅台接口请求、签名、go_bridge 调度、验证码、组单/下单/支付核心逻辑 |
| `accounts.py` | 账号池、登录态、设备信息、地址状态管理 |
| `shield.py` | 当前稳定 BB/sign 生成链路 |
| `shield_legacy.py` | 旧版本 BB/sign 兼容保留 |
| `bangcle/bridge_client.py`（`bb_client.py` 兼容） | **已废弃** — Node `/bb/*` HTTP 对照；抢购 BB 用 `shield.py` |
| `proxy_pool.py` | 代理池读取、轮换和探测 |
| `product_catalog.py` | 商品和路由匹配 |
| `settings/` | 路径、版本号、常用 env 默认值（`paths.py` / `versions.py`） |
| `net_time.py` | 北京时间/服务端时间偏移 |
| `address_db.py` | 地址缓存数据库 |

## 一次性/诊断脚本

这些脚本用于抓包、对齐、审计、验证、补数据，不作为常驻服务入口。

| 文件 | 用途 |
| --- | --- |
| `nodejs/bb/update.cjs --sync` | **零真机**拉最新 H5 runtime + Bangcle sign wasm → `runtime/` |
| `scripts/bb_fetch_latest.sh` | 同上（`MT_BB_UPDATE_SIGN_FALLBACK=0`） |
| `bangcle/`（默认） / `nodejs/bangcle/bangcle_warmup.cjs`（`MT_BANGCLE_BACKEND=node`） | 手动拉取/预热 Bangcle wasm 状态 |
| `tools/audit/compare_py_node_bb.py` | 对比 Python 与 Node 的 BB 输出 |
| `tools/audit/bb_risk_audit.py` | BB 风险字段审计 |
| `tools/audit/runtime_header_audit.py` | 运行时请求头审计 |
| `tools/audit/bench_py_bb_rush_path.py` | BB/rush 路径压测 |
| `tools/audit/verify_update.py` | 小更新后人工验证辅助 |
| `tools/maintenance/fill_empty_address_book.py` | 地址缓存补齐 |
| `tools/maintenance/restore_address_book.py` | 地址缓存恢复 |
| `tools/maintenance/_report_pay_link_addresses.py` | 支付链接/地址排查报告 |
| `tools/audit/compare_files.py` | 文件对比辅助 |
| `tools/ui_mock/mt_native_mock_1to1.py` | 本地模拟/对照脚本 |

## 逆向与抓取工具

这些目录是分析工具，不参与线上启动。

| 目录 | 用途 |
| --- | --- |
| `tools/TraceLens/` | TraceLens 越狱日志：`capture_tracelens_log.sh --full`（`--api` 仅 AFN；H5 抢购需 `--full`） |
| `nodejs/bb/reverse/` | BB 资源历史归档（`update.cjs` 自动写入） |
| `frp_mt_ws/` | FRP 主控/worker 公网映射脚本 |

## 本地数据和生成物

这些不是源码入口，通常不提交 Git。

| 文件/目录 | 用途 |
| --- | --- |
| `.venv/` | 唯一虚拟环境，Python 3.12 |
| `accounts.json` | 账号和登录态 |
| `account_addresses.sqlite3` | 地址缓存 |
| `ui_state.json` | 前端状态 |
| `local_proxy_pool.txt` | 代理池 |
| `logs/`、`*.log`、`*.err`、`*.out` | 运行日志 |
| `go_bridge/rush_bridge` | 本机编译产物 |
| `*.exe`、`*.dll`、`*.bin`、`*.wasm` | 平台二进制/运行时产物 |

## 推荐启动顺序

```bash
cd ~/Builder/StudioLLC/rushbuy
bash scripts/bootstrap.sh
MT_TLS_ENGINE=ja3_client bash scripts/run_webui.sh
```

另开终端启动 worker：

```bash
cd ~/Builder/StudioLLC/rushbuy
MT_TLS_ENGINE=ja3_client scripts/run_worker.sh ws://127.0.0.1:8765
```

macOS/Linux 默认使用 `ja3_client`。`reqrio` 只适用于 Windows 的 `reqrio.dll` 链路。
