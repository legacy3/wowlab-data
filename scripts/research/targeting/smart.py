"""Smart / injured / ranked recipient selection (Track D).

Trinity has **no generic "smart heal" attribute**.  At the pin, recipient choice
that depends on health, auras, grouping or randomness comes from

1. the chain-heal jump rule (``targeting.chain``: largest absolute deficit);
2. two shared helpers called from ``OnObjectAreaTargetSelect`` hooks:
   ``Trinity::SelectRandomInjuredTargets`` (Spell.cpp:9513) and
   ``Trinity::SortTargetsWithPriorityRules`` (Spell.cpp:9578);
3. script-local code: ``RandomResize`` with or without predicates, ``list::sort``
   with ``HealthPctOrderPred`` / lambdas, ``std::partition`` + ``RandomShuffle``,
   ``min_element``, ``SelectRandomContainerElement``;
4. ``Unit::SelectNearbyTarget`` (proc-time random adjacent enemy).

Everything here reproduces those consumers over the fixture; ordering-sensitive
steps read ``world.visit_order`` (the hook's input order) and draws come from
``world.draw`` (see :mod:`targeting.rng`).

Sort stability
--------------
``std::ranges::sort`` is not required to be stable.  The probe's libstdc++ 13
(``stl_algo.h:1848-1978``: introsort, ``_S_threshold = 16``) sorts ranges of at
most 16 elements with a stable insertion sort, so :func:`libstdcxx_sort` is exact
for ``n <= 16`` and fails closed for longer ranges with equal keys (their
relative order is implementation-defined; another standard library may differ
even for short ranges).  ``std::list::sort`` is stable (merge sort).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from . import FailClosed
from . import geometry as g
from . import rng
from .trace import Trace

UNIT_KINDS = ("player", "creature", "pet", "guardian", "totem", "minion", "vehicle")
CREATURE_KINDS = ("creature", "pet", "guardian", "totem", "minion", "vehicle")
LIBSTDCXX_INSERTION_THRESHOLD = 16  # stl_algo.h:1848 _S_threshold
NOMINAL_MELEE_RANGE = 5.0            # ObjectDefines.h NOMINAL_MELEE_RANGE (Unit.h:737 default)


# ---------------------------------------------------------------------------
# unit facts
# ---------------------------------------------------------------------------
def is_unit(world, a: str) -> bool:
    return world.actor(a).kind in UNIT_KINDS


def is_player(world, a: str) -> bool:
    return world.actor(a).kind == "player"


def is_full_health(world, a: str) -> bool:
    """Mirrors: Unit.h:790 ``IsFullHealth`` -- ``GetHealth() == GetMaxHealth()`` (uint64)."""
    x = world.actor(a)
    return int(x.need("health")) == int(x.need("max_health"))


def treated_as_raid_unit(world, a: str) -> bool:
    """Mirrors: Creature.h:235 ``IsTreatedAsRaidUnit`` (CREATURE_STATIC_FLAG_4_TREAT_AS_RAID_UNIT_FOR_HELPFUL_SPELLS).

    Fact name shared with Tracks B/F: ``treated_as_raid_unit`` (``relations.fact``).
    """
    from .relations import fact
    return bool(fact(world, a, "treated_as_raid_unit"))


def in_raid_with(world, a: str, b: str) -> bool:
    """``Unit::IsInRaidWith`` -- Track F's predicate when published, else the directed fixture relation."""
    try:
        from . import groups
        fn = getattr(groups, "is_in_raid_with", None)
    except ImportError:
        fn = None
    if fn is not None:
        return bool(fn(world, a, b))
    return bool(world.relation(a, b, "in_raid_with"))


def has_aura(world, target: str, spell: int, caster: str | None) -> bool:
    """``Unit::HasAura(spellId, casterGUID)`` over the fixture's aura list."""
    return world.actor(target).has_aura(spell, caster)


# ---------------------------------------------------------------------------
# sorting models
# ---------------------------------------------------------------------------
def libstdcxx_sort(items: Sequence[Any], key: Callable[[Any], Any], *, reverse: bool = False,
                   what: str = "std::ranges::sort") -> list[Any]:
    """``std::ranges::sort(range, less|greater, proj)`` as libstdc++ 13 executes it.

    Mirrors: libstdc++13 ranges_algo.h:1775-1801 -> stl_algo.h:1942-1952 ``__sort``.
    ``n <= 16``: stable insertion sort (exact).  ``n > 16`` with duplicate keys:
    FailClosed (introsort order of equal keys is implementation-defined).
    """
    items = list(items)
    keys = [key(i) for i in items]
    if len(items) > LIBSTDCXX_INSERTION_THRESHOLD and len(set(keys)) != len(keys):
        raise FailClosed(f"{what}: {len(items)} > 16 elements with equal keys -- tie order is "
                         "implementation-defined (introsort); state distinct keys or <= 16 candidates")
    order = sorted(range(len(items)), key=keys.__getitem__, reverse=reverse)  # Python sort is stable, also reversed
    return [items[i] for i in order]


def health_pct(world, a: str) -> float:
    """Mirrors: CommonPredicates.cpp:39-46 -- ``float(health) / float(maxHealth)``; non-units and max 0 -> 0.0."""
    if not is_unit(world, a):
        return 0.0
    x = world.actor(a)
    mh = int(x.need("max_health"))
    if not mh:
        return 0.0
    return g.div(g.f32(float(int(x.need("health")))), g.f32(float(mh)))


def list_sort_health_pct(world, targets: Sequence[str], ascending: bool = True,
                         trace: Trace | None = None) -> list[str]:
    """``std::list::sort(HealthPctOrderPred(ascending))`` -- stable merge sort.

    Mirrors: CommonPredicates.cpp:39 + ``std::list::sort`` (stable).
    """
    out = sorted(targets, key=lambda t: health_pct(world, t), reverse=not ascending)
    if trace is not None:
        trace.add("smart.list_sort_health_pct", "CommonPredicates.cpp:39-46", output=list(out))
    return out


# ---------------------------------------------------------------------------
# Trinity::SelectRandomInjuredTargets
# ---------------------------------------------------------------------------
NOT_GROUPED, NOT_PLAYER, NOT_INJURED = 0, 1, 2


def injured_priority(world, t: str, prioritize_players: bool, group_of: str | None) -> int:
    """Mirrors: Spell.cpp:9541-9554 -- the negative-points bit set of one candidate."""
    p = 0
    if group_of is not None and (not is_unit(world, t) or not in_raid_with(world, t, group_of)):
        p |= 1 << NOT_GROUPED
    if prioritize_players and not is_player(world, t) and \
            (world.actor(t).kind not in CREATURE_KINDS or not treated_as_raid_unit(world, t)):
        p |= 1 << NOT_PLAYER
    if not is_unit(world, t) or is_full_health(world, t):
        p |= 1 << NOT_INJURED
    return p


def select_random_injured_targets(world, targets: Sequence[str], max_targets: int, prioritize_players: bool,
                                  group_of: str | None = None, trace: Trace | None = None) -> list[str]:
    """Mirrors: Spell.cpp:9513-9573 ``Trinity::SelectRandomInjuredTargets``.

    * ``size <= max`` -> unchanged, no draws (9515-9516);
    * priority = bit set (NOT_GROUPED=1, NOT_PLAYER=2, NOT_INJURED=4; lower is better, so the
      significance is injured > player > grouped -- an injured stranger beats a full-health group
      member): injured is **binary** (``IsFullHealth``), there is no deficit or percentage ranking;
    * ``std::ranges::sort`` by priority (see :func:`libstdcxx_sort`);
    * walk the 8 classes in ascending order; at the first class ``c`` with
      ``found + count[c] >= max`` shuffle only ``[found, found + count[c])`` -- also when
      it fills the cap exactly (draws are consumed although the kept set is fixed);
    * keep the first ``max`` elements.
    """
    targets = list(targets)
    if len(targets) <= max_targets:
        if trace is not None:
            trace.add("smart.injured.noop", "Spell.cpp:9515-9516", output=targets)
        return targets
    prio = {t: injured_priority(world, t, prioritize_players, group_of) for t in targets}
    counts = [0] * 8
    for t in targets:
        counts[prio[t]] += 1
    ordered = libstdcxx_sort(targets, key=lambda t: prio[t], what="SelectRandomInjuredTargets sort")
    found = 0
    shuffled: tuple[int, int] | None = None
    for c in range(8):
        if found + counts[c] >= max_targets:
            seg = rng.random_shuffle(world, ordered[found:found + counts[c]], trace,
                                     what=f"SelectRandomInjuredTargets shuffle class {c}")
            ordered[found:found + counts[c]] = seg
            shuffled = (found, found + counts[c])
            if found + counts[c] == max_targets and counts[c] > 1 and trace is not None:
                trace.add("smart.injured.exact_fill_shuffle", "Spell.cpp:9559-9567", output=seg,
                          defect="TG-D-DEF-10", notes=["boundary class fills the cap exactly but is shuffled anyway"])
            break
        found += counts[c]
    out = ordered[:max_targets]
    if trace is not None:
        trace.add("smart.injured", "Spell.cpp:9513-9573", output=out,
                  inputs={"priority": prio, "max": max_targets, "shuffled_range": shuffled})
    return out


# ---------------------------------------------------------------------------
# Trinity::SortTargetsWithPriorityRules
# ---------------------------------------------------------------------------
Rule = Callable[[Any, str], bool]


def sort_targets_with_priority_rules(world, targets: Sequence[str], max_targets: int, rules: Sequence[Rule],
                                     trace: Trace | None = None) -> list[str]:
    """Mirrors: Spell.cpp:9575-9613 ``Trinity::SortTargetsWithPriorityRules``.

    score bit ``(len-1-i)`` for rule ``i`` (earlier rules dominate); sort by score
    descending (``std::ranges::greater``, unstable in general); if the element just
    past the cutoff has the cutoff score, shuffle the whole ``equal_range`` of that
    score (which may include elements *before* the cutoff); keep the first ``max``.
    ``TargetPriorityRule`` typed on ``Unit*`` is false for non-units (Spell.h:1096-1103).
    """
    targets = list(targets)
    if len(targets) <= max_targets:
        if trace is not None:
            trace.add("smart.rules.noop", "Spell.cpp:9580-9581", output=targets)
        return targets
    if len(rules) > 31:
        raise FailClosed("SortTargetsWithPriorityRules: N <= 31 (Spell.h:1115)")
    score = {}
    for t in targets:
        s = 0
        for i, rule in enumerate(rules):
            if rule(world, t):
                s |= 1 << (len(rules) - 1 - i)
        score[t] = s
    ordered = libstdcxx_sort(targets, key=lambda t: score[t], reverse=True, what="SortTargetsWithPriorityRules sort")
    tie = score[ordered[max_targets - 1]]
    shuffled = None
    if score[ordered[max_targets]] == tie:
        lo = next(i for i, t in enumerate(ordered) if score[t] == tie)
        hi = max(i for i, t in enumerate(ordered) if score[t] == tie) + 1
        ordered[lo:hi] = rng.random_shuffle(world, ordered[lo:hi], trace, what="SortTargetsWithPriorityRules tie shuffle")
        shuffled = (lo, hi)
    out = ordered[:max_targets]
    if trace is not None:
        trace.add("smart.rules", "Spell.cpp:9575-9613", output=out,
                  inputs={"score": score, "max": max_targets, "shuffled_range": shuffled})
    return out


def unit_rule(fn: Callable[[Any, str], bool]) -> Rule:
    """``TargetPriorityRule`` built from a ``Unit const*`` lambda: non-units score false (Spell.h:1098-1099)."""
    return lambda world, t: is_unit(world, t) and fn(world, t)


SPELL_PRIEST_ATONEMENT_EFFECT = 194384  # spell_priest.cpp enum (validated by the script index)


def radiance_rules(caster: str, explicit: str | None) -> list[Rule]:
    """Mirrors: spell_priest.cpp:3387-3397 ``GetRadianceRules``."""
    return [
        lambda w, t: t == explicit,
        unit_rule(lambda w, t: not has_aura(w, t, SPELL_PRIEST_ATONEMENT_EFFECT, caster)),
        unit_rule(lambda w, t: not is_full_health(w, t)),
        lambda w, t: is_player(w, t) or (w.actor(t).kind in CREATURE_KINDS and treated_as_raid_unit(w, t)),
        unit_rule(lambda w, t: in_raid_with(w, t, caster)),
    ]


# ---------------------------------------------------------------------------
# script adapters (current-player reach)
# ---------------------------------------------------------------------------
def _value(world, key: str) -> Any:
    if key not in world.spell_value:
        raise FailClosed(f"fixture: spell_value.{key} not stated")
    return world.spell_value[key]


def wild_growth(world, targets: Sequence[str], trace: Trace) -> list[str]:
    """Mirrors: spell_druid.cpp:3021-3032 -- ``maxTargets = EFFECT_1 value (+ Tree of Life EFFECT_2)``,
    ``SelectRandomInjuredTargets(targets, maxTargets, true, caster)``.

    ``spell_value.wild_growth_max_targets`` = that int32 sum (payload arithmetic, not modelled).
    """
    return select_random_injured_targets(world, targets, int(_value(world, "wild_growth_max_targets")), True,
                                         world.caster, trace)


def power_word_radiance(world, targets: Sequence[str], trace: Trace) -> list[str]:
    """Mirrors: spell_priest.cpp:3360-3378 -- ``maxTargets = EFFECT_2 value + 1``; five priority rules.

    The explicit target only wins if the area search returned it (it is rule 1, not an insertion).
    """
    max_targets = int(_value(world, "radiance_effect2_value")) + 1
    return sort_targets_with_priority_rules(world, targets, max_targets,
                                            radiance_rules(world.caster, world.explicit.get("unit")), trace)


def starfall_dummy(world, targets: Sequence[str], trace: Trace) -> list[str]:
    """Mirrors: spell_druid.cpp:2272-2275 -- ``RandomResize(targets, 2)``."""
    return rng.random_resize(world, targets, 2, trace, what="Starfall dummy RandomResize")


SPELL_WARLOCK_SEED_OF_CORRUPTION = 27243


def seed_of_corruption_select(world, targets: Sequence[str], trace: Trace) -> list[str]:
    """Mirrors: spell_warlock.cpp:1127-1145 ``SelectTarget`` (registered for EFFECT_1 and EFFECT_2; on 12.1 rows the two effects are grouped, so it runs once per cast).

    ``< 2`` candidates: unchanged.  Explicit target without the caster's Seed -> [explicit]
    (no draw).  Otherwise drop every candidate *with* the caster's seed
    (``UnitAuraCheck(true, ...)``) and keep one at random (``RandomResize(1)``: draws only
    if more than one remains); none left -> [explicit].
    """
    targets = list(targets)
    if len(targets) < 2:
        trace.add("seed.noop", "spell_warlock.cpp:1129-1130", output=targets, evidence="script-consumer")
        return targets
    expl = world.explicit.get("unit")
    if expl is None:
        raise FailClosed("fixture: Seed of Corruption hook dereferences GetExplTargetUnit()")
    if not has_aura(world, expl, SPELL_WARLOCK_SEED_OF_CORRUPTION, world.caster):
        trace.add("seed.primary", "spell_warlock.cpp:1132-1137", output=[expl], evidence="script-consumer")
        return [expl]
    rest = [t for t in targets if not (is_unit(world, t) and has_aura(world, t, SPELL_WARLOCK_SEED_OF_CORRUPTION, world.caster))]
    if rest:
        out = rng.random_resize(world, rest, 1, trace, what="Seed of Corruption RandomResize")
    else:
        out = [expl]
    trace.add("seed.other", "spell_warlock.cpp:1138-1145", output=out, evidence="script-consumer")
    return out


def select_nearby_target(world, unit: str, exclude: str | None, dist: float = NOMINAL_MELEE_RANGE,
                         trace: Trace | None = None) -> str | None:
    """Mirrors: Unit.cpp:10920-10949 ``Unit::SelectNearbyTarget``.

    Blade Flurry (13877) at the pin: only the draw and the proc gate are live; the returned
    unit never receives 22482 on 12.1 rows (HandleProc mask 0, R3-01).

    Candidates in ``visit_order``: alive, ``IsWithinDist(u, dist)`` (3D, both combat reaches),
    ``!IsFriendlyTo(u)`` (neutral units qualify; no ``IsValidAttackTarget``); minus the
    current victim (``facts.victim``) and ``exclude``; minus no-LOS (``IsWithinLOSInMap``,
    no spell LOS exemptions), totems, spirit services and critters; then one
    ``SelectRandomContainerElement`` draw (none when empty).
    """
    me = world.actor(unit)
    mpos = g.vec(me.need("pos"))
    cands: list[str] = []
    for c in world.enumeration():
        a = world.actor(c)
        if a.kind not in UNIT_KINDS:
            continue
        if not a.need("alive"):
            continue
        if not g.is_within_dist(mpos, me.need("combat_reach"), g.vec(a.need("pos")), a.need("combat_reach"), dist):
            continue
        if world.relation(unit, c, "friendly"):
            continue
        cands.append(c)
    victim = me.fact("victim")
    cands = [c for c in cands if c != victim and c != exclude]
    cands = [c for c in cands if world.in_los(unit, c) and world.actor(c).kind != "totem"
             and not world.actor(c).fact("spirit_service") and not world.actor(c).fact("critter")]
    if not cands:
        if trace is not None:
            trace.add("select_nearby_target.none", "Unit.cpp:10941-10943", output=None)
        return None
    return rng.select_random(world, cands, trace, what="SelectNearbyTarget")


def killing_spree_pick(world, guids: Sequence[str], trace: Trace) -> str | None:
    """DEAD at the pin on 12.0.7/12.1 rows (R3-06): the list is never filled, so this never runs.

    Kept as the mirror of spell_rogue.cpp:664-677 -- loop: one ``SelectRandomContainerElement`` draw per
    iteration; a GUID that no longer resolves (``facts.resolvable`` false) is removed and the
    loop draws again; the first resolvable pick is used."""
    pool = list(guids)
    while pool:
        pick = rng.select_random(world, pool, trace, what="Killing Spree target")
        if world.actor(pick).fact("resolvable"):
            return pick
        pool.remove(pick)
    return None


SPELL_SHAMAN_FLAME_SHOCK = 188389


def molten_assault(world, candidates: Sequence[str], effect_value: int, trace: Trace) -> list[str]:
    """Mirrors: spell_sha_molten_assault (spell_shaman.cpp:2249-2275).

    ``std::partition`` (unstable!) puts candidates with the caster's Flame Shock first; if
    ``effectValue + 1 - withShock != 0`` (size_t arithmetic: also when *negative*, i.e.
    wrapped) the without-shock tail is shuffled; the first ``min(size, value + 1)`` get
    Flame Shock.  ``std::partition`` on a random-access range in libstdc++ swaps
    elements (not stable) -- its exact output order is modelled by
    :func:`libstdcxx_partition`.
    """
    has = lambda t: has_aura(world, t, SPELL_SHAMAN_FLAME_SHOCK, world.caster)  # noqa: E731
    parted, split = libstdcxx_partition(list(candidates), has)
    missing = (effect_value + 1 - split) & 0xFFFFFFFFFFFFFFFF
    if missing:
        parted[split:] = rng.random_shuffle(world, parted[split:], trace, what="Molten Assault shuffle")
    out = parted[:min(len(parted), effect_value + 1)]
    trace.add("molten_assault", "spell_shaman.cpp:2262-2272", output=out, evidence="script-consumer",
              inputs={"with_shock": split})
    return out


def libstdcxx_partition(items: list[Any], pred: Callable[[Any], bool]) -> tuple[list[Any], int]:
    """libstdc++ 13 ``std::partition`` for a ``std::vector`` (random-access -> bidirectional overload).

    Mirrors: stl_algo.h ``__partition(_BidirectionalIterator, ..., bidirectional_iterator_tag)``:
    advance ``first`` while pred; retreat ``last`` while !pred; swap; repeat.
    (``std::vector::iterator`` is random-access, which dispatches to the bidirectional
    overload.)  Checked by the probe.
    """
    a = list(items)
    first, last = 0, len(a)
    while True:
        while True:
            if first == last:
                return a, first
            if pred(a[first]):
                first += 1
            else:
                break
        while True:
            last -= 1
            if first == last:
                return a, first
            if not pred(a[last]):
                continue
            break
        a[first], a[last] = a[last], a[first]
        first += 1


def min_element_first(values: Sequence[tuple[str, int]]) -> str:
    """``std::ranges::min_element(range, less, proj)`` -- the *first* minimum (Divine Procession).

    Mirrors: spell_priest.cpp:3555-3566 (unresolvable GUID / missing aura project to INT32_MAX).
    """
    if not values:
        raise FailClosed("min_element on an empty range returns end(); caller guards")
    best = values[0]
    for v in values[1:]:
        if v[1] < best[1]:
            best = v
    return best[0]


@dataclass(frozen=True)
class Family:
    id: str
    consumer: str
    spells_in_reach: tuple[int, ...]
    candidates: str
    metric: str
    tie_break: str
    determinism: str
    cap: str
    explicit_guarantee: str
    full_health: str
    pets: str
    per_effect: str
    evidence: str = "trinity-consumer"
    status: str = "live"   # live | draw-only (RNG consumed, no recipient) | dead (never runs on 12.1 rows)


FAMILIES: tuple[Family, ...] = (
    Family("chain-heal-deficit", "Spell.cpp:2283-2298", (1064,),
           "chain prefilter around the previous target (radius 12.5 * jumps (+mods))",
           "largest uint32(maxHealth - health), within 12.5y (+reaches) and LOS of the previous target",
           "first in visit order among equal deficits (strict >)", "deterministic (no draws)",
           "ChainTargets (+SpellModOp::ChainTargets) - 1 jumps", "initial target is the explicit/nearby unit, added before the jumps",
           "eligible; chosen only if it is the first acceptable candidate (deficit 0 never beats a found one)",
           "eligible if the ALLY check passes (pets, guardians; the caster itself too)",
           "one selection per effect-mask group (first effect's ChainTargets)"),
    Family("select-random-injured", "Spell.cpp:9513-9573",
           (48438,), "the area hook input (grid visit order, after the area check)",
           "3 binary bits, most significant first: full health (4) / not player unless TreatAsRaidUnit creature (2) / not in raid with X (1)",
           "unstable sort within a class (libstdc++ <=16: input order), random shuffle of the boundary class",
           "random only inside the boundary class; draws consumed even when the class exactly fills the cap",
           "script maxTargets; then Spell.cpp:1448 RandomResize(MaxAffectedTargets) if the spell has one",
           "none (explicit target is not special)", "eligible only after every injured candidate (grouped or not)",
           "NOT_PLAYER bit unless TreatAsRaidUnit; pets of the group are 'grouped' only if IsInRaidWith holds",
           "hook bound to one effect/target pair; other effects keep their own lists"),
    Family("priority-rules", "Spell.cpp:9575-9613", (194509,),
           "the area hook input", "rule bit vector (Radiance: explicit > no own Atonement > injured > player/raid-unit > in raid)",
           "unstable sort; random shuffle of the whole equal-score range if the cutoff splits it",
           "random only among equal scores at the cutoff", "EFFECT_2 value + 1",
           "explicit target first only if it is in the area list", "eligible (rule 3 false)", "rule 4 false unless TreatAsRaidUnit",
           "hook on EFFECT_1 only"),
    Family("random-resize-hook", "spell_druid.cpp:2274; spell_warlock.cpp:1127-1145", (50286, 27243),
           "hook input", "none (uniform) / aura predicate first (Seed)", "selection sampling keeps visit order",
           "size draws when size > n", "2 (Starfall) / 1 (Seed)", "Seed: explicit kept when it lacks Seed or nothing else qualifies",
           "n/a (enemies)", "n/a", "Seed: on 12.1 rows EFFECT_1/EFFECT_2 are one effect-mask group (same selectors/radius, same hook function), so the hook runs once for the lead EFFECT_1 and both effects share its single selection (Spell.cpp:741-788, 9066)"),
    Family("proc-random-adjacent", "Unit.cpp:10920-10949", (13877,),
           "AnyUnfriendlyUnitInObjectRangeCheck within 5y of the aura owner (Cell visit order)",
           "none (uniform)", "n/a", "1 draw during DoCheckProc, before the aura's proc roll",
           "one target", "current victim and the proc's action target are excluded",
           "n/a", "neutral units qualify (only !IsFriendlyTo)", "per proc event",
           status="draw-only: DoCheckProc executes (draw + proc gate) but the recipient-acting HandleProc "
                  "(EFFECT_0, aura 110/138) has affected mask 0 on 12.1 rows (spell_rogue.cpp:296-301; effect 0 "
                  "is SPELL_AURA_DUMMY) -> 22482 is never cast; script-layout drift (R3-01)"),
    Family("shooting-stars", "spell_druid.cpp:2170-2215", (202342,),
           "all units within 100y with the druid's Moonfire / Sunfire (UnitWorker visit order)",
           "none", "roll_chance(frac) then RandomResize(procs) per DoT list (moonfire list first)",
           "random", "floor(amount*sqrt(n)/100) (+1 on the roll)", "n/a", "n/a", "n/a",
           "periodic tick", "script-consumer"),
    Family("killing-spree", "spell_rog_killing_spree_aura (spell_rogue.cpp:664-677)", (51690,),
           "GUID list filled by the spell script", "none", "uniform; stale GUIDs removed and redrawn",
           "1+ draws per tick", "1 per tick", "n/a", "n/a", "n/a", "per tick", "script-consumer",
           status="dead: the list is filled only by spell_rog_killing_spree::HandleDummy (EFFECT_1, "
                  "SPELL_EFFECT_DUMMY), but 51690 EFFECT_1 is an aura effect (6/33) on 12.0.7 and 12.1 -> mask 0; "
                  "the periodic tick sees an empty list: no draw, no teleport, no damage (R3-06)"),
    Family("molten-assault", "spell_shaman.cpp:2249-2275", (60103,),
           "enemies within 10y of the Lava Lash target (VisitAllObjects order)",
           "has caster's Flame Shock first (std::partition, unstable)", "shuffle of the no-shock tail",
           "random tail", "effect value + 1", "n/a", "n/a", "n/a", "EFFECT_2 hit", "script-consumer"),
    Family("divine-procession", "spell_priest.cpp:3540-3575", (472361,),
           "Atonement target GUID vector (insertion order)", "shortest remaining Atonement duration",
           "first minimum (min_element)", "deterministic", "1", "n/a", "n/a", "n/a", "per proc", "script-consumer"),
)
