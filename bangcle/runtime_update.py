"""Headless refresh of runtime/sign_wasm.bin via nodejs/bb/update.cjs (Python entry)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any, Literal

from settings.paths import PATHS

BbUpdateMode = Literal["check", "apply", "auto", "sync"]

_SIGN_ERROR_MARKERS = (
    "run_wac",
    "sign wasm",
    "sign_wasm",
    "validatepair",
    "版本不匹配",
    "empty payload",
    "版本列表",
)


def nodejs_executable() -> str:
    for name in ("node", "nodejs"):
        found = shutil.which(name)
        if found:
            return found
    return "node"


def collect_sign_warnings(
    result: dict[str, Any],
    *,
    expect_sign_version: str | None = None,
) -> list[str]:
    """Heuristics for unusable or risky sign wasm after update.cjs."""
    warnings: list[str] = []
    expect = (expect_sign_version or os.environ.get("MT_BB_SIGN_EXPECT_VERSION") or "").strip()

    if not result.get("ok"):
        err = str(result.get("error") or "")
        low = err.lower()
        if any(m in low for m in _SIGN_ERROR_MARKERS):
            warnings.append(f"拉取的 sign wasm 不合适或未通过校验: {err[:800]}")
        elif err:
            warnings.append(f"BB 资源更新失败（可能含 sign）: {err[:800]}")
        return warnings

    report = result.get("report")
    if not isinstance(report, dict):
        return warnings

    validation = report.get("validation")
    if not isinstance(validation, dict):
        validation = {}

    net = validation.get("signNetworkFetch")
    if isinstance(net, dict) and net:
        reason = net.get("reason") or net.get("strategyError") or "unknown"
        warnings.append(
            "sign wasm 未从 Bangcle 线上拉取成功，已使用本地回退包；"
            f"原因: {reason}（抢购签名可能与真机不一致）"
        )

    next_meta = report.get("next")
    if isinstance(next_meta, dict):
        ver = str(next_meta.get("signWasmVersion") or "")
        sign_mod = next_meta.get("signModule")
        size = 0
        if isinstance(sign_mod, dict):
            size = int(sign_mod.get("size") or 0)
        if expect and ver and ver != expect:
            warnings.append(f"sign wasm 版本 {ver} 与期望 {expect} 不一致")
        if ver:
            try:
                from settings.versions import LEGACY_BB_SIGN_WASM_VERSIONS

                if ver in LEGACY_BB_SIGN_WASM_VERSIONS:
                    warnings.append(
                        f"sign wasm 版本 {ver} 在 legacy 列表中，可能无法匹配当前 App/H5"
                    )
            except Exception:
                pass
        if size and size < 2048:
            warnings.append(f"sign wasm 体积异常偏小 ({size} B)，请确认下载是否完整")

    pair = validation.get("pair")
    if isinstance(pair, dict):
        body_len = int(pair.get("bodyLength") or 0)
        if body_len <= 0:
            warnings.append("run_wac 探针返回 body 为空，当前 sign wasm 可能不可用")
    elif report.get("applied") and not net:
        # update.cjs 成功 apply 时应有 pair；缺失则可疑
        if validation and "pair" not in validation:
            warnings.append("更新报告缺少 run_wac 探针结果，无法确认 sign wasm 是否可用")

    if report.get("applied") and isinstance(net, dict) and net:
        warnings.append("已写盘 sign_wasm.bin，但来源为回退而非线上包，建议修复网络后重新 --sync")

    if report.get("applied"):
        ch = report.get("changes")
        if isinstance(ch, dict) and any(ch.values()) and not report.get("archivedTo"):
            warnings.append(
                "版本已更新但未生成归档目录（nodejs/bb/reverse/），请检查 update.cjs 日志"
            )

    return warnings


def emit_sign_warnings(warnings: list[str]) -> None:
    for line in warnings:
        print(f"[bb-sign-warn] {line}", file=sys.stderr, flush=True)


def run_bb_runtime_update(
    mode: BbUpdateMode = "sync",
    *,
    timeout_sec: float = 600.0,
    env: dict[str, str] | None = None,
    refresh_version_constants: bool = True,
    expect_sign_version: str | None = None,
    notify_console: bool = True,
) -> dict[str, Any]:
    """
    Run ``node nodejs/bb/update.cjs --<mode>`` and write ``runtime/sign_wasm.bin`` (+ version.json).

    Returns ``{ok, report, error, signWarnings}``. Emits ``[bb-sign-warn]`` on stderr when sign 不合适.
    """
    script = PATHS.node.bb_update
    if not script.is_file():
        out = {"ok": False, "error": f"missing update script: {script}"}
        if notify_console:
            emit_sign_warnings(collect_sign_warnings(out, expect_sign_version=expect_sign_version))
        return out

    cli_mode = mode if mode in ("check", "apply", "auto", "sync") else "sync"
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)
    if cli_mode == "sync":
        proc_env.setdefault("MT_BB_UPDATE_SIGN_FALLBACK", "0")

    try:
        proc = subprocess.run(
            [nodejs_executable(), str(script), f"--{cli_mode}"],
            cwd=str(PATHS.repo_root),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=proc_env,
        )
    except subprocess.TimeoutExpired:
        out = {"ok": False, "error": f"update.cjs timeout ({timeout_sec}s)", "mode": cli_mode}
        if notify_console:
            emit_sign_warnings(collect_sign_warnings(out, expect_sign_version=expect_sign_version))
        return out
    except OSError as ex:
        out = {"ok": False, "error": str(ex), "mode": cli_mode}
        if notify_console:
            emit_sign_warnings(collect_sign_warnings(out, expect_sign_version=expect_sign_version))
        return out

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    report: dict[str, Any] | None = None
    if stdout:
        try:
            parsed = json.loads(stdout)
            report = parsed if isinstance(parsed, dict) else {"rawStdout": stdout[-4000:]}
        except json.JSONDecodeError:
            report = {"rawStdout": stdout[-4000:]}

    ok = proc.returncode == 0 and isinstance(report, dict)
    err = ""
    if not ok:
        err = stderr or stdout or f"exit {proc.returncode}"

    result: dict[str, Any] = {
        "ok": ok,
        "report": report,
        "error": err,
        "mode": cli_mode,
        "stderr": stderr[:2000] if stderr else "",
    }

    if ok and refresh_version_constants and cli_mode != "check":
        try:
            from settings.versions import reload_from_disk

            meta = reload_from_disk()
            result["signWasmVersion"] = meta.get("signWasmVersion")
            result["signWasmBytes"] = (
                PATHS.sign_wasm.stat().st_size if PATHS.sign_wasm.is_file() else 0
            )
        except Exception as ex:
            result["versionReloadWarning"] = str(ex)

    warnings = collect_sign_warnings(result, expect_sign_version=expect_sign_version)
    result["signWarnings"] = warnings
    if notify_console and warnings:
        emit_sign_warnings(warnings)

    if notify_console and ok and isinstance(report, dict):
        archived = report.get("archivedTo")
        if archived:
            print(f"[bb-update] 已归档旧版本 → {archived}", flush=True)

    return result


def fetch_latest_runtime_artifacts(**kwargs: Any) -> dict[str, Any]:
    """Force ``--sync`` (same as ``scripts/bb_fetch_latest.sh``)."""
    return run_bb_runtime_update("sync", **kwargs)
