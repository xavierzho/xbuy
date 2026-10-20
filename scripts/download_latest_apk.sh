#!/usr/bin/env bash
# 模拟手机浏览器打开官方下载页，从页面脚本解析 APK 地址并下载。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PAGE_URL="${MT_APK_PAGE_URL:-https://fe.moutai519.com.cn/mt-frontend/html/mt/download.html}"
OUT="${1:-${MT_APK_OUT:-$ROOT/downloads/imoutai.apk}}"
UA="${MT_APK_UA:-Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36}"

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

python3 - "$tmpdir/page.js" >"$tmpdir/apk_url.txt" <<'PY'
import re, sys
js = open(sys.argv[1], encoding="utf-8", errors="replace").read()
match = re.search(r"https://[^\"'\s]+\.apk", js)
if not match:
    sys.exit("download script has no https apk url")
print(match.group(0))
PY
apk_url="$(cat "$tmpdir/apk_url.txt")"

mkdir -p "$(dirname "$OUT")"
part="$OUT.part"
curl -fL --retry 2 \
  -A "$UA" \
  -H "Referer: $PAGE_URL" \
  -o "$part" \
  "$apk_url"
mv -f "$part" "$OUT"

python3 - "$OUT" "$apk_url" <<'PY'
import hashlib, pathlib, sys
path, url = sys.argv[1], sys.argv[2]
data = pathlib.Path(path).read_bytes()
digest = hashlib.sha256(data).hexdigest()
print(f"url: {url}")
print(f"path: {path}")
print(f"size: {len(data)}")
print(f"sha256: {digest}")
PY
