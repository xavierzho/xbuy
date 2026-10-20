# 自动化部署

这个项目现在按两种模式启动：

- 桌面模式：`python start.py`，适合本机带 GUI 的环境。
- 服务模式：`scripts/run_webui.sh`，适合 Linux/macOS 服务器、systemd、CI、远程主控和自动化部署。

服务部署不要直接跑 `start.py`，因为它会加载 PyQt6/WebEngine；服务器上建议跑 `webui_server.py`。

## 依赖要求

- Python 3.12。项目只保留 `.venv` 一个虚拟环境，bootstrap 会拒绝非 3.12 环境。
- Node.js + npm，仓库根 `npm install`（workspaces：`nodejs/*`）。
- Go，用于编译 `go_bridge/rush_bridge`。
- `curl`，用于健康检查。

## 一键初始化

```bash
cd /Users/zho/Builder/StudioLLC/rushbuy
make bootstrap
```

等价命令：

```bash
scripts/bootstrap.sh
```

如需指定 Python：

```bash
PYTHON_BIN=/usr/local/bin/python3.12 scripts/bootstrap.sh
```

## 本机启动

```bash
make run
```

默认监听：

- WebUI: `http://127.0.0.1:8989/`
- 局域网入口: `http://本机IP:8989/`
- 分布式 WebSocket: `8765`
- Go bridge: `127.0.0.1:19091`

健康检查：

```bash
make health
```

## 环境变量

常用变量：

```bash
MT_WEBUI_BIND=0.0.0.0
MT_WEBUI_PORT=8989
MT_DIST_WS_PORT=8765
MT_GO_BRIDGE_PORT=19091
MT_HTTP_BACKEND=node
MT_RUSH_HTTP_NODE=1
MT_BATCH_RUSH_NODE=1
MT_CURL_WORKER_POOL=1
MT_TLS_ENGINE=ja3_client
```

公网隧道：

```bash
MT_CLOUDFLARED_AUTOSTART=1
MT_CLOUDFLARED_PUBLIC_PAGES=1
MT_CLOUDFLARED_BIN=/usr/local/bin/cloudflared
```

Linux/macOS 上不要使用 Windows 的 `cloudflared.exe`，把对应系统版本的 `cloudflared` 放到 PATH，或设置 `MT_CLOUDFLARED_BIN`。

## Linux systemd 用户服务

初始化依赖后安装用户级服务：

```bash
scripts/install_systemd_user.sh
systemctl --user start rushbuy-webui.service
```

查看状态：

```bash
systemctl --user status rushbuy-webui.service
journalctl --user -u rushbuy-webui.service -f
```

开机保活需要用户 linger：

```bash
loginctl enable-linger "$USER"
```

## 无真机 / 纯本机模拟（推荐服务器）

完整说明（资源从哪来、自动更新 sign_wasm、踩坑）：[HEADLESS_RESOURCES.md](./HEADLESS_RESOURCES.md)

日常抢购、登录、组单**不需要**接 Android adb 或越狱 iPhone。本仓库默认就是在 PC 上**软件模拟** i茅台 Android WebView + 浩天 BS-DVID + go_bridge 发包：

| 能力 | 是否需要真机 |
| --- | --- |
| 发码 / 登录 / 抢购 / 组单 | 否（要**手机号收短信**；HTTP 全在本机） |
| 浩天 BS-DVID | 否（`nodejs/bs-dvid` + Node，发码前自动跑） |
| 代理池 | 否（`local_proxy_pool.txt`） |
| BB `update.cjs` | 否（`--transport local`；sign wasm 走 Bangcle `/api/v1/1/1` **无 id** 拉最新，勿手填 `?id=`） |
| 全局手机发 / 分布式节点 App | **是**（可选；不用即可） |
建议环境：

```bash
# 服务部署（无 Qt）
MT_HTTP_BACKEND=node MT_RUSH_HTTP_NODE=1 MT_BATCH_RUSH_NODE=1
MT_HAOTIAN_BEFORE_VCODE=1          # 发码前浩天（本机 Node）
# 不要开：全局手机发、distributedRushEnabled（除非你真有多台 worker）

# BB 资源：零真机直接拉最新（推荐）
bash scripts/bb_fetch_latest.sh
# 或 curl -X POST http://127.0.0.1:8989/api/bb/update/sync

# 仅检查、有变再 apply
node nodejs/bb/update.cjs --check --transport local
node nodejs/bb/update.cjs --apply --transport local

详见 [HEADLESS_RESOURCES.md](./HEADLESS_RESOURCES.md)。

WebUI 默认 `MT_BB_AUTO_UPDATE=1`、`MT_BB_UPDATE_MODE=sync`：启动 + 每 6h 拉线上最新 wasm 到 `runtime/`。
关闭自动：`MT_BB_AUTO_UPDATE=0`。
```

若本机无法直连茅台 CDN，用**代理池**或 HTTP 代理环境变量给 Node/Python 即可，仍无需真机。

## 分布式 worker

主控机启动 WebUI 后，worker 节点执行：

```bash
MT_TLS_ENGINE=ja3_client MT_DIST_MASTER_WS=ws://主控机IP:8765 make worker
```

或：

```bash
MT_TLS_ENGINE=ja3_client scripts/run_worker.sh ws://主控机IP:8765
```

macOS/Linux 使用 `ja3_client`。`reqrio` 只适用于 Windows 的 `reqrio.dll` 链路。

## 部署前检查

```bash
git status --short
make bootstrap
make run
make health
```

如果 `make health` 失败，优先检查：

- `webui_server.py` 控制台日志。
- `go_bridge/bridge.log`。
- 端口 `8989`、`8765`、`19091` 是否被占用。
- Linux/macOS 上是否已经编译出 `go_bridge/rush_bridge`。
