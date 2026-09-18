"""Track K: Core / research boundary navigation (no snapshot load).

Covers the curated registry (shape, vocabulary, navigation-only wording, cited
Core-audit ids exist), the anchor verification against the pinned Core and
Trinity trees, the attribute flattening, and the Core arithmetic mirrors used by
the navigation timelines.  Each mirror test names the competing model it rules
out.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from aura_lifecycle import CORE_ROOT, CORPORA, PINS, ROOT, TC_ROOT, FailClosed, records
from aura_lifecycle import coremap as k

AUDIT = ROOT / "docs" / "research" / "core-audit-corpora"
CORPUS = CORPORA / "core-navigation.json"


def _core_at_pin() -> bool:
    if not (CORE_ROOT / "crates").is_dir():
        return False
    head = subprocess.run(["git", "-C", str(CORE_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    return head.stdout.strip() == PINS["core_commit"]


needs_core = pytest.mark.skipif(not _core_at_pin(), reason="Core not checked out at the pin")
needs_tc = pytest.mark.skipif(not (TC_ROOT / "src" / "server" / "game").is_dir(), reason="TrinityCore absent")


# --- registry shape ---------------------------------------------------------------------------------

def test_navigation_records_validate_and_are_contiguous():
    nav = k.navigation_records()
    records.validate("core_navigation", nav)
    assert [r["id"] for r in nav] == [f"AL-K-K-{i:02d}" for i in range(1, len(nav) + 1)]
    for r in nav:
        assert r["status"] in k.STATUSES
        assert r["questions"] and set(r["questions"]) <= set("ABCDEFGHI")
        assert r["core_coords"], r["id"]
        assert r["reopen_condition"].strip(), r["id"]


def test_every_research_question_has_navigation():
    matrix = k.question_matrix(k.navigation_records())
    assert set(matrix) == set("ABCDEFGHI")
    for q, row in matrix.items():
        assert row["records"], f"question {q} has no Core navigation"


def test_wording_is_navigation_not_prescription():
    """Rules out drifting into Core prescriptions (the Core audit owns correctives)."""
    banned = re.compile(r"\b(core should|should be|must be fixed|must change|fix(es|ed)? (it|core)|corrective)\b", re.I)
    for r in k.REGISTRY:
        for field in ("observation", "reopen_condition"):
            assert not banned.search(r[field]), (r["id"], field)


def test_cited_audit_ids_exist_in_core_audit_corpora():
    known: set[str] = set()
    for path in AUDIT.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for rec in payload.get("records", []) if isinstance(payload, dict) else []:
            if isinstance(rec, dict) and "id" in rec:
                known.add(rec["id"])
    if not known:
        pytest.skip("core-audit corpora absent")
    cited = {ref for r in k.REGISTRY for ref in r["audit_refs"]}
    assert cited, "registry cites no audit ids"
    assert sorted(cited - known) == []


def test_unknowns_and_falsification_validate():
    records.validate("unknowns", k.UNKNOWNS)
    records.validate("falsification", k.FALSIFICATION)


def test_malformed_coord_fails_closed(tmp_path):
    (tmp_path / "a.rs").write_text("one\ntwo\n", encoding="utf-8")
    assert k.anchor_problems(tmp_path, {"a.rs:2": "two"}) == []
    assert k.anchor_problems(tmp_path, {"a.rs:1": "two"}) != []          # wrong line
    assert k.anchor_problems(tmp_path, {"a.rs:5": "two"}) != []          # outside file
    assert k.anchor_problems(tmp_path, {"b.rs:1": "x"}) != []            # missing file
    assert k.anchor_problems(tmp_path, {"a.rs": "x"}) != []              # malformed


# --- pinned trees -----------------------------------------------------------------------------------

@needs_core
def test_core_anchors_hold_at_pin():
    assert k.verify_core_coords() == sum(len(r["anchors"]) for r in k.REGISTRY)


@needs_tc
def test_trinity_anchors_hold_at_pin():
    assert k.verify_trinity_coords() == sum(len(r["tc_anchors"]) for r in k.REGISTRY)


@needs_tc
def test_trinity_attribute_flattening():
    """Rules out a 1-based or per-word-unflattened bit model: raw = word*32 + bit (0-based)."""
    names = k.trinity_attribute_names()
    assert names[0] == "SPELL_ATTR0_PROC_FAILURE_BURNS_CHARGE"
    assert names[43] == "SPELL_ATTR1_AURA_UNIQUE"
    assert names[103] == "SPELL_ATTR3_DOT_STACKING_RULE"
    assert names[169] == "SPELL_ATTR5_EXTRA_INITIAL_PERIOD"
    assert names[436] == "SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION"
    assert names[489] == "SPELL_ATTR15_UNK9"


@needs_core
def test_core_catalog_classes_for_lifecycle_attributes():
    core = k.core_attribute_support()
    assert core[436][1] == "implemented"
    assert core[103][1] == "ignored"
    assert core[334][1] == "disabled"
    assert core[116][1] == "unimplemented"
    assert 189 not in core          # ATTR5_AURA_UNIQUE_PER_CASTER is uncatalogued -> owner refused
    assert k.core_admission(None).startswith("refused")
    assert k.core_admission("ignored") == "admitted-ignored"


# --- Core arithmetic mirrors ------------------------------------------------------------------------

def test_capped_carryover_reproduces_core_probe_witness():
    """e_capped_carryover: D=250, refresh at 150 -> expiry 475 (Core probe, 2/2 pass at the pin)."""
    assert k.core_projected_expiry("CappedCarryover", 150, 250, 250) == 475


def test_capped_carryover_separates_core_from_pinned_trinity_late_refresh():
    """Rules out 'Core carryover == pinned Trinity pandemic': at refresh 225 Core carries min(25, 75)=25 -> 500,
    whereas Spell.cpp:3284-3288 (read after RefreshTimers, per track B) yields min(D+D, trunc(1.3D)) -> 225+325=550."""
    core = k.core_projected_expiry("CappedCarryover", 225, 250, 250)
    pinned_trinity = 225 + min(250 + 250, 250 * 130 // 100)
    assert (core, pinned_trinity) == (500, 550)


def test_capped_carryover_floors_the_thirty_percent_limit():
    """Rules out a rounded 30% limit: D=255 gives floor(76.5)=76, not 77."""
    assert k.core_projected_expiry("CappedCarryover", 1000, 255, 1000 + 200) == 1000 + 255 + 76


def test_restart_lifetime_can_shorten_but_capped_cannot():
    """Rules out 'Core never shortens a deadline on reapplication' (AL-F-K-01)."""
    assert k.core_projected_expiry("RestartLifetime", 100, 300, 1000) == 400
    assert k.core_projected_expiry("CappedCarryover", 100, 300, 1000) == 1000
    assert k.core_projected_expiry("IndependentStackDeadlines", 100, 300, 1000) == 1000


def test_preserve_existing_lifetime_keeps_active_deadline_only():
    assert k.core_projected_expiry("PreserveExistingLifetime", 100, 300, 250) == 250
    assert k.core_projected_expiry("PreserveExistingLifetime", 100, 300, None) == 400


def test_mirror_fails_closed_on_unknown_policy_and_zero_duration():
    with pytest.raises(FailClosed):
        k.core_projected_expiry("Pandemic", 0, 100, None)
    with pytest.raises(FailClosed):
        k.core_projected_expiry("RestartLifetime", 0, 0, None)


def test_stack_projection_does_not_start_at_capacity():
    """Rules out 'initial stacks = capacity' (Phalanx trap): a fresh application starts at its own count."""
    assert k.core_projected_stacks(0, 1, 2) == 1
    assert k.core_projected_stacks(1, 1, 2) == 2
    assert k.core_projected_stacks(2, 1, 2) == 2          # saturates, does not reject
    with pytest.raises(FailClosed):
        k.core_projected_stacks(0, 3, 2)                   # StackCountExceedsMaximum


# --- committed corpus -------------------------------------------------------------------------------

@pytest.mark.skipif(not CORPUS.exists(), reason="core-navigation.json not generated")
def test_committed_corpus_matches_registry():
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    records.validate_corpus(payload)
    assert payload["provenance"]["pins"] == PINS
    assert payload["core_navigation"] == json.loads(json.dumps(k.navigation_records()))
    assert payload["counts"]["records"] == len(k.REGISTRY)
    assert payload["counts"]["probe_tests"] == 22
    assert "/home/" not in CORPUS.read_text(encoding="utf-8")
