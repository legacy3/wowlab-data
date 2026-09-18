"""Track G -- passive vs active auras and the acquisition lifecycle.

The question: which lifecycle rules genuinely differ when the aura's spell is
Passive, how a passive is *acquired* (learn / trait / spec / equip / set /
enchant / form / aura-state) and *lost* (unlearn / rank change / unequip /
form loss / state loss / interrupt / charges / expiry / explicit removal), and
which passives carry mutable state (stacks, charges, proc state, periodic
timers, finite duration, script state) despite the name.

Three layers, all pure:

* :func:`facts` / :func:`profile`  -- one spell, from the snapshot + world overlay;
* :func:`census`                     -- the provider populations (``providers.populations``);
* :class:`Sim` / :func:`timelines`   -- millisecond timelines over explicit fixtures,
  each step mirroring the Trinity consumer it names, each timeline naming the
  competing model it rules out.

"Passive" throughout means Trinity's effective ``SpellInfo::IsPassive`` -- the
DB2 ``SPELL_ATTR0_PASSIVE`` bit *plus* the two load-time corrections that set it
(:func:`effective_passive`).  Trinity is a consumer oracle, not Retail truth; the
rows say so in ``evidence``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from procs.enums import attr, aura, effect

from . import FailClosed
from .providers import populations

# ---------------------------------------------------------------------------
# constants (each with its consumer)
# ---------------------------------------------------------------------------

ATTR0_PASSIVE = attr("SPELL_ATTR0_PASSIVE")
ATTR0_NOT_SHAPESHIFTED = attr("SPELL_ATTR0_NOT_SHAPESHIFTED")
ATTR1_CAST_WHEN_LEARNED = attr("SPELL_ATTR1_CAST_WHEN_LEARNED")
ATTR2_ALLOW_WHILE_NOT_SHAPESHIFTED = attr("SPELL_ATTR2_ALLOW_WHILE_NOT_SHAPESHIFTED_CASTER_FORM")
ATTR3_ALLOW_AURA_WHILE_DEAD = attr("SPELL_ATTR3_ALLOW_AURA_WHILE_DEAD")
ATTR4_ALLOW_ENTERING_ARENA = attr("SPELL_ATTR4_ALLOW_ENTERING_ARENA")
ATTR5_REMOVE_ENTERING_ARENA = attr("SPELL_ATTR5_REMOVE_ENTERING_ARENA")

# Mirrors: SpellMgr.cpp:5301-5302 (loop over every SpellInfo from SpellMgr.cpp:5244):
#   if (spellInfo->ActiveIconFileDataId == 135754) Attributes |= SPELL_ATTR0_PASSIVE  // flight
PASSIVE_ICON_FILE_DATA_ID = 135754
# Mirrors: SpellMgr.cpp:3839-3843 ApplySpellFix({ 59630 }) Black Magic -> |= SPELL_ATTR0_PASSIVE
PASSIVE_SPELL_FIXES = frozenset({59630})

# Mirrors: AuraEffect::CalculatePeriodic switch, SpellAuraEffects.cpp:966-983
PERIODIC_AURAS = frozenset(aura(n) for n in (
    "OBS_MOD_POWER", "PERIODIC_DAMAGE", "PERIODIC_HEAL", "OBS_MOD_HEALTH", "PERIODIC_TRIGGER_SPELL",
    "PERIODIC_TRIGGER_SPELL_FROM_CLIENT", "PERIODIC_ENERGIZE", "PERIODIC_LEECH", "PERIODIC_HEALTH_FUNNEL",
    "PERIODIC_MANA_LEECH", "PERIODIC_DAMAGE_PERCENT", "POWER_BURN", "PERIODIC_DUMMY",
    "PERIODIC_TRIGGER_SPELL_WITH_VALUE"))
SPELL_EFFECT_APPLY_AURA = effect("APPLY_AURA")
SPELL_EFFECT_MODIFY_AURA_STACKS = effect("MODIFY_AURA_STACKS")      # Spell::EffectModifyAuraStacks SpellEffects.cpp:6126
SPELL_EFFECT_REMOVE_AURA = effect("REMOVE_AURA")                    # Spell::EffectRemoveAura SpellEffects.cpp:5152
SPELL_EFFECT_REMOVE_AURA_2 = effect("REMOVE_AURA_2")
SPELL_EFFECT_LEARN_SPELL = effect("LEARN_SPELL")
SPELL_EFFECT_SKILL_STEP = effect("SKILL_STEP")

ITEM_SPELLTRIGGER_ON_EQUIP = 1          # ItemTemplate.h:113
AURA_STATE_ENRAGED = 17                 # SharedDefines.h:2849
ITEM_ENCHANTMENT_TYPE_COMBAT_SPELL = 1  # DBCEnums.h:1223
ITEM_ENCHANTMENT_TYPE_EQUIP_SPELL = 3   # DBCEnums.h:1225
ITEM_ENCHANTMENT_TYPE_USE_SPELL = 7     # DBCEnums.h:1229

TC = "src/server/game/"

# AuraInterruptFlags that occur on passives, with the Trinity consumer that fires them.
# Names: SpellDefines.h:77-145.  ``None`` consumer = not located by track G (not a claim of NYI);
# "NYI" only where SpellDefines.h says so.
INTERRUPT_FLAGS: dict[tuple[int, int], tuple[str, str | None]] = {
    (0, 0x00000001): ("HostileActionReceived", "Spells/Spell.cpp:3142"),
    (0, 0x00000002): ("Damage", "Entities/Unit/Unit.cpp:875"),
    (0, 0x00000004): ("Action", "Entities/Unit/Unit.cpp:4218 (filter); cast paths"),
    (0, 0x00000008): ("Moving", "Entities/Unit/Unit.cpp:656"),
    (0, 0x00000400): ("Interacting", "Handlers/NPCHandler.cpp:171"),
    (0, 0x00000800): ("Looting", "Handlers/LootHandler.cpp:231"),
    (0, 0x00001000): ("Attacking", "Entities/Unit/Unit.cpp:2283"),
    (0, 0x00002000): ("ItemUse", "Entities/Player/Player.cpp:31723"),
    (0, 0x00020000): ("Mount", "Entities/Unit/Unit.cpp:8313"),
    (0, 0x00080000): ("LeaveWorld", "Entities/Unit/Unit.cpp:10276 (Unit::RemoveFromWorld)"),
    (0, 0x00400000): ("EnterWorld", "Entities/Unit/Unit.cpp:10250 (Unit::AddToWorld)"),
    (0, 0x10000000): ("EnteringCombat", "Entities/Unit/Unit.cpp:9232"),
    (0, 0x20000000): ("Login", "Handlers/CharacterHandler.cpp:1308 (after LoadFromDB cast the passives)"),
    (0, 0x80000000): ("LeavingCombat", "Entities/Unit/Unit.cpp:9249"),
    (1, 0x00000040): ("ChangeSpec", "Entities/Player/Player.cpp:28809 (ActivateTalentGroup)"),
    (1, 0x00000080): ("AbandonVehicle", "Entities/Unit/Unit.cpp:12963"),
    (1, 0x00000100): ("StartOfRaidEncounterAndStartOfMythicPlus", "Entities/Unit/Unit.cpp:549"),
    (1, 0x00000200): ("EndOfRaidEncounterAndStartOfMythicPlus", "Entities/Unit/Unit.cpp:554"),
    (1, 0x00000800): ("EnteringInstance", "Maps/Map.cpp:413"),
    (1, 0x00002000): ("LeaveArenaOrBattleground", "Battlegrounds/Battleground.cpp:844"),
    (1, 0x00004000): ("ChangeTalent", "Entities/Player/Player.cpp:2642 (legacy Player::AddTalent only; trait edits never fire it)"),
    (1, 0x00008000): ("ChangeGlyph", "Spells/SpellEffects.cpp:3434"),
    (1, 0x00010000): ("SeamlessTransfer", "NYI (SpellDefines.h:136)"),
    (1, 0x00800000): ("StartOfEncounter", "Entities/Unit/Unit.cpp:550"),
    (1, 0x01000000): ("EndOfEncounter", "Entities/Unit/Unit.cpp:575"),
}


# What the firing path does to a passive afterwards (Trinity).  "lost" = removed, nothing on
# that path re-acquires it (no-self-restoration, AL-R-G-09).
INTERRUPT_NET: dict[str, tuple[str, str]] = {
    "ChangeSpec": ("reset-if-trait-or-spec-route-else-lost",
                   "Player::ActivateTalentGroup fires it (Player.cpp:28809) before ApplyTraitConfig(false) (28885) / "
                   "RemoveSpecializationSpells (28888) and the relearn (28950, 28957): trait/spec passives are recreated"),
    "ChangeTalent": ("not-fired-by-trait-edits", "only legacy Player::AddTalent fires it (Player.cpp:2642); UpdateTraitConfig does not"),
    "ChangeGlyph": ("lost", "Spell::EffectApplyGlyph fires it (SpellEffects.cpp:3434); nothing on that path relearns"),
    "EnterWorld": ("lost-at-every-world-entry", "Unit::AddToWorld (Unit.cpp:10250) runs after LoadFromDB cast the passives, and on every map add"),
    "LeaveWorld": ("lost-at-world-exit", "Unit::RemoveFromWorld (Unit.cpp:10276): far teleport loses it; logout moot (not saved)"),
    "Login": ("lost-after-login", "WorldSession::HandlePlayerLogin fires it (CharacterHandler.cpp:1308) after the load-time cast"),
}


def interrupt_net_effect(name: str, route_kinds: list[str]) -> str:
    """Trinity net effect of one interrupt flag on a passive acquired by ``route_kinds``."""
    consumer = next((v[1] for v in INTERRUPT_FLAGS.values() if v[0] == name), None)
    if name not in INTERRUPT_NET:
        if consumer is None:
            return "consumer-not-located"
        if consumer.startswith("NYI"):
            return "not-fired-NYI"
    kind = INTERRUPT_NET.get(name, ("lost-until-reacquisition", ""))[0]
    if kind == "reset-if-trait-or-spec-route-else-lost":
        return "reset" if any(k in ("class-trait", "spec-spell") for k in route_kinds) else "lost"
    return kind


def interrupt_flag_names(flags: tuple[int, int]) -> list[str]:
    out = []
    for word, value in enumerate(flags):
        for bit in range(32):
            if value >> bit & 1:
                name = INTERRUPT_FLAGS.get((word, 1 << bit), (f"flags{word}:bit{bit}", None))[0]
                out.append(name)
    return out



def _c(path: str, line: int | str) -> str:
    return f"{TC}{path}:{line}"


# ---------------------------------------------------------------------------
# per-spell facts
# ---------------------------------------------------------------------------

def _has(attrs: tuple[int, ...], key: tuple[int, int]) -> bool:
    word, bit = key
    return bool(attrs[word] & bit) if word < len(attrs) else False


def _attrs(misc: dict | None) -> tuple[int, ...]:
    if not misc:
        return ()
    return tuple(int(misc.get(f"Attributes_{i}", 0) or 0) & 0xFFFFFFFF for i in range(17))


def effective_passive(data, spell: int) -> dict[str, Any]:
    """Trinity's ``SpellInfo::IsPassive`` for one spell, with its reasons.

    Mirrors: SpellInfo.cpp:1753-1756 (IsPassive = HasAttribute(SPELL_ATTR0_PASSIVE));
    SpellMgr.cpp:3839-3843 and SpellMgr.cpp:5301-5302 (load-time corrections that set it).
    No Trinity code clears the bit (SpellMgr.cpp has no ``&= ~SPELL_ATTR0_PASSIVE``).
    """
    misc = data.row("SpellMisc", spell)
    if misc is None:
        raise FailClosed(f"{spell}: no DIFFICULTY_NONE SpellMisc row; IsPassive undetermined")
    reasons = []
    if _has(_attrs(misc), ATTR0_PASSIVE):
        reasons.append("db2:SPELL_ATTR0_PASSIVE")
    if misc.get("ActiveIconFileDataID") == PASSIVE_ICON_FILE_DATA_ID:
        reasons.append("trinity-correction:ActiveIconFileDataId==135754 (SpellMgr.cpp:5301)")
    if spell in PASSIVE_SPELL_FIXES:
        reasons.append("trinity-correction:ApplySpellFix (SpellMgr.cpp:3839)")
    return {"passive": bool(reasons), "db2_passive": reasons[:1] == ["db2:SPELL_ATTR0_PASSIVE"],
            "reasons": reasons}


def _periodic_effects(effects: list[dict]) -> list[dict]:
    return [{"index": e["EffectIndex"], "aura": e["EffectAura"], "period_ms": e["EffectAuraPeriod"]}
            for e in effects if e["EffectAura"] in PERIODIC_AURAS and _is_aura_row(e)]


def _is_aura_row(e: dict) -> bool:
    from procs.enums import is_aura_effect
    return is_aura_effect(e["Effect"], e["EffectAura"])


@dataclass
class Facts:
    spell: int
    name: str
    passive: dict[str, Any]
    effects: list[dict]
    attributes: tuple[int, ...]
    duration_index: int
    duration: dict | None
    stack_cap: int
    proc_charges_db2: int
    proc_type_mask: tuple[int, int]
    spell_proc: dict | None
    proc_entry: dict | None
    scripts: list[str]
    stances: int
    caster_aura_state: int
    equipped_item_class: int
    aura_interrupt_flags: tuple[int, int]
    build_skew: bool
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_passive(self) -> bool:
        return self.passive["passive"]

    def has_attr(self, key: tuple[int, int]) -> bool:
        return _has(self.attributes, key)

    @property
    def aura_effects(self) -> list[dict]:
        return [e for e in self.effects if _is_aura_row(e)]

    @property
    def has_apply_aura(self) -> bool:
        return any(e["Effect"] == SPELL_EFFECT_APPLY_AURA for e in self.effects)

    @property
    def area(self) -> bool:
        from procs.enums import AREA_AURA_EFFECTS
        return any(e["Effect"] in AREA_AURA_EFFECTS for e in self.effects)

    @property
    def periodic(self) -> list[dict]:
        return _periodic_effects(self.effects)

    @property
    def proc_charges(self) -> int:
        """Base max charges before the caster's ProcCharges SpellMod.

        Mirrors: Aura::CalcMaxCharges SpellAuras.cpp:1004-1008 -- the effective proc entry's Charges
        (spell_proc row, even 0, or generated copy) wins over SpellInfo::ProcCharges (SpellInfo.cpp:1388)."""
        if self.proc_entry is not None:
            return self.proc_entry["charges"]
        return self.proc_charges_db2


_STORES: dict[int, Any] = {}


def proc_store(ctx):
    """The proc research's ``mSpellProcMap`` model (procs.definition.ProcEntryStore), one per context."""
    key = id(ctx)
    if key not in _STORES:
        from procs.definition import ProcEntryStore
        _STORES[key] = ProcEntryStore(ctx.catalog, ctx.bundle.proc_overlay)
    return _STORES[key]


def _proc_entry(ctx, spell: int) -> dict | None:
    """Mirrors: SpellMgr::GetSpellProcEntry via procs.definition.ProcEntryStore.lookup
    (SpellMgr::LoadSpellProcs SpellMgr.cpp:1497-1658 rows, 1765-1817 generation)."""
    e = proc_store(ctx).lookup(spell)
    if e is None:
        return None
    return {"origin": e.origin, "charges": e.charges, "cooldown_ms": e.cooldown_ms, "chance": e.chance,
            "ppm": e.procs_per_minute, "proc_flags": e.proc_flags}


def facts(ctx, spell: int, data=None) -> Facts:
    drift_view = data is not None and data is not ctx.data
    data = data or ctx.data
    effects = data.effects(spell)
    if not effects:
        raise FailClosed(f"{spell}: no DIFFICULTY_NONE SpellEffect rows")
    misc = data.row("SpellMisc", spell)
    passive = effective_passive(data, spell)
    di = int(misc["DurationIndex"] or 0)
    ao = data.row("SpellAuraOptions", spell) or {}
    restr = data.row("SpellAuraRestrictions", spell) or {}
    shape = data.row("SpellShapeshift", spell) or {}
    equip = data.row("SpellEquippedItems", spell)
    interrupts = data.row("SpellInterrupts", spell) or {}
    sp = ctx.bundle.proc_overlay.spell_proc.get(spell)
    return Facts(
        spell=spell, name=ctx.name(spell), passive=passive, effects=effects, attributes=_attrs(misc),
        duration_index=di, duration=data.duration(di) if di else None,
        stack_cap=int(ao.get("CumulativeAura", 0) or 0), proc_charges_db2=int(ao.get("ProcCharges", 0) or 0),
        proc_type_mask=(int(ao.get("ProcTypeMask_0", 0) or 0), int(ao.get("ProcTypeMask_1", 0) or 0)),
        spell_proc=({"charges": sp.charges, "cooldown": sp.cooldown, "chance": sp.chance,
                     "ppm": sp.procs_per_minute, "proc_flags": sp.proc_flags} if sp else None),
        # the proc model reads the primary snapshot's catalog; a drift view carries no entry
        proc_entry=None if drift_view else _proc_entry(ctx, spell),
        scripts=sorted(ctx.bundle.script_names.get(spell, [])),
        stances=(int(shape.get("ShapeshiftMask_0", 0) or 0) & 0xFFFFFFFF)
        | ((int(shape.get("ShapeshiftMask_1", 0) or 0) & 0xFFFFFFFF) << 32),
        caster_aura_state=int(restr.get("CasterAuraState", 0) or 0),
        equipped_item_class=int(equip["EquippedItemClass"]) if equip else -1,
        aura_interrupt_flags=(int(interrupts.get("AuraInterruptFlags_0", 0) or 0),
                              int(interrupts.get("AuraInterruptFlags_1", 0) or 0)),
        build_skew=ctx.is_skew(spell),
    )


# ---------------------------------------------------------------------------
# per-spell lifecycle policy (Trinity consumer semantics)
# ---------------------------------------------------------------------------

def max_duration(f: Facts) -> dict[str, Any]:
    """Base max duration before caster SpellMods.

    Mirrors: Aura::CalcMaxDuration SpellAuras.cpp:925-926 (IsPassive && !DurationEntry -> -1);
    SpellInfo::GetMaxDuration SpellInfo.cpp:3993-3998 (!DurationEntry -> IsPassive ? -1 : 0).
    A passive WITH a DurationEntry keeps a finite duration: Passive != permanent.
    """
    if not f.duration_index or f.duration is None:
        if f.duration_index and f.duration is None:
            # SpellInfo.cpp:1364 LookupEntry of a missing row -> DurationEntry == nullptr
            note = "DurationIndex names no SpellDuration row -> DurationEntry null"
        else:
            note = "no DurationIndex"
        if f.is_passive:
            return {"kind": "permanent", "ms": -1, "note": note, "coords": [_c("Spells/Auras/SpellAuras.cpp", 925)]}
        return {"kind": "zero", "ms": 0, "note": note + " (non-passive: GetMaxDuration 0; track B)",
                "coords": [_c("Spells/SpellInfo.cpp", 3995)]}
    raw = int(f.duration["MaxDuration"])
    ms = -1 if raw == -1 else abs(raw)
    kind = "permanent" if ms == -1 else ("zero" if ms == 0 else "finite")
    return {"kind": kind, "ms": ms, "note": f"SpellDuration {f.duration_index}",
            "coords": [_c("Spells/SpellInfo.cpp", 3997), _c("Spells/Auras/SpellAuras.cpp", 919)]}


def reapplication(*, passive: bool, multislot_by_id: bool = False, same_caster: bool, same_spell: bool,
                  incoming_cast_item: bool, existing_cast_item: bool, has_apply_aura_effect: bool,
                  is_area: bool = False, effect_masks_equal: bool = True,
                  new_is_highest_exclusive: bool = True) -> dict[str, Any]:
    """What a second application of the same spell does to the owner's existing aura object.

    Only the passive branch is decided here; the active branch returns the entry point
    that tracks A/B/C own (and fails closed where their inputs are needed).

    Mirrors:
      Unit::_TryStackingOrRefreshingExistingAura Unit.cpp:3395 (IsMultiSlotAura -> no lookup);
      SpellInfo::IsMultiSlotAura SpellInfo.cpp:1803-1806 (IsPassive || 3 literal ids);
      Unit::_AddAura Unit.cpp:3452-3454 -> _RemoveNoStackAurasDueToAura Unit.cpp:3714-3730;
      SpellInfo::IsPassiveStackableWithRanks SpellInfo.cpp:1798-1801;
      Aura::CanStackWith SpellAuras.cpp:1651-1652 (passive same caster same id, empty item -> false)
      and SpellAuras.cpp:1758-1766 (rank-of branch: multislot && !IsArea -> true).
    """
    if not same_spell:
        raise FailClosed("different spells: identity/exclusivity rules are track A's (CanStackWith full)")
    multislot = passive or multislot_by_id
    if not multislot:
        if not same_caster:
            raise FailClosed("active, different caster: IsStackableOnOneSlotWithDifferentCasters/CanStackWith (track A)")
        if not effect_masks_equal:
            return {"outcome": "recreate", "object": "new",
                    "why": "effect masks differ -> nullptr -> Aura::Create (Unit.cpp:3405-3406)",
                    "coords": [_c("Entities/Unit/Unit.cpp", 3405)]}
        return {"outcome": "refresh-or-stack", "object": "existing",
                "why": "GetOwnedAura found -> ModStackAmount(+StackAmount) (Unit.cpp:3440); refresh/stack details track B/C",
                "coords": [_c("Entities/Unit/Unit.cpp", 3401), _c("Entities/Unit/Unit.cpp", 3440)]}
    base = {"object": "new", "coords": [_c("Entities/Unit/Unit.cpp", 3395), _c("Spells/SpellInfo.cpp", 1805)]}
    if not new_is_highest_exclusive:
        return {**base, "outcome": "new-removed",
                "why": "!IsHighestExclusiveAura(new) -> new aura->Remove() (Unit.cpp:3722-3726)"}
    if passive and not has_apply_aura_effect:
        return {**base, "outcome": "coexist",
                "why": "IsPassiveStackableWithRanks: no-stack pass skipped (Unit.cpp:3718-3720)",
                "coords": base["coords"] + [_c("Spells/SpellInfo.cpp", 1800)]}
    if not same_caster:
        raise FailClosed("multislot, different caster: CanStackWith generic tail (track A)")
    if passive and not incoming_cast_item:
        return {**base, "outcome": "replace",
                "why": "CanStackWith false (SpellAuras.cpp:1651) -> old owned aura removed BY_DEFAULT before the new one is applied",
                "coords": base["coords"] + [_c("Spells/Auras/SpellAuras.cpp", 1651), _c("Entities/Unit/Unit.cpp", 3729)]}
    # passive with a cast item: falls through to the rank-of branch
    if is_area:
        # SpellAuras.cpp:1760 skipped; 1762-1764 enchant-proc different items; else 1766 false
        raise FailClosed("item-sourced passive AREA aura: depends on SPELL_ATTR0_CU_ENCHANT_PROC and item GUIDs (SpellAuras.cpp:1762-1766)")
    return {**base, "outcome": "coexist",
            "why": "item-sourced passive: 1651 skipped (cast item non-empty); IsRankOf && IsMultiSlotAura && !IsArea -> true (SpellAuras.cpp:1760)",
            "coords": base["coords"] + [_c("Spells/Auras/SpellAuras.cpp", 1760)]}


def removal_policy(f: Facts) -> dict[str, dict[str, Any]]:
    """Per removal/loss event: does the aura go, and does it come back by itself.

    ``returns_by_itself`` is always False in Trinity: there is no periodic passive
    re-application loop; only the acquisition events of :func:`reacquisition` recreate one.
    """
    p = f.is_passive
    dead_ok = f.has_attr(ATTR3_ALLOW_AURA_WHILE_DEAD)
    out: dict[str, dict[str, Any]] = {}

    def put(event, removed, why, coords, mode=None):
        out[event] = {"removed": removed, "remove_mode": mode, "why": why, "coords": coords}

    put("owner-death", (not p and not dead_ok), "RemoveAllAurasOnDeath skips IsPassive and IsDeathPersistent (applied and owned lists)",
        [_c("Entities/Unit/Unit.cpp", 4479), _c("Entities/Unit/Unit.cpp", 4488)], "AURA_REMOVE_BY_DEATH")
    put("dispel", "never" if p else "dispel-type-dependent (track E)",
        "GetDispellableAuraList: 'don't try to remove passive auras'", [_c("Entities/Unit/Unit.cpp", 4746)])
    put("spell-steal", "never" if p else "track E", "EffectStealBeneficialBuff skips aura->IsPassive()",
        [_c("Spells/SpellEffects.cpp", 4747)])
    put("immunity-purge", "never" if p else "track E", "SpellInfo::ApplyAllSpellImmunitiesTo purge lambda skips passives",
        [_c("Spells/SpellInfo.cpp", 3718)])
    put("player-cancel", "never" if p else "if positive (track E)", "CMSG_CANCEL_AURA refuses passive",
        [_c("Handlers/SpellHandler.cpp", 281)])
    put("arena-entry", (not p and not f.has_attr(ATTR4_ALLOW_ENTERING_ARENA)) or f.has_attr(ATTR5_REMOVE_ENTERING_ARENA),
        "RemoveArenaAuras: !IsPassive conjunct, ATTR5_REMOVE_ENTERING_ARENA overrides", [_c("Entities/Unit/Unit.cpp", 4436)])
    put("aura-interrupt", bool(f.aura_interrupt_flags[0] or f.aura_interrupt_flags[1]),
        "RemoveAurasWithInterruptFlags has no passive exemption", [_c("Entities/Unit/Unit.cpp", 4250)], "AURA_REMOVE_BY_INTERRUPT")
    put("explicit-remove-effect", True, "Spell::EffectRemoveAura -> RemoveAurasDueToSpell(TriggerSpell), no passive guard",
        [_c("Spells/SpellEffects.cpp", 5160)], "AURA_REMOVE_BY_DEFAULT")
    put("charges-exhausted", f.proc_charges > 0, "Aura::ModCharges charges<=0 -> Remove(removeMode); no passive guard",
        [_c("Spells/Auras/SpellAuras.cpp", 1027)])
    md = max_duration(f)
    put("expiry", md["kind"] == "finite", "Unit::_UpdateSpells IsExpired -> RemoveOwnedAura BY_EXPIRE; passive only permanent without DurationEntry",
        [_c("Entities/Unit/Unit.cpp", 2986), _c("Spells/Auras/SpellAuras.cpp", 925)], "AURA_REMOVE_BY_EXPIRE")
    shape_lost = bool(f.stances) and not f.has_attr(ATTR2_ALLOW_WHILE_NOT_SHAPESHIFTED) and not f.has_attr(ATTR0_NOT_SHAPESHIFTED)
    put("shapeshift-lost", shape_lost, "Aura::IsRemovedOnShapeLost (caster==target && Stances && !ATTR2 && !ATTR0_NOT_SHAPESHIFTED), applies to actives too",
        [_c("Spells/Auras/SpellAuras.cpp", 1160), _c("Spells/Auras/SpellAuraEffects.cpp", 1631)])
    put("caster-aura-state-lost", bool(f.caster_aura_state) and (p or f.caster_aura_state != AURA_STATE_ENRAGED),
        "ModifyAuraState(flag,false) removes own auras with that CasterAuraState (actives exempt only for ENRAGED)",
        [_c("Entities/Unit/Unit.cpp", 6125)])
    put("save-logout", "not saved (rebuilt on login)" if p else "saved if CanBeSaved (track E)",
        "Aura::CanBeSaved: IsPassive -> false", [_c("Spells/Auras/SpellAuras.cpp", 1170)])
    put("evade-creature-owner", True, "RemoveAurasOnEvade has no passive exemption (player-owned units exempt entirely)",
        [_c("Entities/Unit/Unit.cpp", 4442)])
    return out


def spellmod_recalculation(f: Facts) -> dict[str, Any]:
    """Whether a later SpellMod (points) change on the caster recalculates this aura's amounts.

    Mirrors: AuraEffect::ApplySpellMod SpellAuraEffects.cpp:1187-1245 (recalculation loop 1227-1245): only auras with
    (IsPassive || IsPermanent || IsUpdatingTemporaryAuraValuesBySpellMod) cast by the target itself.
    """
    md = max_duration(f)
    if f.is_passive:
        timing = "explicit-recalculation"
    elif md["kind"] == "permanent":
        timing = "explicit-recalculation"
    else:
        timing = "application-snapshot"
    return {"input_timing": timing, "requires": "caster == target (aura->GetCasterGUID() == target GUID)",
            "exception": "SpellInfo::IsUpdatingTemporaryAuraValuesBySpellMod literal 384669 (SpellInfo.cpp:2021-2029)",
            "coords": [_c("Spells/Auras/SpellAuraEffects.cpp", 1233)]}


# ---------------------------------------------------------------------------
# acquisition
# ---------------------------------------------------------------------------

ROUTES: dict[str, dict[str, Any]] = {
    "class-trait": {
        "apply": "Player::ApplyTraitEntry -> LearnSpell(trait rank) -> AddSpell -> (passive) HandlePassiveSpellLearn -> CastSpell",
        "remove": "ApplyTraitEntry(apply=false) -> RemoveSpell -> RemoveOwnedAura(id, self)",
        "rank_change": "ApplyTraitEntry(false) then ApplyTraitEntry(true): remove + recreate (new object)",
        "amount": "trait rank -> TraitDefinitionEffectPoints curve in SpellEffectInfo::CalcValue",
        "coords": [_c("Entities/Player/Player.cpp", 29391), _c("Entities/Player/Player.cpp", 29266),
                   _c("Entities/Player/Player.cpp", 3195), _c("Spells/SpellInfo.cpp", 534)]},
    "spec-spell": {
        "apply": "LearnSpecializationSpells -> LearnSpell -> AddSpell -> HandlePassiveSpellLearn -> CastSpell",
        "remove": "RemoveSpecializationSpells -> RemoveSpell (mastery: RemoveAurasDueToSpell, any caster)",
        "rank_change": None,
        "coords": [_c("Entities/Player/Player.cpp", 30631), _c("Entities/Player/Player.cpp", 30649),
                   _c("Entities/Player/Player.cpp", 30668)]},
    "current-gear:on-equip": {
        "apply": "ApplyItemEquipSpell -> ApplyEquipSpell -> CastSpell(this, id, item) (passive or not)",
        "remove": "ApplyEquipSpell(apply=false) -> RemoveAurasDueToItemSpell(id, item GUID)",
        "coords": [_c("Entities/Player/Player.cpp", 8315), _c("Entities/Player/Player.cpp", 8368),
                   _c("Entities/Player/Player.cpp", 8391)]},
    "current-set": {
        "apply": "AddItemsSetItem (threshold/spec/subtree gates) -> ApplyEquipSpell(spell, nullptr)",
        "remove": "RemoveItemsSetItem -> ApplyEquipSpell(false) -> RemoveAurasDueToSpell(id) -- ANY caster",
        "coords": ["src/server/game/Entities/Item/Item.cpp:164", "src/server/game/Entities/Item/Item.cpp:209",
                   _c("Entities/Player/Player.cpp", 8393)]},
    "enchant:equip-spell": {
        "apply": "ApplyEnchantment ITEM_ENCHANTMENT_TYPE_EQUIP_SPELL -> CastSpell(this, id, item)",
        "remove": "RemoveAurasDueToItemSpell(id, item GUID)",
        "coords": [_c("Entities/Player/Player.cpp", 13487), _c("Entities/Player/Player.cpp", 13489)]},
}
NOT_ACQUISITION = {
    "current-gear:on-use": "ItemEffect TriggerType != ON_EQUIP: cast on use / proc, not an acquisition aura",
    "enchant:combat-spell": "ITEM_ENCHANTMENT_TYPE_COMBAT_SPELL: cast by the enchant proc, not held",
    "enchant:use-spell": "ITEM_ENCHANTMENT_TYPE_USE_SPELL: cast on use",
    "class-skill": "extended scope only",
}


def _route_kinds(roots_detail: list[dict]) -> list[str]:
    kinds = []
    for d in roots_detail:
        k = d["kind"]
        if k == "current-gear":
            kinds.append("current-gear:on-equip" if d.get("trigger_type") == ITEM_SPELLTRIGGER_ON_EQUIP
                         else "current-gear:on-use")
        elif k in ("current-gem", "current-enchant"):
            t = d.get("enchant_effect_type")
            kinds.append({ITEM_ENCHANTMENT_TYPE_EQUIP_SPELL: "enchant:equip-spell",
                          ITEM_ENCHANTMENT_TYPE_COMBAT_SPELL: "enchant:combat-spell",
                          ITEM_ENCHANTMENT_TYPE_USE_SPELL: "enchant:use-spell"}.get(t, f"enchant:type-{t}"))
        else:
            kinds.append(k)
    return sorted(set(kinds))


def learn_applies(f: Facts) -> dict[str, Any]:
    """Does learning the spell (trait/spec/spellbook route) put an aura on the player?

    Mirrors: Player::AddSpell Player.cpp:2911-2925 (talent LEARN_SPELL / IsPassive ->
    HandlePassiveSpellLearn / SKILL_STEP / ATTR1_CAST_WHEN_LEARNED) and
    Player::HandlePassiveSpellLearn Player.cpp:3079-3104.
    """
    if f.is_passive:
        gates = []
        if f.stances:
            gates.append("Stances: cast only in a matching form (or caster form with ATTR2_ALLOW_WHILE_NOT_SHAPESHIFTED); "
                         "later form entry casts it (HandleShapeshiftBoosts SpellAuraEffects.cpp:1582-1597)")
        if f.equipped_item_class >= 0 and f.aura_effects:
            gates.append("EquippedItemClass: AddAura (not CastSpell) only if !HasAura && HasItemFitToSpellRequirements; "
                         "re-added on equip by ApplyItemDependentAuras (Player.cpp:8280-8296)")
        if f.caster_aura_state:
            gates.append(f"CasterAuraState {f.caster_aura_state}: cast only while the state is set; "
                         "ModifyAuraState casts on gain / removes on loss (Unit.cpp:6094-6126)")
        return {"applies": True, "via": "HandlePassiveSpellLearn", "gates": gates,
                "coords": [_c("Entities/Player/Player.cpp", 2920), _c("Entities/Player/Player.cpp", 3079)]}
    if f.has_attr(ATTR1_CAST_WHEN_LEARNED):
        return {"applies": True, "via": "SPELL_ATTR1_CAST_WHEN_LEARNED -> CastSpell", "gates": [],
                "coords": [_c("Entities/Player/Player.cpp", 2924), _c("Entities/Player/Player.cpp", 2927)]}
    return {"applies": False, "via": "not cast on learn (ability)", "gates": [],
            "coords": [_c("Entities/Player/Player.cpp", 2912)]}


def acquisition(ctx, f: Facts) -> dict[str, Any]:
    scope = ctx.scope
    detail = scope.roots.detail.get(f.spell, [])
    kinds = _route_kinds(detail)
    parent = scope.parent.get(f.spell) if f.spell in scope.reach else None
    routes = []
    for k in kinds:
        if k in ROUTES:
            routes.append({"route": k, **ROUTES[k]})
        else:
            routes.append({"route": k, "not_acquisition": NOT_ACQUISITION.get(k, "unmapped root kind")})
    return {
        "player_reach": f.spell in scope.reach,
        "root_routes": routes,
        "reached_from": ({"spell": parent[0], "edge": parent[1], "name": ctx.name(parent[0])} if parent else None),
        "learn": learn_applies(f),
    }


def reacquisition(f: Facts, route_kinds: list[str]) -> list[dict[str, Any]]:
    """Events that (re)create the aura after it was lost.  Nothing else does."""
    out = [{"event": "login", "why": "passives are never saved; AddSpell(loading) re-casts them",
            "coords": [_c("Spells/Auras/SpellAuras.cpp", 1170), _c("Entities/Player/Player.cpp", 2920)]}] if f.is_passive else []
    if any(k in ("class-trait", "spec-spell") for k in route_kinds):
        out.append({"event": "relearn / trait edit / spec change",
                    "coords": [_c("Entities/Player/Player.cpp", 29247), _c("Entities/Player/Player.cpp", 30631)]})
    if any(k.startswith("current-gear:on-equip") or k.startswith("enchant:equip") or k == "current-set" for k in route_kinds):
        out.append({"event": "equip / set threshold / form change re-check (UpdateEquipSpellsAtFormChange)",
                    "coords": [_c("Entities/Player/Player.cpp", 8397)]})
    if f.is_passive and f.stances:
        out.append({"event": "entering a matching form", "coords": [_c("Spells/Auras/SpellAuraEffects.cpp", 1597)]})
    if f.is_passive and f.caster_aura_state:
        out.append({"event": f"aura state {f.caster_aura_state} set", "coords": [_c("Entities/Unit/Unit.cpp", 6097)]})
    if f.is_passive and f.equipped_item_class >= 0:
        out.append({"event": "equipping a fitting item (ApplyItemDependentAuras)", "coords": [_c("Entities/Player/Player.cpp", 8294)]})
    return out


# ---------------------------------------------------------------------------
# mutable state carried by a passive
# ---------------------------------------------------------------------------

def mutable_state(f: Facts, writers: dict[str, list[int]] | None = None) -> list[dict[str, Any]]:
    """Every mutable-state carrier present on the spell (authored or overlay).  Empty == immutable
    *as far as DB2 + overlay show*; scripts are listed as carriers, never assumed inert."""
    out = []
    md = max_duration(f)
    if md["kind"] == "finite":
        out.append({"carrier": "finite-duration", "value": md["ms"], "evidence": "db2-fact"})
    if f.stack_cap > 1:
        w = (writers or {}).get("modify_stacks", [])
        out.append({"carrier": "stack-capacity", "value": f.stack_cap, "writers": w,
                    "note": "initial stacks are 1 (AuraCreateInfo::StackAmount); passive re-application never stacks "
                            "(multislot) -- only MODIFY_AURA_STACKS / SetAuraStack / scripts mutate it",
                    "evidence": "db2-fact" if not w else ["db2-fact", "trinity-consumer"]})
    if f.proc_charges:
        out.append({"carrier": "proc-charges", "value": f.proc_charges,
                    "source": "spell_proc" if f.spell_proc else "db2",
                    "evidence": "world-db-fact" if f.spell_proc else "db2-fact"})
    if f.proc_entry is not None:
        out.append({"carrier": "effective-proc-entry", "value": f.proc_entry,
                    "note": "per-aura proc state: m_procCooldown (AddProcCooldown SpellAuras.cpp:1777), "
                            "m_lastProcAttemptTime/SuccessTime (RPPM), charges",
                    "evidence": ["trinity-consumer"] + (["world-db-fact"] if f.proc_entry["origin"] == "spell_proc" else ["db2-fact"])})
    elif f.proc_type_mask != (0, 0):
        out.append({"carrier": "inert-proc-signal", "value": list(f.proc_type_mask),
                    "note": "DB2 ProcTypeMask set but LoadSpellProcs generates no entry -> no per-aura proc state in Trinity",
                    "evidence": ["db2-fact", "trinity-consumer"]})
    if f.periodic:
        out.append({"carrier": "periodic-timer", "value": f.periodic,
                    "note": "passive periodics tick while duration < 0 (AuraEffect::Update SpellAuraEffects.cpp:1252)",
                    "evidence": "db2-fact"})
    if f.scripts:
        out.append({"carrier": "script-state", "value": f.scripts, "evidence": "script-consumer"})
    if f.aura_interrupt_flags != (0, 0):
        out.append({"carrier": "interruptible", "value": list(f.aura_interrupt_flags), "evidence": "db2-fact"})
    rem = (writers or {}).get("remove_aura", [])
    if rem:
        out.append({"carrier": "explicit-removal-writers", "value": rem, "evidence": "db2-fact"})
    return out


def writers_index(data) -> dict[str, dict[int, list[int]]]:
    """Spells whose effects mutate another spell's aura by id (EffectTriggerSpell).

    Mirrors: Spell::EffectModifyAuraStacks SpellEffects.cpp:6131 (GetAura(TriggerSpell));
    Spell::EffectRemoveAura SpellEffects.cpp:5160 (RemoveAurasDueToSpell(TriggerSpell)).
    """
    out: dict[str, dict[int, list[int]]] = {"modify_stacks": defaultdict(list), "remove_aura": defaultdict(list)}
    for (spell, diff), effs in data.table("SpellEffect").items():
        if diff != 0:
            continue
        for e in effs.values():
            t = e["EffectTriggerSpell"]
            if not t:
                continue
            if e["Effect"] == SPELL_EFFECT_MODIFY_AURA_STACKS:
                out["modify_stacks"][t].append(spell)
            elif e["Effect"] in (SPELL_EFFECT_REMOVE_AURA, SPELL_EFFECT_REMOVE_AURA_2):
                out["remove_aura"][t].append(spell)
    return {k: {t: sorted(set(v)) for t, v in d.items()} for k, d in out.items()}


def profile(ctx, spell: int, writers: dict | None = None) -> dict[str, Any]:
    """``aura_lifecycle.py passive <spell>``."""
    f = facts(ctx, spell)
    if not f.aura_effects:
        raise FailClosed(f"{spell}: no aura effect -- not an aura provider")
    w = writers if writers is not None else writers_index(ctx.data)
    mine = {k: v.get(spell, []) for k, v in w.items()}
    acq = acquisition(ctx, f)
    route_kinds = [r["route"] for r in acq["root_routes"]]
    rp = reapplication(passive=f.is_passive, same_caster=True, same_spell=True, incoming_cast_item=False,
                       existing_cast_item=False, has_apply_aura_effect=f.has_apply_aura, is_area=f.area) \
        if f.is_passive else {"outcome": "not-passive", "why": "active re-application: tracks A/B/C"}
    rp_item = None
    if f.is_passive and not f.area:
        rp_item = reapplication(passive=True, same_caster=True, same_spell=True, incoming_cast_item=True,
                                existing_cast_item=True, has_apply_aura_effect=f.has_apply_aura)
    drift = None
    if ctx.drift is not None and ctx.drift.row("SpellMisc", spell) is not None:
        try:
            df = facts(ctx, spell, data=ctx.drift)
            drift = {"passive": df.is_passive, "duration_index": df.duration_index, "stack_cap": df.stack_cap,
                     "changed": [k for k, a, b in (("passive", f.is_passive, df.is_passive),
                                                   ("duration_index", f.duration_index, df.duration_index),
                                                   ("stack_cap", f.stack_cap, df.stack_cap),
                                                   ("proc_charges_db2", f.proc_charges_db2, df.proc_charges_db2))
                                 if a != b]}
        except FailClosed as exc:
            drift = {"fail_closed": str(exc)}
    return {
        "spell": spell, "name": f.name, "build_skew": f.build_skew,
        "passive": f.passive,
        "aura_effects": [{"index": e["EffectIndex"], "effect": e["Effect"], "aura": e["EffectAura"]} for e in f.aura_effects],
        "max_duration": max_duration(f),
        "reapplication_same_caster_no_item": rp,
        "reapplication_same_caster_item_sourced": rp_item,
        "passive_stackable_with_ranks": f.is_passive and not f.has_apply_aura,
        "mutable_state": mutable_state(f, mine),
        "removal": removal_policy(f),
        "spellmod_recalculation": spellmod_recalculation(f),
        "acquisition": acq,
        "reacquisition": reacquisition(f, route_kinds),
        "drift": drift,
        "evidence": ["db2-fact", "trinity-consumer"] + (["world-db-fact"] if f.spell_proc else [])
                    + (["script-consumer"] if f.scripts else []) + (["build-skew"] if f.build_skew else []),
    }


# ---------------------------------------------------------------------------
# census
# ---------------------------------------------------------------------------

CENSUS_KEYS = (
    "passive", "passive_db2", "passive_by_correction_only", "active",
    "passive_finite_duration", "passive_zero_duration_entry", "passive_stack_cap_gt1", "passive_proc_charges",
    "passive_effective_proc_entry", "passive_inert_proc_signal", "passive_spell_proc_row", "passive_periodic", "passive_scripted",
    "passive_stances", "passive_caster_aura_state", "passive_equipped_item", "passive_interruptible",
    "passive_stackable_with_ranks", "passive_area", "passive_death_persistent_attr",
    "passive_modify_stacks_target", "passive_remove_aura_target", "passive_any_mutable",
    "active_cast_when_learned", "active_on_equip_route", "passive_build_skew", "passive_mutable_build_skew",
)


def census(ctx) -> dict[str, Any]:
    pops = populations(ctx)
    w = writers_index(ctx.data)
    rows: dict[int, dict[str, bool]] = {}
    fails: list[dict] = []
    for spell in sorted(pops["all"]):
        try:
            f = facts(ctx, spell)
        except FailClosed as exc:
            fails.append({"spell": spell, "reason": str(exc)})
            continue
        p = f.is_passive
        md = max_duration(f)
        route_kinds = _route_kinds(ctx.scope.roots.detail.get(spell, []))
        flags = {
            "passive": p,
            "passive_db2": f.passive["db2_passive"],
            "passive_by_correction_only": p and not f.passive["db2_passive"],
            "active": not p,
            "passive_finite_duration": p and md["kind"] == "finite",
            "passive_zero_duration_entry": p and md["kind"] == "zero",
            "passive_stack_cap_gt1": p and f.stack_cap > 1,
            "passive_proc_charges": p and f.proc_charges > 0,
            "passive_effective_proc_entry": p and f.proc_entry is not None,
            "passive_inert_proc_signal": p and f.proc_entry is None and f.proc_type_mask != (0, 0),
            "passive_spell_proc_row": p and f.spell_proc is not None,
            "passive_periodic": p and bool(f.periodic),
            "passive_scripted": p and bool(f.scripts),
            "passive_stances": p and bool(f.stances),
            "passive_caster_aura_state": p and bool(f.caster_aura_state),
            "passive_equipped_item": p and f.equipped_item_class >= 0,
            "passive_interruptible": p and f.aura_interrupt_flags != (0, 0),
            "passive_stackable_with_ranks": p and not f.has_apply_aura,
            "passive_area": p and f.area,
            "passive_death_persistent_attr": p and f.has_attr(ATTR3_ALLOW_AURA_WHILE_DEAD),
            "passive_modify_stacks_target": p and spell in w["modify_stacks"],
            "passive_remove_aura_target": p and spell in w["remove_aura"],
            "active_cast_when_learned": (not p) and f.has_attr(ATTR1_CAST_WHEN_LEARNED),
            "active_on_equip_route": (not p) and any(k in ("current-gear:on-equip", "enchant:equip-spell", "current-set")
                                                      for k in route_kinds),
        }
        flags["passive_build_skew"] = p and f.build_skew
        flags["passive_any_mutable"] = p and any(flags[k] for k in (
            "passive_finite_duration", "passive_stack_cap_gt1", "passive_proc_charges", "passive_effective_proc_entry",
            "passive_periodic", "passive_scripted", "passive_interruptible", "passive_modify_stacks_target",
            "passive_remove_aura_target"))
        flags["passive_mutable_build_skew"] = flags["passive_any_mutable"] and f.build_skew
        flags["_interrupts"] = interrupt_flag_names(f.aura_interrupt_flags) if p else []
        flags["_routes"] = route_kinds
        par = ctx.scope.parent.get(spell) if spell in ctx.scope.reach else None
        flags["_edge"] = par[1].split(":")[0] if par else None
        rows[spell] = flags
    counts = {}
    witnesses = {}
    interrupt: dict[str, dict[str, dict[str, Any]]] = {}
    routes: dict[str, dict[str, Any]] = {}
    for pop, members in sorted(pops.items()):
        c = Counter()
        wit: dict[str, list[int]] = defaultdict(list)
        c["providers"] = len(members)
        for spell in sorted(members):
            flags = rows.get(spell)
            if flags is None:
                c["fail_closed"] += 1
                continue
            for k in CENSUS_KEYS:
                if flags[k]:
                    c[k] += 1
                    if len(wit[k]) < 8:
                        wit[k].append(spell)
        counts[pop] = {k: c.get(k, 0) for k in ("providers", "fail_closed") + CENSUS_KEYS}
        ib: dict[str, dict[str, Any]] = {}
        rb: Counter = Counter()
        rw: dict[str, list[int]] = defaultdict(list)
        for spell in sorted(members):
            flags = rows.get(spell)
            if not flags or not flags["passive"]:
                continue
            for name in flags["_interrupts"]:
                e = ib.setdefault(name, {"count": 0, "witnesses": [], "consumer": next(
                    (v[1] for v in INTERRUPT_FLAGS.values() if v[0] == name), None),
                    "net_effect_note": INTERRUPT_NET.get(name, ("", "no re-acquisition on the firing path"))[1],
                    "net_effect": {}})
                e["count"] += 1
                net = interrupt_net_effect(name, flags["_routes"])
                e["net_effect"][net] = e["net_effect"].get(net, 0) + 1
                if len(e["witnesses"]) < 8:
                    e["witnesses"].append(spell)
            for k in flags["_routes"] or ["reached-only:" + (flags["_edge"] or "none")]:
                rb[k] += 1
                if len(rw[k]) < 6:
                    rw[k].append(spell)
        interrupt[pop] = dict(sorted(ib.items()))
        routes[pop] = {k: {"count": rb[k], "witnesses": rw[k]} for k in sorted(rb)}
        witnesses[pop] = {k: v for k, v in sorted(wit.items())}
    return {"counts": counts, "witnesses": witnesses, "passive_interrupt_flags": interrupt,
            "passive_acquisition_routes": routes, "fail_closed": fails[:50], "fail_closed_total": len(fails),
            "rows": rows}


def drift_census(ctx, spells: list[int]) -> dict[str, Any]:
    """Build drift (69497 -> 69814) of passive-lifecycle fields over ``spells``."""
    if ctx.drift is None:
        return {"available": False}
    changed = []
    absent = 0
    for s in spells:
        if ctx.drift.row("SpellMisc", s) is None:
            absent += 1
            continue
        try:
            a, b = facts(ctx, s), facts(ctx, s, data=ctx.drift)
        except FailClosed:
            absent += 1
            continue
        diff = [k for k, x, y in (("passive", a.is_passive, b.is_passive),
                                  ("duration_index", a.duration_index, b.duration_index),
                                  ("stack_cap", a.stack_cap, b.stack_cap),
                                  ("proc_charges_db2", a.proc_charges_db2, b.proc_charges_db2),
                                  ("proc_type_mask", a.proc_type_mask, b.proc_type_mask)) if x != y]
        if diff:
            changed.append({"spell": s, "fields": diff})
    return {"available": True, "compared": len(spells) - absent, "absent_in_drift": absent,
            "changed": changed, "absent_tables": sorted(ctx.drift.absent)}


# ---------------------------------------------------------------------------
# synthetic timelines
# ---------------------------------------------------------------------------

@dataclass
class AuraObj:
    obj: int
    spell: int
    passive: bool
    caster: str
    cast_item: str | None
    stacks: int = 1
    charges: int = 0
    max_ms: int = -1
    remaining_ms: int = -1
    amount: int = 0
    proc_cooldown_until: int = 0


class Sim:
    """A tiny owner-side aura list.  Each method mirrors one Trinity path and logs the step.

    Not a runtime: only the branches the passive/active questions need, fail-closed elsewhere.
    """

    def __init__(self) -> None:
        self.auras: list[AuraObj] = []
        self.log: list[dict[str, Any]] = []
        self._next = 1
        self.now = 0

    def _state(self) -> list[dict[str, Any]]:
        return [dict(a.__dict__) for a in sorted(self.auras, key=lambda a: a.obj)]

    def _emit(self, event: str, **kw) -> None:
        self.log.append({"t_ms": self.now, "event": event, **kw, "state": self._state()})

    def advance(self, to_ms: int) -> None:
        """Mirrors: Unit::_UpdateSpells Unit.cpp:2957-2999 -> Aura::UpdateOwner -> duration;
        IsExpired -> RemoveOwnedAura(BY_EXPIRE)."""
        if to_ms < self.now:
            raise FailClosed("time goes forward")
        dt = to_ms - self.now
        self.now = to_ms
        for a in list(self.auras):
            if a.max_ms == -1:
                continue
            a.remaining_ms = max(0, a.remaining_ms - dt)
            if a.remaining_ms == 0:
                self.auras.remove(a)
                self._emit("expire", obj=a.obj, remove_mode="AURA_REMOVE_BY_EXPIRE")

    def apply(self, spell: int, *, passive: bool, caster: str = "self", cast_item: str | None = None,
              max_ms: int = -1, charges: int = 0, amount: int = 0, has_apply_aura_effect: bool = True) -> int:
        """Aura::TryRefreshStackOrCreate -> _TryStackingOrRefreshingExistingAura / Create -> _AddAura."""
        existing = [a for a in self.auras if a.spell == spell]
        if not passive and existing:
            same = [a for a in existing if a.caster == caster]
            if not same:
                raise FailClosed("active, other caster: track A")
            a = same[0]
            # Mirrors Aura::ModStackAmount SpellAuras.cpp:1093-1127 with StackAmount 0 (non-stacking):
            # stackAmount clamps to 1, refresh=True -> RefreshTimers + SetCharges(CalcMaxCharges)
            a.remaining_ms = a.max_ms
            a.charges = charges
            self._emit("refresh", obj=a.obj, coords=[_c("Entities/Unit/Unit.cpp", 3440),
                                                     _c("Spells/Auras/SpellAuras.cpp", 1119)])
            return a.obj
        new = AuraObj(self._next, spell, passive, caster, cast_item, charges=charges, max_ms=max_ms,
                      remaining_ms=max_ms, amount=amount)
        self._next += 1
        self.auras.append(new)
        removed = []
        for old in existing:
            r = reapplication(passive=passive, same_caster=old.caster == caster, same_spell=True,
                              incoming_cast_item=cast_item is not None, existing_cast_item=old.cast_item is not None,
                              has_apply_aura_effect=has_apply_aura_effect)
            if r["outcome"] == "replace":
                self.auras.remove(old)
                removed.append(old.obj)
        self._emit("create", obj=new.obj, replaced=removed, remove_mode="AURA_REMOVE_BY_DEFAULT" if removed else None,
                   coords=[_c("Entities/Unit/Unit.cpp", 3395), _c("Entities/Unit/Unit.cpp", 3454)])
        return new.obj

    def mod_stacks(self, spell: int, delta: int, cap: int) -> None:
        """Spell::EffectModifyAuraStacks (MiscValue 0) -> Aura::ModStackAmount (cap = StackAmount)."""
        target = next((a for a in self.auras if a.spell == spell), None)
        if target is None:
            self._emit("modify-stacks-noop", spell=spell)
            return
        target.stacks = min(cap, target.stacks + delta) if delta > 0 else target.stacks + delta
        if target.stacks <= 0:
            self.auras.remove(target)
        self._emit("modify-stacks", obj=target.obj, coords=[_c("Spells/SpellEffects.cpp", 6138),
                                                            _c("Spells/Auras/SpellAuras.cpp", 1093)])

    def proc(self, obj: int, cooldown_ms: int = 0) -> None:
        """Aura proc consuming a charge: Aura::ModCharges(-1) SpellAuras.cpp:1017-1034; AddProcCooldown."""
        a = next(x for x in self.auras if x.obj == obj)
        a.proc_cooldown_until = self.now + cooldown_ms
        if a.charges:
            a.charges -= 1
            if a.charges <= 0:
                self.auras.remove(a)
                self._emit("charges-exhausted", obj=obj, coords=[_c("Spells/Auras/SpellAuras.cpp", 1027)])
                return
        self._emit("proc", obj=obj)

    def remove_spell(self, spell: int, caster: str = "self") -> None:
        """Player::RemoveSpell -> RemoveOwnedAura(spell_id, GetGUID()) Player.cpp:3195."""
        gone = [a.obj for a in self.auras if a.spell == spell and a.caster == caster]
        self.auras = [a for a in self.auras if not (a.spell == spell and a.caster == caster)]
        self._emit("unlearn", removed=gone, remove_mode="AURA_REMOVE_BY_DEFAULT",
                   coords=[_c("Entities/Player/Player.cpp", 3195)])

    def remove_item_spell(self, spell: int, item: str) -> None:
        """Unit::RemoveAurasDueToItemSpell Unit.cpp:4112-4124."""
        gone = [a.obj for a in self.auras if a.spell == spell and a.cast_item == item]
        self.auras = [a for a in self.auras if not (a.spell == spell and a.cast_item == item)]
        self._emit("unequip", item=item, removed=gone, coords=[_c("Entities/Unit/Unit.cpp", 4116)])

    def death(self, death_persistent: dict[int, bool] | None = None) -> None:
        """Unit::RemoveAllAurasOnDeath Unit.cpp:4472-4492."""
        dp = death_persistent or {}
        gone = [a.obj for a in self.auras if not a.passive and not dp.get(a.spell, False)]
        self.auras = [a for a in self.auras if a.obj not in gone]
        self._emit("death", removed=gone, remove_mode="AURA_REMOVE_BY_DEATH",
                   coords=[_c("Entities/Unit/Unit.cpp", 4479), _c("Entities/Unit/Unit.cpp", 4488)])

    def logout_login(self, relearn: list[dict[str, Any]]) -> None:
        """Save: Aura::CanBeSaved SpellAuras.cpp:1168-1204 (passive -> false; item permanent -> false).
        Load: _LoadSpells -> AddSpell(loading) -> HandlePassiveSpellLearn -> CastSpell (Player.cpp:2920)."""
        saved = [a for a in self.auras if not a.passive and not (a.cast_item and a.max_ms == -1)]
        dropped = [a.obj for a in self.auras if a not in saved]
        self.auras = saved
        self._emit("logout-save", dropped=dropped, coords=[_c("Spells/Auras/SpellAuras.cpp", 1170)])
        for spec in relearn:
            self.apply(**spec)

    def interrupt(self, flag: str, flagged: set[int]) -> None:
        """Unit::RemoveAurasWithInterruptFlags Unit.cpp:4240-4270 -- no passive exemption."""
        gone = [a.obj for a in self.auras if a.spell in flagged]
        self.auras = [a for a in self.auras if a.obj not in gone]
        self._emit(f"interrupt:{flag}", removed=gone, remove_mode="AURA_REMOVE_BY_INTERRUPT",
                   coords=[_c("Entities/Unit/Unit.cpp", 4255)])

    def spellmod_change(self, new_amount: int) -> None:
        """AuraEffect spell-mod recalculation SpellAuraEffects.cpp:1227-1245: passive or permanent own auras only."""
        touched = []
        for a in self.auras:
            if (a.passive or a.max_ms == -1) and a.caster == "self":
                a.amount = new_amount
                touched.append(a.obj)
        self._emit("spellmod-change", recalculated=touched, coords=[_c("Spells/Auras/SpellAuraEffects.cpp", 1233)])


def timelines() -> list[dict[str, Any]]:
    """Exact-ms timelines answering the passive/active questions; each names the model it rules out."""
    out = []

    s = Sim()
    s.apply(900001, passive=True, charges=0)
    s.advance(1000)
    s.mod_stacks(900001, +1, cap=2)
    s.advance(2000)
    s.apply(900001, passive=True)
    out.append({"id": "TL-G-01", "title": "passive re-application replaces the object (stacks lost)",
                "fixture": "passive P, CumulativeAura 2, cast by self with no cast item; MODIFY_AURA_STACKS +1 at 1000; second CastSpell of P at 2000 "
                           "(reachable paths: a trigger/script cast of P, HandleShapeshiftBoosts on a form switch when P's Stances cover both forms "
                           "[structural-inference]; NOT a relearn of a still-known spell -- AddSpell early-returns, Player.cpp:2751)",
                "rules_out": "refresh-in-place model (passive re-application keeps object/stacks like an active refresh)",
                "log": s.log})

    s = Sim()
    s.apply(900002, passive=False, max_ms=10000, charges=3)
    s.advance(1000)
    s.proc(1)
    s.advance(2000)
    s.apply(900002, passive=False, max_ms=10000, charges=3)
    out.append({"id": "TL-G-02", "title": "active non-stacking re-application refreshes the same object (contrast to TL-G-01)",
                "fixture": "active A, StackAmount 0, 10000 ms, 3 charges; proc at 1000; recast at 2000",
                "rules_out": "'every re-application creates a new object' model",
                "log": s.log})

    s = Sim()
    s.apply(900003, passive=True, cast_item="item:1")
    s.apply(900003, passive=True, cast_item="item:2")
    s.advance(500)
    s.remove_item_spell(900003, "item:1")
    out.append({"id": "TL-G-03", "title": "item-sourced passive: two items with the same equip spell coexist; unequip removes one",
                "fixture": "passive equip spell E on two equipped items",
                "rules_out": "'one aura per SpellId per caster' model for passives",
                "log": s.log})

    s = Sim()
    s.apply(900004, passive=True, max_ms=15000)
    s.advance(15000)
    s.advance(60000)
    s.logout_login([{"spell": 900004, "passive": True, "max_ms": 15000}])
    out.append({"id": "TL-G-04", "title": "passive with a DurationEntry expires and is not re-cast until an acquisition event",
                "fixture": "passive P with SpellDuration 15000 ms learned at 0; login at 60000",
                "rules_out": "'passive == permanent' model (SpellAuras.cpp:925 only forces -1 without DurationEntry)",
                "log": s.log})

    s = Sim()
    s.apply(900005, passive=True, amount=10)
    s.apply(900006, passive=False, max_ms=30000, amount=10)
    s.advance(1000)
    s.spellmod_change(new_amount=12)
    s.advance(2000)
    s.death()
    out.append({"id": "TL-G-05", "title": "spell-mod change recalculates passive amounts only; death removes the active only",
                "fixture": "own passive P (amount 10) and own temporary active A (30000 ms, amount 10); points SpellMod at 1000; death at 2000",
                "rules_out": "'all own auras recalc on spell-mod change' and 'death clears all auras' models",
                "log": s.log})

    s = Sim()
    s.apply(900007, passive=True, charges=1)
    s.advance(700)
    s.proc(1)
    s.advance(60000)
    s.remove_spell(900007)
    s.apply(900007, passive=True, charges=1)
    out.append({"id": "TL-G-06", "title": "passive with proc charges is consumed and stays gone until relearn",
                "fixture": "passive P with ProcCharges 1; proc at 700; trait rank change (remove+learn) at 60700",
                "rules_out": "'passive is re-applied automatically after loss' model",
                "log": s.log})

    s = Sim()
    s.apply(900008, passive=True)
    s.advance(3000)
    s.proc(1, cooldown_ms=10000)
    s.advance(4000)
    s.remove_spell(900008)
    s.apply(900008, passive=True)
    out.append({"id": "TL-G-07", "title": "trait rank change = remove + recreate: proc cooldown state is reset",
                "fixture": "passive P with proc cooldown 10000 ms; proc at 3000; rank 1->2 at 4000 (Player.cpp:29266-29268)",
                "rules_out": "'rank change mutates the existing aura in place' model",
                "log": s.log})
    s = Sim()
    s.apply(900009, passive=True)   # trait-route passive, ChangeSpec flag
    s.apply(900010, passive=True)   # passive, ChangeGlyph flag
    s.advance(1000)
    s.interrupt("ChangeSpec", {900009})
    s.remove_spell(900009)          # ApplyTraitConfig(false) Player.cpp:28885
    s.apply(900009, passive=True)   # ApplyTraitConfig(true) Player.cpp:28957
    s.advance(2000)
    s.interrupt("ChangeGlyph", {900010})
    s.advance(60000)
    out.append({"id": "TL-G-08", "title": "interrupt-flagged passives: spec change resets a trait passive, glyph change loses one",
                "fixture": "trait passive P1 with ChangeSpec; passive P2 with ChangeGlyph; spec change at 1000 (same spec's traits reapplied); glyph applied at 2000",
                "rules_out": "'passives are immune to aura interrupt flags' and 'the firing path always restores them' models",
                "log": s.log})
    return out
