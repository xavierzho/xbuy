# web

这里放 WebUI 静态页面。

| 文件 | 对外路径 | 用途 |
| --- | --- | --- |
| `ui.html` | `/`、`/ui.html` | 主控页面 |
| `remote_login.html` | `/remote_login.html`、`/remote_login` | 远程上号页面 |
| `mobile_order.html` | `/mobile_order.html`、`/mobile_order` | 查单/支付页面 |

页面由 `webui_server.py` 读取并返回，外部访问路径保持不变。
