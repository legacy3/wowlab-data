"""Source vocabulary for controlled units, transcribed from the pinned TrinityCore.

Every enum here is copied from a header and carries the header coordinate.
The *branch* functions mirror the two consumers that turn a ``SummonProperties``
row into a runtime class:

* ``Map::SummonCreature``   (Entities/Object/Object.cpp:1193-1323) -- picks the
  ``UnitTypeMask`` and therefore the C++ class (``TempSummon`` / ``Minion`` /
  ``Guardian`` / ``Puppet`` / ``Totem``);
* ``Spell::EffectSummonType`` (Spells/SpellEffects.cpp:1867-2085) -- picks the
  summon path (``SummonGuardian`` vs. direct ``SummonCreature``), the
  ``TempSummonType`` for the default path and which identity fields are set;
* ``Guardian::Guardian`` (Entities/Creature/TemporarySummon.cpp:513-523) -- adds
  ``UNIT_MASK_CONTROLABLE_GUARDIAN`` for ``Title == Pet`` or ``Control == PET``.

Names are Trinity's.  Nothing here decides behaviour from a name: the category
of a population record is the branch the consumer takes for the row's integers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import CORPORA, SNAPSHOT_BUILD, TRINITY_COMMIT

TC = "src/server/game"

# ---------------------------------------------------------------------------
# DBCEnums.h -- SummonPropertiesFlags (Flags[0] of SummonProperties.db2)
# ---------------------------------------------------------------------------
SUMMON_PROPERTIES_FLAGS_COORD = f"{TC}/DataStores/DBCEnums.h:2754-2790"
#: name -> (bit, implemented-in-Trinity).  ``NYI`` markers copied from the header comments.
SUMMON_PROPERTIES_FLAGS: dict[str, tuple[int, bool]] = {
    "AttackSummoner": (0x00000001, False),
    "HelpWhenSummonedInCombat": (0x00000002, False),
    "UseLevelOffset": (0x00000004, False),
    "DespawnOnSummonerDeath": (0x00000008, False),
    "OnlyVisibleToSummoner": (0x00000010, True),
    "CannotDismissPet": (0x00000020, False),
    "UseDemonTimeout": (0x00000040, True),
    "UnlimitedSummons": (0x00000080, False),
    "UseCreatureLevel": (0x00000100, True),
    "JoinSummonerSpawnGroup": (0x00000200, False),   # NYI comment, but read by Map::SummonCreature / EffectSummonType
    "DoNotToggle": (0x00000400, False),
    "DespawnWhenExpired": (0x00000800, False),
    "UseSummonerFaction": (0x00001000, True),
    "DoNotFollowMountedSummoner": (0x00002000, False),
    "SavePetAutocast": (0x00004000, False),
    "IgnoreSummonerPhase": (0x00008000, True),
    "OnlyVisibleToSummonerGroup": (0x00010000, True),
    "DespawnOnSummonerLogout": (0x00020000, False),
    "CastRideVehicleSpellOnSummoner": (0x00040000, False),
    "GuardianActsLikePet": (0x00080000, False),
    "DontSnapSessileToGround": (0x00100000, False),
    "SummonFromBattlePetJournal": (0x00200000, True),
    "UnitClutter": (0x00400000, False),
    "DefaultNameColor": (0x00800000, False),
    "UseOwnInvisibilityDetection": (0x01000000, False),
    "DespawnWhenReplaced": (0x02000000, False),
    "DespawnWhenTeleportingOutOfRange": (0x04000000, False),
    "SummonedAtGroupFormationPosition": (0x08000000, False),
    "DontDespawnOnSummonerDeath": (0x10000000, False),
    "UseTitleAsCreatureName": (0x20000000, False),
    "AttackableBySummoner": (0x40000000, False),
    "DontDismissWhenEncounterIsAborted": (0x80000000, False),
}
#: flags that a consumer in the pinned checkout actually reads (grep of ``SummonPropertiesFlags::`` in src/server/game)
SUMMON_FLAG_CONSUMERS: dict[str, list[str]] = {
    "OnlyVisibleToSummoner": [f"{TC}/Spells/SpellEffects.cpp:1889"],
    "OnlyVisibleToSummonerGroup": [f"{TC}/Spells/SpellEffects.cpp:1889", f"{TC}/Spells/SpellEffects.cpp:1895"],
    "JoinSummonerSpawnGroup": [f"{TC}/Spells/SpellEffects.cpp:1944", f"{TC}/Entities/Object/Object.cpp:1232"],
    "UseDemonTimeout": [f"{TC}/Spells/SpellEffects.cpp:2006", f"{TC}/Entities/Creature/TemporarySummon.cpp:199"],
    "IgnoreSummonerPhase": [f"{TC}/Entities/Object/Object.cpp:1278"],
    "UseCreatureLevel": [f"{TC}/Entities/Creature/TemporarySummon.cpp:241"],
    "UseSummonerFaction": [f"{TC}/Entities/Creature/TemporarySummon.cpp:251"],
    "SummonFromBattlePetJournal": [f"{TC}/Entities/Creature/TemporarySummon.cpp:257", f"{TC}/Entities/Unit/Unit.cpp:6309", f"{TC}/Spells/SpellMgr.cpp:2521"],
}

# ---------------------------------------------------------------------------
# SharedDefines.h
# ---------------------------------------------------------------------------
SUMMON_CATEGORY_COORD = f"{TC}/Miscellaneous/SharedDefines.h:6656-6663"
SUMMON_CATEGORY: dict[int, str] = {
    0: "SUMMON_CATEGORY_WILD", 1: "SUMMON_CATEGORY_ALLY", 2: "SUMMON_CATEGORY_PET",
    3: "SUMMON_CATEGORY_PUPPET", 4: "SUMMON_CATEGORY_POSSESSED_VEHICLE", 5: "SUMMON_CATEGORY_VEHICLE",
}
SUMMON_TITLE_COORD = f"{TC}/Miscellaneous/SharedDefines.h:6666-6711"
SUMMON_TITLE: dict[int, str] = {
    0: "None", 1: "Pet", 2: "Guardian", 3: "Minion", 4: "Totem", 5: "Companion", 6: "Runeblade", 7: "Construct",
    8: "Opponent", 9: "Vehicle", 10: "Mount", 11: "Lightwell", 12: "Butler", 13: "aka", 14: "Gateway", 15: "Hatred",
    16: "Statue", 17: "Spirit", 18: "WarBanner", 19: "Heartwarmer", 20: "HiredBy", 21: "PurchasedBy", 22: "Pride",
    23: "TwistedImage", 24: "NoodleCart", 25: "InnerDemon", 26: "Bodyguard", 27: "Name", 28: "Squire", 29: "Champion",
    30: "TheBetrayer", 31: "EruptingReflection", 32: "HopelessReflection", 33: "MalignantReflection",
    34: "WailingReflection", 35: "Assistant", 36: "Enforcer", 37: "Recruit", 38: "Admirer", 39: "EvilTwin",
    40: "Greed", 41: "LostMind", 44: "ServantOfNZoth",
}
SUMMON_SLOT_COORD = f"{TC}/Miscellaneous/SharedDefines.h:6713-6725"
SUMMON_SLOT: dict[int, str] = {
    -1: "SUMMON_SLOT_ANY_TOTEM", 0: "SUMMON_SLOT_PET", 1: "SUMMON_SLOT_TOTEM", 2: "SUMMON_SLOT_TOTEM_2",
    3: "SUMMON_SLOT_TOTEM_3", 4: "SUMMON_SLOT_TOTEM_4", 5: "SUMMON_SLOT_MINIPET", 6: "SUMMON_SLOT_QUEST",
}
MAX_SUMMON_SLOT = 7
MAX_TOTEM_SLOT = 5  # SUMMON_SLOT_TOTEM..SUMMON_SLOT_TOTEM_4 (TemporarySummon.cpp:378-379)

# ---------------------------------------------------------------------------
# Unit.h / ObjectDefines.h / CharmInfo.h / UnitDefines.h / PetDefines.h
# ---------------------------------------------------------------------------
UNIT_TYPE_MASK_COORD = f"{TC}/Entities/Unit/Unit.h:354-367"
UNIT_TYPE_MASK: dict[str, int] = {
    "UNIT_MASK_SUMMON": 0x1, "UNIT_MASK_MINION": 0x2, "UNIT_MASK_GUARDIAN": 0x4, "UNIT_MASK_TOTEM": 0x8,
    "UNIT_MASK_PET": 0x10, "UNIT_MASK_VEHICLE": 0x20, "UNIT_MASK_PUPPET": 0x40, "UNIT_MASK_HUNTER_PET": 0x80,
    "UNIT_MASK_CONTROLABLE_GUARDIAN": 0x100, "UNIT_MASK_ACCESSORY": 0x200,
}
TEMPSUMMON_TYPE_COORD = f"{TC}/Entities/Object/ObjectDefines.h:61-71"
TEMPSUMMON_TYPE: dict[int, str] = {
    1: "TEMPSUMMON_TIMED_OR_DEAD_DESPAWN", 2: "TEMPSUMMON_TIMED_OR_CORPSE_DESPAWN", 3: "TEMPSUMMON_TIMED_DESPAWN",
    4: "TEMPSUMMON_TIMED_DESPAWN_OUT_OF_COMBAT", 5: "TEMPSUMMON_CORPSE_DESPAWN", 6: "TEMPSUMMON_CORPSE_TIMED_DESPAWN",
    7: "TEMPSUMMON_DEAD_DESPAWN", 8: "TEMPSUMMON_MANUAL_DESPAWN",
}
CHARM_TYPE_COORD = f"{TC}/Entities/Unit/CharmInfo.h:67-73"
CHARM_TYPE: dict[int, str] = {0: "CHARM_TYPE_CHARM", 1: "CHARM_TYPE_POSSESS", 2: "CHARM_TYPE_VEHICLE", 3: "CHARM_TYPE_CONVERT"}
PET_TYPE_COORD = f"{TC}/Entities/Pet/PetDefines.h:29-34"
PET_TYPE: dict[int, str] = {0: "SUMMON_PET", 1: "HUNTER_PET"}
PET_SAVE_MODE_COORD = f"{TC}/Entities/Pet/PetDefines.h:40-49"
PET_SAVE_MODE: dict[int, str] = {
    -3: "PET_SAVE_AS_CURRENT", -2: "PET_SAVE_AS_DELETED", -1: "PET_SAVE_NOT_IN_SLOT",
    0: "PET_SAVE_FIRST_ACTIVE_SLOT", 5: "PET_SAVE_FIRST_STABLE_SLOT",
}
MAX_ACTIVE_PETS, MAX_PET_STABLES = 5, 200  # PetDefines.h:36-37
#: exclusive upper bounds (PetDefines.h:45, 47); LAST_ACTIVE (5) == FIRST_STABLE (5)
PET_SAVE_LAST_ACTIVE_SLOT, PET_SAVE_LAST_STABLE_SLOT = 5, 205
REACT_STATES_COORD = f"{TC}/Entities/Unit/UnitDefines.h:539-545"
REACT_STATES: dict[int, str] = {0: "REACT_PASSIVE", 1: "REACT_DEFENSIVE", 2: "REACT_AGGRESSIVE", 3: "REACT_ASSIST"}
COMMAND_STATES_COORD = f"{TC}/Entities/Unit/UnitDefines.h:559-566"
COMMAND_STATES: dict[int, str] = {0: "COMMAND_STAY", 1: "COMMAND_FOLLOW", 2: "COMMAND_ATTACK", 3: "COMMAND_ABANDON", 4: "COMMAND_MOVE_TO"}
UNIT_FLAGS_COORD = f"{TC}/Entities/Unit/UnitDefines.h:170,178,191"
UNIT_FLAGS_CONTROL: dict[str, int] = {"UNIT_FLAG_PLAYER_CONTROLLED": 0x8, "UNIT_FLAG_PET_IN_COMBAT": 0x800, "UNIT_FLAG_POSSESSED": 0x01000000}
PET_ENTRY_COORD = f"{TC}/Entities/Creature/TemporarySummon.h:23-39"
#: hard-coded creature entries the engine names (identity checks, not data)
PET_ENTRY: dict[str, int] = {
    "PET_IMP": 416, "PET_FEL_HUNTER": 691, "PET_VOID_WALKER": 1860, "PET_SUCCUBUS": 1863, "PET_DOOMGUARD": 18540,
    "PET_FELGUARD": 30146, "PET_INCUBUS": 184600, "PET_GHOUL": 26125, "PET_SPIRIT_WOLF": 29264,
}
#: creature entries switched on inside Guardian::InitStatsForLevel (Pet.cpp) and elsewhere -- stat semantics belong to Track B
HARDCODED_CREATURE_SWITCH: dict[int, str] = {
    510: "mage Water Elemental (Pet.cpp:958)", 1964: "force of nature (Pet.cpp:963)", 15352: "earth elemental (Pet.cpp:972)",
    15438: "fire elemental (Pet.cpp:980)", 19668: "Shadowfiend (Pet.cpp:992); NPC_PRIEST_SHADOWFIEND (scripts/Spells/spell_priest.cpp:317)",
    19833: "Snake Trap - Venomous Snake (Pet.cpp:1005)",
    19921: "Snake Trap - Viper (Pet.cpp:1011)", 29264: "Feral Spirit (Pet.cpp:1017)", 31216: "Mirror Image (Pet.cpp:1034)",
    27829: "Ebon Gargoyle (Pet.cpp:1045)", 28017: "Bloodworms (Pet.cpp:1057)",
    27893: "Dancing Rune Weapon: weapon display copied from owner (SpellEffects.cpp:5054-5064); NPC_DK_DANCING_RUNE_WEAPON (scripts/Spells/spell_dk.cpp:117)",
    62982: "NPC_PRIEST_MINDBENDER (scripts/Spells/spell_priest.cpp:316)", 224466: "NPC_PRIEST_VOIDWRAITH (scripts/Spells/spell_priest.cpp:318)",
    198236: "NPC_PRIEST_DIVINE_IMAGE (scripts/Spells/spell_priest.cpp:315)",
}

# ---------------------------------------------------------------------------
# handler tables: which effect / aura types create or control a unit
# ---------------------------------------------------------------------------
EFFECT_HANDLERS_COORD = f"{TC}/Spells/SpellEffects.cpp:120-352"
#: effect id -> (Trinity name, handler, coordinate of the table entry)
EFFECT_HANDLERS: dict[int, tuple[str, str, str]] = {
    28: ("SPELL_EFFECT_SUMMON", "Spell::EffectSummonType", f"{TC}/Spells/SpellEffects.cpp:120"),
    34: ("SPELL_EFFECT_SUMMON_CHANGE_ITEM", "Spell::EffectSummonChangeItem", f"{TC}/Spells/SpellEffects.cpp:126"),
    55: ("SPELL_EFFECT_TAMECREATURE", "Spell::EffectTameCreature", f"{TC}/Spells/SpellEffects.cpp:147"),
    56: ("SPELL_EFFECT_SUMMON_PET", "Spell::EffectSummonPet", f"{TC}/Spells/SpellEffects.cpp:148"),
    76: ("SPELL_EFFECT_SUMMON_OBJECT_WILD", "Spell::EffectSummonObjectWild", f"{TC}/Spells/SpellEffects.cpp:168"),
    85: ("SPELL_EFFECT_SUMMON_PLAYER", "Spell::EffectSummonPlayer", f"{TC}/Spells/SpellEffects.cpp:177"),
    102: ("SPELL_EFFECT_DISMISS_PET", "Spell::EffectDismissPet", f"{TC}/Spells/SpellEffects.cpp:194"),
    104: ("SPELL_EFFECT_SUMMON_OBJECT_SLOT1", "Spell::EffectSummonObject", f"{TC}/Spells/SpellEffects.cpp:196"),
    119: ("SPELL_EFFECT_APPLY_AREA_AURA_PET", "Spell::EffectUnused", f"{TC}/Spells/SpellEffects.cpp:211"),
    135: ("SPELL_EFFECT_CALL_PET", "Spell::EffectNULL", f"{TC}/Spells/SpellEffects.cpp:227"),
    152: ("SPELL_EFFECT_SUMMON_RAF_FRIEND", "Spell::EffectSummonRaFFriend", f"{TC}/Spells/SpellEffects.cpp:244"),
    153: ("SPELL_EFFECT_CREATE_TAMED_PET", "Spell::EffectCreateTamedPet", f"{TC}/Spells/SpellEffects.cpp:245"),
    168: ("SPELL_EFFECT_ALLOW_CONTROL_PET", "Spell::EffectNULL", f"{TC}/Spells/SpellEffects.cpp:260"),
    188: ("SPELL_EFFECT_SUMMON_STABLED_PET_AS_GUARDIAN", "Spell::EffectNULL", f"{TC}/Spells/SpellEffects.cpp:280"),
    199: ("SPELL_EFFECT_DESPAWN_SUMMON", "Spell::EffectNULL", f"{TC}/Spells/SpellEffects.cpp:291"),
    202: ("SPELL_EFFECT_APPLY_AREA_AURA_SUMMONS", "Spell::EffectUnused", f"{TC}/Spells/SpellEffects.cpp:294"),
    260: ("SPELL_EFFECT_SUMMON_STABLED_PET", "Spell::EffectNULL", f"{TC}/Spells/SpellEffects.cpp:352"),
}
#: effects that make a *Unit* (the population filter); object summons (76/104) are excluded by the brief
UNIT_CREATING_EFFECTS = {28, 55, 56, 153}
UNIT_LIFECYCLE_EFFECTS = {102, 135, 168, 188, 199, 260}
AURA_HANDLERS_COORD = f"{TC}/Spells/Auras/SpellAuraEffects.cpp:74-501"
AURA_HANDLERS: dict[int, tuple[str, str, str]] = {
    2: ("SPELL_AURA_MOD_POSSESS", "AuraEffect::HandleModPossess", f"{TC}/Spells/Auras/SpellAuraEffects.cpp:74"),
    6: ("SPELL_AURA_MOD_CHARM", "AuraEffect::HandleModCharm", f"{TC}/Spells/Auras/SpellAuraEffects.cpp:78"),
    146: ("SPELL_AURA_ALLOW_TAME_PET_TYPE", "AuraEffect::HandleNoImmediateEffect", f"{TC}/Spells/Auras/SpellAuraEffects.cpp:218"),
    157: ("SPELL_AURA_PET_DAMAGE_MULTI", "AuraEffect::HandleNULL", f"{TC}/Spells/Auras/SpellAuraEffects.cpp:229"),
    177: ("SPELL_AURA_AOE_CHARM", "AuraEffect::HandleCharmConvert", f"{TC}/Spells/Auras/SpellAuraEffects.cpp:249"),
    339: ("SPELL_AURA_MOD_CRIT_CHANCE_FOR_CASTER_PET", "AuraEffect::HandleNoImmediateEffect", f"{TC}/Spells/Auras/SpellAuraEffects.cpp:411"),
    378: ("SPELL_AURA_MOD_POSSESS_PET", "AuraEffect::HandleModPossessPet", f"{TC}/Spells/Auras/SpellAuraEffects.cpp:450"),
    381: ("SPELL_AURA_MOD_DAMAGE_TAKEN_FROM_CASTER_PET", "AuraEffect::HandleNULL", f"{TC}/Spells/Auras/SpellAuraEffects.cpp:453"),
    382: ("SPELL_AURA_MOD_PET_STAT_PCT", "AuraEffect::HandleNULL", f"{TC}/Spells/Auras/SpellAuraEffects.cpp:454"),
    429: ("SPELL_AURA_MOD_SUMMON_DAMAGE", "AuraEffect::HandleNULL", f"{TC}/Spells/Auras/SpellAuraEffects.cpp:501"),
}
CONTROL_AURAS = {2, 6, 177, 378}
SPELL_EFFECT_APPLY_AURA = 6

#: MiscValueB values for which EffectSummonType reads the number of summons from the effect value (SpellEffects.cpp:1915-1937)
NUMSUMMONS_FROM_EFFECT_VALUE: tuple[int, ...] = (64, 61, 1101, 66, 648, 2301, 1061, 1261, 629, 181, 715, 1562, 833, 1161, 713)
NUMSUMMONS_COORD = f"{TC}/Spells/SpellEffects.cpp:1915-1937"


# ---------------------------------------------------------------------------
# decoding + branch mirrors
# ---------------------------------------------------------------------------

def decode_summon_flags(flags0: int) -> dict[str, Any]:
    """``SummonPropertiesEntry::GetFlags`` reads only ``Flags[0]`` (DB2Structure.h:4325)."""
    names: list[str] = []
    nyi: list[str] = []
    known = 0
    for name, (bit, implemented) in SUMMON_PROPERTIES_FLAGS.items():
        known |= bit
        if flags0 & bit:
            names.append(name)
            if not implemented:
                nyi.append(name)
    unknown = flags0 & ~known & 0xFFFFFFFF
    return {"value": flags0, "names": names, "nyi": nyi, "unknown_bits": unknown,
            "consumed": [n for n in names if n in SUMMON_FLAG_CONSUMERS]}


@dataclass
class SummonProps:
    id: int
    control: int
    faction: int
    title: int
    slot: int
    flags0: int
    flags1: int

    @property
    def flag_names(self) -> list[str]:
        return decode_summon_flags(self.flags0)["names"]

    def has(self, name: str) -> bool:
        return bool(self.flags0 & SUMMON_PROPERTIES_FLAGS[name][0])

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "control": self.control, "control_name": SUMMON_CATEGORY.get(self.control, f"unknown({self.control})"),
                "faction": self.faction, "title": self.title, "title_name": SUMMON_TITLE.get(self.title, f"unknown({self.title})"),
                "slot": self.slot, "slot_name": SUMMON_SLOT.get(self.slot, f"unknown({self.slot})"),
                "flags": decode_summon_flags(self.flags0), "flags_1": self.flags1}


def unit_mask_for(p: SummonProps | None) -> tuple[str | None, str]:
    """Mirrors: ``Map::SummonCreature`` mask selection (Object.cpp:1195-1241) and the
    ``new`` switch (Object.cpp:1246-1263).  Returns (unit mask name, C++ class) or
    (None, 'nullptr') when the function returns ``nullptr`` (unknown Control)."""
    if p is None:
        return "UNIT_MASK_SUMMON", "TempSummon"
    c = p.control
    if c == 2:
        mask = "UNIT_MASK_GUARDIAN"
    elif c == 3:
        mask = "UNIT_MASK_PUPPET"
    elif c in (4, 5):
        mask = "UNIT_MASK_MINION"
    elif c in (0, 1):
        t = p.title
        if t in (3, 2, 6):
            mask = "UNIT_MASK_GUARDIAN"
        elif t in (4, 11):
            mask = "UNIT_MASK_TOTEM"
        elif t in (9, 10):
            mask = "UNIT_MASK_SUMMON"
        elif t == 5:
            mask = "UNIT_MASK_MINION"
        else:
            mask = "UNIT_MASK_GUARDIAN" if p.has("JoinSummonerSpawnGroup") else "UNIT_MASK_SUMMON"
    else:
        return None, "nullptr"
    cls = {"UNIT_MASK_SUMMON": "TempSummon", "UNIT_MASK_GUARDIAN": "Guardian", "UNIT_MASK_PUPPET": "Puppet",
           "UNIT_MASK_TOTEM": "Totem", "UNIT_MASK_MINION": "Minion"}[mask]
    return mask, cls


def controllable_guardian(p: SummonProps | None, cls: str) -> bool:
    """Mirrors: ``Guardian::Guardian`` (TemporarySummon.cpp:518-522)."""
    return cls == "Guardian" and p is not None and (p.title == 1 or p.control == 2)


def default_branch_tempsummon_type(duration_ms: int | None, p: SummonProps) -> str:
    """Mirrors: the ``default:`` case of ``EffectSummonType`` (SpellEffects.cpp:2001-2007);
    ``duration`` is ``SpellInfo::CalcDuration`` (0 when no DurationEntry, -1 permanent)."""
    d = 0 if duration_ms is None else duration_ms
    if d == 0:
        return "TEMPSUMMON_DEAD_DESPAWN"
    if d == -1:
        return "TEMPSUMMON_MANUAL_DESPAWN"
    if p.has("UseDemonTimeout"):
        return "TEMPSUMMON_TIMED_DESPAWN_OUT_OF_COMBAT"
    return "TEMPSUMMON_TIMED_DESPAWN"


def initstats_tempsummon_type(duration_ms: int | None, p: SummonProps | None) -> str:
    """Mirrors: ``TempSummon::InitStats`` (TemporarySummon.cpp:195-203) for summons whose
    type is still ``TEMPSUMMON_MANUAL_DESPAWN`` (constructor default, :38)."""
    d = 0 if duration_ms is None else duration_ms
    if d <= 0:
        return "TEMPSUMMON_DEAD_DESPAWN"
    if p is not None and p.has("UseDemonTimeout"):
        return "TEMPSUMMON_TIMED_DESPAWN_OUT_OF_COMBAT"
    return "TEMPSUMMON_TIMED_DESPAWN"


@dataclass
class Branch:
    """The path ``Spell::EffectSummonType`` takes for one effect row."""
    branch: str                      # identifier of the switch arm
    category: str                    # research category derived from the branch (never from a name)
    cxx_class: str                   # runtime class instantiated by Map::SummonCreature
    unit_mask: str | None
    controllable_guardian: bool
    summon_path: str                 # SummonGuardian | Map::SummonCreature | none
    tempsummon_type: str | None      # for the TempSummon the effect creates (None when no unit)
    owner_guid: str                  # what UnitData::SummonedBy is set to
    creator_guid: str                # what UnitData::CreatedBy is set to
    demon_creator: str
    num_summons: str
    coordinates: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence_class: str = "trinity-consumer"

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def summon_branch(p: SummonProps | None, creature_entry: int, duration_ms: int | None, misc1: int) -> Branch:
    """Mirrors: ``Spell::EffectSummonType`` (SpellEffects.cpp:1867-2085).  ``p`` is the
    ``SummonProperties`` row for ``MiscValueB``; ``None`` when the snapshot lacks it."""
    E = f"{TC}/Spells/SpellEffects.cpp"
    num = "effect value (max 1)" if misc1 in NUMSUMMONS_FROM_EFFECT_VALUE else "1"
    if not creature_entry:
        return Branch("entry-zero", "unresolved", "none", None, False, "none", None, "-", "-", "-", num,
                      [f"{E}:1872-1874"], ["EffectSummonType returns before any lookup: MiscValue (creature entry) is 0"], "unresolved")
    if p is None:
        return Branch("no-summon-properties", "unresolved", "none", None, False, "none", None, "-", "-", "-", num,
                      [f"{E}:1876-1881"], [f"sSummonPropertiesStore has no row {misc1}: TC_LOG_ERROR 'Unhandled summon type' and return"], "unresolved")
    mask, cls = unit_mask_for(p)
    cg = controllable_guardian(p, cls)
    c, t = p.control, p.title
    coords = [f"{E}:1939", f"{TC}/Entities/Object/Object.cpp:1195-1241"]

    def guardian(arm: str) -> Branch:
        # SummonGuardian (SpellEffects.cpp:5013-5068): Map::SummonCreature(summoner = unitCaster or totem owner);
        # Minion::InitStats sets CreatorGUID = owner and calls owner->SetMinion (OwnerGUID = owner).
        # Only a Guardian/Minion/Totem/Puppet mask gives an owner; a plain TempSummon out of this path has
        # neither owner nor creator (EffectSummonType's trailing SetCreatorGUID only runs for `summon != nullptr`).
        if cls in ("Guardian", "Minion", "Totem", "Puppet"):
            owner, creator = "unitCaster (totem -> its owner) via Minion::InitStats -> Unit::SetMinion", "owner (Minion::InitStats)"
        else:
            owner, creator = "none (plain TempSummon: SummonGuardian path sets no owner)", "none"
        cat = {"Guardian": ("controllable-guardian" if cg else "guardian"), "Minion": "minion", "Totem": "totem",
               "Puppet": "puppet", "TempSummon": "wild-summon-via-SummonGuardian"}[cls]
        ts = initstats_tempsummon_type(duration_ms, p)
        return Branch(arm, cat, cls, mask, cg, "SummonGuardian", ts, owner, creator,
                      "-", num, coords + [f"{E}:5013-5068", f"{TC}/Entities/Creature/TemporarySummon.cpp:456-466",
                                          f"{TC}/Entities/Unit/Unit.cpp:6246-6337"],
                      ["duration = SpellInfo::CalcDuration(m_originalCaster) (SpellEffects.cpp:5024)",
                       "TempSummonType left at ctor default MANUAL_DESPAWN, resolved by TempSummon::InitStats (TemporarySummon.cpp:195-203)"])

    if c in (0, 1):
        if p.has("JoinSummonerSpawnGroup"):
            b = guardian("wild/ally+JoinSummonerSpawnGroup")
            b.coordinates.insert(1, f"{E}:1944-1948")
            return b
        if t in (1, 2, 6, 3):
            b = guardian(f"wild/ally+Title::{SUMMON_TITLE[t]}")
            b.coordinates.insert(1, f"{E}:1952-1957")
            if t == 1 and cls != "Guardian":
                b.notes.append("Title::Pet without JoinSummonerSpawnGroup: Map::SummonCreature falls to its default arm -> plain TempSummon (Object.cpp:1231-1234)")
            return b
        if t in (9, 10, 11):
            cat = "lightwell-totem" if t == 11 else "vehicle-summon"
            return Branch(f"wild/ally+Title::{SUMMON_TITLE[t]}", cat, cls, mask, cg, "Map::SummonCreature",
                          initstats_tempsummon_type(duration_ms, p), "none (no SetOwnerGUID on this arm)" if cls == "TempSummon" else "unitCaster via Minion::InitStats",
                          "caster (EffectSummonType:2082)", "-", num, coords + [f"{E}:1959-1968", f"{E}:2080-2084"])
        if t == 4:
            return Branch("wild/ally+Title::Totem", "totem", cls, mask, cg, "Map::SummonCreature",
                          initstats_tempsummon_type(duration_ms, p), "unitCaster via Minion::InitStats -> SetMinion",
                          "owner (Minion::InitStats) then caster (EffectSummonType:2082)", "-", num,
                          coords + [f"{E}:1969-1984", f"{TC}/Entities/Totem/Totem.cpp:53-88", f"{E}:2080-2084"],
                          ["effect value != 0 overrides max health (SpellEffects.cpp:1978-1982)",
                           "Totem::Update ignores TempSummonType: own m_duration timer + owner alive check (Totem.cpp:34-51)"])
        if t == 5:
            return Branch("wild/ally+Title::Companion", "companion-minion", cls, mask, cg, "Map::SummonCreature",
                          initstats_tempsummon_type(duration_ms, p), "unitCaster via Minion::InitStats -> SetMinion (also CritterGUID, Unit.cpp:6303-6322)",
                          "owner then caster (EffectSummonType:2082)", "-", num, coords + [f"{E}:1985-1996", f"{E}:2080-2084"],
                          ["SetImmuneToAll(true) (SpellEffects.cpp:1994)"])
        # default arm
        ts = default_branch_tempsummon_type(duration_ms, p)
        owner = "caster (SetOwnerGUID, EffectSummonType:2023-2024)" if c == 1 else "none"
        demon = "caster if player (SetDemonCreatorGUID, :2025-2026)" if c == 0 else "-"
        cat = "ally-summon" if c == 1 else "wild-summon"
        return Branch(f"{'ally' if c == 1 else 'wild'}+default", cat, cls, mask, cg, "Map::SummonCreature", ts, owner,
                      "none (early return before the trailing SetCreatorGUID, :2030)", demon, num,
                      coords + [f"{E}:1997-2031"],
                      ["caster = m_originalCaster if set else m_caster (SpellEffects.cpp:1883-1885); Map::SummonCreature summoner = unitCaster (GetUnitCasterForEffectHandlers, Spell.cpp:8353-8356)",
                       "ALLY owner is set by SetOwnerGUID only -- the unit is NOT inserted in owner->m_Controlled (no SetMinion on this arm)"])
    if c == 2:
        b = guardian("Control::PET")
        b.coordinates.insert(1, f"{E}:2035-2037")
        return b
    if c == 3:
        return Branch("Control::PUPPET", "puppet", cls, mask, cg, "Map::SummonCreature", initstats_tempsummon_type(duration_ms, p),
                      "unitCaster via Minion::InitStats -> SetMinion", "owner then caster (:2082)", "-", num,
                      coords + [f"{E}:2038-2045", f"{TC}/Entities/Creature/TemporarySummon.cpp:556-588"],
                      ["Puppet::InitSummon -> SetCharmedBy(owner, CHARM_TYPE_POSSESS) (TemporarySummon.cpp:569-574): owner must be a player (ctor ASSERT :559)"])
    if c in (4, 5):
        return Branch(f"Control::{SUMMON_CATEGORY[c]}", "vehicle", cls, mask, cg, "Map::SummonCreature", initstats_tempsummon_type(duration_ms, p),
                      "unitCaster via Minion::InitStats -> SetMinion", "owner then caster (:2082)", "-", num,
                      coords + [f"{E}:2046-2077"], ["caster casts VEHICLE_SPELL_RIDE_HARDCODED / effect-value spell on the summon (:2058-2075)"])
    return Branch(f"Control::{c}", "unresolved", "nullptr", None, False, "none", None, "-", "-", "-", num,
                  coords + [f"{TC}/Entities/Object/Object.cpp:1238-1239"], [f"Control {c} is outside the switch: Map::SummonCreature returns nullptr"], "unresolved")


def control_aura_branch(aura: int, misc0: int) -> dict[str, Any]:
    """Mirrors the charm/possess aura handlers (SpellAuraEffects.cpp:3238-3331)."""
    A = f"{TC}/Spells/Auras/SpellAuraEffects.cpp"
    U = f"{TC}/Entities/Unit/Unit.cpp"
    if aura == 6:
        return {"category": "charmed", "charm_type": "CHARM_TYPE_CHARM", "handler": "AuraEffect::HandleModCharm",
                "coordinates": [f"{A}:3303-3316", f"{U}:11790-11962"], "charmer": "Aura::GetCaster() (aura caster)",
                "notes": ["target->SetCharmedBy(caster, CHARM_TYPE_CHARM, aurApp)"], "evidence_class": "trinity-consumer"}
    if aura == 2:
        return {"category": "possessed", "charm_type": "CHARM_TYPE_POSSESS (CHARM_TYPE_CHARM when the aura caster is a creature)",
                "handler": "AuraEffect::HandleModPossess", "coordinates": [f"{A}:3238-3258", f"{U}:11790-11962"],
                "charmer": "Aura::GetCaster() (must be a player for POSSESS, Unit.cpp:11802)",
                "notes": ["creature casters fall back to HandleModCharm (:3248-3252)"], "evidence_class": "trinity-consumer"}
    if aura == 177:
        return {"category": "charmed-convert", "charm_type": "CHARM_TYPE_CONVERT", "handler": "AuraEffect::HandleCharmConvert",
                "coordinates": [f"{A}:3318-3331"], "charmer": "Aura::GetCaster()", "notes": [], "evidence_class": "trinity-consumer"}
    if aura == 378:
        return {"category": "possessed-own-pet", "charm_type": "CHARM_TYPE_POSSESS", "handler": "AuraEffect::HandleModPossessPet",
                "coordinates": [f"{A}:3260-3301"], "charmer": "Aura::GetCaster() (player) on its own Pet only (:3266-3281)",
                "notes": ["target must be caster->GetPet(); on removal the pet is removed if out of visibility range (:3289-3290)"],
                "evidence_class": "trinity-consumer"}
    return {"category": "unresolved", "handler": "?", "coordinates": [], "charmer": "-", "notes": [f"aura {aura} not a control aura"], "evidence_class": "unresolved"}


def effect_branch(effect: int, aura: int, misc0: int, misc1: int, duration_ms: int | None, p: SummonProps | None) -> dict[str, Any]:
    """Category + branch for any population effect row.  ``category`` is derived from the
    handler the pinned checkout dispatches to and the switch arm it takes."""
    E = f"{TC}/Spells/SpellEffects.cpp"
    if effect == 28:
        b = summon_branch(p, misc0, duration_ms, misc1)
        return {"handler": "Spell::EffectSummonType", "category": b.category, "branch": b.to_dict()}
    if effect == 56:
        return {"handler": "Spell::EffectSummonPet", "category": "permanent-class-pet",
                "branch": {"branch": "EffectSummonPet", "cxx_class": "Pet", "pet_type": "SUMMON_PET", "unit_mask": "UNIT_MASK_PET|UNIT_MASK_GUARDIAN|UNIT_MASK_MINION|UNIT_MASK_SUMMON|UNIT_MASK_CONTROLABLE_GUARDIAN",
                           "summon_path": "Player::SummonPet (LoadPetFromDB or fresh Create)", "tempsummon_type": None,
                           "owner_guid": "owner (Unit::SetMinion, Player.cpp:30493 / Pet.cpp:374)", "creator_guid": "owner (Player.cpp:30486 / Pet.cpp:307)",
                           "coordinates": [f"{E}:2656-2744", f"{TC}/Entities/Player/Player.cpp:30438-30523", f"{TC}/Entities/Pet/Pet.cpp:205-449", f"{TC}/Entities/Pet/Pet.cpp:46-65"],
                           "notes": ["entry 0 => Call Pet: PetSaveMode from effect value (SpellEffects.cpp:2714-2716)",
                                     "caster not a player (and not a totem owned by one) => SummonGuardian with SummonProperties 67 (:2671-2677)",
                                     "same-entry pet already active => teleport + PetSpellInitialize, no new unit (:2682-2706)",
                                     "duration passed to SummonPet is 0 (:2721): the pet is not timed by the summon spell's duration"],
                           "evidence_class": "trinity-consumer"}}
    if effect == 55:
        return {"handler": "Spell::EffectTameCreature", "category": "hunter-pet",
                "branch": {"branch": "EffectTameCreature", "cxx_class": "Pet", "pet_type": "HUNTER_PET", "summon_path": "Unit::CreateTamedPetFrom(Creature*) -> Pet::CreateBaseAtCreature -> Unit::InitTamedPet",
                           "owner_guid": "caster (Unit::SetMinion, SpellEffects.cpp:2647)", "creator_guid": "caster (Unit.cpp:11145)",
                           "coordinates": [f"{E}:2601-2654", f"{TC}/Entities/Unit/Unit.cpp:11089-11111", f"{TC}/Entities/Unit/Unit.cpp:11133-11168", f"{TC}/Entities/Pet/Pet.cpp:768-836"],
                           "notes": ["caster must be a hunter with no pet (:2607, :2621); target creature is despawned (:2633)", "needs a free active stable slot (Unit.cpp:11137-11143)"],
                           "evidence_class": "trinity-consumer"}}
    if effect == 153:
        return {"handler": "Spell::EffectCreateTamedPet", "category": "hunter-pet",
                "branch": {"branch": "EffectCreateTamedPet", "cxx_class": "Pet", "pet_type": "HUNTER_PET", "summon_path": "Unit::CreateTamedPetFrom(entry) -> Pet::CreateBaseAtCreatureInfo -> Unit::InitTamedPet",
                           "owner_guid": "target player (SetMinion, :4933)", "creator_guid": "target player (Unit.cpp:11145)",
                           "coordinates": [f"{E}:4911-4940", f"{TC}/Entities/Unit/Unit.cpp:11113-11131"],
                           "notes": ["target must be a hunter player without a pet (:4916)"], "evidence_class": "trinity-consumer"}}
    if effect == 102:
        return {"handler": "Spell::EffectDismissPet", "category": "lifecycle-op",
                "branch": {"branch": "EffectDismissPet", "coordinates": [f"{E}:3586-3598"], "notes": ["target Pet -> Pet::Remove(PET_SAVE_NOT_IN_SLOT)"], "evidence_class": "trinity-consumer"}}
    if effect in EFFECT_HANDLERS and EFFECT_HANDLERS[effect][1] == "Spell::EffectNULL":
        name, handler, coord = EFFECT_HANDLERS[effect]
        return {"handler": handler, "category": "no-consumer",
                "branch": {"branch": name, "coordinates": [coord], "notes": [f"{name} dispatches to Spell::EffectNULL: no server behaviour in the pinned checkout"], "evidence_class": "trinity-consumer"}}
    if effect == SPELL_EFFECT_APPLY_AURA and aura in CONTROL_AURAS:
        b = control_aura_branch(aura, misc0)
        return {"handler": b["handler"], "category": b["category"], "branch": b}
    return {"handler": "?", "category": "unresolved", "branch": {"branch": "not-a-population-effect", "coordinates": [], "notes": [], "evidence_class": "unresolved"}}


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

def vocabulary_corpus() -> dict[str, Any]:
    return {
        "provenance": {"snapshot_build": SNAPSHOT_BUILD, "trinitycore_commit": TRINITY_COMMIT,
                       "generator": "python3 controlled_units.py vocabulary", "note": "transcribed enums; every block names its header coordinate"},
        "SummonPropertiesFlags": {"coordinate": SUMMON_PROPERTIES_FLAGS_COORD, "values": {k: {"bit": v[0], "implemented": v[1], "consumers": SUMMON_FLAG_CONSUMERS.get(k, [])} for k, v in SUMMON_PROPERTIES_FLAGS.items()},
                                  "reader": "SummonPropertiesEntry::GetFlags reads Flags[0] only (DB2Structure.h:4317-4326)"},
        "SummonCategory": {"coordinate": SUMMON_CATEGORY_COORD, "values": SUMMON_CATEGORY},
        "SummonTitle": {"coordinate": SUMMON_TITLE_COORD, "values": SUMMON_TITLE},
        "SummonSlot": {"coordinate": SUMMON_SLOT_COORD, "values": SUMMON_SLOT, "MAX_SUMMON_SLOT": MAX_SUMMON_SLOT, "MAX_TOTEM_SLOT": MAX_TOTEM_SLOT},
        "UnitTypeMask": {"coordinate": UNIT_TYPE_MASK_COORD, "values": UNIT_TYPE_MASK},
        "TempSummonType": {"coordinate": TEMPSUMMON_TYPE_COORD, "values": TEMPSUMMON_TYPE},
        "CharmType": {"coordinate": CHARM_TYPE_COORD, "values": CHARM_TYPE},
        "PetType": {"coordinate": PET_TYPE_COORD, "values": PET_TYPE},
        "PetSaveMode": {"coordinate": PET_SAVE_MODE_COORD, "values": PET_SAVE_MODE, "MAX_ACTIVE_PETS": MAX_ACTIVE_PETS, "MAX_PET_STABLES": MAX_PET_STABLES,
                        "PET_SAVE_LAST_ACTIVE_SLOT_exclusive": PET_SAVE_LAST_ACTIVE_SLOT, "PET_SAVE_LAST_STABLE_SLOT_exclusive": PET_SAVE_LAST_STABLE_SLOT},
        "ReactStates": {"coordinate": REACT_STATES_COORD, "values": REACT_STATES},
        "CommandStates": {"coordinate": COMMAND_STATES_COORD, "values": COMMAND_STATES},
        "UnitFlagsControl": {"coordinate": UNIT_FLAGS_COORD, "values": UNIT_FLAGS_CONTROL},
        "PetEntry": {"coordinate": PET_ENTRY_COORD, "values": PET_ENTRY},
        "HardcodedCreatureSwitch": HARDCODED_CREATURE_SWITCH,
        "EffectHandlers": {"coordinate": EFFECT_HANDLERS_COORD, "values": {str(k): {"name": v[0], "handler": v[1], "coordinate": v[2]} for k, v in EFFECT_HANDLERS.items()},
                           "unit_creating": sorted(UNIT_CREATING_EFFECTS), "lifecycle": sorted(UNIT_LIFECYCLE_EFFECTS)},
        "AuraHandlers": {"coordinate": AURA_HANDLERS_COORD, "values": {str(k): {"name": v[0], "handler": v[1], "coordinate": v[2]} for k, v in AURA_HANDLERS.items()},
                         "control": sorted(CONTROL_AURAS)},
        "NumSummonsFromEffectValue": {"coordinate": NUMSUMMONS_COORD, "values": list(NUMSUMMONS_FROM_EFFECT_VALUE)},
        "branch_table": _branch_table(),
    }


def _branch_table() -> list[dict[str, Any]]:
    """Every (Control, Title-class, JoinSummonerSpawnGroup) combination the two switches distinguish."""
    out = []
    for control in range(0, 7):
        for title in (0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 17):
            for join in (0, 0x200):
                p = SummonProps(0, control, 0, title, 0, join, 0)
                b = summon_branch(p, 1, 10000, 0)
                out.append({"control": control, "title": title, "join_spawn_group": bool(join), "branch": b.branch,
                            "category": b.category, "class": b.cxx_class, "unit_mask": b.unit_mask, "controllable_guardian": b.controllable_guardian})
    return out


def register(sub) -> None:
    p = sub.add_parser("vocabulary", help="write controlled-unit-corpora/vocabulary.json (transcribed enums + branch table)")
    p.set_defaults(func=cmd_vocabulary)


def cmd_vocabulary(args) -> int:
    CORPORA.mkdir(parents=True, exist_ok=True)
    path = CORPORA / "vocabulary.json"
    path.write_text(json.dumps(vocabulary_corpus(), indent=1) + "\n", encoding="utf-8")
    print(path)
    return 0
