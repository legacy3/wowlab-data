"""Track J census: lifecycle-axis projection, external surfaces, drift diff.

Synthetic tests pin one projection each on a tiny in-memory reader and say which
wrong model they rule out.  Real-data tests (``al_ctx``) check witnesses and
the regenerated corpus against the snapshot.
"""

from __future__ import annotations

import json

import pytest

from aura_lifecycle import CORPORA, FailClosed, census
from procs.enums import aura, effect


class FakeData:
    """Minimal AuraData-shaped reader (DIFFICULTY_NONE only)."""

    def __init__(self, effects=None, rows=None, durations=None):
        self._effects = effects or {}
        self._rows = rows or {}
        self._durations = durations or {}
        self.absent: set[str] = set()

    def effects(self, spell, difficulty=0):
        return list(self._effects.get(spell, []))

    def row(self, table, spell, difficulty=0):
        return self._rows.get((table, spell))

    def duration(self, index):
        return self._durations.get(index)


def eff(index, effect_type, aura_type, period=0, target=1):
    return {"EffectIndex": index, "Effect": effect_type, "EffectAura": aura_type, "EffectAuraPeriod": period,
            "ImplicitTarget_0": target, "ImplicitTarget_1": 0}


def misc(duration_index=0, **attrs):
    row = {"DurationIndex": duration_index, "PvPDurationIndex": 0, "MinDuration": 0}
    row.update({f"Attributes_{i}": attrs.get(f"a{i}", 0) for i in range(17)})
    return row


APPLY = effect("APPLY_AURA")


# ---------------------------------------------------------------------------
# duration family
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry, passive, family", [
    (None, True, "implicit-permanent"),
    (None, False, "implicit-zero"),
    ({"Duration": -1, "MaxDuration": -1, "DurationPerResource": 0}, False, "permanent"),
    ({"Duration": -600000, "MaxDuration": 600000, "DurationPerResource": 0}, False, "abs-negative"),
    ({"Duration": 0, "MaxDuration": 0, "DurationPerResource": 0}, False, "zero"),
    ({"Duration": 6000, "MaxDuration": 6000, "DurationPerResource": 0}, False, "finite-fixed"),
    ({"Duration": 6000, "MaxDuration": 36000, "DurationPerResource": 6000}, False, "finite-per-resource"),
    ({"Duration": 1000, "MaxDuration": 5000, "DurationPerResource": 0}, False, "finite-varying"),
    ({"Duration": 10000, "MaxDuration": -1, "DurationPerResource": 0}, False, "finite-max-permanent"),
])
def test_duration_family(entry, passive, family):
    """Rules out "every negative duration is permanent" (-600000 is abs-negative, SpellInfo.cpp:3990)
    and "no DurationEntry means permanent" (only for passive spells, SpellInfo.cpp:3988)."""
    data = FakeData(durations={7: entry} if entry else {})
    m = misc(7 if entry else 0, a0=0x40 if passive else 0)
    assert census.duration_family(data, m) == family


def test_duration_family_missing_misc_is_flagged_not_defaulted():
    assert census.duration_family(FakeData(), None) == "no-misc"
    assert census.base_duration_ms(FakeData(), None) is None


def test_base_duration_takes_abs_of_non_sentinel_negative():
    """Mirrors GetDuration: -1 stays -1, other negatives are abs()'d (rules out sign-preserving model)."""
    data = FakeData(durations={1: {"Duration": -1, "MaxDuration": -1, "DurationPerResource": 0},
                               2: {"Duration": -600000, "MaxDuration": 600000, "DurationPerResource": 0}})
    assert census.base_duration_ms(data, misc(1)) == -1
    assert census.base_duration_ms(data, misc(2)) == 600000
    assert census.base_duration_ms(data, misc(0, a0=0x40)) == -1
    assert census.base_duration_ms(data, misc(0)) == 0


# ---------------------------------------------------------------------------
# effect projection
# ---------------------------------------------------------------------------


def test_periodic_class_is_the_calculate_periodic_list_not_the_name():
    """Rules out a name-prefix model: OBS_MOD_HEALTH / POWER_BURN are periodic without 'PERIODIC_' in their
    name, PERIODIC_DAMAGE_PERCENT is, and MOD_STAT is not (SpellAuraEffects.cpp:966-984)."""
    assert census.subtype_class(aura("OBS_MOD_HEALTH")) == "periodic"
    assert census.subtype_class(aura("POWER_BURN")) == "periodic"
    assert census.subtype_class(aura("PERIODIC_DUMMY")) == "periodic"
    assert census.subtype_class(aura("MOD_STAT")) == "other"
    assert census.subtype_class(aura("SCHOOL_ABSORB")) == "absorb-depletable"
    assert len(census.PERIODIC_AURAS) == 14


def test_effect_kind_and_fail_closed():
    assert census.effect_kind(APPLY) == "unit"
    assert census.effect_kind(effect("APPLY_AURA_ON_PET")) == "pet"
    assert census.effect_kind(effect("PERSISTENT_AREA_AURA")) == "persistent-area"
    assert census.effect_kind(effect("APPLY_AREA_AURA_RAID")) == "area:RAID"
    with pytest.raises(FailClosed):
        census.effect_kind(effect("SCHOOL_DAMAGE"))


def test_spell_record_fails_closed_without_provider_effect():
    data = FakeData(effects={1: [eff(0, effect("SCHOOL_DAMAGE"), 0)]})
    with pytest.raises(FailClosed):
        census.spell_record(data, 1)


def test_spell_record_axes_and_no_initial_stack_inference():
    """Capacity is recorded as capacity only: CumulativeAura 2 yields stack_capacity '>1' and stack_amount 2,
    never an initial-stack claim (Phalanx rule: capacity != initial stacks)."""
    data = FakeData(
        effects={5: [eff(0, APPLY, aura("PERIODIC_DAMAGE"), period=2000, target=6),
                     eff(1, effect("SCHOOL_DAMAGE"), 0)]},
        rows={("SpellMisc", 5): misc(3, a3=0x80, a13=0x00100000),
              ("SpellAuraOptions", 5): {"CumulativeAura": 2, "ProcCharges": 0, "ProcChance": 101,
                                        "ProcTypeMask_0": 0, "ProcTypeMask_1": 0, "SpellProcsPerMinuteID": 0,
                                        "ProcCategoryRecovery": 0},
              ("SpellInterrupts", 5): {"AuraInterruptFlags_0": 0, "AuraInterruptFlags_1": 0,
                                       "ChannelInterruptFlags_0": 0, "ChannelInterruptFlags_1": 0},
              ("SpellCategories", 5): {"DispelType": 1, "Mechanic": 0}},
        durations={3: {"Duration": 18000, "MaxDuration": 18000, "DurationPerResource": 0}})
    r = census.spell_record(data, 5, external=("aura-script",))
    assert r["axes"]["stack_capacity"] == ">1" and r["stack_amount"] == 2
    assert r["axes"]["attrs"] == ("DOT_STACKING_RULE", "PERIODIC_REFRESH_EXTENDS_DURATION")
    assert r["axes"]["effects"] == ["1", True]
    assert r["axes"]["interrupts"] == ()
    assert r["effects"] == [("unit", "periodic", "period", "other", "plain")]
    assert r["effects_exact"] == [("unit", "periodic", "period", "other", "plain", aura("PERIODIC_DAMAGE"))]
    assert r["provider_indices"] == [0]
    d = census.derived_predicates(r)
    # IsStackableOnOneSlotWithDifferentCasters is false because of DOT_STACKING_RULE (SpellInfo.cpp:1811)
    assert d["stackable_on_one_slot_different_casters"] is False


def test_passive_axis_uses_effective_passive():
    """R1-02: the flight-icon load-time correction (SpellMgr.cpp:5301) makes a spell passive although
    SPELL_ATTR0_PASSIVE is clear -- rules out the raw-attribute passive model."""
    m = misc(0)
    m["ActiveIconFileDataID"] = 135754
    data = FakeData(effects={7: [eff(0, APPLY, aura("MOD_STAT"))]}, rows={("SpellMisc", 7): m})
    r = census.spell_record(data, 7)
    assert r["axes"]["passive"] is True and r["axes"]["duration"] == "implicit-permanent"
    assert census.spell_record(FakeData(effects={8: [eff(0, APPLY, aura("MOD_STAT"))]}), 8)["axes"]["passive"] == "undetermined"


def test_cannot_be_saved_and_points_bits():
    """CU_AURA_CANNOT_BE_SAVED is server-derived (SpellMgr.cpp:3054-3067/3306-3308), not a DB2 bit."""
    e = eff(0, APPLY, aura("MOD_CHARM"))
    assert census.cannot_be_saved(1, [e], None)
    assert census.cannot_be_saved(2, [eff(0, APPLY, aura("MOD_STAT"))], {"AuraInterruptFlags_0": 0x00080000})
    assert not census.cannot_be_saved(3, [eff(0, APPLY, aura("MOD_STAT"))], {"AuraInterruptFlags_0": 0})
    assert census.effect_points({"EffectAttributes": 0x40 | 0x200}) == "suppress-points-stacking+aura-points-stack"
    assert census.effect_points({"EffectAttributes": 0}) == "plain"


def test_passive_not_duplicated_in_attrs():
    words = census.attributes(misc(0, a0=0x40))
    assert census.lifecycle_attrs(words) == ()


def test_core_raw_mapping():
    """Core SpellAttributeKind raw = word * 32 + bit index (spell_attribute.rs)."""
    by = census.ATTR_BY_LABEL
    assert by["PASSIVE"].core_raw == 6
    assert by["DOT_STACKING_RULE"].core_raw == 103
    assert by["EXTRA_INITIAL_PERIOD"].core_raw == 169        # Core: TickOnApplication
    assert by["HASTE_AFFECTS_DURATION"].core_raw == 273
    assert by["ROLLING_PERIODIC"].core_raw == 334
    assert by["PERIODIC_REFRESH_EXTENDS_DURATION"].core_raw == 436
    assert by["AURA_DOES_NOT_REFRESH"].core_raw == 489
    assert by["ASYNCHRONOUS_STACKING_BUFF"].core_raw == 490
    assert len({a.label for a in census.LIFECYCLE_ATTRS}) == len(census.LIFECYCLE_ATTRS)


# ---------------------------------------------------------------------------
# drift diff
# ---------------------------------------------------------------------------


def test_drift_fields_resolve_duration_rows_and_bits():
    """A changed SpellDuration *row* must surface on the spell (rules out comparing DurationIndex only)."""
    rows = {("SpellMisc", 9): misc(4)}
    old = FakeData(effects={9: [eff(0, APPLY, aura("MOD_STAT"))]}, rows=rows,
                   durations={4: {"Duration": 10000, "MaxDuration": 10000, "DurationPerResource": 0}})
    new = FakeData(effects={9: [eff(0, APPLY, aura("MOD_STAT"))]},
                   rows={("SpellMisc", 9): misc(4, a15=0x200)},
                   durations={4: {"Duration": 12000, "MaxDuration": 12000, "DurationPerResource": 0}})
    a, b = census.drift_fields(old, 9), census.drift_fields(new, 9)
    diff = {k: (x, y) for k, x, y in census.diff_fields(a, b)}
    assert diff["SpellDuration(base).Duration"] == (10000, 12000)
    assert diff["SpellMisc.Attributes_15"] == (0, 0x200)
    assert census.attr_bit_changes(a, b) == ["+AURA_DOES_NOT_REFRESH"]


# ---------------------------------------------------------------------------
# real data
# ---------------------------------------------------------------------------


def test_real_witnesses(al_ctx):
    """Agony (980): periodic DoT with DOT_STACKING_RULE + pandemic + spell haste; PERIODIC_DAMAGE and
    PERIODIC_DUMMY effects; finite-fixed 18 s."""
    r = census.spell_record(al_ctx.data, 980)
    assert r["axes"]["duration"] == "finite-fixed" and r["duration_ms"] == 18000
    assert {"DOT_STACKING_RULE", "PERIODIC_REFRESH_EXTENDS_DURATION", "SPELL_HASTE_AFFECTS_PERIODIC"} <= set(r["axes"]["attrs"])
    assert [e[-1] for e in r["effects_exact"]] == sorted([aura("PERIODIC_DAMAGE"), aura("PERIODIC_DUMMY")])


def test_external_axis_format():
    assert census.external_axis(None) == ()
    assert census.external_axis({"removal": "script", "application": "combined"}) == (
        "application:combined", "removal:script")


def test_external_axis_real_uses_track_h(al_ctx):
    """The external axis is Track H's surface_flags (single definition), not a J-local re-derivation."""
    flags = census.external_flags(al_ctx)
    r = census.spell_record(al_ctx.data, 980, external=census.external_axis(flags.get(980)))
    assert r["axes"]["external"] and all(":" in x for x in r["axes"]["external"])
    from aura_lifecycle import overlays
    assert set(flags.get(980, {})) == set(overlays.external_surfaces(al_ctx, 980))


def test_corpus_consistency():
    """The committed provider-census.json matches its own invariants (regenerate with the CLI)."""
    path = CORPORA / "provider-census.json"
    if not path.exists():
        pytest.skip("provider-census.json not generated")
    p = json.loads(path.read_text(encoding="utf-8"))
    pops = p["populations"]
    assert pops["all"]["provider_spells"] == 189140
    assert pops["all"]["provider_effects"] == 253257
    assert pops["player"]["provider_spells"] == 4218
    assert pops["player+class-skills"]["provider_spells"] >= pops["player"]["provider_spells"]
    assert len(p["spells"]) == len({s["spell"] for s in p["spells"]})
    for pop in ("all", "player", "player+class-skills", "controlled"):
        assert sum(p["marginals"][pop]["duration"].values()) == pops[pop]["provider_spells"]
    from aura_lifecycle import records
    records.validate_corpus(p)
    assert p["provenance"]["command"].startswith("aura_lifecycle.py census")
