"""Lead-owned substrate: provenance, per-effect classification, Core baseline, rules.

These tests pin the facts the report's headline numbers rest on.  They are deliberately
written against REAL source rows rather than fixtures: the whole point of the pass is that
the rule is a function of the shipped data, and a synthetic fixture cannot catch a drift in
the data or in Core's catalogs.

Run: ``uv run --no-project --with pytest python -m pytest tests/test_sp_a_oracle.py``
(system ``python3`` has no pytest on this machine).
"""

from __future__ import annotations

import json

import pytest

from selected_package import CORPORA, coreref
from selected_package.corebaseline import CoreBaseline
from selected_package.effects import classify, classify_package
from selected_package.provenance import Provenance, TraitSpellError
from selected_package.owner import Owners
from selected_package.rule import RULES, Context, core_admitted_shapes, score

#: The packages the mission names, by (entry, rank).
REFERENCE = {
    "improved_vivify": (101_510, 2),
    "martial_expert": (117_409, 1),
    "heart_of_the_crusader": (115_483, 2),
    "phalanx": (137_000, 2),
    "ephemeral_bond": (136_808, 1),
}


@pytest.fixture(scope="module")
def ctx() -> Context:
    return Context()


# -- provenance ------------------------------------------------------------

def test_population_is_the_pinned_size(ctx: Context) -> None:
    """17,139 entries -> 8,279 resolvable -> 8,846 entry x rank variants."""
    entries, variants = set(), 0
    for entry, rank in ctx.variants():
        if isinstance(ctx.provenance.resolve(entry, rank), TraitSpellError):
            continue
        entries.add(entry)
        variants += 1
    assert len(ctx.provenance.traits.entries) == 17_139
    assert len(entries) == 8_279
    assert variants == 8_846


@pytest.mark.parametrize(
    ("entry", "rank", "spell", "definition", "amounts"),
    [
        # Improved Vivify: effect 1 Set-shaped 40 -> 20/40, effect 2 UNSHAPED at 40.
        (101_510, 1, 231_602, 106_512, [20.0, 40.0]),
        (101_510, 2, 231_602, 106_512, [40.0, 40.0]),
        # Heart of the Crusader: all four effects Set 10 -> 10/20.
        (115_483, 1, 406_154, 120_495, [10.0, 10.0, 10.0, 10.0]),
        (115_483, 2, 406_154, 120_495, [20.0, 20.0, 20.0, 20.0]),
        # Phalanx: both effects Multiply 10 -> 10/20.
        (137_000, 1, 1_269_312, 141_763, [10.0, 10.0]),
        (137_000, 2, 1_269_312, 141_763, [20.0, 20.0]),
        # Martial Expert: unshaped 10 and 20 -- the bitwise pair Core pins.
        (117_409, 1, 429_638, 122_421, [10.0, 20.0]),
    ],
)
def test_reference_amounts_are_exact(ctx, entry, rank, spell, definition, amounts) -> None:
    resolved = ctx.provenance.resolve(entry, rank)
    assert not isinstance(resolved, TraitSpellError)
    assert (resolved.spell, resolved.definition_id) == (spell, definition)
    assert [resolved.selected_amount(e.index) for e in resolved.effects] == amounts


def test_improved_vivify_has_mixed_shaping(ctx: Context) -> None:
    """The witness that falsifies the `shaping_uniformity` dimension."""
    resolved = ctx.provenance.resolve(101_510, 1)
    assert resolved.point_operation(1) == "Set"
    assert resolved.point_operation(2) is None
    # Different values at the same rank -- siblings need not agree.
    assert resolved.selected_amount(1) != resolved.selected_amount(2)


def test_detached_heart_twin_resolves_but_differs(ctx: Context) -> None:
    """Same provider, a different definition, and a different rank domain."""
    twin = ctx.provenance.resolve(115_441, 1)
    assert not isinstance(twin, TraitSpellError)
    assert twin.spell == 406_154
    assert twin.definition_id == 120_453
    assert twin.max_ranks == 1
    assert ctx.provenance.traits.entries[115_483].max_ranks == 2


# -- classification --------------------------------------------------------

@pytest.mark.parametrize(
    ("name", "roles"),
    [
        ("improved_vivify", ["SpellModifier/Percent/Direct/ClassMask",
                             "SpellModifier/Percent/Direct/Label"]),
        ("martial_expert", ["SpellModifier/Percent/CriticalBonus/ClassMask",
                            "CriticalBlockAmount"]),
        ("heart_of_the_crusader", ["AutoAttackDamage", "AutoAttackCriticalChance",
                                   "SpellModifier/Percent/Direct/ClassMask",
                                   "SpellModifier/Percent/CriticalBonus/ClassMask"]),
        ("phalanx", ["SpellModifier/Percent/Direct/ClassMask",
                     "SpellModifier/Percent/Direct/Label"]),
        ("ephemeral_bond", ["HealingReceived", "SpellModifier/PercentFirstEffect/Label",
                            "SpellModifier/PercentFirstEffect/Label"]),
    ],
)
def test_reference_packages_classify_without_names(ctx, name, roles) -> None:
    entry, rank = REFERENCE[name]
    resolved = ctx.provenance.resolve(entry, rank)
    classifications = classify_package(resolved)
    assert all(c.ok for c in classifications), [c.blockers for c in classifications]
    assert [c.role.key() for c in classifications] == roles


def test_classification_reads_no_identity(ctx: Context) -> None:
    """The classifier's inputs are the effect's own facts and nothing else.

    Two packages with the same role multiset and different ids must classify identically;
    Improved Vivify and Phalanx are exactly that pair.
    """
    vivify = [c.role.key() for c in classify_package(ctx.provenance.resolve(101_510, 2))]
    phalanx = [c.role.key() for c in classify_package(ctx.provenance.resolve(137_000, 2))]
    assert vivify == phalanx


def test_unclassified_effect_names_its_missing_semantic(ctx: Context) -> None:
    """An unrecognised sibling is never silently dropped."""
    for entry, rank in ctx.variants():
        resolved = ctx.provenance.resolve(entry, rank)
        if isinstance(resolved, TraitSpellError):
            continue
        for classification in classify_package(resolved):
            if classification.role is None:
                assert classification.blockers, classification.effect.ref
                return
    pytest.fail("the population contains no unclassified effect; the census is wrong")


# -- Core's own catalogs ---------------------------------------------------

def test_core_catalogs_are_complete_slices() -> None:
    """``coreref`` fails closed rather than returning a partial catalog."""
    auras, attributes = coreref.aura_subtypes(), coreref.spell_attributes()
    assert len(auras) == 186
    assert len(attributes) == 126
    assert coreref.aura_support(108) == "implemented"
    assert coreref.attribute_support(6) == "implemented"
    assert coreref.aura_support(99_999) == "unknown"


# -- the Core baseline -----------------------------------------------------

def test_core_admits_two_hundred_and_four(ctx: Context) -> None:
    """Contract path 166 + sole-authority path 184, overlapping on 146."""
    contract, sole = set(), set()
    for entry, rank in ctx.variants():
        if not isinstance(ctx.baseline.admit(entry, rank), str):
            resolved = ctx.provenance.resolve(entry, rank)
            mechanics = sum(e.mechanic for e in resolved.effects)
            from selected_package.corebaseline import _owner_final_issues
            if not _owner_final_issues(ctx.baseline, resolved.spell,
                                       ctx.baseline.admit(entry, rank), mechanics):
                contract.add((entry, rank))
        if ctx.baseline.admit_sole(entry, rank) is not None:
            sole.add((entry, rank))
    assert len(contract) == 166
    assert len(sole) == 184
    assert len(contract & sole) == 146
    assert len(contract | sole) == 204


@pytest.mark.parametrize("name", sorted(REFERENCE))
def test_each_named_package_is_admitted_through_its_branch(ctx, name) -> None:
    entry, rank = REFERENCE[name]
    decision = ctx.baseline.admit_final(entry, rank)
    assert not isinstance(decision, str), decision
    assert decision.branch == name


def test_auto_attack_damage_branch_admits_nothing(ctx: Context) -> None:
    """Its only real instance, Precision Strikes, is refused by the school test."""
    reached = [(e, r) for e, r in ctx.variants()
               if not isinstance(ctx.baseline.admit(e, r), str)
               and ctx.baseline.admit(e, r).branch == "auto_attack_damage"]
    assert reached == [(126_443, 1)]
    refusal = ctx.baseline.admit_final(126_443, 1)
    assert isinstance(refusal, str) and "physical shell" in refusal


def test_ephemeral_bond_admits_its_detached_twin(ctx: Context) -> None:
    """Core pins only the provider spell here, unlike Heart -- inconsistency I1."""
    assert not ctx.provenance.traits.nodes_for_entry.get(112_613)
    decision = ctx.baseline.admit_final(112_613, 1)
    assert not isinstance(decision, str)
    assert decision.branch == "ephemeral_bond"
    # Heart's equivalent twin is fail-closed.
    assert isinstance(ctx.baseline.admit_final(115_441, 1), str)


# -- the rules -------------------------------------------------------------

def test_rule_v1_has_only_adjudicated_disagreements(ctx: Context) -> None:
    from selected_package.cmd_rule import ADJUDICATIONS
    result = score(ctx, RULES[0])
    assert result["core_admitted"] == 204
    assert all(key in ADJUDICATIONS for key in result["_fn_keys"]), result["_fn_keys"]


def test_shaping_uniformity_is_falsified(ctx: Context) -> None:
    """v2x must refuse Improved Vivify -- that is why the dimension is dead."""
    rule = next(r for r in RULES if r.version == "v2x")
    assert not rule.evaluate(ctx, 101_510, 1).admitted
    assert RULES[0].evaluate(ctx, 101_510, 1).admitted


def test_role_uniqueness_has_no_marginal_effect(ctx: Context) -> None:
    """Hostile review 2: half of v5's stated delta removes nothing.

    `role_uniqueness` fires on real packages, but every one of them is already refused by
    another dimension, so dropping it from v5 changes no admission.  The dimension is kept
    because a future widening (raising the effect ceiling, adding an authority) could make
    it bite -- but the report must not claim it as part of v5's delta.
    """
    from selected_package.rule import Rule
    final = next(r for r in RULES if r.version == "v5")
    without = Rule("probe", "", tuple(d for d in final.dimensions if d != "role_uniqueness"))
    assert score(ctx, without)["rule_admitted"] == score(ctx, final)["rule_admitted"]


def test_unreachable_critical_subtypes_do_not_classify(ctx: Context) -> None:
    """Hostile review 1: raws 52/57/183/187/197 have a Core authority but no selected path."""
    from selected_package.effects import (CRITICAL_SUBTYPES_WITHOUT_SELECTED_ACTIVATION,
                                          SELECTED_AUTHORITIES)
    assert not set(CRITICAL_SUBTYPES_WITHOUT_SELECTED_ACTIVATION) & set(SELECTED_AUTHORITIES)
    seen = 0
    for entry, rank in ctx.variants():
        resolved = ctx.provenance.resolve(entry, rank)
        if isinstance(resolved, TraitSpellError):
            continue
        for classification in classify_package(resolved):
            if classification.effect.aura in CRITICAL_SUBTYPES_WITHOUT_SELECTED_ACTIVATION:
                assert classification.role is None
                assert classification.blockers
                seen += 1
    assert seen, "no witness for the unreachable critical subtypes; the guard is untested"


def test_modifier_space_is_a_free_cross_product() -> None:
    """Hostile review 2: the value/selector/operation table must not encode the reference set."""
    from selected_package.effects import _MODIFIER_OPERATIONS, _MODIFIER_SUBTYPES
    assert set(_MODIFIER_SUBTYPES) == {107, 108, 218, 219}
    assert set(_MODIFIER_OPERATIONS) == {0, 3, 11, 15, 22}


def test_furious_blows_twin_is_the_only_identity_refusal() -> None:
    payload = json.loads((CORPORA / "false-positives.json").read_text(encoding="utf-8"))
    pinned = [r for r in payload["records"]
              if any(d.startswith("sole-authority identity pin") or d.startswith("identity only")
                     for d in r["differs_in"])]
    assert [(r["entry"], r["rank"]) for r in pinned] == [(112_197, 1)]


#: `rank_amounts` is measured vacuous: source resolution already refuses a non-finite
#: amount, so the dimension can never fire.  It is listed here so the redundancy is an
#: asserted fact rather than an assumption.
VACUOUS_DIMENSIONS = {"rank_amounts"}


def test_every_rule_dimension_is_reachable_except_the_proved_redundancy(ctx: Context) -> None:
    """A dimension that never fires is decoration unless the redundancy is proved."""
    final = next(r for r in RULES if r.version == "v5")
    fired = {name: False for name in final.dimensions}
    for entry, rank in ctx.variants():
        for name, passed in final.evaluate(ctx, entry, rank).dimensions.items():
            if name in fired and not passed:
                fired[name] = True
    never = {name for name, hit in fired.items() if not hit}
    assert never == VACUOUS_DIMENSIONS, never
