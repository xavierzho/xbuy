"""
浩天 BS-DVID：默认 Python 实现；`MT_BS_DVID_BACKEND=node` 时走 nodejs/bs-dvid/cli.js 对照。
"""

from __future__ import annotations

import os
import re

from bs_dvid._node import fetch_via_node
from bs_dvid.flow import get_bs_dvid_with_report

_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

_H5_UA_MODEL_BY_CODENAME = {
    "houji": "23127PN0CC",
    "shennong": "23116PN5BC",
    "fuxi": "2211133C",
    "ishtar": "2304FPN6DC",
    "corot": "23117RK66C",
    "dijia": "24129PN74C",
    "haotian": "2410DPN6CC",
    "xuanyuan": "25010PN30C",
    "chenfeng": "24053PY09C",
    "husky": "Pixel 8 Pro",
    "shiba": "Pixel 8",
    "cheetah": "Pixel 7 Pro",
    "caiman": "Pixel 9 Pro",
    "komodo": "Pixel 9 Pro XL",
    "tokay": "Pixel 9",
    "comet": "Pixel 9 Pro Fold",
}


def _backend() -> str:
    return (os.environ.get("MT_BS_DVID_BACKEND") or "python").strip().lower()


def normalize_h5_ua_model(model: str) -> str:
    s = str(model or "").strip()
    return _H5_UA_MODEL_BY_CODENAME.get(s.lower(), s)


def normalize_h5_ua_model_in_user_agent(ua: str) -> str:
    s = str(ua or "").strip()
    if not s:
        return ""

    def repl(m: re.Match) -> str:
        return f"{m.group(1)}{normalize_h5_ua_model(m.group(2))}{m.group(3)}"

    return re.sub(r"(Android\s+[^;]+;\s*)([^;]+?)(\s+Build/)", repl, s, count=1)


def fetch_bs_dvid_full(device_id: str | None = None, timeout: int = 120) -> dict:
    """执行浩天完整 7 步，返回与 getBsDvidWithReport 相同结构的 dict。"""
    if _backend() in ("node", "js", "nodejs"):
        return fetch_via_node(device_id, timeout)

    try:
        opts = {"deviceId": device_id} if device_id else {}
        return get_bs_dvid_with_report(opts)
    except Exception as e:
        return {"success": False, "error": str(e), "backend": "python"}


def final_bs_dvid_token(result: dict) -> str:
    if not result.get("success"):
        return ""
    token = (
        (result.get("finalBsDvid") or result.get("token") or "").strip()
        or (result.get("bsDvid") or "").strip()
    )
    if (
        not token
        or len(token) < 40
        or _IP_RE.match(token)
        or "/" in token
        or ":" in token
    ):
        return ""
    return token


def app_ua_triple_from_haotian_result(res: dict | None) -> tuple[str, str, str]:
    if not isinstance(res, dict):
        return "", "", ""
    dc = res.get("deviceConfig")
    if isinstance(dc, dict):
        sv = str(dc.get("sdk_version") or "").strip()
        mfr = str(dc.get("manufacturer") or "").strip()
        dev = str(dc.get("device") or "").strip()
        if sv and mfr and dev:
            return sv, mfr, dev
    sv = str(res.get("sdkVersion") or "").strip()
    mfr = str(res.get("manufacturer") or "").strip()
    dev = str(res.get("device") or "").strip()
    if sv and mfr and dev:
        return sv, mfr, dev
    return "", "", ""


def h5_profile_from_haotian_result(res: dict | None) -> dict:
    if not isinstance(res, dict):
        return {}
    dc = res.get("deviceConfig")
    if not isinstance(dc, dict):
        dc = {}

    ua = normalize_h5_ua_model_in_user_agent(res.get("userAgent") or "")

    def _first(*vals) -> str:
        for v in vals:
            s = str(v or "").strip()
            if s:
                return s
        return ""

    android_version = _first(res.get("androidVersion"), dc.get("android_version"))
    device_model = normalize_h5_ua_model(
        _first(res.get("uaModel"), dc.get("ua_model"), res.get("device"), dc.get("device"))
    )
    build_id = _first(res.get("buildId"), res.get("build_id"), dc.get("build_id"))
    chrome_version = _first(res.get("chromeVersion"), res.get("chrome_version"))
    h5_device_id = _first(res.get("deviceIdDoubleMd5"))

    if ua:
        m = re.search(r"Android\s+([^;]+);\s*([^;]+?)\s+Build/([^;]+);", ua)
        if m:
            android_version = android_version or m.group(1).strip()
            device_model = normalize_h5_ua_model(device_model or m.group(2).strip())
            build_id = build_id or m.group(3).strip()
        m = re.search(r"Chrome/([\d.]+)", ua)
        if m:
            chrome_version = chrome_version or m.group(1).strip()
        m = re.search(r"device-id/([^\s;]+)", ua)
        if m:
            h5_device_id = h5_device_id or m.group(1).strip()

    return {
        "h5_user_agent": ua,
        "h5_device_id": h5_device_id,
        "haotian_device_id": _first(res.get("deviceId")),
        "h5_android_version": android_version,
        "h5_device_model": device_model,
        "h5_build_id": build_id,
        "h5_chrome_version": chrome_version,
    }


__all__ = [
    "fetch_bs_dvid_full",
    "final_bs_dvid_token",
    "app_ua_triple_from_haotian_result",
    "h5_profile_from_haotian_result",
    "normalize_h5_ua_model",
    "normalize_h5_ua_model_in_user_agent",
    "get_bs_dvid_with_report",
]
