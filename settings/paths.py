"""仓库内所有稳定路径（数据、子进程、制品）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── 统一 BB / 易盾 runtime 制品（与 nodejs/shared/paths.cjs 一致）──
RUNTIME_DIR = REPO_ROOT / "runtime"
RUNTIME_WASM = RUNTIME_DIR / "runtime.wasm"
SIGN_WASM = RUNTIME_DIR / "sign_wasm.bin"
VERSION_JSON = RUNTIME_DIR / "version.json"


@dataclass(frozen=True)
class NodePaths:
    """按领域划分的 Node 入口（workspaces 在 nodejs/ 下）。"""

    bb_update: Path
    gdx_cookie: Path
    bangcle_warmup: Path
    http_bridge_server: Path
    bs_dvid_cli: Path
    yidun_local_api: Path
    http_bridge_curl_worker: Path


@dataclass(frozen=True)
class RepoPaths:
    repo_root: Path
    runtime_dir: Path
    runtime_wasm: Path
    sign_wasm: Path
    version_json: Path
    accounts_json: Path
    ui_state_json: Path
    mt_item_list_json: Path
    product_list_json: Path
    proxy_pool_txt: Path
    go_bridge_dir: Path
    go_bridge_exe: Path
    web_dir: Path
    logs_dir: Path
    ddddocr_service_dir: Path
    ddddocr_fastapi_dir: Path  # 兼容旧名，与 ddddocr_service_dir 相同
    account_addresses_sqlite: Path
    go_bridge_log: Path
    go_bridge_tls_mode_json: Path
    mt_global_force_local_json: Path
    dist_node_config_json: Path
    debug_last_api_json: Path
    pcas_code_json: Path
    remote_tunnel_url_txt: Path
    public_gate_py: Path
    pay_record_export_dir: Path
    account_record_export_dir: Path
    proxy_pool_import_log: Path
    node: NodePaths


NODE = NodePaths(
    bb_update=REPO_ROOT / "nodejs" / "bb" / "update.cjs",
    gdx_cookie=REPO_ROOT / "nodejs" / "gdx" / "gdx_cookie.cjs",
    bangcle_warmup=REPO_ROOT / "nodejs" / "bangcle" / "bangcle_warmup.cjs",
    http_bridge_server=REPO_ROOT / "nodejs" / "http-bridge" / "server.cjs",
    bs_dvid_cli=REPO_ROOT / "nodejs" / "bs-dvid" / "cli.js",
    yidun_local_api=REPO_ROOT / "nodejs" / "yidun-local" / "app_api.js",
    http_bridge_curl_worker=REPO_ROOT / "nodejs" / "http-bridge" / "curl_cffi_worker.py",
)

PATHS = RepoPaths(
    repo_root=REPO_ROOT,
    runtime_dir=RUNTIME_DIR,
    runtime_wasm=RUNTIME_WASM,
    sign_wasm=SIGN_WASM,
    version_json=VERSION_JSON,
    accounts_json=REPO_ROOT / "accounts.json",
    ui_state_json=REPO_ROOT / "ui_state.json",
    mt_item_list_json=REPO_ROOT / "mt_item_list.json",
    product_list_json=REPO_ROOT / "product_list.json",
    proxy_pool_txt=REPO_ROOT / "local_proxy_pool.txt",
    go_bridge_dir=REPO_ROOT / "go_bridge",
    go_bridge_exe=REPO_ROOT / "go_bridge" / (
        "rush_bridge.exe" if __import__("os").name == "nt" else "rush_bridge"
    ),
    web_dir=REPO_ROOT / "web",
    logs_dir=REPO_ROOT / "logs",
    ddddocr_service_dir=REPO_ROOT / "services" / "ddddocr",
    ddddocr_fastapi_dir=REPO_ROOT / "services" / "ddddocr",
    account_addresses_sqlite=REPO_ROOT / "account_addresses.sqlite3",
    go_bridge_log=REPO_ROOT / "go_bridge" / "bridge.log",
    go_bridge_tls_mode_json=REPO_ROOT / "go_bridge_tls_mode.json",
    mt_global_force_local_json=REPO_ROOT / "mt_global_force_local.json",
    dist_node_config_json=REPO_ROOT / "dist_node_config.json",
    debug_last_api_json=REPO_ROOT / "debug_last_api.json",
    pcas_code_json=REPO_ROOT / "pcas-code.json",
    remote_tunnel_url_txt=REPO_ROOT / "remote_tunnel_url.txt",
    public_gate_py=REPO_ROOT / "public_gate.py",
    pay_record_export_dir=REPO_ROOT / "抢购记录",
    account_record_export_dir=REPO_ROOT / "账号记录",
    proxy_pool_import_log=REPO_ROOT / "logs" / "proxy_api_import.jsonl",
    node=NODE,
)
