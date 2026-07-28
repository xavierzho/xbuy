#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -x "./frps" ]]; then
  echo "[frp] 缺少 frps"
  echo "[frp] 请把 Linux amd64 版本 frps 放到当前目录：$(pwd)"
  exit 1
fi

echo "[frp] 启动公网服务器 frps：控制端口 7000，本机 WS 入口 127.0.0.1:18765"
exec ./frps -c frps.toml

