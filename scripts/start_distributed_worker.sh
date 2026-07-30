#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

MASTER_WS="${1:-${MT_DIST_MASTER_WS:-ws://192.168.31.105:8765}}"

echo "[dist-worker] master=${MASTER_WS}"
echo "[dist-worker] cwd=$(pwd)"

# Worker node only sends rush packets and returns priorityRecordId.
# Compose / verify / submit / pay stay on the master machine.
export MT_DIST_MEMORY_ONLY="${MT_DIST_MEMORY_ONLY:-1}"
export MT_DIST_RUSH_ONLY="${MT_DIST_RUSH_ONLY:-1}"
export MT_USE_LEGACY_BB="${MT_USE_LEGACY_BB:-0}"

# Rush packets use the local Go bridge; BB is calculated by shield.py stable py chain.
export MT_RUSH_HTTP_NODE="${MT_RUSH_HTTP_NODE:-1}"
export MT_BATCH_RUSH_NODE="${MT_BATCH_RUSH_NODE:-1}"
export MT_GO_TLS_CLIENT="${MT_GO_TLS_CLIENT:-1}"
export MT_GO_TLS_CLIENT_PROFILE="${MT_GO_TLS_CLIENT_PROFILE:-chrome_138}"
export MT_TLS_ENGINE="${MT_TLS_ENGINE:-ja3_client}"
export MT_HTTP_BACKEND="${MT_HTTP_BACKEND:-node}"
export MT_CURL_WORKER_POOL="${MT_CURL_WORKER_POOL:-1}"

# Optional tuning.
export MT_DIST_SKIP_DEP_CHECK="${MT_DIST_SKIP_DEP_CHECK:-0}"
export MT_GO_BRIDGE_PORT="${MT_GO_BRIDGE_PORT:-19091}"

echo "[dist-worker] dep_check=${MT_DIST_SKIP_DEP_CHECK}"
echo "[dist-worker] memory_only=${MT_DIST_MEMORY_ONLY} rush_only=${MT_DIST_RUSH_ONLY} legacy_bb=${MT_USE_LEGACY_BB}"
echo "[dist-worker] go_bridge=${MT_GO_BRIDGE_PORT} tls_engine=${MT_TLS_ENGINE} tls_client=${MT_GO_TLS_CLIENT} tls_profile=${MT_GO_TLS_CLIENT_PROFILE} bb=stable_py_v1.0.8"

PY_BIN="${MT_PYTHON:-}"
if [[ -z "${PY_BIN}" && -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
  PY_BIN="${VIRTUAL_ENV}/bin/python"
fi
if [[ -z "${PY_BIN}" && -x ".venv/bin/python" ]]; then
  PY_BIN="$(pwd)/.venv/bin/python"
fi
if [[ -z "${PY_BIN}" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PY_BIN="python3"
  else
    PY_BIN="python"
  fi
fi

echo "[dist-worker] python=${PY_BIN}"
exec "${PY_BIN}" distributed_worker.py "${MASTER_WS}"
