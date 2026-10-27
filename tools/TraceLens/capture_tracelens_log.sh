#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT_DIR/logs/tracelens"
mkdir -p "$LOG_DIR"

MODE="summary"
UDID=""
if [[ "${1:-}" == "--raw" ]]; then
  MODE="raw"
  shift
elif [[ "${1:-}" == "--probe" ]]; then
  MODE="probe"
  shift
elif [[ "${1:-}" == "--full" ]]; then
  MODE="full"
  shift
elif [[ "${1:-}" == "--api" ]]; then
  MODE="api"
  shift
fi
if [[ "${1:-}" == "-u" || "${1:-}" == "--udid" ]]; then
  UDID="${2:-}"
  shift 2
fi

STAMP="$(date '+%Y%m%d_%H%M%S')"
OUT="${1:-$LOG_DIR/tracelens_${STAMP}.log}"
ERR_OUT="${OUT}.err"
IDEVICESYSLOG="${IDEVICESYSLOG:-/opt/homebrew/bin/idevicesyslog}"
IDEVICE_ID="${IDEVICE_ID:-/opt/homebrew/bin/idevice_id}"

if [[ "$MODE" == "raw" || "$MODE" == "probe" ]]; then
  PATTERN=''
elif [[ "$MODE" == "api" ]]; then
  PATTERN='moutai-mall\(TraceLens.*(\[AFN\] (dataTaskWithHTTPMethod|dataTask parameters|dataTask headers|success|failure)|\[PAY\])|TraceLens VERSION 0\.0\.4[6-9]|TraceLens VERSION 0\.0\.[5-9]'
elif [[ "$MODE" == "full" ]]; then
  PATTERN='TraceLens|FIBER_BOOT|ROOT_KEYS|FIBER|STATE|REACT|CLICK|WK|WKUNLOCK|WKDOM|API|AFN|JSON|XHR_|FETCH_|NSURLSession|NSMutableURLRequest'
else
  PATTERN='TraceLens VERSION|TRACE_VERSION|WKSUMMARY|WKNET.*(purchaseInfoV2|detailV2|checkRushPurchase)|CLICK_ACTIONS_DETAIL|CLICK_ITEM_DETAIL|CLICK_STATE_NODE.*actions\.2|WKWEBPACK.*hitKeys|WKNET_ERR|WK.*error'
fi

if [[ -n "$UDID" ]]; then
  if ! "$IDEVICE_ID" -l | grep -q "$UDID"; then
    echo "device not found by $IDEVICE_ID: $UDID" >&2
    exit 1
  fi
else
  if ! "$IDEVICE_ID" -l | grep -q .; then
    echo "no device found by $IDEVICE_ID" >&2
    exit 1
  fi
fi

echo "mode: $MODE"
if [[ "$MODE" == "api" ]]; then
  echo "tip: install TraceLens >= 0.0.46 (default log_level=api). full noise: defaults write com.moutai.mall tracelens_log_level full"
fi
if [[ -n "$UDID" ]]; then
  echo "udid: $UDID"
fi
echo "saving TraceLens syslog to: $OUT"
echo "saving logger stderr to: $ERR_OUT"
echo "press Ctrl+C to stop"

LOGGER_PID=""
FIFO="$(mktemp -u "${TMPDIR:-/tmp}/tracelens.XXXXXX")"
cleanup() {
  trap - INT TERM EXIT
  if [[ -n "${LOGGER_PID:-}" ]]; then
    kill "$LOGGER_PID" 2>/dev/null || true
    wait "$LOGGER_PID" 2>/dev/null || true
  fi
  rm -f "$FIFO"
}
trap cleanup INT TERM EXIT

mkfifo "$FIFO"
if [[ -n "$UDID" ]]; then
  "$IDEVICESYSLOG" -u "$UDID" > "$FIFO" 2> "$ERR_OUT" &
else
  "$IDEVICESYSLOG" > "$FIFO" 2> "$ERR_OUT" &
fi
LOGGER_PID="$!"

if [[ "$MODE" == "probe" ]]; then
  tee "$OUT" < "$FIFO" &
  TEE_PID="$!"
  sleep 8
  kill "$TEE_PID" 2>/dev/null || true
  wait "$TEE_PID" 2>/dev/null || true
elif [[ "$MODE" == "raw" ]]; then
  tee "$OUT" < "$FIFO" || true
else
  grep --line-buffered -E "$PATTERN" < "$FIFO" | tee "$OUT" || true
fi

LOGGER_STATUS=0
wait "$LOGGER_PID" 2>/dev/null || LOGGER_STATUS=$?
LOGGER_PID=""
cleanup
if [[ "$LOGGER_STATUS" -ne 0 ]]; then
  echo "warning: idevicesyslog exited with status $LOGGER_STATUS" >&2
fi
if [[ -s "$ERR_OUT" ]]; then
  echo "== idevicesyslog stderr ==" >&2
  cat "$ERR_OUT" >&2
fi
if [[ ! -s "$OUT" ]]; then
  echo "warning: no matching log lines saved to $OUT" >&2
  echo "try: $0 --probe ${UDID:+-u $UDID}" >&2
  echo "or open the target app, then run summary mode again." >&2
fi
