# tools/maintenance

这里放本地状态修复、地址缓存修复、报告生成等维护脚本。

常用示例：

```bash
python tools/maintenance/fill_empty_address_book.py
python tools/maintenance/restore_address_book.py <备份.json>
python tools/maintenance/_report_pay_link_addresses.py
```

这些脚本会读写项目根目录的 `ui_state.json`、`accounts.json`、`account_addresses.sqlite3` 等本地状态文件。
