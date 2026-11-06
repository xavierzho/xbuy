# rushbuy

## 常用入口

初始化依赖：

```bash
bash scripts/bootstrap.sh   # .venv + pip install -e ".[ddddocr,dev]" + npm workspaces + go_bridge
# 或手动：python3.12 -m venv .venv && .venv/bin/pip install -e ".[ddddocr]"
# 仅 Node 依赖：npm install
```

启动主控 WebUI：

```bash
MT_TLS_ENGINE=ja3_client bash scripts/run_webui.sh
```

**导入 iMT 已登录账号：**在 WebUI 的「批量导入」粘贴完整的 iMT 导出 JSON（包含 `app: "iMT"` 和 `accounts` 数组），点击「导入登录态」。默认跳过已有手机号；需要替换时勾选覆盖。导入会保存 token、cookie、可用的设备信息和收货地址缓存，不会发送验证码，也不会应用导出包里的代理或分组。导出标记为掉线、失效或 JWT 已过期的记录会跳过，请重新登录后导出。

启动分布式 worker：

```bash
MT_TLS_ENGINE=ja3_client scripts/run_worker.sh ws://127.0.0.1:8765
```

健康检查：

```bash
bash scripts/healthcheck.sh
```

离线 Py/Node 对照（固定 fixture，需本机 `node` 与 `runtime/sign_wasm.bin` 等制品）：

```bash
.venv/bin/pytest tests/test_parity_bs_dvid.py tests/test_parity_bangcle.py -q
python scripts/compare_bs_dvid_backends.py
python scripts/compare_bangcle_backends.py
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
- 可安装包：`bangcle`、`bs_dvid`、`settings`、`services.*`（见 `pyproject.toml`）；根目录 `app.py` / `shield.py` 等仍为脚本入口。
