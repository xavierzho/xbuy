#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${HOME}/.config/systemd/user"
UNIT_FILE="${UNIT_DIR}/rushbuy-webui.service"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "[systemd] systemctl not found" >&2
  exit 1
fi

mkdir -p "${UNIT_DIR}"
sed "s#__PROJECT_DIR__#${ROOT_DIR}#g" \
  "${ROOT_DIR}/deploy/systemd/rushbuy-webui.service.template" > "${UNIT_FILE}"

systemctl --user daemon-reload
systemctl --user enable rushbuy-webui.service

echo "[systemd] installed: ${UNIT_FILE}"
echo "[systemd] start: systemctl --user start rushbuy-webui.service"
echo "[systemd] logs:  journalctl --user -u rushbuy-webui.service -f"
