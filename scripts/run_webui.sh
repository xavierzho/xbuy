#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PY_BIN="${MT_PYTHON:-${ROOT_DIR}/.venv/bin/python}"
if [[ ! -x "${PY_BIN}" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PY_BIN="$(command -v python3)"
  else
    PY_BIN="$(command -v python)"
  fi
fi

export MT_WEBUI_BIND="${MT_WEBUI_BIND:-0.0.0.0}"
export MT_WEBUI_PORT="${MT_WEBUI_PORT:-8989}"
export MT_DIST_WS_PORT="${MT_DIST_WS_PORT:-8765}"
export MT_GO_BRIDGE_PORT="${MT_GO_BRIDGE_PORT:-19091}"
export MT_HTTP_BACKEND="${MT_HTTP_BACKEND:-node}"
export MT_RUSH_HTTP_NODE="${MT_RUSH_HTTP_NODE:-1}"
export MT_BATCH_RUSH_NODE="${MT_BATCH_RUSH_NODE:-1}"
export MT_CURL_WORKER_POOL="${MT_CURL_WORKER_POOL:-1}"

echo "[webui] root=${ROOT_DIR}"
echo "[webui] python=${PY_BIN}"
echo "[webui] bind=${MT_WEBUI_BIND}:${MT_WEBUI_PORT} dist_ws=${MT_DIST_WS_PORT} go_bridge=${MT_GO_BRIDGE_PORT}"

exec "${PY_BIN}" webui_server.py
