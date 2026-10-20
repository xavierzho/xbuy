"""Gzip helpers aligned with nodejs/bs-dvid/bs-dvid.js gzipDeterministic."""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def gzip_deterministic(data: bytes) -> bytes:
    """Byte-identical to Node gzipDeterministic (zlib.gzipSync level 9 + header patch)."""
    js = r"""
const zlib = require('zlib');
const chunks = [];
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('end', () => {
  const data = Buffer.concat(chunks);
  const gz = zlib.gzipSync(data, { level: 9 });
  if (gz.length >= 10) {
    gz[4] = 0;
    gz[5] = 0;
    gz[6] = 0;
    gz[7] = 0;
    gz[9] = 0;
  }
  process.stdout.write(gz);
});
"""
    proc = subprocess.run(
        ["node", "-e", js],
        input=data,
        capture_output=True,
        cwd=str(_REPO_ROOT),
        check=True,
    )
    return proc.stdout
