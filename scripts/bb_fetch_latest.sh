#!/usr/bin/env bash
# 零真机：从 H5 CDN + Bangcle 拉最新 runtime.wasm / sign_wasm.bin 并写入 runtime/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export MT_BB_UPDATE_SIGN_FALLBACK="${MT_BB_UPDATE_SIGN_FALLBACK:-0}"
exec node nodejs/bb/update.cjs --sync
