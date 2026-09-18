"""Track E -- death and source/target lifetime.

Death is treated as a *question*, not as natural expiry.  In pinned Trinity the
two differ observably (mode, final tick, on-expire handlers, linked spells,
which auras are affected, and what ran before the death sweep):

* :func:`death_order`  -- the ordered steps of ``Unit::Kill`` -> ``setDeathState``
  for one dying unit, over an explicit fixture of its auras / casts / combat;
* :data:`TICK_GATES`    -- per periodic aura type, what a tick reads about the
  caster (``Aura::GetCaster`` is a live ``ObjectAccessor`` lookup: a dead caster
  is still found, a despawned one is ``nullptr``);
* :data:`LIFETIME_MATRIX` -- holder / caster / owner / controlled-unit /
  source events x aura shapes -> persists / removed (mode) / keeps ticking;
* :func:`simulate`      -- exact-millisecond timelines of one periodic aura on
  one holder under updates, death, caster death/despawn and dispel, mirroring
  ``Unit::_UpdateSpells`` ordering (duration, then ticks, then expiry sweep).

The periodic tick rule is the minimum needed to observe removal (track D owns
periodic semantics); same-timestamp ordering between an external event and the
holder's update is an explicit parameter (track I owns it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from procs.enums import AREA_AURA_EFFECTS

from . import FailClosed
from .removal import attrs_of, coord, death_class, has

# ---------------------------------------------------------------------------
# Tick gates per periodic aura type
# ---------------------------------------------------------------------------

# aura type -> (handler, target-alive gate, caster requirement, caster-derived inputs, anchor)
TICK_GATES: dict[int, dict[str, Any]] = {
    3: {"name": "PERIODIC_DAMAGE", "target_alive": True, "caster": "optional",
        "caster_inputs": "SpellDamageBonusDone only if caster; crit chance 0 without caster (CalcPeriodicCritChance)",
        "anchor": "tick_damage"},
    89: {"name": "PERIODIC_DAMAGE_PERCENT", "target_alive": True, "caster": "optional",
         "caster_inputs": "target max-health based; crit only with caster", "anchor": "tick_damage"},
    70: {"name": "PERIODIC_WEAPON_PERCENT_DAMAGE", "target_alive": True, "caster": "required-unchecked",
         "caster_inputs": "caster->CalculateDamage dereferenced without null check -- latent: raw 70 is not in "
                          "CalculatePeriodic's periodic list so it never ticks unless a script sets it periodic",
         "anchor": "tick_weapon_pct_null"},
    53: {"name": "PERIODIC_LEECH", "target_alive": True, "caster": "optional",
         "caster_inputs": "damage part ticks without caster; the heal-back part requires caster alive",
         "anchor": "tick_leech_caster_heal"},
    62: {"name": "PERIODIC_HEALTH_FUNNEL", "target_alive": True, "caster": "alive",
         "caster_inputs": "no-op tick when caster absent or dead (tick still counted)", "anchor": "tick_funnel"},
    64: {"name": "PERIODIC_MANA_LEECH", "target_alive": True, "caster": "alive",
         "caster_inputs": "no-op tick when caster absent or dead (tick still counted)", "anchor": "tick_mana_leech"},
    162: {"name": "POWER_BURN", "target_alive": True, "caster": "present",
          "caster_inputs": "no-op when caster absent; dead caster still burns", "anchor": "tick_power_burn"},
    8: {"name": "PERIODIC_HEAL", "target_alive": True, "caster": "optional",
        "caster_inputs": "healing bonus only with caster", "anchor": "tick_heal"},
    20: {"name": "OBS_MOD_HEALTH", "target_alive": True, "caster": "optional", "caster_inputs": "as PERIODIC_HEAL",
         "anchor": "tick_heal"},
    24: {"name": "PERIODIC_ENERGIZE", "target_alive": True, "caster": "optional", "caster_inputs": "none gated",
         "anchor": "tick_energize"},
    21: {"name": "OBS_MOD_POWER", "target_alive": True, "caster": "optional", "caster_inputs": "none gated",
         "anchor": "tick_obs_power"},
    23: {"name": "PERIODIC_TRIGGER_SPELL", "target_alive": False, "caster": "depends",
         "caster_inputs": "trigger caster = aura caster if NeedsToBeTriggeredByCaster else target; absent caster -> no cast; "
                          "whether a dead caster's triggered cast succeeds is not established here",
         "anchor": "tick_trigger"},
    227: {"name": "PERIODIC_TRIGGER_SPELL_WITH_VALUE", "target_alive": False, "caster": "depends",
          "caster_inputs": "as PERIODIC_TRIGGER_SPELL", "anchor": "tick_trigger"},
    226: {"name": "PERIODIC_DUMMY", "target_alive": False, "caster": "script", "caster_inputs": "script-controlled",
          "anchor": "tick_trigger"},
}


def tick_effective(aura_type: int, caster: str, target_alive: bool) -> bool:
    """Whether a counted tick has an effect.

    Mirrors: SpellAuraEffects.cpp:5632-5634 (damage: ``!target->IsAlive()`` return),
    :5865 (funnel), :5955 (mana leech), :6088 (power burn).  ``caster`` is
    ``alive`` | ``dead`` | ``absent`` (``GetCaster()`` nullptr).  The tick counter
    (``_ticksDone``) advances regardless (SpellAuraEffects.cpp:1258-1275).
    """
    gate = TICK_GATES.get(aura_type)
    if gate is None:
        raise FailClosed(f"aura type {aura_type}: tick gate not modelled")
    if gate["target_alive"] and not target_alive:
        return False
    req = gate["caster"]
    if req == "alive":
        return caster == "alive"
    if req == "present":
        return caster != "absent"
    if req in ("required-unchecked",):
        if caster == "absent":
            raise FailClosed(f"aura type {aura_type}: null caster dereference (Trinity would crash)")
        return True
    if req in ("depends", "script"):
        raise FailClosed(f"aura type {aura_type}: tick effect is {req}-controlled")
    return True


# ---------------------------------------------------------------------------
# Death order
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HeldAura:
    """An aura applied on (and possibly owned by) the dying unit, or owned by it and applied elsewhere."""
    key: str
    passive: bool = False
    death_persistent: bool = False
    leaving_combat_interrupt: bool = False
    owned_by_dying: bool = True         # unit-owned aura (holder == owner) vs applied from another owner
    applied_on_dying: bool = True       # False: owned by the dying unit, applied only to others (area source)
    channel_of_dying: bool = False      # aura of a spell the dying unit is channelling (on some target)
    recipients: tuple[str, ...] = ()    # other units holding applications of this (owned) aura
    combat_hook: bool = False           # OnEnterLeaveCombat AuraScript hook


@dataclass
class DeathFixture:
    auras: list[HeldAura]
    pve_combat_only: bool = True        # all combat references PvE -> AtExitCombat runs synchronously
    is_player: bool = False
    has_pet: bool = False
    has_summons: bool = False
    killed_by_damage: bool = True       # Unit::Kill path (PROC_FLAG_DEATH procs first)
    in_vehicle: bool = False            # passenger: vehicle-owned CONTROL_VEHICLE aura cast by the dying unit
    charmed_auras: tuple[str, ...] = ()  # charm/possess auras the dying unit holds on OTHER units
    totem_auras: tuple[str, ...] = ()   # auras its totems cast on it / its party
    casting_dynobj_auras: tuple[str, ...] = ()  # dynobj auras of the spell it is casting/channelling (all casts of that id)


def death_order(fx: DeathFixture) -> list[dict[str, Any]]:
    """Ordered observable steps when a unit dies.

    Mirrors: Unit.cpp:11400-11411 (``Unit::Kill``: PROC_FLAG_DEATH procs, then
    ``setDeathState(JUST_DIED)`` -- skipped when an ABSORB_OVERKILL aura sets
    ``skipSettingDeathState``, Unit.cpp:1044: no death sweep at all), Player.cpp:1157 (player: pet removed first),
    Unit.cpp:9160-9179 (``setDeathState``: CombatStop -> InterruptNonMeleeSpells ->
    ExitVehicle -> UnsummonAllTotems -> RemoveAllControlled -> RemoveAllAurasOnDeath),
    Unit.cpp:6011-6036 + CombatManager.cpp:400-416 (PvE combat end calls
    ``AtExitCombat`` synchronously: OnEnterLeaveCombat hooks, then
    LeavingCombat-interrupt auras removed with INTERRUPT), Unit.cpp:4472-4492
    (death sweep: applied first, then owned; skip passive / death-persistent).

    Returns one row per step with the auras it removes and the mode.
    """
    steps: list[dict[str, Any]] = []
    gone: set[str] = set()

    def step(name: str, anchor: str, removed: list[tuple[str, str]] | None = None, **kw: Any) -> None:
        for k, _ in removed or []:
            gone.add(k)
        steps.append({"step": name, "coords": coord(anchor), "removed": [{"aura": k, "mode": m} for k, m in removed or []], **kw})

    if fx.killed_by_damage:
        step("PROC_FLAG_DEATH procs on the victim (auras still present)", "kill_death_procs")
    if fx.is_player and fx.has_pet:
        step("Player::setDeathState: RemovePet (pet unsummoned; its auras cleaned up with DEFAULT)", "player_death_pet")
    step("m_deathState = JUST_DIED (IsAlive() false from here)", "death_state_gate")
    if fx.pve_combat_only:
        hooks = [a.key for a in fx.auras if a.combat_hook and a.applied_on_dying]
        rem = [(a.key, "INTERRUPT") for a in fx.auras if a.applied_on_dying and a.leaving_combat_interrupt]
        step("CombatStop -> EndAllPvECombat -> AtExitCombat: OnEnterLeaveCombat(false) hooks, LeavingCombat interrupt",
             "at_exit_combat_call", rem, hooks=hooks)
    else:
        step("CombatStop: PvP combat only suppressed -> no AtExitCombat here (UNKNOWN when it runs)", "pvp_combat_suppress")
    chan = [(a.key, "CANCEL") for a in fx.auras if a.channel_of_dying and a.key not in gone]
    chan += [(k, "DEFAULT") for k in fx.casting_dynobj_auras]
    step("InterruptNonMeleeSpells -> Spell::cancel: channel auras on all hit targets CANCEL (Spell.cpp:3646); every "
         "dynobject of the cancelled spell id DEFAULT (Spell.cpp:3665)", "death_interrupt_casts", chan)
    step("ExitVehicle: vehicle-owned CONTROL_VEHICLE aura cast by the dying passenger removed (Unit.cpp:12868)",
         "death_exit_vehicle", [("vehicle_control", "DEFAULT")] if fx.in_vehicle else [])
    step("UnsummonAllTotems: totem auras on owner and party removed (Totem.cpp:109-137)", "death_totems",
         [(k, "DEFAULT") for k in fx.totem_auras])
    step("RemoveAllControlled: charm auras on charmed units (RemoveCharmAuras) and summons UnSummon "
         "(ATTR1_IGNORE_OWNERS_DEATH is NYI)", "unsummon_controlled", [(k, "DEFAULT") for k in fx.charmed_auras])
    rem = []
    for a in fx.auras:        # applied loop (Unit.cpp:4476-4483)
        if a.key in gone or not a.applied_on_dying:
            continue
        if not a.passive and not a.death_persistent:
            rem.append((a.key, "DEATH"))
    for a in fx.auras:        # owned loop (Unit.cpp:4485-4491)
        if a.key in gone or not a.owned_by_dying or (a.key, "DEATH") in rem:
            continue
        if not a.passive and not a.death_persistent:
            rem.append((a.key, "DEATH"))
    recips = {a.key: list(a.recipients) for a in fx.auras if a.owned_by_dying and a.recipients and (a.key, "DEATH") in rem}
    step("RemoveAllAurasOnDeath", "death_sweep_call", rem, recipient_applications_removed_with_DEATH=recips)
    return steps


# ---------------------------------------------------------------------------
# Lifetime matrix
# ---------------------------------------------------------------------------

LIFETIME_MATRIX: tuple[dict[str, Any], ...] = (
    {"event": "holder death", "aura": "non-passive, not ATTR3_ALLOW_AURA_WHILE_DEAD", "outcome": "removed DEATH (after combat-exit / channel steps)",
     "ticks": "none further; no final tick", "anchors": ["death_predicate", "death_unapply"]},
    {"event": "holder death", "aura": "passive or ATTR3_ALLOW_AURA_WHILE_DEAD", "outcome": "persists on the corpse; duration keeps running; ends with EXPIRE -- "
     "or DEFAULT when a creature corpse is removed first (RemoveAllAuras, Creature.cpp:442 / despawn cleanup)",
     "ticks": "tick counter advances; damage/heal/energize/power ticks are no-ops on a dead target; PERIODIC_TRIGGER_SPELL(_WITH_VALUE) "
              "and PERIODIC_DUMMY ticks have no alive gate and ACT (cast on the corpse; a failed triggered cast ends the aura with EXPIRE, RM-47)",
     "anchors": ["death_predicate", "tick_damage_target_alive", "tick_trigger", "corpse_remove_all"]},
    {"event": "lethal damage absorbed by SCHOOL_ABSORB_OVERKILL (e.g. Spirit of Redemption)", "aura": "any",
     "outcome": "Kill runs KILL/DEATH procs but skips setDeathState: no combat-exit step, no death sweep, nothing removed with DEATH",
     "ticks": "unchanged", "anchors": ["overkill_skip_death", "kill_death_procs"]},
    {"event": "holder logout / pet dismissal", "aura": "Aura::CanBeSaved", "outcome": "saved, removed DEFAULT, rebuilt as NEW objects on login/summon; "
     "offline countdown only for non-positive or ATTR4_AURA_EXPIRES_OFFLINE auras; exhausted charges refilled",
     "ticks": "periodic phase after reload not established here (AL-U-E-21)", "anchors": ["can_be_saved", "pet_save_auras", "load_offline_countdown"]},
    {"event": "holder death", "aura": "surviving + ATTR7_DISABLE_AURA_WHILE_DEAD", "outcome": "Aura kept, applications unapplied DEFAULT at next target-map update (<=500 ms), re-applied after resurrect",
     "ticks": "no application -> no tick targets", "anchors": ["disable_while_dead", "targetmap_unapply"]},
    {"event": "holder death", "aura": "LeavingCombat interrupt flag, PvE combat", "outcome": "removed INTERRUPT before the death sweep (even if death-persistent)",
     "ticks": "none", "anchors": ["interrupt_leave_combat", "death_combat_stop"]},
    {"event": "holder death", "aura": "area aura OWNED by the dying unit (non-passive)", "outcome": "Aura removed DEATH; every living recipient's application unapplied with DEATH",
     "ticks": "none", "anchors": ["death_owned", "aura_remove_apps"]},
    {"event": "caster death", "aura": "aura owned by another (living) holder, e.g. DoT/HoT, not charm/vehicle/totem/cast-in-progress dynobj", "outcome": "persists; the death path does not touch it (exceptions: next rows)",
     "ticks": "continue; GetCaster() still returns the dead unit (ObjectAccessor), so caster bonuses are read live from a corpse; funnel / mana-leech ticks no-op",
     "anchors": ["get_caster", "tick_funnel", "tick_mana_leech"]},
    {"event": "caster death", "aura": "channelled aura on a target", "outcome": "removed CANCEL (InterruptNonMeleeSpells -> Spell::cancel)",
     "ticks": "none", "anchors": ["death_interrupt_casts", "spell_cancel_channel"]},
    {"event": "caster death", "aura": "dynamic-object aura of a spell the caster is casting/channelling at death",
     "outcome": "removed DEFAULT: InterruptNonMeleeSpells -> Spell::cancel -> RemoveDynObject(spellId) removes ALL its dynobjects of that id (R3-06)",
     "ticks": "none", "anchors": ["death_interrupt_casts", "cancel_dynobj"]},
    {"event": "caster death", "aura": "dynamic-object aura of any other spell", "outcome": "persists until its own end or the caster leaves the world",
     "ticks": "continue with dead caster (ASSERT_NOTNULL satisfied)", "anchors": ["leave_world_dynobj"]},
    {"event": "caster death", "aura": "charm / possess aura on another unit; vehicle CONTROL_VEHICLE aura on the vehicle",
     "outcome": "removed DEFAULT inside setDeathState (RemoveAllControlled -> RemoveCharmAuras; ExitVehicle) before the death sweep (R3-07)",
     "ticks": "none", "anchors": ["charm_release", "death_exit_vehicle", "vehicle_exit"]},
    {"event": "caster death", "aura": "auras its totems cast on it and its party", "outcome": "removed DEFAULT (UnsummonAllTotems -> Totem::UnSummon)",
     "ticks": "none", "anchors": ["death_totems", "totem_unsummon_owner", "totem_unsummon_group"]},
    {"event": "caster leaves world (despawn/logout/map change)", "aura": "aura on another holder", "outcome": "persists unless single-target (RM-30) or channelled (RM-03 CANCEL)",
     "ticks": "continue with GetCaster()==nullptr: no caster bonus, no periodic crit, funnel/mana-leech/power-burn no-op",
     "anchors": ["leave_world_single", "upd_channel_gone", "get_caster"]},
    {"event": "caster leaves world", "aura": "own dynobjects / area auras it owns", "outcome": "dynobjects removed (their aura: DEFAULT); owned area-aura applications on others removed DEFAULT",
     "ticks": "none", "anchors": ["leave_world_dynobj", "leave_world_area"]},
    {"event": "owner (master) death", "aura": "auras on/by its pet or summons", "outcome": "summons UnSummon; player pet RemovePet; the controlled unit's own auras removed DEFAULT (RemoveAllAuras) when it leaves the map; auras it cast on others persist with null caster",
     "ticks": "see caster leaves world", "anchors": ["unsummon_controlled", "player_death_pet", "remove_all_default"]},
    {"event": "recipient removes a foreign-owned area application (interrupt flag, remove-by-spell, linked negative id)",
     "aura": "area aura owned by another unit", "outcome": "application removed with that mode; re-added at the owner's next 500 ms "
     "target-map update if still eligible (flicker; track F AL-R-F-05)", "ticks": "none on that recipient in between",
     "anchors": ["remove_aura_owner_only", "targetmap_unapply"]},
    {"event": "recipient leaves area range", "aura": "area aura application", "outcome": "application unapplied DEFAULT at the next 500 ms target-map update (track F)",
     "ticks": "none on that recipient", "anchors": ["targetmap_unapply"]},
    {"event": "channel target out of range", "aura": "channelled aura", "outcome": "application removed DEFAULT", "ticks": "none",
     "anchors": ["channel_range_remove"]},
    {"event": "aura created while the holder is dead", "aura": "not ATTR3_ALLOW_AURA_WHILE_DEAD (passives included)",
     "outcome": "Aura object may exist but _CreateAuraApplication refuses the application (player loading excepted); "
                "cross-ref track G AL-U-G-05", "ticks": "none", "anchors": ["create_app_dead_gate", "add_aura_dead_gate"]},
    {"event": "holder resurrects", "aura": "ATTR3_ONLY_ON_GHOSTS", "outcome": "trinity-only: survives resurrection except 8326/20584; "
     "the ghost-only sweep runs only at login while alive (RM-19)", "ticks": "none",
     "anchors": ["resurrect_ghost", "player_load_alive"]},
)


def matrix() -> list[dict[str, Any]]:
    return [{**row, "coords": [coord(a) for a in row["anchors"]], "evidence": ["trinity-consumer"]} for row in LIFETIME_MATRIX]


# ---------------------------------------------------------------------------
# Millisecond timelines
# ---------------------------------------------------------------------------

@dataclass
class SimAura:
    duration: int                 # current (ms); -1 permanent
    max_duration: int
    period: int                   # 0 -> not periodic
    aura_type: int = 3
    stacks: int = 1
    passive: bool = False
    death_persistent: bool = False
    trigger_fails_at: int | None = None   # tick number whose triggered cast fails CheckCast (Spell.cpp:3503-3507)
    ticks_done: int = 0
    timer: int = 0
    removed: str | None = None


def total_ticks(a: SimAura) -> int:
    """Mirrors: SpellAuraEffects.cpp:936-946 ``GetTotalTicks`` (no ATTR5_EXTRA_INITIAL_PERIOD)."""
    if a.period and a.max_duration != -1:
        return a.max_duration // a.period
    return 0


def aura_update(a: SimAura, diff: int) -> None:
    """Mirrors: SpellAuras.cpp:855-863 ``Aura::Update`` duration part (periodic costs not modelled)."""
    if a.duration > 0:
        a.duration -= diff
        if a.duration < 0:
            a.duration = 0


def effect_update(a: SimAura, diff: int) -> int:
    """Mirrors: SpellAuraEffects.cpp:1250-1276 ``AuraEffect::Update``; returns ticks performed."""
    permanent = a.max_duration == -1
    if not a.period or (a.duration < 0 and not a.passive and not permanent):
        return 0
    tt = total_ticks(a)
    a.timer += diff
    n = 0
    while a.timer >= a.period:
        a.timer -= a.period
        if not permanent and a.ticks_done + 1 > tt:
            break
        a.ticks_done += 1
        n += 1
    return n


@dataclass
class Timeline:
    events: list[dict[str, Any]] = field(default_factory=list)
    final: dict[str, Any] = field(default_factory=dict)


def simulate(aura: SimAura, update_every: int, until: int, external: list[tuple[int, str, Any]] | None = None,
             external_before_update: bool = True) -> Timeline:
    """One holder with one aura; the holder's ``_UpdateSpells`` runs every ``update_every`` ms.

    Per holder update (Mirrors: Unit.cpp:2976-2991): ``Aura::Update`` (duration),
    ``AuraEffect::Update`` (ticks), then the expiry sweep (``IsExpired`` -> EXPIRE).
    External events (``death``, ``caster_death``, ``caster_despawn``, ``dispel`` with
    a stack count, ``resurrect``) happen synchronously at their time.  When an
    external event and a holder update share a timestamp, ``external_before_update``
    decides the order (track I owns which one Trinity takes; it depends on map
    update order).
    """
    tl = Timeline()
    a = aura
    target_alive = True
    caster = "alive"
    external = sorted(external or [], key=lambda e: e[0])
    t = 0
    last_update = 0
    updates = list(range(update_every, until + 1, update_every))
    queue: list[tuple[int, int, str, Any]] = []
    for u in updates:
        queue.append((u, 1 if external_before_update else 0, "update", None))
    for time, kind, arg in external:
        queue.append((time, 0 if external_before_update else 1, kind, arg))
    queue.sort(key=lambda q: (q[0], q[1]))

    def snap() -> dict[str, Any]:
        return {"duration": a.duration, "ticks_done": a.ticks_done, "timer": a.timer, "stacks": a.stacks,
                "removed": a.removed, "target_alive": target_alive, "caster": caster}

    for time, _, kind, arg in queue:
        if a.removed is not None and kind == "update":
            continue
        if kind == "update":
            diff = time - last_update
            last_update = time
            aura_update(a, diff)
            before = a.ticks_done
            n = effect_update(a, diff)
            for k in range(n):
                if a.aura_type in (23, 227):
                    failed = a.trigger_fails_at is not None and before + k + 1 >= a.trigger_fails_at
                    effective: Any = "trigger-cast-failed" if failed else "trigger-cast"
                    if failed:
                        a.duration = 0   # SetDuration(0); the loop does not re-read duration
                else:
                    effective = tick_effective(a.aura_type, caster, target_alive)
                tl.events.append({"t": time, "event": "tick", "n": before + k + 1, "effective": effective,
                                  "caster": caster, **({"stacks": a.stacks})})
            if a.duration == 0 and a.max_duration != -1:
                a.removed = "EXPIRE"
                tl.events.append({"t": time, "event": "removed", "mode": "EXPIRE", "state": snap()})
        elif kind == "death":
            target_alive = False
            if a.removed is None and not a.passive and not a.death_persistent:
                a.removed = "DEATH"
                tl.events.append({"t": time, "event": "removed", "mode": "DEATH", "state": snap()})
            else:
                tl.events.append({"t": time, "event": "holder_death", "state": snap()})
        elif kind == "resurrect":
            target_alive = True
            tl.events.append({"t": time, "event": "resurrect", "state": snap()})
        elif kind == "caster_death":
            caster = "dead"
            tl.events.append({"t": time, "event": "caster_death", "state": snap()})
        elif kind == "caster_despawn":
            caster = "absent"
            tl.events.append({"t": time, "event": "caster_despawn", "state": snap()})
        elif kind == "dispel":
            if a.removed is not None:
                continue
            n = a.stacks - int(arg)
            if n <= 0:
                a.removed = "ENEMY_SPELL"
                tl.events.append({"t": time, "event": "removed", "mode": "ENEMY_SPELL", "state": snap()})
            else:
                a.stacks = n     # decrease: no RefreshTimers, periodic timer untouched (SpellAuras.cpp:1114-1127)
                tl.events.append({"t": time, "event": "stack_dispelled", "state": snap()})
        else:
            raise FailClosed(f"unknown timeline event {kind!r}")
    tl.final = snap()
    tl.final["effective_ticks"] = sum(1 for e in tl.events if e["event"] == "tick" and e["effective"] is True)
    return tl


def standard_timelines() -> list[dict[str, Any]]:
    """The timelines cited in sections/E.md (12 s DoT, 3 s period, 100 ms holder updates)."""
    def dot(**kw: Any) -> SimAura:
        return SimAura(duration=12000, max_duration=12000, period=3000, **kw)

    cases = [
        ("TL-E-01", "natural expiry", dict(external=[]), "death==expiry model predicts identical outcome for TL-E-02"),
        ("TL-E-02", "holder death 50 ms before expiry", dict(external=[(11950, "death", None)]),
         "rules out 'death == natural expiry' (final tick + EXPIRE)"),
        ("TL-E-03", "holder death at the expiry timestamp, death first", dict(external=[(12000, "death", None)]),
         "same-timestamp order decides 3 vs 4 ticks (track I)"),
        ("TL-E-04", "holder death at the expiry timestamp, holder update first",
         dict(external=[(12000, "death", None)], external_before_update=False), "pairs with TL-E-03"),
        ("TL-E-05", "whole-aura dispel 1 ms before the second tick", dict(external=[(5999, "dispel", 1)]),
         "rules out 'removal flushes a pro-rated/final tick'"),
        ("TL-E-06", "one-stack dispel of a 3-stack DoT 1 ms before the second tick",
         dict(external=[(5999, "dispel", 1)], stacks=3), "rules out 'stack loss refreshes duration / resets timer'"),
        ("TL-E-07", "caster dies at 5000", dict(external=[(5000, "caster_death", None)]),
         "rules out 'caster death removes its DoTs'"),
        ("TL-E-08", "caster despawns at 5000", dict(external=[(5000, "caster_despawn", None)]),
         "ticks continue with null caster (no caster bonus / crit)"),
        ("TL-E-09", "death-persistent DoT, holder dies at 4000", dict(external=[(4000, "death", None)], persistent=True),
         "aura survives on corpse; ticks counted but ineffective; expires EXPIRE at 12000"),
        ("TL-E-10", "health funnel (62), caster dies at 5000", dict(external=[(5000, "caster_death", None)], aura_type=62),
         "caster-alive gated tick: counted, no effect"),
        ("TL-E-11", "periodic trigger (23): the 2nd triggered cast fails CheckCast", dict(external=[], aura_type=23, fails_at=2),
         "rules out 'EXPIRE <=> duration ran out' (R2-10): EXPIRE at 6000 with 6000 ms left"),
    ]
    out = []
    for tid, title, kw, rules_out in cases:
        stacks = kw.pop("stacks", 1)
        persistent = kw.pop("persistent", False)
        aura_type = kw.pop("aura_type", 3)
        fails_at = kw.pop("fails_at", None)
        a = dot(stacks=stacks, death_persistent=persistent, aura_type=aura_type, trigger_fails_at=fails_at)
        tl = simulate(a, 100, 12500, **kw)
        out.append({"id": tid, "title": title, "inputs": {"duration": 12000, "period": 3000, "update_every": 100,
                                                            "stacks": stacks, "death_persistent": persistent,
                                                            "aura_type": aura_type, "trigger_fails_at": fails_at,
                                                            **{k: v for k, v in kw.items()}},
                    "events": tl.events, "final": tl.final, "rules_out": rules_out,
                    "evidence": ["trinity-consumer", "differential"]})
    return out


# ---------------------------------------------------------------------------
# Per-spell lifetime facts
# ---------------------------------------------------------------------------

PERSISTENT_AREA_AURA = 27


def explain(ctx, spell: int) -> dict[str, Any]:
    """``aura_lifecycle.py lifetime <spell>``: holder/caster/source lifetime classification."""
    data = ctx.data
    attrs = attrs_of(data, spell)
    effects = data.effects(spell)
    auras = [e for e in effects if int(e["EffectAura"])]
    if not auras:
        raise FailClosed(f"spell {spell}: no aura effect")
    per_effect = []
    for e in auras:
        at = int(e["EffectAura"])
        eff = int(e["Effect"])
        gate = TICK_GATES.get(at)
        per_effect.append({
            "index": int(e["EffectIndex"]), "effect": eff, "aura": at,
            "owner_shape": ("dynamic-object" if eff == PERSISTENT_AREA_AURA else
                            "area (owner applies to recipients)" if eff in AREA_AURA_EFFECTS else "unit (target owns)"),
            "tick_gate": ({k: v for k, v in gate.items() if k != "anchor"} | {"coords": coord(gate["anchor"])}) if gate else None,
        })
    dc = death_class(attrs)
    return {
        "spell": spell, "name": ctx.name(spell), "build_skew": ctx.is_skew(spell),
        "holder_death": dc,
        "holder_death_note": {"removed-death": "removed with DEATH: no final tick, EXPIRE handlers skipped, linked REMOVE casts skipped",
                              }.get(dc, "survives the death sweep; duration keeps running on the corpse"),
        "channelled": has(attrs, "IS_CHANNELLED") or has(attrs, "IS_SELF_CHANNELLED"),
        "only_on_ghosts": has(attrs, "ONLY_ON_GHOSTS"),
        "ignore_owners_death_nyi": has(attrs, "IGNORE_OWNERS_DEATH"),
        "effects": per_effect,
        "caster_death": "channelled: removed CANCEL" if (has(attrs, "IS_CHANNELLED") or has(attrs, "IS_SELF_CHANNELLED"))
        else "persists on other holders; caster read live (dead caster found, despawned caster null)",
        "evidence": ["db2-fact", "trinity-consumer"],
    }


__all__ = ["DeathFixture", "HeldAura", "LIFETIME_MATRIX", "SimAura", "TICK_GATES", "death_order", "explain",
           "matrix", "simulate", "standard_timelines", "tick_effective"]
