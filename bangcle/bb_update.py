"""用 Python 拉取 H5 runtime.wasm 和 Bangcle sign wasm。

下载走 curl_cffi（与业务相同的浏览器 TLS）和 local_proxy_pool.txt。
先走代理池；整池拿不到再试一次本机 IP。403 / 429 仍算失败。
发码、抢购不走这里：池拿不到且未开本机 IP 时继续报错。
Node 只用于本地 run_wac 探针：CDN 上的 runtime.wasm 含 legacy try，wasmtime 加载不了。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlparse

from settings.paths import PATHS

DEFAULT_PAGE = "https://h5.moutai519.com.cn/mt/item/smsp-detail?appConfig=2_1_2"
IOS_LOOKUP = "https://itunes.apple.com/cn/lookup?bundleId=com.moutai.mall&limit=1"
BB_DIR = PATHS.repo_root / "nodejs" / "bb"
REVERSE_DIR = BB_DIR / "reverse"
LIVE_STATE_PATH = BB_DIR / "live_bangcle_state.json"
PROBE_PAIR = BB_DIR / "probe_pair.cjs"
APP_KEY = "10001"
WASM_SIGN_VERSION_KEY = "__wasm_sign_version__"

_SDK_URL_RE = (
    re.compile(r"""<script[^>]+id=["']bb-sdk["'][^>]+src=["']([^"']+)["']""", re.I),
    re.compile(r"""<script[^>]+src=["']([^"']*bs_h5\.min\.js[^"']*)["']""", re.I),
)
_SDK_VER_RE = (
    re.compile(r"""sdkVersion[=:]\s*["'](V3\.5\.0[^"']+)["']""", re.I),
    re.compile(r"""["'](V3\.5\.0_\d{8}\.\d+_imaotai)["']"""),
)
_STUB_RE = re.compile(r"""["'](stub(?:\.[A-Za-z0-9_-]+)?\.wasm)["']""")


class BbUpdateError(RuntimeError):
    pass


def _env_on(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _env_off(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("0", "false", "no", "off")


def _global_force_local() -> bool:
    path = PATHS.mt_global_force_local_json
    try:
        if path.is_file():
            return bool(json.loads(path.read_text(encoding="utf-8")).get("enabled"))
    except Exception:
        return False
    return False


def _sign_fallback_allowed() -> bool:
    return not _env_off("MT_BB_UPDATE_SIGN_FALLBACK")


def _proxy_brief(url: str) -> str:
    parsed = urlparse(url)
    user = ""
    if parsed.username:
        user = f"{unquote(parsed.username)}@"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{user}{parsed.hostname or '?'}{port}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _artifact(version: dict[str, Any], key: str, default: str) -> Path:
    block = version.get(key) if isinstance(version.get(key), dict) else {}
    name = str((block or {}).get("file") or default)
    return PATHS.runtime_dir / name


class _Egress:
    """更新专用。先走代理池，整池拿不到再试本机 IP。403 / 429 不算成功。"""

    def __init__(self) -> None:
        self.transport = "direct"
        self._urls: list[str] = []
        self._cursor = 0
        self._mode = "direct"
        self._local_only = False
        self._announced_local = False
        self._impersonate = (os.environ.get("MT_NODE_TLS_IMPERSONATE") or "safari17_0").strip() or "safari17_0"
        if _global_force_local():
            self._local_only = True
            return
        import proxy_pool

        urls = [str(item).strip() for item in proxy_pool.reload_now() if str(item or "").strip()]
        urls = [item for item in urls if "://" in item]
        if not urls:
            self._local_only = True
            return
        self._urls = urls
        self._mode = "pool"
        self.transport = "proxy"

    def get(self, url: str, headers: dict[str, str] | None = None, *, direct: bool = False) -> bytes:
        return self._request("GET", url, headers, None, direct=direct)

    def post(self, url: str, headers: dict[str, str] | None = None, body: str = "") -> bytes:
        bag = dict(headers or {})
        if not any(key.lower() == "content-type" for key in bag):
            bag["content-type"] = "text/plain;charset=UTF-8"
        return self._request("POST", url, bag, body, direct=False)

    def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        body: str | None,
        *,
        direct: bool,
    ) -> bytes:
        host = ""
        try:
            host = urlparse(url).hostname or ""
        except Exception:
            host = ""
        force_direct = direct or host == "itunes.apple.com" or self._mode != "pool" or self._local_only
        if force_direct:
            raw = self._attempt(method, url, headers, body, "")
            self.transport = "direct"
            return raw

        start = self._cursor % len(self._urls)
        for n in range(len(self._urls)):
            proxy = self._urls[(start + n) % len(self._urls)]
            try:
                raw = self._attempt(method, url, headers, body, proxy)
            except BbUpdateError as ex:
                if n + 1 < len(self._urls) and _pool_failure_rotates(str(ex)):
                    continue
                if not _pool_failure_rotates(str(ex)):
                    raise
                break
            self._cursor = (start + n) % len(self._urls)
            self.transport = "proxy"
            return raw
        if not self._announced_local:
            print("[bb-update] 代理池未拿到，改用本机 IP", file=sys.stderr, flush=True)
            self._announced_local = True
        try:
            raw = self._attempt(method, url, headers, body, "")
        except BbUpdateError as ex:
            raise BbUpdateError(f"{ex}（已试代理池 {len(self._urls)} 条，本机 IP 也未拿到）") from None
        self._local_only = True
        self.transport = "direct"
        return raw

    def _attempt(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        body: str | None,
        proxy: str,
    ) -> bytes:
        from curl_cffi import requests as http

        where = f" @ {_proxy_brief(proxy)}" if proxy else ""
        proxies = {"http": proxy, "https": proxy} if proxy else None
        try:
            response = http.request(
                method,
                url,
                headers=headers or {},
                data=None if body is None else body.encode("utf-8"),
                proxies=proxies,
                timeout=45,
                impersonate=self._impersonate,
                allow_redirects=True,
            )
        except Exception as ex:
            raise BbUpdateError(f"{ex}{where}") from None
        status = int(getattr(response, "status_code", 0) or 0)
        raw = response.content or b""
        if 200 <= status < 300 and raw:
            return raw
        detail = "响应为空" if 200 <= status < 300 and not raw else f"HTTP {status or '失败'}"
        raise BbUpdateError(f"{detail}{where}")


def _pool_failure_rotates(detail: str) -> bool:
    """代理池里换下一条的失败。明确的 4xx（除 403 / 407 / 429）停在当条，不改走本机。"""
    text = str(detail or "")
    if "响应为空" in text:
        return True
    if "HTTP " not in text:
        return True
    try:
        status = int(text.split("HTTP ", 1)[1].split()[0])
    except (IndexError, ValueError):
        return True
    return status in (403, 407, 429) or status >= 500 or status == 0


class _BangcleSession:
    def __init__(self) -> None:
        from bangcle.sdk import node_random_id, sm4_key, xor_hex_with_one

        self.smk = node_random_id()
        self.smk1 = sm4_key(self.smk)
        self.smi = xor_hex_with_one(self.smk)

    def process(self, data: Any) -> str:
        from bangcle.sdk import sm4_encrypt_cbc_hex

        if isinstance(data, str):
            plain = data
        else:
            plain = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return self.smi + sm4_encrypt_cbc_hex(plain, self.smk1)

    def unprocess(self, cipher_text: str) -> Any:
        from bangcle.sdk import sm4_decrypt_cbc_hex, sm4_key, xor_hex_with_one

        text = str(cipher_text or "")
        mask = xor_hex_with_one(text[: len(self.smk1)])
        plain = sm4_decrypt_cbc_hex(text[len(self.smk1) :], sm4_key(mask))
        return json.loads(plain)


def _unwrap_stored(storage_key: str, wrapped: Any) -> Any:
    from bangcle.sdk import sm4_decrypt_cbc_hex, sm4_key

    if not wrapped:
        return None
    parsed = json.loads(wrapped) if isinstance(wrapped, str) else wrapped
    if not isinstance(parsed, dict):
        return None
    expired = parsed.get("expiredAt")
    if not expired or float(expired) < time.time() * 1000:
        return None
    if storage_key in ("h_five_event", "__wasm_sign_wasm__"):
        return parsed.get("value")
    value = parsed.get("value")
    if not isinstance(value, str) or not value:
        return None
    return json.loads(sm4_decrypt_cbc_hex(value, sm4_key(APP_KEY)))


def _live_state() -> dict[str, Any] | None:
    if not LIVE_STATE_PATH.is_file():
        return None
    try:
        data = json.loads(LIVE_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _context_from_disk(version: dict[str, Any]) -> dict[str, Any]:
    from bangcle.sdk import node_random_id

    live = _live_state() or {}
    decoded = live.get("decoded") if isinstance(live.get("decoded"), dict) else {}
    wrappers = decoded.get("local_wrappers") if isinstance(decoded.get("local_wrappers"), dict) else {}
    local = live.get("local") if isinstance(live.get("local"), dict) else {}
    session = live.get("session") if isinstance(live.get("session"), dict) else {}
    bb = live.get("bb") if isinstance(live.get("bb"), dict) else {}

    def pick(field: str, storage_key: str, wrapper_key: str = "") -> Any:
        raw = local.get(storage_key)
        try:
            got = _unwrap_stored(storage_key, raw)
        except Exception:
            got = None
        if got not in (None, ""):
            return got
        wrap = wrappers.get(wrapper_key or storage_key)
        if isinstance(wrap, dict) and wrap.get("value") not in (None, ""):
            return wrap.get("value")
        return None

    did = str(pick("did", "__ud") or "")
    ctx = {
        "did": did,
        "hp": str(pick("hp", "__mg__") or ""),
        "startId": str(session.get("__36_i__") or ""),
        "tokenPrepetuity": str(pick("rk", "__rk") or "") or node_random_id(),
        "sdkVersion": str(version.get("sdkVersion") or ""),
        "signWasmVersion": str(version.get("signWasmVersion") or ""),
        "serial": 1,
        "deviceInfo": pick("device", "h_five_cp"),
        "userId": "",
    }
    sign_ver = pick("sign", WASM_SIGN_VERSION_KEY)
    if sign_ver:
        ctx["signWasmVersion"] = str(sign_ver)
    if bb.get("__SIGN_WASM_VERSION__"):
        ctx["signWasmVersion"] = str(bb.get("__SIGN_WASM_VERSION__"))
    serial = pick("serial", "h_five_serial")
    if serial not in (None, ""):
        try:
            ctx["serial"] = int(serial)
        except (TypeError, ValueError):
            pass
    sdk = pick("sdk", "_bb_v_")
    if sdk:
        ctx["sdkVersion"] = str(sdk)
    return ctx


def _bangcle_urls(ctx: dict[str, Any]) -> tuple[str, str, str]:
    from bangcle.constants import (
        DEFAULT_BB_HOST,
        DEFAULT_UPLOAD_PATH,
        DEFAULT_WASM_DOWNLOAD_PATH,
        DEFAULT_WASM_VERSION_LIST_PATH,
    )

    host = str(DEFAULT_BB_HOST).rstrip("/")
    upload = f"{host}{DEFAULT_UPLOAD_PATH}"
    versions = f"{host}{DEFAULT_WASM_VERSION_LIST_PATH}"
    download = f"{host}{DEFAULT_WASM_DOWNLOAD_PATH}"
    return upload, versions, download


def _content_info(session: _BangcleSession, ctx: dict[str, Any]) -> dict[str, str]:
    return {"Content-Info-Bb": session.process([ctx.get("did") or "", _now_ms()])}


def _upload_body(session: _BangcleSession, ctx: dict[str, Any], tactics: int, time_delta: int) -> str:
    from bangcle.sdk import object_to_array

    now = _now_ms()
    payload = {
        "001": str(ctx.get("userId") or ""),
        "002": APP_KEY,
        "003": ctx.get("did") or "",
        "004": ctx.get("startId") or "",
        "005": ctx.get("tokenPrepetuity") or "",
        "006": now,
        "007": int(ctx.get("serial") or 1),
        "008": ctx.get("sdkVersion") or "",
        "009": int(tactics or 0),
        "010": int(time_delta),
        "101": ctx.get("deviceInfo") if isinstance(ctx.get("deviceInfo"), dict) else None,
        "102": [],
    }
    return session.process(object_to_array(payload))


def _apply_strategy(session: _BangcleSession, payload: str, started: int, received: int) -> tuple[int, int]:
    try:
        decoded = session.unprocess(payload)
    except Exception:
        decoded = None
    midpoint = started + round((received - started) / 2)
    if isinstance(decoded, list) and decoded and isinstance(decoded[0], list):
        data = decoded
    else:
        data = [[], midpoint, 6]
    server = int(data[1] or midpoint) if len(data) > 1 else midpoint
    tactics = int(data[2] or 6) if len(data) > 2 else 6
    return tactics, server - midpoint


def _extract_versions(payload: str) -> dict[str, Any]:
    try:
        parsed = json.loads(payload)
    except Exception:
        return {"versions": [], "raw": None}
    rows = parsed.get("v") if isinstance(parsed, dict) else parsed
    versions: list[str] = []
    if isinstance(rows, list):
        for entry in rows:
            if isinstance(entry, str) and entry:
                versions.append(entry)
            elif isinstance(entry, dict):
                value = entry.get("v") or entry.get("version") or ""
                if value:
                    versions.append(str(value))
    return {"versions": versions, "raw": parsed}


def _download_ids(info: dict[str, Any], current: str, requested: str) -> list[str]:
    ids: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in ids:
            ids.append(text)

    add(requested)
    add(current)
    raw = info.get("raw")
    rows = raw.get("v") if isinstance(raw, dict) and isinstance(raw.get("v"), list) else []
    for entry in rows:
        if isinstance(entry, str):
            add(entry)
        elif isinstance(entry, dict):
            add(entry.get("id") or entry.get("hash") or entry.get("i"))
            add(entry.get("v") or entry.get("version"))
    for entry in info.get("versions") or []:
        add(entry)
    live = _live_state() or {}
    decoded = live.get("decoded") if isinstance(live.get("decoded"), dict) else {}
    wrappers = decoded.get("local_wrappers") if isinstance(decoded.get("local_wrappers"), dict) else {}
    hashed = wrappers.get("__wasm_sign_version__")
    if isinstance(hashed, dict):
        add(hashed.get("value"))
    return ids


def _extract_wasm_payload(payload: str) -> dict[str, Any]:
    try:
        parsed = json.loads(payload)
    except Exception:
        parsed = None
    if not isinstance(parsed, dict):
        return {"ok": False, "version": "", "code": b"", "reason": "empty payload"}
    if isinstance(parsed.get("code"), int) and parsed.get("code") == 0 and parsed.get("msg"):
        return {
            "ok": False,
            "version": str(parsed.get("v") or parsed.get("version") or ""),
            "code": b"",
            "reason": str(parsed.get("msg")),
        }
    version = str(parsed.get("v") or parsed.get("version") or "")
    code_b64 = parsed.get("code") if isinstance(parsed.get("code"), str) else ""
    if not version or not str(code_b64).strip():
        return {"ok": False, "version": version, "code": b"", "reason": "code payload empty"}
    try:
        raw = base64.b64decode(str(code_b64).strip())
    except Exception:
        raw = b""
    if not raw:
        return {"ok": False, "version": version, "code": b"", "reason": "decoded code payload empty"}
    return {"ok": True, "version": version, "code": raw, "reason": ""}


def _fetch_sign(
    egress: _Egress,
    version: dict[str, Any],
    requested: str,
) -> dict[str, Any]:
    ctx = _context_from_disk(version)
    session = _BangcleSession()
    upload_url, version_url, download_base = _bangcle_urls(ctx)
    strategy_error = ""
    started = _now_ms()
    try:
        body = egress.post(upload_url, body=session.process(APP_KEY))
        tactics, time_delta = _apply_strategy(session, body.decode("utf-8", "replace"), started, _now_ms())
    except Exception as ex:
        strategy_error = str(ex)
        tactics, time_delta = 6, 0
    version_bytes = egress.get(version_url, _content_info(session, ctx))
    info = _extract_versions(version_bytes.decode("utf-8", "replace"))
    download_ids = _download_ids(info, str(version.get("signWasmVersion") or ""), requested)
    if not download_ids:
        raise BbUpdateError("版本列表中没有 sign wasm 版本")
    try:
        egress.post(upload_url, body=_upload_body(session, ctx, tactics, time_delta))
    except Exception:
        pass

    def try_download(url: str, label: str) -> dict[str, Any] | None:
        raw = egress.get(url, _content_info(session, ctx))
        payload = _extract_wasm_payload(raw.decode("utf-8", "replace"))
        if not payload["ok"] or not payload["code"]:
            return {"reason": payload["reason"] or "empty payload"}
        if label.startswith("v") and payload["version"] and payload["version"] != label:
            raise BbUpdateError(f"sign wasm 版本不匹配: 请求 {label}，返回 {payload['version']}")
        return {
            "bytes": payload["code"],
            "version": payload["version"] or label or str(version.get("signWasmVersion") or ""),
            "versions": info.get("versions") or [],
            "sourceUrl": url,
            "transport": egress.transport,
        }

    latest = try_download(download_base, "")
    if latest and latest.get("bytes"):
        return latest
    last_reason = str((latest or {}).get("reason") or "")
    for download_id in download_ids:
        url = f"{download_base}?id={quote(download_id)}"
        hit = try_download(url, download_id)
        if hit and hit.get("bytes"):
            return hit
        if hit and hit.get("reason"):
            last_reason = str(hit["reason"])
    network = {
        "ok": False,
        "reason": last_reason or "empty payload",
        "versions": info.get("versions") or [],
        "attemptedIds": download_ids,
        "strategyError": strategy_error,
    }
    if not _sign_fallback_allowed():
        raise BbUpdateError(f"sign wasm 下载失败: {network['reason']}（已禁用 bundled 回退）")
    sign_path = _artifact(version, "signModule", "sign_wasm.bin")
    if not sign_path.is_file():
        raise BbUpdateError(f"sign wasm 下载失败: {network['reason']}；且本地无 {sign_path}")
    return {
        "bytes": sign_path.read_bytes(),
        "version": requested or str(version.get("signWasmVersion") or ""),
        "versions": network["versions"],
        "sourceUrl": str(sign_path),
        "transport": "bundled",
        "networkFetch": network,
    }


def _extract_sdk_url(html: str, page_url: str) -> str:
    for pattern in _SDK_URL_RE:
        match = pattern.search(html)
        if match:
            return urljoin(page_url, match.group(1).replace("&amp;", "&"))
    raise BbUpdateError("入口页中未找到 bs_h5.min.js")


def _extract_runtime_url(script: str, sdk_url: str) -> dict[str, str]:
    names = _STUB_RE.findall(script)
    if not names:
        raise BbUpdateError("SDK 脚本中未找到 stub*.wasm")
    return {"upstreamName": names[0], "url": urljoin(sdk_url, names[0])}


def _extract_sdk_version(script: str) -> str:
    for pattern in _SDK_VER_RE:
        match = pattern.search(script)
        if match:
            return match.group(1).strip()
    return ""


def _semver(version: str) -> list[int]:
    core = str(version or "").strip().split("-")[0]
    parts = [int(p) if p.isdigit() else 0 for p in core.split(".")]
    while len(parts) < 3:
        parts.append(0)
    return parts[:3]


def _cmp_semver(left: str, right: str) -> int:
    a, b = _semver(left), _semver(right)
    return (a > b) - (a < b)


def _version_code(app_version: str) -> int | None:
    major, minor, patch = _semver(app_version)
    if not str(app_version or "").strip():
        return None
    return major * 10000 + minor * 100 + patch


def _fetch_store(egress: _Egress) -> dict[str, Any] | None:
    try:
        raw = egress.get(IOS_LOOKUP, direct=True)
        parsed = json.loads(raw.decode("utf-8", "replace"))
        row = parsed.get("results") or []
        if not row or not row[0].get("version"):
            return None
        item = row[0]
        return {
            "appVersion": str(item.get("version") or "").strip(),
            "trackId": item.get("trackId"),
            "releaseDate": item.get("currentVersionReleaseDate") or "",
            "source": "itunes_lookup",
        }
    except Exception as ex:
        return {"error": str(ex), "source": "itunes_lookup"}


def _resolve_app_version(
    current: dict[str, Any],
    store: dict[str, Any] | None,
    *,
    app_version: str,
) -> dict[str, Any]:
    current_ver = str(current.get("appVersion") or "").strip()
    if app_version:
        return {"appVersion": app_version.strip(), "appVersionSource": "cli:--app-version"}
    pinned = (os.environ.get("MT_APP_VERSION") or "").strip()
    if pinned:
        return {"appVersion": pinned, "appVersionSource": "env:MT_APP_VERSION"}
    store_ver = ""
    if isinstance(store, dict) and store.get("appVersion") and not store.get("error"):
        store_ver = str(store.get("appVersion") or "").strip()
    if _env_off("MT_BB_ITUNES_APP_VERSION"):
        out = {"appVersion": current_ver, "appVersionSource": "pinned:MT_BB_ITUNES_APP_VERSION=0"}
        if store_ver:
            out["storeLookupVersion"] = store_ver
        return out
    if not store_ver:
        return {"appVersion": current_ver, "appVersionSource": "unchanged:no_itunes"}
    source = "itunes_lookup" if (not current_ver or store_ver != current_ver) else "itunes_lookup:unchanged"
    return {"appVersion": store_ver, "appVersionSource": source, "storeLookupVersion": store_ver}


def _node_bin() -> str:
    for name in ("node", "nodejs"):
        found = shutil.which(name)
        if found:
            return found
    return "node"


def _probe_pair(runtime_bytes: bytes, sign_bytes: bytes) -> dict[str, Any]:
    if not PROBE_PAIR.is_file():
        raise BbUpdateError(f"缺少 {PROBE_PAIR}")
    tmp_runtime = BB_DIR / f".runtime.{os.getpid()}.{_now_ms()}.wasm"
    tmp_sign = BB_DIR / f".sign.{os.getpid()}.{_now_ms()}.bin"
    try:
        tmp_runtime.write_bytes(runtime_bytes)
        tmp_sign.write_bytes(sign_bytes)
        proc = subprocess.run(
            [_node_bin(), str(PROBE_PAIR), str(tmp_runtime), str(tmp_sign)],
            cwd=str(PATHS.repo_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        tmp_runtime.unlink(missing_ok=True)
        tmp_sign.unlink(missing_ok=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "run_wac 探针失败").strip()
        raise BbUpdateError(f"run_wac 探针失败: {detail[-800:]}")
    try:
        parsed = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception as ex:
        raise BbUpdateError(f"run_wac 探针输出不是 JSON: {ex}") from None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("pair"), dict):
        raise BbUpdateError("run_wac 探针输出不完整")
    return parsed


def _wasmtime_check(runtime_bytes: bytes) -> dict[str, Any]:
    from wasmtime_bb import validate_runtime_wasm_file

    tmp = BB_DIR / f".runtime-wt.{os.getpid()}.{_now_ms()}.wasm"
    try:
        tmp.write_bytes(runtime_bytes)
        validate_runtime_wasm_file(str(tmp))
        return {"ok": True}
    except Exception as ex:
        return {"ok": False, "detail": str(ex)}
    finally:
        tmp.unlink(missing_ok=True)


def _legacy_wasmtime(detail: str) -> bool:
    text = str(detail or "").lower()
    return "legacy_exceptions" in text or "legacy exception" in text


def _archive(current: dict[str, Any]) -> str:
    app = str(current.get("appVersion") or current.get("sdkVersion") or "unknown")
    app = re.sub(r"^v", "", app, flags=re.I)
    sign = re.sub(r"^v", "", str(current.get("signWasmVersion") or ""), flags=re.I)
    base = f"v{app}-sign-{sign}" if sign else f"v{app}"
    destination = REVERSE_DIR / base
    if destination.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "Z"
        destination = REVERSE_DIR / f"{base}-{stamp}"
    optional = {"live_bangcle_state.json", "bb_module.cjs", "randk.js"}
    pairs = [
        ("version.json", PATHS.version_json),
        ("bb_sdk.js", BB_DIR / "bb_sdk.js"),
        ("bb_module.cjs", BB_DIR / "bb_module.cjs"),
        ("randk.js", BB_DIR / "randk.js"),
        ("live_bangcle_state.json", LIVE_STATE_PATH),
        (str((current.get("runtime") or {}).get("file") or "runtime.wasm"), _artifact(current, "runtime", "runtime.wasm")),
        (str((current.get("signModule") or {}).get("file") or "sign_wasm.bin"), _artifact(current, "signModule", "sign_wasm.bin")),
    ]
    for name, src in pairs:
        if not src.is_file() and name not in optional:
            raise BbUpdateError(f"归档源文件缺失: {name} ({src})")
    destination.mkdir(parents=False, exist_ok=False)
    for name, src in pairs:
        if src.is_file():
            shutil.copy2(src, destination / name)
    return str(destination)


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f"{path.name}.next-{os.getpid()}")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def run_bb_update(
    mode: str,
    *,
    page: str = "",
    sign_version: str = "",
    app_version: str = "",
    version_code: str = "",
) -> dict[str, Any]:
    """拉最新资源。check 不写盘。失败抛 BbUpdateError，调用方记录，不把 403 当成成功。"""
    cli_mode = mode if mode in ("check", "apply", "auto", "sync") else "sync"
    page_url = (page or DEFAULT_PAGE).strip()
    current = _read_json(PATHS.version_json)
    egress = _Egress()
    page_bytes = egress.get(page_url)
    page_html = page_bytes.decode("utf-8", "replace")
    sdk_url = _extract_sdk_url(page_html, page_url)
    sdk_bytes = egress.get(sdk_url)
    sdk_text = sdk_bytes.decode("utf-8", "replace")
    runtime_ref = _extract_runtime_url(sdk_text, sdk_url)
    runtime_bytes = egress.get(runtime_ref["url"])
    runtime_wasmtime = _wasmtime_check(runtime_bytes)
    current_runtime = _artifact(current, "runtime", "runtime.wasm")
    current_sign = _artifact(current, "signModule", "sign_wasm.bin")
    sign = _fetch_sign(egress, current, sign_version.strip())
    if cli_mode == "sync" and sign.get("networkFetch"):
        reason = (sign.get("networkFetch") or {}).get("reason") or "unknown"
        raise BbUpdateError(f"sync 必须从 Bangcle/H5 拉取 sign wasm，当前走了本地回退: {reason}")
    sign_bytes = sign["bytes"]
    try:
        probe = _probe_pair(runtime_bytes, sign_bytes)
    except BbUpdateError:
        raise
    store = _fetch_store(egress) if cli_mode in ("auto", "sync", "check", "apply") else None
    app_resolved = _resolve_app_version(current, store, app_version=app_version)
    resolved_app = str(app_resolved.get("appVersion") or "")
    derived_code = _version_code(resolved_app)
    source = str(app_resolved.get("appVersionSource") or "")
    use_derived = source.startswith("itunes_lookup") or source in ("cli:--app-version", "env:MT_APP_VERSION")
    if version_code.strip():
        next_code = int(version_code.strip())
    elif use_derived and derived_code is not None:
        next_code = derived_code
    else:
        next_code = int(current.get("versionCode") or 0)
    runtime_sha = _sha256(runtime_bytes)
    sign_sha = _sha256(sign_bytes)
    nxt: dict[str, Any] = dict(current)
    nxt.update(
        {
            "appVersion": resolved_app,
            "appVersionSource": source,
            "versionCode": next_code,
            "sdkVersion": _extract_sdk_version(sdk_text) or current.get("sdkVersion"),
            "signWasmVersion": sign_version.strip() or sign.get("version") or current.get("signWasmVersion"),
            "runtime": {
                "file": "runtime.wasm",
                "upstreamName": runtime_ref["upstreamName"],
                "sourceUrl": runtime_ref["url"],
                "size": len(runtime_bytes),
                "sha256": runtime_sha,
            },
            "signModule": {
                "file": "sign_wasm.bin",
                "sourceUrl": sign.get("sourceUrl"),
                "versions": sign.get("versions") or [],
                "size": len(sign_bytes),
                "sha256": sign_sha,
            },
            "checkedAt": _iso_now(),
        }
    )
    update_meta = dict(nxt.get("update") or {})
    update_meta.update(
        {
            "script": "bangcle/bb_update.py",
            "syncCommand": "python scripts/bb_fetch_latest.py --sync",
            "autoCommand": "python scripts/bb_fetch_latest.py --auto",
            "checkCommand": "python scripts/bb_fetch_latest.py --check",
            "applyCommand": "python scripts/bb_fetch_latest.py --apply",
        }
    )
    nxt["update"] = update_meta
    changes = {
        "appVersion": current.get("appVersion") != nxt.get("appVersion"),
        "versionCode": int(current.get("versionCode") or 0) != int(nxt.get("versionCode") or 0),
        "sdkVersion": current.get("sdkVersion") != nxt.get("sdkVersion"),
        "runtime": (not current_runtime.is_file()) or _sha256(current_runtime.read_bytes()) != runtime_sha,
        "signModule": (not current_sign.is_file()) or _sha256(current_sign.read_bytes()) != sign_sha,
        "signWasmVersion": current.get("signWasmVersion") != nxt.get("signWasmVersion"),
    }
    store_lookup = str(app_resolved.get("storeLookupVersion") or "")
    store_upgrade = bool(store_lookup) and _cmp_semver(store_lookup, str(current.get("appVersion") or "")) > 0
    bb_changed = changes["runtime"] or changes["signModule"] or changes["signWasmVersion"] or changes["sdkVersion"]
    report: dict[str, Any] = {
        "mode": cli_mode,
        "store": store,
        "appVersionResolve": app_resolved,
        "storeUpgrade": store_upgrade,
        "transport": egress.transport,
        "pageUrl": page_url,
        "sdkUrl": sdk_url,
        "next": nxt,
        "changes": changes,
        "validation": {
            "runtimeExportCount": probe.get("runtimeExportCount"),
            "runtimeWasmtime": runtime_wasmtime,
            "pair": probe.get("pair"),
            "signNetworkFetch": sign.get("networkFetch"),
        },
    }
    if cli_mode == "check":
        return report
    should_apply = (
        cli_mode == "sync"
        or (cli_mode == "apply" and any(changes.values()))
        or (cli_mode == "auto" and (bb_changed or store_upgrade or changes["appVersion"]))
    )
    if not should_apply:
        report["applied"] = False
        report["reason"] = "no_change"
        return report

    runtime_skipped = None
    write_runtime = runtime_bytes
    if not runtime_wasmtime.get("ok"):
        detail = str(runtime_wasmtime.get("detail") or "")
        if not _legacy_wasmtime(detail):
            raise BbUpdateError(detail or "runtime.wasm Python wasmtime 校验失败")
        if _env_on("MT_BB_UPDATE_REQUIRE_WASMTIME") or not current_runtime.is_file():
            raise BbUpdateError(detail or "CDN runtime.wasm 含 legacy try，wasmtime 无法加载；无本地可保留的 runtime.wasm")
        kept = current_runtime.read_bytes()
        runtime_skipped = {
            "reason": "wasmtime_legacy_exceptions",
            "detail": detail,
            "keptSha256": _sha256(kept),
            "upstream": {
                "upstreamName": runtime_ref["upstreamName"],
                "sourceUrl": runtime_ref["url"],
                "size": len(runtime_bytes),
                "sha256": runtime_sha,
            },
        }
        nxt["runtime"] = {
            **(current.get("runtime") or {}),
            "file": "runtime.wasm",
            "size": len(kept),
            "sha256": runtime_skipped["keptSha256"],
            "pendingUpstream": runtime_skipped["upstream"],
        }
        nxt["runtimeWasmtimeSkip"] = runtime_skipped
        changes["runtime"] = False
        write_runtime = b""
        print(
            "[bb-update] 已跳过写入 runtime.wasm（wasmtime 不兼容 legacy try）；"
            f"保留本地文件，仍更新 sign/sdk/appVersion。H5 上游: {runtime_ref['upstreamName']}",
            file=sys.stderr,
            flush=True,
        )
    archived = None
    if any(changes.values()):
        archived = _archive(current)
        print(f"[bb-update] 版本有变更，已归档旧制品 → {archived}", file=sys.stderr, flush=True)
    PATHS.runtime_dir.mkdir(parents=True, exist_ok=True)
    if changes["runtime"] and write_runtime:
        _atomic_write(current_runtime, write_runtime)
    _atomic_write(current_sign, sign_bytes)
    _atomic_write(PATHS.version_json, (json.dumps(nxt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    report["applied"] = True
    report["archivedTo"] = archived
    report["next"] = nxt
    report["changes"] = changes
    return report
