#!/usr/bin/env python3
"""Spell IDs that exist in the checked-in snapshot but not in the last snapshot
of the client build family the pinned TrinityCore revision supports.

TrinityCore ``7f3d43b`` supports client builds <= 12.0.7.68453 (``revision``
metadata in the proc archaeology, §25).  The wowlab-data history has a
``12.0.7.68367`` snapshot of ``SpellName.csv`` (commit ``63326dd``).  Any SpellID
present now but absent there is *newer than anything Trinity can know about*,
which is the only evidence-based build-skew marker available offline.

Output: ``docs/research/dummy-corpora/build-skew.json``.
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
OLD_COMMIT = "63326dd"
OLD_LABEL = "12.0.7.68367"
OUT = ROOT / "docs/research/dummy-corpora/build-skew.json"


def ids_from(text: str) -> set[int]:
    reader = csv.DictReader(io.StringIO(text))
    return {int(r["ID"]) for r in reader if r["ID"]}


def main() -> int:
    old_text = subprocess.run(["git", "-C", str(ROOT), "show", f"{OLD_COMMIT}:data/tables/SpellName.csv"],
                              capture_output=True, text=True, check=True).stdout
    old_ids = ids_from(old_text)
    new_ids = ids_from((ROOT / "data/tables/SpellName.csv").read_text(encoding="utf-8"))
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    payload = {
        "provenance": {
            "old_snapshot": OLD_LABEL, "old_commit": subprocess.run(
                ["git", "-C", str(ROOT), "rev-parse", OLD_COMMIT], capture_output=True, text=True, check=True).stdout.strip(),
            "new_snapshot": json.loads((ROOT / "changes/metadata/12.1.0.69497.json").read_text())["version"],
            "wowlab_data_head": head,
            "trinity_supported_build_max": "12.0.7.68453",
            "note": "IDs added after the last supported build family; absence from Trinity is expected, not a finding",
        },
        "spell_count_old": len(old_ids), "spell_count_new": len(new_ids),
        "added": added, "removed": removed,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(added)} added, {len(removed)} removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
