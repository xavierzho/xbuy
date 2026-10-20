"""兼容层：Bangcle BB SDK 已迁入 ``bangcle.sdk``。"""

from __future__ import annotations

import warnings

warnings.warn(
    "py_bb_new 已废弃，请改用 bangcle.sdk",
    DeprecationWarning,
    stacklevel=2,
)

from bangcle.sdk import *  # noqa: F403
