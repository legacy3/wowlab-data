"""Sole-effect selected-passive authorities.

A *sole-effect* selected package is one whose provider declares exactly one effect.
Core admits such a package through a small number of compiler branches that do **not**
consult a multi-effect package shape.  This module transcribes every one of them.

Mirrors (core pinned at ``b1714eda2b4b9393853f94c6517e78cce2dfa21a``):

=========  ==========================================  ===============================
subtype    compiler branch                             Core symbol
=========  ==========================================  ===============================
79         ``passive_damage_modifier.rs:197-215``      ``compile_definition``
87         ``passive_damage_modifier.rs:157-195``      ``compile_definition``
107        ``selected_trait_package.rs:456-474``       ``single_rank_contract`` fallback
           ``selected_trait_package.rs:626-638``       ``ranked_cooldown_contract``
108        ``selected_trait_package.rs:456-474``       ``single_rank_contract`` fallback
           ``selected_trait_package.rs:606-624``       ``ranked_direct_contract``
118        ``passive_healing_received.rs:126-224``     ``compile_definition``
193        ``passive_haste.rs:106-195``                ``compile_definition``
218        ``selected_trait_package.rs:456-474``       ``single_rank_contract`` fallback
219        ``selected_trait_package.rs:456-474``       ``single_rank_contract`` fallback
290        ``critical_modifier.rs:470-529``            ``selected_percent_points`` else-arm
308        ``critical_modifier.rs:470-529``            ``selected_percent_points`` else-arm
319        ``passive_melee_auto_attack_speed.rs:111``  ``validate_selected_source`` (NAMED)
422        ``passive_absorb_received.rs:124-181``      ``compile_definition``
=========  ==========================================  ===============================

Three subtypes the pass brief listed are **not** sole-effect authorities, and the
exclusions are load-bearing:

``334`` ``ModAutoAttackCritChance``
    ``critical_modifier.rs:488-518`` routes it through ``selected_trait_package_contract``.
``344`` ``ModAutoAttackDamage``
    ``passive_auto_attack_damage.rs:171-172`` always demands the package contract.
``638`` ``ModCriticalBlockAmount``
    ``critical_block_amount.rs`` never calls ``try_selected_spell_effect_amounts``.
    Its only selected reachability is as Martial Expert's second effect
    (``critical_block_amount.rs:254-284``).

And ``52`` / ``57`` / ``183`` / ``187`` / ``197`` are unreachable from a selected trait
at all: ``critical_modifier.rs:1059-1070`` refuses a ``SelectedTrait`` activation whose
subtype is not 290, 308 or 334.

Shared source shells transcribed here:

``exact_neutral_aura_source_issue``  ``exact_source.rs:178-231`` -> :func:`neutral_aura_blockers`
``neutral_physical_passive_modifier_owner_issue`` ``exact_source.rs:238-244``
``neutral_physical_passive_owner_shell_issue``    ``exact_source.rs:266-341``
``passive_modifier_owner_policy_with_count_issue`` ``exact_source.rs:437-475``
``selected_trait_owner_policy_issue`` ``selected_trait_package.rs:270-342``
``supports_unconditional_passive_application_policy`` ``passive_spell_modifier.rs:1100-1109``

Two host-side facts cannot be decided from DBC source alone.  Both are recorded rather
than guessed, and both are *assumed satisfied* for a sole-effect package because the
sole effect is by construction the one declared role:

* ``spell_source_effect_count`` -- an ingestion count, equal to the observed effect
  count for every provider this oracle resolves.
* the selected passive-modifier **owner-role count** (``expected_roles``) -- a
  ``ResolvedActionCatalog`` fact supplied by the host, which is 1 for a one-effect
  declaration.

One further modelling decision: Core distinguishes ``allow_neutral_missile`` true/false,
which is the difference between "``spell_missile`` row absent" and "row absent or
present with zero speed/launch/minimum".  The host emits a missile row only for
meaningful timings (``owner.py`` records the all-zero case as an inert class-A sidecar),
so the two spellings collapse to the same predicate here.  That collapse is a finding,
not an approximation that hides one: see ``G2-sole-authorities.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Callable

from . import CORE, FailClosed
from . import coreref
from .owner import Owners
from .provenance import Resolved, SourceEffect

# ---------------------------------------------------------------------------
# Core constants, by raw
# ---------------------------------------------------------------------------

#: ``EffectKind::APPLY_AURA``.
APPLY_AURA = 6

#: ``AuraSubtypeKind`` raws (crates/dbc/src/aura_subtype.rs).
MOD_DAMAGE_DONE_PERCENT = 79
MOD_DAMAGE_TAKEN_PERCENT = 87
ADD_FLAT_MODIFIER = 107
ADD_PERCENT_MODIFIER = 108
MOD_HEALING_RECEIVED_PERCENT = 118
HASTE_ALL = 193
ADD_PERCENT_LABEL_MODIFIER = 218
ADD_FLAT_LABEL_MODIFIER = 219
MOD_ALL_CRIT_CHANCE = 290
CRIT_CHANCE_FOR_CASTER_WITH_ABILITIES = 308
MOD_MELEE_AUTO_ATTACK_SPEED_PERCENT = 319
MOD_ABSORB_RECEIVED_PERCENT = 422

#: ``SpellModOp`` raws, read from ``EffectMiscValue_0``.
OP_HEALING_AND_DAMAGE = 0
OP_POINTS_INDEX_0 = 3
OP_COOLDOWN = 11
OP_PERIODIC_HEALING_AND_DAMAGE = 22

#: ``KNOWN_SCHOOL_BITS`` (damage_modifier.rs:18).
KNOWN_SCHOOL_BITS = 0b0111_1111

#: The all-schools misc mask ``passive_healing_received.rs:195`` pins.
ALL_SCHOOLS_MASK = 127

#: ``EffectAttributeKind`` masks are ``1 << (raw - 1)`` (effect_attribute.rs:29-31).
#: ``compile_effect_point_stacking`` (effect_attribute.rs:77-91) tolerates
#: ``SuppressPointsStacking`` raw 7 plus every catalog-Ignored attribute.
SUPPRESS_POINTS_STACKING_MASK = 1 << (7 - 1)
FORCE_SCALE_CAMERA_MASK = 1 << (14 - 1)
POINT_STACKING_TOLERATED_MASK = SUPPRESS_POINTS_STACKING_MASK | FORCE_SCALE_CAMERA_MASK

#: ``UNSUPPORTED_SOURCE_ATTRIBUTES`` (critical_modifier.rs:30-34), by variant name.
CRITICAL_UNSUPPORTED_ATTRIBUTE_NAMES = (
    "MasteryAffectsPoints",
    "UpdatePassivesOnApplyRemove",
    "ScalesWithCastingItemLevel",
)

#: ``passive_melee_auto_attack_speed.rs:21-24`` -- the one named sole-effect case.
FURIOUS_BLOWS = {
    "entry": 116_954,
    "definition": 121_966,
    "spell": 390_354,
    "effect_index": 1,
    "points": 5.0,
    "family": 4,
}


@cache
def _attribute_raw(name: str) -> int:
    """``SpellAttributeKind`` raw for one variant name, from Core's own enum."""
    for raw, support in coreref.spell_attributes().items():
        if support.name == name:
            return raw
    raise FailClosed(f"SpellAttributeKind::{name} is absent from Core's catalog")


@cache
def _known_effect_attribute_mask() -> int:
    """Union of every ``EffectAttributeKind`` bit Core's enum declares.

    A source bit outside this mask is build skew, and every consumer here refuses it.
    """
    path = CORE / "crates/dbc/src/effect_attribute.rs"
    if not path.exists():
        raise FailClosed(f"missing Core catalog {path}")
    text = path.read_text(encoding="utf-8")
    start = text.find("pub enum EffectAttributeKind {")
    if start < 0:
        raise FailClosed(f"{path}: no `pub enum EffectAttributeKind` block")
    end = text.find("\n    }\n", start)
    raws = [int(m) for m in re.findall(r"^\s+[A-Za-z0-9]+ = (\d+),\s*$",
                                       text[start:end], re.MULTILINE)]
    if not raws:
        raise FailClosed(f"{path}: EffectAttributeKind yielded no members")
    mask = 0
    for raw in raws:
        mask |= 1 << (raw - 1)
    return mask


def _bits(value: float) -> int:
    """``f64::to_bits`` equality: Core compares amounts bitwise, not numerically."""
    import struct

    return struct.unpack("<Q", struct.pack("<d", float(value)))[0]


def _finite(value: float | None) -> bool:
    return value is not None and value == value and abs(value) != float("inf")


def _exact_i32(value: float | None) -> int | None:
    """Mirrors: ``exact_i32`` (selected_trait_package.rs:1109-1113) /
    ``exact_flat_points`` (passive_spell_modifier.rs:841-845)."""
    if not _finite(value):
        return None
    try:
        integer = int(value)
    except (OverflowError, ValueError):
        return None
    if not -(2 ** 31) <= integer < 2 ** 31:
        return None
    return integer if _bits(float(integer)) == _bits(value) else None


# ---------------------------------------------------------------------------
# the shared neutral exact-effect source shell
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuraContract:
    """Mirrors: ``NeutralAuraSourceContract`` (exact_source.rs:166-176)."""

    subtype: int
    points: float | None          # None == the compiler pins no authored value
    targets: tuple[int, int] = (1, 0)
    misc_value_0: int | None = 0  # None == the compiler derives it from the effect

    def to_dict(self) -> dict[str, Any]:
        return {"subtype": self.subtype,
                "authored_points": "rank-derived" if self.points is None else self.points,
                "implicit_targets": list(self.targets),
                "misc_value_0": "effect-derived" if self.misc_value_0 is None
                else self.misc_value_0}


def neutral_aura_blockers(effect: SourceEffect, contract: AuraContract) -> list[str]:
    """Mirrors: ``exact_neutral_aura_source_issue`` (exact_source.rs:178-231).

    Core splits its refusals into four facts (identity / amount / metadata / missing
    aura); this returns the individual failures so a near-miss census can attribute
    them, and the fact name each belongs to is in the message.
    """
    out: list[str] = []

    # -- identity fact
    if effect.kind != APPLY_AURA:
        out.append(f"identity: effect kind {effect.kind} is not APPLY_AURA")
    if effect.aura != contract.subtype:
        out.append(f"identity: aura subtype {effect.aura} != {contract.subtype}")
    if effect.period_ms != 0:
        out.append(f"identity: periodic application ({effect.period_ms} ms)")

    # -- amount fact
    if contract.points is not None and _bits(effect.base_points) != _bits(contract.points):
        out.append(f"amount: authored base points {effect.base_points!r} "
                   f"!= contract points {contract.points!r}")
    if effect.ap_coefficient != 0.0:
        out.append("amount: nonzero attack-power coefficient")
    if effect.bonus_coefficient != 0.0:
        out.append("amount: nonzero spell-power coefficient")
    out.extend(effect_amount_facts_blockers(effect))
    if not effect.chain_is_neutral():
        out.append("amount: nonneutral EffectChainFacts")
    if effect.trigger_spell:
        out.append(f"amount: exact effect triggers spell {effect.trigger_spell}")

    # -- metadata fact
    out.extend(neutral_metadata_blockers(effect, contract.targets, contract.misc_value_0))
    return out


def effect_amount_facts_blockers(effect: SourceEffect) -> list[str]:
    """Mirrors: ``EffectAmountFacts::NEUTRAL`` (crates/data/src/effect_amount.rs:19-30)."""
    out: list[str] = []
    if effect.amplitude != 0.0:
        out.append("amount: nonzero EffectAmplitude")
    if effect.coefficient != 0.0:
        out.append("amount: nonzero scaling Coefficient")
    if effect.pvp_multiplier not in (0.0, 1.0):
        out.append(f"amount: PvpMultiplier {effect.pvp_multiplier!r}")
    if effect.variance != 0.0:
        out.append("amount: nonzero Variance")
    if effect.group_size_coefficient not in (0.0, 1.0):
        out.append("amount: nonneutral GroupSizeBasePointsCoefficient")
    if effect.scaling_class != 0:
        out.append(f"amount: ScalingClass {effect.scaling_class}")
    if effect.points_per_resource != 0.0:
        out.append("amount: nonzero EffectPointsPerResource")
    if effect.real_points_per_level != 0.0:
        out.append("amount: nonzero EffectRealPointsPerLevel")
    if effect.resource_coefficient != 0.0:
        out.append("amount: nonzero ResourceCoefficient")
    return out


def neutral_metadata_blockers(effect: SourceEffect, targets: tuple[int, int],
                              misc_value_0: int | None) -> list[str]:
    """The ``metadata_fact`` half of ``exact_neutral_aura_source_issue`` (exact_source.rs:213-229)."""
    out: list[str] = []
    if (effect.target_a, effect.target_b) != targets:
        out.append(f"metadata: implicit targets ({effect.target_a},{effect.target_b}) "
                   f"!= {targets}")
    if misc_value_0 is not None and effect.misc0 != misc_value_0:
        out.append(f"metadata: misc_value_0 {effect.misc0} != {misc_value_0}")
    if effect.misc1 != 0:
        out.append(f"metadata: misc_value_1 {effect.misc1} != 0")
    if effect.mechanic:
        out.append(f"metadata: exact effect mechanic {effect.mechanic}")
    if effect.radius0 or effect.radius1:
        out.append(f"metadata: radius indices {effect.radius0}/{effect.radius1}")
    if not effect.class_mask_is_empty:
        out.append("metadata: nonempty exact-effect class mask")
    if effect.attributes:
        out.append(f"metadata: exact-effect attributes {effect.attributes:#x}")
    return out


def spell_modifier_source_blockers(effect: SourceEffect) -> list[str]:
    """Mirrors: ``require_source`` x3 (passive_spell_modifier.rs:737-767).

    The spell-modifier compiler demands the same neutrality as the aura contract but
    pins neither the authored base points nor the misc values -- the role rule does
    that instead.
    """
    out: list[str] = []
    if effect.kind != APPLY_AURA:
        out.append(f"identity: effect kind {effect.kind} is not APPLY_AURA")
    if effect.period_ms != 0:
        out.append(f"identity: periodic application ({effect.period_ms} ms)")
    if effect.ap_coefficient != 0.0:
        out.append("amount: nonzero attack-power coefficient")
    if effect.bonus_coefficient != 0.0:
        out.append("amount: nonzero spell-power coefficient")
    out.extend(effect_amount_facts_blockers(effect))
    if not effect.chain_is_neutral():
        out.append("amount: nonneutral EffectChainFacts")
    if effect.trigger_spell:
        out.append(f"amount: exact effect triggers spell {effect.trigger_spell}")
    if (effect.target_a, effect.target_b) != (1, 0):
        out.append(f"metadata: implicit targets ({effect.target_a},{effect.target_b}) != (1, 0)")
    if effect.mechanic:
        out.append(f"metadata: exact effect mechanic {effect.mechanic}")
    if effect.radius0 or effect.radius1:
        out.append(f"metadata: radius indices {effect.radius0}/{effect.radius1}")
    if effect.attributes:
        out.append(f"metadata: exact-effect attributes {effect.attributes:#x}")
    return out


# ---------------------------------------------------------------------------
# owner shells
# ---------------------------------------------------------------------------

#: Every distinct owner shell a sole-effect authority uses, named for the comparison
#: table in ``G2-sole-authorities.md``.
OWNER_SHELLS = {
    "physical-complete": "neutral_physical_passive_modifier_owner_issue (exact_source.rs:238-244)",
    "neutral-selected": "bespoke, passive_haste.rs:249-282",
    "physical-neutral-selected": "bespoke, passive_absorb_received.rs:218-253",
    "spell-modifier": "validate_owner (passive_spell_modifier.rs:941-982)",
    "critical-auxiliary": "validate_no_auxiliary_critical_source_policy "
                          "(critical_modifier.rs:946-1000) + audit_source_attributes :919-943",
    "critical-sole-owner": "critical-auxiliary + validate_sole_critical_owner "
                           "(critical_modifier.rs:779-813) + audit_source_attributes :849-889",
}


def _passive_and_ignored_attribute_blockers(policy, *, label: str) -> list[str]:
    """Mirrors: the ``Passive`` + catalog-``Ignored`` attribute loop that five shells share
    (exact_source.rs:288-341, passive_haste.rs:284-326, passive_absorb_received.rs:255-...,
    passive_spell_modifier.rs:1223-1265)."""
    out: list[str] = []
    if not policy.is_passive:
        out.append(f"owner[{label}]: provider lacks the Passive attribute (raw 6)")
    for attribute in policy.attributes:
        if attribute == coreref.PASSIVE_ATTRIBUTE:
            continue
        support = coreref.attribute_support(attribute)
        if support == "unknown":
            out.append(f"owner[{label}]: attribute {attribute} is absent from Core's catalog")
        elif support != "ignored":
            out.append(f"owner[{label}]: attribute {attribute} support '{support}', not Ignored")
    return out


def _has(rows) -> bool:
    return bool(rows)


def _missile_is_meaningful(owners: Owners, spell: int) -> bool:
    """Whether the host would emit a ``SpellMissileInput`` row at all.

    ``owner.py`` classifies an all-zero missile as an inert class-A sidecar, so a
    meaningful row is exactly a nonzero speed/launch/minimum.
    """
    misc = owners.misc.get(spell) or {}
    return any(float(misc.get(key, 0) or 0) for key in ("Speed", "LaunchDelay", "MinDuration"))


def _sidecar_policy_blockers(owners: Owners, spell: int, *, label: str) -> list[str]:
    """Mirrors: ``passive_modifier_owner_policy_with_count_issue`` (exact_source.rs:437-475)
    and ``selected_trait_owner_policy_issue``'s ``Absent`` arm (selected_trait_package.rs:276-311).

    The owner-role count is a host fact; see the module docstring.
    """
    out: list[str] = []
    if _has(owners.aura_options.get(spell)):
        out.append(f"owner[{label}]: SpellAuraOptions row present")
    if _has(owners.equipped.get(spell)):
        out.append(f"owner[{label}]: SpellEquippedItems row present")
    if _missile_is_meaningful(owners, spell):
        out.append(f"owner[{label}]: meaningful SpellMissile timings")
    return out


def _neutral_identity_blockers(policy, *, label: str, physical: bool) -> list[str]:
    """The ``neutral`` conjunction five shells build from owner identity."""
    out: list[str] = []
    if physical and policy.school_mask != 1:
        out.append(f"owner[{label}]: SchoolMask {policy.school_mask} is not Physical(1)")
    if policy.defense_type:
        out.append(f"owner[{label}]: DefenseType {policy.defense_type}")
    if policy.dispel_type:
        out.append(f"owner[{label}]: DispelType {policy.dispel_type}")
    if policy.mechanic:
        out.append(f"owner[{label}]: spell Mechanic {policy.mechanic}")
    if any(policy.class_mask):
        out.append(f"owner[{label}]: nonempty owner spell class mask")
    return out


def _unconditional_application_blockers(owners: Owners, policy, *, label: str) -> list[str]:
    """Mirrors: ``supports_unconditional_passive_application_policy``
    (passive_spell_modifier.rs:1100-1109)."""
    out: list[str] = []
    if _has(owners.aura_restrictions.get(policy.spell)):
        out.append(f"owner[{label}]: SpellAuraRestrictions row present")
    if policy.duration_index:
        out.append(f"owner[{label}]: authored DurationIndex {policy.duration_index}")
    if _has(owners.shapeshift.get(policy.spell)):
        out.append(f"owner[{label}]: SpellShapeshift row present")
    return out


def owner_blockers(owners: Owners, resolved: Resolved, effect: SourceEffect,
                   shell: str, *, selector_is_class_mask: bool = False) -> list[str]:
    """Dispatch to one named owner shell.  ``shell`` is a key of :data:`OWNER_SHELLS`."""
    policy = owners.policy(resolved.spell)
    all_effect_mechanics = any(candidate.mechanic for candidate in resolved.effects)

    if shell == "physical-complete":
        out = _neutral_identity_blockers(policy, label=shell, physical=True)
        if all_effect_mechanics:
            out.append(f"owner[{shell}]: nonempty all-effect mechanic mask")
        out += _passive_and_ignored_attribute_blockers(policy, label=shell)
        out += _sidecar_policy_blockers(owners, resolved.spell, label=shell)
        return out

    if shell == "neutral-selected":
        out = _neutral_identity_blockers(policy, label=shell, physical=False)
        if all_effect_mechanics:
            out.append(f"owner[{shell}]: nonempty all-effect mechanic mask")
        out += _passive_and_ignored_attribute_blockers(policy, label=shell)
        out += _sidecar_policy_blockers(owners, resolved.spell, label=shell)
        return out

    if shell == "physical-neutral-selected":
        out = _neutral_identity_blockers(policy, label=shell, physical=True)
        if all_effect_mechanics:
            out.append(f"owner[{shell}]: nonempty all-effect mechanic mask")
        out += _passive_and_ignored_attribute_blockers(policy, label=shell)
        out += _sidecar_policy_blockers(owners, resolved.spell, label=shell)
        return out

    if shell == "spell-modifier":
        out = _neutral_identity_blockers(policy, label=shell, physical=False)
        if selector_is_class_mask and policy.family == 0:
            out.append(f"owner[{shell}]: class-mask selector needs a nonzero spell family")
        if all_effect_mechanics:
            out.append(f"owner[{shell}]: nonempty all-effect mechanic mask")
        out += _passive_and_ignored_attribute_blockers(policy, label=shell)
        out += _unconditional_application_blockers(owners, policy, label=shell)
        out += _sidecar_policy_blockers(owners, resolved.spell, label=shell)
        return out

    if shell in ("critical-auxiliary", "critical-sole-owner"):
        out: list[str] = []
        # validate_no_auxiliary_critical_source_policy (critical_modifier.rs:946-1000)
        if effect.radius0 or effect.radius1:
            out.append(f"owner[{shell}]: radius indices {effect.radius0}/{effect.radius1}")
        if _has(owners.aura_options.get(resolved.spell)):
            out.append(f"owner[{shell}]: SpellAuraOptions row present")
        if _has(owners.equipped.get(resolved.spell)):
            out.append(f"owner[{shell}]: SpellEquippedItems row present")
        if not policy.is_passive:
            out.append(f"owner[{shell}]: provider lacks the Passive attribute (raw 6)")
        if shell == "critical-sole-owner":
            hidden = _attribute_raw("Hidden")
            for attribute in policy.attributes:
                if attribute in (coreref.PASSIVE_ATTRIBUTE, hidden):
                    continue
                if coreref.attribute_support(attribute) == "unknown":
                    out.append(f"owner[{shell}]: attribute {attribute} absent from Core's catalog")
                else:
                    out.append(f"owner[{shell}]: attribute {attribute} is not Passive or Hidden")
            if policy.family == 0:
                out.append(f"owner[{shell}]: raw-308 source needs a nonzero spell family")
        else:
            unsupported = {_attribute_raw(name)
                           for name in CRITICAL_UNSUPPORTED_ATTRIBUTE_NAMES}
            for attribute in policy.attributes:
                if attribute in unsupported:
                    out.append(f"owner[{shell}]: attribute {attribute} is in "
                               f"UNSUPPORTED_SOURCE_ATTRIBUTES")
        return out

    raise FailClosed(f"unknown owner shell {shell!r}")


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuthorityRule:
    """One Core compiler branch that admits a sole-effect selected package."""

    subtype: int
    role: str
    compiler: str
    core_lines: str
    named_identity: dict | None
    #: The rank domain the branch accepts, as ``max_ranks`` values.
    rank_domain: tuple[int, ...]
    #: ``max_ranks -> required effect_point_operation`` (``None`` == unpointed,
    #: ``"*"`` == the branch imposes no requirement).
    shaping: dict[int, Any]
    #: Human-readable sign/range fact, for the comparison table.
    sign_rule: str
    owner_shell: str
    contract: AuraContract | None
    _check: Callable[[Resolved, SourceEffect, Owners], list[str]] = field(repr=False,
                                                                         default=None)

    def admits(self, resolved: Resolved, effect: SourceEffect) -> list[str]:
        """Blockers; empty means Core admits this sole-effect package."""
        return self._check(resolved, effect, _owners())

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtype": self.subtype,
            "role": self.role,
            "compiler": self.compiler,
            "core_lines": self.core_lines,
            "named_identity": self.named_identity,
            "rank_domain": list(self.rank_domain),
            "shaping": {str(k): v for k, v in sorted(self.shaping.items())},
            "sign_rule": self.sign_rule,
            "owner_shell": self.owner_shell,
            "owner_shell_core": OWNER_SHELLS[self.owner_shell],
            "contract": self.contract.to_dict() if self.contract else None,
        }


_OWNERS: Owners | None = None


def _owners() -> Owners:
    global _OWNERS
    if _OWNERS is None:
        _OWNERS = Owners()
    return _OWNERS


def set_owners(owners: Owners) -> None:
    """Injection point for tests; the census uses the lazily built default."""
    global _OWNERS
    _OWNERS = owners


def _rank_shape_blockers(resolved: Resolved, effect: SourceEffect,
                         allowed: dict[int, Any], *, fact: str) -> list[str]:
    """``operation_matches`` -- the ``match resolved.max_ranks()`` arm every
    amount-shaped branch writes out longhand."""
    if resolved.max_ranks not in allowed:
        return [f"{fact}: max_ranks {resolved.max_ranks} outside {sorted(allowed)}"]
    required = allowed[resolved.max_ranks]
    actual = resolved.point_operation(effect.index)
    if required == "*":
        return []
    if isinstance(required, tuple):
        if actual not in required:
            return [f"{fact}: point operation {actual!r} not in {list(required)} "
                    f"at max_ranks {resolved.max_ranks}"]
        return []
    if actual != required:
        return [f"{fact}: point operation {actual!r} != {required!r} "
                f"at max_ranks {resolved.max_ranks}"]
    return []


# -- 193 HasteAll ----------------------------------------------------------

def _haste_all(resolved: Resolved, effect: SourceEffect, owners: Owners) -> list[str]:
    """Mirrors: ``compile_definition`` (passive_haste.rs:106-195),
    ``validate_source`` :207-233, ``validate_selected_owner`` :249-282."""
    out = _rank_shape_blockers(resolved, effect,
                               {1: None, 2: "Set"}, fact="amount")
    rank_one = resolved.amount_at_rank(effect.index, 1)
    selected = resolved.selected_amount(effect.index)
    if rank_one is None:
        out.append("amount: no rank-one effect amount")
    elif not (_finite(rank_one) and rank_one > 0.0):
        out.append(f"amount: rank-one {rank_one!r} is not finite positive")
    if selected is None:
        out.append("amount: no effective-rank effect amount")
    elif not (_finite(selected) and selected > 0.0):
        out.append(f"amount: effective-rank {selected!r} is not finite positive")
    if resolved.max_ranks == 2:
        rank_two = resolved.amount_at_rank(effect.index, 2)
        if not (_finite(rank_two) and rank_two > 0.0):
            out.append(f"amount: rank-two {rank_two!r} is not finite positive")
    # validate_complete_source (passive_haste.rs:197-205) is the one-effect fact.
    if len(resolved.effects) != 1:
        out.append("source: provider does not declare exactly one effect")
    out += neutral_aura_blockers(effect, AuraContract(HASTE_ALL, rank_one))
    out += owner_blockers(owners, resolved, effect, "neutral-selected")
    return out


# -- 319 ModMeleeAutoAttackSpeedPercent (NAMED) -----------------------------

def _melee_speed(resolved: Resolved, effect: SourceEffect, owners: Owners) -> list[str]:
    """Mirrors: ``validate_selected_source`` (passive_melee_auto_attack_speed.rs:111-192)."""
    out: list[str] = []
    if resolved.entry_id != FURIOUS_BLOWS["entry"]:
        out.append(f"identity: entry {resolved.entry_id} != Furious Blows {FURIOUS_BLOWS['entry']}")
    if resolved.definition_id != FURIOUS_BLOWS["definition"]:
        out.append(f"identity: definition {resolved.definition_id} "
                   f"!= {FURIOUS_BLOWS['definition']}")
    if resolved.spell != FURIOUS_BLOWS["spell"]:
        out.append(f"identity: provider {resolved.spell} != {FURIOUS_BLOWS['spell']}")
    if effect.index != FURIOUS_BLOWS["effect_index"]:
        out.append(f"identity: effect index {effect.index} != 1")
    if resolved.max_ranks != 1:
        out.append(f"amount: max_ranks {resolved.max_ranks} != 1")
    if resolved.effective_rank != 1:
        out.append(f"amount: effective rank {resolved.effective_rank} != 1")
    if resolved.is_rank_shaped(effect.index):
        out.append("amount: rank-shaped source (Furious Blows must be unshaped)")
    selected = resolved.selected_amount(effect.index)
    if selected is None or _bits(selected) != _bits(FURIOUS_BLOWS["points"]):
        out.append(f"amount: effective-rank {selected!r} != exactly 5.0")
    elif _bits(effect.base_points) != _bits(selected):
        out.append("amount: authored base points differ from the selected amount")
    out += neutral_aura_blockers(
        effect,
        AuraContract(MOD_MELEE_AUTO_ATTACK_SPEED_PERCENT,
                     selected if selected is not None else FURIOUS_BLOWS["points"]))
    out += owner_blockers(owners, resolved, effect, "physical-complete")
    policy = owners.policy(resolved.spell)
    # supports_unconditional_passive_application_policy + the family-four name.
    out += _unconditional_application_blockers(owners, policy, label="physical-complete")
    if policy.family != FURIOUS_BLOWS["family"]:
        out.append(f"owner: spell family {policy.family} != Furious Blows family "
                   f"{FURIOUS_BLOWS['family']}")
    return out


# -- 79 / 87 school damage -------------------------------------------------

def _damage_done(resolved: Resolved, effect: SourceEffect, owners: Owners) -> list[str]:
    """Mirrors: ``compile_definition`` raw-79 arm (passive_damage_modifier.rs:197-215)."""
    out: list[str] = []
    authored = resolved.authored(effect.index)
    selected = resolved.selected_amount(effect.index)
    if resolved.max_ranks != 1:
        out.append(f"amount: max_ranks {resolved.max_ranks} != 1")
    if resolved.point_operation(effect.index) is not None:
        out.append(f"amount: point operation {resolved.point_operation(effect.index)!r} "
                   f"on a one-rank raw-79 source")
    if not (_finite(authored) and authored > 0.0):
        out.append(f"amount: authored {authored!r} is not finite positive")
    if selected is None or authored is None or _bits(selected) != _bits(authored):
        out.append("amount: effective-rank amount differs from the authored amount")
    out += _school_blockers(effect)
    out += neutral_aura_blockers(
        effect, AuraContract(MOD_DAMAGE_DONE_PERCENT, authored, misc_value_0=effect.misc0))
    out += owner_blockers(owners, resolved, effect, "physical-complete")
    return out


def _damage_taken(resolved: Resolved, effect: SourceEffect, owners: Owners) -> list[str]:
    """Mirrors: ``compile_definition`` raw-87 arm (passive_damage_modifier.rs:157-195)."""
    out = _rank_shape_blockers(resolved, effect, {1: None, 2: "Set"}, fact="amount")
    authored = resolved.authored(effect.index)
    rank_one = resolved.amount_at_rank(effect.index, 1)
    selected = resolved.selected_amount(effect.index)
    if rank_one is None:
        out.append("amount: no rank-one effect amount")
    for name, value in (("authored", authored), ("rank-one", rank_one),
                        ("effective-rank", selected)):
        if not (_finite(value) and -100.0 <= value <= 0.0):
            out.append(f"amount: {name} {value!r} outside finite [-100, 0]")
    if resolved.max_ranks == 2:
        rank_two = resolved.amount_at_rank(effect.index, 2)
        if not (_finite(rank_two) and -100.0 <= rank_two <= 0.0):
            out.append(f"amount: rank-two {rank_two!r} outside finite [-100, 0]")
    out += _school_blockers(effect)
    out += neutral_aura_blockers(
        effect, AuraContract(MOD_DAMAGE_TAKEN_PERCENT, authored, misc_value_0=effect.misc0))
    out += owner_blockers(owners, resolved, effect, "physical-complete")
    return out


def _school_blockers(effect: SourceEffect) -> list[str]:
    """Mirrors: ``validated_school_mask`` (damage_modifier.rs:595-608)."""
    raw = effect.misc0
    if raw == 0 or raw < 0 or raw > 255 or raw & ~KNOWN_SCHOOL_BITS:
        return [f"metadata: misc_value_0 {raw} is not a nonzero known school mask"]
    return []


# -- 118 ModHealingReceivedPercent -----------------------------------------

def _healing_received(resolved: Resolved, effect: SourceEffect, owners: Owners) -> list[str]:
    """Mirrors: ``compile_definition`` (passive_healing_received.rs:126-224), sole-effect arm."""
    out: list[str] = []
    if resolved.max_ranks != 1:
        out.append(f"amount: max_ranks {resolved.max_ranks} != 1 "
                   f"(the sole-effect arm is one-rank only)")
    authored = resolved.authored(effect.index)
    selected = resolved.selected_amount(effect.index)
    if not (_finite(authored) and authored >= 0.0):
        out.append(f"amount: authored {authored!r} is not finite nonnegative")
    if not (_finite(selected) and selected >= 0.0):
        out.append(f"amount: effective-rank {selected!r} is not finite nonnegative")
    out += neutral_aura_blockers(
        effect, AuraContract(MOD_HEALING_RECEIVED_PERCENT, authored,
                             misc_value_0=ALL_SCHOOLS_MASK))
    out += owner_blockers(owners, resolved, effect, "physical-complete")
    return out


# -- 422 ModAbsorbReceivedPercent ------------------------------------------

def _absorb_received(resolved: Resolved, effect: SourceEffect, owners: Owners) -> list[str]:
    """Mirrors: ``compile_definition`` (passive_absorb_received.rs:124-181)."""
    out = _rank_shape_blockers(resolved, effect, {2: "Set"}, fact="amount")
    rank_one = resolved.amount_at_rank(effect.index, 1)
    selected = resolved.selected_amount(effect.index)
    if rank_one is None:
        out.append("amount: no rank-one effect amount")
    if selected is None:
        out.append("amount: no effective-rank effect amount")
    elif not (_finite(selected) and selected >= -100.0):
        out.append(f"amount: effective-rank {selected!r} is not finite and >= -100")
    out += neutral_aura_blockers(effect, AuraContract(MOD_ABSORB_RECEIVED_PERCENT, rank_one))
    out += owner_blockers(owners, resolved, effect, "physical-neutral-selected")
    return out


# -- 290 / 308 critical chance ---------------------------------------------

def _critical(subtype: int):
    """Mirrors: ``selected_percent_points`` else-arm (critical_modifier.rs:519-528) plus the
    generic ``validate_source`` (critical_modifier.rs:602-777) and ``validate_activation``
    SelectedTrait arm (critical_modifier.rs:1059-1094)."""

    def check(resolved: Resolved, effect: SourceEffect, owners: Owners) -> list[str]:
        out: list[str] = []
        # -- the selected branch itself: sole effect, finite amount.  No rank domain,
        #    no point-operation requirement and no sign constraint exist here.
        selected = resolved.selected_amount(effect.index)
        if selected is None:
            out.append("amount: no effective-rank effect amount")
        elif not _finite(selected):
            out.append(f"amount: effective-rank {selected!r} is not finite")

        # -- validate_source, shared with non-selected carriers
        if effect.kind != APPLY_AURA:
            out.append(f"identity: effect kind {effect.kind} is not APPLY_AURA")
        out += effect_amount_facts_blockers(effect)
        if effect.aura != subtype:
            out.append(f"identity: aura subtype {effect.aura} != {subtype}")
        if effect.period_ms != 0:
            out.append(f"identity: periodic application ({effect.period_ms} ms)")
        if effect.trigger_spell:
            out.append(f"amount: exact effect triggers spell {effect.trigger_spell}")
        if effect.misc0 != 0 or effect.misc1 != 0:
            out.append(f"metadata: misc values ({effect.misc0},{effect.misc1}) != (0, 0)")
        if effect.mechanic:
            out.append(f"metadata: exact effect mechanic {effect.mechanic}")
        if not effect.chain_is_neutral():
            out.append("amount: nonneutral EffectChainFacts")
        if effect.ap_coefficient != 0.0 or effect.bonus_coefficient != 0.0:
            out.append("amount: nonzero power coefficients")
        if (effect.target_a, effect.target_b) != (1, 0):
            out.append(f"metadata: implicit targets ({effect.target_a},{effect.target_b}) "
                       f"!= (1, 0)")

        if subtype == CRIT_CHANCE_FOR_CASTER_WITH_ABILITIES:
            if effect.class_mask_is_empty:
                out.append("metadata: raw-308 requires a nonempty exact-effect class mask")
            if effect.attributes:
                out.append(f"metadata: raw-308 requires empty exact-effect attributes "
                           f"({effect.attributes:#x})")
            if len(resolved.effects) != 1:
                out.append("source: raw-308 requires a sole-effect owner topology")
            out += owner_blockers(owners, resolved, effect, "critical-sole-owner")
        else:
            if not effect.class_mask_is_empty:
                out.append("metadata: raw-290 requires an empty exact-effect class mask")
            unknown = effect.attributes & ~_known_effect_attribute_mask()
            if unknown:
                out.append(f"metadata: uncataloged exact-effect attribute bits {unknown:#x}")
            elif effect.attributes & ~POINT_STACKING_TOLERATED_MASK:
                out.append(f"metadata: exact-effect attributes {effect.attributes:#x} "
                           f"require behavior (compile_effect_point_stacking refuses them)")
            out += owner_blockers(owners, resolved, effect, "critical-auxiliary")
        return out

    return check


# -- 107 / 108 / 218 / 219 spell modifiers ---------------------------------

def _spell_modifier_value(resolved: Resolved, effect: SourceEffect) -> tuple[str | None, list[str]]:
    """Mirrors: ``generic_spell_modifier`` (selected_trait_package.rs:860-915) and the two
    two-rank gates ``supports_ranked_direct_source`` / ``supports_ranked_flat_cooldown_source``
    (passive_spell_modifier.rs:1037-1096).

    Returns ``(value_kind, blockers)``; ``value_kind`` names the
    ``SelectedSpellModifierValue`` variant so the caller can apply
    ``spell_modifier_amount_is_valid`` (passive_spell_modifier.rs:823-835).
    """
    subtype, operation = effect.aura, effect.misc0
    selected = resolved.selected_amount(effect.index)

    # Which structural branch can even see this effect?
    if resolved.max_ranks == 1:
        branch = "single_rank_contract fallback (selected_trait_package.rs:456-474)"
    elif resolved.max_ranks == 2:
        if subtype == ADD_PERCENT_MODIFIER and operation == OP_HEALING_AND_DAMAGE:
            branch = "ranked_direct_contract (selected_trait_package.rs:606-624)"
        elif subtype == ADD_FLAT_MODIFIER and operation == OP_COOLDOWN:
            branch = "ranked_cooldown_contract (selected_trait_package.rs:626-638)"
        else:
            return None, [f"structure: no two-rank sole-effect contract for "
                          f"subtype {subtype} operation {operation}"]
    else:
        return None, [f"structure: max_ranks {resolved.max_ranks} has no package contract"]

    out: list[str] = []
    if resolved.max_ranks == 2:
        # Both two-rank gates demand a pointed row and both rank values.
        operation_row = resolved.point_operation(effect.index)
        if operation_row not in ("Set", "Multiply"):
            out.append(f"amount: {branch} needs a Set|Multiply point row, got {operation_row!r}")
        if subtype == ADD_PERCENT_MODIFIER:
            for rank in (1, 2):
                value = resolved.amount_at_rank(effect.index, rank)
                if not (_finite(value) and value >= -100.0):
                    out.append(f"amount: rank-{rank} {value!r} is not finite and >= -100")
        else:
            for rank in (1, 2):
                milliseconds = _exact_i32(resolved.amount_at_rank(effect.index, rank))
                if milliseconds is None or milliseconds >= 0:
                    out.append(f"amount: rank-{rank} "
                               f"{resolved.amount_at_rank(effect.index, rank)!r} is not a "
                               f"negative integral i32 millisecond count")

    # -- generic_spell_modifier's (subtype, operation) dispatch
    if subtype == ADD_PERCENT_MODIFIER and operation in (
            OP_HEALING_AND_DAMAGE, OP_PERIODIC_HEALING_AND_DAMAGE, OP_COOLDOWN):
        kind = "Percent/Cooldown" if operation == OP_COOLDOWN else "Percent"
        out += _class_mask_selector_blockers(effect, require_nonempty=True)
        if not _finite(selected):
            out.append(f"amount: effective-rank {selected!r} is not finite")
        elif kind == "Percent/Cooldown":
            if not (-100.0 <= selected < 0.0):
                out.append(f"amount: cooldown percent {selected!r} outside [-100, 0)")
        elif selected < -100.0:
            out.append(f"amount: percent {selected!r} below -100")
        return kind, out

    if subtype == ADD_FLAT_MODIFIER and operation in (OP_POINTS_INDEX_0, OP_COOLDOWN):
        out += _class_mask_selector_blockers(effect, require_nonempty=True)
        points = _exact_i32(selected)
        if points is None:
            out.append(f"amount: effective-rank {selected!r} is not an exact i32")
        elif operation == OP_COOLDOWN and points >= 0:
            out.append(f"amount: flat cooldown {points} is not negative")
        return ("FlatCooldown" if operation == OP_COOLDOWN else "FlatFirstEffect"), out

    if subtype == ADD_PERCENT_LABEL_MODIFIER and operation == OP_PERIODIC_HEALING_AND_DAMAGE:
        out += _label_selector_blockers(effect)
        if not _finite(selected):
            out.append(f"amount: effective-rank {selected!r} is not finite")
        elif selected < -100.0:
            out.append(f"amount: percent {selected!r} below -100")
        return "Percent", out

    if subtype == ADD_FLAT_LABEL_MODIFIER and operation == OP_POINTS_INDEX_0:
        out += _label_selector_blockers(effect)
        if _exact_i32(selected) is None:
            out.append(f"amount: effective-rank {selected!r} is not an exact i32")
        return "FlatFirstEffect", out

    out.append(f"structure: generic_spell_modifier has no arm for "
               f"(subtype {subtype}, operation {operation})")
    return None, out


def _class_mask_selector_blockers(effect: SourceEffect, *, require_nonempty: bool) -> list[str]:
    """``percent_class_mask`` / ``flat_class_mask`` (selected_trait_package.rs:917-938, 1031-1058)."""
    out: list[str] = []
    if effect.misc1 != 0:
        out.append(f"metadata: class-mask selector needs misc_value_1 == 0, got {effect.misc1}")
    if require_nonempty and effect.class_mask_is_empty:
        out.append("metadata: class-mask selector needs a nonempty exact-effect class mask")
    return out


def _label_selector_blockers(effect: SourceEffect) -> list[str]:
    """``percent_any_label`` / ``flat_first_effect_label`` (selected_trait_package.rs:960-1029)."""
    out: list[str] = []
    if effect.misc1 <= 0:
        out.append(f"metadata: label selector needs a positive misc_value_1, got {effect.misc1}")
    if not effect.class_mask_is_empty:
        out.append("metadata: label selector needs an empty exact-effect class mask")
    return out


def _spell_modifier(subtype: int):
    def check(resolved: Resolved, effect: SourceEffect, owners: Owners) -> list[str]:
        if effect.aura != subtype:
            return [f"identity: aura subtype {effect.aura} != {subtype}"]
        kind, out = _spell_modifier_value(resolved, effect)
        out = list(out)
        out += spell_modifier_source_blockers(effect)
        out += owner_blockers(owners, resolved, effect, "spell-modifier",
                              selector_is_class_mask=(effect.aura in (ADD_FLAT_MODIFIER,
                                                                      ADD_PERCENT_MODIFIER)))
        if kind is None and not out:
            out.append("fail-closed: no spell-modifier value could be derived")
        return out

    return check


# ---------------------------------------------------------------------------
# the catalog
# ---------------------------------------------------------------------------

SOLE_AUTHORITIES: dict[int, AuthorityRule] = {
    MOD_DAMAGE_DONE_PERCENT: AuthorityRule(
        subtype=MOD_DAMAGE_DONE_PERCENT, role="PassiveDamageModifier/OutgoingHolder",
        compiler="passive_damage_modifier.rs", core_lines="114-266 (raw-79 arm 197-215)",
        named_identity=None, rank_domain=(1,), shaping={1: None},
        sign_rule="authored finite > 0; effective rank == authored bitwise",
        owner_shell="physical-complete",
        contract=AuraContract(MOD_DAMAGE_DONE_PERCENT, None, misc_value_0=None),
        _check=_damage_done,
    ),
    MOD_DAMAGE_TAKEN_PERCENT: AuthorityRule(
        subtype=MOD_DAMAGE_TAKEN_PERCENT, role="PassiveDamageModifier/IncomingHolder",
        compiler="passive_damage_modifier.rs", core_lines="114-266 (raw-87 arm 157-195)",
        named_identity=None, rank_domain=(1, 2), shaping={1: None, 2: "Set"},
        sign_rule="authored, rank-one, rank-two and effective rank all finite in [-100, 0]",
        owner_shell="physical-complete",
        contract=AuraContract(MOD_DAMAGE_TAKEN_PERCENT, None, misc_value_0=None),
        _check=_damage_taken,
    ),
    ADD_FLAT_MODIFIER: AuthorityRule(
        subtype=ADD_FLAT_MODIFIER, role="SpellModifier/Flat",
        compiler="passive_spell_modifier.rs",
        core_lines="709-772, 847-912, 1068-1096; selected_trait_package.rs:456-474, 626-638",
        named_identity=None, rank_domain=(1, 2),
        shaping={1: None, 2: ("Set", "Multiply")},
        sign_rule="operation 3: any exact i32; operation 11: exact i32 < 0 "
                  "(two-rank: at both ranks)",
        owner_shell="spell-modifier", contract=None, _check=_spell_modifier(ADD_FLAT_MODIFIER),
    ),
    ADD_PERCENT_MODIFIER: AuthorityRule(
        subtype=ADD_PERCENT_MODIFIER, role="SpellModifier/Percent",
        compiler="passive_spell_modifier.rs",
        core_lines="709-772, 847-912, 1037-1064; selected_trait_package.rs:456-474, 606-624",
        named_identity=None, rank_domain=(1, 2),
        shaping={1: None, 2: ("Set", "Multiply")},
        sign_rule="operations 0/22: finite >= -100; operation 11: [-100, 0) "
                  "(two-rank: >= -100 at both ranks)",
        owner_shell="spell-modifier", contract=None, _check=_spell_modifier(ADD_PERCENT_MODIFIER),
    ),
    MOD_HEALING_RECEIVED_PERCENT: AuthorityRule(
        subtype=MOD_HEALING_RECEIVED_PERCENT, role="HealingReceived",
        compiler="passive_healing_received.rs", core_lines="126-224",
        named_identity=None, rank_domain=(1,), shaping={1: "*"},
        sign_rule="authored and effective rank finite >= 0",
        owner_shell="physical-complete",
        contract=AuraContract(MOD_HEALING_RECEIVED_PERCENT, None,
                              misc_value_0=ALL_SCHOOLS_MASK),
        _check=_healing_received,
    ),
    HASTE_ALL: AuthorityRule(
        subtype=HASTE_ALL, role="PassiveHasteAll",
        compiler="passive_haste.rs", core_lines="106-195",
        named_identity=None, rank_domain=(1, 2), shaping={1: None, 2: "Set"},
        sign_rule="rank-one, rank-two and effective rank all finite > 0",
        owner_shell="neutral-selected",
        contract=AuraContract(HASTE_ALL, None), _check=_haste_all,
    ),
    ADD_PERCENT_LABEL_MODIFIER: AuthorityRule(
        subtype=ADD_PERCENT_LABEL_MODIFIER, role="SpellModifier/Percent/Label",
        compiler="passive_spell_modifier.rs",
        core_lines="709-772, 847-912; selected_trait_package.rs:456-474, 960-987",
        named_identity=None, rank_domain=(1,), shaping={1: None},
        sign_rule="operation 22 only; finite >= -100",
        owner_shell="spell-modifier", contract=None,
        _check=_spell_modifier(ADD_PERCENT_LABEL_MODIFIER),
    ),
    ADD_FLAT_LABEL_MODIFIER: AuthorityRule(
        subtype=ADD_FLAT_LABEL_MODIFIER, role="SpellModifier/FlatFirstEffect/Label",
        compiler="passive_spell_modifier.rs",
        core_lines="709-772, 847-912; selected_trait_package.rs:456-474, 1010-1029",
        named_identity=None, rank_domain=(1,), shaping={1: None},
        sign_rule="operation 3 only; any exact i32",
        owner_shell="spell-modifier", contract=None,
        _check=_spell_modifier(ADD_FLAT_LABEL_MODIFIER),
    ),
    MOD_ALL_CRIT_CHANCE: AuthorityRule(
        subtype=MOD_ALL_CRIT_CHANCE, role="CriticalChanceModifier/SourceSpellAndWeapon",
        compiler="critical_modifier.rs", core_lines="470-529 (else-arm 519-528)",
        named_identity=None, rank_domain=(1, 2), shaping={1: "*", 2: "*"},
        sign_rule="finite only -- no sign or range constraint at any rank",
        owner_shell="critical-auxiliary", contract=None,
        _check=_critical(MOD_ALL_CRIT_CHANCE),
    ),
    CRIT_CHANCE_FOR_CASTER_WITH_ABILITIES: AuthorityRule(
        subtype=CRIT_CHANCE_FOR_CASTER_WITH_ABILITIES,
        role="CriticalChanceModifier/TargetCasterSpellMagic",
        compiler="critical_modifier.rs", core_lines="470-529 (else-arm 519-528)",
        named_identity=None, rank_domain=(1, 2), shaping={1: "*", 2: "*"},
        sign_rule="finite only -- no sign or range constraint at any rank",
        owner_shell="critical-sole-owner", contract=None,
        _check=_critical(CRIT_CHANCE_FOR_CASTER_WITH_ABILITIES),
    ),
    MOD_MELEE_AUTO_ATTACK_SPEED_PERCENT: AuthorityRule(
        subtype=MOD_MELEE_AUTO_ATTACK_SPEED_PERCENT, role="MeleeAutoAttackSpeed",
        compiler="passive_melee_auto_attack_speed.rs", core_lines="111-192",
        named_identity=dict(FURIOUS_BLOWS), rank_domain=(1,), shaping={1: None},
        sign_rule="effective rank bitwise == 5.0, and authored == effective rank",
        owner_shell="physical-complete",
        contract=AuraContract(MOD_MELEE_AUTO_ATTACK_SPEED_PERCENT, FURIOUS_BLOWS["points"]),
        _check=_melee_speed,
    ),
    MOD_ABSORB_RECEIVED_PERCENT: AuthorityRule(
        subtype=MOD_ABSORB_RECEIVED_PERCENT, role="PassiveAbsorbReceived",
        compiler="passive_absorb_received.rs", core_lines="124-181",
        named_identity=None, rank_domain=(2,), shaping={2: "Set"},
        sign_rule="effective rank finite >= -100 (no ceiling); ranks 1/2 unconstrained "
                  "beyond the authored == rank-one contract",
        owner_shell="physical-neutral-selected",
        contract=AuraContract(MOD_ABSORB_RECEIVED_PERCENT, None), _check=_absorb_received,
    ),
}

#: Subtypes that appear in a selected branch but have **no** sole-effect surface,
#: with the Core reason.  Kept here so the census can separate "unclaimed" from
#: "claimed but package-only".
PACKAGE_ONLY_SUBTYPES = {
    334: "critical_modifier.rs:488-518 routes ModAutoAttackCritChance through "
         "selected_trait_package_contract",
    344: "passive_auto_attack_damage.rs:171-172 always demands the package contract",
    638: "critical_block_amount.rs has no selected branch; reachable only as Martial "
         "Expert's second effect (critical_block_amount.rs:254-284)",
}

#: Subtypes whose compiler has a selected branch that a selected activation can never
#: reach: ``validate_activation`` (critical_modifier.rs:1059-1070) admits only 290/308/334.
UNREACHABLE_SELECTED_SUBTYPES = {
    52: "critical_modifier.rs:1059-1070 refuses a SelectedTrait activation for raw 52",
    57: "critical_modifier.rs:1059-1070 refuses a SelectedTrait activation for raw 57",
    183: "critical_modifier.rs:1059-1070 refuses a SelectedTrait activation for raw 183",
    187: "critical_modifier.rs:1059-1070 refuses a SelectedTrait activation for raw 187",
    197: "critical_modifier.rs:1059-1070 refuses a SelectedTrait activation for raw 197",
}


def admit_sole_effect(resolved: Resolved) -> tuple[str | None, list[str]]:
    """``(role, blockers)`` for a resolved SINGLE-effect selected package.

    ``role`` is non-``None`` only when ``blockers`` is empty.  A package that is not
    sole-effect, or whose subtype has no Core authority, returns ``(None, [reason])``
    -- unclassified is a first-class result and never a silent pass.
    """
    if len(resolved.effects) != 1:
        return None, [f"structure: {len(resolved.effects)}-effect package is not sole-effect"]
    effect = resolved.effects[0]
    rule = SOLE_AUTHORITIES.get(effect.aura)
    if rule is None:
        reason = (PACKAGE_ONLY_SUBTYPES.get(effect.aura)
                  or UNREACHABLE_SELECTED_SUBTYPES.get(effect.aura))
        if reason:
            return None, [f"no-sole-authority: subtype {effect.aura} -- {reason}"]
        return None, [f"no-sole-authority: subtype {effect.aura} has no Core "
                      f"selected-passive authority at all"]
    blockers = rule.admits(resolved, effect)
    return (rule.role, []) if not blockers else (None, sorted(set(blockers)))
