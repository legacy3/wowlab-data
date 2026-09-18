"""Hostile review R1 (overgeneralization): pin the defects found in the lead's axes/rules.

Each test documents a defect or a vacuity at the pinned inputs; when the pass fixes one,
the matching test must be rewritten to assert the corrected statement.  All tests read
committed corpora, ``data/tables`` CSVs or call pure classifiers on synthetic facts, so
none loads the ~4 GB context.
"""

from __future__ import annotations

import csv
import itertools
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pytest

from aura_lifecycle.duration import (
    ATTR0_PASSIVE,
    ATTR1_AURA_UNIQUE,
    ATTR5_AURA_UNIQUE_PER_CASTER,
    ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION,
    DurationEntry,
    SpellFacts,
    family,
)
from aura_lifecycle.refresh import classify

ROOT = Path(__file__).resolve().parents[3]
CORPORA = ROOT / "docs" / "research" / "aura-lifecycle-corpora"
TABLES = ROOT / "data" / "tables"


def _json(name: str):
    path = CORPORA / name
    if not path.exists():
        pytest.skip(f"{name} absent")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _axes() -> dict:
    return _json("semantic-axes.json")


@lru_cache(maxsize=1)
def _player() -> frozenset[str]:
    cat = _json("lifecycle-signatures.json")["catalog"]["full"]
    return frozenset(str(s) for e in cat for s in e.get("player_members", []))


def _facts(attrs: dict[int, int], stack: int = 0, entry: DurationEntry | None = None) -> SpellFacts:
    words = [0] * 17
    for w, m in attrs.items():
        words[w] |= m
    return SpellFacts(spell=900001, attributes=tuple(words), duration_entry=entry, stack_amount=stack)


def _attr_sets():
    keys = [ATTR1_AURA_UNIQUE, ATTR5_AURA_UNIQUE_PER_CASTER, ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION]
    for n in range(len(keys) + 1):
        for combo in itertools.combinations(keys, n):
            out: dict[int, int] = {}
            for w, m in combo:
                out[w] = out.get(w, 0) | m
            yield out


def test_x02_holds_by_construction():
    """AL-R-X-02 cannot be falsified by the census: RefreshPolicy is new-object iff passive.

    Rules out reading "holds on census (3,453 / 0)" as evidence: refresh.classify returns
    new-object for every passive whatever stack capacity or unique/pandemic attributes
    (refresh.py is_multislot), so the rule restates its own classifier.
    """
    for attrs in _attr_sets():
        for stack in (0, 1, 2, 255):
            a = dict(attrs)
            a[ATTR0_PASSIVE[0]] = a.get(ATTR0_PASSIVE[0], 0) | ATTR0_PASSIVE[1]
            assert classify(_facts(a, stack))["branch"] == "new-object"


def test_x11_permanent_carriers_never_reach_the_carry():
    """R1-04 (fixed): the pandemic carry is gated by Spell.cpp:3268 (`AuraDuration > 0`).

    Originally classify assigned carry=pandemic-reads-refreshed-duration to permanent ATTR13
    spells too (player 111400, 196099).  After the correction a permanent record has no carry and
    no player row pairs a pandemic carry with a non-finite duration family.
    """
    permanent = DurationEntry(id=21, duration=-1, max_duration=-1, per_resource=0)
    f = _facts({ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION[0]: ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION[1]},
               entry=permanent)
    assert family(f) == "permanent-sentinel"
    assert not classify(f)["carry"].startswith("pandemic")
    rows = _axes()["player_rows"]
    perm = sorted(s for s in _player() if "carry=pandemic" in rows[s]["RefreshPolicy"]
                  and rows[s]["DurationPolicy"] in ("permanent-sentinel", "no-duration-index-active",
                                                    "no-duration-index-passive", "zero"))
    assert perm == []


def test_x13_non_passive_counterexamples_are_channels_and_unique():
    """R1 (headline 1, fixed): X-01's counterexamples are carried by passives.

    On non-passive owned-aura player spells (lead rule AL-R-X-13) the only counterexamples are the
    41 self-channels (recast cancels the old channel, R2-07) and the 6 unique-no-timer-refresh spells.
    """
    rows = _axes()["player_rows"]
    act = [s for s in _player() if not rows[s]["ApplicationPolicy"].startswith("passive")
           and rows[s]["AuraIdentity"] != "no-owned-aura" and "dynobj" not in rows[s]["AuraIdentity"]]
    bad = [s for s in act if not rows[s]["RefreshPolicy"].startswith(("refresh", "stack-and-refresh"))]
    assert (len(act), len(bad)) == (763, 47)
    assert Counter(rows[s]["RefreshPolicy"].split("|")[0] for s in bad) == Counter(
        {"self-channel-cancel-then-create": 41, "unique-no-timer-refresh": 6})


def test_class_skills_population_has_no_mounts():
    """R1-01 (fixed): player+class-skills used to be 82% SkillLine 777 "Mounts".

    The corrected population keeps only category-7 lines naming exactly one class; a Mounts-line
    spell outside ``player`` may remain only when it is also on a class line (class mounts).
    """
    if not (TABLES / "SkillLineAbility.csv").exists():
        pytest.skip("no table snapshot")
    rows = _axes()["player_rows"]
    lines: dict[str, set[str]] = {}
    with (TABLES / "SkillLineAbility.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["Spell"] in rows:
                lines.setdefault(r["Spell"], set()).add(r["SkillLine"])
    added = set(rows) - _player()
    mounts = {s for s in added if "777" in lines.get(s, ())}
    # class mounts (Summon Warhorse 13819, Felsteed 5784, ...) are also on their class line
    assert all(lines[s] - {"777"} for s in mounts)
    assert len(mounts) < 0.05 * len(added)


def test_model_passive_population_matches_track_g():
    """R1-02 (fixed): one passive definition (Trinity's corrected IsPassive) everywhere.

    X-02 now applies to the same 16,812 passives as AL-R-G-01, and X-04 has G-03's 9 counterexamples.
    """
    lead = {r["id"]: r for r in _axes()["rules"]}
    assert lead["AL-R-X-02"]["per_population"]["all"]["applies"] == 16812
    assert lead["AL-R-X-04"]["per_population"]["all"]["counterexamples"] == 9


def test_school_absorbs_are_not_classified_bonuses_live():
    """R1-03 (fixed): SCHOOL_ABSORB bakes SpellAbsorbBonusDone/Taken at creation (SpellAuraEffects.cpp:799-806).

    No player absorb may carry the default "base-captured/bonuses-live" amount class any more.
    """
    if not (TABLES / "SpellEffect.csv").exists():
        pytest.skip("no table snapshot")
    rows = _axes()["player_rows"]
    player = _player()
    absorb = set()
    with (TABLES / "SpellEffect.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["DifficultyID"] == "0" and r["EffectAura"] == "69" and r["SpellID"] in player:
                absorb.add(r["SpellID"])
    assert len(absorb) >= 40
    assert all("absorb-bonuses-snapshot" in rows[s]["AmountPolicy"] for s in absorb)


def test_signature_classes_merge_distinct_axis_tuples():
    """R1-07: track J full signatures still group some spells the lead axes separate.

    Before J added the omitted lifecycle attributes this was 37 of 222 player classes; after the
    correction J reports the residue in ``axis_agreement``.  The recomputation must equal J's figure
    and stay nonzero (signatures and axes are different partitions, not the same model).
    """
    rows = _axes()["player_rows"]
    axes = _axes()["axes"]
    sig = _json("lifecycle-signatures.json")
    cat = sig["catalog"]["full"]
    hetero = 0
    for e in cat:
        m = [str(s) for s in e.get("player_members", [])]
        if len({tuple(rows[s][a] for a in axes) for s in m}) > 1:
            hetero += 1
    assert hetero > 0
    assert hetero == sig["axis_agreement"]["full"]["player"]["classes_with_multiple_axis_tuples"]


def test_redundancy_is_strict_zero_only():
    """"No axis determines another" is true only at zero violations: RefreshPolicy -> StackPolicy
    fails on 25 of 4,218 player spells (both classify from passive/StackAmount/unique)."""
    rows = _axes()["player_rows"]
    seen: dict[str, Counter] = {}
    for s in _player():
        seen.setdefault(rows[s]["RefreshPolicy"], Counter())[rows[s]["StackPolicy"]] += 1
    violations = sum(sum(c.values()) - max(c.values()) for c in seen.values())
    assert 0 < violations <= 30
