"""The cross-track unknowns artifact is well formed and consistent with its inputs.

These tests guard the merge (``tools/merge_unknowns.py``): every unresolved
item carries an evidence class from the shared taxonomy and a reopen
condition, ids are unique across tracks, declared cross-references resolve,
and the committed artifact matches a fresh merge of the committed inputs.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
TOOLS = RESEARCH_ROOT / "tools"
DOCS = RESEARCH_ROOT.parents[1] / "docs" / "research"

spec = importlib.util.spec_from_file_location("merge_unknowns", TOOLS / "merge_unknowns.py")
merge_unknowns = importlib.util.module_from_spec(spec)
sys.modules["merge_unknowns"] = merge_unknowns
spec.loader.exec_module(merge_unknowns)  # type: ignore[union-attr]

ARTIFACT = DOCS / "combat-prep-corpora" / "cross-track-unknowns.json"


@pytest.fixture(scope="module")
def artifact() -> dict:
    if not ARTIFACT.is_file():
        pytest.skip("cross-track-unknowns.json not generated")
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def test_inputs_validate() -> None:
    for path in merge_unknowns.INPUTS.values():
        if not path.is_file():
            pytest.skip(f"{path.name} missing")
    assert merge_unknowns.merge(check_only=True) == 0


def test_every_entry_has_reopen_condition_and_class(artifact: dict) -> None:
    for entry in artifact["entries"]:
        assert entry["reopen_condition"].strip(), entry["id"]
        assert entry["evidence_class"] in merge_unknowns.EVIDENCE_CLASSES, entry["id"]
        assert entry["track"] in merge_unknowns.TRACKS, entry["id"]


def test_ids_unique_and_related_resolve(artifact: dict) -> None:
    ids = [e["id"] for e in artifact["entries"]]
    assert len(ids) == len(set(ids))
    known = set(ids)
    for entry in artifact["entries"]:
        for related in entry.get("related", []):
            assert related in known, (entry["id"], related)


def test_summary_counts_match_entries(artifact: dict) -> None:
    entries = artifact["entries"]
    assert artifact["summary"]["entries"] == len(entries)
    per_track = {}
    for entry in entries:
        per_track[entry["track"]] = per_track.get(entry["track"], 0) + 1
    assert artifact["summary"]["per_track"] == per_track
    for cls, members in artifact["by_evidence_class"].items():
        assert all(next(e for e in entries if e["id"] == m)["evidence_class"] == cls
                   for m in members)


def test_artifact_matches_committed_inputs(artifact: dict) -> None:
    import hashlib

    for rel, digest in artifact["provenance"]["inputs"].items():
        path = RESEARCH_ROOT.parents[1] / rel
        assert path.is_file(), rel
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, (
            f"{rel} changed since the artifact was merged; rerun tools/merge_unknowns.py")


def test_validate_rejects_bad_entries() -> None:
    bad = [{"id": "A-1", "track": "A", "topic": "t", "claim_or_gap": "c",
            "evidence_class": "made-up", "reopen_condition": "r"},
           {"id": "A-2", "track": "Z", "topic": "t", "claim_or_gap": "c",
            "evidence_class": "unresolved"}]
    defects = merge_unknowns.validate(bad, "x")
    assert any("not in taxonomy" in d for d in defects)
    assert any("missing reopen_condition" in d for d in defects)
    assert any("track must be" in d for d in defects)
