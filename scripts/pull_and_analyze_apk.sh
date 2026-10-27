#!/usr/bin/env bash
# 一键：官方页拉最新 imoutai.apk + 本地静态摘要（libs / dex strings / 对比 runtime/version.json）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APK="${MT_APK_OUT:-$ROOT/downloads/imoutai.apk}"
PY="${ROOT}/.venv/bin/python3"
ANALYZE="${ROOT}/scripts/analyze_apk.py"

"$ROOT/scripts/download_latest_apk.sh" "$APK"
exec "$PY" "$ANALYZE" "$APK"
