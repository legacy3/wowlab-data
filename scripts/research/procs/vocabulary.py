"""Typed inventory of every source field that participates in proc behaviour.

Each :class:`FieldSpec` names the source table/column, the DB2 type Trinity
loads it as (``DB2Structure.h``), the direct consumer, and a *role*:

``identity``            row identity / difficulty selection
``provider-policy``     immutable fact about the proccing aura itself
``event-matching``      compared against the event (flags, family, school)
``chance-policy``       feeds Aura::CalcProcChance and friends
``cooldown-policy``     proc internal cooldown
``charge-policy``       proc charges / stack consumption
``target-policy``       who the triggered action affects
``suppression-policy``  proc-from-proc and suppression gates
``action``              what HandleProc does
``acquisition``         how a provider reaches a player
``downstream-cast``     read by the *triggered* spell's cast, not by proc gating
``lifetime``            aura duration/removal (not proc gating)
``unconsumed``          loaded by Trinity but read by no consumer

``census`` computes, against the snapshot, how many rows carry a nonzero value
overall and among difficulty-0 proc-flag providers.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from .source import Source
from .spells import DIFFICULTY_NONE, SpellCatalog


@dataclass(frozen=True)
class FieldSpec:
    table: str
    column: str
    db2_type: str
    consumer: str
    role: str
    note: str = ""
    spell_key: str | None = "SpellID"   # column joining to SpellID for the provider census


SPEC: tuple[FieldSpec, ...] = (
    # SpellAuraOptions ----------------------------------------------------
    FieldSpec("SpellAuraOptions", "DifficultyID", "int16", "SpellMgr::LoadSpellInfoStore", "identity",
              "per-difficulty SpellInfo; fallback via Difficulty.FallbackDifficultyID"),
    FieldSpec("SpellAuraOptions", "ProcTypeMask_0", "int32", "SpellInfo::ProcFlags[0] -> SpellProcEntry::ProcFlags",
              "event-matching", "ProcFlags word 0"),
    FieldSpec("SpellAuraOptions", "ProcTypeMask_1", "int32", "SpellInfo::ProcFlags[1] -> SpellProcEntry::ProcFlags",
              "event-matching", "ProcFlags2 word; bits 0x8 and 0x20 have no Trinity name"),
    FieldSpec("SpellAuraOptions", "ProcChance", "uint8", "SpellInfo::ProcChance -> SpellProcEntry::Chance; "
              "Player::CastItemCombatSpell (legacy item path)", "chance-policy",
              "101 is common: guaranteed on the aura path, 'weapon proc chance' on the legacy item path"),
    FieldSpec("SpellAuraOptions", "ProcCharges", "int32", "SpellInfo::ProcCharges -> SpellProcEntry::Charges -> Aura::CalcMaxCharges (uint8)",
              "charge-policy", "-1 becomes 255 through uint32 -> uint8"),
    FieldSpec("SpellAuraOptions", "ProcCategoryRecovery", "int32", "SpellInfo::ProcCooldown -> SpellProcEntry::Cooldown",
              "cooldown-policy", "milliseconds; per-Aura timer"),
    FieldSpec("SpellAuraOptions", "SpellProcsPerMinuteID", "uint16", "SpellInfo::ProcBasePPM / ProcPPMMods",
              "chance-policy", "nonzero selects RPPM whenever BaseProcRate > 0"),
    FieldSpec("SpellAuraOptions", "CumulativeAura", "uint16", "SpellInfo::StackAmount -> Aura::ModStackAmount",
              "charge-policy", "only proc-relevant with PROC_ATTR_USE_STACKS_FOR_CHARGES"),
    # SpellProcsPerMinute ------------------------------------------------
    FieldSpec("SpellProcsPerMinute", "BaseProcRate", "float", "SpellInfo::CalcProcPPM", "chance-policy",
              spell_key=None),
    FieldSpec("SpellProcsPerMinute", "Flags", "int32", "none (DB2 load only)", "unconsumed",
              "values 0/1/3 in snapshot; semantics unproved -- fail closed", spell_key=None),
    FieldSpec("SpellProcsPerMinuteMod", "Type", "int32", "SpellInfo::CalcProcPPM switch", "chance-policy",
              spell_key=None),
    FieldSpec("SpellProcsPerMinuteMod", "Param", "int32", "CalcPPMHasteMod/CritMod/ItemLevelMod, class/spec/race/aura",
              "chance-policy", spell_key=None),
    FieldSpec("SpellProcsPerMinuteMod", "Coeff", "float", "SpellInfo::CalcProcPPM", "chance-policy", spell_key=None),
    FieldSpec("SpellProcsPerMinuteMod", "SpellProcsPerMinuteID", "uint32", "DB2Manager::GetSpellProcsPerMinuteMods",
              "identity", "store order = application order", spell_key=None),
    # SpellEffect ---------------------------------------------------------
    FieldSpec("SpellEffect", "EffectIndex", "int32", "DisableEffectsMask, procEffectMask bits", "provider-policy"),
    FieldSpec("SpellEffect", "Effect", "uint32", "SpellEffectInfo::IsEffect/IsAura", "provider-policy"),
    FieldSpec("SpellEffect", "EffectAura", "int16", "isTriggerAura (generation); AuraEffect::CheckEffectProc; AuraEffect::HandleProc",
              "action"),
    FieldSpec("SpellEffect", "EffectTriggerSpell", "int32", "AuraEffect::HandleProcTriggerSpell[WithValue]AuraProc",
              "action", "triggered SpellID (resolved at the aura's cast difficulty)"),
    FieldSpec("SpellEffect", "EffectSpellClassMask_0", "flag128[0]", "LoadSpellProcs generated SpellFamilyMask", "event-matching"),
    FieldSpec("SpellEffect", "EffectSpellClassMask_1", "flag128[1]", "LoadSpellProcs generated SpellFamilyMask", "event-matching"),
    FieldSpec("SpellEffect", "EffectSpellClassMask_2", "flag128[2]", "LoadSpellProcs generated SpellFamilyMask", "event-matching"),
    FieldSpec("SpellEffect", "EffectSpellClassMask_3", "flag128[3]", "LoadSpellProcs generated SpellFamilyMask", "event-matching"),
    FieldSpec("SpellEffect", "EffectBasePointsF", "float", "MOD_HIT_CHANCE generation (<= -100); PROC_TRIGGER_DAMAGE amount; "
              "WITH_VALUE BasePoint0; breakable-CC amount", "action"),
    FieldSpec("SpellEffect", "EffectMiscValue_0", "int32", "CheckEffectProc: mechanic / school gates", "event-matching"),
    FieldSpec("SpellEffect", "EffectMechanic", "int32", "SpellInfo::GetAllEffectsMechanicMask (event spell)", "event-matching"),
    FieldSpec("SpellEffect", "ImplicitTarget_0", "int32", "triggered spell target selection; provider holder", "downstream-cast",
              "Trinity passes a contextual explicit target first"),
    FieldSpec("SpellEffect", "ScalingClass", "int32", "SpellEffectInfo::CalcBaseValue (not ported)", "action",
              "only matters where an amount is read"),
    FieldSpec("SpellEffect", "DifficultyID", "int32", "LoadSpellInfoStore", "identity"),
    # SpellMisc -----------------------------------------------------------
    FieldSpec("SpellMisc", "Attributes_0", "int32", "PROC_FAILURE_BURNS_CHARGE, IS_ABILITY, PASSIVE", "suppression-policy"),
    FieldSpec("SpellMisc", "Attributes_2", "int32", "PROC_COOLDOWN_ON_FAILURE, AUTO_REPEAT, CANT_CRIT", "cooldown-policy"),
    FieldSpec("SpellMisc", "Attributes_3", "int32", "CAN_PROC_FROM_PROCS, NOT_A_PROC, SUPPRESS_*, ONLY_PROC_*, TREAT_AS_PERIODIC",
              "suppression-policy"),
    FieldSpec("SpellMisc", "Attributes_4", "int32", "SUPPRESS_WEAPON_PROCS, ALLOW_PROC_WHILE_SITTING", "suppression-policy"),
    FieldSpec("SpellMisc", "Attributes_6", "int32", "AURA_IS_WEAPON_PROC, DO_NOT_CONSUME_RESOURCES", "suppression-policy"),
    FieldSpec("SpellMisc", "Attributes_7", "int32", "CAN_PROC_FROM_SUPPRESSED_TARGET_PROCS", "suppression-policy"),
    FieldSpec("SpellMisc", "Attributes_8", "int32", "TARGET_PROCS_ON_CASTER", "target-policy"),
    FieldSpec("SpellMisc", "Attributes_12", "int32", "ONLY_PROC_FROM_CLASS_ABILITIES, *_SUPPRESSED_CASTER_PROCS", "suppression-policy"),
    FieldSpec("SpellMisc", "Attributes_13", "int32", "ALLOW_CLASS_ABILITY_PROCS", "event-matching"),
    FieldSpec("SpellMisc", "SchoolMask", "uint8", "ProcEventInfo::GetSchoolMask (spell events)", "event-matching"),
    FieldSpec("SpellMisc", "DurationIndex", "uint16", "Aura::CalcMaxDuration", "lifetime", "provider lifecycle only"),
    # SpellClassOptions ---------------------------------------------------
    FieldSpec("SpellClassOptions", "SpellClassSet", "uint8", "SpellInfo::IsAffected; generated SpellFamilyName",
              "event-matching"),
    FieldSpec("SpellClassOptions", "SpellClassMask_0", "flag128", "SpellInfo::IsAffected (event spell)", "event-matching"),
    # SpellCategories -----------------------------------------------------
    FieldSpec("SpellCategories", "DefenseType", "int8", "Spell::prepareDataForTriggerSystem (DmgClass)", "event-matching",
              "decides MELEE/RANGED vs spell/ability flags of the *event*"),
    FieldSpec("SpellCategories", "Mechanic", "int8", "GetAllEffectsMechanicMask", "event-matching"),
    FieldSpec("SpellCategories", "Category", "int16", "SpellHistory (triggered cast; ignored under TRIGGERED_IGNORE_SPELL_AND_CATEGORY_CD)",
              "downstream-cast"),
    FieldSpec("SpellCategories", "ChargeCategory", "int16", "SpellHistory charges (triggered casts do not consume)",
              "downstream-cast"),
    # SpellCooldowns / SpellCategory ---------------------------------------
    FieldSpec("SpellCooldowns", "RecoveryTime", "int32", "SpellHistory::HandleCooldowns (skipped for IsIgnoringCooldowns)",
              "downstream-cast", "not a proc cooldown"),
    FieldSpec("SpellCooldowns", "CategoryRecoveryTime", "int32", "SpellHistory (skipped for triggered casts)", "downstream-cast"),
    FieldSpec("SpellCategory", "ChargeRecoveryTime", "int32", "SpellHistory charges", "downstream-cast", spell_key=None),
    # SpellEquippedItems --------------------------------------------------
    FieldSpec("SpellEquippedItems", "EquippedItemClass", "int32", "Aura::GetProcEffectMask equipment gate (passive only); wand event identity",
              "provider-policy"),
    FieldSpec("SpellEquippedItems", "EquippedItemSubclass", "int32", "Item::IsFitToSpellRequirements", "provider-policy"),
    FieldSpec("SpellEquippedItems", "EquippedItemInvTypes", "int32", "Item::IsFitToSpellRequirements", "provider-policy"),
    # not proc gating ------------------------------------------------------
    FieldSpec("SpellTargetRestrictions", "MaxTargets", "uint32", "triggered spell target selection", "downstream-cast"),
    FieldSpec("SpellInterrupts", "AuraInterruptFlags_0", "int32", "Unit::RemoveAurasWithInterruptFlags", "lifetime",
              "removes the provider on events; never gates a proc"),
    FieldSpec("SpellAuraRestrictions", "CasterAuraSpell", "uint32", "Spell::CheckCast (triggered cast)", "downstream-cast",
              "TRIGGERED_IGNORE_CASTER_AURAS is part of TRIGGERED_FULL_MASK"),
    FieldSpec("SpellAuraRestrictions", "TargetAuraSpell", "uint32", "Spell::CheckCast (triggered cast)", "downstream-cast"),
    FieldSpec("SpellShapeshift", "ShapeshiftMask_0", "int32", "Spell::CheckCast (ignored: TRIGGERED_IGNORE_SHAPESHIFT)",
              "downstream-cast"),
    FieldSpec("SpellLabel", "LabelID", "uint32", "spell mods by label (not proc gating)", "downstream-cast",
              "no proc-pipeline consumer"),
    FieldSpec("SpellPower", "ManaCost", "int32", "Spell::GetPowerCost -> PROC_ATTR_REQ_POWER_COST (event); triggered casts pay power",
              "event-matching", "runtime cost is an event fact"),
    # acquisition ----------------------------------------------------------
    FieldSpec("ItemEffect", "TriggerType", "uint8", "ApplyItemEquipSpell / CastItemUseSpell / CastItemCombatSpell", "acquisition"),
    FieldSpec("ItemEffect", "SpellID", "int32", "same", "acquisition"),
    FieldSpec("ItemEffect", "ChrSpecializationID", "uint16", "Player::ApplyItemEquipSpell spec gate", "acquisition"),
    FieldSpec("ItemXItemEffect", "ItemEffectID", "int32", "ObjectMgr::LoadItemTemplates", "acquisition", spell_key=None),
    FieldSpec("ItemSparse", "Flags_0", "int32", "ITEM_FLAG_LEGACY (ApplyItemEquipSpell/CastItemCombatSpell)", "acquisition",
              spell_key=None),
    FieldSpec("ItemSparse", "ItemDelay", "uint16", "enchant classic PPM (proto->GetDelay)", "chance-policy", spell_key=None),
    FieldSpec("SpellItemEnchantment", "Effect_0", "uint8", "Player::ApplyEnchantment / CastItemCombatSpell", "acquisition",
              spell_key=None),
    FieldSpec("SpellItemEnchantment", "EffectArg_0", "uint32", "triggered/equip SpellID", "acquisition", spell_key=None),
    FieldSpec("SpellItemEnchantment", "EffectPointsMin_0", "int16", "CastItemCombatSpell chance (0 = weapon proc chance)",
              "chance-policy", spell_key=None),
    FieldSpec("GemProperties", "Enchant_ID", "uint16 (EnchantId)", "Item::SetGem -> enchant", "acquisition", spell_key=None),
    FieldSpec("ItemSetSpell", "SpellID", "uint32", "Player::ApplyEquipSpell", "acquisition"),
    FieldSpec("TraitDefinition", "SpellID", "int32", "Player::LearnSpell via TraitMgr", "acquisition"),
    FieldSpec("TraitDefinitionEffectPoints", "CurveID", "int32", "SpellEffectInfo::CalcValue trait override",
              "action", "changes effect amounts (proc action magnitude), never proc policy", spell_key=None),
    FieldSpec("TraitTreeLoadout", "TraitTreeID", "int32", "current class/spec trees (census scope)", "acquisition",
              spell_key=None),
    FieldSpec("SpecializationSpells", "SpellID", "int32", "Player::LearnSpecializationSpells", "acquisition"),
    FieldSpec("SkillLineAbility", "SupercedesSpell", "int32", "SpellMgr::LoadSpellRanks (all-ranks rows)", "identity",
              spell_key="Spell"),
    FieldSpec("Difficulty", "FallbackDifficultyID", "int16", "GetSpellInfo / GetSpellProcEntry fallback", "identity",
              spell_key=None),
    FieldSpec("RandPropPoints", "SuperiorF_0", "float", "CalcPPMItemLevelMod via GetRandomPropertyPoints(RARE, CHEST)",
              "chance-policy", spell_key=None),
    FieldSpec("SpellReplacement", "ReplacementSpellID", "(not loaded)", "none: no DB2Storage in this checkout", "unconsumed"),
)

#: Trinity world-database fields (not in data/tables), for completeness.
WORLD_DB_SPEC: tuple[dict[str, str], ...] = (
    {"table": "spell_proc", "column": "*", "consumer": "SpellMgr::LoadSpellProcs",
     "role": "provider-policy (override of DB2 defaults, authored by Trinity)"},
    {"table": "spell_enchant_proc_data", "column": "Chance/ProcsPerMinute/HitMask/AttributesMask",
     "consumer": "Player::CastItemCombatSpell", "role": "chance-policy / event-matching"},
    {"table": "item_template_addon", "column": "SpellPPMChance", "consumer": "Player::CastItemCombatSpell",
     "role": "chance-policy (legacy item PPM)"},
    {"table": "conditions", "column": "SourceTypeOrReferenceId=24", "consumer": "Aura::GetProcEffectMask",
     "role": "event-matching (live actor/target state)"},
    {"table": "spell_script_names", "column": "ScriptName", "consumer": "AuraScript proc hooks",
     "role": "bespoke consumer binding"},
    {"table": "spell_custom_attr", "column": "attributes", "consumer": "SpellInfo::AttributesCu",
     "role": "suppression-policy (CU_DONT_BREAK_STEALTH)"},
)


def census(source: Source, catalog: SpellCatalog) -> list[dict[str, Any]]:
    providers = set(catalog.proc_flag_spells(DIFFICULTY_NONE))
    out = []
    for spec in SPEC:
        if not source.has(spec.table):
            out.append({**asdict(spec), "rows": None, "note_missing": "table absent"})
            continue
        cols = (spec.column,) + ((spec.spell_key,) if spec.spell_key else ())
        rows = source.project(spec.table, cols)
        nonzero = [r for r in rows if r[0] not in (0, "", 0.0)]
        values = Counter(r[0] for r in nonzero)
        entry = {**asdict(spec), "rows": len(rows), "nonzero_rows": len(nonzero),
                 "distinct_nonzero": len(values),
                 "top_values": [[v, n] for v, n in values.most_common(6)]}
        if spec.spell_key:
            entry["nonzero_rows_on_proc_providers"] = sum(1 for r in nonzero if r[1] in providers)
        out.append(entry)
    return out
