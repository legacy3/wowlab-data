"""Track G2: the sole-effect selected-passive authorities.

Every assertion is a claim about Core at pin ``b1714eda2b4b9393853f94c6517e78cce2dfa21a``.
Witnesses are real ``TraitNodeEntry`` rows from the checked-in snapshot, so a snapshot
bump that changes one of them is meant to fail here rather than pass quietly.
"""

from __future__ import annotations

import json

import pytest

from gearing.tables import DEFAULT_TABLES
from selected_package import CORPORA
from selected_package import soleauthority as G2
from selected_package.provenance import Provenance, TraitSpellError

CORPUS = CORPORA / "sole-authorities.json"


@pytest.fixture(scope="module")
def provenance():
    if not DEFAULT_TABLES.is_dir():
        pytest.skip(f"no table snapshot at {DEFAULT_TABLES}")
    return Provenance()


def resolve(provenance, entry: int, rank: int):
    resolved = provenance.resolve(entry, rank)
    assert not isinstance(resolved, TraitSpellError), (entry, rank, resolved)
    return resolved


# ---------------------------------------------------------------------------
# the catalog itself
# ---------------------------------------------------------------------------

def test_catalog_covers_exactly_the_twelve_sole_effect_subtypes():
    """334/344/638 are package-only and 52/57/183/187/197 are unreachable from a
    SelectedTrait activation (critical_modifier.rs:1059-1070)."""
    assert sorted(G2.SOLE_AUTHORITIES) == [79, 87, 107, 108, 118, 193, 218, 219,
                                           290, 308, 319, 422]
    assert sorted(G2.PACKAGE_ONLY_SUBTYPES) == [334, 344, 638]
    assert sorted(G2.UNREACHABLE_SELECTED_SUBTYPES) == [52, 57, 183, 187, 197]
    overlap = set(G2.SOLE_AUTHORITIES) & (set(G2.PACKAGE_ONLY_SUBTYPES)
                                          | set(G2.UNREACHABLE_SELECTED_SUBTYPES))
    assert not overlap


def test_every_rule_cites_a_compiler_and_an_owner_shell():
    for subtype, rule in G2.SOLE_AUTHORITIES.items():
        assert rule.subtype == subtype
        assert rule.compiler.endswith(".rs")
        assert rule.core_lines
        assert rule.owner_shell in G2.OWNER_SHELLS
        assert rule.to_dict()["owner_shell_core"]


def test_only_melee_speed_pins_an_identity():
    named = {s: r.named_identity for s, r in G2.SOLE_AUTHORITIES.items() if r.named_identity}
    assert list(named) == [319]
    assert named[319]["entry"] == 116_954
    assert named[319]["definition"] == 121_966
    assert named[319]["spell"] == 390_354


# ---------------------------------------------------------------------------
# one admitted witness per authority
# ---------------------------------------------------------------------------

#: entry, rank -> the role Core's compiler assigns.  Each row is a real admitted package.
WITNESSES = {
    79: (80_258, 1, "PassiveDamageModifier/OutgoingHolder"),      # 343230:1, +3 Fire
    87: (80_173, 1, "PassiveDamageModifier/IncomingHolder"),      # 383092:1, -4 all-but-physical
    107: (103_860, 1, "SpellModifier/Flat"),      # Psychic Voice 196704:1, Core-named
    108: (80_297, 1, "SpellModifier/Percent"),    # 321752:1, two-rank Set 10/20
    118: (103_284, 1, "HealingReceived"),         # 377796:1, +4 misc 127
    193: (96_198, 1, "PassiveHasteAll"),          # 374265:1, two-rank Set
    218: (126_420, 1, "SpellModifier/Percent/Label"),        # 1265044:1, label 4934
    219: (100_604, 1, "SpellModifier/FlatFirstEffect/Label"),  # 321018:1, label 4071
    290: (100_635, 1, "CriticalChanceModifier/SourceSpellAndWeapon"),  # 378004:1
    308: (136_512, 1, "CriticalChanceModifier/TargetCasterSpellMagic"),  # Vital Clarity
    319: (116_954, 1, "MeleeAutoAttackSpeed"),                           # Furious Blows
    422: (96_180, 1, "PassiveAbsorbReceived"),    # 391571:1, two-rank Set 15/30
}


@pytest.mark.snapshot
@pytest.mark.parametrize("subtype", sorted(WITNESSES))
def test_one_admitted_witness_per_authority(provenance, subtype):
    entry, rank, expected = WITNESSES[subtype]
    resolved = resolve(provenance, entry, rank)
    assert len(resolved.effects) == 1
    assert resolved.effects[0].aura == subtype
    role, blockers = G2.admit_sole_effect(resolved)
    assert blockers == []
    assert role == expected


@pytest.mark.snapshot
def test_core_named_sole_effect_witnesses_are_admitted(provenance):
    """Every sole-effect witness Core names in its own ``aura_subtype.rs`` prose.

    Core catalogs Psychic Voice, Static Charge and Improved Conjuration under raw 107
    (aura_subtype.rs:439), Vital Clarity under raw 308 (:525) and Furious Blows under
    raw 319.  If the oracle refused one of them the transcription would be wrong.
    """
    named = [
        ("Psychic Voice", 103_860, 1, 196_704),
        ("Static Charge r1", 127_896, 1, 265_046),
        ("Static Charge r2", 127_896, 2, 265_046),
        ("Improved Conjuration r1", 134_192, 1, 1_244_025),
        ("Improved Conjuration r2", 134_192, 2, 1_244_025),
        ("Vital Clarity", 136_512, 1, 1_266_748),
        ("Furious Blows", 116_954, 1, 390_354),
    ]
    for label, entry, rank, provider in named:
        resolved = resolve(provenance, entry, rank)
        assert resolved.spell == provider, label
        role, blockers = G2.admit_sole_effect(resolved)
        assert blockers == [], (label, blockers)
        assert role, label


# ---------------------------------------------------------------------------
# Furious Blows: the one named sole-effect case
# ---------------------------------------------------------------------------

@pytest.mark.snapshot
def test_furious_blows_name_is_not_load_bearing(provenance):
    """Entry 112197 is semantically identical and refused only on the name.

    Same provider (390354 effect 1), same one-rank unshaped 5.0 amount, same
    family-four physical Passive owner.  Core refuses it because
    ``passive_melee_auto_attack_speed.rs:125-131`` pins entry 116954 and
    definition 121966.
    """
    twin = resolve(provenance, 112_197, 1)
    blessed = resolve(provenance, 116_954, 1)

    assert twin.spell == blessed.spell == 390_354
    assert twin.max_ranks == blessed.max_ranks == 1
    assert twin.point_operation(1) is blessed.point_operation(1) is None
    assert twin.selected_amount(1) == blessed.selected_amount(1) == 5.0
    assert twin.authored(1) == blessed.authored(1) == 5.0

    role, blockers = G2.admit_sole_effect(twin)
    assert role is None
    assert set(blockers) == {
        "identity: entry 112197 != Furious Blows 116954",
        "identity: definition 117202 != 121966",
    }

    # Everything the name does not pin holds for the twin.
    owners = G2._owners()
    effect = twin.effects[0]
    identity_free = G2.neutral_aura_blockers(
        effect, G2.AuraContract(G2.MOD_MELEE_AUTO_ATTACK_SPEED_PERCENT, 5.0))
    identity_free += G2.owner_blockers(owners, twin, effect, "physical-complete")
    identity_free += G2._unconditional_application_blockers(
        owners, owners.policy(twin.spell), label="physical-complete")
    assert identity_free == []
    assert owners.policy(twin.spell).family == 4


@pytest.mark.snapshot
def test_furious_blows_third_candidate_is_genuinely_different(provenance):
    """Entry 115165 is the only raw-319 sole package the name is not hiding."""
    other = resolve(provenance, 115_165, 1)
    assert other.spell == 403_509
    assert other.max_ranks == 2 and other.point_operation(1) == "Set"
    role, blockers = G2.admit_sole_effect(other)
    assert role is None
    assert any("SpellAuraOptions row present" in blocker for blocker in blockers)


# ---------------------------------------------------------------------------
# near-miss blockers
# ---------------------------------------------------------------------------

@pytest.mark.snapshot
def test_near_miss_raw79_two_rank_is_refused_by_the_one_rank_domain(provenance):
    """``passive_damage_modifier.rs:198`` demands ``max_ranks == 1`` for raw 79 while its
    raw-87 sibling five lines above accepts 1 or 2.  Entry 87693 is a two-rank Set raw-79
    source that the rank domain alone refuses."""
    resolved = resolve(provenance, 87_693, 1)
    assert resolved.effects[0].aura == 79 and resolved.max_ranks == 2
    role, blockers = G2.admit_sole_effect(resolved)
    assert role is None
    assert "amount: max_ranks 2 != 1" in blockers
    assert "amount: point operation 'Set' on a one-rank raw-79 source" in blockers


@pytest.mark.snapshot
def test_near_miss_raw87_multiply_is_refused_by_the_set_only_shaping(provenance):
    """``passive_damage_modifier.rs:170-173`` accepts only ``Set`` at two ranks, while
    ``supports_ranked_direct_source`` (passive_spell_modifier.rs:1055-1058) accepts
    ``Set | Multiply`` for the same rank shape."""
    resolved = resolve(provenance, 115_670, 1)
    assert resolved.effects[0].aura == 87 and resolved.point_operation(1) == "Multiply"
    role, blockers = G2.admit_sole_effect(resolved)
    assert role is None
    assert "amount: point operation 'Multiply' != 'Set' at max_ranks 2" in blockers


@pytest.mark.snapshot
def test_near_miss_owner_attribute_catalog(provenance):
    """Seven of the eight branches demand ``Passive`` plus catalog-``Ignored`` only;
    attribute 416 ``AllowClassAbilityProcs`` is catalog-``disabled`` and refuses this
    otherwise-clean raw-107 flat-cooldown source."""
    resolved = resolve(provenance, 9_928, 1)
    assert resolved.effects[0].aura == 107
    role, blockers = G2.admit_sole_effect(resolved)
    assert role is None
    assert blockers == ["owner[spell-modifier]: attribute 416 support 'disabled', "
                        "not Ignored"]


@pytest.mark.snapshot
def test_near_miss_raw193_owner_attributes(provenance):
    """The haste branch's own shell refuses 54 of 61 raw-193 sole packages, every one of
    them on an owner-attribute fact rather than an amount fact."""
    resolved = resolve(provenance, 125_070, 1)
    assert resolved.effects[0].aura == 193
    role, blockers = G2.admit_sole_effect(resolved)
    assert role is None
    assert blockers == ["owner[neutral-selected]: attribute 122 support 'disabled', "
                        "not Ignored"]


@pytest.mark.snapshot
def test_near_miss_raw290_is_refused_only_by_the_unsupported_attribute_list(provenance):
    """``critical_modifier.rs:936`` refuses only ``UNSUPPORTED_SOURCE_ATTRIBUTES``
    (raws 285/344/354), so raw 344 ``UpdatePassivesOnApplyRemove`` is the single reason
    51 of the 59 raw-290 sole packages fail."""
    resolved = resolve(provenance, 132_028, 1)
    assert resolved.effects[0].aura == 290
    role, blockers = G2.admit_sole_effect(resolved)
    assert role is None
    assert blockers == ["owner[critical-auxiliary]: attribute 344 is in "
                        "UNSUPPORTED_SOURCE_ATTRIBUTES"]


# ---------------------------------------------------------------------------
# fail-closed behaviour
# ---------------------------------------------------------------------------

@pytest.mark.snapshot
def test_multi_effect_package_is_never_admitted_here(provenance):
    """Heart of the Crusader is a four-effect package; the sole-effect gate refuses it
    with a structural reason rather than falling through to a role."""
    resolved = resolve(provenance, 115_483, 2)
    assert len(resolved.effects) == 4
    role, blockers = G2.admit_sole_effect(resolved)
    assert role is None
    assert blockers == ["structure: 4-effect package is not sole-effect"]


@pytest.mark.snapshot
def test_unclaimed_subtype_is_named_not_silently_dropped(provenance):
    """A sole-effect Dummy (raw 4) package has no Core authority at all.  It must come
    back as an explicit ``no-sole-authority`` reason, never as ``(None, [])``."""
    found = None
    for entry_id in sorted(provenance.traits.entries):
        entry = provenance.traits.entry(entry_id)
        for rank in range(1, (entry.max_ranks or 0) + 1):
            resolved = provenance.resolve(entry_id, rank)
            if isinstance(resolved, TraitSpellError) or len(resolved.effects) != 1:
                continue
            if resolved.effects[0].aura == 4:
                found = resolved
                break
        if found:
            break
    assert found is not None
    role, blockers = G2.admit_sole_effect(found)
    assert role is None
    assert len(blockers) == 1
    assert blockers[0].startswith("no-sole-authority: subtype 4")


@pytest.mark.snapshot
def test_package_only_subtypes_report_their_core_reason():
    for subtype, reason in G2.PACKAGE_ONLY_SUBTYPES.items():
        assert ".rs" in reason, subtype
    for subtype, reason in G2.UNREACHABLE_SELECTED_SUBTYPES.items():
        assert "critical_modifier.rs:1059-1070" in reason, subtype


def test_admit_never_returns_a_role_with_blockers(provenance):
    """The contract: ``role is not None`` implies ``blockers == []``."""
    checked = 0
    for entry_id in sorted(provenance.traits.entries)[:4000]:
        entry = provenance.traits.entry(entry_id)
        for rank in range(1, (entry.max_ranks or 0) + 1):
            resolved = provenance.resolve(entry_id, rank)
            if isinstance(resolved, TraitSpellError):
                continue
            role, blockers = G2.admit_sole_effect(resolved)
            assert (role is None) or (blockers == [])
            assert (blockers == []) or (role is None)
            checked += 1
    assert checked > 1000


def test_unknown_owner_shell_fails_closed(provenance):
    from selected_package import FailClosed
    resolved = resolve(provenance, 116_954, 1)
    with pytest.raises(FailClosed):
        G2.owner_blockers(G2._owners(), resolved, resolved.effects[0], "not-a-shell")


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------

@pytest.mark.snapshot
def test_corpus_matches_the_measured_census():
    if not CORPUS.exists():
        pytest.skip(f"missing corpus {CORPUS}")
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert payload["population"] == {
        "trait_node_entries": 17_139,
        "resolvable_entries": 8_279,
        "resolvable_entry_rank_variants": 8_846,
        "sole_effect_variants": 4_864,
        "multi_effect_variants": 3_982,
    }
    assert payload["totals"]["sole_effect_admitted"] == 184
    assert sum(payload["totals"]["admitted_by_role"].values()) == 184
    assert {int(k) for k in payload["authorities"]} == set(G2.SOLE_AUTHORITIES)
    assert payload["uniform_rule_delta"]["uniform_admitted"] == 181
    # 334/344/638 and the five unreachable critical subtypes never occur sole-effect.
    for block in ("claimed_but_package_only", "selected_activation_unreachable"):
        for row in payload[block].values():
            assert row["sole_effect_variants"] == 0
    assert payload["unclaimed_distinct_subtypes"] == 58
    assert payload["unclaimed_total_variants"] == 4_087
