#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Source-backed reconstruction of the retail item scaling pipeline.

This is *research* tooling for the gearing archaeology pass.  It reads the
checked-in Wago CSV tables under ``data/tables`` and reproduces, step by step,
the same computation TrinityCore performs when it turns an equipped item into
combat-relevant numbers.  Every direct consumer that this module mirrors is
named in a ``Mirrors:`` line on the corresponding function so the port can be
re-audited against a newer TrinityCore checkout.

It is deliberately dependency-free (standard library only) so the verification
corpus in ``test_item_scaling.py`` runs anywhere the repository is checked out.
It is not a stable interface and must not be treated as authority for Core
semantics.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TABLES = ROOT / "data" / "tables"

# --------------------------------------------------------------------------
# Enumerations transcribed from the TrinityCore checkout.
# Coordinates are given as file:symbol so each block can be re-verified.
# --------------------------------------------------------------------------

# src/server/game/DataStores/DBCEnums.h :: enum ItemBonusType
BONUS_ITEM_LEVEL = 1
BONUS_STAT = 2
BONUS_QUALITY = 3
BONUS_NAME_SUBTITLE = 4
BONUS_SUFFIX = 5
BONUS_SOCKET = 6
BONUS_APPEARANCE = 7
BONUS_REQUIRED_LEVEL = 8
BONUS_DISPLAY_TOAST_METHOD = 9
BONUS_REPAIR_COST_MULTIPLIER = 10
BONUS_SCALING_STAT_DISTRIBUTION = 11
BONUS_DISENCHANT_LOOT_ID = 12
BONUS_SCALING_STAT_DISTRIBUTION_FIXED = 13
BONUS_ITEM_LEVEL_CAN_INCREASE = 14
BONUS_RANDOM_ENCHANTMENT = 15
BONUS_BONDING = 16
BONUS_RELIC_TYPE = 17
BONUS_OVERRIDE_REQUIRED_LEVEL = 18
BONUS_AZERITE_TIER_UNLOCK_SET = 19
BONUS_SCRAPPING_LOOT_ID = 20
BONUS_OVERRIDE_CAN_DISENCHANT = 21
BONUS_OVERRIDE_CAN_SCRAP = 22
BONUS_ITEM_EFFECT_ID = 23
BONUS_MODIFIED_CRAFTING_STAT = 25
BONUS_REQUIRED_LEVEL_CURVE = 27
BONUS_ICON_FILE_DATA_ID = 28
BONUS_DESCRIPTION_TEXT = 30
BONUS_OVERRIDE_NAME = 31
BONUS_UPGRADE_SEQUENCE_VALUE = 33
BONUS_ITEM_BONUS_LIST_GROUP = 34
BONUS_ITEM_LIMIT_CATEGORY = 35
BONUS_PVP_ITEM_LEVEL_INCREMENT = 36
BONUS_ITEM_CONVERSION = 37
BONUS_ITEM_HISTORY_SLOT = 38
BONUS_OVERRIDE_CAN_SALVAGE = 39
BONUS_OVERRIDE_CAN_RECRAFT = 41
BONUS_ITEM_LEVEL_BASE = 42
BONUS_PVP_ITEM_LEVEL_BASE = 43
BONUS_COSMETIC_STAT = 44
BONUS_OVERRIDE_DESCRIPTION_COLOR = 45
BONUS_OVERRIDE_CANNOT_TRADE_BOP = 46
BONUS_BONDING_WITH_PRIORITY = 47
BONUS_ITEM_OFFSET_CURVE = 48
BONUS_SCALING_CONFIG_AND_REQ_LEVEL = 49
BONUS_ITEM_BONUS_LIST = 50
BONUS_SCALING_CONFIG = 51

BONUS_TYPE_NAMES = {
    v: k[len("BONUS_"):]
    for k, v in list(globals().items())
    if k.startswith("BONUS_") and isinstance(v, int)
}

# src/server/game/Entities/Item/ItemTemplate.h :: enum InventoryType
INVTYPE_NON_EQUIP = 0
INVTYPE_HEAD = 1
INVTYPE_NECK = 2
INVTYPE_SHOULDERS = 3
INVTYPE_BODY = 4
INVTYPE_CHEST = 5
INVTYPE_WAIST = 6
INVTYPE_LEGS = 7
INVTYPE_FEET = 8
INVTYPE_WRISTS = 9
INVTYPE_HANDS = 10
INVTYPE_FINGER = 11
INVTYPE_TRINKET = 12
INVTYPE_WEAPON = 13
INVTYPE_SHIELD = 14
INVTYPE_RANGED = 15
INVTYPE_CLOAK = 16
INVTYPE_2HWEAPON = 17
INVTYPE_BAG = 18
INVTYPE_TABARD = 19
INVTYPE_ROBE = 20
INVTYPE_WEAPONMAINHAND = 21
INVTYPE_WEAPONOFFHAND = 22
INVTYPE_HOLDABLE = 23
INVTYPE_AMMO = 24
INVTYPE_THROWN = 25
INVTYPE_RANGEDRIGHT = 26
INVTYPE_QUIVER = 27
INVTYPE_RELIC = 28
INVTYPE_PROFESSION_TOOL = 29
INVTYPE_PROFESSION_GEAR = 30

INVTYPE_NAMES = {
    v: k[len("INVTYPE_"):]
    for k, v in list(globals().items())
    if k.startswith("INVTYPE_") and isinstance(v, int)
}

# src/server/game/Miscellaneous/SharedDefines.h :: enum ItemQualities
QUALITY_POOR = 0
QUALITY_NORMAL = 1
QUALITY_UNCOMMON = 2
QUALITY_RARE = 3
QUALITY_EPIC = 4
QUALITY_LEGENDARY = 5
QUALITY_ARTIFACT = 6
QUALITY_HEIRLOOM = 7
QUALITY_WOW_TOKEN = 8

QUALITY_NAMES = {
    QUALITY_POOR: "Poor",
    QUALITY_NORMAL: "Common",
    QUALITY_UNCOMMON: "Uncommon",
    QUALITY_RARE: "Rare",
    QUALITY_EPIC: "Epic",
    QUALITY_LEGENDARY: "Legendary",
    QUALITY_ARTIFACT: "Artifact",
    QUALITY_HEIRLOOM: "Heirloom",
    QUALITY_WOW_TOKEN: "WoW Token",
}

# src/server/game/Entities/Item/ItemTemplate.h :: enum ItemModType
MOD_MANA = 0
MOD_HEALTH = 1
MOD_AGILITY = 3
MOD_STRENGTH = 4
MOD_INTELLECT = 5
MOD_SPIRIT = 6
MOD_STAMINA = 7
MOD_DEFENSE_SKILL_RATING = 12
MOD_DODGE_RATING = 13
MOD_PARRY_RATING = 14
MOD_BLOCK_RATING = 15
MOD_HIT_MELEE_RATING = 16
MOD_HIT_RANGED_RATING = 17
MOD_HIT_SPELL_RATING = 18
MOD_CRIT_MELEE_RATING = 19
MOD_CRIT_RANGED_RATING = 20
MOD_CRIT_SPELL_RATING = 21
MOD_CORRUPTION = 22
MOD_CORRUPTION_RESISTANCE = 23
MOD_MODIFIED_CRAFTING_STAT_1 = 24
MOD_MODIFIED_CRAFTING_STAT_2 = 25
MOD_CRIT_TAKEN_RANGED_RATING = 26
MOD_CRIT_TAKEN_SPELL_RATING = 27
MOD_HASTE_MELEE_RATING = 28
MOD_HASTE_RANGED_RATING = 29
MOD_HASTE_SPELL_RATING = 30
MOD_HIT_RATING = 31
MOD_CRIT_RATING = 32
MOD_HIT_TAKEN_RATING = 33
MOD_CRIT_TAKEN_RATING = 34
MOD_RESILIENCE_RATING = 35
MOD_HASTE_RATING = 36
MOD_EXPERTISE_RATING = 37
MOD_ATTACK_POWER = 38
MOD_RANGED_ATTACK_POWER = 39
MOD_VERSATILITY = 40
MOD_SPELL_HEALING_DONE = 41
MOD_SPELL_DAMAGE_DONE = 42
MOD_MANA_REGENERATION = 43
MOD_ARMOR_PENETRATION_RATING = 44
MOD_SPELL_POWER = 45
MOD_HEALTH_REGEN = 46
MOD_SPELL_PENETRATION = 47
MOD_BLOCK_VALUE = 48
MOD_MASTERY_RATING = 49
MOD_EXTRA_ARMOR = 50
MOD_FIRE_RESISTANCE = 51
MOD_FROST_RESISTANCE = 52
MOD_HOLY_RESISTANCE = 53
MOD_SHADOW_RESISTANCE = 54
MOD_NATURE_RESISTANCE = 55
MOD_ARCANE_RESISTANCE = 56
MOD_PVP_POWER = 57
MOD_CR_SPEED = 61
MOD_CR_LIFESTEAL = 62
MOD_CR_AVOIDANCE = 63
MOD_CR_STURDINESS = 64
MOD_AGI_STR_INT = 71
MOD_AGI_STR = 72
MOD_AGI_INT = 73
MOD_STR_INT = 74
MOD_PROFESSION_INSPIRATION = 75

MOD_NAMES = {
    MOD_MANA: "Mana",
    MOD_HEALTH: "Health",
    MOD_AGILITY: "Agility",
    MOD_STRENGTH: "Strength",
    MOD_INTELLECT: "Intellect",
    MOD_SPIRIT: "Spirit",
    MOD_STAMINA: "Stamina",
    MOD_DEFENSE_SKILL_RATING: "DefenseSkillRating",
    MOD_DODGE_RATING: "DodgeRating",
    MOD_PARRY_RATING: "ParryRating",
    MOD_BLOCK_RATING: "BlockRating",
    MOD_HIT_MELEE_RATING: "HitMeleeRating",
    MOD_HIT_RANGED_RATING: "HitRangedRating",
    MOD_HIT_SPELL_RATING: "HitSpellRating",
    MOD_CRIT_MELEE_RATING: "CritMeleeRating",
    MOD_CRIT_RANGED_RATING: "CritRangedRating",
    MOD_CRIT_SPELL_RATING: "CritSpellRating",
    MOD_CORRUPTION: "Corruption",
    MOD_CORRUPTION_RESISTANCE: "CorruptionResistance",
    MOD_MODIFIED_CRAFTING_STAT_1: "ModifiedCraftingStat1",
    MOD_MODIFIED_CRAFTING_STAT_2: "ModifiedCraftingStat2",
    MOD_CRIT_TAKEN_RANGED_RATING: "CritTakenRangedRating",
    MOD_CRIT_TAKEN_SPELL_RATING: "CritTakenSpellRating",
    MOD_HASTE_MELEE_RATING: "HasteMeleeRating",
    MOD_HASTE_RANGED_RATING: "HasteRangedRating",
    MOD_HASTE_SPELL_RATING: "HasteSpellRating",
    MOD_HIT_RATING: "HitRating",
    MOD_CRIT_RATING: "CritRating",
    MOD_HIT_TAKEN_RATING: "HitTakenRating",
    MOD_CRIT_TAKEN_RATING: "CritTakenRating",
    MOD_RESILIENCE_RATING: "ResilienceRating",
    MOD_HASTE_RATING: "HasteRating",
    MOD_EXPERTISE_RATING: "ExpertiseRating",
    MOD_ATTACK_POWER: "AttackPower",
    MOD_RANGED_ATTACK_POWER: "RangedAttackPower",
    MOD_VERSATILITY: "Versatility",
    MOD_SPELL_HEALING_DONE: "SpellHealingDone",
    MOD_SPELL_DAMAGE_DONE: "SpellDamageDone",
    MOD_MANA_REGENERATION: "ManaRegeneration",
    MOD_ARMOR_PENETRATION_RATING: "ArmorPenetrationRating",
    MOD_SPELL_POWER: "SpellPower",
    MOD_HEALTH_REGEN: "HealthRegen",
    MOD_SPELL_PENETRATION: "SpellPenetration",
    MOD_BLOCK_VALUE: "BlockValue",
    MOD_MASTERY_RATING: "MasteryRating",
    MOD_EXTRA_ARMOR: "ExtraArmor",
    MOD_FIRE_RESISTANCE: "FireResistance",
    MOD_FROST_RESISTANCE: "FrostResistance",
    MOD_HOLY_RESISTANCE: "HolyResistance",
    MOD_SHADOW_RESISTANCE: "ShadowResistance",
    MOD_NATURE_RESISTANCE: "NatureResistance",
    MOD_ARCANE_RESISTANCE: "ArcaneResistance",
    MOD_PVP_POWER: "PvpPower",
    MOD_CR_SPEED: "Speed",
    MOD_CR_LIFESTEAL: "Leech",
    MOD_CR_AVOIDANCE: "Avoidance",
    MOD_CR_STURDINESS: "Sturdiness",
    MOD_AGI_STR_INT: "Agi|Str|Int",
    MOD_AGI_STR: "Agi|Str",
    MOD_AGI_INT: "Agi|Int",
    MOD_STR_INT: "Str|Int",
    MOD_PROFESSION_INSPIRATION: "ProfessionInspiration",
}

# Player::_ApplyItemBonuses applies the CombatRatingsMultByILvl multiplier to
# exactly this set (src/server/game/Entities/Player/Player.cpp).
RATING_MULTIPLIED_MODS = frozenset({
    MOD_DEFENSE_SKILL_RATING, MOD_DODGE_RATING, MOD_PARRY_RATING,
    MOD_BLOCK_RATING, MOD_HIT_MELEE_RATING, MOD_HIT_RANGED_RATING,
    MOD_HIT_SPELL_RATING, MOD_CRIT_MELEE_RATING, MOD_CRIT_RANGED_RATING,
    MOD_CRIT_SPELL_RATING, MOD_HIT_RATING, MOD_CRIT_RATING,
    MOD_HIT_TAKEN_RATING, MOD_CRIT_TAKEN_RATING, MOD_RESILIENCE_RATING,
    MOD_HASTE_RATING, MOD_EXPERTISE_RATING, MOD_VERSATILITY,
    MOD_MASTERY_RATING, MOD_CR_SPEED, MOD_CR_LIFESTEAL, MOD_CR_AVOIDANCE,
    MOD_CR_STURDINESS,
})

# Item::GetItemStatValue returns StatPercentEditor verbatim for these.
UNSCALED_MODS = frozenset({MOD_CORRUPTION, MOD_CORRUPTION_RESISTANCE})

PRIMARY_MODS = frozenset({
    MOD_AGILITY, MOD_STRENGTH, MOD_INTELLECT, MOD_SPIRIT,
    MOD_AGI_STR_INT, MOD_AGI_STR, MOD_AGI_INT, MOD_STR_INT,
})

# src/server/game/Entities/Unit/Unit.h :: enum CombatRating.  The index is also
# the zero-based column offset inside CombatRatings.txt after the Level column.
CR_NAMES = [
    "Amplify", "DefenseSkill", "Dodge", "Parry", "Block",
    "HitMelee", "HitRanged", "HitSpell", "CritMelee", "CritRanged",
    "CritSpell", "Corruption", "CorruptionResistance", "Speed",
    "ResilienceCritTaken", "ResiliencePlayerDamage", "Lifesteal",
    "HasteMelee", "HasteRanged", "HasteSpell", "Avoidance", "Sturdiness",
    "Unused7", "Expertise", "ArmorPenetration", "Mastery", "PvPPower",
    "Cleave", "VersatilityDamageDone", "VersatilityHealingDone",
    "VersatilityDamageTaken", "Unused12",
]
CR_INDEX = {name: i for i, name in enumerate(CR_NAMES)}

# Player::_ApplyItemBonuses / Player::ApplyEnchantment: the ITEM_MOD -> combat
# rating routing.  A stat can feed more than one rating slot.
MOD_TO_RATINGS: dict[int, tuple[str, ...]] = {
    MOD_DEFENSE_SKILL_RATING: ("DefenseSkill",),
    MOD_DODGE_RATING: ("Dodge",),
    MOD_PARRY_RATING: ("Parry",),
    MOD_BLOCK_RATING: ("Block",),
    MOD_HIT_MELEE_RATING: ("HitMelee",),
    MOD_HIT_RANGED_RATING: ("HitRanged",),
    MOD_HIT_SPELL_RATING: ("HitSpell",),
    MOD_CRIT_MELEE_RATING: ("CritMelee",),
    MOD_CRIT_RANGED_RATING: ("CritRanged",),
    MOD_CRIT_SPELL_RATING: ("CritSpell",),
    MOD_CRIT_TAKEN_RANGED_RATING: ("ResiliencePlayerDamage",),
    MOD_HASTE_MELEE_RATING: ("HasteMelee",),
    MOD_HASTE_RANGED_RATING: ("HasteRanged",),
    MOD_HASTE_SPELL_RATING: ("HasteSpell",),
    MOD_HIT_RATING: ("HitMelee", "HitRanged", "HitSpell"),
    MOD_CRIT_RATING: ("CritMelee", "CritRanged", "CritSpell"),
    MOD_RESILIENCE_RATING: ("ResiliencePlayerDamage",),
    MOD_HASTE_RATING: ("HasteMelee", "HasteRanged", "HasteSpell"),
    MOD_EXPERTISE_RATING: ("Expertise",),
    MOD_VERSATILITY: ("VersatilityDamageDone", "VersatilityDamageTaken",
                      "VersatilityHealingDone"),
    MOD_ARMOR_PENETRATION_RATING: ("ArmorPenetration",),
    MOD_MASTERY_RATING: ("Mastery",),
    MOD_PVP_POWER: ("PvPPower",),
    MOD_CORRUPTION: ("Corruption",),
    MOD_CORRUPTION_RESISTANCE: ("CorruptionResistance",),
    MOD_CR_SPEED: ("Speed",),
    MOD_CR_LIFESTEAL: ("Lifesteal",),
    MOD_CR_AVOIDANCE: ("Avoidance",),
    MOD_CR_STURDINESS: ("Sturdiness",),
}

# src/server/game/DataStores/DBCEnums.h :: enum class GlobalCurve
GLOBAL_CURVE_NAMES = {
    0: "CritDiminishing", 1: "MasteryDiminishing", 2: "HasteDiminishing",
    3: "SpeedDiminishing", 4: "AvoidanceDiminishing",
    5: "VersatilityDoneDiminishing", 6: "LifestealDiminishing",
    7: "DodgeDiminishing", 8: "BlockDiminishing", 9: "ParryDiminishing",
    11: "VersatilityTakenDiminishing",
    13: "ContentTuningPvpItemLevelHealthScaling",
    14: "ContentTuningPvpLevelDamageScaling",
    15: "ContentTuningPvpItemLevelDamageScaling",
    18: "ArmorItemLevelDiminishing",
    21: "ChallengeModeHealth", 22: "ChallengeModeDamage",
    23: "MythicPlusEndOfRunGearSequenceLevel",
    26: "SpellAreaEffectWarningRadius",
    37: "HouseLevelFavorForLevel", 38: "HouseInteriorDecorBudget",
    39: "HouseExteriorDecorBudget", 40: "HouseRoomPlacementBudget",
    41: "HouseFixtureBudget", 43: "TransmogCost", 46: "MaxHouseSizeForLevel",
}

# The rating whose diminishing curve applies, by GlobalCurve type.
RATING_DIMINISHING_GLOBAL_CURVE = {
    "Dodge": 7, "Parry": 9, "Block": 8,
    "CritMelee": 0, "CritRanged": 0, "CritSpell": 0,
    "Speed": 3, "Lifesteal": 6,
    "HasteMelee": 2, "HasteRanged": 2, "HasteSpell": 2,
    "Avoidance": 4, "Mastery": 1,
    "VersatilityDamageDone": 5, "VersatilityHealingDone": 5,
    "VersatilityDamageTaken": 11,
}

# src/server/game/DataStores/DBCEnums.h :: enum ItemEnchantmentType
ENCHANT_TYPE_NAMES = {
    0: "None", 1: "CombatSpell", 2: "Damage", 3: "EquipSpell",
    4: "Resistance", 5: "Stat", 6: "Totem", 7: "UseSpell",
    8: "PrismaticSocket", 9: "ArtifactPowerBonusRankByType",
    10: "ArtifactPowerBonusRankById", 11: "BonusListId",
    12: "BonusListCurve", 13: "ArtifactPowerBonusRankPicker",
}

# src/server/game/Entities/Item/ItemTemplate.h :: enum ItemSpelltriggerType
ITEM_SPELLTRIGGER_NAMES = {
    0: "OnUse", 1: "OnEquip", 2: "OnProc", 3: "SummonedBySpell",
    4: "OnDeath", 5: "OnPickup", 6: "OnLearn", 7: "OnLooted",
    8: "TeachMount", 9: "OnPickupForced", 10: "OnLootedForced",
}

# src/server/game/Entities/Item/ItemTemplate.h :: MIN_ITEM_LEVEL / MAX_ITEM_LEVEL
MIN_ITEM_LEVEL = 1
MAX_ITEM_LEVEL = 1300

# src/server/game/DataStores/DBCEnums.h :: MAX_LEVEL
MAX_LEVEL = 123
# src/server/game/Miscellaneous/SharedDefines.h :: CURRENT_EXPANSION / GetMaxLevelForExpansion
CURRENT_EXPANSION = 11
EXPANSION_MAX_LEVEL = {
    0: 30, 1: 30, 2: 30, 3: 35, 4: 35, 5: 40, 6: 45, 7: 50,
    8: 60, 9: 70, 10: 80, 11: 90,
}
DEFAULT_PLAYER_LEVEL = EXPANSION_MAX_LEVEL[CURRENT_EXPANSION]

MAX_ITEM_PROTO_STATS = 10
MAX_ITEM_PROTO_SOCKETS = 3
MAX_ITEM_ENCHANTMENT_EFFECTS = 3

# Item::SetGem uses this literal.
CURVE_ID_ARTIFACT_RELIC_ITEM_LEVEL_BONUS = 1718

# src/server/game/DataStores/DBCEnums.h :: enum class ItemContext
ITEM_CONTEXT_NAMES = {
    0: "NONE", 1: "Dungeon_Normal", 2: "Dungeon_Heroic", 3: "Raid_Normal",
    4: "Raid_Raid_Finder", 5: "Raid_Heroic", 6: "Raid_Mythic",
    7: "PVP_Unranked_1", 8: "PVP_Ranked_1_Unrated", 9: "Scenario_Normal",
    10: "Scenario_Heroic", 11: "Quest_Reward", 12: "In_Game_Store",
    13: "Trade_Skill", 14: "Vendor", 15: "Black_Market",
    16: "MythicPlus_End_of_Run", 17: "Dungeon_Lvl_Up_1",
    18: "Dungeon_Lvl_Up_2", 19: "Dungeon_Lvl_Up_3", 20: "Dungeon_Lvl_Up_4",
    21: "Force_to_NONE", 22: "Timewalking", 23: "Dungeon_Mythic",
    24: "Pvp_Honor_Reward", 25: "World_Quest_1", 26: "World_Quest_2",
    27: "World_Quest_3", 28: "World_Quest_4", 29: "World_Quest_5",
    30: "World_Quest_6", 31: "Mission_Reward_1", 32: "Mission_Reward_2",
    33: "MythicPlus_End_of_Run_Time_Chest",
    34: "MythicPlus_Timewalking_End_of_Run", 35: "MythicPlus_Jackpot",
    36: "World_Quest_7", 37: "World_Quest_8", 38: "PVP_Ranked_2_Combatant",
    39: "PVP_Ranked_4_Challenger", 40: "PVP_Ranked_6_Rival",
    41: "PVP_Unranked_2", 42: "World_Quest_9", 43: "World_Quest_10",
    44: "PVP_Ranked_8_Duelist", 45: "PVP_Ranked_9_Elite",
    46: "PVP_Ranked_3_Combatant", 47: "PVP_Unranked_3", 48: "PVP_Unranked_4",
    49: "PVP_Unranked_5", 50: "PVP_Unranked_6", 51: "PVP_Unranked_7",
    52: "PVP_Ranked_5_Challenger", 53: "World_Quest_11",
    54: "World_Quest_12", 55: "World_Quest_13", 56: "PVP_Ranked_Jackpot",
    57: "Tournament_Realm_1", 58: "Relinquished", 59: "Legendary_Forge",
    60: "Quest_Bonus_Loot", 61: "Character_Boost_Dragonflight_70",
    62: "Character_Boost_Shadowlands_50", 63: "Legendary_Crafting_1",
    64: "Legendary_Crafting_2", 65: "Legendary_Crafting_3",
    66: "Legendary_Crafting_4", 67: "Legendary_Crafting_5",
    68: "Legendary_Crafting_6", 69: "Legendary_Crafting_7",
    70: "Legendary_Crafting_8", 71: "Legendary_Crafting_9",
    72: "Weekly_Rewards_Additional", 73: "Weekly_Rewards_Concession",
    74: "World_Quest_Jackpot", 75: "New_Character", 76: "War_Mode",
    77: "PvP_Brawl_1", 78: "PvP_Brawl_2", 79: "Torghast",
    80: "Corpse_Recovery", 81: "World_Boss", 82: "Raid_Normal_Extended",
    83: "Raid_Raid_Finder_Extended", 84: "Raid_Heroic_Extended",
    85: "Raid_Mythic_Extended", 86: "Character_Boost_Shadowlands_60",
    87: "MythicPlus_Timewalking_End_of_Run_Time_Chest",
    88: "Pvp_Ranked_7_Rival", 89: "Raid_Normal_Extended_2",
    90: "Raid_Finder_Extended_2", 91: "Raid_Heroic_Extended_2",
    92: "Raid_Mythic_Extended_2", 93: "Raid_Normal_Extended_3",
    94: "Raid_Finder_Extended_3", 95: "Raid_Heroic_Extended_3",
    96: "Raid_Mythic_Extended_3", 97: "Template_Character_1",
    98: "Template_Character_2", 99: "Template_Character_3",
    100: "Template_Character_4", 101: "Dungeon_Normal_Jackpot",
    102: "Dungeon_Heroic_Jackpot", 103: "Dungeon_Mythic_Jackpot",
    104: "Delves_1", 105: "Timerunning", 106: "Delves_2", 107: "Delves_3",
    108: "Delves_Jackpot", 109: "Delves_Key_1", 110: "Delves_Key_2",
    111: "Delves_Key_3", 112: "Delves_Key_4", 113: "Delves_Key_5",
    114: "Delves_Key_6", 115: "Delves_Key_7", 116: "Delves_Key_8",
    117: "Delves_Bounty_1", 118: "Delves_Bounty_2", 119: "Delves_Bounty_3",
    120: "Delves_Bounty_4", 121: "Delves_Bounty_5", 122: "Delves_Bounty_6",
    123: "Delves_Bounty_7", 124: "Delves_Bounty_8", 125: "Delves_Level_Up_1",
    126: "Delves_Level_Up_2", 127: "Delves_Level_Up_3",
    128: "Delves_Level_Up_4", 129: "Delves_Bonus_1", 130: "Delves_Bonus_2",
    131: "Delves_Bonus_3", 132: "Delves_Bonus_4", 133: "Delves_Bonus_5",
    134: "Delves_Bonus_6", 135: "Delves_Bonus_7", 136: "Delves_Bonus_8",
    137: "Delves_Bonus_9", 138: "Delves_Bonus_10", 139: "Dungeon_Bonus_1",
    140: "Dungeon_Bonus_2", 141: "Dungeon_Bonus_3", 142: "Dungeon_Bonus_4",
    143: "Dungeon_Bonus_5", 144: "Dungeon_Bonus_6", 145: "Dungeon_Bonus_7",
    146: "Dungeon_Bonus_8", 147: "Dungeon_Bonus_9", 148: "Dungeon_Bonus_10",
    149: "Raid_Bonus_1", 150: "Raid_Bonus_2", 151: "Raid_Bonus_3",
    152: "Raid_Bonus_4", 153: "Raid_Bonus_5", 154: "Raid_Bonus_6",
    155: "Raid_Bonus_7", 156: "Raid_Bonus_8", 157: "Raid_Bonus_9",
    158: "Raid_Bonus_10", 159: "Dungeon_Hard_Mode_1",
    160: "Dungeon_Hard_Mode_2", 161: "Dungeon_Hard_Mode_3",
    162: "Tournament_Realm_2", 163: "Tournament_Realm_3",
    164: "Tournament_Realm_4", 165: "Warbound_1", 166: "Warbound_2",
    167: "Warbound_3", 168: "Warbound_4", 169: "Warbound_5",
    170: "Warbound_6", 171: "Warbound_7", 172: "Warbound_8",
    173: "Warbound_9", 174: "Warbound_10", 175: "Warbound_11",
    176: "Warbound_12", 177: "Warbound_13", 178: "Warbound_14",
    179: "Warbound_15", 180: "Warbound_16", 181: "Warbound_17",
    182: "Warbound_18", 183: "Warbound_19", 184: "Warbound_20",
}
ITEM_CONTEXT_NONE = 0
ITEM_CONTEXT_FORCE_TO_NONE = 21

# Contexts referenced by the hardcoded ApplyBonusTreeHelper switches.
CONTEXT_RAID_NORMAL = 3
CONTEXT_RAID_RAID_FINDER = 4
CONTEXT_RAID_HEROIC = 5
CONTEXT_RAID_MYTHIC = 6
CONTEXT_DUNGEON_NORMAL = 1
CONTEXT_DUNGEON_MYTHIC = 23


def context_name(value: int) -> str:
    return ITEM_CONTEXT_NAMES.get(value, f"Context_{value}")


class SourceError(RuntimeError):
    """Raised when required source rows are missing or self-contradictory."""


# --------------------------------------------------------------------------
# CSV / GameTable access
# --------------------------------------------------------------------------


def _coerce(text: str) -> Any:
    if text == "":
        return 0
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


class Table:
    """A CSV table loaded once, with lazily built indexes."""

    __slots__ = ("name", "path", "columns", "rows", "_by", "_multi")

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = path
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            self.columns = next(reader)
            coerce = _coerce
            cols = self.columns
            self.rows: list[dict[str, Any]] = [
                {c: coerce(v) for c, v in zip(cols, row)} for row in reader
            ]
        self._by: dict[str, dict[Any, dict[str, Any]]] = {}
        self._multi: dict[str, dict[Any, list[dict[str, Any]]]] = {}

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)

    def by(self, column: str) -> dict[Any, dict[str, Any]]:
        """Unique index.  Later rows win, mirroring DB2Storage overwrite order."""
        index = self._by.get(column)
        if index is None:
            index = {row[column]: row for row in self.rows}
            self._by[column] = index
        return index

    def group(self, column: str) -> dict[Any, list[dict[str, Any]]]:
        """Multi index preserving CSV row order (which is ascending ID)."""
        index = self._multi.get(column)
        if index is None:
            index = defaultdict(list)
            for row in self.rows:
                index[row[column]].append(row)
            index = dict(index)
            self._multi[column] = index
        return index

    def lookup(self, key: Any, column: str = "ID") -> dict[str, Any] | None:
        return self.by(column).get(key)


class GameTable:
    """A tab-separated GameTable (``*.txt``) keyed by its first column.

    Mirrors: ``GameTable<T>::GetRow`` in
    ``src/server/game/DataStores/GameTables.h`` -- row 0 is a synthetic
    zero row and ``GetRow(n)`` returns ``nullptr`` past the end.
    """

    __slots__ = ("name", "path", "columns", "rows")

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = path
        lines = path.read_text(encoding="utf-8").splitlines()
        self.columns = lines[0].split("\t")[1:]
        self.rows: dict[int, list[float]] = {}
        for line in lines[1:]:
            if not line.strip():
                continue
            parts = line.split("\t")
            self.rows[int(parts[0])] = [float(x) for x in parts[1:]]

    def row(self, index: int) -> list[float] | None:
        return self.rows.get(int(index))

    def column(self, index: int, column: int) -> float | None:
        row = self.row(index)
        return None if row is None else row[column]

    def row_count(self) -> int:
        return len(self.rows) + 1  # +1 for the implicit zero row in Trinity


class Tables:
    """Lazy accessor over a ``data/tables`` directory."""

    def __init__(self, root: Path | str = DEFAULT_TABLES) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise SourceError(f"table directory not found: {self.root}")
        self._csv: dict[str, Table] = {}
        self._gt: dict[str, GameTable] = {}

    def __call__(self, name: str) -> Table:
        table = self._csv.get(name)
        if table is None:
            path = self.root / f"{name}.csv"
            if not path.exists():
                raise SourceError(f"missing source table {name}.csv under {self.root}")
            table = Table(name, path)
            self._csv[name] = table
        return table

    def gametable(self, name: str) -> GameTable:
        table = self._gt.get(name)
        if table is None:
            path = self.root / f"{name}.txt"
            if not path.exists():
                raise SourceError(f"missing game table {name}.txt under {self.root}")
            table = GameTable(name, path)
            self._gt[name] = table
        return table


# --------------------------------------------------------------------------
# Curves
# --------------------------------------------------------------------------

LINEAR = "Linear"
COSINE = "Cosine"
CATMULL_ROM = "CatmullRom"
BEZIER3 = "Bezier3"
BEZIER4 = "Bezier4"
BEZIER = "Bezier"
CONSTANT = "Constant"


@dataclass(frozen=True)
class CurveEval:
    """One curve evaluation with everything needed to re-check it by hand."""

    curve_id: int
    curve_type: int
    mode: str
    x: float
    y: float
    point_count: int
    bracket: tuple[tuple[float, float], ...]
    clamped: str | None
    consumer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "curve_id": self.curve_id,
            "curve_type": self.curve_type,
            "interpolation": self.mode,
            "x": self.x,
            "raw_y": self.y,
            "point_count": self.point_count,
            "surrounding_points": [list(p) for p in self.bracket],
            "clamped": self.clamped,
            "consumer": self.consumer,
        }


class Curves:
    """Curve store and evaluator.

    Mirrors: ``DB2Manager::LoadStores`` curve-point assembly and
    ``DB2Manager::GetCurveValueAt`` / ``DetermineCurveType`` in
    ``src/server/game/DataStores/DB2Stores.cpp``.
    """

    def __init__(self, tables: Tables) -> None:
        curve_rows = tables("Curve")
        self._curves = {row["ID"]: row for row in curve_rows}
        points: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in tables("CurvePoint"):
            if row["CurveID"] in self._curves:
                points[row["CurveID"]].append(row)
        self._points: dict[int, list[tuple[float, float]]] = {}
        for curve_id, rows in points.items():
            # Trinity sorts by OrderIndex, *not* by X.
            rows.sort(key=lambda r: r["OrderIndex"])
            self._points[curve_id] = [
                (float(r["Pos_0"]), float(r["Pos_1"])) for r in rows
            ]
        self.evaluations: list[CurveEval] = []

    def __contains__(self, curve_id: int) -> bool:
        return curve_id in self._points

    def points(self, curve_id: int) -> list[tuple[float, float]]:
        return self._points.get(curve_id, [])

    def curve_type(self, curve_id: int) -> int:
        row = self._curves.get(curve_id)
        return int(row["Type"]) if row else -1

    def x_range(self, curve_id: int) -> tuple[float, float]:
        pts = self._points.get(curve_id)
        if not pts:
            return (0.0, 0.0)
        return (pts[0][0], pts[-1][0])

    def mode(self, curve_id: int) -> str:
        pts = self._points.get(curve_id, [])
        row = self._curves.get(curve_id)
        if row is None:
            return CONSTANT
        return _determine_curve_type(int(row["Type"]), len(pts))

    def value_at(self, curve_id: int, x: float, consumer: str = "") -> float:
        """Evaluate and record provenance.  Unknown curve -> 0.0 (Trinity parity)."""
        pts = self._points.get(curve_id)
        if not pts:
            self.evaluations.append(CurveEval(
                curve_id=curve_id, curve_type=self.curve_type(curve_id),
                mode="Unknown", x=float(x), y=0.0, point_count=0,
                bracket=(), clamped="unknown-curve", consumer=consumer))
            return 0.0
        mode = self.mode(curve_id)
        y, bracket, clamped = _interpolate(mode, pts, float(x))
        self.evaluations.append(CurveEval(
            curve_id=curve_id, curve_type=self.curve_type(curve_id), mode=mode,
            x=float(x), y=y, point_count=len(pts), bracket=tuple(bracket),
            clamped=clamped, consumer=consumer))
        return y


def _determine_curve_type(curve_type: int, point_count: int) -> str:
    """Mirrors: ``DetermineCurveType`` (DB2Stores.cpp)."""
    if curve_type == 1:
        return COSINE if point_count < 4 else CATMULL_ROM
    if curve_type == 2:
        if point_count == 1:
            return CONSTANT
        if point_count == 2:
            return LINEAR
        if point_count == 3:
            return BEZIER3
        if point_count == 4:
            return BEZIER4
        return BEZIER
    if curve_type == 3:
        return COSINE
    return LINEAR if point_count != 1 else CONSTANT


def _interpolate(
    mode: str, points: Sequence[tuple[float, float]], x: float
) -> tuple[float, list[tuple[float, float]], str | None]:
    """Mirrors: ``DB2Manager::GetCurveValueAt(CurveInterpolationMode, ...)``."""
    n = len(points)
    if mode in (LINEAR, COSINE):
        i = 0
        while i < n and points[i][0] <= x:
            i += 1
        if i == 0:
            return points[0][1], [points[0]], "below-first-point"
        if i >= n:
            return points[-1][1], [points[-1]], "above-last-point"
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        dx = x1 - x0
        if dx == 0.0:
            return y1, [points[i - 1], points[i]], "zero-width-segment"
        if mode == LINEAR:
            return ((x - x0) / dx) * (y1 - y0) + y0, [points[i - 1], points[i]], None
        return (
            (y1 - y0) * (1.0 - math.cos((x - x0) / dx * math.pi)) * 0.5 + y0,
            [points[i - 1], points[i]],
            None,
        )
    if mode == CATMULL_ROM:
        i = 1
        while i < n and points[i][0] <= x:
            i += 1
        if i == 1:
            return points[1][1], [points[1]], "below-second-point"
        if i >= n - 1:
            return points[n - 2][1], [points[n - 2]], "above-penultimate-point"
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        dx = x1 - x0
        if dx == 0.0:
            return y1, [points[i - 1], points[i]], "zero-width-segment"
        mu = (x - x0) / dx
        pm2 = points[i - 2][1]
        pp1 = points[i + 1][1]
        a0 = -0.5 * pm2 + 1.5 * y0 - 1.5 * y1 + 0.5 * pp1
        a1 = pm2 - 2.5 * y0 + 2.0 * y1 - 0.5 * pp1
        a2 = -0.5 * pm2 + 0.5 * y1
        a3 = y0
        return (
            a0 * mu * mu * mu + a1 * mu * mu + a2 * mu + a3,
            [points[i - 2], points[i - 1], points[i], points[i + 1]],
            None,
        )
    if mode == BEZIER3:
        dx = points[2][0] - points[0][0]
        if dx == 0.0:
            return points[1][1], list(points[:3]), "zero-width-segment"
        mu = (x - points[0][0]) / dx
        y = ((1.0 - mu) ** 2) * points[0][1] + (1.0 - mu) * 2.0 * mu * points[1][1] + mu * mu * points[2][1]
        return y, list(points[:3]), None
    if mode == BEZIER4:
        dx = points[3][0] - points[0][0]
        if dx == 0.0:
            return points[1][1], list(points[:4]), "zero-width-segment"
        mu = (x - points[0][0]) / dx
        y = (
            ((1.0 - mu) ** 3) * points[0][1]
            + 3.0 * mu * ((1.0 - mu) ** 2) * points[1][1]
            + 3.0 * mu * mu * (1.0 - mu) * points[2][1]
            + mu ** 3 * points[3][1]
        )
        return y, list(points[:4]), None
    if mode == BEZIER:
        dx = points[-1][0] - points[0][0]
        if dx == 0.0:
            return points[-1][1], list(points), "zero-width-segment"
        mu = (x - points[0][0]) / dx
        tmp = [p[1] for p in points]
        i = len(points) - 1
        while i > 0:
            for k in range(i):
                tmp[k] = tmp[k] + mu * (tmp[k + 1] - tmp[k])
            i -= 1
        return tmp[0], list(points), None
    # CONSTANT
    return points[0][1], [points[0]], "constant"


def round_half_away(value: float) -> int:
    """C's ``std::round``: halfway cases go away from zero (not banker's)."""
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


# --------------------------------------------------------------------------
# Item template (Item.db2 + ItemSparse.db2)
# --------------------------------------------------------------------------


@dataclass
class ItemTemplate:
    """Mirrors: ``struct ItemTemplate`` (Entities/Item/ItemTemplate.h).

    ``basic`` is the Item.db2 row (identity/class), ``sparse`` is the
    ItemSparse.db2 row (gameplay payload).  ``effects`` is the ItemEffect list
    assembled through ItemXItemEffect, in Trinity's insertion order.
    """

    item_id: int
    basic: dict[str, Any]
    sparse: dict[str, Any]
    effects: list[dict[str, Any]] = field(default_factory=list)

    # -- Item.db2 --------------------------------------------------------
    @property
    def class_id(self) -> int:
        return int(self.basic["ClassID"])

    @property
    def subclass_id(self) -> int:
        return int(self.basic["SubclassID"])

    # -- ItemSparse.db2 --------------------------------------------------
    @property
    def name(self) -> str:
        return str(self.sparse.get("Display_lang", "") or "")

    @property
    def quality(self) -> int:
        return int(self.sparse["OverallQualityID"])

    @property
    def inventory_type(self) -> int:
        return int(self.sparse["InventoryType"])

    @property
    def base_item_level(self) -> int:
        return int(self.sparse["ItemLevel"])

    @property
    def base_required_level(self) -> int:
        return int(self.sparse["RequiredLevel"])

    @property
    def delay(self) -> int:
        return int(self.sparse["ItemDelay"])

    @property
    def dmg_variance(self) -> float:
        return float(self.sparse["DmgVariance"])

    @property
    def item_set(self) -> int:
        return int(self.sparse["ItemSet"])

    @property
    def gem_properties(self) -> int:
        return int(self.sparse["Gem_properties"])

    @property
    def socket_bonus(self) -> int:
        return int(self.sparse["Socket_match_enchantment_ID"])

    @property
    def expansion_id(self) -> int:
        return int(self.sparse["ExpansionID"])

    def flags(self, index: int) -> int:
        return int(self.sparse[f"Flags_{index}"])

    def has_flag(self, index: int, mask: int) -> bool:
        return (self.flags(index) & mask) != 0

    # ITEM_FLAG2_CASTER_WEAPON == 0x200 in Flags[1]
    @property
    def is_caster_weapon(self) -> bool:
        return self.has_flag(1, 0x00000200)

    # ITEM_FLAG4_CC_TRINKET == 0x4000 in Flags[3]
    @property
    def is_cc_trinket(self) -> bool:
        return self.has_flag(3, 0x02000000)

    # ITEM_FLAG3_IGNORE_ITEM_LEVEL_CAP_IN_PVP == 0x10000000 in Flags[2]
    @property
    def ignores_pvp_item_level_cap(self) -> bool:
        return self.has_flag(2, 0x00000100)

    # ITEM_FLAG_LEGACY == 0x20000000 in Flags[0]
    @property
    def is_legacy(self) -> bool:
        return self.has_flag(0, 0x00000100)

    def socket_color(self, index: int) -> int:
        return int(self.sparse[f"SocketType_{index}"])

    def stat_type(self, index: int) -> int:
        return int(self.sparse[f"StatModifier_bonusStat_{index}"])

    def stat_percent_editor(self, index: int) -> int:
        return int(self.sparse[f"StatPercentEditor_{index}"])

    def stat_percentage_of_socket(self, index: int) -> float:
        return float(self.sparse[f"StatPercentageOfSocket_{index}"])


class ItemStore:
    """Assembles ItemTemplates the way ``ObjectMgr::LoadItemTemplates`` does."""

    def __init__(self, tables: Tables) -> None:
        self.tables = tables
        self._item = tables("Item").by("ID")
        self._sparse = tables("ItemSparse").by("ID")
        self._effect = tables("ItemEffect").by("ID")
        self._x_effect = tables("ItemXItemEffect")
        self._effects_by_item: dict[int, list[dict[str, Any]]] | None = None
        self._cache: dict[int, ItemTemplate] = {}

    def _effect_index(self) -> dict[int, list[dict[str, Any]]]:
        if self._effects_by_item is None:
            index: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in self._x_effect:
                effect = self._effect.get(row["ItemEffectID"])
                if effect is None:
                    continue
                bucket = index[row["ItemID"]]
                # Mirrors: ObjectMgr::LoadItemTemplates inserts at
                # lower_bound(LegacySlotIndex), so equal slots end up in
                # reverse cross-table order.
                keys = [e["LegacySlotIndex"] for e in bucket]
                bucket.insert(bisect_left(keys, effect["LegacySlotIndex"]), effect)
            self._effects_by_item = dict(index)
        return self._effects_by_item

    def get(self, item_id: int) -> ItemTemplate:
        cached = self._cache.get(item_id)
        if cached is not None:
            return cached
        basic = self._item.get(item_id)
        sparse = self._sparse.get(item_id)
        if basic is None and sparse is None:
            raise SourceError(f"unknown ItemID {item_id}: absent from Item.db2 and ItemSparse.db2")
        if basic is None:
            raise SourceError(f"ItemID {item_id} has an ItemSparse row but no Item row")
        if sparse is None:
            raise SourceError(f"ItemID {item_id} has an Item row but no ItemSparse row")
        template = ItemTemplate(item_id, basic, sparse,
                                list(self._effect_index().get(item_id, ())))
        self._cache[item_id] = template
        return template


# --------------------------------------------------------------------------
# BonusData
# --------------------------------------------------------------------------

_INT_MAX = 2 ** 31 - 1


@dataclass
class AppliedBonus:
    bonus_list_id: int
    bonus_id: int
    type: int
    values: tuple[int, int, int, int]
    order_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "bonus_list_id": self.bonus_list_id,
            "item_bonus_id": self.bonus_id,
            "type": self.type,
            "type_name": BONUS_TYPE_NAMES.get(self.type, str(self.type)),
            "values": list(self.values),
            "order_index": self.order_index,
        }


class BonusData:
    """Mirrors: ``struct BonusData`` and ``BonusData::AddBonus`` (Item.cpp).

    Field names are kept identical to Trinity's so the port can be diffed
    line by line against ``BonusData::Initialize`` / ``BonusData::AddBonus``.
    """

    def __init__(self, proto: ItemTemplate, resolver: "ItemResolver") -> None:
        self._resolver = resolver
        self.proto = proto
        self.trace: list[AppliedBonus] = []
        self.unhandled: list[AppliedBonus] = []

        self.Quality = proto.quality
        self.ItemLevel = proto.base_item_level
        self.ItemLevelBonus = 0
        self.RequiredLevel = proto.base_required_level
        self.ItemStatType = [proto.stat_type(i) for i in range(MAX_ITEM_PROTO_STATS)]
        self.StatPercentEditor = [proto.stat_percent_editor(i) for i in range(MAX_ITEM_PROTO_STATS)]
        self.ItemStatSocketCostMultiplier = [
            proto.stat_percentage_of_socket(i) for i in range(MAX_ITEM_PROTO_STATS)
        ]
        self.SocketColor = [proto.socket_color(i) for i in range(MAX_ITEM_PROTO_SOCKETS)]
        self.Bonding = int(proto.sparse["Bonding"])
        self.AppearanceModID = 0
        self.RepairCostMultiplier = 1.0
        self.ContentTuningId = int(proto.sparse["ContentTuningID"])
        self.PlayerLevelToItemLevelCurveId = int(proto.sparse["PlayerLevelToItemLevelCurveID"])
        self.DisenchantLootId = 0
        self.GemItemLevelBonus = [0] * MAX_ITEM_PROTO_SOCKETS
        self.GemRelicType = [-1] * MAX_ITEM_PROTO_SOCKETS
        self.GemRelicRankBonus = [0] * MAX_ITEM_PROTO_SOCKETS
        self.RelicType = -1
        self.RequiredLevelOverride = 0
        self.AzeriteTierUnlockSetId = 0
        self.Suffix = 0
        self.RequiredLevelCurve = 0
        self.PvpItemLevel = 0
        self.PvpItemLevelBonus = 0
        self.ItemLevelOffsetCurveId = int(proto.sparse["ItemLevelOffsetCurveID"])
        self.ItemLevelOffsetItemLevel = int(proto.sparse["ItemLevelOffsetItemLevel"])
        self.ItemLevelOffset = 0
        self.ItemSquishEraID = int(proto.sparse["ItemSquishEraID"])
        self.Effects: list[dict[str, Any]] = list(proto.effects)
        self.LimitCategory = int(proto.sparse["LimitCategory"])
        self.CanDisenchant = not proto.has_flag(0, 0x00008000)  # ITEM_FLAG_NO_DISENCHANT
        self.CanScrap = proto.has_flag(3, 0x00000020)      # ITEM_FLAG4_SCRAPABLE
        self.CanSalvage = not proto.has_flag(3, 0x00800000)  # ITEM_FLAG4_NO_SALVAGE
        self.CanRecraft = proto.has_flag(3, 0x01000000)      # ITEM_FLAG4_RECRAFTABLE
        self.HasFixedLevel = False
        self.CannotTradeBindOnPickup = proto.has_flag(1, 0x00000040)
        self.IgnoreSquish = False
        self.AppliedBonusLists: list[int] = []

        self._suffix_priority = _INT_MAX
        self._appearance_priority = _INT_MAX
        self._disenchant_priority = _INT_MAX
        self._scaling_priority = _INT_MAX
        self._azerite_priority = _INT_MAX
        self._required_level_curve_priority = _INT_MAX
        self._item_level_priority = _INT_MAX
        self._pvp_item_level_priority = _INT_MAX
        self._bonding_priority = _INT_MAX
        self._has_quality_bonus = False
        self._has_item_limit_category = False

    # -- application ----------------------------------------------------
    def add_bonus_list(self, bonus_list_id: int, *, depth: int = 0) -> None:
        """Mirrors: ``BonusData::AddBonusList``."""
        if depth > 16:
            raise SourceError(
                f"bonus list recursion exceeded depth 16 at list {bonus_list_id}")
        self.AppliedBonusLists.append(bonus_list_id)
        for row in self._resolver.bonuses_for_list(bonus_list_id):
            applied = AppliedBonus(
                bonus_list_id=bonus_list_id,
                bonus_id=int(row["ID"]),
                type=int(row["Type"]),
                values=(int(row["Value_0"]), int(row["Value_1"]),
                        int(row["Value_2"]), int(row["Value_3"])),
                order_index=int(row["OrderIndex"]),
            )
            self.add_bonus(applied, depth=depth)

    def add_bonus(self, applied: AppliedBonus, *, depth: int = 0) -> None:
        """Mirrors: ``BonusData::AddBonus`` (Item.cpp, one case per bonus type)."""
        self.trace.append(applied)
        t = applied.type
        v = applied.values
        if t == BONUS_ITEM_LEVEL:
            self.ItemLevelBonus += v[0]
        elif t == BONUS_STAT:
            index = MAX_ITEM_PROTO_STATS
            for i in range(MAX_ITEM_PROTO_STATS):
                if self.ItemStatType[i] == v[0] or self.ItemStatType[i] == -1:
                    index = i
                    break
            if index < MAX_ITEM_PROTO_STATS:
                self.ItemStatType[index] = v[0]
                self.StatPercentEditor[index] += v[1]
        elif t == BONUS_QUALITY:
            if not self._has_quality_bonus:
                self.Quality = v[0]
                self._has_quality_bonus = True
            elif self.Quality < v[0]:
                self.Quality = v[0]
        elif t == BONUS_SUFFIX:
            if v[1] < self._suffix_priority:
                self.Suffix = v[0]
                self._suffix_priority = v[1]
        elif t == BONUS_SOCKET:
            remaining = v[0]
            for i in range(MAX_ITEM_PROTO_SOCKETS):
                if remaining <= 0:
                    break
                if not self.SocketColor[i]:
                    self.SocketColor[i] = v[1]
                    remaining -= 1
        elif t == BONUS_APPEARANCE:
            if v[1] < self._appearance_priority:
                self.AppearanceModID = v[0]
                self._appearance_priority = v[1]
        elif t == BONUS_REQUIRED_LEVEL:
            self.RequiredLevel += v[0]
        elif t == BONUS_REPAIR_COST_MULTIPLIER:
            self.RepairCostMultiplier *= v[0] * 0.01
        elif t in (BONUS_SCALING_STAT_DISTRIBUTION, BONUS_SCALING_STAT_DISTRIBUTION_FIXED):
            if v[1] < self._scaling_priority:
                self.ContentTuningId = v[2]
                self.PlayerLevelToItemLevelCurveId = v[3]
                self._scaling_priority = v[1]
                self.HasFixedLevel = t == BONUS_SCALING_STAT_DISTRIBUTION_FIXED
        elif t == BONUS_DISENCHANT_LOOT_ID:
            if v[1] < self._disenchant_priority:
                self.DisenchantLootId = v[0]
                self._disenchant_priority = v[1]
        elif t == BONUS_BONDING:
            self.Bonding = v[0]
        elif t == BONUS_RELIC_TYPE:
            self.RelicType = v[0]
        elif t == BONUS_OVERRIDE_REQUIRED_LEVEL:
            self.RequiredLevelOverride = v[0]
        elif t == BONUS_AZERITE_TIER_UNLOCK_SET:
            if v[1] < self._azerite_priority:
                self.AzeriteTierUnlockSetId = v[0]
                self._azerite_priority = v[1]
        elif t == BONUS_OVERRIDE_CAN_DISENCHANT:
            self.CanDisenchant = v[0] != 0
        elif t == BONUS_OVERRIDE_CAN_SCRAP:
            self.CanScrap = v[0] != 0
        elif t == BONUS_ITEM_EFFECT_ID:
            effect = self._resolver.item_effect(v[0])
            if effect is not None:
                self.Effects.append(effect)
        elif t == BONUS_REQUIRED_LEVEL_CURVE:
            if v[2] < self._required_level_curve_priority:
                self.RequiredLevelCurve = v[0]
                self._required_level_curve_priority = v[2]
                if v[1]:
                    self.ContentTuningId = v[1]
        elif t == BONUS_ITEM_LIMIT_CATEGORY:
            if not self._has_item_limit_category:
                self.LimitCategory = v[0]
                self._has_item_limit_category = True
        elif t == BONUS_PVP_ITEM_LEVEL_INCREMENT:
            self.PvpItemLevelBonus += v[0]
        elif t == BONUS_OVERRIDE_CAN_SALVAGE:
            self.CanSalvage = v[0] != 0
        elif t == BONUS_OVERRIDE_CAN_RECRAFT:
            self.CanRecraft = v[0] != 0
        elif t == BONUS_ITEM_LEVEL_BASE:
            if v[1] < self._item_level_priority:
                self.ItemLevel = v[0]
                self._item_level_priority = v[1]
        elif t == BONUS_PVP_ITEM_LEVEL_BASE:
            if v[1] < self._pvp_item_level_priority:
                self.PvpItemLevel = v[0]
                self._pvp_item_level_priority = v[1]
        elif t == BONUS_OVERRIDE_CANNOT_TRADE_BOP:
            self.CannotTradeBindOnPickup = v[0] != 0
        elif t == BONUS_BONDING_WITH_PRIORITY:
            if v[1] < self._bonding_priority:
                self.Bonding = v[0]
                self._bonding_priority = v[1]
        elif t == BONUS_ITEM_OFFSET_CURVE:
            if v[3] < self._scaling_priority:
                self.ItemLevelOffsetCurveId = v[0]
                self.ItemLevelOffsetItemLevel = v[1]
                self._scaling_priority = v[3]
        elif t == BONUS_SCALING_CONFIG_AND_REQ_LEVEL:
            if v[1] < self._scaling_priority:
                config = self._resolver.scaling_config(v[0])
                if config is not None:
                    offset_curve = self._resolver.item_offset_curve(config["ItemOffsetCurveID"])
                    if offset_curve is not None:
                        self.ItemLevelOffsetCurveId = int(offset_curve["CurveID"])
                        self.ItemLevelOffset = int(offset_curve["Offset"])
                    self.ItemLevelOffsetItemLevel = int(config["ItemLevel"])
                    self.ItemSquishEraID = int(config["ItemSquishEraID"])
                    if int(config["Flags"]) & 0x1:
                        self.IgnoreSquish = True
                    if v[1] < self._required_level_curve_priority:
                        self.RequiredLevelOverride = int(config["RequiredLevel"])
                        self.RequiredLevelCurve = 0
        elif t == BONUS_ITEM_BONUS_LIST:
            self.add_bonus_list(v[0], depth=depth + 1)
        elif t == BONUS_SCALING_CONFIG:
            if v[1] < self._scaling_priority:
                config = self._resolver.scaling_config(v[0])
                if config is not None:
                    offset_curve = self._resolver.item_offset_curve(config["ItemOffsetCurveID"])
                    if offset_curve is not None:
                        self.ItemLevelOffsetCurveId = int(offset_curve["CurveID"])
                        self.ItemLevelOffset = int(offset_curve["Offset"])
                    self.ItemLevelOffsetItemLevel = 0
                    self.ItemSquishEraID = int(config["ItemSquishEraID"])
                    if int(config["Flags"]) & 0x1:
                        self.IgnoreSquish = True
        else:
            # Every remaining ItemBonusType is presentation-only or NYI in the
            # direct consumer; recorded so the census can report coverage.
            self.unhandled.append(applied)


# --------------------------------------------------------------------------
# Bonus tree resolution
# --------------------------------------------------------------------------


@dataclass
class BonusGenerationParams:
    """Mirrors: ``ItemBonusMgr::ItemBonusGenerationParams``."""

    context: int = ITEM_CONTEXT_NONE
    mythic_plus_keystone_level: int | None = None
    pvp_tier: int | None = None


@dataclass
class TreeStep:
    """One decision taken while walking the item's bonus trees."""

    tree_id: int
    node_id: int
    action: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"tree_id": self.tree_id, "node_id": self.node_id,
                "action": self.action, **self.detail}


@dataclass
class BonusListSelection:
    """Result of ``ItemBonusMgr::GetBonusListsForItem``."""

    bonus_list_ids: list[int]
    item_level_selector_id: int
    trace: list[TreeStep]
    trees_visited: list[int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "bonus_list_ids": list(self.bonus_list_ids),
            "item_level_selector_id": self.item_level_selector_id,
            "trees_visited": list(self.trees_visited),
            "trace": [s.to_dict() for s in self.trace],
        }


class ItemResolver:
    """The whole gearing pipeline for one ``data/tables`` snapshot.

    Every method carries a ``Mirrors:`` note naming the TrinityCore function it
    reproduces.  Nothing here is a guess from tooltip output.
    """

    def __init__(self, tables: Tables | Path | str = DEFAULT_TABLES) -> None:
        self.tables = tables if isinstance(tables, Tables) else Tables(tables)
        t = self.tables
        self.items = ItemStore(t)
        self.curves = Curves(t)

        self._item_bonus = t("ItemBonus").group("ParentItemBonusListID")
        self._item_effect = t("ItemEffect").by("ID")
        self._bonus_tree = t("ItemBonusTree").by("ID")
        self._bonus_tree_nodes = t("ItemBonusTreeNode").group("ParentItemBonusTreeID")
        self._item_to_tree: dict[int, list[int]] = defaultdict(list)
        for row in t("ItemXBonusTree"):
            self._item_to_tree[row["ItemID"]].append(row["ItemBonusTreeID"])
        self._bonus_list_group_entries = t("ItemBonusListGroupEntry").group("ItemBonusListGroupID")
        self._level_selector = t("ItemLevelSelector").by("ID")
        self._level_selector_quality_set = t("ItemLevelSelectorQualitySet").by("ID")
        self._level_selector_qualities: dict[int, list[dict[str, Any]]] = {}
        for row in t("ItemLevelSelectorQuality"):
            self._level_selector_qualities.setdefault(row["ParentILSQualitySetID"], []).append(row)
        for rows in self._level_selector_qualities.values():
            rows.sort(key=lambda r: r["Quality"])
        self._level_delta_to_bonus_list: dict[int, int] = {}
        for row in t("ItemBonusListLevelDelta"):
            # Mirrors: last row wins (plain map assignment in ItemBonusMgr::Load).
            self._level_delta_to_bonus_list[int(row["ItemLevelDelta"])] = int(row["ID"])
        self._context_by_group: dict[int, list[int]] = defaultdict(list)
        for row in t("ItemCreationContext"):
            self._context_by_group[row["ItemCreationContextGroupID"]].append(int(row["ItemContext"]))
        self._scaling_config = t("ItemScalingConfig").by("ID")
        self._item_offset_curve = t("ItemOffsetCurve").by("ID")
        self._azerite_unlock_mapping = t("AzeriteUnlockMapping").group("SetID")
        self._content_tuning = t("ContentTuning").by("ID")
        self._conditional_content_tuning = t("ConditionalContentTuning").group("ParentContentTuningID") \
            if (self.tables.root / "ConditionalContentTuning.csv").exists() else {}
        self._rand_prop_points = t("RandPropPoints").by("ID")
        self._global_curve = {int(r["Type"]): int(r["CurveID"]) for r in t("GlobalCurve")}
        self._item_squish_era = t("ItemSquishEra").by("ID")
        self._squish_era_count = len(t("ItemSquishEra"))

        self._armor_quality = t("ItemArmorQuality").by("ID")
        self._armor_total = t("ItemArmorTotal").by("ID")
        self._armor_shield = t("ItemArmorShield").by("ID")
        self._armor_location = t("ArmorLocation").by("ID")
        self._damage_tables = {
            "OneHand": t("ItemDamageOneHand").by("ID"),
            "OneHandCaster": t("ItemDamageOneHandCaster").by("ID"),
            "TwoHand": t("ItemDamageTwoHand").by("ID"),
            "TwoHandCaster": t("ItemDamageTwoHandCaster").by("ID"),
            "Ammo": t("ItemDamageAmmo").by("ID"),
        }

        self._gt_combat_ratings = t.gametable("CombatRatings")
        self._gt_combat_ratings_mult = t.gametable("CombatRatingsMultByILvl")
        self._gt_stamina_mult = t.gametable("StaminaMultByILvl")
        self._gt_socket_cost = t.gametable("ItemSocketCostPerLevel")
        self._gt_hp_per_sta = t.gametable("HpPerSta")

    # -- small accessors used by BonusData -------------------------------
    def bonuses_for_list(self, bonus_list_id: int) -> list[dict[str, Any]]:
        """Mirrors: ``ItemBonusMgr::GetItemBonuses``.

        Trinity keeps ItemBonus rows in DB2 load order (ascending ID); the CSV
        row order is the same, so no re-sort is applied.
        """
        return self._item_bonus.get(bonus_list_id, [])

    def item_effect(self, effect_id: int) -> dict[str, Any] | None:
        return self._item_effect.get(effect_id)

    def scaling_config(self, config_id: int) -> dict[str, Any] | None:
        return self._scaling_config.get(config_id)

    def item_offset_curve(self, curve_id: int) -> dict[str, Any] | None:
        return self._item_offset_curve.get(curve_id)

    def global_curve(self, curve_type: int) -> int:
        return self._global_curve.get(curve_type, 0)

    # -- ItemBonusMgr ----------------------------------------------------
    def _can_apply_bonus_tree_to_item(
        self, proto: ItemTemplate, tree_id: int, params: BonusGenerationParams
    ) -> bool:
        """Mirrors: ``ItemBonusMgr::CanApplyBonusTreeToItem``."""
        tree = self._bonus_tree.get(tree_id)
        if tree is not None:
            slot_mask = int(tree["InventoryTypeSlotMask"])
            if slot_mask and not ((1 << proto.inventory_type) & slot_mask):
                return False
            flags = int(tree["Flags"])
            if flags & 0x8 and not proto.is_caster_weapon:
                return False
            if flags & 0x10 and proto.is_caster_weapon:
                return False
            if flags & 0x20 and not proto.is_cc_trinket:
                return False
            if flags & 0x40 and proto.is_cc_trinket:
                return False
            if flags & 0x4:
                return True

        nodes = self._bonus_tree_nodes.get(tree_id)
        if nodes:
            any_matched = False
            for node in nodes:
                if int(node["MinMythicPlusLevel"]) > 0:
                    continue
                node_context = int(node["ItemContext"])
                if node_context == ITEM_CONTEXT_NONE or node_context == params.context:
                    if any_matched:
                        return False
                    any_matched = True
        return True

    def _apply_bonus_tree(
        self,
        proto: ItemTemplate,
        tree_id: int,
        params: BonusGenerationParams,
        sequence_level: int,
        state: dict[str, Any],
        depth: int = 0,
    ) -> None:
        """Mirrors: ``ItemBonusMgr::ApplyBonusTreeHelper``.

        ``GetBonusTreeIdOverride`` is a no-op in the direct consumer because
        ``passedTimeEvents`` is an empty local (``TODO: configure globally``),
        so ChallengeModeItemBonusOverride never fires.  That is reproduced here.
        """
        if depth > 32:
            raise SourceError(f"bonus tree recursion exceeded depth 32 at tree {tree_id}")
        original_tree_id = tree_id
        state["trees"].append(tree_id)

        if not self._can_apply_bonus_tree_to_item(proto, tree_id, params):
            state["trace"].append(TreeStep(tree_id, 0, "tree-rejected"))
            return

        nodes = self._bonus_tree_nodes.get(tree_id)
        if not nodes:
            state["trace"].append(TreeStep(tree_id, 0, "tree-has-no-nodes"))
            return

        for node in nodes:
            node_id = int(node["ID"])
            node_context = int(node["ItemContext"])
            flags = int(node["Flags"])
            required_context = node_context if node_context != ITEM_CONTEXT_FORCE_TO_NONE else ITEM_CONTEXT_NONE
            if node_context != ITEM_CONTEXT_NONE and params.context != required_context:
                if not (flags & 0x1):
                    state["trace"].append(TreeStep(
                        tree_id, node_id, "node-skipped-context",
                        {"node_context": node_context,
                         "node_context_name": context_name(node_context)}))
                    continue
            elif flags & 0x1 and node_context != ITEM_CONTEXT_NONE:
                state["trace"].append(TreeStep(
                    tree_id, node_id, "node-skipped-inverted-context",
                    {"node_context": node_context}))
                continue

            group_id = int(node["ItemCreationContextGroupID"])
            if group_id:
                has_context = params.context in self._context_by_group.get(group_id, ())
                if bool(flags & 0x1) == has_context:
                    state["trace"].append(TreeStep(
                        tree_id, node_id, "node-skipped-context-group",
                        {"item_creation_context_group_id": group_id,
                         "context_in_group": has_context}))
                    continue

            if params.mythic_plus_keystone_level is not None:
                min_mp = int(node["MinMythicPlusLevel"])
                max_mp = int(node["MaxMythicPlusLevel"])
                if min_mp and params.mythic_plus_keystone_level < min_mp:
                    state["trace"].append(TreeStep(
                        tree_id, node_id, "node-skipped-min-mythic-plus",
                        {"min_mythic_plus_level": min_mp}))
                    continue
                if max_mp and params.mythic_plus_keystone_level > max_mp:
                    state["trace"].append(TreeStep(
                        tree_id, node_id, "node-skipped-max-mythic-plus",
                        {"max_mythic_plus_level": max_mp}))
                    continue

            child_tree = int(node["ChildItemBonusTreeID"])
            child_list = int(node["ChildItemBonusListID"])
            child_selector = int(node["ChildItemLevelSelectorID"])
            child_group = int(node["ChildItemBonusListGroupID"])

            if child_tree:
                state["trace"].append(TreeStep(tree_id, node_id, "descend",
                                               {"child_tree_id": child_tree}))
                self._apply_bonus_tree(proto, child_tree, params, sequence_level,
                                       state, depth + 1)
            elif child_list:
                state["bonus_list_ids"].append(child_list)
                state["trace"].append(TreeStep(tree_id, node_id, "bonus-list",
                                               {"bonus_list_id": child_list}))
            elif child_selector:
                state["item_level_selector_id"] = child_selector
                state["trace"].append(TreeStep(tree_id, node_id, "item-level-selector",
                                               {"item_level_selector_id": child_selector}))
            elif child_group:
                resolved = self._resolve_sequence_level(
                    original_tree_id, node, params, sequence_level)
                picked = None
                for entry in self._bonus_list_group_entries.get(child_group, ()):
                    seq = int(entry["SequenceValue"])
                    if (resolved > 0 or seq <= 0) and resolved != seq:
                        continue
                    picked = entry
                    break
                if picked is None:
                    state["trace"].append(TreeStep(
                        tree_id, node_id, "bonus-list-group-no-match",
                        {"item_bonus_list_group_id": child_group,
                         "resolved_sequence_level": resolved}))
                    continue
                selector = int(picked["ItemLevelSelectorID"])
                bonus_list = int(picked["ItemBonusListID"])
                state["item_level_selector_id"] = selector
                state["bonus_list_ids"].append(bonus_list)
                state["trace"].append(TreeStep(
                    tree_id, node_id, "bonus-list-group",
                    {"item_bonus_list_group_id": child_group,
                     "resolved_sequence_level": resolved,
                     "group_entry_id": int(picked["ID"]),
                     "bonus_list_id": bonus_list,
                     "item_level_selector_id": selector}))

    def _resolve_sequence_level(
        self, original_tree_id: int, node: dict[str, Any],
        params: BonusGenerationParams, sequence_level: int,
    ) -> int:
        """Mirrors: the hardcoded tree-id switch inside ``ApplyBonusTreeHelper``.

        These are *policy in the consumer*, not source data: TrinityCore hard
        codes tree ids 4001/4079/4125/4126/4127/4128/4140 and, for 4079, six
        curve ids that map a Mythic+ keystone level onto a sequence value.
        """
        resolved = sequence_level
        if original_tree_id == 4001:
            return 1
        if original_tree_id == 4079:
            if params.mythic_plus_keystone_level is not None:
                mapping = {2909: 62951, 2910: 62952, 2911: 62954,
                           3007: 64388, 3008: 64389, 3009: 64395}
                curve_id = mapping.get(int(node["IblGroupPointsModSetID"]))
                if curve_id is not None:
                    return int(self.curves.value_at(
                        curve_id, params.mythic_plus_keystone_level,
                        consumer="ItemBonusMgr::ApplyBonusTreeHelper/MythicPlusSequenceLevel"))
            return resolved
        if original_tree_id == 4125:
            return 2
        if original_tree_id == 4126:
            return 3
        if original_tree_id == 4127:
            return 4
        if original_tree_id == 4128:
            if params.context in (CONTEXT_RAID_NORMAL, CONTEXT_RAID_RAID_FINDER,
                                  CONTEXT_RAID_HEROIC):
                return 2
            if params.context == CONTEXT_RAID_MYTHIC:
                return 6
            return resolved
        if original_tree_id == 4140:
            if params.context == CONTEXT_DUNGEON_NORMAL:
                return 2
            if params.context == CONTEXT_DUNGEON_MYTHIC:
                return 4
            return resolved
        return resolved

    def _azerite_unlock_bonus_list(
        self, set_id: int, min_item_level: int, inventory_type: int
    ) -> int:
        """Mirrors: ``ItemBonusMgr::GetAzeriteUnlockBonusList``."""
        if not set_id:
            return 0
        selected = None
        for row in self._azerite_unlock_mapping.get(set_id, ()):
            if min_item_level < int(row["MinItemLevel"]):
                continue
            if selected is not None and int(selected["MinItemLevel"]) > int(row["MinItemLevel"]):
                continue
            selected = row
        if selected is None:
            return 0
        if inventory_type == INVTYPE_HEAD:
            return int(selected["HeadBonus"])
        if inventory_type == INVTYPE_SHOULDERS:
            return int(selected["ShoulderBonus"])
        if inventory_type in (INVTYPE_CHEST, INVTYPE_ROBE):
            return int(selected["ChestBonus"])
        return 0

    def bonus_lists_for_item(
        self, item_id: int, params: BonusGenerationParams
    ) -> BonusListSelection:
        """Mirrors: ``ItemBonusMgr::GetBonusListsForItem``."""
        proto = self.items.get(item_id)
        state: dict[str, Any] = {
            "bonus_list_ids": [], "item_level_selector_id": 0,
            "trace": [], "trees": [],
        }
        for tree_id in self._item_to_tree.get(item_id, ()):
            self._apply_bonus_tree(proto, tree_id, params, 0, state)

        selector_id = state["item_level_selector_id"]
        selector = self._level_selector.get(selector_id)
        if selector is not None:
            min_item_level = int(selector["MinItemLevel"])
            delta = min_item_level - proto.base_item_level
            bonus = self._level_delta_to_bonus_list.get(delta, 0)
            if bonus:
                state["bonus_list_ids"].append(bonus)
                state["trace"].append(TreeStep(
                    0, 0, "selector-level-delta",
                    {"item_level_selector_id": selector_id,
                     "selector_min_item_level": min_item_level,
                     "base_item_level": proto.base_item_level,
                     "item_level_delta": delta,
                     "bonus_list_id": bonus}))
            else:
                state["trace"].append(TreeStep(
                    0, 0, "selector-level-delta-missing",
                    {"item_level_selector_id": selector_id,
                     "item_level_delta": delta}))

            quality_set_id = int(selector["ItemLevelSelectorQualitySetID"])
            quality_set = self._level_selector_quality_set.get(quality_set_id)
            qualities = self._level_selector_qualities.get(quality_set_id)
            if quality_set is not None and qualities:
                quality = QUALITY_UNCOMMON
                if min_item_level >= int(quality_set["IlvlEpic"]):
                    quality = QUALITY_EPIC
                elif min_item_level >= int(quality_set["IlvlRare"]):
                    quality = QUALITY_RARE
                # Mirrors: std::lower_bound over a set ordered by Quality.
                picked = next((q for q in qualities if int(q["Quality"]) >= quality), None)
                if picked is not None:
                    state["bonus_list_ids"].append(int(picked["QualityItemBonusListID"]))
                    state["trace"].append(TreeStep(
                        0, 0, "selector-quality",
                        {"item_level_selector_quality_set_id": quality_set_id,
                         "computed_quality": quality,
                         "matched_quality": int(picked["Quality"]),
                         "bonus_list_id": int(picked["QualityItemBonusListID"])}))

            azerite = self._azerite_unlock_bonus_list(
                int(selector["AzeriteUnlockMappingSetID"]), min_item_level,
                proto.inventory_type)
            if azerite:
                state["bonus_list_ids"].append(azerite)
                state["trace"].append(TreeStep(0, 0, "azerite-unlock",
                                               {"bonus_list_id": azerite}))

        return BonusListSelection(
            bonus_list_ids=state["bonus_list_ids"],
            item_level_selector_id=selector_id,
            trace=state["trace"],
            trees_visited=state["trees"],
        )

    # -- content tuning --------------------------------------------------
    def redirected_content_tuning_id(
        self, content_tuning_id: int, redirect_flags: Sequence[int] = ()
    ) -> int:
        """Mirrors: ``DB2Manager::GetRedirectedContentTuningId``."""
        rows = self._conditional_content_tuning.get(content_tuning_id) if self._conditional_content_tuning else None
        if not rows:
            return content_tuning_id
        for row in sorted(rows, key=lambda r: -int(r["OrderIndex"])):
            redirect_enum = int(row["RedirectEnum"])
            block, flag = divmod(redirect_enum, 32)
            if block >= len(redirect_flags):
                continue
            # Reproduces Trinity's `flag & redirectFlag[block]` verbatim -- note
            # this compares the *bit index*, not `1 << flag`.  See the
            # source-conflict section of the research document.
            if flag & redirect_flags[block]:
                return int(row["RedirectContentTuningID"])
        return content_tuning_id

    def content_tuning_levels(
        self, content_tuning_id: int, redirect_flags: Sequence[int] = (),
        for_item: bool = False,
    ) -> tuple[int, int] | None:
        """Mirrors: ``DB2Manager::GetContentTuningData`` (MinLevel/MaxLevel only).

        Source-name conflict: Trinity's ``MinLevel``/``MaxLevel`` are the CSV
        columns ``MinLevelSquish``/``MaxLevelSquish``, and its
        ``MinLevelType``/``MaxLevelType`` are ``MinLevelScalingOffset`` /
        ``MaxLevelScalingOffset``.  Positional layout is identical.
        """
        row = self._content_tuning.get(
            self.redirected_content_tuning_id(content_tuning_id, redirect_flags))
        if row is None:
            return None
        if for_item and (int(row["Flags"]) & 0x04):   # ContentTuningFlag::DisabledForItem
            return None

        def adjust(calc_type: int) -> int:
            if calc_type == 1:
                return 1
            if calc_type == 2:
                return EXPANSION_MAX_LEVEL[CURRENT_EXPANSION]
            if calc_type == 3:
                return EXPANSION_MAX_LEVEL[max(CURRENT_EXPANSION - 1, 0)]
            return 0

        min_level = int(row["MinLevelSquish"]) + adjust(int(row["MinLevelScalingOffset"]))
        max_level = int(row["MaxLevelSquish"]) + adjust(int(row["MaxLevelScalingOffset"]))
        return (max(1, min(min_level, MAX_LEVEL)), max(1, min(max_level, MAX_LEVEL)))

    # -- effective item level -------------------------------------------
    def effective_item_level(
        self,
        proto: ItemTemplate,
        bonus: BonusData,
        *,
        player_level: int = DEFAULT_PLAYER_LEVEL,
        fixed_level: int = 0,
        min_item_level: int = 0,
        min_item_level_cutoff: int = 0,
        max_item_level: int = 0,
        pvp_bonus: bool = False,
        azerite_level: int = 0,
        current_build_patch: int | None = None,
        provenance: list[dict[str, Any]] | None = None,
    ) -> int:
        """Mirrors: ``Item::GetItemLevel(ItemTemplate const*, BonusData const&, ...)``.

        ``current_build_patch`` selects which ItemSquishEra rows apply; Trinity
        derives it from the *realm's* client build via
        ``ClientBuild::GetMinorMajorBugfixVersionForBuild``, not from the item.
        """
        steps = provenance if provenance is not None else []
        item_level = bonus.ItemLevel
        steps.append({"step": "base", "item_level": item_level,
                      "source": "BonusData::ItemLevel"})

        azerite_row = None
        if azerite_level:
            azerite_row = self.tables("AzeriteLevelInfo").lookup(azerite_level)
        if azerite_row is not None:
            item_level = int(azerite_row["ItemLevel"])
            steps.append({"step": "azerite-level-info", "item_level": item_level,
                          "azerite_level": azerite_level})

        if not bonus.ItemLevelOffsetCurveId:
            if bonus.PlayerLevelToItemLevelCurveId:
                level = player_level
                if fixed_level:
                    level = fixed_level
                    steps.append({"step": "fixed-level", "level": level})
                else:
                    levels = self.content_tuning_levels(bonus.ContentTuningId, (), True)
                    if levels is not None:
                        level = min(max(player_level, levels[0]), levels[1])
                        steps.append({"step": "content-tuning-level-clamp",
                                      "content_tuning_id": bonus.ContentTuningId,
                                      "min_level": levels[0], "max_level": levels[1],
                                      "clamped_level": level})
                raw = self.curves.value_at(
                    bonus.PlayerLevelToItemLevelCurveId, level,
                    consumer="Item::GetItemLevel/PlayerLevelToItemLevelCurve")
                item_level = round_half_away(raw)
                steps.append({"step": "player-level-to-item-level-curve",
                              "curve_id": bonus.PlayerLevelToItemLevelCurveId,
                              "x": level, "raw_y": raw, "item_level": item_level,
                              "rounding": "std::round -> uint32"})
            if bonus.ItemLevelBonus:
                item_level += bonus.ItemLevelBonus
                steps.append({"step": "item-level-bonus",
                              "delta": bonus.ItemLevelBonus, "item_level": item_level,
                              "source": "sum of ITEM_BONUS_ITEM_LEVEL (type 1)"})
        else:
            raw = self.curves.value_at(
                bonus.ItemLevelOffsetCurveId, bonus.ItemLevelOffsetItemLevel,
                consumer="Item::GetItemLevel/ItemLevelOffsetCurve")
            item_level = bonus.ItemLevelOffset + round_half_away(raw)
            steps.append({"step": "item-level-offset-curve",
                          "curve_id": bonus.ItemLevelOffsetCurveId,
                          "x": bonus.ItemLevelOffsetItemLevel, "raw_y": raw,
                          "offset": bonus.ItemLevelOffset, "item_level": item_level,
                          "rounding": "std::round -> uint32",
                          "note": "ITEM_BONUS_ITEM_LEVEL is NOT added on this branch"})

        gem_bonus = sum(bonus.GemItemLevelBonus)
        if gem_bonus:
            item_level += gem_bonus
            steps.append({"step": "gem-item-level-bonus", "delta": gem_bonus,
                          "item_level": item_level})

        item_level_before_upgrades = item_level

        if pvp_bonus:
            if bonus.PvpItemLevel:
                item_level = bonus.PvpItemLevel
                steps.append({"step": "pvp-item-level-base", "item_level": item_level})
            if bonus.PvpItemLevelBonus:
                item_level += bonus.PvpItemLevelBonus
                steps.append({"step": "pvp-item-level-increment",
                              "delta": bonus.PvpItemLevelBonus, "item_level": item_level})

        if not bonus.IgnoreSquish and current_build_patch is not None:
            for squish_id in range(bonus.ItemSquishEraID + 1, self._squish_era_count + 1):
                squish = self._item_squish_era.get(squish_id)
                if squish is None or (int(squish["Flags"]) & 0x1):
                    continue
                if int(squish["Patch"]) > current_build_patch:
                    break
                curve_id = int(squish["CurveID"])
                if curve_id:
                    raw = self.curves.value_at(
                        curve_id, item_level, consumer="Item::GetItemLevel/ItemSquishEra")
                    item_level = round_half_away(raw)
                    steps.append({"step": "item-squish-era",
                                  "item_squish_era_id": squish_id,
                                  "curve_id": curve_id, "raw_y": raw,
                                  "item_level": item_level})

        if proto.inventory_type != INVTYPE_NON_EQUIP:
            if (min_item_level
                    and (not min_item_level_cutoff or item_level_before_upgrades >= min_item_level_cutoff)
                    and item_level < min_item_level):
                item_level = min_item_level
                steps.append({"step": "unit-min-item-level-floor", "item_level": item_level})
            if max_item_level and item_level > max_item_level:
                item_level = max_item_level
                steps.append({"step": "unit-max-item-level-cap", "item_level": item_level})

        clamped = min(max(item_level, MIN_ITEM_LEVEL), MAX_ITEM_LEVEL)
        if clamped != item_level:
            steps.append({"step": "global-clamp", "item_level": clamped,
                          "bounds": [MIN_ITEM_LEVEL, MAX_ITEM_LEVEL]})
        return clamped

    # -- stat generation -------------------------------------------------
    def random_property_points(
        self, item_level: int, quality: int, inventory_type: int, subclass: int
    ) -> float:
        """Mirrors: ``GetRandomPropertyPoints`` (ItemEnchantmentMgr.cpp)."""
        prop_index = self.rand_prop_index(inventory_type, subclass)
        if prop_index is None:
            return 0.0
        row = self._rand_prop_points.get(item_level)
        if row is None:
            return 0.0
        if quality == QUALITY_UNCOMMON:
            return float(row[f"GoodF_{prop_index}"])
        if quality in (QUALITY_RARE, QUALITY_HEIRLOOM):
            return float(row[f"SuperiorF_{prop_index}"])
        if quality in (QUALITY_EPIC, QUALITY_LEGENDARY, QUALITY_ARTIFACT):
            return float(row[f"EpicF_{prop_index}"])
        return 0.0

    @staticmethod
    def rand_prop_index(inventory_type: int, subclass: int) -> int | None:
        """The inventory-type -> RandPropPoints column map from Trinity."""
        if inventory_type in (INVTYPE_HEAD, INVTYPE_BODY, INVTYPE_CHEST,
                              INVTYPE_LEGS, INVTYPE_RANGED, INVTYPE_2HWEAPON,
                              INVTYPE_ROBE, INVTYPE_THROWN):
            return 0
        if inventory_type == INVTYPE_RANGEDRIGHT:
            return 3 if subclass == 19 else 0   # ITEM_SUBCLASS_WEAPON_WAND
        if inventory_type in (INVTYPE_WEAPON, INVTYPE_WEAPONMAINHAND,
                              INVTYPE_WEAPONOFFHAND):
            return 3
        if inventory_type in (INVTYPE_SHOULDERS, INVTYPE_WAIST, INVTYPE_FEET,
                              INVTYPE_HANDS, INVTYPE_TRINKET):
            return 1
        if inventory_type in (INVTYPE_NECK, INVTYPE_WRISTS, INVTYPE_FINGER,
                              INVTYPE_SHIELD, INVTYPE_CLOAK, INVTYPE_HOLDABLE):
            return 2
        if inventory_type == INVTYPE_RELIC:
            return 4
        return None

    def item_stat_value(
        self, proto: ItemTemplate, bonus: BonusData, index: int, item_level: int
    ) -> tuple[float, dict[str, Any]]:
        """Mirrors: ``Item::GetItemStatValue``.  Returns (value, provenance)."""
        stat_type = bonus.ItemStatType[index]
        alloc = bonus.StatPercentEditor[index]
        socket_mult = bonus.ItemStatSocketCostMultiplier[index]
        detail: dict[str, Any] = {
            "stat_index": index, "stat_type": stat_type,
            "stat_name": MOD_NAMES.get(stat_type, str(stat_type)),
            "stat_allocation": alloc,
            "stat_percentage_of_socket": socket_mult,
        }
        if stat_type in UNSCALED_MODS:
            detail["path"] = "verbatim-StatPercentEditor"
            return float(alloc), detail

        rpp = self.random_property_points(item_level, bonus.Quality,
                                          proto.inventory_type, proto.subclass_id)
        detail["rand_prop_points"] = rpp
        detail["rand_prop_index"] = self.rand_prop_index(proto.inventory_type,
                                                         proto.subclass_id)
        detail["rand_prop_row"] = item_level
        detail["rand_prop_quality_column"] = _rand_prop_column(bonus.Quality)
        if not rpp:
            detail["path"] = "no-rand-prop-points"
            return 0.0, detail
        value = float(alloc * rpp) * 0.0001
        detail["path"] = "allocation * randPropPoints * 0.0001"
        detail["value_before_socket_cost"] = value
        socket_cost = self._gt_socket_cost.column(item_level, 0)
        if socket_cost is not None:
            detail["socket_cost_per_level"] = socket_cost
            value -= float(socket_mult * socket_cost)
        detail["value_after_socket_cost"] = value
        return value, detail

    def armor(self, proto: ItemTemplate, quality: int, item_level: int) -> int:
        """Mirrors: ``ItemTemplate::GetArmor``."""
        q = quality if quality != QUALITY_HEIRLOOM else QUALITY_RARE
        if q > QUALITY_ARTIFACT:
            return 0
        if proto.class_id != 4 or proto.subclass_id != 6:   # not a shield
            armor_quality = self._armor_quality.get(item_level)
            armor_total = self._armor_total.get(item_level)
            if armor_quality is None or armor_total is None:
                return 0
            inv = proto.inventory_type
            if inv == INVTYPE_ROBE:
                inv = INVTYPE_CHEST
            location = self._armor_location.get(inv)
            if location is None:
                return 0
            if not 1 <= proto.subclass_id <= 4:
                return 0
            total, modifier = {
                1: ("Cloth", "Clothmodifier"),
                2: ("Leather", "Leathermodifier"),
                3: ("Mail", "Chainmodifier"),
                4: ("Plate", "Platemodifier"),
            }[proto.subclass_id]
            return int(float(armor_quality[f"Qualitymod_{q}"])
                       * float(armor_total[total])
                       * float(location[modifier]) + 0.5)
        shield = self._armor_shield.get(item_level)
        if shield is None:
            return 0
        return int(float(shield[f"Quality_{q}"]) + 0.5)

    def dps(self, proto: ItemTemplate, quality: int, item_level: int) -> float:
        """Mirrors: ``ItemTemplate::GetDPS``."""
        q = quality if quality != QUALITY_HEIRLOOM else QUALITY_RARE
        if proto.class_id != 2 or q > QUALITY_ARTIFACT:
            return 0.0
        inv = proto.inventory_type
        caster = proto.is_caster_weapon
        table = None
        if inv == INVTYPE_AMMO:
            table = "Ammo"
        elif inv == INVTYPE_2HWEAPON:
            table = "TwoHandCaster" if caster else "TwoHand"
        elif inv in (INVTYPE_RANGED, INVTYPE_THROWN, INVTYPE_RANGEDRIGHT):
            if proto.subclass_id == 19:                     # wand
                table = "OneHandCaster"
            elif proto.subclass_id in (2, 3, 18):           # bow, gun, crossbow
                table = "TwoHandCaster" if caster else "TwoHand"
        elif inv in (INVTYPE_WEAPON, INVTYPE_WEAPONMAINHAND, INVTYPE_WEAPONOFFHAND):
            table = "OneHandCaster" if caster else "OneHand"
        if table is None:
            return 0.0
        row = self._damage_tables[table].get(item_level)
        if row is None:
            raise SourceError(
                f"ItemDamage{table} has no row for item level {item_level}")
        return float(row[f"Quality_{q}"])

    def weapon_damage(
        self, proto: ItemTemplate, quality: int, item_level: int
    ) -> tuple[float, float, float]:
        """Mirrors: ``ItemTemplate::GetDamage``.  Returns (min, max, dps)."""
        dps = self.dps(proto, quality, item_level)
        if dps <= 0.0:
            return (0.0, 0.0, 0.0)
        avg = dps * proto.delay * 0.001
        variance = proto.dmg_variance
        min_damage = (variance * -0.5 + 1.0) * avg
        max_damage = math.floor(avg * (variance * 0.5 + 1.0) + 0.5)
        return (min_damage, max_damage, dps)

    def ilvl_stat_multiplier(self, gt: GameTable, item_level: int,
                             inventory_type: int) -> float | None:
        """Mirrors: ``GetIlvlStatMultiplier`` (GameTables.cpp).

        Column order in the .txt is Armor, Weapon, Trinket, Jewelry.
        """
        row = gt.row(item_level)
        if row is None:
            return None
        if inventory_type in (INVTYPE_NECK, INVTYPE_FINGER):
            return row[3]
        if inventory_type == INVTYPE_TRINKET:
            return row[2]
        if inventory_type in (INVTYPE_WEAPON, INVTYPE_SHIELD, INVTYPE_RANGED,
                              INVTYPE_2HWEAPON, INVTYPE_WEAPONMAINHAND,
                              INVTYPE_WEAPONOFFHAND, INVTYPE_HOLDABLE,
                              INVTYPE_RANGEDRIGHT):
            return row[1]
        return row[0]

    def rating_multiplier(self, rating: str, player_level: int) -> float:
        """Mirrors: ``Player::GetRatingMultiplier`` (1 / CombatRatings column)."""
        row = self._gt_combat_ratings.row(player_level)
        if row is None:
            return 1.0
        value = row[CR_INDEX[rating]]
        if not value:
            return 1.0
        return 1.0 / value

    def rating_bonus_value(self, rating: str, amount: float,
                           player_level: int) -> tuple[float, CurveEval | None]:
        """Mirrors: ``Player::GetRatingBonusValue`` + ``ApplyRatingDiminishing``."""
        base = amount * self.rating_multiplier(rating, player_level)
        curve_type = RATING_DIMINISHING_GLOBAL_CURVE.get(rating)
        if curve_type is None:
            return base, None
        curve_id = self.global_curve(curve_type)
        if not curve_id:
            return base, None
        before = len(self.curves.evaluations)
        value = self.curves.value_at(
            curve_id, base,
            consumer=f"Player::ApplyRatingDiminishing/{rating}")
        return value, self.curves.evaluations[before]


def _rand_prop_column(quality: int) -> str:
    if quality == QUALITY_UNCOMMON:
        return "GoodF"
    if quality in (QUALITY_RARE, QUALITY_HEIRLOOM):
        return "SuperiorF"
    if quality in (QUALITY_EPIC, QUALITY_LEGENDARY, QUALITY_ARTIFACT):
        return "EpicF"
    return "<none>"


# --------------------------------------------------------------------------
# High level resolution + variant discovery
# --------------------------------------------------------------------------


@dataclass
class Variant:
    """A source-backed context/upgrade variant of one base item."""

    label: str
    context: int
    mythic_plus_keystone_level: int | None = None
    pvp_tier: int | None = None
    extra_bonus_lists: tuple[int, ...] = ()
    origin: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "context": self.context,
            "context_name": context_name(self.context),
            "mythic_plus_keystone_level": self.mythic_plus_keystone_level,
            "pvp_tier": self.pvp_tier,
            "extra_bonus_lists": list(self.extra_bonus_lists),
            "origin": self.origin,
        }


@dataclass
class ResolvedItem:
    """Everything the pipeline derives for one (item, variant, player) triple."""

    item_id: int
    name: str
    variant: Variant
    player_level: int
    base_item_level: int
    base_quality: int
    quality: int
    inventory_type: int
    class_id: int
    subclass_id: int
    required_level: int
    effective_item_level: int
    item_level_provenance: list[dict[str, Any]]
    selection: BonusListSelection
    bonus_trace: list[AppliedBonus]
    applied_bonus_lists: list[int]
    stats: list[dict[str, Any]]
    sockets: list[int]
    armor: int
    weapon: dict[str, Any] | None
    effects: list[dict[str, Any]]
    item_set: dict[str, Any] | None
    curve_evaluations: list[CurveEval]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "variant": self.variant.to_dict(),
            "player_level": self.player_level,
            "base_item_level": self.base_item_level,
            "base_quality": self.base_quality,
            "base_quality_name": QUALITY_NAMES.get(self.base_quality),
            "quality": self.quality,
            "quality_name": QUALITY_NAMES.get(self.quality),
            "inventory_type": self.inventory_type,
            "inventory_type_name": INVTYPE_NAMES.get(self.inventory_type),
            "class_id": self.class_id,
            "subclass_id": self.subclass_id,
            "required_level": self.required_level,
            "effective_item_level": self.effective_item_level,
            "item_level_provenance": self.item_level_provenance,
            "bonus_selection": self.selection.to_dict(),
            "applied_bonus_lists": self.applied_bonus_lists,
            "bonus_trace": [b.to_dict() for b in self.bonus_trace],
            "stats": self.stats,
            "sockets": self.sockets,
            "armor": self.armor,
            "weapon": self.weapon,
            "effects": self.effects,
            "item_set": self.item_set,
            "curves_used": [c.to_dict() for c in self.curve_evaluations],
            "warnings": self.warnings,
        }


class GearPipeline:
    """Convenience facade tying the resolver pieces together."""

    def __init__(self, tables: Tables | Path | str = DEFAULT_TABLES) -> None:
        self.resolver = ItemResolver(tables)
        self.tables = self.resolver.tables
        t = self.tables
        self._item_set = t("ItemSet").by("ID")
        self._item_set_spell = t("ItemSetSpell").group("ItemSetID")
        self._gem_properties = t("GemProperties").by("ID")
        self._enchant = t("SpellItemEnchantment").by("ID")

    # -- variants --------------------------------------------------------
    def discover_variants(self, item_id: int) -> list[Variant]:
        """Enumerate every context/upgrade variant derivable from source rows.

        Sources of candidate contexts, all read from the item's own reachable
        bonus trees:
          * ``ItemBonusTreeNode.ItemContext`` on any reachable node;
          * every ``ItemContext`` member of a referenced
            ``ItemCreationContextGroupID``;
          * the contexts named by Trinity's hardcoded tree-id switches when one
            of those trees is reachable;
          * ``NONE``, always, as the un-contextualised baseline.
        Mythic+ keystone levels come from reachable Min/MaxMythicPlusLevel
        bounds.  Nothing is fabricated.
        """
        r = self.resolver
        proto = r.items.get(item_id)
        contexts: dict[int, str] = {ITEM_CONTEXT_NONE: "baseline (no context)"}
        keystone_levels: set[int] = set()
        seen_trees: set[int] = set()
        hardcoded_trees: set[int] = set()

        def walk(tree_id: int) -> None:
            if tree_id in seen_trees:
                return
            seen_trees.add(tree_id)
            if tree_id in (4001, 4079, 4125, 4126, 4127, 4128, 4140):
                hardcoded_trees.add(tree_id)
            for node in r._bonus_tree_nodes.get(tree_id, ()):
                ctx = int(node["ItemContext"])
                if ctx and ctx != ITEM_CONTEXT_FORCE_TO_NONE:
                    contexts.setdefault(ctx, f"ItemBonusTreeNode {node['ID']}.ItemContext")
                group_id = int(node["ItemCreationContextGroupID"])
                for member in r._context_by_group.get(group_id, ()):
                    contexts.setdefault(
                        member,
                        f"ItemCreationContextGroup {group_id} via node {node['ID']}")
                for key in ("MinMythicPlusLevel", "MaxMythicPlusLevel"):
                    level = int(node[key])
                    if level:
                        keystone_levels.add(level)
                child = int(node["ChildItemBonusTreeID"])
                if child:
                    walk(child)

        for tree_id in r._item_to_tree.get(item_id, ()):
            walk(tree_id)

        if 4128 in hardcoded_trees:
            for ctx in (CONTEXT_RAID_NORMAL, CONTEXT_RAID_RAID_FINDER,
                        CONTEXT_RAID_HEROIC, CONTEXT_RAID_MYTHIC):
                contexts.setdefault(ctx, "hardcoded sequence-level switch, tree 4128")
        if 4140 in hardcoded_trees:
            for ctx in (CONTEXT_DUNGEON_NORMAL, CONTEXT_DUNGEON_MYTHIC):
                contexts.setdefault(ctx, "hardcoded sequence-level switch, tree 4140")

        variants: list[Variant] = []
        for ctx in sorted(contexts):
            variants.append(Variant(label=context_name(ctx), context=ctx,
                                    origin=contexts[ctx]))
            if keystone_levels:
                for level in sorted(keystone_levels):
                    variants.append(Variant(
                        label=f"{context_name(ctx)}+M{level}", context=ctx,
                        mythic_plus_keystone_level=level,
                        origin="ItemBonusTreeNode Min/MaxMythicPlusLevel"))
        _ = proto
        return variants

    def discover_upgrade_steps(self, item_id: int) -> list[dict[str, Any]]:
        """List the ItemBonusListGroupEntry rows reachable from the item.

        Each row is one step of an upgrade track: a ``SequenceValue``, the
        bonus list applied at that step, and the item-level selector it points
        at.  Live item instances carry the step's bonus list directly, so this
        is the source-backed enumeration of "upgrade state" for the item.
        """
        r = self.resolver
        seen_trees: set[int] = set()
        groups: dict[int, list[int]] = {}

        def walk(tree_id: int) -> None:
            if tree_id in seen_trees:
                return
            seen_trees.add(tree_id)
            for node in r._bonus_tree_nodes.get(tree_id, ()):
                group_id = int(node["ChildItemBonusListGroupID"])
                if group_id:
                    groups.setdefault(group_id, []).append(int(node["ID"]))
                child = int(node["ChildItemBonusTreeID"])
                if child:
                    walk(child)

        for tree_id in r._item_to_tree.get(item_id, ()):
            walk(tree_id)

        group_rows = self.tables("ItemBonusListGroup").by("ID")
        steps: list[dict[str, Any]] = []
        for group_id, node_ids in sorted(groups.items()):
            group = group_rows.get(group_id)
            for entry in r._bonus_list_group_entries.get(group_id, ()):
                selector = r._level_selector.get(int(entry["ItemLevelSelectorID"]))
                steps.append({
                    "item_bonus_list_group_id": group_id,
                    "reached_from_nodes": node_ids,
                    "group_sequence_spell_id": int(group["SequenceSpellID"]) if group else None,
                    "group_entry_id": int(entry["ID"]),
                    "sequence_value": int(entry["SequenceValue"]),
                    "bonus_list_id": int(entry["ItemBonusListID"]),
                    "item_level_selector_id": int(entry["ItemLevelSelectorID"]),
                    "selector_min_item_level": int(selector["MinItemLevel"]) if selector else None,
                    "player_condition_id": int(entry["PlayerConditionID"]),
                    "flags": int(entry["Flags"]),
                })
        return steps

    # -- resolution ------------------------------------------------------
    def resolve(
        self,
        item_id: int,
        variant: Variant | None = None,
        *,
        player_level: int = DEFAULT_PLAYER_LEVEL,
        extra_bonus_lists: Iterable[int] = (),
        fixed_level: int = 0,
        min_item_level: int = 0,
        min_item_level_cutoff: int = 0,
        max_item_level: int = 0,
        pvp_bonus: bool = False,
        current_build_patch: int | None = None,
        auto_bonus_lists: bool = True,
    ) -> ResolvedItem:
        r = self.resolver
        proto = r.items.get(item_id)
        variant = variant or Variant(label="baseline (no context)",
                                     context=ITEM_CONTEXT_NONE,
                                     origin="caller default")
        warnings: list[str] = []
        curve_mark = len(r.curves.evaluations)

        params = BonusGenerationParams(
            context=variant.context,
            mythic_plus_keystone_level=variant.mythic_plus_keystone_level,
            pvp_tier=variant.pvp_tier,
        )
        if auto_bonus_lists:
            selection = r.bonus_lists_for_item(item_id, params)
        else:
            selection = BonusListSelection([], 0, [], [])

        bonus = BonusData(proto, r)
        seen: set[int] = set()
        for bonus_list_id in list(selection.bonus_list_ids) + \
                list(variant.extra_bonus_lists) + list(extra_bonus_lists):
            if bonus_list_id in seen:
                warnings.append(
                    f"bonus list {bonus_list_id} supplied more than once; "
                    f"Trinity would apply it twice (additive bonuses double)")
            seen.add(bonus_list_id)
            if not r.bonuses_for_list(bonus_list_id):
                warnings.append(
                    f"bonus list {bonus_list_id} has no ItemBonus rows in this build")
            bonus.add_bonus_list(bonus_list_id)

        provenance: list[dict[str, Any]] = []
        item_level = r.effective_item_level(
            proto, bonus, player_level=player_level, fixed_level=fixed_level,
            min_item_level=min_item_level,
            min_item_level_cutoff=min_item_level_cutoff,
            max_item_level=max_item_level, pvp_bonus=pvp_bonus,
            current_build_patch=current_build_patch, provenance=provenance)

        rating_mult = r.ilvl_stat_multiplier(
            r._gt_combat_ratings_mult, item_level, proto.inventory_type)
        stamina_mult = r.ilvl_stat_multiplier(
            r._gt_stamina_mult, item_level, proto.inventory_type)

        stats: list[dict[str, Any]] = []
        for index in range(MAX_ITEM_PROTO_STATS):
            stat_type = bonus.ItemStatType[index]
            if stat_type == -1:
                continue
            raw, detail = r.item_stat_value(proto, bonus, index, item_level)
            if raw == 0:
                detail["final_value"] = 0
                detail["skipped"] = "zero before multipliers"
                stats.append(detail)
                continue
            value = raw
            if stat_type == MOD_STAMINA and stamina_mult is not None:
                value *= stamina_mult
                detail["stamina_mult_by_ilvl"] = stamina_mult
            elif stat_type in RATING_MULTIPLIED_MODS and rating_mult is not None:
                value *= rating_mult
                detail["combat_ratings_mult_by_ilvl"] = rating_mult
            detail["value_before_round"] = value
            final = round_half_away(value)
            detail["final_value"] = final
            detail["rounding"] = "std::round then int32 truncation at use site"
            detail["ratings"] = list(MOD_TO_RATINGS.get(stat_type, ()))
            detail["is_primary"] = stat_type in PRIMARY_MODS
            stats.append(detail)

        armor = r.armor(proto, bonus.Quality, item_level)
        weapon = None
        if proto.class_id == 2:
            min_dmg, max_dmg, dps = r.weapon_damage(proto, bonus.Quality, item_level)
            weapon = {
                "dps": dps, "min_damage": min_dmg, "max_damage": max_dmg,
                "speed_ms": proto.delay, "dmg_variance": proto.dmg_variance,
                "weapon_attack_power": int(dps * 6.0),
                "note": "Player::_ApplyWeaponDamage sets weapon AP to int32(dps*6)",
            }

        effects = [{
            "item_effect_id": int(e["ID"]),
            "spell_id": int(e["SpellID"]),
            "trigger_type": int(e["TriggerType"]),
            "trigger_name": ITEM_SPELLTRIGGER_NAMES.get(int(e["TriggerType"]),
                                                        str(e["TriggerType"])),
            "legacy_slot_index": int(e["LegacySlotIndex"]),
            "charges": int(e["Charges"]),
            "cooldown_ms": int(e["CoolDownMSec"]),
            "category_cooldown_ms": int(e["CategoryCoolDownMSec"]),
            "spell_category_id": int(e["SpellCategoryID"]),
            "chr_specialization_id": int(e["ChrSpecializationID"]),
            "player_condition_id": int(e["PlayerConditionID"]),
        } for e in bonus.Effects]

        item_set = None
        if proto.item_set:
            set_row = self._item_set.get(proto.item_set)
            item_set = {
                "item_set_id": proto.item_set,
                "name": str(set_row["Name_lang"]) if set_row else None,
                "set_flags": int(set_row["SetFlags"]) if set_row else None,
                "member_item_ids": [int(set_row[f"ItemID_{i}"]) for i in range(17)
                                    if set_row and int(set_row[f"ItemID_{i}"])] if set_row else [],
                "spells": [{
                    "item_set_spell_id": int(s["ID"]),
                    "threshold": int(s["Threshold"]),
                    "spell_id": int(s["SpellID"]),
                    "chr_spec_id": int(s["ChrSpecID"]),
                    "trait_sub_tree_id": int(s["TraitSubTreeID"]),
                } for s in self._item_set_spell.get(proto.item_set, ())],
            }
            if set_row is None:
                warnings.append(
                    f"ItemSparse.ItemSet {proto.item_set} has no ItemSet row; "
                    f"AddItemsSetItem would log an error and skip the item")

        if proto.is_legacy:
            warnings.append("ITEM_FLAG_LEGACY set: ApplyItemEquipSpell skips all effects")

        return ResolvedItem(
            item_id=item_id,
            name=proto.name,
            variant=variant,
            player_level=player_level,
            base_item_level=proto.base_item_level,
            base_quality=proto.quality,
            quality=bonus.Quality,
            inventory_type=proto.inventory_type,
            class_id=proto.class_id,
            subclass_id=proto.subclass_id,
            required_level=bonus.RequiredLevelOverride or bonus.RequiredLevel,
            effective_item_level=item_level,
            item_level_provenance=provenance,
            selection=selection,
            bonus_trace=bonus.trace,
            applied_bonus_lists=bonus.AppliedBonusLists,
            stats=stats,
            sockets=list(bonus.SocketColor),
            armor=armor,
            weapon=weapon,
            effects=effects,
            item_set=item_set,
            curve_evaluations=r.curves.evaluations[curve_mark:],
            warnings=warnings,
        )

    # -- gem / enchant description --------------------------------------
    def describe_gem(self, gem_item_id: int) -> dict[str, Any]:
        """Mirrors: the canonical half of ``Item::SetGem``.

        gem item -> ItemSparse.Gem_properties -> GemProperties.Enchant_ID ->
        SpellItemEnchantment effects.
        """
        proto = self.resolver.items.get(gem_item_id)
        gem_props = self._gem_properties.get(proto.gem_properties)
        result: dict[str, Any] = {
            "gem_item_id": gem_item_id,
            "name": proto.name,
            "gem_properties_id": proto.gem_properties,
            "gem_type_mask": int(gem_props["Type"]) if gem_props else None,
            "enchant_id": int(gem_props["Enchant_ID"]) if gem_props else 0,
        }
        if gem_props:
            result["enchant"] = self.describe_enchant(int(gem_props["Enchant_ID"]))
        return result

    def describe_enchant(self, enchant_id: int) -> dict[str, Any]:
        """Classify a SpellItemEnchantment row by its three effect slots.

        Mirrors: ``Player::ApplyEnchantment``'s dispatch on ``Effect[s]``.
        """
        row = self._enchant.get(enchant_id)
        if row is None:
            raise SourceError(f"unknown SpellItemEnchantment {enchant_id}")
        effects = []
        for i in range(MAX_ITEM_ENCHANTMENT_EFFECTS):
            effect_type = int(row[f"Effect_{i}"])
            if effect_type == 0:
                continue
            arg = int(row[f"EffectArg_{i}"])
            entry: dict[str, Any] = {
                "slot": i,
                "type": effect_type,
                "type_name": ENCHANT_TYPE_NAMES.get(effect_type, str(effect_type)),
                "effect_arg": arg,
                "effect_points_min": int(row[f"EffectPointsMin_{i}"]),
                "effect_scaling_points": float(row[f"EffectScalingPoints_{i}"]),
            }
            if effect_type == 5:                      # STAT
                entry["stat_type"] = arg
                entry["stat_name"] = MOD_NAMES.get(arg, str(arg))
                entry["ratings"] = list(MOD_TO_RATINGS.get(arg, ()))
                entry["acquisition"] = "direct stat/rating addition"
            elif effect_type == 4:                    # RESISTANCE
                entry["acquisition"] = "direct resistance addition"
            elif effect_type == 3:                    # EQUIP_SPELL
                entry["spell_id"] = arg
                entry["acquisition"] = "equip aura: CastSpell(this, spell, item)"
            elif effect_type == 1:                    # COMBAT_SPELL
                entry["spell_id"] = arg
                entry["acquisition"] = "proc root: Player::CastItemCombatSpell"
            elif effect_type == 7:                    # USE_SPELL
                entry["spell_id"] = arg
                entry["acquisition"] = "on-use root: Player::CastItemUseSpell"
            elif effect_type in (2, 6):               # DAMAGE / TOTEM
                entry["acquisition"] = "weapon damage-done modifier"
            elif effect_type == 8:                    # PRISMATIC_SOCKET
                entry["acquisition"] = "adds a socket (item mutation, no stats)"
            elif effect_type in (11, 12):             # BONUS_LIST_ID / CURVE
                entry["acquisition"] = "item-level bonus for the host item (gems only)"
            else:
                entry["acquisition"] = "no direct stat/spell consumer"
            effects.append(entry)
        return {
            "enchant_id": enchant_id,
            "name": str(row["Name_lang"]),
            "scaling_class": int(row["ScalingClass"]),
            "scaling_class_restricted": int(row["ScalingClassRestricted"]),
            "min_level": int(row["MinLevel"]),
            "max_level": int(row["MaxLevel"]),
            "required_skill_id": int(row["RequiredSkillID"]),
            "required_skill_rank": int(row["RequiredSkillRank"]),
            "condition_id": int(row["Condition_ID"]),
            "flags": int(row["Flags"]),
            "item_level": int(row["ItemLevel"]),
            "effects": effects,
        }

    def enchant_stat_amount(self, enchant_id: int, slot: int,
                            player_level: int) -> tuple[int, dict[str, Any]]:
        """Mirrors: the ScalingClass branch of ``Player::ApplyEnchantment``.

        ``SpellScaling.txt`` column index is ``ScalingClass``; a negative class
        indexes the trailing "item"/"consumable" columns in Trinity's
        ``GetSpellScalingColumnForClass``.
        """
        row = self._enchant.get(enchant_id)
        if row is None:
            raise SourceError(f"unknown SpellItemEnchantment {enchant_id}")
        amount = int(row[f"EffectPointsMin_{slot}"])
        detail: dict[str, Any] = {"effect_points_min": amount, "scaled": False}
        scaling_class = int(row["ScalingClass"])
        if scaling_class:
            flags = int(row["Flags"])
            # SpellItemEnchantmentFlags::ScaleAsAGem == 0x4
            min_level = 1 if (flags & 0x4) else 60
            gt = self.tables.gametable("SpellScaling")
            max_level = int(row["MaxLevel"]) or (gt.row_count() - 1)
            scaling_level = player_level
            if min_level > player_level:
                scaling_level = min_level
            elif max_level < player_level:
                scaling_level = max_level
            column = _spell_scaling_column(scaling_class)
            value = gt.column(scaling_level, column) if column is not None else None
            if value is not None:
                amount = int(float(row[f"EffectScalingPoints_{slot}"]) * value)
                detail.update({
                    "scaled": True, "scaling_class": scaling_class,
                    "spell_scaling_column": SPELL_SCALING_COLUMNS[column],
                    "scaling_level": scaling_level,
                    "spell_scaling_value": value,
                    "effect_scaling_points": float(row[f"EffectScalingPoints_{slot}"]),
                    "rounding": "uint32 truncation",
                })
        amount = max(amount, 1)
        detail["final_amount"] = amount
        return amount, detail


# SpellScaling.txt column order (after the Level key), matching
# ``struct GtSpellScalingEntry`` in GameTables.h field for field.
SPELL_SCALING_COLUMNS = (
    "Rogue", "Druid", "Hunter", "Mage", "Paladin", "Priest", "Shaman",
    "Warlock", "Warrior", "DeathKnight", "Monk", "DemonHunter", "Evoker",
    "Adventurer", "Traveler", "Item", "Consumable", "Gem1", "Gem2", "Gem3",
    "Health", "DamageReplaceStat", "DamageSecondary", "ManaConsumable",
)
_SPELL_SCALING_COLUMN_INDEX = {n: i for i, n in enumerate(SPELL_SCALING_COLUMNS)}

# Mirrors: ``GetSpellScalingColumnForClass`` (GameTables.h).  Positive keys are
# Classes enum ids, negative keys are the non-class columns.
_SCALING_CLASS_TO_COLUMN = {
    1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest",
    6: "DeathKnight", 7: "Shaman", 8: "Mage", 9: "Warlock", 10: "Monk",
    11: "Druid", 12: "DemonHunter", 13: "Evoker", 14: "Adventurer",
    15: "Traveler",
    -1: "Item", -7: "Item", -2: "Consumable", -3: "Gem1", -4: "Gem2",
    -5: "Gem3", -6: "Health", -8: "DamageReplaceStat",
    -9: "DamageSecondary", -10: "ManaConsumable",
}


def _spell_scaling_column(scaling_class: int) -> int | None:
    """Return the zero-based SpellScaling.txt column for a ScalingClass."""
    name = _SCALING_CLASS_TO_COLUMN.get(scaling_class)
    return None if name is None else _SPELL_SCALING_COLUMN_INDEX[name]


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

REPORT_COLUMNS = (
    "item_id", "name", "variant", "context", "bonus_lists", "selector",
    "item_level", "quality", "inv_type", "primary", "stamina",
    "secondary_1", "secondary_2", "armor_or_dps", "curves",
)


def report_row(resolved: ResolvedItem) -> dict[str, Any]:
    """One row of the cross-difficulty report, following source semantics."""
    primary = []
    stamina = 0
    secondaries: list[tuple[str, int]] = []
    for stat in resolved.stats:
        value = stat.get("final_value", 0)
        if not value:
            continue
        stat_type = stat["stat_type"]
        if stat_type == MOD_STAMINA:
            stamina = value
        elif stat_type in PRIMARY_MODS:
            primary.append((stat["stat_name"], value))
        elif stat.get("ratings"):
            secondaries.append((stat["stat_name"], value))
    if resolved.weapon:
        combat = f"{resolved.weapon['min_damage']:.1f}-{resolved.weapon['max_damage']:.0f} @ {resolved.weapon['dps']:.2f} dps"
    else:
        combat = str(resolved.armor)
    return {
        "item_id": resolved.item_id,
        "name": resolved.name,
        "variant": resolved.variant.label,
        "context": resolved.variant.context,
        "bonus_lists": ",".join(str(b) for b in resolved.applied_bonus_lists) or "-",
        "selector": resolved.selection.item_level_selector_id or "-",
        "item_level": resolved.effective_item_level,
        "quality": QUALITY_NAMES.get(resolved.quality, resolved.quality),
        "inv_type": INVTYPE_NAMES.get(resolved.inventory_type, resolved.inventory_type),
        "primary": ", ".join(f"{n} {v}" for n, v in primary) or "-",
        "stamina": stamina or "-",
        "secondary_1": f"{secondaries[0][0]} {secondaries[0][1]}" if secondaries else "-",
        "secondary_2": f"{secondaries[1][0]} {secondaries[1][1]}" if len(secondaries) > 1 else "-",
        "armor_or_dps": combat,
        "curves": ",".join(sorted({str(c.curve_id) for c in resolved.curve_evaluations})) or "-",
    }


def render_table(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> str:
    if not rows:
        return "(no rows)"
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    out = [" | ".join(c.ljust(widths[c]) for c in columns),
           "-+-".join("-" * widths[c] for c in columns)]
    for row in rows:
        out.append(" | ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns))
    return "\n".join(out)


def render_markdown(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> str:
    if not rows:
        return "_(no rows)_"
    out = ["| " + " | ".join(columns) + " |",
           "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|")
                                     for c in columns) + " |")
    return "\n".join(out)


def render_explain(resolved: ResolvedItem) -> str:
    r = resolved
    lines: list[str] = []
    lines.append(f"Item {r.item_id}  {r.name!r}")
    lines.append(f"  variant           : {r.variant.label} "
                 f"(context {r.variant.context} = {context_name(r.variant.context)})")
    if r.variant.origin:
        lines.append(f"  variant origin    : {r.variant.origin}")
    if r.variant.mythic_plus_keystone_level is not None:
        lines.append(f"  keystone level    : {r.variant.mythic_plus_keystone_level}")
    lines.append(f"  player level      : {r.player_level}")
    lines.append(f"  class/subclass    : {r.class_id}/{r.subclass_id}")
    lines.append(f"  inventory type    : {r.inventory_type} "
                 f"({INVTYPE_NAMES.get(r.inventory_type)})")
    lines.append(f"  base item level   : {r.base_item_level}  (ItemSparse.ItemLevel)")
    lines.append(f"  base quality      : {r.base_quality} "
                 f"({QUALITY_NAMES.get(r.base_quality)})")
    lines.append(f"  effective quality : {r.quality} ({QUALITY_NAMES.get(r.quality)})")
    lines.append(f"  required level    : {r.required_level}")

    lines.append("")
    lines.append("  bonus-list selection (ItemBonusMgr::GetBonusListsForItem)")
    lines.append(f"    trees visited   : {r.selection.trees_visited or '-'}")
    for step in r.selection.trace:
        detail = ", ".join(f"{k}={v}" for k, v in step.detail.items())
        lines.append(f"    tree {step.tree_id:>6} node {step.node_id:>6}  "
                     f"{step.action}{'  ' + detail if detail else ''}")
    lines.append(f"    selected lists  : {r.selection.bonus_list_ids or '-'}")
    lines.append(f"    level selector  : {r.selection.item_level_selector_id or '-'}")
    lines.append(f"    applied lists   : {r.applied_bonus_lists or '-'}")

    if r.bonus_trace:
        lines.append("")
        lines.append("  applied ItemBonus rows (BonusData::AddBonus)")
        for b in r.bonus_trace:
            lines.append(f"    list {b.bonus_list_id:>7}  bonus {b.bonus_id:>7}  "
                         f"type {b.type:>2} {BONUS_TYPE_NAMES.get(b.type, '?'):<34} "
                         f"values {list(b.values)}")

    lines.append("")
    lines.append("  effective item level (Item::GetItemLevel)")
    for step in r.item_level_provenance:
        rest = ", ".join(f"{k}={v}" for k, v in step.items() if k != "step")
        lines.append(f"    {step['step']:<34} {rest}")
    lines.append(f"    => effective item level {r.effective_item_level}")

    if r.curve_evaluations:
        lines.append("")
        lines.append("  curve evaluations")
        for c in r.curve_evaluations:
            pts = " ".join(f"({x:g},{y:g})" for x, y in c.bracket)
            lines.append(f"    curve {c.curve_id} type {c.curve_type} "
                         f"mode {c.mode} x={c.x:g} -> raw {c.y!r}"
                         f"{'  [' + c.clamped + ']' if c.clamped else ''}")
            lines.append(f"      points({c.point_count}) around x: {pts}")
            lines.append(f"      consumer: {c.consumer}")

    lines.append("")
    lines.append("  stats (Item::GetItemStatValue -> Player::_ApplyItemBonuses)")
    for s in r.stats:
        if not s.get("final_value") and s.get("skipped"):
            continue
        parts = [f"alloc={s['stat_allocation']}"]
        if "rand_prop_points" in s:
            parts.append(f"randProp[{s['rand_prop_quality_column']}"
                         f"[{s['rand_prop_index']}]@ilvl{s['rand_prop_row']}]"
                         f"={s['rand_prop_points']}")
        if "socket_cost_per_level" in s:
            parts.append(f"socketCost={s['socket_cost_per_level']}"
                         f"*{s['stat_percentage_of_socket']}")
        if "combat_ratings_mult_by_ilvl" in s:
            parts.append(f"ratingMult={s['combat_ratings_mult_by_ilvl']}")
        if "stamina_mult_by_ilvl" in s:
            parts.append(f"staminaMult={s['stamina_mult_by_ilvl']}")
        if "value_before_round" in s:
            parts.append(f"pre-round={s['value_before_round']!r}")
        lines.append(f"    [{s['stat_index']}] {s['stat_name']:<14} = "
                     f"{s['final_value']:>7}   " + "  ".join(parts))
        if s.get("ratings"):
            lines.append(f"         -> CombatRatings {', '.join(s['ratings'])}")

    if any(r.sockets):
        lines.append("")
        lines.append(f"  sockets           : {r.sockets}")
    if r.armor:
        lines.append(f"  armor             : {r.armor}  (ItemTemplate::GetArmor)")
    if r.weapon:
        lines.append("")
        lines.append("  weapon (ItemTemplate::GetDPS / GetDamage)")
        for k, v in r.weapon.items():
            lines.append(f"    {k:<22} {v}")
    if r.effects:
        lines.append("")
        lines.append("  item effects (ItemXItemEffect -> ItemEffect)")
        for e in r.effects:
            lines.append(f"    effect {e['item_effect_id']:>7}  spell {e['spell_id']:>8}  "
                         f"{e['trigger_name']:<16} spec={e['chr_specialization_id']} "
                         f"cd={e['cooldown_ms']}ms cat={e['spell_category_id']}")
    if r.item_set:
        lines.append("")
        lines.append(f"  item set {r.item_set['item_set_id']}: {r.item_set['name']!r}")
        for s in r.item_set["spells"]:
            lines.append(f"    {s['threshold']}p -> spell {s['spell_id']:>8}  "
                         f"spec={s['chr_spec_id']} subtree={s['trait_sub_tree_id']}")
    if r.warnings:
        lines.append("")
        lines.append("  warnings")
        for w in r.warnings:
            lines.append(f"    ! {w}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _parse_int_list(text: str | None) -> list[int]:
    if not text:
        return []
    return [int(part) for part in text.replace(",", " ").split()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="item_scaling.py",
        description="Reproduce the retail item scaling pipeline from checked-in "
                    "Wago tables, mirroring TrinityCore's direct consumers.")
    parser.add_argument("--tables", type=Path, default=DEFAULT_TABLES,
                        help="directory holding the CSV/GameTable snapshot")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="resolve one item in one context")
    show.add_argument("item_id", type=int)
    show.add_argument("--context", type=int, default=0,
                      help="ItemContext value (default 0 = NONE)")
    show.add_argument("--bonus-list", dest="bonus_lists", action="append",
                      default=[], help="extra bonus list id (repeatable)")
    show.add_argument("--no-auto-bonus-lists", action="store_true",
                      help="skip ItemBonusMgr tree resolution; use only --bonus-list")
    show.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    show.add_argument("--mythic-plus-level", type=int, default=None)
    show.add_argument("--pvp", action="store_true",
                      help="apply the PvP item level branch")
    show.add_argument("--fixed-level", type=int, default=0,
                      help="ITEM_MODIFIER_TIMEWALKER_LEVEL equivalent")
    show.add_argument("--min-item-level", type=int, default=0)
    show.add_argument("--min-item-level-cutoff", type=int, default=0)
    show.add_argument("--max-item-level", type=int, default=0)
    show.add_argument("--squish-patch", type=int, default=None,
                      help="realm build patch number for ItemSquishEra application")

    variants = sub.add_parser(
        "variants", help="discover and evaluate every source-backed variant")
    variants.add_argument("item_id", type=int)
    variants.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    variants.add_argument("--distinct", action="store_true",
                          help="collapse variants that resolve identically")
    variants.add_argument("--explain", action="store_true",
                          help="print the full derivation for each variant")

    upgrades = sub.add_parser(
        "upgrades", help="list ItemBonusListGroupEntry upgrade steps for an item")
    upgrades.add_argument("item_id", type=int)
    upgrades.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    upgrades.add_argument("--evaluate", action="store_true",
                          help="resolve the item at each upgrade step")

    curve = sub.add_parser("curve", help="explain one curve evaluation")
    curve.add_argument("curve_id", type=int)
    curve.add_argument("x", type=float, nargs="?", default=None)

    gem = sub.add_parser("gem", help="describe a gem item's enchant payload")
    gem.add_argument("item_id", type=int)

    enchant = sub.add_parser("enchant", help="describe a SpellItemEnchantment")
    enchant.add_argument("enchant_id", type=int)
    enchant.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)

    ratings = sub.add_parser("ratings", help="rating -> percentage conversion")
    ratings.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    ratings.add_argument("--amount", type=float, default=1000.0)

    report = sub.add_parser("report", help="cross-variant report for a corpus")
    report.add_argument("item_ids", type=int, nargs="*")
    report.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    report.add_argument("--markdown", action="store_true")
    report.add_argument("--distinct", action="store_true", default=True)

    sub.add_parser("census", help="population counts used for architectural leverage")
    return parser


def _emit(payload: Any, as_json: bool, text: str) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=False, default=str))
    else:
        print(text)


def _distinct_key(resolved: ResolvedItem) -> tuple[Any, ...]:
    return (
        resolved.effective_item_level,
        tuple(resolved.applied_bonus_lists),
        resolved.selection.item_level_selector_id,
        resolved.quality,
        tuple((s["stat_index"], s.get("final_value", 0)) for s in resolved.stats),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pipeline = GearPipeline(args.tables)
    r = pipeline.resolver

    if args.command == "show":
        variant = Variant(label=context_name(args.context), context=args.context,
                          mythic_plus_keystone_level=args.mythic_plus_level,
                          origin="supplied on the command line")
        resolved = pipeline.resolve(
            args.item_id, variant,
            player_level=args.player_level,
            extra_bonus_lists=_parse_int_list(",".join(args.bonus_lists)),
            fixed_level=args.fixed_level,
            min_item_level=args.min_item_level,
            min_item_level_cutoff=args.min_item_level_cutoff,
            max_item_level=args.max_item_level,
            pvp_bonus=args.pvp,
            current_build_patch=args.squish_patch,
            auto_bonus_lists=not args.no_auto_bonus_lists)
        _emit(resolved.to_dict(), args.json, render_explain(resolved))
        return 0

    if args.command == "variants":
        found = pipeline.discover_variants(args.item_id)
        resolved_all: list[ResolvedItem] = []
        seen: set[tuple[Any, ...]] = set()
        for variant in found:
            resolved = pipeline.resolve(args.item_id, variant,
                                        player_level=args.player_level)
            if args.distinct:
                key = _distinct_key(resolved)
                if key in seen:
                    continue
                seen.add(key)
            resolved_all.append(resolved)
        if args.json:
            _emit({"item_id": args.item_id,
                   "discovered_variants": [v.to_dict() for v in found],
                   "resolved": [x.to_dict() for x in resolved_all]}, True, "")
        elif args.explain:
            print("\n\n".join(render_explain(x) for x in resolved_all))
        else:
            print(render_table([report_row(x) for x in resolved_all], REPORT_COLUMNS))
        return 0

    if args.command == "upgrades":
        steps = pipeline.discover_upgrade_steps(args.item_id)
        rows: list[dict[str, Any]] = []
        for step in steps:
            row = dict(step)
            if args.evaluate and step["bonus_list_id"]:
                variant = Variant(label=f"seq{step['sequence_value']}",
                                  context=ITEM_CONTEXT_NONE,
                                  extra_bonus_lists=(step["bonus_list_id"],),
                                  origin=f"ItemBonusListGroupEntry {step['group_entry_id']}")
                resolved = pipeline.resolve(args.item_id, variant,
                                            player_level=args.player_level,
                                            auto_bonus_lists=False)
                row["effective_item_level"] = resolved.effective_item_level
                row["quality"] = resolved.quality
            rows.append(row)
        columns = ["item_bonus_list_group_id", "group_entry_id", "sequence_value",
                   "bonus_list_id", "item_level_selector_id",
                   "selector_min_item_level", "player_condition_id"]
        if args.evaluate:
            columns += ["effective_item_level", "quality"]
        _emit({"item_id": args.item_id, "upgrade_steps": rows}, args.json,
              render_table(rows, columns))
        return 0

    if args.command == "curve":
        points = r.curves.points(args.curve_id)
        if not points:
            raise SourceError(f"curve {args.curve_id} has no CurvePoint rows")
        payload: dict[str, Any] = {
            "curve_id": args.curve_id,
            "curve_type": r.curves.curve_type(args.curve_id),
            "interpolation": r.curves.mode(args.curve_id),
            "point_count": len(points),
            "x_range": list(r.curves.x_range(args.curve_id)),
            "points": [list(p) for p in points],
        }
        text = [f"curve {args.curve_id}  type={payload['curve_type']} "
                f"mode={payload['interpolation']} points={len(points)} "
                f"x in [{points[0][0]:g}, {points[-1][0]:g}]"]
        for i, (x, y) in enumerate(points):
            text.append(f"  [{i:>3}] x={x!r} y={y!r}")
        if args.x is not None:
            value = r.curves.value_at(args.curve_id, args.x, consumer="cli")
            evaluation = r.curves.evaluations[-1]
            payload["evaluation"] = evaluation.to_dict()
            payload["rounded"] = round_half_away(value)
            text.append(f"  value_at({args.x!r}) = {value!r} "
                        f"(std::round -> {round_half_away(value)})")
            text.append(f"  bracket: {[list(p) for p in evaluation.bracket]}")
            if evaluation.clamped:
                text.append(f"  clamped: {evaluation.clamped}")
        _emit(payload, args.json, "\n".join(text))
        return 0

    if args.command == "gem":
        payload = pipeline.describe_gem(args.item_id)
        _emit(payload, args.json, json.dumps(payload, indent=2, default=str))
        return 0

    if args.command == "enchant":
        payload = pipeline.describe_enchant(args.enchant_id)
        for effect in payload["effects"]:
            if effect["type"] in (4, 5):
                amount, detail = pipeline.enchant_stat_amount(
                    args.enchant_id, effect["slot"], args.player_level)
                effect["resolved_amount"] = amount
                effect["resolution"] = detail
        _emit(payload, args.json, json.dumps(payload, indent=2, default=str))
        return 0

    if args.command == "ratings":
        rows = []
        for name in CR_NAMES:
            if name.startswith("Unused"):
                continue
            multiplier = r.rating_multiplier(name, args.player_level)
            value, curve = r.rating_bonus_value(name, args.amount, args.player_level)
            rows.append({
                "rating": name,
                "combat_ratings_column": CR_INDEX[name],
                "per_point": multiplier,
                "amount": args.amount,
                "linear_percent": args.amount * multiplier,
                "diminishing_curve": curve.curve_id if curve else "-",
                "final_percent": round(value, 6),
            })
        _emit({"player_level": args.player_level, "ratings": rows}, args.json,
              render_table(rows, ["rating", "combat_ratings_column", "per_point",
                                  "amount", "linear_percent",
                                  "diminishing_curve", "final_percent"]))
        return 0

    if args.command == "report":
        item_ids = args.item_ids or list(DEFAULT_CORPUS)
        rows = []
        for item_id in item_ids:
            seen: set[tuple[Any, ...]] = set()
            for variant in pipeline.discover_variants(item_id):
                resolved = pipeline.resolve(item_id, variant,
                                            player_level=args.player_level)
                if args.distinct:
                    key = _distinct_key(resolved)
                    if key in seen:
                        continue
                    seen.add(key)
                rows.append(report_row(resolved))
        if args.json:
            _emit({"player_level": args.player_level, "rows": rows}, True, "")
        elif args.markdown:
            print(render_markdown(rows, REPORT_COLUMNS))
        else:
            print(render_table(rows, REPORT_COLUMNS))
        return 0

    if args.command == "census":
        payload = census(pipeline)
        _emit(payload, args.json,
              render_table([{"population": k, "count": v}
                            for k, v in payload.items()],
                           ["population", "count"]))
        return 0

    return 1


# Representative corpus used by `report` when no ids are supplied.  Chosen in
# the research pass; see docs/research/gearing-pipeline-archaeology.md.
DEFAULT_CORPUS: tuple[int, ...] = ()


def census(pipeline: GearPipeline) -> dict[str, int]:
    """Population counts that estimate architectural leverage."""
    t = pipeline.tables
    sparse = t("ItemSparse")
    equippable = sum(1 for row in sparse if int(row["InventoryType"]) != INVTYPE_NON_EQUIP)
    stat_bearing = sum(
        1 for row in sparse
        if any(int(row[f"StatModifier_bonusStat_{i}"]) != -1
               for i in range(MAX_ITEM_PROTO_STATS)))
    socketed = sum(1 for row in sparse
                   if any(int(row[f"SocketType_{i}"]) for i in range(MAX_ITEM_PROTO_SOCKETS)))
    effects = t("ItemEffect")
    trigger_counts: dict[int, int] = defaultdict(int)
    for row in effects:
        trigger_counts[int(row["TriggerType"])] += 1
    enchant_effect_types: dict[int, int] = defaultdict(int)
    for row in t("SpellItemEnchantment"):
        for i in range(MAX_ITEM_ENCHANTMENT_EFFECTS):
            value = int(row[f"Effect_{i}"])
            if value:
                enchant_effect_types[value] += 1
    gear_spells: set[int] = set()
    for row in effects:
        if int(row["SpellID"]):
            gear_spells.add(int(row["SpellID"]))
    for row in t("ItemSetSpell"):
        if int(row["SpellID"]):
            gear_spells.add(int(row["SpellID"]))
    for row in t("SpellItemEnchantment"):
        for i in range(MAX_ITEM_ENCHANTMENT_EFFECTS):
            if int(row[f"Effect_{i}"]) in (1, 3, 7) and int(row[f"EffectArg_{i}"]):
                gear_spells.add(int(row[f"EffectArg_{i}"]))
    bonus_types: dict[int, int] = defaultdict(int)
    for row in t("ItemBonus"):
        bonus_types[int(row["Type"])] += 1
    result = {
        "Item rows": len(t("Item")),
        "ItemSparse rows": len(sparse),
        "equippable items (InventoryType != 0)": equippable,
        "stat-bearing items": stat_bearing,
        "items with a template socket": socketed,
        "ItemEffect rows": len(effects),
        "ItemXItemEffect edges": len(t("ItemXItemEffect")),
        "on-use roots (TriggerType 0)": trigger_counts[0],
        "equip roots (TriggerType 1)": trigger_counts[1],
        "proc roots (TriggerType 2)": trigger_counts[2],
        "SpellItemEnchantment rows": len(t("SpellItemEnchantment")),
        "enchant stat effects (type 5)": enchant_effect_types[5],
        "enchant equip-spell effects (type 3)": enchant_effect_types[3],
        "enchant combat/proc effects (type 1)": enchant_effect_types[1],
        "enchant use-spell effects (type 7)": enchant_effect_types[7],
        "GemProperties rows": len(t("GemProperties")),
        "ItemSet rows": len(t("ItemSet")),
        "ItemSetSpell rows": len(t("ItemSetSpell")),
        "distinct gear-reachable spell ids": len(gear_spells),
        "ItemBonus rows": len(t("ItemBonus")),
        "distinct bonus lists": len({int(r["ParentItemBonusListID"]) for r in t("ItemBonus")}),
        "distinct ItemBonus types in use": len(bonus_types),
        "ItemBonusTree rows": len(t("ItemBonusTree")),
        "ItemBonusTreeNode rows": len(t("ItemBonusTreeNode")),
        "ItemXBonusTree edges": len(t("ItemXBonusTree")),
        "ItemBonusListGroupEntry rows": len(t("ItemBonusListGroupEntry")),
        "ItemLevelSelector rows": len(t("ItemLevelSelector")),
        "ItemBonusListLevelDelta rows": len(t("ItemBonusListLevelDelta")),
        "Curve rows": len(t("Curve")),
        "CurvePoint rows": len(t("CurvePoint")),
        "GlobalCurve rows": len(t("GlobalCurve")),
    }
    return result


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SourceError as error:
        print(f"source error: {error}", file=sys.stderr)
        sys.exit(2)
