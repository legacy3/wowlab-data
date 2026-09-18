"""Per-effect semantic classification, from semantic facts only.

Mirrors: ``SelectedTraitEffectRole`` / ``SelectedSpellModifierValue`` /
``SelectedSpellModifierOperation`` / ``SelectedSpellModifierSelector``
(``core/crates/combat/src/program/selected_trait_package.rs``).

The rule here never reads an entry, definition or spell id.  A classification is a
function of:

    (effect kind, aura subtype, misc0, misc1, class-mask emptiness, period,
     trigger, mechanic, radii, implicit targets, coefficients, chain shaping,
     exact-effect attributes)

and nothing else.  An effect that no rule matches is ``unclassified`` and carries
the exact missing semantic -- it is never quietly dropped, because "every effect I
happened to recognise is supported" is precisely the failure mode this pass exists
to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .provenance import Resolved, SourceEffect

#: ``EffectKind::APPLY_AURA``.
APPLY_AURA = 6

#: ``AuraSubtypeKind`` raws Core names in the selected paths (crates/dbc/src/aura_subtype.rs).
ADD_FLAT_MODIFIER = 107
ADD_PERCENT_MODIFIER = 108
MOD_HEALING_RECEIVED_PERCENT = 118
ADD_PERCENT_LABEL_MODIFIER = 218
ADD_FLAT_LABEL_MODIFIER = 219
MOD_AUTO_ATTACK_CRIT_CHANCE = 334
MOD_AUTO_ATTACK_DAMAGE = 344
MOD_CRITICAL_BLOCK_AMOUNT = 638

#: ``SpellSchoolMask`` with every school set.
ALL_SCHOOLS_MASK = 127

#: Roles whose ``EffectMiscValue_0`` is a ``SpellSchoolMask`` SELECTOR rather than a fixed
#: neutral value.  A damage-taken or absorb-received modifier legitimately applies to a
#: subset of schools, so the mask is recorded as part of the role, never required to be 127.
#: raw-118 is deliberately NOT in this set: Core's received-healing contract requires the
#: all-schools mask exactly (``passive_healing_received.rs``, ``misc_value_0: 127``).
#: raw-422 is deliberately excluded: `SPELL_AURA_MOD_ABSORB_RECEIVED_PCT` has **no
#: consumer anywhere in TrinityCore's `src/server/game`**, so there is no evidence its
#: misc value is a school mask at all.  The only admitted witness (provider 391571)
#: carries 0, and `passive_absorb_received.rs` never reads the field, so it is treated as
#: a neutral value that must be zero -- fail closed rather than invent a selector.
SCHOOL_SELECTOR_ROLES = frozenset({79, 87})

#: Aura subtypes that reach a selected-passive role, with the pinned consumer that
#: reads their value.  Every one dispatches to ``AuraEffect::HandleNoImmediateEffect``
#: -- application itself does nothing and the value is read at the consumption site --
#: so none of them reads ``SpellEffect.EffectTriggerSpell``.
#:
#: Mirrors: ``AuraEffectHandlerTable`` (TrinityCore SpellAuraEffects.cpp:179, 180, 190,
#: 290, 291, 406, 416, 710).
TRIGGER_INERT_SUBTYPES = {
    ADD_FLAT_MODIFIER: "AuraEffect::CalculateSpellMod (SpellAuraEffects.cpp:179)",
    ADD_PERCENT_MODIFIER: "AuraEffect::CalculateSpellMod (SpellAuraEffects.cpp:180)",
    MOD_HEALING_RECEIVED_PERCENT: "Unit::SpellHealingBonus (SpellAuraEffects.cpp:190)",
    ADD_PERCENT_LABEL_MODIFIER: "AuraEffect::CalculateSpellMod (SpellAuraEffects.cpp:290)",
    ADD_FLAT_LABEL_MODIFIER: "AuraEffect::CalculateSpellMod (SpellAuraEffects.cpp:291)",
    MOD_AUTO_ATTACK_CRIT_CHANCE: "Unit::RollMeleeOutcomeAgainst (SpellAuraEffects.cpp:406)",
    MOD_AUTO_ATTACK_DAMAGE: "Unit::MeleeDamageBonusDone (SpellAuraEffects.cpp:416)",
    MOD_CRITICAL_BLOCK_AMOUNT: "Unit::CalculateMeleeDamage (SpellAuraEffects.cpp:710)",
    87: "Unit::MeleeDamageBonus / Unit::SpellDamageBonus (SpellAuraEffects.cpp:159)",
    308: "Unit::GetUnitSpellCriticalChance (SpellAuraEffects.cpp:380)",
    422: "Unit::SpellAbsorbBonusTaken (SpellAuraEffects.cpp:494)",
}

#: Deliberately NOT trigger-inert, although they are selected roles: raw 79
#: (``HandleModDamagePercentDone``, :151), 193 (``HandleModCombatSpeedPct``, :265), 290
#: (``HandleAuraModCritPct``, :362) and 319 (``HandleModMeleeSpeedPct``, :391) have real
#: application handlers, so the "application does nothing" argument does not apply to them
#: and a live ``EffectTriggerSpell`` stays a blocker.  Fail closed.

#: ``SpellModOp`` values Core names, by ``EffectMiscValue_0``.
OP_HEALING_AND_DAMAGE = 0
OP_POINTS_INDEX_0 = 3
OP_COOLDOWN = 11
OP_CRIT_DAMAGE_AND_HEALING_BONUS = 15
OP_PERIODIC_HEALING_AND_DAMAGE = 22


@dataclass(frozen=True)
class Role:
    """One ordinary Core semantic authority that may own an exact effect."""

    name: str                       # SelectedTraitEffectRole variant
    value: str | None = None        # SelectedSpellModifierValue variant
    operation: str | None = None    # SelectedSpellModifierOperation
    selector: str | None = None     # "ClassMask" | "Label"
    label: int | None = None
    #: Whether Core can reach this role WITHOUT a named-package branch, i.e. through
    #: ``generic_spell_modifier`` (selected_trait_package.rs:846-913).
    generic_in_core: bool = False

    def key(self) -> str:
        parts = [self.name]
        if self.value:
            parts.append(self.value)
        if self.operation:
            parts.append(self.operation)
        if self.selector:
            parts.append(self.selector)
        return "/".join(parts)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.name, "key": self.key(),
                               "generic_in_core": self.generic_in_core}
        for field_name in ("value", "operation", "selector", "label"):
            value = getattr(self, field_name)
            if value is not None:
                out[field_name] = value
        return out


@dataclass
class Classification:
    """The verdict for one exact effect, plus everything it was decided from."""

    effect: SourceEffect
    role: Role | None
    blockers: list[str] = field(default_factory=list)
    #: Facts that were required and held; recorded so a reviewer can attack them.
    checked: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.role is not None and not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect.ref,
            "index": self.effect.index,
            "facts": effect_facts(self.effect),
            "role": self.role.to_dict() if self.role else None,
            "blockers": sorted(self.blockers),
            "checked": sorted(self.checked),
        }


def effect_facts(effect: SourceEffect) -> dict[str, Any]:
    """The complete semantic census row for one exact effect."""
    return {
        "kind": effect.kind,
        "aura_subtype": effect.aura,
        "misc_0": effect.misc0,
        "misc_1": effect.misc1,
        "class_mask": list(effect.class_mask),
        "class_mask_empty": effect.class_mask_is_empty,
        "period_ms": effect.period_ms,
        "amplitude": effect.amplitude,
        "trigger_spell": effect.trigger_spell,
        "mechanic": effect.mechanic,
        "radius_0": effect.radius0,
        "radius_1": effect.radius1,
        "implicit_targets": [effect.target_a, effect.target_b],
        "chain_targets": effect.chain_targets,
        "chain_amplitude": effect.chain_amplitude,
        "exact_effect_attributes": effect.attributes,
        "item_type": effect.item_type,
        "pos_facing": effect.pos_facing,
        "scaling_class": effect.scaling_class,
        "coefficients": {
            "base_points": effect.base_points,
            "bonus": effect.bonus_coefficient,
            "attack_power": effect.ap_coefficient,
            "coefficient": effect.coefficient,
            "variance": effect.variance,
            "resource": effect.resource_coefficient,
            "group_size": effect.group_size_coefficient,
            "pvp_multiplier": effect.pvp_multiplier,
            "points_per_resource": effect.points_per_resource,
            "real_points_per_level": effect.real_points_per_level,
        },
    }


# ---------------------------------------------------------------------------
# neutrality: the shell every selected exact effect must satisfy
# ---------------------------------------------------------------------------

def neutral_blockers(effect: SourceEffect, *, expect_self_delivery: bool = True) -> list[str]:
    """Facts Core requires of every admitted selected exact effect.

    Mirrors: ``basic_modifier_source`` (selected_trait_package.rs:1055-1069),
    ``exact_neutral_aura_source_issue`` (exact_source.rs:178-231) and
    ``exact_heart_auto_critical_source`` (selected_trait_package.rs:571-602).
    """
    blockers = []
    if effect.kind != APPLY_AURA:
        blockers.append(f"effect kind {effect.kind} is not APPLY_AURA")
    if effect.period_ms != 0:
        blockers.append(f"periodic exact effect (period {effect.period_ms} ms)")
    # EffectTriggerSpell is consumed only by subtypes whose own semantics read it
    # (periodic-trigger, proc-trigger, trigger-on-expire/health/power) and, for
    # non-aura effects, by SpellInfo.cpp:5041 `if (!effect.ApplyAuraName && effect.TriggerSpell)`.
    # For the subtypes that reach a selected role it is dead data in the pinned
    # consumer, so it is a blocker only elsewhere.  Caveat: no Trinity consumer is not
    # proof of no Retail behaviour -- see the report's cross-build discipline section.
    if effect.trigger_spell and effect.aura not in TRIGGER_INERT_SUBTYPES:
        blockers.append(f"exact effect triggers spell {effect.trigger_spell} "
                        f"and subtype {effect.aura} has no proved trigger-inert consumer")
    if effect.mechanic:
        blockers.append(f"exact effect mechanic {effect.mechanic}")
    if effect.radius0 or effect.radius1:
        blockers.append(f"nonzero effect radii {effect.radius0}/{effect.radius1}")
    if not effect.chain_is_neutral():
        blockers.append("nonneutral chain shaping")
    if not effect.coefficients_are_neutral():
        blockers.append("nonneutral amount/power coefficients")
    # PvpMultiplier is loaded by Trinity (DB2Structure.h:3914, SpellMgr.cpp:2777) but has
    # no gameplay consumer anywhere in src/server/game -- it is inert for a PvE sim.  Core
    # is inconsistent about it: EffectAmountFacts::NEUTRAL rejects it for the non-modifier
    # roles while basic_modifier_source never looks at it.  Recorded, never blocking.
    if effect.attributes:
        blockers.append(f"exact-effect attributes {effect.attributes}")
    if expect_self_delivery and (effect.target_a, effect.target_b) != (1, 0):
        blockers.append(f"implicit targets {effect.target_a}/{effect.target_b} are not self-only")
    return blockers


# ---------------------------------------------------------------------------
# role table -- keyed only by semantic facts
# ---------------------------------------------------------------------------

#: The spell-property modifier space, as Core's own representation defines it.
#:
#: ``SelectedSpellModifierValue`` (selected_trait_package.rs:95-140) is a FREE CROSS PRODUCT:
#: every value variant carries an independent ``selector``, and ``Percent`` an independent
#: ``operation``.  The translation to the downstream authority
#: (``passive_spell_modifier.rs:776-777``) maps the selector generically for every variant,
#: and applicability consumes both selectors uniformly (``:698-702``).
#:
#: Core's *classifier* is narrower than its representation: ``generic_spell_modifier``
#: handles only the (subtype, operation) pairs its named branches happened to need.  This
#: table deliberately restores the cross product, because a candidate generic rule is not
#: allowed to inherit a restriction that the target representation does not have.  Hostile
#: review 2 found the cost of the restriction: 18 owner-clean packages refused by nothing
#: else (14 on raw-219 operation 11, 4 on raw-218 operation 15).
#:
#: subtype -> (value family, selector)
_MODIFIER_SUBTYPES = {
    ADD_PERCENT_MODIFIER: ("Percent", "ClassMask"),
    ADD_FLAT_MODIFIER: ("Flat", "ClassMask"),
    ADD_PERCENT_LABEL_MODIFIER: ("Percent", "Label"),
    ADD_FLAT_LABEL_MODIFIER: ("Flat", "Label"),
}

#: ``SpellModOp`` -> the payload value stage it selects.
_MODIFIER_OPERATIONS = {
    OP_HEALING_AND_DAMAGE: "Direct",
    OP_PERIODIC_HEALING_AND_DAMAGE: "Periodic",
    OP_COOLDOWN: "Cooldown",
    OP_CRIT_DAMAGE_AND_HEALING_BONUS: "CriticalBonus",
    OP_POINTS_INDEX_0: "FirstEffect",
}

#: The (subtype, operation) cells ``generic_spell_modifier`` reaches without a named branch
#: (selected_trait_package.rs:846-913).  Used only to report how much of the cross product
#: Core's classifier currently covers -- never to restrict admission.
_GENERIC_IN_CORE = {
    (ADD_PERCENT_MODIFIER, OP_HEALING_AND_DAMAGE),
    (ADD_PERCENT_MODIFIER, OP_PERIODIC_HEALING_AND_DAMAGE),
    (ADD_PERCENT_MODIFIER, OP_COOLDOWN),
    (ADD_FLAT_MODIFIER, OP_POINTS_INDEX_0),
    (ADD_FLAT_MODIFIER, OP_COOLDOWN),
    (ADD_FLAT_LABEL_MODIFIER, OP_POINTS_INDEX_0),
    (ADD_PERCENT_LABEL_MODIFIER, OP_PERIODIC_HEALING_AND_DAMAGE),
}


def _modifier_role(effect: SourceEffect) -> tuple[Role | None, list[str]]:
    """Spell-property modifier roles (raw 107/108/218/219), as a free cross product."""
    entry = _MODIFIER_SUBTYPES.get(effect.aura)
    if entry is None:
        return None, []
    family, selector = entry
    operation = _MODIFIER_OPERATIONS.get(effect.misc0)
    if operation is None:
        return None, [f"raw-{effect.aura} operation {effect.misc0} has no admitted "
                      "spell-modifier stage"]

    label = None
    if selector == "Label":
        if not effect.class_mask_is_empty:
            return None, ["label selection requires an empty effect class mask"]
        if effect.misc1 <= 0:
            return None, [f"label selector {effect.misc1} is not a positive SpellLabel"]
        label = effect.misc1
    else:
        if effect.class_mask_is_empty:
            return None, [f"raw-{effect.aura} class-mask selection requires a nonempty "
                          "effect class mask"]
        if effect.misc1 != 0:
            return None, [f"raw-{effect.aura} secondary misc {effect.misc1} is not neutral"]

    # The value variant Core would build for this cell.
    if operation == "FirstEffect":
        value = "FlatFirstEffect" if family == "Flat" else "PercentFirstEffect"
        stage = None
    elif family == "Flat" and operation == "Cooldown":
        value, stage = "FlatCooldown", None
    elif family == "Flat":
        value, stage = "FlatFirstEffect", None
    else:
        value, stage = "Percent", operation

    return Role("SpellModifier", value, stage, selector, label,
                generic_in_core=(effect.aura, effect.misc0) in _GENERIC_IN_CORE), []


#: Every ordinary Core authority that already accepts a SELECTED-TRAIT activation,
#: discovered by following ``try_selected_spell_effect_amounts`` through
#: ``crates/combat/src/program/`` (8 compilers).  The five that
#: ``SelectedTraitEffectRole`` names are only the ones that can appear in a
#: MULTI-effect package; the rest are reachable only as sole-effect packages.
#:
#: raw -> (role name, compiler, reachable inside a multi-effect package?)
SELECTED_AUTHORITIES: dict[int, tuple[str, str, bool]] = {
    MOD_AUTO_ATTACK_DAMAGE: ("AutoAttackDamage", "passive_auto_attack_damage.rs", True),
    MOD_AUTO_ATTACK_CRIT_CHANCE: ("AutoAttackCriticalChance", "critical_modifier.rs", True),
    MOD_HEALING_RECEIVED_PERCENT: ("HealingReceived", "passive_healing_received.rs", True),
    MOD_CRITICAL_BLOCK_AMOUNT: ("CriticalBlockAmount", "critical_block_amount.rs", True),
    193: ("HasteAll", "passive_haste.rs", False),
    290: ("AllCriticalChance", "critical_modifier.rs", False),
    308: ("CriticalChanceForCasterWithAbilities", "critical_modifier.rs", False),
    319: ("MeleeAutoAttackSpeed", "passive_melee_auto_attack_speed.rs", False),
    79: ("DamageDonePercent", "passive_damage_modifier.rs", False),
    87: ("DamageTakenPercent", "passive_damage_modifier.rs", False),
    422: ("AbsorbReceivedPercent", "passive_absorb_received.rs", False),
}

#: Subtypes that ``critical_modifier.rs`` compiles, but NOT from a selected-trait
#: activation: ``validate_activation`` (critical_modifier.rs:1059-1070) admits
#: ``CriticalChanceModifierActivation::SelectedTrait`` only for 290, 308 and 334.
#: They must NOT classify -- an earlier draft of this table listed them as roles, which
#: would have admitted nine exact effects Core has no selected path for.  Raw 52 is the
#: sharpest: it is in Trinity's ``isTriggerAura[]`` (SpellMgr.cpp:1724), i.e. exactly the
#: proc-triggering semantic this pass treats as disqualifying.
CRITICAL_SUBTYPES_WITHOUT_SELECTED_ACTIVATION = {
    52: "WeaponCriticalChance",
    57: "SpellCriticalChance",
    183: "CriticalChanceVersusTargetHealth",
    187: "AttackerMeleeCriticalChance",
    197: "AttackerSpellAndWeaponCriticalChance",
}


def _passive_stat_role(effect: SourceEffect) -> tuple[Role | None, list[str]]:
    """Non-modifier passive roles Core already owns for selected sources."""
    if effect.aura in CRITICAL_SUBTYPES_WITHOUT_SELECTED_ACTIVATION:
        role = CRITICAL_SUBTYPES_WITHOUT_SELECTED_ACTIVATION[effect.aura]
        return None, [f"raw-{effect.aura} ({role}) has a Core authority but no selected-trait "
                      "activation (critical_modifier.rs:1059-1070 admits only 290/308/334)"]
    entry = SELECTED_AUTHORITIES.get(effect.aura)
    name = entry[0] if entry else None
    if name is None:
        return None, []
    blockers = []
    school_mask = None
    if effect.aura in SCHOOL_SELECTOR_ROLES:
        # The mask is the selector.  Only an empty one is meaningless.
        if effect.misc0 <= 0 or effect.misc0 > ALL_SCHOOLS_MASK:
            blockers.append(f"raw-{effect.aura} school mask {effect.misc0} is not a "
                            "nonempty SpellSchoolMask")
        else:
            school_mask = effect.misc0
    else:
        # raw-118 carries the all-schools misc mask 127; the rest are neutral.
        # (aura_subtype.rs:457 "an ordinary single-recipient ApplyAura carrier with the
        #  all-schools misc mask 127".)
        expected_misc0 = (ALL_SCHOOLS_MASK
                          if effect.aura == MOD_HEALING_RECEIVED_PERCENT else 0)
        if effect.misc0 != expected_misc0:
            blockers.append(f"raw-{effect.aura} primary misc {effect.misc0} is not the "
                            f"required {expected_misc0}")
    if effect.misc1 != 0:
        blockers.append(f"raw-{effect.aura} secondary misc {effect.misc1} is not neutral")
    # raw-308 selects on the payload's class mask by design; the other stat authorities
    # carry no mask.  (critical_modifier.rs compile_applicability.)
    if not effect.class_mask_is_empty and effect.aura != 308:
        blockers.append(f"raw-{effect.aura} carries a nonempty effect class mask")
    if effect.aura == MOD_HEALING_RECEIVED_PERCENT and effect.base_points <= 0:
        blockers.append("raw-118 selected passive contract admits only positive points")
    # Sole-effect authorities each have their own generic compiler; the four that can
    # also appear inside a multi-effect package reach that only via a named branch.
    return Role(name, selector=("SchoolMask" if school_mask is not None else None),
                label=school_mask, generic_in_core=not entry[2]), blockers


def classify(effect: SourceEffect) -> Classification:
    """Classify one exact effect from semantic facts alone."""
    blockers = list(neutral_blockers(effect))
    role, role_blockers = _modifier_role(effect)
    if role is None and not role_blockers:
        role, role_blockers = _passive_stat_role(effect)
    blockers.extend(role_blockers)
    if role is None and not role_blockers:
        blockers.append(f"aura subtype {effect.aura} has no selected-passive semantic in Core")
    checked = ["APPLY_AURA", "nonperiodic", "no trigger", "no mechanic", "no radii",
               "neutral chain", "neutral coefficients", "no exact-effect attributes",
               "self delivery"]
    return Classification(effect, role if role is not None else None, blockers, checked)


def classify_package(resolved: Resolved) -> list[Classification]:
    """Classify the COMPLETE provider effect set, before any filtering."""
    return [classify(effect) for effect in resolved.effects]
