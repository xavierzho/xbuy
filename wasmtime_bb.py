"""Wasmtime 配置：茅台 H5 runtime.wasm（shield / bangcle.sdk）。

CDN 上的 stub*.wasm 在 Node/V8 下可运行，但部分版本含 legacy `try` 指令；
wasmtime-py 需开启 exceptions，且目前无法开启 legacy_exceptions（见 bytecodealliance/wasmtime#11861 仅暴露 wasm_exceptions）。
"""

from __future__ import annotations

import os

_LEGACY_TRY_MARKERS = (
    "legacy_exceptions feature required",
    "legacy_exceptions feature required for try instruction",
)

_LEGACY_TRY_HINT = (
    "runtime.wasm 含 Wasm legacy exception（try）编码，当前 Python wasmtime 无法加载；"
    "Node 校验可通过但 shield 预热会失败。"
    "可从 nodejs/bb/reverse/*/runtime.wasm 恢复到 runtime/，或暂不执行 --sync 直至上游 wasm 与 wasmtime 对齐。"
)


def make_bb_wasm_engine():
    """Engine for BB runtime.wasm: exceptions proposal on."""
    import wasmtime as wt

    cfg = wt.Config()
    cfg.wasm_exceptions = True
    return wt.Engine(cfg)


def compile_bb_wasm_module(engine, wasm_path: str):
    """Load runtime.wasm; raise RuntimeError with remediation hint on legacy try."""
    import wasmtime as wt

    try:
        return wt.Module.from_file(engine, wasm_path)
    except Exception as ex:
        msg = str(ex)
        if any(m in msg for m in _LEGACY_TRY_MARKERS):
            raise RuntimeError(f"{_LEGACY_TRY_HINT}\n原始错误: {ex}") from ex
        raise


def validate_runtime_wasm_file(path: str) -> None:
    """Raise if path is missing or wasmtime cannot compile the module."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    engine = make_bb_wasm_engine()
    compile_bb_wasm_module(engine, path)
