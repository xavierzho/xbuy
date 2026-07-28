#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${SCRIPT_DIR}"

FRPS_PID=""
MASTER_WS="${1:-ws://127.0.0.1:18765}"
GO_VERSION="${MT_GO_VERSION:-1.24.3}"
FRP_TAG="${MT_FRP_TAG:-v0.68.1}"

cleanup() {
  if [[ -n "${FRPS_PID}" ]] && kill -0 "${FRPS_PID}" >/dev/null 2>&1; then
    echo "[server-all] stop frps pid=${FRPS_PID}"
    kill "${FRPS_PID}" >/dev/null 2>&1 || true
    wait "${FRPS_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

port_listening() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -lnt 2>/dev/null | awk '{print $4}' | grep -Eq "(:|\\])${port}$"
    return $?
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -lnt 2>/dev/null | awk '{print $4}' | grep -Eq "(:|\\])${port}$"
    return $?
  fi
  return 1
}

download_file() {
  local url="$1"
  local out="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --connect-timeout 15 --retry 2 -o "${out}" "${url}"
    return $?
  fi
  if command -v wget >/dev/null 2>&1; then
    wget -O "${out}" "${url}"
    return $?
  fi
  echo "[server-all] missing curl/wget, cannot download ${url}"
  return 1
}

ensure_frps() {
  if [[ -x "./frps" ]]; then
    return 0
  fi
  if [[ -f "./frps" ]]; then
    chmod +x ./frps
    return 0
  fi

  local machine arch ver file tmp work
  machine="$(uname -m)"
  case "${machine}" in
    x86_64|amd64) arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *)
      echo "[server-all] unsupported arch for frp: ${machine}"
      return 1
      ;;
  esac
  ver="${FRP_TAG#v}"
  file="frp_${ver}_linux_${arch}.tar.gz"
  tmp="/tmp/${file}"
  work="/tmp/frp_${ver}_linux_${arch}"
  echo "[server-all] missing frps, downloading frp ${FRP_TAG} linux-${arch}"
  if ! download_file "https://github.com/fatedier/frp/releases/download/${FRP_TAG}/${file}" "${tmp}"; then
    if ! download_file "https://gh.llkk.cc/https://github.com/fatedier/frp/releases/download/${FRP_TAG}/${file}" "${tmp}"; then
      download_file "https://ghproxy.net/https://github.com/fatedier/frp/releases/download/${FRP_TAG}/${file}" "${tmp}"
    fi
  fi
  tar -tzf "${tmp}" >/dev/null
  rm -rf "${work}"
  tar -C /tmp -xzf "${tmp}"
  cp -f "${work}/frps" "${SCRIPT_DIR}/frps"
  chmod +x "${SCRIPT_DIR}/frps"
  if [[ -f "${work}/frpc" ]]; then
    cp -f "${work}/frpc" "${SCRIPT_DIR}/frpc"
    chmod +x "${SCRIPT_DIR}/frpc" || true
  fi
}

ensure_go() {
  if command -v go >/dev/null 2>&1; then
    return 0
  fi
  if [[ -x "/usr/local/go/bin/go" ]]; then
    export PATH="/usr/local/go/bin:${PATH}"
    return 0
  fi
  if [[ -x "${PROJECT_DIR}/.local/go/bin/go" ]]; then
    export PATH="${PROJECT_DIR}/.local/go/bin:${PATH}"
    return 0
  fi

  local machine go_arch prefix tmp url1 url2
  machine="$(uname -m)"
  case "${machine}" in
    x86_64|amd64) go_arch="amd64" ;;
    aarch64|arm64) go_arch="arm64" ;;
    *)
      echo "[server-all] unsupported arch for auto Go install: ${machine}"
      return 1
      ;;
  esac

  if [[ "$(id -u)" == "0" ]]; then
    prefix="/usr/local"
  else
    prefix="${PROJECT_DIR}/.local"
    mkdir -p "${prefix}"
  fi

  tmp="/tmp/go${GO_VERSION}.linux-${go_arch}.tar.gz"
  url1="https://golang.google.cn/dl/go${GO_VERSION}.linux-${go_arch}.tar.gz"
  url2="https://go.dev/dl/go${GO_VERSION}.linux-${go_arch}.tar.gz"
  echo "[server-all] go not found, downloading Go ${GO_VERSION} linux-${go_arch}"
  if ! download_file "${url1}" "${tmp}"; then
    download_file "${url2}" "${tmp}"
  fi
  rm -rf "${prefix}/go"
  tar -C "${prefix}" -xzf "${tmp}"
  export PATH="${prefix}/go/bin:${PATH}"
  go version
}

ensure_go_bridge() {
  local bridge="${PROJECT_DIR}/go_bridge/rush_bridge"
  if [[ -x "${bridge}" ]]; then
    echo "[server-all] go_bridge ready: ${bridge}"
    return 0
  fi
  if [[ ! -d "${PROJECT_DIR}/go_bridge" ]]; then
    echo "[server-all] missing go_bridge dir: ${PROJECT_DIR}/go_bridge"
    return 1
  fi
  ensure_go
  echo "[server-all] build go_bridge/rush_bridge"
  (
    cd "${PROJECT_DIR}/go_bridge"
    go env -w GOPROXY="${GOPROXY:-https://goproxy.cn,direct}" || true
    go env -w GOSUMDB="${GOSUMDB:-sum.golang.google.cn}" || true
    go mod tidy
    go build -o rush_bridge .
    chmod +x rush_bridge || true
  )
}

ensure_frps

if [[ ! -f "${PROJECT_DIR}/distributed_worker.py" ]]; then
  echo "[server-all] missing distributed_worker.py in ${PROJECT_DIR}"
  exit 1
fi
if [[ ! -x "${PROJECT_DIR}/start_distributed_worker.sh" ]]; then
  chmod +x "${PROJECT_DIR}/start_distributed_worker.sh" || true
fi
if [[ ! -x "${SCRIPT_DIR}/start_worker_via_frp.sh" ]]; then
  chmod +x "${SCRIPT_DIR}/start_worker_via_frp.sh" || true
fi

if port_listening 7000; then
  echo "[server-all] frps already listening on 7000, skip starting frps"
else
  echo "[server-all] start frps: 0.0.0.0:7000 -> 127.0.0.1:18765"
  ./frps -c frps.toml &
  FRPS_PID="$!"

  sleep 0.8
  if ! kill -0 "${FRPS_PID}" >/dev/null 2>&1; then
    echo "[server-all] frps exited early"
    wait "${FRPS_PID}" || true
    exit 1
  fi

  echo "[server-all] frps pid=${FRPS_PID}"
fi

ensure_go_bridge

echo "[server-all] now start worker via ${MASTER_WS}"
echo "[server-all] home master must run frpc: start_frpc_home.bat"
cd "${PROJECT_DIR}"
if [[ -x ".venv/bin/python" ]]; then
  export MT_PYTHON="${PROJECT_DIR}/.venv/bin/python"
fi

exec "${SCRIPT_DIR}/start_worker_via_frp.sh" "${MASTER_WS}"
