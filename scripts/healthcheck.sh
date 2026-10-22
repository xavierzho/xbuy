#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 "${ROOT}/scripts/validate_ui_css.py"
python3 "${ROOT}/scripts/validate_runtime_wasm.py"

WEBUI_HOST="${WEBUI_HOST:-127.0.0.1}"
WEBUI_PORT="${MT_WEBUI_PORT:-8989}"
GO_BRIDGE_PORT="${MT_GO_BRIDGE_PORT:-19091}"

curl -fsS "http://${WEBUI_HOST}:${WEBUI_PORT}/api/state" >/dev/null
curl -fsS "http://127.0.0.1:${GO_BRIDGE_PORT}/health" >/dev/null

echo "ok"
