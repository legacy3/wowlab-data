"""Track E -- removal reasons: the ``AuraRemoveMode`` taxonomy and its call sites.

Every way an aura (``Aura``) or one of its applications (``AuraApplication``)
stops existing in pinned TrinityCore is listed in :data:`SITES` with the remove
mode it passes, the scope it acts on, what runs (script hooks, handler
branches, linked spells) and whether a final periodic tick happens.  Each row
points at :data:`ANCHORS`, exact ``file:line`` + verbatim text at the pinned
revision; :func:`verify_anchors` re-reads the checkout and fails on any drift.

Observable semantics vs storage:

* the *mode* is observable (scripts / handlers branch on it); the multimap the
  aura sat in is storage;
* removal never runs a periodic tick (``_UnapplyAura`` has no tick path) --
  only the ``_UpdateSpells`` update pass ticks, and it ticks *before* the
  expiry sweep of the same update;
* the same outcome ("charges exhausted", "aura ended because its duration ran
  out") reaches consumers under **different modes** depending on the path.

Nothing here decides Retail behaviour.  Trinity is a consumer oracle.

Census helpers (:func:`spell_removal_facts`, :func:`census`) read DB2 rows via
``context.get()`` and never interpret a field beyond the consumer predicate
named in their ``Mirrors:`` line.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import TC_ROOT, FailClosed

GAME = "src/server/game/"
SCRIPTS = "src/server/scripts/"

# AuraRemoveMode, SpellAuraDefines.h:55-64
REMOVE_MODES: dict[int, str] = {0: "NONE", 1: "DEFAULT", 2: "INTERRUPT", 3: "CANCEL",
                                4: "ENEMY_SPELL", 5: "EXPIRE", 6: "DEATH"}
MODE_VALUE = {v: k for k, v in REMOVE_MODES.items()}

# id -> (path relative to TrinityCore/src/server/game, exact line, verbatim substring of that line)
ANCHORS: dict[str, tuple[str, int, str]] = {
    "mode_enum": ("Spells/Auras/SpellAuraDefines.h", 55, "enum AuraRemoveMode"),
    "upd_owner_loop": ("Entities/Unit/Unit.cpp", 2980, "i_aura->UpdateOwner(time, this);"),
    "upd_expire": ("Entities/Unit/Unit.cpp", 2987, "RemoveOwnedAura(i, AURA_REMOVE_BY_EXPIRE);"),
    "upd_channel_gone": ("Entities/Unit/Unit.cpp", 2989, "RemoveOwnedAura(i, AURA_REMOVE_BY_CANCEL); // remove channeled"),
    "upd_delete": ("Entities/Unit/Unit.cpp", 2999, "_DeleteRemovedAuras();"),
    "is_expired": ("Spells/Auras/SpellAuras.h", 226, "bool IsExpired() const"),
    "aura_update_call": ("Spells/Auras/SpellAuras.cpp", 837, "Update(diff, caster);"),
    "effect_update_call": ("Spells/Auras/SpellAuras.cpp", 846, "effect->Update(diff, caster);"),
    "periodic_cost_remove": ("Spells/Auras/SpellAuras.cpp", 893, "Remove();"),
    "tick_loop": ("Spells/Auras/SpellAuraEffects.cpp", 1258, "while (_periodicTimer >= _period)"),
    "tick_cap": ("Spells/Auras/SpellAuraEffects.cpp", 1262, "if (!GetBase()->IsPermanent() && (_ticksDone + 1) > totalTicks)"),
    "dynobj_expired": ("Entities/DynamicObject/DynamicObject.cpp", 156, "expired = true;"),
    "dynobj_remove_default": ("Entities/DynamicObject/DynamicObject.cpp", 204, "_removedAura->_Remove(AURA_REMOVE_BY_DEFAULT);"),
    "spell_cancel_channel": ("Spells/Spell.cpp", 3646, "unit->RemoveOwnedAura(m_spellInfo->Id, m_originalCasterGUID, 0, AURA_REMOVE_BY_CANCEL);"),
    "spell_channel_no_targets": ("Spells/Spell.cpp", 4288, "unit->RemoveOwnedAura(m_spellInfo->Id, m_originalCasterGUID, 0, AURA_REMOVE_BY_CANCEL);"),
    "channel_range_check": ("Spells/Spell.cpp", 3410, "if (m_caster != unit && !m_caster->IsWithinDistInMap(unit, range))"),
    "channel_range_remove": ("Spells/Spell.cpp", 3413, "unit->RemoveAura(aurApp);"),
    "cancel_no_aura_cancel": ("Handlers/SpellHandler.cpp", 266, "if (spellInfo->HasAttribute(SPELL_ATTR0_NO_AURA_CANCEL))"),
    "cancel_channel_interrupt": ("Handlers/SpellHandler.cpp", 274, "_player->InterruptSpell(CURRENT_CHANNELED_SPELL);"),
    "cancel_positive_gate": ("Handlers/SpellHandler.cpp", 281, "if (!spellInfo->IsPositive() || spellInfo->IsPassive())"),
    "cancel_remove": ("Handlers/SpellHandler.cpp", 284, "_player->RemoveOwnedAura(cancelAura.SpellID, cancelAura.CasterGUID, 0, AURA_REMOVE_BY_CANCEL);"),
    "pet_cancel_remove": ("Handlers/SpellHandler.cpp", 315, "pet->RemoveOwnedAura(packet.SpellID, ObjectGuid::Empty, 0, AURA_REMOVE_BY_CANCEL);"),
    "playerai_cancel": ("AI/PlayerAI/PlayerAI.cpp", 616, "me->RemoveOwnedAura(aura, AURA_REMOVE_BY_CANCEL);"),
    "interrupt_remove": ("Entities/Unit/Unit.cpp", 4255, "RemoveAura(aura, AURA_REMOVE_BY_INTERRUPT);"),
    "interrupt_damage": ("Entities/Unit/Unit.cpp", 875, "victim->RemoveAurasWithInterruptFlags(SpellAuraInterruptFlags::Damage, spellProto);"),
    "interrupt_nonperiodic": ("Entities/Unit/Unit.cpp", 1078, "victim->RemoveAurasWithInterruptFlags(SpellAuraInterruptFlags::NonPeriodicDamage, spellProto);"),
    "interrupt_enter_combat": ("Entities/Unit/Unit.cpp", 9232, "RemoveAurasWithInterruptFlags(SpellAuraInterruptFlags::EnteringCombat);"),
    "exit_combat_hooks": ("Entities/Unit/Unit.cpp", 9246, "aurApp->GetBase()->CallScriptEnterLeaveCombatHandlers(aurApp, false);"),
    "interrupt_leave_combat": ("Entities/Unit/Unit.cpp", 9249, "RemoveAurasWithInterruptFlags(SpellAuraInterruptFlags::LeavingCombat);"),
    "interrupt_leave_world": ("Entities/Unit/Unit.cpp", 10276, "RemoveAurasWithInterruptFlags(SpellAuraInterruptFlags::LeaveWorld);"),
    "interrupt_encounter_start": ("Entities/Unit/Unit.cpp", 550, "RemoveAurasWithInterruptFlags(SpellAuraInterruptFlags2::StartOfEncounter);"),
    "interrupt_encounter_end": ("Entities/Unit/Unit.cpp", 575, "RemoveAurasWithInterruptFlags(SpellAuraInterruptFlags2::EndOfEncounter);"),
    "interrupt_shapeshifting": ("Spells/Auras/SpellAuraEffects.cpp", 2002, "target->RemoveAurasWithInterruptFlags(SpellAuraInterruptFlags::Shapeshifting, GetSpellInfo());"),
    "travel_form_interrupt": ("Entities/Unit/Unit.cpp", 8466, "CancelTravelShapeshiftForm(AURA_REMOVE_BY_INTERRUPT);"),
    "area_location_interrupt": ("Entities/Player/Player.cpp", 26500, "RemoveOwnedAura(iter, AURA_REMOVE_BY_INTERRUPT);"),
    "dispel_on_dispel": ("Entities/Unit/Unit.cpp", 4017, "aura->CallScriptDispel(&dispelInfo);"),
    "dispel_charges": ("Entities/Unit/Unit.cpp", 4020, "aura->ModCharges(-dispelInfo.GetRemovedCharges(), AURA_REMOVE_BY_ENEMY_SPELL);"),
    "dispel_stacks": ("Entities/Unit/Unit.cpp", 4022, "aura->ModStackAmount(-dispelInfo.GetRemovedCharges(), AURA_REMOVE_BY_ENEMY_SPELL);"),
    "dispel_after": ("Entities/Unit/Unit.cpp", 4025, "aura->CallScriptAfterDispel(&dispelInfo);"),
    "steal_charges": ("Entities/Unit/Unit.cpp", 4101, "aura->ModCharges(-stolenCharges, AURA_REMOVE_BY_ENEMY_SPELL);"),
    "steal_stacks": ("Entities/Unit/Unit.cpp", 4103, "aura->ModStackAmount(-stolenCharges, AURA_REMOVE_BY_ENEMY_SPELL);"),
    "mech_dispel_roll": ("Spells/SpellEffects.cpp", 4252, "if (roll_chance(aura->CalcDispelChance(unitTarget, !unitTarget->IsFriendlyTo(m_caster))))"),
    "mech_dispel_remove": ("Spells/SpellEffects.cpp", 4261, "unitTarget->RemoveAura(itr->first, itr->second, 0, AURA_REMOVE_BY_ENEMY_SPELL);"),
    "absorb_depleted_1": ("Entities/Unit/Unit.cpp", 1946, "absorbAurEff->GetBase()->Remove(AURA_REMOVE_BY_ENEMY_SPELL);"),
    "absorb_depleted_2": ("Entities/Unit/Unit.cpp", 2023, "absorbAurEff->GetBase()->Remove(AURA_REMOVE_BY_ENEMY_SPELL);"),
    "absorb_depleted_3": ("Entities/Unit/Unit.cpp", 2156, "absorbAurEff->GetBase()->Remove(AURA_REMOVE_BY_ENEMY_SPELL);"),
    "stack_zero": ("Spells/Auras/SpellAuras.cpp", 1108, "else if (stackAmount <= 0)"),
    "set_stack_amount": ("Spells/Auras/SpellAuras.cpp", 1056, "void Aura::SetStackAmount(uint8 stackAmount)"),
    "modify_stacks_set": ("Spells/SpellEffects.cpp", 6141, "targetAura->SetStackAmount(GetEffectValueAsInt());"),
    "charges_zero": ("Spells/Auras/SpellAuras.cpp", 1028, "else if (charges <= 0)"),
    "proc_use_stacks": ("Spells/Auras/SpellAuras.cpp", 1825, "ModStackAmount(-1);"),
    "proc_charges_out": ("Spells/Auras/SpellAuras.cpp", 1829, "if (!GetCharges())"),
    "intercept_charge_expire": ("Entities/Unit/Unit.cpp", 6591, "(*i)->GetBase()->DropCharge(AURA_REMOVE_BY_EXPIRE);"),
    "spellmod_charge_delayed": ("Entities/Object/Object.cpp", 2627, "aurEff->GetBase()->DropChargeDelayed(delay, AURA_REMOVE_BY_EXPIRE);"),
    "spellmod_charge": ("Entities/Object/Object.cpp", 2630, "aurEff->GetBase()->DropCharge(AURA_REMOVE_BY_EXPIRE);"),
    "smartscript_charges": ("AI/SmartScripts/SmartScript.cpp", 864, "aur->ModCharges(-static_cast<int32>(e.action.removeAura.charges), AURA_REMOVE_BY_EXPIRE);"),
    "nostack_owned": ("Entities/Unit/Unit.cpp", 3729, "RemoveOwnedAuras([aura](Aura const* ownedAura) { return !aura->CanStackWith(ownedAura); }, AURA_REMOVE_BY_DEFAULT);"),
    "nostack_applied": ("Entities/Unit/Unit.cpp", 3731, "RemoveAppliedAuras([aura](AuraApplication const* appliedAura) { return !aura->CanStackWith(appliedAura->GetBase()); }, AURA_REMOVE_BY_DEFAULT);"),
    "death_predicate": ("Entities/Unit/Unit.cpp", 4479, "if (!aura->IsPassive() && !aura->IsDeathPersistent())"),
    "death_unapply": ("Entities/Unit/Unit.cpp", 4480, "_UnapplyAura(iter, AURA_REMOVE_BY_DEATH);"),
    "death_owned": ("Entities/Unit/Unit.cpp", 4489, "RemoveOwnedAura(iter, AURA_REMOVE_BY_DEATH);"),
    "death_persistent": ("Spells/SpellInfo.cpp", 1830, "return HasAttribute(SPELL_ATTR3_ALLOW_AURA_WHILE_DEAD);"),
    "death_state_gate": ("Entities/Unit/Unit.cpp", 9167, "if (s != ALIVE && s != JUST_RESPAWNED)"),
    "death_combat_stop": ("Entities/Unit/Unit.cpp", 9169, "CombatStop();"),
    "death_interrupt_casts": ("Entities/Unit/Unit.cpp", 9172, "InterruptNonMeleeSpells(false);"),
    "death_totems": ("Entities/Unit/Unit.cpp", 9177, "UnsummonAllTotems();"),
    "death_controlled": ("Entities/Unit/Unit.cpp", 9178, "RemoveAllControlled();"),
    "death_sweep_call": ("Entities/Unit/Unit.cpp", 9179, "RemoveAllAurasOnDeath();"),
    "kill_death_procs": ("Entities/Unit/Unit.cpp", 11400, "Unit::ProcSkillsAndAuras(victim, victim, PROC_FLAG_NONE, PROC_FLAG_DEATH"),
    "kill_set_death": ("Entities/Unit/Unit.cpp", 11411, "victim->setDeathState(JUST_DIED);"),
    "player_death_pet": ("Entities/Player/Player.cpp", 1157, "RemovePet(nullptr, PET_SAVE_NOT_IN_SLOT, true);"),
    "player_load_dead": ("Entities/Player/Player.cpp", 18699, "RemoveAllAurasOnDeath();"),
    "player_load_alive": ("Entities/Player/Player.cpp", 18701, "RemoveAllAurasRequiringDeadTarget();"),
    "pve_combat_end": ("Entities/Unit/Unit.cpp", 6032, "m_combatManager.EndAllPvECombat(unitFilter);"),
    "pvp_combat_suppress": ("Entities/Unit/Unit.cpp", 6036, "m_combatManager.SuppressPvPCombat(unitFilter);"),
    "at_exit_combat_call": ("Combat/CombatManager.cpp", 416, "_owner->AtExitCombat();"),
    "unsummon_controlled": ("Entities/Unit/Unit.cpp", 6625, "target->ToTempSummon()->UnSummon();"),
    "ghost_predicate": ("Entities/Unit/Unit.cpp", 4500, "if (!aura->IsPassive() && aura->GetSpellInfo()->IsRequiringDeadTarget())"),
    "disable_while_dead": ("Spells/Auras/SpellAuras.cpp", 2543, "if (GetSpellInfo()->HasAttribute(SPELL_ATTR7_DISABLE_AURA_WHILE_DEAD) && !unitOwner->IsAlive())"),
    "targetmap_unapply": ("Spells/Auras/SpellAuras.cpp", 778, "unit->_UnapplyAura(aurApp, AURA_REMOVE_BY_DEFAULT);"),
    "effmask_unapply": ("Spells/Auras/SpellAuras.cpp", 199, "_target->_UnapplyAura(this, AURA_REMOVE_BY_DEFAULT);"),
    "create_app_dead_gate": ("Entities/Unit/Unit.cpp", 3508, "if (!IsAlive() && !aurSpellInfo->IsDeathPersistent() &&"),
    "add_aura_dead_gate": ("Entities/Unit/Unit.cpp", 12292, "if (!target->IsAlive() && !spellInfo->IsPassive() && !spellInfo->HasAttribute(SPELL_ATTR2_ALLOW_DEAD_TARGET))"),
    "immunity_purge": ("Spells/SpellInfo.cpp", 3743, "target->RemoveAurasWithMechanic(mechanicImmunity, AURA_REMOVE_BY_DEFAULT, Id, immuneInfo->RemoveEffectsWithMechanic);"),
    "movement_impair": ("Entities/Unit/Unit.cpp", 4296, "RemoveAurasWithMechanic(1 << MECHANIC_ROOT, AURA_REMOVE_BY_DEFAULT, 0, true);"),
    "shape_lost_predicate": ("Spells/Auras/SpellAuras.cpp", 1160, "bool Aura::IsRemovedOnShapeLost(Unit* target) const"),
    "shape_lost_remove": ("Spells/Auras/SpellAuraEffects.cpp", 1631, "if (itr->second->GetBase()->IsRemovedOnShapeLost(target) && !(itr->second->GetBase()->GetSpellInfo()->Stances & newStance))"),
    "shapeshift_snare_root": ("Entities/Unit/Unit.cpp", 4333, "void Unit::RemoveAurasByShapeShift()"),
    "item_remove": ("Entities/Unit/Unit.cpp", 4112, "void Unit::RemoveAurasDueToItemSpell(uint32 spellId, ObjectGuid castItemGuid)"),
    "item_unequip": ("Entities/Player/Player.cpp", 8391, "RemoveAurasDueToItemSpell(spellInfo->Id, item->GetGUID());  // un-apply all spells, not only at-equipped"),
    "evade": ("Entities/Unit/Unit.cpp", 4442, "void Unit::RemoveAurasOnEvade()"),
    "evade_stays": ("Entities/Unit/Unit.cpp", 4457, "if (aura->GetSpellInfo()->HasAttribute(SPELL_ATTR1_AURA_STAYS_AFTER_COMBAT))"),
    "arena": ("Entities/Unit/Unit.cpp", 4428, "void Unit::RemoveArenaAuras()"),
    "group_buffs": ("Entities/Unit/Unit.cpp", 4558, "void Unit::RemoveAllGroupBuffsFromCaster(ObjectGuid casterGUID)"),
    "single_target": ("Entities/Unit/Unit.cpp", 4158, "void Unit::RemoveNotOwnSingleTargetAuras(bool onPhaseChange /*= false*/)"),
    "leave_world_single": ("Entities/Unit/Unit.cpp", 10275, "RemoveNotOwnSingleTargetAuras();"),
    "leave_world_dynobj": ("Entities/Unit/Unit.cpp", 10279, "RemoveAllDynObjects();"),
    "leave_world_area": ("Entities/Unit/Unit.cpp", 4348, "void Unit::RemoveAreaAurasDueToLeaveWorld()"),
    "cleanup_all": ("Entities/Unit/Unit.cpp", 10331, "RemoveAllAuras();"),
    "remove_all_default": ("Entities/Unit/Unit.cpp", 4389, "_UnapplyAura(aurAppIter, AURA_REMOVE_BY_DEFAULT);"),
    "effect_remove_aura": ("Spells/SpellEffects.cpp", 5160, "unitTarget->RemoveAurasDueToSpell(effectInfo->TriggerSpell);"),
    "effect_remove_label": ("Spells/SpellEffects.cpp", 5477, "return aurApp->GetBase()->GetSpellInfo()->HasLabel(effectInfo->MiscValue);"),
    "totem_expire": ("Entities/Unit/Unit.cpp", 3660, "if (aurApp->GetRemoveMode() == AURA_REMOVE_BY_EXPIRE && GetTypeId() == TYPEID_UNIT && IsTotem())"),
    "script_remove_default": ("Spells/SpellScript.h", 1855, "void Remove(AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT);"),
    "linked_remove_not_death": ("Spells/Auras/SpellAuras.cpp", 1427, "else if (removeMode != AURA_REMOVE_BY_DEATH)"),
    "linked_aura_propagate": ("Spells/Auras/SpellAuras.cpp", 1440, "target->RemoveAura(*itr, GetCasterGUID(), 0, removeMode);"),
    "spell_area_autoremove": ("Spells/Auras/SpellAuras.cpp", 1390, "target->RemoveAurasDueToSpell(itr->second->spellId);"),
    "invis_on_expire": ("Spells/Auras/SpellAuras.cpp", 1502, "if (removeMode != AURA_REMOVE_BY_EXPIRE)"),
    "pws_enemy_spell": ("Spells/Auras/SpellAuras.cpp", 1514, "if (removeMode == AURA_REMOVE_BY_ENEMY_SPELL && GetSpellInfo()->SpellFamilyFlags[0] & 0x00000001)"),
    "h495_on_expire": ("Spells/Auras/SpellAuraEffects.cpp", 5424, "if (!(mode & AURA_EFFECT_HANDLE_REAL) || apply || aurApp->GetRemoveMode() != AURA_REMOVE_BY_EXPIRE)"),
    "h86_on_death": ("Spells/Auras/SpellAuraEffects.cpp", 5149, "if (apply || aurApp->GetRemoveMode() != AURA_REMOVE_BY_DEATH)"),
    "h430_on_expire": ("Spells/Auras/SpellAuraEffects.cpp", 6376, "if (aurApp->GetRemoveMode() == AURA_REMOVE_BY_EXPIRE)"),
    "h398_not_default": ("Spells/Auras/SpellAuraEffects.cpp", 6512, "if (!apply && aurApp->GetRemoveMode() != AURA_REMOVE_BY_DEFAULT)"),
    "h4_43681_expire": ("Spells/Auras/SpellAuraEffects.cpp", 4964, "if (target->GetTypeId() != TYPEID_PLAYER || aurApp->GetRemoveMode() != AURA_REMOVE_BY_EXPIRE)"),
    "hook_on_remove": ("Spells/Auras/SpellAuraEffects.cpp", 1159, "prevented = GetBase()->CallScriptEffectRemoveHandlers(this, aurApp, (AuraEffectHandleModes)mode);"),
    "hook_after_remove": ("Spells/Auras/SpellAuraEffects.cpp", 1177, "GetBase()->CallScriptAfterEffectRemoveHandlers(this, aurApp, (AuraEffectHandleModes)mode);"),
    "unapply_app_remove": ("Entities/Unit/Unit.cpp", 3648, "aurApp->_Remove();"),
    "unapply_for_target": ("Entities/Unit/Unit.cpp", 3649, "aura->_UnapplyForTarget(this, caster, aurApp);"),
    "unapply_effects": ("Entities/Unit/Unit.cpp", 3654, "aurApp->_HandleEffect(aurEff->GetEffIndex(), false);"),
    "unapply_specific_mods": ("Entities/Unit/Unit.cpp", 3680, "aura->HandleAuraSpecificMods(aurApp, caster, false, false);"),
    "owned_erase": ("Entities/Unit/Unit.cpp", 3759, "m_ownedAuras.erase(i);"),
    "owned_remove": ("Entities/Unit/Unit.cpp", 3766, "aura->_Remove(removeMode);"),
    "aura_remove_flag": ("Spells/Auras/SpellAuras.cpp", 639, "m_isRemoved = true;"),
    "aura_remove_apps": ("Spells/Auras/SpellAuras.cpp", 645, "target->_UnapplyAura(aurApp, removeMode);"),
    "get_caster": ("Spells/Auras/SpellAuras.cpp", 562, "return ObjectAccessor::GetUnit(*GetOwner(), GetCasterGUID());"),
    "tick_damage": ("Spells/Auras/SpellAuraEffects.cpp", 5632, "void AuraEffect::HandlePeriodicDamageAurasTick(Unit* target, Unit* caster) const"),
    "tick_damage_target_alive": ("Spells/Auras/SpellAuraEffects.cpp", 5634, "if (!target->IsAlive())"),
    "tick_weapon_pct_null": ("Spells/Auras/SpellAuraEffects.cpp", 5687, "damage = CalculatePct(caster->CalculateDamage(attackType, false, true), GetAmount());"),
    "tick_leech": ("Spells/Auras/SpellAuraEffects.cpp", 5762, "void AuraEffect::HandlePeriodicHealthLeechAuraTick(Unit* target, Unit* caster) const"),
    "tick_leech_caster_heal": ("Spells/Auras/SpellAuraEffects.cpp", 5846, "if (!caster || !caster->IsAlive())"),
    "tick_funnel": ("Spells/Auras/SpellAuraEffects.cpp", 5865, "if (!caster || !caster->IsAlive() || !target->IsAlive())"),
    "tick_heal": ("Spells/Auras/SpellAuraEffects.cpp", 5893, "void AuraEffect::HandlePeriodicHealAurasTick(Unit* target, Unit* caster) const"),
    "tick_mana_leech": ("Spells/Auras/SpellAuraEffects.cpp", 5955, "if (!caster || !caster->IsAlive() || !target->IsAlive() || target->GetPowerType() != powerType)"),
    "tick_obs_power": ("Spells/Auras/SpellAuraEffects.cpp", 6010, "void AuraEffect::HandleObsModPowerAuraTick(Unit* target, Unit* caster) const"),
    "tick_energize": ("Spells/Auras/SpellAuraEffects.cpp", 6053, "void AuraEffect::HandlePeriodicEnergizeAuraTick(Unit* target, Unit* caster) const"),
    "tick_power_burn": ("Spells/Auras/SpellAuraEffects.cpp", 6088, "if (!caster || !target->IsAlive() || target->GetPowerType() != powerType)"),
    "tick_trigger": ("Spells/Auras/SpellAuraEffects.cpp", 5594, "if (Unit* triggerCaster = triggeredSpellInfo->NeedsToBeTriggeredByCaster(m_spellInfo) ? caster : target)"),
    "periodic_type_list": ("Spells/Auras/SpellAuraEffects.cpp", 982, "m_isPeriodic = true;"),
    "remove_owned_aura_guard": ("Spells/Auras/SpellAuras.cpp", 2535, "if (IsRemoved())"),
    "st_cap_loop": ("Entities/Unit/Unit.cpp", 3477, "uint32 maxOtherAuras = aura->GetSpellInfo()->MaxAffectedTargets - 1;"),
    "st_cap_remove": ("Entities/Unit/Unit.cpp", 3480, "aurasSharingLimit.back()->Remove();"),
    "st_with": ("Spells/Auras/SpellAuras.cpp", 1207, "bool Aura::IsSingleTargetWith(Aura const* aura) const"),
    "excl_recipient": ("Spells/Auras/SpellAuras.cpp", 721, "if (addUnit && !itr->first->IsHighestExclusiveAura(this, true))"),
    "excl_remove_weaker": ("Entities/Unit/Unit.cpp", 14320, "RemoveAura(aurApp);"),
    "excl_new_self": ("Entities/Unit/Unit.cpp", 3722, "if (!IsHighestExclusiveAura(aura))"),
    "excl_new_remove": ("Entities/Unit/Unit.cpp", 3724, "aura->Remove();"),
    "mount_amount_zero": ("Entities/Unit/Unit.cpp", 8473, "aurEff->GetBase()->Remove();"),
    "vehicle_exit": ("Entities/Unit/Unit.cpp", 12868, "GetVehicleBase()->RemoveAurasByType(SPELL_AURA_CONTROL_VEHICLE, GetGUID());"),
    "death_exit_vehicle": ("Entities/Unit/Unit.cpp", 9174, "ExitVehicle();                                      // Exit vehicle before calling RemoveAllControlled"),
    "charm_release": ("Entities/Unit/Unit.cpp", 6623, "target->RemoveCharmAuras();"),
    "aurastate_remove": ("Entities/Unit/Unit.cpp", 6125, "if (itr->second->GetBase()->GetCasterGUID() == GetGUID() && spellProto->CasterAuraState == uint32(flag) && (spellProto->IsPassive() || flag != AURA_STATE_ENRAGED))"),
    "aurastate_recast": ("Entities/Unit/Unit.cpp", 6097, "CastSpell(this, itr->first, true);"),
    "pet_save_auras": ("Entities/Pet/Pet.cpp", 483, "_SaveAuras(trans);"),
    "pet_remove_all": ("Entities/Pet/Pet.cpp", 491, "RemoveAllAuras();"),
    "load_offline_countdown": ("Entities/Player/Player.cpp", 19061, "if (remainTime != -1 && (!spellInfo->IsPositive() || spellInfo->HasAttribute(SPELL_ATTR4_AURA_EXPIRES_OFFLINE)))"),
    "can_be_saved": ("Spells/Auras/SpellAuras.cpp", 1168, "bool Aura::CanBeSaved() const"),
    "totem_unsummon_self": ("Entities/Totem/Totem.cpp", 109, "RemoveAurasDueToSpell(GetSpell(), GetGUID());"),
    "totem_unsummon_owner": ("Entities/Totem/Totem.cpp", 121, "GetOwner()->RemoveAurasDueToSpell(GetSpell(), GetGUID());"),
    "totem_unsummon_group": ("Entities/Totem/Totem.cpp", 137, "target->RemoveAurasDueToSpell(GetSpell(), GetGUID());"),
    "cancel_dynobj": ("Spells/Spell.cpp", 3665, "m_originalCaster->RemoveDynObject(m_spellInfo->Id);"),
    "linked_negative": ("Spells/Auras/SpellAuras.cpp", 1426, "target->RemoveAurasDueToSpell(-(*itr));"),
    "trigger_fail_expire": ("Spells/Spell.cpp", 3506, "triggeredByAura->GetBase()->SetDuration(0);"),
    "trigger_fail_gate": ("Spells/Spell.cpp", 3503, "if (triggeredByAura && triggeredByAura->IsPeriodic() && !triggeredByAura->GetBase()->IsPassive())"),
    "resurrect_ghost": ("Entities/Player/Player.cpp", 4309, "RemoveAurasDueToSpell(8326);                            // SPELL_AURA_GHOST"),
    "shapeshift_form_gate": ("Spells/Auras/SpellAuraEffects.cpp", 1970, "target->RemoveAurasByShapeShift();"),
    "corpse_remove_all": ("Entities/Creature/Creature.cpp", 442, "RemoveAllAuras();"),
    "overkill_skip_death": ("Entities/Unit/Unit.cpp", 1044, "skipSettingDeathState = true;"),
    "remove_aura_owner_only": ("Entities/Unit/Unit.cpp", 3837, "aura->Remove(mode);"),
    "absorb_zero_pass": ("Entities/Unit/Unit.cpp", 1946, "absorbAurEff->GetBase()->Remove(AURA_REMOVE_BY_ENEMY_SPELL);"),
    "dispel_deferred": ("Spells/SpellEffects.cpp", 2216, "for (DispelableAura const& dispelableAura : successList)"),
    "dynobj_deferred": ("Entities/DynamicObject/DynamicObject.cpp", 168, "AddObjectToRemoveList();"),
    "steal_enemy_caster": ("Entities/Unit/Unit.cpp", 4079, ".SetCasterGUID(aura->GetCasterGUID())"),
    "modify_stacks_any": ("Spells/SpellEffects.cpp", 6131, "Aura* targetAura = unitTarget->GetAura(effectInfo->TriggerSpell);"),
    "leave_world_charm": ("Entities/Unit/Unit.cpp", 10273, "RemoveCharmAuras();"),
    "npc_dismount": ("Handlers/NPCHandler.cpp", 360, "GetPlayer()->RemoveAurasByType(SPELL_AURA_MOUNTED);"),
    "vehicle_enter_dismount": ("Entities/Vehicle/Vehicle.cpp", 863, "Passenger->RemoveAurasByType(SPELL_AURA_MOUNTED);"),
    "map_change_leave_world": ("Maps/Map.cpp", 928, "player->RemoveFromWorld();"),
    "load_auras": ("Entities/Player/Player.cpp", 18976, "void Player::_LoadAuras("),
}


def _tc_file(rel: str) -> Path:
    return TC_ROOT / GAME / rel


@lru_cache(maxsize=None)
def _lines(rel: str) -> tuple[str, ...]:
    return tuple(_tc_file(rel).read_text(encoding="utf-8", errors="replace").split("\n"))


def coord(anchor: str) -> str:
    rel, line, _ = ANCHORS[anchor]
    return f"{rel.rsplit('/', 1)[-1]}:{line}"


def verify_anchors() -> list[str]:
    """Anchors whose verbatim text is not at the pinned line (empty list = all live).

    Raises :class:`FailClosed` when the TrinityCore checkout is absent.
    """
    if not (TC_ROOT / GAME).is_dir():
        raise FailClosed(f"TrinityCore checkout not found at {TC_ROOT}")
    bad = []
    for key, (rel, line, text) in sorted(ANCHORS.items()):
        lines = _lines(rel)
        if line > len(lines) or text not in lines[line - 1]:
            bad.append(f"{key}: {rel}:{line} does not contain {text!r}")
    return bad


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Site:
    """One way an aura or aura application ends.

    ``scope``: ``aura`` (the Aura object and all its applications), ``application``
    (one target's application; the Aura may live on), ``stack``/``charge`` (count
    mutation that removes the whole Aura when it reaches zero).
    ``mode``: the AuraRemoveMode reaching ``_UnapplyAura`` / scripts, or ``caller``.
    """
    id: str
    reason: str
    mode: str
    scope: str
    anchors: tuple[str, ...]
    predicate: str
    runs: str
    final_tick: str
    evidence: tuple[str, ...] = ("trinity-consumer",)
    notes: str = ""
    depends_on: tuple[str, ...] = ()
    population: str = ""
    foreign_owned_outcome: str = ""

    def row(self) -> dict[str, Any]:
        fo = self.foreign_owned_outcome or (_FOREIGN_TRANSIENT if self.scope == "application" else "")
        return {"id": self.id, "reason": self.reason, "mode": self.mode, "scope": self.scope,
                "coords": [coord(a) for a in self.anchors], "predicate": self.predicate, "runs": self.runs,
                "final_tick": self.final_tick, "evidence": list(self.evidence), "notes": self.notes,
                "depends_on": list(self.depends_on), "population": self.population,
                "foreign_owned_outcome": fo}


_NO_TICK = "none: _UnapplyAura has no tick path (Unit.cpp:3601-3693)"
_FOREIGN_TRANSIENT = ("aura owned by another unit (area parent): only this recipient's application is removed "
                      "(Unit::RemoveAura removes the Aura only when owner == this, Unit.cpp:3837); it is re-applied at "
                      "the owner's next 500 ms target-map update if still eligible (R3-10; track F AL-R-F-05)")
_COMMON_RUNS = ("per applied effect: OnEffectRemove hooks -> default handler (unless prevented) -> "
                "AfterEffectRemove hooks; then HandleAuraSpecificMods (spell_linked_spell, spell_area, family branches)")

SITES: tuple[Site, ...] = (
    Site("RM-01", "natural expiry (unit-owned aura)", "EXPIRE", "aura",
         ("upd_owner_loop", "aura_update_call", "effect_update_call", "upd_expire", "is_expired"),
         "Aura::IsExpired(): duration == 0 && no pending charge-drop event, checked after every owned aura's UpdateOwner",
         _COMMON_RUNS + "; area-aura recipients also receive EXPIRE",
         "yes when the last period boundary falls inside the same update: AuraEffect::Update ticks before the sweep",
         notes="Remove happens in the owner's _UpdateSpells, after ALL owned auras updated (sweep loop Unit.cpp:2984-2991).",
         depends_on=("D",)),
    Site("RM-02", "natural expiry (dynamic-object aura, e.g. persistent area aura)", "DEFAULT", "aura",
         ("dynobj_expired", "dynobj_remove_default"),
         "DynamicObject::Update sees aura removed or IsExpired -> Remove -> RemoveFromWorld -> RemoveAura",
         _COMMON_RUNS + "; every recipient application is unapplied with DEFAULT, never EXPIRE",
         "yes if the boundary is in the same update (Aura::UpdateOwner ticks first); removal is deferred to the object remove list",
         notes="Observable asymmetry: EXPIRE-gated handlers (raw 495, 430, family on-expire, scripts testing EXPIRE) never fire "
               "for dynobj auras (= track F AL-D-F-03). Removal is deferred to the map remove list (DynamicObject.cpp:168): "
               "recipients keep their applications until the end of that map update.",
         depends_on=("F",)),
    Site("RM-03", "channel: caster not on the same map", "CANCEL", "aura",
         ("upd_channel_gone",), "IsChanneled && caster != owner && !ObjectAccessor::GetWorldObject(owner, caster)",
         _COMMON_RUNS, _NO_TICK),
    Site("RM-04", "channel cancelled / interrupted (incl. caster death, client cancel of a channel)", "CANCEL", "aura",
         ("spell_cancel_channel", "cancel_channel_interrupt", "death_interrupt_casts"),
         "Spell::cancel in SPELL_STATE_CHANNELING: RemoveOwnedAura(spellId, originalCaster) on every hit target",
         _COMMON_RUNS, _NO_TICK),
    Site("RM-05", "channel: no valid (alive) channel target left", "CANCEL", "aura",
         ("spell_channel_no_targets",), "Spell::update CHANNELING and !UpdateChanneledTargetList()", _COMMON_RUNS, _NO_TICK),
    Site("RM-06", "channel: target moved out of range", "DEFAULT", "application",
         ("channel_range_check", "channel_range_remove"),
         "target != caster && !IsWithinDistInMap(max range + min(10%, tolerance))", _COMMON_RUNS, _NO_TICK),
    Site("RM-07", "player cancels aura (client CMSG_CANCEL_AURA)", "CANCEL", "aura",
         ("cancel_no_aura_cancel", "cancel_positive_gate", "cancel_remove"),
         "!ATTR0_NO_AURA_CANCEL; channels are interrupted instead (RM-04); else SpellInfo::IsPositive() && !IsPassive(); "
         "matches owned auras of that spell + caster GUID",
         _COMMON_RUNS, _NO_TICK,
         notes="Positivity gate is spell-level SpellInfo::IsPositive, not the application's positive flag."),
    Site("RM-08", "pet aura cancel / PlayerAI cancel", "CANCEL", "aura", ("pet_cancel_remove", "playerai_cancel"),
         "pet must be alive; no positivity gate on the pet path", _COMMON_RUNS, _NO_TICK),
    Site("RM-09", "aura interrupt flags (damage, action, movement, combat enter/leave, leave world, encounter, ...)",
         "INTERRUPT", "application",
         ("interrupt_remove", "interrupt_damage", "interrupt_nonperiodic", "interrupt_enter_combat",
          "interrupt_leave_combat", "interrupt_leave_world", "interrupt_encounter_start", "interrupt_encounter_end",
          "interrupt_shapeshifting"),
         "SpellAuraInterruptFlags[2] bit set, aura spell != interrupting source spell, IsInterruptFlagIgnoredForSpell false",
         _COMMON_RUNS + "; RemoveAura(Aura*) also removes the owned Aura when owner == this",
         _NO_TICK,
         notes="Only bits that game code consumes act: 8 bits have no Trinity consumer (DamageCancelsScript, NotMoving, "
               "Disconnect, SeamlessTransfer, TouchingGround, ChromieTime, SplineFlightOrFreeFlight, ProcOrPeriodicAttacking; "
               "see census interrupt_flag_consumed) -- no-consumer, Retail unknown. DamageChannelDuration is channel pushback "
               "only. Damage fires on DOT damage too (DealDamage path); NonPeriodicDamage only for direct damage. "
               "No passive exemption: interrupt-flagged passives are lost until re-acquired (track G AL-D-G-01). "
               "INTERRUPT is also produced outside interrupt flags (RM-10, RM-41)."),
    Site("RM-10", "area / zone location change (Player::UpdateAreaDependentAuras)", "INTERRUPT", "aura",
         ("area_location_interrupt",), "SpellInfo::CheckLocation fails for the new area", _COMMON_RUNS, _NO_TICK),
    Site("RM-11", "dispel (Spell::EffectDispel -> RemoveAurasDueToSpellByDispel)", "ENEMY_SPELL", "stack",
         ("dispel_on_dispel", "dispel_charges", "dispel_stacks", "dispel_after", "dispel_deferred"),
         "see dispel.py: selection over owned auras; one stack/charge per success unless ATTR1_DISPEL_ALL_STACKS",
         "OnDispel hook -> ModCharges/ModStackAmount(-n, ENEMY_SPELL) -> [remove path if zero] -> AfterDispel hook",
         _NO_TICK + "; a partial (stack) dispel keeps the periodic timer and does not refresh duration",
         notes="Removal is deferred until every attempt is rolled (loop SpellEffects.cpp:2163-2194, removals from "
               "SpellEffects.cpp:2216).", depends_on=("C",)),
    Site("RM-12", "spell steal", "ENEMY_SPELL", "stack", ("steal_charges", "steal_stacks", "steal_enemy_caster"),
         "positive application, not passive, !ATTR4_CANNOT_BE_STOLEN, chance > 0",
         "stealer gets a copy (duration min(2 min, remaining)); victim ModCharges/ModStackAmount(-n, ENEMY_SPELL); no OnDispel hooks",
         _NO_TICK, notes="The stolen copy keeps the ORIGINAL enemy caster GUID (Unit.cpp:4079): caster-live inputs, group-leave "
                         "removal and same-caster identity on the stealer key on the enemy (R3-23)."),
    Site("RM-13", "mechanic dispel (Spell::EffectDispelMechanic)", "ENEMY_SPELL", "application",
         ("mech_dispel_roll", "mech_dispel_remove"),
         "chance rolled for EVERY applied owned aura before the mechanic filter; whole aura removed",
         "no OnDispel/AfterDispel hooks; OnEffectRemove/AfterEffectRemove with ENEMY_SPELL", _NO_TICK),
    Site("RM-14", "absorb shield depleted", "ENEMY_SPELL", "aura",
         ("absorb_depleted_1", "absorb_depleted_2", "absorb_depleted_3"),
         "absorb amount reaches zero during damage/heal absorb", _COMMON_RUNS, _NO_TICK,
         notes="Same mode as dispel: scripts cannot distinguish 'broken' from 'dispelled' by mode alone. A shield whose "
               "amount is already 0 is removed on its first absorb pass even when nothing is absorbed (>= 0 test, "
               "Unit.cpp:1938-1946)."),
    Site("RM-15", "stack count reaches zero (ModStackAmount)", "caller", "stack",
         ("stack_zero", "set_stack_amount", "modify_stacks_set", "modify_stacks_any"),
         "m_stackAmount + num <= 0 -> Remove(removeMode of the caller); SetStackAmount(0) does NOT remove",
         _COMMON_RUNS, _NO_TICK,
         notes="raw-289 set-mode (SetStackAmount) with value 0 leaves a 0-stack live aura in Trinity. Every stack change "
               "runs HandleAuraSpecificMods(remove, onReapply) (track H AL-D-H-01). raw-289 targets GetAura(TriggerSpell) of "
               "ANY caster, incl. a foreign-owned area parent: mode-0 decrement to 0 removes that parent for all its "
               "recipients (SpellEffects.cpp:6131, R3-22).",
         depends_on=("C",)),
    Site("RM-16", "charges exhausted", "caller", "charge",
         ("charges_zero", "proc_charges_out", "proc_use_stacks", "intercept_charge_expire", "spellmod_charge",
          "spellmod_charge_delayed", "smartscript_charges"),
         "ModCharges: m_procCharges + num <= 0 -> Remove(mode); proc consumption: Remove() (DEFAULT) or ModStackAmount(-1) (DEFAULT); "
         "spell-mod consumption and melee-redirect: EXPIRE",
         _COMMON_RUNS, _NO_TICK,
         notes="Path-dependent mode for the same observable (last charge used): DEFAULT via proc, EXPIRE via spell mod.",
         depends_on=("C",)),
    Site("RM-17", "replacement: new aura cannot stack with existing (exclusive / same-slot / spell group)", "DEFAULT", "aura",
         ("nostack_owned", "nostack_applied"),
         "!newAura->CanStackWith(existing) at _AddAura/_ApplyAura", _COMMON_RUNS, _NO_TICK, depends_on=("A",)),
    Site("RM-18", "holder death", "DEATH", "aura",
         ("death_state_gate", "death_predicate", "death_unapply", "death_owned", "death_persistent", "death_sweep_call"),
         "on the dying unit only: applied auras then owned auras with !IsPassive && !IsDeathPersistent (ATTR3_ALLOW_AURA_WHILE_DEAD)",
         _COMMON_RUNS + "; owned area auras remove recipients' applications with DEATH although recipients live; "
         "spell_linked_spell REMOVE casts skipped for DEATH",
         _NO_TICK + " -- remaining ticks are lost, no on-expire behaviour (EXPIRE-gated handlers do not run)",
         notes="Runs AFTER CombatStop (LeavingCombat INTERRUPT, OnEnterLeaveCombat hooks) and channel cancel (CANCEL): see lifetime.death_order."),
    Site("RM-19", "login while alive: ghost-only auras", "DEFAULT", "aura", ("ghost_predicate", "player_load_alive", "resurrect_ghost"),
         "!IsPassive && ATTR3_ONLY_ON_GHOSTS; the only caller of RemoveAllAurasRequiringDeadTarget is Player::LoadFromDB",
         _COMMON_RUNS, _NO_TICK, evidence=("trinity-consumer",),
         notes="trinity-only: Player::ResurrectPlayer removes only 20584 and 8326 (Player.cpp:4308-4309); any other "
               "ONLY_ON_GHOSTS aura survives resurrection in Trinity (R3-04).", population="20 all / 0 player (ATTR3_ONLY_ON_GHOSTS)"),
    Site("RM-20", "owner dead with ATTR7_DISABLE_AURA_WHILE_DEAD (surviving aura)", "DEFAULT", "application",
         ("disable_while_dead", "targetmap_unapply"),
         "UnitAura::FillTargetMap returns no targets while owner dead -> next UpdateTargetMap unapplies every application; Aura object kept",
         _COMMON_RUNS, _NO_TICK + "; the Aura keeps counting duration and its periodic loop has no application to tick",
         notes="Only relevant when the aura survives death (passive or ATTR3_ALLOW_AURA_WHILE_DEAD); re-applies on resurrect at next target-map update.",
         depends_on=("F",)),
    Site("RM-21", "recipient leaves area / becomes immune / cannot stack (target map update)", "DEFAULT", "application",
         ("targetmap_unapply",), "Aura::UpdateTargetMap (every 500 ms)", _COMMON_RUNS, _NO_TICK, depends_on=("F",)),
    Site("RM-22", "application effect mask emptied", "DEFAULT", "application", ("effmask_unapply",),
         "AuraApplication::UpdateApplyEffectMask removes all effects and adds none", _COMMON_RUNS, _NO_TICK),
    Site("RM-23", "immunity purge / movement-impair removal (RemoveAurasWithMechanic)", "DEFAULT", "application",
         ("immunity_purge", "movement_impair"), "applied mechanic mask intersects; effect-only mechanics update target map instead",
         _COMMON_RUNS, _NO_TICK),
    Site("RM-24", "shapeshift lost: caster-self aura requiring a stance", "DEFAULT", "application",
         ("shape_lost_predicate", "shape_lost_remove"),
         "caster == target && Stances && !ATTR2_ALLOW_WHILE_NOT_SHAPESHIFTED_CASTER_FORM && !ATTR0_NOT_SHAPESHIFTED && new stance not in Stances",
         _COMMON_RUNS, _NO_TICK),
    Site("RM-25", "shapeshift: snare/root removal", "DEFAULT", "application", ("shapeshift_snare_root", "shapeshift_form_gate"),
         "only when entering cat/travel/aquatic/bear/dire-bear/flight/moonkin/tree form or a druid shifting out "
         "(SpellAuraEffects.cpp:1962-1970, 2008-2014); then any-effect mechanic mask has SNARE|ROOT && !ATTR0_CU_AURA_CC",
         _COMMON_RUNS, _NO_TICK),
    Site("RM-26", "equipment loss (item-cast auras)", "DEFAULT", "application", ("item_remove", "item_unequip"),
         "same SpellId and CastItemGUID == unequipped item", _COMMON_RUNS, _NO_TICK, depends_on=("A", "G")),
    Site("RM-27", "creature evade", "DEFAULT", "aura", ("evade", "evade_stays"),
         "not player-controlled; keeps CONTROL_VEHICLE, CLONE_CASTER, ATTR1_AURA_STAYS_AFTER_COMBAT", _COMMON_RUNS, _NO_TICK),
    Site("RM-28", "arena entry", "DEFAULT", "application", ("arena",),
         "!ATTR4_ALLOW_ENTERING_ARENA && !passive && (positive || !ATTR3_ALLOW_AURA_WHILE_DEAD) || ATTR5_REMOVE_ENTERING_ARENA",
         _COMMON_RUNS, _NO_TICK),
    Site("RM-29", "group leave: group buffs of the other caster", "DEFAULT", "aura", ("group_buffs",),
         "caster GUID match && SpellInfo::IsGroupBuff (TargetA check PARTY/RAID/RAID_CLASS)", _COMMON_RUNS, _NO_TICK),
    Site("RM-30", "single-target aura: holder or caster leaves world / phase", "DEFAULT", "aura",
         ("single_target", "leave_world_single"), "aura->IsSingleTarget() and caster/holder mismatch or phase change",
         _COMMON_RUNS, _NO_TICK),
    Site("RM-31", "leave world (map change / far teleport): Unit::RemoveFromWorld", "DEFAULT", "application",
         ("map_change_leave_world", "leave_world_charm", "leave_world_single", "interrupt_leave_world", "leave_world_dynobj", "leave_world_area"),
         "charm + bind-sight auras, not-own single-target auras, LeaveWorld interrupts (INTERRUPT), dynobjects, "
         "totems/controlled units, area applications in both directions",
         _COMMON_RUNS, _NO_TICK,
         notes="The unit's OWN auras survive a teleport (no RemoveAllAuras on this path, R3-12). Auras this unit CAST on "
               "other units keep ticking with GetCaster()==nullptr.",
         foreign_owned_outcome="area applications received from others are removed and not re-added while out of the map"),
    Site("RM-37", "cleanup / despawn / logout / delete: Unit::CleanupBeforeRemoveFromMap -> RemoveAllAuras", "DEFAULT", "aura",
         ("cleanup_all", "remove_all_default"), "every applied then owned aura, repeated until empty",
         _COMMON_RUNS, _NO_TICK, notes="Logout and pet dismissal save first (RM-43)."),
    Site("RM-32", "spell effect REMOVE_AURA (164/203) / REMOVE_AURA_BY_SPELL_LABEL (212)", "DEFAULT", "application",
         ("effect_remove_aura", "effect_remove_label", "remove_aura_owner_only"),
         "164/203: every application of EffectTriggerSpell from ANY caster; 212: every applied aura with the label; "
         "whole aura only when the target owns it",
         _COMMON_RUNS, _NO_TICK),
    Site("RM-33", "periodic resource cost cannot be paid", "DEFAULT", "aura", ("periodic_cost_remove",),
         "Aura::Update: m_timeCla elapsed and caster lacks ManaPerSecond (health: <=)", _COMMON_RUNS, _NO_TICK,
         depends_on=("B",)),
    Site("RM-34", "CATCH-ALL (not a reason): script / engine direct removal (AuraScript::Remove, ModCharges, ModStackAmount, Unit::RemoveAura*)", "caller", "aura",
         ("script_remove_default",), "script code; default mode DEFAULT; scripts also WRITE non-default modes "
         "(script_mode_writers, e.g. Painbringer decay EXPIRE spell_dh.cpp:1736, Alter Time EXPIRE)", _COMMON_RUNS, _NO_TICK,
         evidence=("trinity-consumer", "script-consumer"), depends_on=("H",)),
    Site("RM-35", "linked aura removal (spell_linked_spell SPELL_LINK_AURA)", "caller", "aura",
         ("linked_aura_propagate", "linked_remove_not_death"),
         "on unapply of aura X: RemoveAura(linked, caster, 0, removeMode) -> the parent's mode propagates",
         _COMMON_RUNS, _NO_TICK, evidence=("trinity-consumer", "world-db-fact"), depends_on=("H",)),
    Site("RM-36", "spell_area autoremove", "DEFAULT", "aura", ("spell_area_autoremove",),
         "SPELL_AREA_FLAG_AUTOREMOVE and requirements no longer met", _COMMON_RUNS, _NO_TICK,
         evidence=("trinity-consumer", "world-db-fact")),
    Site("RM-38", "single-target cap eviction (caster re-applies a LIMIT_N aura elsewhere)", "DEFAULT", "aura",
         ("st_cap_loop", "st_cap_remove", "st_with"),
         "new aura IsSingleTarget (ATTR5_LIMIT_N): the caster's other auras IsSingleTargetWith it beyond MaxAffectedTargets-1 "
         "are Remove()d oldest-first inside the new hit's _AddAura, before the new aura applies",
         _COMMON_RUNS, _NO_TICK, population="player: 29 LIMIT_N providers (census attr:LIMIT_N) incl. Lifebloom 33763, Earth Shield 974",
         notes="Moving Lifebloom removes the old one with DEFAULT: its EXPIRE|ENEMY_SPELL bloom does not fire (AL-X-E-05)."),
    Site("RM-39", "EXCLUSIVE_HIGHEST spell group: weaker existing application displaced", "DEFAULT", "application",
         ("excl_recipient", "excl_remove_weaker"),
         "IsHighestExclusiveAura(this, true) from the recipient map or _RemoveNoStackAurasDueToAura: a weaker (|amount|, then "
         "effect count) EXCLUSIVE_HIGHEST application on this unit is RemoveAura'd; never from its own area owner",
         _COMMON_RUNS, _NO_TICK, evidence=("trinity-consumer", "world-db-fact"), depends_on=("A",),
         notes="Spell.cpp:6874-6883 refuses the cast (SPELL_FAILED_AURA_BOUNCED) for the same test at check time (not a removal)."),
    Site("RM-40", "EXCLUSIVE_HIGHEST: the NEW aura is not the highest -> the new aura removes itself", "DEFAULT", "aura",
         ("excl_new_self", "excl_new_remove"),
         "_RemoveNoStackAurasDueToAura: !IsHighestExclusiveAura(aura) -> aura->Remove() before any application exists",
         "no OnEffectRemove/AfterEffectRemove hooks (no application yet)", _NO_TICK, depends_on=("A",)),
    Site("RM-41", "mount / travel form / vehicle / charm type sweeps", "INTERRUPT|DEFAULT", "application",
         ("travel_form_interrupt", "mount_amount_zero", "npc_dismount", "vehicle_enter_dismount", "vehicle_exit", "charm_release"),
         "UpdateMountCapability: travel form lost with INTERRUPT (no interrupt flag involved), mount aura with recalculated "
         "amount 0 Remove()d DEFAULT; RemoveAurasByType(MOUNTED) at NPC interaction, vehicle entry, battleground, gameobject use; "
         "ExitVehicle removes the vehicle-owned CONTROL_VEHICLE aura cast by the passenger; RemoveCharmAuras",
         _COMMON_RUNS, _NO_TICK),
    Site("RM-42", "aura-state loss (CasterAuraState auras; passives toggle)", "DEFAULT", "application",
         ("aurastate_remove", "aurastate_recast"),
         "ModifyAuraState(flag, false): own auras with CasterAuraState == flag (ENRAGED: passives only) RemoveAura'd; on state "
         "gain passives are re-cast as NEW objects",
         _COMMON_RUNS, _NO_TICK, population="player: census caster_aura_state (Unshackled Fury 76856, Cruelty 392931)",
         depends_on=("G",)),
    Site("RM-43", "save -> removal -> reload (player logout, pet dismissal)", "DEFAULT", "aura",
         ("pet_save_auras", "pet_remove_all", "can_be_saved", "load_auras", "load_offline_countdown"),
         "Aura::CanBeSaved (not passive, not channelled, no area/foreign single-target, charges left, no permanent item aura) "
         "saved, then RemoveAllAuras DEFAULT; _LoadAuras rebuilds NEW objects: offline time subtracted only for non-positive "
         "or ATTR4_AURA_EXPIRES_OFFLINE auras (positives freeze), remainCharges <= 0 refilled to ProcCharges",
         _COMMON_RUNS, _NO_TICK, depends_on=("A", "G"),
         notes="periodic phase after reload: see periodic/track D (AL-U-E-21)"),
    Site("RM-44", "totem unsummon / passive totem aura expiry", "DEFAULT", "aura",
         ("totem_unsummon_self", "totem_unsummon_owner", "totem_unsummon_group", "totem_expire"),
         "Totem::UnSummon removes the totem spell's auras cast by the totem from itself, its owner and same-subgroup members "
         "(any caller: owner death UnsummonAllTotems, recast, timer); a passive totem whose aura EXPIREs dies",
         _COMMON_RUNS, _NO_TICK),
    Site("RM-45", "spell cancel removes that spell's dynamic objects (incl. earlier casts)", "DEFAULT", "aura",
         ("cancel_dynobj", "dynobj_remove_default"),
         "every Spell::cancel with an original caster: RemoveDynObject(spellId) -- all dynobjects of that spell id of the caster "
         "(caster death / interrupt / movement cancel)", _COMMON_RUNS, _NO_TICK,
         notes="So a caster dying while casting/channelling X loses all its dynobjects of X (R3-06)."),
    Site("RM-46", "spell_linked_spell SPELL_LINK_REMOVE with negative id", "DEFAULT", "application",
         ("linked_negative",), "on unapply of X (ANY mode, including DEATH): RemoveAurasDueToSpell(-id) from ANY caster",
         _COMMON_RUNS, _NO_TICK, evidence=("trinity-consumer", "world-db-fact"), depends_on=("H",),
         notes="Only POSITIVE linked ids (casts) are skipped on DEATH (SpellAuras.cpp:1427)."),
    Site("RM-47", "periodically triggered spell fails CheckCast -> aura duration set to 0", "EXPIRE", "aura",
         ("trigger_fail_gate", "trigger_fail_expire", "upd_expire"),
         "Spell::prepare CheckCast failure with triggeredByAura periodic and not passive: SetDuration(0); the owner's expiry "
         "sweep of the same update removes it with EXPIRE",
         _COMMON_RUNS + "; EXPIRE handlers and linked REMOVE casts run although the duration did not run out",
         "remaining catch-up ticks of the same AuraEffect::Update loop still fire (the loop never re-reads duration)",
         population="providers with raw 23/227 periodic trigger effects (census periodic_trigger)", depends_on=("D",),
         notes="R2-10. Trigger ticks have no target-alive gate: a trigger aura surviving on a corpse can end this way."),
)


def taxonomy() -> list[dict[str, Any]]:
    return [s.row() for s in SITES]


# Handlers / engine branches that read the remove mode (engine code, not scripts).
MODE_BRANCHES: tuple[dict[str, Any], ...] = (
    {"what": "aura type 495 TRIGGER_SPELL_ON_EXPIRE casts EffectTriggerSpell", "gate": "== EXPIRE", "anchor": "h495_on_expire", "aura": 495},
    {"what": "aura type 86 CHANNEL_DEATH_ITEM creates the item", "gate": "== DEATH", "anchor": "h86_on_death", "aura": 86},
    {"what": "aura type 430 PLAY_SCENE completes the scene", "gate": "== EXPIRE", "anchor": "h430_on_expire", "aura": 430},
    {"what": "aura type 397/398 BATTLEGROUND_PLAYER_POSITION flag handling", "gate": "!= DEFAULT", "anchor": "h398_not_default", "aura": 398},
    {"what": "aura type 4 DUMMY spell 43681 Inactive -> leave battleground", "gate": "== EXPIRE", "anchor": "h4_43681_expire", "aura": 4},
    {"what": "spell_linked_spell SPELL_LINK_REMOVE cast of linked spell", "gate": "!= DEATH", "anchor": "linked_remove_not_death", "aura": None},
    {"what": "spell_linked_spell SPELL_LINK_AURA removal propagates the mode", "gate": "propagates", "anchor": "linked_aura_propagate", "aura": None},
    {"what": "Mage Invisibility (66) casts 32612", "gate": "== EXPIRE", "anchor": "invis_on_expire", "aura": None},
    {"what": "Priest PW:Shield family flag -> Rapture", "gate": "== ENEMY_SPELL", "anchor": "pws_enemy_spell", "aura": None},
    {"what": "passive totem aura expiry kills the totem", "gate": "== EXPIRE", "anchor": "totem_expire", "aura": None},
)


# ---------------------------------------------------------------------------
# Script census: AuraScript / SpellScript classes that read the remove mode
# ---------------------------------------------------------------------------

_MODE_RE = re.compile(r"AURA_REMOVE_BY_([A-Z_]+)")
_CMP_RE = re.compile(r"(==|!=)\s*AURA_REMOVE_BY_([A-Z_]+)|AURA_REMOVE_BY_([A-Z_]+)\s*(==|!=)|case\s+AURA_REMOVE_BY_([A-Z_]+)")
_CLASS_RE = re.compile(r"^\s*(?:class|struct)\s+(\w+)\s*(?:final\s*)?:\s*public\s+(AuraScript|SpellScript)\b")
_REG_RE = re.compile(r"Register(?:SpellScript|SpellAndAuraScriptPair|SpellScriptWithArgs)\s*\(\s*(\w+)\s*(?:,\s*(\w+))?")


@dataclass
class ScriptModeRef:
    file: str
    line: int
    text: str
    modes: tuple[str, ...]
    comparisons: tuple[str, ...]
    reads_mode: bool


def _script_files() -> list[Path]:
    root = TC_ROOT / SCRIPTS
    if not root.is_dir():
        raise FailClosed(f"TrinityCore scripts not found at {root}")
    return sorted(root.rglob("*.cpp"))


@lru_cache(maxsize=1)
def script_mode_refs() -> tuple[ScriptModeRef, ...]:
    """Every line in ``src/server/scripts`` naming an AuraRemoveMode or calling GetRemoveMode()."""
    out = []
    for path in _script_files():
        rel = str(path.relative_to(TC_ROOT))
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
            modes = tuple(_MODE_RE.findall(line))
            reads = "GetRemoveMode()" in line
            if not modes and not reads:
                continue
            comps = []
            for m in _CMP_RE.finditer(line):
                if m.group(1):
                    comps.append(f"{m.group(1)} {m.group(2)}")
                elif m.group(3):
                    comps.append(f"{m.group(4)} {m.group(3)}")
                else:
                    comps.append(f"case {m.group(5)}")
            out.append(ScriptModeRef(rel, n, line.strip(), modes, tuple(comps), reads))
    return tuple(out)


@lru_cache(maxsize=None)
def _lexical_classes(rel: str) -> tuple[tuple[str, str, int, int], ...]:
    """(name, base, first line, last line) of SpellScript/AuraScript classes by brace matching.

    Fallback for files the Dummy-pass script index could not parse (it lists
    ``spell_priest.cpp`` under ``provenance.parse_errors``).  Lexical: braces in
    strings/comments are not special-cased; checked against the index where both exist (tests).
    """
    text = (TC_ROOT / rel).read_text(encoding="utf-8", errors="replace").split("\n")
    out = []
    for i, line in enumerate(text):
        m = _CLASS_RE.match(line)
        if not m:
            continue
        depth, started, end = 0, False, None
        for j in range(i, len(text)):
            depth += text[j].count("{") - text[j].count("}")
            if "{" in text[j]:
                started = True
            if started and depth <= 0:
                end = j + 1
                break
        out.append((m.group(1), m.group(2), i + 1, end or len(text)))
    return tuple(out)


@lru_cache(maxsize=None)
def _lexical_registrations(rel: str) -> dict[str, tuple[str, ...]]:
    """registration name -> class names, lexical (``RegisterSpellScript(X)``, ``RegisterSpellAndAuraScriptPair(A, B)``)."""
    out: dict[str, list[str]] = defaultdict(list)
    for m in _REG_RE.finditer((TC_ROOT / rel).read_text(encoding="utf-8", errors="replace")):
        names = [g for g in m.groups() if g]
        out[names[0]].extend(names)
    return {k: tuple(v) for k, v in out.items()}


def script_classes_reading_mode(bundle) -> list[dict[str, Any]]:
    """Script classes containing remove-mode references, with the spells bound to them.

    Class attribution: innermost class of the Dummy-pass script index whose line
    range contains the reference; files outside the index use the lexical fallback.
    Spell binding: ``spell_script_names`` (world overlay) -> registration -> classes.
    """
    index = bundle.index
    by_file: dict[str, list] = defaultdict(list)
    for sc in index.classes.values():
        by_file[sc.file].append(sc)
    parse_errors = set(index.provenance.get("parse_errors", []))
    classes: dict[tuple[str, str], dict[str, Any]] = {}
    unattributed = []
    for ref in script_mode_refs():
        owner = None
        if ref.file in by_file and ref.file not in parse_errors:
            cands = [c for c in by_file[ref.file] if c.line <= ref.line <= c.end_line and c.kind in ("AuraScript", "SpellScript")]
            if cands:
                c = min(cands, key=lambda c: c.end_line - c.line)
                owner = (c.name, c.kind, c.line, [h.get("list") for h in c.hooks])
        else:
            cands = [c for c in _lexical_classes(ref.file) if c[2] <= ref.line <= c[3]]
            if cands:
                c = min(cands, key=lambda c: c[3] - c[2])
                owner = (c[0], c[1], c[2], None)
        if owner is None:
            unattributed.append({"file": ref.file, "line": ref.line, "text": ref.text})
            continue
        key = (ref.file, owner[0])
        row = classes.setdefault(key, {"class": owner[0], "kind": owner[1], "file": ref.file, "line": owner[2],
                                       "hooks": sorted(set(h for h in owner[3] or [] if h)) if owner[3] is not None else None,
                                       "index": owner[3] is not None, "refs": [], "modes": set(), "comparisons": set(),
                                       "opaque_reads": 0})
        row["refs"].append(ref.line)
        row["modes"].update(ref.modes)
        row["comparisons"].update(ref.comparisons)
        if ref.reads_mode and not ref.modes:
            row["opaque_reads"] += 1
    # bind spells
    catalog = bundle.catalog
    by_name = bundle.world.script_names(catalog)
    class_spells: dict[tuple[str, str], set[int]] = defaultdict(set)
    for spell, names in by_name.items():
        for name in names:
            res = index.resolve_script_name(name)
            for sc in res["classes"]:
                class_spells[(sc.file, sc.name)].add(spell)
            if not res["resolved"]:
                for rel in parse_errors:
                    regs = _lexical_registrations(rel)
                    for cname in regs.get(name, ()):
                        class_spells[(rel, cname)].add(spell)
    out = []
    for key, row in sorted(classes.items()):
        row["modes"] = sorted(row["modes"])
        row["comparisons"] = sorted(row["comparisons"])
        row["spells"] = sorted(class_spells.get(key, ()))
        out.append(row)
    return out + ([{"unattributed": unattributed}] if unattributed else [])


def script_mode_writers(reader_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Script lines that PRODUCE a remove mode (mode named as an argument, not compared).

    Mirrors the reader attribution of :func:`script_classes_reading_mode` (same classes, same spell binding);
    a line counts as a writer when it names ``AURA_REMOVE_BY_*`` outside ``==`` / ``!=`` / ``case``.
    """
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in reader_rows:
        if "class" in r:
            by_file[r["file"]].append(r)
    out = []
    for ref in script_mode_refs():
        compared = {c.split()[-1] for c in ref.comparisons}
        written = [m for m in ref.modes if m not in compared]
        if not written:
            continue
        owner = next((r for r in by_file.get(ref.file, []) if ref.line in r["refs"]), None)
        out.append({"file": ref.file, "line": ref.line, "modes": sorted(set(written)), "text": ref.text,
                    "class": owner["class"] if owner else None, "spells": owner["spells"] if owner else []})
    return out


@lru_cache(maxsize=1)
def interrupt_flag_consumed() -> dict[str, bool]:
    """``"1:Name"`` / ``"2:Name"`` -> whether any game source names ``SpellAuraInterruptFlags[2]::Name``
    outside its enum definition (SpellDefines.h).  Lexical consumer search over src/server/game/*.cpp."""
    root = TC_ROOT / GAME
    if not root.is_dir():
        raise FailClosed("TrinityCore checkout absent")
    code = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted(root.rglob("*.cpp")))
    out = {}
    for tag, enum, table in (("1", "SpellAuraInterruptFlags", AURA_INTERRUPT_FLAGS),
                             ("2", "SpellAuraInterruptFlags2", AURA_INTERRUPT_FLAGS2)):
        for name in table.values():
            out[f"{tag}:{name}"] = re.search(rf"{enum}::{name}\b", code) is not None
    return out


# ---------------------------------------------------------------------------
# Per-spell removal facts (DB2 + overlay + scripts)
# ---------------------------------------------------------------------------

# SpellAuraInterruptFlags / SpellAuraInterruptFlags2 (SpellDefines.h:77-148)
AURA_INTERRUPT_FLAGS = {
    0x00000001: "HostileActionReceived", 0x00000002: "Damage", 0x00000004: "Action", 0x00000008: "Moving",
    0x00000010: "Turning", 0x00000020: "Anim", 0x00000040: "Dismount", 0x00000080: "UnderWater",
    0x00000100: "AboveWater", 0x00000200: "Sheathing", 0x00000400: "Interacting", 0x00000800: "Looting",
    0x00001000: "Attacking", 0x00002000: "ItemUse", 0x00004000: "DamageChannelDuration", 0x00008000: "Shapeshifting",
    0x00010000: "ActionDelayed", 0x00020000: "Mount", 0x00040000: "Standing", 0x00080000: "LeaveWorld",
    0x00100000: "StealthOrInvis", 0x00200000: "InvulnerabilityBuff", 0x00400000: "EnterWorld", 0x00800000: "PvPActive",
    0x01000000: "NonPeriodicDamage", 0x02000000: "LandingOrFlight", 0x04000000: "Release", 0x08000000: "DamageCancelsScript",
    0x10000000: "EnteringCombat", 0x20000000: "Login", 0x40000000: "Summon", 0x80000000: "LeavingCombat",
}
AURA_INTERRUPT_FLAGS2 = {
    0x1: "Falling", 0x2: "Swimming", 0x4: "NotMoving", 0x8: "Ground", 0x10: "Transform", 0x20: "Jump",
    0x40: "ChangeSpec", 0x80: "AbandonVehicle", 0x100: "StartOfRaidEncounterAndStartOfMythicPlus",
    0x200: "EndOfRaidEncounterAndStartOfMythicPlus", 0x400: "Disconnect", 0x800: "EnteringInstance", 0x1000: "DuelEnd",
    0x2000: "LeaveArenaOrBattleground", 0x4000: "ChangeTalent", 0x8000: "ChangeGlyph", 0x10000: "SeamlessTransfer",
    0x20000: "WarModeLeave", 0x40000: "TouchingGround", 0x80000: "ChromieTime", 0x100000: "SplineFlightOrFreeFlight",
    0x200000: "ProcOrPeriodicAttacking", 0x400000: "ChallengeModeStart", 0x800000: "StartOfEncounter",
    0x1000000: "EndOfEncounter", 0x2000000: "ReleaseEmpower",
}
LEAVING_COMBAT = 0x80000000

# (word, bit) of the attributes the removal consumers read (SharedDefines.h)
ATTR = {
    "PASSIVE": (0, 0x40), "NOT_SHAPESHIFTED": (0, 0x10000), "NO_AURA_CANCEL": (0, 0x80000000),
    "IS_CHANNELLED": (1, 0x4), "IS_SELF_CHANNELLED": (1, 0x40), "IGNORE_OWNERS_DEATH": (1, 0x00800000),
    "AURA_STAYS_AFTER_COMBAT": (1, 0x02000000), "DISPEL_ALL_STACKS": (1, 0x40000000),
    "ALLOW_WHILE_NOT_SHAPESHIFTED_CASTER_FORM": (2, 0x00080000), "ALLOW_DEAD_TARGET": (2, 0x1),
    "ONLY_ON_GHOSTS": (3, 0x1000), "ALLOW_AURA_WHILE_DEAD": (3, 0x00100000),
    "CANNOT_BE_STOLEN": (4, 0x40), "AURA_EXPIRES_OFFLINE": (4, 0x4), "ALLOW_ENTERING_ARENA": (4, 0x00200000),
    "REMOVE_ENTERING_ARENA": (5, 0x4), "DISABLE_AURA_WHILE_DEAD": (7, 0x4), "DISPEL_REMOVES_CHARGES": (7, 0x400),
    "REMOVE_OUTSIDE_DUNGEONS_AND_RAIDS": (8, 0x4), "LIMIT_N": (5, 0x20),
}

MODE_SENSITIVE_AURAS = {495: "EXPIRE", 86: "DEATH", 430: "EXPIRE", 397: "!DEFAULT", 398: "!DEFAULT"}


def attrs_of(data, spell: int) -> tuple[int, ...]:
    misc = data.row("SpellMisc", spell)
    if misc is None:
        raise FailClosed(f"spell {spell}: no DIFFICULTY_NONE SpellMisc row")
    words = [int(misc[f"Attributes_{i}"]) & 0xFFFFFFFF for i in range(17)]
    from .passive import effective_passive
    if effective_passive(data, spell)["passive"]:  # SpellMgr.cpp:3839-3843, 5301-5302 (R1-02)
        words[0] |= 0x40
    return tuple(words)


def has(attrs: tuple[int, ...], name: str) -> bool:
    word, bit = ATTR[name]
    return bool(attrs[word] & bit)


def decode_flags(value: int, table: dict[int, str]) -> list[str]:
    return [name for bit, name in sorted(table.items()) if value & bit]


def death_class(attrs: tuple[int, ...]) -> str:
    """Fate of an aura of this spell on its dying holder.

    Mirrors: Unit.cpp:4479 ``!aura->IsPassive() && !aura->IsDeathPersistent()``
    (IsDeathPersistent = SpellInfo.cpp:1830 ATTR3_ALLOW_AURA_WHILE_DEAD) and
    SpellAuras.cpp:2543 (ATTR7_DISABLE_AURA_WHILE_DEAD, surviving auras only).

    Spell-level attributes only; ``LoadSpellInfoCorrections`` edits are not
    ported (flagged separately).  Does not include the combat-exit / channel
    pre-steps of death (see :func:`aura_lifecycle.lifetime.death_order`).
    """
    passive, persistent = has(attrs, "PASSIVE"), has(attrs, "ALLOW_AURA_WHILE_DEAD")
    if not passive and not persistent:
        return "removed-death"
    base = "survives-passive" if passive else "survives-death-persistent"
    return base + ("+disabled-while-dead" if has(attrs, "DISABLE_AURA_WHILE_DEAD") else "")


def spell_removal_facts(ctx, spell: int) -> dict[str, Any]:
    """Every DB2/overlay/script fact that selects a removal path for ``spell``."""
    data = ctx.data
    attrs = attrs_of(data, spell)
    effects = data.effects(spell)
    auras = sorted({int(e["EffectAura"]) for e in effects if e["EffectAura"]})
    inter = data.row("SpellInterrupts", spell) or {}
    f1 = int(inter.get("AuraInterruptFlags_0", 0)) & 0xFFFFFFFF
    f2 = int(inter.get("AuraInterruptFlags_1", 0)) & 0xFFFFFFFF
    shape = data.row("SpellShapeshift", spell) or {}
    stances = (int(shape.get("ShapeshiftMask_0", 0)) & 0xFFFFFFFF) | ((int(shape.get("ShapeshiftMask_1", 0)) & 0xFFFFFFFF) << 32)
    cats = data.row("SpellCategories", spell) or {}
    equipped = data.row("SpellEquippedItems", spell)
    linked = ctx.bundle.world.linked_for(ctx.catalog, spell)
    return {
        "spell": spell, "name": ctx.name(spell), "build_skew": ctx.is_skew(spell),
        "passive": has(attrs, "PASSIVE"), "death_persistent": has(attrs, "ALLOW_AURA_WHILE_DEAD"),
        "disable_while_dead": has(attrs, "DISABLE_AURA_WHILE_DEAD"), "only_on_ghosts": has(attrs, "ONLY_ON_GHOSTS"),
        "ignore_owners_death_nyi": has(attrs, "IGNORE_OWNERS_DEATH"),
        "death_class": death_class(attrs),
        "channelled": has(attrs, "IS_CHANNELLED") or has(attrs, "IS_SELF_CHANNELLED"),
        "no_aura_cancel": has(attrs, "NO_AURA_CANCEL"), "stays_after_combat": has(attrs, "AURA_STAYS_AFTER_COMBAT"),
        "arena": {"allow_entering": has(attrs, "ALLOW_ENTERING_ARENA"), "remove_entering": has(attrs, "REMOVE_ENTERING_ARENA")},
        "aura_interrupt_flags": decode_flags(f1, AURA_INTERRUPT_FLAGS),
        "aura_interrupt_flags2": decode_flags(f2, AURA_INTERRUPT_FLAGS2),
        "leaving_combat_interrupt": bool(f1 & LEAVING_COMBAT),
        "stances_mask": stances,
        "shape_loss_candidate": bool(stances) and not has(attrs, "ALLOW_WHILE_NOT_SHAPESHIFTED_CASTER_FORM")
        and not has(attrs, "NOT_SHAPESHIFTED"),
        "equipped_item_requirement": bool(equipped and int(equipped.get("EquippedItemClass", -1)) >= 0),
        "dispel_type": int(cats.get("DispelType", 0)), "mechanic": int(cats.get("Mechanic", 0)),
        "effect_mechanics": sorted({int(e["EffectMechanic"]) for e in effects if e["EffectMechanic"]}),
        "aura_types": auras,
        "mode_sensitive_aura_types": {str(a): MODE_SENSITIVE_AURAS[a] for a in auras if a in MODE_SENSITIVE_AURAS},
        "linked": {k: sorted(v) for k, v in linked.items()},
    }


def explain(ctx, spell: int, script_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """``aura_lifecycle.py removal <spell>``: which taxonomy rows can end this spell's auras, and under which mode."""
    facts = spell_removal_facts(ctx, spell)
    if not any(int(e["EffectAura"]) for e in ctx.data.effects(spell)):
        raise FailClosed(f"spell {spell}: no aura effect (not a provider)")
    applies: list[dict[str, Any]] = []

    def add(site: str, why: str) -> None:
        s = next(x for x in SITES if x.id == site)
        applies.append({"site": site, "reason": s.reason, "mode": s.mode, "why": why, "coords": [coord(a) for a in s.anchors]})

    add("RM-01", "every finite unit-owned aura (dynobj auras: RM-02)")
    add("RM-17", "any later aura that cannot stack with it (track A decides which)")
    add("RM-34", "script / engine direct removal is always possible")
    if facts["death_class"] == "removed-death":
        add("RM-18", "not passive and not ATTR3_ALLOW_AURA_WHILE_DEAD")
    if facts["death_class"].endswith("+disabled-while-dead"):
        add("RM-20", "survives death but ATTR7_DISABLE_AURA_WHILE_DEAD")
    if facts["only_on_ghosts"]:
        add("RM-19", "ATTR3_ONLY_ON_GHOSTS")
    if facts["channelled"]:
        for s in ("RM-03", "RM-04", "RM-05", "RM-06"):
            add(s, "channelled spell")
    if not facts["no_aura_cancel"] and not facts["passive"] and not facts["channelled"]:
        add("RM-07", "cancellable if SpellInfo::IsPositive (positivity: targeting.positivity, not evaluated here)")
    if facts["aura_interrupt_flags"] or facts["aura_interrupt_flags2"]:
        add("RM-09", "interrupt flags " + ",".join(facts["aura_interrupt_flags"] + facts["aura_interrupt_flags2"]))
    if facts["dispel_type"]:
        add("RM-11", f"DispelType {facts['dispel_type']} (see dispel.py)")
    if facts["mechanic"] or facts["effect_mechanics"]:
        add("RM-13", "has a mechanic: mechanic dispel / immunity purge candidate")
    if facts["shape_loss_candidate"]:
        add("RM-24", "stance-restricted caster-self aura")
    if facts["equipped_item_requirement"]:
        add("RM-26", "SpellEquippedItems row (item-cast identity is runtime)")
    if not facts["stays_after_combat"]:
        add("RM-27", "creature holders only: removed on evade")
    if 69 in facts["aura_types"] or 301 in facts["aura_types"]:
        add("RM-14", "absorb aura type")
    rows = script_rows if script_rows is not None else []
    scripts = [r for r in rows if spell in r.get("spells", [])]
    return {**facts, "removal_sites": applies, "script_mode_readers": scripts,
            "death_note": "death != natural expiry: DEATH mode, no final tick, EXPIRE handlers skipped (lifetime.py)",
            "evidence": ["db2-fact", "trinity-consumer"] + (["script-consumer"] if scripts else [])}


def census(ctx, pops: dict[str, frozenset[int]], script_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts of removal-relevant facts over the provider populations (all / player / controlled)."""
    data = ctx.data
    out: dict[str, Any] = {}
    script_spells = {s for r in script_rows for s in r.get("spells", [])}
    for pop, spells in sorted(pops.items()):
        c: Counter = Counter()
        flags: Counter = Counter()
        missing = 0
        for spell in sorted(spells):
            try:
                attrs = attrs_of(data, spell)
            except FailClosed:
                missing += 1
                continue
            c[f"death_class:{death_class(attrs)}"] += 1
            for name in ("ONLY_ON_GHOSTS", "IGNORE_OWNERS_DEATH", "NO_AURA_CANCEL", "AURA_STAYS_AFTER_COMBAT",
                         "DISPEL_ALL_STACKS", "DISPEL_REMOVES_CHARGES", "CANNOT_BE_STOLEN", "REMOVE_ENTERING_ARENA",
                         "ALLOW_ENTERING_ARENA", "AURA_EXPIRES_OFFLINE", "REMOVE_OUTSIDE_DUNGEONS_AND_RAIDS"):
                if has(attrs, name):
                    c[f"attr:{name}"] += 1
            if has(attrs, "IS_CHANNELLED") or has(attrs, "IS_SELF_CHANNELLED"):
                c["channelled"] += 1
            inter = data.row("SpellInterrupts", spell) or {}
            f1 = int(inter.get("AuraInterruptFlags_0", 0)) & 0xFFFFFFFF
            f2 = int(inter.get("AuraInterruptFlags_1", 0)) & 0xFFFFFFFF
            if f1 or f2:
                c["any_aura_interrupt_flag"] += 1
            for n in decode_flags(f1, AURA_INTERRUPT_FLAGS):
                flags[n] += 1
            for n in decode_flags(f2, AURA_INTERRUPT_FLAGS2):
                flags["2:" + n] += 1
            if f1 & LEAVING_COMBAT and death_class(attrs) != "removed-death":
                c["survives_death_sweep_but_leaving_combat_interrupt"] += 1
            auras = {int(e["EffectAura"]) for e in data.effects(spell) if e["EffectAura"]}
            for a in sorted(auras & set(MODE_SENSITIVE_AURAS)):
                c[f"mode_sensitive_aura:{a}"] += 1
            cats = data.row("SpellCategories", spell) or {}
            if int(cats.get("DispelType", 0)):
                c["dispel_type_nonzero"] += 1
            if spell in script_spells:
                c["script_reads_remove_mode"] += 1
            if has(attrs, "LIMIT_N"):
                c["attr:LIMIT_N"] += 1
            restr = data.row("SpellAuraRestrictions", spell) or {}
            if int(restr.get("CasterAuraState", 0)):
                c["caster_aura_state"] += 1
                if has(attrs, "PASSIVE"):
                    c["caster_aura_state_passive"] += 1
            if auras & {23, 227}:
                c["periodic_trigger"] += 1
        consumed = interrupt_flag_consumed()
        unconsumed = {k: v for k, v in flags.items()
                      if not consumed.get(k if k.startswith("2:") else "1:" + k, True)}
        out[pop] = {"population": pop, "spells": len(spells), "no_spellmisc_row": missing,
                    "counts": dict(sorted(c.items())), "aura_interrupt_flag_counts": dict(sorted(flags.items())),
                    "unconsumed_interrupt_flag_occurrences": dict(sorted(unconsumed.items())),
                    "unconsumed_interrupt_flag_total": sum(unconsumed.values())}
    return out


__all__ = ["ANCHORS", "MODE_BRANCHES", "REMOVE_MODES", "SITES", "census", "coord", "death_class", "explain",
           "interrupt_flag_consumed", "script_classes_reading_mode", "script_mode_refs", "script_mode_writers", "spell_removal_facts", "taxonomy", "verify_anchors"]
