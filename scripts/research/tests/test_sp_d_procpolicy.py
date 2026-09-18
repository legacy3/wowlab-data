"""Track D: AuraOptions -> effective proc policy (``selected_package.procpolicy``).

Real-data tests against the checked-in snapshot plus the pinned TrinityCore world
overlay.  Every assertion here stands for a claim in
``bag/selected-package-pass/sections/D-proc-policy.md``; if a data refresh moves
one, re-derive it and update both.
"""

from __future__ import annotations

import pytest

from procs.source import Source
from selected_package import coreref
from selected_package.procpolicy import (
    CORE_LITERALS,
    CROWD_CONTROL_AMOUNT_AURAS,
    FAMILIES,
    FAMILY_ADMITS,
    PROC_FLAG_NAMES,
    ProcPolicy,
    proc_flag_names,
    shape_key,
)

pytestmark = pytest.mark.snapshot

HEART = 406154
PHALANX = 1269312


@pytest.fixture(scope="module")
def policy(tables):
    return ProcPolicy(source=Source(str(tables.root)))


# ---------------------------------------------------------------------------
# witness 1 -- Heart of the Crusader 406154
# ---------------------------------------------------------------------------

def test_heart_row_matches_cores_hardcoded_literal(policy):
    """selected_trait_package.rs:557-566, field for field."""
    row = policy.classify(HEART).aura_options
    assert row is not None
    assert (row.cumulative_aura, row.proc_category_recovery_ms, row.proc_chance,
            row.proc_charges, row.procs_per_minute_id, row.proc_type_mask) == (0, 0, 101, 0, 0, 4)
    assert row.matches_core_literal(CORE_LITERALS[HEART])


def test_heart_is_inert_and_lands_in_the_no_trigger_aura_family(policy):
    v = policy.classify(HEART)
    assert v.family == "inert-no-trigger-aura"
    assert v.blockers == ()
    assert v.inert and v.runtime_inert
    assert v.is_passive is True


def test_heart_generates_no_entry_because_no_subtype_is_proc_triggering(policy):
    """Core's own stated reason (aura_subtype.rs:534), verified against SpellMgr.cpp:1807."""
    v = policy.classify(HEART)
    assert v.has_effective_entry is False
    assert v.generation_reason == "no-trigger-aura"
    assert v.aura_subtypes == (108, 334, 344)
    assert v.proc_triggering_subtypes == ()      # none of 108 / 334 / 344 is in isTriggerAura[]
    assert v.has_spell_proc_overlay is False     # "and no explicit proc overlay exists"
    assert v.script_names == ()


def test_heart_bails_at_the_third_gate_not_the_second(policy):
    """ProcFlags is NONZERO, so the bail is SpellMgr.cpp:1807, not :1765."""
    v = policy.classify(HEART)
    assert v.aura_options.proc_type_mask != 0
    assert v.generation_reason != "no-proc-flags"
    assert "isTriggerAura" in v.effective_reason


def test_proc_type_mask_4_is_deal_melee_swing(policy):
    """SpellMgr.h:97 -- PROC_FLAG_DEAL_MELEE_SWING, '02 Done melee auto attack'."""
    assert PROC_FLAG_NAMES[2] == "PROC_FLAG_DEAL_MELEE_SWING"
    assert proc_flag_names(4) == ["PROC_FLAG_DEAL_MELEE_SWING"]
    assert policy.classify(HEART).aura_options.to_dict()["proc_flag_names"] == [
        "PROC_FLAG_DEAL_MELEE_SWING"]


def test_heart_carries_no_crowd_control_subtype_so_no_mask_bit_matters(policy):
    """The one consumer that reads ProcFlags without an entry (SpellAuraEffects.cpp:786-798)."""
    v = policy.classify(HEART)
    assert not (CROWD_CONTROL_AMOUNT_AURAS & set(v.aura_subtypes))


def test_the_crowd_control_guard_is_empty_on_this_snapshot_but_still_armed(policy):
    """D-CE-12: proved empty, retained.  A hit here would be a real admission bug."""
    offenders = [
        s for s, v in _all(policy).items()
        if v.aura_options and v.aura_options.proc_type_mask and not v.has_effective_entry
        and (CROWD_CONTROL_AMOUNT_AURAS & set(v.aura_subtypes))
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# witness 2 -- Phalanx 1269312, CumulativeAura = 2
# ---------------------------------------------------------------------------

def test_phalanx_row_matches_cores_hardcoded_literal(policy):
    """selected_trait_package.rs:672-681, field for field."""
    row = policy.classify(PHALANX).aura_options
    assert row is not None
    assert (row.cumulative_aura, row.proc_category_recovery_ms, row.proc_chance,
            row.proc_charges, row.procs_per_minute_id, row.proc_type_mask) == (2, 0, 101, 0, 0, 0)
    assert row.matches_core_literal(CORE_LITERALS[PHALANX])


def test_phalanx_is_inert_stack_capacity_only(policy):
    v = policy.classify(PHALANX)
    assert v.family == "stack-capacity-only"
    assert v.blockers == ()
    assert v.is_passive is True


def test_phalanx_bails_at_the_second_gate_not_the_third(policy):
    """ProcTypeMask == 0, so SpellMgr.cpp:1765 fires and no other field is read at all.

    Core's single ``Exact`` mechanism covers two structurally different bails.
    """
    v = policy.classify(PHALANX)
    assert v.aura_options.proc_type_mask == 0
    assert v.generation_reason == "no-proc-flags"
    assert policy.classify(HEART).generation_reason == "no-trigger-aura"


def test_cumulative_aura_is_reported_as_a_cap_not_as_initial_stacks(policy):
    """SpellInfo.cpp:1390 -> Aura::CalcMaxStackAmount; never on the construction path."""
    signals = policy.classify(PHALANX).authored_proc_signals
    assert any(s.startswith("CumulativeAura 2 -- a cap, not initial stacks") for s in signals)


def test_cumulative_aura_above_one_is_only_inert_because_the_provider_is_passive(policy):
    """The is_passive conjunct is load-bearing: 11 non-passive providers break it (D-CE-07)."""
    broken = {
        s: v for s, v in _all(policy).items()
        if v.aura_options and v.aura_options.cumulative_aura > 1
        and not v.has_effective_entry and not v.is_passive
    }
    assert broken, "expected non-passive high-cap providers in the population"
    for verdict in broken.values():
        assert any("on a non-passive" in b for b in verdict.blockers)
    assert 980 in broken            # Agony, cap 8
    assert 323764 in broken         # Convoke the Spirits, cap 99


# ---------------------------------------------------------------------------
# the family set
# ---------------------------------------------------------------------------

def test_every_family_has_a_definition_and_an_admission_policy():
    assert set(FAMILIES) == set(FAMILY_ADMITS)
    assert len(FAMILIES) == 11
    for name, definition in FAMILIES.items():
        assert len(definition) > 80, name
        admits, evidence = FAMILY_ADMITS[name]
        assert isinstance(admits, bool)
        assert evidence in ("source", "core", "trinity", "probe", "inferred", "unknown")


def test_only_the_four_inert_families_admit():
    admitting = {n for n, (ok, _) in FAMILY_ADMITS.items() if ok}
    assert admitting == {"no-auraoptions", "inert-no-proc-flags",
                         "inert-no-trigger-aura", "stack-capacity-only"}


@pytest.mark.parametrize("spell,family", [
    (HEART, "inert-no-trigger-aura"),
    (PHALANX, "stack-capacity-only"),
    (387095, "effective-proc"),          # spell_proc row, no aura-options row at all
    (441346, "rppm"),                    # Thousand Cuts, SpellProcsPerMinuteID 195
    (382517, "proc-icd"),                # Deeper Daggers, 500 ms
    (73685, "charges"),                  # Unleash Life, ProcCharges 1
    (14190, "guarded-proc"),             # Seal Fate, infinite-loop guard
    (2637, "inert-no-proc-flags"),       # Hibernate
])
def test_family_assignment_witnesses(policy, spell, family):
    assert policy.classify(spell).family == family


def test_a_family_member_admits_exactly_when_its_blockers_are_empty(policy):
    for spell, verdict in _all(policy).items():
        admits, _ = FAMILY_ADMITS[verdict.family]
        if not admits:
            assert verdict.blockers, f"{spell} in non-admitting {verdict.family} has no blocker"


# ---------------------------------------------------------------------------
# counterexamples
# ---------------------------------------------------------------------------

def test_ce01_absent_aura_options_does_not_prove_absence_of_a_proc(policy):
    """D-CE-01: breaks Core's SelectedAuraOptionsContract::Absent arm outright."""
    v = policy.classify(387095)                  # Pyrogenics
    assert v.aura_options is None                # Core's `Absent` would accept this provider
    assert v.has_effective_entry is True
    assert v.entry_origin == "spell_proc"
    assert v.proc_triggering_subtypes == ()      # and not because of a triggering aura, either
    assert v.script_names == ("spell_warl_pyrogenics",)
    assert not v.inert and not v.runtime_inert

    population = [s for s, w in _all(policy).items()
                  if w.aura_options is None and w.has_effective_entry]
    assert len(population) >= 10


def test_ce02_rppm_identity_with_no_entry_shares_hearts_exact_proc_mask(policy):
    """D-CE-02: Thousand Cuts 441346 -- same ProcTypeMask 4, plus an RPPM clock."""
    v = policy.classify(441346)
    assert v.has_effective_entry is False
    assert v.aura_options.proc_type_mask == policy.classify(HEART).aura_options.proc_type_mask == 4
    assert v.aura_options.procs_per_minute_id == 195
    assert v.procs_per_minute_resolves is True
    assert v.procs_per_minute_base_rate == pytest.approx(4.5)
    assert v.runtime_inert is True               # Trinity really never rolls it ...
    assert not v.inert                           # ... but the authored clock rejects it
    assert [b for b in v.blockers if b.startswith("[authored]")]


def test_ce03_authored_internal_cooldown_with_no_entry(policy):
    """D-CE-03: ProcCategoryRecovery reaches nothing without an entry, yet is a clock."""
    for spell, icd in ((382517, 500), (383314, 500), (377098, 700)):
        v = policy.classify(spell)
        assert v.family == "proc-icd"
        assert v.aura_options.proc_category_recovery_ms == icd
        assert v.has_effective_entry is False
        assert v.runtime_inert is True and not v.inert


def test_ce04_proc_charges_create_mutable_state_with_no_entry(policy):
    """D-CE-04: Aura::CalcMaxCharges (SpellAuras.cpp:1004-1015) reads ProcCharges regardless."""
    v = policy.classify(73685)                   # Unleash Life
    assert v.family == "charges"
    assert v.has_effective_entry is False
    assert v.aura_options.proc_charges == 1
    assert v.runtime_inert is False              # this one breaks on Trinity's own terms
    assert any("CalcMaxCharges" in b for b in v.blockers)
    assert any("m_procCharges" in m for m in v.mutable_state)


def test_ce05_infinite_loop_guard_is_not_inertness(policy):
    """D-CE-05: no entry, but the provider has proc flags AND aura 42 PROC_TRIGGER_SPELL."""
    v = policy.classify(14190)                   # Seal Fate
    assert v.family == "guarded-proc"
    assert v.has_effective_entry is False
    assert v.generation_reason == "infinite-loop-guard"
    assert 42 in v.proc_triggering_subtypes
    assert not v.runtime_inert


def test_ce08_stack_capacity_and_charges_are_not_independent(policy):
    """D-CE-08: PROC_ATTR_USE_STACKS_FOR_CHARGES makes a proc consume a stack."""
    coupled = [s for s, v in _all(policy).items()
               if v.aura_options and v.aura_options.cumulative_aura > 1
               and v.aura_options.proc_charges]
    assert set(coupled) >= {73685, 108839, 191634}
    for spell in coupled:
        assert any("USE_STACKS_FOR_CHARGES" in b for b in policy.classify(spell).blockers)


def test_ce13_no_overlay_row_in_the_population_fails_to_produce_an_entry(policy):
    """D-CE-13: proved empty -- the overlay adds no behaviour beyond D-CE-01."""
    assert [s for s, v in _all(policy).items()
            if v.has_spell_proc_overlay and not v.has_effective_entry] == []


# ---------------------------------------------------------------------------
# fail-closed
# ---------------------------------------------------------------------------

def test_unknown_aura_subtype_fails_closed_on_real_data(policy):
    """A provider carrying a subtype Core's AuraSubtypeKind catalog does not name."""
    offenders = {s: v for s, v in _all(policy).items() if v.uncatalogued_subtypes}
    assert offenders, "expected build skew between Core's catalog and this snapshot"
    for verdict in offenders.values():
        assert verdict.family == "unknown"
        assert not verdict.inert
        assert any(b.startswith("[unknown] provider carries aura subtype")
                   for b in verdict.blockers)


def test_unknown_aura_subtype_fails_closed_even_on_an_otherwise_perfect_row(policy, monkeypatch):
    """Take Heart of the Crusader -- inert with zero blockers -- and un-catalogue raw 334."""
    assert policy.classify(HEART).inert

    real = coreref.aura_support
    monkeypatch.setattr(coreref, "aura_support",
                        lambda raw: "unknown" if raw == 334 else real(raw))
    fresh = ProcPolicy(source=policy.source)
    v = fresh.classify(HEART)
    assert v.uncatalogued_subtypes == (334,)
    assert v.family == "unknown"
    assert not v.inert


def test_no_provider_is_ever_unknown_without_a_blocker(policy):
    """The defensive arm: 'unknown' can never be a silent pass."""
    for spell, verdict in _all(policy).items():
        if verdict.family == "unknown":
            assert verdict.blockers, spell


def test_missing_spellinfo_fails_closed(policy):
    v = policy.classify(999_999_999)
    assert v.family == "unknown"
    assert v.blockers == ("[unknown] provider has no base-difficulty SpellInfo",)
    assert not v.inert


# ---------------------------------------------------------------------------
# shape helpers
# ---------------------------------------------------------------------------

def test_shape_key_round_trips_the_six_fields(policy):
    assert shape_key(None) == "absent"
    assert shape_key(policy.classify(HEART).aura_options) == (
        "cum=0;rec=0;chance=101;charges=0;ppm=0;mask=0x4")
    assert shape_key(policy.classify(PHALANX).aura_options) == (
        "cum=2;rec=0;chance=101;charges=0;ppm=0;mask=0x0")


def test_the_two_witnesses_have_different_shapes_but_one_core_contract(policy):
    """The literal pins six fields; the two rows agree on only four of them."""
    heart = policy.classify(HEART).aura_options
    phalanx = policy.classify(PHALANX).aura_options
    differing = [f for f in ("cumulative_aura", "proc_category_recovery_ms", "proc_chance",
                             "proc_charges", "procs_per_minute_id", "proc_type_mask")
                 if getattr(heart, f) != getattr(phalanx, f)]
    assert differing == ["cumulative_aura", "proc_type_mask"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_POPULATION: dict[int, object] = {}


def _all(policy):
    """Verdicts for every provider spell of the resolvable selected entries, cached."""
    if not _POPULATION:
        from selected_package.provenance import Provenance, TraitSpellError
        provenance = Provenance(source=policy.source)
        spells = set()
        for entry_id, entry in provenance.traits.entries.items():
            for rank in range(1, max(entry.max_ranks, 1) + 1):
                resolved = provenance.resolve(entry_id, rank)
                if not isinstance(resolved, TraitSpellError):
                    spells.add(resolved.spell)
                    break
        _POPULATION.update({s: policy.classify(s) for s in sorted(spells)})
    return _POPULATION
