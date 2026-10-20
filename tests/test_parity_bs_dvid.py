from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bs_dvid.parity import build_bs_dvid_parity_artifacts

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/parity/bs_dvid_flow.json"
REPO_ROOT = Path(__file__).resolve().parents[1]


def test_bs_dvid_parity_matches_node_cli() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    py_out = build_bs_dvid_parity_artifacts(fixture)
    proc = subprocess.run(
        ["node", "nodejs/bs-dvid/parity_cli.js"],
        input=json.dumps(fixture).encode("utf-8"),
        capture_output=True,
        cwd=str(REPO_ROOT),
        check=True,
    )
    node_out = json.loads(proc.stdout.decode("utf-8"))
    assert py_out == node_out
