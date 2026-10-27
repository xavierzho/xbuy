# 零真机 BB 资源

`runtime.wasm` 与 `sign_wasm.bin` 与 H5 WebView 同源，**本机公网拉取**即可，无需 adb、越狱或 dump。

## 拉最新（推荐）

```bash
bash scripts/bb_fetch_latest.sh
# 或
python scripts/bb_fetch_latest.py --sync
```

等价：

```bash
MT_BB_UPDATE_SIGN_FALLBACK=0 node nodejs/bb/update.cjs --sync
```

若拉取的 sign 未通过校验、走了本地回退或版本不符合预期，控制台会打印 **`[bb-sign-warn]`**（可用 `MT_BB_SIGN_EXPECT_VERSION=v1.1.7.4` 做版本对比）。

**版本变更归档**：`appVersion` / `versionCode` / `sdkVersion` / `signWasmVersion` / wasm 字节任一变化时，`update.cjs` 会把**更新前**的 `runtime/` 制品与 `version.json` 等复制到 `nodejs/bb/reverse/v<app>-sign-<signVer>/`（重名则加时间戳），控制台输出 `[bb-update] 已归档旧制品 → …`。

流程：H5 商品页 → `bs_h5.min.js` → `stub.*.wasm`；Bangcle `GET /api/v1/1/1`（**无 `?id=`**）→ sign wasm；`run_wac` 校验 → 写入仓库根 `runtime/`。

**唯一制品目录**：`runtime/runtime.wasm`、`runtime/sign_wasm.bin`、`runtime/version.json`（Python `settings.paths` / `project_versions.py`；Node `nodejs/shared/paths.cjs`）。SDK 与 update 脚本在 `nodejs/bb/`。

## 其他模式

| 命令 | 作用 |
| --- | --- |
| `--check` | 只对比 `version.json`，不写盘 |
| `--apply` | 有变更才写盘 |
| `--auto` | App Store + H5/BB，有变更才写盘（`MT_BB_UPDATE_MODE=auto`） |

## WebUI

| API | 作用 |
| --- | --- |
| `POST /api/bb/update/sync` | 等同 `--sync` |
| `POST /api/bb/update/run` | 等同 `--auto` 或 `MT_BB_UPDATE_MODE` |
| `GET /api/bb/resources` | 当前 `version.json` 摘要 |
| `GET /api/bb/update/status` | 上次定时任务结果 |

默认：`MT_BB_AUTO_UPDATE=1`、`MT_BB_UPDATE_MODE=sync`（启动 + 每 6h）。

## 公网来源

| 字段 | 来源 |
| --- | --- |
| `appVersion` / `versionCode` | 默认跟 **iOS** [iTunes lookup](https://itunes.apple.com/cn/lookup?bundleId=com.moutai.mall) 的 `results[0].version`（`com.moutai.mall`，与安卓无关）；`versionCode` 按 `1.9.14→10914` 推导。关闭：`MT_BB_ITUNES_APP_VERSION=0` |
| runtime.wasm | `resource.moutai519.com.cn` … `stub.*.wasm` |
| sign_wasm.bin | `fk1.moutai519.com.cn/bangcle/api/v1/1/1` |

若 CDN 的 `stub.*.wasm` 含 legacy exception（Node 可跑、Python wasmtime 不能编译），`--auto`/`--sync` 会**保留本地** `runtime.wasm`，在 `version.json` 记 `pendingUpstream`，并继续更新 sign / App 版本。强制拒绝：`MT_BB_UPDATE_REQUIRE_WASMTIME=1`。
| sdkVersion | `bs_h5.min.js` |
| appVersion | App Store lookup `com.moutai.mall` |

`bb_sdk.js` 仅在 H5 接口变更时需对照 `tools/audit/` 更新；日常 wasm 更新不必改 SDK 代码。
