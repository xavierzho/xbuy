#!/usr/bin/env python3
"""Audit small client updates that may invalidate runtime parameters."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
LOG_DIR = ROOT / "tools" / "TraceLens" / "logs" / "tracelens"
DUMP_DIR = ROOT / "tools" / "BBWasmLens" / "dumps"


@dataclass
class Hit:
    file: Path
    line: int
    value: str
    kind: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def first(pattern: str, text: str, default: str = "") -> str:
    match = re.search(pattern, text, re.S)
    return match.group(1) if match else default


def add(hits: dict[str, list[Hit]], key: str, file: Path, line: int, value: Any, kind: str) -> None:
    s = str(value or "").strip()
    if s:
        hits[key].append(Hit(file=file, line=line, value=s, kind=kind))


def parse_eval_json(line: str) -> Any:
    marker = "evaluateJavaScript result="
    if marker not in line:
        return None
    tail = line.split(marker, 1)[1]
    if " error=" in tail:
        tail = tail.split(" error=", 1)[0]
    try:
        return json.loads(tail)
    except json.JSONDecodeError:
        return None


def scan_logs(log_dir: Path) -> dict[str, list[Hit]]:
    hits: dict[str, list[Hit]] = defaultdict(list)
    for path in sorted(log_dir.glob("*.log")):
        try:
            lines = read_text(path).splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, start=1):
            for version in re.findall(r'MT-APP-Version"?\s*[=:]\s*"?([0-9]+\.[0-9]+\.[0-9]+)', line):
                add(hits, "app_versions", path, n, version, "MT-APP-Version")
            for version in re.findall(r'"app_version"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"', line):
                add(hits, "app_versions", path, n, version, "app_version")
            for version in re.findall(r'"ver_name"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"', line):
                add(hits, "app_versions", path, n, version, "ver_name")
            for version in re.findall(r'"version"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"', line):
                add(hits, "h5_app_versions", path, n, version, "H5 localStorage version")
            for code in re.findall(r'"ver_code"\s*:\s*"?([0-9]+)"?', line):
                add(hits, "app_ver_codes", path, n, code, "ver_code")
            for version in re.findall(r'moutaiapp/([0-9]+\.[0-9]+\.[0-9]+)', line):
                add(hits, "ua_moutai_versions", path, n, version, "UA moutaiapp")
            for version in re.findall(r"V\d+\.\d+\.\d+_\d{8}\.\d+_imaotai", line):
                add(hits, "bb_sdk_versions", path, n, version, "BB SDK")
            for version in re.findall(r"\bv\d+\.\d+(?:\.\d+){0,3}\b", line):
                if "/bangcle/" in line or "SIGN_WASM" in line or "wasm" in line.lower():
                    add(hits, "bb_wasm_versions", path, n, version, "BB wasm")
            for captcha_id in re.findall(r'"captchaId"\s*:\s*"([0-9a-fA-F]{16,64})"', line):
                add(hits, "captcha_ids", path, n, captcha_id, "captchaId")
            for app_config in re.findall(r"appConfig=([0-9]_[0-9]_[0-9])", line):
                add(hits, "app_configs", path, n, app_config, "H5 appConfig")
            for chrome in re.findall(r"Chrome/([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", line):
                add(hits, "chrome_versions", path, n, chrome, "UA Chrome")

            payload = parse_eval_json(line)
            if isinstance(payload, dict):
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                body = str(data.get("body") or "")
                if body:
                    try:
                        parsed_body = json.loads(body)
                    except json.JSONDecodeError:
                        parsed_body = None
                    if isinstance(parsed_body, dict):
                        parsed_data = parsed_body.get("data")
                        if isinstance(parsed_data, dict):
                            if parsed_data.get("captchaId"):
                                add(hits, "captcha_ids", path, n, parsed_data.get("captchaId"), "response captchaId")
                        if data.get("url") and "/bangcle/api/v1/1/2" in str(data.get("url")):
                            for item in parsed_body.get("v") or []:
                                if isinstance(item, dict):
                                    add(hits, "bb_wasm_versions", path, n, item.get("v"), "bangcle version list")
    return hits


def scan_bbwasmlens_dumps(dump_dir: Path) -> dict[str, list[Hit]]:
    hits: dict[str, list[Hit]] = defaultdict(list)
    for path in sorted(dump_dir.glob("**/wk_storage_*.json")):
        try:
            payload = json.loads(read_text(path))
        except (OSError, json.JSONDecodeError):
            continue
        cookie = str(payload.get("cookie") or "")
        for version in re.findall(r"(?:^|;\s*)_sdk_v_=([^;]+)", cookie):
            add(hits, "bb_sdk_versions", path, 1, version, "BBWasmLens cookie")
        href = str(payload.get("href") or "")
        for app_config in re.findall(r"appConfig=([0-9]_[0-9]_[0-9])", href):
            add(hits, "app_configs", path, 1, app_config, "BBWasmLens href")
        globals_obj = payload.get("globals") if isinstance(payload.get("globals"), dict) else {}
        add(hits, "bb_wasm_versions", path, 1, globals_obj.get("__SIGN_WASM_VERSION__"), "BBWasmLens globals")
    for path in sorted(dump_dir.glob("**/runtime_events_*.json")):
        try:
            payload = json.loads(read_text(path))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            text = json.dumps(item, ensure_ascii=False)
            for version in re.findall(r"V\d+\.\d+\.\d+_\d{8}\.\d+_imaotai", text):
                add(hits, "bb_sdk_versions", path, 1, version, "BBWasmLens runtime")
            href = str(item.get("href") or "")
            for app_config in re.findall(r"appConfig=([0-9]_[0-9]_[0-9])", href):
                add(hits, "app_configs", path, 1, app_config, "BBWasmLens runtime")
    return hits


def merge_hits(*groups: dict[str, list[Hit]]) -> dict[str, list[Hit]]:
    merged: dict[str, list[Hit]] = defaultdict(list)
    for group in groups:
        for key, values in group.items():
            merged[key].extend(values)
    return merged


def unique_latest(hits: list[Hit]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for hit in sorted(hits, key=hit_sort_key, reverse=True):
        if hit.value not in seen:
            seen.add(hit.value)
            out.append(hit.value)
    return out


def hit_sort_key(hit: Hit) -> tuple[float, int]:
    match = re.search(r"_(\d{13})_", hit.file.name)
    if match:
        return (float(match.group(1)), hit.line)
    return (hit.file.stat().st_mtime, hit.line)


def latest_hit(hits: list[Hit], value: str) -> Hit | None:
    selected = [h for h in hits if h.value == value]
    if not selected:
        return None
    return max(selected, key=hit_sort_key)


def local_baseline() -> dict[str, str]:
    shield = read_text(ROOT / "shield.py")
    app = read_text(ROOT / "app.py")
    node_bb = read_text(ROOT / "node_bridge" / "bb" / "bb_module.cjs")
    local_yidun = read_text(ROOT / "local_yidun" / "service" / "app_api.js")
    return {
        "release_ver": first(r'RELEASE_VER\s*=\s*"([^"]+)"', shield),
        "shield_ver": first(r'SHIELD_VER\s*=\s*"([^"]+)"', shield),
        "wasm_tag": first(r'WASM_TAG\s*=\s*"([^"]+)"', shield),
        "bb_module_sdk": first(r"const SDK_VERSION = '([^']+)'", node_bb),
        "bb_module_wasm": first(r"const DEFAULT_SIGN_WASM_VERSION = '([^']+)'", node_bb),
        "fixed_chrome": first(r'H5_FIXED_CHROME_VERSION\s*=\s*"([^"]+)"', app),
        "captcha_ids": ", ".join(sorted(set(re.findall(r'"([0-9a-fA-F]{32})"', app + "\n" + local_yidun)))),
    }


def format_evidence(hits: dict[str, list[Hit]], key: str, limit: int = 8) -> list[str]:
    lines: list[str] = []
    values = unique_latest(hits.get(key, []))
    if not values:
        return ["- No hits."]
    for value in values[:limit]:
        hit = latest_hit(hits[key], value)
        if hit:
            lines.append(f"- `{value}` from `{rel(hit.file)}:{hit.line}` ({hit.kind})")
    return lines


def build_report() -> str:
    base = local_baseline()
    hits = merge_hits(scan_logs(LOG_DIR), scan_bbwasmlens_dumps(DUMP_DIR))
    observed_app = unique_latest(hits.get("app_versions", []))
    observed_wasm = unique_latest(hits.get("bb_wasm_versions", []))
    observed_sdk = unique_latest(hits.get("bb_sdk_versions", []))
    observed_chrome = unique_latest(hits.get("chrome_versions", []))
    observed_captcha = unique_latest(hits.get("captcha_ids", []))

    risks: list[str] = []
    if observed_app and base["release_ver"] not in observed_app:
        risks.append("App version mismatch")
    if observed_wasm and base["wasm_tag"] not in observed_wasm and base["bb_module_wasm"] not in observed_wasm:
        risks.append("BB sign wasm mismatch")
    if observed_sdk and base["shield_ver"] not in observed_sdk and base["bb_module_sdk"] not in observed_sdk:
        risks.append("BB SDK version mismatch")
    if observed_chrome and base["fixed_chrome"] not in observed_chrome:
        risks.append("Chrome/WebView UA mismatch")
    if observed_captcha:
        known = set(re.findall(r"[0-9a-fA-F]{32}", base["captcha_ids"]))
        unknown = [x for x in observed_captcha if x not in known]
        if unknown:
            risks.append("Unknown captchaId observed")

    lines = [
        "# Small Update Risk Audit",
        "",
        f"- Project root: `{ROOT}`",
        f"- Log directory: `{LOG_DIR}`",
        f"- Result: {'risk: ' + ', '.join(risks) if risks else 'no high-risk mismatch observed'}",
        "",
        "## Baseline vs Observation",
        "",
        "| Item | Local baseline | Latest TraceLens / BBWasmLens observation | Risk |",
        "| --- | --- | --- | --- |",
    ]
    known_captcha = set(re.findall(r"[0-9a-fA-F]{32}", base["captcha_ids"]))
    unknown_captcha = [x for x in observed_captcha if x not in known_captcha]
    rows = [
        ("App version", base["release_ver"], ", ".join(observed_app[:5]), "High" if observed_app and base["release_ver"] not in observed_app else "OK"),
        ("BB SDK", base["shield_ver"], ", ".join(observed_sdk[:5]), "High" if observed_sdk and base["shield_ver"] not in observed_sdk and base["bb_module_sdk"] not in observed_sdk else "OK"),
        ("BB sign wasm", f"{base['wasm_tag']} / {base['bb_module_wasm']}", ", ".join(observed_wasm[:5]), "High" if observed_wasm and base["wasm_tag"] not in observed_wasm and base["bb_module_wasm"] not in observed_wasm else "OK"),
        ("Chrome/WebView", base["fixed_chrome"], ", ".join(observed_chrome[:5]), "Medium" if observed_chrome and base["fixed_chrome"] not in observed_chrome else "OK"),
        (
            "captchaId",
            "known mappings in app/local_yidun",
            ", ".join(observed_captcha[:5]),
            "Unknown" if unknown_captcha else ("OK" if observed_captcha else "No data"),
        ),
    ]
    for item, local, observed, risk in rows:
        lines.append(f"| {item} | `{local}` | `{observed}` | `{risk}` |")

    lines.extend(
        [
            "",
            "## Update-sensitive points",
            "",
            "- `shield.RELEASE_VER` drives `MT-APP-Version`, app UA `moutaiapp/x`, and MT signing inputs.",
            "- `shield.WASM_TAG` and `node_bridge/bb/bb_module.cjs` `DEFAULT_SIGN_WASM_VERSION` drive BB payload field `140` and `Content-Web-Bb` consistency.",
            "- `SHIELD_VER` / `_sdk_v_` / `Sdk-Ver-Bb` must stay aligned with Bangcle SDK.",
            "- `captchaId` mappings in `app.py` and `local_yidun/service/app_api.js` must match the current H5 page and item code.",
            "- H5 `appConfig` and referer paths affect captcha, BB warmup, and priority verify flow.",
            "",
            "## Evidence",
            "",
            "### App versions",
            *format_evidence(hits, "app_versions"),
            "### App ver_code",
            *format_evidence(hits, "app_ver_codes"),
            "### UA moutaiapp versions",
            *format_evidence(hits, "ua_moutai_versions"),
            "### BB SDK versions",
            *format_evidence(hits, "bb_sdk_versions"),
            "### BB wasm versions",
            *format_evidence(hits, "bb_wasm_versions"),
            "### captchaIds",
            *format_evidence(hits, "captcha_ids"),
            "### H5 appConfig",
            *format_evidence(hits, "app_configs"),
            "### Chrome/WebView versions",
            *format_evidence(hits, "chrome_versions"),
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown", type=Path, help="write markdown report")
    args = parser.parse_args()
    report = build_report()
    print(report)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(report + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
