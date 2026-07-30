# 自动化部署

这个项目现在按两种模式启动：

- 桌面模式：`python start.py`，适合本机带 GUI 的环境。
- 服务模式：`scripts/run_webui.sh`，适合 Linux/macOS 服务器、systemd、CI、远程主控和自动化部署。

服务部署不要直接跑 `start.py`，因为它会加载 PyQt6/WebEngine；服务器上建议跑 `webui_server.py`。

## 依赖要求

- Python 3.12。项目只保留 `.venv` 一个虚拟环境，bootstrap 会拒绝非 3.12 环境。
- Node.js + npm，用于 `node_bridge/`、`bs_dvid_node/`、`local_yidun/service/`。
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
