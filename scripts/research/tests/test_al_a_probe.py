"""Track A -- differential: identity.py vs verbatim Trinity (tools/tc_aura_identity_probe).

The probe compiles Unit::_TryStackingOrRefreshingExistingAura, Unit::GetOwnedAura,
Aura::CanStackWith and SpellInfo::_LoadSpellSpecific (+ predicates) from the pinned
TrinityCore checkout.  Discriminating questions:
  * same spell, same caster, different cast item (CU_ENCHANT_PROC unset / set);
  * different caster, stackable-on-one-slot (shared object keeps the first caster);
  * SpellSpecific classification and cross-caster CanStackWith over every current-player provider.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from aura_lifecycle import TC_ROOT, RESEARCH
from aura_lifecycle.identity import (
    SPECIFIC_VALUES, AuraObj, CreateInfo, SpellGroups, STACK_RULES, can_stack_with, probe_spell_line,
    try_refresh_stack_or_create,
)
from test_al_a_identity import mk

PROBE_DIR = RESEARCH / "tools" / "tc_aura_identity_probe"
PROBE = PROBE_DIR / "probe"
NO_GROUPS = SpellGroups([], [])


@pytest.fixture(scope="module")
def probe():
    if not (TC_ROOT / "src/server/game/Spells/Auras/SpellAuras.cpp").exists():
        pytest.skip("TrinityCore checkout absent")
    r = subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], capture_output=True, text=True)
    if r.returncode != 0 or not PROBE.exists():
        pytest.skip(f"probe build failed: {r.stderr[-400:]}")

    def run(lines: list[str]) -> list[dict]:
        out = subprocess.run([str(PROBE)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
        return [json.loads(x) for x in out.stdout.splitlines() if x.strip()]
    return run


GUID = {"A": 5, "B": 7, "I1": 91, "I2": 92, "": 0}


def test_same_caster_different_cast_item(probe):
    """Rules out 'cast item is part of identity' (plain) and 'cast item is ignored' (CU_ENCHANT_PROC)."""
    for cu, expect_found in ((0, True), (1, False)):
        p = mk(100, cu=cu)
        res = probe([probe_spell_line(p), "aura X 100 5 91 1", "create 100 5 92 1 1"])[0]
        owned = [AuraObj(p, "A", "T", cast_item="I1", label="X")]
        model = try_refresh_stack_or_create(owned, CreateInfo(p, "A", "T", cast_item="I2"), NO_GROUPS)
        assert (res["found"] == "X") is expect_found
        assert (model["outcome"] == "refresh") is expect_found
        if expect_found:
            assert res["item"] == GUID["I2"] and owned[0].cast_item == "I2"
            assert res["mod"] == ["ModStackAmount(1,1,1)"]


def test_other_caster_stackable_on_one_slot(probe):
    """Rules out per-caster identity for StackAmount>1: B finds A's aura, caster stays A."""
    p = mk(101, stack=5)
    res = probe([probe_spell_line(p), "aura X 101 5 0 1", "create 101 7 0 1 1"])[0]
    owned = [AuraObj(p, "A", "T", label="X")]
    model = try_refresh_stack_or_create(owned, CreateInfo(p, "B", "T"), NO_GROUPS)
    assert res["found"] == "X" and res["caster"] == GUID["A"]
    assert model["outcome"] == "refresh" and owned[0].caster == "A"


def test_other_caster_not_one_slot_not_found(probe):
    p = mk(102, stack=5, attrs=("SPELL_ATTR3_DOT_STACKING_RULE",))
    res = probe([probe_spell_line(p), "aura X 102 5 0 1", "create 102 7 0 1 1"])[0]
    assert res["found"] is None


def test_effect_mask_mismatch_not_refreshed(probe):
    from aura_lifecycle.identity import APPLY_AURA
    from procs.enums import aura
    p = mk(103, effects=((APPLY_AURA, aura("MOD_STAT")), (APPLY_AURA, aura("MOD_STAT"))))
    res = probe([probe_spell_line(p), "aura X 103 5 0 1", "create 103 5 0 3 1"])[0]
    assert res["found"] is None


def test_multislot_never_looked_up(probe):
    p = mk(104, attrs=("SPELL_ATTR0_PASSIVE",))
    res = probe([probe_spell_line(p), "aura X 104 5 0 1", "create 104 5 0 1 1"])[0]
    assert res["found"] is None


def _canstack_line(a: int, ac: str, b: int, bc: str, mask_a: int, mask_b: int) -> str:
    return f"canstack {a} {GUID[ac]} 0 0 {mask_a} {b} {GUID[bc]} 0 0 {mask_b}"


def test_real_player_providers_specific_and_cross_caster(al_ctx, probe):
    """Differential over every current-player unit-aura provider:
    _LoadSpellSpecific and CanStackWith(same spell, caster B vs A)."""
    from aura_lifecycle.identity import PropsBuilder
    from aura_lifecycle.providers import populations
    pb = PropsBuilder(al_ctx)
    groups = SpellGroups.from_overlay(al_ctx)
    spells = [s for s in sorted(populations(al_ctx)["player"]) if pb(s).unit_aura_mask()]
    lines, expect_spec, expect_stack = [], [], []
    rules = set()
    for s in spells:
        p = pb(s)
        lines.append(probe_spell_line(p))
        rule = groups.rule(p.first_rank, p.first_rank)
        if rule != "DEFAULT":
            rules.add(f"rule {p.first_rank} {p.first_rank} {STACK_RULES.index(rule)}")
    lines += sorted(rules)
    for s in spells:
        p = pb(s)
        lines.append(f"specific {s}")
        expect_spec.append(SPECIFIC_VALUES[p.spell_specific])
    for s in spells:
        p = pb(s)
        m = p.unit_aura_mask()
        lines.append(_canstack_line(s, "B", s, "A", m, m))
        expect_stack.append(can_stack_with(AuraObj(p, "B", "T"), AuraObj(p, "A", "T"), groups))
    out = probe(lines)
    got_spec = [o["specific"] for o in out[:len(spells)]]
    got_stack = [o["stack"] for o in out[len(spells):]]
    spec_diff = [(s, e, g) for s, e, g in zip(spells, expect_spec, got_spec) if e != g]
    stack_diff = [(s, e, g) for s, e, g in zip(spells, expect_stack, got_stack) if e != g]
    assert not spec_diff, spec_diff[:10]
    assert not stack_diff, stack_diff[:10]
    assert len(spells) > 4000


def test_real_specific_pairs(al_ctx, probe):
    """AL-D-A-01/02 witnesses through verbatim CanStackWith (same caster)."""
    from aura_lifecycle.identity import PropsBuilder
    pb = PropsBuilder(al_ctx)
    groups = SpellGroups.from_overlay(al_ctx)
    pairs = [(48265, 48263), (111400, 108370), (1022, 1044), (974, 52127), (2823, 381664), (386164, 386208)]
    spells = sorted({s for pr in pairs for s in pr})
    lines = [probe_spell_line(pb(s)) for s in spells]
    for a, b in pairs:
        r = groups.rule(pb(a).first_rank, pb(b).first_rank)
        lines.append(f"rule {pb(a).first_rank} {pb(b).first_rank} {STACK_RULES.index(r)}")
    for a, b in pairs:
        lines.append(_canstack_line(a, "A", b, "A", pb(a).unit_aura_mask(), pb(b).unit_aura_mask()))
    out = probe(lines)
    for (a, b), o in zip(pairs, out):
        model = can_stack_with(AuraObj(pb(a), "A", "T"), AuraObj(pb(b), "A", "T"), groups)
        assert o["stack"] is model is False, (a, b)
