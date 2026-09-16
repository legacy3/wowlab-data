"""Real current-data proc witnesses (snapshot 12.1.0.69497 + TDB1200.26021 overlay).

Each witness pins a fact the research document cites.  If a data refresh moves
one, re-derive it with ``proc_research.py provider <id>`` and update both.
"""

from __future__ import annotations

import pytest

from procs import enums as E
from procs.census import Census, classify
from procs.chance import RppmInputs
from procs.cli import Context, mutable_state
from procs.eligibility import ActorFacts, HolderState
from procs.events import spell_hit
from procs.state import Provider, StreamItem, Timeline

pytestmark = pytest.mark.snapshot


@pytest.fixture(scope="module")
def ctx(tables):
    return Context(str(tables.root), None, False)


@pytest.fixture(scope="module")
def census(ctx):
    return Census(ctx.defs, ctx.origins)


def facts(ctx, sid):
    d = ctx.defs.get(sid)
    return d, d.entry, classify(d)


# -- items, trinkets, enchants --------------------------------------------------

def test_rppm_icd_damage_trinket_chains_into_a_second_provider(ctx):
    d, e, rec = facts(ctx, 1250564)              # Xathuux's Last Roar, item 250228
    assert d.status == "generated" and e.chance == 101.0 and e.cooldown_ms == 30000
    assert d.info.base_ppm == 1.0 and rec["chance_model"] == "rppm"
    assert d.triggered_spells == [1254180]
    assert ctx.origins.origins(1250564).current_gear_items == [250228]
    child, ce, crec = facts(ctx, 1254180)        # the buff it applies is itself a provider
    assert ce is not None and child.triggered_spells == [1254331]
    assert ce.disable_effects_mask == 0b1         # MOD_RATING effect disabled
    assert ctx.origins.lifecycle(1254180)["lifecycle"] == "active-timed"
    assert "Aura::m_procCooldown (per Aura, shared across applications)" in mutable_state(d)


def test_stacking_proc_trinket(ctx):
    d, e, rec = facts(ctx, 1295884)              # Hex Lord's Dooming Idol, item 270169
    assert (e.chance, e.cooldown_ms, d.triggered_spells) == (101.0, 6000, [1307470])
    assert ctx.catalog.require(1307470).stack_amount == 30
    assert rec["shape"].startswith("mixed")      # plus an inert DUMMY effect
    assert ctx.defs.get(1307470).entry is None   # the stat buff is an ordinary spell


def test_fixed_icd_trinket_with_unnamed_flag_word(ctx):
    d, e, rec = facts(ctx, 1307356)              # Sszorak's Ferocity
    assert e.proc_flags == E.PROC_FLAG_KILL | E.PROC_FLAG_2_TARGET_DIES
    assert e.attributes_mask == E.PROC_ATTR_REQ_EXP_OR_HONOR
    assert rec["shape"] == "fixed-guaranteed / icd / trigger-spell"


def test_stat_proc_trinket_rppm(ctx):
    d, e, rec = facts(ctx, 1295643)              # Idol of the Howling Nexus
    assert d.info.base_ppm == 2.0 and d.triggered_spells == [1295649]
    assert e.proc_flags & E.TAKEN_HIT_PROC_FLAG_MASK
    # taken-hit flags + PROC_TRIGGER_SPELL -> LoadSpellProcs adds TRIGGERED_CAN_PROC
    assert e.attributes_mask == E.PROC_ATTR_TRIGGERED_CAN_PROC
    auras = {x.aura for x in ctx.catalog.require(1295649).effects}
    assert E.aura("MOD_STAT") in auras


def test_unknown_flag_bit_trinket_fails_closed(ctx):
    d, e, rec = facts(ctx, 1250557)              # Void Execution Mandate (on-use)
    assert d.unknown_proc_flag_bits == 0x20 << 32
    assert rec["shape"] == "unsupported:unknown-proc-flag-bits" and not rec["generic"]


def test_inert_dummy_trinket(ctx):
    d, e, rec = facts(ctx, 1250546)              # Mindpiercer's Sigil
    assert e is not None and d.triggered_spells == []
    assert rec["shape"].startswith("inert-only")


def test_rppm_equip_enchant(ctx):
    out = ctx.origins.describe_enchant(8614)     # Farstrider's Hawkeye
    provider = out["effects"][0]["proc_provider"]
    assert provider["spell_id"] == 1262337 and provider["chance"]["model"] == "rppm"
    assert provider["triggered_spells"] == [1262336]


def test_rppm_haste_weapon_oil(ctx):
    d, e, rec = facts(ctx, 1237014)              # Oil of Dawn (enchant 8053)
    assert [(m["type"], m["param"]) for m in d.info.ppm_mods] == [(1, 4)]
    assert rec["shape"] == "rppm+haste / no-icd / trigger-spell"


def test_legacy_enchant_and_item_paths(ctx):
    ench = ctx.origins.describe_enchant(803)     # Fiery Weapon: spell_enchant_proc_data PPM 6
    combat = ench["effects"][0]["enchant_combat_proc"]
    assert ench["spell_enchant_proc_data"]["ProcsPerMinute"] == 6
    assert combat["triggered_spell"] == 13897 and "two independent" in combat["rng"]
    item = ctx.origins.describe_item(647)        # Destiny: ItemEffect OnProc
    legacy = item["effects"][0]["legacy_item_proc"]
    assert legacy["triggered_spell"] == 17152
    assert item["item_template_addon_spell_ppm"] == pytest.approx(1.3)


def test_scripted_item_provider(ctx):
    d, e, rec = facts(ctx, 17619)                # Alchemist Stone
    assert d.status == "spell_proc"
    assert d.scripts["script_names"] == ["spell_item_alchemist_stone"]
    assert rec["shape"] == "script:DoCheckProc+OnEffectProc"
    assert "equip-bound" in ctx.origins.lifecycle(17619)["bindings"][0]


# -- traits and specialization ----------------------------------------------------

@pytest.mark.parametrize("sid,trigger,shape", [
    (16166, 260734, "fixed-guaranteed / no-icd / trigger-spell"),      # Shaman
    (207104, 221322, "rppm+haste / no-icd / trigger-spell"),          # Death Knight
    (205148, 266030, "rppm / no-icd / trigger-spell"),                # Warlock
    (382549, 382551, "fixed-guaranteed / icd / trigger-spell"),       # Warrior (heal)
    (5301, 5302, "rppm+other-mods / icd / trigger-spell"),            # Warrior spec spell
    (203555, 203796, "fixed-guaranteed / no-icd / trigger-spell"),    # Demon Hunter
])
def test_class_providers(ctx, sid, trigger, shape):
    d, e, rec = facts(ctx, sid)
    assert d.triggered_spells == [trigger]
    assert rec["shape"] == shape and rec["generic"]
    kinds = ctx.origins.origins(sid).kinds()
    assert {"class-trait", "spec-spell"} & set(kinds)


def test_trait_with_disabled_spellmod_effect(ctx):
    d, e, _ = facts(ctx, 16166)
    assert e.disable_effects_mask == 0b10       # ADD_FLAT_MODIFIER_BY_SPELL_LABEL is not a trigger aura


def test_revenge_trigger_aura_modifier(ctx):
    d, e, _ = facts(ctx, 5301)
    assert [(m["type"], m["param"]) for m in d.info.ppm_mods] == [(8, 202560)]
    from procs.chance import rppm_rate
    assert rppm_rate(3.0, d.info.ppm_mods, RppmInputs(auras=frozenset({202560}))).chance_percent == pytest.approx(3.6, rel=1e-6)
    assert rppm_rate(3.0, d.info.ppm_mods, RppmInputs(auras=frozenset())).chance_percent == 3.0


def test_target_held_breakable_cc_with_charges(ctx):
    d, e, rec = facts(ctx, 2094)                 # Blind
    assert rec["shape"] == "fixed-guaranteed / no-icd / charges / breakable-cc"
    assert e.proc_flags & E.TAKEN_HIT_PROC_FLAG_MASK and e.charges == 1
    assert e.spell_type_mask == E.PROC_SPELL_TYPE_DAMAGE


def test_scripted_trait_chain(ctx):
    d, e, _ = facts(ctx, 53576)                  # Infusion of Light (DUMMY with trigger spell)
    assert d.status == "spell_proc" and d.triggered_spells == [54149]
    assert d.effects[0].aura == E.SPELL_AURA_DUMMY  # DUMMY + trigger reaches HandleProcTriggerSpellAuraProc
    child, ce, crec = facts(ctx, 54149)
    assert ce.charges == 1 and crec["script_hooks"] == ["DoCheckEffectProc", "OnEffectProc"]


# -- recursion ----------------------------------------------------------------------

def test_self_recursive_provider_is_suppressed(ctx):
    d, e, _ = facts(ctx, 3439)                   # Wandering Plague triggers itself
    assert d.triggered_spells == [3439]
    own = spell_hit(ctx.catalog.require(3439), damage=1, spell_is_triggered=True,
                    spell_triggered_by_aura=3439).with_(target_mask=E.PROC_FLAG_TAKE_MELEE_ABILITY)
    res = ctx.evaluator.evaluate(d, own, "target", HolderState(), ActorFacts(actor_level=80))
    assert res.reason == "not-triggered-by-this-aura"


def test_triggered_spell_marked_not_a_proc(ctx):
    d, e, rec = facts(ctx, 370455)               # Charged Blast (Evoker)
    assert d.triggered_spells == [370454]
    assert ctx.catalog.require(370454).has_attr(E.ATTR3_NOT_A_PROC)


def test_proc_chain_rime(ctx):
    d, e, _ = facts(ctx, 59057)                  # Rime: CAST_SUCCESSFUL -> 59052 (charges)
    assert e.proc_flags == E.PROC_FLAG_2_CAST_SUCCESSFUL and e.spell_phase_mask == E.PROC_SPELL_PHASE_CAST
    child, ce, crec = facts(ctx, 59052)
    assert ce.charges == 1 and crec["actions"] == ["charge-or-cooldown-consumer"]


def test_authored_cycles_and_depth(census):
    cycles = {tuple(c["spells"]) for c in census.graph.cycles_with_proc_edge()}
    assert (3439,) in cycles and (38334, 38346) in cycles
    depth = census.graph.max_proc_depth()
    assert depth["max_proc_edges_on_acyclic_path"] == 3


# -- state machine on a real provider ------------------------------------------------

def test_real_rppm_icd_timeline(ctx):
    d = ctx.defs.get(1250564)
    p = Provider(d, "actor", rppm_inputs=RppmInputs())
    tl = Timeline(ctx.evaluator, [p])
    tl.apply(1250564, 0)
    ability = ctx.catalog.require(1254180)
    ev = spell_hit(ability, damage=100)
    facts_ = ActorFacts(actor_level=90)
    log = tl.run([StreamItem(1000, "event", event=ev, rolls=[0.0], facts=facts_),
                  StreamItem(2000, "event", event=ev, rolls=[], facts=facts_),
                  StreamItem(31000, "event", event=ev, rolls=[99.0], facts=facts_)])
    assert log[0]["steps"][0]["success"] and log[0]["steps"][0]["triggered"][0]["cast_spell"] == 1254180
    assert log[1]["steps"][0]["reason"] == "proc-cooldown"
    # 30 s since both attempt (clamped to 10) and success: bad-luck term floors at 1
    assert log[2]["steps"][0]["chance"]["chance_percent"] == pytest.approx(10 / 60 * 100, rel=1e-5)
    assert log[2]["steps"][0]["success"] is False


# -- census snapshot --------------------------------------------------------------------

def test_census_headline_numbers(census):
    s = census.summary(list(census.records.values()))
    assert s["providers"] == 14227
    assert s["with_entry"] == 12500
    assert s["status"] == {"generated": 11240, "no-entry": 1727, "spell_proc": 1260}
    assert census.unknown_bits() == {"0x0000002000000000": 177, "0x0000000800000000": 1}
