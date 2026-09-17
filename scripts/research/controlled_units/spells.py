"""Controlled-unit spell acquisition, availability, AI and execution (Track A4, report sections 8-9).

Acquisition paths (all read at TrinityCore 7f3d43b):

``levelup``          ``SpellMgr::LoadPetLevelupSpellMap`` (SpellMgr.cpp:2152-2199) -> ``Pet::InitLevelupSpellsForLevel`` (Pet.cpp:1486-1519)
``family-passive``   ``SpellMgr::LoadPetFamilySpellsStore`` (SpellMgr.cpp:5557-5588) -> ``Pet::LearnPetPassives`` (Pet.cpp:1709-1728)
``default``          ``SpellMgr::LoadPetDefaultSpells`` (SpellMgr.cpp:2249-2294) -> ``InitLevelupSpellsForLevel`` (Pet.cpp:1503-1519)
``spec``             ``Pet::LearnSpecializationSpells`` (Pet.cpp:1850-1868), override via SPELL_AURA_OVERRIDE_PET_SPECS
``charm``            ``CharmInfo::InitCharmCreateSpells`` (CharmInfo.cpp:113-164) for CONTROLABLE guardians with a player owner (TemporarySummon.cpp:531-532)
``totem``            ``Totem::InitSummon`` (Totem.cpp:88-96) casts ``m_spells[0]`` (passive totem) and ``m_spells[1]``
``template-ai``      ``m_spells[0..7]`` read by generic AIs (CombatAI.cpp:198-230, PassiveAI.cpp:113-123) or scripts
``script``           a spell constant in ``src/server/scripts/Pet/*.cpp`` (structural script index)
``pet-aura``         ``spell_pet_auras`` -> ``Player::AddPetAura`` -> ``Pet::CastPetAuras`` (Pet.cpp:1730-1762)
``learn-pet-spell``  ``SPELL_EFFECT_LEARN_PET_SPELL`` (SpellEffects.cpp:2746-2770)

``pet-only`` paths exist only on the ``Pet`` class; a ``Guardian`` gets spells
only through ``charm`` / ``template-ai`` / ``script``.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from controlled_units import CORPORA, TABLES, SourceError
from controlled_units import stats as st
from procs.enums import is_aura_effect

TC = st.TC
TC_ROOT = Path(__file__).resolve().parents[4] / "TrinityCore"  # sibling of wowlab-data under pallet/

ATTR0_PASSIVE = 0x40  # SharedDefines.h:443
ATTR1_NO_AUTOCAST_AI = 0x20000  # SharedDefines.h:491
ATTR5_NOT_AVAILABLE_WHILE_CHARMED = 0x4000  # SharedDefines.h:636
ATTR9_AUTOCAST_OFF_BY_DEFAULT = 0x20000  # SharedDefines.h:787
ACQUIRE_AUTOMATIC_CHAR_LEVEL = 2  # DBCEnums.h SkillLineAbilityAcquireMethod::AutomaticCharLevel
SPEC_FLAG_PET_OVERRIDE = 0x20  # ChrSpecialization.Flags 32 -> stored at PET_SPEC_OVERRIDE_CLASS_INDEX (DB2Stores.cpp:1264-1273)

PET_AURA_NAME = re.compile(r"PET|SUMMON|MINION|GUARDIAN|TOTEM|CHARM|POSSESS|CONTROLLED|OWNER")

PATH_DEFS = {
    "levelup": {"unit_classes": ["Pet"], "coordinates": [TC + "Spells/SpellMgr.cpp:2152-2199", TC + "Entities/Pet/Pet.cpp:1486-1501"],
                "rule": "SkillLineAbility on CreatureFamily.SkillLine[0..1], AcquireMethod == AutomaticCharLevel(2), SpellInfo exists, SpellLevel != 0; learned when SpellLevel <= pet level, unlearned above"},
    "family-passive": {"unit_classes": ["Pet"], "coordinates": [TC + "Spells/SpellMgr.cpp:5557-5588", TC + "Entities/Pet/Pet.cpp:1709-1728"],
                       "rule": "SkillLineAbility spell with SpellInfo, no DIFFICULTY_NONE SpellLevels row with SpellLevel != 0, IsPassive, on a family SkillLine, AcquireMethod 2; added as PETSPELL_FAMILY and cast on self"},
    "default": {"unit_classes": ["Pet"], "coordinates": [TC + "Spells/SpellMgr.cpp:2201-2294", TC + "Entities/Pet/Pet.cpp:1503-1519"],
                "rule": "creature_template_spell Index 0..3 of any creature summoned by a DIFFICULTY_NONE SUMMON(28)/SUMMON_PET(56) effect, minus the family levelup set; learned when SpellLevel <= pet level"},
    "spec": {"unit_classes": ["Pet (hunter)"], "coordinates": [TC + "Entities/Pet/Pet.cpp:1850-1868", TC + "Entities/Pet/Pet.cpp:413-417", TC + "Spells/Auras/SpellAuraEffects.cpp:6325-6346"],
             "rule": "SpecializationSpells of the pet's ChrSpecialization (ClassID 0: 74/79/81, override 535/536/537 when the owner has SPELL_AURA_OVERRIDE_PET_SPECS) with SpellLevel <= pet level"},
    "charm": {"unit_classes": ["Guardian (CONTROLABLE, player owner)", "Pet via m_charmInfo"], "coordinates": [TC + "Entities/Unit/CharmInfo.cpp:113-164", TC + "Entities/Creature/TemporarySummon.cpp:525-535"],
              "rule": "m_spells[0..3]: skip ATTR5_NOT_AVAILABLE_WHILE_CHARMED; passive -> cast on self; else action bar, autocast ENABLED only if autocastable && !ATTR9_AUTOCAST_OFF_BY_DEFAULT && NeedsExplicitUnitTarget"},
    "totem": {"unit_classes": ["Totem"], "coordinates": [TC + "Entities/Totem/Totem.cpp:78-96"],
              "rule": "m_spells[0] cast at summon when the totem is passive (spell has no cast time); m_spells[1] always cast"},
    "template-ai": {"unit_classes": ["any Creature"], "coordinates": [TC + "Entities/Creature/Creature.cpp:575", TC + "AI/CoreAI/CombatAI.cpp:198-230", TC + "AI/CoreAI/PassiveAI.cpp:113-123"],
                    "rule": "m_spells[0..7] = creature_template_spell; used only by the selected CreatureAI (generic CombatAI family reads m_spells[0]) or scripts"},
    "script": {"unit_classes": ["any"], "coordinates": [TC.replace("game/", "scripts/Pet/")],
               "rule": "spell ID appears as a constant in a src/server/scripts/Pet/*.cpp creature script (structural; the script decides when it is cast)"},
    "pet-aura": {"unit_classes": ["Pet (IsPermanentPetFor)"], "coordinates": [TC + "Spells/SpellMgr.cpp:1965-2029", TC + "Entities/Pet/Pet.cpp:1657-1678", TC + "Entities/Pet/Pet.cpp:1730-1762"],
                 "rule": "spell_pet_auras row whose source spell the owner has; cast only if Pet::IsPermanentPetFor (HUNTER_PET; SUMMON_PET with warlock+demon, DK+undead, mage+elemental)"},
    "learn-pet-spell": {"unit_classes": ["Pet"], "coordinates": [TC + "Spells/SpellEffects.cpp:2746-2770"],
                        "rule": "SPELL_EFFECT_LEARN_PET_SPELL(57): pet->learnSpell(EffectTriggerSpell)"},
}

EXECUTION_DIFFERENCES = [
    {"topic": "spell mod owner", "rule": "WorldObject::GetSpellModOwner returns the player owner only for IsPet() || IsTotem(); Guardian/Minion/TempSummon casts get no owner spell mods, no owner versatility, and no spell crit",
     "coordinates": [TC + "Entities/Object/Object.cpp:1648-1670", TC + "Entities/Unit/Unit.cpp:7124-7125"], "evidence_class": "trinity-consumer"},
    {"topic": "totem forwarding", "rule": "Unit::SpellDamageBonusDone for a totem returns owner->SpellDamageBonusDone (whole computation on the owner)",
     "coordinates": [TC + "Entities/Unit/Unit.cpp:6833-6836"], "evidence_class": "trinity-consumer"},
    {"topic": "guardian bonus damage", "rule": "UNIT_MASK_GUARDIAN adds Guardian::GetBonusDamage() (m_bonusSpellDamage from UpdateAttackPowerAndDamage) to DoneAdvertisedBenefit",
     "coordinates": [TC + "Entities/Unit/Unit.cpp:6845-6848"], "evidence_class": "trinity-consumer"},
    {"topic": "creature spell damage rate", "rule": "a non-Pet creature multiplies spell damage by Creature::GetSpellDamageMod(classification) = Rate.Creature.*.SpellDamage (default 1)",
     "coordinates": [TC + "Entities/Unit/Unit.cpp:6922-6924", TC + "Entities/Creature/Creature.cpp:1739-1760"], "evidence_class": "trinity-consumer"},
    {"topic": "non-player spell base damage", "rule": "SpellBaseDamageBonusDone for a non-player = SPELL_AURA_MOD_DAMAGE_DONE by school only (no intellect, no base spell power)",
     "coordinates": [TC + "Entities/Unit/Unit.cpp:7083-7118"], "evidence_class": "trinity-consumer"},
    {"topic": "melee pct done", "rule": "MeleeDamageBonusDone for a non-player takes the school pct from GetTotalAuraMultiplierByMiscMask(MOD_DAMAGE_PERCENT_DONE)",
     "coordinates": [TC + "Entities/Unit/Unit.cpp:8078-8081"], "evidence_class": "trinity-consumer"},
    {"topic": "caster-pet crit", "rule": "victim-side SPELL_AURA_MOD_CRIT_CHANCE_FOR_CASTER_PET (339) matches the attacker's summoner GUID for any TempSummon",
     "coordinates": [TC + "Entities/Unit/Unit.cpp:2927-2933", TC + "Entities/Unit/Unit.cpp:7261-7267"], "evidence_class": "trinity-consumer"},
    {"topic": "melee crit base", "rule": "creature melee crit = 5.0 + MOD_WEAPON_CRIT_PERCENT + MOD_CRIT_PCT unless flags_extra NO_CRIT; no owner crit",
     "coordinates": [TC + "Entities/Unit/Unit.cpp:2896-2903"], "evidence_class": "trinity-consumer"},
    {"topic": "haste", "rule": "only auras on the unit itself (HandleModMeleeSpeedPct / HandleModCastingSpeed); no owner haste propagation",
     "coordinates": [TC + "Spells/Auras/SpellAuraEffects.cpp:4483-4523", TC + "Spells/Auras/SpellAuraEffects.cpp:4575-4592"], "evidence_class": "trinity-consumer"},
]

AI_RULES = [
    {"rule": "IsPet() -> PetAI unconditionally (overrides AIName/ScriptName)", "coordinates": [TC + "AI/CreatureAISelector.cpp:88-91"]},
    {"rule": "else ScriptName CreatureAI (ScriptMgr::GetCreatureAI), else AIName registry item, else highest Permit", "coordinates": [TC + "AI/CreatureAISelector.cpp:93-104", TC + "AI/CreatureAISelector.cpp:62-84"]},
    {"rule": "PetAI::Permissible: CONTROLABLE_GUARDIAN with player owner -> PERMIT_BASE_PROACTIVE (so a controllable Guardian without script/AIName gets PetAI)", "coordinates": [TC + "AI/CoreAI/PetAI.cpp:35-45"]},
    {"rule": "PetAI::UpdateAI autocast: candidates from GetPetAutoSpellOnPos (charm slots 0..3 with ACT_ENABLED; Pet overrides with m_autospells); positive spells -> self/owner/allies out of combat; offensive -> victim; JUMP_DEST only on enemies; one random candidate cast per update (urand)",
     "coordinates": [TC + "AI/CoreAI/PetAI.cpp:55-222", TC + "Entities/Creature/Creature.cpp:3374-3385"]},
    {"rule": "IsAutocastable = !passive && !ATTR1_NO_AUTOCAST_AI; IsAutocastEnabledByDefault = !ATTR9_AUTOCAST_OFF_BY_DEFAULT", "coordinates": [TC + "Spells/SpellInfo.cpp:1758-1770"]},
    {"rule": "Pet::addSpell ACT_DECIDE: PASSIVE if not autocastable, ENABLED if enabled by default, else DISABLED; passive spells cast on self when CasterAuraState is satisfied, others go to the action bar",
     "coordinates": [TC + "Entities/Pet/Pet.cpp:1394-1450"]},
    {"rule": "action bar: pet spell slots 3..6 (ACTION_BAR_INDEX_PET_SPELL_START 3, _END 7), 10 slots; ActiveStates PASSIVE 0x01, DISABLED 0x81, ENABLED 0xC1, COMMAND 0x07, REACTION 0x06; React PASSIVE/DEFENSIVE/AGGRESSIVE/ASSIST; Command STAY/FOLLOW/ATTACK/ABANDON/MOVE_TO",
     "coordinates": [TC + "Entities/Unit/CharmInfo.h:80-82", TC + "Entities/Unit/UnitDefines.h:529-565"]},
    {"rule": "owner commands: HandlePetActionHelper / HandlePetCastSpellOpcode", "coordinates": [TC + "Handlers/PetHandler.cpp:141", TC + "Handlers/PetHandler.cpp:678"]},
    {"rule": "Guardian::InitStats sets REACT_AGGRESSIVE and InitCharmCreateSpells for CONTROLABLE guardians of a player", "coordinates": [TC + "Entities/Creature/TemporarySummon.cpp:525-535"]},
]


def _csv(name: str) -> list[dict[str, str]]:
    with (TABLES / f"{name}.csv").open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@lru_cache(maxsize=1)
def _bundle():
    from dummy_semantics.loaders import Bundle
    return Bundle()


@lru_cache(maxsize=1)
def _scope_reach() -> frozenset[int]:
    from dummy_semantics.scope import Scope
    return frozenset(Scope(_bundle(), include_class_skills=False).reach)


@lru_cache(maxsize=1)
def _db2() -> dict[str, Any]:
    names = {int(r["ID"]): r["Name_lang"] for r in _csv("SpellName")}
    attrs: dict[int, tuple[int, ...]] = {}
    for r in _csv("SpellMisc"):
        if r["DifficultyID"] == "0":
            attrs[int(r["SpellID"])] = tuple(int(r[f"Attributes_{i}"]) & 0xFFFFFFFF for i in range(17))
    levels: dict[int, int] = {}
    for r in _csv("SpellLevels"):
        if r["DifficultyID"] == "0":
            levels[int(r["SpellID"])] = int(r["SpellLevel"])  # later rows overwrite (SpellMgr.cpp:5560-5562)
    families = {int(r["ID"]): r for r in _csv("CreatureFamily")}
    sla_by_skill: dict[int, list[dict[str, str]]] = defaultdict(list)
    for r in _csv("SkillLineAbility"):
        sla_by_skill[int(r["SkillLine"])].append(r)
    skill_names = {int(r["ID"]): r["DisplayName_lang"] for r in _csv("SkillLine")}
    specs = {int(r["ID"]): r for r in _csv("ChrSpecialization")}
    spec_spells: dict[int, list[dict[str, str]]] = defaultdict(list)
    for r in _csv("SpecializationSpells"):
        spec_spells[int(r["SpecID"])].append(r)
    auras_by_type: dict[int, list[int]] = defaultdict(list)
    summon_entries: set[int] = set()
    learn_pet: dict[int, list[int]] = defaultdict(list)
    for r in _csv("SpellEffect"):
        if r["DifficultyID"] != "0":
            continue
        eff, aura = int(r["Effect"]), int(r["EffectAura"])
        if aura and is_aura_effect(eff, aura):
            auras_by_type[aura].append(int(r["SpellID"]))
        if eff in (28, 56):
            summon_entries.add(int(r["EffectMiscValue_0"]))
        if eff == 57:
            learn_pet[int(r["EffectTriggerSpell"])].append(int(r["SpellID"]))
    return {"names": names, "attrs": attrs, "levels": levels, "families": families, "sla": sla_by_skill,
            "skill_names": skill_names, "specs": specs, "spec_spells": spec_spells, "auras_by_type": auras_by_type,
            "summon_entries": summon_entries, "learn_pet": learn_pet}


def spell_flags(spell: int) -> dict[str, Any]:
    d = _db2()
    if spell not in d["names"]:
        return {"exists": False}
    a = d["attrs"].get(spell, (0,) * 17)
    passive = bool(a[0] & ATTR0_PASSIVE)
    autocastable = not passive and not (a[1] & ATTR1_NO_AUTOCAST_AI)
    return {"exists": True, "name": d["names"][spell], "passive": passive, "autocastable": autocastable,
            "autocast_enabled_by_default": not (a[9] & ATTR9_AUTOCAST_OFF_BY_DEFAULT),
            "not_available_while_charmed": bool(a[5] & ATTR5_NOT_AVAILABLE_WHILE_CHARMED),
            "spell_level": d["levels"].get(spell, 0)}


def pet_decide_state(flags: dict[str, Any]) -> str:
    """Pet::addSpell ACT_DECIDE (Pet.cpp:1394-1402)."""
    if not flags["autocastable"]:
        return "ACT_PASSIVE"
    return "ACT_ENABLED" if flags["autocast_enabled_by_default"] else "ACT_DISABLED"


@lru_cache(maxsize=1)
def family_tables() -> dict[str, Any]:
    """levelup map, family passive store, per-family SLA classification."""
    d = _db2()
    levelup: dict[int, set[tuple[int, int]]] = defaultdict(set)
    passives: dict[int, set[int]] = defaultdict(set)
    rows = []
    for fam_id, fam in sorted(d["families"].items()):
        for j in (0, 1):
            skill = int(fam[f"SkillLine_{j}"])
            if not skill:
                continue
            for sla in d["sla"].get(skill, []):
                spell = int(sla["Spell"])
                acq = int(sla["AcquireMethod"])
                f = spell_flags(spell)
                paths = []
                reason = []
                if acq != ACQUIRE_AUTOMATIC_CHAR_LEVEL:
                    reason.append(f"AcquireMethod {acq} != 2")
                elif not f["exists"]:
                    reason.append("no SpellInfo (no SpellName row)")
                else:
                    if f["spell_level"]:
                        levelup[fam_id].add((f["spell_level"], spell))
                        paths.append("levelup")
                    else:
                        if f["passive"]:
                            passives[fam_id].add(spell)
                            paths.append("family-passive")
                        else:
                            reason.append("SpellLevel 0 and not passive: neither levelup (SpellMgr.cpp:2186) nor family passive (:5573)")
                rows.append({"family": fam_id, "family_name": fam["Name_lang"], "skill_line": skill,
                             "skill_line_name": d["skill_names"].get(skill, ""), "sla_id": int(sla["ID"]), "spell": spell,
                             "name": f.get("name", ""), "acquire_method": acq, "spell_level": f.get("spell_level"),
                             "passive": f.get("passive"), "paths": paths, "no_path_reason": reason})
    return {"levelup": levelup, "passives": passives, "rows": rows}


@lru_cache(maxsize=1)
def pet_script_constants() -> dict[int, list[str]]:
    """SPELL_* constants of src/server/scripts/Pet/*.cpp from the structural script index (dummy-corpora)."""
    out: dict[int, list[str]] = defaultdict(list)
    idx = json.loads((CORPORA.parent / "dummy-corpora" / "script-index.json").read_text(encoding="utf-8"))
    for path, consts in idx["constants"].items():
        if "/scripts/Pet/" not in path:
            continue
        for name, value in consts.items():
            if isinstance(value, int) and name.startswith("SPELL"):
                out[value].append(f"{path}:{name}")
    return dict(out)


@lru_cache(maxsize=1)
def creature_template_spells() -> dict[int, dict[int, int]]:
    b = _bundle()
    out: dict[int, dict[int, int]] = defaultdict(dict)
    for r in b.world.table("creature_template_spell").dicts():
        out[int(r["CreatureID"])][int(r["Index"])] = int(r["Spell"])
    return dict(out)


@lru_cache(maxsize=1)
def entry_unit_classes() -> dict[int, set[str]]:
    """creature entry -> Trinity unit classes it is summoned as (every DIFFICULTY_NONE effect 28/56)."""
    from controlled_units import inheritance
    out: dict[int, set[str]] = defaultdict(set)
    for spell in inheritance._db2()["effects"]:
        for e in inheritance.summon_effects(spell):
            cls = e["trinity_unit_class"]
            if cls == "Guardian" and e.get("controllable"):
                cls = "Guardian/CONTROLABLE"
            out[e["entry"]].add(cls)
    return dict(out)


#: which unit classes each path applies to (Trinity class that runs the acquisition code)
PATH_APPLIES = {"default": {"Pet"}, "charm": {"Guardian/CONTROLABLE"}, "totem": {"Totem"},
                "template-ai": {"Guardian", "Guardian/CONTROLABLE", "Minion", "Puppet", "TempSummon", "Totem"}}


def spell_paths(spell: int, entry: int | None = None, family: int | None = None) -> dict[str, Any]:
    """Every Trinity acquisition path that can give ``spell`` to a controlled unit."""
    d = _db2()
    b = _bundle()
    ft = family_tables()
    f = spell_flags(spell)
    paths: list[dict[str, Any]] = []
    for fam, s in ft["levelup"].items():
        for lvl, sid in s:
            if sid == spell and (family is None or fam == family):
                paths.append({"path": "levelup", "family": fam, "level": lvl})
    for fam, s in ft["passives"].items():
        if spell in s and (family is None or fam == family):
            paths.append({"path": "family-passive", "family": fam})
    cts = creature_template_spells()
    for cid, slots in sorted(cts.items()):
        if entry is not None and cid != entry:
            continue
        for idx, sid in sorted(slots.items()):
            if sid != spell:
                continue
            classes = entry_unit_classes().get(cid, set())
            p = {"entry": cid, "index": idx, "summoned_as": sorted(classes)}
            cand = []
            if idx < 4 and cid in d["summon_entries"]:
                cand.append({"path": "default", **p, "note": "dropped if the entry's family levelup set holds it (LoadPetDefaultSpells_helper)"})
            if idx < 4:
                cand.append({"path": "charm", **p, "skipped_attr5": f.get("not_available_while_charmed", False)})
            if idx < 2:
                cand.append({"path": "totem", **p})
            cand.append({"path": "template-ai", **p})
            for c in cand:
                c["applicable"] = bool(PATH_APPLIES[c["path"]] & classes) and not c.get("skipped_attr5", False)
                paths.append(c)
    for spec_id, rows in d["spec_spells"].items():
        spec = d["specs"].get(spec_id)
        if spec is None or spec["ClassID"] != "0":
            continue
        if any(int(r["SpellID"]) == spell for r in rows):
            paths.append({"path": "spec", "spec": spec_id, "spec_name": spec["Name_lang"],
                          "override": bool(int(spec["Flags"]) & SPEC_FLAG_PET_OVERRIDE)})
    for ref in pet_script_constants().get(spell, []):
        paths.append({"path": "script", "reference": ref})
    for r in b.world.table("spell_pet_auras").dicts():
        if int(r["aura"]) == spell:
            paths.append({"path": "pet-aura", "source_spell": int(r["spell"]), "pet": int(r["pet"]),
                          "source_exists": b.catalog.exists(int(r["spell"]))})
    for src in d["learn_pet"].get(spell, []):
        paths.append({"path": "learn-pet-spell", "source_spell": src})
    applicable = [p for p in paths if p.get("applicable", True)]
    status = "has-path" if applicable else ("build-skew" if b.skew.is_newer_than_trinity(spell) else "unresolved")
    return {"spell": spell, **f, "pet_default_active_state": pet_decide_state(f) if f.get("exists") else None,
            "script_bindings": b.script_names.get(spell, []), "in_player_scope": spell in _scope_reach(),
            "build_skew_newer_than_trinity": b.skew.is_newer_than_trinity(spell), "paths": paths, "status": status}


def pet_aura_types() -> list[dict[str, Any]]:
    b = _bundle()
    d = _db2()
    reach = _scope_reach()
    aura_file = TC_ROOT / "src/server/game/Spells/Auras/SpellAuraEffects.cpp"
    defs_file = TC_ROOT / "src/server/game/Spells/Auras/SpellAuraDefines.h"
    handler_line: dict[int, int] = {}
    define_line: dict[int, int] = {}
    if aura_file.exists():
        for n, line in enumerate(aura_file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = re.match(r"\s*&AuraEffect::\w+,\s*//\s*(\d+)", line)
            if m:
                handler_line.setdefault(int(m.group(1)), n)
    if defs_file.exists():
        for n, line in enumerate(defs_file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = re.match(r"\s*(SPELL_AURA_\w+)\s*=\s*(\d+)", line)
            if m:
                define_line.setdefault(int(m.group(2)), n)
    out = []
    for value, row in sorted(b.dispatch.aura_handlers.items()):
        name = row["name"] or ""
        if not PET_AURA_NAME.search(name):
            continue
        users = sorted(set(d["auras_by_type"].get(value, [])))
        in_scope = [s for s in users if s in reach]
        out.append({"aura": value, "name": name, "handler": row["handler"], "note": row.get("note"),
                    "define": f"{TC}Spells/Auras/SpellAuraDefines.h:{define_line.get(value, '?')}",
                    "handler_row": f"{TC}Spells/Auras/SpellAuraEffects.cpp:{handler_line.get(value, '?')}",
                    "implemented": row["handler"] not in ("HandleNULL", "HandleUnused"),
                    "spells_12_1": len(users), "spells_in_player_scope": len(in_scope),
                    "in_scope_examples": [{"spell": s, "name": d["names"].get(s, "")} for s in in_scope[:12]]})
    # 12.1 aura types absent from Trinity's handler table
    return out


def ability_census() -> dict[str, Any]:
    """Every pet-family SkillLineAbility spell + every creature_template_spell of a summoned entry."""
    ft = family_tables()
    d = _db2()
    b = _bundle()
    cts = creature_template_spells()
    spells: set[int] = {r["spell"] for r in ft["rows"]}
    for cid in d["summon_entries"]:
        spells.update(cts.get(cid, {}).values())
    for spec_id, rows in d["spec_spells"].items():
        if d["specs"].get(spec_id, {}).get("ClassID") == "0":
            spells.update(int(r["SpellID"]) for r in rows)
    counts: Counter[str] = Counter()
    by_path: Counter[str] = Counter()
    unresolved = []
    for s in sorted(spells):
        if not s:
            continue
        rec = spell_paths(s)
        if not rec.get("exists"):
            counts["no-spellinfo"] += 1
            continue
        counts[rec["status"]] += 1
        for p in {p["path"] for p in rec["paths"] if p.get("applicable", True)}:
            by_path[p] += 1
        if rec["status"] != "has-path":
            unresolved.append({"spell": s, "name": rec.get("name"), "status": rec["status"]})
    return {"population": "pet-family SkillLineAbility spells (CreatureFamily.SkillLine[0..1]) + creature_template_spell of every "
                          "entry summoned by a DIFFICULTY_NONE effect 28/56 + SpecializationSpells of ClassID-0 specs",
            "spells_total": len([s for s in spells if s]), "by_status": dict(sorted(counts.items())),
            "by_path": dict(sorted(by_path.items())), "without_path": unresolved}


WITNESS_ENTRIES = {416: "Imp", 17252: "Felguard", 55659: "Wild Imp", 143622: "Wild Imp (Inner Demons)", 26125: "Risen Ghoul",
                   24207: "Army of the Dead ghoul", 31216: "Mirror Image", 78116: "Water Elemental", 19668: "Shadowfiend",
                   62982: "Mindbender", 95061: "Fire Elemental", 95072: "Earth Elemental", 29264: "Spirit Wolf",
                   3527: "Healing Stream Totem", 103822: "Treant (Force of Nature)", 54983: "Treant (Grove Guardians)",
                   63508: "Xuen", 27829: "Ebon Gargoyle", 510: "Water Elemental (legacy)", 15438: "Fire Elemental (legacy)"}


def witness_entries() -> list[dict[str, Any]]:
    cts = creature_template_spells()
    out = []
    for entry, label in sorted(WITNESS_ENTRIES.items()):
        slots = cts.get(entry, {})
        out.append({"entry": entry, "label": label, "summoned_by_12_1_effect": entry in _db2()["summon_entries"],
                    "creature_template_spell": {str(k): v for k, v in sorted(slots.items())},
                    "spells": [spell_paths(s, entry=entry) for _, s in sorted(slots.items()) if s]})
    return out


def build_corpus() -> dict[str, Any]:
    ft = family_tables()
    fam_summary = Counter()
    for r in ft["rows"]:
        fam_summary["+".join(r["paths"]) or "none"] += 1
    felguard = spell_paths(89751)
    return {
        "provenance": {**st.provenance("python3 controlled_units.py spells"),
                       "world_database": "docs/research/dummy-corpora/trinity-server-overlay.json (creature_template_spell, spell_pet_auras, spell_script_names)",
                       "scope": "dummy_semantics.scope.Scope(Bundle(), include_class_skills=False)"},
        "acquisition_paths": PATH_DEFS,
        "availability_and_ai": AI_RULES,
        "execution_differences": EXECUTION_DIFFERENCES,
        "pet_aura_types": pet_aura_types(),
        "family_skill_line_abilities": {"rows": ft["rows"], "summary": dict(sorted(fam_summary.items())),
                                        "families_with_levelup": len(ft["levelup"]), "families_with_passives": len(ft["passives"])},
        "ability_census": ability_census(),
        "witness_entries": witness_entries(),
        "spotlight": {"felstorm_89751": felguard},
    }


def cmd_spells(args: argparse.Namespace) -> int:
    if args.spell is not None:
        print(json.dumps(spell_paths(args.spell, entry=args.entry), indent=1))
        return 0
    out = Path(args.out) if args.out else CORPORA / "spells.json"
    doc = build_corpus()
    st.write_json(out, doc)
    c = doc["ability_census"]
    print(f"wrote {out} ({c['spells_total']} census spells: {c['by_status']}; {len(doc['pet_aura_types'])} pet aura types)")
    return 0


def cmd_aura_types(args: argparse.Namespace) -> int:
    if not args.pet:
        raise SourceError("only --pet is implemented")
    rows = pet_aura_types()
    if args.json:
        print(json.dumps(rows, indent=1))
        return 0
    for r in rows:
        print(f"{r['aura']:>4} {r['name']:<50} {r['handler']:<28} 12.1:{r['spells_12_1']:>4} scope:{r['spells_in_player_scope']:>3}")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("spells", help="controlled-unit spell acquisition: --spell ID prints one spell, otherwise writes spells.json")
    p.add_argument("--spell", type=int)
    p.add_argument("--entry", type=int)
    p.add_argument("--out")
    p.set_defaults(func=cmd_spells)
    q = subparsers.add_parser("aura-types", help="pet-related aura types, their Trinity handler and 12.1/player-scope usage")
    q.add_argument("--pet", action="store_true", required=True)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_aura_types)
