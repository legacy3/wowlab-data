"""Enumerations and constants transcribed from the TrinityCore checkout.

Every block names the file and symbol it was copied from so it can be diffed
against a newer checkout.  Nothing here is inferred from tooltips.

Checked against TrinityCore 7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f.
"""

from __future__ import annotations

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


# src/server/game/Entities/Item/ItemTemplate.h :: enum ItemClass / subclasses
ITEM_CLASS_WEAPON = 2
ITEM_CLASS_ARMOR = 4

ITEM_SUBCLASS_ARMOR_CLOTH = 1
ITEM_SUBCLASS_ARMOR_LEATHER = 2
ITEM_SUBCLASS_ARMOR_MAIL = 3
ITEM_SUBCLASS_ARMOR_PLATE = 4
ITEM_SUBCLASS_ARMOR_SHIELD = 6

ITEM_SUBCLASS_WEAPON_BOW = 2
ITEM_SUBCLASS_WEAPON_GUN = 3
ITEM_SUBCLASS_WEAPON_CROSSBOW = 18
ITEM_SUBCLASS_WEAPON_WAND = 19

ARMOR_SUBCLASS_COLUMNS = {
    ITEM_SUBCLASS_ARMOR_CLOTH: ("Cloth", "Clothmodifier"),
    ITEM_SUBCLASS_ARMOR_LEATHER: ("Leather", "Leathermodifier"),
    ITEM_SUBCLASS_ARMOR_MAIL: ("Mail", "Chainmodifier"),
    ITEM_SUBCLASS_ARMOR_PLATE: ("Plate", "Platemodifier"),
}

# src/server/game/Entities/Item/ItemTemplate.h :: item flag bits actually
# consulted by the gearing path.  (word index, mask).
FLAG_NO_DISENCHANT = (0, 0x00008000)
FLAG_LEGACY = (0, 0x00000100)
FLAG2_NO_TRADE_BIND_ON_ACQUIRE = (1, 0x00000040)
FLAG2_CASTER_WEAPON = (1, 0x00000200)
FLAG3_IGNORE_ITEM_LEVEL_CAP_IN_PVP = (2, 0x00000100)
FLAG4_SCRAPABLE = (3, 0x00000020)
FLAG4_NO_SALVAGE = (3, 0x00800000)
FLAG4_RECRAFTABLE = (3, 0x01000000)
FLAG4_CC_TRINKET = (3, 0x02000000)

# src/server/game/Entities/Item/ItemTemplate.h :: enum SocketColor.  Index is
# the ItemSparse.SocketType_N value; value is the gem-type mask that fits.
# Mirrors: ``SocketColorToGemTypeMask`` (ItemTemplate.cpp).
SOCKET_COLOR_TO_GEM_TYPE_MASK = (
    0,
    0x00000001,                            # Meta
    0x00000002,                            # Red
    0x00000004,                            # Yellow
    0x00000008,                            # Blue
    0x00000010,                            # Hydraulic
    0x00000020,                            # Cogwheel
    0x00000002 | 0x00000004 | 0x00000008,  # Prismatic
    0x00000040, 0x00000080, 0x00000100, 0x00000200, 0x00000400,
    0x00000800, 0x00001000, 0x00002000, 0x00004000, 0x00008000,
    0x00010000,                            # Relic Iron .. Relic Holy
    0x00020000, 0x00040000, 0x00080000,    # Punchcard red/yellow/blue
    0x00100000 | 0x00200000 | 0x00400000,  # Domination
    0x00800000,                            # Cypher
    0x01000000,                            # Tinker
    0x02000000,                            # Primordial
    0x04000000,                            # Fragrance
    0x08000000,                            # Singing Thunder
    0x10000000,                            # Singing Sea
    0x20000000,                            # Singing Wind
    0x40000000,                            # Fiber
)

# src/server/game/Miscellaneous/SharedDefines.h :: enum Classes
CLASS_NAMES = {
    1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest",
    6: "DeathKnight", 7: "Shaman", 8: "Mage", 9: "Warlock", 10: "Monk",
    11: "Druid", 12: "DemonHunter", 13: "Evoker", 14: "Adventurer",
    15: "Traveler",
}

# Map.db2 InstanceType.  Used only to separate dungeon and raid journal
# instances; Trinity's own enum is ``MapTypes`` in DBCEnums.h.
MAP_INSTANCE_TYPE_NONE = 0
MAP_INSTANCE_TYPE_PARTY = 1
MAP_INSTANCE_TYPE_RAID = 2
MAP_INSTANCE_TYPE_NAMES = {
    0: "None", 1: "Party", 2: "Raid", 3: "PvP", 4: "Arena", 5: "Scenario",
}

# src/server/game/DataStores/DBCEnums.h :: enum class ContentTuningFlag
CONTENT_TUNING_FLAG_DISABLED_FOR_ITEM = 0x04

# src/server/game/DataStores/DBCEnums.h :: enum class PlayerConditionFlags
PLAYER_CONDITION_FLAG_CLIENT_EXECUTABLE = 0x0001
PLAYER_CONDITION_FLAG_DISABLED = 0x0100

# src/server/game/DataStores/DBCEnums.h :: enum class ModifierTreeOperator
MODIFIER_TREE_OP_SINGLE_TRUE = 2
MODIFIER_TREE_OP_SINGLE_FALSE = 3
MODIFIER_TREE_OP_ALL = 4
MODIFIER_TREE_OP_SOME = 8

# src/server/game/DataStores/DBCEnums.h :: ModifierTreeType::HasTimeEventPassed
MODIFIER_TREE_TYPE_HAS_TIME_EVENT_PASSED = 289

# SpellItemEnchantmentFlags::ScaleAsAGem (DBCEnums.h)
ENCHANT_FLAG_SCALE_AS_A_GEM = 0x4

# ItemBonusTree.Flags bits consulted by ItemBonusMgr::CanApplyBonusTreeToItem.
BONUS_TREE_FLAG_ALWAYS_APPLY = 0x4
BONUS_TREE_FLAG_REQUIRE_CASTER_WEAPON = 0x8
BONUS_TREE_FLAG_REQUIRE_NON_CASTER_WEAPON = 0x10
BONUS_TREE_FLAG_REQUIRE_CC_TRINKET = 0x20
BONUS_TREE_FLAG_REQUIRE_NON_CC_TRINKET = 0x40

# ItemBonusTreeNode.Flags bit consulted by ItemBonusMgr::ApplyBonusTreeHelper.
BONUS_TREE_NODE_FLAG_INVERT_CONTEXT = 0x1

# ItemScalingConfig.Flags bit consulted by BonusData::AddBonus.
SCALING_CONFIG_FLAG_IGNORE_SQUISH = 0x1

# ItemSquishEra.Flags bit consulted by Item::GetItemLevel.
ITEM_SQUISH_ERA_FLAG_SKIP = 0x1

# ItemSet.SetFlags bit consulted by AddItemsSetItem.
ITEM_SET_FLAG_LEGACY_INACTIVE = 0x1
