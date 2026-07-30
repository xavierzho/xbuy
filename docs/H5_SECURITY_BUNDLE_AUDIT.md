# H5 安全资源审计

本次拉取目录：

`tools/audit/h5_bundles/20260729_225819`

## 结论

这次已经拉到 H5 页面和 BS/百度浩天安全资源，但还没有证明存在一个可直接下载的原始 `bb_sdk_1_9_6.js`。

`bs_h5.min.js` 不是项目里的 `bb_sdk_1_9_6.js` 原文件。项目里的 `bb_sdk_1_9_6.js` 更像是根据 H5 bundle 模块、运行时 storage、Bangcle 接口和 wasm 调用行为整理出来的 Node 适配层。

## 已拉取资源

| 资源 | 说明 |
| --- | --- |
| `entry.html` | H5 入口页面 |
| `resources/001_8f2d34092f_bs_h5_conf.min.js` | BS/百度浩天配置，设置 `_bb_h`、`_bb_w`、`_bb_a` |
| `resources/002_0b47dbf720_bs_h5.min.js` | BS/百度浩天 H5 安全 SDK 主体 |
| `resources/009_abbec75d52_chunk-vendors-6ca066c4.js` | 页面公共 vendor chunk |
| `resources/010_63aef51214_chunk-without-components-pages-c3f29dc1.js` | 页面公共业务 chunk |
| `resources/011_09c5ce1df5_chunk-tetris-b3903daa.js` | 页面业务/组件 chunk |
| `resources/013_c3baddd4d2_index-d58d4a53.js` | 商品详情页业务入口 |

## 命中模块

`tools/audit/analyze_h5_security_bundle.py` 从 `bs_h5.min.js` 拆出了 9 个安全相关模块：

| 模块 | 作用线索 |
| --- | --- |
| `module_9603.js` | 版本、接口路径、storage key、默认 task 配置 |
| `module_1639.js` | `checkAndUpdateSignWasm`，请求 wasm 版本列表和下载 sign wasm |
| `module_6312.js` | `processData`、`unProcessData`、`objectToArray`、设备信息采集 |
| `module_9271.js` | XHR/fetch hook，生成 `Content-Web-Bb`、`Content-Hh-Bb`、`Sdk-Ver-Bb` |
| `module_runWac.js` | 调用 `run_wac` 的包装线索 |
| `module_loadSignWasm.js` | 加载 sign wasm 的包装线索 |
| `module_send.js` | XHR send hook 片段 |
| `module_fetch.js` | fetch hook 片段 |
| `module_value.js` | `checkAndUpdateSignWasm` 调用片段 |

## 与项目还原版的对应关系

| H5 bundle 线索 | 项目还原版 |
| --- | --- |
| `e.v="V3.5.0_20260721.2_imaotai"` | `SDK_VERSION_1_9_6` |
| `e.u="/bbprbdata/upload"` | `DEFAULT_UPLOAD_PATH_1_9_6` |
| `e.wasmVersionListUrl="/api/v1/1/2"` | `DEFAULT_WASM_VERSION_LIST_PATH_1_9_6` |
| `e.wasmDownloadUrl="/api/v1/1/1"` | `DEFAULT_WASM_DOWNLOAD_PATH_1_9_6` |
| `__wasm_sign_wasm__` | `WASM_SIGN_STORAGE_KEY_1_9_6` |
| `__wasm_sign_version__` | `WASM_SIGN_VERSION_KEY_1_9_6` |
| `processData` / `unProcessData` | `processData_1_9_6` / `unProcessData_1_9_6` |
| `objectToArray` | `objectToArray_1_9_6` |
| `Content-Web-Bb` / `Sdk-Ver-Bb` / `Content-Hh-Bb` | `buildRushPurchaseBbHeaders_1_9_6` |
| `checkAndUpdateSignWasm` | `buildBangcleVersionListRequest_1_9_6` + `extractBangcleWasmDownloadPayload_1_9_6` |

## 仍需确认

真正的 BB/邦邦 SDK 如果指的是 App 端 native/framework 层，当前 H5 拉取结果还不能覆盖。下一步要从新版 App 包或运行时加载库里查：

- iOS `Payload/*.app/Frameworks/`
- iOS 主程序 Mach-O 的字符串和符号
- 运行时已加载 dylib/framework 列表
- WebView 运行时是否还有非 `bs_h5` 的额外安全脚本

## 复跑命令

```bash
.venv/bin/python tools/audit/extract_h5_bundles.py --from-trace --limit 80
.venv/bin/python tools/audit/analyze_h5_security_bundle.py tools/audit/h5_bundles/<时间>
```
