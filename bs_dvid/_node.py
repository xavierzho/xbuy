"""Node cli.js 后端（对照 / 回退）。"""

from __future__ import annotations

import json
import shutil
import subprocess

from settings.paths import PATHS

_CLI = PATHS.node.bs_dvid_cli
_NODE_DIR = _CLI.parent


def _cli_stdout_to_json(stdout_text: str):
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
    tail = s[s.rfind('{"success"'):] if '{"success"' in s else ""
    if tail:
        try:
            return json.loads(tail)
        except json.JSONDecodeError:
            return None
    return None


def fetch_via_node(device_id: str | None, timeout: int) -> dict:
    if not shutil.which("node"):
        return {
            "success": False,
            "error": "未找到 node 命令，请安装 Node.js (https://nodejs.org/)",
        }
    if not _CLI.exists():
        return {
            "success": False,
            "error": f"缺少 {_CLI}，请确认 nodejs/bs-dvid 已部署并在根目录 npm install",
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

    data = _cli_stdout_to_json(out)
    if data is None:
        return {
            "success": False,
            "error": f"JSON 解析失败: {out[:200]}",
            "stderr": err[:800],
        }

    if not data.get("success") and proc.returncode not in (0, 2):
        data.setdefault("error", err or f"exit={proc.returncode}")
    data.setdefault("backend", "node")
    return data
