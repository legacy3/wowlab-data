"""Aura-side recipient selection: area auras and persistent area auras (Track J).

The third recipient pipeline (after ``Spell::SelectSpellTargets`` and the
AreaTrigger target list): an aura owned by a unit (``UnitAura``) or by a
DynamicObject (``DynObjAura``) periodically recomputes its own recipient map.

    Aura::UpdateOwner          SpellAuras.cpp:839   (timer; first call runs at once, interval 0)
      Aura::UpdateTargetMap    SpellAuras.cpp:658   (every UPDATE_TARGET_MAP_INTERVAL = 500 ms, SpellAuras.h:58)
        UnitAura::FillTargetMap   SpellAuras.cpp:2540  (static applications + per-effect switch)
        DynObjAura::FillTargetMap SpellAuras.cpp:2709  (TargetA/TargetB check type, dynobj radius)
        filters: immunity, CanBeAppliedOn, IsHighestExclusiveAura, in-flight (dynobj), CanStackWith

What the spell pipeline contributes (Track G, not re-derived here): the aura
**owner** is every unit hit by any of the spell's unit-owned aura effects
(``Spell::DoSpellEffectHit`` Spell.cpp:3226 -> ``Aura::TryRefreshStackOrCreate``);
the aura carries **all** unit-owned aura effects of the spell
(``BuildEffectMaskForOwner(MAX_EFFECT_MASK)`` Spell.cpp:3241, ``_InitEffects``
SpellAuras.cpp:2511), and ``AddStaticApplication`` (SpellAuras.cpp:2650) keeps only
``SPELL_EFFECT_APPLY_AURA`` bits -- so area-aura effects (and 174
``APPLY_AURA_ON_PET``) reach every recipient, the owner included, only through
this module's map.  ``EffectUnused`` is the hit handler of 35/65/119/128/129/143/202/271
(SpellEffects.cpp:127...363).

Fixture contract (``targeting-fixture/1`` plus an ``aura`` block in the raw JSON)::

    "aura": {
      "caster": "p1" | null,             # GetCaster() (null = caster not found -> ref = owner)
      "static_applications": {"p1": [1]},# UnitAura::_staticApplications (effect indices), from the spell hit
      "applications": {"p2": [0]},       # current AuraApplications {unit: effect indices} (update only)
      "spell_labels_suppressed": false   # optional shorthand, see can_be_applied_on
    }

Owner facts: ``in_world``, ``banished`` (``HasAuraState(AURA_STATE_BANISHED, ...)``),
``pet`` (GetPetGUID target id or null; APPLY_AURA_ON_PET only).  DynamicObject owner:
``facts.radius`` (``DynamicObjectData.Radius``; :func:`dynobj_radius` computes it at creation)
and ``facts.caster``.  Target facts read by the update filters: ``in_world``,
``aura_immune``/``aura_immune_existing``, ``aura_immune_effects``, ``suppressed_by_label``,
``highest_exclusive``, ``can_stack`` (only when the target states other auras), ``in_flight``
(dynobj only).  Relations: ``(owner, charmer/owner) in_same_phase`` for AREA_AURA_OWNER/PET and
``(dynobj, unit) in_same_phase`` for the dynobj searcher phase.

Candidate enumeration is ``world.enumeration()`` (grid visit order); it must list only units in
the visited cells: UnitAura searches with ``radius.Max`` (``+ EXTRA_CELL_SEARCH_RADIUS`` only for
AREA_AURA_ENEMY), DynObjAura with ``radius + 0`` (WorldObject combat reach), while the accept
predicate extends by the target's combat reach (see TG-J-D01).  The returned map is a
``std::unordered_map``: application order is not defined and is not modelled.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from . import FailClosed
from . import area as A
from . import geometry as g
from .trace import Trace

UPDATE_TARGET_MAP_INTERVAL = 500           # SpellAuras.h:58
EXTRA_CELL_SEARCH_RADIUS = 40.0            # ObjectDefines.h:47

#: SharedDefines.h SpellEffectName (pinned) -- aura-map effect types
EFFECT = {
    27: "PERSISTENT_AREA_AURA", 35: "APPLY_AREA_AURA_PARTY", 65: "APPLY_AREA_AURA_RAID",
    119: "APPLY_AREA_AURA_PET", 128: "APPLY_AREA_AURA_FRIEND", 129: "APPLY_AREA_AURA_ENEMY",
    143: "APPLY_AREA_AURA_OWNER", 174: "APPLY_AURA_ON_PET", 202: "APPLY_AREA_AURA_SUMMONS",
    271: "APPLY_AREA_AURA_PARTY_NONRANDOM",
}
APPLY_AURA = 6
#: SpellInfo.cpp:480 IsAreaAuraEffect (174 is NOT an area aura effect)
AREA_AURA_EFFECTS = frozenset({35, 65, 119, 128, 129, 143, 202, 271})
#: SpellInfo.cpp:494 IsUnitOwnedAuraEffect
UNIT_OWNED_AURA_EFFECTS = AREA_AURA_EFFECTS | {APPLY_AURA, 174}
PERSISTENT_AREA_AURA = 27
AURA_MAP_EFFECTS = frozenset(EFFECT)

#: UnitAura::FillTargetMap switch (SpellAuras.cpp:2587-2630): effect -> TARGET_CHECK_*
SELECTION = {35: "PARTY", 271: "PARTY", 65: "RAID", 128: "ALLY", 129: "ENEMY", 202: "SUMMONED"}

GRID_UNITS = A.GRID_MASK["PLAYER"] | A.GRID_MASK["CREATURE"]


@dataclass(frozen=True)
class _Sel:
    """The (check, object) pair ``WorldObjectSpellTargetCheck`` reads (no selector id)."""
    check: str
    object: str = "UNIT"
    id: int | None = None


# ---------------------------------------------------------------------------
# fixture access
# ---------------------------------------------------------------------------
def aura_block(world) -> dict[str, Any]:
    block = world.raw.get("aura")
    if not isinstance(block, dict):
        raise FailClosed("fixture: aura target map needs an `aura` block (caster, static_applications, ...)")
    return block


def _aura_key(world, key: str) -> Any:
    block = aura_block(world)
    if key not in block:
        raise FailClosed(f"fixture: aura block does not state {key!r}")
    return block[key]


def _raw_actor(world, uid: str) -> dict[str, Any]:
    for a in world.raw.get("actors", []):
        if a.get("id") == uid:
            return a
    raise FailClosed(f"fixture: unknown actor {uid!r}")


def _bool_fact(world, uid: str, key: str) -> bool:
    value = world.actor(uid).fact(key)
    if not isinstance(value, bool):
        raise FailClosed(f"fixture: fact {key!r} of {uid!r} must be a bool")
    return value


def _mask(indices) -> int:
    m = 0
    for i in indices:
        m |= 1 << int(i)
    return m


def _indices(mask: int) -> list[int]:
    return [i for i in range(32) if mask & (1 << i)]


def aura_type(world, owner_id: str) -> str:
    """``Aura::GetType``: UNIT_AURA_TYPE for unit owners, DYNOBJ_AURA_TYPE for dynamic objects."""
    kind = world.actor(owner_id).kind
    if kind == "dynamicobject":
        return "dynobj"
    if kind in A.UNIT_KINDS:
        return "unit"
    raise FailClosed(f"aura: owner kind {kind!r} cannot own an aura (SpellAuras.cpp:427-456)")


def aura_caster(world) -> str | None:
    """``Aura::GetCaster`` -- the fixture states it (null = not found / despawned)."""
    caster = _aura_key(world, "caster")
    if caster is not None:
        world.actor(caster)
    return caster


# ---------------------------------------------------------------------------
# owner effect mask
# ---------------------------------------------------------------------------
def effect_mask_for_owner(sv, owner_kind: str, available: int = 0xFFFFFFFF) -> int:
    """Mirrors: SpellAuras.cpp:320 ``Aura::BuildEffectMaskForOwner``."""
    mask = 0
    for e in sv.effects:
        if owner_kind == "dynobj":
            if e.effect == PERSISTENT_AREA_AURA:
                mask |= 1 << e.index
        elif owner_kind == "unit":
            if e.effect in UNIT_OWNED_AURA_EFFECTS:
                mask |= 1 << e.index
        else:
            raise FailClosed(f"aura: BuildEffectMaskForOwner ABORT for owner kind {owner_kind!r}")
    return mask & available


def static_application_mask(sv, mask: int) -> int:
    """Mirrors: SpellAuras.cpp:2650 ``UnitAura::AddStaticApplication`` -- only APPLY_AURA bits survive."""
    for e in sv.effects:
        if mask & (1 << e.index) and e.effect != APPLY_AURA:
            mask &= ~(1 << e.index)
    return mask


# ---------------------------------------------------------------------------
# radius
# ---------------------------------------------------------------------------
def unit_aura_radius(world, sv, eff, ref: str, trace: Trace | None) -> tuple[float, float]:
    """``spellEffectInfo.CalcRadius(ref)`` -- TargetA entry, the aura **caster's** level / mods / movement.

    Mirrors: SpellAuras.cpp:2581 -> SpellInfo.cpp:783 (``targeting.oracle.calc_radius``).
    """
    from .oracle import calc_radius
    r = calc_radius(world, sv, eff, "A", ref, trace)
    if trace is not None:
        trace.add("auramap.radius", "SpellAuras.cpp:2581", output=list(r),
                  inputs={"effect": eff.index, "ref": ref, "entry": None if eff.radius_a is None else eff.radius_a.__dict__},
                  notes=["CalcRadius(ref): TargetA radius entry; no entry -> {0,0} (no movement bonus)"])
    return r


def dynobj_radius(world, sv, effect_index: int, caster: str) -> float:
    """Radius stored on the DynamicObject at creation (``Spell::EffectPersistentAA``).

    Mirrors: SpellEffects.cpp:1533-1538 -- max over PERSISTENT_AREA_AURA effects with index <= the
    handling (last PAA) effect of ``CalcRadius(unitCaster).Max``; Min is dropped (SpellAuras.cpp:2728).
    """
    from .oracle import calc_radius
    radius = 0.0
    for e in sv.effects:
        if e.index > effect_index:
            break
        if e.effect == PERSISTENT_AREA_AURA:
            radius = max(radius, calc_radius(world, sv, e, "A", caster)[1])
    return radius


# ---------------------------------------------------------------------------
# per-effect selection
# ---------------------------------------------------------------------------
def _conditions_refused(eff) -> None:
    if eff.conditions is not None:
        raise FailClosed(f"auramap: effect {eff.index} has implicit-target conditions "
                         "(IsObjectMeetToConditions / GetSearcherTypeMaskForConditionList not ported)")


def _unit_area_search(world, sv, eff, owner: str, ref: str, check: str, radius: tuple[float, float],
                      extra: float, trace: Trace | None) -> list[str]:
    """``WorldObjectSpellAreaTargetCheck(radius, unitOwner, ref, unitOwner, ..., UNIT)`` over the grid.

    Mirrors: SpellAuras.cpp:2632-2642; Spell.cpp:2125 (container mask), Spell.cpp:2163 (SearchTargets,
    AlwaysVisible phase), Spell.cpp:9418-9452 (9430 cylinder + AoETarget immunity + base check).
    """
    mask = A.searcher_type_mask(sv, eff, "UNIT")
    if not mask:
        if trace is not None:
            trace.add("auramap.search", "SpellAuras.cpp:2634", output=[], notes=["GetSearcherTypeMask == 0: no search"])
        return []
    center = A._pos(world, owner)
    rmin, rmax = g.f32(radius[0]), g.f32(radius[1])
    sel = _Sel(check)
    out, rejected = [], []
    for aid in A._candidates(world, mask, None, "auramap.search"):
        if A.area_predicate(world, sv, aid, center, (rmin, rmax), "Area") and \
                A.target_check(world, sv, ref, aid, sel, owner, trace):
            out.append(aid)
        else:
            rejected.append(aid)
    if trace is not None:
        trace.add("auramap.search", "SpellAuras.cpp:2636", output=out,
                  inputs={"center": owner, "caster": ref, "referer": owner, "check": check, "radius": [rmin, rmax],
                          "search_radius": g.add(rmax, extra), "mask": mask},
                  notes=[f"rejected: {rejected}", "phase: AlwaysVisible (no filter); no LOS check",
                         "candidates must lie in cells covering search_radius (fixture visit_order)"])
    return out


def _owner_branch(world, eff, owner: str, radius: tuple[float, float], trace: Trace | None) -> list[str]:
    """AREA_AURA_OWNER (and the PET fallthrough): the owner's charmer-or-owner, 3D range with the
    aura owner's combat reach only (``IsInRange3d(Position const*)`` overload).

    Mirrors: SpellAuras.cpp:2601-2611; Object.cpp:635 ``WorldObject::IsInRange3d``.
    """
    from . import groups
    master = groups.charmer_or_owner(world, owner)
    out: list[str] = []
    notes = [f"GetCharmerOrOwner -> {master}"]
    if master is not None:
        if not _bool_fact(world, master, "in_world"):
            notes.append("owner not in world")
        elif not world.relation(owner, master, "in_same_phase"):
            notes.append("not InSamePhase")
        elif not g.is_in_range3d(A._pos(world, owner), A.combat_reach(world, owner), A._pos(world, master),
                                 g.f32(radius[0]), g.f32(radius[1])):
            notes.append("not IsInRange3d(min, max) [aura owner's reach only]")
        else:
            out.append(master)
    if trace is not None:
        trace.add("auramap.owner", "SpellAuras.cpp:2607", output=out, inputs={"owner": owner, "radius": list(radius)},
                  notes=notes)
    return out


def fill_target_map(world, sv, aura_owner_id: str, effect: int, trace: Trace | None = None) -> list[str]:
    """Units the aura's effect ``effect`` selects this update, in vector order (duplicates kept).

    Mirrors: SpellAuras.cpp:2540 ``UnitAura::FillTargetMap`` (area part, one effect) or
    SpellAuras.cpp:2709 ``DynObjAura::FillTargetMap``.  Static applications are **not** included
    (see :func:`target_map`).  Returns ``[]`` for effects the switch ignores (plain APPLY_AURA is
    skipped at 2575; any other type falls to ``default`` at 2628).
    """
    kind = aura_type(world, aura_owner_id)
    eff = sv.effect(effect)
    if kind == "dynobj":
        return _dynobj_fill(world, sv, aura_owner_id, eff, trace)
    owner = aura_owner_id
    owner_mask = effect_mask_for_owner(sv, "unit")
    if not owner_mask & (1 << effect):
        if trace is not None:
            trace.add("auramap.effect", "SpellAuras.cpp:2571", output=[], notes=["!HasEffect: not a unit-owned aura effect"])
        return []
    if sv.has_attr("SPELL_ATTR7_DISABLE_AURA_WHILE_DEAD") and not world.actor(owner).need("alive"):
        if trace is not None:
            trace.add("auramap.dead-owner", "SpellAuras.cpp:2543", output=[])
        return []
    if not _bool_fact(world, owner, "in_world"):
        if trace is not None:
            trace.add("auramap.not-in-world", "SpellAuras.cpp:2563", output=[])
        return []
    if _bool_fact(world, owner, "banished"):
        if trace is not None:
            trace.add("auramap.banished", "SpellAuras.cpp:2566", output=[])
        return []
    if eff.effect == APPLY_AURA:
        if trace is not None:
            trace.add("auramap.effect", "SpellAuras.cpp:2575", output=[], notes=["APPLY_AURA: static applications only"])
        return []
    caster = aura_caster(world)
    ref = caster if caster is not None else owner
    radius = unit_aura_radius(world, sv, eff, ref, trace)
    units: list[str] = []
    selection = SELECTION.get(eff.effect)
    extra = 0.0
    if eff.effect == 129:
        extra = EXTRA_CELL_SEARCH_RADIUS if radius[1] > 0.0 else 0.0
    if eff.effect in (119, 143, 174, 202):
        _conditions_refused(eff)
    if eff.effect == 119:
        units.append(owner)                                   # 2603
        units += _owner_branch(world, eff, owner, radius, trace)
    elif eff.effect == 143:
        units += _owner_branch(world, eff, owner, radius, trace)
    elif eff.effect == 174:
        pet = world.actor(owner).fact("pet")                  # GetPetGUID, 2615
        if pet is not None and _bool_fact(world, pet, "in_owner_map"):
            units.append(pet)
        if trace is not None:
            trace.add("auramap.pet", "SpellAuras.cpp:2615", output=list(units),
                      notes=["ObjectAccessor::GetUnit(owner map): no range, phase, alive or in-world filter here"])
    elif eff.effect == 202:
        units.append(owner)                                   # 2623
    if selection is not None:
        _conditions_refused(eff)
        units += _unit_area_search(world, sv, eff, owner, ref, selection, radius, extra, trace)
    elif eff.effect not in (119, 143, 174):
        if trace is not None:
            trace.add("auramap.effect", "SpellAuras.cpp:2628", output=[], notes=[f"effect {eff.effect}: switch default"])
    if trace is not None:
        trace.add("auramap.fill", "SpellAuras.cpp:2646", output=list(units),
                  inputs={"owner": owner, "ref": ref, "effect": effect, "type": EFFECT.get(eff.effect, eff.effect),
                          "selection": selection})
    return units


def _dynobj_fill(world, sv, dynobj: str, eff, trace: Trace | None) -> list[str]:
    """Mirrors: SpellAuras.cpp:2709-2737 ``DynObjAura::FillTargetMap``.

    Check type = TargetA's, or TargetB's when TargetB references DEST; caster = referer = the dynobj's
    caster; radius ``{Min 0, Max dynobj.Radius}``; searcher phase = the dynobj's; no
    ``GetSearcherTypeMask``: the per-effect ``PlayersOnly`` attribute is not applied (the spell attributes
    ONLY_ON_PLAYER / NOT_ON_PLAYER / ONLY_ON_GHOSTS are re-checked by CheckTarget, SpellInfo.cpp:2368, 2443-2450).
    """
    from .selectors import info
    if not effect_mask_for_owner(sv, "dynobj") & (1 << eff.index):
        if trace is not None:
            trace.add("auramap.effect", "SpellAuras.cpp:2717", output=[], notes=["!HasEffect (not PERSISTENT_AREA_AURA)"])
        return []
    _conditions_refused(eff)
    d = world.actor(dynobj)
    caster = d.fact("caster")
    if caster is None:
        raise FailClosed("dynobj aura: DynamicObject::Update ASSERTs a caster (DynamicObject.cpp:137)")
    stated = aura_caster(world)
    if stated is not None and stated != caster:
        raise FailClosed("fixture: aura.caster differs from the dynobj caster")
    a, b = info(eff.target_a), info(eff.target_b)
    check = b.check if b.reference == "DEST" else a.check
    radius = g.f32(float(d.fact("radius")))
    center = A._pos(world, dynobj)
    sel = _Sel(check)
    out, rejected = [], []
    for aid in world.enumeration():
        kind = A.KIND_GRID.get(world.actor(aid).kind)
        if kind is None or not GRID_UNITS & A.GRID_MASK[kind]:
            continue                                          # UnitListSearcher: players + creatures only
        if not world.relation(dynobj, aid, "in_same_phase"):
            rejected.append(aid)
            continue
        if A.area_predicate(world, sv, aid, center, (0.0, radius), "Area") and \
                A.target_check(world, sv, caster, aid, sel, caster, trace):
            out.append(aid)
        else:
            rejected.append(aid)
    if trace is not None:
        trace.add("auramap.dynobj", "SpellAuras.cpp:2728", output=out,
                  inputs={"dynobj": dynobj, "caster": caster, "check": check, "radius": radius,
                          "target_a": a.name, "target_b": b.name},
                  notes=[f"rejected: {rejected}", "Cell::VisitAllObjects(dynobj, radius): no extra cell radius",
                         "no GetSearcherTypeMask (effect PlayersOnly ignored); no LOS check"])
    return out


def target_map(world, sv, aura_owner_id: str, trace: Trace | None = None) -> dict[str, int]:
    """The whole ``targets`` map ``{unit: effect mask}`` (keys sorted; Trinity's order is unordered).

    Mirrors: SpellAuras.cpp:2540 / 2709 (static applications, then per effect ``targets[unit] |= bit``).
    """
    kind = aura_type(world, aura_owner_id)
    targets: dict[str, int] = defaultdict(int)
    if kind == "unit":
        owner = aura_owner_id
        if sv.has_attr("SPELL_ATTR7_DISABLE_AURA_WHILE_DEAD") and not world.actor(owner).need("alive"):
            return {}
        statics = _aura_key(world, "static_applications")
        for uid, idx in statics.items():
            m = _mask(idx)
            if m != static_application_mask(sv, m):
                raise FailClosed(f"fixture: static application of {uid!r} names non-APPLY_AURA effects "
                                 "(AddStaticApplication strips them)")
            if uid != owner and not _bool_fact(world, uid, "in_owner_map"):
                continue                                      # 2554: ObjectAccessor::GetUnit failed
            if m:
                targets[uid] |= m
        if not _bool_fact(world, owner, "in_world") or _bool_fact(world, owner, "banished"):
            return dict(sorted(targets.items()))
        mask = effect_mask_for_owner(sv, "unit")
    else:
        mask = effect_mask_for_owner(sv, "dynobj")
    for i in _indices(mask):
        for uid in fill_target_map(world, sv, aura_owner_id, i, trace):
            targets[uid] |= 1 << i
    return dict(sorted(targets.items()))


# ---------------------------------------------------------------------------
# UpdateTargetMap filters
# ---------------------------------------------------------------------------
def can_be_applied_on(world, owner: str, target: str) -> bool:
    """Mirrors: SpellAuras.cpp:1607 ``Aura::CanBeAppliedOn`` (label suppression, not-in-world,
    ``CheckAreaTarget`` -> ``DoCheckAreaTarget`` hooks; no current-player spell registers one)."""
    if _bool_fact(world, target, "suppressed_by_label"):
        return False
    if not _bool_fact(world, target, "in_world"):
        if target != owner:
            return False
        # do not apply non-selfcast single target auras (SpellInfo::IsSingleTarget stated on the owner)
        return not (aura_caster(world) != owner and bool(world.actor(owner).fact("single_target_spell")))
    hooks = aura_block(world).get("check_area_target_hooks", [])
    if hooks:
        raise FailClosed("auramap: DoCheckAreaTarget hooks are script bodies (not modelled)")
    return True


def _immune(world, target: str, existing: bool) -> bool:
    key = "aura_immune_existing" if existing else "aura_immune"
    return _bool_fact(world, target, key)


def _immune_effects(world, target: str) -> int:
    return _mask(world.actor(target).fact("aura_immune_effects"))


def can_stack_on(world, sv, owner: str, target: str) -> bool:
    """The stacking loop of SpellAuras.cpp:729-744 over the target's applied auras.

    Exact for DYNOBJ auras (SpellAuras.cpp:1642-1647: same caster and same spell id -> no stack);
    unit auras read the fact ``can_stack`` unless the target states no foreign aura.
    """
    raw = _raw_actor(world, target)
    if "auras" not in raw:
        raise FailClosed(f"fixture: actor {target!r} does not state its applied `auras` (stacking check)")
    foreign = [a for a in raw["auras"] if a.get("owner") != owner or a.get("spell") != sv.id]
    if not foreign:
        return True
    caster = aura_caster(world)
    if aura_type(world, owner) == "dynobj":
        return not any(a.get("caster") == caster and a.get("spell") == sv.id for a in foreign)
    return _bool_fact(world, target, "can_stack")


def update_target_map(world, sv, aura_owner_id: str, trace: Trace | None = None) -> dict[str, Any]:
    """One ``Aura::UpdateTargetMap(caster, apply=true)``.

    Mirrors: SpellAuras.cpp:658-792.  Reads ``aura.applications`` (the current AuraApplications).
    Returns ``{"remove": [...], "create": {u: [idx]}, "update": {u: [idx]}, "keep": [...]}``
    (sorted; Trinity iterates unordered containers).
    """
    owner = aura_owner_id
    kind = aura_type(world, owner)
    targets = target_map(world, sv, owner, trace)
    apps = {u: _mask(i) for u, i in _aura_key(world, "applications").items()}
    to_remove: list[str] = []
    keep: list[str] = []
    for unit, app_mask in apps.items():
        if unit not in targets:
            to_remove.append(unit)                            # 678
            continue
        if _immune(world, unit, True) or not can_be_applied_on(world, owner, unit):
            to_remove.append(unit)                            # 684-688 (stays in targets)
            continue
        targets[unit] &= ~_immune_effects(world, unit)        # 691-693
        if app_mask != targets[unit]:
            continue
        del targets[unit]                                     # 701
        keep.append(unit)
    create: dict[str, list[int]] = {}
    update: dict[str, list[int]] = {}
    notes: list[str] = []
    for unit in sorted(targets):
        m = targets[unit]
        add = True
        existing = unit in apps
        if not existing:
            m &= ~_immune_effects(world, unit)                # 711-714
            if not m or _immune(world, unit, False) or not can_be_applied_on(world, owner, unit):
                add = False
        if add:
            raw = _raw_actor(world, unit)
            if raw.get("auras") == []:
                pass                                          # no aura effects: IsHighestExclusiveAura is true
            elif not _bool_fact(world, unit, "highest_exclusive"):
                add = False
                notes.append(f"{unit}: !IsHighestExclusiveAura (721)")
        if add and kind == "dynobj" and _bool_fact(world, unit, "in_flight"):
            add = False                                       # 725
            notes.append(f"{unit}: in flight (725)")
        if add and unit != owner and not can_stack_on(world, sv, owner, unit):
            add = False                                       # 732-741
            notes.append(f"{unit}: !CanStackWith (739)")
        if not add:
            continue
        if existing:
            if unit not in to_remove:                         # 764 (a to-be-removed app is updated, then unapplied)
                update[unit] = _indices(m)
        else:
            create[unit] = _indices(m)                        # 769
    result = {"remove": sorted(to_remove), "create": create, "update": update, "keep": sorted(keep)}
    if trace is not None:
        trace.add("auramap.update", "SpellAuras.cpp:658", output=result, notes=notes)
    return result


def next_update(interval_remaining: int, diff: int) -> tuple[bool, int]:
    """Mirrors: SpellAuras.cpp:839-842 -- ``m_updateTargetMapInterval <= int32(diff)`` runs and resets to 500.

    The constructor sets the interval to 0 (SpellAuras.cpp:480), so the first owner update always runs.
    """
    if interval_remaining <= diff:
        return True, UPDATE_TARGET_MAP_INTERVAL
    return False, interval_remaining - diff


def recipients(world, sv, aura_owner_id: str, effect: int, trace: Trace | None = None) -> list[str]:
    """Units holding ``effect`` after one update from the stated applications (sorted).

    Combines :func:`update_target_map` with the previous applications: the witness entry point.
    """
    res = update_target_map(world, sv, aura_owner_id, trace)
    apps = {u: set(i) for u, i in _aura_key(world, "applications").items()}
    for u in res["remove"]:
        apps.pop(u, None)
    for u, idx in {**res["create"], **res["update"]}.items():
        apps[u] = set(idx)
    return sorted(u for u, idx in apps.items() if effect in idx)


# ---------------------------------------------------------------------------
# witnesses
# ---------------------------------------------------------------------------
WITNESS_SCHEMA = "targeting-auramap-witness/1"
WITNESS_KEYS = ("spell", "effect", "shape", "selectors", "consumer", "discriminates", "build_skew", "expect", "fixture")


def witness_paths() -> list:
    from pathlib import Path
    return sorted((Path(__file__).parent / "fixtures" / "auramap").glob("*.json"))


def evaluate_witness(doc) -> dict[str, Any]:
    """Evaluate one ``targeting-auramap-witness/1`` document (dict or path).

    The fixture's ``aura.owner`` names the aura owner; the result is the sorted recipients of
    ``effect`` after one ``UpdateTargetMap`` from ``aura.applications``, plus the full update and trace.
    """
    import json
    from pathlib import Path

    from .fixture import World
    from .oracle import for_world
    if not isinstance(doc, dict):
        doc = json.loads(Path(doc).read_text(encoding="utf-8"))
    if doc.get("schema") != WITNESS_SCHEMA:
        raise FailClosed(f"witness: schema must be {WITNESS_SCHEMA!r}")
    missing = [k for k in WITNESS_KEYS if k not in doc]
    if missing:
        raise FailClosed(f"witness: missing keys {missing}")
    world = World.from_dict(doc["fixture"])
    if int(world.spell.get("id", -1)) != int(doc["spell"]):
        raise FailClosed("witness: fixture spell differs from witness spell")
    sv = for_world(world)
    owner = _aura_key(world, "owner")
    trace = Trace()
    update = update_target_map(world, sv, owner, None)
    got = recipients(world, sv, owner, int(doc["effect"]), trace)
    return {"spell": int(doc["spell"]), "effect": int(doc["effect"]), "owner": owner, "recipients": got,
            "expected": doc["expect"]["recipients"], "ok": got == doc["expect"]["recipients"],
            "update": update, "trace": trace.to_json()}


# ---------------------------------------------------------------------------
# census
# ---------------------------------------------------------------------------
def census(ctx=None) -> dict[str, Any]:
    """Current-player aura-map effects (``ctx.scope.reach``, IsEffect, DIFFICULTY_NONE).

    Rows carry raw and load-corrected selectors (SpellMgr.cpp:5286-5291 area-aura rewrite via
    ``attributes.corrected_effects``), radius entries, aura-owner derivation and per-spec reach.
    """
    from . import context
    from .attributes import corrected_effects
    from .selectors import info
    ctx = ctx or context.get()
    rows = []
    for spell in sorted(ctx.scope.reach):
        effs = ctx.data.effects(spell, 0)
        if not any(e.effect in AURA_MAP_EFFECTS for e in effs):
            continue
        fixed = {r["index"]: r for r in corrected_effects(spell, effs)[0]}
        carriers = sorted({(info(fixed[e.index]["a"]).name or "NONE", info(fixed[e.index]["b"]).name or "NONE")
                           for e in effs if fixed[e.index]["effect"] in UNIT_OWNED_AURA_EFFECTS})
        for e in effs:
            if e.effect not in AURA_MAP_EFFECTS:
                continue
            fx = fixed[e.index]
            ra = ctx.data.radii.get(e.radius_a) if e.radius_a else None
            rows.append({
                "spell": spell, "effect": e.index, "name": ctx.name(spell), "type": e.effect,
                "type_name": EFFECT[e.effect], "aura": e.aura,
                "targets_raw": [e.target_a, e.target_b], "targets": [fx["a"], fx["b"]],
                "target_names": [info(fx["a"]).name or "NONE", info(fx["b"]).name or "NONE"],
                "radius_index": [e.radius_a, e.radius_b],
                "radius_a": None if ra is None else {k: float(ra[k]) for k in ("Radius", "RadiusPerLevel", "RadiusMin", "RadiusMax")},
                "effect_attributes": e.attributes,
                "owner_carriers": [list(c) for c in carriers],
                "build_skew": bool(ctx.is_skew(spell)),
            })
    by_type = Counter(r["type_name"] for r in rows)
    specs = {}
    keys = {(r["spell"], r["effect"]) for r in rows}
    for spec, spells in sorted(ctx.scope.specs_reach.items()):
        mine = sorted(f"{s}:{i}" for (s, i) in keys if s in spells)
        if mine:
            specs[str(spec)] = mine
    return {"rows": rows, "by_type": dict(sorted(by_type.items())), "per_spec": specs,
            "all_types": {str(k): v for k, v in sorted(EFFECT.items())}}


__all__ = ["AREA_AURA_EFFECTS", "EFFECT", "UPDATE_TARGET_MAP_INTERVAL", "aura_block", "can_be_applied_on",
           "census", "dynobj_radius", "effect_mask_for_owner", "evaluate_witness", "fill_target_map", "next_update", "recipients",
           "static_application_mask", "target_map", "update_target_map"]
