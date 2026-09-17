#!/usr/bin/env python3
"""Merge the per-track unknown / reopen-condition corpora into one artifact.

Inputs (all committed, one per research track of the combat-preparation pass):

  docs/research/controlled-unit-corpora/unknowns.json   (Track A)
  docs/research/weapon-combat-corpora/unknowns.json     (Track B)
  docs/research/character-prep-corpora/unknowns.json    (Track C)

Output:

  docs/research/combat-prep-corpora/cross-track-unknowns.json

Every entry is validated against the shared shape (see ``REQUIRED``) and the
shared evidence-class taxonomy; a malformed entry fails the merge instead of
being dropped, so the artifact can never silently lose a reopen condition.
Entries are grouped by ``topic`` and by ``evidence_class`` and cross-referenced
by ``related`` ids across tracks (declared by the authors, never inferred).

Usage::

    python3 tools/merge_unknowns.py            # writes the artifact
    python3 tools/merge_unknowns.py --check    # validates only, exit 1 on defects
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve()
WOWLAB_DATA = HERE.parents[3]
RESEARCH = WOWLAB_DATA / "docs" / "research"

INPUTS = {
    "A": RESEARCH / "controlled-unit-corpora" / "unknowns.json",
    "B": RESEARCH / "weapon-combat-corpora" / "unknowns.json",
    "C": RESEARCH / "character-prep-corpora" / "unknowns.json",
}
OUT = RESEARCH / "combat-prep-corpora" / "cross-track-unknowns.json"

EVIDENCE_CLASSES = (
    "db2-fact", "world-db-fact", "trinity-consumer", "trinity-probe", "differential",
    "structural-inference", "legacy-only", "build-skew", "unresolved",
)
REQUIRED = ("id", "track", "topic", "claim_or_gap", "evidence_class", "reopen_condition")
OPTIONAL = ("witnesses", "coordinates", "related", "owner", "severity", "notes", "section")
TRACKS = {"A", "B", "C"}


def load(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"missing input corpus: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "entries" in raw:
        raw = raw["entries"]
    if not isinstance(raw, list):
        raise SystemExit(f"{path}: expected a list (or an object with 'entries')")
    return raw


def validate(entries: list[dict], label: str) -> list[str]:
    defects: list[str] = []
    for index, entry in enumerate(entries):
        where = f"{label}[{index}]"
        if not isinstance(entry, dict):
            defects.append(f"{where}: not an object")
            continue
        for key in REQUIRED:
            if key not in entry or entry[key] in ("", None):
                defects.append(f"{where} ({entry.get('id', '?')}): missing {key}")
        unknown = set(entry) - set(REQUIRED) - set(OPTIONAL)
        if unknown:
            defects.append(f"{where} ({entry.get('id', '?')}): unknown keys {sorted(unknown)}")
        if entry.get("track") not in TRACKS:
            defects.append(f"{where}: track must be one of {sorted(TRACKS)}")
        if entry.get("evidence_class") not in EVIDENCE_CLASSES:
            defects.append(f"{where} ({entry.get('id', '?')}): evidence_class "
                           f"{entry.get('evidence_class')!r} not in taxonomy")
        for key in ("witnesses", "coordinates", "related"):
            if key in entry and not isinstance(entry[key], list):
                defects.append(f"{where} ({entry.get('id', '?')}): {key} must be a list")
    return defects


def git_head() -> str:
    try:
        return subprocess.run(["git", "-C", str(WOWLAB_DATA), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def merge(check_only: bool = False) -> int:
    all_entries: list[dict] = []
    defects: list[str] = []
    per_track: dict[str, int] = {}
    input_hashes: dict[str, str] = {}
    for track, path in INPUTS.items():
        entries = load(path)
        input_hashes[str(path.relative_to(WOWLAB_DATA))] = hashlib.sha256(
            path.read_bytes()).hexdigest()
        defects.extend(validate(entries, path.name))
        for entry in entries:
            if entry.get("track") != track:
                defects.append(f"{path.name}: entry {entry.get('id')} declares track "
                               f"{entry.get('track')!r} but lives in track {track}'s corpus")
        per_track[track] = len(entries)
        all_entries.extend(entries)

    ids = Counter(e.get("id") for e in all_entries)
    for value, count in sorted(ids.items(), key=lambda kv: str(kv[0])):
        if count > 1:
            defects.append(f"duplicate id {value!r} ({count} entries)")
    known = set(ids)
    for entry in all_entries:
        for related in entry.get("related", []):
            if related not in known:
                defects.append(f"{entry.get('id')}: related id {related!r} does not exist")

    if defects:
        for defect in defects:
            print(f"defect: {defect}", file=sys.stderr)
        return 1
    if check_only:
        print(f"ok: {len(all_entries)} entries ({per_track})")
        return 0

    all_entries.sort(key=lambda e: (e["track"], str(e["id"])))
    by_topic: dict[str, list[str]] = {}
    by_class: dict[str, list[str]] = {}
    for entry in all_entries:
        by_topic.setdefault(entry["topic"], []).append(entry["id"])
        by_class.setdefault(entry["evidence_class"], []).append(entry["id"])
    track_of = {e["id"]: e["track"] for e in all_entries}
    cross = [e["id"] for e in all_entries
             if any(track_of[r] != e["track"] for r in e.get("related", []))]

    payload = {
        "provenance": {
            "tool": "scripts/research/tools/merge_unknowns.py",
            "wowlab_data_commit": git_head(),
            "inputs": input_hashes,
            "evidence_classes": list(EVIDENCE_CLASSES),
            "note": "Every entry is an unresolved value, a build-skew population or a conclusion "
                    "whose evidence class is weaker than a direct consumer, together with the "
                    "exact observation that would resolve or falsify it. Entries are authored "
                    "per track; cross-track links are declared, never inferred.",
        },
        "summary": {
            "entries": len(all_entries),
            "per_track": dict(sorted(per_track.items())),
            "per_evidence_class": {k: len(v) for k, v in sorted(by_class.items())},
            "topics": {k: len(v) for k, v in sorted(by_topic.items())},
            "cross_track_linked": cross,
        },
        "by_topic": dict(sorted(by_topic.items())),
        "by_evidence_class": dict(sorted(by_class.items())),
        "entries": all_entries,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(WOWLAB_DATA)}: {len(all_entries)} entries {per_track}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="validate only")
    args = parser.parse_args()
    return merge(check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
