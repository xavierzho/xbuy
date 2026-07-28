#!/usr/bin/env python3
"""
调用 bs_dvid_node/cli.js（浩天百度 BS-DVID 完整 7 步流程，与 mt1.9.6 同源）。
需安装 Node.js，并在 bs_dvid_node 目录执行: npm install
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_NODE_DIR = _ROOT / "bs_dvid_node"
_CLI = _NODE_DIR / "cli.js"
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


def _cli_stdout_to_json(stdout_text: str, _stderr_text: str = ""):
    """解析 cli.js 输出；兼容历史版本把 log 打在 stdout 时最后一行 JSON。"""
    s = (stdout_text or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    for line in reversed(s.splitlines()):
        line = line.strip()
        if len(line) > 2 and line[0] == "{" and line[-1] == "}":
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    # 从右向左找第一个 "success" 片段前的对象起始（弱兜底）
    tail = s[s.rfind('{"success"'):] if '{"success"' in s else ""
    if tail:
        try:
            return json.loads(tail)
        except json.JSONDecodeError:
            return None
    return None


def fetch_bs_dvid_full(device_id: str | None = None, timeout: int = 120) -> dict:
    """
    执行浩天完整流程，返回与 getBsDvidWithReport 相同结构的 dict（JSON 解析后）。
    成功时含 success, bsDvid, finalBsDvid, userAgent, deviceId 等字段。
    """
    if not shutil.which("node"):
        return {
            "success": False,
            "error": "未找到 node 命令，请安装 Node.js (https://nodejs.org/)",
        }
    if not _CLI.exists():
        return {
            "success": False,
            "error": f"缺少 {_CLI}，请确认 bs_dvid_node 已部署",
        }

    cmd = ["node", str(_CLI)]
    if device_id:
        cmd.append(str(device_id).strip())

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_NODE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"BS-DVID 请求超时（>{timeout}s）"}
    except Exception as e:
        return {"success": False, "error": str(e)}

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if not out:
        return {
            "success": False,
            "error": f"node 无输出: exit={proc.returncode} stderr={err[:500]}",
        }

    data = _cli_stdout_to_json(out, err)
    if data is None:
        return {
            "success": False,
            "error": f"JSON 解析失败: {out[:200]}",
            "stderr": err[:800],
        }

    if not data.get("success") and proc.returncode not in (0, 2):
        data.setdefault("error", err or f"exit={proc.returncode}")

    return data


def final_bs_dvid_token(result: dict) -> str:
    """从 fetch_bs_dvid_full 结果中取最终用于请求的 BS-DVID 字符串。"""
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
    """
    与 mt1.9.6 test-full-flow initDeviceForLogin 一致：App 行
    android;sdk_version;manufacturer;device。
    从 getBsDvidWithReport 返回的 deviceConfig（或顶层别名）解析。
    """
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
    """Extract the full H5/WebView profile returned by the Haotian flow."""
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
