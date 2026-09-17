"""Ownership and attribution: which identity each Trinity consumer reads.

Identities (Trinity's names, brief section 5):

* owner        = ``UnitData::SummonedBy``  (``Unit::GetOwnerGUID``, Unit.h:1190)
* creator      = ``UnitData::CreatedBy``   (``Unit::GetCreatorGUID``, Unit.h:1192)
* summoner     = ``TempSummon::m_summonerGUID`` (TemporarySummon.cpp:41-42)
* demon creator= ``UnitData::DemonCreator`` (Unit.h:1202)
* charmer      = ``UnitData::CharmedBy``   (Unit.h:1207) + ``CharmInfo``
* original caster = ``Spell::m_originalCasterGUID`` (Spell.cpp:510-521)
* aura caster  = ``Aura::m_casterGuid``    (SpellAuras.cpp:477)

The consumer table below records, per function, which identity it returns and
whether the controlled unit *collapses* into its controller (the consumer
answers with the owner/charmer/player) or *stays distinct* (the unit itself).
``topology(record)`` applies the table to one population record.
"""

from __future__ import annotations

import json
from typing import Any

from . import CORPORA
from .population import Context, Record, provenance

TC = "src/server/game"

#: function, coordinate, identity returned, collapse|distinct|chain, note
CONSUMERS: list[dict[str, Any]] = [
    # -- accessors ---------------------------------------------------------
    {"function": "Unit::GetOwnerGUID", "coordinate": f"{TC}/Entities/Unit/Unit.h:1190", "returns": "UnitData::SummonedBy", "kind": "identity", "evidence_class": "trinity-consumer"},
    {"function": "Unit::GetCreatorGUID", "coordinate": f"{TC}/Entities/Unit/Unit.h:1192", "returns": "UnitData::CreatedBy", "kind": "identity", "evidence_class": "trinity-consumer"},
    {"function": "Unit::GetMinionGUID / GetPetGUID / GetCritterGUID", "coordinate": f"{TC}/Entities/Unit/Unit.h:1194-1198", "returns": "UnitData::Summon / m_SummonSlot[SUMMON_SLOT_PET] / UnitData::Critter", "kind": "identity", "evidence_class": "trinity-consumer"},
    {"function": "Unit::GetDemonCreatorGUID", "coordinate": f"{TC}/Entities/Unit/Unit.h:1202", "returns": "UnitData::DemonCreator (set for WILD summons by players, SpellEffects.cpp:2025-2026)", "kind": "identity", "evidence_class": "trinity-consumer"},
    {"function": "Unit::GetCharmerGUID / GetCharmer", "coordinate": f"{TC}/Entities/Unit/Unit.h:1207-1208", "returns": "UnitData::CharmedBy / m_charmer", "kind": "identity", "evidence_class": "trinity-consumer"},
    {"function": "Unit::GetCharmerOrOwnerGUID", "coordinate": f"{TC}/Entities/Unit/Unit.h:1215", "returns": "charmer if charmed else owner", "kind": "collapse", "evidence_class": "trinity-consumer"},
    {"function": "Unit::GetCharmerOrOwner", "coordinate": f"{TC}/Entities/Unit/Unit.h:1220", "returns": "charmer if charmed else owner (Unit*)", "kind": "collapse", "evidence_class": "trinity-consumer"},
    {"function": "WorldObject::GetOwner", "coordinate": f"{TC}/Entities/Object/Object.cpp:1605-1608", "returns": "ObjectAccessor::GetUnit(GetOwnerGUID())", "kind": "identity", "evidence_class": "trinity-consumer"},
    {"function": "WorldObject::GetCharmerOrOwnerOrSelf", "coordinate": f"{TC}/Entities/Object/Object.cpp:1620-1626", "returns": "charmer/owner, else the unit itself", "kind": "collapse", "evidence_class": "trinity-consumer"},
    {"function": "WorldObject::GetCharmerOrOwnerPlayerOrPlayerItself", "coordinate": f"{TC}/Entities/Object/Object.cpp:1628-1635", "returns": "the player charmer/owner (one level) else ToPlayer()", "kind": "collapse", "note": "one level only: a guardian of a pet resolves to nullptr", "evidence_class": "trinity-consumer"},
    {"function": "WorldObject::GetAffectingPlayer", "coordinate": f"{TC}/Entities/Object/Object.cpp:1637-1646", "returns": "charmer/owner -> its GetCharmerOrOwnerPlayerOrPlayerItself (two levels)", "kind": "collapse", "evidence_class": "trinity-consumer"},
    {"function": "Unit::GetControllingPlayer", "coordinate": f"{TC}/Entities/Unit/Unit.cpp:6201-6212", "returns": "recursive charmer/owner chain -> player", "kind": "collapse", "evidence_class": "trinity-consumer"},
    {"function": "WorldObject::GetSpellModOwner", "coordinate": f"{TC}/Entities/Object/Object.cpp:1648-1670", "returns": "player itself; for creatures only IsPet() || IsTotem() -> owner->ToPlayer(); Guardian/Minion/TempSummon -> nullptr", "kind": "collapse-pet-totem-only",
     "note": "owner spell mods (SPELLMOD_*, incl. Duration used by SpellInfo::CalcDuration SpellInfo.cpp:3975-3984) apply to Pet and Totem casts only; temporary guardians get none", "evidence_class": "trinity-consumer"},
    {"function": "WorldObject::GetCharmerOrOwnerOrOwnGUID", "coordinate": f"{TC}/Entities/Object/Object.cpp:1597-1603", "returns": "REGRESSION at 4ba5e27055c (2026-01-02): returns own GUID when a charmer/owner exists, Empty otherwise (pre-rework Unit.cpp: charmer/owner else own GUID)", "kind": "collapse-inverted",
     "note": "consumers: Unit::IsCharmedOwnedByPlayerOrPlayer (Unit.h:1216) -> Spell.cpp:2774 (PvP enabling), Creature.cpp:1775/1802 (sparring), Unit.cpp:4444 (RemoveAurasOnEvade), GameObject.cpp:3170; false for uncharmed players and player-owned pets in the pinned build", "evidence_class": "trinity-consumer"},
    # -- spell cast identity ------------------------------------------------
    {"function": "Spell::Spell (m_caster)", "coordinate": f"{TC}/Spells/Spell.cpp:475", "returns": "the casting unit, or its charmer/owner when SPELL_ATTR6_ORIGINATE_FROM_CONTROLLER (SharedDefines.h:677)", "kind": "distinct (attr-redirect)", "evidence_class": "trinity-consumer"},
    {"function": "Spell::Spell (m_originalCasterGUID)", "coordinate": f"{TC}/Spells/Spell.cpp:510-521", "returns": "explicit originalCasterGUID else m_caster GUID; m_originalCaster resolved only if in world", "kind": "distinct", "evidence_class": "trinity-consumer"},
    {"function": "Spell::GetUnitCasterForEffectHandlers", "coordinate": f"{TC}/Spells/Spell.cpp:8353-8356", "returns": "m_originalCaster if set else m_caster->ToUnit()", "kind": "distinct", "note": "the summoner passed to Map::SummonCreature by EffectSummonType/SummonGuardian", "evidence_class": "trinity-consumer"},
    {"function": "WorldObject::CastSpell -> new Spell(..., args.OriginalCaster)", "coordinate": f"{TC}/Entities/Object/Object.cpp:2239", "returns": "CastSpellExtraArgs::OriginalCaster (SpellDefines.h:499) propagates", "kind": "distinct", "evidence_class": "trinity-consumer"},
    {"function": "AuraEffect::HandleProcTriggerSpellAuraProc", "coordinate": f"{TC}/Spells/Auras/SpellAuraEffects.cpp:6155-6172", "returns": "triggerCaster = aurApp->GetTarget() (the aura holder), no OriginalCaster set -> original caster = holder", "kind": "distinct", "note": "a proc aura held by the pet triggers with the pet as caster; held by the owner -> owner", "evidence_class": "trinity-consumer"},
    {"function": "AuraEffect::HandleAuraDummy (linked casts)", "coordinate": f"{TC}/Spells/Auras/SpellAuraEffects.cpp:5013-5038", "returns": "caster = Aura::GetCaster(), args.OriginalCaster = GetCasterGUID()", "kind": "distinct", "evidence_class": "trinity-consumer"},
    {"function": "Aura::Aura (m_casterGuid)", "coordinate": f"{TC}/Spells/Auras/SpellAuras.cpp:476-478", "returns": "AuraCreateInfo.CasterGUID = the casting unit (pet-cast auras are owned by the pet GUID)", "kind": "distinct", "evidence_class": "trinity-consumer"},
    {"function": "Aura::GetCaster", "coordinate": f"{TC}/Spells/Auras/SpellAuras.cpp:557-563", "returns": "unit for m_casterGuid (nullptr once the pet is gone)", "kind": "distinct", "evidence_class": "trinity-consumer"},
    # -- damage / log / procs ---------------------------------------------
    {"function": "SpellNonMeleeDamage::SpellNonMeleeDamage", "coordinate": f"{TC}/Entities/Unit/Unit.cpp:303-304", "returns": "attacker = the casting unit", "kind": "distinct", "evidence_class": "trinity-consumer"},
    {"function": "Unit::SendSpellNonMeleeDamageLog", "coordinate": f"{TC}/Entities/Unit/Unit.cpp:5539-5543", "returns": "packet.CasterGUID = log->attacker (the unit)", "kind": "distinct", "evidence_class": "trinity-consumer"},
    {"function": "Unit::DealDamage", "coordinate": f"{TC}/Entities/Unit/Unit.cpp:820-862", "returns": "attacker = the unit; victim's m_Controlled get OwnerAttackedBy (856-862)", "kind": "distinct", "evidence_class": "trinity-consumer"},
    {"function": "Unit::ProcSkillsAndAuras", "coordinate": f"{TC}/Entities/Unit/Unit.cpp:5569-5599", "returns": "actor = the unit that acted; actor->TriggerAurasProcOnEvent on the actor's own auras (5597-5598)", "kind": "distinct", "note": "owner auras never see a pet's DONE events through this path", "evidence_class": "trinity-consumer"},
    {"function": "Spell::TargetInfo::DoDamageAndTriggers (spell hit procs)", "coordinate": f"{TC}/Spells/Spell.cpp:2842; {TC}/Spells/Spell.cpp:2980", "returns": "caster = m_originalCaster ?: m_caster -> ProcSkillsAndAuras actor; damage/healing are also computed from that unit's data (comment :2840-2841)", "kind": "distinct (original-caster)",
     "note": "a script cast from a pet that passes OriginalCaster = owner makes the OWNER the proc actor and damage source; a plain pet cast keeps the pet", "evidence_class": "trinity-consumer"},
    {"function": "Spell::cast / Spell::finish (cast-phase procs)", "coordinate": f"{TC}/Spells/Spell.cpp:3915; {TC}/Spells/Spell.cpp:4223", "returns": "ProcSkillsAndAuras(m_originalCaster, ...) for PROC_SPELL_PHASE_CAST / FINISH", "kind": "distinct (original-caster)", "evidence_class": "trinity-consumer"},
    {"function": "Spell::CheckCast (SUMMON_PET replacement)", "coordinate": f"{TC}/Spells/Spell.cpp:6461-6468", "returns": "old Pet casts PET_SUMMONING_DISORIENTATION (32752) on itself with OriginalCaster = the pet; non-player casters need SPELL_ATTR1_DISMISS_PET_FIRST", "kind": "distinct", "evidence_class": "trinity-consumer"},
    {"function": "Unit::Kill (KILL proc)", "coordinate": f"{TC}/Entities/Unit/Unit.cpp:11382-11392", "returns": "PROC_FLAG_KILL to attacker; additionally to attacker->GetOwner() only if attacker IsPet() || IsTotem()", "kind": "collapse-pet-totem-only", "note": "guardians/minions do not forward KILL procs to the owner", "evidence_class": "trinity-consumer"},
    {"function": "Unit::Kill (reward player)", "coordinate": f"{TC}/Entities/Unit/Unit.cpp:11251-11254", "returns": "player = attacker->GetCharmerOrOwnerPlayerOrPlayerItself()", "kind": "collapse", "evidence_class": "trinity-consumer"},
    {"function": "Unit::Kill (killing-blow achievements)", "coordinate": f"{TC}/Entities/Unit/Unit.cpp:11405", "returns": "attacker->GetCharmerOrOwnerPlayerOrPlayerItself()", "kind": "collapse", "evidence_class": "trinity-consumer"},
    {"function": "Unit::Kill (pet KilledUnit AI)", "coordinate": f"{TC}/Entities/Unit/Unit.cpp:11414-11425", "returns": "each tapper's Pet gets AI()->KilledUnit(victim)", "kind": "owner->pet", "evidence_class": "trinity-consumer"},
    {"function": "Creature::SetTappedBy", "coordinate": f"{TC}/Entities/Creature/Creature.cpp:1370-1389", "returns": "tapper = unit->GetCharmerOrOwnerPlayerOrPlayerItself()", "kind": "collapse", "evidence_class": "trinity-consumer"},
    {"function": "KillRewarder::KillRewarder", "coordinate": f"{TC}/Entities/Player/KillRewarder.cpp:75-89", "returns": "killers = tap list players; victim owned by player -> PvP branch (:85)", "kind": "collapse", "evidence_class": "trinity-consumer"},
    # -- threat / combat -------------------------------------------------
    {"function": "ThreatManager::CanHaveThreatList", "coordinate": f"{TC}/Combat/ThreatManager.cpp:172-187", "returns": "false for pets, totems, triggers and MINION|GUARDIAN summoned by a player", "kind": "distinct", "note": "player-controlled units have no threat list; they still appear as their own entry on enemies' lists", "evidence_class": "trinity-consumer"},
    {"function": "ThreatManager::AddThreat", "coordinate": f"{TC}/Combat/ThreatManager.cpp:382-471", "returns": "threat entry keyed by target->GetGUID() (the attacking unit); no owner redirection", "kind": "distinct", "evidence_class": "trinity-consumer"},
    {"function": "CombatManager::CanBeginCombat", "coordinate": f"{TC}/Combat/CombatManager.cpp:52-56", "returns": "GetCharmerOrOwnerPlayerOrPlayerItself for the GM check", "kind": "collapse", "evidence_class": "trinity-consumer"},
    {"function": "CombatManager (EndCombat)", "coordinate": f"{TC}/Combat/CombatManager.cpp:421-422", "returns": "GetCharmerOrOwner()->UpdatePetCombatState()", "kind": "pet->owner", "note": "Unit::UpdatePetCombatState (Unit.cpp:9281-9300): owner UNIT_FLAG_PET_IN_COMBAT from any m_Controlled in combat", "evidence_class": "trinity-consumer"},
    # -- state copied from the controller ---------------------------------
    {"function": "Unit::SetMinion (apply)", "coordinate": f"{TC}/Entities/Unit/Unit.cpp:6264-6336", "returns": "OwnerGUID=owner (6264), m_Controlled (6266), m_ControlledByPlayer + UNIT_FLAG_PLAYER_CONTROLLED if owner is a player (6268-6272), PvP flags copied (6325), speed rates copied for Pets (6328-6330), cooldown-started-on-event (6332-6336)", "kind": "snapshot", "evidence_class": "trinity-consumer"},
    {"function": "Unit::SetCharm (apply)", "coordinate": f"{TC}/Entities/Unit/Unit.cpp:6442-6470", "returns": "Charm/CharmedBy fields, m_ControlledByPlayer + UNIT_FLAG_PLAYER_CONTROLLED if charmer is a player (6446-6458), PvP flags copied (6461)", "kind": "snapshot", "evidence_class": "trinity-consumer"},
    {"function": "Minion::InitStats", "coordinate": f"{TC}/Entities/Creature/TemporarySummon.cpp:456-466", "returns": "REACT_PASSIVE, CreatorGUID = owner, faction = owner faction (overrides SummonProperties faction/flag), owner->SetMinion", "kind": "snapshot", "evidence_class": "trinity-consumer"},
    {"function": "TempSummon::InitStats (level/faction)", "coordinate": f"{TC}/Entities/Creature/TemporarySummon.cpp:241-255", "returns": "level = clamp(summoner level, ScalingLevelMin/Max+Delta) unless UseCreatureLevel; faction = SummonProperties.Faction or summoner faction with UseSummonerFaction", "kind": "snapshot", "evidence_class": "trinity-consumer"},
    {"function": "Unit::RestoreFaction", "coordinate": f"{TC}/Entities/Unit/Unit.cpp:12074-12098", "returns": "MINION -> owner faction re-read (dynamic on charm end)", "kind": "dynamic", "evidence_class": "trinity-consumer"},
    {"function": "Pet::SynchronizeLevelWithOwner", "coordinate": f"{TC}/Entities/Pet/Pet.cpp:1784-1798", "returns": "SUMMON_PET/HUNTER_PET level = owner level; called on Player::GiveLevel (Player.cpp:2259-2261) and Player::InitStatsForLevel (2497)", "kind": "dynamic-event", "evidence_class": "trinity-consumer"},
    {"function": "Spell::EffectSummonType (caster)", "coordinate": f"{TC}/Spells/SpellEffects.cpp:1883-1885", "returns": "caster = m_originalCaster if set else m_caster (owner/creator source for the default arm)", "kind": "distinct", "evidence_class": "trinity-consumer"},
    {"function": "Spell::SummonGuardian (totem caster)", "coordinate": f"{TC}/Spells/SpellEffects.cpp:5019-5020", "returns": "a totem caster is replaced by the totem's owner", "kind": "collapse", "evidence_class": "trinity-consumer"},
    {"function": "Spell::EffectSummonPet (owner)", "coordinate": f"{TC}/Spells/SpellEffects.cpp:2661-2667", "returns": "owner = caster player, or GetCharmerOrOwnerPlayerOrPlayerItself of a totem caster", "kind": "collapse", "evidence_class": "trinity-consumer"},
    # -- pet auras (spell_pet_auras) ---------------------------------------
    {"function": "SpellMgr::LoadSpellPetAuras / Player::AddPetAura / Pet::CastPetAuras", "coordinate": f"{TC}/Spells/SpellMgr.cpp:1965-2012; {TC}/Entities/Player/Player.cpp:22179-22191; {TC}/Entities/Pet/Pet.cpp:1730-1762",
     "returns": "owner dummy aura -> pet self-cast aura (aura caster = the pet); only for IsPermanentPetFor (Pet.cpp:1657-1678); pinned overlay has 2 rows (20895, 28757), neither in player scope", "kind": "owner->pet", "evidence_class": "world-db-fact"},
]


def charmer_or_owner_or_own_guid(own: str, charmer_or_owner: str | None, pinned: bool = True) -> str | None:
    """Mirror of ``WorldObject::GetCharmerOrOwnerOrOwnGUID`` (Object.cpp:1597-1603).

    ``pinned=True`` is the code at 7f3d43b7 (``if (!guid.IsEmpty()) guid = GetGUID();``, lines 1600-1602
    from 4ba5e27055c); ``pinned=False`` is the pre-4ba5e27055c body (``if (!guid.IsEmpty()) return guid;
    return GetGUID();``).  ``None`` models ``ObjectGuid::Empty``."""
    if pinned:
        return own if charmer_or_owner else None
    return charmer_or_owner if charmer_or_owner else own


def is_charmed_owned_by_player_or_player(own: str, charmer_or_owner: str | None, pinned: bool = True) -> bool:
    """``Unit::IsCharmedOwnedByPlayerOrPlayer`` (Unit.h:1216); GUID strings starting with ``Player-`` are players."""
    g = charmer_or_owner_or_own_guid(own, charmer_or_owner, pinned)
    return bool(g) and g.startswith("Player-")


def topology(r: Record) -> dict[str, Any]:
    """Identity topology for one population record, from the branch it takes."""
    cat, br = r.category, r.branch
    cls = br.get("cxx_class", "-")
    is_pet = cls == "Pet"
    is_totem = cls == "Totem"
    in_controlled = cls in ("Pet", "Guardian", "Minion", "Totem", "Puppet")
    if cat in ("charmed", "possessed", "charmed-convert", "possessed-own-pet"):
        return {
            "spell": r.spell_id, "name": r.spell_name, "effect_index": r.effect_index, "category": cat, "unit": "the aura target (existing unit)",
            "charmer": br.get("charmer"), "charm_type": br.get("charm_type"),
            "owner": "unchanged (UnitData::SummonedBy of the target)", "creator": "unchanged",
            "in_charmer_m_controlled": "yes (Unit::SetCharm inserts into m_Controlled, Unit.cpp:6442-6470)",
            "player_controlled_flag": "set when the charmer is a player (Unit.cpp:6453-6455)",
            "pvp_flags": "copied from charmer (Unit.cpp:6461)", "faction": "charmer faction; old faction restored on removal (Unit.cpp:11860-11861, 11987-11993)",
            "caster_of_own_casts": "the charmed unit (Spell::m_caster), redirected to the charmer for SPELL_ATTR6_ORIGINATE_FROM_CONTROLLER spells (Spell.cpp:475)",
            "spell_mod_owner": "none unless the charmed unit IsPet/IsTotem (Object.cpp:1653-1661)",
            "proc_actor_for_own_casts": "the charmed unit (Unit::ProcSkillsAndAuras actor)",
            "kill_credit": "collapses to the charmer player (GetCharmerOrOwnerPlayerOrPlayerItself, Unit.cpp:11251-11254)",
            "threat_list": "CanHaveThreatList: unchanged for a charmed creature (not pet/totem, not a player summon)",
            "coordinates": br.get("coordinates", []), "evidence_class": br.get("evidence_class", "trinity-consumer"),
        }
    if cat in ("lifecycle-op", "no-consumer", "unresolved", "script-summon"):
        return {"spell": r.spell_id, "name": r.spell_name, "effect_index": r.effect_index, "category": cat, "unit": "none created by this row",
                "notes": br.get("notes", []), "coordinates": br.get("coordinates", []), "evidence_class": br.get("evidence_class", "unresolved")}
    return {
        "spell": r.spell_id, "name": r.spell_name, "effect_index": r.effect_index, "category": cat, "cxx_class": cls, "unit_mask": br.get("unit_mask"),
        "summoner_guid": "m_summonerGUID = summoner passed to Map::SummonCreature = GetUnitCasterForEffectHandlers() (original caster if set) (TemporarySummon.cpp:41-42; Spell.cpp:8353-8356)" if not is_pet else "n/a (Pet is not created through Map::SummonCreature)",
        "owner": br.get("owner_guid"), "creator": br.get("creator_guid"), "demon_creator": br.get("demon_creator", "-"),
        "caster_of_summon_spell": "m_caster (player) ; EffectSummonType prefers m_originalCaster for the default arm's owner/creator (SpellEffects.cpp:1883-1885)",
        "in_owner_m_controlled": "yes (Minion::InitStats -> Unit::SetMinion, Unit.cpp:6266)" if in_controlled else "no (plain TempSummon: never inserted in m_Controlled; ALLY default arm only sets SummonedBy)",
        "player_controlled_flag": "UNIT_FLAG_PLAYER_CONTROLLED + m_ControlledByPlayer at SetMinion when the owner is a player (Unit.cpp:6268-6272)" if in_controlled else "only via IsTrigger()&&m_spells[0] (TemporarySummon.cpp:207-208)",
        "pet_guid_slot": "SetPetGUID (Unit.cpp:6274-6295) -- IsGuardianPet: IsPet() or Control==SUMMON_CATEGORY_PET" if (is_pet or (r.summon_properties or {}).get("control") == 2) else "MinionGUID only if UNIT_MASK_CONTROLABLE_GUARDIAN and empty (Unit.cpp:6297-6301)" if br.get("controllable_guardian") else "none",
        "pvp_flags": "copied from owner at SetMinion (Unit.cpp:6325)" if in_controlled else "not copied",
        "faction": ("owner faction (Minion::InitStats, TemporarySummon.cpp:463) after SummonProperties/UseSummonerFaction (TempSummon::InitStats :250-255)" if in_controlled
                    else "SummonProperties.Faction, or summoner faction with UseSummonerFaction (TemporarySummon.cpp:250-255)"),
        "level": ("owner level via Pet::SynchronizeLevelWithOwner (dynamic on owner level-up)" if is_pet
                  else "clamp(summoner level, ScalingLevelMin/Max+Delta) at TempSummon::InitStats unless UseCreatureLevel (TemporarySummon.cpp:241-247) -- snapshot"),
        "caster_of_own_casts": "the unit itself (Spell::m_caster), unless SPELL_ATTR6_ORIGINATE_FROM_CONTROLLER redirects to charmer/owner (Spell.cpp:475)",
        "original_caster_of_own_casts": "the unit (default m_originalCasterGUID = m_caster GUID, Spell.cpp:510-513); scripts may pass the owner explicitly",
        "aura_caster_of_own_auras": "the unit GUID (Aura::m_casterGuid, SpellAuras.cpp:477) -- distinct from the owner",
        "spell_mod_owner": "owner player (IsPet/IsTotem, Object.cpp:1653-1661)" if (is_pet or is_totem) else "none: GetSpellModOwner returns nullptr for Guardian/Minion/TempSummon (Object.cpp:1653-1661)",
        "proc_actor_for_own_casts": "the unit (Unit::ProcSkillsAndAuras actor; its own auras proc, Unit.cpp:5591-5598)",
        "kill_proc": "PROC_FLAG_KILL on the unit and, because IsPet/IsTotem, also on GetOwner() (Unit.cpp:11383-11388)" if (is_pet or is_totem) else "PROC_FLAG_KILL on the unit only; not forwarded to the owner (Unit.cpp:11383-11392)",
        "kill_credit_loot": "collapses to GetCharmerOrOwnerPlayerOrPlayerItself (Unit.cpp:11251-11254; Creature.cpp:1389)" if (in_controlled or br.get("owner_guid", "").startswith("caster")) else "no owner -> no player credit (GetCharmerOrOwnerGUID empty)",
        "combat_log_source": "the unit (SpellNonMeleeDamage.attacker, Unit.cpp:303-304 / 5543)",
        "threat_list": ("none (pet/totem, ThreatManager.cpp:180)" if (is_pet or is_totem) else
                        "none when summoned by a player (MINION|GUARDIAN, ThreatManager.cpp:184-187)" if cls in ("Guardian", "Minion", "Puppet") else
                        "own threat list (plain TempSummon)"),
        "threat_on_enemies": "the unit is its own entry on enemies' threat lists (ThreatManager::AddThreat keys target GUID, :445-465); no owner redirection",
        "owner_combat_state": "owner UNIT_FLAG_PET_IN_COMBAT follows m_Controlled (Unit::UpdatePetCombatState via CombatManager.cpp:421-422)" if in_controlled else "not propagated",
        "coordinates": br.get("coordinates", []), "evidence_class": br.get("evidence_class", "trinity-consumer"),
    }


def per_category_topology() -> dict[str, Any]:
    """The topology as a function of category alone (what the census can say without a spell)."""
    from .vocabulary import SummonProps, summon_branch
    samples = {
        "controllable-guardian": SummonProps(0, 2, 0, 2, -1, 0, 0), "guardian": SummonProps(0, 1, 0, 2, 0, 0, 0),
        "totem": SummonProps(0, 1, 0, 4, -1, 0, 0), "companion-minion": SummonProps(0, 1, 0, 5, 5, 0, 0),
        "puppet": SummonProps(0, 3, 0, 0, 0, 0, 0), "vehicle": SummonProps(0, 5, 0, 0, 0, 0, 0), "ally-summon": SummonProps(0, 1, 0, 0, 0, 0, 0),
        "wild-summon": SummonProps(0, 0, 0, 0, 0, 0, 0), "lightwell-totem": SummonProps(0, 1, 0, 11, 0, 0, 0), "vehicle-summon": SummonProps(0, 1, 0, 9, 0, 0, 0),
    }
    out = {}
    for cat, p in samples.items():
        b = summon_branch(p, 1, 10000, 0)
        assert b.category == cat, (cat, b.category)
        rec = Record(0, cat, 0, 0, 28, "SPELL_EFFECT_SUMMON", 0, "", "Spell::EffectSummonType", cat, 1, 0, p.to_dict(), b.to_dict(), 10000, "synthetic", [], [], [], [], False, None, "not-applicable", None)
        out[cat] = {k: v for k, v in topology(rec).items() if k not in ("spell", "name", "effect_index")}
    return out


def ownership_corpus(ctx: Context) -> dict[str, Any]:
    recs = ctx.records(ctx.scope, "default")
    return {
        "provenance": provenance("python3 controlled_units.py ownership --write"),
        "identities": {"owner": "UnitData::SummonedBy (Unit.h:1190)", "creator": "UnitData::CreatedBy (Unit.h:1192)", "summoner": "TempSummon::m_summonerGUID (TemporarySummon.cpp:41-42)",
                       "demon_creator": "UnitData::DemonCreator (Unit.h:1202)", "charmer": "UnitData::CharmedBy (Unit.h:1207)",
                       "original_caster": "Spell::m_originalCasterGUID (Spell.cpp:510-521)", "aura_caster": "Aura::m_casterGuid (SpellAuras.cpp:477)"},
        "consumers": CONSUMERS,
        "per_category": per_category_topology(),
        "player_scope": [topology(r) for r in recs],
        "attribution_witnesses_pet_owner_forward_cast": pet_owner_hooks(ctx),
        "regression_GetCharmerOrOwnerOrOwnGUID": {
            "coordinate": "src/server/game/Entities/Object/Object.cpp:1597-1603", "introduced_by": "4ba5e27055c (2026-01-02, 'Reorder Object type casting functions', a refactor)",
            "truth_table": [{"unit": u, "charmer_or_owner": c, "pinned": is_charmed_owned_by_player_or_player(o, c, True), "pre_refactor": is_charmed_owned_by_player_or_player(o, c, False)}
                            for u, o, c in (("uncharmed player", "Player-1", None), ("player-owned pet", "Pet-1", "Player-1"), ("player-owned guardian", "Creature-1", "Player-1"),
                                            ("charmed player (by creature)", "Player-1", "Creature-2"), ("player charmed by player", "Player-1", "Player-2"), ("wild creature", "Creature-1", None))],
            "consumers": ["src/server/game/Spells/Spell.cpp:2774 (PvP enabling on friendly spell hit)", "src/server/game/Entities/Creature/Creature.cpp:1775 (CalculateDamageForSparring)",
                          "src/server/game/Entities/Creature/Creature.cpp:1802 (ShouldFakeDamageFrom)", "src/server/game/Entities/Unit/Unit.cpp:4444 (RemoveAurasOnEvade)",
                          "src/server/game/Entities/GameObject/GameObject.cpp:3170", "src/server/scripts/Spells/spell_generic.cpp:2295", "src/server/scripts/Kalimdor/Firelands/boss_alysrazor.cpp:205",
                          "src/server/scripts/Northrend/AzjolNerub/AzjolNerub/instance_azjol_nerub.cpp:89"],
            "evidence_class": "trinity-consumer", "note": "absent from pinned Trinity != absent from Retail: this is a consumer defect, not a Retail semantic; a Track A consumer must not copy it"},
    }


def pet_owner_hooks(ctx: Context) -> list[dict[str, Any]]:
    """Player-scope script hooks classified ``pet-owner-forward-cast`` or acting on ``m_Controlled`` (dummy families corpus)."""
    if not ctx.families_corpus:
        return []
    out = []
    for h in ctx.families_corpus.get("player_hooks", []):
        if h["family"] == "pet-owner-forward-cast" or "pet-owner" in h.get("actions", []):
            out.append({"spell": h["spell_id"], "name": ctx.b.name(h["spell_id"]), "script": h["script"], "handler": h["handler"],
                        "coordinate": f"{h['file']}:{h['line']}", "list": h["list"], "family": h["family"], "children": h.get("children", []),
                        "actions": h.get("actions", []), "in_default_scope": h["spell_id"] in ctx.scope.reach, "evidence_class": "structural-inference"})
    return out


def register(sub) -> None:
    p = sub.add_parser("ownership", help="ownership/attribution topology; --spell for one summoning spell")
    p.add_argument("--spell", type=int, default=None)
    p.add_argument("--write", action="store_true", help="write controlled-unit-corpora/ownership.json")
    p.set_defaults(func=cmd_ownership)


def cmd_ownership(args) -> int:
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
            print(json.dumps({"spell": args.spell, "name": ctx.b.name(args.spell), "scope": scope_name, "error": "no unit-creating or control effect on this spell (DIFFICULTY_NONE)",
                              "effects": [(e.index, e.effect, e.aura) for e in info.effects]}, indent=1))
            return 1
        print(json.dumps({"spell": args.spell, "name": ctx.b.name(args.spell), "scope": scope_name, "topology": [topology(r) for r in recs]}, indent=1))
        return 0
    corpus = ownership_corpus(ctx)
    if args.write:
        CORPORA.mkdir(parents=True, exist_ok=True)
        path = CORPORA / "ownership.json"
        path.write_text(json.dumps(corpus, indent=1) + "\n", encoding="utf-8")
        print(path)
    else:
        print(json.dumps({"consumers": len(corpus["consumers"]), "player_scope_records": len(corpus["player_scope"]), "per_category": list(corpus["per_category"])}, indent=1))
    return 0
