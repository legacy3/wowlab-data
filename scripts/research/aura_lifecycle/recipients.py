"""Area aura recipient lifecycle (Track F).

The targeting pass (``targeting/auratargets.py``, ``tools/tc_target_auramap_probe``)
answers *who* a single ``Aura::UpdateTargetMap`` selects.  This module answers the
time dimension: how per-recipient ``AuraApplication`` objects of **one** parent
``Aura`` appear, persist and disappear, and which state is parent-owned (shared by
every recipient) versus application-owned.

Three recipient pipelines produce auras on units other than the one hit by the spell:

* ``unit-area``   -- ``UnitAura`` owned by a unit, area-aura effects 35/65/119/128/129/143/202/271
  and ``APPLY_AURA_ON_PET`` 174; recipients from ``UnitAura::FillTargetMap`` (SpellAuras.cpp:2540).
* ``dynobj``      -- ``DynObjAura`` owned by a DynamicObject (``PERSISTENT_AREA_AURA`` 27);
  recipients from ``DynObjAura::FillTargetMap`` (SpellAuras.cpp:2709).
* ``areatrigger`` -- an AreaTrigger's enter actions / scripts cast or add a **separate** aura on
  each entering unit (AreaTrigger.cpp:1140-1210, scripts); every recipient owns its own ``Aura``.

Parent vs application (observable semantics, Trinity @ 7f3d43b)
---------------------------------------------------------------
Parent-owned, one value for every recipient of a unit/dynobj area aura:
duration / max duration (``Aura::m_duration``), stacks, charges, the effect amount
(``AuraEffect::_amount``, computed once with the *owner* as target, SpellAuraEffects.cpp:777),
the periodic timer and tick counter (``AuraEffect::_periodicTimer``/``_ticksDone``,
SpellAuraEffects.cpp:1250), the recipient-map timer (``m_updateTargetMapInterval``).
Application-owned: applied effect mask, remove mode, visible slot, positive/negative flag,
per-target registration (``m_modAuras``), diminishing registration.

Storage (not observable): ``Aura::m_applications`` is a ``std::unordered_map<ObjectGuid, AuraApplication*>``
(SpellAuras.h:172); tick delivery order within one tick therefore follows hash order
(``GetApplicationList``, SpellAuraEffects.cpp:767) and is not defined; this model sorts by unit id.

Runtime timelines (:class:`World`) are pure functions of an explicit fixture; the recipient
*selection* (range, relations) is an input: it is the targeting pass's job.  Everything this
module models is mirrored from Trinity call paths; anything else raises :class:`FailClosed`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import FailClosed

# ---------------------------------------------------------------------------
# vocabulary (pinned SharedDefines.h / SpellAuras.h values)
# ---------------------------------------------------------------------------
UPDATE_TARGET_MAP_INTERVAL = 500                     # SpellAuras.h:58
APPLY_AURA = 6
PERSISTENT_AREA_AURA = 27
APPLY_AURA_ON_PET = 174
CREATE_AREATRIGGER = 179
CREATE_AREATRIGGER_2 = 353
AURA_AREA_TRIGGER = 395                              # SPELL_AURA_AREA_TRIGGER (creates an AT)

AREA_EFFECT = {
    35: "APPLY_AREA_AURA_PARTY", 65: "APPLY_AREA_AURA_RAID", 119: "APPLY_AREA_AURA_PET",
    128: "APPLY_AREA_AURA_FRIEND", 129: "APPLY_AREA_AURA_ENEMY", 143: "APPLY_AREA_AURA_OWNER",
    202: "APPLY_AREA_AURA_SUMMONS", 271: "APPLY_AREA_AURA_PARTY_NONRANDOM",
    174: "APPLY_AURA_ON_PET", 27: "PERSISTENT_AREA_AURA",
}

#: Does the aura owner receive this effect through its own target map?
#: Mirrors: SpellAuras.cpp:2587-2630 (owner pushed explicitly for 119/202; PARTY/RAID/FRIEND reach
#: the owner through the area search when the owner passes its own target check; ENEMY/OWNER/ON_PET never).
OWNER_RECEIVES = {
    35: "via-search", 271: "via-search", 65: "via-search", 128: "via-search",
    129: "no", 143: "no", 174: "no", 119: "always", 202: "always", 27: "via-search",
}

#: AuraEffect::CalculatePeriodic switch (SpellAuraEffects.cpp:961-990): aura types that are periodic.
PERIODIC_AURAS = frozenset({21, 3, 8, 20, 23, 48, 24, 53, 62, 64, 89, 162, 226, 227})

# AuraRemoveMode (SpellAuraDefines.h)
DEFAULT, INTERRUPT, CANCEL, ENEMY_SPELL, EXPIRE, DEATH = (
    "BY_DEFAULT", "INTERRUPT", "CANCEL", "ENEMY_SPELL", "EXPIRE", "DEATH")

PIPELINES = ("unit-area", "dynobj", "areatrigger", "static")

T = "src/server/game/"
COORDS = {
    "UpdateOwner": T + "Spells/Auras/SpellAuras.cpp:817",
    "UpdateOwner.timer": T + "Spells/Auras/SpellAuras.cpp:839",
    "Aura::Update": T + "Spells/Auras/SpellAuras.cpp:855",
    "UpdateTargetMap": T + "Spells/Auras/SpellAuras.cpp:658",
    "UpdateTargetMap.remove": T + "Spells/Auras/SpellAuras.cpp:775",
    "UpdateTargetMap.stack": T + "Spells/Auras/SpellAuras.cpp:728",
    "ctor.interval0": T + "Spells/Auras/SpellAuras.cpp:480",
    "_ApplyForTarget": T + "Spells/Auras/SpellAuras.cpp:586",
    "_UnapplyForTarget": T + "Spells/Auras/SpellAuras.cpp:606",
    "_Remove": T + "Spells/Auras/SpellAuras.cpp:636",
    "RefreshTimers": T + "Spells/Auras/SpellAuras.cpp:976",
    "SetStackAmount": T + "Spells/Auras/SpellAuras.cpp:1056",
    "CanStackWith.dynobj": T + "Spells/Auras/SpellAuras.cpp:1643",
    "UnitAura::FillTargetMap": T + "Spells/Auras/SpellAuras.cpp:2540",
    "FillTargetMap.deadowner": T + "Spells/Auras/SpellAuras.cpp:2543",
    "DynObjAura::FillTargetMap": T + "Spells/Auras/SpellAuras.cpp:2709",
    "UnitAura::Remove": T + "Spells/Auras/SpellAuras.cpp:2533",
    "DynObjAura::Remove": T + "Spells/Auras/SpellAuras.cpp:2702",
    "AuraEffect::ctor": T + "Spells/Auras/SpellAuraEffects.cpp:736",
    "AuraEffect::CalculateAmount": T + "Spells/Auras/SpellAuraEffects.cpp:777",
    "GetTotalTicks": T + "Spells/Auras/SpellAuraEffects.cpp:936",
    "ResetPeriodic": T + "Spells/Auras/SpellAuraEffects.cpp:949",
    "CalculatePeriodic": T + "Spells/Auras/SpellAuraEffects.cpp:961",
    "ChangeAmount": T + "Spells/Auras/SpellAuraEffects.cpp:1091",
    "AuraEffect::Update": T + "Spells/Auras/SpellAuraEffects.cpp:1250",
    "GetApplicationList": T + "Spells/Auras/SpellAuraEffects.cpp:767",
    "_UpdateSpells": T + "Entities/Unit/Unit.cpp:2957",
    "_UpdateSpells.expire": T + "Entities/Unit/Unit.cpp:2986",
    "_CreateAuraApplication": T + "Entities/Unit/Unit.cpp:3488",
    "_CreateAuraApplication.dead": T + "Entities/Unit/Unit.cpp:3508",
    "_UnapplyAura": T + "Entities/Unit/Unit.cpp:3601",
    "RemoveOwnedAura": T + "Entities/Unit/Unit.cpp:3750",
    "RemoveAura.app": T + "Entities/Unit/Unit.cpp:3827",
    "RemoveAurasDueToSpellByDispel": T + "Entities/Unit/Unit.cpp:4006",
    "RemoveAreaAurasDueToLeaveWorld": T + "Entities/Unit/Unit.cpp:4348",
    "RemoveAllAurasOnDeath": T + "Entities/Unit/Unit.cpp:4472",
    "GetDispellableAuraList": T + "Entities/Unit/Unit.cpp:4735",
    "setDeathState": T + "Entities/Unit/Unit.cpp:9160",
    "Unit::Update": T + "Entities/Unit/Unit.cpp:423",
    "RemoveFromWorld": T + "Entities/Unit/Unit.cpp:10275",
    "AddAura": T + "Entities/Unit/Unit.cpp:12287",
    "CheckTarget.dead": T + "Spells/SpellInfo.cpp:2452",
    "IsDeathPersistent": T + "Spells/SpellInfo.cpp:1828",
    "EffectApplyAura": T + "Spells/SpellEffects.cpp:1110",
    "EffectPersistentAA": T + "Spells/SpellEffects.cpp:1512",
    "EffectPersistentAA.register": T + "Spells/SpellEffects.cpp:1556",
    "DoSpellEffectHit": T + "Spells/Spell.cpp:3226",
    "DoDamageAndTriggers.apply": T + "Spells/Spell.cpp:3029",
    "ApplyForTargets": T + "Spells/Auras/SpellAuras.h:210",
    "DynamicObject::Update": T + "Entities/DynamicObject/DynamicObject.cpp:134",
    "DynamicObject::RemoveAura": T + "Entities/DynamicObject/DynamicObject.cpp:198",
    "DynamicObject::RemoveFromWorld": T + "Entities/DynamicObject/DynamicObject.cpp:65",
    "CancelAura": T + "Handlers/SpellHandler.cpp:259",
    "AT::Update": T + "Entities/AreaTrigger/AreaTrigger.cpp:341",
    "AT::UpdateTargetList": T + "Entities/AreaTrigger/AreaTrigger.cpp:641",
    "AT::HandleUnitEnterExit": T + "Entities/AreaTrigger/AreaTrigger.cpp:853",
    "AT::DoActions": T + "Entities/AreaTrigger/AreaTrigger.cpp:1140",
    "AT::UndoActions": T + "Entities/AreaTrigger/AreaTrigger.cpp:1190",
    "AT::RemoveFromWorld": T + "Entities/AreaTrigger/AreaTrigger.cpp:82",
}


def coord(key: str) -> str:
    return COORDS[key]


# ---------------------------------------------------------------------------
# static classification over DB2 rows
# ---------------------------------------------------------------------------
def effect_pipeline(effect: int, aura: int) -> str | None:
    """Recipient pipeline of one SpellEffect row (None: not an aura-map producer).

    Mirrors: SpellInfo.cpp IsAreaAuraEffect / IsUnitOwnedAuraEffect; SpellAuras.cpp:320
    ``BuildEffectMaskForOwner`` (27 only for DynamicObject owners).
    """
    if effect == PERSISTENT_AREA_AURA and aura:
        return "dynobj"
    if effect in AREA_EFFECT and effect != PERSISTENT_AREA_AURA and aura:
        return "unit-area"
    if effect in (CREATE_AREATRIGGER, CREATE_AREATRIGGER_2) or (effect == APPLY_AURA and aura == AURA_AREA_TRIGGER):
        return "areatrigger"
    if effect == APPLY_AURA and aura:
        return "static"
    return None


def is_periodic_aura(aura: int) -> bool:
    """Mirrors: SpellAuraEffects.cpp:965-983 (the ``m_isPeriodic`` switch; a script hook may change it)."""
    return aura in PERIODIC_AURAS


# ---------------------------------------------------------------------------
# runtime model
# ---------------------------------------------------------------------------
@dataclass
class EffectState:
    """One ``AuraEffect``: parent-owned; shared by every application."""
    index: int
    aura: int
    period: int = 0
    periodic: bool = False
    extra_initial_period: bool = False
    area: bool = True                     # False: plain APPLY_AURA (static application only)
    timer: int = 0
    ticks_done: int = 0

    def reset_periodic(self, reset_timer: bool) -> None:
        """Mirrors: SpellAuraEffects.cpp:949 ``AuraEffect::ResetPeriodic``."""
        self.ticks_done = 0
        if reset_timer:
            self.timer = 0
            if self.extra_initial_period:
                self.timer = self.period


@dataclass
class Application:
    unit: str
    mask: int
    created_at: int


@dataclass
class AuraModel:
    """One parent ``Aura`` (UnitAura or DynObjAura) and its applications."""
    id: str
    spell: int
    kind: str                             # "unit" | "dynobj"
    owner: str                            # unit id, or the dynobj id
    caster: str
    effects: list[EffectState]
    max_duration: int
    passive: bool = False
    death_persistent: bool = False        # SPELL_ATTR3_ALLOW_AURA_WHILE_DEAD
    disable_while_dead: bool = False      # SPELL_ATTR7_DISABLE_AURA_WHILE_DEAD
    allow_dead_target: bool = False       # SpellInfo::IsAllowingDeadTarget
    static_mask: int = 0                  # _staticApplications[owner] (APPLY_AURA bits only)
    duration: int = 0
    map_timer: int = 0                    # m_updateTargetMapInterval
    removed: bool = False
    remove_mode: str | None = None
    apps: dict[str, Application] = field(default_factory=dict)

    @property
    def permanent(self) -> bool:
        return self.max_duration == -1

    def expired(self) -> bool:
        """Mirrors: SpellAuras.h:226 ``IsExpired`` (no charge-drop event modelled)."""
        return self.duration == 0

    def total_ticks(self, e: EffectState) -> int:
        """Mirrors: SpellAuraEffects.cpp:936 ``AuraEffect::GetTotalTicks``."""
        n = 0
        if e.period and not self.permanent:
            n = self.max_duration // e.period
            if e.extra_initial_period:
                n += 1
        return n

    def area_mask(self) -> int:
        m = 0
        for e in self.effects:
            if e.area:
                m |= 1 << e.index
        return m


class World:
    """A fixed-step owner-update world for one or more parent auras.

    Fixture (dict)::

        {"tick_ms": 100, "until": 3000,
         "units": {"p1": {}, "p2": {}},
         "update_order": ["p1", "d1"],                 # owners (units / dynobjs) in update order
         "auras": [{"id": "A", "spell": 465, "kind": "unit", "owner": "p1", "caster": "p1",
                    "max_duration": -1, "created_at": 0, "creation": "spell-hit",
                    "effects": [{"index": 0, "aura": 22, "period": 0}], "static_mask": 0,
                    "passive": false, "death_persistent": false, "disable_while_dead": false,
                    "allow_dead_target": false,
                    "in_area": {"p1": [[0, null]], "p2": [[0, 1250]]}}],   # FillTargetMap selection, per unit
         "no_stack": [["B", "A"]],                     # B !CanStackWith A (fixture-stated, Track A)
         "events": [{"t": 900, "kind": "death", "unit": "p2"}, ...]}

    ``in_area`` is the unit's membership in the aura's FillTargetMap *before* the alive gate
    (the model applies ``CheckTarget``'s dead rejection itself, SpellInfo.cpp:2452); intervals
    are half-open ``[from, to)`` with ``null`` = open ended.  Events at ``t`` run before the
    world step that ends at ``t`` (a session-before-map convention; see AL-U-F-03).
    """

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fx = fixture
        self.tick_ms = int(fixture["tick_ms"])
        if self.tick_ms <= 0:
            raise FailClosed("world: tick_ms must be positive")
        self.until = int(fixture["until"])
        self.alive: dict[str, bool] = {u: bool(v.get("alive", True)) for u, v in fixture["units"].items()}
        self.in_world: dict[str, bool] = {u: True for u in fixture["units"]}
        self.pending: list[dict[str, Any]] = [dict(a) for a in fixture["auras"]]
        self.auras: dict[str, AuraModel] = {}
        self.no_stack = {tuple(p) for p in fixture.get("no_stack", [])}
        self.events = sorted(fixture.get("events", []), key=lambda e: (int(e["t"]), e.get("seq", 0)))
        self.log: list[dict[str, Any]] = []
        self.now = 0
        self.deferred_removals: list[str] = []
        for a in self.pending:
            if a.get("kind") not in ("unit", "dynobj"):
                raise FailClosed(f"world: aura kind {a.get('kind')!r} not modelled")
            if a.get("highest_exclusive") is not None:
                raise FailClosed("world: IsHighestExclusiveAura contests are Track A's (not modelled)")

    # -- logging ------------------------------------------------------------
    def _emit(self, kind: str, aura: str | None = None, unit: str | None = None, **detail: Any) -> None:
        row = {"t": self.now, "event": kind}
        if aura is not None:
            row["aura"] = aura
        if unit is not None:
            row["unit"] = unit
        row.update(detail)
        self.log.append(row)

    # -- selection inputs ---------------------------------------------------
    def _in_area(self, spec: dict[str, Any], unit: str, t: int) -> bool:
        for lo, hi in spec.get("in_area", {}).get(unit, []):
            if lo <= t and (hi is None or t < hi):
                return True
        return False

    def _fill_target_map(self, a: AuraModel) -> dict[str, int]:
        """Selection of this update: static applications + area members passing the alive gate.

        Mirrors: SpellAuras.cpp:2540-2648 (UnitAura) / 2709-2737 (DynObjAura) as far as time goes:
        the owner-dead gate (2543), the static applications (2549-2558, not alive-gated), the area
        part only when the owner is in world (2563), and CheckTarget's dead rejection
        (SpellInfo.cpp:2452) for area members.  Range/relations come from ``in_area``.
        """
        spec = self._spec(a.id)
        targets: dict[str, int] = {}
        if a.kind == "unit":
            if a.disable_while_dead and not self.alive[a.owner]:
                return {}
            if a.static_mask:
                targets[a.owner] = a.static_mask
            if not self.in_world[a.owner]:
                return targets
        amask = a.area_mask()
        if not amask:
            return targets
        for unit in sorted(spec.get("in_area", {})):
            if not self._in_area(spec, unit, self.now):
                continue
            if not self.alive[unit] and not a.allow_dead_target:
                continue
            if not self.in_world.get(unit, False):
                continue
            if a.kind == "unit" and unit == a.owner and spec.get("owner_excluded"):
                continue
            targets[unit] = targets.get(unit, 0) | amask
        return targets

    def _spec(self, aid: str) -> dict[str, Any]:
        for a in self.fx["auras"]:
            if a["id"] == aid:
                return a
        raise FailClosed(f"world: unknown aura {aid!r}")

    def _can_stack_on(self, a: AuraModel, unit: str) -> bool:
        """Mirrors: SpellAuras.cpp:728-744 (skipped for the owner) with CanStackWith inputs:
        dynobj rule computed (SpellAuras.cpp:1643-1648), unit rule fixture-stated (``no_stack``)."""
        if unit == a.owner:
            return True
        for other in self.auras.values():
            if other is a or other.removed or unit not in other.apps:
                continue
            if a.kind == "dynobj" or other.kind == "dynobj":
                if a.caster == other.caster and a.spell == other.spell:
                    return False
                continue
            if (a.id, other.id) in self.no_stack:
                return False
        return True

    # -- aura operations ----------------------------------------------------
    def _create(self, spec: dict[str, Any]) -> None:
        effects = []
        for e in spec["effects"]:
            period = int(e.get("period", 0))
            periodic = is_periodic_aura(int(e["aura"])) and period > 0     # (SpellAuraEffects.cpp:1012-1013)
            effects.append(EffectState(index=int(e["index"]), aura=int(e["aura"]), period=period, periodic=periodic,
                                       extra_initial_period=bool(e.get("extra_initial_period", False)),
                                       area=bool(e.get("area", True))))
        a = AuraModel(id=spec["id"], spell=int(spec["spell"]), kind=spec["kind"], owner=spec["owner"],
                      caster=spec.get("caster", spec["owner"]), effects=effects,
                      max_duration=int(spec["max_duration"]), passive=bool(spec.get("passive", False)),
                      death_persistent=bool(spec.get("death_persistent", False)),
                      disable_while_dead=bool(spec.get("disable_while_dead", False)),
                      allow_dead_target=bool(spec.get("allow_dead_target", False)),
                      static_mask=int(spec.get("static_mask", 0)))
        for e in a.effects:
            if not e.area and not (a.static_mask >> e.index) & 1:
                raise FailClosed(f"world: non-area effect {e.index} of {a.id} is not in static_mask")
        a.duration = a.max_duration                           # SpellAuras.cpp:496-497
        for e in a.effects:                                   # AuraEffect ctor -> CalculatePeriodic(reset=true)
            e.reset_periodic(True)
        self.auras[a.id] = a
        self._emit("aura-created", a.id, detail_owner=a.owner, max_duration=a.max_duration, coord=coord("AuraEffect::ctor"))
        creation = spec.get("creation", "spell-hit")
        if creation == "spell-hit":
            # interval stays 0 (SpellAuras.cpp:480): area recipients wait for the first owner update;
            # the owner's static APPLY_AURA effects are applied at the hit (SpellEffects.cpp:1110, Spell.cpp:3029).
            if a.kind != "unit":
                raise FailClosed("world: spell-hit creation is the UnitAura path")
            if a.static_mask:
                self._apply_new(a, a.owner, a.static_mask, why="static-application-at-hit")
        elif creation in ("add-aura", "dynobj"):
            # ApplyForTargets / _RegisterForTargets + _ApplyEffectForTargets: immediate map update.
            self._update_target_map(a, why=creation)
        else:
            raise FailClosed(f"world: creation {creation!r} not modelled")

    def _apply_new(self, a: AuraModel, unit: str, mask: int, why: str) -> None:
        if not self.alive[unit] and not a.death_persistent:
            # Mirrors Unit.cpp:3508: _CreateAuraApplication returns nullptr for a dead unit.
            self._emit("application-refused-dead", a.id, unit, coord=coord("_CreateAuraApplication.dead"))
            return
        a.apps[unit] = Application(unit, mask, self.now)
        self._emit("apply", a.id, unit, mask=_indices(mask), why=why, duration=a.duration,
                   coord=coord("_CreateAuraApplication"))

    def _unapply(self, a: AuraModel, unit: str, mode: str, why: str) -> None:
        if unit in a.apps:
            del a.apps[unit]
            self._emit("unapply", a.id, unit, mode=mode, why=why, coord=coord("_UnapplyAura"))

    def _remove_aura(self, a: AuraModel, mode: str, why: str) -> None:
        """Mirrors: SpellAuras.cpp:636 ``Aura::_Remove`` -- every application unapplied with one mode."""
        if a.removed:
            return
        a.removed = True
        a.remove_mode = mode
        self._emit("aura-removed", a.id, mode=mode, why=why, recipients=sorted(a.apps), coord=coord("_Remove"))
        for unit in sorted(a.apps):
            self._unapply(a, unit, mode, why="parent-removed")

    def _update_target_map(self, a: AuraModel, why: str = "timer") -> None:
        """Mirrors: SpellAuras.cpp:658-792 ``Aura::UpdateTargetMap(caster, apply=true)``.

        Immunity / CanBeAppliedOn / IsHighestExclusiveAura are not modelled (fixture selection);
        the stacking filter is (``_can_stack_on``).
        """
        if a.removed:
            return
        a.map_timer = UPDATE_TARGET_MAP_INTERVAL
        targets = self._fill_target_map(a)
        before = sorted(a.apps)
        to_remove = [u for u in sorted(a.apps) if u not in targets]
        for unit in sorted(targets):
            mask = targets[unit]
            if unit in a.apps:
                if a.apps[unit].mask != mask:
                    a.apps[unit].mask = mask
                    self._emit("mask-update", a.id, unit, mask=_indices(mask), coord=coord("UpdateTargetMap"))
                continue
            if not self._can_stack_on(a, unit):
                self._emit("blocked-no-stack", a.id, unit, coord=coord("UpdateTargetMap.stack"))
                continue
            self._apply_new(a, unit, mask, why=f"map:{why}")
        for unit in to_remove:
            self._unapply(a, unit, DEFAULT, why="left-target-map")
        self._emit("map-update", a.id, before=before, after=sorted(a.apps), why=why, coord=coord("UpdateTargetMap"))

    def _owner_update(self, a: AuraModel, diff: int) -> None:
        """Mirrors: SpellAuras.cpp:817-853 ``Aura::UpdateOwner`` -- duration, then map timer, then effects."""
        if a.removed:
            return
        if a.duration > 0:                                    # Aura::Update 857-861
            a.duration = max(0, a.duration - diff)
        if a.map_timer <= diff:                               # 839-842
            self._update_target_map(a)
        else:
            a.map_timer -= diff
        for e in a.effects:                                   # 845-846 -> AuraEffect::Update
            self._effect_update(a, e, diff)

    def _effect_update(self, a: AuraModel, e: EffectState, diff: int) -> None:
        """Mirrors: SpellAuraEffects.cpp:1250-1275 ``AuraEffect::Update`` -- one shared timer; each tick
        goes to every application that currently has the effect."""
        if not e.periodic or (a.duration < 0 and not a.passive and not a.permanent):
            return
        total = a.total_ticks(e)
        e.timer += diff
        while e.timer >= e.period:
            e.timer -= e.period
            if not a.permanent and e.ticks_done + 1 > total:
                break
            e.ticks_done += 1
            recipients = sorted(u for u, app in a.apps.items() if app.mask >> e.index & 1)
            self._emit("tick", a.id, effect=e.index, tick=e.ticks_done, recipients=recipients,
                       coord=coord("AuraEffect::Update"))

    # -- events -------------------------------------------------------------
    def _event(self, ev: dict[str, Any]) -> None:
        kind = ev["kind"]
        if kind == "death":
            self._death(ev["unit"])
        elif kind == "resurrect":
            self.alive[ev["unit"]] = True
            self._emit("resurrect", unit=ev["unit"])
        elif kind == "refresh":
            self._refresh(self.auras[ev["aura"]], ev)
        elif kind == "cancel":
            self._cancel(ev["unit"], ev["aura"])
        elif kind == "dispel":
            self._dispel(ev["unit"], ev["aura"])
        elif kind == "leave-world":
            self._leave_world(ev["unit"])
        elif kind == "create":
            spec = next(s for s in self.pending if s["id"] == ev["aura"])
            self._create(spec)
        else:
            raise FailClosed(f"world: event {kind!r} not modelled")

    def _death(self, unit: str) -> None:
        """Mirrors: Unit.cpp:9160 setDeathState -> 4472 ``RemoveAllAurasOnDeath``: applied (non-passive,
        non-death-persistent) applications unapplied BY_DEATH, then such owned auras removed BY_DEATH."""
        self.alive[unit] = False
        self._emit("death", unit=unit, coord=coord("RemoveAllAurasOnDeath"))
        for a in sorted(self.auras.values(), key=lambda x: x.id):
            if a.removed or unit not in a.apps:
                continue
            if not a.passive and not a.death_persistent:
                self._unapply(a, unit, DEATH, why="recipient-death")
        for a in sorted(self.auras.values(), key=lambda x: x.id):
            if a.removed or a.kind != "unit" or a.owner != unit:
                continue
            if not a.passive and not a.death_persistent:
                self._remove_aura(a, DEATH, why="owner-death")

    def _refresh(self, a: AuraModel, ev: dict[str, Any]) -> None:
        """Re-hit of the owner: ``ModStackAmount`` -> ``SetStackAmount`` (ChangeAmount REAPPLY on every
        application, SpellAuras.cpp:1056) -> ``RefreshTimers`` (976: duration, CalculatePeriodic(reset))
        -> DoSpellEffectHit SetMaxDuration/SetDuration (Spell.cpp:3294).  The new duration is an input
        (Track B); ``reset_periodic`` is DoSpellEffectHit's ``resetPeriodicTimer`` (Spell.cpp:3240)."""
        if a.removed:
            raise FailClosed("world: refresh of a removed aura creates a new aura (not a refresh)")
        pandemic = bool(ev.get("pandemic", False))
        reset = bool(ev.get("reset_periodic", True)) and not pandemic
        new = int(ev["duration"])
        a.max_duration = new
        a.duration = new
        for e in a.effects:
            e.reset_periodic(reset)
        self._emit("refresh", a.id, duration=new, reset_periodic=reset, reapplied=sorted(a.apps),
                   coord=coord("RefreshTimers"))

    def _cancel(self, unit: str, aid: str) -> None:
        """Mirrors: SpellHandler.cpp:284 -- ``RemoveOwnedAura`` on the cancelling player only."""
        a = self.auras[aid]
        if a.kind == "unit" and a.owner == unit and not a.removed:
            self._remove_aura(a, CANCEL, why="owner-cancel")
        else:
            self._emit("cancel-ignored", aid, unit, why="not owned by the cancelling unit", coord=coord("CancelAura"))

    def _dispel(self, unit: str, aid: str) -> None:
        """Mirrors: Unit.cpp:4735 ``GetDispellableAuraList`` iterates owned auras only; removal of the
        last stack goes through ``ModStackAmount`` -> ``Remove`` (whole parent, ENEMY_SPELL)."""
        a = self.auras[aid]
        if a.kind == "unit" and a.owner == unit and not a.removed:
            if a.passive:
                self._emit("dispel-ignored", aid, unit, why="passive (Unit.cpp:4746)")
                return
            self._remove_aura(a, ENEMY_SPELL, why="owner-dispelled")
        else:
            self._emit("dispel-ignored", aid, unit, why="not an owned aura of the dispel target",
                       coord=coord("GetDispellableAuraList"))

    def _leave_world(self, unit: str) -> None:
        """Mirrors: Unit.cpp:4348 ``RemoveAreaAurasDueToLeaveWorld`` (via RemoveFromWorld 10287):
        applications of owned auras on other units removed (RemoveAura default mode), then this unit's
        applications of foreign auras.  The unit stops updating (Unit::Update 430)."""
        for a in sorted(self.auras.values(), key=lambda x: x.id):
            if a.removed:
                continue
            if a.kind == "unit" and a.owner == unit:
                for u in sorted(a.apps):
                    if u != unit:
                        self._unapply(a, u, DEFAULT, why="owner-leave-world")
            elif unit in a.apps:
                self._unapply(a, unit, DEFAULT, why="recipient-leave-world")
        self.in_world[unit] = False
        self._emit("leave-world", unit=unit, coord=coord("RemoveAreaAurasDueToLeaveWorld"))

    # -- the loop -------------------------------------------------------------
    def run(self) -> list[dict[str, Any]]:
        order = self.fx["update_order"]
        creations = {a["id"]: int(a["created_at"]) for a in self.pending if a.get("created_at") is not None}
        events = list(self.events)
        for aid, t0 in sorted(creations.items(), key=lambda kv: (kv[1], kv[0])):
            events.append({"t": t0, "kind": "create", "aura": aid, "seq": -1})
        events.sort(key=lambda e: (int(e["t"]), e.get("seq", 0)))
        t = 0
        while True:
            # events stamped in (t - tick, t] run before the step at t
            while events and int(events[0]["t"]) <= t:
                ev = events.pop(0)
                self.now = int(ev["t"])
                self._event(ev)
            self.now = t
            if t > 0:
                self._step(order, self.tick_ms)
            if t + self.tick_ms > self.until:
                break                                         # steps at tick, 2*tick, ... <= until
            t += self.tick_ms
        return self.log

    def _step(self, order: list[str], diff: int) -> None:
        for owner in order:
            owned = [a for a in sorted(self.auras.values(), key=lambda x: x.id) if a.owner == owner]
            if not owned:
                continue
            if owned[0].kind == "unit" and not self.in_world[owner]:
                continue                                      # Unit::Update returns (430)
            for a in owned:
                self._owner_update(a, diff)
            for a in owned:
                if a.removed:
                    continue
                if a.expired():
                    if a.kind == "unit":
                        self._remove_aura(a, EXPIRE, why="expired")    # Unit.cpp:2986
                    else:
                        # DynamicObject::Update 148-160: Remove() -> remove list; RemoveFromWorld ->
                        # RemoveAura -> _Remove(AURA_REMOVE_BY_DEFAULT) at the map's remove-list pass.
                        self.deferred_removals.append(a.id)
        for aid in self.deferred_removals:
            self._remove_aura(self.auras[aid], DEFAULT, why="dynobj-expired (remove list)")
        self.deferred_removals.clear()


def _indices(mask: int) -> list[int]:
    return [i for i in range(32) if mask >> i & 1]


def timeline(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    return World(fixture).run()


def recipient_intervals(log: list[dict[str, Any]], aura: str) -> dict[str, list[list[int | None]]]:
    """Per-unit application intervals ``[applied_at, unapplied_at)`` of one parent aura."""
    open_: dict[str, int] = {}
    out: dict[str, list[list[int | None]]] = defaultdict(list)
    for row in log:
        if row.get("aura") != aura:
            continue
        if row["event"] == "apply":
            open_[row["unit"]] = row["t"]
        elif row["event"] == "unapply":
            out[row["unit"]].append([open_.pop(row["unit"]), row["t"]])
    for u, t0 in open_.items():
        out[u].append([t0, None])
    return {u: v for u, v in sorted(out.items())}


def ticks_by_unit(log: list[dict[str, Any]], aura: str) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for row in log:
        if row.get("aura") == aura and row["event"] == "tick":
            for u in row["recipients"]:
                out[u].append(row["t"])
    return {u: v for u, v in sorted(out.items())}


# ---------------------------------------------------------------------------
# AreaTrigger per-unit pipeline
# ---------------------------------------------------------------------------
def at_timeline(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-unit auras created by AreaTrigger enter actions (ADDAURA / CAST of an aura spell).

    Mirrors: AreaTrigger.cpp:341 ``Update`` (duration first; expiry -> Remove -> RemoveFromWorld ->
    ``HandleUnitEnterExit({}, ByExpire)`` 82-100), 641 ``UpdateTargetList`` every AT update (no 500 ms
    timer), 853 enter-before-exit, 1140 ``DoActions`` (``caster->AddAura`` -> own ``Aura`` per unit),
    1190 ``UndoActions`` -> ``RemoveAurasDueToSpell(spell, casterGuid)`` (every aura of that spell from
    that caster on the unit, whichever AT created it).

    Fixture::

        {"tick_ms": 100, "until": 3000, "caster": "c",
         "ats": [{"id": "at1", "created_at": 0, "duration": 2000, "spell": 81782,
                  "inside": {"p1": [[0, null]]}}],
         "aura": {"max_duration": 1000, "period": 0}}

    The first target list is built by the AT's first ``Update`` (AreaTrigger.cpp:402), not at creation.
    Returns the log; each unit's aura has its own duration (started at enter).  Expiry of the per-unit
    aura while the unit stays inside does not re-add it (the unit is not a new entrant).
    """
    tick = int(fixture["tick_ms"])
    until = int(fixture["until"])
    caster = fixture["caster"]
    aura_spec = fixture["aura"]
    log: list[dict[str, Any]] = []
    inside: dict[str, set[str]] = {a["id"]: set() for a in fixture["ats"]}
    alive_at = {a["id"]: True for a in fixture["ats"]}
    auras: dict[str, dict[str, Any]] = {}        # unit -> {"duration", "from_at"}
    max_d = int(aura_spec["max_duration"])

    def member(at, unit, t):
        return any(lo <= t and (hi is None or t < hi) for lo, hi in at["inside"].get(unit, []))

    def undo(unit, t, at_id, why):
        if unit in auras:
            log.append({"t": t, "event": "unapply", "unit": unit, "at": at_id, "why": why,
                        "aura_from_at": auras[unit]["from_at"], "coord": coord("AT::UndoActions")})
            del auras[unit]

    t = 0
    while t <= until:
        if t > 0:
            # unit-owned per-unit auras: countdown and expiry (Unit::_UpdateSpells), before or after the
            # AT update depending on map object order -- not modelled: units first, stated as AL-U-F-04.
            for unit in sorted(auras):
                if auras[unit]["duration"] > 0:
                    auras[unit]["duration"] = max(0, auras[unit]["duration"] - tick)
                    if auras[unit]["duration"] == 0:
                        log.append({"t": t, "event": "expire", "unit": unit, "coord": coord("_UpdateSpells.expire")})
                        del auras[unit]
        for at in fixture["ats"]:
            aid = at["id"]
            if not alive_at[aid] or t <= int(at["created_at"]):
                continue                                      # first UpdateTargetList: first AT Update (402), not Create
            remaining = int(at["duration"]) - (t - int(at["created_at"]))
            if remaining <= 0:                                # 390-397: duration <= diff -> Remove()
                alive_at[aid] = False
                for unit in sorted(inside[aid]):
                    undo(unit, t, aid, "at-expired")
                inside[aid].clear()
                log.append({"t": t, "event": "at-removed", "at": aid, "coord": coord("AT::RemoveFromWorld")})
                continue
            now_inside = {u for u in at["inside"] if member(at, u, t)}
            for unit in sorted(now_inside - inside[aid]):
                if unit in auras:
                    log.append({"t": t, "event": "refresh", "unit": unit, "at": aid, "duration": max_d,
                                "coord": coord("AddAura")})
                    auras[unit] = {"duration": max_d, "from_at": aid}
                else:
                    auras[unit] = {"duration": max_d, "from_at": aid}
                    log.append({"t": t, "event": "apply", "unit": unit, "at": aid, "duration": max_d,
                                "coord": coord("AT::DoActions")})
            for unit in sorted(inside[aid] - now_inside):
                undo(unit, t, aid, "left-at")
            inside[aid] = now_inside
        t += tick
    _ = caster
    return log


# ---------------------------------------------------------------------------
# canonical timelines
# ---------------------------------------------------------------------------
def _aura(aid="A", owner="o", max_duration=6000, effects=None, **kw) -> dict[str, Any]:
    spec = {"id": aid, "spell": kw.pop("spell", 1), "kind": kw.pop("kind", "unit"), "owner": owner,
            "caster": kw.pop("caster", owner), "max_duration": max_duration, "created_at": kw.pop("created_at", 0),
            "creation": kw.pop("creation", "spell-hit"),
            "effects": effects if effects is not None else [{"index": 0, "aura": 8, "period": 2000}]}
    spec.update(kw)
    return spec


def _world(auras, units, until, events=(), order=None, no_stack=(), tick=100) -> dict[str, Any]:
    return {"tick_ms": tick, "until": until, "units": {u: {} for u in units},
            "update_order": order or sorted({a["owner"] for a in auras}), "auras": auras,
            "events": list(events), "no_stack": [list(p) for p in no_stack]}


#: Each scenario: the question, the competing models it rules out, the fixture, and whether the
#: verbatim-Trinity probe (tools/tc_aura_area_probe) can run it (single unit aura, no events).
SCENARIOS: list[dict[str, Any]] = [
    {"id": "S01-enter-leave-ticks",
     "question": "When does a unit that enters / leaves the area gain / lose the application, and which periodic ticks reach it?",
     "rules_out": ["recipient-map reevaluated continuously (enter/leave at the crossing time)",
                   "per-recipient periodic timer started at application",
                   "per-recipient duration started at application"],
     "probe": True,
     "fixture": _world([_aura(in_area={"o": [[0, None]], "p2": [[0, 1250]], "p3": [[2900, None]], "p4": [[1100, 1950]]})],
                       ["o", "p2", "p3", "p4"], 7000)},
    {"id": "S02-extra-initial-period",
     "question": "With SPELL_ATTR5_EXTRA_INITIAL_PERIOD, does the first tick reach the recipients mapped in the same update?",
     "rules_out": ["effects updated before the target map within UpdateOwner"],
     "probe": True,
     "fixture": _world([_aura(max_duration=3000, effects=[{"index": 0, "aura": 8, "period": 1000, "extra_initial_period": True}],
                              in_area={"o": [[0, None]], "p2": [[0, None]]})], ["o", "p2"], 3500)},
    {"id": "S03-permanent-passive-periodic",
     "question": "Does a permanent passive periodic area aura tick without a tick cap on every current holder?",
     "rules_out": ["GetTotalTicks cap applied to permanent auras"],
     "probe": True,
     "fixture": _world([_aura(max_duration=-1, passive=True, effects=[{"index": 0, "aura": 8, "period": 1000}],
                              in_area={"o": [[0, None]], "p2": [[1500, None]]})], ["o", "p2"], 4000)},
    {"id": "S04-add-aura-creation",
     "question": "An aura created by Unit::AddAura (AreaTrigger ADDAURA, scripts) maps at creation: when is the next map update?",
     "rules_out": ["all unit auras first map at the first owner update"],
     "probe": True,
     "fixture": _world([_aura(creation="add-aura", effects=[{"index": 0, "aura": 22, "period": 0}], max_duration=3000,
                              in_area={"o": [[0, None]], "p2": [[250, None]]})], ["o", "p2"], 1200)},
    {"id": "S05-static-plus-area",
     "question": "Spell with APPLY_AURA (static) + area effect: what does the owner hold at the hit and after the first map?",
     "rules_out": ["area effects applied to the owner at the spell hit"],
     "probe": True,
     "fixture": _world([_aura(static_mask=2, max_duration=2000,
                              effects=[{"index": 0, "aura": 22, "period": 0}, {"index": 1, "aura": 22, "period": 0, "area": False}],
                              in_area={"o": [[0, None]], "p2": [[0, None]]})], ["o", "p2"], 700)},
    {"id": "S06-owner-death",
     "question": "Owner of a non-passive, non-death-persistent area aura dies.",
     "rules_out": ["recipients keep the aura until the next map update", "recipients get natural expiry"],
     "probe": False,
     "fixture": _world([_aura(max_duration=10000, in_area={"o": [[0, None]], "p2": [[0, None]]})], ["o", "p2"], 1500,
                       events=[{"t": 1000, "kind": "death", "unit": "o"}])},
    {"id": "S07-owner-death-death-persistent",
     "question": "Owner of a death-persistent (SPELL_ATTR3_ALLOW_AURA_WHILE_DEAD) area aura dies, later resurrects (Devotion Aura shape).",
     "rules_out": ["owner death removes the area aura", "dead owner keeps its own application"],
     "probe": False,
     "fixture": _world([_aura(max_duration=-1, death_persistent=True, effects=[{"index": 0, "aura": 87, "period": 0}],
                              in_area={"o": [[0, None]], "p2": [[0, None]]})], ["o", "p2"], 3600,
                       events=[{"t": 1000, "kind": "death", "unit": "o"}, {"t": 3000, "kind": "resurrect", "unit": "o"}])},
    {"id": "S08-owner-death-passive-disable-while-dead",
     "question": "Passive area aura with SPELL_ATTR7_DISABLE_AURA_WHILE_DEAD: owner dies, resurrects.",
     "rules_out": ["ATTR7 removes recipients at the death instant"],
     "probe": False,
     "fixture": _world([_aura(max_duration=-1, passive=True, disable_while_dead=True, effects=[{"index": 0, "aura": 22, "period": 0}],
                              in_area={"o": [[0, None]], "p2": [[0, None]]})], ["o", "p2"], 3600,
                       events=[{"t": 1000, "kind": "death", "unit": "o"}, {"t": 3000, "kind": "resurrect", "unit": "o"}])},
    {"id": "S09-owner-death-passive",
     "question": "Passive area aura without ATTR7: owner dies (17 current-player effects have this shape).",
     "rules_out": ["owner death ends every area aura it owns"],
     "probe": False,
     "fixture": _world([_aura(max_duration=-1, passive=True, effects=[{"index": 0, "aura": 4, "period": 0}],
                              in_area={"o": [[0, None]], "p2": [[0, None]]})], ["o", "p2"], 2000,
                       events=[{"t": 1000, "kind": "death", "unit": "o"}])},
    {"id": "S10-recipient-death-resurrect",
     "question": "A non-owner recipient dies and is resurrected inside the area: new application, which duration?",
     "rules_out": ["resurrected recipient gets a fresh duration", "recipient keeps the application while dead"],
     "probe": False,
     "fixture": _world([_aura(max_duration=10000, effects=[{"index": 0, "aura": 22, "period": 0}],
                              in_area={"o": [[0, None]], "p2": [[0, None]]})], ["o", "p2"], 4500,
                       events=[{"t": 1000, "kind": "death", "unit": "p2"}, {"t": 4000, "kind": "resurrect", "unit": "p2"}])},
    {"id": "S11-parent-refresh",
     "question": "The owner is re-hit (refresh): what happens to every recipient's duration and tick schedule?",
     "rules_out": ["refresh only affects the owner's application", "periodic schedule kept on refresh (non-pandemic)"],
     "probe": False,
     "fixture": _world([_aura(max_duration=6000, in_area={"o": [[0, None]], "p2": [[0, None]]})], ["o", "p2"], 9500,
                       events=[{"t": 3000, "kind": "refresh", "aura": "A", "duration": 6000, "reset_periodic": True}])},
    {"id": "S12-cancel",
     "question": "A recipient right-click cancels a foreign area aura; then the owner cancels it.",
     "rules_out": ["recipient cancel removes (or removes then re-adds) its application"],
     "probe": False,
     "fixture": _world([_aura(max_duration=10000, effects=[{"index": 0, "aura": 22, "period": 0}],
                              in_area={"o": [[0, None]], "p2": [[0, None]]})], ["o", "p2"], 2500,
                       events=[{"t": 1000, "kind": "cancel", "unit": "p2", "aura": "A"},
                               {"t": 2000, "kind": "cancel", "unit": "o", "aura": "A"}])},
    {"id": "S13-dispel",
     "question": "A dispel hits a non-owner recipient, then the owner.",
     "rules_out": ["recipient applications are dispel candidates"],
     "probe": False,
     "fixture": _world([_aura(max_duration=10000, effects=[{"index": 0, "aura": 22, "period": 0}],
                              in_area={"o": [[0, None]], "p2": [[0, None]]})], ["o", "p2"], 2500,
                       events=[{"t": 1000, "kind": "dispel", "unit": "p2", "aura": "A"},
                               {"t": 2000, "kind": "dispel", "unit": "o", "aura": "A"}])},
    {"id": "S14-dynobj-lifetime",
     "question": "Persistent area aura: when are recipients applied, and with which remove mode do they lose it at expiry?",
     "rules_out": ["dynobj recipients wait for the first owner update", "dynobj expiry removes with AURA_REMOVE_BY_EXPIRE"],
     "probe": False,
     "fixture": _world([_aura(owner="d1", caster="c", kind="dynobj", creation="dynobj", max_duration=2000,
                              effects=[{"index": 0, "aura": 3, "period": 1000}], in_area={"p1": [[0, None]]})],
                       ["c", "d1", "p1"], 2500)},
    {"id": "S15-dynobj-same-caster-overlap",
     "question": "Two persistent areas of one spell from one caster overlap a unit.",
     "rules_out": ["overlapping same-caster persistent areas stack on a unit", "the second area takes over at the first's expiry instant"],
     "probe": False,
     "fixture": _world([_aura("D1", owner="d1", caster="c", kind="dynobj", creation="dynobj", max_duration=2000,
                              effects=[{"index": 0, "aura": 3, "period": 0}], in_area={"p1": [[0, None]]}),
                        _aura("D2", owner="d2", caster="c", kind="dynobj", creation="dynobj", max_duration=2000, created_at=1000,
                              effects=[{"index": 0, "aura": 3, "period": 0}], in_area={"p1": [[0, None]]})],
                       ["c", "d1", "d2", "p1"], 3100, order=["d1", "d2"])},
    {"id": "S16-two-owners-no-stack",
     "question": "Two owners' non-stacking area auras cover one unit (two Devotion Auras): who holds it, and when does the other take over?",
     "rules_out": ["the newer area aura replaces the older on the unit", "take-over latency independent of owner update order"],
     "probe": False,
     "variants": {"order": [["o1", "o2"], ["o2", "o1"]]},
     "fixture": _world([_aura("A", owner="o1", max_duration=-1, effects=[{"index": 0, "aura": 87, "period": 0}],
                              in_area={"o1": [[0, None]], "p": [[0, 2000]]}),
                        _aura("B", owner="o2", max_duration=-1, effects=[{"index": 0, "aura": 87, "period": 0}],
                              in_area={"o2": [[0, None]], "p": [[0, None]]})],
                       ["o1", "o2", "p"], 3000, order=["o1", "o2"], no_stack=[("B", "A"), ("A", "B")])},
    {"id": "S17-owner-leave-world",
     "question": "The owner leaves the world (logout, despawn, teleport): what happens to recipients and to the owner's own application?",
     "rules_out": ["leave-world removes the parent aura"],
     "probe": False,
     "fixture": _world([_aura(max_duration=-1, effects=[{"index": 0, "aura": 22, "period": 0}],
                              in_area={"o": [[0, None]], "p2": [[0, None]]})], ["o", "p2"], 1500,
                       events=[{"t": 1000, "kind": "leave-world", "unit": "o"}])},
]

AT_SCENARIOS: list[dict[str, Any]] = [
    {"id": "S18-areatrigger-per-unit",
     "question": "AreaTrigger ADDAURA / enter-cast auras: own duration per unit; exit removal; overlapping ATs of one caster.",
     "rules_out": ["AT-created auras share one parent duration", "leaving one of two overlapping same-caster ATs keeps the aura"],
     "fixture": {"tick_ms": 100, "until": 3000, "caster": "c", "aura": {"max_duration": 2000},
                 "ats": [{"id": "at1", "created_at": 0, "duration": 2500, "spell": 81782,
                          "inside": {"p1": [[0, None]], "p2": [[300, 900]], "p3": [[0, 1500]]}},
                         {"id": "at2", "created_at": 0, "duration": 2500, "spell": 81782,
                          "inside": {"p3": [[0, None]]}}]}},
]


def run_scenario(sc: dict[str, Any], order: list[str] | None = None) -> dict[str, Any]:
    fx = dict(sc["fixture"])
    if order is not None:
        fx["update_order"] = order
    log = timeline(fx)
    auras = [a["id"] for a in fx["auras"]]
    return {"intervals": {a: recipient_intervals(log, a) for a in auras},
            "ticks": {a: ticks_by_unit(log, a) for a in auras},
            "removals": [{k: r[k] for k in ("t", "aura", "unit", "mode", "why") if k in r}
                         for r in log if r["event"] == "unapply"],
            "log": log}


def run_all_scenarios() -> list[dict[str, Any]]:
    out = []
    for sc in SCENARIOS:
        res = {"id": sc["id"], "question": sc["question"], "rules_out": sc["rules_out"], "probe": sc["probe"],
               "evidence": ["trinity-consumer", "differential"] if sc["probe"] else ["trinity-consumer"]}
        orders = sc.get("variants", {}).get("order")
        if orders:
            res["variants"] = [{"order": o, **{k: v for k, v in run_scenario(sc, o).items() if k != "log"}} for o in orders]
        else:
            r = run_scenario(sc)
            res.update({k: v for k, v in r.items() if k != "log"})
            res["log"] = [row for row in r["log"] if row["event"] != "map-update"]
        out.append(res)
    for sc in AT_SCENARIOS:
        out.append({"id": sc["id"], "question": sc["question"], "rules_out": sc["rules_out"], "probe": False,
                    "evidence": ["trinity-consumer"], "log": at_timeline(sc["fixture"])})
    return out


# ---------------------------------------------------------------------------
# per-spell profile
# ---------------------------------------------------------------------------
def _attrs(ctx, spell: int) -> dict[str, bool]:
    from procs.enums import attr
    info = ctx.catalog.get(spell)
    if info is None:
        raise FailClosed(f"recipients: spell {spell} not in the catalog")
    names = {
        "passive": "SPELL_ATTR0_PASSIVE",
        "death_persistent": "SPELL_ATTR3_ALLOW_AURA_WHILE_DEAD",
        "disable_while_dead": "SPELL_ATTR7_DISABLE_AURA_WHILE_DEAD",
        "allow_dead_target_attr": "SPELL_ATTR2_ALLOW_DEAD_TARGET",
        "extra_initial_period": "SPELL_ATTR5_EXTRA_INITIAL_PERIOD",
        "pandemic": "SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION",
        "no_aura_cancel": "SPELL_ATTR0_NO_AURA_CANCEL",
        "only_on_ghosts": "SPELL_ATTR3_ONLY_ON_GHOSTS",
    }
    return {k: info.has_attr(attr(v)) for k, v in names.items()}


def profile(ctx, spell: int) -> dict[str, Any]:
    """Lifecycle profile of one spell's aura-map effects (DIFFICULTY_NONE rows)."""
    effects = ctx.data.effects(spell)
    if not effects:
        raise FailClosed(f"recipients: spell {spell} has no DIFFICULTY_NONE SpellEffect rows")
    at = _attrs(ctx, spell)
    misc = ctx.data.row("SpellMisc", spell)
    dur = ctx.data.duration(misc["DurationIndex"]) if misc and misc["DurationIndex"] else None
    rows = []
    pipes = set()
    for e in effects:
        pipe = effect_pipeline(int(e["Effect"]), int(e["EffectAura"]))
        if pipe is None:
            continue
        pipes.add(pipe)
        aura = int(e["EffectAura"])
        row = {
            "effect": int(e["EffectIndex"]), "type": int(e["Effect"]),
            "type_name": AREA_EFFECT.get(int(e["Effect"]), {6: "APPLY_AURA", 179: "CREATE_AREATRIGGER", 353: "CREATE_AREATRIGGER_2"}.get(int(e["Effect"]), str(e["Effect"]))),
            "aura": aura, "pipeline": pipe,
            "periodic": is_periodic_aura(aura) and int(e["EffectAuraPeriod"]) > 0,
            "period": int(e["EffectAuraPeriod"]),
        }
        if pipe in ("unit-area", "dynobj"):
            row["owner_receives"] = OWNER_RECEIVES[int(e["Effect"])]
        rows.append(row)
    if not rows:
        raise FailClosed(f"recipients: spell {spell} has no aura-map / aura / areatrigger effect")
    lifecycle = []
    if "unit-area" in pipes:
        lifecycle += [
            {"q": "initial recipients", "a": "first owner update after the hit (interval 0); only static APPLY_AURA effects apply at the hit",
             "coords": [coord("ctor.interval0"), coord("UpdateOwner.timer"), coord("EffectApplyAura")], "evidence": "trinity-consumer"},
            {"q": "reevaluation cadence", "a": "every 500 ms of owner update time (UPDATE_TARGET_MAP_INTERVAL), counted in owner diffs",
             "coords": [coord("UpdateOwner.timer")], "evidence": "trinity-consumer"},
            {"q": "duration/stacks/amount of a recipient", "a": "parent-owned (one Aura); a late recipient gets the remaining duration",
             "coords": [coord("Aura::Update"), coord("AuraEffect::CalculateAmount")], "evidence": "trinity-consumer"},
            {"q": "owner death", "a": "non-passive, non-death-persistent: parent removed BY_DEATH on every recipient; passive: parent survives"
             + (" but ATTR7 empties the map at the next update" if at["disable_while_dead"] else "; recipients keep it (owner leaves its own map: dead)"),
             "coords": [coord("RemoveAllAurasOnDeath"), coord("FillTargetMap.deadowner")], "evidence": "trinity-consumer"},
        ]
    if "dynobj" in pipes:
        lifecycle += [
            {"q": "initial recipients", "a": "at cast: _RegisterForTargets + _ApplyEffectForTargets",
             "coords": [coord("EffectPersistentAA.register")], "evidence": "trinity-consumer"},
            {"q": "expiry remove mode", "a": "recipients unapplied AURA_REMOVE_BY_DEFAULT via the dynobj remove list (not BY_EXPIRE)",
             "coords": [coord("DynamicObject::Update"), coord("DynamicObject::RemoveAura")], "evidence": "trinity-consumer"},
            {"q": "caster death", "a": "no removal path: the dynobj keeps updating (RemoveAllDynObjects runs only on leave-world)",
             "coords": [coord("setDeathState"), coord("RemoveFromWorld")], "evidence": "trinity-consumer"},
        ]
    periodic_area = [r for r in rows if r["periodic"] and r["pipeline"] in ("unit-area", "dynobj")]
    if periodic_area:
        lifecycle.append({"q": "tick attribution", "a": "one shared timer per AuraEffect; each tick goes to every application holding the effect at that update (no per-recipient timer, no join tick)",
                          "coords": [coord("AuraEffect::Update"), coord("GetApplicationList")], "evidence": "trinity-probe"})
    return {
        "spell": spell, "name": ctx.name(spell), "build_skew": bool(ctx.is_skew(spell)),
        "effects": rows, "pipelines": sorted(pipes), "attributes": at,
        "duration": None if dur is None else {k: dur[k] for k in ("Duration", "MaxDuration")},
        "lifecycle": lifecycle,
    }


# ---------------------------------------------------------------------------
# census
# ---------------------------------------------------------------------------
def census(ctx) -> dict[str, Any]:
    """Area-aura providers by pipeline, effect type and lifecycle-relevant flags, per population."""
    from procs.enums import attr

    from .providers import populations, provider_effects
    pops = populations(ctx)
    flags = {
        "passive": attr("SPELL_ATTR0_PASSIVE"),
        "death_persistent": attr("SPELL_ATTR3_ALLOW_AURA_WHILE_DEAD"),
        "disable_while_dead": attr("SPELL_ATTR7_DISABLE_AURA_WHILE_DEAD"),
        "extra_initial_period": attr("SPELL_ATTR5_EXTRA_INITIAL_PERIOD"),
        "pandemic": attr("SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION"),
        "cooldown_on_event": attr("SPELL_ATTR0_COOLDOWN_ON_EVENT"),
    }
    rows = []
    for r in provider_effects(ctx.data):
        pipe = effect_pipeline(r["effect"], r["aura"])
        if pipe not in ("unit-area", "dynobj"):
            continue
        info = ctx.catalog.get(r["spell"])
        e = ctx.data.table("SpellEffect")[(r["spell"], 0)][r["index"]]
        period = int(e["EffectAuraPeriod"])
        rows.append({
            "spell": r["spell"], "effect": r["index"], "type": r["effect"], "type_name": AREA_EFFECT[r["effect"]],
            "aura": r["aura"], "pipeline": pipe,
            "periodic": is_periodic_aura(r["aura"]) and period > 0, "period": period,
            "flags": sorted(k for k, v in flags.items() if info is not None and info.has_attr(v)),
            "populations": sorted(k for k, s in pops.items() if r["spell"] in s),
        })
    out: dict[str, Any] = {"rows_total": len(rows), "by_population": {}}
    for pop in ("all", "player", "controlled"):
        sel = [x for x in rows if pop in x["populations"]]
        out["by_population"][pop] = {
            "effects": len(sel), "spells": len({x["spell"] for x in sel}),
            "by_pipeline": dict(sorted(Counter(x["pipeline"] for x in sel).items())),
            "by_type": dict(sorted(Counter(x["type_name"] for x in sel).items())),
            "periodic_effects": sum(1 for x in sel if x["periodic"]),
            "by_flag": dict(sorted(Counter(f for x in sel for f in x["flags"]).items())),
            "passive_without_disable_while_dead": sum(
                1 for x in sel if "passive" in x["flags"] and "disable_while_dead" not in x["flags"]),
            "owner_never_receives": sum(1 for x in sel if OWNER_RECEIVES[x["type"]] == "no"),
        }
    player = [x for x in rows if "player" in x["populations"] or "controlled" in x["populations"]]
    out["player_rows"] = sorted(player, key=lambda x: (x["spell"], x["effect"]))
    periodic = sorted((x for x in rows if x["periodic"]), key=lambda x: (x["spell"], x["effect"]))
    out["periodic_by_aura"] = dict(sorted(Counter(str(x["aura"]) for x in periodic).items()))
    out["periodic_examples"] = [{"spell": x["spell"], "effect": x["effect"], "type_name": x["type_name"],
                                 "aura": x["aura"], "period": x["period"]} for x in periodic[:12]]
    return out


def areatrigger_aura_rows() -> dict[str, Any]:
    """AreaTrigger-created per-unit auras, read from the targeting corpus (``areatriggers.json``)."""
    import json

    from . import ROOT
    path = ROOT / "docs" / "research" / "targeting-corpora" / "areatriggers.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for r in doc["rows"]:
        cp = r.get("create_properties") or {}
        actions = [a for a in cp.get("actions", []) if a.get("type") in ("ADDAURA", "CAST")]
        script = r.get("script") if isinstance(r.get("script"), dict) else None
        enter = script.get("enter", []) if script else []
        exit_ = script.get("exit", []) if script else []
        if not actions and not enter:
            continue
        rows.append({
            "key": r["key"], "name": r["name"], "class": r["class"], "build_skew": r["build_skew"],
            "template_actions": actions,
            "script": None if not script else {"name": script.get("name"), "file_line": script.get("file_line"),
                                               "enter": enter, "exit": exit_},
            # ADDAURA always creates an aura owned by the unit; enter *casts* do so only when the cast
            # spell applies an aura, which this row does not classify unless the exit removes an aura.
            "per_unit_aura": "yes" if any(a.get("type") == "ADDAURA" for a in actions) or
                             any("remove_aura" in x for x in exit_) else "unclassified",
            "exit_removes": bool(actions) or bool(exit_),
        })
    rows.sort(key=lambda x: x["key"])
    return {"rows": rows, "source": "docs/research/targeting-corpora/areatriggers.json (targeting.py areatriggers)",
            "count": len(rows), "per_unit_aura": sum(1 for r in rows if r["per_unit_aura"] == "yes")}


__all__ = ["AREA_EFFECT", "build_corpus", "LIFECYCLE_TABLE", "STATE_OWNERSHIP", "AuraModel", "OWNER_RECEIVES", "PERIODIC_AURAS", "UPDATE_TARGET_MAP_INTERVAL", "World",
           "areatrigger_aura_rows", "at_timeline", "census", "effect_pipeline", "is_periodic_aura", "profile",
           "recipient_intervals", "run_all_scenarios", "run_scenario", "SCENARIOS", "AT_SCENARIOS",
           "ticks_by_unit", "timeline"]


# ---------------------------------------------------------------------------
# corpus: lifecycle table, state ownership, cross-cutting records
# ---------------------------------------------------------------------------
def _c(*keys: str) -> list[str]:
    return [coord(k) for k in keys]


#: The area lifecycle table: one row per lifecycle question, one answer per pipeline.
LIFECYCLE_TABLE: list[dict[str, Any]] = [
    {"question": "initial recipient set",
     "unit-area": "static APPLY_AURA effects on the hit unit at the hit; every area recipient (owner included) at the first owner update after creation (interval starts at 0); AddAura-created: at creation",
     "dynobj": "at cast: _RegisterForTargets (create) + _ApplyEffectForTargets (apply)",
     "areatrigger": "first AT Update after creation (UpdateTargetList), then each entering unit gets its own aura",
     "coords": _c("ctor.interval0", "UpdateOwner.timer", "EffectApplyAura", "DoDamageAndTriggers.apply", "ApplyForTargets",
                  "EffectPersistentAA.register", "AT::Update", "AT::DoActions"),
     "evidence": ["trinity-consumer", "trinity-probe"], "timelines": ["S01", "S04", "S05", "S14", "S18"]},
    {"question": "reevaluation cadence",
     "unit-area": "every 500 ms of accumulated owner update diff (m_updateTargetMapInterval <= diff -> map, reset to 500)",
     "dynobj": "same timer, driven by DynamicObject::Update",
     "areatrigger": "every AT update (no interval)",
     "coords": _c("UpdateOwner.timer", "UpdateTargetMap", "DynamicObject::Update", "AT::UpdateTargetList"),
     "evidence": ["trinity-consumer", "trinity-probe"], "timelines": ["S01", "S04"]},
    {"question": "enter / leave latency",
     "unit-area": "enter: applied at the next map update (<= 500 ms + one update); leave: kept (and ticked) until the next map update, then unapplied BY_DEFAULT",
     "dynobj": "same", "areatrigger": "next AT update; exit runs UndoActions (RemoveAurasDueToSpell(spell, caster))",
     "coords": _c("UpdateTargetMap", "UpdateTargetMap.remove", "AT::HandleUnitEnterExit", "AT::UndoActions"),
     "evidence": ["trinity-consumer", "trinity-probe"], "timelines": ["S01", "S18"]},
    {"question": "recipient duration / stacks / charges / amount",
     "unit-area": "parent-owned: a late recipient receives the remaining parent duration and the parent's amount (computed with the owner as target)",
     "dynobj": "parent-owned (the dynobj aura)", "areatrigger": "per-unit aura: own duration from its enter",
     "coords": _c("Aura::Update", "AuraEffect::CalculateAmount", "AT::DoActions", "AddAura"),
     "evidence": ["trinity-consumer", "trinity-probe"], "timelines": ["S01", "S10", "S18"]},
    {"question": "periodic tick attribution",
     "unit-area": "one timer per AuraEffect; each tick is delivered to every application holding the effect in that update; no join tick, no per-recipient phase; a leaver not yet unmapped still ticks",
     "dynobj": "same", "areatrigger": "per-unit aura: own timer from its enter",
     "coords": _c("AuraEffect::Update", "GetApplicationList", "UpdateOwner"),
     "evidence": ["trinity-consumer", "trinity-probe"], "timelines": ["S01", "S02", "S03", "S14"]},
    {"question": "parent refresh",
     "unit-area": "re-hit of the owner refreshes the one parent: every recipient sees the new duration and (non-pandemic) the reset tick schedule; ChangeAmount(REAPPLY) runs on every application",
     "dynobj": "no refresh path: each cast creates a new DynamicObject + aura (same-caster overlap blocked per unit)",
     "areatrigger": "re-entry (or a second AT) refreshes the unit's own aura",
     "coords": _c("RefreshTimers", "SetStackAmount", "ChangeAmount", "DoSpellEffectHit", "CanStackWith.dynobj"),
     "evidence": ["trinity-consumer"], "timelines": ["S11", "S15", "S18"]},
    {"question": "parent expiry",
     "unit-area": "Unit::_UpdateSpells removes the expired parent after all owner updates: every recipient BY_EXPIRE; the tick at duration 0 is delivered first",
     "dynobj": "DynamicObject::Update -> remove list -> _Remove(AURA_REMOVE_BY_DEFAULT): recipients never see BY_EXPIRE",
     "areatrigger": "AT expiry: HandleUnitEnterExit({}, ByExpire) runs UndoActions on every inside unit (unless DontRunOnLeaveWhenExpiring suppresses only the hooks, not UndoActions)",
     "coords": _c("_UpdateSpells.expire", "DynamicObject::Update", "DynamicObject::RemoveAura", "AT::RemoveFromWorld"),
     "evidence": ["trinity-consumer", "trinity-probe"], "timelines": ["S01", "S14", "S18"]},
    {"question": "owner death",
     "unit-area": "non-passive and non-death-persistent: parent removed BY_DEATH from every recipient at the death; passive or death-persistent: parent survives, the dead owner leaves its own map at the next update (CheckTarget TARGETS_DEAD) while others keep it; ATTR7_DISABLE_AURA_WHILE_DEAD empties the map at the next update",
     "dynobj": "caster death has no removal path (dynobjs are removed on leave-world only)",
     "areatrigger": "no death path in AreaTrigger.cpp (caster despawn: RemoveAllAreaTriggers on leave-world)",
     "coords": _c("setDeathState", "RemoveAllAurasOnDeath", "FillTargetMap.deadowner", "CheckTarget.dead", "RemoveFromWorld"),
     "evidence": ["trinity-consumer"], "timelines": ["S06", "S07", "S08", "S09"]},
    {"question": "recipient death / resurrection",
     "unit-area": "the recipient's own application unapplied BY_DEATH (unless passive / death-persistent); the parent continues; after resurrection a new application of the same parent at the next map update (remaining duration)",
     "dynobj": "same", "areatrigger": "templated ATs drop dead/ghost players unless action-set flags allow (AreaTrigger.cpp:687-701); the per-unit aura follows the normal death path",
     "coords": _c("RemoveAllAurasOnDeath", "_CreateAuraApplication.dead", "CheckTarget.dead", "AT::UpdateTargetList"),
     "evidence": ["trinity-consumer"], "timelines": ["S10"]},
    {"question": "cancel / dispel on a recipient",
     "unit-area": "ignored for non-owners (cancel: RemoveOwnedAura on the player; dispel list: owned auras only); on the owner the whole parent goes (CANCEL / ENEMY_SPELL)",
     "dynobj": "never cancellable / dispellable by a recipient (no unit owns it)",
     "areatrigger": "per-unit aura is owned by the recipient: normal cancel / dispel; the AT does not re-add it until re-entry",
     "coords": _c("CancelAura", "GetDispellableAuraList", "RemoveAurasDueToSpellByDispel"),
     "evidence": ["trinity-consumer"], "timelines": ["S12", "S13"]},
    {"question": "owner leaves the world",
     "unit-area": "applications on other units removed (default mode); the owner's own application and the parent persist (the owner stops updating)",
     "dynobj": "caster leave-world: RemoveAllDynObjects", "areatrigger": "caster leave-world: RemoveAllAreaTriggers(UnitDespawn)",
     "coords": _c("RemoveAreaAurasDueToLeaveWorld", "RemoveFromWorld", "Unit::Update"),
     "evidence": ["trinity-consumer"], "timelines": ["S17"]},
    {"question": "two sources cover one unit",
     "unit-area": "a non-stacking second parent is refused while the first application exists (UpdateTargetMap stack filter; owner exempt); take-over at the take-over parent's next map update after the holder unmaps -> latency depends on owner update order",
     "dynobj": "same caster + same spell never stack on a unit (CanStackWith dynobj rule)",
     "areatrigger": "same caster: one aura, refreshed; any AT exit removes it (even if still inside the other AT)",
     "coords": _c("UpdateTargetMap.stack", "CanStackWith.dynobj", "AT::UndoActions"),
     "evidence": ["trinity-consumer"], "timelines": ["S15", "S16", "S18"]},
]

STATE_OWNERSHIP: list[dict[str, Any]] = [
    {"state": "duration / max duration", "owner": "parent", "coords": _c("Aura::Update")},
    {"state": "stack amount / charges", "owner": "parent", "coords": _c("SetStackAmount")},
    {"state": "effect amount (_amount, base amount)", "owner": "parent", "coords": _c("AuraEffect::CalculateAmount", "ChangeAmount"),
     "note": "computed once with the aura owner (not the recipient) as target; per-target modifiers only inside tick/handler code (Track D)"},
    {"state": "periodic timer / ticks done / period", "owner": "parent (per AuraEffect)", "coords": _c("AuraEffect::Update", "ResetPeriodic")},
    {"state": "recipient-map timer", "owner": "parent", "coords": _c("UpdateOwner.timer")},
    {"state": "applied effect mask / effects to apply", "owner": "application", "coords": _c("UpdateTargetMap")},
    {"state": "remove mode", "owner": "application (the parent's _Remove passes one mode to all)", "coords": _c("_UnapplyAura", "_Remove")},
    {"state": "visible slot, positive/negative flag", "owner": "application", "coords": [T + "Spells/Auras/SpellAuras.cpp:72"]},
    {"state": "diminishing registration", "owner": "application (UnitAura::_ApplyForTarget per recipient)", "coords": [T + "Spells/Auras/SpellAuras.cpp:2515"]},
    {"state": "cooldown-on-event start / event", "owner": "caster, but triggered per application", "coords": _c("_ApplyForTarget", "_UnapplyForTarget"),
     "note": "see AL-D-F-02"},
]

RULES: list[dict[str, Any]] = [
    {"id": "AL-R-F-01", "name": "one-parent-duration",
     "definition": "Every recipient of a unit/dynobj area aura holds an application of one parent Aura; its remaining duration, stacks, charges and amount are the parent's (a late or re-entering recipient gets the remaining duration).",
     "population": {"name": "all area-aura provider effects (unit-area + dynobj)", "count": None},
     "counterexamples": ["AreaTrigger-created auras (own Aura per unit) -- a different pipeline, not a counterexample"],
     "status": "holds-on-census", "evidence": ["trinity-consumer", "trinity-probe"]},
    {"id": "AL-R-F-02", "name": "500ms-recipient-map",
     "definition": "The recipient map is recomputed when m_updateTargetMapInterval <= diff, then reset to 500; a spell-hit UnitAura first maps at its first owner update, AddAura / dynobj auras map at creation.",
     "population": {"name": "all area-aura provider effects", "count": None}, "counterexamples": [],
     "status": "holds-on-census", "evidence": ["trinity-consumer", "trinity-probe"]},
    {"id": "AL-R-F-03", "name": "shared-tick-to-current-holders",
     "definition": "A periodic area effect ticks on one timer per AuraEffect; each tick reaches exactly the applications holding the effect during that owner update (map updated before effects).",
     "population": {"name": "periodic area-aura effects (all)", "count": None},
     "counterexamples": ["script OnEffectUpdatePeriodic / OnEffectPeriodic hooks may prevent a tick per application"],
     "status": "refined", "evidence": ["trinity-consumer", "trinity-probe"]},
    {"id": "AL-R-F-04", "name": "parent-removal-propagates",
     "definition": "Removing the parent (expire, owner death, cancel/dispel on the owner) unapplies every recipient with one remove mode; dynobj expiry uses AURA_REMOVE_BY_DEFAULT.",
     "population": {"name": "all area-aura provider effects", "count": None}, "counterexamples": [],
     "status": "holds-on-census", "evidence": ["trinity-consumer"]},
    {"id": "AL-R-F-05", "name": "recipient-side-requests-stop-at-application",
     "definition": "Cancel and dispel aimed at a non-owner recipient do nothing; recipient death / leave-world unapplies only that application and the parent continues.",
     "population": {"name": "unit-area + dynobj recipients that are not the owner", "count": None}, "counterexamples": [],
     "status": "holds-on-census", "evidence": ["trinity-consumer"]},
    {"id": "AL-R-F-06", "name": "owner-death-policy",
     "definition": "Owner death removes a non-passive, non-death-persistent area aura BY_DEATH; passive or death-persistent parents survive and keep serving living recipients (ATTR7 empties the map at the next update).",
     "population": {"name": "unit-area provider effects by flag", "count": None},
     "counterexamples": ["script death hooks (Track H)"], "status": "trinity-only", "evidence": ["trinity-consumer", "retail-unknown"]},
    {"id": "AL-R-F-07", "name": "areatrigger-per-unit-aura",
     "definition": "AreaTrigger ADDAURA / enter-cast auras are separate Aura objects owned by each unit, with their own duration and timers; exit removes every aura of that spell from that caster on the unit.",
     "population": {"name": "current-player AT rows whose actions/scripts give each unit its own aura (targeting areatriggers corpus)", "count": None},
     "counterexamples": [], "status": "holds-on-census", "evidence": ["trinity-consumer", "world-db-fact", "script-consumer"]},
]

FALSIFICATION: list[dict[str, Any]] = [
    {"id": "AL-F-F-01", "rule": "AL-R-F-01", "attempt": "model: each recipient gets a fresh duration at its application",
     "result": "died: one m_duration per Aura (SpellAuras.cpp:855); probe S01 shows p3 applied at 3100 with 2900 left and expiring with the owner",
     "action": "kept one-parent-duration"},
    {"id": "AL-F-F-02", "rule": "AL-R-F-03", "attempt": "model: a late joiner starts its own periodic timer (first tick one period after joining) or gets a join tick",
     "result": "died: _periodicTimer is per AuraEffect (SpellAuraEffects.cpp:1250); probe S01 p3 joins 3100 and ticks at 4000 (900 ms later)",
     "action": "kept shared-tick-to-current-holders"},
    {"id": "AL-F-F-03", "rule": "AL-R-F-02", "attempt": "model: a unit leaving the radius loses the aura at the crossing time",
     "result": "died: UpdateTargetMap runs every 500 ms; probe S01 p4 leaves 1950, still ticks at 2000, unapplied 2100",
     "action": "kept 500ms-recipient-map"},
    {"id": "AL-F-F-04", "rule": "AL-R-F-02", "attempt": "model: every area aura first maps at its first owner update",
     "result": "refined: Unit::AddAura (ApplyForTargets) and PERSISTENT_AREA_AURA (_RegisterForTargets) map at creation; probe S04",
     "action": "rule split by creation path"},
    {"id": "AL-F-F-05", "rule": "AL-R-F-06", "attempt": "model: owner death ends every area aura it owns",
     "result": "refined: RemoveAllAurasOnDeath skips passive and ALLOW_AURA_WHILE_DEAD (Unit.cpp:4472-4492); census counts both shapes in the player population",
     "action": "rule restated per flag; Retail experiments AL-X-F-02/03"},
    {"id": "AL-F-F-06", "rule": "AL-R-F-05", "attempt": "model: a recipient can cancel or have dispelled its copy of a foreign area aura",
     "result": "died: HandleCancelAuraOpcode uses RemoveOwnedAura (SpellHandler.cpp:284); GetDispellableAuraList iterates owned auras (Unit.cpp:4737)",
     "action": "kept; Retail experiment AL-X-F-05 for cancel"},
    {"id": "AL-F-F-07", "rule": "AL-R-F-04", "attempt": "model: persistent area aura recipients are removed BY_EXPIRE",
     "result": "died: DynamicObject::RemoveAura -> _Remove(AURA_REMOVE_BY_DEFAULT) (DynamicObject.cpp:198-205)",
     "action": "rule records the exception; AL-D-F-03"},
    {"id": "AL-F-F-08", "rule": "AL-R-F-01", "attempt": "model: the area effect amount is computed per recipient",
     "result": "refined: AuraEffect::CalculateAmount passes the aura owner as target (SpellAuraEffects.cpp:777-782); per-target factors live in tick handlers (Track D)",
     "action": "amount listed as parent-owned"},
    {"id": "AL-F-F-09", "rule": "AL-R-F-03", "attempt": "search for current-player periodic area-aura effects (to witness tick attribution)",
     "result": "none: 0 of 35 current-player area-aura effects are periodic; the rule is witnessed only in the all-population (NPC) census",
     "action": "kept; priority of AL-X-F-04 lowered"},
    {"id": "AL-F-F-10", "rule": "AL-R-F-07", "attempt": "model: an AT-created aura follows the AT and is re-added after it expires while the unit stays inside",
     "result": "died: DoActions runs only for entering units (AreaTrigger.cpp:853-877); timeline S18 p1 expires at 2100 inside the AT",
     "action": "kept per-unit rule"},
]

DEFECTS: list[dict[str, Any]] = [
    {"id": "AL-D-F-01", "coords": [coord("AT::UndoActions")],
     "description": "UndoActions removes every aura of the action spell from the AT caster on the exiting unit (RemoveAurasDueToSpell(spell, casterGuid)), including one refreshed by another overlapping AT of the same caster the unit is still inside.",
     "lifecycle_effect": "unit loses the aura although still inside an AT that grants it; not re-added until re-entry",
     "oracle_behaviour": "reproduced in at_timeline (S18 p3 at 1500); no current-player AT known to overlap itself"},
    {"id": "AL-D-F-02", "coords": [coord("_ApplyForTarget"), coord("_UnapplyForTarget")],
     "description": "Cooldown-on-event handling runs per application: every new recipient restarts the infinite cooldown and every recipient that leaves sends the cooldown event, while the parent aura is still active.",
     "lifecycle_effect": "caster cooldown can start when any recipient leaves an area aura with SPELL_ATTR0_COOLDOWN_ON_EVENT (or a CooldownEventOnLeaveCombat category)",
     "oracle_behaviour": "flagged by census flag cooldown_on_event; not simulated"},
    {"id": "AL-D-F-03", "coords": [coord("DynamicObject::Update"), coord("DynamicObject::RemoveAura")],
     "description": "Persistent-area-aura expiry reaches recipients as AURA_REMOVE_BY_DEFAULT (via the remove list) instead of BY_EXPIRE.",
     "lifecycle_effect": "remove-mode-gated script hooks (GetRemoveMode() == EXPIRE) never run for dynobj recipients",
     "oracle_behaviour": "reproduced (S14)"},
]

UNKNOWNS: list[dict[str, Any]] = [
    {"id": "AL-U-F-01", "subject": "passive area aura, dead owner",
     "question": "In Retail, do recipients keep a passive area aura (e.g. Blessing of Dawn 183416:1) while its owner is dead?",
     "known": "Trinity: yes, only the dead owner drops its own application (S09); 17 current-player effects are passive without ATTR7",
     "why_unresolved": "Trinity default, no Retail observation", "evidence": ["trinity-consumer", "retail-unknown"],
     "coords": _c("RemoveAllAurasOnDeath", "CheckTarget.dead"), "blocker": "Retail observation",
     "reopen_condition": "AL-X-F-03 result", "build_skew": False},
    {"id": "AL-U-F-02", "subject": "death-persistent paladin auras",
     "question": "In Retail, does a dead paladin keep its own Devotion / Concentration / Crusader Aura application, and do living party members keep theirs?",
     "known": "Trinity: parent survives (ALLOW_AURA_WHILE_DEAD); the dead owner is dropped from its own map at the next update (TARGETS_DEAD) and re-added after resurrection (S07)",
     "why_unresolved": "Trinity mechanism (CheckTarget on the owner) is incidental; no Retail observation",
     "evidence": ["trinity-consumer", "retail-unknown"], "coords": _c("CheckTarget.dead", "RemoveAllAurasOnDeath"),
     "blocker": "Retail observation", "reopen_condition": "AL-X-F-02 result", "build_skew": False},
    {"id": "AL-U-F-03", "subject": "event vs owner-update ordering",
     "question": "Where inside a server tick do a cast / death / refresh land relative to the owner's aura update (the first owner update after creation receives the full world diff)?",
     "known": "Model convention: events at t run before the owner update step ending at t; WorldSession updates precede map updates",
     "why_unresolved": "Track I owns same-timestamp ordering", "evidence": ["structural-inference", "unresolved"],
     "coords": _c("Unit::Update", "UpdateOwner"), "blocker": "Track I ordering model",
     "reopen_condition": "Track I publishes the world-tick ordering model", "build_skew": False},
    {"id": "AL-U-F-04", "subject": "AreaTrigger vs unit update order",
     "question": "Does an AT's UpdateTargetList run before or after the per-unit aura countdown of the same map tick?",
     "known": "both are map object updates; order is map container order", "why_unresolved": "not proven from call paths",
     "evidence": ["unresolved"], "coords": _c("AT::Update", "_UpdateSpells"), "blocker": "Map::Update object iteration order",
     "reopen_condition": "Track I or a probe of Map::Update", "build_skew": False},
    {"id": "AL-U-F-05", "subject": "Retail recipient-map cadence",
     "question": "Is Retail's area-aura enter/leave latency bounded by a 500 ms reevaluation like Trinity's?",
     "known": "Trinity constant UPDATE_TARGET_MAP_INTERVAL = 500", "why_unresolved": "server constant, not in client data",
     "evidence": ["trinity-consumer", "retail-unknown"], "coords": [T + "Spells/Auras/SpellAuras.h:58"],
     "blocker": "Retail observation", "reopen_condition": "AL-X-F-01 result", "build_skew": False},
    {"id": "AL-U-F-06", "subject": "tick delivery order within one tick",
     "question": "Which recipient receives a shared tick first (matters for procs / deaths inside the tick loop)?",
     "known": "Trinity iterates std::unordered_map<ObjectGuid, ...> (hash order, undefined)",
     "why_unresolved": "unspecified container order", "evidence": ["trinity-consumer", "unresolved"],
     "coords": _c("GetApplicationList"), "blocker": "none in data; Retail observation only",
     "reopen_condition": "a consumer depends on intra-tick order", "build_skew": False},
    {"id": "AL-U-F-07", "subject": "late-joiner periodic attribution in Retail",
     "question": "Does a unit entering a periodic area aura in Retail tick on the parent's schedule (shared timer) or on its own?",
     "known": "Trinity: shared; current-player population has no periodic area-aura effect (0/35)",
     "why_unresolved": "no Retail observation; no player witness", "evidence": ["trinity-probe", "retail-unknown"],
     "coords": _c("AuraEffect::Update"), "blocker": "needs an NPC periodic area aura", "reopen_condition": "AL-X-F-04 result",
     "build_skew": False},
]

EXPERIMENTS: list[dict[str, Any]] = [
    {"id": "AL-X-F-01", "question": "Leave / enter latency of a raid aura (Devotion Aura 465:0).",
     "models": [{"name": "trinity-500ms", "prediction": "SPELL_AURA_REMOVED 0-600 ms after crossing the radius, quantised to the owner's 500 ms map schedule"},
                {"name": "continuous", "prediction": "removal within one server tick of the crossing"}],
     "setup": "paladin with Devotion Aura stationary; party member walks out of range on a known path; repeat 20x",
     "observable": "combat-log SPELL_AURA_APPLIED/REMOVED timestamps vs position trace",
     "discriminates": "reevaluation cadence (AL-R-F-02)", "fidelity": "approximate", "related": ["AL-U-F-05"]},
    {"id": "AL-X-F-02", "question": "Paladin dies with Devotion Aura active.",
     "models": [{"name": "trinity", "prediction": "party keeps Devotion Aura; the dead paladin loses its own within 500 ms; regained after resurrection"},
                {"name": "all-kept", "prediction": "paladin and party keep it"},
                {"name": "all-removed", "prediction": "aura removed from everyone at death"}],
     "setup": "2-player party, paladin dies (fall damage), inspect both buff bars, then resurrect",
     "observable": "UNIT_AURA on both units; combat log", "discriminates": "owner-death policy for death-persistent parents",
     "fidelity": "exact", "related": ["AL-U-F-02", "AL-R-F-06"]},
    {"id": "AL-X-F-03", "question": "Owner of a passive party aura dies (Blessing of Dawn 183416:1 / Time of Need 368412:0).",
     "models": [{"name": "trinity", "prediction": "living recipients keep it; owner drops its own"},
                {"name": "removed", "prediction": "recipients lose it at death"}],
     "setup": "party of two, owner dies in range", "observable": "UNIT_AURA on the recipient",
     "discriminates": "passive parent survives owner death", "fidelity": "exact", "related": ["AL-U-F-01"]},
    {"id": "AL-X-F-04", "question": "Late joiner of a periodic NPC area aura: when is its first tick?",
     "models": [{"name": "shared-timer", "prediction": "first tick at the parent's next tick boundary (any delay after entering)"},
                {"name": "own-timer", "prediction": "first tick one period after entering"}],
     "setup": "enter an NPC periodic area aura at varied offsets relative to its tick boundary",
     "observable": "combat-log periodic tick timestamps of the joiner vs an original recipient",
     "discriminates": "AL-R-F-03", "fidelity": "approximate", "related": ["AL-U-F-07"]},
    {"id": "AL-X-F-05", "question": "A party member right-click cancels another player's area aura (Devotion Aura).",
     "models": [{"name": "trinity", "prediction": "nothing happens"},
                {"name": "remove-readd", "prediction": "removed then re-applied within ~500 ms"},
                {"name": "removed", "prediction": "removed until re-entry"}],
     "setup": "two paladins not needed; one paladin + one other player", "observable": "UNIT_AURA on the canceller",
     "discriminates": "AL-R-F-05 cancel", "fidelity": "exact", "related": ["AL-R-F-05"]},
    {"id": "AL-X-F-06", "question": "Duration shown on a late joiner of a timed party aura (Frenzied Regeneration 22842:2 / Convoke 391528:2).",
     "models": [{"name": "one-parent", "prediction": "joiner's expiration time equals the owner's"},
                {"name": "fresh", "prediction": "joiner's expiration = join time + full duration"}],
     "setup": "cast, have the second player enter range mid-duration", "observable": "UnitAura expirationTime on both",
     "discriminates": "AL-R-F-01", "fidelity": "exact", "related": ["AL-R-F-01"]},
]

CORE_NAVIGATION: list[dict[str, Any]] = [
    {"id": "AL-K-F-01", "topic": "area-aura effect kinds",
     "core_coords": ["crates/data/src/effect.rs:56-62 (owns_unit_aura_effect: 6|35|65|119|128|129|143|174|202|271; 27 excluded as spatial membership)"],
     "research_ref": "LIFECYCLE_TABLE initial recipient set / reevaluation cadence; AL-R-F-01..03",
     "observation": "Core groups the area kinds with APPLY_AURA as unit-aura owners; in Trinity those kinds reach any recipient (the owner included) only through the 500 ms recipient map of one shared parent.",
     "reopen_condition": "Core models a recipient other than the aura owner for an area kind"},
    {"id": "AL-K-F-02", "topic": "area-aura expiry driver",
     "core_coords": ["crates/combat/tests/expiry_drivers.rs:707 (area_aura_party_source_kind_can_own_an_expiry_driver)"],
     "research_ref": "STATE_OWNERSHIP duration row; AL-R-F-04",
     "observation": "A kind-35 source may own an expiry driver; in Trinity the driver is the parent's duration and its expiry unapplies every recipient at once (dynobj: BY_DEFAULT).",
     "reopen_condition": "Core attaches expiry to a non-owner recipient"},
]


def build_corpus(ctx) -> dict[str, Any]:
    from . import records
    c = census(ctx)
    at_rows = areatrigger_aura_rows()
    rules = [dict(r) for r in RULES]
    allp = c["by_population"]["all"]
    counts = {"AL-R-F-01": allp["effects"], "AL-R-F-02": allp["effects"], "AL-R-F-03": allp["periodic_effects"],
              "AL-R-F-04": allp["effects"], "AL-R-F-05": allp["effects"],
              "AL-R-F-06": allp["by_pipeline"].get("unit-area", 0), "AL-R-F-07": at_rows["per_unit_aura"]}
    for r in rules:
        r["population"] = {**r["population"], "count": counts[r["id"]]}
    witnesses = []
    for spell in WITNESS_SPELLS:
        try:
            witnesses.append(profile(ctx, spell))
        except FailClosed as exc:
            witnesses.append({"spell": spell, "fail_closed": str(exc)})
    payload = {
        "schema": "aura-lifecycle-area/1",
        "provenance": records.provenance("aura_lifecycle.py recipients --corpus --out docs/research/aura-lifecycle-corpora/area-lifecycle.json",
                                         population_note="all = every DIFFICULTY_NONE area-aura provider effect; player = Scope.reach; controlled = controlled-unit spells"),
        "lifecycle_table": LIFECYCLE_TABLE,
        "state_ownership": STATE_OWNERSHIP,
        "census": c,
        "areatrigger_auras": at_rows,
        "timelines": run_all_scenarios(),
        "witnesses": witnesses,
        "rules": rules,
        "falsification": FALSIFICATION,
        "trinity_defects": DEFECTS,
        "unknowns": UNKNOWNS,
        "retail_experiments": EXPERIMENTS,
        "core_navigation": CORE_NAVIGATION,
        "dependencies": {"A": "CanStackWith / IsHighestExclusiveAura inputs of the stack filter",
                         "B": "refresh duration value", "D": "per-tick amount / snapshot of periodic area effects",
                         "E": "remove-mode consumers (scripts gated on EXPIRE / DEATH)", "H": "AT scripts and DoCheckAreaTarget hooks",
                         "I": "event vs update ordering, first-update full diff", "targeting": "who FillTargetMap selects (auratargets.py)"},
    }
    records.validate_corpus(payload)
    return payload


#: Current-player witnesses (plus Power Word: Barrier, the template-action AT).
WITNESS_SPELLS = (465, 740, 5740, 22842, 32223, 62618, 171975, 183416, 360194, 368412, 391528)
