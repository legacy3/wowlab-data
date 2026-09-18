"""Track E: selected-package topology, over-admission and the MAX_PACKAGE_EFFECTS ceiling.

Each test names the wrong model it rules out.  The absolute counts that depend on the
lead's classification table (``selected_package.effects``) are asserted against the
checked-in corpus only when the corpus was built from the current substrate, so a
substrate change shows up as a stale-corpus skip rather than a false failure; the
structural invariants and the named witnesses are asserted unconditionally.
"""

from __future__ import annotations

import json

import pytest

from gearing.tables import DEFAULT_TABLES
from selected_package import CORPORA
from selected_package.topology import (
    MAX_PACKAGE_EFFECTS,
    TopologyCensus,
    delivery_class,
    substrate_digest,
)

#: The five packages the pass uses as references (BRIEF.md).
REFERENCES = {
    "improved_vivify": 101510,
    "martial_expert": 117409,
    "heart_of_the_crusader": 115483,
    "phalanx": 137000,
    "ephemeral_bond": 136808,
}


@pytest.fixture(scope="module")
def census() -> TopologyCensus:
    if not DEFAULT_TABLES.is_dir():
        pytest.skip(f"no table snapshot at {DEFAULT_TABLES}")
    built = TopologyCensus()
    built.packages  # noqa: B018 -- warm the cache once for the whole module
    return built


@pytest.fixture(scope="module")
def by_key(census: TopologyCensus) -> dict[tuple[int, int], object]:
    return {(p.entry_id, p.effective_rank): p for p in census.packages}


def _corpus(name: str) -> dict:
    path = CORPORA / name
    if not path.exists():
        pytest.skip(f"missing corpus {name}; run the Track E generator")
    payload = json.loads(path.read_text(encoding="utf-8"))
    recorded = payload["provenance"]["substrate"]
    live = substrate_digest()
    stale = {k: (recorded.get(k), live[k]) for k in live if recorded.get(k) != live[k]}
    if stale:
        pytest.skip(f"{name} was built from a different substrate: {sorted(stale)}")
    return payload


# ---------------------------------------------------------------------------
# population invariants
# ---------------------------------------------------------------------------

def test_population_matches_cores_source_rules(census):
    """Rules out 'the topology census sees a different population than Provenance'."""
    packages = census.packages
    assert len(packages) == 8846
    assert len({p.entry_id for p in packages}) == 8279
    assert sum(p.total_effects for p in packages) == 16639


def test_every_package_is_partitioned_exactly_once(census):
    """Rules out 'over-admission is a fuzzy category': the three classes partition the population."""
    packages = census.packages
    full = sum(1 for p in packages if p.fully_recognized)
    over = sum(1 for p in packages if p.over_admitted)
    none = sum(1 for p in packages if p.recognized == 0)
    assert full + over + none == len(packages)
    for package in packages:
        assert package.recognized + package.unrecognized == package.total_effects
        assert package.over_admitted == (0 < package.recognized < package.total_effects)


def test_effect_indices_are_dense(census):
    """Rules out 'SpellEffectRef must cope with sparse indices' -- Provenance already refuses them."""
    assert all(p.effect_index_density for p in census.packages)


def test_delivery_class_reads_the_source_not_the_role():
    """Rules out 'a caster-referenced selector always means the caster'."""
    assert delivery_class(1, 0, 6) == "self"
    assert delivery_class(1, 1, 6) == "self"
    assert delivery_class(0, 0, 6) == "unspecified"
    assert delivery_class(6, 0, 6) == "other_unit"          # TARGET_UNIT_TARGET_ENEMY
    assert delivery_class(5, 0, 6) == "other_unit"          # TARGET_UNIT_PET
    assert delivery_class(120, 0, 6) == "other_unit"        # TARGET_UNIT_CASTER_AND_SUMMONS
    assert delivery_class(22, 0, 6) == "location"           # TARGET_SRC_CASTER
    # the effect kind alone can move the recipient (APPLY_AURA_ON_PET)
    assert delivery_class(1, 0, 174) == "other_unit"


# ---------------------------------------------------------------------------
# the five reference packages
# ---------------------------------------------------------------------------

#: (entry, rank) -> complete source effect count, for the five reference packages.
REFERENCE_ARITY = {
    (REFERENCES["improved_vivify"], 1): 2, (REFERENCES["improved_vivify"], 2): 2,
    (REFERENCES["martial_expert"], 1): 2,
    (REFERENCES["heart_of_the_crusader"], 1): 4, (REFERENCES["heart_of_the_crusader"], 2): 4,
    (REFERENCES["phalanx"], 1): 2, (REFERENCES["phalanx"], 2): 2,
    (REFERENCES["ephemeral_bond"], 1): 3,
}


def test_reference_packages_are_complete_and_self_delivered(by_key):
    """Rules out 'the reference packages need a named branch to be complete'."""
    assert {entry for entry, _ in REFERENCE_ARITY} == set(REFERENCES.values())
    for key, effect_count in REFERENCE_ARITY.items():
        package = by_key[key]
        assert package.total_effects == effect_count, key
        assert package.fully_recognized, key
        assert not package.over_admitted, key
        assert not package.exceeds_effect_cap, key
        assert package.deliveries == ("self",), key
        assert not package.splits_by_actor and not package.splits_by_effect_kind, key


def test_reference_package_role_shapes(by_key):
    """Pins each reference package's role sequence, so a substrate drift is visible here first."""
    assert by_key[(101510, 2)].role_sequence == ("SpellModifier", "SpellModifier")
    assert by_key[(117409, 1)].role_sequence == ("SpellModifier", "CriticalBlockAmount")
    assert by_key[(115483, 2)].role_sequence == (
        "AutoAttackDamage", "AutoAttackCriticalChance", "SpellModifier", "SpellModifier")
    assert by_key[(137000, 2)].role_sequence == ("SpellModifier", "SpellModifier")
    assert by_key[(136808, 1)].role_sequence == (
        "HealingReceived", "SpellModifier", "SpellModifier")
    # Phalanx and Improved Vivify are both 2x SpellModifier and differ only in selection.
    assert by_key[(137000, 2)].mixed_selectors and by_key[(101510, 2)].mixed_selectors
    # Ephemeral Bond, Martial Expert and Heart all mix SpellModifier with another authority.
    assert by_key[(136808, 1)].mixed_authorities
    assert by_key[(117409, 1)].mixed_authorities
    assert by_key[(115483, 2)].mixed_authorities


# ---------------------------------------------------------------------------
# mixed shaping (Improved Vivify is the witness Core hardcodes)
# ---------------------------------------------------------------------------

def test_improved_vivify_is_the_mixed_shaping_witness(by_key):
    """Rules out 'rank shaping covers the whole package'.

    Core gates this shape on entry 101510 + provider 231602 + an amount predicate
    (selected_trait_package.rs:498-504); the source fact is simply that the definition
    has fewer TraitDefinitionEffectPoints rows than the provider has effects.
    """
    for rank in (1, 2):
        package = by_key[(101510, rank)]
        assert package.provider == 231602
        assert package.mixed_shaping
        assert package.shaped_indices == (1,)
        assert package.unshaped_indices == (2,)
        assert package.point_operations == ("Set",)


def test_mixed_shaping_is_not_rare(census):
    """Rules out 'Improved Vivify is a one-off' -- it is the generic consequence of the 4-point cap."""
    mixed = [p for p in census.packages if p.mixed_shaping]
    assert len(mixed) > 100
    assert any(p.entry_id == 101510 for p in mixed)
    # No package can be shaped beyond MAX_RANK_SHAPED_EFFECTS (selected_spell.rs:7).
    assert max(len(p.shaped_indices) for p in census.packages) == 4


# ---------------------------------------------------------------------------
# over-admission witnesses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "key,sibling_index,aura_subtype,support,block_group,delivery",
    [
        # presentation sibling: a model-scale aura next to five combat roles.
        ((112232, 1), 5, 61, "ignored", "aura_ignored", "self"),
        # actor sibling: a spell modifier carried by SPELL_EFFECT_APPLY_AURA_ON_PET.
        ((117442, 1), 6, 218, "n/a", "non_aura_effect_kind", "other_unit"),
        # actor sibling via the implicit target alone: a role matched, the shell refused it.
        ((100523, 1), 2, 87, "implemented", "aura_implemented_nonneutral_shell", "other_unit"),
        # same-authority sibling: another AddPercentModifier, unowned SpellModOp.
        ((9968, 1), 2, 108, "implemented", "aura_implemented_no_selected_semantic", "self"),
        # disabled sibling.
        ((80267, 1), 3, 226, "disabled", "aura_disabled", "self"),
        # non-aura sibling.
        ((96260, 1), 2, 0, "n/a", "non_aura_effect_kind", "unspecified"),
    ],
)
def test_over_admission_witnesses(by_key, key, sibling_index, aura_subtype, support,
                                  block_group, delivery):
    """Rules out 'the effects I recognised are the package'.

    Each of these packages has at least one effect that enters a role and a named sibling
    that does not; a recognised-effects-only filter admits the provider and silently drops
    the sibling.
    """
    package = by_key[key]
    assert package.over_admitted, key
    assert package.recognized > 0 and package.unrecognized > 0, key
    sibling = next(e for e in package.effects if e.index == sibling_index)
    assert not sibling.recognized, key
    assert sibling.aura_subtype == aura_subtype, key
    assert sibling.aura_support == support, key
    assert sibling.block_group == block_group, key
    assert sibling.delivery == delivery, key
    assert sibling.missing_semantic, key


def test_actor_split_is_real(by_key):
    """Rules out 'every effect of a trait provider lands on the selecting player'.

    Xalan's Cruelty carries three player-side spell modifiers and two more spell modifiers
    on SPELL_EFFECT_APPLY_AURA_ON_PET carriers -- the same authority, a different actor.
    """
    package = by_key[(117442, 1)]
    pets = [e for e in package.effects if e.kind == 174]
    assert len(pets) >= 2
    assert all(e.delivery == "other_unit" for e in pets)
    assert all(e.aura_subtype == 218 for e in pets)
    assert package.splits_by_actor
    assert any(e.recognized and e.delivery == "self" for e in package.effects)


def test_no_recognized_effect_is_delivered_elsewhere(census):
    """Rules out 'the actor axis is closed by the package rule'.

    It is closed by the exact-effect neutrality shell instead: every effect that reaches a
    role is TARGET_UNIT_CASTER-delivered, so relaxing the shell reopens the actor split.
    """
    assert all(e.delivery == "self"
               for p in census.packages for e in p.effects if e.recognized)
    assert not any(p.fully_recognized and p.splits_by_actor for p in census.packages)


def test_over_admission_corpus_agrees_with_the_census(census):
    """Rules out 'the corpus and the oracle have drifted'."""
    payload = _corpus("over-admission.json")
    live = census.over_admitted()
    assert payload["count"] == len(live)
    assert payload["count"] > 1000
    assert payload["groups"] == census.over_admission_groups()
    assert payload["partition"] == census.over_admission_partition()
    assert sum(payload["partition"].values()) == payload["count"]
    keys = {(row["entry_id"], row["rank"]) for row in payload["packages"]}
    assert keys == {(p.entry_id, p.effective_rank) for p in live}
    for row in payload["packages"]:
        assert row["recognized_roles"] and row["unrecognized_siblings"]
        for sibling in row["unrecognized_siblings"]:
            assert sibling["missing_semantic"]
            assert sibling["block_group"] in payload["groups"]


# ---------------------------------------------------------------------------
# the MAX_PACKAGE_EFFECTS ceiling
# ---------------------------------------------------------------------------

def test_packages_exceed_the_four_effect_cap(census):
    """Rules out 'four effects is a semantic boundary'.

    438 variants carry more than four source effects and 26 of them classify completely;
    the largest clean shape is Barbaric Training's eight.
    """
    over_cap = [p for p in census.packages if p.exceeds_effect_cap]
    assert len(over_cap) > 400
    clean = [p for p in over_cap if p.fully_recognized]
    assert len(clean) >= 20
    assert max(p.total_effects for p in census.packages) == 18
    assert max(p.total_effects for p in clean) == 8
    assert all(p.total_effects > MAX_PACKAGE_EFFECTS for p in over_cap)


def test_clean_over_cap_packages_are_homogeneous_spell_modifiers(census):
    """Rules out 'a 5+ effect package needs the mixed-authority machinery'.

    Every clean >4 package is SpellModifier-only, so it compiles to one activation per
    exact effect and binds through passive_spell_modifier_package_effects -- neither of
    which is arity-bounded.  The cap is storage, not semantics.
    """
    clean = [p for p in census.packages if p.exceeds_effect_cap and p.fully_recognized]
    assert clean
    for package in clean:
        assert package.role_names == ("SpellModifier",), package.entry_id
        assert not package.mixed_authorities
        assert package.deliveries == ("self",)
        # rank shaping can never cover a >4 package: MAX_RANK_SHAPED_EFFECTS is 4.
        assert len(package.shaped_indices) < package.total_effects


def test_barbaric_training_is_the_eight_effect_witness(by_key):
    """The 8-effect Direct+CriticalBonus shape, at both ranks, with repeated role keys."""
    for rank in (1, 2):
        package = by_key[(112236, rank)]
        assert package.provider == 383082
        assert package.total_effects == 8
        assert package.fully_recognized and package.exceeds_effect_cap
        assert package.max_ranks == 2
        assert package.mixed_shaping and package.shaped_indices == (1, 2)
        counts = package.repeated_roles
        assert counts and max(counts.values()) == 4


def test_topology_corpus_records_the_cap_analysis(census):
    """Rules out 'the cap finding lives only in prose'."""
    payload = _corpus("topology.json")
    cap = payload["effect_cap"]
    assert cap["cap"] == MAX_PACKAGE_EFFECTS
    assert cap["exceeding"] == sum(1 for p in census.packages if p.exceeds_effect_cap)
    assert cap["max_shaped_effects_observed"] == 4
    assert cap["exceeding_with_every_effect_shaped"] == 0
    assert len(cap["exceeding_fully_recognized"]) >= 20
    assert payload["summary"]["variants"] == len(census.packages)
    assert payload["summary"]["effect_index_density_violations"] == 0


# ---------------------------------------------------------------------------
# order
# ---------------------------------------------------------------------------

def test_role_order_is_not_a_function_of_the_role_set(census):
    """Rules out 'roles can be slotted positionally, as Core's array patterns do'.

    Core's own branches disagree (heart slots AutoAttackDamage first, the single-rank
    auto-attack package slots the SpellModifier first), and the data carries the same
    role multiset in two different source orders.
    """
    variance = census.shape_order_variance()
    assert variance
    for orders in variance.values():
        assert len(orders) >= 2
        assert len({tuple(order) for order in orders}) == len(orders)


def test_healing_received_is_not_always_first(by_key):
    """Attuned to the Dream is Ephemeral Bond's role multiset in the opposite order.

    Core's ephemeral-bond branch destructures `[healing, first, second]`
    (selected_trait_package.rs:351); this provider puts HealingReceived last.
    """
    package = by_key[(87699, 1)]
    assert package.provider == 376930
    assert package.role_sequence == ("SpellModifier", "SpellModifier", "HealingReceived")
    assert package.fully_recognized
    assert package.mixed_authorities
