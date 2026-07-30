#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

MASTER_WS="${1:-${MT_DIST_MASTER_WS:-ws://127.0.0.1:8765}}"
exec "${ROOT_DIR}/scripts/start_distributed_worker.sh" "${MASTER_WS}"
