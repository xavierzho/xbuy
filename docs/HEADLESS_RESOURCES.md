# 零真机 BB 资源

`runtime.wasm` 与 `sign_wasm.bin` 与 H5 WebView 同源，**本机公网拉取**即可，无需 adb、越狱或 dump。

## 拉最新（推荐）

```bash
bash scripts/bb_fetch_latest.sh
```

等价：

```bash
MT_BB_UPDATE_SIGN_FALLBACK=0 node nodejs/bb/update.cjs --sync
```

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
| runtime.wasm | `resource.moutai519.com.cn` … `stub.*.wasm` |
| sign_wasm.bin | `fk1.moutai519.com.cn/bangcle/api/v1/1/1` |
| sdkVersion | `bs_h5.min.js` |
| appVersion | App Store lookup `com.moutai.mall` |

`bb_sdk.js` 仅在 H5 接口变更时需对照 `tools/audit/` 更新；日常 wasm 更新不必改 SDK 代码。
