"""Routing of combined ItemModType primary stats onto concrete stats.

The gearing pass found four combined identities in ``ItemSparse``:

    71 AGI_STR_INT, 72 AGI_STR, 73 AGI_INT, 74 STR_INT

The direct consumer is ``Player::_ApplyItemBonuses``
(``src/server/game/Entities/Player/Player.cpp``), and the answer is blunt:
**there is no selection**.  Each combined type calls
``HandleStatFlatModifier(UNIT_MOD_STAT_<each named stat>, BASE_VALUE, val)``
for *every* stat it names, with the same rounded value, then
``UpdateStatBuffMod`` for each.

So a plate wearer with an ``AGI_STR_INT`` item genuinely gains Strength,
Agility *and* Intellect.  What makes only one of them matter is downstream and
is entirely coefficient-driven:

* attack power uses ``ChrClasses.AttackPowerPerStrength`` and
  ``AttackPowerPerAgility``, which are 0 for the stats a class does not use;
* spell power from intellect is gated on
  ``Player::GetPrimaryStat() == STAT_INTELLECT``, i.e. on
  ``ChrSpecialization.PrimaryStatPriority``;
* armour specialisation grants a percentage to one specific stat, chosen by the
  granted spell's ``EffectMiscValue_1`` bitmask.

That is the information-dropping boundary: the ItemModType must survive to the
character stage, because nothing item-side can resolve it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .identity import (
    STAT_AGILITY,
    STAT_INTELLECT,
    STAT_NAMES,
    STAT_STAMINA,
    STAT_STRENGTH,
    ClassInfo,
    SpecInfo,
    primary_stat_from_priority,
)

# src/server/game/Entities/Item/ItemTemplate.h :: enum ItemModType
MOD_AGILITY = 3
MOD_STRENGTH = 4
MOD_INTELLECT = 5
MOD_SPIRIT = 6
MOD_STAMINA = 7
MOD_AGI_STR_INT = 71
MOD_AGI_STR = 72
MOD_AGI_INT = 73
MOD_STR_INT = 74

#: Mirrors: the ``ITEM_MOD_*`` cases of ``Player::_ApplyItemBonuses`` that call
#: ``HandleStatFlatModifier(UNIT_MOD_STAT_*, BASE_VALUE, ...)``.  The value is
#: applied to every listed stat, not distributed among them.
ITEM_MOD_TO_STATS: dict[int, tuple[int, ...]] = {
    MOD_AGILITY: (STAT_AGILITY,),
    MOD_STRENGTH: (STAT_STRENGTH,),
    MOD_INTELLECT: (STAT_INTELLECT,),
    MOD_SPIRIT: (4,),
    MOD_STAMINA: (STAT_STAMINA,),
    MOD_AGI_STR_INT: (STAT_AGILITY, STAT_STRENGTH, STAT_INTELLECT),
    MOD_AGI_STR: (STAT_AGILITY, STAT_STRENGTH),
    MOD_AGI_INT: (STAT_AGILITY, STAT_INTELLECT),
    MOD_STR_INT: (STAT_STRENGTH, STAT_INTELLECT),
}

COMBINED_ITEM_MODS = frozenset({MOD_AGI_STR_INT, MOD_AGI_STR, MOD_AGI_INT,
                                MOD_STR_INT})


@dataclass(frozen=True)
class PrimaryStatRouting:
    """How a (class, spec) consumes each stat a combined ItemModType grants."""

    class_id: int
    spec_id: int | None
    primary_stat: int
    primary_stat_priority: int
    attack_power_per_strength: int
    attack_power_per_agility: int
    ranged_attack_power_per_agility: int
    spell_power_from_intellect: bool

    @property
    def primary_stat_name(self) -> str:
        return STAT_NAMES[self.primary_stat]

    def effective_stats(self, item_mod_type: int) -> dict[str, Any]:
        """Which stats an ItemModType feeds, and whether each one does anything."""
        granted = ITEM_MOD_TO_STATS.get(item_mod_type, ())
        out = []
        for stat in granted:
            if stat == STAT_STRENGTH:
                used = self.attack_power_per_strength > 0
                why = (f"ChrClasses.AttackPowerPerStrength = "
                       f"{self.attack_power_per_strength}")
            elif stat == STAT_AGILITY:
                used = (self.attack_power_per_agility > 0
                        or self.ranged_attack_power_per_agility > 0)
                why = (f"ChrClasses.AttackPowerPerAgility = "
                       f"{self.attack_power_per_agility}, "
                       f"RangedAttackPowerPerAgility = "
                       f"{self.ranged_attack_power_per_agility}")
            elif stat == STAT_INTELLECT:
                used = self.spell_power_from_intellect
                why = (f"Player::GetPrimaryStat() == "
                       f"{self.primary_stat_name} (PrimaryStatPriority "
                       f"{self.primary_stat_priority})")
            else:
                used = True
                why = "always consumed"
            out.append({"stat": stat, "stat_name": STAT_NAMES[stat],
                        "granted": True, "contributes": used, "why": why})
        return {
            "item_mod_type": item_mod_type,
            "granted_stats": [STAT_NAMES[s] for s in granted],
            "detail": out,
            "note": "Player::_ApplyItemBonuses applies the same rounded value "
                    "to every granted stat; nothing selects one of them",
        }

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["primary_stat_name"] = self.primary_stat_name
        return out


def routing_for(class_info: ClassInfo,
                spec_info: SpecInfo | None) -> PrimaryStatRouting:
    """Mirrors: ``Player::GetPrimaryStat`` plus the ChrClasses AP coefficients.

    ``GetPrimaryStat`` prefers the spec's ``PrimaryStatPriority`` and falls back
    to the class's when the player has no specialization.
    """
    priority = (spec_info.primary_stat_priority if spec_info is not None
                else class_info.primary_stat_priority)
    primary = primary_stat_from_priority(priority)
    return PrimaryStatRouting(
        class_id=class_info.class_id,
        spec_id=spec_info.spec_id if spec_info is not None else None,
        primary_stat=primary,
        primary_stat_priority=priority,
        attack_power_per_strength=class_info.attack_power_per_strength,
        attack_power_per_agility=class_info.attack_power_per_agility,
        ranged_attack_power_per_agility=class_info.ranged_attack_power_per_agility,
        spell_power_from_intellect=primary == STAT_INTELLECT,
    )
