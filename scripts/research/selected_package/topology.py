"""Package topology: the complete shape of one provider's effect set.

Mirrors: ``SelectedTraitPackageContract`` / ``selected_trait_package_contract``
(``core/crates/combat/src/program/selected_trait_package.rs:33-46,216-228``) and the
positional array patterns every admitted branch destructures the *complete* source
effect slice with (``selected_trait_package.rs:351,388,420,457``).

Core's contract is a fixed ``[Option<SelectedTraitEffectContract>; MAX_PACKAGE_EFFECTS]``
(``selected_trait_package.rs:26,35``) filled by exhaustive slice patterns.  Three
structural assumptions are baked into that shape and none of them is stated anywhere:

1. one selected provider spell is one *atomic* package -- every source effect of the
   provider belongs to it, for one actor, exactly once;
2. the package is *positional* -- role assignment is by source effect order;
3. the package fits in four slots.

This module measures all three against the 8,846 resolvable entry x rank variants,
and -- the point of the exercise -- records every package where a naive
"the effects I recognised are the package" rule would admit a provider that still
carries an unrecognised sibling effect.  Core itself does not have that bug: its
array patterns fix the arity, so an unrecognised sibling makes the pattern fail.  A
*generic* rule that merely filters effects by classifiability would have it.

Nothing here reads a talent, entry, definition or spell identity to reach a verdict.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Iterator

from procs._trinity_names import SPELL_EFFECT_NAMES, SPELL_TARGET_NAMES
from targeting.selectors import info as selector_info

from . import coreref
from . import ROOT
from .effects import APPLY_AURA, Classification, classify_package
from .provenance import Provenance, Resolved, SourceEffect, TraitSpellError

#: ``MAX_PACKAGE_EFFECTS`` (selected_trait_package.rs:26).
MAX_PACKAGE_EFFECTS = 4

#: ``Targets::TARGET_UNIT_CASTER`` -- the only self-delivery implicit target pair Core
#: accepts (``effects.neutral_blockers``; exact_source.rs:178-231).
SELF_TARGETS = (1, 0)

#: ``Targets::TARGET_UNIT_CASTER`` raw (Spell.cpp:1749 resolves it to ``m_caster``).
TARGET_UNIT_CASTER = 1

#: ``SpellEffectName`` values that deliver the aura to a unit *other than the caster*
#: regardless of the implicit target pair -- the effect kind itself is the recipient
#: rule (Spell.cpp ``EffectApplyAreaAura`` family / ``EffectApplyAuraOnPet``).
ACTOR_SPLIT_EFFECT_KINDS = {
    27,   # PERSISTENT_AREA_AURA
    35,   # APPLY_AREA_AURA_PARTY
    65,   # APPLY_AREA_AURA_RAID
    119,  # APPLY_AREA_AURA_PET
    128,  # APPLY_AREA_AURA_FRIEND
    129,  # APPLY_AREA_AURA_ENEMY
    143,  # APPLY_AREA_AURA_OWNER
    174,  # APPLY_AURA_ON_PET
    202,  # APPLY_AREA_AURA_SUMMONS
    271,  # APPLY_AREA_AURA_PARTY_NONRANDOM
}

#: ``SpellImplicitTargetInfo`` object classes that name a unit recipient (SpellInfo.h:63).
UNIT_OBJECTS = ("UNIT", "UNIT_AND_DEST")


def delivery_class(target_a: int, target_b: int, kind: int) -> str:
    """Which actor an exact effect is delivered to, from source facts only.

    ``self``         every nonzero implicit target is ``TARGET_UNIT_CASTER``
    ``unspecified``  no implicit target at all (recipients come from the cast, not the row)
    ``other_unit``   at least one implicit target names a unit that need not be the caster,
                     or the effect kind is an area-aura/pet delivery kind
    ``location``     only SRC/DEST/GOBJ objects -- no unit recipient named by the row
    """
    if kind in ACTOR_SPLIT_EFFECT_KINDS:
        return "other_unit"
    targets = [t for t in (target_a, target_b) if t]
    if not targets:
        return "unspecified"
    if all(t == TARGET_UNIT_CASTER for t in targets):
        return "self"
    for target in targets:
        if target == TARGET_UNIT_CASTER:
            continue
        if selector_info(target).object in UNIT_OBJECTS:
            return "other_unit"
    return "location"


#: A reference role order, taken from Core's *multi-authority* branches: ephemeral bond
#: slots ``HealingReceived`` first (selected_trait_package.rs:351-375) and heart slots
#: ``AutoAttackDamage`` then ``AutoAttackCriticalChance`` then the modifiers (:507-560).
#:
#: It is a measurement yardstick, NOT a rule, and Core itself does not obey it: the
#: single-rank auto-attack branch slots the ``SpellModifier`` first and
#: ``AutoAttackDamage`` second (:420-446), the opposite of heart.  ``order_canonical``
#: therefore only answers "does this package arrive in heart's order", and the finding
#: the pass reports is that Core has no canonical order to generalise.
ROLE_ORDER = (
    "HealingReceived",
    "AutoAttackDamage",
    "AutoAttackCriticalChance",
    "SpellModifier",
    "CriticalBlockAmount",
)

#: Why one unrecognised sibling cannot be owned, in the grouping the pass reports.
#: ``coreref.aura_support`` classes plus the two non-aura buckets.
BLOCK_GROUPS = (
    "non_aura_effect_kind",
    "aura_unimplemented",
    "aura_disabled",
    "aura_ignored",
    "aura_absent_from_core_catalog",
    "aura_implemented_no_selected_semantic",
    "aura_implemented_nonneutral_shell",
)


def target_name(value: int) -> str:
    """``Targets`` enum name (``procs._trinity_names``, from Trinity's SharedDefines.h)."""
    return SPELL_TARGET_NAMES.get(value, "NONE" if value == 0 else f"TARGET_{value}")


def effect_kind_name(value: int) -> str:
    return SPELL_EFFECT_NAMES.get(value, f"SPELL_EFFECT_{value}")


def aura_name(raw: int) -> str | None:
    """Core's own ``AuraSubtypeKind`` variant name, or ``None`` when Core has no row."""
    entry = coreref.aura_subtypes().get(raw)
    return entry.name if entry else None


# ---------------------------------------------------------------------------
# per-effect
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EffectTopology:
    """One exact effect's place in its package."""

    index: int                      # 1-based, == SpellEffectRef::effect_index
    kind: int
    kind_name: str
    aura_subtype: int
    aura_subtype_name: str | None
    aura_support: str               # coreref support class, "unknown" == build skew
    role: str | None                # Role.key(), or None when nothing classified it
    role_name: str | None           # SelectedTraitEffectRole variant
    recognized: bool                # Classification.ok -- role AND no blockers
    role_without_shell: bool        # a role matched but the neutrality shell refused it
    blockers: tuple[str, ...]
    block_group: str | None         # BLOCK_GROUPS member, None when recognized
    missing_semantic: str | None    # the precise semantic/consumer Core lacks
    shaped: bool                    # resolved.is_rank_shaped(index)
    point_operation: str | None     # resolved.point_operation(index)
    implicit_targets: tuple[int, int]
    implicit_target_names: tuple[str, str]
    self_delivered: bool
    delivery: str                   # delivery_class(): self|unspecified|other_unit|location
    trigger_spell: int
    period_ms: int
    selector: str | None            # "ClassMask" | "Label" | None
    label: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "kind_name": self.kind_name,
            "aura_subtype": self.aura_subtype,
            "aura_subtype_name": self.aura_subtype_name,
            "aura_support": self.aura_support,
            "role": self.role,
            "role_name": self.role_name,
            "recognized": self.recognized,
            "role_without_shell": self.role_without_shell,
            "blockers": list(self.blockers),
            "block_group": self.block_group,
            "missing_semantic": self.missing_semantic,
            "shaped": self.shaped,
            "point_operation": self.point_operation,
            "implicit_targets": list(self.implicit_targets),
            "implicit_target_names": list(self.implicit_target_names),
            "self_delivered": self.self_delivered,
            "delivery": self.delivery,
            "trigger_spell": self.trigger_spell,
            "period_ms": self.period_ms,
            "selector": self.selector,
            "label": self.label,
        }


def _block_group(effect: SourceEffect, classification: Classification) -> tuple[str, str]:
    """``(BLOCK_GROUPS member, precise missing semantic)`` for one unrecognised effect.

    The grouping answers the pass question "what exactly stops Core owning this
    sibling": a non-aura carrier, a subtype Core's catalog refuses
    (``disabled``/``unimplemented``/``ignored``), a subtype Core's catalog has never
    heard of (build skew against the pinned Core), or a subtype Core *does* implement
    for ordinary auras but which has no *selected-passive* consumer.
    """
    if effect.kind != APPLY_AURA:
        return (
            "non_aura_effect_kind",
            f"effect kind {effect.kind} ({effect_kind_name(effect.kind)}) is not APPLY_AURA; "
            "the selected-passive contract owns aura carriers only "
            "(selected_trait_package.rs:1055-1069)",
        )

    support = coreref.aura_support(effect.aura)
    name = aura_subtype_display(effect.aura)
    if support == "unknown":
        return (
            "aura_absent_from_core_catalog",
            f"aura subtype {effect.aura} has no row in Core's AuraSubtypeKind catalog "
            "(crates/dbc/src/aura_subtype.rs); build skew -- no consumer exists to name",
        )
    if support in ("disabled", "unimplemented", "ignored"):
        return (
            f"aura_{support}",
            f"aura subtype {effect.aura} ({name}) is `{support}` in Core's catalog "
            "(crates/dbc/src/aura_subtype.rs); it has no runtime consumer at all, "
            "selected or otherwise",
        )

    # implemented as an ordinary aura, but not by a selected-passive authority.
    if classification.role is not None:
        return (
            "aura_implemented_nonneutral_shell",
            f"aura subtype {effect.aura} ({name}) reaches a role but the exact-effect "
            "neutrality shell refuses it: " + "; ".join(sorted(classification.blockers)),
        )
    detail = "; ".join(sorted(classification.blockers))
    return (
        "aura_implemented_no_selected_semantic",
        f"aura subtype {effect.aura} ({name}) is implemented for ordinary auras but no "
        "selected-passive authority consumes it "
        f"(SelectedTraitEffectRole has 5 variants, selected_trait_package.rs:80-90): {detail}",
    )


def aura_subtype_display(raw: int) -> str:
    return aura_name(raw) or f"raw-{raw}"


def effect_topology(resolved: Resolved, classification: Classification) -> EffectTopology:
    effect = classification.effect
    role = classification.role
    recognized = classification.ok
    group, missing = (None, None)
    if not recognized:
        group, missing = _block_group(effect, classification)
    return EffectTopology(
        index=effect.index,
        kind=effect.kind,
        kind_name=effect_kind_name(effect.kind),
        aura_subtype=effect.aura,
        aura_subtype_name=aura_name(effect.aura),
        aura_support=coreref.aura_support(effect.aura) if effect.kind == APPLY_AURA else "n/a",
        role=role.key() if role else None,
        role_name=role.name if role else None,
        recognized=recognized,
        role_without_shell=role is not None and not recognized,
        blockers=tuple(sorted(classification.blockers)),
        block_group=group,
        missing_semantic=missing,
        shaped=resolved.is_rank_shaped(effect.index),
        point_operation=resolved.point_operation(effect.index),
        implicit_targets=(effect.target_a, effect.target_b),
        implicit_target_names=(target_name(effect.target_a), target_name(effect.target_b)),
        self_delivered=(effect.target_a, effect.target_b) == SELF_TARGETS,
        delivery=delivery_class(effect.target_a, effect.target_b, effect.kind),
        trigger_spell=effect.trigger_spell,
        period_ms=effect.period_ms,
        selector=role.selector if role else None,
        label=role.label if role else None,
    )


# ---------------------------------------------------------------------------
# per-package
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PackageTopology:
    """The complete structural shape of one provider's effect set for one entry x rank."""

    entry_id: int
    definition_id: int
    provider: int
    max_ranks: int
    effective_rank: int
    effects: tuple[EffectTopology, ...]

    # -- arity ------------------------------------------------------------
    @property
    def total_effects(self) -> int:
        """The COMPLETE source effect set, before any role filtering."""
        return len(self.effects)

    @property
    def exceeds_effect_cap(self) -> bool:
        """Cannot fit Core's ``[Option<_>; MAX_PACKAGE_EFFECTS]`` (rs:26,35)."""
        return self.total_effects > MAX_PACKAGE_EFFECTS

    # -- recognition ------------------------------------------------------
    @property
    def recognized(self) -> int:
        return sum(1 for e in self.effects if e.recognized)

    @property
    def unrecognized(self) -> int:
        return self.total_effects - self.recognized

    @property
    def unrecognized_subtypes(self) -> tuple[int, ...]:
        return tuple(sorted({e.aura_subtype for e in self.effects if not e.recognized}))

    @property
    def over_admitted(self) -> bool:
        """A naive recognised-effects-only rule would admit this provider.

        At least one effect enters a role and at least one sibling does not: filtering
        the package down to "what I recognised" silently drops a real source effect.
        """
        return self.recognized > 0 and self.unrecognized > 0

    @property
    def fully_recognized(self) -> bool:
        return self.recognized == self.total_effects

    # -- order ------------------------------------------------------------
    @property
    def role_sequence(self) -> tuple[str | None, ...]:
        """Role names in source effect-index order; ``None`` for unrecognised slots."""
        return tuple(e.role_name for e in self.effects)

    @property
    def role_key_sequence(self) -> tuple[str | None, ...]:
        return tuple(e.role for e in self.effects)

    @property
    def order_canonical(self) -> bool:
        """Whether the recognised roles appear in :data:`ROLE_ORDER`.

        Core assigns roles *positionally* from the source slice, so a package whose
        roles arrive in a different order than Core's slot order is a package Core's
        array pattern would mis-slot if the branch were made generic.
        """
        ranks = [ROLE_ORDER.index(e.role_name) for e in self.effects
                 if e.role_name in ROLE_ORDER]
        return all(a <= b for a, b in zip(ranks, ranks[1:]))

    @property
    def repeated_roles(self) -> dict[str, int]:
        counts = Counter(e.role for e in self.effects if e.role)
        return {key: n for key, n in sorted(counts.items()) if n > 1}

    @property
    def repeated_role_names(self) -> dict[str, int]:
        counts = Counter(e.role_name for e in self.effects if e.role_name)
        return {key: n for key, n in sorted(counts.items()) if n > 1}

    # -- selection / authority mixing -------------------------------------
    @property
    def selectors(self) -> tuple[str, ...]:
        return tuple(sorted({e.selector for e in self.effects if e.selector}))

    @property
    def mixed_selectors(self) -> bool:
        """``ClassMask`` and ``Label`` selection inside one package."""
        return len(self.selectors) > 1

    @property
    def role_names(self) -> tuple[str, ...]:
        return tuple(sorted({e.role_name for e in self.effects if e.role_name}))

    @property
    def mixed_authorities(self) -> bool:
        """``SpellModifier`` mixed with a non-modifier authority.

        These are exactly the packages ``state/construction.rs:334-337``
        ``mixed_passive_effects`` exists for, and the only ones that get a same-actor
        atomicity check across *different* authorities.
        """
        names = set(self.role_names)
        return "SpellModifier" in names and len(names) > 1

    # -- rank shaping ------------------------------------------------------
    @property
    def shaped_indices(self) -> tuple[int, ...]:
        return tuple(e.index for e in self.effects if e.shaped)

    @property
    def unshaped_indices(self) -> tuple[int, ...]:
        return tuple(e.index for e in self.effects if not e.shaped)

    @property
    def mixed_shaping(self) -> bool:
        """Some effects rank-shaped by a curve, some carrying their authored amount.

        Improved Vivify is exactly this shape and Core hardcodes it
        (``selected_trait_package.rs:498-504`` gates on entry 101510 + provider 231602
        + ``has_improved_vivify_amounts``).
        """
        return bool(self.shaped_indices) and bool(self.unshaped_indices)

    @property
    def point_operations(self) -> tuple[str, ...]:
        return tuple(sorted({e.point_operation for e in self.effects if e.point_operation}))

    # -- index density -----------------------------------------------------
    @property
    def effect_index_density(self) -> bool:
        """Whether the source effect indices are the dense ``1..n`` ``SpellEffectRef`` assumes."""
        return [e.index for e in self.effects] == list(range(1, self.total_effects + 1))

    # -- split axes (deliverable 3) ----------------------------------------
    @property
    def non_self_effects(self) -> tuple[EffectTopology, ...]:
        """Effects whose implicit target pair is not exactly ``TARGET_UNIT_CASTER``/NONE."""
        return tuple(e for e in self.effects if not e.self_delivered)

    @property
    def other_unit_effects(self) -> tuple[EffectTopology, ...]:
        """Effects the source names a *different* unit recipient for."""
        return tuple(e for e in self.effects if e.delivery == "other_unit")

    @property
    def splits_by_actor(self) -> bool:
        """At least one effect is delivered to a unit that need not be the selecting player."""
        return bool(self.other_unit_effects)

    @property
    def deliveries(self) -> tuple[str, ...]:
        return tuple(sorted({e.delivery for e in self.effects}))

    @property
    def child_actions(self) -> tuple[int, ...]:
        return tuple(sorted({e.trigger_spell for e in self.effects if e.trigger_spell}))

    @property
    def splits_by_child_action(self) -> bool:
        return bool(self.child_actions)

    @property
    def aura_kinds(self) -> tuple[int, ...]:
        return tuple(sorted({e.kind for e in self.effects}))

    @property
    def splits_by_effect_kind(self) -> bool:
        """An aura carrier and an instant effect kind in the same package."""
        kinds = set(self.aura_kinds)
        return APPLY_AURA in kinds and len(kinds) > 1

    @property
    def periodic_indices(self) -> tuple[int, ...]:
        return tuple(e.index for e in self.effects if e.period_ms)

    @property
    def shape_key(self) -> tuple[str, ...]:
        """A package shape: the ordered role keys, unrecognised slots named by subtype."""
        return tuple(e.role or f"unclassified:{e.aura_subtype}" for e in self.effects)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "definition_id": self.definition_id,
            "provider": self.provider,
            "max_ranks": self.max_ranks,
            "effective_rank": self.effective_rank,
            "total_effects": self.total_effects,
            "recognized": self.recognized,
            "unrecognized": self.unrecognized,
            "unrecognized_subtypes": list(self.unrecognized_subtypes),
            "over_admitted": self.over_admitted,
            "fully_recognized": self.fully_recognized,
            "exceeds_effect_cap": self.exceeds_effect_cap,
            "role_sequence": list(self.role_sequence),
            "role_key_sequence": list(self.role_key_sequence),
            "order_canonical": self.order_canonical,
            "repeated_roles": self.repeated_roles,
            "repeated_role_names": self.repeated_role_names,
            "selectors": list(self.selectors),
            "mixed_selectors": self.mixed_selectors,
            "role_names": list(self.role_names),
            "mixed_authorities": self.mixed_authorities,
            "shaped": list(self.shaped_indices),
            "unshaped": list(self.unshaped_indices),
            "mixed_shaping": self.mixed_shaping,
            "point_operations": list(self.point_operations),
            "effect_index_density": self.effect_index_density,
            "deliveries": list(self.deliveries),
            "splits_by_actor": self.splits_by_actor,
            "splits_by_child_action": self.splits_by_child_action,
            "splits_by_effect_kind": self.splits_by_effect_kind,
            "child_actions": list(self.child_actions),
            "periodic": list(self.periodic_indices),
            "shape_key": list(self.shape_key),
            "effects": [e.to_dict() for e in self.effects],
        }


def topology(resolved: Resolved,
             classifications: list[Classification] | None = None) -> PackageTopology:
    """Build the topology record for one already-resolved entry x rank."""
    classifications = classifications or classify_package(resolved)
    return PackageTopology(
        entry_id=resolved.entry_id,
        definition_id=resolved.definition_id,
        provider=resolved.spell,
        max_ranks=resolved.max_ranks,
        effective_rank=resolved.effective_rank,
        effects=tuple(effect_topology(resolved, c) for c in classifications),
    )


# ---------------------------------------------------------------------------
# population
# ---------------------------------------------------------------------------

class TopologyCensus:
    """Every resolvable entry x rank, with its package topology."""

    def __init__(self, provenance: Provenance | None = None) -> None:
        self.provenance = provenance or Provenance()

    def variants(self) -> Iterator[PackageTopology]:
        """Mirrors the population ``selected_trait_package_contract`` is offered."""
        traits = self.provenance.traits
        for entry_id in sorted(traits.entries):
            entry = traits.entries[entry_id]
            probe = self.provenance.resolve(entry_id, 1)
            if isinstance(probe, TraitSpellError):
                continue
            for rank in range(1, entry.max_ranks + 1):
                resolved = probe if rank == 1 else self.provenance.resolve(entry_id, rank)
                if isinstance(resolved, TraitSpellError):
                    continue
                yield topology(resolved)

    @cached_property
    def packages(self) -> list[PackageTopology]:
        return list(self.variants())

    # -- summaries ---------------------------------------------------------
    def effect_count_histogram(self) -> dict[int, int]:
        return dict(sorted(Counter(p.total_effects for p in self.packages).items()))

    def over_admitted(self) -> list[PackageTopology]:
        return [p for p in self.packages if p.over_admitted]

    def over_admission_groups(self) -> dict[str, int]:
        """``BLOCK_GROUPS`` -> number of over-admitted packages blocked by that group.

        A package is counted once per *distinct* group present among its unrecognised
        siblings, so the sum can exceed the package count; ``blocked_by_only`` below
        gives the partition.
        """
        counts: Counter[str] = Counter()
        for package in self.over_admitted():
            for group in {e.block_group for e in package.effects if e.block_group}:
                counts[group] += 1
        return dict(sorted(counts.items()))

    def over_admission_partition(self) -> dict[str, int]:
        """Exact partition: the sorted tuple of groups blocking each over-admitted package."""
        counts: Counter[str] = Counter()
        for package in self.over_admitted():
            key = "+".join(sorted({e.block_group for e in package.effects if e.block_group}))
            counts[key] += 1
        return dict(sorted(counts.items()))

    def over_admitted_subtypes(self) -> dict[str, dict[str, Any]]:
        """Every unrecognised sibling *carrier* seen in an over-admitted package.

        Keyed ``aura:<raw>`` for an ``APPLY_AURA`` carrier and ``kind:<raw>`` otherwise:
        a non-aura effect row still carries a stale ``EffectAura`` value that means
        nothing, so the two spaces must not be merged.
        """
        out: dict[str, dict[str, Any]] = {}
        for package in self.over_admitted():
            for e in package.effects:
                if e.recognized:
                    continue
                key = f"aura:{e.aura_subtype}" if e.kind == APPLY_AURA else f"kind:{e.kind}"
                row = out.setdefault(key, {
                    "carrier": key,
                    "effect_kind": e.kind,
                    "effect_kind_name": e.kind_name,
                    "aura_subtype": e.aura_subtype if e.kind == APPLY_AURA else None,
                    "aura_subtype_name": e.aura_subtype_name if e.kind == APPLY_AURA else None,
                    "aura_support": e.aura_support,
                    "block_groups": set(),
                    "effects": 0,
                    "packages": set(),
                    "providers": set(),
                    "missing_semantic": e.missing_semantic,
                })
                row["block_groups"].add(e.block_group)
                row["effects"] += 1
                row["packages"].add((package.entry_id, package.effective_rank))
                row["providers"].add(package.provider)
        for row in out.values():
            row["block_groups"] = sorted(row["block_groups"])
            row["packages"] = len(row["packages"])
            row["providers"] = sorted(row["providers"])
        return dict(sorted(out.items(),
                           key=lambda kv: (-kv[1]["effects"], kv[0])))

    def shape_order_variance(self) -> dict[str, list[list[str | None]]]:
        """Role multisets observed in more than one source order.

        Core slots roles positionally; a multiset with two orders proves that source
        order is an independent fact and not derivable from the role set.
        """
        by_multiset: dict[str, set[tuple[str | None, ...]]] = defaultdict(set)
        for package in self.packages:
            if not package.fully_recognized:
                continue
            key = "|".join(sorted(k or "?" for k in package.role_key_sequence))
            by_multiset[key].add(package.role_key_sequence)
        return {key: sorted([list(s) for s in orders])
                for key, orders in sorted(by_multiset.items()) if len(orders) > 1}

    # -- corpora -----------------------------------------------------------
    def compact(self, package: PackageTopology) -> dict[str, Any]:
        """One population row: everything but the per-effect detail."""
        return {
            "entry_id": package.entry_id,
            "definition_id": package.definition_id,
            "provider": package.provider,
            "max_ranks": package.max_ranks,
            "rank": package.effective_rank,
            "total_effects": package.total_effects,
            "recognized": package.recognized,
            "unrecognized": package.unrecognized,
            "shape": list(package.shape_key),
            "flags": sorted(flag for flag, on in {
                "over_admitted": package.over_admitted,
                "fully_recognized": package.fully_recognized,
                "exceeds_effect_cap": package.exceeds_effect_cap,
                "mixed_shaping": package.mixed_shaping,
                "mixed_selectors": package.mixed_selectors,
                "mixed_authorities": package.mixed_authorities,
                "order_noncanonical": not package.order_canonical,
                "repeated_roles": bool(package.repeated_roles),
                "splits_by_actor": package.splits_by_actor,
                "splits_by_child_action": package.splits_by_child_action,
                "splits_by_effect_kind": package.splits_by_effect_kind,
                "periodic": bool(package.periodic_indices),
            }.items() if on),
        }

    def summary(self) -> dict[str, Any]:
        packages = self.packages
        over = self.over_admitted()

        def count(predicate) -> int:
            return sum(1 for package in packages if predicate(package))

        return {
            "entries_resolvable": len({p.entry_id for p in packages}),
            "variants": len(packages),
            "providers": len({p.provider for p in packages}),
            "effect_count_histogram": {str(k): v
                                       for k, v in self.effect_count_histogram().items()},
            "total_source_effects": sum(p.total_effects for p in packages),
            "fully_recognized": count(lambda p: p.fully_recognized),
            "fully_unrecognized": count(lambda p: p.recognized == 0),
            "over_admitted": len(over),
            "exceeds_effect_cap": count(lambda p: p.exceeds_effect_cap),
            "exceeds_effect_cap_fully_recognized":
                count(lambda p: p.exceeds_effect_cap and p.fully_recognized),
            "mixed_shaping": count(lambda p: p.mixed_shaping),
            "mixed_shaping_fully_recognized":
                count(lambda p: p.mixed_shaping and p.fully_recognized),
            "mixed_selectors": count(lambda p: p.mixed_selectors),
            "mixed_authorities": count(lambda p: p.mixed_authorities),
            "repeated_roles": count(lambda p: bool(p.repeated_roles)),
            "order_noncanonical": count(lambda p: not p.order_canonical),
            "effect_index_density_violations":
                count(lambda p: not p.effect_index_density),
            "splits_by_actor": count(lambda p: p.splits_by_actor),
            "splits_by_actor_over_admitted":
                count(lambda p: p.splits_by_actor and p.over_admitted),
            "splits_by_actor_fully_recognized":
                count(lambda p: p.splits_by_actor and p.fully_recognized),
            "splits_by_child_action": count(lambda p: p.splits_by_child_action),
            "splits_by_child_action_fully_recognized":
                count(lambda p: p.splits_by_child_action and p.fully_recognized),
            "splits_by_effect_kind": count(lambda p: p.splits_by_effect_kind),
            "periodic": count(lambda p: bool(p.periodic_indices)),
            "recognized_effects_not_self_delivered":
                sum(1 for p in packages for e in p.effects
                    if e.recognized and e.delivery != "self"),
            "recognized_effects_with_trigger_spell":
                sum(1 for p in packages for e in p.effects
                    if e.recognized and e.trigger_spell),
            "delivery_classes": dict(sorted(Counter(
                e.delivery for p in packages for e in p.effects).items())),
            "shaped_count_histogram": {str(k): v for k, v in sorted(Counter(
                len(p.shaped_indices) for p in packages).items())},
        }

    def shapes(self) -> list[dict[str, Any]]:
        """Distinct package shapes, most frequent first."""
        groups: dict[tuple[str, ...], list[PackageTopology]] = defaultdict(list)
        for package in self.packages:
            groups[package.shape_key].append(package)
        rows = []
        for shape, members in groups.items():
            first = members[0]
            rows.append({
                "shape": list(shape),
                "total_effects": first.total_effects,
                "variants": len(members),
                "entries": len({p.entry_id for p in members}),
                "providers": len({p.provider for p in members}),
                "fully_recognized": first.fully_recognized,
                "over_admitted": first.over_admitted,
                "example": [first.entry_id, first.effective_rank, first.provider],
            })
        rows.sort(key=lambda row: (-row["variants"], row["shape"]))
        return rows


#: The lead's substrate modules this census's verdicts are a function of.  A number
#: here is only reproducible against these exact files, so every corpus records them.
SUBSTRATE_MODULES = ("source.py", "provenance.py", "effects.py", "coreref.py", "topology.py")


def substrate_digest() -> dict[str, str]:
    """sha256 of every substrate module this track's numbers depend on."""
    base = ROOT / "scripts" / "research" / "selected_package"
    return {name: hashlib.sha256((base / name).read_bytes()).hexdigest()[:16]
            for name in SUBSTRATE_MODULES}


def topology_corpus(census: TopologyCensus, reference_entries: dict[str, int]) -> dict[str, Any]:
    """``docs/research/selected-package-corpora/topology.json``."""
    packages = census.packages
    references = {}
    for name, entry_id in sorted(reference_entries.items()):
        rows = [p for p in packages if p.entry_id == entry_id]
        references[name] = [p.to_dict() for p in sorted(rows, key=lambda p: p.effective_rank)]
    cap = [p for p in packages if p.exceeds_effect_cap]
    return {
        "provenance": {
            "question": "is one selected provider spell one atomic runtime package for one actor?",
            "core_pin": "b1714eda2b4b9393853f94c6517e78cce2dfa21a",
            "mirrors": [
                "selected_trait_package.rs:26 MAX_PACKAGE_EFFECTS",
                "selected_trait_package.rs:35 effects: [Option<_>; MAX_PACKAGE_EFFECTS]",
                "selected_trait_package.rs:351,388,420,457 complete-slice array patterns",
                "selected_spell.rs:145 SelectedTraitSpellEffects::effects",
            ],
            "population": "every TraitNodeEntry x effective rank that resolves through "
                          "Provenance.resolve (Core's try_selected_spell_effect_amounts)",
            "substrate": substrate_digest(),
        },
        "summary": census.summary(),
        "over_admission_groups": census.over_admission_groups(),
        "over_admission_partition": census.over_admission_partition(),
        "shapes": census.shapes(),
        "shape_order_variance": census.shape_order_variance(),
        "reference_packages": references,
        "effect_cap": {
            "cap": MAX_PACKAGE_EFFECTS,
            "exceeding": len(cap),
            "exceeding_fully_recognized": [p.to_dict() for p in cap if p.fully_recognized],
            "arity_histogram_fully_recognized": {str(k): v for k, v in sorted(Counter(
                p.total_effects for p in packages if p.fully_recognized).items())},
            "max_shaped_effects_observed": max(len(p.shaped_indices) for p in packages),
            "exceeding_with_every_effect_shaped":
                sum(1 for p in cap if len(p.shaped_indices) == p.total_effects),
        },
        "mixed_shaping": [census.compact(p) for p in packages if p.mixed_shaping],
        "mixed_selectors": [census.compact(p) for p in packages if p.mixed_selectors],
        "mixed_authorities": [census.compact(p) for p in packages if p.mixed_authorities],
        "order_noncanonical": [census.compact(p) for p in packages if not p.order_canonical],
        "packages": [census.compact(p) for p in packages],
    }


def over_admission_corpus(census: TopologyCensus) -> dict[str, Any]:
    """``docs/research/selected-package-corpora/over-admission.json``.

    Every entry x rank a recognised-effects-only rule over-admits, with the precise
    reason each unrecognised sibling cannot be owned.
    """
    rows = []
    for package in census.over_admitted():
        rows.append({
            "entry_id": package.entry_id,
            "rank": package.effective_rank,
            "max_ranks": package.max_ranks,
            "definition_id": package.definition_id,
            "provider": package.provider,
            "total_effects": package.total_effects,
            "recognized_roles": [
                {"index": e.index, "role": e.role, "role_name": e.role_name}
                for e in package.effects if e.recognized
            ],
            "unrecognized_siblings": [
                {
                    "index": e.index,
                    "effect_kind": e.kind,
                    "effect_kind_name": e.kind_name,
                    "aura_subtype": e.aura_subtype,
                    "aura_subtype_name": e.aura_subtype_name,
                    "core_support": e.aura_support,
                    "block_group": e.block_group,
                    "missing_semantic": e.missing_semantic,
                    "delivery": e.delivery,
                    "implicit_targets": list(e.implicit_target_names),
                    "trigger_spell": e.trigger_spell,
                    "period_ms": e.period_ms,
                    "blockers": list(e.blockers),
                }
                for e in package.effects if not e.recognized
            ],
            "splits_by_actor": package.splits_by_actor,
            "splits_by_child_action": package.splits_by_child_action,
            "splits_by_effect_kind": package.splits_by_effect_kind,
        })
    rows.sort(key=lambda row: (row["entry_id"], row["rank"]))
    return {
        "provenance": {
            "definition": "an entry x rank where at least one exact effect classifies into a "
                          "role AND at least one sibling effect does not; a rule that keeps only "
                          "the effects it recognises admits the provider while silently dropping "
                          "a real source effect",
            "core_pin": "b1714eda2b4b9393853f94c6517e78cce2dfa21a",
            "note": "Core itself does not over-admit: every admitted branch destructures the "
                    "complete source effect slice with a fixed-size array pattern "
                    "(selected_trait_package.rs:351,388,420,457), so an unrecognised sibling "
                    "makes the pattern fail. The risk is created by generalising to a filter.",
            "substrate": substrate_digest(),
        },
        "count": len(rows),
        "entries": len({row["entry_id"] for row in rows}),
        "providers": len({row["provider"] for row in rows}),
        "groups": census.over_admission_groups(),
        "partition": census.over_admission_partition(),
        "carriers": census.over_admitted_subtypes(),
        "packages": rows,
    }
