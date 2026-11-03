#!/usr/bin/env bash
# 零真机：从 H5 CDN + Bangcle 拉最新 runtime.wasm / sign_wasm.bin 并写入 runtime/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export MT_BB_UPDATE_SIGN_FALLBACK="${MT_BB_UPDATE_SIGN_FALLBACK:-0}"
PY="${MT_PYTHON:-${ROOT}/.venv/bin/python}"
if [[ ! -x "${PY}" ]]; then
  PY="$(command -v python3)"
fi
exec "${PY}" "${ROOT}/scripts/bb_fetch_latest.py" --sync
