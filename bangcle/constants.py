from __future__ import annotations

DEFAULT_BB_APP_KEY = "10001"
DEFAULT_BB_HOST = "https://fk1.moutai519.com.cn/bangcle"
DEFAULT_WASM_RUNTIME_FALLBACK_HOST = "http://172.16.202.6:9101"
DEFAULT_UPLOAD_PATH = "/bbprbdata/upload"
DEFAULT_WASM_VERSION_LIST_PATH = "/api/v1/1/2"
DEFAULT_WASM_DOWNLOAD_PATH = "/api/v1/1/1"
H5_ORIGIN = "https://h5.moutai519.com.cn"

LIVE_BB_WASM_VERSION = "v1.1.3.2"
LIVE_BB_STRATEGY_VERSION = 6
WARMUP_ALG_TAG = "py-v1.1.3.2"
WARMUP_MIN_INTERVAL_MS = 60_000
WARMUP_PORTABLE_STATE_KEYS = (
    "bbSignWasmBase64",
    "bbSignWasmVersion",
    "bbSignWasmOk",
    "bbSdkVersion",
    "__bbWasmFetched",
    "__bbWasmFetchedAt",
    "__bbWasmUnavailableReason",
)

CONTENT_INFO_BB_HEADER = "Content-Info-Bb"

DEFAULT_BB_TASK: list = [
    "",
    "",
    0,
    1,
    0,
    0,
    60,
    10,
    [0, 10],
    [
        "mousemove",
        "error",
        "click",
        "touchstart",
        "touchend",
        "pageshow",
        "pagehide",
        "mousedown",
        "mouseup",
        "a1",
        "a2",
        "a3",
        "a4",
        "c1",
        "c2",
        "c3",
        "c4",
        "c5",
        "c6",
        "c7",
        "h",
        "i",
        "j",
        "k",
        "l",
        "m",
        "n",
    ],
    "",
    "",
    1,
    0,
    100,
    10,
    1,
    [],
    [0, 0],
    [0, 10],
    3600,
    0,
    0,
    20,
    3,
    [0, "", "", "", "", "", "", "", ""],
]

LIVE_BB_REMOTE_TASK: list = [
    1,
    1,
    [],
    3600,
    0,
    0,
    10,
    20,
    3,
    [
        0,
        "7a382327999a48569b105dcc7e937e03",
        "300013070715",
        "14C9C8F90EDBA243F0D0CDCB936E1770",
        "https://msg.cmpassport.com/h5/getMobile",
        "https://ua.cmpassport.com/api/h5/uaidGetCUAddress",
        "https://ua.cmpassport.com/api/h5/uaidGetCTAddress",
        "https://ua.cmpassport.com/api/h5/uaidGetCTCUToken",
        "https://cloudcenter.bangcle.com/saas/third/device/uaid",
    ],
]

SIGN_WASM_STORAGE_KEY = "__wasm_sign_wasm__"
WASM_SIGN_VERSION_KEY = "__wasm_sign_version__"
WASM_SIGN_OK_STORAGE_KEY = "__wasm_sign_ok__"
# wrapped_state 与 bb_sdk 命名别名
SIGN_WASM_OK_STORAGE_KEY = WASM_SIGN_OK_STORAGE_KEY
SIGN_WASM_STORAGE_TTL_MS = 2_592_000_000
GEN_F_TOKEN_TTL_MS = 5_316_758_292
TOKEN_STORAGE_TTL_MS = 30_000
DEVICE_INFO_STORAGE_TTL_MS = 433_296_000
REMOTE_CONFIG_STORAGE_TTL_MS = 28_809_082
DEFAULT_STORAGE_TTL_MS = 86_400_000
PERSISTENT_STORAGE_TTL_MS = 259_200_000_000
