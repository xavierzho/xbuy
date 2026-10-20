from __future__ import annotations

import json
import subprocess

from settings.paths import PATHS

_SCRIPT = PATHS.node.bangcle_warmup


def warmup_via_node(payload: dict, timeout: float) -> dict:
    import shutil

    node_exe = shutil.which("node") or shutil.which("nodejs")
    if not node_exe:
        return {"success": False, "message": "node executable not found"}
    if not _SCRIPT.exists():
        return {"success": False, "message": f"missing {_SCRIPT}"}
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    try:
        proc = subprocess.run(
            [node_exe, str(_SCRIPT.name)],
            input=data,
            capture_output=True,
            timeout=timeout,
            cwd=str(_SCRIPT.parent),
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "message": f"node bangcle warmup timeout ({timeout}s)"}
    except Exception as ex:
        return {"success": False, "message": f"node bangcle warmup failed: {ex}"}
    stdout = proc.stdout.decode("utf-8", errors="replace").strip()
    if not stdout:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        return {"success": False, "message": f"node bangcle warmup no output; stderr={stderr[:300]}"}
    out = json.loads(stdout)
    if isinstance(out, dict):
        out.setdefault("backend", "node")
    return out if isinstance(out, dict) else {"success": False, "message": "non-object response"}
