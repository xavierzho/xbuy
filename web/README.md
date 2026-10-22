# 主控 Web 静态资源

## 页面

| 文件 | 说明 |
| --- | --- |
| `ui.html` | 主控单页（业务逻辑与 DOM id 勿随意改） |
| `remote_login.html` | 远程上号 |
| `mobile_order.html` | 移动查单/支付 |

须通过 `webui_server` 访问（默认 `http://127.0.0.1:8989/`），不要 `file://` 打开 `ui.html`，否则样式与 API 均不可用。

## 样式分层

| 文件 | 职责 |
| --- | --- |
| `ui-shell.css` | 布局与行为：flex/grid、Tab 显隐、模态定位、`content-visibility`、表单行宽工具类等 |
| `ui-theme.css` | 视觉：设计 token（`:root`）、颜色、边框、扁平化组件皮肤 |

加载顺序（见 `ui.html`）：**shell → theme**（后者覆盖前者）。

改 UI 时：

1. 结构/交互类规则优先放 **shell**（或复用已有 class）。
2. 颜色、圆角、强调色放 **theme**（茅台金 `--accent` 仅主操作与关键数据）。
3. 改完后执行：`python3 scripts/validate_ui_css.py`（`healthcheck.sh` 也会跑）。
4. 整理 shell 格式：`python3 scripts/validate_ui_css.py --format`。
5. 防止 shell 回退旧金色：`python3 scripts/validate_ui_css.py --strict-shell`（可选，CI 可启用）。

`ui-theme.css` 提供少量布局工具类（如 `topbar-btn-gap`、`card-compact`、`label-row-start`、`dist-popover-head`），用于减少 `ui.html` 内联 `style=`。

## 缓存

`ui.html` 中 `?v=` 递增后强刷浏览器；Qt WebEngine 壳需重启或清缓存。
