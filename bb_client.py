"""兼容层：Node HTTP BB 客户端已迁入 ``bangcle.bridge_client``。"""

from __future__ import annotations

import warnings

warnings.warn(
    "bb_client 已废弃，请改用 bangcle.bridge_client（抢购 BB 用 shield.py）",
    DeprecationWarning,
    stacklevel=2,
)

from bangcle.bridge_client import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("bangcle.bridge_client", run_name="__main__")
