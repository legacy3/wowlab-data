#!/usr/bin/env python3
"""Structural index over TrinityCore spell/aura scripts and engine hardcoded IDs.

Research-only navigation aid built with tree-sitter (``tree-sitter-cpp``).  It
extracts *syntax*, never semantics: which classes derive from ``SpellScript`` /
``AuraScript`` / ``AreaTriggerAI``, what each ``Register()`` installs, what each
handler body calls, which numeric constants it references, where the engine
switches on literal SpellIDs, and what ``LoadSpellInfoCorrections`` writes.

Everything downstream (``scripts/research/dummy_semantics``) treats this index
as evidence to be verified against the consumers documented in
``docs/research/dummy-server-semantics-archaeology.md``.

Run with::

    cd scripts/research
    uv run --with tree-sitter==0.25.2 --with tree-sitter-cpp==0.23.4 \\
        python tools/tc_script_index.py

Output: ``docs/research/dummy-corpora/script-index.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

import tree_sitter_cpp
from tree_sitter import Language, Node, Parser

HERE = Path(__file__).resolve()
WOWLAB_DATA = HERE.parents[3]
WORKSPACE_PARENT = WOWLAB_DATA.parent
DEFAULT_OUT = WOWLAB_DATA / "docs/research/dummy-corpora/script-index.json"

LANG = Language(tree_sitter_cpp.language())
PARSER = Parser(LANG)

SCRIPT_BASES = {
    "SpellScript", "AuraScript", "AreaTriggerAI", "SpellScriptLoader",
    "CreatureScript", "GameObjectScript", "AreaTriggerEntityScript",
    "PlayerScript", "UnitScript", "ItemScript", "WorldScript",
    "ScriptedAI", "BossAI", "PetAI", "PassiveAI", "NullCreatureAI",
    "SmartAI", "TurretAI", "CasterAI", "AggressorAI", "CombatAI",
    "AreaTriggerScript", "OnlyOnceAreaTriggerScript", "ConversationScript",
    "SceneScript", "QuestScript", "VehicleScript", "InstanceMapScript",
    "AchievementCriteriaScript", "PlayerChoiceScript", "GenericSpellScript",
}
REG_MACROS = {
    "RegisterSpellScript", "RegisterSpellScriptWithArgs",
    "RegisterSpellAndAuraScriptPair", "RegisterSpellAndAuraScriptPairWithArgs",
    "RegisterAreaTriggerAI", "RegisterCreatureAI", "RegisterCreatureAIWithFactory",
    "RegisterGameObjectAI", "RegisterGameObjectAIWithFactory",
    "RegisterConversationScript", "RegisterSceneScript", "RegisterQuestScript",
    "RegisterPlayerScript", "RegisterPlayerChoiceScript",
}
HOOK_FN_MACROS = re.compile(r"^(Spell|Aura|BeforeSpell)\w*Fn$")

#: callee -> category.  A call is recorded in full only when its callee is here
#: (or when it carries a resolved integer argument); everything else is counted.
VOCAB: dict[str, str] = {}
for _cat, _names in {
    "cast": ["CastSpell", "CastCustomSpell", "AddAura", "CastStop", "InterruptNonMeleeSpells",
             "InterruptSpell", "SendPlaySpellVisual", "SendPlaySpellVisualKit", "AddSpellMod",
             "AddSpellBP", "SetTriggerFlags", "SetOriginalCaster", "SetTriggeringSpell",
             "SetTriggeringAura", "SetCastItem", "SetCustomArg", "SetOriginalCastId",
             "SetCastDifficulty", "SetScriptResult", "SetScriptWaitsForSpellHit", "FinishCast"],
    "prevent": ["PreventDefaultAction", "PreventHitDefaultEffect", "PreventHitEffect",
                "PreventHitAura", "PreventHitDamage", "PreventHitHeal"],
    "amount": ["SetHitDamage", "SetHitHeal", "SetEffectValue", "GetEffectValue", "GetHitDamage",
               "GetHitHeal", "GetEffectValueAsInt", "GetAmount", "GetAmountAsInt", "CalculatePct",
               "AddPct", "ApplyPct", "ApplyPercentModFloatVar", "CalcValue", "CalcValueAsInt",
               "SetAmount", "ChangeAmount", "RecalculateAmount", "GetTotalAttackPowerValue",
               "SpellBaseDamageBonusDone", "SpellBaseHealingBonusDone", "SpellDamageBonusDone",
               "SpellHealingBonusDone", "GetMaxHealth", "GetHealth", "CountPctFromMaxHealth",
               "CountPctFromCurHealth", "GetHealthPct", "GetPower", "GetMaxPower", "GetPowerPct",
               "GetDamage", "GetHeal", "GetOriginalDamage", "GetOriginalHeal", "GetAbsorb",
               "GetEffectiveHeal", "GetSpellPowerModifier", "GetStat", "GetArmor",
               "GetWeaponDamageRange", "GetBaseAmount", "GetEstimatedAmount", "CalcPeriodicCritChance",
               "GetVersatilityBonus", "GetRatingBonusValue", "GetTotalAuraModifier",
               "GetTotalAuraMultiplier", "GetTotalAuraModifierByMiscMask", "GetTotalAuraModifierByMiscValue",
               "std::max", "std::min", "std::clamp", "RoundToInterval", "GetMeleeCritChance",
               "GetUnitSpellCriticalChance", "GetUnitCriticalChanceDone", "ModifyAuraState",
               "GetCritChance", "SetDamage", "SetHeal", "AbsorbDamage", "ModifyDamage", "ResistDamage",
               "GetRemainingTicks", "GetTotalTicks", "GetPeriod", "GetPeriodicTimer"],
    "aura": ["RemoveAura", "RemoveAurasDueToSpell", "RemoveAurasByType", "RemoveAuraFromStack",
             "RemoveOwnedAura", "RemoveAppliedAuras", "RemoveAurasWithFamily", "RemoveAurasWithAttribute",
             "RemoveMovementImpairingAuras", "RemoveAurasWithMechanic", "RemoveAurasByShapeShift",
             "GetAura", "HasAura", "GetAuraEffect", "HasAuraEffect", "GetAuraOfRankedSpell",
             "GetAuraEffectOfRankedSpell", "ModStackAmount", "SetStackAmount", "GetStackAmount",
             "DropCharge", "ModCharges", "SetCharges", "GetCharges", "SetDuration", "RefreshDuration",
             "GetDuration", "GetMaxDuration", "SetMaxDuration", "Remove", "GetAuraCount",
             "GetAuraApplication", "GetAuraEffectsByType", "GetAuraApplicationOfRankedSpell",
             "HasAuraType", "HasAuraTypeWithMiscvalue", "HasAuraWithMechanic", "GetAuraEffectDummy",
             "GetAuraEffectByFamilyFlag", "GetAuraEffectsByTypeAndCasterGUID", "GetOwnedAura",
             "RefreshTimers", "ResetPeriodic", "SetPeriodicTimer", "GetRemainingCharges",
             "GetTargetApplication", "SetPeriodic", "GetSpellEffectInfo", "GetEffIndex",
             "ModDuration", "GetApplicationOfTarget", "GetApplicationList", "GetTargetList"],
    "cooldown": ["ResetCooldown", "ModifyCooldown", "ModifyChargeRecoveryTime", "RestoreCharge",
                 "ConsumeCharge", "ResetCharges", "StartCooldown", "AddCooldown", "HasCooldown",
                 "GetRemainingCooldown", "GetChargeRecoveryTime", "ModifyCoooldowns",
                 "ResetAllCooldowns", "ModifySpellCooldown", "ModifyCooldowns", "GetRemainingCategoryCooldown",
                 "HasCharge", "GetMaxCharges", "ModifyChargeRecoveryTime", "GetSpellHistory",
                 "ResetAllCharges", "ForceSendSpellCharge", "ForceSendSetSpellCharges"],
    "power": ["ModifyPower", "SetPower", "EnergizeBySpell", "SetHealth", "ModifyHealth",
              "HealBySpell", "GetPowerType", "SetPowerType",
              "CalcAbsorbResist",
              "GetComboPoints", "ClearComboPoints", "AddComboPoints", "SetMaxPower", "SetFullPower",
              "SetFullHealth", "GetMaxHealthByPercent", "GetPowerIndex", "AddComboPoints"],
    "direct-damage": ["DealDamage", "DealHeal", "Kill", "DealSpellDamage", "DealMeleeDamage", "SendSpellNonMeleeDamageLog",
                      "SpellNonMeleeDamageLog", "KillSelf", "SendSpellDamageResist", "SendSpellDamageImmune"],
    "movement": ["NearTeleportTo", "JumpTo", "KnockbackFrom", "MoveJump", "MoveCharge", "TeleportTo", "MovePoint",
                 "GetMotionMaster", "SetFacingTo", "SetFacingToObject", "StopMoving", "MoveFall", "MoveIdle", "Clear",
                 "MoveFollow", "MoveChase", "MoveTakeoff", "MoveLand", "MoveCirclePath", "MoveBackwards"],
    "areatrigger": ["GetAreaTriggers", "GetAreaTrigger", "GetInsideUnits", "GetAreaTriggerCreateProperties",
                    "SetDestination", "GetOrbit", "InitSplines", "GetTimeToTarget", "GetTimeSinceCreated"],
    "rng": ["roll_chance_i", "roll_chance_f", "urand", "irand", "frand", "rand_norm", "rand_chance",
            "RandomResize", "SelectRandomContainerElement", "RandomShuffle",
            "SelectRandomWeightedContainerElement", "urandms", "rand32", "roll_chance"],
    "delay": ["AddEvent", "AddEventAtOffset", "Schedule", "Repeat", "GetScheduler", "Update",
              "KillAllEvents", "CancelEventGroup", "DelayGroup", "CancelAll", "ScheduleEvent",
              "ModifyPeriodicTimer"],
    "summon": ["SummonCreature", "SummonGameObject", "CreateAreaTrigger", "GetSummonedCreatureByEntry",
               "GetPet", "GetGuardianPet", "GetCharmerOrOwner", "GetOwner", "GetSummoner",
               "GetAllMinionsByEntry", "GetCharmedOrSelf", "GetCharmerOrOwnerOrSelf",
               "GetCharmerOrOwnerPlayerOrPlayerItself", "UnSummon", "DespawnOrUnsummon",
               "SummonPet", "GetFirstMinion", "GetMinionGUID", "IsSummon", "IsGuardian", "IsPet",
               "ToTempSummon", "ToPet", "GetCreatorGUID", "GetMinionByEntry", "SetOwnerGUID",
               "GetSummonerGUID", "GetSummonedCreatureGUIDs", "RemoveAllMinionsByEntry"],
    "target": ["remove_if", "resize", "sort", "clear", "push_back", "erase", "front", "back",
               "GetHitUnit", "GetExplTargetUnit", "GetExplTargetWorldObject", "GetExplTargetDest",
               "SelectNearestTarget", "GetOriginalCaster", "GetCaster", "GetTarget", "GetUnitOwner",
               "GetActor", "GetActionTarget", "GetProcTarget", "IsInPartyWith", "IsInRaidWith",
               "IsFriendlyTo", "IsHostileTo", "IsValidAttackTarget", "IsValidAssistTarget",
               "IsPlayer", "GetGroup", "GetDistance", "IsWithinDist", "IsWithinDistInMap",
               "GetTypeId", "ToPlayer", "ToUnit", "ToCreature", "GetVictim", "GetCasterGUID",
               "IsAlive", "IsDead", "GetOwnerGUID", "GetGUID", "IsInCombat", "GetHitDest",
               "SetExplTargetDest", "GetUnitTargetCountForEffect", "GetSpellValue", "GetSpell",
               "SelectTarget", "IsInWorld", "GetExactDist", "GetExactDist2d", "IsUnit",
               "IsCreature", "HasUnitState", "IsPlayer", "IsCharmed", "GetCharmer", "GetPetGUID",
               "GetTargetGUID", "GetCurrentSpell", "GetLastDamagedTargetGuid", "GetMap",
               "GetPosition", "GetNearPosition", "GetRandomPoint", "GetFirstCollisionPosition",
               "MovePositionToFirstCollision", "IsWithinLOSInMap", "IsWithinMeleeRange",
               "GetSpellMaxRangeForTarget", "GetRoleForGroup", "GetGroupRole", "GetPlayerListInGrid",
               "GetPartyMembers", "GetRaidMembers", "GetHealthPct", "GetLevel", "IsPvP"],
    "state": ["GetShapeshiftForm", "GetPrimarySpecialization", "GetClass", "HasSpell", "GetSpellInfo",
              "GetProcSpell", "GetDamageInfo", "GetHealInfo", "GetTriggeringSpell", "GetCastItem",
              "GetId", "GetSchoolMask", "GetHitMask", "GetTypeMask", "GetSpellPhaseMask",
              "GetSpellTypeMask", "GetEffectInfo", "GetEffect", "GetTriggeredByAura", "GetSpellInfo",
              "HasAttribute", "IsPositive", "GetAttackType", "GetSpellCastResult", "GetCastTime",
              "GetPowerCost", "GetSpellHistory", "GetRace", "GetGender", "GetPowerType",
              "IsAffectingSpell", "GetSpellModOwner", "GetAffectedEffectsMask", "GetAuraType",
              "GetMiscValue", "GetMiscValueB", "GetTickNumber", "IsInCombat", "IsMounted",
              "HasUnitFlag", "HasUnitState", "GetCreatureType", "GetRemoveMode", "GetTriggeringAuraEffect",
              "GetSpellValue", "GetSpellXSpellVisualId", "HasLabel", "GetLabels", "IsRanked",
              "GetRank", "GetEmpowerStage", "GetEmpoweredSpellStage", "GetUnitMovementFlags",
              "IsMoving", "GetHitDamage", "IsFullHealth", "IsCritical", "GetSpellCastFlags"],
    "learn": ["LearnSpell", "RemoveSpell", "AddTemporarySpell", "RemoveTemporarySpell",
              "SetOverrideSpellsId", "AddOverrideSpell", "RemoveOverrideSpell", "CastItemCombatSpell"],
    "amount2": ["SetSpellValue", "HealthBelowPct", "HealthAbovePct", "HealthBelowPctDamaged", "ApplySpellMod",
                "GetUnitTargetIndexForEffect", "CalcPowerCost", "GetPowerTypeCostAmount", "CountPctFromMaxHealth"],
    "state2": ["GetScript", "HasUnitMovementFlag", "isInBack", "isInFront", "IsInBack", "IsInFront", "GetSingleCastAuras",
               "GetApplicationVector", "GetPrimarySpecializationEntry", "HasItemCount", "GetItemByEntry", "GetWeaponForAttack",
               "IsWithinCombatRange", "GetSpellModOwner", "HasAuraTypeWithAffectMask", "HasAuraTypeWithFamilyFlags",
               "IsAuraEffectPositive", "IsPositiveEffect", "GetTotalAuraModifierByAffectMask"],
    "summon2": ["ModifyTimer", "SetTimer", "GetTimer", "InitCharmInfo", "SetReactState"],
}.items():
    _cat = {"amount2": "amount", "state2": "state", "summon2": "summon"}.get(_cat, _cat)
    for _n in _names:
        VOCAB.setdefault(_n, _cat)

TOKEN_RE = re.compile(
    r"^(SPELLVALUE_|TRIGGERED_|EFFECT_(\d|ALL|FIRST_FOUND)|SPELL_AURA_|SPELL_EFFECT_|TARGET_|POWER_|"
    r"AURA_REMOVE_|PROC_(FLAG|HIT|SPELL)|SPELL_ATTR|MECHANIC_|SPELL_SCHOOL|SPELLMOD_|SPELL_FAILED_|"
    r"SPELL_CAST_OK|AURA_EFFECT_HANDLE_|UNIT_STATE_|IMMUNITY_|DIFFICULTY_|SPELL_MISS_|SPELLFAMILY_|"
    r"ChrSpecialization::|SpellEffectAttributes|SpellModOp|CURRENT_|BASE_ATTACK|OFF_ATTACK|RANGED_ATTACK|"
    r"SPELL_DIRECT_DAMAGE|DOT|HEAL\b|SPELL_SCRIPT_HOOK|AURA_SCRIPT_HOOK|SpellCastResult|"
    r"CastSpellExtraArgs|SpellCastTargets|DamageInfo|HealInfo|ProcEventInfo|Milliseconds|Seconds)")

ENGINE_FILES = [
    "src/server/game/Spells/Spell.cpp", "src/server/game/Spells/SpellEffects.cpp",
    "src/server/game/Spells/SpellInfo.cpp", "src/server/game/Spells/SpellMgr.cpp",
    "src/server/game/Spells/SpellHistory.cpp", "src/server/game/Spells/SpellScript.cpp",
    "src/server/game/Spells/Auras/SpellAuras.cpp", "src/server/game/Spells/Auras/SpellAuraEffects.cpp",
    "src/server/game/Entities/Unit/Unit.cpp", "src/server/game/Entities/Player/Player.cpp",
    "src/server/game/Entities/Pet/Pet.cpp", "src/server/game/Entities/Creature/TemporarySummon.cpp",
    "src/server/game/Entities/Creature/Creature.cpp", "src/server/game/Entities/Totem/Totem.cpp",
    "src/server/game/Entities/Item/Item.cpp", "src/server/game/Entities/Item/ItemEnchantmentMgr.cpp",
    "src/server/game/Entities/AreaTrigger/AreaTrigger.cpp", "src/server/game/Combat/ThreatManager.cpp",
    "src/server/game/Conditions/ConditionMgr.cpp", "src/server/game/Scripting/ScriptMgr.cpp",
    "src/server/game/Globals/ObjectMgr.cpp", "src/server/game/Entities/Unit/StatSystem.cpp",
    "src/server/game/Entities/Creature/Guardian.cpp", "src/server/game/Entities/Creature/Minion.cpp",
    "src/server/game/Entities/DynamicObject/DynamicObject.cpp", "src/server/game/Entities/Unit/CharmInfo.cpp",
    "src/server/game/Entities/Player/PlayerTaxi.cpp", "src/server/game/Handlers/SpellHandler.cpp",
    "src/server/game/Handlers/PetHandler.cpp", "src/server/game/AI/CoreAI/PetAI.cpp",
    "src/server/game/AI/CoreAI/UnitAI.cpp", "src/server/game/AI/CreatureAI.cpp",
    "src/server/game/Entities/Item/ItemBonusMgr.cpp", "src/server/game/Loot/LootMgr.cpp",
]
ENGINE_ID_CALLEES = {
    "CastSpell", "CastCustomSpell", "HasAura", "GetSpellInfo", "AssertSpellInfo", "RemoveAurasDueToSpell",
    "RemoveAura", "GetAura", "GetAuraEffect", "HasAuraEffect", "AddAura", "ResetCooldown",
    "ApplySpellImmune", "GetAuraOfRankedSpell", "HasSpell", "LearnSpell", "RemoveSpell",
    "GetAuraEffectOfRankedSpell", "GetSpellLinked", "AddCooldown", "ModifyCooldown", "GetAuraCount",
    "RemoveAurasByType", "ModStackAmount", "HasAuraType", "GetAuraApplication", "RemoveOwnedAura",
    "CastItemCombatSpell", "GetPetAura", "GetSpellProcEntry", "GetSpellScriptsBounds",
    "IsSpellUsedInSpellClickConditions", "GetSpellTargetPosition", "GetItemByEntry", "HasItemCount",
    "SetChampioningFaction", "PlayDirectSound", "PlayDistanceSound",
}


# ---------------------------------------------------------------------------
# tree helpers
# ---------------------------------------------------------------------------

def walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def text(node: Node | None, limit: int = 0) -> str:
    if node is None:
        return ""
    s = node.text.decode("utf-8", errors="replace")
    s = " ".join(s.split())
    if limit and len(s) > limit:
        return s[:limit] + "…"
    return s


def line(node: Node) -> int:
    return node.start_point.row + 1


INT_SUFFIX = re.compile(r"[uUlL]+$")


def parse_int_literal(s: str) -> int | None:
    s = INT_SUFFIX.sub("", s.replace("'", ""))
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        if s.startswith("0") and len(s) > 1 and s.isdigit():
            return int(s, 8)
        if s.isdigit():
            return int(s)
        if s.startswith("-") and s[1:].isdigit():
            return int(s)
    except ValueError:
        return None
    return None


def eval_const(node: Node, consts: dict[str, int], depth: int = 0) -> int | None:
    """Best-effort integer evaluation of a constant expression."""
    if depth > 12 or node is None:
        return None
    t = node.type
    if t == "number_literal":
        return parse_int_literal(node.text.decode())
    if t in ("identifier", "type_identifier"):
        return consts.get(node.text.decode())
    if t == "qualified_identifier":
        name = node.text.decode().split("::")[-1]
        return consts.get(name, consts.get(node.text.decode()))
    if t == "parenthesized_expression":
        inner = [c for c in node.children if c.type not in ("(", ")")]
        return eval_const(inner[0], consts, depth + 1) if len(inner) == 1 else None
    if t == "unary_expression":
        op = node.child_by_field_name("operator")
        arg = node.child_by_field_name("argument")
        v = eval_const(arg, consts, depth + 1)
        if v is None or op is None:
            return None
        o = op.text.decode()
        return {"-": -v, "~": ~v, "+": v}.get(o)
    if t == "binary_expression":
        left = eval_const(node.child_by_field_name("left"), consts, depth + 1)
        right = eval_const(node.child_by_field_name("right"), consts, depth + 1)
        op = node.child_by_field_name("operator")
        if left is None or right is None or op is None:
            return None
        o = op.text.decode()
        try:
            return {"+": left + right, "-": left - right, "|": left | right, "&": left & right,
                    "<<": left << right, ">>": left >> right, "*": left * right,
                    "/": left // right if right else None, "^": left ^ right}.get(o)
        except (OverflowError, ValueError):
            return None
    if t in ("cast_expression", "static_cast"):
        val = node.child_by_field_name("value")
        return eval_const(val, consts, depth + 1) if val is not None else None
    if t == "call_expression":  # uint32(123) style
        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if fn is not None and args is not None:
            inner = [c for c in args.children if c.type not in ("(", ")", ",")]
            if len(inner) == 1 and fn.type in ("primitive_type", "type_identifier", "identifier"):
                return eval_const(inner[0], consts, depth + 1)
    return None


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

def collect_constants(root: Node, consts: dict[str, int]) -> dict[str, int]:
    """Enumerators and integral ``constexpr`` initialisers, file scope or nested."""
    for n in walk(root):
        if n.type == "enumerator_list":
            prev = -1
            for e in n.children:
                if e.type != "enumerator":
                    continue
                name = e.child_by_field_name("name")
                value = e.child_by_field_name("value")
                if value is not None:
                    v = eval_const(value, consts)
                else:
                    v = prev + 1
                if name is not None and v is not None:
                    consts[name.text.decode()] = v
                    prev = v
                else:
                    prev = prev + 1 if v is None else v
        elif n.type in ("declaration", "field_declaration"):
            if b"constexpr" not in n.text and b"const " not in n.text[:60]:
                continue
            for d in n.children:
                if d.type == "init_declarator":
                    name = d.child_by_field_name("declarator")
                    value = d.child_by_field_name("value")
                    if name is not None and value is not None and name.type in ("identifier", "field_identifier"):
                        v = eval_const(value, consts)
                        if v is not None:
                            consts[name.text.decode()] = v
    return consts


# ---------------------------------------------------------------------------
# facts inside a function body
# ---------------------------------------------------------------------------

def callee_name(fn: Node) -> tuple[str, str]:
    """(callee, receiver) for a call's function node."""
    if fn.type == "field_expression":
        field = fn.child_by_field_name("field")
        arg = fn.child_by_field_name("argument")
        return (text(field), text(arg, 60))
    if fn.type == "qualified_identifier":
        parts = fn.text.decode().split("::")
        return (parts[-1].split("<")[0], "::".join(parts[:-1]))
    if fn.type == "template_function":
        name = fn.child_by_field_name("name")
        return (text(name), "")
    if fn.type == "identifier":
        return (fn.text.decode(), "")
    return (text(fn, 40), "")


def split_args(args: Node | None) -> list[Node]:
    if args is None:
        return []
    return [c for c in args.children if c.type not in ("(", ")", ",", "comment")]


def body_facts(body: Node, consts: dict[str, int], global_consts: dict[str, int], engine: bool = False) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    other = Counter()
    refs: dict[str, int] = {}
    tokens: set[str] = set()
    case_ints: list[dict[str, Any]] = []
    cmp_ints: list[dict[str, Any]] = []
    literals: set[int] = set()
    news: list[str] = []
    lambdas = 0
    loops = 0
    for n in walk(body):
        t = n.type
        if t == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is None:
                continue
            callee, recv = callee_name(fn)
            args = split_args(n.child_by_field_name("arguments"))
            ints = []
            for a in args:
                v = eval_const(a, consts)
                if v is None and a.type in ("identifier", "qualified_identifier"):
                    v = global_consts.get(a.text.decode().split("::")[-1])
                if v is not None:
                    ints.append(v)
            cat = VOCAB.get(callee)
            if engine:
                if callee in ENGINE_ID_CALLEES and ints:
                    calls.append({"callee": callee, "recv": recv, "ints": ints, "line": line(n),
                                  "args": [text(a, 50) for a in args][:6]})
                other[callee] += 1
                continue
            if cat or ints:
                rec = {"callee": callee, "cat": cat or "other", "recv": recv,
                       "args": [text(a, 60) for a in args][:8], "line": line(n)}
                if ints:
                    rec["ints"] = ints
                calls.append(rec)
            else:
                other[callee] += 1
        elif t == "identifier":
            s = n.text.decode()
            if s in consts:
                refs[s] = consts[s]
            elif s in global_consts and s.isupper():
                refs[s] = global_consts[s]
            elif TOKEN_RE.match(s):
                tokens.add(s)
        elif t == "case_statement":
            value = n.child_by_field_name("value")
            if value is None:
                continue
            v = eval_const(value, consts)
            if v is None:
                v = global_consts.get(text(value).split("::")[-1])
            sw = n.parent
            while sw is not None and sw.type != "switch_statement":
                sw = sw.parent
            subject = text(sw.child_by_field_name("condition"), 60) if sw is not None else ""
            rec = {"value": v, "text": text(value, 40), "subject": subject, "line": line(n)}
            if engine:
                # what the case body does (statements are children of case_statement)
                body_calls = Counter()
                body_ints: set[int] = set()
                for m in walk(n):
                    if m is n:
                        continue
                    if m.type == "call_expression":
                        fn2 = m.child_by_field_name("function")
                        if fn2 is not None:
                            body_calls[callee_name(fn2)[0]] += 1
                            for a in split_args(m.child_by_field_name("arguments")):
                                v2 = eval_const(a, consts)
                                if v2 is not None and v2 >= 100:
                                    body_ints.add(v2)
                    elif m.type == "case_statement":
                        break
                rec["body_calls"] = dict(body_calls.most_common(12))
                if body_ints:
                    rec["body_ints"] = sorted(body_ints)
                rec["end_line"] = n.end_point.row + 1
            case_ints.append(rec)
        elif t == "binary_expression":
            op = n.child_by_field_name("operator")
            if op is not None and op.text in (b"==", b"!="):
                for side, o in ((n.child_by_field_name("left"), n.child_by_field_name("right")),
                                (n.child_by_field_name("right"), n.child_by_field_name("left"))):
                    v = eval_const(side, consts)
                    if v is None and side is not None and side.type in ("identifier", "qualified_identifier"):
                        v = global_consts.get(side.text.decode().split("::")[-1])
                    if v is not None and v >= 100:
                        cmp_ints.append({"value": v, "other": text(o, 50), "line": line(n)})
                        break
        elif t == "number_literal":
            v = parse_int_literal(n.text.decode())
            if v is not None and v >= 100:
                literals.add(v)
        elif t == "new_expression":
            tn = n.child_by_field_name("type")
            if tn is not None:
                news.append(text(tn, 60))
        elif t == "lambda_expression":
            lambdas += 1
        elif t in ("for_statement", "for_range_loop", "while_statement", "do_statement"):
            loops += 1
    out: dict[str, Any] = {"calls": calls, "other_calls": dict(other.most_common()),
                           "refs": refs, "tokens": sorted(tokens)}
    if case_ints:
        out["case_ints"] = case_ints
    if cmp_ints:
        out["cmp_ints"] = cmp_ints
    if literals:
        out["literals"] = sorted(literals)
    if lambdas:
        out["lambdas"] = lambdas
    if loops:
        out["loops"] = loops
    if news:
        out["news"] = news
    return out


# ---------------------------------------------------------------------------
# classes
# ---------------------------------------------------------------------------

def base_names(cls: Node) -> list[str]:
    out = []
    for c in cls.children:
        if c.type == "base_class_clause":
            for b in walk(c):
                if b.type in ("type_identifier", "qualified_identifier", "template_type"):
                    name = b.text.decode()
                    if b.type == "template_type":
                        name = name.split("<")[0]
                    name = name.split("::")[-1]
                    if name not in out and b.parent is not None and b.parent.type in ("base_class_clause", "template_type", "qualified_identifier"):
                        out.append(name)
    # keep only the direct base names (first level); template args are noise
    return out[:3]


def declarator_name(fn: Node) -> str:
    d = fn.child_by_field_name("declarator")
    while d is not None and d.type not in ("identifier", "field_identifier", "qualified_identifier",
                                            "destructor_name", "operator_name"):
        nxt = d.child_by_field_name("declarator")
        if nxt is None:
            # e.g. reference_declarator / pointer_declarator children
            inner = [c for c in d.children if c.type.endswith("declarator") or c.type in ("identifier", "field_identifier")]
            nxt = inner[0] if inner else None
        d = nxt
    return text(d) if d is not None else ""


def params_text(fn: Node) -> str:
    for n in walk(fn):
        if n.type == "parameter_list":
            return text(n, 200)
    return ""


def parse_register(body: Node, consts: dict[str, int]) -> list[dict[str, Any]]:
    hooks = []
    for n in walk(body):
        if n.type != "assignment_expression":
            continue
        op = n.child_by_field_name("operator")
        if op is None or op.text != b"+=":
            continue
        left = n.child_by_field_name("left")
        right = n.child_by_field_name("right")
        if right is None or right.type != "call_expression":
            continue
        fn = right.child_by_field_name("function")
        macro = text(fn)
        args = split_args(right.child_by_field_name("arguments"))
        if not args:
            continue
        raw_handler = text(args[0]).lstrip("&")
        tmpl = re.findall(r"<([^<>]*)>", raw_handler)
        handler = re.sub(r"<[^<>]*>", "", raw_handler).split("::")[-1]
        rest = [text(a, 60) for a in args[1:]]
        rec = {"list": text(left), "fn": macro, "handler": handler, "args": rest, "line": line(n)}
        if tmpl:
            rec["handler_template_args"] = tmpl
        if "::" in raw_handler and not raw_handler.split("::")[0].startswith("spell_") and raw_handler.count("::") >= 1:
            rec["handler_scope"] = re.sub(r"<[^<>]*>", "", raw_handler).rsplit("::", 1)[0]
        if HOOK_FN_MACROS.match(macro) and macro in ("SpellEffectFn", "AuraEffectApplyFn", "AuraEffectRemoveFn",
                                                     "AuraEffectPeriodicFn", "AuraEffectUpdatePeriodicFn",
                                                     "AuraEffectCalcAmountFn", "AuraEffectCalcPeriodicFn",
                                                     "AuraEffectCalcSpellModFn", "AuraEffectCalcCritChanceFn",
                                                     "AuraEffectCalcDamageFn", "AuraEffectCalcHealingFn",
                                                     "AuraEffectAbsorbFn", "AuraEffectAbsorbHealFn",
                                                     "AuraEffectAbsorbOverkillFn", "AuraEffectCalcAbsorbFn",
                                                     "AuraEffectManaShieldFn", "AuraEffectSplitFn",
                                                     "AuraCheckEffectProcFn", "AuraEffectProcFn",
                                                     "SpellObjectAreaTargetSelectFn", "SpellObjectTargetSelectFn",
                                                     "SpellDestinationTargetSelectFn"):
            if len(rest) >= 1:
                rec["eff_index"] = rest[0]
            if len(rest) >= 2:
                rec["eff_name"] = rest[1]
        hooks.append(rec)
    return hooks


def parse_validate(body: Node, consts: dict[str, int], global_consts: dict[str, int]) -> dict[str, Any]:
    ids: list[int] = []
    unresolved: list[str] = []
    effects: list[dict[str, Any]] = []
    for n in walk(body):
        if n.type != "call_expression":
            continue
        fn = n.child_by_field_name("function")
        name = text(fn)
        if name not in ("ValidateSpellInfo", "ValidateSpellEffect"):
            continue
        for a in split_args(n.child_by_field_name("arguments")):
            for item in walk(a):
                if item.type in ("initializer_list", "argument_list", "{", "}", ",", "comment"):
                    continue
                if item.parent is not None and item.parent.type == "initializer_list":
                    if name == "ValidateSpellEffect" and item.type == "initializer_list":
                        continue
                    v = eval_const(item, consts)
                    if v is None and item.type in ("identifier", "qualified_identifier"):
                        v = global_consts.get(item.text.decode().split("::")[-1])
                    if name == "ValidateSpellEffect":
                        parts = [c for c in item.parent.children if c.type not in ("{", "}", ",")]
                        if len(parts) == 2 and item is parts[0]:
                            effects.append({"spell": v, "text": text(item, 60), "eff": text(parts[1], 30)})
                        continue
                    if v is not None:
                        ids.append(v)
                    elif item.type not in ("number_literal",):
                        unresolved.append(text(item, 80))
    out: dict[str, Any] = {"spells": sorted(set(ids))}
    if unresolved:
        out["unresolved"] = sorted(set(unresolved))
    if effects:
        out["effects"] = effects
    return out


def parse_class(cls: Node, rel: str, consts: dict[str, int], global_consts: dict[str, int], force: bool = False) -> dict[str, Any] | None:
    name = cls.child_by_field_name("name")
    if name is None:
        return None
    bases = base_names(cls)
    if not force and not any(b in SCRIPT_BASES for b in bases):
        # helper class/struct in a script file: indexed too (static helpers, shared base classes), flagged
        if not rel.startswith("src/server/scripts/"):
            return None
        force = True
        is_helper = True
    else:
        is_helper = False
    body = cls.child_by_field_name("body")
    info: dict[str, Any] = {"name": text(name), "file": rel, "line": line(cls),
                            "end_line": cls.end_point.row + 1, "bases": bases, "helper_class": is_helper,
                            "hooks": [], "validate": None, "methods": {}, "fields": [],
                            "ctor_params": None, "ctor_name": None, "nested": []}
    if body is None:
        return info
    for child in body.children:
        if child.type == "field_declaration":
            # nested class/struct definitions are wrapped in a field_declaration
            inner_cls = [c for c in child.children if c.type in ("class_specifier", "struct_specifier")]
            if inner_cls and inner_cls[0].child_by_field_name("body") is not None:
                nested = parse_class(inner_cls[0], rel, consts, global_consts, force=True)
                if nested:
                    info["nested"].append(nested)
                continue
            # data member (not a method declaration)
            if any(c.type == "function_declarator" for c in walk(child)):
                continue
            if b"static" in child.text[:40] and b"constexpr" in child.text[:60]:
                continue
            info["fields"].append(text(child, 100))
        elif child.type in ("function_definition", "template_declaration"):
            fn = child
            if fn.type == "template_declaration":
                inner = [c for c in fn.children if c.type == "function_definition"]
                if not inner:
                    continue
                fn = inner[0]
            mname = declarator_name(fn)
            fbody = fn.child_by_field_name("body")
            if fbody is None:
                continue
            if mname == "Register":
                info["hooks"] = parse_register(fbody, consts)
            elif mname == "Validate":
                info["validate"] = parse_validate(fbody, consts, global_consts)
            elif mname == info["name"]:
                info["ctor_params"] = params_text(fn)
                for n in walk(fn):
                    if n.type == "field_initializer_list":
                        for s in walk(n):
                            if s.type == "string_literal":
                                info["ctor_name"] = s.text.decode().strip('"')
                                break
                        break
            else:
                facts = body_facts(fbody, consts, global_consts)
                facts["line"] = line(fn)
                facts["params"] = params_text(fn)
                facts["static"] = fn.text.lstrip().startswith(b"static")
                info["methods"][mname] = facts
        elif child.type == "declaration":
            # constructor declared with init list on the same node in some grammars
            pass
        elif child.type in ("class_specifier", "struct_specifier"):
            # nested helper classes (BasicEvent subclasses, functors) carry actions too
            nested = parse_class(child, rel, consts, global_consts, force=True)
            if nested:
                info["nested"].append(nested)
        elif child.type == "access_specifier":
            continue
    # SpellScriptLoader: which nested classes are returned by GetSpellScript/GetAuraScript
    if "SpellScriptLoader" in bases:
        loaders = {}
        for n in walk(body):
            if n.type == "function_definition":
                mname = declarator_name(n)
                if mname in ("GetSpellScript", "GetAuraScript"):
                    for m in walk(n):
                        if m.type == "new_expression":
                            tn = m.child_by_field_name("type")
                            loaders[mname] = text(tn)
        info["loader_returns"] = loaders
    return info


# ---------------------------------------------------------------------------
# registrations
# ---------------------------------------------------------------------------

def strip_parens(s: str) -> str:
    s = s.strip()
    while s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
    return s


def parse_registrations(root: Node, rel: str) -> list[dict[str, Any]]:
    out = []
    for n in walk(root):
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is None or fn.type != "identifier":
                continue
            macro = fn.text.decode()
            if macro not in REG_MACROS:
                continue
            args = [text(a, 200) for a in split_args(n.child_by_field_name("arguments"))]
            rec: dict[str, Any] = {"macro": macro, "file": rel, "line": line(n), "raw_args": args}
            if macro == "RegisterSpellScript":
                rec["name"] = args[0]
                rec["classes"] = [strip_parens(args[0])]
            elif macro == "RegisterSpellScriptWithArgs":
                rec["name"] = args[1].strip().strip('"')
                rec["classes"] = [strip_parens(args[0])]
                rec["ctor_args"] = args[2:]
            elif macro == "RegisterSpellAndAuraScriptPair":
                rec["name"] = args[0]
                rec["classes"] = [strip_parens(args[0]), strip_parens(args[1])]
            elif macro == "RegisterSpellAndAuraScriptPairWithArgs":
                rec["name"] = args[2].strip().strip('"')
                rec["classes"] = [strip_parens(args[0]), strip_parens(args[1])]
                rec["ctor_args"] = args[3:]
            else:
                rec["name"] = args[0]
                rec["classes"] = [strip_parens(args[0])]
            out.append(rec)
        elif n.type == "new_expression":
            # old style: new spell_foo(); / new npc_bar("name")
            parent = n.parent
            if parent is None or parent.type != "expression_statement":
                continue
            tn = n.child_by_field_name("type")
            args = split_args(n.child_by_field_name("arguments"))
            rec = {"macro": "new", "file": rel, "line": line(n), "classes": [text(tn)],
                   "name": args[0].text.decode().strip('"') if args and args[0].type == "string_literal" else None}
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------

def enclosing_function(n: Node) -> str:
    p = n.parent
    while p is not None:
        if p.type == "function_definition":
            return declarator_name(p)
        p = p.parent
    return ""


def parse_corrections(root: Node, consts: dict[str, int]) -> list[dict[str, Any]]:
    """Every ``ApplySpellFix({ids}, lambda)`` inside ``LoadSpellInfoCorrections``."""
    target = None
    for n in walk(root):
        if n.type == "function_definition" and declarator_name(n).endswith("LoadSpellInfoCorrections"):
            target = n
            break
    if target is None:
        return []
    fixes = []
    for n in walk(target.child_by_field_name("body")):
        if n.type != "call_expression":
            continue
        fn = n.child_by_field_name("function")
        if text(fn) != "ApplySpellFix":
            continue
        args = split_args(n.child_by_field_name("arguments"))
        if len(args) < 2:
            continue
        ids: list[dict[str, Any]] = []
        for item in args[0].children:
            if item.type == "comment":
                if ids and ids[-1]["line"] == line(item):
                    ids[-1]["note"] = item.text.decode().strip("/ *").strip()
                continue
            if item.type in ("{", "}", ","):
                continue
            v = eval_const(item, consts)
            ids.append({"id": v, "text": text(item, 40) if v is None else None, "line": line(item)})
        lam = args[1]
        writes: list[dict[str, Any]] = []
        other_stmts: list[str] = []
        lam_body = lam.child_by_field_name("body") if lam.type == "lambda_expression" else lam
        def collect(body: Node, eff: str | None) -> None:
            for s in walk(body):
                if s.type == "assignment_expression":
                    left = text(s.child_by_field_name("left"))
                    op = text(s.child_by_field_name("operator"))
                    right = text(s.child_by_field_name("right"), 120)
                    if left.startswith(("spellInfo->", "spellEffectInfo->")):
                        writes.append({"target": left, "op": op, "value": right, "effect": eff, "line": line(s)})
                elif s.type == "call_expression":
                    f = text(s.child_by_field_name("function"))
                    if f == "ApplySpellEffectFix":
                        a = split_args(s.child_by_field_name("arguments"))
                        if len(a) >= 3:
                            inner = a[2].child_by_field_name("body") if a[2].type == "lambda_expression" else a[2]
                            collect(inner, text(a[1]))
                    elif f.startswith(("spellInfo->", "spellEffectInfo->")) and "GetEffect" not in f:
                        other_stmts.append(text(s, 120))
        if lam_body is not None:
            collect(lam_body, None)
        # the comment right above the statement
        note = None
        stmt = n
        while stmt.parent is not None and stmt.type != "expression_statement":
            stmt = stmt.parent
        prev = stmt.prev_sibling
        if prev is not None and prev.type == "comment" and prev.end_point.row >= stmt.start_point.row - 1:
            note = prev.text.decode().strip("/ *").strip()
        # de-duplicate nested effect writes that the outer walk also saw
        seen = set()
        uniq = []
        for w in writes:
            key = (w["target"], w["op"], w["value"], w["line"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(w)
        # writes seen with effect=None but really inside ApplySpellEffectFix: prefer the effect-tagged copy
        by_line = {}
        for w in uniq:
            k = (w["target"], w["op"], w["value"], w["line"])
            if k not in by_line or w["effect"] is not None:
                by_line[k] = w
        fixes.append({"ids": ids, "line": line(n), "note": note, "writes": list(by_line.values()),
                      "other": other_stmts[:10]})
    return fixes


def parse_engine_file(path: Path, rel: str, global_consts: dict[str, int]) -> dict[str, Any]:
    src = path.read_bytes()
    tree = PARSER.parse(src)
    root = tree.root_node
    consts = collect_constants(root, dict())
    functions: dict[str, dict[str, Any]] = {}
    for n in walk(root):
        if n.type != "function_definition":
            continue
        body = n.child_by_field_name("body")
        if body is None:
            continue
        fname = declarator_name(n)
        facts = body_facts(body, consts, global_consts, engine=True)
        if not (facts.get("case_ints") or facts.get("cmp_ints") or facts["calls"]):
            continue
        rec = {"line": line(n), "end_line": n.end_point.row + 1}
        if facts.get("case_ints"):
            rec["case_ints"] = facts["case_ints"]
        if facts.get("cmp_ints"):
            rec["cmp_ints"] = facts["cmp_ints"]
        if facts["calls"]:
            rec["id_calls"] = facts["calls"]
        key = fname
        i = 2
        while key in functions:
            key = f"{fname}#{i}"
            i += 1
        functions[key] = rec
    out: dict[str, Any] = {"sha256": hashlib.sha256(src).hexdigest(), "functions": functions,
                           "has_error": root.has_error}
    if rel.endswith("SpellMgr.cpp"):
        out["corrections"] = parse_corrections(root, consts)
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def git(tc_root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tc_root), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tc-root", type=Path, default=WORKSPACE_PARENT / "TrinityCore")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    tc_root = args.tc_root.resolve()
    script_root = tc_root / "src/server/scripts"
    files = sorted(script_root.rglob("*.cpp"))

    # pass 1: constants everywhere (for cross-file resolution, flagged as global)
    parsed: dict[str, Any] = {}
    file_consts: dict[str, dict[str, int]] = {}
    global_consts: dict[str, int] = {}
    conflicts: Counter = Counter()
    for path in files:
        rel = str(path.relative_to(tc_root))
        src = path.read_bytes()
        tree = PARSER.parse(src)
        parsed[rel] = (tree, src)
        consts = collect_constants(tree.root_node, {})
        file_consts[rel] = consts
        for k, v in consts.items():
            if k in global_consts and global_consts[k] != v:
                conflicts[k] += 1
            global_consts.setdefault(k, v)
    for k in conflicts:
        global_consts.pop(k, None)  # ambiguous across files: never resolve globally

    classes: dict[str, dict[str, Any]] = {}
    helpers: dict[str, dict[str, Any]] = {}
    registrations: list[dict[str, Any]] = []
    file_hashes: dict[str, str] = {}
    errors: list[str] = []
    for rel, (tree, src) in parsed.items():
        root = tree.root_node
        file_hashes[rel] = hashlib.sha256(src).hexdigest()
        if root.has_error:
            errors.append(rel)
        consts = file_consts[rel]
        for n in walk(root):
            if n.type in ("class_specifier", "struct_specifier"):
                # only top-level (nested handled recursively)
                p = n.parent
                nested = False
                while p is not None:
                    if p.type in ("class_specifier", "struct_specifier"):
                        nested = True
                        break
                    p = p.parent
                if nested:
                    continue
                info = parse_class(n, rel, consts, global_consts)
                if info is None:
                    continue
                key = f"{info['name']}@{rel}:{info['line']}"
                classes[key] = info
        registrations.extend(parse_registrations(root, rel))
        # free (non-member) function definitions, at file scope or inside namespaces: shared helpers live here
        def collect_free(container: Node, ns: str) -> None:
            for n in container.children:
                if n.type == "namespace_definition":
                    nm = n.child_by_field_name("name")
                    body_ns = n.child_by_field_name("body")
                    if body_ns is not None:
                        collect_free(body_ns, (ns + "::" if ns else "") + (text(nm) if nm is not None else "(anonymous)"))
                    continue
                fn = n
                if fn.type == "template_declaration":
                    inner = [c for c in fn.children if c.type == "function_definition"]
                    fn = inner[0] if inner else None
                if fn is None or fn.type != "function_definition":
                    continue
                fname = declarator_name(fn)
                if fname.startswith("AddSC_") or "::" in fname:
                    continue
                fbody = fn.child_by_field_name("body")
                if fbody is None:
                    continue
                facts = body_facts(fbody, consts, global_consts)
                facts["line"] = line(fn)
                facts["params"] = params_text(fn)
                facts["file"] = rel
                facts["name"] = fname
                if ns:
                    facts["namespace"] = ns
                helpers[f"{(ns + '::') if ns else ''}{fname}@{rel}:{line(fn)}"] = facts
        collect_free(root, "")

    engine: dict[str, Any] = {}
    for rel in ENGINE_FILES:
        path = tc_root / rel
        if not path.exists():
            engine[rel] = {"missing": True}
            continue
        engine[rel] = parse_engine_file(path, rel, global_consts)

    payload = {
        "provenance": {
            "trinitycore_commit": git(tc_root, "rev-parse", "HEAD"),
            "script_root": "src/server/scripts",
            "script_files": len(files),
            "parse_errors": errors,
            "tree_sitter": "tree-sitter==0.25.2 tree-sitter-cpp==0.23.4",
            "global_constant_conflicts_dropped": sorted(conflicts),
            "note": "syntax-level index; verify every fact against the consumer before use",
        },
        "file_hashes": file_hashes,
        "constants": {rel: c for rel, c in file_consts.items() if c},
        "classes": classes,
        "helpers": helpers,
        "registrations": registrations,
        "engine": engine,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB): {len(classes)} script classes, "
          f"{len(registrations)} registrations, {len(helpers)} free functions, {len(files)} files, {len(errors)} parse errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
