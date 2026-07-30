# scripts

这里放正式运行和部署脚本。

## 常用命令

```bash
bash scripts/bootstrap.sh
bash scripts/run_webui.sh
scripts/run_worker.sh ws://127.0.0.1:8765
bash scripts/healthcheck.sh
```

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `bootstrap.sh` | 初始化 `.venv`、安装依赖、编译 Go bridge |
| `run_webui.sh` | 启动主控 WebUI |
| `run_worker.sh` | 启动分布式 worker 的稳定入口 |
| `start_distributed_worker.sh` | worker 底层启动脚本，由 `run_worker.sh` 调用 |
| `healthcheck.sh` | 检查 WebUI 和 Go bridge |
| `install_systemd_user.sh` | 安装 systemd 用户服务 |

业务源码不要放到这个目录。
