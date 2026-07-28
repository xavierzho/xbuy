# rushbuy / xbuy Project Structure

当前目录 `rushbuy` 是工作整理目录；同级 `../xbuy` 是新解压的原始项目，体积更大，并包含 `.git`、反编译产物、分析报告、虚拟环境和 node_modules。

主程序源码目前保持根目录扁平结构，因为多个 Python 文件直接从项目根目录互相导入。不要直接把根目录 Python 文件搬进子目录，除非同步调整 import、打包脚本和启动脚本。

## 启动入口

- `main.py`: 启动 PyQt6 桌面壳，加载 `ui.html` 并内置 WebUI 后端。
- `webui_server.py`: 本地 WebUI HTTP 服务和 `/api/*` 接口。
- `app.py`: 核心业务流程。
- `start.py`: 中文启动脚本。
- `start_distributed_worker.sh` / `start_distributed_worker.bat`: 分布式 worker 启动脚本。
- `scripts/run_webui.sh`: 服务化启动入口，适合 Linux/macOS/CI/systemd 自动化部署。
- `scripts/bootstrap.sh`: 跨平台依赖初始化和 Go bridge 编译脚本。

`../xbuy` 原始项目里也有 `start.py`，作用是直接拉起 `webui_server.py`。

## 核心模块

- `accounts.py`, `address_db.py`, `state_file_backup.py`: 账号、地址和状态持久化。
- `proxy_pool.py`, `local_proxy.env.example`: 代理配置和代理池。
- `bb_client.py`, `shield.py`, `py_bb_new.py`, `bs_dvid.py`: 请求、设备和签名相关运行模块。
- `grab_bot_ops.py`, `distributed_worker.py`, `mobile_relay.py`, `mobile_relay_client.py`: 批量、分布式和移动端桥接能力。
- `product_catalog.py`, `net_time.py`, `public_gate.py`: 商品、时间和公开网关工具。

## UI 和桥接组件

- `ui.html`, `mobile_order.html`, `remote_login.html`: 浏览器页面。
- `desktop_ui.py`: 桌面 UI 辅助代码。
- `node_bridge/`: Node HTTP/TLS 桥。
- `bs_dvid_node/`: BS-DVID Node 实现。
- `go_bridge/`: Go bridge、runtime 和测试。
- `local_yidun/`: 本地服务组件。
- `frp_mt_ws/`, `cloudflared.exe`: 远程访问和隧道相关工具。
- `scripts/`, `deploy/`: 自动化部署脚本和 systemd 模板。

## 原始项目多出的目录

`../xbuy` 相比当前 `rushbuy` 多出：

- `docs/`, `tools/`: 文档和 Frida 工具。
- `reports/`, `extracted/`, `imoutai-1.9.10-decompiled/`: 分析报告、提取结果和反编译产物。
- `frida_hook_libart/`, `frida-ios-dump/`, `phantom-frida/`, `tweak/`, `android-reverse-engineering-skill/`: 逆向分析辅助工作区。
- `.git`, `.venv`, `node_modules`: 原始解压目录里包含版本库、本地环境和依赖缓存。

## 本地数据和忽略策略

以下内容属于本机运行数据或生成物，不建议提交或作为源码整理对象：

- `accounts.json`, `accounts_*.txt`, `account_addresses.sqlite3`
- `ui_state.json`, `state_backups/`
- `local_proxy.env`, `local_proxy_pool.txt`
- `logs/`, `*.log`, `*.err`, `*.out`, `debug_last_api.json`
- `__pycache__/`, `.venv/`, `node_modules/`
- `*.exe`, `*.dll`, `*.bin`, `*.wasm`
- 反编译输出、tombstones、临时构建目录

## 建议整理路线

1. 保留根目录源码布局，先不要移动 Python 主模块。
2. 从 `../xbuy` 选择性同步 `docs/`、`tools/`、`reports/` 中确实要保留的资料。
3. 把账号、代理、运行状态、日志、虚拟环境、依赖缓存和二进制产物视为本地文件。
4. 如果后续要做代码级重构，先把根目录脚本改成包导入，再迁移目录结构。
