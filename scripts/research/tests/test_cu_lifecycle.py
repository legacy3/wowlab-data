"""Track A5: lifecycle phases, duration policy, slot replacement, owner events."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from controlled_units.lifecycle import (CHARM, CREATE_HUNTER_PET, CREATE_SUMMON_PET, CREATE_TEMPSUMMON, DEATH, NUMERIC,
                                        OWNER_EVENTS, REPLACEMENT_RULES, UPDATE_DESPAWN, policy, simulate_tempsummon,
                                        simulate_totem)
from controlled_units.population import Context, Record
from controlled_units.vocabulary import TEMPSUMMON_TYPE, SummonProps, summon_branch
from cu_a_helpers import context, coordinates_in, trinity_line, trinity_root

PHASES = {"CREATE_TEMPSUMMON": CREATE_TEMPSUMMON, "CREATE_SUMMON_PET": CREATE_SUMMON_PET, "CREATE_HUNTER_PET": CREATE_HUNTER_PET,
          "CHARM": CHARM, "UPDATE_DESPAWN": UPDATE_DESPAWN, "DEATH": DEATH, "OWNER_EVENTS": OWNER_EVENTS}


def synthetic(control: int, title: int, slot: int, duration: int | None, flags0: int = 0, props_id: int = 0) -> Record:
    p = SummonProps(props_id, control, 0, title, slot, flags0, 0)
    b = summon_branch(p, 1, duration, props_id)
    return Record(1, "synthetic", 0, 0, 28, "SPELL_EFFECT_SUMMON", 0, "", "Spell::EffectSummonType", b.category, 1, props_id,
                  p.to_dict(), b.to_dict(), duration, "synthetic", [], [], [], [], False, None, "not-applicable", None)


def _policies(spell: int) -> list[dict]:
    ctx = context()
    info = ctx.b.catalog.get(spell)
    scope = ctx.scope if spell in ctx.scope.reach else ctx.extended
    return [policy(ctx.record_for(spell, e, scope, "default")) for e in info.effects if Context.is_population_effect(e)]


def test_phase_lists_are_ordered_integers():
    for name, phases in PHASES.items():
        assert [p["phase"] for p in phases] == list(range(1, len(phases) + 1)), name
        assert all(isinstance(p["phase"], int) and p["coordinate"] and p["function"] for p in phases), name


def test_phase_and_rule_coordinates_resolve_in_the_pinned_checkout():
    root = trinity_root()
    sizes: dict[str, int] = {}
    blobs = [p["coordinate"] for ps in PHASES.values() for p in ps] + [r["coordinate"] for r in REPLACEMENT_RULES]
    for blob in blobs:
        coords = coordinates_in(blob)
        assert coords, blob
        for path, a, b in coords:
            if path not in sizes:
                sizes[path] = len((root / path).read_text(encoding="utf-8", errors="replace").splitlines())
            assert 0 < a <= b <= sizes[path], (blob, path, a, b)


def test_slot_rule_anchor_and_policy():
    assert "if (slot != 0)" in trinity_line("src/server/game/Entities/Creature/TemporarySummon.cpp", 230)
    assert "slot == SUMMON_SLOT_ANY_TOTEM" in trinity_line("src/server/game/Entities/Creature/TemporarySummon.cpp", 227)
    totem = policy(synthetic(1, 4, -1, 15000))
    assert "any-totem-slot" in totem["replacement"] and "totem-duration" in totem["replacement"]
    guardian_slot0 = policy(synthetic(1, 2, 0, 15000))
    assert guardian_slot0["replacement"] == ["none"] and "no m_SummonSlot bookkeeping" in guardian_slot0["slot"]
    quest = policy(synthetic(1, 2, 6, 15000))
    assert "slot" in quest["replacement"]
    pet_control = policy(synthetic(2, 2, 0, 15000))
    assert {"guardian-pet", "minion-succession"} <= set(pet_control["replacement"])
    num = policy(synthetic(1, 2, 0, 15000, props_id=64))
    assert "num-summons" in num["replacement"]


def test_replacement_rules_cover_required_mechanisms():
    names = {r["rule"] for r in REPLACEMENT_RULES}
    assert {"slot", "any-totem-slot", "guardian-pet", "one-pet-per-player", "minion-succession", "num-summons",
            "by-entry-removal", "totem-duration", "pet-not-tempsummon-timed", "timer-mutation"} <= names
    assert all(r["evidence_class"] == "trinity-consumer" for r in REPLACEMENT_RULES)


def test_totem_zero_or_permanent_duration_is_removed_on_first_update():
    assert simulate_totem(0, [(1, True, True)]) == 0
    assert simulate_totem(-1, [(100, True, True)]) == 0
    assert simulate_totem(250, [(100, True, True)] * 5) == 2          # 250 -> 150 -> 50 -> (50 <= 100) removed
    assert simulate_totem(10_000, [(100, False, True)]) == 0          # owner dead
    assert "m_duration <= Milliseconds(diff)" in trinity_line("src/server/game/Entities/Totem/Totem.cpp", 42)
    t = policy(synthetic(1, 4, -1, 0))
    assert "first Totem::Update" in t["timer"]


def test_tempsummon_timer_semantics():
    alive = [(100, False, "ALIVE")] * 20
    assert simulate_tempsummon("TEMPSUMMON_TIMED_DESPAWN", 300, alive) == 2        # <= diff, not < diff
    assert simulate_tempsummon("TEMPSUMMON_MANUAL_DESPAWN", 300, alive) is None
    assert simulate_tempsummon("TEMPSUMMON_DEAD_DESPAWN", 300, alive + [(100, False, "DEAD")]) == 20
    combat = [(100, True, "ALIVE")] * 5 + [(100, False, "ALIVE")] * 5
    assert simulate_tempsummon("TEMPSUMMON_TIMED_DESPAWN_OUT_OF_COMBAT", 300, combat) == 7   # timer reset while in combat
    assert simulate_tempsummon("TEMPSUMMON_TIMED_DESPAWN", 300, combat) == 2
    assert simulate_tempsummon("TEMPSUMMON_CORPSE_DESPAWN", 300, [(100, False, "ALIVE"), (100, False, "CORPSE")]) == 1


@settings(max_examples=200, deadline=None)
@given(duration=st.integers(1, 5000), diffs=st.lists(st.integers(1, 400), min_size=1, max_size=40))
def test_timed_despawn_fires_on_the_tick_that_reaches_duration(duration, diffs):
    got = simulate_tempsummon("TEMPSUMMON_TIMED_DESPAWN", duration, [(d, False, "ALIVE") for d in diffs])
    total, expected = 0, None
    for i, d in enumerate(diffs):
        if duration - total <= d:
            expected = i
            break
        total += d
    assert got == expected
    # out-of-combat variant is identical when never in combat
    assert simulate_tempsummon("TEMPSUMMON_TIMED_DESPAWN_OUT_OF_COMBAT", duration, [(d, False, "ALIVE") for d in diffs]) == got


def test_every_tempsummon_type_is_simulated():
    for name in TEMPSUMMON_TYPE.values():
        simulate_tempsummon(name, 100, [(10, False, "ALIVE")])
    assert simulate_tempsummon("bogus", 100, [(10, False, "ALIVE")]) == 0


def test_numeric_records_follow_the_brief():
    keys = {"source_type", "intermediate_type", "aggregation_precision", "rounding", "integer_conversion", "units", "evidence_class"}
    assert {n["quantity"] for n in NUMERIC} >= {"summon duration", "TempSummon timer", "Totem timer", "Pet timer", "summon level"}
    for n in NUMERIC:
        assert keys <= set(n), n["quantity"]
    assert "(double(basevalue) + totalflat) * totalmul" in trinity_line("src/server/game/Entities/Player/Player.cpp", 22851)
    assert "float totalmul = 1.0f;" in trinity_line("src/server/game/Entities/Player/Player.cpp", 22846)


@pytest.mark.snapshot
def test_witness_duration_policies():
    (raise_dead,) = _policies(46585)
    assert raise_dead["creation"] == "CREATE_TEMPSUMMON" and raise_dead["summon_path"] == "SummonGuardian"
    assert raise_dead["tempsummon_type"] == "TEMPSUMMON_TIMED_DESPAWN" and raise_dead["duration_ms_spellduration"] == 60000
    (hst,) = _policies(5394)
    assert hst["cxx_class"] == "Totem" and "any-totem-slot" in hst["replacement"]
    (felguard,) = _policies(30146)
    assert felguard["creation"] == "CREATE_SUMMON_PET" and felguard["tempsummon_type"] is None
    assert "duration 0" in felguard["duration_source"]
    mc = _policies(605)
    assert mc and all(p["creation"] == "CHARM" and p["timer"] == "aura timer" for p in mc)


@pytest.mark.snapshot
def test_default_population_has_no_zero_duration_totem_and_every_record_has_a_policy():
    ctx = context()
    for r in ctx.records(ctx.scope, "default"):
        p = policy(r)
        assert p["category"] == r.category and p["creation"]
        if r.branch.get("cxx_class") == "Totem":
            assert r.duration_ms and r.duration_ms > 0, r.spell_id
            assert not r.branch.get("totem_removed_on_first_update")
        if r.branch.get("summon_path") in ("SummonGuardian", "Map::SummonCreature") and r.branch.get("cxx_class") != "Totem":
            assert p["tempsummon_type"] in TEMPSUMMON_TYPE.values()
