# BB SDK Parameter Audit

- Project root: `/Users/zho/Builder/StudioLLC/rushbuy`
- Log directory: `/Users/zho/Builder/StudioLLC/rushbuy/tools/TraceLens/logs/tracelens`
- Result: changed: Runtime sign wasm

## Comparison

| Item | node_bridge baseline | latest TraceLens / live state observation |
| --- | --- | --- |
| SDK version | `V3.5.0_20260605.1_imaotai` | `V3.5.0_20260605.1_imaotai` |
| BB host | `https://fk1.moutai519.com.cn/bangcle` | `https://fk1.moutai519.com.cn/bangcle` |
| Runtime sign wasm | `v1.0.8` | `v1.1.3.2` |
| State __SIGN_WASM_VERSION__ | `v1.0.6` | `v1.1.3.2` |
| State __wasm_sign_version__ | `a2abd765daa2ec438a96fc2d97209be4` | `` |
| Local sign_wasm.bin bytes | `3789` | `3779` |

## Latest Evidence

### SDK versions
- No hits.
### Bangcle server wasm versions
- `v1.1.3.2` from `tools/TraceLens/logs/tracelens/tracelens_20260728_201156.log:38` (bangcle version list)
### All wasm version markers
- `v1.1.3.2` from `tools/TraceLens/logs/tracelens/tracelens_20260728_201156.log:38` (raw)
### Bangcle hosts
- `https://fk1.moutai519.com.cn/bangcle` from `tools/TraceLens/logs/tracelens/tracelens_20260728_201156.log:38` (raw)
### BB headers
- No hits.

