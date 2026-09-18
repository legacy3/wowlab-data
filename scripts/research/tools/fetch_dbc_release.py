#!/usr/bin/env python3
"""Fetch a dbc-resolver table release into the workspace scratch dir.

The aura-lifecycle pass uses the latest retail Dump release as a build-drift
snapshot next to the committed ``data/tables`` snapshot.  The archive is
verified against ``aura_lifecycle.PINS['drift_archive_sha256']`` and unpacked
to ``aura_lifecycle.DRIFT_TABLES`` (``<workspace>/bag/dbc-releases/<tag>``).
Nothing is written inside the repository.

Usage::

    python3 tools/fetch_dbc_release.py            # fetch + verify + unpack (idempotent)
    python3 tools/fetch_dbc_release.py --check    # exit 1 unless the snapshot is present and verified
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura_lifecycle import DRIFT_TABLES, PINS  # noqa: E402

REPO = "wowlabdev/dbc-resolver"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    tag = PINS["drift_release"].split()[-1]
    dest = Path(DRIFT_TABLES)
    archive = dest.parent / f"{tag}.tar.gz"
    if (dest / "SpellEffect.csv").exists() and archive.exists() and _sha256(archive) == PINS["drift_archive_sha256"]:
        print(f"ok {dest}")
        return 0
    if args.check:
        print(f"missing or unverified drift snapshot at {dest}", file=sys.stderr)
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        subprocess.run(["gh", "release", "download", tag, "-R", REPO, "-p", f"{tag}.tar.gz",
                        "-D", str(dest.parent)], check=True)
    digest = _sha256(archive)
    if digest != PINS["drift_archive_sha256"]:
        print(f"sha256 mismatch for {archive}: {digest}", file=sys.stderr)
        return 1
    dest.mkdir(exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="data")
    print(f"ok {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
