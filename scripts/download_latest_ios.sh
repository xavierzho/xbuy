#!/usr/bin/env bash
# 模拟 iPhone Safari 打开官方下载页，解析 App Store 链接，并用 iTunes Lookup 拉版本信息。
# 说明：该页面不会提供 .ipa 直链（Android 才有 imoutai.apk）；IPA 需 App Store / ipatool / 越狱机导出。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PAGE_URL="${MT_IOS_PAGE_URL:-https://fe.moutai519.com.cn/mt-frontend/html/mt/download.html}"
BUNDLE_ID="${MT_IOS_BUNDLE_ID:-com.moutai.mall}"
OUT_META="${1:-${MT_IOS_META_OUT:-$ROOT/downloads/imoutai_ios.json}}"
UA="${MT_IOS_UA:-Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1}"
ITUNES_LOOKUP="https://itunes.apple.com/cn/lookup?bundleId=${BUNDLE_ID}&limit=1"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

curl -fsSL \
  -A "$UA" \
  -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" \
  -H "Accept-Language: zh-CN,zh;q=0.9" \
  -o "$tmpdir/page.html" \
  "$PAGE_URL"

python3 - "$tmpdir/page.html" "$PAGE_URL" >"$tmpdir/script_src.txt" <<'PY'
import re, sys
html = open(sys.argv[1], encoding="utf-8", errors="replace").read()
page_url = sys.argv[2]
match = re.search(r'src="([^"]*mt/download/index-[^"]+\.js)"', html)
if not match:
    sys.exit("download page has no mt/download/index-*.js")
src = match.group(1)
if src.startswith("//"):
    src = "https:" + src
elif src.startswith("/"):
    origin = page_url.split("/", 3)[:3]
    src = "/".join(origin) + src
print(src)
PY
script_src="$(cat "$tmpdir/script_src.txt")"

curl -fsSL \
  -A "$UA" \
  -H "Referer: $PAGE_URL" \
  -o "$tmpdir/page.js" \
  "$script_src"

python3 - "$tmpdir/page.js" >"$tmpdir/appstore_url.txt" <<'PY'
import re, sys
js = open(sys.argv[1], encoding="utf-8", errors="replace").read()
match = re.search(r"https://apps\.apple\.com[^\"'\s]+", js)
if not match:
    sys.exit("download script has no App Store url (need iPhone UA / isIos branch)")
print(match.group(0))
PY
appstore_url="$(cat "$tmpdir/appstore_url.txt")"

curl -fsSL "$ITUNES_LOOKUP" -o "$tmpdir/lookup.json"

mkdir -p "$(dirname "$OUT_META")"
python3 - "$OUT_META" "$appstore_url" "$PAGE_URL" "$script_src" "$ITUNES_LOOKUP" "$tmpdir/lookup.json" <<'PY'
import json, pathlib, sys

out, appstore, page, script, lookup_url, lookup_path = sys.argv[1:7]
lookup = json.loads(pathlib.Path(lookup_path).read_text(encoding="utf-8"))
results = lookup.get("results") or []
app = results[0] if results else {}

payload = {
    "bundleId": app.get("bundleId"),
    "version": app.get("version"),
    "trackId": app.get("trackId"),
    "trackViewUrl": app.get("trackViewUrl") or appstore,
    "releaseNotes": app.get("releaseNotes"),
    "minimumOsVersion": app.get("minimumOsVersion"),
    "source": {
        "downloadPage": page,
        "downloadPageScript": script,
        "appStoreUrlFromPageJs": appstore,
        "itunesLookup": lookup_url,
    },
    "ipa": {
        "directDownloadFromOfficialPage": False,
        "note": "官方 H5 在 iOS 上跳转 App Store，不提供 imoutai.ipa。可用 ipatool 或越狱机导出 Payload。",
    },
}

pathlib.Path(out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY

if [[ -n "${MT_IOS_IPA_OUT:-}" ]]; then
  if [[ -n "${MT_IOS_IPA_URL:-}" ]]; then
    part="${MT_IOS_IPA_OUT}.part"
    curl -fL --retry 2 -A "$UA" -o "$part" "$MT_IOS_IPA_URL"
    mv -f "$part" "$MT_IOS_IPA_OUT"
    echo "ipa saved: $MT_IOS_IPA_OUT"
  else
    echo "skip ipa: set MT_IOS_IPA_URL to a direct .ipa URL, or use ipatool / device export." >&2
  fi
fi
