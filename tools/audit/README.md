# tools/audit

这里放一次性审计、对比、压测脚本。

这些脚本默认从自身路径向上定位项目根目录，因此应从项目根目录执行：

```bash
python tools/audit/compare_py_node_bb.py
python tools/audit/bb_risk_audit.py
python tools/audit/runtime_header_audit.py
python tools/audit/extract_h5_bundles.py --from-trace
python tools/audit/analyze_h5_security_bundle.py
python tools/audit/scan_ios_app_security.py /path/to/moutai.ipa
```

它们不是常驻服务入口，不由 WebUI 自动调用。

## H5 bundle 提取

```bash
.venv/bin/python tools/audit/extract_h5_bundles.py \
  --entry 'https://h5.moutai519.com.cn/mt/item/smsp-detail?appConfig=2_1_2' \
  --from-trace \
  --proxy 'http://127.0.0.1:7897'
```

输出目录默认是 `tools/audit/h5_bundles/<时间>/`：

- `entry.html`: H5 入口页面。
- `resources/`: 下载到的 JS/CSS/JSON 资源。
- `manifest.json`: URL、状态码、大小、sha256、错误信息。

拉取完成后拆出 H5 安全相关 webpack 模块：

```bash
.venv/bin/python tools/audit/analyze_h5_security_bundle.py tools/audit/h5_bundles/<时间>
```

输出：

- `security_bundle_report.json`: 命中的模块、标记、hash。
- `security_modules/`: 从 `bs_h5.min.js` 拆出来的安全相关模块。

注意：`bs_h5.min.js` 属于 BS/百度浩天这层，不直接等同于项目里的 `bb_sdk_1_9_6.js`。

## iOS App 安全 SDK 扫描

```bash
.venv/bin/python tools/audit/scan_ios_app_security.py /path/to/moutai.ipa
.venv/bin/python tools/audit/scan_ios_app_security.py /path/to/Payload/moutai-mall.app
```

输出目录默认是 `tools/audit/ios_app_security/`：

- `ios_app_security_report.md`: 人可读摘要。
- `ios_app_security_report.json`: 完整文件清单、命中标记、主程序链接库。

用于区分 App 包里的 native/framework 线索和 H5 运行时资源线索。
