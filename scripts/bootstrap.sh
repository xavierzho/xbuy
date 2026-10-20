#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  for candidate in python3.12 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      PYTHON_BIN="${candidate}"
      break
    fi
  done
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "[bootstrap] missing python; install Python 3.12 or set PYTHON_BIN=/path/to/python3.12" >&2
  exit 1
fi

echo "[bootstrap] python=$(${PYTHON_BIN} --version 2>&1)"
PY_MM="$("${PYTHON_BIN}" - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
if [[ "${PY_MM}" != "3.12" ]]; then
  echo "[bootstrap] Python 3.12 is required, got ${PY_MM}. Set PYTHON_BIN=/path/to/python3.12" >&2
  exit 1
fi

if [[ ! -d ".venv" ]]; then
  "${PYTHON_BIN}" -m venv .venv
fi

VENV_PY="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "${VENV_PY}" ]]; then
  echo "[bootstrap] invalid venv: ${VENV_PY}" >&2
  exit 1
fi

"${VENV_PY}" -m pip install --upgrade pip
"${VENV_PY}" -m pip install -r requirements.txt

if [[ -f "${ROOT_DIR}/package.json" ]] && command -v npm >/dev/null 2>&1; then
  echo "[bootstrap] npm install (workspaces: node_bridge, bs_dvid_node, local_yidun/service)"
  (cd "${ROOT_DIR}" && npm install)
elif command -v npm >/dev/null 2>&1; then
  install_node_deps() {
    local dir="$1"
    if [[ -f "${dir}/package.json" ]]; then
      echo "[bootstrap] npm install ${dir}"
      (cd "${dir}" && npm install)
    fi
  }
  install_node_deps "node_bridge"
  install_node_deps "bs_dvid_node"
  install_node_deps "local_yidun/service"
else
  echo "[bootstrap] skip node deps: npm not found" >&2
fi

if [[ -d "go_bridge" ]]; then
  if command -v go >/dev/null 2>&1; then
    echo "[bootstrap] build go_bridge"
    bridge_name="rush_bridge"
    if [[ "${OS:-}" == "Windows_NT" ]]; then
      bridge_name="rush_bridge.exe"
    fi
    (cd go_bridge && go test ./... && go build -o "${bridge_name}" .)
  else
    echo "[bootstrap] skip go_bridge build: go not found" >&2
  fi
fi

echo "[bootstrap] done"
