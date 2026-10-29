#!/usr/bin/env bash
# systemd 模板实例：rushbuy-worker@<数字>
# 19091 留给本机 WebUI，worker@1 用 19101，worker@2 用 19102，以此类推。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTANCE="${1:?worker instance id}"
MASTER_WS="${2:-${MT_DIST_MASTER_WS:-ws://127.0.0.1:8765}}"

case "${INSTANCE}" in
  ''|*[!0-9]*)
    echo "[worker-instance] instance must be a positive integer, got: ${INSTANCE}" >&2
    exit 1
    ;;
esac
if [[ "${INSTANCE}" -lt 1 ]]; then
  echo "[worker-instance] instance must be >= 1" >&2
  exit 1
fi

export MT_DIST_NODE_ID="worker-${INSTANCE}"
export MT_DIST_NODE_NAME="worker-${INSTANCE}"
export MT_GO_BRIDGE_PORT="$((19100 + INSTANCE))"
export MT_DIST_SKIP_DEP_CHECK="${MT_DIST_SKIP_DEP_CHECK:-1}"
export MT_TLS_ENGINE="${MT_TLS_ENGINE:-ja3_client}"

echo "[worker-instance] id=${MT_DIST_NODE_ID} go_bridge=${MT_GO_BRIDGE_PORT} master=${MASTER_WS}"
exec "${ROOT_DIR}/scripts/run_worker.sh" "${MASTER_WS}"
