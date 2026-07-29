# BB SDK Parameter Audit

- Project root: `/Users/zho/Builder/StudioLLC/rushbuy`
- Log directory: `/Users/zho/Builder/StudioLLC/rushbuy/tools/TraceLens/logs/tracelens`
- Result: no observed change in SDK/host; check wasm details below

## Comparison

| Item | node_bridge baseline | latest TraceLens / BBWasmLens observation |
| --- | --- | --- |
| SDK version | `V3.5.0_20260721.2_imaotai` | `V3.5.0_20260721.2_imaotai` |
| BB host | `https://fk1.moutai519.com.cn/bangcle` | `https://fk1.moutai519.com.cn/bangcle` |
| Runtime sign wasm | `v1.1.3.2` | `v1.1.3.2` |
| State __SIGN_WASM_VERSION__ | `v1.0.6` | `v1.1.3.2` |
| State __wasm_sign_version__ | `a2abd765daa2ec438a96fc2d97209be4` | `` |
| Local sign_wasm.bin bytes | `16631` | `16631` |

## Latest Evidence

### SDK versions
- `V3.5.0_20260721.2_imaotai` from `tools/BBWasmLens/dumps/BBWasmLens/wk_storage_1785318244786_loadRequest_20s.json:1` (BBWasmLens cookie)
### Bangcle server wasm versions
- `v1.1.3.2` from `tools/BBWasmLens/dumps/BBWasmLens/wk_storage_1785318244786_loadRequest_20s.json:1` (BBWasmLens globals)
### All wasm version markers
- `v1.1.3.2` from `tools/BBWasmLens/dumps/BBWasmLens/wk_storage_1785318244786_loadRequest_20s.json:1` (BBWasmLens globals)
### Bangcle hosts
- `https://fk1.moutai519.com.cn/bangcle` from `tools/TraceLens/logs/tracelens/tracelens_20260728_201156.log:38` (raw)
### BB headers
- No hits.
### BBWasmLens sign_wasm byte lengths
- `16631` from `tools/BBWasmLens/dumps/BBWasmLens/wk_storage_1785318244786_loadRequest_20s.json:1` (BBWasmLens localStorage)

