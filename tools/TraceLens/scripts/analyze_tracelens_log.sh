#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs/tracelens"

if [[ $# -gt 0 ]]; then
  LOG_FILE="$1"
else
  LOG_FILE="$(ls -t "$LOG_DIR"/*.log 2>/dev/null | head -n 1 || true)"
fi

if [[ -z "${LOG_FILE:-}" || ! -f "$LOG_FILE" ]]; then
  echo "no log file found under $LOG_DIR"
  exit 1
fi

echo "analyzing: $LOG_FILE"
echo

short_lines() {
  awk '{ if (length($0) > 1800) print substr($0, 1, 1800) "..."; else print $0; }'
}

echo "== Summary =="
grep -nE 'TraceLens VERSION|TRACE_VERSION|WKSUMMARY|PURCHASE_SUMMARY|DETAIL_SUMMARY|purchaseInfoV2|detailV2|inventory|canAddCart|forbiddenBuyDesc|itemPriorityActId|startTimeList|startTimes' "$LOG_FILE" | short_lines || true

echo
echo "== Button State =="
grep -nE 'CLICK_ACTIONS_DETAIL|CLICK_ITEM_DETAIL|CLICK_STATE_NODE.*actions\.2|btn-far-right|已售罄|投放' "$LOG_FILE" | short_lines || true

echo "== H5/Fiber =="
grep -nE '\[H5LOG\].*(FIBER_BOOT|ROOT_KEYS|FIBER|STATE|REACT|CLICK|XHR_|FETCH_)|FIBER_BOOT_ERR|FIBER_ERR|pendingProps|memoizedProps|reactKeys|已售罄|购买|下单|库存|预约|20:00' "$LOG_FILE" | short_lines || true

echo
echo "== Native API =="
grep -nE '\[(API|AFN|JSON|NSURLSession|NSMutableURLRequest|REQ|RESP)\]|/xhr/|https?://' "$LOG_FILE" | short_lines || true

echo
echo "== Errors =="
grep -nEi 'crash|exception|error=|FIBER_ERR|STATE_ERR|REACT_ERR|WK.*error|Terminating app' "$LOG_FILE" | short_lines || true
