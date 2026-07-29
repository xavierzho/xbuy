#!/usr/bin/env python3
"""Audit node_bridge BB SDK parameters against TraceLens captures."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[3]
BB_DIR = ROOT / "node_bridge" / "bb"
LOG_DIR = ROOT / "tools" / "TraceLens" / "logs" / "tracelens"


def shorten(value: Any, size: int = 18) -> str:
    text = "" if value is None else str(value)
    if len(text) <= size * 2 + 3:
        return text
    return f"{text[:size]}...{text[-size:]}"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def extract_first(pattern: str, text: str, default: str = "") -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else default


def parse_wrapper(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        if isinstance(parsed, dict) and "value" in parsed:
            return parsed.get("value")
        return parsed
    return value


def parse_live_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    state = json.loads(read_text(path))
    decoded = state.get("decoded") or {}
    local_wrappers = decoded.get("local_wrappers") or {}
    bb = state.get("bb") or {}
    cookie = state.get("cookie") or ""
    result = {
        "cookie_sdk_version": extract_first(r"(?:^|;\s*)_sdk_v_=([^;]+)", cookie),
        "bb_host_h": bb.get("_bb_h", ""),
        "bb_host_w": bb.get("_bb_w", ""),
        "bb_sign_wasm_version": bb.get("__SIGN_WASM_VERSION__", ""),
        "bb_sign_wasm_ok": bb.get("__SIGN_WASM_OK__", ""),
        "wrapped_sign_wasm_version": parse_wrapper(local_wrappers.get("__wasm_sign_version__")),
        "wrapped_bb_v": parse_wrapper(local_wrappers.get("_bb_v_")),
    }
    wasm_wrapper = local_wrappers.get("__wasm_sign_wasm__")
    if isinstance(wasm_wrapper, dict):
        result["wrapped_wasm_len"] = wasm_wrapper.get("len") or (decoded.get("sign_wasm") or {}).get("byte_len", "")
    result["decoded_sign_wasm_bytes"] = (decoded.get("sign_wasm") or {}).get("byte_len", "")
    return result


@dataclass
class Hit:
    file: Path
    line: int
    value: str
    context: str


def add_hit(hits: dict[str, list[Hit]], key: str, file: Path, line: int, value: str, context: str) -> None:
    if value:
        hits[key].append(Hit(file=file, line=line, value=value, context=context))


def parse_json_payload(line: str) -> dict[str, Any] | None:
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
        for index, line in enumerate(lines, start=1):
            for version in re.findall(r"V\d+\.\d+\.\d+_\d{8}\.\d+_imaotai", line):
                add_hit(hits, "sdk_versions", path, index, version, "raw")
            for version in re.findall(r"\bv\d+\.\d+(?:\.\d+){0,3}\b", line):
                add_hit(hits, "wasm_versions", path, index, version, "raw")
            for host in re.findall(r"https://[^\"'\s]+?/bangcle", line):
                add_hit(hits, "bangcle_hosts", path, index, host, "raw")
            for header in ("Content-Web-Bb", "Content-Hh-Bb", "Sdk-Ver-Bb"):
                if header in line:
                    add_hit(hits, "headers", path, index, header, "raw")

            payload = parse_json_payload(line)
            if not payload:
                continue
            if not isinstance(payload, dict):
                continue
            data = payload.get("data") or {}
            if not isinstance(data, dict):
                continue
            url = str(data.get("url") or "")
            body = str(data.get("body") or "")
            if "/bangcle/api/v1/1/2" in url and body:
                try:
                    parsed_body = json.loads(body)
                except json.JSONDecodeError:
                    parsed_body = None
                if isinstance(parsed_body, dict):
                    for item in parsed_body.get("v") or []:
                        if isinstance(item, dict):
                            add_hit(
                                hits,
                                "server_wasm_versions",
                                path,
                                index,
                                str(item.get("v") or ""),
                                "bangcle version list",
                            )
                    if "w" in parsed_body:
                        add_hit(hits, "server_wasm_ok", path, index, str(parsed_body["w"]), "bangcle version list")
            if "MTWVDUMP:" in line:
                encoded = line.split("MTWVDUMP:", 1)[1]
                decoded = unquote(encoded)
                for version in re.findall(r"V\d+\.\d+\.\d+_\d{8}\.\d+_imaotai", decoded):
                    add_hit(hits, "sdk_versions", path, index, version, "MTWVDUMP")
                for version in re.findall(r'"__SIGN_WASM_VERSION__"\s*:\s*"([^"]+)"', decoded):
                    add_hit(hits, "wasm_versions", path, index, version, "MTWVDUMP")
    return hits


def unique_values(hits: list[Hit]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for hit in sorted(hits, key=lambda h: (h.file.stat().st_mtime, h.line), reverse=True):
        if hit.value not in seen:
            seen.add(hit.value)
            values.append(hit.value)
    return values


def latest_hit(hits: list[Hit], value: str | None = None) -> Hit | None:
    selected = [hit for hit in hits if value is None or hit.value == value]
    if not selected:
        return None
    return max(selected, key=lambda h: (h.file.stat().st_mtime, h.line))


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build_report() -> str:
    module_text = read_text(BB_DIR / "bb_module.cjs")
    sdk_text = read_text(BB_DIR / "bb_sdk_1_9_6.js")
    live = parse_live_state(BB_DIR / "live_bangcle_state_1_9_6.json")
    hits = scan_logs(LOG_DIR)
    local_sign_wasm = BB_DIR / "sign_wasm.bin"

    current = {
        "module_sdk_version": extract_first(r"const SDK_VERSION = '([^']+)'", module_text),
        "sdk_file_version": extract_first(r"const SDK_VERSION_1_9_6 = '([^']+)'", sdk_text),
        "default_bb_host": extract_first(r"const DEFAULT_BB_HOST_1_9_6 = '([^']+)'", sdk_text),
        "default_sign_wasm_version": extract_first(r"const DEFAULT_SIGN_WASM_VERSION = '([^']+)'", module_text),
        "local_sign_wasm_bytes": str(local_sign_wasm.stat().st_size) if local_sign_wasm.exists() else "",
    }

    observed_sdk_versions = unique_values(hits.get("sdk_versions", []))
    observed_server_wasm_versions = unique_values(hits.get("server_wasm_versions", []))
    observed_wasm_versions = unique_values(hits.get("wasm_versions", []))
    observed_hosts = unique_values(hits.get("bangcle_hosts", []))

    rows = [
        ("SDK version", current["module_sdk_version"], ", ".join(observed_sdk_versions) or live.get("cookie_sdk_version", "")),
        ("BB host", current["default_bb_host"], ", ".join(observed_hosts[:5]) or live.get("bb_host_h", "")),
        ("Runtime sign wasm", current["default_sign_wasm_version"], ", ".join(observed_server_wasm_versions) or live.get("bb_sign_wasm_version", "")),
        ("State __SIGN_WASM_VERSION__", live.get("bb_sign_wasm_version", ""), ", ".join(observed_wasm_versions[:5])),
        ("State __wasm_sign_version__", shorten(live.get("wrapped_sign_wasm_version", "")), ""),
        (
            "Local sign_wasm.bin bytes",
            current["local_sign_wasm_bytes"],
            str(live.get("decoded_sign_wasm_bytes") or live.get("wrapped_wasm_len", "")),
        ),
    ]

    changed = []
    if observed_sdk_versions and current["module_sdk_version"] not in observed_sdk_versions:
        changed.append("SDK version")
    if observed_server_wasm_versions and current["default_sign_wasm_version"] not in observed_server_wasm_versions:
        changed.append("Runtime sign wasm")
    if observed_hosts and not any(host.startswith(current["default_bb_host"]) for host in observed_hosts):
        changed.append("BB host")

    lines = [
        "# BB SDK Parameter Audit",
        "",
        f"- Project root: `{ROOT}`",
        f"- Log directory: `{LOG_DIR}`",
        f"- Result: {'changed: ' + ', '.join(changed) if changed else 'no observed change in SDK/host; check wasm details below'}",
        "",
        "## Comparison",
        "",
        "| Item | node_bridge baseline | latest TraceLens / live state observation |",
        "| --- | --- | --- |",
    ]
    for name, baseline, observed in rows:
        lines.append(f"| {name} | `{baseline}` | `{observed}` |")

    lines.extend(["", "## Latest Evidence", ""])
    for key, title in (
        ("sdk_versions", "SDK versions"),
        ("server_wasm_versions", "Bangcle server wasm versions"),
        ("wasm_versions", "All wasm version markers"),
        ("bangcle_hosts", "Bangcle hosts"),
        ("headers", "BB headers"),
    ):
        values = unique_values(hits.get(key, []))
        lines.append(f"### {title}")
        if not values:
            lines.append("- No hits.")
            continue
        for value in values[:10]:
            hit = latest_hit(hits[key], value)
            if hit:
                lines.append(f"- `{value}` from `{rel(hit.file)}:{hit.line}` ({hit.context})")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown", type=Path, help="write report to a markdown file")
    args = parser.parse_args()

    report = build_report()
    print(report)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(report + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
