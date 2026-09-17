"""Summon resolution and inheritance classification (Track A3, report section 7).

* :func:`resolve_summon` maps a 12.1 spell to the Trinity unit class that its
  summon effect produces, using Trinity's own routing
  (``Spell::EffectSummonPet`` SpellEffects.cpp:2656-2740,
  ``Spell::EffectSummonType`` SpellEffects.cpp:1867-2085,
  ``Spell::SummonGuardian`` SpellEffects.cpp:5013-5068 and the unit-mask switch
  of ``Map::SummonCreature`` Object.cpp:1193-1263).
* ``inheritance.json``: per unit class x stat classification (creation
  snapshot / application snapshot / dynamic owner lookup / explicit
  recalculation / independent pet value / legacy-only / unresolved), the owner
  facts each consumer reads, the snapshot-side pet-scaling spell census kept
  separate from the ``Guardian::Update*`` code, and the witness resolutions with
  the oracle run under a fixed owner fact set.

The per-rule routing table is Trinity's; the population census of how many
12.1 spells fall into each rule is Track A's (``census.json``) and is not
recomputed here.
"""

from __future__ import annotations

import argparse
import csv
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from controlled_units import CORPORA, TABLES, SourceError
from controlled_units import stats as st

TC = st.TC

SPELL_EFFECT_SUMMON = 28
SPELL_EFFECT_SUMMON_PET = 56
TRIGGER_EFFECTS = {64: "TRIGGER_SPELL", 142: "TRIGGER_SPELL_WITH_VALUE", 140: "FORCE_CAST", 32: "TRIGGER_MISSILE",
                   151: "TRIGGER_SPELL_2", 3: "DUMMY"}

FLAG_JOIN_SUMMONER_SPAWN_GROUP = 0x200  # DBCEnums.h:2766 (marked NYI, but consumed by the two switches below)
FLAG_USE_CREATURE_LEVEL = 0x100  # DBCEnums.h:2765
FLAG_USE_DEMON_TIMEOUT = 0x40  # DBCEnums.h:2763

CONTROL_NAMES = {0: "WILD", 1: "ALLY", 2: "PET", 3: "PUPPET", 4: "POSSESSED_VEHICLE", 5: "VEHICLE"}  # SharedDefines.h:6656-6664
TITLE_NAMES = {0: "None", 1: "Pet", 2: "Guardian", 3: "Minion", 4: "Totem", 5: "Companion", 6: "Runeblade",
               7: "Construct", 8: "Opponent", 9: "Vehicle", 10: "Mount", 11: "Lightwell", 12: "Butler"}  # SharedDefines.h:6666-6711
#: SUMMON_PET numSummons list (SpellEffects.cpp:1915-1937)
MULTI_SUMMON_PROPERTIES = (64, 61, 1101, 66, 648, 2301, 1061, 1261, 629, 181, 715, 1562, 833, 1161, 713)

MASK_TO_CLASS = {"UNIT_MASK_GUARDIAN": "Guardian", "UNIT_MASK_TOTEM": "Totem", "UNIT_MASK_MINION": "Minion",
                 "UNIT_MASK_PUPPET": "Puppet", "UNIT_MASK_SUMMON": "TempSummon"}


def _csv(name: str) -> list[dict[str, str]]:
    with (TABLES / f"{name}.csv").open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@lru_cache(maxsize=1)
def _db2() -> dict[str, Any]:
    effects: dict[int, list[dict[str, str]]] = {}
    for row in _csv("SpellEffect"):
        if row["DifficultyID"] == "0":
            effects.setdefault(int(row["SpellID"]), []).append(row)
    for rows in effects.values():
        rows.sort(key=lambda r: int(r["EffectIndex"]))
    names = {int(r["ID"]): r["Name_lang"] for r in _csv("SpellName")}
    props = {int(r["ID"]): r for r in _csv("SummonProperties")}
    class_set: dict[int, int] = {}
    for r in sorted(_csv("ChrClasses"), key=lambda r: int(r["ID"])):  # lowest class ID wins (14 Adventurer shares set 11 with Shaman)
        if r["SpellClassSet"] not in ("", "0") and int(r["ID"]) <= 13:
            class_set.setdefault(int(r["SpellClassSet"]), int(r["ID"]))
    options = {int(r["SpellID"]): int(r["SpellClassSet"]) for r in _csv("SpellClassOptions")}
    return {"effects": effects, "names": names, "props": props, "class_set": class_set, "options": options}


def mask_for_properties(control: int, title: int, flags: int) -> tuple[str, str]:
    """``Map::SummonCreature`` unit-mask switch (Object.cpp:1195-1240)."""
    if control == 2:
        return "UNIT_MASK_GUARDIAN", "Object.cpp:1200-1202"
    if control == 3:
        return "UNIT_MASK_PUPPET", "Object.cpp:1203-1205"
    if control in (4, 5):
        return "UNIT_MASK_MINION", "Object.cpp:1206-1209"
    if control in (0, 1):
        if title in (3, 2, 6):
            return "UNIT_MASK_GUARDIAN", "Object.cpp:1214-1218"
        if title in (4, 11):
            return "UNIT_MASK_TOTEM", "Object.cpp:1219-1222"
        if title in (9, 10):
            return "UNIT_MASK_SUMMON", "Object.cpp:1223-1226"
        if title == 5:
            return "UNIT_MASK_MINION", "Object.cpp:1227-1229"
        if flags & FLAG_JOIN_SUMMONER_SPAWN_GROUP:
            return "UNIT_MASK_GUARDIAN", "Object.cpp:1230-1233"
        return "UNIT_MASK_SUMMON", "Object.cpp:1194 (default mask)"
    return "nullptr", "Object.cpp:1238-1239 (unknown Control -> return nullptr)"


def route_summon_effect(control: int, title: int, flags: int) -> dict[str, Any]:
    """``Spell::EffectSummonType`` dispatch (SpellEffects.cpp:1940-2076) + the resulting mask."""
    if control in (0, 1):
        if flags & FLAG_JOIN_SUMMONER_SPAWN_GROUP:
            route, coord = "SummonGuardian", "SpellEffects.cpp:1945-1949"
        elif title in (1, 2, 6, 3):
            route, coord = "SummonGuardian", "SpellEffects.cpp:1953-1958"
        elif title in (9, 10, 11):
            route, coord = "Map::SummonCreature", "SpellEffects.cpp:1960-1969"
        elif title == 4:
            route, coord = "Map::SummonCreature + totem health override", "SpellEffects.cpp:1970-1985"
        elif title == 5:
            route, coord = "Map::SummonCreature + SetImmuneToAll", "SpellEffects.cpp:1986-1996"
        else:
            route, coord = "Map::SummonCreature x numSummons (owner GUID only for ALLY)", "SpellEffects.cpp:1997-2031"
    elif control == 2:
        route, coord = "SummonGuardian", "SpellEffects.cpp:2036-2038"
    elif control == 3:
        route, coord = "Map::SummonCreature (puppet)", "SpellEffects.cpp:2039-2046"
    elif control in (4, 5):
        route, coord = "Map::SummonCreature + ride vehicle", "SpellEffects.cpp:2047-2075"
    else:
        route, coord = "no case (nothing summoned)", "SpellEffects.cpp:1940"
    mask, mcoord = mask_for_properties(control, title, flags)
    return {"route": route, "route_coordinate": TC + "Spells/" + coord, "unit_mask": mask,
            "mask_coordinate": TC + "Entities/Object/" + mcoord, "trinity_unit_class": MASK_TO_CLASS.get(mask, "none"),
            "controllable": bool(mask == "UNIT_MASK_GUARDIAN" and (title == 1 or control == 2)),  # Guardian ctor TemporarySummon.cpp:517-521
            "use_creature_level": bool(flags & FLAG_USE_CREATURE_LEVEL)}


def owner_class_for_spell(spell: int) -> int | None:
    d = _db2()
    cs = d["options"].get(spell)
    return d["class_set"].get(cs) if cs else None


def summon_effects(spell: int) -> list[dict[str, Any]]:
    d = _db2()
    out = []
    for e in d["effects"].get(spell, []):
        eff = int(e["Effect"])
        if eff not in (SPELL_EFFECT_SUMMON, SPELL_EFFECT_SUMMON_PET):
            continue
        rec: dict[str, Any] = {"effect_row_id": int(e["ID"]), "effect_index": int(e["EffectIndex"]), "effect": eff,
                               "entry": int(e["EffectMiscValue_0"]), "base_points": float(e["EffectBasePointsF"])}
        if eff == SPELL_EFFECT_SUMMON:
            sp_id = int(e["EffectMiscValue_1"])
            prop = d["props"].get(sp_id)
            rec["summon_properties"] = sp_id
            if prop is None:
                rec.update({"route": "EffectSummonType: Unhandled summon type (no SummonProperties row)",
                            "route_coordinate": TC + "Spells/SpellEffects.cpp:1876-1881", "trinity_unit_class": "none"})
            else:
                control, title, flags = int(prop["Control"]), int(prop["Title"]), int(prop["Flags_0"])
                rec["summon_properties_row"] = {"Control": control, "Title": title, "Slot": int(prop["Slot"]),
                                                "Flags_0": flags, "Flags_1": int(prop["Flags_1"])}
                rec.update(route_summon_effect(control, title, flags))
                rec["num_summons"] = (f"max(effect value, 1) = {max(int(float(e['EffectBasePointsF'])), 1)}"
                                      if sp_id in MULTI_SUMMON_PROPERTIES else "1")
        else:
            rec.update({"route": "Spell::EffectSummonPet -> Player::SummonPet" if rec["entry"] else
                        "Spell::EffectSummonPet -> Player::SummonPet(0, slot=effect value) -> Pet::LoadPetFromDB (call pet)",
                        "route_coordinate": TC + "Spells/SpellEffects.cpp:2656-2721",
                        "trinity_unit_class": "Pet", "unit_mask": "UNIT_MASK_PET|GUARDIAN|CONTROLABLE_GUARDIAN"})
        out.append(rec)
    return out


def resolve_summon(spell: int, entry: int | None = None, owner_class: int | None = None) -> dict[str, Any]:
    """Resolve the unit the spell summons; ``status`` is ``ok``, ``ambiguous`` or ``unresolved``."""
    d = _db2()
    if spell not in d["names"]:
        return {"spell": spell, "status": "unresolved", "reason": "no SpellName row in the 12.1 snapshot"}
    effs = summon_effects(spell)
    source = "ChrClasses.SpellClassSet == SpellClassOptions.SpellClassSet (db2-fact)"
    if owner_class is None:
        owner_class = owner_class_for_spell(spell)
    else:
        source = "supplied"
    base = {"spell": spell, "name": d["names"][spell], "owner_class_default": owner_class, "owner_class_source": source}
    if not effs:
        triggers = [{"effect_index": int(e["EffectIndex"]), "effect": int(e["Effect"]),
                     "trigger_spell": int(e["EffectTriggerSpell"])}
                    for e in d["effects"].get(spell, []) if int(e["Effect"]) in TRIGGER_EFFECTS]
        return {**base, "status": "unresolved",
                "reason": "no SPELL_EFFECT_SUMMON(28)/SUMMON_PET(56) at DIFFICULTY_NONE; the summon (if any) is "
                          "reached through a trigger/dummy/script edge -- resolve the child spell instead",
                "trigger_or_dummy_effects": triggers}
    candidates = [e for e in effs if entry is None or e["entry"] == entry]
    distinct = sorted({(e["entry"], e["trinity_unit_class"]) for e in candidates})
    if entry is not None and not candidates:
        return {**base, "status": "unresolved", "reason": f"spell does not summon entry {entry}", "summons": effs}
    if len(distinct) != 1:
        return {**base, "status": "ambiguous", "reason": "several summoned entries/classes; pass --entry", "summons": effs}
    eff = candidates[0]
    cls = eff["trinity_unit_class"]
    if owner_class is None:
        return {**base, "status": "unresolved", "reason": "owner class not derivable from SpellClassOptions; pass --owner-class",
                "summons": effs, "entry": eff["entry"], "trinity_unit_class": cls}
    if cls == "Pet":
        if eff["entry"] == 0:
            cls = "Pet/HUNTER_PET"
        else:
            cls = "Pet/SUMMON_PET"  # Player::SummonPet constructs Pet(this, SUMMON_PET) (Player.cpp:30442)
    elif cls != "Guardian":
        return {**base, "status": "unresolved", "entry": eff["entry"], "trinity_unit_class": cls, "summons": effs,
                "reason": f"{cls}: no Guardian stat code; plain Creature::UpdateLevelDependantStats values (independent), "
                          "the prepared-stat oracle covers only Pet/Guardian"}
    return {**base, "status": "ok", "entry": eff["entry"], "trinity_unit_class": cls, "summon_effect": eff,
            "summons": effs}


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

#: fixed owner fact set used to exercise every witness (level-90 owner, round numbers; inputs, not Retail claims)
STANDARD_OWNER = {"level": 90, "stats": (1000, 1000, 20000, 1000, 0), "armor": 5000,
                  "attack_power_melee": 10000.0, "attack_power_ranged": 10000.0, "sp": 10000}

WITNESS_SPELLS = [
    # (label, spell, entry or None)
    ("Hunter pet (Call Pet 1)", 883, None),
    ("Warlock Imp", 688, None),
    ("Warlock Grimoire: Imp", 111859, None),
    ("Warlock Felguard", 30146, None),
    ("Warlock Grimoire: Felguard", 111898, None),
    ("Warlock Wild Imp", 104317, None),
    ("Warlock Wild Imp (Inner Demons)", 279910, None),
    ("DK Raise Dead (talent spell)", 46584, None),
    ("DK Raise Dead pet summon", 52150, None),
    ("DK Raise Dead guardian", 46585, None),
    ("DK Army of the Dead (aura)", 42650, None),
    ("DK Army ghoul summon", 42651, None),
    ("DK Ebon Gargoyle", 49206, None),
    ("Mage Mirror Image (cast)", 55342, None),
    ("Mage Mirror Image summon", 321686, None, st.CLASS_MAGE),
    ("Mage Water Elemental", 31687, None),
    ("Priest Shadowfiend (cast)", 34433, None),
    ("Priest Shadowfiend summon", 1280172, None),
    ("Priest Mindbender", 123040, None),
    ("Shaman Fire Elemental (cast)", 198067, None),
    ("Shaman Fire Elemental summon", 188592, None),
    ("Shaman Fire Elemental (pet variant)", 372335, None, st.CLASS_SHAMAN),
    ("Shaman Earth Elemental (cast)", 198103, None),
    ("Shaman Earth Elemental summon", 188616, None),
    ("Shaman Feral Spirit (cast)", 51533, None),
    ("Shaman Feral Spirit summon", 228562, None),
    ("Shaman Healing Stream Totem", 5394, None),
    ("Druid Force of Nature (cast)", 205636, None),
    ("Druid Force of Nature summon", 248280, None),
    ("Druid Grove Guardians", 102693, None),
    ("Paladin Guardian of Ancient Kings", 86659, None),
    ("Monk Xuen", 123904, None),
    ("Warlock Summon Infernal", 1122, None),
]


def classification_matrix() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in st.consumer_table():
        out.setdefault(row["unit_class"], {})[row["stat"]] = row["classification"]
    return out


OWNER_FACTS_CONSUMED = [
    {"owner_fact": "Stats[STAMINA] (UnitData int32 -> float)", "consumer": "Guardian::UpdateStats", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:1146-1160"],
     "read_when": "every Guardian::UpdateStats(STA): InitStatsForLevel; Player::UpdateStats(STA) for GetPet() only", "classification": st.EXPLICIT},
    {"owner_fact": "Stats[STRENGTH]", "consumer": "Guardian::UpdateStats (ghoul 26125 only)", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:1146-1155"],
     "read_when": "as above", "classification": st.EXPLICIT},
    {"owner_fact": "Stats[INTELLECT] (warlock/mage owner)", "consumer": "Guardian::UpdateStats", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:1162-1169"],
     "read_when": "as above", "classification": st.EXPLICIT},
    {"owner_fact": "GetArmor() (Resistances[0] int32 -> uint32)", "consumer": "Guardian::UpdateArmor (Pet only); InitStatsForLevel 29264", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:1239-1242", TC + "Entities/Pet/Pet.cpp:1028"],
     "read_when": "UpdateArmor: InitStatsForLevel, Player::UpdateArmor -> GetPet() (StatSystem.cpp:276-278); 29264: creation only", "classification": st.EXPLICIT},
    {"owner_fact": "GetTotalAttackPowerValue(RANGED_ATTACK)", "consumer": "Guardian::UpdateAttackPowerAndDamage (hunter pet)", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:1310-1315"],
     "read_when": "InitStatsForLevel; Player::UpdateAttackPowerAndDamage(ranged) -> hunter GetPet() (StatSystem.cpp:403-407)", "classification": st.EXPLICIT},
    {"owner_fact": "GetTotalAttackPowerValue(BASE_ATTACK)", "consumer": "Guardian::UpdateAttackPowerAndDamage (ghoul, spirit wolf); InitStatsForLevel 27829/28017", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:1316-1326", TC + "Entities/Pet/Pet.cpp:1053", TC + "Entities/Pet/Pet.cpp:1060"],
     "read_when": "Player::UpdateAttackPowerAndDamage(melee) -> ghoul GetPet() / spirit-wolf GetGuardianPet() (StatSystem.cpp:419-423); 27829/28017 creation only", "classification": st.EXPLICIT},
    {"owner_fact": "ActivePlayerData::ModDamageDonePos/Neg[FIRE,SHADOW,FROST,NATURE] (int32; Neg stored <= 0)", "consumer": "Guardian::UpdateAttackPowerAndDamage (other Pet: max(fire,shadow)); UpdateDamagePhysical (1964/15438)", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:1328-1345", TC + "Entities/Unit/StatSystem.cpp:1370-1382", TC + "Entities/Unit/StatSystem.cpp:180-188"],
     "read_when": "InitStatsForLevel; AuraEffect::HandleModDamageDone on the owner -> GetGuardianPet() (SpellAuraEffects.cpp:4731-4732); NOT Player::UpdateSpellDamageAndHealingBonus / ApplySpellPowerBonus (no pet call)", "classification": st.EXPLICIT},
    {"owner_fact": "ModDamageDonePos[FIRE/SHADOW] (Pos only)", "consumer": "InitStatsForLevel SUMMON_PET bonus damage", "coordinates": [TC + "Entities/Pet/Pet.cpp:929-935"],
     "read_when": "creation; overwritten by UpdateAllStats -> UpdateAttackPowerAndDamage (Pos-Neg) in the same call", "classification": st.CREATION_SNAPSHOT},
    {"owner_fact": "SpellBaseDamageBonusDone(FROST/NATURE/FIRE/SHADOW)", "consumer": "InitStatsForLevel entry branches 510/1964/15438/19668/31216", "coordinates": [TC + "Entities/Pet/Pet.cpp:958-1044"],
     "read_when": "creation only", "classification": st.CREATION_SNAPSHOT},
    {"owner_fact": "owner level", "consumer": "Pet level (SUMMON_PET/HUNTER_PET sync) / TempSummon clamp", "coordinates": [TC + "Entities/Pet/Pet.cpp:1784-1798", TC + "Entities/Creature/TemporarySummon.cpp:241-247"],
     "read_when": "Pet: creation, load and SynchronizeLevelWithOwner; Guardian: creation", "classification": st.EXPLICIT},
    {"owner_fact": "GetRatingBonusValue(CR_VERSATILITY_DAMAGE_DONE) + MOD_VERSATILITY", "consumer": "Unit::SpellDamagePctDone / MeleeDamageBonusDone via GetSpellModOwner()", "coordinates": [TC + "Entities/Unit/Unit.cpp:6926-6928", TC + "Entities/Object/Object.cpp:1648-1670"],
     "read_when": "at damage time, Pet and Totem only", "classification": st.DYNAMIC},
    {"owner_fact": "owner spell mods (SpellModifier list)", "consumer": "Unit::SpellDamageBonusDone / Spell via GetSpellModOwner()", "coordinates": [TC + "Entities/Unit/Unit.cpp:6854-6858", TC + "Entities/Unit/Unit.cpp:6895-6896"],
     "read_when": "at cast/damage time, Pet and Totem only", "classification": st.DYNAMIC},
    {"owner_fact": "Totem: the whole SpellDamageBonusDone", "consumer": "Unit::SpellDamageBonusDone", "coordinates": [TC + "Entities/Unit/Unit.cpp:6833-6836"],
     "read_when": "at damage time", "classification": st.DYNAMIC},
    {"owner_fact": "owner haste / crit / mastery", "consumer": "none", "coordinates": [TC + "Entities/Unit/StatSystem.cpp:108-424", TC + "Entities/Player/Player.cpp:5237"],
     "read_when": "never (no Player::Update* for these touches a controlled unit)", "classification": st.UNRESOLVED},
]


def pet_scaling_census() -> dict[str, Any]:
    """Snapshot-side 'scaling' spells and whether Trinity applies them (kept apart from Guardian::Update*)."""
    d = _db2()
    rows = []
    for sid, name in sorted(d["names"].items()):
        low = name.lower()
        if "scaling" in low and ("pet" in low or "summon" in low or "guardian" in low or "minion" in low):
            effs = [{"index": int(e["EffectIndex"]), "effect": int(e["Effect"]), "aura": int(e["EffectAura"]),
                     "base_points": float(e["EffectBasePointsF"]), "misc0": int(e["EffectMiscValue_0"])}
                    for e in d["effects"].get(sid, [])]
            rows.append({"spell": sid, "name": name, "effects": effs})
    return {
        "query": "SpellName.Name_lang contains 'scaling' and one of pet/summon/guardian/minion (case-insensitive)",
        "rows": rows,
        "count": len(rows),
        "trinity_application": {
            "spell_pet_auras": "2 TDB rows: (20895 -> 24529) both absent from 12.1 SpellName; (28757 -> 28758) present (Stalker's Ally); "
                               "loaded by SpellMgr::LoadSpellPetAuras (Spells/SpellMgr.cpp:1965-2029), cast by Pet::CastPetAuras "
                               "only when IsPermanentPetFor (Pet.cpp:1657-1678, 1730-1747)",
            "spell_pet.cpp": "legacy pet-scaling AuraScripts (scripts/Spells/spell_pet.cpp:89-1571) reference spell IDs absent from 12.1 "
                             "and AddSC_pet_spell_scripts (spell_pet.cpp:1628) is never called by scripts/Spells/spell_script_loader.cpp "
                             "(rg finds only the definition) -> never registered (trinity-consumer, static)",
            "Guardian::Update*": "owner-derived stats come from hardcoded code (stats.json), not from any snapshot scaling aura",
        },
        "classification": "the 12.1 '[DNT] Periodic Scaling Summons' spells have no Trinity consumer: unresolved "
                          "(absent from pinned Trinity != absent from Retail)",
    }


def build_corpus() -> dict[str, Any]:
    db2, world = st.sources()
    witnesses = []
    for label, spell, entry, *cls in WITNESS_SPELLS:
        res = resolve_summon(spell, entry, cls[0] if cls else None)
        rec: dict[str, Any] = {"label": label, "resolution": res}
        if res.get("status") == "ok":
            owner = st.OwnerFacts(class_id=res["owner_class_default"], level=STANDARD_OWNER["level"],
                                  stats=STANDARD_OWNER["stats"], armor=STANDARD_OWNER["armor"],
                                  attack_power_melee=STANDARD_OWNER["attack_power_melee"],
                                  attack_power_ranged=STANDARD_OWNER["attack_power_ranged"],
                                  mod_damage_done_pos=(STANDARD_OWNER["sp"],) * st.MAX_SPELL_SCHOOL,
                                  spell_base_damage_bonus={k: STANDARD_OWNER["sp"] for k in ("fire", "frost", "nature", "shadow")})
            try:
                out = st.run_oracle(res["entry"], res["trinity_unit_class"], owner, db2=db2, world=world,
                                    use_creature_level=bool(res["summon_effect"].get("use_creature_level")))
                out.pop("trace")
                rec["oracle"] = {"status": "ok", **out}
            except SourceError as exc:
                rec["oracle"] = {"status": "unresolved", "reason": str(exc)}
        witnesses.append(rec)
    return {
        "provenance": {**st.provenance("python3 controlled_units.py inheritance"),
                       "world_database_corpora": world.sources, "world_status": world.status},
        "routing_rules": {
            "effect_56_SUMMON_PET": "player caster -> Player::SummonPet -> new Pet(SUMMON_PET) (Player.cpp:30438-30523); "
                                    "entry 0 -> LoadPetFromDB slot (call pet); non-player caster -> SummonGuardian with SummonProperties 67 (SpellEffects.cpp:2671-2677)",
            "effect_28_SUMMON": [
                {"control": "WILD/ALLY", "title": "any", "flags": "JoinSummonerSpawnGroup 0x200", **route_summon_effect(1, 0, 0x200)},
                {"control": "WILD/ALLY", "title": "Pet (1)", "flags": "no 0x200", **route_summon_effect(1, 1, 0)},
                {"control": "WILD/ALLY", "title": "Guardian/Minion/Runeblade (2/3/6)", "flags": "no 0x200", **route_summon_effect(1, 2, 0)},
                {"control": "WILD/ALLY", "title": "Totem (4)", "flags": "no 0x200", **route_summon_effect(1, 4, 0)},
                {"control": "WILD/ALLY", "title": "Totem (4)", "flags": "0x200", **route_summon_effect(1, 4, 0x200)},
                {"control": "WILD/ALLY", "title": "Lightwell (11)", "flags": "no 0x200", **route_summon_effect(1, 11, 0)},
                {"control": "WILD/ALLY", "title": "Companion (5)", "flags": "no 0x200", **route_summon_effect(1, 5, 0)},
                {"control": "WILD/ALLY", "title": "Vehicle/Mount (9/10)", "flags": "no 0x200", **route_summon_effect(1, 9, 0)},
                {"control": "WILD/ALLY", "title": "other (0, 7, 8, 12+)", "flags": "no 0x200", **route_summon_effect(1, 0, 0)},
                {"control": "PET (2)", "title": "any", "flags": "any", **route_summon_effect(2, 0, 0)},
                {"control": "PUPPET (3)", "title": "any", "flags": "any", **route_summon_effect(3, 0, 0)},
                {"control": "VEHICLE / POSSESSED_VEHICLE (5/4)", "title": "any", "flags": "any", **route_summon_effect(5, 0, 0)},
            ],
            "notes": [
                "Title Pet under WILD/ALLY without 0x200 is routed to SummonGuardian but Map::SummonCreature allocates a plain "
                "TempSummon (UNIT_MASK_SUMMON): no Guardian stat code, no owner inheritance (SpellEffects.cpp:1953-1958 vs Object.cpp:1230-1234)",
                "Guardian ctor marks CONTROLABLE_GUARDIAN + InitCharmInfo for Title Pet or Control PET (TemporarySummon.cpp:513-523)",
                "SummonGuardian re-runs InitStatsForLevel(skill/5) for engineering items (SpellEffects.cpp:5044-5048)",
            ],
        },
        "classification_matrix": classification_matrix(),
        "owner_facts_consumed": OWNER_FACTS_CONSUMED,
        "pet_scaling_spells": pet_scaling_census(),
        "standard_owner_for_witness_runs": {k: list(v) if isinstance(v, tuple) else v for k, v in STANDARD_OWNER.items()},
        "witness_resolutions": witnesses,
    }


def cmd_inheritance(args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else CORPORA / "inheritance.json"
    doc = build_corpus()
    st.write_json(out, doc)
    ok = sum(1 for w in doc["witness_resolutions"] if w.get("oracle", {}).get("status") == "ok")
    print(f"wrote {out} ({len(doc['witness_resolutions'])} witness resolutions, {ok} oracle runs ok)")
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    print(json.dumps(resolve_summon(args.spell, args.entry, args.owner_class), indent=1))
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("inheritance", help="write controlled-unit-corpora/inheritance.json")
    p.add_argument("--out")
    p.set_defaults(func=cmd_inheritance)
    q = subparsers.add_parser("resolve-summon", help="Trinity unit class produced by a spell's summon effect")
    q.add_argument("--spell", type=int, required=True)
    q.add_argument("--entry", type=int)
    q.add_argument("--owner-class", type=int, dest="owner_class")
    q.set_defaults(func=cmd_resolve)
