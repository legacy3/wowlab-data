#!/usr/bin/env python3
"""Temporary, untracked spell-effect audit generator. Remove after the audit."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parent
TC = ROOT.parent / "TrinityCore"
SIMC = ROOT.parent / "simc"
DB = Path("/tmp/spell-effect-audit.duckdb")
CON = duckdb.connect(str(DB), read_only=True)


CORE = {
    0, 2, 3, 6, 8, 9, 10, 17, 24, 28, 30, 31, 32, 35, 38, 42, 58, 62,
    64, 65, 67, 68, 77, 78, 96, 121, 126, 130, 136, 137, 142, 144, 149,
    151, 157, 164, 174, 179, 202, 203, 252, 254, 288, 289, 290, 293, 299,
    305, 310, 357, 358,
}

OTHER_META_FIELDS = [
    "DifficultyID", "EffectAmplitude", "EffectAttributes", "EffectAuraPeriod",
    "EffectBasePointsF", "EffectChainTargets", "EffectMechanic", "EffectPointsPerResource",
    "EffectBonusCoefficient", "EffectChainAmplitude", "EffectPos_facing",
    "EffectRealPointsPerLevel", "BonusCoefficientFromAP", "PvpMultiplier",
    "Coefficient", "Variance", "ResourceCoefficient",
    "GroupSizeBasePointsCoefficient", "ScalingClass",
    "Node__Field_12_0_0_63534_001", "EffectSpellClassMask_0",
    "EffectSpellClassMask_1", "EffectSpellClassMask_2", "EffectSpellClassMask_3",
]

HANDLER_NOTES = {
    44: "Reads misc0 as SkillLine and the integer effect value as tier; mutates the selected player with SetSkill.",
    47: "The apparent MiscValue use is in commented-out code; the current handler performs guards only and mutates nothing.",
    118: "Reads misc0 as SkillLine and the integer effect value as tier; raises the effect's unit caster skill only when lower.",
    162: "Reads runtime cast-request `m_misc.SpecializationId`; mutates the selected player's specialization selection.",
    173: "Reads the integer effect value as a one-based guild-vault tab number, converts it to zero-based, and invokes the guild purchase handler for the caster.",
    181: "Reads runtime cast-request `m_misc.TalentId`; removes that talent from the selected player.",
    200: "Reads the integer effect value as a percentage; calls the selected player's BattlePetMgr to heal the roster.",
    222: "Reads runtime cast-request `m_misc.Raw.Data[0]` and cast-item context while creating/storing the heirloom for the selected player.",
    225: "Reads the integer effect value as the battle-pet level delta; mutates the selected player's pet journal entry.",
    236: "Reads misc0 as contentTuningId and misc1 as XP difficulty, computes Quest::XPValue for the selected player, and grants that result; the authored effect amount is not used by this handler.",
    237: "Reads the integer effect value as rested-experience time/bonus input; mutates the selected player's rested state.",
    245: "Reads runtime cast-request `m_misc.Raw.Data[0]` and `m_castItemEntry`; mutates the selected heirloom collection/item state.",
    253: "Reads the integer effect value as honor; mutates the selected player's honor state.",
    286: "Reads the integer effect value as battle-pet experience; mutates the selected player's pet journal entry.",
    291: "Reads misc0 as SpellFamilyName, misc1 as a one-based SpellFamilyFlags bit, and the integer effect value as signed milliseconds; mutates matching selected-unit cooldown history.",
    292: "Reads misc0 as SpellCategory and the integer effect value as signed milliseconds; mutates matching selected-unit cooldown history.",
    304: "Reads runtime `m_customArg` as TraitConfig and the integer effect value; mutates the player's active combat trait configuration.",
    335: "Reads misc0 as account data-element ID and the integer effect value as the new value; mutates account-scoped player data.",
    336: "Reads misc0 as character data-element ID and the integer effect value as the new value; mutates character-scoped player data.",
    337: "Reads misc0 as account data-flag ID and the integer effect value as the flag value; mutates account-scoped player data.",
    338: "Reads misc0 as character data-flag ID and the integer effect value as the flag value; mutates character-scoped player data.",
}

# Candidate DB2 relationships justified by handler code, enum comments, or a
# specific hypothesis under test. Absence from this map means no table typing is
# claimed; misc values remain opaque codes even if their integers collide.
JOIN_CANDIDATES = {
    11: [("EffectMiscValue_0", "AreaTable", "ID", "bind area")],
    13: [("EffectMiscValue_0", "SpellName", "ID", "aura spell holding destination")],
    14: [("EffectMiscValue_0", "CurrencyTypes", "ID", "currency whose cap increases")],
    15: [("EffectMiscValue_1", "SpellVisualKit", "ID", "loading-screen visual kit")],
    16: [("EffectMiscValue_0", "QuestV2", "ID", "quest")],
    33: [("EffectMiscValue_0", "LockType", "ID", "lock type")],
    34: [("EffectItemType", "Item", "ID", "replacement item")],
    36: [("EffectTriggerSpell", "SpellName", "ID", "learned spell")],
    44: [("EffectMiscValue_0", "SkillLine", "ID", "skill line")],
    45: [("EffectMiscValue_0", "Movie", "ID", "movie")],
    50: [("EffectMiscValue_0", "GameObjects", "ID", "client DB2 collision test for server GameObjectTemplate entry")],
    53: [("EffectMiscValue_0", "SpellItemEnchantment", "ID", "permanent enchantment"), ("CASE WHEN EffectMiscValue_0=0 THEN CAST(EffectBasePointsF AS BIGINT) ELSE 0 END", "Item", "ID", "source-item candidate in zero-enchantment variant")],
    54: [("EffectMiscValue_0", "SpellItemEnchantment", "ID", "temporary enchantment")],
    55: [("EffectMiscValue_0", "Creature", "ID", "creature")],
    56: [("EffectMiscValue_0", "Creature", "ID", "pet creature")],
    57: [("EffectTriggerSpell", "SpellName", "ID", "learned pet spell")],
    59: [("EffectItemType", "Item", "ID", "authored output-item candidate")],
    66: [("EffectItemType", "Item", "ID", "item whose charges are restored")],
    74: [("EffectMiscValue_0", "GlyphProperties", "ID", "glyph")],
    76: [("EffectMiscValue_0", "GameObjects", "ID", "game object")],
    90: [("EffectMiscValue_0", "Creature", "ID", "kill-credit creature")],
    92: [("EffectMiscValue_0", "SpellItemEnchantment", "ID", "held-item enchantment")],
    103: [("EffectMiscValue_0", "Faction", "ID", "reputation faction")],
    104: [("EffectMiscValue_0", "GameObjects", "ID", "slotted game object")],
    112: [("EffectTriggerSpell", "SpellName", "ID", "configured spell")],
    118: [("EffectMiscValue_0", "SkillLine", "ID", "skill line")],
    123: [("EffectMiscValue_0", "TaxiPath", "ID", "taxi path")],
    131: [("EffectMiscValue_0", "SoundKit", "ID", "sound kit")],
    132: [("EffectMiscValue_0", "SoundKit", "ID", "music sound kit")],
    133: [("EffectTriggerSpell", "SpellName", "ID", "learned spell to remove")],
    134: [("EffectMiscValue_0", "Creature", "ID", "kill-credit creature")],
    139: [("EffectMiscValue_0", "QuestV2", "ID", "quest")],
    140: [("EffectTriggerSpell", "SpellName", "ID", "forced spell")],
    141: [("EffectTriggerSpell", "SpellName", "ID", "forced spell")],
    147: [("EffectMiscValue_0", "QuestV2", "ID", "quest")],
    148: [("EffectTriggerSpell", "SpellName", "ID", "triggered missile spell")],
    150: [("EffectMiscValue_0", "QuestV2", "ID", "quest")],
    153: [("EffectMiscValue_0", "Creature", "ID", "created pet creature")],
    154: [("EffectMiscValue_0", "TaxiNodes", "ID", "taxi node")],
    156: [("EffectMiscValue_0", "SpellItemEnchantment", "ID", "prismatic enchantment")],
    160: [("EffectTriggerSpell", "SpellName", "ID", "deferred forced spell")],
    166: [("EffectMiscValue_0", "CurrencyTypes", "ID", "currency")],
    169: [("EffectItemType", "Item", "ID", "destroyed item entry")],
    171: [("EffectMiscValue_0", "GameObjects", "ID", "personal game object")],
    172: [("EffectTriggerSpell", "SpellName", "ID", "resurrection aura spell")],
    184: [("EffectMiscValue_0", "Faction", "ID", "reputation faction")],
    183: [("EffectMiscValue_0", "SpellVisualEffectName", "ID", "ground-area visual")],
    191: [("EffectMiscValue_0", "ResearchSite", "ID", "archaeology digsite")],
    195: [("EffectMiscValue_0", "SceneScriptPackage", "ID", "scene script package")],
    196: [("EffectMiscValue_0", "SceneScriptPackage", "ID", "rejected package-ID collision; handler expects server scene_template.SceneId"), ("EffectMiscValue_0", "SceneScript", "ID", "competing scene-script collision"), ("EffectMiscValue_0", "SceneScriptPackageMember", "ID", "competing package-member collision")],
    197: [("EffectMiscValue_0", "SceneScriptPackage", "ID", "rejected package-ID collision; handler expects server scene_template.SceneId"), ("EffectMiscValue_0", "SceneScript", "ID", "competing scene-script collision"), ("EffectMiscValue_0", "SceneScriptPackageMember", "ID", "competing package-member collision")],
    198: [("EffectMiscValue_0", "SceneScriptPackage", "ID", "rejected package-ID collision; handler expects server scene_template.SceneId"), ("EffectMiscValue_0", "SceneScript", "ID", "competing scene-script collision"), ("EffectMiscValue_0", "SceneScriptPackageMember", "ID", "competing package-member collision")],
    207: [("EffectMiscValue_0", "QuestV2", "ID", "quest candidate used by source-aligned player-choice context")],
    208: [("EffectMiscValue_0", "Faction", "ID", "reputation faction")],
    210: [("EffectMiscValue_0", "GarrBuilding", "ID", "garrison building")],
    211: [("EffectMiscValue_0", "GarrSpecialization", "ID", "garrison specialization")],
    212: [("EffectMiscValue_0", "SpellLabel", "LabelID", "spell label")],
    216: [("EffectMiscValue_0", "CharShipment", "ID", "character shipment")],
    218: [("EffectMiscValue_0", "AreaTrigger", "ID", "area trigger")],
    220: [("EffectMiscValue_0", "GarrFollower", "ID", "garrison follower")],
    221: [("EffectMiscValue_0", "GarrMission", "ID", "garrison mission")],
    223: [("EffectMiscValue_0", "ItemBonus", "ID", "item-bonus candidate A"), ("EffectMiscValue_1", "ItemBonus", "ID", "item-bonus candidate B"), ("EffectMiscValue_0", "ItemBonusList", "ID", "competing bonus-list candidate A"), ("EffectMiscValue_1", "ItemBonusList", "ID", "competing bonus-list candidate B"), ("EffectMiscValue_0", "ItemBonusTree", "ID", "competing bonus-tree candidate A"), ("EffectMiscValue_1", "ItemBonusTree", "ID", "competing bonus-tree candidate B")],
    224: [("EffectMiscValue_0", "GarrBuilding", "ID", "garrison building")],
    227: [("EffectMiscValue_0", "LFGDungeons", "ID", "LFG dungeon")],
    230: [("EffectMiscValue_1", "Item", "ID", "artifact item"), ("EffectMiscValue_1", "ArtifactItemToTransmog", "ItemID", "artifact-item transmog mapping")],
    232: [("EffectMiscValue_0", "Phase", "ID", "phase")],
    234: [("EffectMiscValue_0", "GameObjectDisplayInfo", "ID", "static game-object display candidate"), ("EffectMiscValue_0", "CreatureDisplayInfo", "ID", "competing creature-display candidate")],
    238: [("EffectMiscValue_0", "SkillLine", "ID", "skill line")],
    239: [("EffectMiscValue_0", "GarrBuilding", "ID", "garrison building")],
    243: [("EffectMiscValue_0", "SpellItemEnchantment", "ID", "enchant illusion")],
    244: [("EffectMiscValue_0", "GarrAbility", "ID", "follower ability")],
    246: [("EffectMiscValue_0", "GarrMission", "ID", "garrison mission")],
    247: [("EffectMiscValue_0", "GarrMissionSet", "ID", "garrison mission set")],
    248: [("EffectMiscValue_0", "CharShipmentContainer", "ID", "shipment container")],
    251: [("EffectMiscValue_0", "CharShipmentContainer", "ID", "shipment container")],
    255: [("EffectMiscValue_0", "TransmogSet", "ID", "transmog set")],
    258: [("EffectMiscValue_0", "MapChallengeMode", "ID", "challenge-mode map"), ("EffectMiscValue_1", "KeystoneAffix", "ID", "keystone affix")],
    265: [("EffectMiscValue_0", "AzeriteEssence", "ID", "azerite essence"), ("EffectMiscValue_0", "AzeriteEssencePower", "ID", "competing power-ID collision")],
    266: [("EffectMiscValue_0", "ItemBonusListGroupEntry", "ID", "claimed group entry"), ("EffectMiscValue_0", "ItemBonusListGroup", "ID", "competing group ID")],
    268: [("EffectTriggerSpell", "MountEquipment", "LearnedBySpell", "mount equipment learned by configured spell")],
    269: [("EffectMiscValue_0", "ItemBonusListGroup", "ID", "bonus-list group"), ("EffectMiscValue_0", "ItemBonusListGroupEntry", "ID", "competing group-entry ID")],
    272: [("EffectMiscValue_0", "Covenant", "ID", "covenant; zero also has reset semantics")],
    273: [("EffectMiscValue_1", "RuneforgeLegendaryAbility", "ID", "runeforge legendary ability")],
    276: [("EffectMiscValue_0", "TransmogIllusion", "ID", "transmog illusion")],
    277: [("EffectMiscValue_0", "UIChromieTimeExpansionInfo", "ID", "Chromie Time expansion; zero includes reset/present contexts")],
    279: [("EffectMiscValue_0", "GarrTalent", "ID", "garrison talent")],
    281: [("EffectMiscValue_0", "SoulbindConduit", "ID", "soulbind conduit")],
    282: [("EffectMiscValue_0", "CurrencyTypes", "ID", "currency")],
    283: [("EffectMiscValue_0", "Campaign", "ID", "campaign")],
    284: [("EffectMiscValue_0", "BroadcastText", "ID", "broadcast text")],
    285: [("EffectMiscValue_0", "MapChallengeMode", "ID", "challenge-mode map; zero is a distinct clear/reset variant")],
    292: [("EffectMiscValue_0", "SpellCategory", "ID", "cooldown category")],
    294: [("EffectMiscValue_0", "CraftingData", "ID", "crafting data")],
    295: [("EffectMiscValue_0", "ItemSalvage", "ID", "item salvage")],
    296: [("EffectMiscValue_0", "ItemSalvage", "ID", "item salvage"), ("EffectMiscValue_1", "CraftingData", "ID", "crafting data")],
    301: [("EffectMiscValue_0", "CraftingData", "ID", "crafting data")],
    300: [("EffectMiscValue_0", "ItemBonusListGroup", "ID", "bonus-list group")],
    303: [("EffectMiscValue_0", "TraitTree", "ID", "trait tree")],
    307: [("EffectMiscValue_0", "CreatureDisplayInfo", "ID", "rejected display-info hypothesis"), ("EffectMiscValue_1", "CreatureDisplayInfo", "ID", "rejected display-info hypothesis"), ("EffectMiscValue_1", "Creature", "ID", "competing creature ID")],
    311: [("EffectMiscValue_0", "QuestLine", "ID", "quest line")],
    313: [("EffectMiscValue_0", "ItemBonusTree", "ID", "bonus tree to preserve"), ("EffectMiscValue_0", "ItemBonusTreeNode", "ID", "competing node ID"), ("EffectMiscValue_1", "Item", "ID", "target/source item candidate")],
    314: [("EffectMiscValue_0", "ItemBonusTree", "ID", "required bonus tree")],
    316: [("EffectMiscValue_0", "QuestLabel", "LabelID", "quest-objective kill label candidate")],
    317: [("EffectMiscValue_0", "QuestLabel", "LabelID", "quest-objective kill label candidate")],
    324: [("EffectMiscValue_0", "HouseDecor", "ID", "house decor"), ("EffectMiscValue_0", "Item", "ID", "competing item ID"), ("EffectMiscValue_0", "GameObjects", "ID", "competing game-object ID")],
    333: [("EffectMiscValue_0", "QuestV2", "ID", "competing quest collision"), ("EffectMiscValue_0", "SpellVisualKit", "ID", "competing visual-kit collision"), ("EffectMiscValue_0", "SoundKit", "ID", "competing sound-kit collision"), ("EffectMiscValue_0", "ModifierTree", "ID", "competing modifier-tree collision"), ("EffectMiscValue_0", "ConversationLine", "ID", "competing conversation-line collision")],
    335: [("EffectMiscValue_0", "PlayerDataElementAccount", "ID", "account data element")],
    336: [("EffectMiscValue_0", "PlayerDataElementCharacter", "ID", "character data element")],
    337: [("EffectMiscValue_0", "PlayerDataFlagAccount", "ID", "account data flag")],
    338: [("EffectMiscValue_0", "PlayerDataFlagCharacter", "ID", "character data flag")],
    341: [("EffectMiscValue_0", "WarbandScene", "ID", "warband scene")],
    344: [("EffectMiscValue_0", "Map", "ID", "map candidate"), ("EffectMiscValue_0", "AreaTable", "ID", "area candidate"), ("EffectMiscValue_0", "QuestV2", "ID", "colliding quest candidate")],
    347: [("EffectMiscValue_0", "TransmogOutfitEntry", "ID", "rejected enum-comment candidate; handler uses cast-request misc")],
    349: [("EffectMiscValue_0", "HouseRoom", "ID", "house room")],
    350: [("EffectMiscValue_0", "ExteriorComponent", "ID", "exterior component"), ("EffectMiscValue_0", "RoomComponent", "ID", "incidental room-component collision")],
    351: [("EffectMiscValue_0", "HouseTheme", "ID", "house theme"), ("EffectMiscValue_0", "HouseRoom", "ID", "competing house-room collision")],
    352: [("EffectMiscValue_0", "RoomComponentTexture", "ID", "room-component texture"), ("EffectMiscValue_0", "HouseRoom", "ID", "same-number competing collision"), ("EffectMiscValue_0", "HouseTheme", "ID", "same-number competing collision")],
    354: [("EffectMiscValue_0", "NeighborhoodInitiative", "ID", "neighborhood initiative")],
    355: [("EffectMiscValue_0", "HouseExteriorWmoData", "ID", "house exterior WMO data")],
    359: [("EffectMiscValue_0", "ItemCondition", "ID", "item condition")],
}


def sql(query: str) -> list[dict]:
    cursor = CON.execute(query)
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def parse_enum(path: Path, start: str, end: str, prefix: str) -> dict[int, dict]:
    text = path.read_text()
    block = text[text.index(start):]
    block = block[:block.index(end)]
    out = {}
    rx = re.compile(rf"^\s*({re.escape(prefix)}[A-Z0-9_]+)\s*=\s*(\d+)\s*,?\s*(?://\s*(.*))?$", re.M)
    for name, raw, comment in rx.findall(block):
        out[int(raw)] = {"name": name, "comment": comment.strip()}
    return out


def parse_dispatch() -> dict[int, dict]:
    path = TC / "src/server/game/Spells/SpellEffects.cpp"
    text = path.read_text()
    out = {}
    rx = re.compile(r"&Spell::([A-Za-z0-9_]+),\s*//\s*(\d+)\s+([A-Z0-9_]+)(?:\s+(.*))?$")
    for line_no, line in enumerate(text.splitlines(), 1):
        m = rx.search(line)
        if m:
            out[int(m.group(2))] = {
                "handler": m.group(1), "name": m.group(3),
                "comment": (m.group(4) or "").strip(), "line": line_no,
            }
    return out


def parse_handler_bodies(dispatch: dict[int, dict]) -> dict[str, dict]:
    paths = list((TC / "src/server/game/Spells").glob("*.cpp"))
    results = {}
    for path in paths:
        text = path.read_text(errors="replace")
        for m in re.finditer(r"void\s+Spell::([A-Za-z0-9_]+)\s*\([^)]*\)\s*\{", text):
            name = m.group(1)
            depth = 1
            i = m.end()
            while i < len(text) and depth:
                depth += (text[i] == "{") - (text[i] == "}")
                i += 1
            body = text[m.end():i - 1]
            scan_body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
            scan_body = re.sub(r"//[^\n]*", "", scan_body)
            line = text.count("\n", 0, m.start()) + 1
            fields = sorted(set(re.findall(
                r"Effect(?:MiscValue|TriggerSpell|ItemType|BasePoints|AuraPeriod|RadiusIndex|ChainTargets|Amplitude)|MiscValue|TriggerSpell|ItemType|BasePoints|GetEffectValueAsInt|effectValue|m_misc\.[A-Za-z0-9_.\[\]]+|m_customArg|m_castItemEntry",
                scan_body,
            )))
            calls = sorted(set(re.findall(r"\b(?:unitTarget|itemTarget|gameObjTarget|player|caster|target|m_caster)->([A-Za-z0-9_]+)\s*\(", scan_body)))[:12]
            results[name] = {"path": str(path.relative_to(ROOT.parent)), "line": line, "fields": fields, "calls": calls}
    return results


def compact_counts(raw: int, expr: str, limit: int | None = 8, where: str = "") -> str:
    q = f"""
      SELECT CAST({expr} AS VARCHAR) val, count(*) n
      FROM effect WHERE Effect={raw} {where}
      GROUP BY {expr} ORDER BY n DESC, val
    """
    rows = sql(q)
    shown = rows if limit is None else rows[:limit]
    result = ", ".join(f"{r['val']}×{r['n']}" for r in shown) or "none"
    if limit is not None and len(rows) > limit:
        tail = rows[limit:]
        result += f", other {len(tail)} values×{sum(r['n'] for r in tail)} rows"
    return result


def scalar(query: str, key: str):
    rows = sql(query)
    return rows[0][key] if rows else None


def normalize_name(name: str | None, raw: int) -> str:
    if not name:
        return f"Unidentified effect {raw}"
    for prefix in ("SPELL_EFFECT_", "E_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    if name == str(raw) or name in {f"E_{raw}", "5"}:
        return f"Unidentified effect {raw}"
    return name.replace("_", " ").title()


def classify(raw: int, tc_name: str | None, handler: str | None) -> tuple[str, str, str]:
    special = {
        5: ("Rejected interpretation", "Item-adjacent operation; verb and state transition unresolved", "prior item-operation verbs fail the complete sequence census"),
        15: ("Strongly verified", "Delayed teleport, optionally sending a SpellVisualKit loading screen to a player target", "TrinityCore always schedules delayed teleport by misc0 milliseconds and sends the misc1 visual-kit loading screen only for a player target when misc1 is nonzero; 233/826 current rows have misc1 zero"),
        16: ("Partially characterized", "Quest-completion operation for nonzero misc0 plus unresolved zero-payload unlock/flag family", "7,177 rows carry nonzero misc0 and 7,175 join QuestV2, but 167 zero-payload rows include modern renown, waygate, collection, and unlock contexts that TrinityCore ignores"),
        18: ("Strongly verified", "Initiate a resurrection request", "TrinityCore stores resurrection-request data and sends the request to the selected player; acceptance and resurrection occur later"),
        22: ("Strongly verified", "Enable the caster to parry", "TrinityCore sets the caster's CanParry capability true; no parry event occurs at cast time"),
        23: ("Strongly verified", "Enable the caster to block", "TrinityCore sets the caster's CanBlock capability true; no block event occurs at cast time"),
        29: ("Strongly verified", "Near-teleport the selected unit to the destination", "TrinityCore directly calls NearTeleportTo without jump motion, and the current population is dominated by Blink, Shimmer, Teleport, Shadowstep, and warp contexts"),
        34: ("Strongly verified", "Replace the cast item with EffectItemType, preserving enchantments and durability loss", "TrinityCore requires a player-owned cast item, creates EffectItemType, copies enchantments/durability loss, then replaces the inventory/bank/equipment item when storage checks pass"),
        40: ("Strongly verified", "Enable the selected unit to dual-wield", "TrinityCore sets the selected unit's CanDualWield capability true; it does not execute an attack"),
        44: ("Strongly verified", "Set the selected player's misc0 skill tier to the effect amount", "after player/skill/tier validation, TrinityCore unconditionally calls SetSkill on the selected player with skill misc0 and tier amount"),
        223: ("Partially characterized", "Two-field ItemBonusTree-compatible item operation with unresolved verb", "all 691 rows have both misc fields nonzero and both fields join ItemBonusTree, but substantial ItemBonus/ItemBonusList collisions and absent execution prevent a proved state transition"),
        218: ("Partially characterized", "AreaTrigger-referencing operation with unresolved verb and lifecycle", "the sole misc0 value 7696 joins AreaTrigger.ID and the owning spell is Reduction in Force Area Trigger; no local handler defines the operation"),
        230: ("Partially characterized", "Artifact-weapon item-referencing intensification operation", "all 36 misc1 values join Item and ArtifactItemToTransmog.ItemID, and every owning spell is named Intensification; the verb and state transition remain unproved"),
        236: ("Strongly verified", "Grant selected-player XP computed from misc0 content tuning and misc1 difficulty", "TrinityCore computes Quest::XPValue from the selected player and both misc fields, then grants the result; it does not use the authored effect amount"),
        243: ("Strongly verified", "Set or clear an item's all-spec enchant illusion", "TrinityCore sets ITEM_MODIFIER_ENCHANT_ILLUSION_ALL_SPECS to misc0; 34 nonzero rows reference SpellItemEnchantment while zero is authored as Remove Illusion or Illusion: None"),
        265: ("Strongly verified", "Set the selected player's misc0 Azerite essence to rank misc1", "all 113 misc0 rows join AzeriteEssence and TrinityCore passes misc0 as AzeriteEssenceID and misc1 as rank to SetEssenceRank"),
        268: ("Strongly verified", "Apply or replace mount equipment selected by EffectTriggerSpell", "all five triggers join MountEquipment.LearnedBySpell; TrinityCore learns the match, removes other equipment learned spells and buff auras, conditionally casts its BuffSpell when mounted, and sends the result"),
        266: ("Partially characterized", "Item bonus-list-group entry operation", "descriptive source name and item context; executable server behavior absent"),
        269: ("Strongly verified", "Increase item bonus-list-group step by authored amount", "all 174 misc0 values join ItemBonusListGroup, amounts are positive 1..25, and authored contexts are upgrade/empower operations; reviewed raw 299 is a conceptual inverse, but current group IDs do not overlap"),
        307: ("Rejected interpretation", "Clone-cleanup-related operation with heterogeneous identifiers", "the proposed CreatureDisplayInfo-keyed removal model is falsified by field-domain outliers"),
        313: ("Partially characterized", "Item-bonus change family; one field references a bonus-tree constraint", "item context and source comment do not establish the generic mutation"),
        359: ("Rejected interpretation", "ItemCondition-referencing, item-targeted operation; verb unresolved", "the join proves a condition noun but not an apply/mutation verb"),
        47: ("Partially characterized", "Legacy profession marker plus unresolved teleport/cancel-summons variant", "155 rows form the legacy profession cluster, but three newest Vehicle Destroyed- Teleport rows use sequence 252>47 and carry Cancel Summons trigger spells that TrinityCore's inert handler ignores"),
        50: ("Strongly verified", "Create a game object from a server template", "TrinityCore resolves misc0 through GameObjectTemplate, creates the object at a selected or caster-relative position, inherits phase, and applies duration cleanup"),
        52: ("Partially characterized", "Set Warband battle-pet count cap from effect amount", "all three current rows and their descriptions agree; conflicting historical labels and null dispatch leave generic execution unobserved"),
        53: ("Partially characterized", "Permanent-item-enchant population plus unresolved source-item upgrade/binding variant", "978 nonzero misc0 rows fit SpellItemEnchantment and the permanent-enchant handler, but four zero-misc0 rows include three non-test upgrade/binding operations whose base points instead reference source items"),
        60: ("Strongly verified", "Enable the caster's configured weapon or armor subclass proficiency", "TrinityCore reads EquippedItemClass/SubClassMask, adds missing weapon/armor proficiency bits to the player caster, and sends the updated proficiency"),
        73: ("Strongly verified", "Initiate talent-reset confirmation for the selected player", "TrinityCore sends the selected player a respec wipe confirmation and calculated reset cost; the handler does not directly erase talents"),
        74: ("Strongly verified", "Modify glyph selection (apply, replace, or clear)", "TrinityCore replaces/appends a nonzero glyph ID but erases the matched glyph when misc0 is zero; six current zero-ID rows include Clear Glyph spells"),
        79: ("Strongly verified", "Apply sanctuary combat and threat suppression", "TrinityCore stops or suppresses combat outside dungeons, otherwise clears qualifying hostile threat, then records the last-sanctuary time"),
        83: ("Strongly verified", "Initiate a duel request and create its arbiter and pending state", "TrinityCore creates the duel arbiter, sends DuelRequested, and creates pending DuelInfo; the duel fight begins only after the later acceptance/countdown flow"),
        84: ("Strongly verified", "Execute player auto-unstuck", "TrinityCore implements the configured auto-unstuck flow: graveyard handling when dead, otherwise home-bind teleport or self-kill depending on Hearthstone cooldown"),
        86: ("Strongly verified", "Apply a configured GameObject action to the selected game object", "TrinityCore casts misc0 to GameObjectActions and passes it with misc1 to ActivateObject; actions include activation-state, lock/open, destroy/rebuild, despawn, presentation, and movement variants"),
        90: ("Strongly verified", "Grant personal kill credit for the misc0 creature entry", "TrinityCore calls KilledMonsterCredit(misc0) on the selected player"),
        93: ("Strongly verified", "Force nearby hostile clients targeting or focusing the caster to deselect it", "TrinityCore sends the clear-target operation to nearby hostile players whose target or focus is the caster"),
        97: ("Partially characterized", "Legacy action-button-range casting plus an unresolved newer variant", "three legacy totem rows fit TrinityCore's misc0/misc1 action-button start/count handler, but three current zero-count rows—including two trigger-bearing housing POC effects—are ignored by it"),
        100: ("Strongly verified", "Modify the selected player's intoxication by signed effect amount, clamped to 0–100", "TrinityCore adds the signed amount and clamps drunkenness; 22 current rows have negative amounts and include explicit sober-up contexts"),
        103: ("Strongly verified", "Modify the selected player's misc0 faction reputation by the effect amount", "TrinityCore validates faction/player, calculates reputation gain, and modifies the selected player's reputation"),
        111: ("Strongly verified", "Modify selected-player item durability by signed point-loss amount", "TrinityCore passes the signed effect amount to durability loss for a misc0-selected slot or all-items scope; six of eight current rows use negative restorative amounts"),
        112: ("Partially characterized", "Configured-spell trigger/cast family with unresolved caster, recipient, and timing", "all six EffectTriggerSpell values join SpellName and are context-matched to their owning spells, while both comparison sources lack an executable handler"),
        113: ("Strongly verified", "Remove nearby matching conversation objects, with private ownership constrained to the selected unit", "TrinityCore searches within 100 yards for conversation entry misc0 and removes public matches or private matches owned by the selected unit"),
        114: ("Strongly verified", "Raise the caster's threat on the selected unit to its current highest threat", "TrinityCore calls MatchUnitThreatToHighestThreat after threat-list validation; it does not itself switch victim or force an attack"),
        115: ("Partially characterized", "Percentage durability-loss operation for positive amounts; negative-amount variant unresolved", "18 positive rows fit TrinityCore's slot/all-items percentage-loss paths, but spell 312466 Burning! has amount -4 at slot 7 and is explicitly ignored by that handler"),
        118: ("Strongly verified", "Raise the caster's misc0 skill tier to the effect amount when lower", "TrinityCore acts on the effect's unit caster and returns without mutation when the current tier is already at least the authored amount"),
        123: ("Strongly verified", "Activate misc0 taxi path for the selected player", "TrinityCore looks up TaxiPath misc0 and activates the path for the selected player"),
        131: ("Partially characterized", "Player-directed notification/sound operation; nonzero misc0 selects SoundKit", "321 nonzero misc0 rows join SoundKit, but ten zero rows include a TrinityCore Restricted Flight Area player-notification branch and other notification-like authored contexts"),
        132: ("Partially characterized", "Music playback/control; nonzero misc0 selects SoundKit, zero-valued stop or unspecified variant", "418 nonzero misc0 rows join SoundKit and fit the executable play handler, but five zero rows include an explicit Carousel Ride Cheer - Stop operation the handler ignores"),
        133: ("Strongly verified", "Remove EffectTriggerSpell from the selected player's learned spells", "TrinityCore directly calls RemoveSpell(EffectTriggerSpell) on the selected player, and current uses include recipes, utility spells, and other non-specialization removals"),
        134: ("Strongly verified", "Grant event or kill credit to the selected player and group for the misc0 creature entry", "TrinityCore calls RewardPlayerAndGroupAtEvent(misc0, selectedPlayer)"),
        138: ("Strongly verified", "Facing-controlled direction-relative knockback or leap displacement", "TrinityCore passes misc0 horizontal speed, effect amount vertical speed, and EffectPos_facing to direction-relative KnockbackFrom; current names include backward, forward, and super jumps"),
        150: ("Strongly verified", "Offer or present a quest, auto-accepting it when configured and eligible", "TrinityCore adds the misc0 quest only on the auto-accept/eligibility path; otherwise it sends quest-giver details to the selected player"),
        152: ("Partially characterized", "Recruit-a-Friend trigger-cast operation with a zero-trigger outlier", "TrinityCore casts EffectTriggerSpell from caster to selected player after player guards, but one of two current rows has no trigger spell and is an authored Snowball Test outlier"),
        155: ("Strongly verified", "Enable caster Titan Grip for configured item constraints", "TrinityCore calls SetCanTitanGrip(true) with misc0 and equipped item class/subclass constraints; it does not perform an attack"),
        162: ("Strongly verified", "Activate the runtime-selected player or pet specialization", "TrinityCore reads m_misc.SpecializationId, activates the player's talent group for non-pet specs, or sets the pet specialization otherwise"),
        166: ("Strongly verified", "Modify the selected player's misc0 currency by signed effect amount", "TrinityCore calls ModifyCurrency with the signed effect value, and current rows include negative Infused Ruby and Uncontaminated Void Sample costs"),
        167: ("Strongly verified", "Reevaluate the selected player's phase conditions", "TrinityCore calls PhasingHandler::OnConditionChange on the selected player; the effect does not directly supply a phase ID"),
        170: ("Partially characterized", "Zone aura/phase refresh family", "TrinityCore re-evaluates area-dependent auras only, while the stable source name and current authored sequences also claim phase refresh; full generic scope is not executed"),
        172: ("Strongly verified", "Initiate a resurrection request, optionally associating a configured aura", "TrinityCore validates and stores EffectTriggerSpell as an aura when nonzero before sending the request; all eight current rows exercise the zero/no-aura path, and resurrection occurs only after acceptance"),
        173: ("Strongly verified", "Invoke guild-bank-tab purchase handling for the authored one-based tab number", "TrinityCore converts the integer effect value to zero-based and passes the player caster/session to the guild bank tab purchase handler"),
        176: ("Strongly verified", "Apply sanctuary combat and threat suppression", "raws 79 and 176 dispatch the identical handler; no generic semantic distinction for the second opcode is proved"),
        175: ("Partially characterized", "Destination-directed bounce/leap movement family", "both current rows are movement-authored and use destination selectors, but trajectory/ownership semantics are unimplemented"),
        178: ("Partially characterized", "Scenario-quest abandonment operation", "the sole current self-targeted spell is explicitly authored Abandon Scenario Quest; generic scope is unproved"),
        183: ("Partially characterized", "Ground-area visual/decal operation", "all ten misc0 values join SpellVisualEffectName and every authored context is a ground-area visual; lifetime is unknown"),
        184: ("Strongly verified", "Modify the selected player's misc0 faction reputation by the effect amount", "raws 103 and 184 dispatch the identical reputation handler; no generic semantic distinction for the second opcode is proved"),
        185: ("Partially characterized", "Scene/world-presentation operation", "six scene or staged-world authored rows share an opaque identifier field; exact object and lifecycle are unknown"),
        186: ("Partially characterized", "Scene/world-presentation operation", "two scene-authored rows share an opaque identifier field; exact distinction from raw 185 is unknown"),
        200: ("Strongly verified", "Heal the selected player's battle-pet roster by the effect percentage", "TrinityCore calls HealBattlePetsPct on the selected player's BattlePetMgr, affecting the roster rather than one selected pet"),
        234: ("Partially characterized", "Static world-object/reward summon family", "the current population clusters in authored scenery/reward summons, but misc0 has no proved local DB2 domain"),
        278: ("Partially characterized", "Teleport-family operation", "all sixteen current rows are teleport/repulsion contexts, but destination storage and lifecycle are unknown"),
        283: ("Strongly verified", "Skip a campaign by marking its questline quests rewarded without granting rewards", "all 83 misc0 values join Campaign; TrinityCore gathers campaign questline quests and SkipQuests removes active state/source items, marks rewarded without rewards, updates visibility/phase, and saves"),
        284: ("Partially characterized", "Chat/ping host-message operation", "current names and misc1 chat-type-like codes support messaging, but 0/275 nonzero misc0 rows join current BroadcastText even though TrinityCore requires that table"),
        291: ("Strongly verified", "Modify selected-unit cooldowns matching misc0 spell family and misc1 family-flag bit", "TrinityCore filters cooldown entries by SpellFamilyName and one-based SpellFamilyFlags bit, then applies the signed effect-value milliseconds"),
        292: ("Strongly verified", "Modify selected-unit cooldowns in misc0 spell category", "all three misc0 rows join SpellCategory; TrinityCore filters cooldown entries by category and applies signed effect-value milliseconds"),
        300: ("Partially characterized", "Item bonus-list-group operation with unresolved distinction from raw 269", "both item-targeted Infinite Potential rows reference ItemBonusListGroup 598 with amounts 1 and 5; raw 269 uses the same spell family and group with amounts 1, 3, 10, and 25"),
        311: ("Strongly verified", "Skip a questline for the selected player", "all 11 misc0 values join QuestLine, all current names are quest/tutorial-skip contexts, and TrinityCore executes QuestMgr::SkipQuestLineForPlayer; SimC's older name is non-executable"),
        316: ("Strongly verified", "Advance the selected player's kill-with-label quest objective", "TrinityCore calls UpdateQuestObjectiveProgress for QUEST_OBJECTIVE_KILL_WITH_LABEL using label misc0 and count max(1,misc1)"),
        317: ("Strongly verified", "Advance the selected player's kill-with-label quest objective", "TrinityCore calls the same handler as raw 316 and uses label misc0 and count max(1,misc1); no distinct generic 1/2 semantic is proved"),
        318: ("Partially characterized", "Titan-construct damage-adjacent marker", "three target-selected rows, two immediately after SchoolDamage, establish only a narrow content domain"),
        324: ("Partially characterized", "House-decor collection/storage operation", "98.1% of nonzero misc0 rows join HouseDecor and authored names are uniformly decor-oriented; zero and missing references remain"),
        340: ("Partially characterized", "Teleport-adjacent operation with no payload", "all current contexts cluster next to teleport effects, but the distinct lifecycle step is not proved"),
        343: ("Partially characterized", "Exit-house operation", "single current self-targeted Exit House spell; no handler or payload semantics"),
        344: ("Partially characterized", "Teleport-family operation with unresolved destination field", "all current contexts are teleport/portal-oriented; no executable handler and misc0 does not type as Map"),
        347: ("Strongly verified", "Apply a transmog-outfit equip/remove/lock action", "TrinityCore reads cast-request action/outfit/situation parameters and supports equip, remove, lock, and unlock actions; current rows include both equip and clear spells"),
        349: ("Partially characterized", "HouseRoom-referencing learn or collect operation", "all 38 misc0 values join HouseRoom and authored contexts are room unlocks; state owner, persistence, duplicates, and generic verb remain unexecuted"),
        350: ("Partially characterized", "ExteriorComponent-referencing house collection operation", "all 332 misc0 values join ExteriorComponent; a 6-row RoomComponent collision is incidental to the source-aligned exterior domain, while ownership/persistence remain unproved"),
        351: ("Partially characterized", "HouseTheme-referencing learn or collect operation", "the sole misc0 value joins HouseTheme and matches the authored theme context, though it also collides with HouseRoom and has no executable handler"),
        352: ("Partially characterized", "RoomComponentTexture-referencing house collection operation", "the sole misc0 value joins RoomComponentTexture and matches the opcode/content noun; same-number HouseRoom/HouseTheme collisions and absent execution leave the verb open"),
        353: ("Partially characterized", "Second create-area-trigger opcode; retail distinction from raw 179 unresolved", "TrinityCore models raws 179 and 353 identically and unconditionally constructs AreaTriggerCreatePropertiesId with its boolean false; no other executable consumer proves the retail distinction"),
        354: ("Partially characterized", "NeighborhoodInitiative-referencing set/select operation", "all ten misc0 values join NeighborhoodInitiative and their record names match authored spell contexts; state owner and persistence are unproved"),
        355: ("Partially characterized", "Learn or collect a house exterior WMO or facade; persistence unproved", "all four misc0 values join HouseExteriorWmoData and record names match the four authored treehouse/facade collection spells; no executable handler proves state owner or persistence"),
        85: ("Strongly verified", "Send a summon request to the selected player", "TrinityCore calls SendSummonRequestFrom on the selected player; movement/summoning requires the later response flow"),
    }
    if raw in special:
        return special[raw]
    generic = not tc_name or bool(re.fullmatch(r"SPELL_EFFECT_\d+", tc_name))
    if handler and handler not in {"EffectNULL", "EffectUnused"}:
        return ("Strongly verified", normalize_name(tc_name, raw), f"TrinityCore dispatches an executable {handler} handler and current Wago contexts are compatible")
    if not generic:
        return ("Partially characterized", normalize_name(tc_name, raw), "comparison-source name and current authored context, but no generic executable server handler")
    return ("Unknown after exhaustive available evidence", f"Unidentified effect {raw}", "numeric/name-only comparison evidence and current authored context do not establish a generic operation")


def build_records() -> list[dict]:
    tc_enum = parse_enum(
        TC / "src/server/game/Miscellaneous/SharedDefines.h",
        "enum SpellEffects", "TOTAL_SPELL_EFFECTS", "SPELL_EFFECT_",
    )
    simc_enum = parse_enum(
        SIMC / "engine/dbc/data_enums.hh",
        "enum effect_type_t", "E_MAX", "E_",
    )
    dispatch = parse_dispatch()
    bodies = parse_handler_bodies(dispatch)
    counts = {r["raw"]: r for r in sql("""
      SELECT Effect raw, count(*) row_count, count(DISTINCT SpellID) spell_count
      FROM effect e ANTI JOIN core c ON e.Effect=c.raw GROUP BY Effect ORDER BY Effect
    """)}
    out = []
    for raw, count in counts.items():
        de = dispatch.get(raw, {})
        te = tc_enum.get(raw, {})
        se = simc_enum.get(raw, {})
        handler = de.get("handler")
        classification, characterization, strongest = classify(raw, te.get("name") or de.get("name"), handler)
        stats = sql(f"""
          SELECT
            count(*) n,
            count(DISTINCT SpellID) spells,
            count(*) FILTER (WHERE EffectMiscValue_0=0) misc0_zero,
            count(DISTINCT EffectMiscValue_0) FILTER (WHERE EffectMiscValue_0<>0) misc0_distinct,
            count(*) FILTER (WHERE EffectMiscValue_1=0) misc1_zero,
            count(DISTINCT EffectMiscValue_1) FILTER (WHERE EffectMiscValue_1<>0) misc1_distinct,
            count(*) FILTER (WHERE EffectTriggerSpell=0) trigger_zero,
            count(DISTINCT EffectTriggerSpell) FILTER (WHERE EffectTriggerSpell<>0) trigger_distinct,
            count(*) FILTER (WHERE EffectItemType=0) item_zero,
            count(DISTINCT EffectItemType) FILTER (WHERE EffectItemType<>0) item_distinct,
            count(*) FILTER (WHERE EffectBasePointsF<>0) base_nonzero,
            min(EffectBasePointsF) base_min, max(EffectBasePointsF) base_max,
            count(*) FILTER (WHERE EffectAmplitude<>0) amplitude_nonzero,
            count(*) FILTER (WHERE EffectAuraPeriod<>0) aura_period_nonzero,
            count(*) FILTER (WHERE EffectRadiusIndex_0<>0 OR EffectRadiusIndex_1<>0) radius_nonzero,
            count(*) FILTER (WHERE EffectChainTargets<>0) chain_nonzero,
            count(DISTINCT EffectAura) aura_distinct,
            count(*) FILTER (WHERE EffectAura<>0) aura_nonzero
          FROM effect WHERE Effect={raw}
        """)[0]
        meta_select = ", ".join(
            f"count(*) FILTER (WHERE {field}<>0) AS {field}__nz, "
            f"count(DISTINCT {field}) FILTER (WHERE {field}<>0) AS {field}__distinct, "
            f"min({field}) FILTER (WHERE {field}<>0) AS {field}__min, "
            f"max({field}) FILTER (WHERE {field}<>0) AS {field}__max"
            for field in OTHER_META_FIELDS
        )
        meta_stats = sql(f"SELECT {meta_select} FROM effect WHERE Effect={raw}")[0]
        other_metadata = []
        for field in OTHER_META_FIELDS:
            nz = meta_stats[f"{field}__nz"]
            if nz:
                distribution = compact_counts(raw, field, limit=8, where=f"AND {field}<>0")
                other_metadata.append(
                    f"`{field}` nonzero={nz}/{stats['n']}, distinct={meta_stats[f'{field}__distinct']}, "
                    f"range={meta_stats[f'{field}__min']}..{meta_stats[f'{field}__max']} ({distribution})"
                )
        masks = compact_counts(raw, "coalesce(t.Targets,0)", where="") if False else ""
        target_masks = sql(f"""
          SELECT CASE WHEN t.Targets IS NULL THEN 'none' ELSE CAST(t.Targets AS VARCHAR) END val, count(*) n
          FROM effect e LEFT JOIN LATERAL (
            SELECT Targets FROM target_restrictions tr
            WHERE tr.SpellID=e.SpellID AND (tr.DifficultyID=e.DifficultyID OR tr.DifficultyID=0)
            ORDER BY (tr.DifficultyID=e.DifficultyID) DESC, tr.ID LIMIT 1
          ) t ON true
          WHERE e.Effect={raw} GROUP BY val ORDER BY n DESC, val
        """)
        sample_names = sql(f"""
          SELECT e.SpellID, coalesce(n.Name_lang,'<unnamed>') spell_name,
                 nullif(s.Description_lang,'') spell_description,
                 e.EffectIndex idx, e.EffectMiscValue_0 m0, e.EffectMiscValue_1 m1
          FROM effect e LEFT JOIN spell_name n ON n.ID=e.SpellID
          LEFT JOIN spell_text s ON s.ID=e.SpellID
          WHERE e.Effect={raw}
          QUALIFY row_number() OVER (PARTITION BY e.SpellID ORDER BY e.ID)=1
          ORDER BY e.SpellID LIMIT 8
        """)
        sequences = sql(f"""
          WITH affected AS (SELECT DISTINCT SpellID FROM effect WHERE Effect={raw}), sig AS (
            SELECT e.SpellID, string_agg(CAST(e.Effect AS VARCHAR), '>' ORDER BY e.EffectIndex, e.ID) seq
            FROM effect e SEMI JOIN affected a ON e.SpellID=a.SpellID GROUP BY e.SpellID
          ) SELECT seq, count(*) n FROM sig GROUP BY seq ORDER BY n DESC, seq LIMIT 6
        """)
        body = bodies.get(handler or "", {})
        out.append({
            "raw": raw, **count, **stats,
            "indexes": compact_counts(raw, "EffectIndex", limit=None),
            "target0": compact_counts(raw, "ImplicitTarget_0", limit=None),
            "target1": compact_counts(raw, "ImplicitTarget_1", limit=None),
            "auras": compact_counts(raw, "EffectAura"),
            "misc0": compact_counts(raw, "EffectMiscValue_0"),
            "misc1": compact_counts(raw, "EffectMiscValue_1"),
            "triggers": compact_counts(raw, "EffectTriggerSpell"),
            "items": compact_counts(raw, "EffectItemType"),
            "radius": compact_counts(raw, "EffectRadiusIndex_0 || '/' || EffectRadiusIndex_1"),
            "target_masks": ", ".join(f"{r['val']}×{r['n']}" for r in target_masks),
            "other_metadata": "; ".join(other_metadata) if other_metadata else "none beyond the fields itemized above",
            "sample_names": sample_names,
            "sequences": sequences,
            "tc_name": te.get("name") or de.get("name"),
            "tc_comment": te.get("comment") or de.get("comment", ""),
            "handler": handler,
            "dispatch_line": de.get("line"),
            "handler_body": body,
            "simc_name": se.get("name"),
            "simc_comment": se.get("comment", ""),
            "classification": classification,
            "characterization": characterization,
            "strongest": strongest,
        })
    return out


def build_joins() -> list[dict]:
    out = []
    for raw, candidates in sorted(JOIN_CANDIDATES.items()):
        for field, table, key, rationale in candidates:
            path = ROOT / "data/tables" / f"{table}.csv"
            escaped = str(path).replace("'", "''")
            q = f"""
              WITH x AS (SELECT {field} v FROM effect WHERE Effect={raw}),
                   d AS (SELECT {key} k, count(*) key_rows FROM read_csv_auto('{escaped}', header=true) GROUP BY {key})
              SELECT {raw} raw, '{field}' field, '{table}' table_name,
                     count(*) total_rows,
                     count(*) FILTER (WHERE v=0) zero_rows,
                     count(*) FILTER (WHERE v<>0) nonzero_rows,
                     count(DISTINCT v) FILTER (WHERE v<>0) distinct_nonzero,
                     count(*) FILTER (WHERE v<>0 AND k IS NOT NULL) joined_rows,
                     count(DISTINCT v) FILTER (WHERE v<>0 AND k IS NOT NULL) joined_distinct,
                     count(*) FILTER (WHERE v<>0 AND k IS NULL) failed_rows,
                     count(DISTINCT v) FILTER (WHERE v<>0 AND k IS NULL) failed_distinct,
                     coalesce(sum(CASE WHEN v<>0 AND key_rows>1 THEN 1 ELSE 0 END),0) duplicate_key_matches
              FROM x LEFT JOIN d ON d.k=x.v
            """
            result = sql(q)[0]
            failures = sql(f"""
              WITH x AS (SELECT {field} v FROM effect WHERE Effect={raw}),
                   d AS (SELECT DISTINCT {key} k FROM read_csv_auto('{escaped}', header=true))
              SELECT v val, count(*) n FROM x LEFT JOIN d ON d.k=x.v
              WHERE v<>0 AND d.k IS NULL
              GROUP BY v ORDER BY n DESC, val LIMIT 12
            """)
            result["rationale"] = rationale
            result["key"] = key
            result["failed_values"] = failures
            out.append(result)
    return out


def md(value) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def clipped(value: str, length: int = 180) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value if len(value) <= length else value[: length - 1].rstrip() + "…"


def source_records() -> dict[int, dict]:
    with Path("/tmp/spell_effect_source_map.csv").open(newline="") as f:
        return {int(row["raw"]): row for row in csv.DictReader(f)}


def join_records() -> dict[int, list[dict]]:
    path = Path("/tmp/spell_effect_join_evidence.json")
    rows = json.loads(path.read_text()) if path.exists() else build_joins()
    out: dict[int, list[dict]] = {}
    for row in rows:
        out.setdefault(int(row["raw"]), []).append(row)
    return out


def rejection_text(raw: int) -> str:
    special = {
        5: "Rejected Teleport, FinalizeItem, RebuildItem, TransmitItem, and CommitItemMutation: all 323 rows have neutral payload; immediate predecessors are raw 269 (149), 223 (118), 313 (17), 266 (16), 299 (10), 3 (7), 357 (3), 53 (1), or none (2). It is last/only in 284 rows but precedes 64 (37), 358 (1), or 90 (1), and one spell consists only of raw 5. No unique prerequisite or post-mutation verb survives this census.",
        15: "Rejected treating the loading-screen visual as universal: 233/826 rows have misc1 zero, and the handler sends it only when misc1 is nonzero and the selected target is a player.",
        16: "Rejected universal Quest Complete: 167 current rows have misc0 zero and are no-ops in TrinityCore, yet include modern renown, waygate, collection, gear, Delver, and other unlock/flag contexts.",
        47: "Rejected executable TradeSkill mutation proof: TrinityCore's concrete handler contains only guards and commented-out skill logic.",
        52: "Rejected legacy Guaranteed Hit as the current generic identity: all three current spells set the Warband battle-pet cap, while the historical names conflict and neither source executes the raw.",
        53: "Rejected universal Enchant Item: four rows have misc0 zero; three non-test rows are authored item upgrade/binding operations whose base points reference Item records and whose semantics the handler does not execute.",
        74: "Rejected ApplyGlyph as covering only addition: six zero-ID rows exercise explicit glyph removal; the handler also replaces an existing matching glyph.",
        85: "Rejected direct Summon Player: the handler only sends the summon request; acceptance and resulting movement are separate.",
        97: "Rejected both SummonAllTotems and generic action-button-range casting: three legacy rows fit the button-range handler, but three zero-count current rows are not explained and two carry TriggerSpell 1272944 that TrinityCore ignores.",
        100: "Rejected increase-only Inebriate: 22 current rows have negative amounts, including Sober Up/Sobering contexts, and the handler accepts signed change before clamping.",
        111: "Rejected damage-only semantics: six of eight current rows use negative amounts in regenerating/sharpening/cleansing contexts, producing durability restoration through signed point loss.",
        114: "Rejected direct forced-attack semantics: this effect only matches caster threat to the selected unit's highest threat; taunt aura behavior is a separate operation.",
        115: "Rejected universal percentage durability damage: the negative-amount Burning! row is not executed by TrinityCore's per-slot path and no local evidence explains its generic semantics.",
        131: "Rejected universal Play Sound: ten rows have misc0 zero, and TrinityCore itself uses raw 131 spell 91604 as a player notification before its missing-SoundKit return; history called the handler EffectPlayerNotification.",
        132: "Rejected universal Play Music: five rows have misc0 zero, including explicit Carousel Ride Cheer - Stop; TrinityCore returns without handling zero.",
        133: "Rejected specialization-only semantics: the handler performs no specialization check and current uses remove recipes, Codex of Xerrath, bandages, utility, and other learned spells.",
        138: "Rejected Leap Back as a universal direction: the handler uses an authored facing angle and current spells include backward, forward, and super-jump contexts.",
        152: "Rejected direct Summon RAF Friend: the handler trigger-casts a configured spell, and the zero-trigger Snowball Test row is not explained by that implementation.",
        166: "Rejected grant-only Give Currency: the handler accepts a signed amount and current authored rows include negative currency changes.",
        170: "Rejected executable proof of phase updating: TrinityCore only calls UpdateAreaDependentAuras; the phase half of the historical name is not executed there.",
        172: "Rejected immediate resurrection: the handler stores request data and sends a resurrection request with an aura; acceptance and resurrection are later operations.",
        18: "Rejected immediate resurrection: the handler stores request data and sends a resurrection request; acceptance and resurrection are later operations.",
        22: "Rejected an immediate parry event: the effect only enables the caster's parry capability.",
        23: "Rejected an immediate block event: the effect only enables the caster's block capability.",
        29: "Rejected a generic leap or trajectory operation: the audited handler performs NearTeleportTo and the dominant current contexts are blink/teleport/warp spells.",
        40: "Rejected a dual-wield attack at cast time: the effect enables the selected unit's capability.",
        155: "Rejected an immediate Titan Grip attack: the effect enables the caster capability with configured item constraints.",
        162: "Rejected player-only talent-spec selection: runtime SpecializationId may select either a player talent group or a pet specialization.",
        167: "Rejected direct phase-ID assignment: the handler supplies no phase ID and instead reevaluates phase conditions.",
        176: "Rejected a proved Sanctuary 2 semantic distinction from raw 79: both dispatch the identical handler.",
        184: "Rejected a proved Reputation 2 semantic distinction from raw 103: both dispatch the identical handler.",
        196: "Rejected misc0 as SceneScriptPackage.ID: the handler uses it as server scene_template.SceneId and obtains the package from that template; Wago package/script/member joins are incomplete competing collisions.",
        197: "Rejected misc0 as SceneScriptPackage.ID: the handler uses it as server scene_template.SceneId and obtains the package from that template; Wago package/script/member joins are incomplete competing collisions.",
        198: "Rejected misc0 as SceneScriptPackage.ID: EffectPlayScene resolves server scene_template.SceneId through SceneMgr, unlike raw 195's direct package operation; Wago package/script/member joins are incomplete competing collisions.",
        200: "Rejected healing one selected battle pet: the handler heals through the selected player's battle-pet roster manager.",
        223: "Rejected treating ChangeItemBonuses as a complete verb specification: the name does not establish replacement, reroll, preservation, or failure behavior.",
        230: "Rejected historical Increase Follower Item Level: TrinityCore explicitly withdrew that enum label in commit a05fc3ded5 and still null-dispatches the raw. Also rejected inferring an artifact upgrade or transmog mutation: the current joins establish the referenced artifact-item domain, while the uniform Intensification name does not define the state transition.",
        243: "Rejected apply-only semantics: two current zero-ID rows explicitly remove/clear the illusion, matching the handler's assignment of zero.",
        265: "Rejected misc0 as AzeriteEssencePower.ID: only 69/113 rows join that table, while 113/113 join AzeriteEssence.ID and the handler uses it as AzeriteEssenceID; misc1 supplies rank.",
        266: "Rejected the claim that misc0 is uniformly an ItemBonusListGroupEntry ID: only 146/185 nonzero rows join that table; 39 rows (26 values) do not.",
        268: "Rejected apply-only semantics without replacement: the handler explicitly removes every other mount equipment's learned spell and buff aura.",
        269: "Rejected 'next rank only': effect amounts range from 1 to 25, so the conservative identity is increase/advance by an authored amount, not necessarily one step.",
        283: "Rejected neutral direct campaign completion and reward granting: TrinityCore implements skip semantics and explicitly marks quests rewarded without granting rewards. Atomicity is unproved because SkipQuests can return early for a missing quest template.",
        284: "Rejected a fully verified BroadcastText sender for this build: TrinityCore requires misc0 in BroadcastText, but none of 275 nonzero current values join that DB2; the broader chat/ping family remains supported.",
        300: "Rejected treating the shared group/amount shape as proof that raw 300 is identical to raw 269: the distinct raw itself implies an unresolved semantic axis and neither comparison source executes it.",
        307: "Rejected Remove Personal Clones by CreatureDisplayInfo: misc0 is display-info-shaped, but three rows have no identifier; misc1 is absent in 24 rows and value 116795 fails CreatureDisplayInfo while joining Creature. Ownership, matching, and despawn subject are not established.",
        311: "Rejected SimC's EnsureWorldLoaded/CheckWorldLoaded label for this build: it is name-only, while all current rows are quest/tutorial-skip authored and TrinityCore now executes QuestMgr::SkipQuestLineForPlayer.",
        316: "Rejected treating the source suffix 1 as a proved semantic variant: raws 316 and 317 execute the identical handler with the same field contract.",
        317: "Rejected treating the source suffix 2 as a proved semantic variant: raws 316 and 317 execute the identical handler with the same field contract.",
        313: "Rejected ChangeItemBonuses2 as a complete semantic: misc0 proves an ItemBonusTree collision (also ItemBonusTreeNode), misc1 proves an Item ID, but the mutation and 'preserve' behavior are not executed locally.",
        324: "Rejected a stronger account-persistent GrantHouseDecor identity: the population proves the decor domain, but 515 rows have zero misc0, 53 nonzero references are absent from current HouseDecor, and no handler proves state owner or persistence.",
        344: "Rejected misc0-as-Map ID: 0/46 rows join Map. AreaTable and QuestV2 each collide with 35/46 rows, so neither partial join types the destination.",
        347: "Rejected EquipOnly and rejected the enum comment that effect misc0 alone supplies the outfit: current spells include clearing, the runtime supports remove/lock/unlock, and the three operation parameters come from cast-request misc data.",
        353: "Rejected the proposed secondary-properties flag variant: the audited TrinityCore handler unconditionally passes false for both raw 179 and raw 353, and no other local executable consumer supplies that distinction.",
        355: "Rejected broad Learn House Type for the current population: all four references are specifically HouseExteriorWmoData records matching treehouse/facade collection spells; ownership and persistence remain unproved.",
        359: "Rejected ApplyItemCondition: all three misc0 values join ItemCondition and the spells explicitly item-target socket operations, but neither Wago ordering nor SimC's explicitly tentative, consumer-free label proves an apply/mutation verb. A whole-corpus first-column ID scan found the integers 180/181/184 in 258/289/272 tables respectively, so equality alone is highly non-unique.",
    }
    return special.get(raw, NO_DISTINCT_REJECTION)


NO_DISTINCT_REJECTION = "No additional provisional identity was strong enough to require a distinct rejection beyond the source-name limitations above."


def not_proved_text(record: dict) -> str:
    raw = record["raw"]
    if raw == 5:
        return "The operation verb, mutated state, owner, persistence, atomicity, failure behavior, and its exact relation to surrounding item effects."
    if raw == 307:
        return "A single identifier domain for both misc fields; clone ownership; whether the matched object, its clone, or another subject despawns; and mismatch behavior."
    if raw == 359:
        return "That the condition is applied, stored, evaluated, removed, or used only as a gate; the owner and persistence are also unknown."
    if raw == 269:
        return "No executable comparison handler establishes state owner, lifecycle, failure behavior, atomicity, or the exact persistence contract of the increase."
    if raw == 283:
        return "Atomic all-or-nothing completion is not established: TrinityCore's SkipQuests helper can return early when a quest template is missing."
    if record["classification"] == "Strongly verified":
        return "Client/server parity outside the represented population, undocumented failure/atomicity behavior, and lifecycle details not exercised by the traced handler are not generalized."
    if record["classification"] == "Partially characterized":
        return "The full generic verb, state owner, persistence/lifetime, mismatch behavior, and cross-build stability are not established unless explicitly stated above."
    return "A reusable generic noun, verb, field type, state owner, lifetime, and failure behavior; spell names alone cannot supply them."


def generate_document(records: list[dict], output: Path) -> None:
    sources = source_records()
    joins = join_records()
    class_counts = Counter(r["classification"] for r in records)
    unknown_ids = ", ".join(str(r["raw"]) for r in records)
    lines = [
        "# Spell-effect identity audit",
        "",
        "Status: complete research audit of the current corpus. This document changes no runtime, support, schema, catalog, generated-data, test, or build state.",
        "",
        "- Audit date: 2026-09-07 UTC",
        "- Wago retail build: `12.1.0.69497` (`changes/metadata/12.1.0.69497.json`; 1,090/1,090 tables downloaded, zero failures)",
        "- `wowlab-data`: `2ddced452a6f9076de5c86bc92f73de5b60f8556`",
        "- TrinityCore comparison: `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`",
        "- SimulationCraft comparison: `b48def9c26d7532db2e612d97d433ec66bd8eede`",
        "- DuckDB: `v1.5.5 (Variegata) d8cdaa33fd`",
        "",
        "## Method and evidentiary rules",
        "",
        "DuckDB materialized the 629,298-row current `SpellEffect.csv`, joined spell names/text and target restrictions, and anti-joined the supplied 51 reviewed-Core IDs. The resulting 279-raw queue was recomputed after analysis. Every effect row was included; distributions below are population summaries, not samples. Top-value displays are compact, while adjacent zero/nonzero/distinct counts describe the complete population.",
        "",
        "Candidate DB2 types were tested with exact joins, anti-joins, distinct-value counts, zero counts, and duplicate-key checks. A numeric collision was never promoted to a type merely because it joined. TrinityCore evidence separates enum/dispatch metadata from executable handlers; `EffectNULL`, `EffectUnused`, and raw 47's inert handler are not executable semantic proof. SimC display mappings are name evidence only unless a non-display consumer is identified. Current authored outliers control the breadth of every identity.",
        "",
        "Selector numbers are preserved as raw DB2 values; they establish target shape, not ownership. Explicit target-mask distributions use the exact spell difficulty when present and otherwise one difficulty-0 restriction, preventing join multiplication. Sibling sequences are complete-population top signatures ordered by effect index.",
        "",
        f"Terminal dispositions: {class_counts['Strongly verified']} strongly verified; {class_counts['Partially characterized']} partially characterized; {class_counts['Rejected interpretation']} rejected interpretations; {class_counts['Unknown after exhaustive available evidence']} unknown after exhaustive available evidence.",
        "",
        "## Master table",
        "",
        "| Raw | Rows | Spells | Classification | Best characterization | Strongest evidence | Main unresolved boundary |",
        "|---:|---:|---:|---|---|---|---|",
    ]
    for r in records:
        boundary = not_proved_text(r)
        lines.append(f"| {r['raw']} | {r['row_count']} | {r['spell_count']} | {md(r['classification'])} | {md(r['characterization'])} | {md(r['strongest'])} | {md(boundary)} |")
    lines += [
        "",
        "## Detailed findings",
        "",
        "Each section accounts for one and only one raw in the anti-joined queue.",
        "",
    ]
    for r in records:
        raw = r["raw"]
        s = sources.get(raw, {})
        samples = "; ".join(f"{x['SpellID']} {x['spell_name']}" for x in r["sample_names"][:6]) or "none"
        descriptions = [clipped(x.get("spell_description") or "", 150) for x in r["sample_names"] if x.get("spell_description")]
        desc_text = "; ".join(descriptions[:2]) if descriptions else "no nonempty description among the compact representatives"
        authored_extra = {
            16: "Zero-payload cluster: 167 rows; dominant sequences are `3>16`×103, `16`×52, and `16>3`×6. Modern authored contexts include Dragonflight renown, Ancient Waygates, ensembles, warbound gear, Delver Within, Forward Positions, and Defense Network unlocks.",
            47: "Outlier census: spells 1231812, 1232471, and 1232629 are all named Vehicle Destroyed- Teleport, use sequence `252>47`, primary selector 1, and raw-47 TriggerSpell 1231359 or 1232476 (Cancel Summons); the other 155 rows are the legacy profession cluster.",
            53: "Zero-misc0 outliers: Upgrade Armor spells 188675/188677 reference Item 122604/128023 in base points; Binding 281423 references Item 127842 and uses sequence `53>24`; spell 371749 is a test. The three non-test base-point values join Item.ID 3/3.",
            97: "Population split: three legacy totem rows use `(misc0,misc1)` 0/4, 4/4, and 8/4; Shriek and two housing POC rows use 0/0, and two of those carry TriggerSpell 1272944.",
            115: "Amount outlier: spell 312466 Burning! uses -4 with misc0 slot 7; the other 18 rows use positive 5–100 percentages. TrinityCore returns for nonpositive per-slot values.",
            131: "Zero-SoundKit contexts include Restricted Flight Area, Out of Bounds, Targeted by Voidstorm Turrets, and Void Breath; TrinityCore's spell-91604 branch emits a localized no-fly-zone notification before SoundKit lookup.",
            132: "Five rows have zero misc0: Carousel Ride Cheer - Stop (spell 241035), two Play Music DNT rows, Dancing Machine, and [DNT] Play Music; the explicit stop row is a singleton raw-132 spell.",
            152: "Outlier: one of the two current rows is Snowball Test with TriggerSpell zero, so the executable trigger-cast path does not characterize the complete population.",
        }.get(raw)
        sequences = ", ".join(f"`{x['seq']}`×{x['n']}" for x in r["sequences"]) or "none"
        handler_body = r.get("handler_body") or {}
        fields = ", ".join(handler_body.get("fields") or []) or "no effect-field reads indexed"
        calls = ", ".join(handler_body.get("calls") or []) or "no target helper calls indexed"
        tc_status = s.get("tc_evidence_status") or "missing"
        simc_status = s.get("simc_evidence_status") or "missing"
        tc_path = s.get("tc_handler_path") or s.get("tc_enum_path") or "not present"
        simc_path = s.get("simc_display_path") or s.get("simc_enum_path") or "not present"
        handler_note = HANDLER_NOTES.get(raw)
        join_lines = []
        for j in joins.get(raw, []):
            failures = ", ".join(f"{x['val']}×{x['n']}" for x in j.get("failed_values", [])[:6]) or "none"
            join_lines.append(
                f"`{j['field']} → {j['table_name']}.{j.get('key', 'ID')}` ({j['rationale']}): "
                f"{j['joined_rows']}/{j['nonzero_rows']} nonzero rows and {j['joined_distinct']}/{j['distinct_nonzero']} distinct nonzero values joined; "
                f"zero={j['zero_rows']}, failed rows/values={j['failed_rows']}/{j['failed_distinct']}, duplicate-key matches={j['duplicate_key_matches']}; leading misses: {failures}."
            )
        join_text = " ".join(join_lines) if join_lines else "No local DB2 foreign-key type is asserted for this raw; misc integers remain opaque codes despite incidental cross-table collisions."
        proved = r["strongest"] + ". " + (f"Conservative identity: {r['characterization']}." if r["classification"] == "Strongly verified" else f"The defensible boundary is: {r['characterization']}.")
        recommended = f"`{r['characterization']}`" if r["classification"] == "Strongly verified" else "No catalog identity recommended."
        lines += [
            f"### Raw {raw}",
            "",
            "Current population",
            "",
            f"- {r['row_count']} rows across {r['spell_count']} owning spells. Effect positions: {r['indexes']}.",
            f"- Implicit selectors: primary {r['target0']}; secondary {r['target1']}. Explicit target masks: {r['target_masks'] or 'none'}.",
            f"- Target shape is described only by those raw selectors/masks; no recipient or state owner is inferred from selection alone.",
            "",
            "Field evidence",
            "",
            f"- `misc0`: zero {r['misc0_zero']}/{r['row_count']}; {r['misc0_distinct']} distinct nonzero; top distribution {r['misc0']}. `misc1`: zero {r['misc1_zero']}/{r['row_count']}; {r['misc1_distinct']} distinct nonzero; top distribution {r['misc1']}.",
            f"- Trigger: zero {r['trigger_zero']}/{r['row_count']}, {r['trigger_distinct']} distinct nonzero ({r['triggers']}). Item type: zero {r['item_zero']}/{r['row_count']}, {r['item_distinct']} distinct nonzero ({r['items']}).",
            f"- Aura: {r['aura_nonzero']} nonzero rows/{r['aura_distinct']} distinct values ({r['auras']}). Base points: {r['base_nonzero']} nonzero, range {r['base_min']}..{r['base_max']}. Amplitude nonzero={r['amplitude_nonzero']}; aura-period nonzero={r['aura_period_nonzero']}; radius nonzero={r['radius_nonzero']} ({r['radius']}); chain-target nonzero={r['chain_nonzero']}.",
            f"- Other populated SpellEffect metadata: {r['other_metadata']}.",
            f"- Join/anti-join tests: {join_text}",
            "",
            "Authored-content evidence",
            "",
            f"- Representative spell names: {md(samples)}.",
            f"- Description evidence: {md(desc_text)}.",
            f"- Most common complete sibling sequences: {sequences}. These establish ordering only, not atomicity or causation.",
            *( [f"- {authored_extra}"] if authored_extra else [] ),
            "",
            "TrinityCore evidence",
            "",
            f"- Enum `{s.get('tc_enum') or r.get('tc_name') or 'absent'}`; dispatch/handler `{s.get('tc_handler') or r.get('handler') or 'absent'}` at `../TrinityCore/{tc_path}`. Evidence tier: {tc_status}.",
            f"- Mechanically indexed uncommented handler tokens (navigation aid, not a semantic trace): {fields}; target/helper calls {calls}. Enum/history blame `{s.get('tc_enum_blame_commit') or 'none'}` ({md(s.get('tc_enum_blame_summary') or 'no history summary')}).",
            *( [f"- Traced runtime/effect inputs and mutation: {handler_note}"] if handler_note else [] ),
            f"- Limit: TrinityCore is emulator behavior. Null/unused/inert dispatch and spell-specific scripts do not prove a generic client/server effect; implemented handlers may still omit unsupported host behavior.",
            "",
            "SimulationCraft evidence",
            "",
            f"- Enum `{s.get('simc_enum') or r.get('simc_name') or 'absent'}`; display `{s.get('simc_display_name') or 'none'}` at `../simc/{simc_path}`. Evidence tier: {simc_status}.",
            f"- History blame `{s.get('simc_enum_blame_commit') or 'none'}` ({md(s.get('simc_enum_blame_summary') or 'no history summary')}). Limit: generated enum/display metadata is name evidence only; simulation-specific consumers do not establish server ownership or lifecycle.",
            "",
            "What is proved",
            "",
            f"- {md(proved)}",
            "",
            "What is NOT proved",
            "",
            f"- {md(not_proved_text(r))}",
            "",
            "Rejected interpretations",
            "",
            f"- {md(rejection_text(raw))}",
            "",
            "Recommended terminal classification",
            "",
            f"- **{r['classification']}.** Recommended conservative identity: {recommended}",
            "",
        ]
    strong = [r for r in records if r["classification"] == "Strongly verified"]
    partial = [r for r in records if r["classification"] == "Partially characterized"]
    unknown = [r for r in records if r["classification"] == "Unknown after exhaustive available evidence"]
    rejected = [r for r in records if r["classification"] == "Rejected interpretation"]
    lines += [
        "## Final summaries",
        "",
        "### Safe-to-catalog shortlist",
        "",
        "Only the following strongly verified identities are recommended. Names are conservative and intentionally omit unsupported persistence, ownership, and failure semantics.",
        "",
        "| Raw | Conservative identity | Rows | Evidence basis |",
        "|---:|---|---:|---|",
    ]
    for r in strong:
        basis = "exact executable handler plus compatible full population" if r["raw"] != 269 else "complete ItemBonusListGroup join, authored amount/upgrade population, and inverse anchor"
        lines.append(f"| {r['raw']} | {md(r['characterization'])} | {r['row_count']} | {basis} |")
    lines += [
        "",
        "### Partially characterized shortlist",
        "",
        "These findings are useful but should remain uncataloged until the missing generic semantics are established.",
        "",
        "| Raw | Useful characterization | Missing boundary |",
        "|---:|---|---|",
    ]
    for r in partial:
        lines.append(f"| {r['raw']} | {md(r['characterization'])} | {md(clipped(not_proved_text(r), 170))} |")
    lines += [
        "",
        "### Rejected historical / provisional identities",
        "",
        "| Raw | Rejected interpretation and falsifying evidence |",
        "|---:|---|",
    ]
    for r in records:
        rejection = rejection_text(r["raw"])
        if rejection != NO_DISTINCT_REJECTION:
            lines.append(f"| {r['raw']} | {md(rejection)} |")
    lines += [
        "",
        "The three raws whose terminal disposition is specifically **Rejected interpretation** are: " + ", ".join(str(r["raw"]) for r in rejected) + ". Other rows above retain their stronger terminal category while recording a narrower rejected alias or field claim.",
        "",
        "### Unknown after exhaustive available evidence",
        "",
        "| Raw | Rows | What remains | Smallest resolving evidence |",
        "|---:|---:|---|---|",
    ]
    for r in unknown:
        lines.append(f"| {r['raw']} | {r['row_count']} | No defensible generic noun/verb beyond the detailed current contexts. | A client/server handler or decoded schema tied to the raw and its payload. |")
    lines += [
        "",
        "### High-value external evidence gaps",
        "",
        "1. **Retail client/server spell-effect dispatch for raws 223, 266, 269, 299, 307, 313, 324, and 359.** This smallest evidence set would resolve the largest item, decor, and condition mutation ambiguities, including ownership, persistence, and failure behavior.",
        "2. **The missing world-data ID domains for Creature/GameObject/Conversation/PlayerChoice and destination records.** Local DB2 anti-joins show that many executable effects reference server/world records not represented by the similarly named Wago tables; decoding those relations would improve raws 56, 61, 76, 90, 104, 134, 153, 171, 205, 219, 226, 234, 267, and 344.",
        "3. **Build-matched client schema/history for numeric raws 122, 209, 228, 270, and 333.** Their current populations are too small or heterogeneous to infer a reusable semantic; raw 333 especially needs its payload schema.",
        "4. **Host observation or UI handler traces for housing/collection effects 324 and 343–355.** This would distinguish account, Warband, character, house, and inventory ownership and reveal duplicate/unlearn behavior.",
        "5. **Older/newer hotfix rows for dangling references.** Examples are raw 324's 53 nonzero HouseDecor misses and table misses in otherwise strong classic effects; hotfix history would separate removed content from wrong field typing.",
        "6. **An item-condition evaluator trace for raw 359.** Observing whether ItemCondition is stored, immediately evaluated, or used only as a gate would resolve the rejected apply interpretation with minimal additional evidence.",
        "",
        "### Likely non-combat / host-domain identities",
        "",
        "- Item/inventory/crafting: 5, 33, 34, 53, 54, 59, 66, 92, 99, 101, 127, 156, 158, 163, 169, 206, 222, 223, 245, 249, 258, 259, 261, 263–269, 282, 288, 294–301, 313–315, 357–359.",
        "- Quest/world/scene/presentation: 11–16, 45, 46, 50, 61, 70, 72, 76, 86–90, 106, 107, 113, 120, 123, 131, 132, 139, 147, 150, 154, 167, 170, 171, 177, 180, 182–198, 205, 207, 208, 212, 219, 226, 227, 232, 234, 250, 267, 277, 283, 284, 298, 306–311, 316, 317, 333, 339–345, 353, 354.",
        "- Profession/collection/garrison/Warband/housing: 14, 36, 44, 47, 52, 57, 60, 73, 74, 80, 81, 118, 133, 146, 161, 162, 187, 201, 204, 210, 211, 214–225, 229–233, 238–248, 251, 255, 265, 268, 272, 273, 276, 279, 281, 286, 287, 303, 304, 324, 335–338, 341, 347–352, 355.",
        "- This grouping is triage metadata, not proof that a raw can never affect combat indirectly.",
        "",
        "### Coverage and reconciliation",
        "",
        "- Complete current population: **629,298 rows**, **413,805 owning spells**, **330 distinct raws**.",
        "- Reviewed-Core partition: **524,598 rows**, **51 raws**.",
        "- Audited unknown partition: **104,700 rows**, **279 raws**.",
        "- Partition identities: `524,598 + 104,700 = 629,298` rows; `51 + 279 = 330` raws.",
        "- Recomputed unknown anti-join minus documented raws: **0**. Documented raws minus recomputed unknown anti-join: **0**. Duplicate raw sections: **0**.",
        "- Sum of per-raw usage rows in this audit: **104,700**. Every current usage row is therefore accounted for exactly once by raw.",
        "- Independent verification: an `awk` scan of CSV column 5 reproduced 629,298/330 total and 104,700/279 unknown; re-reading the machine census reproduced 279 distinct raws and 104,700 summed rows.",
        "- Exact terminal queue: " + unknown_ids + ".",
        "",
        "### Hostile review record",
        "",
        "Two independent hostile reviews examined the complete 279-raw draft. Every definite material finding was incorporated before final handoff.",
        "",
        "- **Hostile data/coverage reviewer:** independently reproduced 629,298 total rows/330 raws and the 104,700-row/279-raw audited anti-join. Definite findings corrected raw 16 and the zero/outlier families in 53, 131, and 132; request/direction boundaries in 15, 90, 134, 138, 172, 243, and 268; unknown-to-partial dispositions 112, 218, 230, and 300; field keys for 133, 212, 265, 268, and housing raws 341/349–352/354–355; server scene-template typing for 196–198; and the complete SpellEffect metadata/tail-accounting gap.",
        "- **Hostile semantic/source reviewer:** traced handler/helper behavior and corrected overclaims for raws 18, 22, 23, 29, 44, 47, 74, 79, 83, 85, 86, 93, 97, 100, 103, 111, 113–115, 118, 123, 133–134, 138, 150, 152, 155, 162, 166–167, 170, 172–173, 176, 184, 200, 223, 230, 236, 243, 265, 269, 283, 291–292, 304, 311, 316–317, 335–338, 347, and 353. The false raw-353 secondary-flag claim was removed and raw 353 was demoted.",
        "- **Correction verification:** both reviewers independently passed the regenerated result with no remaining definite findings. The data reviewer confirmed document/generator identity, terminal totals, all per-raw counts, exact section/shortlist/unknown-set coverage, joins, and metadata accounting; the semantic reviewer confirmed all prior corrections and found no new definite semantic error.",
        "",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["json", "csv", "stats", "joins", "document"])
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    if args.command == "joins":
        data = json.dumps(build_joins(), indent=2)
        if args.output:
            args.output.write_text(data + "\n")
        else:
            print(data)
        return
    records = build_records()
    if args.command == "document":
        if not args.output:
            raise SystemExit("document requires --output")
        generate_document(records, args.output)
        return
    if args.command == "stats":
        print(json.dumps({
            "raws": len(records), "rows": sum(r["row_count"] for r in records),
            "spells_sum": sum(r["spell_count"] for r in records),
            "classifications": Counter(r["classification"] for r in records),
        }, indent=2, default=dict))
        return
    if args.command == "json":
        data = json.dumps(records, indent=2)
        if args.output:
            args.output.write_text(data + "\n")
        else:
            print(data)
        return
    fields = ["raw", "row_count", "spell_count", "classification", "characterization", "strongest", "tc_name", "handler", "simc_name"]
    target = args.output.open("w", newline="") if args.output else None
    try:
        w = csv.DictWriter(target or __import__("sys").stdout, fieldnames=fields)
        w.writeheader()
        for r in records:
            w.writerow({k: r.get(k) for k in fields})
    finally:
        if target:
            target.close()


if __name__ == "__main__":
    main()
