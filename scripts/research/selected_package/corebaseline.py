"""Core's CURRENT admission, transcribed branch for branch.

This is the baseline every candidate generic rule is scored against.  It is a
deliberate, literal transcription of

    selected_trait_package_contract        selected_trait_package.rs:217-228
      single_rank_contract                 selected_trait_package.rs:338-461
      two_rank_contract                    selected_trait_package.rs:463-499

together with the predicates those branches call:

    is_ephemeral_bond_package              exact_source.rs:108-120
    is_martial_expert_package              exact_source.rs:122-138
    heart_of_the_crusader_effects          exact_source.rs:46-106
    is_selected_auto_attack_damage_package passive_auto_attack_damage.rs
    supports_ranked_direct_source          passive_spell_modifier.rs:1037-1063
    supports_ranked_flat_cooldown_source   passive_spell_modifier.rs:1068-1094
    supports_phalanx_source                passive_spell_modifier.rs:1117-1185
    has_improved_vivify_amounts            passive_spell_modifier.rs:1187-...
    is_ranked_direct_and_periodic_package  passive_spell_modifier.rs:984-1032
    generic_spell_modifier                 selected_trait_package.rs:846-913
    supports_unconditional_passive_application_policy  passive_spell_modifier.rs:1100-1110

Nothing here is a proposal.  Deviating from Core is a bug in this file, and the
tests pin the eleven shapes Core's own test
(``classifier_maps_every_existing_package_branch_to_structural_roles``,
selected_trait_package.rs:1231-1330) exercises.

**Branch order is load-bearing** and is preserved exactly: an earlier branch
shadows a later one, so `admit` also reports which branch fired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .effects import (
    ADD_FLAT_LABEL_MODIFIER, ADD_FLAT_MODIFIER, ADD_PERCENT_LABEL_MODIFIER,
    ADD_PERCENT_MODIFIER, MOD_AUTO_ATTACK_CRIT_CHANCE, MOD_AUTO_ATTACK_DAMAGE,
    MOD_CRITICAL_BLOCK_AMOUNT, OP_COOLDOWN, OP_CRIT_DAMAGE_AND_HEALING_BONUS,
    OP_HEALING_AND_DAMAGE, OP_PERIODIC_HEALING_AND_DAMAGE, OP_POINTS_INDEX_0,
)
from .owner import Owners
from .provenance import Provenance, Resolved, SourceEffect, TraitSpellError

# -- exact identities Core still pins (exact_source.rs:30-36, passive_spell_modifier.rs:33-37,
#    selected_trait_package.rs:27-30).  Listed here only so the falsification ledger can
#    count how many survive a candidate rule.
EPHEMERAL_BOND_PROVIDER = 426_563
MARTIAL_EXPERT_ENTRY, MARTIAL_EXPERT_DEFINITION, MARTIAL_EXPERT_PROVIDER = 117_409, 122_421, 429_638
HEART_ENTRY, HEART_DEFINITION, HEART_PROVIDER = 115_483, 120_495, 406_154
PHALANX_ENTRY, PHALANX_DEFINITION, PHALANX_PROVIDER, PHALANX_LABEL = 137_000, 141_763, 1_269_312, 5_918
IMPROVED_VIVIFY_ENTRY, IMPROVED_VIVIFY_PROVIDER, IMPROVED_VIVIFY_LABEL = 101_510, 231_602, 2_930

NAMED_IDENTITIES = {
    "ephemeral_bond": {"spell": EPHEMERAL_BOND_PROVIDER},
    "martial_expert": {"entry": MARTIAL_EXPERT_ENTRY, "definition": MARTIAL_EXPERT_DEFINITION,
                       "spell": MARTIAL_EXPERT_PROVIDER},
    "heart_of_the_crusader": {"entry": HEART_ENTRY, "definition": HEART_DEFINITION,
                              "spell": HEART_PROVIDER},
    "phalanx": {"entry": PHALANX_ENTRY, "definition": PHALANX_DEFINITION,
                "spell": PHALANX_PROVIDER, "label": PHALANX_LABEL},
    "improved_vivify": {"entry": IMPROVED_VIVIFY_ENTRY, "spell": IMPROVED_VIVIFY_PROVIDER,
                        "label": IMPROVED_VIVIFY_LABEL},
}

MAX_PACKAGE_EFFECTS = 4


@dataclass
class CoreAdmission:
    branch: str
    roles: list[str]
    owner: str                      # absent | physical_absent | exact | physical_exact
    owner_roles: int
    allow_neutral_missile: bool
    aura_options: dict[str, int] | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"branch": self.branch, "roles": list(self.roles), "owner": self.owner,
                "owner_roles": self.owner_roles,
                "allow_neutral_missile": self.allow_neutral_missile,
                "aura_options": self.aura_options, "notes": sorted(self.notes)}


def _bits(value: float) -> int:
    import struct
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def _eq(a: float | None, b: float) -> bool:
    return a is not None and _bits(a) == _bits(b)


def _exact_i32(value: float | None) -> int | None:
    """Mirrors: ``exact_i32`` (selected_trait_package.rs:1097-1105)."""
    if value is None or value != value or abs(value) == float("inf"):
        return None
    integer = int(value)
    return integer if -(2**31) <= integer < 2**31 and float(integer) == value else None


class CoreBaseline:
    """Answers "does Core admit this entry at this rank, and through which branch?"."""

    def __init__(self, provenance: Provenance | None = None, owners: Owners | None = None) -> None:
        self.provenance = provenance or Provenance()
        self.owners = owners or Owners(self.provenance.source)

    # -- owner policy Core requires before the contract is even consulted ---
    def unconditional_passive(self, spell: int) -> bool:
        """Mirrors: ``supports_unconditional_passive_application_policy``."""
        owner = self.owners
        return (not owner.aura_restrictions.get(spell)
                and not int(owner.misc.get(spell, {}).get("DurationIndex", 0) or 0)
                and not owner.shapeshift.get(spell))

    def admit(self, entry_id: int, rank: int) -> CoreAdmission | str:
        resolved = self.provenance.resolve(entry_id, rank)
        if isinstance(resolved, TraitSpellError):
            return f"source:{resolved.value}"
        entry = self.provenance.traits.entries[entry_id]
        contract = (self._single_rank(resolved, entry)
                    if entry.max_ranks == 1 else
                    self._two_rank(resolved, entry) if entry.max_ranks == 2 else None)
        if contract is None:
            return "no admitted structural selected-trait effect package"
        return contract

    # -- single_rank_contract ---------------------------------------------
    def _single_rank(self, r: Resolved, entry) -> CoreAdmission | None:
        effects = r.effects

        # 1. is_ephemeral_bond_package -- provider spell id, 1 rank, indices 1/2/3
        if (r.spell == EPHEMERAL_BOND_PROVIDER and r.max_ranks == 1
                and [e.index for e in effects] == [1, 2, 3]):
            if all(self._percent_first_effect_label(r, e) for e in effects[1:]):
                return CoreAdmission("ephemeral_bond",
                                     ["HealingReceived", "PercentFirstEffect/Label",
                                      "PercentFirstEffect/Label"],
                                     "absent", 3, True,
                                     notes=["pins provider spell 426563"])
            return None

        # 2. Martial Expert -- definition id AND entry id AND provider AND exact amounts
        if entry.definition_id == MARTIAL_EXPERT_DEFINITION and self._is_martial(r, effects):
            critical_bonus, critical_block = effects
            if not self._percent_class_mask(r, critical_bonus, OP_CRIT_DAMAGE_AND_HEALING_BONUS):
                return None
            if critical_block.aura != MOD_CRITICAL_BLOCK_AMOUNT:
                return None
            return CoreAdmission("martial_expert",
                                 ["Percent/CriticalBonus/ClassMask", "CriticalBlockAmount"],
                                 "absent", 2, True,
                                 notes=["pins entry 117409, definition 122421, spell 429638",
                                        "pins authored amounts 10.0 and 20.0"])

        # 3. is_selected_auto_attack_damage_package -- purely semantic
        if self._is_auto_attack_damage(r, effects):
            spell_modifier, auto_attack = effects
            if not self._percent_class_mask(r, spell_modifier, OP_HEALING_AND_DAMAGE):
                return None
            if auto_attack.aura != MOD_AUTO_ATTACK_DAMAGE:
                return None
            return CoreAdmission("auto_attack_damage",
                                 ["Percent/Direct/ClassMask", "AutoAttackDamage"],
                                 "physical_absent", 2, True)

        # 4. fallback: exactly one effect through generic_spell_modifier
        if len(effects) != 1:
            return None
        role = self._generic_spell_modifier(r, effects[0])
        if role is None:
            return None
        return CoreAdmission("generic_single_effect", [role], "absent", 1, False)

    # -- two_rank_contract -------------------------------------------------
    def _two_rank(self, r: Resolved, entry) -> CoreAdmission | None:
        effects = r.effects

        if self._is_heart(r, entry, effects):
            auto_damage, auto_critical, direct, critical_bonus = effects
            if auto_damage.aura != MOD_AUTO_ATTACK_DAMAGE:
                return None
            if auto_critical.aura != MOD_AUTO_ATTACK_CRIT_CHANCE:
                return None
            if not self._percent_class_mask(r, direct, OP_HEALING_AND_DAMAGE):
                return None
            if not self._percent_class_mask(r, critical_bonus, OP_CRIT_DAMAGE_AND_HEALING_BONUS):
                return None
            return CoreAdmission("heart_of_the_crusader",
                                 ["AutoAttackDamage", "AutoAttackCriticalChance",
                                  "Percent/Direct/ClassMask", "Percent/CriticalBonus/ClassMask"],
                                 "physical_exact", 4, False,
                                 {"cumulative_aura": 0, "proc_category_recovery_ms": 0,
                                  "proc_chance": 101, "proc_charges": 0,
                                  "procs_per_minute_id": 0, "proc_type_mask": 4},
                                 ["pins entry 115483, definition 120495, spell 406154",
                                  "pins effect indices 1,2,3,4 and Set 10.0 -> 10/20",
                                  "pins the literal AuraOptions row"])

        if self._supports_ranked_direct(r, effects):
            if self._percent_class_mask(r, effects[0], OP_HEALING_AND_DAMAGE):
                return CoreAdmission("ranked_direct", ["Percent/Direct/ClassMask"],
                                     "absent", 1, False)
            return None

        if self._supports_ranked_flat_cooldown(r, effects):
            if self._flat_class_mask(r, effects[0], OP_COOLDOWN) is not None:
                return CoreAdmission("ranked_flat_cooldown", ["FlatCooldown/ClassMask"],
                                     "absent", 1, False)
            return None

        if self._supports_phalanx(r, entry, effects):
            return CoreAdmission("phalanx",
                                 ["Percent/Direct/ClassMask", "Percent/Direct/Label"],
                                 "exact", 2, False,
                                 {"cumulative_aura": 2, "proc_category_recovery_ms": 0,
                                  "proc_chance": 101, "proc_charges": 0,
                                  "procs_per_minute_id": 0, "proc_type_mask": 0},
                                 ["pins entry 137000, definition 141763, spell 1269312",
                                  "pins label 5918 and Multiply 10.0 -> 10/20",
                                  "pins the literal AuraOptions row incl. CumulativeAura 2"])

        if (r.entry_id == IMPROVED_VIVIFY_ENTRY and r.spell == IMPROVED_VIVIFY_PROVIDER
                and self._has_improved_vivify_amounts(r)):
            direct, label = effects
            if not self._percent_class_mask(r, direct, OP_HEALING_AND_DAMAGE):
                return None
            if not self._percent_label(r, label, OP_HEALING_AND_DAMAGE, IMPROVED_VIVIFY_LABEL):
                return None
            return CoreAdmission("improved_vivify",
                                 ["Percent/Direct/ClassMask", "Percent/Direct/Label"],
                                 "absent", 2, True,
                                 notes=["pins entry 101510 and spell 231602",
                                        "pins label 2930 and authored 40.0 with Set 20/40",
                                        "pins the mixed shaped/unshaped sibling pair"])

        if self._is_ranked_direct_and_periodic(r, effects):
            roles = []
            for effect in effects:
                if effect.misc0 == OP_HEALING_AND_DAMAGE:
                    if not self._percent_class_mask(r, effect, OP_HEALING_AND_DAMAGE):
                        return None
                    roles.append("Percent/Direct/ClassMask")
                elif effect.misc0 == OP_PERIODIC_HEALING_AND_DAMAGE:
                    if not self._percent_class_mask(r, effect, OP_PERIODIC_HEALING_AND_DAMAGE):
                        return None
                    roles.append("Percent/Periodic/ClassMask")
                else:
                    return None
            return CoreAdmission("ranked_direct_and_periodic", roles, "absent", 2, True)

        return None

    # -- the predicates ----------------------------------------------------
    def _is_martial(self, r: Resolved, effects: list[SourceEffect]) -> bool:
        return (r.entry_id == MARTIAL_EXPERT_ENTRY and r.spell == MARTIAL_EXPERT_PROVIDER
                and r.max_ranks == 1 and r.effective_rank == 1 and len(effects) == 2
                and [e.index for e in effects] == [1, 2]
                and not r.is_rank_shaped(1) and not r.is_rank_shaped(2)
                and _eq(r.selected_amount(1), 10.0) and _eq(r.selected_amount(2), 20.0))

    def _is_auto_attack_damage(self, r: Resolved, effects: list[SourceEffect]) -> bool:
        if r.max_ranks != 1 or len(effects) != 2:
            return False
        spell_modifier, auto_attack = effects
        points = r.selected_amount(spell_modifier.index)
        other = r.selected_amount(auto_attack.index)
        return (spell_modifier.index == 1 and auto_attack.index == 2
                and spell_modifier.aura == ADD_PERCENT_MODIFIER
                and auto_attack.aura == MOD_AUTO_ATTACK_DAMAGE
                and spell_modifier.misc0 == 0 and auto_attack.misc0 == 0
                and spell_modifier.misc1 == 0 and auto_attack.misc1 == 0
                and not spell_modifier.class_mask_is_empty and auto_attack.class_mask_is_empty
                and points is not None and points == points and points >= -100.0
                and _eq(other, points)
                and _eq(r.authored(spell_modifier.index), points)
                and _eq(r.authored(auto_attack.index), points))

    def _is_heart(self, r: Resolved, entry, effects: list[SourceEffect]) -> bool:
        if len(effects) != 4:
            return False
        exact = lambda i: (r.point_operation(i) == "Set" and _eq(r.authored(i), 10.0)
                           and _eq(r.amount_at_rank(i, 1), 10.0)
                           and _eq(r.amount_at_rank(i, 2), 20.0))
        return (r.entry_id == HEART_ENTRY
                and entry.definition_id == HEART_DEFINITION and entry.max_ranks == 2
                and r.spell == HEART_PROVIDER and r.max_ranks == 2
                and r.effective_rank in (1, 2)
                and [e.index for e in effects] == [1, 2, 3, 4]
                and all(exact(e.index) for e in effects))

    def _supports_ranked_direct(self, r: Resolved, effects: list[SourceEffect]) -> bool:
        if len(effects) != 1:
            return False
        effect = effects[0]
        return (self.unconditional_passive(r.spell)
                and effect.aura == ADD_PERCENT_MODIFIER
                and effect.misc0 == OP_HEALING_AND_DAMAGE
                and r.point_operation(effect.index) in ("Set", "Multiply")
                and all((a := r.amount_at_rank(effect.index, rank)) is not None
                        and a == a and a >= -100.0 for rank in (1, 2)))

    def _supports_ranked_flat_cooldown(self, r: Resolved, effects: list[SourceEffect]) -> bool:
        if len(effects) != 1:
            return False
        effect = effects[0]
        return (self.unconditional_passive(r.spell)
                and effect.aura == ADD_FLAT_MODIFIER
                and effect.misc0 == OP_COOLDOWN
                and r.point_operation(effect.index) in ("Set", "Multiply")
                and all((ms := _exact_i32(r.amount_at_rank(effect.index, rank))) is not None
                        and ms < 0 for rank in (1, 2)))

    def _supports_phalanx(self, r: Resolved, entry, effects: list[SourceEffect]) -> bool:
        if len(effects) != 2:
            return False
        direct, label = effects
        exact = lambda i: (r.point_operation(i) == "Multiply" and _eq(r.authored(i), 10.0)
                           and _eq(r.amount_at_rank(i, 1), 10.0)
                           and _eq(r.amount_at_rank(i, 2), 20.0))
        options = self.owners.aura_options.get(r.spell, [])
        sidecars_ok = (len(options) == 1
                       and int(options[0]["CumulativeAura"]) == 2
                       and int(options[0]["ProcCategoryRecovery"]) == 0
                       and int(options[0]["ProcChance"]) == 101
                       and int(options[0]["ProcCharges"]) == 0
                       and int(options[0]["SpellProcsPerMinuteID"]) == 0
                       and int(options[0]["ProcTypeMask_0"]) == 0
                       and int(options[0]["ProcTypeMask_1"]) == 0
                       and not self.owners.equipped.get(r.spell)
                       and not _has_missile(self.owners, r.spell))
        return (r.entry_id == PHALANX_ENTRY and entry.definition_id == PHALANX_DEFINITION
                and entry.max_ranks == 2 and r.spell == PHALANX_PROVIDER
                and direct.index == 1 and label.index == 2
                and exact(1) and exact(2)
                and direct.aura == ADD_PERCENT_MODIFIER and direct.misc0 == OP_HEALING_AND_DAMAGE
                and direct.misc1 == 0 and direct.class_mask == (128, 0, 0, 0)
                and label.aura == ADD_PERCENT_LABEL_MODIFIER
                and label.misc0 == OP_HEALING_AND_DAMAGE and label.misc1 == PHALANX_LABEL
                and label.class_mask_is_empty
                and sidecars_ok)

    def _has_improved_vivify_amounts(self, r: Resolved) -> bool:
        return (r.max_ranks == 2 and r.point_operation(1) == "Set"
                and r.point_operation(2) is None
                and _eq(r.authored(1), 40.0) and _eq(r.authored(2), 40.0)
                and _eq(r.amount_at_rank(1, 1), 20.0) and _eq(r.amount_at_rank(1, 2), 40.0))

    def _is_ranked_direct_and_periodic(self, r: Resolved, effects: list[SourceEffect]) -> bool:
        if len(effects) != 2:
            return False
        first, second = effects
        if first.aura != ADD_PERCENT_MODIFIER or second.aura != ADD_PERCENT_MODIFIER:
            return False
        supported = lambda i: all((a := r.amount_at_rank(i, rank)) is not None
                                  and a == a and a >= -100.0 for rank in (1, 2))
        return (r.point_operation(first.index) == "Set"
                and r.point_operation(second.index) == "Set"
                and supported(first.index) and supported(second.index)
                and {first.misc0, second.misc0} == {OP_HEALING_AND_DAMAGE,
                                                    OP_PERIODIC_HEALING_AND_DAMAGE}
                and first.misc0 != second.misc0)

    # -- value classifiers -------------------------------------------------
    def _basic(self, effect: SourceEffect, subtype: int, operation: int) -> bool:
        """Mirrors: ``basic_modifier_source`` (selected_trait_package.rs:1055-1069)."""
        return (effect.kind == 6 and effect.aura == subtype
                and effect.period_ms == 0 and effect.misc0 == operation)

    def _percent_class_mask(self, r: Resolved, effect: SourceEffect, operation: int) -> bool:
        points = r.selected_amount(effect.index)
        return (self._basic(effect, ADD_PERCENT_MODIFIER, operation)
                and effect.misc1 == 0 and not effect.class_mask_is_empty
                and points is not None and points == points and abs(points) != float("inf"))

    def _percent_any_label(self, r: Resolved, effect: SourceEffect, operation: int) -> int | None:
        if not self._basic(effect, ADD_PERCENT_LABEL_MODIFIER, operation):
            return None
        if effect.misc1 <= 0 or not effect.class_mask_is_empty:
            return None
        points = r.selected_amount(effect.index)
        if points is None or points != points or abs(points) == float("inf"):
            return None
        return effect.misc1

    def _percent_label(self, r, effect, operation, expected) -> bool:
        return self._percent_any_label(r, effect, operation) == expected

    def _percent_first_effect_label(self, r: Resolved, effect: SourceEffect) -> bool:
        return self._percent_any_label(r, effect, OP_POINTS_INDEX_0) is not None

    def _flat_class_mask(self, r: Resolved, effect: SourceEffect, operation: int) -> int | None:
        if not self._basic(effect, ADD_FLAT_MODIFIER, operation):
            return None
        if effect.misc1 != 0 or effect.class_mask_is_empty:
            return None
        return _exact_i32(r.selected_amount(effect.index))

    def _flat_first_effect_label(self, r: Resolved, effect: SourceEffect) -> int | None:
        if not self._basic(effect, ADD_FLAT_LABEL_MODIFIER, OP_POINTS_INDEX_0):
            return None
        if effect.misc1 <= 0 or not effect.class_mask_is_empty:
            return None
        return _exact_i32(r.selected_amount(effect.index))

    def _generic_spell_modifier(self, r: Resolved, effect: SourceEffect) -> str | None:
        """Mirrors: ``generic_spell_modifier`` (selected_trait_package.rs:846-913).

        Note the gaps: raw-108 operation 15 (CriticalBonus), raw-218 operation 0 and
        raw-218 operation 3 are reachable ONLY through a named branch.
        """
        subtype, operation = effect.aura, effect.misc0
        if subtype == ADD_PERCENT_MODIFIER and operation == OP_HEALING_AND_DAMAGE:
            return ("Percent/Direct/ClassMask"
                    if self._percent_class_mask(r, effect, operation) else None)
        if subtype == ADD_PERCENT_MODIFIER and operation == OP_PERIODIC_HEALING_AND_DAMAGE:
            return ("Percent/Periodic/ClassMask"
                    if self._percent_class_mask(r, effect, operation) else None)
        if subtype == ADD_PERCENT_MODIFIER and operation == OP_COOLDOWN:
            return ("Percent/Cooldown/ClassMask"
                    if self._percent_class_mask(r, effect, operation) else None)
        if subtype == ADD_FLAT_MODIFIER and operation == OP_POINTS_INDEX_0:
            return ("FlatFirstEffect/ClassMask"
                    if self._flat_class_mask(r, effect, operation) is not None else None)
        if subtype == ADD_FLAT_LABEL_MODIFIER and operation == OP_POINTS_INDEX_0:
            return ("FlatFirstEffect/Label"
                    if self._flat_first_effect_label(r, effect) is not None else None)
        if subtype == ADD_FLAT_MODIFIER and operation == OP_COOLDOWN:
            return ("FlatCooldown/ClassMask"
                    if self._flat_class_mask(r, effect, operation) is not None else None)
        if subtype == ADD_PERCENT_LABEL_MODIFIER and operation == OP_PERIODIC_HEALING_AND_DAMAGE:
            return ("Percent/Periodic/Label"
                    if self._percent_any_label(r, effect, operation) is not None else None)
        return None


def _has_missile(owners: Owners, spell: int) -> bool:
    misc = owners.misc.get(spell, {})
    return bool(float(misc.get("Speed", 0) or 0) or float(misc.get("LaunchDelay", 0) or 0)
                or float(misc.get("MinDuration", 0) or 0))


# ---------------------------------------------------------------------------
# final-compilation revalidation
# ---------------------------------------------------------------------------
#
# ``selected_trait_package_contract`` is only the COLD SOURCE CONTRACT.  Final
# ``CombatProgram`` compilation repeats it and independently proves the owner:
#
#     validate_owner                      passive_spell_modifier.rs:940-980
#     validate_owner_attributes           passive_spell_modifier.rs (see below)
#     selected_trait_owner_policy_issue   selected_trait_package.rs:270-338
#
# Core's *effective* admitted population is contract AND this.  Scoring a candidate
# rule against the contract alone overstates Core's admissions, so `CoreBaseline.
# admit_final` is the number the falsification ledger uses.

#: Roles whose value carries a ClassMask selector; `validate_owner` then additionally
#: requires a nonzero owner spell family (passive_spell_modifier.rs:952-954).
_CLASS_MASK_ROLES = ("ClassMask",)


def _owner_final_issues(baseline: "CoreBaseline", spell: int, admission: CoreAdmission,
                        all_effect_mechanics: int) -> list[str]:
    owners = baseline.owners
    policy = owners.policy(spell)
    issues: list[str] = []

    # -- validate_owner (passive_spell_modifier.rs:940-980)
    uses_class_mask = any(role.endswith(_CLASS_MASK_ROLES) for role in admission.roles)
    if uses_class_mask and policy.family == 0:
        issues.append("class-mask selection requires a nonzero owner spell family")
    if policy.defense_type:
        issues.append(f"owner DefenseType {policy.defense_type}")
    if policy.dispel_type:
        issues.append(f"owner DispelType {policy.dispel_type}")
    if any(policy.class_mask):
        issues.append("owner carries a nonempty spell class mask")
    if policy.mechanic:
        issues.append(f"owner Mechanic {policy.mechanic}")
    if all_effect_mechanics:
        issues.append("an exact effect carries a mechanic")

    # -- validate_owner_attributes
    if not policy.is_passive:
        issues.append("selected trait owner lacks Passive raw 6")
    for attribute in policy.attributes:
        if attribute == 6:
            continue
        from . import coreref
        support = coreref.attribute_support(attribute)
        if support == "unknown":
            issues.append(f"uncataloged owner attribute {attribute}")
        elif support != "ignored":
            issues.append(f"owner attribute {attribute} support '{support}' is not Ignored")

    # -- supports_unconditional_passive_application_policy
    if not baseline.unconditional_passive(spell):
        issues.append("conditional or finite passive application policy "
                      "(aura restrictions, duration or shapeshift)")

    # -- selected_trait_owner_policy_issue: sidecars must match the contract exactly
    options = owners.aura_options.get(spell, [])
    if admission.aura_options is None:
        if options:
            issues.append("contract requires no SpellAuraOptions row, but one is present")
    else:
        expected = admission.aura_options
        actual_ok = len(options) == 1 and (
            int(options[0]["CumulativeAura"]) == expected["cumulative_aura"]
            and int(options[0]["ProcCategoryRecovery"]) == expected["proc_category_recovery_ms"]
            and int(options[0]["ProcChance"]) == expected["proc_chance"]
            and int(options[0]["ProcCharges"]) == expected["proc_charges"]
            and int(options[0]["SpellProcsPerMinuteID"]) == expected["procs_per_minute_id"]
            and ((int(options[0]["ProcTypeMask_0"]) & 0xFFFFFFFF)
                 | ((int(options[0]["ProcTypeMask_1"]) & 0xFFFFFFFF) << 32))
            == expected["proc_type_mask"])
        if not actual_ok:
            issues.append("SpellAuraOptions row does not match the contract literal")
    if owners.equipped.get(spell):
        issues.append("SpellEquippedItems row present")
    if not admission.allow_neutral_missile and _has_missile(owners, spell):
        issues.append("missile row present where the contract forbids one")
    elif _has_missile(owners, spell):
        issues.append("missile row is not fully neutral")

    # -- physical shell (exact_source.rs:283-330)
    if admission.owner.startswith("physical") and policy.school_mask != 1:
        issues.append(f"physical shell required, owner school mask is {policy.school_mask}")

    return issues


def _admit_sole(self: "CoreBaseline", entry_id: int, rank: int):
    """Core's OTHER selected path: the per-authority sole-effect compilers.

    ``selected_trait_package_contract`` is not the only way in.  A single-effect
    selected package is admitted by the ordinary authority that owns its aura subtype
    -- passive_haste.rs (193), critical_modifier.rs (290/308), passive_damage_modifier.rs
    (79/87), passive_absorb_received.rs (422), passive_healing_received.rs (118),
    passive_melee_auto_attack_speed.rs (319) -- without ever building a package contract.
    Scoring a candidate rule against the contract path alone therefore understates Core.
    """
    resolved = self.provenance.resolve(entry_id, rank)
    if isinstance(resolved, TraitSpellError) or len(resolved.effects) != 1:
        return None
    try:
        from .soleauthority import admit_sole_effect
    except ImportError:
        return None
    role, blockers = admit_sole_effect(resolved)
    if role is None or blockers:
        return None
    return CoreAdmission(f"sole:{role}", [role], "authority", 1, False,
                         notes=["admitted by the ordinary authority compiler for its aura "
                                "subtype, not by selected_trait_package_contract"])


def _admit_final(self: "CoreBaseline", entry_id: int, rank: int):
    """Core's effective admission: the cold contract AND final owner revalidation,
    OR the per-authority sole-effect path."""
    admission = self.admit(entry_id, rank)
    if isinstance(admission, str):
        sole = _admit_sole(self, entry_id, rank)
        return sole if sole is not None else admission
    resolved = self.provenance.resolve(entry_id, rank)
    mechanics = sum(effect.mechanic for effect in resolved.effects)
    issues = _owner_final_issues(self, resolved.spell, admission, mechanics)
    if issues:
        sole = _admit_sole(self, entry_id, rank)
        return sole if sole is not None else "owner:" + "; ".join(sorted(issues))
    return admission


CoreBaseline.admit_sole = _admit_sole
CoreBaseline.admit_final = _admit_final
