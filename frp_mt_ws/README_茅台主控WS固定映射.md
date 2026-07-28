# FRP 固定映射：家里电脑主控 -> 公网服务器节点

目标：

- 家里电脑运行茅台主控，WS 监听 `127.0.0.1:8765`
- 公网服务器运行 `frps`
- 家里电脑运行 `frpc`
- 公网服务器节点固定连接 `ws://127.0.0.1:18765` 或 `ws://公网服务器IP:18765`

## 最省事用法

只需要下载 frp，然后按下面放文件：

- 家里 Windows 主控电脑：把 `frpc.exe` 放到本目录
- 公网 Linux 服务器：把整个 `frp_mt_ws` 目录传过去，再把 `frps` 放到目录里

公网服务器启动：

```bash
chmod +x frps start_frps_server.sh
./start_frps_server.sh
```

家里电脑先打开茅台主控，再双击：

```text
start_frpc_home.bat
```

第一次会让你输入公网服务器 IP，后面会自动保存。

公网服务器节点启动：

```bash
../start_distributed_worker.sh ws://127.0.0.1:18765
```

或者在本目录执行：

```bash
chmod +x start_worker_via_frp.sh
./start_worker_via_frp.sh
```

## 1. 下载 frp

下载地址：

https://github.com/fatedier/frp/releases

需要两个文件：

- 公网服务器：`frps`
- 家里 Windows 电脑：`frpc.exe`

版本要一致，优先用最新稳定版。

## 2. 公网服务器配置

把本目录的 `frps.toml` 传到公网服务器，例如：

```bash
/opt/frp/frps.toml
```

公网服务器启动：

```bash
chmod +x frps
./frps -c frps.toml
```

也可以直接：

```bash
chmod +x frps start_frps_server.sh
./start_frps_server.sh
```

服务器安全组/防火墙至少放行：

- `7000/tcp`：家里电脑 frpc 连 frps
- `18765/tcp`：节点连接主控 WS

当前默认已经固定为只允许公网服务器本机节点访问：

```toml
proxyBindAddr = "127.0.0.1"
```

这样 `18765` 不会暴露给全网，节点本机连：

```bash
./start_distributed_worker.sh ws://127.0.0.1:18765
```

## 3. 家里主控电脑配置

把 Windows 版本的 `frpc.exe` 放到这个目录：

```text
frp_mt_ws\frpc.exe
```

第一次双击 `start_frpc_home.bat` 会自动提示输入公网服务器 IP，并写入 `frpc.toml`。

也可以手动修改 `frpc.toml`：

```toml
serverAddr = "你的公网服务器IP"
```

先启动茅台主控软件，确认日志里有：

```text
[dist] WS 已就绪: ws://0.0.0.0:8765
```

再双击：

```text
start_frpc_home.bat
```

看到 `start proxy success` / `login to server success` 类似日志，就说明映射成功。

## 4. 公网服务器启动节点

如果节点就在公网服务器本机：

```bash
./start_distributed_worker.sh ws://127.0.0.1:18765
```

如果是其他节点电脑通过这台公网服务器中转：

```bash
./start_distributed_worker.sh ws://公网服务器IP:18765
```

## 5. 常见问题

连不上时按这个顺序查：

1. 家里主控软件是否正在运行，`8765` 是否已就绪。
2. 公网服务器 `frps` 是否正在运行。
3. 家里电脑 `frpc` 是否显示登录成功。
4. 服务器安全组是否放行 `7000/tcp`。
5. 如果外部节点要连，服务器安全组还要放行 `18765/tcp`。
6. token 两边必须完全一致。
