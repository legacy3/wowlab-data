"""Mastery: from rating to the spec's mastery spell effects.

The generic half is:

    ITEM_MOD_MASTERY_RATING allocation
      -> Item::GetItemStatValue * CombatRatingsMultByILvl -> std::round
      -> Player::ApplyRatingMod(CR_MASTERY) -> CombatRatings[25]
      -> Player::UpdateMastery:
             if !CanUseMastery(): Mastery = 0 and stop
             Mastery = GetTotalAuraModifier(SPELL_AURA_MASTERY)
                     + GetRatingBonusValue(CR_MASTERY)
      -> ActivePlayerData::Mastery

``CanUseMastery()`` is ``HasSpell(ChrSpecialization.MasterySpellID[0]) ||
HasSpell(...[1])``, so a character that has not acquired its mastery spell gets
nothing from mastery rating at all.

The spec-specific half is **not** special.  ``SpellEffectInfo::CalcValue``
(``src/server/game/Spells/SpellInfo.cpp``) does:

    if (spellInfo->HasAttribute(SPELL_ATTR8_MASTERY_AFFECTS_POINTS))
        if (Player const* p = caster->ToPlayer())
            value += *p->m_activePlayerData->Mastery * BonusCoefficient;

so a mastery effect's amount is ``base points + Mastery * BonusCoefficient`` and
then behaves as whatever aura type it is.  Everything downstream is ordinary
spell semantics.

Three important consequences, all reproduced here:

* "Mastery points" (``ActivePlayerData::Mastery``) is a *percentage-shaped
  scalar* produced by the rating conversion; it is not per-spec.
* "Mastery percentage" as a tooltip shows it is
  ``Mastery * BonusCoefficient`` of one specific effect -- there is no single
  spec-level mastery percentage in the data.
* a mastery effect whose aura is ``SPELL_AURA_DUMMY`` has no generic meaning at
  all and is script-dependent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._aura_names import AURA_TYPE_NAMES
from .identity import SpecInfo

SPELL_AURA_DUMMY = 4
SPELL_AURA_PERIODIC_DUMMY = 226
SPELL_AURA_PROC_TRIGGER_SPELL = 42
SPELL_AURA_PROC_TRIGGER_SPELL_WITH_VALUE = 92

#: Aura types whose amount is consumed by a generic engine rule.  This is a
#: *classification aid*, not a claim that the whole effect is implemented; each
#: one is named so a reviewer can check the handler in
#: ``SpellAuraEffects.cpp``'s dispatch table.  The
#: ``*_BY_SPELL_LABEL`` variants are included because they are ordinary spell
#: modifiers with a label selector, not bespoke behaviour.
ORDINARY_VALUE_AURAS = frozenset({
    13,    # SPELL_AURA_MOD_DAMAGE_DONE
    29,    # SPELL_AURA_MOD_STAT
    51,    # SPELL_AURA_MOD_BLOCK_PERCENT
    79,    # SPELL_AURA_MOD_DAMAGE_PERCENT_DONE
    107,   # SPELL_AURA_ADD_FLAT_MODIFIER
    108,   # SPELL_AURA_ADD_PCT_MODIFIER
    133,   # SPELL_AURA_MOD_INCREASE_HEALTH_PERCENT
    135,   # SPELL_AURA_MOD_HEALING_DONE_PERCENT
    137,   # SPELL_AURA_MOD_TOTAL_STAT_PERCENTAGE
    166,   # SPELL_AURA_MOD_ATTACK_POWER_PCT
    178,   # SPELL_AURA_MOD_MAX_POWER_PCT
    189,   # SPELL_AURA_MOD_RATING
    193,   # SPELL_AURA_MELEE_SLOW
    218,   # SPELL_AURA_ADD_PCT_MODIFIER_BY_SPELL_LABEL
    219,   # SPELL_AURA_ADD_FLAT_MODIFIER_BY_SPELL_LABEL
    253,   # SPELL_AURA_MOD_BLOCK_CRIT_CHANCE
    344,   # SPELL_AURA_MOD_AUTOATTACK_DAMAGE
    379,   # SPELL_AURA_MOD_MANA_REGEN_PCT
    422,   # SPELL_AURA_MOD_ABSORB_TAKEN_PCT
    429,   # SPELL_AURA_MOD_SUMMON_DAMAGE
})

#: Aura types with no generic value semantics; a mastery built on these needs a
#: script or a spell-specific rule.
SCRIPT_DEPENDENT_AURAS = frozenset({
    SPELL_AURA_DUMMY, SPELL_AURA_PERIODIC_DUMMY,
    SPELL_AURA_PROC_TRIGGER_SPELL, SPELL_AURA_PROC_TRIGGER_SPELL_WITH_VALUE,
})

CATEGORY_ORDINARY = "ordinary-spell-semantics"
CATEGORY_SCRIPTED = "script-or-consumer-dependent"
CATEGORY_UNSUPPORTED = "blocked-on-unsupported-generic-semantic"
CATEGORY_NO_MASTERY = "no-mastery-spell"


@dataclass
class MasteryEffect:
    effect_index: int
    effect: int
    aura_type: int
    aura_name: str
    base_points: float
    bonus_coefficient: float
    misc_value_0: int
    misc_value_1: int

    def amount_at(self, mastery: float) -> float:
        """Mirrors: ``SpellEffectInfo::CalcValue`` under MASTERY_AFFECTS_POINTS."""
        return self.base_points + mastery * self.bonus_coefficient

    @property
    def participates_in_mastery(self) -> bool:
        """``UpdateMastery`` only recalculates effects with a non-zero coefficient."""
        return self.bonus_coefficient != 0.0

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class MasterySpell:
    spell_id: int
    name: str
    mastery_affects_points: bool
    effects: list[MasteryEffect]

    def to_dict(self) -> dict[str, Any]:
        return {
            "spell_id": self.spell_id, "name": self.name,
            "mastery_affects_points": self.mastery_affects_points,
            "effects": [e.to_dict() for e in self.effects],
        }


@dataclass
class MasteryProfile:
    spec_id: int
    spec_name: str
    class_id: int
    spells: list[MasterySpell]
    category: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id, "spec_name": self.spec_name,
            "class_id": self.class_id, "category": self.category,
            "reason": self.reason,
            "spells": [s.to_dict() for s in self.spells],
        }


def build_profile(spec: SpecInfo, raw_spells: list[dict[str, Any]]
                  ) -> MasteryProfile:
    """Classify a spec's mastery from its spell effect shape alone."""
    spells: list[MasterySpell] = []
    for raw in raw_spells:
        effects = [MasteryEffect(
            effect_index=e["effect_index"], effect=e["effect"],
            aura_type=e["effect_aura"],
            aura_name=AURA_TYPE_NAMES.get(e["effect_aura"],
                                          f"aura_{e['effect_aura']}"),
            base_points=e["base_points"],
            bonus_coefficient=e["bonus_coefficient"],
            misc_value_0=e["misc_value_0"], misc_value_1=e["misc_value_1"])
            for e in raw["effects"]]
        spells.append(MasterySpell(
            spell_id=raw["spell_id"], name=raw["name"],
            mastery_affects_points=raw["mastery_affects_points"],
            effects=effects))

    if not spells:
        return MasteryProfile(
            spec.spec_id, spec.name, spec.class_id, spells,
            CATEGORY_NO_MASTERY,
            "ChrSpecialization carries no MasterySpellID; CanUseMastery() is "
            "false and ActivePlayerData::Mastery stays 0")

    scaling = [e for s in spells for e in s.effects if e.participates_in_mastery]
    if not scaling:
        return MasteryProfile(
            spec.spec_id, spec.name, spec.class_id, spells,
            CATEGORY_UNSUPPORTED,
            "the mastery spell has no effect with a non-zero BonusCoefficient, "
            "so UpdateMastery recalculates nothing")

    scripted = [e for e in scaling if e.aura_type in SCRIPT_DEPENDENT_AURAS]
    unknown = [e for e in scaling
               if e.aura_type not in SCRIPT_DEPENDENT_AURAS
               and e.aura_type not in ORDINARY_VALUE_AURAS]

    if scripted:
        return MasteryProfile(
            spec.spec_id, spec.name, spec.class_id, spells,
            CATEGORY_SCRIPTED,
            "mastery-scaled effects use "
            + ", ".join(sorted({e.aura_name for e in scripted}))
            + ", which carry no generic value semantics")
    if unknown:
        return MasteryProfile(
            spec.spec_id, spec.name, spec.class_id, spells,
            CATEGORY_UNSUPPORTED,
            "mastery-scaled effects use "
            + ", ".join(sorted({e.aura_name for e in unknown}))
            + ", which this classification does not yet cover")
    return MasteryProfile(
        spec.spec_id, spec.name, spec.class_id, spells,
        CATEGORY_ORDINARY,
        "every mastery-scaled effect uses an aura whose amount a generic engine "
        "rule consumes: "
        + ", ".join(sorted({e.aura_name for e in scaling})))
