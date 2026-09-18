"""Track I -- same-timestamp ordering of aura lifecycle events.

Three layers:

* :data:`SCHEDULER` -- the proven call path of one world tick (``Map::Update`` -> sessions ->
  per-player ``Player::Update`` -> ``Unit::Update`` -> ``WorldObject::Update`` (event processor) ->
  ``Unit::_UpdateSpells`` -> ``Aura::UpdateOwner`` -> ``Aura::Update`` / ``AuraEffect::Update`` ->
  expired removal -> ``_DeleteRemovedAuras``; then melee; then creatures in the player's cells).
  Every step carries an *anchor* (a verbatim source line) so :func:`verify_anchors` re-derives the
  ``file:line`` from the pinned checkout instead of trusting a hand-typed number.
* :class:`World` -- a millisecond timeline model that mirrors the verbatim Trinity functions compiled
  by ``tools/tc_aura_order_probe`` (same scenario language, same log shape), so every timeline below is
  differentially checked against Trinity's own text.
* :data:`PAIRS` -- the ordering census: event pair x proven order x path coordinates x evidence x
  the factor the order depends on, and the scenario (timeline) that demonstrates it.

Scenario language (one command per line; identical to the probe's stdin protocol)::

    unit <name> <isPlayer 0|1> <ModCastingSpeed>          update order = declaration order
    aura <template> <owner> <spellId> <baseMs> <StackAmount> <flags>
    effect <template> <period>                            0 = non-periodic effect
    ontick <template> <effIndex> <action...>
    at <tick> <action...>                                 session phase of world tick N
    cast <caster> <castTimeMs> <tick> <action...>         Spell::prepare in session phase of tick N
    run <diffMs> <count>
    actions: create <t> | kill <unit> | remove <t> | refresh <t> <resetPeriodic 0|1> <hitDurationMs>

flags: 1 ATTR5_EXTRA_INITIAL_PERIOD, 2 ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION, 4 ATTR1_AURA_UNIQUE,
8 ATTR5_AURA_UNIQUE_PER_CASTER, 16 passive, 32 death persistent, 64 ATTR5_SPELL_HASTE_AFFECTS_PERIODIC.

Cut points shared with the probe (not modelled; owned by other tracks): ``CalcMaxDuration`` is the
template base (B), ``ModSpellDuration`` identity (B), amounts (C/D), scripts (H), target maps (F).
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import FailClosed
from .numeric import calculate_pct_int, int_times_float, uint8_of

# AuraRemoveMode (SpellAuraDefines.h:55-64)
REMOVE_DEFAULT, REMOVE_INTERRUPT, REMOVE_CANCEL, REMOVE_ENEMY_SPELL, REMOVE_EXPIRE, REMOVE_DEATH = 1, 2, 3, 4, 5, 6

F_EXTRA_INITIAL, F_ATTR13, F_UNIQUE, F_UNIQUE_PER_CASTER, F_PASSIVE, F_DEATH_PERSISTENT, F_HASTE = 1, 2, 4, 8, 16, 32, 64


# ==========================================================================
# proven scheduler path (anchors re-derive file:line from the pinned checkout)
# ==========================================================================

SCHEDULER: tuple[dict[str, Any], ...] = (
    {"step": "Map::Update: sessions (client packets; instant casts resolve here)", "file": "Maps/Map.cpp",
     "anchor": "session->Update(t_diff, updater);", "order": 1},
    {"step": "Map::Update: per player -> player->Update(t_diff)", "file": "Maps/Map.cpp",
     "anchor": "        player->Update(t_diff);", "order": 2},
    {"step": "Map::Update: then creatures/objects in cells near that player (each cell once per tick)",
     "file": "Maps/Map.cpp", "anchor": "        VisitNearbyCellsOf(player, grid_object_update, world_object_update);", "order": 3},
    {"step": "Map::VisitNearbyCellsOf: marked cells skipped (a unit updates at most once per tick)",
     "file": "Maps/Map.cpp", "anchor": "            if (isCellMarked(cell_id))", "order": 3},
    {"step": "Player::Update -> Unit::Update", "file": "Entities/Player/Player.cpp",
     "anchor": "    Unit::Update(p_time);\n    SetCanDelayTeleport(false);", "order": 4},
    {"step": "Unit::Update -> WorldObject::Update first", "file": "Entities/Unit/Unit.cpp",
     "anchor": "    WorldObject::Update(p_time);\n\n    if (!IsInWorld())", "order": 5},
    {"step": "WorldObject::Update -> m_Events.Update (SpellEvent: cast progress/completion, delayed hits, ChargeDropEvent)",
     "file": "Entities/Object/Object.cpp", "anchor": "    m_Events.Update(diff);", "order": 6},
    {"step": "EventProcessor::Update: due events (time <= m_time) in time order, FIFO within a time; events added "
             "during the loop with time <= m_time run in the same call", "file": "../../common/Utilities/EventProcessor.cpp",
     "anchor": "    while (((i = m_events.begin()) != m_events.end()) && i->first <= m_time)", "order": 6},
    {"step": "Spell::prepare schedules its SpellEvent at m_time + 1 ms (runs in the next EventProcessor::Update, "
             "with that update's full diff)", "file": "Spells/Spell.cpp",
     "anchor": "    m_caster->m_Events.AddEvent(_spellEvent, m_caster->m_Events.CalculateTime(1ms));", "order": 6},
    {"step": "SpellEvent::Execute: unfinished spell re-planned at e_time + 1", "file": "Spells/Spell.cpp",
     "anchor": "    m_Spell->GetCaster()->m_Events.AddEvent(this, Milliseconds(e_time + 1), false);\n    return false;                                           // event not complete",
     "order": 6},
    {"step": "Unit::Update -> _UpdateSpells after events", "file": "Entities/Unit/Unit.cpp",
     "anchor": "    _UpdateSpells(p_time);", "order": 7},
    {"step": "_UpdateSpells: finished current spells cleared before auras", "file": "Entities/Unit/Unit.cpp",
     "anchor": "        if (spell && spell->getState() == SPELL_STATE_FINISHED)", "order": 8},
    {"step": "_UpdateSpells: owned auras in m_ownedAuras (multimap keyed by SpellId) order; update iterator "
             "advanced BEFORE UpdateOwner", "file": "Entities/Unit/Unit.cpp",
     "anchor": "        ++m_auraUpdateIterator;                            // need shift to next for allow update if need into aura update",
     "order": 9},
    {"step": "Aura::UpdateOwner: Aura::Update (duration) first", "file": "Spells/Auras/SpellAuras.cpp",
     "anchor": "    Update(diff, caster);\n\n    if (m_updateTargetMapInterval <= int32(diff))", "order": 10},
    {"step": "Aura::Update: m_duration -= diff, floor 0 (no per-diff splitting)", "file": "Spells/Auras/SpellAuras.cpp",
     "anchor": "        m_duration -= diff;", "order": 10},
    {"step": "Aura::UpdateOwner: target map (every 500 ms) between duration and ticks", "file": "Spells/Auras/SpellAuras.cpp",
     "anchor": "        UpdateTargetMap(caster);\n    else\n        m_updateTargetMapInterval -= diff;", "order": 11},
    {"step": "Aura::UpdateOwner: AuraEffect::Update per effect index", "file": "Spells/Auras/SpellAuras.cpp",
     "anchor": "        effect->Update(diff, caster);", "order": 12},
    {"step": "AuraEffect::Update: catch-up loop, all due ticks in this call, bounded by GetTotalTicks", "file": "Spells/Auras/SpellAuraEffects.cpp",
     "anchor": "    while (_periodicTimer >= _period)", "order": 12},
    {"step": "AuraEffect::Update: application list re-read before every tick", "file": "Spells/Auras/SpellAuraEffects.cpp",
     "anchor": "        GetApplicationList(effectApplications);\n\n        // tick on targets of effects", "order": 12},
    {"step": "_UpdateSpells: expired removal AFTER all owned auras updated", "file": "Entities/Unit/Unit.cpp",
     "anchor": "            RemoveOwnedAura(i, AURA_REMOVE_BY_EXPIRE);", "order": 13},
    {"step": "Aura::IsExpired = !duration && !m_dropEvent", "file": "Spells/Auras/SpellAuras.h",
     "anchor": "bool IsExpired() const { return !GetDuration() && !m_dropEvent; }", "order": 13},
    {"step": "_UpdateSpells: removed Aura objects deleted at the end", "file": "Entities/Unit/Unit.cpp",
     "anchor": "    _DeleteRemovedAuras();\n\n    if (!m_gameObj.empty())", "order": 14},
    {"step": "Unit::Update: attack timers decremented after _UpdateSpells", "file": "Entities/Unit/Unit.cpp",
     "anchor": "            setAttackTimer(BASE_ATTACK, (p_time >= base_att ? 0 : base_att - p_time));", "order": 15},
    {"step": "Player::Update: melee swing after Unit::Update", "file": "Entities/Player/Player.cpp",
     "anchor": "    DoMeleeAttackIfReady();\n\n    if (HasPlayerFlag(PLAYER_FLAGS_RESTING))", "order": 16},
    {"step": "Creature::Update (ALIVE) -> Unit::Update then AI (melee via AI)", "file": "Entities/Creature/Creature.cpp",
     "anchor": "            // creature can be dead after Unit::Update call", "order": 17},
)


# Facts about which units are updated at all (R2-12).  Source-backed only (trinity-consumer).
UPDATE_COVERAGE: tuple[dict[str, Any], ...] = (
    {"fact": "a unit is updated only when its cell is visited in that map tick: cells around players (Map.cpp:705), "
             "around viewpoints (708-709), around distant creatures in PvE combat with a player (711-721), around distant "
             "non-player casters of auras a player holds (723-733), around distant summons (735-747), around active "
             "non-player objects (750-760)",
     "coords": ["Maps/Map.cpp:695-760", "Grids/Notifiers/GridNotifiers.cpp:283-288 ObjectUpdater::Visit"],
     "consequence": "auras owned by a unit in an unvisited cell freeze: no duration countdown, no ticks, no expiry. The diff "
                    "is per map tick and not accumulated per unit, so the frozen time is never credited back; wall-clock "
                    "lifetime exceeds authored duration by the frozen time",
     "evidence": ["trinity-consumer"], "status": "trinity-only", "unknown": "AL-U-I-07"},
    {"fact": "cross-unit order claims (OR-I-02..18 involving two units, settlement_b_vs_d) rest on the Map::Update source; "
             "the probe driver hand-writes that order (sessions, then units in declared order) and verifies only the "
             "per-unit components (_UpdateSpells, UpdateOwner, AuraEffect::Update, EventProcessor, ModStackAmount chain)",
     "coords": ["tools/tc_aura_order_probe/probe.cpp driver (run loop)"], "evidence": ["trinity-consumer"],
     "status": "label", "unknown": None},
)


def _tc_game(tc_root: Path) -> Path:
    return tc_root / "src" / "server" / "game"


def anchor_line(tc_root: Path, file: str, anchor: str) -> int:
    """1-based line of the unique ``anchor`` in ``file`` (relative to ``src/server/game``)."""
    path = (_tc_game(tc_root) / file).resolve()
    text = path.read_text(encoding="utf-8")
    pos = text.find(anchor)
    if pos < 0:
        raise FailClosed(f"anchor not found in {file}: {anchor!r}")
    if text.find(anchor, pos + 1) >= 0:
        raise FailClosed(f"anchor not unique in {file}: {anchor!r}")
    return text.count("\n", 0, pos) + 1


def verify_anchors(tc_root: Path) -> list[dict[str, Any]]:
    """The scheduler path with ``file:line`` re-derived from the checkout."""
    out = []
    for s in SCHEDULER:
        line = anchor_line(tc_root, s["file"], s["anchor"])
        out.append({"order": s["order"], "step": s["step"], "coords": f"{Path(s['file']).name}:{line}"})
    return out


# ==========================================================================
# timeline model (mirrors the verbatim functions compiled by the probe)
# ==========================================================================

@dataclass
class Template:
    name: str
    owner: str
    spell: int
    base: int
    stack_amount: int
    flags: int
    periods: list[int] = field(default_factory=list)
    on_tick: list[list[str]] = field(default_factory=list)
    generations: int = 0

    def has(self, flag: int) -> bool:
        return bool(self.flags & flag)


@dataclass
class Effect:
    index: int
    authored_period: int
    period: int = 0
    timer: int = 0
    ticks: int = 0
    periodic: bool = False


@dataclass(eq=False)
class App:
    target: "Unit"
    base: "Aura"
    mask: int
    remove_mode: int = 0

    def has_effect(self, i: int) -> bool:
        return bool(self.mask & (1 << i))


@dataclass(eq=False)
class Aura:
    t: Template
    gen: int
    owner: "Unit"
    effects: list[Effect]
    max_duration: int = 0
    duration: int = 0
    stack: int = 1
    removed: bool = False
    drop_event: bool = False
    apps: dict[str, App] = field(default_factory=dict)

    @property
    def spell(self) -> int:
        return self.t.spell

    def is_permanent(self) -> bool:
        """Mirrors: SpellAuras.h:227"""
        return self.max_duration == -1

    def is_expired(self) -> bool:
        """Mirrors: SpellAuras.h:226"""
        return not self.duration and not self.drop_event

    def state(self) -> dict[str, Any]:
        return {"gen": self.gen, "duration": self.duration, "max": self.max_duration, "stack": self.stack,
                "removed": self.removed,
                "effects": [{"timer": e.timer, "period": e.period, "ticks": e.ticks} for e in self.effects]}


@dataclass
class _Spell:
    caster: "Unit"
    cast_time: int
    action: list[str]
    timer: int = 0
    finished: bool = False


class EventProcessor:
    """Mirrors: common/Utilities/EventProcessor.cpp:40-80 (multimap by time, FIFO within a time)."""

    def __init__(self) -> None:
        self.time = 0
        self._events: list[tuple[int, int, Any]] = []
        self._seq = 0

    def add(self, when: int, event: Any) -> None:
        self._seq += 1
        bisect.insort(self._events, (when, self._seq, event), key=lambda e: (e[0], e[1]))

    def update(self, p_time: int, world: "World") -> None:
        self.time += p_time
        while self._events and self._events[0][0] <= self.time:
            e_time, _, spell = self._events.pop(0)
            # SpellEvent::Execute (Spell.cpp:8375-8466), PREPARING case of Spell::update (Spell.cpp:4260-4273)
            if not spell.finished:
                if spell.timer > 0:
                    spell.timer = 0 if p_time >= spell.timer else spell.timer - p_time
                if spell.timer == 0:
                    world.log("cast", spell.caster.name, "", timer=spell.timer)
                    spell.finished = True
                    world.run_action(spell.action)
            if spell.finished:
                continue                    # IsDeletable -> event destroyed
            self.add(e_time + 1, spell)


@dataclass(eq=False)
class Unit:
    name: str
    player: bool
    speed: float
    owned: list[Aura] = field(default_factory=list)       # m_ownedAuras (multimap by SpellId)
    applied: list[App] = field(default_factory=list)      # m_appliedAuras (multimap by SpellId)
    removed_auras: list[Aura] = field(default_factory=list)
    update_next: Aura | None = None                        # m_auraUpdateIterator (None == end())
    events: EventProcessor = field(default_factory=EventProcessor)
    alive: bool = True


def _multimap_insert(seq: list, item, key) -> None:
    """``std::multimap::emplace``: after every element with an equal key (upper bound)."""
    k = key(item)
    pos = len(seq)
    for i, x in enumerate(seq):
        if key(x) > k:
            pos = i
            break
    seq.insert(pos, item)


class World:
    """One map: session phase, then units in declared order (players first as Map::Update does)."""

    def __init__(self) -> None:
        self.units: dict[str, Unit] = {}
        self.order: list[Unit] = []
        self.templates: dict[str, Template] = {}
        self.live: dict[str, Aura] = {}
        self.final: dict[str, dict] = {}
        self.session: dict[int, list[list[str]]] = {}
        self.now = 0
        self.tick = 0
        self.phase = "init"
        self.events: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ log
    def log(self, ev: str, unit: str, aura: str, **extra: Any) -> None:
        self.events.append({"t": self.now, "phase": self.phase, "ev": ev, "unit": unit, "aura": aura, **extra})

    # ------------------------------------------------------------------ aura objects
    def _instantiate(self, name: str) -> Aura:
        t = self.templates[name]
        t.generations += 1
        a = Aura(t=t, gen=t.generations, owner=self.units[t.owner],
                 effects=[Effect(index=i, authored_period=p) for i, p in enumerate(t.periods)])
        self.live[name] = a
        return a

    def _calculate_periodic(self, a: Aura, e: Effect, reset: bool) -> None:
        """Mirrors: SpellAuraEffects.cpp:961-1032 (CalculatePeriodic, load=false; no spell mods, no channel)."""
        e.period = e.authored_period
        if e.authored_period:                                   # periodic aura type in the probe protocol
            e.periodic = True
        if not e.periodic:
            return
        if e.period:
            caster = a.owner                                    # probe: caster == owner
            if a.t.has(F_HASTE):
                e.period = int_times_float(e.period, caster.speed)
        else:
            e.periodic = False
        self._reset_periodic(a, e, reset)

    @staticmethod
    def _reset_periodic(a: Aura, e: Effect, reset: bool) -> None:
        """Mirrors: SpellAuraEffects.cpp:949-959"""
        e.ticks = 0
        if reset:
            e.timer = 0
            if a.t.has(F_EXTRA_INITIAL):
                e.timer = e.period

    def _create(self, a: Aura) -> None:
        a.max_duration = a.t.base
        a.duration = a.max_duration
        for e in a.effects:
            self._calculate_periodic(a, e, True)
        _multimap_insert(a.owner.owned, a, key=lambda x: x.spell)
        mask = 0
        for e in a.effects:
            mask |= 1 << e.index
        app = App(target=a.owner, base=a, mask=mask)
        a.apps[a.owner.name] = app
        _multimap_insert(a.owner.applied, app, key=lambda x: x.base.spell)
        self.log("create", a.owner.name, a.t.name, gen=a.gen, duration=a.duration)

    @staticmethod
    def total_ticks(a: Aura, e: Effect) -> int:
        """Mirrors: SpellAuraEffects.cpp:936-946"""
        if e.period and not a.is_permanent():
            n = a.max_duration // e.period
            return n + 1 if a.t.has(F_EXTRA_INITIAL) else n
        return 0

    # ------------------------------------------------------------------ removal
    def _unapply(self, unit: Unit, app: App, mode: int) -> None:
        """Probe cut point for ``Unit::_UnapplyAura(iterator&)`` (Unit.cpp:3601): bookkeeping only."""
        app.remove_mode = mode
        unit.applied.remove(app)
        app.base.apps.pop(unit.name, None)
        app.mask = 0
        self.log("unapply", unit.name, app.base.t.name, mode=mode)

    def _aura_remove(self, a: Aura, mode: int) -> None:
        """Mirrors: SpellAuras.cpp:636-656 (Aura::_Remove)"""
        if a.removed:
            raise FailClosed("ASSERT(!m_isRemoved)")
        a.removed = True
        while a.apps:
            app = next(iter(a.apps.values()))
            self._unapply(app.target, app, mode)

    def remove_owned(self, unit: Unit, a: Aura, mode: int) -> None:
        """Mirrors: Unit.cpp:3750-3766 (RemoveOwnedAura(iterator&)); UnitAura::Remove SpellAuras.cpp:2533"""
        if a.removed:
            return
        if unit.update_next is a:
            i = unit.owned.index(a)
            unit.update_next = unit.owned[i + 1] if i + 1 < len(unit.owned) else None
        unit.owned.remove(a)
        unit.removed_auras.insert(0, a)
        self._aura_remove(a, mode)

    def remove_all_on_death(self, unit: Unit) -> None:
        """Mirrors: Unit.cpp:4472-4493"""
        i = 0
        while i < len(unit.applied):
            app = unit.applied[i]
            if not app.base.t.has(F_PASSIVE) and not app.base.t.has(F_DEATH_PERSISTENT):
                self._unapply(unit, app, REMOVE_DEATH)
                i = 0
            else:
                i += 1
        i = 0
        while i < len(unit.owned):
            a = unit.owned[i]
            if not a.t.has(F_PASSIVE) and not a.t.has(F_DEATH_PERSISTENT):
                self.remove_owned(unit, a, REMOVE_DEATH)
                i = 0
            else:
                i += 1

    # ------------------------------------------------------------------ refresh
    def _mod_stack_amount(self, a: Aura, num: int, reset: bool) -> None:
        """Mirrors: SpellAuras.cpp:1093-1124 (ModStackAmount -> RefreshTimers 976 -> RefreshDuration 952)."""
        stack = a.stack + num
        max_stack = a.t.stack_amount                            # CalcMaxStackAmount, no spell mods
        if num > 0 and stack > max_stack:
            stack = 1 if not a.t.stack_amount else max_stack
        elif stack <= 0:
            self.remove_owned(a.owner, a, REMOVE_DEFAULT)
            return
        refresh = stack >= a.stack and (bool(a.t.stack_amount) or (not a.t.has(F_UNIQUE) and not a.t.has(F_UNIQUE_PER_CASTER)))
        a.stack = uint8_of(stack)
        if refresh:
            a.max_duration = a.t.base                           # CalcMaxDuration (cut point)
            if a.t.has(F_ATTR13):
                reset = False
            a.duration = a.max_duration                         # RefreshDuration(withMods=false)
            for e in a.effects:
                e.ticks = 0                                     # ResetTicks
            for e in a.effects:
                self._calculate_periodic(a, e, reset)

    def _commit_hit_duration(self, a: Aura, hit: int) -> None:
        """Mirrors: Spell.cpp:3260-3298 with refresh == true, SpellValue::Duration unset, DurationMul 1.0,
        ModSpellDuration identity, no channel, no ATTR8 haste."""
        d = hit
        if d > 0:
            d = int_times_float(d, 1.0)
            if a.t.has(F_ATTR13):
                new = d + a.duration
                if not -(2 ** 31) <= new < 2 ** 31:
                    raise FailClosed("int32 overflow in pandemic sum")
                d = min(new, calculate_pct_int(d, 130))
        if d != a.max_duration:
            a.max_duration = d
            a.duration = d

    # ------------------------------------------------------------------ actions
    def run_action(self, act: list[str]) -> None:
        verb = act[0]
        if verb == "create":
            self._create(self._instantiate(act[1]))
        elif verb == "kill":
            u = self.units[act[1]]
            self.log("death", u.name, "")
            u.alive = False
            self.remove_all_on_death(u)
        elif verb == "remove":
            a = self.live.get(act[1])
            if a is None or a.removed:
                self.log("remove-miss", "", act[1])
            else:
                self.remove_owned(a.owner, a, REMOVE_ENEMY_SPELL)
        elif verb == "refresh":
            a = self.live.get(act[1])
            reset, hit = act[2] != "0", int(act[3])
            if a is None or a.removed:
                self.log("refresh-miss", "", act[1])
                self._create(self._instantiate(act[1]))
                return
            self._mod_stack_amount(a, 1, reset)
            self._commit_hit_duration(a, hit)
            self.log("refresh", a.owner.name, a.t.name, duration=a.duration, max=a.max_duration, stack=a.stack,
                     effects=[[e.timer, e.ticks] for e in a.effects])
        else:
            raise FailClosed(f"unknown action {verb!r}")

    # ------------------------------------------------------------------ update
    def _effect_update(self, a: Aura, e: Effect, diff: int) -> None:
        """Mirrors: SpellAuraEffects.cpp:1250-1276"""
        if not e.periodic or (a.duration < 0 and not a.t.has(F_PASSIVE) and not a.is_permanent()):
            return
        total = self.total_ticks(a, e)
        e.timer += diff
        while e.timer >= e.period:
            e.timer -= e.period
            if not a.is_permanent() and e.ticks + 1 > total:
                break
            e.ticks += 1
            for app in [x for x in a.apps.values() if x.has_effect(e.index)]:
                self.log("tick", app.target.name, a.t.name, eff=e.index, n=e.ticks, stack=a.stack)
                act = a.t.on_tick[e.index]
                if act:
                    self.run_action(act)

    def _update_owner(self, a: Aura, diff: int) -> None:
        """Mirrors: SpellAuras.cpp:817-853 (UpdateOwner) and 855-905 (Update, no periodic costs)."""
        if a.duration > 0:
            a.duration -= diff
            if a.duration < 0:
                a.duration = 0
        for e in a.effects:
            self._effect_update(a, e, diff)

    def update_spells(self, u: Unit, diff: int) -> None:
        """Mirrors: Unit.cpp:2957-3000 (no current spells, no channel-caster check, no game objects)."""
        u.update_next = u.owned[0] if u.owned else None
        while u.update_next is not None:
            a = u.update_next
            i = u.owned.index(a)
            u.update_next = u.owned[i + 1] if i + 1 < len(u.owned) else None
            self._update_owner(a, diff)
        i = 0
        while i < len(u.owned):
            if u.owned[i].is_expired():
                self.remove_owned(u, u.owned[i], REMOVE_EXPIRE)
                i = 0
            else:
                i += 1
        for a in u.removed_auras:                       # _DeleteRemovedAuras (Unit.cpp:2946)
            self.final[a.t.name] = a.state()
            if self.live.get(a.t.name) is a:
                del self.live[a.t.name]
        u.removed_auras.clear()

    def run(self, diff: int, count: int) -> None:
        for _ in range(count):
            self.tick += 1
            self.now += diff
            self.phase = "session"
            for act in self.session.get(self.tick, []):
                if act[0] == "__cast":
                    caster = self.units[act[1]]
                    sp = _Spell(caster=caster, cast_time=int(act[2]), action=act[3:])
                    sp.timer = sp.cast_time
                    caster.events.add(caster.events.time + 1, sp)
                    self.log("prepare", caster.name, "", cast_time=sp.cast_time)
                else:
                    self.run_action(act)
            for u in self.order:
                self.phase = "events"
                u.events.update(diff, self)
                self.phase = "spells"
                self.update_spells(u, diff)
                if u.player:
                    self.phase = "melee"
                    self.log("swing-point", u.name, "", auras=[app.base.t.name for app in u.applied])

    # ------------------------------------------------------------------ scenario text
    def feed(self, script: str) -> "World":
        for raw in script.strip().splitlines():
            w = raw.split()
            if not w:
                continue
            if w[0] == "unit":
                u = Unit(name=w[1], player=w[2] == "1", speed=float(w[3]))
                self.units[u.name] = u
                self.order.append(u)
            elif w[0] == "aura":
                self.templates[w[1]] = Template(name=w[1], owner=w[2], spell=int(w[3]), base=int(w[4]),
                                                stack_amount=int(w[5]), flags=int(w[6]))
            elif w[0] == "effect":
                t = self.templates[w[1]]
                t.periods.append(int(w[2]))
                t.on_tick.append([])
            elif w[0] == "ontick":
                self.templates[w[1]].on_tick[int(w[2])] = w[3:]
            elif w[0] == "at":
                self.session.setdefault(int(w[1]), []).append(w[2:])
            elif w[0] == "cast":
                self.session.setdefault(int(w[3]), []).append(["__cast", w[1], w[2], *w[4:]])
            elif w[0] == "run":
                self.run(int(w[1]), int(w[2]))
            else:
                raise FailClosed(f"unknown scenario command {w[0]!r}")
        return self

    def result(self) -> dict[str, Any]:
        auras = {}
        for name, t in sorted(self.templates.items()):
            live = self.live.get(name)
            auras[name] = {"generations": t.generations, "deleted": live is None,
                           "state": live.state() if live else self.final.get(name)}
        return {"log": self.events, "auras": auras}


def simulate(script: str) -> dict[str, Any]:
    return World().feed(script).result()


# ==========================================================================
# discriminating scenarios (each also run through the probe)
# ==========================================================================

SCENARIOS: dict[str, dict[str, Any]] = {
    "tick-vs-expiry": {
        "script": "unit T 0 1\naura A T 100 6000 0 0\neffect A 2000\nat 1 create A\nrun 100 62",
        "question": "tick due at the expiry millisecond: does it fire, and before or after removal?",
        "rules_out": "expiry-first (last tick lost when duration reaches 0 in the same update)",
    },
    "catch-up-large-diff": {
        "script": "unit T 0 1\naura A T 100 6000 0 0\neffect A 2000\nat 1 create A\nrun 5000 3",
        "question": "diff spanning several periods and the expiry: how many ticks, at which timestamps?",
        "rules_out": "one tick per update / ticks clipped by remaining duration",
    },
    "non-multiple-duration": {
        "script": "unit T 0 1\naura A T 100 7000 0 0\neffect A 2000\nat 1 create A\nrun 100 80",
        "question": "duration not a multiple of the period: partial tick at expiry?",
        "rules_out": "partial final tick (Core NUM-E-003 model)",
    },
    "session-creation-decrement": {
        "script": "unit T 0 1\naura A T 100 1000 0 0\neffect A 0\nat 1 create A\nrun 400 4",
        "question": "an aura created by a packet cast in tick N: is it decremented by tick N's diff?",
        "rules_out": "aura lifetime measured from the processing instant of the cast",
    },
    "late-owner-creation": {
        "script": "unit P 1 1\nunit C 0 1\naura B P 200 1000 0 0\neffect B 0\ncast C 0 1 create B\nrun 400 4",
        "question": "an aura created on an earlier-updated unit by a later-updated caster: first decrement?",
        "rules_out": "update-order-independent lifetime (compare session-creation-decrement: one diff longer)",
    },
    "trigger-insert-before-next": {
        "script": "unit T 0 1\naura A T 100 5000 0 0\neffect A 1000\naura B T 200 3000 0 0\neffect B 0\n"
                  "ontick A 0 create B\nat 1 create A\nrun 500 3",
        "question": "aura created during another aura's tick with a larger SpellId but no later neighbour",
        "rules_out": "'larger SpellId than the ticking aura => updated in the same pass'",
    },
    "trigger-insert-after-next": {
        "script": "unit T 0 1\naura A T 100 5000 0 0\neffect A 1000\naura C T 300 9000 0 0\neffect C 0\n"
                  "aura B T 400 3000 0 0\neffect B 0\nontick A 0 create B\nat 1 create A\nat 1 create C\nrun 500 3",
        "question": "aura created during a tick, sorting after the already-advanced update iterator",
        "rules_out": "'auras created during an update pass are never updated in that pass'",
    },
    "death-lower-id-kills": {
        "script": "unit T 0 1\naura A T 100 10000 0 0\neffect A 1000\naura B T 200 10000 0 0\neffect B 1000\n"
                  "ontick A 0 kill T\nat 1 create A\nat 1 create B\nrun 250 5",
        "question": "two periodic auras due at the same ms; the lower SpellId's tick kills the owner",
        "rules_out": "'all ticks due at one timestamp resolve before death removal'",
    },
    "death-higher-id-kills": {
        "script": "unit T 0 1\naura A T 100 10000 0 0\neffect A 1000\naura B T 200 10000 0 0\neffect B 1000\n"
                  "ontick B 0 kill T\nat 1 create A\nat 1 create B\nrun 250 5",
        "question": "same as death-lower-id-kills with the killing tick on the higher SpellId",
        "rules_out": "'death at a timestamp cancels every other tick of that timestamp'",
    },
    "death-persistent-survivor-ticks": {
        "script": "unit T 0 1\naura A T 100 10000 0 0\neffect A 1000\naura B T 200 10000 0 32\neffect B 1000\n"
                  "ontick A 0 kill T\nat 1 create A\nat 1 create B\nrun 250 5",
        "question": "a death-persistent aura sorting after the killing aura: does it still reach its tick at the death ms?",
        "rules_out": "'a lethal tick denies every later same-ms tick' (R2-11): survivors of the death sweep still tick",
    },
    "refresh-before-due-tick": {
        "script": "unit T 0 1\naura A T 100 10000 0 0\neffect A 2000\nat 1 create A\nat 20 refresh A 1 10000\nrun 100 45",
        "question": "packet refresh (resetting the timer) processed in the tick where a tick is due",
        "rules_out": "'the due tick is delivered before the refresh at the same timestamp'",
    },
    "refresh-after-due-tick": {
        "script": "unit T 0 1\nunit C 0 1\naura A T 100 10000 0 0\neffect A 2000\nat 1 create A\ncast C 0 20 refresh A 1 10000\nrun 100 45",
        "question": "the same refresh delivered by a caster updated after the owner (creature SpellEvent)",
        "rules_out": "'same-timestamp tick/refresh order is a property of the aura' (it is a property of update order)",
    },
    "pandemic-refresh-reads-refreshed-duration": {
        "script": "unit T 0 1\naura A T 100 10000 0 2\neffect A 2000\nat 1 create A\nat 81 refresh A 1 10000\nrun 100 220",
        "question": "ATTR13 refresh with 2000 ms remaining: which duration does Spell.cpp:3286 read?",
        "rules_out": "min(new + old remaining, 130%) (Core audit NUM-E-002 wording): Trinity commits 130% at any remaining",
    },
    "pandemic-unique-keeps-remaining": {
        "script": "unit T 0 1\naura A T 100 10000 0 6\neffect A 2000\nat 1 create A\nat 81 refresh A 1 10000\nrun 100 220",
        "question": "ATTR13 + ATTR1_AURA_UNIQUE refresh: ModStackAmount skips RefreshTimers",
        "rules_out": "'every same-caster reapplication refreshes timers'",
    },
    "pandemic-tick-cap": {
        "script": "unit T 0 1\naura A T 100 10000 0 2\neffect A 3000\nat 1 create A\nat 26 refresh A 1 10000\nrun 100 160",
        "question": "phase-keeping refresh: timer-due ticks vs floor(newMax / period)",
        "rules_out": "'ticks = floor((newMax + phase) / period)' (the GetTotalTicks bound drops one)",
    },
    "reapply-after-expiry-new-generation": {
        "script": "unit T 0 1\naura A T 100 1000 0 0\neffect A 500\nat 1 create A\nat 11 refresh A 1 1000\nrun 100 12",
        "question": "reapplication in the tick after expiry-removal: same object or new generation?",
        "rules_out": "'reapplication of the same spell by the same caster reuses the aura object'",
    },
    "remove-then-refresh-same-tick": {
        "script": "unit T 0 1\naura A T 100 5000 0 0\neffect A 1000\nat 1 create A\nat 5 remove A\nat 5 refresh A 1 5000\nrun 100 12",
        "question": "dispel then reapply in the same session phase: the removed object is still allocated",
        "rules_out": "'a removed-but-undeleted aura can be found and refreshed'",
    },
    "extra-initial-period": {
        "script": "unit T 0 1\naura A T 100 4000 0 1\neffect A 1000\nat 1 create A\nrun 100 45",
        "question": "ATTR5_EXTRA_INITIAL_PERIOD: timestamp of the initial tick and tick count",
        "rules_out": "'initial tick at the instant of application' (it is at the first owner update)",
    },
    "hasted-period-truncation": {
        "script": "unit T 0 0.7692308\naura A T 100 12000 0 64\neffect A 3000\nat 1 create A\nrun 1 12000",
        "question": "hasted period int32(3000 * 0.7692308f) and tick bound floor(12000 / period)",
        "rules_out": "exact rational period 3000/1.3",
    },
}


def run_scenarios(names: list[str] | None = None) -> dict[str, dict[str, Any]]:
    out = {}
    for name, sc in SCENARIOS.items():
        if names and name not in names:
            continue
        out[name] = simulate(sc["script"])
    return out


def compact(result: dict[str, Any], *, include_swings: bool = False) -> list[str]:
    """Human-readable timeline lines (``t phase ev unit aura extras``)."""
    out = []
    for e in result["log"]:
        if e["ev"] == "swing-point" and not include_swings:
            continue
        extra = {k: v for k, v in e.items() if k not in ("t", "phase", "ev", "unit", "aura")}
        tail = " ".join(f"{k}={v}" for k, v in sorted(extra.items()))
        out.append(f"{e['t']:>6} {e['phase']:<7} {e['ev']:<12} {e['unit']:<2} {e['aura']:<2} {tail}".rstrip())
    return out


# ==========================================================================
# ordering census
# ==========================================================================

PAIRS: tuple[dict[str, Any], ...] = (
    {"id": "OR-I-01", "pair": "periodic tick / natural expiry (same aura, same ms)", "see_also": ["AL-T-D-01", "AL-T-D-08", "AL-T-D-09", "AL-T-D-16"], 
     "order": "tick-first", "depends_on": None,
     "path": ["Unit.cpp:2975-2980 UpdateOwner loop", "SpellAuras.cpp:845-846 AuraEffect::Update", "Unit.cpp:2983-2991 expiry loop"],
     "scenario": "tick-vs-expiry", "evidence": ["trinity-consumer", "trinity-probe", "differential"],
     "note": "duration reaches 0 in Aura::Update, the tick still fires in the same UpdateOwner; removal only in the "
             "second loop. With catch-up diffs every tick up to GetTotalTicks fires at the update timestamp."},
    {"id": "OR-I-02", "pair": "tick of aura X / expiry of aura Y on the same owner (same ms)",
     "order": "all-ticks-first", "depends_on": None,
     "path": ["Unit.cpp:2975-2980", "Unit.cpp:2983-2991"],
     "scenario": "tick-vs-expiry", "evidence": ["trinity-consumer", "trinity-probe"],
     "note": "every owned aura is updated (and ticks) before any owned aura is expire-removed, independent of SpellId."},
    {"id": "OR-I-03", "pair": "tick / refresh (same aura, same ms)", "see_also": ["AL-T-D-03", "AL-T-D-04", "AL-T-D-17", "AL-T-D-18", "AL-R-B-06"], 
     "order": "depends", "depends_on": "which phase delivers the refresh relative to the owner's _UpdateSpells",
     "path": ["Map.cpp:670 sessions", "Object.cpp:247 caster events", "Unit.cpp:2975-2980 owner auras",
              "SpellAuras.cpp:976-992 RefreshTimers", "SpellAuraEffects.cpp:949-959 ResetPeriodic"],
     "scenario": "refresh-before-due-tick", "evidence": ["trinity-consumer"], "probe": "trinity-probe (components only; cross-unit order is driver-glued)",
     "also": ["refresh-after-due-tick"],
     "note": "packet casts and SpellEvents of units updated before the owner land first: a resetting refresh zeroes "
             "the timer and the tick due at that timestamp is not delivered; pandemic (ATTR13) or "
             "TRIGGERED_DONT_RESET_PERIODIC_TIMER keeps the phase, so the due tick fires after the refresh with "
             "_ticksDone restarted. A refresh delivered by a unit updated after the owner comes after the tick."},
    {"id": "OR-I-04", "pair": "tick / dispel or other explicit removal (same ms)", "see_also": ["AL-T-D-11", "AL-T-D-12"], 
     "order": "depends", "depends_on": "delivery phase of the removing spell relative to the owner's update",
     "path": ["Map.cpp:670", "Unit.cpp:3750-3766 RemoveOwnedAura", "Unit.cpp:2975-2980"],
     "scenario": "remove-then-refresh-same-tick", "evidence": ["trinity-consumer"], "probe": "trinity-probe (components only; cross-unit order is driver-glued)",
     "note": "a removed aura leaves m_ownedAuras immediately, so a removal delivered before the owner's update "
             "suppresses the due tick; one delivered after it (later unit) follows the tick. Dispel effect semantics: Track E."},
    {"id": "OR-I-05", "pair": "tick of aura X / owner death caused by tick of aura Y (same ms)",
     "order": "spellid-order", "depends_on": "m_ownedAuras key order (SpellId) of X relative to Y; only for auras the death sweep removes",
     "path": ["Unit.cpp:2975-2980", "Unit.cpp:4472-4493 RemoveAllAurasOnDeath (skips IsPassive / IsDeathPersistent)",
              "Unit.cpp:3750-3766 iterator shift", "SpellAuraEffects.cpp:5634/5764/5865/5895/5955/6018/6056/6088 alive checks",
              "SpellAuraEffects.cpp:5583-5605, 5607-5630 trigger ticks without alive check"],
     "scenario": "death-lower-id-kills", "also": ["death-higher-id-kills", "death-persistent-survivor-ticks"],
     "evidence": ["trinity-consumer", "trinity-probe"],
     "note": "narrowed (R2-11): only auras removed by RemoveAllAurasOnDeath and sorting after the killer lose their same-ms "
             "tick. Passive and ATTR3_ALLOW_AURA_WHILE_DEAD auras survive the sweep and still reach PeriodicTick in the "
             "same pass: damage/heal/leech/energize/power ticks then return early on the dead target, but "
             "PERIODIC_TRIGGER_SPELL(_WITH_VALUE) casts on the corpse (no alive check; a failed CheckCast then sets the "
             "aura's duration to 0 unless passive, Spell.cpp:3503-3507, so it is expire-removed in the same _UpdateSpells) and PERIODIC_DUMMY runs its scripts. Census: death_survivor_census."},
    {"id": "OR-I-06", "pair": "white swing of a player / expiry or tick of the player's own auras (same ms)",
     "order": "auras-first", "depends_on": None,
     "path": ["Player.cpp:944 Unit::Update", "Unit.cpp:433 _UpdateSpells", "Unit.cpp:461-468 attack timers",
              "Player.cpp:1012 DoMeleeAttackIfReady"],
     "scenario": "late-owner-creation", "evidence": ["trinity-consumer"], "probe": "trinity-probe (components only; cross-unit order is driver-glued)",
     "note": "a player-owned buff expiring at T is gone before the player's swing at T; a buff applied by a packet "
             "cast or by the player's own SpellEvent in tick T is present for the swing at T."},
    {"id": "OR-I-07", "pair": "white swing of a player / tick or expiry of auras owned by a creature near the player",
     "order": "swing-first", "depends_on": "creature updated in the player's cell visit (Map.cpp:705)",
     "path": ["Map.cpp:703 player->Update", "Map.cpp:705 VisitNearbyCellsOf"],
     "scenario": "late-owner-creation", "evidence": ["trinity-consumer"], "probe": "trinity-probe (components only; cross-unit order is driver-glued)",
     "note": "DoTs on a dummy are owned by the dummy (UnitAura owner = target): the player's swing at T resolves before "
             "the dummy's tick/expiry at T. A creature already visited by an earlier player's cell pass is not "
             "updated again (marked cells)."},
    {"id": "OR-I-08", "pair": "refresh / old expiry (refresh at the ms the old application expires)", "see_also": ["AL-R-B-04", "AL-R-B-05"], 
     "order": "depends", "depends_on": "delivery phase relative to the owner's update; same-object vs new generation",
     "path": ["Unit.cpp:2983-2991", "Unit.cpp:3386-3445 _TryStackingOrRefreshingExistingAura", "SpellAuras.cpp:350-387"],
     "scenario": "reapply-after-expiry-new-generation", "evidence": ["trinity-consumer"], "probe": "trinity-probe (components only; cross-unit order is driver-glued)",
     "note": "refresh first: the same Aura object continues (no remove/apply hooks). Expiry first: the old object is "
             "removed (AURA_REMOVE_BY_EXPIRE) and GetOwnedAura no longer finds it -> Aura::Create builds a new "
             "generation (fresh stacks, timers, charges; pandemic reads nothing)."},
    {"id": "OR-I-09", "pair": "cast completion (SpellEvent) / caster's own aura update (same tick)",
     "order": "cast-first", "depends_on": None,
     "path": ["Unit.cpp:428 WorldObject::Update", "Object.cpp:247 m_Events.Update", "Unit.cpp:433 _UpdateSpells"],
     "scenario": "late-owner-creation", "evidence": ["trinity-consumer"], "probe": "trinity-probe (components only; cross-unit order is driver-glued)",
     "note": "an aura the caster applies to itself at cast completion is decremented by the same diff in the same tick."},
    {"id": "OR-I-10", "pair": "application / first decrement and first tick", "see_also": ["AL-T-D-10", "AL-D-D-03", "AL-U-F-03"], 
     "order": "depends", "depends_on": "whether the owner's _UpdateSpells runs after the application in the same world tick",
     "path": ["Map.cpp:662-705", "Unit.cpp:2975-2980"],
     "scenario": "session-creation-decrement", "evidence": ["trinity-consumer"], "probe": "trinity-probe (components only; cross-unit order is driver-glued)",
     "note": "packet-phase applications and applications made by units updated before the owner are decremented by "
             "the current diff at once (effectively timestamped at the previous world tick); applications made after the "
             "owner's update (later unit) are not. Effective lifetime differs by one world diff by update order."},
    {"id": "OR-I-11", "pair": "aura created during the owner's update pass (triggered by a tick) / its first update",
     "order": "spellid-order", "depends_on": "new SpellId key position relative to the already-advanced m_auraUpdateIterator",
     "path": ["Unit.cpp:2975-2980", "Unit.cpp:3452 m_ownedAuras.emplace", "SpellAuraEffects.cpp:5596 CastSpell (TRIGGERED_FULL_MASK: CAST_DIRECTLY)"],
     "scenario": "trigger-insert-after-next", "evidence": ["trinity-consumer", "trinity-probe", "differential"],
     "note": "updated in the same pass iff inserted after the *next* aura; inserting after the current aura but before "
             "(or with no) next aura is not enough."},
    {"id": "OR-I-12", "pair": "stack mutation / tick at the same ms",
     "order": "depends", "depends_on": "delivery phase (as OR-I-03); StackAmount >= 2 never resets the timer",
     "path": ["Spell.cpp:3240 resetPeriodicTimer = StackAmount < 2 && !DONT_RESET", "SpellAuras.cpp:1093-1129", "SpellAuras.cpp:1056-1076 SetStackAmount"],
     "scenario": "refresh-before-due-tick", "evidence": ["trinity-consumer"], "probe": "trinity-probe (components only; cross-unit order is driver-glued)",
     "note": "a stack added before the owner's update is seen by the tick at the same ms (amount recalculated in "
             "SetStackAmount before the tick); amount semantics: Track C/D."},
    {"id": "OR-I-13", "pair": "area-recipient map change / tick at the same ms", "see_also": ["S01-enter-leave-ticks", "S02-extra-initial-period"], 
     "order": "map-first", "depends_on": "the 500 ms target-map timer elapsing in this UpdateOwner",
     "path": ["SpellAuras.cpp:837-840 UpdateTargetMap before effect updates", "SpellAuraEffects.cpp:1269-1274 application list per tick"],
     "scenario": None, "evidence": ["trinity-consumer"],
     "note": "a recipient dropped by the map update gets no tick at that ms; a recipient added gets the tick immediately "
             "if one is due (application list re-read per tick). Recipient semantics: Track F."},
    {"id": "OR-I-14", "pair": "charge-drop event / expiry",
     "order": "drop-first", "depends_on": None,
     "path": ["SpellAuras.h:226 IsExpired", "SpellAuras.cpp:1046-1054 DropChargeDelayed (AddEventAtOffset 1053)", "SpellAuras.cpp:46-60 ChargeDropEvent"],
     "scenario": None, "evidence": ["trinity-consumer"],
     "note": "an aura with a pending ChargeDropEvent is never expire-removed (IsExpired false) even at duration 0; the "
             "drop runs in the owner's event phase (before _UpdateSpells), after which expiry can proceed."},
    {"id": "OR-I-15", "pair": "periodic tick / cast completion of a spell whose target owns the aura", "see_also": ["UNK-E-003", "AL-T-D-17", "AL-T-D-18"], 
     "order": "depends", "depends_on": "caster vs owner update order (players before creatures in their cells)",
     "path": ["Map.cpp:703-705", "Object.cpp:247", "Unit.cpp:2975-2980"],
     "scenario": "late-owner-creation", "evidence": ["trinity-consumer"], "probe": "trinity-probe (components only; cross-unit order is driver-glued)",
     "note": "resolves the Trinity side of core-audit UNK-E-003: a packet (instant) cast is processed in the session phase, "
             "before every unit update of that world tick, so it precedes a tick due in that tick (the opposite of Core's "
             "'instant cast observes the tick first'); a hard-cast completion runs in the caster's event phase and precedes "
             "the owner's tick iff the caster is updated before the owner (caster == owner: always, AL-T-D-17; player "
             "caster on a creature in its cells: always; creature caster on a player: never, AL-T-D-18)."},
    {"id": "OR-I-17", "pair": "AreaTrigger target-list update / unit aura countdown in the same world tick",
     "see_also": ["AL-U-F-04", "S18-areatrigger-per-unit"],
     "order": "depends", "depends_on": "owner kind and cell: players before any cell they activate; within a cell, "
                                       "grid container (GameObject, Creature, DynamicObject, Corpse, AreaTrigger, ...) then "
                                       "world container (pets)",
     "path": ["Map.cpp:703-705", "Map.cpp:634-635 Visit(grid) then Visit(world)", "GridDefines.h:91-92 container type order",
              "TypeContainerVisitor.h:36-39 head-first", "GridNotifiers.h:243 players not visited", "AreaTrigger.cpp:341"],
     "scenario": None, "evidence": ["trinity-consumer"],
     "note": "a player owner's aura countdown precedes the update of area triggers in the cells it activates, but follows "
             "area triggers in cells an earlier player already visited; a non-pet creature precedes area triggers of its "
             "own cell; pets follow them; cells are visited x-major then y (Map.cpp:620-623)."},
    {"id": "OR-I-18", "pair": "units of one map: update order",
     "see_also": ["UNK-E-003", "AL-U-F-03"],
     "order": "proven", "depends_on": "m_mapRefManager player order; cell marking",
     "path": ["Map.cpp:662-672 sessions", "Map.cpp:695-748 per player: Update, own cells, far combat creatures, far aura "
              "casters, far summons", "Map.cpp:750-760 active non-player objects' cells", "Map.cpp:627-630 marked cells"],
     "scenario": "late-owner-creation", "evidence": ["trinity-consumer"], "probe": "trinity-probe (components only; cross-unit order is driver-glued)",
     "note": "sessions of all players, then for each player in map-reference order: that player, then every not-yet-visited "
             "cell around it (creatures, area triggers, pets), then cells around distant creatures it fights and distant "
             "non-player casters of auras it holds; finally cells of active non-player objects. Each unit updates at most "
             "once per world tick with the full diff."},
    {"id": "OR-I-16", "pair": "same-timestamp events of one unit's event queue",
     "order": "fifo", "depends_on": "insertion order (std::multimap equal-key insertion at upper bound)",
     "path": ["EventProcessor.cpp:47-79", "EventProcessor.cpp:113-119"],
     "scenario": None, "evidence": ["trinity-consumer", "trinity-probe"],
     "note": "events added during the loop with time <= m_time run in the same Update call."},
)


# Where a spell hit (refresh / stack / dispel / new application) is delivered inside one world tick,
# relative to the owner's aura update (_UpdateSpells).  Settles B (AL-U-B-09 'update(t) then hit(t)')
# against D ('own events first, then auras; caster != owner by map order').
DELIVERY: tuple[dict[str, Any], ...] = (
    {"channel": "packet cast executed at once", "phase": "session (before every unit update of the tick)",
     "vs_owner_update": "hit-first for every owner", "path": ["Map.cpp:662-672", "SpellHandler.cpp:241-242",
     "Player.cpp:31499-31510 RequestSpellCast -> ExecutePendingSpellCastRequest"], "scenario": "refresh-before-due-tick",
     "evidence": ["trinity-consumer"], "probe": "components only"},
    {"channel": "packet cast queued (spell queue window: GCD or cast in progress)",
     "phase": "Player::Update after Unit::Update of the casting player, before its melee",
     "vs_owner_update": "update-first for the caster's own auras; hit-first for owners updated later (creatures in the "
                        "player's not-yet-visited cells); update-first for units updated earlier",
     "path": ["Player.cpp:944-949", "Player.cpp:31530-31541 CanRequestSpellCast"], "scenario": None,
     "evidence": ["trinity-consumer"]},
    {"channel": "caster event: hard-cast completion, channel start, delayed (missile) hit, charge drop",
     "phase": "caster's WorldObject::Update (m_Events) at the start of the caster's Unit::Update",
     "vs_owner_update": "hit-first when caster == owner or the caster is updated before the owner; update-first when "
                        "the caster is updated after the owner",
     "path": ["Unit.cpp:428", "Object.cpp:247", "Spell.cpp:8375-8466 (LAUNCHED 8395, handle_delayed 8429)", "Spell.cpp:3467"],
     "scenario": "refresh-after-due-tick", "evidence": ["trinity-consumer"], "probe": "components only"},
    {"channel": "creature AI cast (UpdateAI; instant casts resolve inside prepare)",
     "phase": "Creature::Update after its Unit::Update",
     "vs_owner_update": "update-first for the creature's own auras; otherwise map order",
     "path": ["Creature.cpp:848", "Creature.cpp:874 AIUpdateTick", "Spell.cpp:3586-3611 willCastDirectly"], "scenario": None,
     "evidence": ["trinity-consumer"]},
    {"channel": "synchronous triggered cast (periodic trigger / proc, TRIGGERED_FULL_MASK incl. CAST_DIRECTLY)",
     "phase": "inside the triggering unit's update (often inside the owner's own aura pass)",
     "vs_owner_update": "within the same owner pass: auras already updated this pass see the hit after their update, "
                        "auras after the advanced iterator see it before (OR-I-11)",
     "path": ["SpellAuraEffects.cpp:5596", "SpellDefines.h:281,293", "Spell.cpp:3578-3579 CAST_DIRECTLY cast(true)"],
     "scenario": "trigger-insert-after-next", "evidence": ["trinity-consumer"], "probe": "components only"},
)

SETTLEMENT_B_D = {
    "question": "within one world tick, does the owner's aura update run before or after a spell hit on that aura? "
                "(AL-U-B-09 assumes update(t) then hit(t); Track D: own events first, caster != owner by map order)",
    "verdict": "Track D is right; Track B's convention holds only for hits delivered after the owner's update "
               "(caster updated after the owner, queued player casts on the caster's own auras, creature AI casts on "
               "the creature's own auras, triggered casts reaching auras earlier in the running pass). Packet casts "
               "executed at once and a unit's own SpellEvents (cast completion, delayed hits) land before the "
               "owner's update.",
    "proof": ["Map.cpp:662-672 sessions before objects", "Unit.cpp:428-433 WorldObject::Update (m_Events) before "
              "_UpdateSpells", "Player.cpp:944-949 queued request after Unit::Update", "Map.cpp:695-705 player then its cells"],
    "probe": ["refresh-before-due-tick (session hit swallows the 2000 ms tick)",
              "refresh-after-due-tick (creature SpellEvent after owner: tick at 2000 then refresh)"],
    "evidence": ["trinity-consumer"],
    "probe_role": "trinity-probe (components): the probe's run loop hand-writes 'sessions, then units in declared order'; "
                  "the verbatim parts are _UpdateSpells / UpdateOwner / AuraEffect::Update / EventProcessor / SpellEvent",
}


def pair_census(results: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    results = results if results is not None else run_scenarios()
    out = []
    for p in PAIRS:
        row = dict(p)
        if p["scenario"]:
            row["timeline"] = compact(results[p["scenario"]], include_swings=p["scenario"] == "late-owner-creation")
        out.append(row)
    return sorted(out, key=lambda r: r["id"])



# ==========================================================================
# refresh-commit path census (which ATTR13 providers take which branch)
# ==========================================================================

def refresh_path_census(data, pops: dict[str, frozenset[int]], provider_effects: list[dict],
                        surfaces: dict[int, dict[str, str]] | None = None) -> dict[str, Any]:
    """Split ATTR13 (pandemic) provider spells by Track B's reapplication classification (identical definition:
    ``refresh.classify(duration.facts(...))["carry"]``), so the counts equal ``carryover.json`` census.carry.

    Adds what B does not: the finite-duration subset, the channel subset (same-caster recast is
    cancel-then-create, Unit.cpp:3113-3117 / Spell.cpp:3636-3647), and which spells have an externally
    touched duration / refresh / periodic surface (``overlays.surface_flags``, Track H).
    """
    from .duration import empower_spells, facts, spellinfo_get_duration
    from .numeric import PERIODIC_AURAS, _count
    from .refresh import classify
    emp = empower_spells(data.source)
    periodic = {r["spell"] for r in provider_effects if r["aura"] in PERIODIC_AURAS}
    spells = sorted({r["spell"] for r in provider_effects})
    surfaces = surfaces or {}
    touched_keys = ("duration", "refresh", "periodic")
    groups: dict[str, dict[str, list[int]]] = {}
    for spell in spells:
        f = facts(data, spell, emp)
        carry = classify(f)["carry"]
        if carry == "none":
            continue
        g = groups.setdefault(carry, {"all": [], "finite": [], "periodic": [], "channel": [], "touched": []})
        g["all"].append(spell)
        if spellinfo_get_duration(f) > 0:
            g["finite"].append(spell)
        if spell in periodic:
            g["periodic"].append(spell)
        if f.channeled:
            g["channel"].append(spell)
        if any(k in surfaces.get(spell, {}) for k in touched_keys):
            g["touched"].append(spell)
    return {
        "definition": "Track B refresh.classify carry (same as carryover.json census.carry); populations incl. player+class-skills",
        "external_surface_keys": list(touched_keys),
        "branches": {k: {"counts": _count(g["all"], pops), "finite_duration": _count(g["finite"], pops),
                         "with_periodic_effect": _count(g["periodic"], pops),
                         "channels_cancel_then_create": _count(g["channel"], pops),
                         "externally_touched": _count(g["touched"], pops),
                         "player_spells": sorted(set(g["all"]) & pops["player"]),
                         "player_spells_externally_touched": sorted(set(g["touched"]) & pops["player"])}
                     for k, g in sorted(groups.items())},
    }



def death_survivor_census(data, pops: dict[str, frozenset[int]], provider_effects: list[dict]) -> dict[str, Any]:
    """R2-11: periodic provider effects that survive RemoveAllAurasOnDeath (Trinity IsPassive incl. load-time
    corrections via passive.effective_passive, or ATTR3_ALLOW_AURA_WHILE_DEAD) and therefore still reach
    PeriodicTick on a dead owner in the same pass, split by what the tick does on a dead target.

    Mirrors: Unit.cpp:4472-4493; SpellInfo.cpp:1828-1831; tick handlers' alive checks (SpellAuraEffects.cpp:5634,
    5764, 5865, 5895, 5955, 6018, 6056, 6088) vs none in 5583-5630 (trigger) and 226 (dummy, scripts only)
    """
    from procs.enums import attr, aura
    from .numeric import PERIODIC_AURAS, _attrs, _count, _has
    from .passive import effective_passive
    allow_dead = attr("SPELL_ATTR3_ALLOW_AURA_WHILE_DEAD")
    acts = {aura("PERIODIC_TRIGGER_SPELL"): "trigger-cast-on-corpse", aura("PERIODIC_TRIGGER_SPELL_WITH_VALUE"): "trigger-cast-on-corpse",
            aura("PERIODIC_DUMMY"): "script-only", aura("PERIODIC_TRIGGER_SPELL_FROM_CLIENT"): "no-op"}
    kinds: dict[str, set[int]] = {}
    for r in provider_effects:
        if r["aura"] not in PERIODIC_AURAS:
            continue
        spell = r["spell"]
        try:
            passive = effective_passive(data, spell)["passive"]
        except Exception:                      # no SpellMisc row: IsPassive undetermined -> not counted
            continue
        if not (passive or _has(_attrs(data, spell), allow_dead)):
            continue
        kinds.setdefault(acts.get(r["aura"], "silent (alive check)"), set()).add(spell)
    return {"population_note": "provider spells with a periodic aura effect that survives the death sweep",
            "by_tick_kind": {k: _count(v, pops) for k, v in sorted(kinds.items())},
            "player_witnesses": {k: sorted(v & pops["player"])[:10] for k, v in sorted(kinds.items())}}
