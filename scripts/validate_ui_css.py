#!/usr/bin/env python3
"""Light checks for main WebUI stylesheets (no external CSS parser)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
SHELL = WEB / "ui-shell.css"
THEME = WEB / "ui-theme.css"
UI_HTML = WEB / "ui.html"

ORPHAN_PROP = re.compile(r"^\s*[a-zA-Z-]+\s*:\s*$")


def brace_balance(css: str) -> int:
    depth = 0
    i = 0
    n = len(css)
    while i < n:
        if css[i : i + 2] == "/*":
            j = css.find("*/", i)
            if j < 0:
                break
            i = j + 2
            continue
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth < 0:
                return depth
        i += 1
    return depth


def format_css(css: str) -> str:
    """Normalize rule blocks (one declaration per line, consistent braces)."""
    css = re.sub(r"(\*/)\s*(?=[#.@\w])", r"\1\n\n", css)
    out: list[str] = []
    i, n = 0, len(css)
    while i < n:
        if css[i : i + 2] == "/*":
            j = css.find("*/", i)
            if j < 0:
                out.append(css[i:])
                break
            block = css[i : j + 2]
            if not block.endswith("\n"):
                block += "\n"
            out.append(block)
            i = j + 2
            continue
        while i < n and css[i].isspace():
            if css[i] == "\n":
                out.append("\n")
            i += 1
        if i >= n:
            break
        if css[i] == "@":
            brace = css.find("{", i)
            j = brace + 1
            depth = 1
            while j < n and depth:
                if css[j] == "{":
                    depth += 1
                elif css[j] == "}":
                    depth -= 1
                j += 1
            out.append(css[i:j].strip() + "\n\n")
            i = j
            continue
        brace = css.find("{", i)
        if brace < 0:
            out.append(css[i:])
            break
        selector = " ".join(css[i:brace].split())
        j = brace + 1
        depth = 1
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        body = css[brace + 1 : j - 1]
        decls: list[str] = []
        for part in body.split(";"):
            part = part.strip()
            if not part:
                continue
            if "\n" in part:
                for line in part.splitlines():
                    line = line.strip()
                    if line:
                        decls.append(line if line.endswith(";") else line + ";")
            else:
                decls.append(part if part.endswith(";") else part + ";")
        out.append(selector + " {\n")
        for d in decls:
            out.append("  " + d + "\n")
        out.append("}\n\n")
        i = j
    text = "".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(\*/)\s*(?=[#.@\w])", r"\1\n\n", text)
    return text.strip() + "\n"


LEGACY_GOLD_IN_SHELL = re.compile(r"rgba\(\s*184\s*,\s*148\s*,\s*78", re.I)


def check_file(path: Path, *, allow_root: bool, strict_shell: bool = False) -> list[str]:
    errs: list[str] = []
    text = path.read_text(encoding="utf-8")
    if brace_balance(text) != 0:
        errs.append(f"{path.name}: unbalanced {{ }}")
    for lineno, line in enumerate(text.splitlines(), 1):
        if ORPHAN_PROP.match(line):
            errs.append(f"{path.name}:{lineno}: incomplete declaration: {line.strip()}")
        if re.search(r"/\*[^*]*\*/\s*[#.]", line):
            errs.append(
                f"{path.name}:{lineno}: comment glued to selector (add newline before rule)",
            )
    if not allow_root and re.search(r":root\s*\{", text):
        errs.append(f"{path.name}: :root block should live only in ui-theme.css")
    if allow_root and not re.search(r":root\s*\{", text):
        errs.append("ui-theme.css: missing :root token block")
    if strict_shell and path.name == "ui-shell.css" and LEGACY_GOLD_IN_SHELL.search(text):
        errs.append(
            f"{path.name}: legacy gold rgba(184,148,78,…) belongs in ui-theme.css (--strict-shell)",
        )
    return errs


def check_html_links() -> list[str]:
    errs: list[str] = []
    html = UI_HTML.read_text(encoding="utf-8")
    if "/ui-shell.css" not in html:
        errs.append("ui.html: missing link to /ui-shell.css")
    if "/ui-theme.css" not in html:
        errs.append("ui.html: missing link to /ui-theme.css")
    if "<style>" in html.lower():
        errs.append("ui.html: inline <style> reintroduced; use ui-shell.css + ui-theme.css")
    return errs


def format_file(path: Path) -> bool:
    raw = path.read_text(encoding="utf-8")
    formatted = format_css(raw)
    if formatted != raw:
        path.write_text(formatted, encoding="utf-8")
        print(f"formatted {path.name}")
        return True
    print(f"{path.name}: already formatted")
    return False


def run_format(paths: list[Path]) -> int:
    missing = [p for p in paths if not p.is_file()]
    if missing:
        for p in missing:
            print(f"missing {p}", file=sys.stderr)
        return 1
    for p in paths:
        format_file(p)
    return 0


def run_validate(strict_shell: bool = False) -> int:
    errs: list[str] = []
    for p in (SHELL, THEME, UI_HTML):
        if not p.is_file():
            errs.append(f"missing file: {p}")
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return 1
    errs.extend(check_file(SHELL, allow_root=False, strict_shell=strict_shell))
    errs.extend(check_file(THEME, allow_root=True))
    errs.extend(check_html_links())
    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return 1
    print("validate_ui_css: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate (and optionally format) WebUI CSS.")
    parser.add_argument(
        "--format",
        action="store_true",
        help="Reformat web/ui-shell.css in place, then validate.",
    )
    parser.add_argument(
        "--format-all",
        action="store_true",
        help="Reformat ui-shell.css and ui-theme.css, then validate.",
    )
    parser.add_argument(
        "--strict-shell",
        action="store_true",
        help="Fail if ui-shell.css still contains legacy gold rgba(184,148,78,…).",
    )
    args = parser.parse_args()
    if args.format_all:
        code = run_format([SHELL, THEME])
        if code != 0:
            return code
    elif args.format:
        code = run_format([SHELL])
        if code != 0:
            return code
    return run_validate(strict_shell=args.strict_shell)


if __name__ == "__main__":
    raise SystemExit(main())
