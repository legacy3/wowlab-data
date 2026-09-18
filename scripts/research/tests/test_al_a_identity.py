"""Track A -- aura identity (identity.py): synthetic discriminators + real-data witnesses."""

from __future__ import annotations

import dataclasses
import json

import pytest

from aura_lifecycle import CORPORA, FailClosed
from aura_lifecycle.identity import (
    APPLY_AURA, PERSISTENT_AREA_AURA, AuraObj, CreateInfo, Eff, Props, SpellGroups, can_stack_with,
    lookup_key, same_spell_other_caster, same_spell_same_caster, try_refresh_stack_or_create,
)
from procs.enums import attr, aura, effect

PERIODIC_DAMAGE = aura("PERIODIC_DAMAGE")
MOD_STAT = aura("MOD_STAT")
AREA_RAID = effect("APPLY_AREA_AURA_RAID")
NO_GROUPS = SpellGroups([], [])


def mk(spell: int, *, effects=((APPLY_AURA, MOD_STAT),), stack: int = 0, family: int = 0, attrs=(), cu: int = 0,
       first_rank: int | None = None, triggers: dict[int, int] | None = None) -> Props:
    words = [0] * 17
    for name in attrs:
        w, b = attr(name)
        words[w] |= b
    effs = tuple(Eff(i, e, a, (triggers or {}).get(i, 0), 6, 0, 0) for i, (e, a) in enumerate(effects))
    return Props(spell=spell, first_rank=first_rank or spell, rank=1, family=family, family_flags=0, stack_amount=stack,
                 attributes=tuple(words), cu=cu, dispel=0, aura_interrupt0=0, effects=effs, max_targets=0)


# ---------------------------------------------------------------------------
# synthetic discriminators
# ---------------------------------------------------------------------------

def test_one_slot_any_caster_is_shared_and_keeps_first_caster():
    """Rules out 'aura identity is always (spell, caster, target)' (Core AuraKey model)."""
    p = mk(1, stack=5)
    owned: list[AuraObj] = []
    a = try_refresh_stack_or_create(owned, CreateInfo(p, "A", "T"), NO_GROUPS)
    b = try_refresh_stack_or_create(owned, CreateInfo(p, "B", "T"), NO_GROUPS)
    assert a["outcome"] == "create" and b["outcome"] == "refresh"
    assert len(owned) == 1 and owned[0].caster == "A"
    assert "caster stays A" in b["changes"][0]


def test_dot_stacking_rule_turns_shared_into_per_caster():
    """Rules out 'StackAmount>1 always shares the object': ATTR3_DOT_STACKING_RULE disables the wildcard."""
    p = mk(2, effects=((APPLY_AURA, PERIODIC_DAMAGE),), stack=10, attrs=("SPELL_ATTR3_DOT_STACKING_RULE",))
    assert lookup_key(p, "A", "")["caster"] == "A"
    owned: list[AuraObj] = []
    try_refresh_stack_or_create(owned, CreateInfo(p, "A", "T"), NO_GROUPS)
    out = try_refresh_stack_or_create(owned, CreateInfo(p, "B", "T"), NO_GROUPS)
    assert out["outcome"] == "create" and len([a for a in owned if not a.removed]) == 2


def test_channeled_stack_spell_is_per_caster():
    p = mk(3, stack=3, attrs=("SPELL_ATTR1_IS_CHANNELLED",))
    assert not p.one_slot_any_caster


def test_non_periodic_buff_from_second_caster_replaces():
    """Rules out 'different casters coexist unless DB2 says otherwise' (same-rank-chain fallthrough)."""
    p = mk(4)
    assert same_spell_other_caster(p, NO_GROUPS) == ("replace", "SpellAuras.cpp:1766 same rank chain")


def test_periodic_non_area_from_second_caster_coexists_but_area_periodic_replaces():
    dot = mk(5, effects=((APPLY_AURA, PERIODIC_DAMAGE),))
    assert same_spell_other_caster(dot, NO_GROUPS)[0] == "coexist"
    area = dataclasses.replace(dot, spell=6, first_rank=6,
                               effects=(Eff(0, APPLY_AURA, PERIODIC_DAMAGE, 0, 15, 0, 0),))  # 15 = TARGET_UNIT_SRC_AREA_ENEMY
    assert same_spell_other_caster(area, NO_GROUPS)[0] == "replace"


def test_family_mismatch_short_circuits_before_caster_checks():
    a = AuraObj(mk(7, family=6), "A", "T")
    b = AuraObj(mk(8, family=7, first_rank=7), "A", "T")
    why: list[str] = []
    assert can_stack_with(a, b, NO_GROUPS, trace=why) and why[-1].startswith("SpellAuras.cpp:1689")


def test_passive_multislot_same_caster_replaces_other_caster_coexists():
    """Rules out 'same SpellId + same caster always refreshes' (multislot has no lookup)."""
    p = mk(9, attrs=("SPELL_ATTR0_PASSIVE",))
    assert lookup_key(p, "A", "") is None
    assert same_spell_same_caster(p, NO_GROUPS)[0] == "replace"
    assert same_spell_other_caster(p, NO_GROUPS)[0] == "coexist"


def test_passive_with_cast_item_coexists_with_same_caster():
    p = mk(10, attrs=("SPELL_ATTR0_PASSIVE",))
    owned: list[AuraObj] = []
    try_refresh_stack_or_create(owned, CreateInfo(p, "A", "T", cast_item="I1"), NO_GROUPS)
    out = try_refresh_stack_or_create(owned, CreateInfo(p, "A", "T", cast_item="I2"), NO_GROUPS)
    assert out["outcome"] == "create" and not any(a.removed for a in owned)


def test_cast_item_is_identity_only_with_enchant_proc():
    """Rules out 'cast item is part of the key': without CU_ENCHANT_PROC the item is overwritten on refresh."""
    plain = mk(11)
    owned: list[AuraObj] = []
    try_refresh_stack_or_create(owned, CreateInfo(plain, "A", "T", cast_item="I1"), NO_GROUPS)
    out = try_refresh_stack_or_create(owned, CreateInfo(plain, "A", "T", cast_item="I2"), NO_GROUPS)
    assert out["outcome"] == "refresh" and owned[0].cast_item == "I2"
    ench = mk(12, cu=0x1)
    owned = []
    try_refresh_stack_or_create(owned, CreateInfo(ench, "A", "T", cast_item="I1"), NO_GROUPS)
    out = try_refresh_stack_or_create(owned, CreateInfo(ench, "A", "T", cast_item="I2"), NO_GROUPS)
    assert out["outcome"] == "create" and not owned[0].removed and len(owned) == 2


def test_effect_mask_mismatch_recreates_and_removes_old():
    """Rules out 'a found aura is always refreshed'."""
    p = mk(13, effects=((APPLY_AURA, MOD_STAT), (APPLY_AURA, MOD_STAT)))
    owned: list[AuraObj] = []
    try_refresh_stack_or_create(owned, CreateInfo(p, "A", "T", effect_mask=0b01), NO_GROUPS)
    out = try_refresh_stack_or_create(owned, CreateInfo(p, "A", "T"), NO_GROUPS)
    assert out["outcome"] == "replace" and owned[0].removed and not owned[1].removed


def test_trigger_link_protects_before_groups():
    parent = mk(20, triggers={0: 21})
    child = mk(21)
    groups = SpellGroups([(1001, 20), (1001, 21)], [(1001, 1)])
    why: list[str] = []
    assert can_stack_with(AuraObj(child, "A", "T"), AuraObj(parent, "A", "T"), groups, trace=why)
    assert why[-1].startswith("SpellAuras.cpp:1654")


def test_exclusive_group_and_subgroup_exemption():
    groups = SpellGroups([(1001, 30), (1001, 31), (1001, -1002), (1002, 32), (1002, 33)], [(1001, 1)])
    assert groups.rule(30, 31) == "EXCLUSIVE"
    assert groups.rule(30, 32) == "EXCLUSIVE"
    assert groups.rule(32, 33) == "DEFAULT"   # common sub-group 1002 has no rule; 1001 excluded by the sub-group test
    reserved = SpellGroups([(10, 40), (10, 41)], [(10, 1)])
    assert reserved.rule(40, 41) == "DEFAULT" and reserved.dropped


def test_exclusive_highest_fails_closed():
    groups = SpellGroups([(1001, 50), (1001, 51)], [(1001, 4)])
    owned = [AuraObj(mk(50), "A", "T")]
    with pytest.raises(FailClosed):
        try_refresh_stack_or_create(owned, CreateInfo(mk(51), "A", "T"), groups)


def test_dynobj_same_caster_same_spell_replaces_on_recipient():
    p = mk(60, effects=((PERSISTENT_AREA_AURA, PERIODIC_DAMAGE),))
    a, b, c = (AuraObj(p, x, "T", kind="dynobj") for x in ("A", "A", "B"))
    assert not can_stack_with(b, a, NO_GROUPS) and can_stack_with(c, a, NO_GROUPS)


# ---------------------------------------------------------------------------
# real data
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tools(al_ctx):
    from aura_lifecycle.identity import PropsBuilder
    return PropsBuilder(al_ctx), SpellGroups.from_overlay(al_ctx)


def test_agony_witness_dot_stacking_rule(al_ctx, tools):
    pb, groups = tools
    p = pb(980)
    assert 980 in al_ctx.scope.reach and p.dot_stacking_rule and p.stack_amount > 1 and not p.one_slot_any_caster
    assert same_spell_other_caster(p, groups)[0] == "coexist"


def test_non_stacking_buffs_replace_across_casters(al_ctx, tools):
    pb, groups = tools
    for s in (1126, 10060, 1160):
        assert s in al_ctx.scope.reach
        assert same_spell_other_caster(pb(s), groups) == ("replace", "SpellAuras.cpp:1766 same rank chain")


def test_exclusive_group_pairs(al_ctx, tools):
    pb, groups = tools
    for a, b in ((2823, 381664), (386164, 386208)):
        assert a in al_ctx.scope.reach and b in al_ctx.scope.reach
        assert groups.rule(pb(a).first_rank, pb(b).first_rank) == "EXCLUSIVE"
        assert not can_stack_with(AuraObj(pb(a), "A", "T"), AuraObj(pb(b), "A", "T"), groups)


def test_legacy_spell_specific_defects(al_ctx, tools):
    """AL-D-A-01/02: modern spells caught by legacy SpellSpecific classes (reproduced, not corrected)."""
    pb, groups = tools
    assert pb(48265).spell_specific == pb(48263).spell_specific == "PRESENCE"
    assert pb(48263).passive
    assert not can_stack_with(AuraObj(pb(48265), "A", "A"), AuraObj(pb(48263), "A", "A"), groups)
    assert pb(111400).spell_specific == pb(108370).spell_specific == "WARLOCK_ARMOR"


def test_divine_hymn_shared_across_casters(al_ctx, tools):
    pb, groups = tools
    assert pb(64844).one_slot_any_caster
    assert same_spell_other_caster(pb(64844), groups)[0] == "shared-refresh"


def test_corpus_counts_match_census(al_ctx, tools):
    from aura_lifecycle.identity import census
    path = CORPORA / "identity.json"
    if not path.exists():
        pytest.skip("identity.json not generated")
    corpus = json.loads(path.read_text(encoding="utf-8"))
    pb, groups = tools
    fresh = census(al_ctx, pb, groups)["counts"]
    assert fresh == corpus["counts"]
    player = fresh["player"]
    assert sum(player["lookup"].values()) == player["evaluated"]
    assert sum(player["other_caster"].values()) == player["evaluated"]


# --- review corrections (R3-11, R3-21, R1) ---------------------------------------------------

def test_area_aura_second_owner_blocked_not_replaced():
    """R3-11: rules out 'other caster replaces' for area auras (recipient-map admission, SpellAuras.cpp:732-745)."""
    from aura_lifecycle.identity import area_other_caster
    p = mk(70, effects=((AREA_RAID, MOD_STAT),))
    out, why = same_spell_other_caster(p, NO_GROUPS)
    assert out == "area:blocked" and "recipient map" in why
    assert area_other_caster(p, NO_GROUPS)["on_owner"] == "replace"
    passive = mk(71, effects=((AREA_RAID, MOD_STAT),), attrs=("SPELL_ATTR0_PASSIVE",))
    assert passive.passive_stackable_with_ranks
    assert area_other_caster(passive, NO_GROUPS)["on_owner"] == "coexist"


def test_shared_object_adopts_latest_applier_base_points():
    """R3-21: shared any-caster object keeps caster A but carries B's base points."""
    p = mk(72, stack=5)
    owned: list[AuraObj] = []
    try_refresh_stack_or_create(owned, CreateInfo(p, "A", "T"), NO_GROUPS)
    out = try_refresh_stack_or_create(owned, CreateInfo(p, "B", "T"), NO_GROUPS)
    assert owned[0].caster == "A" and owned[0].base_from == "B"
    assert any("base points" in c for c in out["changes"])


def test_item_sourced_passive_same_caster_coexists():
    """R1: rules out 'same caster re-applying a passive always replaces' (cast item skips SpellAuras.cpp:1651)."""
    from aura_lifecycle.identity import signature
    p = mk(73, attrs=("SPELL_ATTR0_PASSIVE",))
    assert signature(p, NO_GROUPS)["same_caster"] == "replace"
    sig = signature(p, NO_GROUPS, item_sourced=True)
    assert sig["same_caster"] == "coexist" and sig["same_caster_no_item"] == "replace" and "item-sourced" in sig["flags"]


def test_area_only_passive_witnesses_real(al_ctx, tools):
    pb, groups = tools
    for s in (1270083, 363558, 368412, 400129, 404752):
        out = same_spell_other_caster(pb(s), groups)[0]
        assert out.startswith("area:") and out != "replace"
