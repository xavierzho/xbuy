#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MASTER_WS="${1:-${MT_DIST_MASTER_WS:-ws://127.0.0.1:18765}}"

FRPS_PORT="${MT_FRPS_PORT:-7000}"
REMOTE_WS_PORT="${MT_REMOTE_WS_PORT:-18765}"
TOKEN="${MT_FRP_TOKEN:-c99893c0ae57d0f9186d9b1439b627ae85cbabc1c9503d49}"

log() {
  echo "[onekey] $*"
}

fail() {
  echo "[onekey] ERROR: $*" >&2
  exit 1
}

need_root() {
  if [[ "$(id -u)" != "0" ]]; then
    fail "please run as root"
  fi
}

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
  local out="$1"
  shift
  local tmp="${out}.tmp"
  rm -f "${tmp}"
  for url in "$@"; do
    [[ -n "${url}" ]] || continue
    log "download: ${url}"
    if command -v curl >/dev/null 2>&1; then
      if curl -fL --connect-timeout 20 --retry 2 -o "${tmp}" "${url}"; then
        mv -f "${tmp}" "${out}"
        return 0
      fi
    elif command -v wget >/dev/null 2>&1; then
      if wget -O "${tmp}" "${url}"; then
        mv -f "${tmp}" "${out}"
        return 0
      fi
    else
      fail "curl/wget missing"
    fi
    rm -f "${tmp}"
  done
  return 1
}

fetch_text() {
  local url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --connect-timeout 12 --retry 1 "${url}"
  else
    wget -qO- "${url}"
  fi
}

repair_apt() {
  log "repair apt/dpkg"
  dpkg --configure -a || true
  apt-get -f install -y || true
  if ! apt-get check >/dev/null 2>&1; then
    log "apt still broken, remove old node packages that often conflict"
    apt-get remove -y libnode-dev nodejs-doc node-gyp npm nodejs || true
    dpkg --configure -a || true
    apt-get -f install -y || true
  fi
}

install_base_packages() {
  repair_apt
  log "install base packages"
  apt-get update || true
  apt-get install -y \
    ca-certificates curl wget tar gzip xz-utils unzip \
    python3 python3-venv python3-pip python3-dev \
    build-essential git procps net-tools openssl || {
      repair_apt
      apt-get install -y \
        ca-certificates curl wget tar gzip xz-utils unzip \
        python3 python3-venv python3-pip python3-dev \
        build-essential git procps net-tools openssl
    }
}

arch_go() {
  case "$(uname -m)" in
    x86_64|amd64) echo "amd64" ;;
    aarch64|arm64) echo "arm64" ;;
    *) fail "unsupported arch for Go: $(uname -m)" ;;
  esac
}

arch_node() {
  case "$(uname -m)" in
    x86_64|amd64) echo "x64" ;;
    aarch64|arm64) echo "arm64" ;;
    *) fail "unsupported arch for Node: $(uname -m)" ;;
  esac
}

arch_frp() {
  case "$(uname -m)" in
    x86_64|amd64) echo "amd64" ;;
    aarch64|arm64) echo "arm64" ;;
    *) fail "unsupported arch for frp: $(uname -m)" ;;
  esac
}

latest_go_version() {
  local data ver
  for url in "https://go.dev/dl/?mode=json" "https://golang.google.cn/dl/?mode=json"; do
    data="$(fetch_text "${url}" 2>/dev/null || true)"
    ver="$(printf '%s' "${data}" | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["version"])' 2>/dev/null || true)"
    if [[ "${ver}" == go* ]]; then
      echo "${ver}"
      return 0
    fi
  done
  echo "go1.24.3"
}

latest_node_lts_version() {
  local data ver
  for url in "https://nodejs.org/dist/index.json" "https://npmmirror.com/mirrors/node/index.json"; do
    data="$(fetch_text "${url}" 2>/dev/null || true)"
    ver="$(printf '%s' "${data}" | python3 -c 'import sys,json; a=json.load(sys.stdin); print(next(x["version"] for x in a if x.get("lts")))' 2>/dev/null || true)"
    if [[ "${ver}" == v* ]]; then
      echo "${ver}"
      return 0
    fi
  done
  echo "v20.20.2"
}

latest_frp_tag() {
  local data tag
  data="$(fetch_text "https://api.github.com/repos/fatedier/frp/releases/latest" 2>/dev/null || true)"
  tag="$(printf '%s' "${data}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["tag_name"])' 2>/dev/null || true)"
  if [[ "${tag}" == v* ]]; then
    echo "${tag}"
    return 0
  fi
  echo "v0.68.1"
}

install_go_latest() {
  if command -v go >/dev/null 2>&1; then
    log "go exists: $(go version)"
    return 0
  fi
  local ver arch file tarball ver_plain
  ver="${MT_GO_VERSION:-$(latest_go_version)}"
  arch="$(arch_go)"
  file="${ver}.linux-${arch}.tar.gz"
  tarball="/tmp/${file}"
  log "install Go ${ver} linux-${arch}"
  download_file "${tarball}" \
    "https://golang.google.cn/dl/${file}" \
    "https://go.dev/dl/${file}" \
    "https://mirrors.aliyun.com/golang/${file}" \
    "https://mirrors.tuna.tsinghua.edu.cn/golang/${file}" || fail "download Go failed"
  tar -tzf "${tarball}" >/dev/null || fail "Go tarball broken: ${tarball}"
  rm -rf /usr/local/go
  tar -C /usr/local -xzf "${tarball}"
  ln -sf /usr/local/go/bin/go /usr/local/bin/go
  ln -sf /usr/local/go/bin/gofmt /usr/local/bin/gofmt
  export PATH="/usr/local/go/bin:${PATH}"
  grep -q '/usr/local/go/bin' ~/.bashrc 2>/dev/null || echo 'export PATH=/usr/local/go/bin:$PATH' >> ~/.bashrc
  log "$(go version)"
}

install_node_lts() {
  local current_major
  current_major="$(node -p 'parseInt(process.versions.node.split(".")[0], 10)' 2>/dev/null || echo 0)"
  if [[ "${current_major}" =~ ^[0-9]+$ ]] && (( current_major >= 18 )); then
    log "node exists: $(node -v), npm $(npm -v 2>/dev/null || true)"
    return 0
  fi
  local ver arch file tarball target
  ver="${MT_NODE_VERSION:-$(latest_node_lts_version)}"
  arch="$(arch_node)"
  file="node-${ver}-linux-${arch}.tar.xz"
  tarball="/tmp/${file}"
  target="/usr/local/node-${ver}-linux-${arch}"
  log "install Node LTS ${ver} linux-${arch}"
  download_file "${tarball}" \
    "https://nodejs.org/dist/${ver}/${file}" \
    "https://npmmirror.com/mirrors/node/${ver}/${file}" \
    "https://mirrors.aliyun.com/nodejs-release/${ver}/${file}" || fail "download Node failed"
  tar -tJf "${tarball}" >/dev/null || fail "Node tarball broken: ${tarball}"
  rm -rf "${target}" /usr/local/node-lts
  tar -C /usr/local -xJf "${tarball}"
  ln -s "${target}" /usr/local/node-lts
  ln -sf /usr/local/node-lts/bin/node /usr/local/bin/node
  ln -sf /usr/local/node-lts/bin/npm /usr/local/bin/npm
  ln -sf /usr/local/node-lts/bin/npx /usr/local/bin/npx
  export PATH="/usr/local/node-lts/bin:${PATH}"
  grep -q '/usr/local/node-lts/bin' ~/.bashrc 2>/dev/null || echo 'export PATH=/usr/local/node-lts/bin:$PATH' >> ~/.bashrc
  log "node $(node -v), npm $(npm -v)"
}

install_frp_latest() {
  cd "${SCRIPT_DIR}"
  if [[ -x "./frps" && -x "./frpc" ]]; then
    log "frp exists: ${SCRIPT_DIR}/frps"
    return 0
  fi
  local tag ver arch file tarball work
  tag="${MT_FRP_TAG:-$(latest_frp_tag)}"
  ver="${tag#v}"
  arch="$(arch_frp)"
  file="frp_${ver}_linux_${arch}.tar.gz"
  tarball="/tmp/${file}"
  work="/tmp/frp_${ver}_linux_${arch}"
  log "install frp ${tag} linux-${arch}"
  download_file "${tarball}" \
    "https://github.com/fatedier/frp/releases/download/${tag}/${file}" \
    "https://gh.llkk.cc/https://github.com/fatedier/frp/releases/download/${tag}/${file}" \
    "https://ghproxy.net/https://github.com/fatedier/frp/releases/download/${tag}/${file}" || fail "download frp failed"
  tar -tzf "${tarball}" >/dev/null || fail "frp tarball broken: ${tarball}"
  rm -rf "${work}"
  tar -C /tmp -xzf "${tarball}"
  cp -f "${work}/frps" "${SCRIPT_DIR}/frps"
  cp -f "${work}/frpc" "${SCRIPT_DIR}/frpc"
  chmod +x "${SCRIPT_DIR}/frps" "${SCRIPT_DIR}/frpc"
}

write_frps_config() {
  cd "${SCRIPT_DIR}"
  cat > frps.toml <<EOF
bindAddr = "0.0.0.0"
bindPort = ${FRPS_PORT}
proxyBindAddr = "127.0.0.1"

auth.method = "token"
auth.token = "${TOKEN}"

allowPorts = [
  { single = ${REMOTE_WS_PORT} }
]
EOF
}

setup_python_venv() {
  cd "${PROJECT_DIR}"
  if [[ ! -x ".venv/bin/python" ]]; then
    log "create python venv"
    rm -rf .venv
    if ! python3 -m venv .venv; then
      local pyver
      pyver="$(python3 -c 'import sys; print(f"python{sys.version_info[0]}.{sys.version_info[1]}-venv")')"
      apt-get install -y "${pyver}" || true
      python3 -m venv .venv
    fi
  fi
  if ! ./.venv/bin/python -m pip --version >/dev/null 2>&1; then
    log "bootstrap pip in venv"
    ./.venv/bin/python -m ensurepip --upgrade || true
    if ! ./.venv/bin/python -m pip --version >/dev/null 2>&1; then
      download_file /tmp/get-pip.py \
        "https://bootstrap.pypa.io/get-pip.py" \
        "https://gh.llkk.cc/https://bootstrap.pypa.io/get-pip.py" || fail "download get-pip.py failed"
      ./.venv/bin/python /tmp/get-pip.py -i https://pypi.tuna.tsinghua.edu.cn/simple
    fi
  fi
  ./.venv/bin/python -m pip install -U pip setuptools wheel -i https://pypi.tuna.tsinghua.edu.cn/simple || true
  local pkgs=(
    "websockets>=13.0"
    "curl_cffi>=0.7.0"
    "requests"
    "PySocks>=1.7.1"
    "gmssl>=3.2.1"
    "pycryptodome>=3.20.0"
    "wasmtime>=24.0.0"
    "fastapi==0.68.0"
    "uvicorn==0.15.0"
    "ddddocr==1.5.5"
    "opencv-python-headless"
    "numpy"
    "Pillow"
    "python-multipart==0.0.5"
  )
  local ok=0
  for index in \
    "https://pypi.tuna.tsinghua.edu.cn/simple" \
    "https://mirrors.aliyun.com/pypi/simple" \
    "https://pypi.org/simple"; do
    log "pip install deps from ${index}"
    if ./.venv/bin/python -m pip install -U "${pkgs[@]}" -i "${index}" --trusted-host "$(echo "${index}" | awk -F/ '{print $3}')"; then
      ok=1
      break
    fi
  done
  [[ "${ok}" == "1" ]] || fail "pip install deps failed"
}

build_go_bridge() {
  cd "${PROJECT_DIR}/go_bridge"
  if [[ -x "./rush_bridge" ]]; then
    log "go_bridge exists: ${PROJECT_DIR}/go_bridge/rush_bridge"
    return 0
  fi
  local ok=0
  for proxy in \
    "https://goproxy.cn,direct" \
    "https://goproxy.io,direct" \
    "https://proxy.golang.org,direct" \
    "direct"; do
    log "go build with GOPROXY=${proxy}"
    go env -w GOPROXY="${proxy}" || true
    go env -w GOSUMDB=sum.golang.google.cn || true
    if go mod tidy && go build -o rush_bridge .; then
      ok=1
      break
    fi
    go env -w GOSUMDB=off || true
    if go mod tidy && go build -o rush_bridge .; then
      ok=1
      break
    fi
  done
  [[ "${ok}" == "1" ]] || fail "go_bridge build failed"
  chmod +x rush_bridge
  ls -lh rush_bridge
}

install_node_bridge_deps() {
  cd "${PROJECT_DIR}/node_bridge"
  if [[ -d "node_modules" ]]; then
    log "node_bridge node_modules exists"
    return 0
  fi
  local ok=0
  for reg in \
    "https://registry.npmmirror.com" \
    "https://registry.npmjs.org"; do
    log "npm install node_bridge deps from ${reg}"
    npm config set registry "${reg}" || true
    if npm install; then
      ok=1
      break
    fi
  done
  [[ "${ok}" == "1" ]] || fail "npm install failed"
}

start_all() {
  cd "${SCRIPT_DIR}"
  if port_listening "${FRPS_PORT}"; then
    log "frps already listening on ${FRPS_PORT}, skip starting frps"
  else
    log "start frps: 0.0.0.0:${FRPS_PORT}, proxy 127.0.0.1:${REMOTE_WS_PORT}"
    nohup ./frps -c frps.toml > frps.log 2>&1 &
    sleep 1
    port_listening "${FRPS_PORT}" || {
      tail -80 frps.log || true
      fail "frps failed to listen on ${FRPS_PORT}"
    }
  fi

  cd "${PROJECT_DIR}"
  chmod +x start_distributed_worker.sh || true
  export PATH="/usr/local/go/bin:/usr/local/node-lts/bin:${PATH}"
  export MT_PYTHON="${PROJECT_DIR}/.venv/bin/python"
  export MT_DIST_MEMORY_ONLY="${MT_DIST_MEMORY_ONLY:-1}"
  export MT_DIST_RUSH_ONLY="${MT_DIST_RUSH_ONLY:-1}"
  export MT_USE_LEGACY_BB="${MT_USE_LEGACY_BB:-0}"
  export MT_RUSH_HTTP_NODE="${MT_RUSH_HTTP_NODE:-1}"
  export MT_BATCH_RUSH_NODE="${MT_BATCH_RUSH_NODE:-1}"
  export MT_HTTP_BACKEND="${MT_HTTP_BACKEND:-node}"
  export MT_CURL_WORKER_POOL="${MT_CURL_WORKER_POOL:-1}"
  export MT_GO_BRIDGE_PORT="${MT_GO_BRIDGE_PORT:-19091}"
  export MT_NODE_BRIDGE_PORT="${MT_NODE_BRIDGE_PORT:-19090}"
  export MT_NODE_BRIDGE_COUNT="${MT_NODE_BRIDGE_COUNT:-2}"
  export MT_BB_NODE_PORTS="${MT_BB_NODE_PORTS:-19090,19092}"

  log "start worker: ${MASTER_WS}"
  log "home master must run frpc to this server first/also; worker can reconnect until ready."
  exec ./start_distributed_worker.sh "${MASTER_WS}"
}

main() {
  need_root
  log "project=${PROJECT_DIR}"
  log "master_ws=${MASTER_WS}"
  install_base_packages
  install_go_latest
  install_node_lts
  install_frp_latest
  write_frps_config
  setup_python_venv
  build_go_bridge
  install_node_bridge_deps
  start_all
}

main "$@"
