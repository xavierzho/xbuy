# Small Update Risk Audit

- Project root: `/Users/zho/Builder/StudioLLC/rushbuy`
- Log directory: `/Users/zho/Builder/StudioLLC/rushbuy/tools/TraceLens/logs/tracelens`
- Result: no high-risk mismatch observed

## Baseline vs Observation

| Item | Local baseline | Latest TraceLens / BBWasmLens observation | Risk |
| --- | --- | --- | --- |
| App version | `1.9.10` | `1.9.10` | `OK` |
| BB SDK | `V3.5.0_20260721.2_imaotai` | `V3.5.0_20260721.2_imaotai` | `OK` |
| BB sign wasm | `v1.1.3.2 / v1.1.3.2` | `v1.1.3.2` | `OK` |
| Chrome/WebView | `138.0.7204.168` | `` | `OK` |
| captchaId | `known mappings in app/local_yidun` | `5f1aab1567f54fdd8c137afed42af465, 22912abb4c484691b71f6efaf778c252` | `OK` |

## Update-sensitive points

- `shield.RELEASE_VER` drives `MT-APP-Version`, app UA `moutaiapp/x`, and MT signing inputs.
- `shield.WASM_TAG` and `node_bridge/bb/bb_module.cjs` `DEFAULT_SIGN_WASM_VERSION` drive BB payload field `140` and `Content-Web-Bb` consistency.
- `SHIELD_VER` / `_sdk_v_` / `Sdk-Ver-Bb` must stay aligned with Bangcle SDK.
- `captchaId` mappings in `app.py` and `local_yidun/service/app_api.js` must match the current H5 page and item code.
- H5 `appConfig` and referer paths affect captcha, BB warmup, and priority verify flow.

## Evidence

### App versions
- `1.9.10` from `tools/TraceLens/logs/tracelens/tracelens_20260728_191606.log:970` (MT-APP-Version)
### App ver_code
- `307` from `tools/TraceLens/logs/tracelens/tracelens_20260724_143719.log:312` (ver_code)
### UA moutaiapp versions
- No hits.
### BB SDK versions
- `V3.5.0_20260721.2_imaotai` from `tools/BBWasmLens/dumps/BBWasmLens/wk_storage_1785318244786_loadRequest_20s.json:1` (BBWasmLens cookie)
### BB wasm versions
- `v1.1.3.2` from `tools/BBWasmLens/dumps/BBWasmLens/wk_storage_1785318244786_loadRequest_20s.json:1` (BBWasmLens globals)
### captchaIds
- `5f1aab1567f54fdd8c137afed42af465` from `tools/TraceLens/logs/tracelens/tracelens_20260728_201646.log:29` (response captchaId)
- `22912abb4c484691b71f6efaf778c252` from `tools/TraceLens/logs/tracelens/tracelens_20260728_201156.log:294` (response captchaId)
### H5 appConfig
- `2_1_2` from `tools/BBWasmLens/dumps/BBWasmLens/runtime_events_1785318244795_loadRequest_20s.json:1` (BBWasmLens runtime)
- `1_1_1` from `tools/BBWasmLens/dumps/BBWasmLens/wk_storage_1785313839773_loadRequest_20s.json:1` (BBWasmLens href)
- `2_0_1` from `tools/BBWasmLens/dumps/BBWasmLens/wk_storage_1785313501998_loadRequest_20s.json:1` (BBWasmLens href)
- `1_0_2` from `tools/TraceLens/logs/tracelens/tracelens_20260728_201646.log:57` (H5 appConfig)
- `1_1_2` from `tools/TraceLens/logs/tracelens/tracelens_20260726_195526.log:9` (H5 appConfig)
### Chrome/WebView versions
- No hits.

