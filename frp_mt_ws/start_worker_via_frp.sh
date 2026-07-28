#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

MASTER_WS="${1:-ws://127.0.0.1:18765}"

if [[ -x ".venv/bin/python" ]]; then
  export MT_PYTHON="${PROJECT_DIR}/.venv/bin/python"
fi

echo "[frp-worker] python=${MT_PYTHON:-python3}"
exec ./start_distributed_worker.sh "${MASTER_WS}"
