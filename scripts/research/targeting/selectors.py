"""Implicit-target selector vocabulary (Track A).

The five-axis descriptor of every raw ``ImplicitTarget`` id and the consumer that
handles it.  The table below is a transcription of
``SpellImplicitTargetInfo::_data`` (SpellInfo.cpp:246-401); it is **not** the
source of truth: ``tests/test_tg_a_probe.py`` differentially checks every raw id
``0..TOTAL_SPELL_TARGETS-1`` (and every derived function here) against
``tools/tc_target_selector_probe``, which compiles Trinity's own table and
functions.

A known selector id is **not** a complete target policy.  The descriptor only
says which ``Spell::SelectImplicit*`` routine runs; the recipients further
depend on effect-mask grouping (Spell.cpp:741-790), the other selector of the
pair, ``m_targets`` state written by earlier selectors, per-candidate checks
(``WorldObjectSpellTargetCheck``, ``SpellInfo::CheckTarget``,
``Spell::CheckEffectTarget``), caps, chains, conditions and scripts.

Mirrors (vocabulary): SharedDefines.h:2960-3110 (``enum Targets``),
SpellInfo.h:41-112 (axis enums), SpellInfo.cpp:44-244 (functions).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import lru_cache

from . import FailClosed

TOTAL_SPELL_TARGETS = 153  # SharedDefines.h:3109 (probe-checked)
TOTAL_SPELL_EFFECTS = 356  # SharedDefines.h:1703 (probe-checked)

# -- axis enums (SpellInfo.h:41-112) ----------------------------------------------
OBJECT = ("NONE", "SRC", "DEST", "UNIT", "UNIT_AND_DEST", "GOBJ", "GOBJ_ITEM", "ITEM", "CORPSE",
          "CORPSE_ENEMY", "CORPSE_ALLY")                                   # SpellInfo.h:63
REFERENCE = ("NONE", "CASTER", "TARGET", "LAST", "SRC", "DEST")            # SpellInfo.h:53
CATEGORY = ("NYI", "DEFAULT", "CHANNEL", "NEARBY", "CONE", "AREA", "TRAJ", "LINE")  # SpellInfo.h:41
CHECK = ("DEFAULT", "ENTRY", "ENEMY", "ALLY", "PARTY", "RAID", "RAID_CLASS", "PASSENGER", "SUMMONED")  # :79
DIRECTION = ("NONE", "FRONT", "BACK", "RIGHT", "LEFT", "FRONT_RIGHT", "BACK_RIGHT", "BACK_LEFT",
             "FRONT_LEFT", "RANDOM", "ENTRY")                              # SpellInfo.h:92
EFFECT_IMPLICIT = ("NONE", "EXPLICIT", "CASTER")                           # SpellInfo.h:107

# -- SpellCastTargetFlags (SpellDefines.h:309) --------------------------------------
TARGET_FLAG = {
    "NONE": 0x0, "UNUSED_1": 0x1, "UNIT": 0x2, "UNIT_RAID": 0x4, "UNIT_PARTY": 0x8, "ITEM": 0x10,
    "SOURCE_LOCATION": 0x20, "DEST_LOCATION": 0x40, "UNIT_ENEMY": 0x80, "UNIT_ALLY": 0x100,
    "CORPSE_ENEMY": 0x200, "UNIT_DEAD": 0x400, "GAMEOBJECT": 0x800, "TRADE_ITEM": 0x1000,
    "STRING": 0x2000, "GAMEOBJECT_ITEM": 0x4000, "CORPSE_ALLY": 0x8000, "UNIT_MINIPET": 0x10000,
    "GLYPH_SLOT": 0x20000, "DEST_TARGET": 0x40000, "EXTRA_TARGETS": 0x80000, "UNIT_PASSENGER": 0x100000,
}
TF = TARGET_FLAG
TARGET_FLAG_UNIT_MASK = (TF["UNIT"] | TF["UNIT_RAID"] | TF["UNIT_PARTY"] | TF["UNIT_ENEMY"] | TF["UNIT_ALLY"]
                         | TF["UNIT_DEAD"] | TF["UNIT_MINIPET"] | TF["UNIT_PASSENGER"])
TARGET_FLAG_GAMEOBJECT_MASK = TF["GAMEOBJECT"] | TF["GAMEOBJECT_ITEM"]
TARGET_FLAG_CORPSE_MASK = TF["CORPSE_ALLY"] | TF["CORPSE_ENEMY"]
TARGET_FLAG_ITEM_MASK = TF["TRADE_ITEM"] | TF["ITEM"] | TF["GAMEOBJECT_ITEM"]


def flag_names(mask: int) -> list[str]:
    return [n for n, v in TARGET_FLAG.items() if v and mask & v]


# -- enum Targets names (SharedDefines.h:2960); ids without a name are gaps ---------
TARGET_NAMES: dict[int, str] = {
    1: "TARGET_UNIT_CASTER", 2: "TARGET_UNIT_NEARBY_ENEMY", 3: "TARGET_UNIT_NEARBY_ALLY",
    4: "TARGET_UNIT_NEARBY_PARTY", 5: "TARGET_UNIT_PET", 6: "TARGET_UNIT_TARGET_ENEMY",
    7: "TARGET_UNIT_SRC_AREA_ENTRY", 8: "TARGET_UNIT_DEST_AREA_ENTRY", 9: "TARGET_DEST_HOME",
    11: "TARGET_UNIT_SRC_AREA_UNK_11", 15: "TARGET_UNIT_SRC_AREA_ENEMY", 16: "TARGET_UNIT_DEST_AREA_ENEMY",
    17: "TARGET_DEST_DB", 18: "TARGET_DEST_CASTER", 20: "TARGET_UNIT_CASTER_AREA_PARTY",
    21: "TARGET_UNIT_TARGET_ALLY", 22: "TARGET_SRC_CASTER", 23: "TARGET_GAMEOBJECT_TARGET",
    24: "TARGET_UNIT_CONE_ENEMY_24", 25: "TARGET_UNIT_TARGET_ANY", 26: "TARGET_GAMEOBJECT_ITEM_TARGET",
    27: "TARGET_UNIT_MASTER", 28: "TARGET_DEST_DYNOBJ_ENEMY", 29: "TARGET_DEST_DYNOBJ_ALLY",
    30: "TARGET_UNIT_SRC_AREA_ALLY", 31: "TARGET_UNIT_DEST_AREA_ALLY", 32: "TARGET_DEST_CASTER_SUMMON",
    33: "TARGET_UNIT_SRC_AREA_PARTY", 34: "TARGET_UNIT_DEST_AREA_PARTY", 35: "TARGET_UNIT_TARGET_PARTY",
    36: "TARGET_DEST_CASTER_UNK_36", 37: "TARGET_UNIT_LASTTARGET_AREA_PARTY", 38: "TARGET_UNIT_NEARBY_ENTRY",
    39: "TARGET_DEST_CASTER_FISHING", 40: "TARGET_GAMEOBJECT_NEARBY_ENTRY",
    41: "TARGET_DEST_CASTER_FRONT_RIGHT", 42: "TARGET_DEST_CASTER_BACK_RIGHT",
    43: "TARGET_DEST_CASTER_BACK_LEFT", 44: "TARGET_DEST_CASTER_FRONT_LEFT",
    45: "TARGET_UNIT_TARGET_CHAINHEAL_ALLY", 46: "TARGET_DEST_NEARBY_ENTRY", 47: "TARGET_DEST_CASTER_FRONT",
    48: "TARGET_DEST_CASTER_BACK", 49: "TARGET_DEST_CASTER_RIGHT", 50: "TARGET_DEST_CASTER_LEFT",
    51: "TARGET_GAMEOBJECT_SRC_AREA", 52: "TARGET_GAMEOBJECT_DEST_AREA", 53: "TARGET_DEST_TARGET_ENEMY",
    54: "TARGET_UNIT_CONE_180_DEG_ENEMY", 55: "TARGET_DEST_CASTER_FRONT_LEAP", 56: "TARGET_UNIT_CASTER_AREA_RAID",
    57: "TARGET_UNIT_TARGET_RAID", 58: "TARGET_UNIT_NEARBY_RAID", 59: "TARGET_UNIT_CONE_ALLY",
    60: "TARGET_UNIT_CONE_ENTRY", 61: "TARGET_UNIT_TARGET_AREA_RAID_CLASS", 62: "TARGET_DEST_CASTER_GROUND",
    63: "TARGET_DEST_TARGET_ANY", 64: "TARGET_DEST_TARGET_FRONT", 65: "TARGET_DEST_TARGET_BACK",
    66: "TARGET_DEST_TARGET_RIGHT", 67: "TARGET_DEST_TARGET_LEFT", 68: "TARGET_DEST_TARGET_FRONT_RIGHT",
    69: "TARGET_DEST_TARGET_BACK_RIGHT", 70: "TARGET_DEST_TARGET_BACK_LEFT", 71: "TARGET_DEST_TARGET_FRONT_LEFT",
    72: "TARGET_DEST_CASTER_RANDOM", 73: "TARGET_DEST_CASTER_RADIUS", 74: "TARGET_DEST_TARGET_RANDOM",
    75: "TARGET_DEST_TARGET_RADIUS", 76: "TARGET_DEST_CHANNEL_TARGET", 77: "TARGET_UNIT_CHANNEL_TARGET",
    78: "TARGET_DEST_DEST_FRONT", 79: "TARGET_DEST_DEST_BACK", 80: "TARGET_DEST_DEST_RIGHT",
    81: "TARGET_DEST_DEST_LEFT", 82: "TARGET_DEST_DEST_FRONT_RIGHT", 83: "TARGET_DEST_DEST_BACK_RIGHT",
    84: "TARGET_DEST_DEST_BACK_LEFT", 85: "TARGET_DEST_DEST_FRONT_LEFT", 86: "TARGET_DEST_DEST_RANDOM",
    87: "TARGET_DEST_DEST", 88: "TARGET_DEST_DYNOBJ_NONE", 89: "TARGET_DEST_TRAJ",
    90: "TARGET_UNIT_TARGET_MINIPET", 91: "TARGET_DEST_DEST_RADIUS", 92: "TARGET_UNIT_SUMMONER",
    93: "TARGET_CORPSE_SRC_AREA_ENEMY", 94: "TARGET_UNIT_VEHICLE", 95: "TARGET_UNIT_TARGET_PASSENGER",
    96: "TARGET_UNIT_PASSENGER_0", 97: "TARGET_UNIT_PASSENGER_1", 98: "TARGET_UNIT_PASSENGER_2",
    99: "TARGET_UNIT_PASSENGER_3", 100: "TARGET_UNIT_PASSENGER_4", 101: "TARGET_UNIT_PASSENGER_5",
    102: "TARGET_UNIT_PASSENGER_6", 103: "TARGET_UNIT_PASSENGER_7",
    104: "TARGET_UNIT_CONE_CASTER_TO_DEST_ENEMY", 105: "TARGET_UNIT_CASTER_AND_PASSENGERS",
    106: "TARGET_DEST_NEARBY_DB", 107: "TARGET_DEST_NEARBY_ENTRY_2",
    108: "TARGET_GAMEOBJECT_CONE_CASTER_TO_DEST_ENEMY", 109: "TARGET_GAMEOBJECT_CONE_CASTER_TO_DEST_ALLY",
    110: "TARGET_UNIT_CONE_CASTER_TO_DEST_ENTRY", 111: "TARGET_UNK_111", 112: "TARGET_UNK_112",
    113: "TARGET_UNK_113", 114: "TARGET_UNK_114", 115: "TARGET_UNIT_SRC_AREA_FURTHEST_ENEMY",
    116: "TARGET_UNIT_AND_DEST_LAST_ENEMY", 117: "TARGET_UNK_117", 118: "TARGET_UNIT_TARGET_ALLY_OR_RAID",
    119: "TARGET_CORPSE_SRC_AREA_RAID", 120: "TARGET_UNIT_CASTER_AND_SUMMONS", 121: "TARGET_CORPSE_TARGET_ALLY",
    122: "TARGET_UNIT_AREA_THREAT_LIST", 123: "TARGET_UNIT_AREA_TAP_LIST", 124: "TARGET_UNIT_TARGET_TAP_LIST",
    125: "TARGET_DEST_CASTER_GROUND_2", 126: "TARGET_UNIT_CASTER_AREA_ENEMY_CLUMP",
    127: "TARGET_DEST_CASTER_ENEMY_CLUMP_CENTROID", 128: "TARGET_UNIT_RECT_CASTER_ALLY",
    129: "TARGET_UNIT_RECT_CASTER_ENEMY", 130: "TARGET_UNIT_RECT_CASTER", 131: "TARGET_DEST_SUMMONER",
    132: "TARGET_DEST_TARGET_ALLY", 133: "TARGET_UNIT_LINE_CASTER_TO_DEST_ALLY",
    134: "TARGET_UNIT_LINE_CASTER_TO_DEST_ENEMY", 135: "TARGET_UNIT_LINE_CASTER_TO_DEST",
    136: "TARGET_UNIT_CONE_CASTER_TO_DEST_ALLY", 137: "TARGET_DEST_CASTER_MOVEMENT_DIRECTION",
    138: "TARGET_DEST_DEST_GROUND", 139: "TARGET_UNK_139", 140: "TARGET_DEST_CASTER_CLUMP_CENTROID",
    141: "TARGET_UNK_141", 142: "TARGET_DEST_NEARBY_ENTRY_OR_DB", 143: "TARGET_UNK_143", 144: "TARGET_UNK_144",
    145: "TARGET_UNK_145", 146: "TARGET_UNK_146", 147: "TARGET_UNK_147",
    148: "TARGET_DEST_DEST_TARGET_TOWARDS_CASTER", 149: "TARGET_UNK_149", 150: "TARGET_UNIT_OWN_CRITTER",
    151: "TARGET_UNK_151", 152: "TARGET_UNK_152",
}
_N = {v: k for k, v in TARGET_NAMES.items()}


def tid(name: str) -> int:
    return _N[name]


# -- SpellImplicitTargetInfo::_data (SpellInfo.cpp:246-401) --------------------------
#     (object, reference, category, check, direction), index = raw id
_DATA: tuple[tuple[str, str, str, str, str], ...] = (
    ("NONE", "NONE", "NYI", "DEFAULT", "NONE"),                 # 0
    ("UNIT", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 1
    ("UNIT", "CASTER", "NEARBY", "ENEMY", "NONE"),              # 2
    ("UNIT", "CASTER", "NEARBY", "ALLY", "NONE"),               # 3
    ("UNIT", "CASTER", "NEARBY", "PARTY", "NONE"),              # 4
    ("UNIT", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 5
    ("UNIT", "TARGET", "DEFAULT", "ENEMY", "NONE"),             # 6
    ("UNIT", "SRC", "AREA", "ENTRY", "NONE"),                   # 7
    ("UNIT", "DEST", "AREA", "ENTRY", "NONE"),                  # 8
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 9
    ("NONE", "NONE", "NYI", "DEFAULT", "NONE"),                 # 10
    ("UNIT", "SRC", "NYI", "DEFAULT", "NONE"),                  # 11
    ("NONE", "NONE", "NYI", "DEFAULT", "NONE"),                 # 12
    ("NONE", "NONE", "NYI", "DEFAULT", "NONE"),                 # 13
    ("NONE", "NONE", "NYI", "DEFAULT", "NONE"),                 # 14
    ("UNIT", "SRC", "AREA", "ENEMY", "NONE"),                   # 15
    ("UNIT", "DEST", "AREA", "ENEMY", "NONE"),                  # 16
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 17
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 18
    ("NONE", "NONE", "NYI", "DEFAULT", "NONE"),                 # 19
    ("UNIT", "CASTER", "AREA", "PARTY", "NONE"),                # 20
    ("UNIT", "TARGET", "DEFAULT", "ALLY", "NONE"),              # 21
    ("SRC", "CASTER", "DEFAULT", "DEFAULT", "NONE"),            # 22
    ("GOBJ", "TARGET", "DEFAULT", "DEFAULT", "NONE"),           # 23
    ("UNIT", "CASTER", "CONE", "ENEMY", "FRONT"),               # 24
    ("UNIT", "TARGET", "DEFAULT", "DEFAULT", "NONE"),           # 25
    ("GOBJ_ITEM", "TARGET", "DEFAULT", "DEFAULT", "NONE"),      # 26
    ("UNIT", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 27
    ("DEST", "DEST", "DEFAULT", "ENEMY", "NONE"),               # 28
    ("DEST", "DEST", "DEFAULT", "ALLY", "NONE"),                # 29
    ("UNIT", "SRC", "AREA", "ALLY", "NONE"),                    # 30
    ("UNIT", "DEST", "AREA", "ALLY", "NONE"),                   # 31
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "FRONT_LEFT"),     # 32
    ("UNIT", "SRC", "AREA", "PARTY", "NONE"),                   # 33
    ("UNIT", "DEST", "AREA", "PARTY", "NONE"),                  # 34
    ("UNIT", "TARGET", "DEFAULT", "PARTY", "NONE"),             # 35
    ("DEST", "CASTER", "NYI", "DEFAULT", "NONE"),               # 36
    ("UNIT", "LAST", "AREA", "PARTY", "NONE"),                  # 37
    ("UNIT", "CASTER", "NEARBY", "ENTRY", "NONE"),              # 38
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 39
    ("GOBJ", "CASTER", "NEARBY", "ENTRY", "NONE"),              # 40
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "FRONT_RIGHT"),    # 41
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "BACK_RIGHT"),     # 42
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "BACK_LEFT"),      # 43
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "FRONT_LEFT"),     # 44
    ("UNIT", "TARGET", "DEFAULT", "ALLY", "NONE"),              # 45
    ("DEST", "CASTER", "NEARBY", "ENTRY", "NONE"),              # 46
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "FRONT"),          # 47
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "BACK"),           # 48
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "RIGHT"),          # 49
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "LEFT"),           # 50
    ("GOBJ", "SRC", "AREA", "DEFAULT", "NONE"),                 # 51
    ("GOBJ", "DEST", "AREA", "DEFAULT", "NONE"),                # 52
    ("DEST", "TARGET", "DEFAULT", "ENEMY", "NONE"),             # 53
    ("UNIT", "CASTER", "CONE", "ENEMY", "FRONT"),               # 54
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 55
    ("UNIT", "CASTER", "AREA", "RAID", "NONE"),                 # 56
    ("UNIT", "TARGET", "DEFAULT", "RAID", "NONE"),              # 57
    ("UNIT", "CASTER", "NEARBY", "RAID", "NONE"),               # 58
    ("UNIT", "CASTER", "CONE", "ALLY", "FRONT"),                # 59
    ("UNIT", "CASTER", "CONE", "ENTRY", "FRONT"),               # 60
    ("UNIT", "TARGET", "AREA", "RAID_CLASS", "NONE"),           # 61
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 62
    ("DEST", "TARGET", "DEFAULT", "DEFAULT", "NONE"),           # 63
    ("DEST", "TARGET", "DEFAULT", "DEFAULT", "FRONT"),          # 64
    ("DEST", "TARGET", "DEFAULT", "DEFAULT", "BACK"),           # 65
    ("DEST", "TARGET", "DEFAULT", "DEFAULT", "RIGHT"),          # 66
    ("DEST", "TARGET", "DEFAULT", "DEFAULT", "LEFT"),           # 67
    ("DEST", "TARGET", "DEFAULT", "DEFAULT", "FRONT_RIGHT"),    # 68
    ("DEST", "TARGET", "DEFAULT", "DEFAULT", "BACK_RIGHT"),     # 69
    ("DEST", "TARGET", "DEFAULT", "DEFAULT", "BACK_LEFT"),      # 70
    ("DEST", "TARGET", "DEFAULT", "DEFAULT", "FRONT_LEFT"),     # 71
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "RANDOM"),         # 72
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "RANDOM"),         # 73
    ("DEST", "TARGET", "DEFAULT", "DEFAULT", "RANDOM"),         # 74
    ("DEST", "TARGET", "DEFAULT", "DEFAULT", "RANDOM"),         # 75
    ("DEST", "CASTER", "CHANNEL", "DEFAULT", "NONE"),           # 76
    ("UNIT", "CASTER", "CHANNEL", "DEFAULT", "NONE"),           # 77
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "FRONT"),            # 78
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "BACK"),             # 79
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "RIGHT"),            # 80
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "LEFT"),             # 81
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "FRONT_RIGHT"),      # 82
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "BACK_RIGHT"),       # 83
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "BACK_LEFT"),        # 84
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "FRONT_LEFT"),       # 85
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "RANDOM"),           # 86
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "NONE"),             # 87
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "NONE"),             # 88
    ("DEST", "DEST", "TRAJ", "DEFAULT", "NONE"),                # 89
    ("UNIT", "TARGET", "DEFAULT", "DEFAULT", "NONE"),           # 90
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "RANDOM"),           # 91
    ("UNIT", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 92
    ("CORPSE", "SRC", "AREA", "ENEMY", "NONE"),                 # 93
    ("UNIT", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 94
    ("UNIT", "TARGET", "DEFAULT", "PASSENGER", "NONE"),         # 95
    *((("UNIT", "CASTER", "DEFAULT", "DEFAULT", "NONE"),) * 8),  # 96-103
    ("UNIT", "CASTER", "CONE", "ENEMY", "FRONT"),               # 104
    ("UNIT", "CASTER", "AREA", "DEFAULT", "NONE"),              # 105
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 106
    ("DEST", "CASTER", "NEARBY", "ENTRY", "NONE"),              # 107
    ("GOBJ", "CASTER", "CONE", "ENEMY", "FRONT"),               # 108
    ("GOBJ", "CASTER", "CONE", "ALLY", "FRONT"),                # 109
    ("UNIT", "CASTER", "CONE", "ENTRY", "FRONT"),               # 110
    ("NONE", "NONE", "NYI", "DEFAULT", "NONE"),                 # 111
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 112
    ("DEST", "TARGET", "DEFAULT", "DEFAULT", "NONE"),           # 113
    ("NONE", "NONE", "NYI", "DEFAULT", "NONE"),                 # 114
    ("UNIT", "SRC", "AREA", "ENEMY", "NONE"),                   # 115
    ("UNIT_AND_DEST", "LAST", "AREA", "ENEMY", "NONE"),         # 116
    ("NONE", "NONE", "NYI", "DEFAULT", "NONE"),                 # 117
    ("UNIT", "TARGET", "AREA", "RAID", "NONE"),                 # 118
    ("CORPSE", "CASTER", "AREA", "RAID", "NONE"),               # 119
    ("UNIT", "CASTER", "AREA", "SUMMONED", "NONE"),             # 120
    ("CORPSE", "TARGET", "DEFAULT", "ALLY", "NONE"),            # 121
    ("UNIT", "CASTER", "AREA", "DEFAULT", "NONE"),              # 122
    ("UNIT", "CASTER", "AREA", "DEFAULT", "NONE"),              # 123
    ("UNIT", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 124
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 125
    ("UNIT", "NONE", "NYI", "DEFAULT", "NONE"),                 # 126
    ("DEST", "NONE", "NYI", "DEFAULT", "NONE"),                 # 127
    ("UNIT", "CASTER", "CONE", "ALLY", "FRONT"),                # 128
    ("UNIT", "CASTER", "CONE", "ENEMY", "FRONT"),               # 129
    ("UNIT", "CASTER", "CONE", "DEFAULT", "FRONT"),             # 130
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 131
    ("DEST", "TARGET", "DEFAULT", "ALLY", "NONE"),              # 132
    ("UNIT", "DEST", "LINE", "ALLY", "NONE"),                   # 133
    ("UNIT", "DEST", "LINE", "ENEMY", "NONE"),                  # 134
    ("UNIT", "DEST", "LINE", "DEFAULT", "NONE"),                # 135
    ("UNIT", "CASTER", "CONE", "ALLY", "FRONT"),                # 136
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 137
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "NONE"),             # 138
    ("NONE", "NONE", "NYI", "DEFAULT", "NONE"),                 # 139
    ("DEST", "NONE", "NYI", "DEFAULT", "NONE"),                 # 140
    ("NONE", "NONE", "NYI", "DEFAULT", "NONE"),                 # 141
    ("DEST", "CASTER", "NEARBY", "ENTRY", "FRONT_RIGHT"),       # 142
    *((("NONE", "NONE", "NYI", "DEFAULT", "NONE"),) * 5),       # 143-147
    ("DEST", "DEST", "DEFAULT", "DEFAULT", "NONE"),             # 148
    ("DEST", "CASTER", "DEFAULT", "DEFAULT", "RANDOM"),         # 149
    ("UNIT", "CASTER", "DEFAULT", "DEFAULT", "NONE"),           # 150
    ("UNIT", "CASTER", "AREA", "ENEMY", "NONE"),                # 151
    ("NONE", "NONE", "NYI", "DEFAULT", "NONE"),                 # 152
)
assert len(_DATA) == TOTAL_SPELL_TARGETS

# -- SpellEffectInfo::_data (SpellInfo.cpp:959-1318) ---------------------------------
# two chars per effect id: <ImplicitTargetType digit><UsedTargetObjectType hex digit>
_EFFECT_DATA = (
    "0013130000171313131313130014131413131a130303030313030312121403130016031313031303"
    "13130214131303030303120313171713121313130300131300131313131413131213031312000303"
    "13131314030015151515131313030313130313171317131312131213131203131313131319130313"
    "13130013131313171313130313130312131313131313131314141313001213000013130317131713"
    "1313131713132313132313021a001300131300020023000013000013121313132313000202020213"
    "00001313131317131300131313021313131300021313131713131313000013131300020013131313"
    "13001317130000000013131314130213000017170017001717131723131713131317000013130013"
    "00131313131313001313131313131317171713000017101313001300000000130017002313130000"
    "000000000000000000000000000000131313130000130000000000000000000000020000"
)
assert len(_EFFECT_DATA) == 2 * TOTAL_SPELL_EFFECTS


def effect_implicit_target_type(effect: int) -> int:
    """Mirrors: SpellInfo.cpp:859 ``SpellEffectInfo::GetImplicitTargetType``."""
    if not 0 <= effect < TOTAL_SPELL_EFFECTS:
        raise FailClosed(f"effect {effect} outside SpellEffectInfo::_data (TOTAL_SPELL_EFFECTS={TOTAL_SPELL_EFFECTS})")
    return int(_EFFECT_DATA[2 * effect])


def effect_used_object(effect: int) -> int:
    """Mirrors: SpellInfo.cpp:864 ``SpellEffectInfo::GetUsedTargetObjectType``."""
    if not 0 <= effect < TOTAL_SPELL_EFFECTS:
        raise FailClosed(f"effect {effect} outside SpellEffectInfo::_data (TOTAL_SPELL_EFFECTS={TOTAL_SPELL_EFFECTS})")
    return int(_EFFECT_DATA[2 * effect + 1], 16)


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def f32_hex(x: float) -> str:
    return "0x%08X" % struct.unpack("<I", struct.pack("<f", x))[0]


_M_PI = 3.14159265358979323846
_DIR_ANGLE = {  # SpellInfo.cpp:108-133, static_cast<float>(double expr)
    "FRONT": 0.0, "BACK": f32(_M_PI), "RIGHT": f32(-_M_PI / 2), "LEFT": f32(_M_PI / 2),
    "FRONT_RIGHT": f32(-_M_PI / 4), "BACK_RIGHT": f32(-3 * _M_PI / 4), "BACK_LEFT": f32(3 * _M_PI / 4),
    "FRONT_LEFT": f32(_M_PI / 4),
}


def target_flag_mask(obj: int | str) -> int:
    """Mirrors: SpellInfo.cpp:44 ``GetTargetFlagMask``."""
    o = OBJECT[obj] if isinstance(obj, int) else obj
    return {"DEST": TF["DEST_LOCATION"], "UNIT_AND_DEST": TF["DEST_LOCATION"] | TF["UNIT"],
            "CORPSE_ALLY": TF["CORPSE_ALLY"], "CORPSE_ENEMY": TF["CORPSE_ENEMY"],
            "CORPSE": TF["CORPSE_ALLY"] | TF["CORPSE_ENEMY"], "UNIT": TF["UNIT"], "GOBJ": TF["GAMEOBJECT"],
            "GOBJ_ITEM": TF["GAMEOBJECT_ITEM"], "ITEM": TF["ITEM"], "SRC": TF["SOURCE_LOCATION"]}.get(o, 0)


@dataclass(frozen=True)
class SelectorInfo:
    id: int
    name: str | None
    object: str
    reference: str
    category: str
    check: str
    direction: str

    @property
    def is_area(self) -> bool:
        """Mirrors: SpellInfo.cpp:78 ``IsArea`` (AREA or CONE; not LINE/TRAJ/NEARBY)."""
        return self.category in ("AREA", "CONE")

    def direction_angle(self, rand_norm: float | None = None) -> float:
        """binary32 result of ``CalcDirectionAngle``.

        Mirrors: SpellInfo.cpp:108.  ``RANDOM`` consumes one ``rand_norm()`` draw
        (``rand_norm() * float(2*M_PI)`` in float arithmetic); without a draw it fails closed.
        ``NONE``/``ENTRY`` fall to ``default: 0.0f``.
        """
        if self.direction == "RANDOM":
            if rand_norm is None:
                raise FailClosed(f"selector {self.id}: TARGET_DIR_RANDOM needs a rand_norm() draw")
            return f32(f32(rand_norm) * f32(2 * _M_PI))
        return _DIR_ANGLE.get(self.direction, 0.0)

    def explicit_target_mask(self, src_set: bool, dst_set: bool) -> tuple[int, bool, bool]:
        """(mask, src_set', dst_set').  Mirrors: SpellInfo.cpp:140 ``GetExplicitTargetMask``."""
        mask = 0
        if self.id == 89:  # TARGET_DEST_TRAJ
            if not src_set:
                mask = TF["SOURCE_LOCATION"]
            if not dst_set:
                mask |= TF["DEST_LOCATION"]
        elif self.reference == "SRC":
            if not src_set:
                mask = TF["SOURCE_LOCATION"]
        elif self.reference == "DEST":
            if not dst_set:
                mask = TF["DEST_LOCATION"]
        elif self.reference == "TARGET":
            if self.object == "GOBJ":
                mask = TF["GAMEOBJECT"]
            elif self.object == "GOBJ_ITEM":
                mask = TF["GAMEOBJECT_ITEM"]
            elif self.object in ("UNIT_AND_DEST", "UNIT", "DEST"):
                mask = {"ENEMY": TF["UNIT_ENEMY"], "ALLY": TF["UNIT_ALLY"], "PARTY": TF["UNIT_PARTY"],
                        "RAID": TF["UNIT_RAID"], "PASSENGER": TF["UNIT_PASSENGER"]}.get(self.check, TF["UNIT"])
        if self.object == "SRC":
            src_set = True
        elif self.object in ("DEST", "UNIT_AND_DEST"):
            dst_set = True
        return mask, src_set, dst_set

    @property
    def target_flag_mask(self) -> int:
        return target_flag_mask(self.object)

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "object": self.object, "reference": self.reference,
                "category": self.category, "check": self.check, "direction": self.direction}


@lru_cache(maxsize=None)
def info(target_id: int) -> SelectorInfo:
    """Mirrors: SpellInfo.cpp:83-106 (``_data[_target]`` getters).

    Ids outside ``0..TOTAL_SPELL_TARGETS-1`` would index ``_data`` out of bounds in
    Trinity (``Targets(target)`` is unchecked at SpellInfo.cpp:75): fail closed.
    """
    if not isinstance(target_id, int) or not 0 <= target_id < TOTAL_SPELL_TARGETS:
        raise FailClosed(f"implicit target {target_id!r} outside SpellImplicitTargetInfo::_data "
                         f"(TOTAL_SPELL_TARGETS={TOTAL_SPELL_TARGETS}); Trinity reads out of bounds")
    o, r, c, k, d = _DATA[target_id]
    return SelectorInfo(target_id, TARGET_NAMES.get(target_id), o, r, c, k, d)


# -- consumer routing -----------------------------------------------------------------
S = "Spell.cpp"

# Per-selector branches inside the SelectImplicit* routines (verified anchors; see
# tests/test_tg_a_probe.py::test_consumer_anchors).
CASTER_OBJECT_BRANCH = {  # Spell.cpp:1747-1792 SelectImplicitCasterObjectTargets
    1: ("m_caster, checkIfValid=false", f"{S}:1749"),
    27: ("GetCharmerOrOwner()", f"{S}:1753"),
    5: ("GetGuardianPet()", f"{S}:1756"),
    92: ("summoner unit if caster IsSummon()", f"{S}:1760"),
    94: ("GetVehicleBase()", f"{S}:1765"),
    **{t: (f"vehicle-kit passenger seat {t - 96} (caster must be a vehicle Creature)", f"{S}:1769") for t in range(96, 104)},
    124: ("random tap-list member (SelectRandomContainerElement, 1 draw) of creature caster", f"{S}:1781"),
    150: ("GetCritterGUID() critter", f"{S}:1786"),
}
CASTER_DEST_BRANCH = {  # Spell.cpp:1469-1644 SelectImplicitCasterDestTargets
    18: ("caster position", f"{S}:1471"),
    9: ("player homebind (non-player: caster position)", f"{S}:1473"),
    17: ("spell_target_position row (teleport/bind effects keep map; others only same-map); "
         "no row -> explicit object target position, else caster", f"{S}:1477"),
    39: ("fishing: frand range + rand_norm angle, liquid checks can finish the spell", f"{S}:1493"),
    55: ("MovePosition(radius.Max, 0) from caster (leap)", f"{S}:1526"),
    137: ("MovePosition(radius.Max, movement-flag angle)", f"{S}:1527"),
    62: ("caster x/y at map water-or-ground Z", f"{S}:1577"),
    125: ("caster x/y at map water-or-ground Z", f"{S}:1578"),
    131: ("TempSummon summoner position (else caster)", f"{S}:1581"),
    106: ("random spell_target_position within radius Min..Max (SelectRandomContainerElement); none -> "
          "SPELL_FAILED_NO_VALID_TARGETS", f"{S}:1587"),
}
CASTER_DEST_DEFAULT_SPECIAL = {  # inside default: Spell.cpp:1612-1633
    32: "dist = PET_FOLLOW_DIST",
    72: "dist = objSize + (dist - objSize) (arithmetic no-op; randomness only from CalcRadius sqrt(rand_norm))",
    41: "no radius on this target index -> dist = 3.0 (DefaultTotemDistance)",
    42: "no radius on this target index -> dist = 3.0 (DefaultTotemDistance)",
    43: "no radius on this target index -> dist = 3.0 (DefaultTotemDistance)",
    44: "no radius on this target index -> dist = 3.0 (DefaultTotemDistance)",
}
TARGET_DEST_NO_OFFSET = {53, 63, 132}          # Spell.cpp:1666-1669
DEST_DEST_BRANCH = {                            # Spell.cpp:1700-1733
    28: "no-op (keep dst)", 29: "no-op (keep dst)", 88: "no-op (keep dst)", 87: "no-op (keep dst)",
    138: "dst Z = GetMapHeight", 148: "move radius.Max from dst towards caster; orientation caster->dst",
}
AREA_BRANCH = {                                  # Spell.cpp:1383-1424, 1439-1451
    105: "caster + every vehicle-kit passenger; no radius search, no check", 118: (
        "explicit unit target: if caster not unit or not IsInRaidWith(target) -> only the target (unchecked push); "
        "else area search centred on the target with referer=target (RAID check uses target's raid)"),
    120: "caster pushed first, then area search (SUMMONED check: summoner == caster)",
    122: "caster's unsorted threat list; no radius, no relation check",
    123: "creature caster's tap list players; no radius, no relation check",
    115: "area search, sort by distance descending from referer, cap = truncate (no RandomResize)",
}


def handler(target_id: int) -> dict:
    """Which ``Spell::SelectImplicit*`` routine and branch handles the selector.

    Mirrors: Spell.cpp:945 ``SelectEffectImplicitTargets`` dispatch plus the per-routine
    reference/check/object guards (ABORT_MSG paths are reported as ``abort``).
    """
    s = info(target_id)
    out: dict = {"function": None, "consumer": None, "branch": None, "abort": None, "writes": [],
                 "adds": [], "uses_effect_mask": False, "chain": False, "notes": []}
    if target_id == 0:
        out.update(function=None, consumer=f"{S}:947", branch="GetTarget()==0 -> return (unused slot)")
        return out
    cat = s.category
    if cat == "NYI":
        out.update(function="(none)", consumer=f"{S}:1020", branch="TARGET_SELECT_CATEGORY_NYI -> debug log only")
        out["notes"].append("no recipients, no m_targets write")
        return out
    if cat == "CHANNEL":
        out.update(function="SelectImplicitChannelTargets", consumer=f"{S}:1029", uses_effect_mask=True)
        if s.reference != "CASTER":
            out["abort"] = f"{S}:1033"
        elif target_id == 77:
            out.update(branch="each m_originalCaster ChannelObjects unit -> AddUnitTarget(checkIfValid=true, implicit=true)",
                       adds=["unit"])
        elif target_id == 76:
            out.update(branch="channeled spell dst, else first ChannelObject position", writes=["dst:set"])
        else:
            out["abort"] = f"{S}:1084"
        out["notes"].append("no current channeled spell on m_originalCaster -> nothing (Spell.cpp:1038)")
        return out
    if cat == "NEARBY":
        out.update(function="SelectImplicitNearbyTargets", consumer=f"{S}:1089", uses_effect_mask=True, chain=True)
        if s.reference != "CASTER":
            out["abort"] = f"{S}:1093"
            return out
        if s.check not in ("ENEMY", "ALLY", "PARTY", "RAID", "RAID_CLASS", "ENTRY", "DEFAULT"):
            out["abort"] = f"{S}:1114"
        out["branch"] = {"ENEMY": "range = GetMaxRange(false)", "ENTRY": "range = GetMaxRange(IsPositive())",
                         "DEFAULT": "range = GetMaxRange(IsPositive())"}.get(s.check, "range = GetMaxRange(true)")
        if s.check == "ENTRY":
            out["notes"].append("no ImplicitTargetConditions -> emergency path (Spell.cpp:1121): GOBJ/DEST use spell focus "
                                "if RequiresSpellFocus; TARGET_DEST_NEARBY_ENTRY_OR_DB tries spell_target_position")
        if target_id == 142:
            out["notes"].append("no target found -> caster with random radius offset (Spell.cpp:1187-1194)")
        out["notes"].append("closest candidate strictly inside range (WorldObjectSpellNearbyTargetCheck, Spell.cpp:9402); "
                            "none -> SPELL_FAILED_BAD_IMPLICIT_TARGETS finishes the spell (Spell.cpp:1200-1205)")
        if s.object == "UNIT":
            out["adds"] = ["unit"]
            out["notes"].append("AddUnitTarget(checkIfValid=true, implicit=false) (Spell.cpp:1221)")
        elif s.object == "GOBJ":
            out["adds"] = ["gameobject"]
        elif s.object == "CORPSE":
            out["adds"] = ["corpse"]
        elif s.object == "DEST":
            out["writes"] = ["dst:set"]
        else:
            out["abort"] = f"{S}:1266"
        return out
    if cat == "CONE":
        out.update(function="SelectImplicitConeTargets", consumer=f"{S}:1273", uses_effect_mask=True,
                   adds=["unit", "gameobject", "corpse"])
        if s.reference != "CASTER":
            out["abort"] = f"{S}:1277"
            return out
        out["branch"] = ("ConeAngle==0 -> 180 (dead after LoadSpellInfoCorrections sets 0 -> 90, SpellMgr.cpp:5281-5283)"
                         if target_id == 54 else "cone from caster position/orientation (coneSrc=*m_caster)")
        out["notes"].append("cap: RandomResize(MaxAffectedTargets) (Spell.cpp:1310-1311); AddUnitTarget(checkIfValid=false)")
        out["notes"].append("direction axis unused; CASTER_TO_DEST names do not orient the cone to dst")
        return out
    if cat == "AREA":
        out.update(function="SelectImplicitAreaTargets", consumer=f"{S}:1326", uses_effect_mask=True,
                   adds=["unit", "gameobject", "corpse"])
        if s.reference not in ("SRC", "DEST", "CASTER", "TARGET", "LAST"):
            out["abort"] = f"{S}:1355"
            return out
        centre = {"SRC": "m_targets src position", "DEST": "m_targets dst position (read even if !HasDst)",
                  "CASTER": "caster", "TARGET": "explicit unit target (none -> no targets)",
                  "LAST": "last m_UniqueTargetInfo entry carrying this effect's bit, else caster"}[s.reference]
        out["branch"] = AREA_BRANCH.get(target_id, f"SearchAreaTargets centred on {centre}; RandomResize cap")
        out["centre"] = centre
        if s.object == "UNIT_AND_DEST":
            out["writes"] = ["dst:mod (referer position; ModDst ASSERTs HasDst)"]
        out["notes"].append("AddUnitTarget(checkIfValid=false, implicit=true, losPosition=centre) (Spell.cpp:1456)")
        return out
    if cat == "TRAJ":
        out.update(function="SelectImplicitTrajTargets", consumer=f"{S}:1878",
                   branch="CheckDst(); if HasTraj: clip dst at first collision along the arc", writes=["dst:check", "dst:mod"])
        out["notes"].append("adds no recipients; effect TriggerSpell range read (Spell.cpp:1908)")
        return out
    if cat == "LINE":
        out.update(function="SelectImplicitLineTargets", consumer=f"{S}:1964", uses_effect_mask=True,
                   adds=["unit", "gameobject", "corpse"])
        if s.reference not in ("SRC", "DEST", "CASTER", "TARGET"):
            out["abort"] = f"{S}:1985"
            return out
        out["branch"] = (f"line from caster oriented towards {s.reference.lower()} position; cap = sort nearest to caster + "
                         "truncate when MaxAffectedTargets < count")
        return out
    # DEFAULT
    if s.object == "SRC":
        out.update(function="(inline)", consumer=f"{S}:976")
        if s.reference == "CASTER":
            out.update(branch="m_targets.SetSrc(*m_caster)", writes=["src:set"])
        else:
            out["abort"] = f"{S}:983"
        return out
    if s.object == "DEST":
        if s.reference == "CASTER":
            out.update(function="SelectImplicitCasterDestTargets", consumer=f"{S}:1465", writes=["dst:set"])
            if target_id in CASTER_DEST_BRANCH:
                out["branch"], out["branch_consumer"] = CASTER_DEST_BRANCH[target_id]
            else:
                out["branch"] = "default: MovePosition(caster, max(radius.Max, combat reach), CalcDirectionAngle())"
                out["branch_consumer"] = f"{S}:1606"
                if target_id in CASTER_DEST_DEFAULT_SPECIAL:
                    out["notes"].append(CASTER_DEST_DEFAULT_SPECIAL[target_id])
        elif s.reference == "TARGET":
            out.update(function="SelectImplicitTargetDestTargets", consumer=f"{S}:1653", writes=["dst:set"])
            out["branch"] = ("explicit object target position" if target_id in TARGET_DEST_NO_OFFSET else
                             "MovePosition(target, CalcRadius(nullptr).Max, CalcDirectionAngle())")
            out["notes"].append("no object target: ASSERT unless DontFailSpellOnTargetingFailure, then nothing (Spell.cpp:1655-1660)")
        elif s.reference == "DEST":
            out.update(function="SelectImplicitDestDestTargets", consumer=f"{S}:1690", writes=["dst:check", "dst:mod"])
            out["branch"] = DEST_DEST_BRANCH.get(target_id, "MovePosition(dst, radius.Max, CalcDirectionAngle())")
        else:
            out.update(function="(none)", consumer=f"{S}:987", abort=f"{S}:1000")
        return out
    if s.reference == "CASTER":
        out.update(function="SelectImplicitCasterObjectTargets", consumer=f"{S}:1742", uses_effect_mask=True,
                   adds=["unit", "gameobject", "corpse"])
        if target_id in CASTER_OBJECT_BRANCH:
            out["branch"], out["branch_consumer"] = CASTER_OBJECT_BRANCH[target_id]
        else:
            out["branch"] = "no switch case -> target stays nullptr -> nothing added (unless a script sets one)"
            out["branch_consumer"] = f"{S}:1790"
        return out
    if s.reference == "TARGET":
        out.update(function="SelectImplicitTargetObjectTargets", consumer=f"{S}:1807", uses_effect_mask=True,
                   chain=True, adds=["unit", "gameobject", "corpse", "item"],
                   branch="explicit object target (AddUnitTarget checkIfValid=true, implicit=false), then chain; "
                          "else explicit item target")
        if target_id == 45:
            out["notes"].append("chain uses isChainHeal=true: 12.5y jump, highest HP deficit (Spell.cpp:1848, 2239, 2283)")
        out["notes"].append("no object/item target: ASSERT unless DontFailSpellOnTargetingFailure (Spell.cpp:1809)")
        return out
    out.update(function="(none)", consumer=f"{S}:1005", abort=f"{S}:1014")
    return out


def effect_dependence(target_id: int) -> list[str]:
    """Places where the handler's behaviour reads the effect row itself (beyond radius/conditions)."""
    notes = []
    if target_id == 17:
        notes.append("Spell.cpp:1481 TELEPORT_UNITS / TELEPORT_WITH_SPELL_VISUAL_KIT_LOADING_SCREEN / BIND keep the "
                     "spell_target_position map; other effects only use a same-map row")
    if target_id == 89:
        notes.append("Spell.cpp:1908 effect TriggerSpell max range bounds the trajectory")
    if info(target_id).category in ("NEARBY", "DEFAULT") and handler(target_id).get("chain"):
        notes.append("Spell.cpp:1834 effect ChainTargets (+SpellModOp::ChainTargets) -> SelectImplicitChainTargets")
    if target_id in (106, 142):
        notes.append("spell_target_position rows are keyed by (spell, effect index)")
    return notes


GLOBAL_EFFECT_DEPENDENCE = [
    "Spell.cpp:797 SelectEffectTypeImplicitTargets(effect) runs for every IsEffect effect after A/B: "
    "EffectImplicitTargetTypes (SpellEffectInfo::_data) adds the explicit unit/corpse/GO/item or the caster when "
    "GetMissingTargetMask() (SpellInfo.cpp:835) is non-zero, i.e. when neither selector provides the effect's used object type",
    "Spell.cpp:2030-2061 SUMMON_RAF_FRIEND / SUMMON_PLAYER bypass the target map entirely (player selection)",
    "Spell.cpp:2445-2447 AddUnitTarget clears effect bits whose CheckEffectTarget fails (aura possess/charm guards, "
    "SKIN_PLAYER_CORPSE, LOS) -- per effect of the whole spell, not only the grouped mask",
    "Spell.cpp:8262 CheckEffectTarget(GameObject): GAMEOBJECT_DAMAGE/REPAIR/SET_DESTRUCTION_STATE need a destructible building "
    "(AddGOTarget)",
    "SpellMgr.cpp:5286-5291 area-aura effects whose A or B IsArea are rewritten to (TARGET_UNIT_CASTER, 0) at load",
]


def catalog() -> list[dict]:
    rows = []
    for t in range(TOTAL_SPELL_TARGETS):
        s = info(t)
        row = s.to_json()
        row["is_area"] = s.is_area
        row["target_flag_mask"] = s.target_flag_mask
        row["target_flag_names"] = flag_names(s.target_flag_mask)
        if s.direction == "RANDOM":
            row["direction_angle"] = {"random": True, "formula": "f32(rand_norm()) * f32(2*M_PI)",
                                      "at_rand_norm_0.5": f32_hex(s.direction_angle(0.5))}
        else:
            row["direction_angle"] = {"random": False, "binary32": f32_hex(s.direction_angle()),
                                      "value": s.direction_angle()}
        row["explicit_target_mask"] = [
            {"src_in": si, "dst_in": di, "mask": m, "mask_names": flag_names(m), "src_out": so, "dst_out": do}
            for si in (False, True) for di in (False, True)
            for (m, so, do) in [s.explicit_target_mask(si, di)]]
        row["handler"] = handler(t)
        row["effect_dependence"] = effect_dependence(t)
        row["evidence"] = "differential"
        row["consumer"] = "SpellInfo.cpp:246"
        rows.append(row)
    return rows


def understood(target_id: int) -> tuple[str, list[str]]:
    """Closure verdict of the selector path alone (not the full recipient policy)."""
    h = handler(target_id)
    s = info(target_id)
    tags = []
    if target_id == 0:
        return "n/a", ["unused-slot"]
    tags.append(s.category.lower())
    if s.object in ("DEST", "UNIT_AND_DEST", "SRC"):
        tags.append("dest" if s.object != "SRC" else "src")
    if s.check in ("PARTY", "RAID", "RAID_CLASS"):
        tags.append(s.check.lower().replace("_", "-"))
    if h["chain"]:
        tags.append("chain-capable")
    if h["abort"]:
        return "blocked", tags + ["abort-path"]
    if s.category == "NYI":
        return "blocked", tags + ["nyi"]
    if s.check == "ENTRY" or target_id in (17, 106, 142, 46, 107):
        tags.append("condition" if s.check == "ENTRY" else "world-db")
        return "fixture-dependent", tags
    if h["function"] == "SelectImplicitCasterObjectTargets" and target_id not in CASTER_OBJECT_BRANCH:
        return "understood-with-defect", tags + ["silent-no-target"]
    if target_id in (54, 72, 116):
        return "understood-with-defect", tags
    moves = "MovePosition" in (h.get("branch") or "") or "move radius" in (h.get("branch") or "")
    if moves or s.category in ("AREA", "CONE", "LINE", "NEARBY", "TRAJ", "CHANNEL") or target_id in (39, 62, 125, 138, 131):
        tags.append("world-geometry" if s.category != "CHANNEL" else "runtime-state")
        return "fixture-dependent", tags
    return "understood", tags


# -- current-player census --------------------------------------------------------------
ROOT_KIND_LABEL = {"class-trait": "trait", "spec-spell": "spec", "class-skill": "skill", "current-gear": "gear",
                   "current-set": "set", "current-gem": "gem", "current-enchant": "enchant"}
CORE_ADMITTED_PAIRS = {(0, 0), (1, 0), (25, 0), (6, 0), (21, 0)}  # core/docs/architecture.md:103-109


def controlled_unit_spells() -> set[int]:
    """Controlled-unit ability population, recomputed exactly as
    ``controlled_units.spells.ability_census`` (controlled-unit-corpora/spells.json ``spells_total``)."""
    from controlled_units import spells as cs
    ft = cs.family_tables()
    d = cs._db2()
    cts = cs.creature_template_spells()
    out = {r["spell"] for r in ft["rows"]}
    for cid in d["summon_entries"]:
        out.update(cts.get(cid, {}).values())
    for spec_id, rows in d["spec_spells"].items():
        if d["specs"].get(spec_id, {}).get("ClassID") == "0":
            out.update(int(r["SpellID"]) for r in rows)
    out.discard(0)
    return out


def effect_rows(ctx, spells) -> list[dict]:
    """IsEffect() rows at DIFFICULTY_NONE with raw and load-corrected selector pairs."""
    from .attributes import corrected_effects
    out = []
    for spell in sorted(spells):
        effs = ctx.data.effects(spell)
        if not any(e.is_effect for e in effs):
            continue
        corrected, notes = corrected_effects(spell, effs)
        cmap = {c["index"]: c for c in corrected}
        for e in effs:
            if not e.is_effect:
                continue
            c = cmap[e.index]
            out.append({"spell": spell, "effect": e.index, "effect_type": e.effect, "a": e.target_a,
                        "b": e.target_b, "attributes": e.attributes, "chain_targets": e.chain_targets,
                        "corrected": [c["a"], c["b"]] if c["effect"] else None,
                        "correction_notes": [n for n in notes if f"EFFECT_{e.index} " in n],
                        "build_skew": ctx.is_skew(spell)})
    return out


def _pair_key(a: int, b: int) -> str:
    return f"{a},{b}"


def effect_class(row: dict) -> dict:
    """BRIEF §10 verdict of the selector path of one effect (raw pair, after load corrections)."""
    from .composition import classify, pair_hazards
    tags: list[str] = []
    unknowns: list[str] = []
    pair = tuple(row["corrected"]) if row["corrected"] else None
    if pair is None:
        return {"class": "n/a", "tags": ["trinity-correction", "effect-removed"], "unknowns": [],
                "build_skew": row["build_skew"], "selectors": [row["a"], row["b"]]}
    if list(pair) != [row["a"], row["b"]]:
        tags.append("trinity-correction")
    order = ("understood", "understood-with-defect", "fixture-dependent", "blocked", "unresolved")
    verdict = "understood"
    for t in pair:
        if t >= TOTAL_SPELL_TARGETS:
            verdict = "blocked"
            tags.append("selector-outside-trinity")
            unknowns.append("TG-A-01")
            continue
        v, tg = understood(t)
        if v == "n/a":
            continue
        tags += tg
        if order.index(v) > order.index(verdict):
            verdict = v
    if all(t < TOTAL_SPELL_TARGETS for t in pair):
        rel = classify(*pair)["relation"]
        tags.append(f"rel-{rel}")
        haz = pair_hazards(*pair)
        if any("ASSERT" in h for h in haz):
            verdict = "understood-with-defect" if verdict in ("understood",) else verdict
            tags.append("assert-hazard")
        if pair[0] == 0 and pair[1] == 0:
            tags.append("effect-type-implicit")
    if row.get("chain_targets", 0) > 1 and any(t < TOTAL_SPELL_TARGETS and handler(t)["chain"] for t in pair):
        tags.append("chain")
        if verdict == "understood":
            verdict = "fixture-dependent"
    if pair in CORE_ADMITTED_PAIRS:
        tags.append("core-admitted-pair")
    return {"class": verdict, "tags": sorted(set(tags)), "unknowns": sorted(set(unknowns)),
            "build_skew": row["build_skew"], "selectors": list(pair)}


def census(ctx) -> dict:
    from collections import Counter, defaultdict
    reach = ctx.scope.reach
    rows = effect_rows(ctx, reach)
    roots = ctx.scope.roots
    spec_of: dict[int, set[int]] = defaultdict(set)
    for spec, spells in ctx.scope.specs_reach.items():
        for s in spells:
            spec_of[s].add(spec)

    def kinds(spell: int) -> list[str]:
        k = roots.by_spell.get(spell)
        return sorted(ROOT_KIND_LABEL[x] for x in k) if k else ["triggered"]

    def count(rs: list[dict], corrected: bool = False) -> dict:
        def sel(r):
            return tuple(r["corrected"]) if corrected else (r["a"], r["b"])
        live = [r for r in rs if not corrected or r["corrected"]]
        return {"effects": len(rs), "spells": len({r["spell"] for r in rs}),
                "target_a": dict(sorted(Counter(sel(r)[0] for r in live).items())),
                "target_b": dict(sorted(Counter(sel(r)[1] for r in live).items())),
                "pairs": dict(sorted(Counter(_pair_key(*sel(r)) for r in live).items(),
                                     key=lambda kv: tuple(map(int, kv[0].split(","))))),
                "build_skew_effects": sum(r["build_skew"] for r in rs)}

    by_kind: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        for k in kinds(r["spell"]):
            by_kind[k].append(r)
    per_spec = {}
    for spec in sorted(ctx.scope.specs_reach):
        rs = [r for r in rows if spec in spec_of[r["spell"]]]
        c = count(rs)
        per_spec[str(spec)] = {"name": roots.spec_names.get(spec), "class": roots.class_names.get(roots.class_of_spec.get(spec)),
                               "effects": c["effects"], "spells": c["spells"], "pairs": c["pairs"]}
    cu = controlled_unit_spells()
    cu_rows = effect_rows(ctx, {s for s in cu if ctx.catalog.exists(s)})
    core = Counter("admitted" if (r["a"], r["b"]) in CORE_ADMITTED_PAIRS else "not-admitted" for r in rows)
    beyond = sorted({t for r in rows + cu_rows for t in (r["a"], r["b"]) if t >= TOTAL_SPELL_TARGETS})
    corrected_changes = [{"spell": r["spell"], "effect": r["effect"], "raw": [r["a"], r["b"]], "corrected": r["corrected"],
                          "notes": r["correction_notes"]}
                         for r in rows if r["corrected"] != [r["a"], r["b"]]]
    return {
        "population": "IsEffect() SpellEffect rows at DIFFICULTY_NONE of ctx.scope.reach (current-player authored reach)",
        "all": count(rows),
        "all_after_load_corrections": count(rows, corrected=True),
        "by_root_kind": {k: count(v) for k, v in sorted(by_kind.items())},
        "root_kind_note": "a spell counts under every acquisition root kind it has; non-root reach = 'triggered'",
        "per_spec": per_spec,
        "controlled_units": {"population": "controlled_units.spells.ability_census population (controlled-unit-corpora/spells.json)",
                             "spells_total": len(cu), "with_effects": count(cu_rows),
                             "overlap_with_player_reach": len(cu & reach)},
        "core_admitted_pairs": {"pairs": sorted(map(list, CORE_ADMITTED_PAIRS)), "source": "core/docs/architecture.md:103-109",
                                "player_effects": dict(core)},
        "selector_ids_beyond_trinity": beyond,
        "load_corrections_changing_pairs": corrected_changes,
        "_rows": rows, "_cu_rows": cu_rows,
    }


def core_catalog_crosscheck() -> dict:
    """Parse Core's implicit-target catalog textually (read-only) and compare the five axes per raw id."""
    import re
    from . import ROOT
    path = ROOT.parent / "core" / "crates" / "dbc" / "src" / "implicit_target.rs"
    if not path.is_file():
        raise FailClosed(f"core catalog {path} not present")
    text = path.read_text(encoding="utf-8")
    kinds = dict((int(v), k) for k, v in re.findall(r"^\s+(\w+) = (\d+),$", text[:text.index("/// Object produced")], re.M))
    table = text[text.index("pub const IMPLICIT_TARGET_SEMANTICS"):]
    table = table[:table.index("];")]
    rows = re.findall(r"target\(O::(\w+), R::(\w+), S::(\w+), C::(\w+), D::(\w+)\)", table)
    o_map = {"None": "NONE", "Source": "SRC", "Destination": "DEST", "Unit": "UNIT", "UnitAndDestination": "UNIT_AND_DEST",
             "GameObject": "GOBJ", "GameObjectItem": "GOBJ_ITEM", "Item": "ITEM", "Corpse": "CORPSE",
             "CorpseEnemy": "CORPSE_ENEMY", "CorpseAlly": "CORPSE_ALLY"}
    r_map = {"None": "NONE", "Caster": "CASTER", "Target": "TARGET", "Last": "LAST", "Source": "SRC", "Destination": "DEST"}
    s_map = {"NotImplemented": "NYI", "Default": "DEFAULT", "Channel": "CHANNEL", "Nearby": "NEARBY", "Cone": "CONE",
             "Area": "AREA", "Trajectory": "TRAJ", "Line": "LINE"}
    c_map = {"Default": "DEFAULT", "Entry": "ENTRY", "Enemy": "ENEMY", "Ally": "ALLY", "Party": "PARTY", "Raid": "RAID",
             "RaidClass": "RAID_CLASS", "Passenger": "PASSENGER", "Summoned": "SUMMONED"}
    d_map = {"None": "NONE", "Front": "FRONT", "Back": "BACK", "Right": "RIGHT", "Left": "LEFT", "FrontRight": "FRONT_RIGHT",
             "BackRight": "BACK_RIGHT", "BackLeft": "BACK_LEFT", "FrontLeft": "FRONT_LEFT", "Random": "RANDOM", "Entry": "ENTRY"}
    axis_mismatch = []
    names = []
    for raw, (o, r, s, c, d) in enumerate(rows):
        core_axes = (o_map[o], r_map[r], s_map[s], c_map[c], d_map[d])
        if raw < TOTAL_SPELL_TARGETS:
            t = info(raw)
            tc_axes = (t.object, t.reference, t.category, t.check, t.direction)
            if core_axes != tc_axes:
                axis_mismatch.append({"id": raw, "core": core_axes, "trinity": tc_axes})
        core_name = kinds.get(raw)
        tc_name = TARGET_NAMES.get(raw)
        norm = (core_name or "").lower()
        tcn = (tc_name or "").removeprefix("TARGET_").replace("_", "").lower()
        tcn = (tcn.replace("dest", "destination").replace("src", "source").replace("gameobject", "gameobject"))
        if tcn != norm:
            if tc_name is None:
                kind = "trinity-gap (no enum name)"
            elif tc_name.startswith("TARGET_UNK_") and (core_name or "").startswith("Reserved"):
                kind = "unk-vs-reserved"
            elif tc_name.startswith("TARGET_UNK_"):
                kind = "trinity-unk-named-by-core-from-axes"
            elif raw == 57:
                kind = ("semantic-name-difference (Core 'UnitCasterTargetRaid' vs Trinity TARGET_UNIT_TARGET_RAID; both "
                        "catalogs' reference axis is TARGET: the explicit target, not the caster)")
            else:
                kind = "spelling/abbreviation"
            names.append({"id": raw, "core": core_name, "trinity": tc_name, "kind": kind})
    return {"core_file": "core/crates/dbc/src/implicit_target.rs (read-only, parsed textually)",
            "core_rows": len(rows), "core_max_raw": len(rows) - 1, "trinity_total_spell_targets": TOTAL_SPELL_TARGETS,
            "axis_mismatches": axis_mismatch, "name_differences": names,
            "note": "vocabulary mapping only; Core reads the same five axes (object, reference, selection, check, direction)"}
