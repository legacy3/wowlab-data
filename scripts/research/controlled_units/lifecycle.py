"""Lifecycle of controlled units: creation order, duration, despawn, replacement, owner events.

Phase lists are transcribed call sequences (``function`` + ``path:line``) read in
the pinned checkout.  ``policy(record)`` applies them to one population record:
which ``TempSummonType`` the unit gets, from which duration source, which slot
rule replaces it, and what happens on death / owner death / logout.
Stat *values* set during initialisation belong to Track B; this module names the
functions and their order only.
"""

from __future__ import annotations

import json
from typing import Any

from . import CORPORA
from .population import Context, Record, provenance
from .vocabulary import MAX_TOTEM_SLOT, NUMSUMMONS_FROM_EFFECT_VALUE, SUMMON_SLOT, TEMPSUMMON_TYPE

TC = "src/server/game"
E = f"{TC}/Spells/SpellEffects.cpp"
O = f"{TC}/Entities/Object/Object.cpp"
T = f"{TC}/Entities/Creature/TemporarySummon.cpp"
U = f"{TC}/Entities/Unit/Unit.cpp"
P = f"{TC}/Entities/Pet/Pet.cpp"
PL = f"{TC}/Entities/Player/Player.cpp"
TO = f"{TC}/Entities/Totem/Totem.cpp"


def ph(n: int, fn: str, coord: str, what: str, snapshot: str | None = None) -> dict[str, Any]:
    d = {"phase": n, "function": fn, "coordinate": coord, "what": what}
    if snapshot:
        d["identity_control_snapshot"] = snapshot
    return d


#: creation through Spell::EffectSummonType / Spell::SummonGuardian / Map::SummonCreature
CREATE_TEMPSUMMON: list[dict[str, Any]] = [
    ph(1, "Spell::EffectSummonType", f"{E}:1869-1881", "LAUNCH phase only; entry = MiscValue, properties = SummonProperties[MiscValueB] (return if missing)"),
    ph(2, "Spell::EffectSummonType", f"{E}:1883-1902", "caster = m_originalCaster or m_caster; privateObjectOwner from OnlyVisibleToSummoner[Group]; duration = SpellInfo::CalcDuration(caster)", "duration snapshot (owner Duration spell mods applied through GetSpellModOwner)"),
    ph(3, "Spell::EffectSummonType", f"{E}:1915-1937", "numSummons = effect value for the MiscValueB list, else 1"),
    ph(4, "Spell::EffectSummonType", f"{E}:1939-2078", "switch(Control) / switch(Title): SummonGuardian or Map::SummonCreature (see vocabulary.branch_table)"),
    ph(5, "Spell::SummonGuardian", f"{E}:5015-5037", "unitCaster (totem -> owner); duration = CalcDuration(m_originalCaster); Map::SummonCreature per count at dest / random point (radius 5)"),
    ph(6, "Map::SummonCreature", f"{O}:1195-1263", "UnitTypeMask from Control/Title -> new TempSummon/Guardian/Puppet/Totem/Minion (Minion ctor: m_owner, InitCharmInfo T:446-454; Guardian ctor: CONTROLABLE_GUARDIAN for Title::Pet or Control::PET T:513-523)"),
    ph(7, "Map::SummonCreature -> Creature::Create", f"{O}:1265-1269", "GUID = GenerateLowGuid<HighGuid::Creature>; fails (nullptr) without a creature_template", "generation identity: fresh low GUID per summon, no reuse counter"),
    ph(8, "Map::SummonCreature", f"{O}:1271-1283", "transport passenger; PhasingHandler::InheritPhaseShift unless IgnoreSummonerPhase; SetCreatedBySpell(spellId); SetHomePosition", "CreatedBySpell snapshot"),
    ph(9, "TempSummon::InitStats", f"{T}:188-203", "m_timer = m_lifetime = duration; TempSummonType from duration/UseDemonTimeout when still MANUAL_DESPAWN", "duration policy snapshot"),
    ph(10, "TempSummon::InitStats", f"{T}:205-218", "player summoner: trigger creatures with a spell become m_ControlledByPlayer; creature_summoned_data visible-to-summoner ids"),
    ph(11, "TempSummon::InitStats", f"{T}:224-239", "slot bookkeeping: Slot -1 -> FindUsableTotemSlot; Slot != 0 -> UnSummon the previous occupant, m_SummonSlot[slot] = this", "replacement rule (slot)"),
    ph(12, "TempSummon::InitStats", f"{T}:241-255", "SetLevel(clamp(summoner level, scaling min/max)) unless UseCreatureLevel; faction from properties or summoner (UseSummonerFaction)", "level + faction snapshot"),
    ph(13, "Minion::InitStats", f"{T}:456-466", "REACT_PASSIVE; SetCreatorGUID(owner); SetFaction(owner faction); owner->SetMinion(this, true)", "creator + faction snapshot; enters m_Controlled"),
    ph(14, "Unit::SetMinion(apply)", f"{U}:6264-6336", "OwnerGUID; m_Controlled; PLAYER_CONTROLLED flag; guardian-pet single-slot rule (6274-6295); MinionGUID for controllable guardians; Critter for Companions; PvP flags copy; speed copy for Pets; cooldown-started-on-event", "owner, PvP flags, controlled-by-player snapshot; replacement rule (pet slot)"),
    ph(15, "Guardian::InitStats", f"{T}:525-535", "InitStatsForLevel(GetLevel()) [Track B]; InitCharmCreateSpells for player-owned controllable guardians; REACT_AGGRESSIVE"),
    ph(16, "Totem::InitStats / Puppet::InitStats", f"{TO}:53-88; {T}:563-567", "totem: SMSG_TOTEM_CREATED for slots 1-4, race model, Minion::InitStats, TOTEM_ACTIVE if the totem spell has a cast time, m_duration = duration; puppet: Minion::InitStats + REACT_PASSIVE"),
    ph(17, "Map::SummonCreature", f"{O}:1287-1314", "SetPrivateObjectOwner; smooth phasing; AddToMap (failure deletes the summon)"),
    ph(18, "TempSummon::InitSummon", f"{T}:261-278", "summoner creature/gameobject AI JustSummoned; own AI IsSummonedBy"),
    ph(19, "Guardian::InitSummon / Totem::InitSummon / Puppet::InitSummon", f"{T}:537-547; {TO}:90-98; {T}:569-574", "guardian: CharmSpellInitialize when it is the player's MinionGUID and no charm; totem: cast passive totem spell(s); puppet: SetCharmedBy(owner, CHARM_TYPE_POSSESS)"),
    ph(20, "Spell::EffectSummonType (default arm)", f"{E}:2022-2028", "SetTempSummonType(computed); ALLY -> SetOwnerGUID(caster); WILD by player -> SetDemonCreatorGUID(caster); no CreatorGUID (early return :2030)", "owner/demon-creator snapshot (ALLY/WILD only)"),
    ph(21, "Spell::SummonGuardian (post)", f"{E}:5041-5066", "engineering-skill level for guardians summoned by an engineering item; follow angle for minions with a dest; 27893 weapon display; ExecuteLogEffectSummonObject"),
    ph(22, "Spell::EffectSummonType (post)", f"{E}:2080-2084", "for the non-SummonGuardian arms with a summon: SetCreatorGUID(caster)", "creator snapshot"),
]

#: creation of a permanent class pet (SPELL_EFFECT_SUMMON_PET)
CREATE_SUMMON_PET: list[dict[str, Any]] = [
    ph(1, "Spell::EffectSummonPet", f"{E}:2658-2677", "HIT phase; owner = caster player (totem -> its player); non-player -> SummonGuardian(properties 67)"),
    ph(2, "Spell::EffectSummonPet", f"{E}:2679-2712", "existing Pet: same entry (or entry 0) -> teleport + PetSpellInitialize, return; other entry -> Player::RemovePet(PET_SAVE_NOT_IN_SLOT)", "replacement rule (one Pet per player)"),
    ph(3, "Spell::EffectSummonPet", f"{E}:2714-2721", "entry 0 -> PetSaveMode = effect value (Call Pet N); Player::SummonPet(entry, slot, pos, duration = 0)"),
    ph(4, "Player::SummonPet -> new Pet(SUMMON_PET)", f"{PL}:30440-30444; {P}:46-65", "Pet ctor: UNIT_MASK_PET (+HUNTER_PET), CONTROLABLE_GUARDIAN, InitCharmInfo; LoadPetFromDB(entry, 0, current=false, slot)"),
    ph(5, "Pet::LoadPetFromDB", f"{P}:205-449", "stable lookup (GetLoadPetInfo); HighGuid::Pet GUID; setPetType; faction = owner; SetCreatedBySpell(saved); SetPetNumber; SUMMON_PET level = owner level; SetCreatorGUID(owner); InitStatsForLevel [B]; SynchronizeLevelWithOwner; ReactState from save; health/mana from save unless !current summon pet; current-pet index; owner->SetMinion; LoadPetActionBar; AddToMap; async: _LoadAuras, _LoadSpells, cooldowns, LearnPetPassives, InitLevelupSpellsForLevel, CastPetAuras, SetSpecialization, PetSpellInitialize", "identity: PetNumber, CreatedBySpell, spec, action bar, react state come from character_pet (Track C input)"),
    ph(6, "Player::SummonPet (fresh)", f"{PL}:30462-30522", "Create(HighGuid::Pet, GeneratePetNumber); RemovePet(current); InheritPhaseShift; SetCreatorGUID; SetFaction(owner); InitStatsForLevel(owner level) [B]; SetMinion; SetPetNumber; SetClass(CLASS_MAGE); XP fields; full health/mana; AddToMap; unslotted stable index; FillPetInfo; InitPetCreateSpells; SavePetToDB(AS_CURRENT); PetSpellInitialize", "generation identity: new PetNumber (sObjectMgr->GeneratePetNumber) per fresh summon"),
    ph(7, "Spell::EffectSummonPet (post)", f"{E}:2725-2743", "isNew: react state from caster type; SetCreatedBySpell(m_spellInfo->Id); generated name; ExecuteLogEffectSummonObject", "CreatedBySpell snapshot"),
]

#: hunter tame (SPELL_EFFECT_TAMECREATURE / CREATE_TAMED_PET)
CREATE_HUNTER_PET: list[dict[str, Any]] = [
    ph(1, "Spell::EffectTameCreature", f"{E}:2603-2626", "HIT_TARGET; caster without pet, target creature not a pet, caster class hunter; finish()"),
    ph(2, "Unit::CreateTamedPetFrom(Creature*)", f"{U}:11089-11111", "new Pet(HUNTER_PET); Pet::CreateBaseAtCreature (P:768-799: CreateBaseAtTamed -> Create(HighGuid::Pet, GeneratePetNumber), display id, family name); level = target level (min owner-5); InitTamedPet"),
    ph(3, "Unit::InitTamedPet", f"{U}:11133-11168", "free active stable slot required; SetCreatorGUID; SetFaction(owner); SetCreatedBySpell(tame spell); UNIT_FLAG_PLAYER_CONTROLLED; InitStatsForLevel [B]; phase; SetPetNumber; InitPetCreateSpells; full health; stable index; FillPetInfo; AddPetToUpdateFields", "creator, faction, CreatedBySpell, pet number snapshot"),
    ph(4, "Spell::EffectTameCreature (post)", f"{E}:2633-2653", "DespawnOrUnsummon target; level-1 then level; AddToMap; caster->SetMinion; SavePetToDB(AS_CURRENT); PetSpellInitialize"),
    ph(5, "Spell::EffectCreateTamedPet", f"{E}:4911-4940", "same through CreateTamedPetFrom(entry) (U:11113-11131) on the target hunter player"),
]

#: charm / possess through auras
CHARM: list[dict[str, Any]] = [
    ph(1, "AuraEffect::HandleModCharm / HandleModPossess / HandleCharmConvert / HandleModPossessPet", f"{TC}/Spells/Auras/SpellAuraEffects.cpp:3238-3331", "REAL apply: target->SetCharmedBy(Aura::GetCaster(), type, aurApp); remove: RemoveCharmedBy"),
    ph(2, "Unit::SetCharmedBy", f"{U}:11790-11858", "guards: POSSESS needs a player charmer; not self; not already charmed; CastStop/AttackStop; charmer StopCastingCharm/BindSight; aura not already removed"),
    ph(3, "Unit::SetCharmedBy", f"{U}:11860-11879", "_oldFactionId saved; SetFaction(charmer); movement cleared; charmer->SetCharm(this, true) (U:6442-6470: Charm/CharmedBy, PLAYER_CONTROLLED, PvP copy)", "faction + PvP snapshot"),
    ph(4, "Unit::SetCharmedBy", f"{U}:11900-11908", "InitCharmInfo + InitPossessCreateSpells / InitCharmCreateSpells unless a CharmInfo exists (pets keep theirs)"),
    ph(5, "Unit::SetCharmedBy", f"{U}:11910-11961", "player charmer: VEHICLE/POSSESS -> UNIT_FLAG_POSSESSED + client control; CHARM -> warlock demon class hack, CharmSpellInitialize; UNIT_STATE_CHARMED; AI OnCharmed/ScheduleAIChange"),
    ph(6, "Unit::RemoveCharmedBy", f"{U}:11964-12072", "type from state; faction restored (_oldFactionId / RestoreFaction); LastCharmerGUID; charmer->SetCharm(false); client control back; DeleteCharmInfo unless IsGuardian; AI change"),
]

#: update / despawn
UPDATE_DESPAWN: list[dict[str, Any]] = [
    ph(1, "TempSummon::Update", f"{T}:73-81", "m_deathState == DEAD -> UnSummon (every type)"),
    ph(2, "TempSummon::Update", f"{T}:84-185", "per TempSummonType: TIMED_DESPAWN counts down always; TIMED_DESPAWN_OUT_OF_COMBAT counts down only out of combat and resets m_timer = m_lifetime while in combat; CORPSE_* wait for CORPSE; TIMED_OR_DEAD/CORPSE combine; MANUAL/DEAD never time out"),
    ph(3, "Totem::Update", f"{TO}:34-51", "ignores TempSummonType: UnSummon when owner or totem dead, or when m_duration <= diff (signed Milliseconds, Totem.h:57): a Totem-class unit created with duration 0 or -1 is removed on its first update"),
    ph(4, "Puppet::Update", f"{T}:576-588", "UnSummon when not alive"),
    ph(5, "Pet::Update", f"{P}:619-709", "CORPSE: Remove(NOT_IN_SLOT) unless hunter pet corpse timer; ALIVE: removed when owner out of visibility range or owner->GetPetGUID() lost; m_duration countdown -> Remove(AS_DELETED for non-SUMMON_PET, else NOT_IN_SLOT); focus regen"),
    ph(6, "TempSummon::UnSummon", f"{T}:332-357", "msTime -> ForcedDespawnDelayEvent; Pet -> Pet::Remove(NOT_IN_SLOT); summoner AI SummonedCreatureDespawn; AddObjectToRemoveList"),
    ph(7, "Totem::UnSummon", f"{TO}:100-143", "CombatStop; remove totem spell auras from self, owner and same-subgroup members; clear owner's totem slot; cooldown event; AddObjectToRemoveList"),
    ph(8, "TempSummon::RemoveFromWorld", f"{T}:359-374", "clear any m_SummonSlot of the summoner holding this GUID (Slot != 0)"),
    ph(9, "Minion::RemoveFromWorld -> Unit::SetMinion(remove)", f"{T}:468-475; {U}:6338-6416", "m_Controlled.erase; Critter/Pet GUID cleared; totem -> RemoveAllMinionsByEntry of its summon effects; cooldown event; MinionGUID handed to another controllable guardian (PetSpellInitialize / CharmSpellInitialize)", "replacement rule (minion succession)"),
    ph(10, "Pet::Remove -> Player::RemovePet", f"{P}:711-714; {PL}:22035-22128", "reagent return; CombatStop; ExitAllAreaTriggers; SavePetToDB(mode); stable index per PetSaveMode; SetMinion(false); AddObjectToRemoveList; m_removed; PetSpells packet"),
]

DEATH: list[dict[str, Any]] = [
    ph(1, "Creature::setDeathState(JUST_DIED)", f"{TC}/Entities/Creature/Creature.cpp:2207-2213", "m_corpseRemoveTime = now + m_corpseDelay"),
    ph(2, "Unit::setDeathState", f"{U}:9160-9180", "CombatStop; interrupt; ExitVehicle; UnsummonAllTotems; RemoveAllControlled; RemoveAllAurasOnDeath (applies to the dying summon's own minions/totems)"),
    ph(3, "Minion::setDeathState", f"{T}:477-497", "JUST_DIED of a guardian pet that is the player's MinionGUID: another alive controlled unit of the same entry becomes MinionGUID/PetGUID (CharmSpellInitialize)", "replacement rule (same-entry succession on death)"),
    ph(4, "Pet::setDeathState", f"{P}:598-617", "CORPSE hunter pet: non-lootable/skinnable; ALIVE: CastPetAuras(true)"),
    ph(5, "TempSummon::Update", f"{T}:77-81", "DEAD -> UnSummon; CORPSE handled per type (CORPSE_DESPAWN immediate, CORPSE_TIMED_DESPAWN timer, TIMED_OR_CORPSE)"),
]

OWNER_EVENTS: list[dict[str, Any]] = [
    ph(1, "Unit::setDeathState (owner dies)", f"{U}:9167-9180", "UnsummonAllTotems (U:6743-6754: every m_SummonSlot summon UnSummon'd); RemoveAllControlled (U:6612-6637: charmed -> RemoveCharmAuras, owned summons -> UnSummon; FATAL logs if Pet/Minion/Charm GUIDs survive)"),
    ph(2, "Player::setDeathState(JUST_DIED)", f"{PL}:1157", "RemovePet(nullptr, PET_SAVE_NOT_IN_SLOT, returnreagent=true)"),
    ph(3, "Totem::Update (owner dead)", f"{TO}:36-40", "UnSummon"),
    ph(4, "WorldSession::LogoutPlayer", f"{TC}/Server/WorldSession.cpp:619", "RemovePet(nullptr, PET_SAVE_AS_CURRENT)"),
    ph(5, "Player::RemoveFromWorld", f"{PL}:1538-1540", "StopCastingCharm; UnsummonPetTemporaryIfAny (PL:27931-27944: remembers PetNumber + CreatedBySpell, RemovePet(AS_CURRENT))"),
    ph(6, "Unit::RemoveFromWorld (owner)", f"{U}:10273-10299", "RemoveCharmAuras; RemoveBindSightAuras; RemoveAllGameObjects; ExitVehicle; UnsummonAllTotems; RemoveAllControlled; a summon being removed is erased from its owner's m_Controlled (10297-10299)"),
    ph(7, "Player::TeleportTo", f"{PL}:1311; {PL}:1402", "UnsummonPetTemporaryIfAny on far teleports"),
    ph(8, "Player::IsPetNeedBeTemporaryUnsummoned", f"{PL}:27994-27997", "not in world / dead / flying / advanced flying -> temporary unsummon; ResummonPetTemporaryUnSummonedIfAny (27946-27963) reloads by PetNumber"),
    ph(9, "Player::Update (pet out of range)", f"{PL}:1094-1097", "RemovePet(NOT_IN_SLOT, returnreagent) when the pet is beyond visibility range and not possessed"),
    ph(10, "Player::ActivateTalentGroup (spec change)", f"{PL}:28801-28807", "RemovePet(NOT_IN_SLOT); UnsummonAllTotems; ExitVehicle; RemoveAllControlled"),
    ph(11, "Player::ResetTalents", f"{PL}:3458", "RemovePet(NOT_IN_SLOT, returnreagent)"),
    ph(12, "Player::GiveLevel / InitStatsForLevel", f"{PL}:2259-2261; {PL}:2497", "pet->SynchronizeLevelWithOwner (dynamic level for Pet only)"),
    ph(13, "Player::AddPetAura / RemovePetAura", f"{PL}:22179-22191", "spell_pet_auras rows: cast/remove on the current Pet; Pet::CastPetAuras on load and revive (P:408, 615)"),
]

REPLACEMENT_RULES: list[dict[str, Any]] = [
    {"rule": "slot", "coordinate": f"{T}:224-239", "statement": "SummonProperties.Slot != 0: the previous occupant of m_SummonSlot[slot] is UnSummon'd and replaced; Slot == 0 (SUMMON_SLOT_PET) does NO slot bookkeeping here",
     "slots": SUMMON_SLOT, "evidence_class": "trinity-consumer"},
    {"rule": "any-totem-slot", "coordinate": f"{T}:376-405", "statement": "Slot == -1: (1) slot already holding this GUID, (2) slot whose summon shares a TotemCategory / Totem id with this summon spell (IsSharingTotemSlotWith :407-434, SpellInfo::TotemCategory/Totem), (3) first empty totem slot, (4) slot holding the same creature entry, else no slot at all",
     "totem_slots": [SUMMON_SLOT[i] for i in range(1, MAX_TOTEM_SLOT)], "evidence_class": "trinity-consumer"},
    {"rule": "guardian-pet", "coordinate": f"{U}:6274-6295", "statement": "IsGuardianPet (IsPet() or Control == SUMMON_CATEGORY_PET): an existing guardian pet is removed when either is a Pet or the entries differ; two same-entry non-Pet guardian pets coexist; PetGUID = new minion",
     "evidence_class": "trinity-consumer"},
    {"rule": "one-pet-per-player", "coordinate": f"{E}:2679-2712; {PL}:30481-30482; {P}:222-223", "statement": "SUMMON_PET: same entry active -> no new unit (teleport); other entry -> RemovePet(NOT_IN_SLOT); LoadPetFromDB refuses to reload the current pet", "evidence_class": "trinity-consumer"},
    {"rule": "minion-succession", "coordinate": f"{U}:6379-6413; {T}:480-496", "statement": "when the MinionGUID unit is removed or a guardian pet dies, another controllable guardian (or same-entry alive unit) becomes MinionGUID/PetGUID", "evidence_class": "trinity-consumer"},
    {"rule": "num-summons", "coordinate": f"{E}:1915-1937", "statement": "for MiscValueB in the hard-coded list the effect value is the number of units; every other summon effect creates exactly one", "list": list(NUMSUMMONS_FROM_EFFECT_VALUE), "evidence_class": "trinity-consumer"},
    {"rule": "by-entry-removal", "coordinate": f"{U}:6429-6440", "statement": "Unit::RemoveAllMinionsByEntry UnSummons every m_Controlled summon with the entry; used by totem removal (U:6357-6370) and scripts (e.g. Ring of Frost spell_mage.cpp:1803-1817)", "evidence_class": "trinity-consumer"},
    {"rule": "totem-duration", "coordinate": f"{TO}:42-48; {TC}/Entities/Totem/Totem.h:57; {TO}:87", "statement": "Totem-class units (Title Totem or Lightwell under WILD/ALLY) are timed by Totem::m_duration = the InitStats duration, never by TempSummonType; duration <= 0 means removal on the first Totem::Update",
     "evidence_class": "trinity-consumer"},
    {"rule": "pet-not-tempsummon-timed", "coordinate": f"{P}:619-709; {TC}/Entities/Pet/Pet.h:151; {PL}:30446, 30514", "statement": "Pet::Update calls Creature::Update (never TempSummon::Update); the only Pet timer is Pet::m_duration (int32), set only when Player::SummonPet gets duration > 0 -- both callers pass 0 (SpellEffects.cpp:2721 EffectSummonPet, 4298 EffectResurrectPet)",
     "evidence_class": "trinity-consumer"},
    {"rule": "timer-mutation", "coordinate": f"{TC}/Entities/Creature/TemporarySummon.h:64-65", "statement": "RefreshTimer (m_timer = m_lifetime) / ModifyTimer (both) are script-only mutations (e.g. Inescapable Torment spell_priest.cpp:2630)", "evidence_class": "trinity-consumer"},
]


#: numeric behaviour records (brief section 4) for the lifecycle quantities
NUMERIC: list[dict[str, Any]] = [
    {"quantity": "summon duration", "source_type": "SpellDuration.Duration int32 (DurationEntry)", "reader": "SpellInfo::GetDuration (SpellInfo.cpp:3986-3991): no entry -> IsPassive ? -1 : 0; -1 kept; else abs()",
     "intermediate_type": "int32 base; double(base) + int32 flat, times float (binary32) totalmul, in Player::ApplySpellMod<int32> (Player.cpp:22844-22852)",
     "aggregation_precision": "totalmul is a float product accumulated in Player::GetSpellModValues (Player.cpp:22627); promoted to double only for the final multiply",
     "rounding": "int32() cast = truncation toward zero", "integer_conversion": "T(...) with T=int32 at Player.cpp:22851; reachable for any summon spell with a SpellModOp::Duration modifier on a Player/Pet/Totem caster (GetSpellModOwner)",
     "units": "milliseconds", "consumers": [f"{E}:1902 (EffectSummonType, caster = original caster or m_caster)", f"{E}:5024 (SummonGuardian, m_originalCaster)"],
     "notes": ["same binary32-product pattern as the op-11 cooldown 41,999 vs 42,000 ms divergence; a Python double product can differ by 1 ms after truncation",
               "the -1 (permanent) sentinel also passes through ApplySpellMod: a flat or pct Duration mod would turn it into a finite/other value (not guarded)"],
     "evidence_class": "trinity-consumer"},
    {"quantity": "TempSummon timer", "source_type": "Milliseconds (std::chrono, signed 64-bit)", "reader": "TempSummon::InitStats m_timer = m_lifetime = duration (TemporarySummon.cpp:192-193)",
     "intermediate_type": "Milliseconds; update diff uint32 ms converted to Milliseconds (TemporarySummon.cpp:83)", "aggregation_precision": "exact integer ms", "rounding": "none",
     "integer_conversion": "Milliseconds(int32 CalcDuration) at SpellEffects.cpp:1902/5024", "units": "milliseconds",
     "notes": ["UnSummon when m_timer <= diff (not < diff): a summon lives for the number of whole updates whose cumulative diff first reaches the duration"], "evidence_class": "trinity-consumer"},
    {"quantity": "Totem timer", "source_type": "Milliseconds", "reader": "Totem::InitStats m_duration = duration (Totem.cpp:87)", "intermediate_type": "Milliseconds", "aggregation_precision": "exact",
     "rounding": "none", "integer_conversion": "none", "units": "milliseconds", "notes": ["m_duration <= diff -> UnSummon (Totem.cpp:42-46); duration <= 0 -> first update"], "evidence_class": "trinity-consumer"},
    {"quantity": "Pet timer", "source_type": "uint32 duration argument of Player::SummonPet -> int32 Pet::m_duration", "reader": "Pet::SetDuration (Pet.h:88)", "intermediate_type": "int32",
     "aggregation_precision": "exact", "rounding": "none", "integer_conversion": "uint32 -> int32 in SetDuration; unreachable from spells (both callers pass 0)", "units": "milliseconds",
     "notes": ["Pet::Update: uint32(m_duration) > diff -> subtract, else Remove (Pet.cpp:660-668)"], "evidence_class": "trinity-consumer"},
    {"quantity": "summon level", "source_type": "UnitData ScalingLevelMin/Max/Delta (int32) and summoner level (uint8)", "reader": "TempSummon::InitStats (TemporarySummon.cpp:241-247)",
     "intermediate_type": "int32 clamp", "aggregation_precision": "exact", "rounding": "none", "integer_conversion": "std::clamp<int32> result stored in uint8 level (TemporarySummon.cpp:245): truncation modulo 256, unreachable while player levels <= 90",
     "units": "level", "notes": ["skipped with SummonPropertiesFlags::UseCreatureLevel"], "evidence_class": "trinity-consumer"},
]


def simulate_tempsummon(ts: str, duration_ms: int, ticks: list[tuple[int, bool, str]]) -> int | None:
    """Mirror of ``TempSummon::Update`` (TemporarySummon.cpp:73-186).

    ``ticks`` = [(diff_ms, in_combat, death_state)] with death_state in ALIVE/CORPSE/DEAD.
    Returns the index of the tick on which UnSummon is called, or None."""
    timer = lifetime = duration_ms
    for i, (diff, combat, death) in enumerate(ticks):
        if death == "DEAD":
            return i
        if ts in ("TEMPSUMMON_MANUAL_DESPAWN", "TEMPSUMMON_DEAD_DESPAWN"):
            continue
        if ts == "TEMPSUMMON_TIMED_DESPAWN":
            if timer <= diff:
                return i
            timer -= diff
        elif ts == "TEMPSUMMON_TIMED_DESPAWN_OUT_OF_COMBAT":
            if not combat:
                if timer <= diff:
                    return i
                timer -= diff
            elif timer != lifetime:
                timer = lifetime
        elif ts == "TEMPSUMMON_CORPSE_TIMED_DESPAWN":
            if death == "CORPSE":
                if timer <= diff:
                    return i
                timer -= diff
        elif ts == "TEMPSUMMON_CORPSE_DESPAWN":
            if death == "CORPSE":
                return i
        elif ts == "TEMPSUMMON_TIMED_OR_CORPSE_DESPAWN":
            if death == "CORPSE":
                return i
            if not combat:
                if timer <= diff:
                    return i
                timer -= diff
            elif timer != lifetime:
                timer = lifetime
        elif ts == "TEMPSUMMON_TIMED_OR_DEAD_DESPAWN":
            if not combat and death == "ALIVE":
                if timer <= diff:
                    return i
                timer -= diff
            elif timer != lifetime:
                timer = lifetime
        else:
            return i  # unknown type: UnSummon + error log (:181-184)
    return None


def simulate_totem(duration_ms: int, ticks: list[tuple[int, bool, bool]]) -> int | None:
    """Mirror of ``Totem::Update`` (Totem.cpp:34-51): ticks = [(diff_ms, owner_alive, totem_alive)]."""
    remaining = duration_ms
    for i, (diff, owner_alive, alive) in enumerate(ticks):
        if not owner_alive or not alive:
            return i
        if remaining <= diff:
            return i
        remaining -= diff
    return None


def policy(r: Record) -> dict[str, Any]:
    cat, br = r.category, r.branch
    cls = br.get("cxx_class", "-")
    out: dict[str, Any] = {"spell": r.spell_id, "name": r.spell_name, "effect_index": r.effect_index, "category": cat, "cxx_class": cls,
                           "duration_ms_spellduration": r.duration_ms, "evidence_class": br.get("evidence_class", "trinity-consumer")}
    if cat in ("permanent-class-pet",):
        out.update({"creation": "CREATE_SUMMON_PET", "duration_source": "none: EffectSummonPet passes duration 0 to Player::SummonPet (SpellEffects.cpp:2721); SpellDuration of the summon spell only marks isTemporarySummon for spell/action-bar loading (Pet.cpp:227)",
                    "tempsummon_type": None, "timer": "Pet::m_duration == 0 (no countdown)", "slot": "SUMMON_SLOT_PET via SetPetGUID (guardian-pet rule)",
                    "replacement": ["one-pet-per-player", "guardian-pet"], "death": "Pet::Update CORPSE -> Remove(NOT_IN_SLOT) (Pet.cpp:629-637); CastPetAuras on revive",
                    "owner_death": "Player::setDeathState -> RemovePet(NOT_IN_SLOT, returnreagent) (Player.cpp:1157)", "owner_logout": "RemovePet(PET_SAVE_AS_CURRENT) (WorldSession.cpp:619)",
                    "level": "dynamic: SynchronizeLevelWithOwner", "reset": "re-summon reloads character_pet state (LoadPetFromDB) -- Track C input"})
        return out
    if cat == "hunter-pet":
        out.update({"creation": "CREATE_HUNTER_PET", "duration_source": "none (permanent Pet)", "tempsummon_type": None, "timer": "none",
                    "slot": "SUMMON_SLOT_PET via SetPetGUID; active stable slot 0-4 (PetSaveMode)", "replacement": ["one-pet-per-player", "guardian-pet"],
                    "death": "hunter pet corpse persists until m_corpseRemoveTime, never removed for death (Pet.cpp:631-633); revive restores", "owner_death": "RemovePet(NOT_IN_SLOT) (Player.cpp:1157)",
                    "owner_logout": "RemovePet(PET_SAVE_AS_CURRENT)", "level": "dynamic: SynchronizeLevelWithOwner", "reset": "character_pet row (PetNumber, CreatedBySpellId = tame spell, spec, action bar) -- Track C input"})
        return out
    if cat in ("charmed", "possessed", "charmed-convert", "possessed-own-pet"):
        out.update({"creation": "CHARM", "duration_source": "the control aura's duration (SpellDuration + owner mods); removal -> RemoveCharmedBy",
                    "tempsummon_type": None, "timer": "aura timer", "slot": "none (Charm GUID)", "replacement": ["a player can hold one charm (ASSERT Unit.cpp:6448); StopCastingCharm before a new one (11837)"],
                    "death": "charm auras removed on death (RemoveAllAurasOnDeath) / RemoveAllControlled", "owner_death": "Unit::setDeathState -> RemoveAllControlled -> RemoveCharmAuras (Unit.cpp:6622-6623)",
                    "owner_logout": "Player::RemoveFromWorld -> StopCastingCharm (Player.cpp:1538)", "level": "unchanged", "reset": "faction restored, CharmInfo deleted unless guardian (Unit.cpp:12059-12060)"})
        return out
    if cat in ("lifecycle-op", "no-consumer", "unresolved", "script-summon"):
        out.update({"creation": "none", "notes": br.get("notes", [])})
        return out
    # temp summons
    ts = br.get("tempsummon_type")
    p = r.summon_properties or {}
    slot = p.get("slot", 0)
    slot_rule = "any-totem-slot" if slot == -1 else ("slot" if slot else "none (Slot 0: no m_SummonSlot bookkeeping)")
    dur_src = ("SpellInfo::CalcDuration(m_originalCaster) in SummonGuardian (SpellEffects.cpp:5024)" if br.get("summon_path") == "SummonGuardian"
               else "SpellInfo::CalcDuration(caster) in EffectSummonType (SpellEffects.cpp:1902)")
    if cls == "Totem":
        timer = "Totem::m_duration (Totem.cpp:87) counted in Totem::Update regardless of TempSummonType; owner death -> UnSummon"
        if r.duration_ms is None or r.duration_ms <= 0:
            timer += "; duration <= 0 -> removed on the first Totem::Update (Totem.cpp:42-46)"
    elif cls == "Pet":
        timer = "n/a"
    else:
        timer = TEMPSUMMON_TYPE_TIMER.get(ts or "", "?")
    repl = [slot_rule] if slot_rule != "none (Slot 0: no m_SummonSlot bookkeeping)" else []
    if cls == "Totem":
        repl.append("totem-duration")
    if p.get("control") == 2:
        repl.append("guardian-pet")
    if br.get("controllable_guardian"):
        repl.append("minion-succession")
    if r.summon_properties_id in NUMSUMMONS_FROM_EFFECT_VALUE:
        repl.append("num-summons")
    out.update({"creation": "CREATE_TEMPSUMMON", "summon_path": br.get("summon_path"), "duration_source": dur_src, "tempsummon_type": ts, "timer": timer,
                "slot": f"{p.get('slot_name', 'n/a')} ({slot_rule})", "replacement": repl or ["none"],
                "death": ("Totem::Update !IsAlive -> UnSummon" if cls == "Totem" else "TempSummon::Update DEAD -> UnSummon; CORPSE per TempSummonType; Minion::setDeathState succession for guardian pets"),
                "owner_death": ("UnsummonAllTotems (slotted) + RemoveAllControlled (owned) in Unit::setDeathState (Unit.cpp:9177-9178)" if cls in ("Guardian", "Minion", "Totem", "Puppet")
                                else "not owned: survives the summoner's death unless slotted (UnsummonAllTotems walks m_SummonSlot) -- DespawnOnSummonerDeath flag is NYI"),
                "owner_logout": ("Player::RemoveFromWorld -> Unit::RemoveFromWorld: RemoveCharmAuras, UnsummonAllTotems, RemoveAllControlled (Unit.cpp:10273-10285) remove every owned summon with the owner" if cls in ("Guardian", "Minion", "Totem", "Puppet") else "DespawnOnSummonerLogout flag is NYI: an unowned summon persists until its timer (only slotted ones fall to UnsummonAllTotems, Unit.cpp:10284)"),
                "level": "snapshot at TempSummon::InitStats (summoner level clamp) unless UseCreatureLevel" if not p.get("flags", {}).get("names") or "UseCreatureLevel" not in p["flags"]["names"] else "creature template level (UseCreatureLevel)",
                "reset": "no reset: a new cast creates a new GUID; slot/pet rules remove the previous unit; scripts may RefreshTimer/ModifyTimer",
                "num_summons": br.get("num_summons"), "in_combat_timer_reset": ts == "TEMPSUMMON_TIMED_DESPAWN_OUT_OF_COMBAT"})
    return out


TEMPSUMMON_TYPE_TIMER = {
    "TEMPSUMMON_TIMED_DESPAWN": "m_timer counts down every update; UnSummon at 0 (TemporarySummon.cpp:89-99)",
    "TEMPSUMMON_TIMED_DESPAWN_OUT_OF_COMBAT": "counts down only out of combat; in combat m_timer is reset to m_lifetime (TemporarySummon.cpp:100-116)",
    "TEMPSUMMON_DEAD_DESPAWN": "no timer; UnSummon when m_deathState == DEAD (TemporarySummon.cpp:77-81, 86-88)",
    "TEMPSUMMON_MANUAL_DESPAWN": "no timer; script/owner UnSummon only (TemporarySummon.cpp:86-88)",
    "TEMPSUMMON_CORPSE_DESPAWN": "UnSummon when CORPSE (132-142)", "TEMPSUMMON_CORPSE_TIMED_DESPAWN": "timer runs while CORPSE (118-131)",
    "TEMPSUMMON_TIMED_OR_CORPSE_DESPAWN": "CORPSE or out-of-combat timer (143-164)", "TEMPSUMMON_TIMED_OR_DEAD_DESPAWN": "out-of-combat+alive timer (165-180)",
}


def lifecycle_corpus(ctx: Context) -> dict[str, Any]:
    recs = ctx.records(ctx.scope, "default")
    return {
        "provenance": provenance("python3 controlled_units.py lifecycle --write"),
        "phases": {"CREATE_TEMPSUMMON": CREATE_TEMPSUMMON, "CREATE_SUMMON_PET": CREATE_SUMMON_PET, "CREATE_HUNTER_PET": CREATE_HUNTER_PET,
                   "CHARM": CHARM, "UPDATE_DESPAWN": UPDATE_DESPAWN, "DEATH": DEATH, "OWNER_EVENTS": OWNER_EVENTS},
        "replacement_rules": REPLACEMENT_RULES,
        "numeric": NUMERIC,
        "tempsummon_type_timers": TEMPSUMMON_TYPE_TIMER,
        "player_scope": [policy(r) for r in recs],
    }


def register(sub) -> None:
    p = sub.add_parser("lifecycle", help="lifecycle policy; --spell for one summoning spell")
    p.add_argument("--spell", type=int, default=None)
    p.add_argument("--write", action="store_true", help="write controlled-unit-corpora/lifecycle.json")
    p.set_defaults(func=cmd_lifecycle)


def cmd_lifecycle(args) -> int:
    ctx = Context()
    if args.spell is not None:
        info = ctx.b.catalog.get(args.spell)
        if info is None:
            print(json.dumps({"spell": args.spell, "error": "spell not in snapshot"}))
            return 1
        scope = ctx.scope if args.spell in ctx.scope.reach else ctx.extended
        scope_name = "default" if args.spell in ctx.scope.reach else ("class-skill" if args.spell in scope.reach else "out-of-scope")
        recs = [ctx.record_for(args.spell, e, scope, scope_name) for e in info.effects if Context.is_population_effect(e)]
        if not recs:
            print(json.dumps({"spell": args.spell, "name": ctx.b.name(args.spell), "scope": scope_name, "error": "no unit-creating or control effect on this spell"}, indent=1))
            return 1
        views = []
        for r in recs:
            v = policy(r)
            v["phases"] = {"CREATE_TEMPSUMMON": CREATE_TEMPSUMMON, "CREATE_SUMMON_PET": CREATE_SUMMON_PET, "CREATE_HUNTER_PET": CREATE_HUNTER_PET, "CHARM": CHARM}.get(v.get("creation", ""), [])
            views.append(v)
        print(json.dumps({"spell": args.spell, "name": ctx.b.name(args.spell), "scope": scope_name, "policy": views}, indent=1))
        return 0
    corpus = lifecycle_corpus(ctx)
    if args.write:
        CORPORA.mkdir(parents=True, exist_ok=True)
        path = CORPORA / "lifecycle.json"
        path.write_text(json.dumps(corpus, indent=1) + "\n", encoding="utf-8")
        print(path)
    else:
        print(json.dumps({"phases": {k: len(v) for k, v in corpus["phases"].items()}, "replacement_rules": [r["rule"] for r in corpus["replacement_rules"]], "player_scope": len(corpus["player_scope"])}, indent=1))
    return 0
