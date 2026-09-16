"""Proc event identity: what runtime facts produce each flag, and event builders.

Every ``Unit::ProcSkillsAndAuras`` call site in the checkout is listed in
:data:`EVENT_PRODUCERS` with the exact masks it passes.  The builders below
reproduce the *mask arithmetic* of those call sites from explicit combat facts
(hit outcome, damage/heal amounts, spell class, positivity).  They never decide
those facts -- a combat engine supplies them.

A proc event has two sides.  ``ProcSkillsAndAuras(actor, actionTarget,
typeMaskActor, typeMaskActionTarget, spellTypeMask, spellPhaseMask, hitMask,
spell, damageInfo, healInfo)`` builds one ``ProcEventInfo`` for the actor's
auras (with ``typeMaskActor``) and one for the action target's auras (with
``typeMaskActionTarget``); every other field is shared.  :class:`ProcEvent`
keeps both masks; :meth:`ProcEvent.side` yields the per-holder view that
:mod:`procs.eligibility` evaluates.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .enums import (
    ATTR0_IS_ABILITY,
    ATTR2_AUTO_REPEAT,
    ATTR3_TREAT_AS_PERIODIC,
    PROC_FLAG_2_CAST_SUCCESSFUL,
    PROC_FLAG_2_KNOCKBACK,
    PROC_FLAG_2_SUCCESSFUL_DISPEL,
    PROC_FLAG_2_TARGET_DIES,
    PROC_FLAG_CAST_ENDED,
    PROC_FLAG_DEAL_HARMFUL_ABILITY,
    PROC_FLAG_DEAL_HARMFUL_PERIODIC,
    PROC_FLAG_DEAL_HARMFUL_SPELL,
    PROC_FLAG_DEAL_HELPFUL_ABILITY,
    PROC_FLAG_DEAL_HELPFUL_PERIODIC,
    PROC_FLAG_DEAL_HELPFUL_SPELL,
    PROC_FLAG_DEAL_MELEE_ABILITY,
    PROC_FLAG_DEAL_MELEE_SWING,
    PROC_FLAG_DEAL_RANGED_ABILITY,
    PROC_FLAG_DEAL_RANGED_ATTACK,
    PROC_FLAG_DEATH,
    PROC_FLAG_ENCOUNTER_START,
    PROC_FLAG_ENTER_COMBAT,
    PROC_FLAG_HEARTBEAT,
    PROC_FLAG_JUMP,
    PROC_FLAG_KILL,
    PROC_FLAG_LOOTED,
    PROC_FLAG_MAIN_HAND_WEAPON_SWING,
    PROC_FLAG_NONE,
    PROC_FLAG_OFF_HAND_WEAPON_SWING,
    PROC_FLAG_TAKE_ANY_DAMAGE,
    PROC_FLAG_TAKE_HARMFUL_ABILITY,
    PROC_FLAG_TAKE_HARMFUL_PERIODIC,
    PROC_FLAG_TAKE_HARMFUL_SPELL,
    PROC_FLAG_TAKE_HELPFUL_ABILITY,
    PROC_FLAG_TAKE_HELPFUL_PERIODIC,
    PROC_FLAG_TAKE_HELPFUL_SPELL,
    PROC_FLAG_TAKE_MELEE_ABILITY,
    PROC_FLAG_TAKE_MELEE_SWING,
    PROC_FLAG_TAKE_RANGED_ABILITY,
    PROC_FLAG_TAKE_RANGED_ATTACK,
    PROC_HIT_ABSORB,
    PROC_HIT_BLOCK,
    PROC_HIT_CRITICAL,
    PROC_HIT_DEFLECT,
    PROC_HIT_DODGE,
    PROC_HIT_EVADE,
    PROC_HIT_FULL_BLOCK,
    PROC_HIT_FULL_RESIST,
    PROC_HIT_IMMUNE,
    PROC_HIT_MISS,
    PROC_HIT_NONE,
    PROC_HIT_NORMAL,
    PROC_HIT_PARRY,
    PROC_HIT_REFLECT,
    PROC_SPELL_PHASE_CAST,
    PROC_SPELL_PHASE_FINISH,
    PROC_SPELL_PHASE_HIT,
    PROC_SPELL_PHASE_NONE,
    PROC_SPELL_TYPE_DAMAGE,
    PROC_SPELL_TYPE_HEAL,
    PROC_SPELL_TYPE_MASK_ALL,
    PROC_SPELL_TYPE_NO_DMG_HEAL,
    PROC_SPELL_TYPE_NONE,
)

# SharedDefines.h SpellDmgClass
SPELL_DAMAGE_CLASS_NONE = 0
SPELL_DAMAGE_CLASS_MAGIC = 1
SPELL_DAMAGE_CLASS_MELEE = 2
SPELL_DAMAGE_CLASS_RANGED = 3

BASE_ATTACK, OFF_ATTACK, RANGED_ATTACK = "base", "off", "ranged"

#: (site, actor mask, action-target mask, spell type, phase, hit mask, spell?,
#:  damage?, heal?)  -- verbatim from the call sites.
EVENT_PRODUCERS: list[dict[str, Any]] = [
    {"site": "Unit::AttackerStateUpdate (Unit.cpp)", "event": "white melee swing",
     "actor": "DEAL_MELEE_SWING | MAIN_HAND_WEAPON_SWING (base) or | OFF_HAND_WEAPON_SWING (off)",
     "target": "TAKE_MELEE_SWING, | TAKE_ANY_DAMAGE if damage > 0 after resilience",
     "spell_type": "NONE", "phase": "NONE", "hit": "DamageInfo(CalcDamageInfo)::m_hitMask",
     "spell": False, "damage_info": True, "heal_info": False,
     "also": "Player::CastItemCombatSpell for the attacker (Unit.cpp, before the proc call)"},
    {"site": "Spell::TargetInfo::DoDamageAndTriggers (Spell.cpp)", "event": "spell hit on a unit target",
     "actor": "prepareDataForTriggerSystem by DmgClass, else FinalizeDataForTriggerSystem(positive)",
     "target": "matching TAKE_* flag, | TAKE_ANY_DAMAGE if damage dealt (not immune)",
     "spell_type": "HEAL if healing>0, | DAMAGE if damage>0, NO_DMG_HEAL if neither",
     "phase": "HIT", "hit": "per-target ProcHitMask (+DISPEL/INTERRUPT from effects)",
     "spell": True, "damage_info": "if damage or neither", "heal_info": "if healing",
     "gate": "unitTarget->CanProc() (m_procDeep == 0) else no event at all",
     "also": "Player::CastItemCombatSpell if DAMAGE|NO_DMG_HEAL and DmgClass melee/ranged"},
    {"site": "Spell::_cast (Spell.cpp)", "event": "cast success",
     "actor": "m_procAttacker or FinalizeDataForTriggerSystem(IsPositive()).first, | 2_CAST_SUCCESSFUL",
     "target": "NONE (no action target)", "spell_type": "MASK_ALL", "phase": "CAST",
     "hit": "m_hitMask | NORMAL unless CRITICAL", "spell": True, "damage_info": False, "heal_info": False},
    {"site": "Spell::_handle_finish_phase (Spell.cpp)", "event": "cast finish",
     "actor": "m_procAttacker or FinalizeDataForTriggerSystem(IsPositive()).first",
     "target": "NONE", "spell_type": "accumulated m_procSpellType (NO_DMG_HEAL if no targets)",
     "phase": "FINISH", "hit": "accumulated m_hitMask (NORMAL if no targets)",
     "spell": True, "damage_info": False, "heal_info": False},
    {"site": "Spell::finish (Spell.cpp)", "event": "cast ended (any result)",
     "actor": "CAST_ENDED", "target": "NONE", "spell_type": "MASK_ALL", "phase": "NONE",
     "hit": "NONE", "spell": True, "damage_info": False, "heal_info": False},
    {"site": "AuraEffect::HandlePeriodicDamageAurasTick (SpellAuraEffects.cpp)", "event": "periodic damage tick",
     "actor": "DEAL_HARMFUL_PERIODIC", "target": "TAKE_HARMFUL_PERIODIC, | TAKE_ANY_DAMAGE if damage",
     "spell_type": "DAMAGE", "phase": "HIT", "hit": "DamageInfo hit mask | CRITICAL/NORMAL if damage",
     "spell": False, "damage_info": True, "heal_info": False},
    {"site": "AuraEffect::HandlePeriodicHealthLeechAuraTick", "event": "leech tick (damage half)",
     "actor": "DEAL_HARMFUL_PERIODIC", "target": "TAKE_HARMFUL_PERIODIC, | TAKE_ANY_DAMAGE if damage",
     "spell_type": "DAMAGE", "phase": "HIT", "hit": "as periodic damage", "spell": False,
     "damage_info": True, "heal_info": False,
     "also": "then (caster, caster) DEAL/TAKE_HELPFUL_PERIODIC, HEAL for the heal half"},
    {"site": "AuraEffect::HandlePeriodicHealthFunnelAuraTick", "event": "health funnel tick",
     "actor": "DEAL_HARMFUL_PERIODIC", "target": "TAKE_HARMFUL_PERIODIC", "spell_type": "HEAL",
     "phase": "HIT", "hit": "NORMAL", "spell": False, "damage_info": False, "heal_info": True},
    {"site": "AuraEffect::HandlePeriodicHealAurasTick", "event": "periodic heal tick",
     "actor": "DEAL_HELPFUL_PERIODIC", "target": "TAKE_HELPFUL_PERIODIC", "spell_type": "HEAL",
     "phase": "HIT", "hit": "CRITICAL or NORMAL", "spell": False, "damage_info": False, "heal_info": True},
    {"site": "AuraEffect::HandlePeriodicManaLeechAuraTick/PowerBurn", "event": "power burn tick",
     "actor": "DEAL_HARMFUL_PERIODIC", "target": "TAKE_HARMFUL_PERIODIC, | TAKE_ANY_DAMAGE if damage",
     "spell_type": "NO_DMG_HEAL | DAMAGE if damage", "phase": "HIT",
     "hit": "createProcHitMask(damage, MISS_NONE)", "spell": False, "damage_info": True, "heal_info": False},
    {"site": "Unit::DealDamageShieldDamage/split damage (Unit.cpp CalcAbsorbResist)", "event": "split damage",
     "actor": "NONE", "target": "TAKE_HARMFUL_SPELL", "spell_type": "DAMAGE", "phase": "HIT",
     "hit": "NONE", "spell": False, "damage_info": True, "heal_info": False},
    {"site": "ProcReflectDelayed (Spell.cpp)", "event": "reflect",
     "actor": "NONE", "target": "TAKE_HARMFUL_SPELL | TAKE_HARMFUL_ABILITY",
     "spell_type": "DAMAGE | NO_DMG_HEAL", "phase": "NONE", "hit": "REFLECT",
     "spell": False, "damage_info": False, "heal_info": False},
    {"site": "Spell::EffectDispel / EffectStealBeneficialBuff / EffectDispelMechanic", "event": "successful dispel",
     "actor": "2_SUCCESSFUL_DISPEL", "target": "NONE (target passed, mask NONE)",
     "spell_type": "MASK_ALL", "phase": "HIT", "hit": "NONE", "spell": False,
     "damage_info": False, "heal_info": False},
    {"site": "Spell::EffectKnockBack", "event": "knocked back",
     "actor": "NONE", "target": "2_KNOCKBACK", "spell_type": "MASK_ALL", "phase": "HIT",
     "hit": "NONE", "spell": False, "damage_info": False, "heal_info": False},
    {"site": "Unit::Update heartbeat (Unit.cpp)", "event": "heartbeat", "actor": "HEARTBEAT",
     "target": "NONE", "spell_type": "MASK_ALL", "phase": "NONE", "hit": "NONE", "spell": False,
     "damage_info": False, "heal_info": False},
    {"site": "Unit::AtStartOfEncounter", "event": "encounter start", "actor": "ENCOUNTER_START",
     "target": "NONE", "spell_type": "MASK_ALL", "phase": "NONE", "hit": "NONE", "spell": False,
     "damage_info": False, "heal_info": False},
    {"site": "Unit::AtEnterCombat", "event": "enter combat", "actor": "ENTER_COMBAT",
     "target": "NONE", "spell_type": "MASK_ALL", "phase": "NONE", "hit": "NONE", "spell": False,
     "damage_info": False, "heal_info": False},
    {"site": "Unit::Kill", "event": "kill / assist / death",
     "actor": "KILL (killer and its owner); 2_TARGET_DIES (every tapper)",
     "target": "victim receives DEATH as action target of itself",
     "spell_type": "MASK_ALL", "phase": "NONE", "hit": "NONE", "spell": False,
     "damage_info": False, "heal_info": False},
    {"site": "WorldSession::HandleMovementOpcode (MovementHandler.cpp)", "event": "jump",
     "actor": "JUMP", "target": "NONE", "spell_type": "MASK_ALL", "phase": "NONE", "hit": "NONE",
     "spell": False, "damage_info": False, "heal_info": False},
    {"site": "LootHandler / Player::StoreLootItem", "event": "looted", "actor": "LOOTED",
     "target": "NONE", "spell_type": "MASK_ALL", "phase": "NONE", "hit": "NONE", "spell": False,
     "damage_info": False, "heal_info": False},
]

#: Flags defined in SpellMgr.h that no ProcSkillsAndAuras call site produces.
UNPRODUCED_FLAGS = {
    "PROC_CLONE_SPELL": "no producer in this checkout",
    "2_DO_EMOTE": "no producer in this checkout",
}


@dataclass(frozen=True)
class ProcEvent:
    """One ``ProcSkillsAndAuras`` invocation, as explicit facts.

    ``spell_id`` is ``ProcEventInfo::GetSpellInfo()`` (proc spell, else the
    damage/heal info spell).  ``has_proc_spell`` is whether a ``Spell`` object
    is attached (``GetProcSpell()``) -- false for melee swings, periodic ticks,
    reflects, dispels and knockbacks even when ``spell_id`` is set.
    """

    actor_mask: int
    target_mask: int
    spell_type_mask: int
    spell_phase_mask: int
    hit_mask: int
    spell_id: int | None = None
    has_proc_spell: bool = False
    has_damage_info: bool = False
    has_heal_info: bool = False
    damage: int = 0
    attack_type: str = BASE_ATTACK
    school_mask: int | None = None
    has_action_target: bool = True
    # Spell-object facts (meaningful only when has_proc_spell)
    spell_is_triggered: bool = False
    spell_triggered_by_aura: int | None = None
    spell_cast_item: bool = False
    spell_power_cost_positive: bool | None = None
    spell_cast_time_nonzero: bool | None = None
    spell_applied_mod_auras: frozenset[int] = frozenset()
    spell_proc_disabled: bool = False
    proc_chain_length: int = 0
    label: str = ""

    def side(self, holder: str) -> EventSide:
        if holder not in ("actor", "target"):
            raise ValueError("holder must be 'actor' or 'target'")
        return EventSide(event=self, holder=holder,
                         type_mask=self.actor_mask if holder == "actor" else self.target_mask)

    def with_(self, **changes: Any) -> ProcEvent:
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["spell_applied_mod_auras"] = sorted(self.spell_applied_mod_auras)
        return d


@dataclass(frozen=True)
class EventSide:
    event: ProcEvent
    holder: str
    type_mask: int


# ---------------------------------------------------------------------------
# hit masks
# ---------------------------------------------------------------------------

MELEE_OUTCOMES = ("miss", "dodge", "parry", "evade", "block", "crushing", "glancing",
                  "normal", "crit")


def melee_hit_mask(outcome: str, *, immune: bool = False, full_block_state: bool = False,
                   absorb: bool = False, full_absorb: bool = False,
                   full_resist: bool = False, blocked_amount: bool = False) -> int:
    """Mirrors: ``DamageInfo::DamageInfo(CalcDamageInfo const&)``."""
    if outcome not in MELEE_OUTCOMES:
        raise ValueError(f"unknown melee outcome {outcome!r}")
    mask = PROC_HIT_NONE
    if immune:
        mask |= PROC_HIT_IMMUNE
    elif full_block_state:
        mask |= PROC_HIT_FULL_BLOCK
    if absorb or full_absorb:
        mask |= PROC_HIT_ABSORB
    if full_resist:
        mask |= PROC_HIT_FULL_RESIST
    if blocked_amount:
        mask |= PROC_HIT_BLOCK
    nullified = full_absorb or full_resist or bool(mask & (PROC_HIT_IMMUNE | PROC_HIT_FULL_BLOCK))
    if outcome == "miss":
        mask |= PROC_HIT_MISS
    elif outcome == "dodge":
        mask |= PROC_HIT_DODGE
    elif outcome == "parry":
        mask |= PROC_HIT_PARRY
    elif outcome == "evade":
        mask |= PROC_HIT_EVADE
    elif outcome in ("block", "crushing", "glancing", "normal"):
        if not nullified:
            mask |= PROC_HIT_NORMAL
    elif outcome == "crit":
        if not nullified:
            mask |= PROC_HIT_CRITICAL
    return mask


SPELL_MISS = ("none", "miss", "resist", "dodge", "parry", "block", "evade", "immune",
              "immune2", "deflect", "absorb", "reflect")


def spell_hit_mask(miss: str = "none", *, crit: bool = False, blocked: bool = False,
                   full_block: bool = False, absorb: bool = False,
                   full_absorb: bool = False, full_resist: bool = False) -> int:
    """Mirrors: ``createProcHitMask(SpellNonMeleeDamage*, SpellMissInfo)`` (Unit.cpp)."""
    if miss not in SPELL_MISS:
        raise ValueError(f"unknown spell miss {miss!r}")
    if miss != "none":
        return {
            "miss": PROC_HIT_MISS, "dodge": PROC_HIT_DODGE, "parry": PROC_HIT_PARRY,
            "block": PROC_HIT_BLOCK | PROC_HIT_FULL_BLOCK, "evade": PROC_HIT_EVADE,
            "immune": PROC_HIT_IMMUNE, "immune2": PROC_HIT_IMMUNE,
            "deflect": PROC_HIT_DEFLECT, "absorb": PROC_HIT_ABSORB,
            "reflect": PROC_HIT_REFLECT, "resist": PROC_HIT_FULL_RESIST,
        }[miss]
    mask = PROC_HIT_NONE
    if blocked:
        mask |= PROC_HIT_BLOCK
        if full_block:
            mask |= PROC_HIT_FULL_BLOCK
    if absorb or full_absorb:
        mask |= PROC_HIT_ABSORB
    nullified = full_absorb or full_resist or bool(mask & PROC_HIT_FULL_BLOCK)
    if not nullified:
        mask |= PROC_HIT_CRITICAL if crit else PROC_HIT_NORMAL
    elif full_resist:
        mask |= PROC_HIT_FULL_RESIST
    return mask


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def melee_swing(hand: str = BASE_ATTACK, outcome: str = "normal", *, damage: int = 1,
                school_mask: int = 0x1, **hit_kwargs: Any) -> ProcEvent:
    """Mirrors: ``Unit::CalculateMeleeDamage`` + ``Unit::AttackerStateUpdate``."""
    if hand == BASE_ATTACK:
        actor = PROC_FLAG_DEAL_MELEE_SWING | PROC_FLAG_MAIN_HAND_WEAPON_SWING
    elif hand == OFF_ATTACK:
        actor = PROC_FLAG_DEAL_MELEE_SWING | PROC_FLAG_OFF_HAND_WEAPON_SWING
    else:
        raise ValueError("melee swings are base or off hand (CalculateMeleeDamage returns otherwise)")
    target = PROC_FLAG_TAKE_MELEE_SWING
    if damage > 0:
        target |= PROC_FLAG_TAKE_ANY_DAMAGE
    return ProcEvent(actor_mask=actor, target_mask=target,
                     spell_type_mask=PROC_SPELL_TYPE_NONE, spell_phase_mask=PROC_SPELL_PHASE_NONE,
                     hit_mask=melee_hit_mask(outcome, **hit_kwargs), spell_id=None,
                     has_proc_spell=False, has_damage_info=True, damage=damage,
                     attack_type=hand, school_mask=school_mask, label=f"melee swing {hand} {outcome}")


def trigger_flags_for_spell(info, positive: bool, attack_type: str = BASE_ATTACK) -> tuple[int, int]:
    """``prepareDataForTriggerSystem`` then ``FinalizeDataForTriggerSystem``."""
    if info.dmg_class == SPELL_DAMAGE_CLASS_MELEE:
        actor = PROC_FLAG_DEAL_MELEE_ABILITY | (
            PROC_FLAG_OFF_HAND_WEAPON_SWING if attack_type == OFF_ATTACK else PROC_FLAG_MAIN_HAND_WEAPON_SWING)
        return actor, PROC_FLAG_TAKE_MELEE_ABILITY
    if info.dmg_class == SPELL_DAMAGE_CLASS_RANGED:
        if info.has_attr(ATTR2_AUTO_REPEAT):
            return PROC_FLAG_DEAL_RANGED_ATTACK, PROC_FLAG_TAKE_RANGED_ATTACK
        return PROC_FLAG_DEAL_RANGED_ABILITY, PROC_FLAG_TAKE_RANGED_ABILITY
    # default branch: wand auto-repeat (ITEM_CLASS_WEAPON=2, SUBCLASS_WAND=19)
    if (info.equipped_item_class == 2 and info.equipped_item_subclass_mask & (1 << 19)
            and info.has_attr(ATTR2_AUTO_REPEAT)):
        return PROC_FLAG_DEAL_RANGED_ATTACK, PROC_FLAG_TAKE_RANGED_ATTACK
    if info.has_attr(ATTR3_TREAT_AS_PERIODIC):
        return ((PROC_FLAG_DEAL_HELPFUL_PERIODIC, PROC_FLAG_TAKE_HELPFUL_PERIODIC) if positive
                else (PROC_FLAG_DEAL_HARMFUL_PERIODIC, PROC_FLAG_TAKE_HARMFUL_PERIODIC))
    if info.has_attr(ATTR0_IS_ABILITY):
        return ((PROC_FLAG_DEAL_HELPFUL_ABILITY, PROC_FLAG_TAKE_HELPFUL_ABILITY) if positive
                else (PROC_FLAG_DEAL_HARMFUL_ABILITY, PROC_FLAG_TAKE_HARMFUL_ABILITY))
    return ((PROC_FLAG_DEAL_HELPFUL_SPELL, PROC_FLAG_TAKE_HELPFUL_SPELL) if positive
            else (PROC_FLAG_DEAL_HARMFUL_SPELL, PROC_FLAG_TAKE_HARMFUL_SPELL))


def spell_hit(info, *, damage: int = 0, healing: int = 0, all_hit_effects_positive: bool | None = None,
              miss: str = "none", crit: bool = False, immune_to_damage: bool = False,
              attack_type: str = BASE_ATTACK, extra_hit_mask: int = 0, **spell_facts: Any) -> ProcEvent:
    """Mirrors: ``Spell::TargetInfo::DoDamageAndTriggers`` for one unit target.

    ``all_hit_effects_positive`` stands in for ``SpellInfo::IsPositiveEffect``
    over the hit effects (not ported); it is only consulted when the spell
    neither damaged nor healed, exactly like the consumer.
    """
    if miss not in ("none", "block") and (damage or healing):
        # Spell::TargetInfo::PreprocessTarget zeroes m_damage/m_healing on a miss.
        raise ValueError(f"a '{miss}' result carries no damage or healing")
    if damage > 0:
        positive = False
    elif not healing:
        if all_hit_effects_positive is None:
            raise ValueError("positivity of a no-damage/no-heal hit is an external fact "
                             "(SpellInfo::IsPositiveEffect is not ported)")
        positive = all_hit_effects_positive
    else:
        positive = True
    actor, target = trigger_flags_for_spell(info, positive, attack_type)
    spell_type = PROC_SPELL_TYPE_NONE
    hit = extra_hit_mask
    has_damage_info = has_heal_info = False
    if healing > 0:
        hit |= PROC_HIT_CRITICAL if crit else PROC_HIT_NORMAL
        spell_type |= PROC_SPELL_TYPE_HEAL
        has_heal_info = True
    if damage > 0:
        if immune_to_damage:
            hit = PROC_HIT_IMMUNE
        else:
            hit |= spell_hit_mask(miss, crit=crit)
            target |= PROC_FLAG_TAKE_ANY_DAMAGE
        spell_type |= PROC_SPELL_TYPE_DAMAGE
        has_damage_info = True
    if not healing and not damage:
        hit |= spell_hit_mask(miss)
        spell_type |= PROC_SPELL_TYPE_NO_DMG_HEAL
        has_damage_info = True
    return ProcEvent(actor_mask=actor, target_mask=target, spell_type_mask=spell_type,
                     spell_phase_mask=PROC_SPELL_PHASE_HIT, hit_mask=hit, spell_id=info.id,
                     has_proc_spell=True, has_damage_info=has_damage_info,
                     has_heal_info=has_heal_info, damage=damage, attack_type=attack_type,
                     school_mask=info.school_mask, label=f"spell hit {info.id}", **spell_facts)


def spell_cast(info, *, positive: bool, attack_type: str = BASE_ATTACK, hit_mask: int = 0,
               **spell_facts: Any) -> ProcEvent:
    """Mirrors: the cast-success block in ``Spell::_cast``."""
    actor, _ = trigger_flags_for_spell(info, positive, attack_type)
    actor |= PROC_FLAG_2_CAST_SUCCESSFUL
    if not hit_mask & PROC_HIT_CRITICAL:
        hit_mask |= PROC_HIT_NORMAL
    return ProcEvent(actor_mask=actor, target_mask=PROC_FLAG_NONE,
                     spell_type_mask=PROC_SPELL_TYPE_MASK_ALL, spell_phase_mask=PROC_SPELL_PHASE_CAST,
                     hit_mask=hit_mask, spell_id=info.id, has_proc_spell=True,
                     school_mask=info.school_mask, has_action_target=False,
                     attack_type=attack_type, label=f"spell cast {info.id}", **spell_facts)


def spell_finish(info, *, positive: bool, spell_type_mask: int | None, hit_mask: int | None,
                 attack_type: str = BASE_ATTACK, **spell_facts: Any) -> ProcEvent:
    """Mirrors: ``Spell::_handle_finish_phase``; ``None`` masks = no unit targets."""
    actor, _ = trigger_flags_for_spell(info, positive, attack_type)
    if spell_type_mask is None:
        spell_type_mask = PROC_SPELL_TYPE_NO_DMG_HEAL
    if hit_mask is None:
        hit_mask = PROC_HIT_NORMAL
    return ProcEvent(actor_mask=actor, target_mask=PROC_FLAG_NONE, spell_type_mask=spell_type_mask,
                     spell_phase_mask=PROC_SPELL_PHASE_FINISH, hit_mask=hit_mask, spell_id=info.id,
                     has_proc_spell=True, school_mask=info.school_mask, has_action_target=False,
                     attack_type=attack_type, label=f"spell finish {info.id}", **spell_facts)


def cast_ended(info, **spell_facts: Any) -> ProcEvent:
    return ProcEvent(actor_mask=PROC_FLAG_CAST_ENDED, target_mask=PROC_FLAG_NONE,
                     spell_type_mask=PROC_SPELL_TYPE_MASK_ALL, spell_phase_mask=PROC_SPELL_PHASE_NONE,
                     hit_mask=PROC_HIT_NONE, spell_id=info.id, has_proc_spell=True,
                     school_mask=info.school_mask, has_action_target=False,
                     label=f"cast ended {info.id}", **spell_facts)


def periodic_damage(info, *, damage: int, crit: bool = False, absorb: bool = False,
                    proc_chain_length: int = 0) -> ProcEvent:
    """Mirrors: ``AuraEffect::HandlePeriodicDamageAurasTick``.  No Spell object."""
    hit = PROC_HIT_ABSORB if absorb else PROC_HIT_NONE
    target = PROC_FLAG_TAKE_HARMFUL_PERIODIC
    if damage:
        hit |= PROC_HIT_CRITICAL if crit else PROC_HIT_NORMAL
        target |= PROC_FLAG_TAKE_ANY_DAMAGE
    return ProcEvent(actor_mask=PROC_FLAG_DEAL_HARMFUL_PERIODIC, target_mask=target,
                     spell_type_mask=PROC_SPELL_TYPE_DAMAGE, spell_phase_mask=PROC_SPELL_PHASE_HIT,
                     hit_mask=hit, spell_id=info.id, has_proc_spell=False, has_damage_info=True,
                     damage=damage, school_mask=info.school_mask,
                     proc_chain_length=proc_chain_length, label=f"periodic damage {info.id}")


def periodic_heal(info, *, crit: bool = False, proc_chain_length: int = 0) -> ProcEvent:
    """Mirrors: ``AuraEffect::HandlePeriodicHealAurasTick``.  No Spell object."""
    return ProcEvent(actor_mask=PROC_FLAG_DEAL_HELPFUL_PERIODIC, target_mask=PROC_FLAG_TAKE_HELPFUL_PERIODIC,
                     spell_type_mask=PROC_SPELL_TYPE_HEAL, spell_phase_mask=PROC_SPELL_PHASE_HIT,
                     hit_mask=PROC_HIT_CRITICAL if crit else PROC_HIT_NORMAL, spell_id=info.id,
                     has_proc_spell=False, has_heal_info=True, school_mask=info.school_mask,
                     proc_chain_length=proc_chain_length, label=f"periodic heal {info.id}")


def simple_event(kind: str) -> ProcEvent:
    """Heartbeat / combat / encounter / kill / death / jump / looted / dispel / knockback."""
    table = {
        "heartbeat": (PROC_FLAG_HEARTBEAT, PROC_FLAG_NONE, PROC_SPELL_PHASE_NONE, False),
        "enter_combat": (PROC_FLAG_ENTER_COMBAT, PROC_FLAG_NONE, PROC_SPELL_PHASE_NONE, False),
        "encounter_start": (PROC_FLAG_ENCOUNTER_START, PROC_FLAG_NONE, PROC_SPELL_PHASE_NONE, False),
        "kill": (PROC_FLAG_KILL, PROC_FLAG_NONE, PROC_SPELL_PHASE_NONE, True),
        "target_dies": (PROC_FLAG_2_TARGET_DIES, PROC_FLAG_NONE, PROC_SPELL_PHASE_NONE, True),
        "death": (PROC_FLAG_NONE, PROC_FLAG_DEATH, PROC_SPELL_PHASE_NONE, True),
        "jump": (PROC_FLAG_JUMP, PROC_FLAG_NONE, PROC_SPELL_PHASE_NONE, False),
        "looted": (PROC_FLAG_LOOTED, PROC_FLAG_NONE, PROC_SPELL_PHASE_NONE, False),
        "dispel": (PROC_FLAG_2_SUCCESSFUL_DISPEL, PROC_FLAG_NONE, PROC_SPELL_PHASE_HIT, True),
        "knockback": (PROC_FLAG_NONE, PROC_FLAG_2_KNOCKBACK, PROC_SPELL_PHASE_HIT, True),
    }
    actor, target, phase, has_target = table[kind]
    return ProcEvent(actor_mask=actor, target_mask=target, spell_type_mask=PROC_SPELL_TYPE_MASK_ALL,
                     spell_phase_mask=phase, hit_mask=PROC_HIT_NONE, has_action_target=has_target,
                     label=kind)


def reflect_event() -> ProcEvent:
    return ProcEvent(actor_mask=PROC_FLAG_NONE,
                     target_mask=PROC_FLAG_TAKE_HARMFUL_SPELL | PROC_FLAG_TAKE_HARMFUL_ABILITY,
                     spell_type_mask=PROC_SPELL_TYPE_DAMAGE | PROC_SPELL_TYPE_NO_DMG_HEAL,
                     spell_phase_mask=PROC_SPELL_PHASE_NONE, hit_mask=PROC_HIT_REFLECT,
                     label="reflect")
