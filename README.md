# rushbuy

## 常用入口

初始化依赖：

```bash
bash scripts/bootstrap.sh   # Python venv + 根目录 npm install (workspaces) + go_bridge
# 仅 Node 依赖：npm install
```

启动主控 WebUI：

```bash
MT_TLS_ENGINE=ja3_client bash scripts/run_webui.sh
```

启动分布式 worker：

```bash
MT_TLS_ENGINE=ja3_client scripts/run_worker.sh ws://127.0.0.1:8765
```

健康检查：

```bash
bash scripts/healthcheck.sh
```

## 文档

- [入口清单](docs/ENTRYPOINTS.md)
- [自动化部署](docs/DEPLOYMENT.md)
- [配置项清单](docs/CONFIG_INVENTORY.md)
- [项目结构](docs/PROJECT_STRUCTURE.md)

## 约定

- 项目只使用 `.venv`，Python 版本固定为 3.12。
- macOS/Linux 默认使用 `MT_TLS_ENGINE=ja3_client`。
- `reqrio` 只适用于 Windows 的 `reqrio.dll` 链路。
- 根目录 Python 文件之间存在直接 import，未完成包化前不要随意搬目录。
