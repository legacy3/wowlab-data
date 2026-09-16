"""Hardcoded SpellID consumers in the engine (outside the script system).

Source: the engine part of ``script-index.json`` (``case <int>:`` labels,
``== <int>`` comparisons and calls that carry a literal SpellID) for the files
listed in ``tools/tc_script_index.py::ENGINE_FILES``.

Two questions are answered per site:

1. **Is the literal a SpellID?**  Decided from the switch subject / compared
   expression text (``Id``, ``GetId()``, ``spellInfo``, ``m_spellInfo``,
   ``spellId``, ``triggered_spell_id`` ...) *and* existence in the snapshot
   catalog.  Anything else (creature entries, item ids, map ids, quest ids,
   faction ids) is kept as ``domain != spell`` for the record.
2. **What role does the site play?**  Decided from the enclosing function
   (a fixed table, ``FUNCTION_ROLES``): source correction / classification,
   legacy content policy, gameplay semantic in a generic handler, validation
   or navigation.  Within gameplay-semantic sites the case body's calls are
   summarised so identical bodies can be normalised into one family
   (``normalize``): e.g. 40 cases that all do ``CastSpell(target, <child>)``
   are one "cast authored child" family with 40 parameters, not 40 exceptions.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from .loaders import Bundle

SPELL_SUBJECT = re.compile(
    r"(Id\b|GetId\(\)|spellInfo|SpellInfo|m_spellInfo|spellId|spell_id|SpellID|triggered_spell_id|"
    r"triggerSpellId|GetSpellInfo\(\)|->Id|aura->|Aura|spellproto|procSpell|GetTriggeredByAuraSpell|"
    r"m_triggeredByAuraSpell|CastSpell|HasAura|RemoveAura|GetAuraEffect|GetSpellLinked|castItem)")
CREATURE_SUBJECT = re.compile(r"(GetEntry\(\)|entry\b|Entry\b|creature|Creature|GetCreatureTemplate|npc|petType|GetPetType)", re.I)
ITEM_SUBJECT = re.compile(r"(item|Item|proto|GetTemplate|enchant|Enchant)", re.I)
MAP_SUBJECT = re.compile(r"(map|Map|zone|Zone|area|Area|GetMapId|mapid|quest|Quest|faction|Faction|achievement|skill|Skill|race|class\b|Class\b|power|Power|form|Form|misc|Misc|mode|Mode|type|Type|Flag|flag|school|School|mechanic|Mechanic|state|State|slot|Slot|opcode|effect\b|Effect\b|EffectIndex|aura_type|AuraType|GetAuraType|GetSpellEffectInfo|TargetA|TargetB|team|Team|gender|Gender|level|Level|Mask|mask|index|Index|stat|Stat)")

#: enclosing function -> (role, note).  Everything not listed is "gameplay-semantic" if it
#: lives in a handler, else "unclassified".
FUNCTION_ROLES: dict[str, tuple[str, str]] = {
    "SpellMgr::LoadSpellInfoCorrections": ("source-correction", "data patch applied at load (see corrections module)"),
    "SpellMgr::LoadSpellInfoCustomAttributes": ("source-classification", "computed AttributesCu bits with hardcoded exceptions"),
    "SpellMgr::LoadSpellInfoSpellSpecificAndAuraState": ("source-classification", "SpellSpecific / AuraState derivation"),
    "SpellInfo::_LoadSpellSpecific": ("source-classification", "SpellSpecificType from family flags / ids"),
    "SpellInfo::_LoadAuraState": ("source-classification", "AuraStateType from family / ids"),
    "SpellInfo::_IsPositiveEffect": ("source-classification", "positivity decision"),
    "SpellInfo::CheckLocation": ("legacy-content-policy", "battleground / Wintergrasp area rules"),
    "SpellInfo::CheckTarget": ("target-validation", "target legality exceptions"),
    "SpellInfo::CheckExplicitTarget": ("target-validation", "explicit target exceptions"),
    "SpellInfo::CheckShapeshift": ("cast-validation", "shapeshift exceptions"),
    "SpellInfo::GetDiminishingReturnsGroupForSpell": ("source-classification", "DR grouping by id/family"),
    "SpellInfo::_LoadImmunityInfo": ("source-classification", "immunity derivation"),
    "SpellInfo::IsAuraExclusiveBySpecificWith": ("source-classification", "stacking specifics"),
    "SpellInfo::IsAuraExclusiveBySpecificPerCasterWith": ("source-classification", "stacking specifics"),
    "SpellInfo::GetMaxRange": ("source-correction", "range exceptions"),
    "Spell::EffectDummy": ("gameplay-semantic", "hardcoded Dummy effect consumer"),
    "Spell::EffectScriptEffect": ("gameplay-semantic", "hardcoded ScriptEffect consumer"),
    "Spell::EffectTriggerSpell": ("gameplay-semantic", "special-cased trigger spells"),
    "Spell::EffectForceCast": ("gameplay-semantic", "special-cased force casts"),
    "Spell::EffectApplyGlyph": ("gameplay-semantic", "glyph handling"),
    "Spell::EffectSummonType": ("gameplay-semantic", "summon special cases"),
    "Spell::EffectDispel": ("gameplay-semantic", "dispel exceptions"),
    "Spell::EffectKnockBack": ("gameplay-semantic", "knockback exceptions"),
    "Spell::EffectEnergize": ("gameplay-semantic", "energize exceptions"),
    "Spell::EffectWeaponDmg": ("gameplay-semantic", "weapon damage exceptions"),
    "Spell::EffectHeal": ("gameplay-semantic", "heal exceptions"),
    "Spell::EffectSchoolDMG": ("gameplay-semantic", "damage exceptions"),
    "Spell::EffectResurrectPet": ("gameplay-semantic", "pet resurrection"),
    "Spell::EffectSummonPet": ("gameplay-semantic", "pet summon"),
    "Spell::CheckCast": ("cast-validation", "cast legality exceptions"),
    "Spell::CheckItems": ("cast-validation", "item requirement exceptions"),
    "Spell::CheckRange": ("cast-validation", "range exceptions"),
    "Spell::CheckPetCast": ("cast-validation", "pet cast exceptions"),
    "Spell::CheckEffectTarget": ("target-validation", "per-effect target exceptions"),
    "Spell::prepareDataForTriggerSystem": ("proc-classification", "proc flag preparation exceptions"),
    "Spell::DoTriggersOnSpellHit": ("gameplay-semantic", "spell_linked_spell HIT + exceptions"),
    "Spell::UpdatePointers": ("navigation", "pointer refresh"),
    "Spell::TakeReagents": ("gameplay-semantic", "reagent exceptions"),
    "Spell::TakePower": ("gameplay-semantic", "power cost exceptions"),
    "Spell::SelectImplicitCasterDestTargets": ("target-adapter", "destination exceptions"),
    "Spell::SelectImplicitTargetDestTargets": ("target-adapter", "destination exceptions"),
    "Spell::SelectImplicitDestDestTargets": ("target-adapter", "destination exceptions"),
    "Spell::SearchAreaTargets": ("target-adapter", "area search exceptions"),
    "AuraEffect::HandleAuraDummy": ("gameplay-semantic", "hardcoded Dummy aura consumer"),
    "AuraEffect::CalculateAmount": ("amount-adapter", "hardcoded amount rules per aura type / id"),
    "AuraEffect::CalculatePeriodic": ("amount-adapter", "hardcoded period rules"),
    "AuraEffect::CalculateSpellMod": ("amount-adapter", "spell mod construction rules"),
    "AuraEffect::HandleShapeshiftBoosts": ("gameplay-semantic", "shapeshift form -> passive spells table"),
    "AuraEffect::HandleAuraModShapeshift": ("gameplay-semantic", "shapeshift exceptions"),
    "AuraEffect::HandleAuraTransform": ("gameplay-semantic", "transform display exceptions"),
    "AuraEffect::HandlePeriodicDamageAurasTick": ("gameplay-semantic", "periodic damage exceptions"),
    "AuraEffect::HandlePeriodicHealAurasTick": ("gameplay-semantic", "periodic heal exceptions"),
    "AuraEffect::HandleProcTriggerSpellAuraProc": ("gameplay-semantic", "proc trigger exceptions"),
    "AuraEffect::HandleAuraModDisarm": ("gameplay-semantic", "disarm slot"),
    "AuraEffect::HandleAuraLinked": ("gameplay-semantic", "linked aura"),
    "Aura::HandleAuraSpecificMods": ("gameplay-semantic", "hardcoded apply/remove side effects (spell_area, spell_linked_spell + family switch)"),
    "Aura::CanStackWith": ("source-classification", "stacking exceptions"),
    "Aura::_ApplyForTarget": ("gameplay-semantic", "apply exceptions"),
    "Aura::CalcMaxDuration": ("amount-adapter", "duration exceptions"),
    "Aura::IsProcOnCooldown": ("proc-classification", "proc cooldown exceptions"),
    "Aura::GetProcEffectMask": ("proc-classification", "proc eligibility exceptions"),
    "Unit::HandleDummyAuraProc": ("gameplay-semantic", "legacy dummy proc consumer"),
    "Unit::HandleProcTriggerSpell": ("gameplay-semantic", "legacy proc trigger consumer"),
    "Unit::SpellDamageBonusDone": ("amount-adapter", "damage bonus exceptions"),
    "Unit::SpellDamageBonusTaken": ("amount-adapter", "damage taken exceptions"),
    "Unit::SpellHealingBonusDone": ("amount-adapter", "heal bonus exceptions"),
    "Unit::SpellHealingBonusTaken": ("amount-adapter", "heal taken exceptions"),
    "Unit::MeleeDamageBonusDone": ("amount-adapter", "melee bonus exceptions"),
    "Unit::SpellCritChanceDone": ("amount-adapter", "crit exceptions"),
    "Unit::SpellCritChanceTaken": ("amount-adapter", "crit taken exceptions"),
    "Unit::SpellCriticalDamageBonus": ("amount-adapter", "crit damage exceptions"),
    "Unit::GetShapeshiftFormModelId": ("navigation", "display ids"),
    "Unit::GetModelForForm": ("navigation", "display ids"),
    "Unit::GetTotalAuraModifierByMiscMask": ("amount-adapter", "aura modifier exceptions"),
    "Unit::ProcSkillsAndAuras": ("proc-classification", "proc dispatch exceptions"),
    "Unit::RemoveAurasDueToSpellBySteal": ("gameplay-semantic", "spellsteal exceptions"),
    "Unit::CalcAbsorbResist": ("amount-adapter", "absorb exceptions"),
    "Unit::CalcHealAbsorb": ("amount-adapter", "heal absorb exceptions"),
    "Unit::SetShapeshiftForm": ("gameplay-semantic", "form state"),
    "Unit::GetCastSpellInfo": ("gameplay-semantic", "OVERRIDE_ACTIONBAR_SPELLS consumer"),
    "Player::CastItemCombatSpell": ("proc-classification", "legacy item proc"),
    "Player::ApplyEquipSpell": ("gameplay-semantic", "equip spell exceptions"),
    "Player::UpdateAreaDependentAuras": ("gameplay-semantic", "spell_area consumer"),
    "Player::LearnSpell": ("acquisition", "learn exceptions"),
    "Player::AddSpell": ("acquisition", "learn exceptions"),
    "Player::LearnDefaultSkills": ("acquisition", "default skills"),
    "Player::UpdateSkillsForLevel": ("acquisition", "skill exceptions"),
    "Player::RewardQuest": ("legacy-content-policy", "quest rewards"),
    "Player::UpdatePvP": ("legacy-content-policy", "pvp state"),
    "Player::SetSkill": ("acquisition", "skill exceptions"),
    "Pet::CreateBaseAtCreature": ("gameplay-semantic", "pet creation"),
    "Pet::LoadPetFromDB": ("navigation", "pet load"),
    "Pet::InitStatsForLevel": ("amount-adapter", "pet stats by creature entry"),
    "Pet::CastPetAuras": ("gameplay-semantic", "spell_pet_auras consumer"),
    "Pet::LearnPetPassives": ("acquisition", "pet passives"),
    "Pet::InitLevelupSpellsForLevel": ("acquisition", "pet levelup spells"),
    "Guardian::InitStatsForLevel": ("amount-adapter", "guardian stats by creature entry"),
    "Guardian::UpdateAttackPowerAndDamage": ("amount-adapter", "guardian AP by creature entry"),
    "Guardian::UpdateDamagePhysical": ("amount-adapter", "guardian damage by creature entry"),
    "Totem::InitStats": ("gameplay-semantic", "totem stats"),
    "Item::GetItemLevel": ("amount-adapter", "item level exceptions"),
    "ConditionMgr::isSourceTypeValid": ("validation", "condition validation"),
    "ConditionMgr::isConditionTypeValid": ("validation", "condition validation"),
    "ConditionMgr::addToLootTemplate": ("validation", "condition wiring"),
    "ObjectMgr::LoadSpellScriptNames": ("validation", "script binding validation"),
    "ObjectMgr::ValidateSpellScripts": ("validation", "script binding validation"),
    "ThreatManager::ForwardThreatForAssistingMe": ("gameplay-semantic", "threat exceptions"),
    "ThreatManager::AddThreat": ("gameplay-semantic", "threat exceptions"),
}
GAMEPLAY_HANDLER_PREFIXES = ("Spell::Effect", "AuraEffect::Handle", "Aura::Handle", "Unit::Handle")


@dataclass
class Site:
    file: str
    function: str
    kind: str                 # case | compare | call
    value: int
    line: int
    subject: str
    domain: str               # spell | creature | item | other | unknown
    exists_as_spell: bool
    role: str
    role_note: str
    body_calls: dict[str, int] = field(default_factory=dict)
    body_ints: list[int] = field(default_factory=list)
    callee: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def classify_domain(subject: str, exists: bool) -> str:
    if SPELL_SUBJECT.search(subject):
        return "spell" if exists else "spell-like-missing"
    if CREATURE_SUBJECT.search(subject):
        return "creature"
    if ITEM_SUBJECT.search(subject):
        return "item"
    if MAP_SUBJECT.search(subject):
        return "other"
    return "unknown" if not exists else "unknown-exists-as-spell"


def function_role(func: str) -> tuple[str, str]:
    if func in FUNCTION_ROLES:
        return FUNCTION_ROLES[func]
    base = func.split("#")[0]
    if base in FUNCTION_ROLES:
        return FUNCTION_ROLES[base]
    if base.startswith(GAMEPLAY_HANDLER_PREFIXES):
        return ("gameplay-semantic", "generic handler with hardcoded ids (unlisted function)")
    if base.startswith(("SpellMgr::Load", "ObjectMgr::Load", "SpellInfo::_Load", "SpellInfo::_Init")):
        return ("source-classification", "load-time derivation (unlisted function)")
    if "Check" in base or "Validate" in base or "IsValid" in base:
        return ("validation", "unlisted validation function")
    return ("unclassified", "unlisted function")


def normalize_body(calls: dict[str, int], ints: list[int]) -> str:
    """Body shape key for family normalisation (calls that produce actions, ordered)."""
    action = [c for c in ("CastSpell", "AddAura", "RemoveAurasDueToSpell", "RemoveAura", "RemoveAurasByType",
                          "RemoveMovementImpairingAuras", "SetStackAmount", "ModStackAmount", "SetCharges", "ModCharges",
                          "ResetCooldown", "ModifyCooldown", "AddThreat", "PlayDirectSound", "PlayDistanceSound", "SetEntry",
                          "ApplySpellImmune", "SetChampioningFaction", "MoveFall", "UnSummon", "DespawnOrUnsummon", "DestroyItemCount",
                          "RemoveItem", "ModifyPower", "EnergizeBySpell", "DealDamage", "SetHealth", "LeaveBattleground",
                          "CastCustomSpell", "AddCooldown", "HasCooldown", "GetRemainingCooldown", "CalculatePct", "GetMaxPower")
              if c in calls]
    if not action:
        return "no-action" if not calls else "query-only"
    return "+".join(action[:4]) + (":child" if ints else "")


class HardcodedIndex:
    def __init__(self, bundle: Bundle) -> None:
        self.b = bundle
        self.sites: list[Site] = []
        self._build()

    def _build(self) -> None:
        cat = self.b.catalog
        for rel, data in self.b.index.engine.items():
            if data.get("missing"):
                continue
            for func, facts in data["functions"].items():
                role, note = function_role(func)
                for c in facts.get("case_ints", []):
                    v = c.get("value")
                    if v is None or v < 100:
                        continue
                    exists = cat.exists(v)
                    self.sites.append(Site(rel, func, "case", v, c["line"], c.get("subject", ""),
                                           classify_domain(c.get("subject", ""), exists), exists, role, note,
                                           c.get("body_calls", {}), c.get("body_ints", [])))
                for c in facts.get("cmp_ints", []):
                    v = c["value"]
                    exists = cat.exists(v)
                    self.sites.append(Site(rel, func, "compare", v, c["line"], c.get("other", ""),
                                           classify_domain(c.get("other", ""), exists), exists, role, note))
                for c in facts.get("id_calls", []):
                    for v in c.get("ints", []):
                        if v < 100:
                            continue
                        exists = cat.exists(v)
                        subject = c["callee"]
                        self.sites.append(Site(rel, func, "call", v, c["line"], subject,
                                               "spell" if exists and c["callee"] in ("CastSpell", "HasAura", "GetSpellInfo", "AssertSpellInfo",
                                                                                     "RemoveAurasDueToSpell", "RemoveAura", "GetAura",
                                                                                     "GetAuraEffect", "HasAuraEffect", "AddAura", "ResetCooldown",
                                                                                     "GetAuraOfRankedSpell", "HasSpell", "CastCustomSpell")
                                               else classify_domain(subject, exists), exists, role, note, callee=c["callee"]))

    # ------------------------------------------------------------------
    def spell_sites(self, spells: set[int] | None = None) -> list[Site]:
        return [s for s in self.sites if s.domain == "spell" and (spells is None or s.value in spells)]

    def by_spell(self) -> dict[int, list[Site]]:
        out: dict[int, list[Site]] = defaultdict(list)
        for s in self.spell_sites():
            out[s.value].append(s)
        return out

    def census(self, spells: set[int] | None = None) -> dict[str, Any]:
        sites = self.spell_sites(spells)
        by_role = Counter(s.role for s in sites)
        by_func = Counter(s.function for s in sites)
        # normalised families among gameplay-semantic case sites
        fam = Counter()
        fam_members: dict[str, set[int]] = defaultdict(set)
        for s in sites:
            if s.role == "gameplay-semantic" and s.kind == "case":
                key = f"{s.function}::{normalize_body(s.body_calls, s.body_ints)}"
                fam[key] += 1
                fam_members[key].add(s.value)
        return {
            "raw_spell_id_sites": len(sites),
            "distinct_spell_ids": len({s.value for s in sites}),
            "by_role": dict(by_role.most_common()),
            "by_function": dict(by_func.most_common(40)),
            "domains_all_sites": dict(Counter(s.domain for s in self.sites if spells is None or s.value in spells).most_common()),
            "gameplay_case_families": {k: {"cases": n, "spells": sorted(fam_members[k])[:12]} for k, n in fam.most_common()},
            "gameplay_case_family_count": len(fam),
            "gameplay_case_count": sum(fam.values()),
        }
