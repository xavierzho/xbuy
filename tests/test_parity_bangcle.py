from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bangcle.parity import build_bangcle_warmup_parity_artifacts

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/parity/bangcle_warmup.json"
REPO_ROOT = Path(__file__).resolve().parents[1]


def test_bangcle_warmup_parity_matches_node_cli() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    py_out = build_bangcle_warmup_parity_artifacts(fixture)
    proc = subprocess.run(
        ["node", "nodejs/bangcle/parity_warmup.cjs"],
        input=json.dumps(fixture).encode("utf-8"),
        capture_output=True,
        cwd=str(REPO_ROOT),
        check=True,
    )
    node_out = json.loads(proc.stdout.decode("utf-8"))
    assert py_out == node_out
