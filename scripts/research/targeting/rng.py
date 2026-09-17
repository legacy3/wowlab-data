"""Target-selection randomness and its place in the shared RNG stream (Track D).

Trinity has **one** random source (``Random.cpp``: a thread-local SFMT engine
behind ``urand`` / ``irand`` / ``frand`` / ``rand_norm`` / ``rand_chance`` /
``RandomEngine``).  Targeting draws, hit/miss rolls, crit rolls, proc rolls and
payload rolls all consume that one stream, so the observable contract is the
*order* in which they are drawn, not a separate "targeting RNG".

The oracle therefore never generates randomness.  Every stage takes explicit
draws from ``World.draw`` (``fixture.rng.draws``), in consumer order:

* integer draws for ``urand(lo, hi)`` / ``irand(lo, hi)`` (the value itself);
* float draws in ``[0, 1)`` for ``rand_norm()``, in ``[0, 100)`` for ``rand_chance()``;
* for ``RandomShuffle`` one swap index ``j_i`` in ``[0, i]`` per position ``i = 1..n-1``
  (libstdc++ 13's forward Fisher-Yates, see :func:`random_shuffle`); these are not engine
  calls (libstdc++ makes ``floor(n/2)`` distribution calls) and the model is stdlib-specific.

Mirrors (generator): Random.cpp:36-94, Random.h:24-66.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from . import FailClosed
from .trace import Trace

UINT32_MAX = 0xFFFFFFFF


# ---------------------------------------------------------------------------
# primitive draws
# ---------------------------------------------------------------------------
def urand(world, lo: int, hi: int, what: str, trace: Trace | None = None) -> int:
    """One ``urand(lo, hi)`` (inclusive) value from the fixture stream.

    Mirrors: Random.cpp:42 ``urand`` -- ``ASSERT(max >= min)``; ``uniform_int_distribution<uint32>``.
    """
    if hi < lo:
        raise FailClosed(f"rng: urand({lo}, {hi}) asserts max >= min (Random.cpp:44)")
    v = world.draw(f"urand({lo},{hi}) {what}")
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise FailClosed(f"rng: draw {v!r} for urand({lo},{hi}) ({what}) is not an int in range")
    return v


def irand(world, lo: int, hi: int, what: str) -> int:
    """Mirrors: Random.cpp:35 ``irand`` (int32, inclusive)."""
    return urand(world, lo, hi, what)


def rand_norm(world, what: str) -> float:
    """Mirrors: Random.cpp:75 ``rand_norm`` -- ``uniform_real_distribution<float>`` in [0, 1)."""
    v = world.draw(f"rand_norm {what}")
    if not isinstance(v, float) or not 0.0 <= v < 1.0:
        raise FailClosed(f"rng: rand_norm draw {v!r} ({what}) must be a float in [0,1)")
    return v


def roll_chance_f(world, chance: float, what: str) -> bool:
    """Mirrors: Random.h:52 ``roll_chance<float>`` -- ``chance > rand_chance()``; always draws."""
    v = world.draw(f"rand_chance {what}")
    if not isinstance(v, float) or not 0.0 <= v < 100.0:
        raise FailClosed(f"rng: rand_chance draw {v!r} ({what}) must be a float in [0,100)")
    return chance > v


# ---------------------------------------------------------------------------
# container helpers (Containers.h)
# ---------------------------------------------------------------------------
def random_resize_draw_count(size: int, n: int) -> int:
    """Number of ``urand`` draws ``RandomResize(container, n)`` consumes.

    Mirrors: Containers.h:69-86 -- none when ``size <= n``; otherwise exactly
    ``size`` (one per element, including after the keep quota is exhausted).
    """
    return 0 if size <= n else size


def random_resize(world, items: Sequence[Any], n: int, trace: Trace | None = None,
                  what: str = "RandomResize") -> list[Any]:
    """Selection sampling that keeps the relative order of the kept elements.

    Mirrors: Containers.h:67 ``RandomResize(C&, size_t)``:
    ``elementsToProcess = uint32(size)``; early return when ``<= requestedSize``;
    for each element ``urand(1, elementsToProcess) <= elementsToKeep`` keeps it.
    """
    items = list(items)
    if n < 0:
        raise FailClosed("rng: RandomResize requestedSize is a size_t (negative value would wrap)")
    remaining = len(items)
    if remaining <= n:
        if trace is not None:
            trace.add(f"{what}.noop", "Containers.h:70-71", output=items, notes=["size <= requested: no draws"])
        return items
    keep_left = n
    kept: list[Any] = []
    draws: list[int] = []
    for item in items:
        d = urand(world, 1, remaining, what)
        draws.append(d)
        if d <= keep_left:
            kept.append(item)
            keep_left -= 1
        remaining -= 1
    if trace is not None:
        trace.add(what, "Containers.h:67-87", output=kept, draws=draws,
                  inputs={"size": len(items), "requested": n})
    return kept


def random_resize_pred(world, items: Sequence[Any], pred: Callable[[Any], bool], n: int,
                       trace: Trace | None = None, what: str = "RandomResize(pred)") -> list[Any]:
    """Mirrors: Containers.h:91-102 -- filter (order kept), then ``RandomResize`` only if ``n != 0``."""
    filtered = [i for i in items if pred(i)]
    if n:
        return random_resize(world, filtered, n, trace, what)
    if trace is not None:
        trace.add(f"{what}.filter-only", "Containers.h:98-101", output=filtered, notes=["requestedSize 0: no resize, no draws"])
    return filtered


def select_random(world, items: Sequence[Any], trace: Trace | None = None,
                  what: str = "SelectRandomContainerElement") -> Any:
    """Mirrors: Containers.h:110-115 -- one ``urand(0, size-1)``, even for a single element.

    An empty container is undefined behaviour in Trinity (``urand(0, uint32(-1))``
    then advancing past the end); every pinned caller guards it, so the oracle
    fails closed.
    """
    items = list(items)
    if not items:
        raise FailClosed("rng: SelectRandomContainerElement on an empty container is UB (callers must guard)")
    i = urand(world, 0, len(items) - 1, what)
    if trace is not None:
        trace.add(what, "Containers.h:110-115", output=items[i], draws=[i], inputs={"size": len(items)})
    return items[i]


def random_shuffle(world, items: Sequence[Any], trace: Trace | None = None,
                   what: str = "RandomShuffle") -> list[Any]:
    """Forward Fisher-Yates: for ``i = 1..n-1``: ``swap(a[i], a[j_i])``, ``j_i`` in ``[0, i]``.

    Mirrors: Containers.h:171 ``RandomShuffle`` -> ``std::ranges::shuffle(begin, end,
    RandomEngine())`` -> **libstdc++ 13** ``std::shuffle`` (stl_algo.h:3742-3805).

    The fixture draws are the **swap indices** ``j_1 .. j_{n-1}``, not engine calls.  With
    the 32-bit engine libstdc++ takes the paired branch (3768-3798): for an even ``n`` one
    ``uniform_int_distribution{0,1}`` call produces ``j_1``; then every further *pair* of
    positions ``(i, i+1)`` is produced by one ``__gen_two_uniform_ints`` call (one
    ``uniform_int_distribution`` over ``(i+1)(i+2)`` values), i.e. ``floor(n/2)``
    distribution calls in total, each of which may take several engine words (rejection).
    The swap order is still ascending ``i`` (probe-checked with a logging ``swap``).
    The index model is **standard-library specific**: libc++ and MSVC implement
    ``shuffle`` differently (TG-D-24), so it is exact only for the probe's libstdc++ 13.
    ``n <= 1``: no draws.
    """
    a = list(items)
    draws: list[int] = []
    for i in range(1, len(a)):
        j = urand(world, 0, i, f"{what} j_{i}")
        draws.append(j)
        a[i], a[j] = a[j], a[i]
    if trace is not None:
        trace.add(what, "Containers.h:171-174; libstdc++13 stl_algo.h:3742-3805", output=a, draws=draws,
                  evidence="trinity-probe",
                  notes=["draws are swap indices j_i, not engine calls (libstdc++13: floor(n/2) distribution calls)"])
    return a


# ---------------------------------------------------------------------------
# hit-result draw plan (the draws AddUnitTarget consumes at selection time)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HitFacts:
    """Facts ``WorldObject::SpellHitResult`` reads before it draws.

    All fields are required: the plan never defaults a gate.
    """
    immune: bool                    # canImmune && IsImmunedToSpell (AddUnitTarget passes canImmune=false)
    damage_immune: bool             # HasOnlyDamageEffects && IsImmunedToDamage
    positive_not_hostile: bool      # IsPositive() && !IsHostileTo(victim)
    self_target: bool               # this == victim
    evading: bool                   # creature victim IsEvadingAttacks
    can_reflect: bool               # AddUnitTarget: m_canReflect && !(IsPositive && FriendlyTo)
    reflect_chance: float           # sum of REFLECT_SPELLS (+ school) auras
    always_hit: bool                # SPELL_ATTR3_ALWAYS_HIT
    dmg_class: int                  # 0 NONE, 1 MAGIC, 2 MELEE, 3 RANGED
    no_avoidance: bool              # SPELL_ATTR3_NO_AVOIDANCE
    victim_dead_non_player: bool    # MagicSpellHitResult early exit


def hit_draw_plan(f: HitFacts, can_immune: bool = False) -> list[dict[str, Any]]:
    """The ordered draws one ``SpellHitResult`` call may consume.

    Mirrors: Object.cpp:1949-1996 ``SpellHitResult``; Object.cpp:1853-1942
    ``MagicSpellHitResult`` (one ``irand(0, 9999)`` at 1919); Unit.cpp:2621-2633
    ``MeleeSpellHitResult`` (one ``urand(0, 9999)`` at 2633; the dodge/parry/block
    and resist/deflect branches reuse that roll).  A reflect success returns
    before the hit roll, so the second entry is conditional on the first
    (``"if": "reflect-failed"``).
    """
    if can_immune and f.immune:
        return []
    if f.damage_immune or f.positive_not_hostile or f.self_target or f.evading:
        return []
    plan: list[dict[str, Any]] = []
    if f.can_reflect and f.reflect_chance > 0:
        plan.append({"draw": "rand_chance", "for": "reflect", "consumer": "Object.cpp:1976"})
    if f.always_hit:
        return plan
    cond = {"if": "reflect-failed"} if plan else {}
    if f.dmg_class in (2, 3):
        if not f.no_avoidance:
            plan.append({"draw": "urand(0,9999)", "for": "melee-spell-hit", "consumer": "Unit.cpp:2633", **cond})
    elif f.dmg_class == 1:
        if not (f.victim_dead_non_player or f.no_avoidance):
            plan.append({"draw": "irand(0,9999)", "for": "magic-spell-hit", "consumer": "Object.cpp:1919", **cond})
    elif f.dmg_class != 0:
        raise FailClosed(f"rng: unknown DmgClass {f.dmg_class}")
    return plan


# ---------------------------------------------------------------------------
# one cast: ordering of targeting draws relative to hit and crit draws
# ---------------------------------------------------------------------------
CAST_ORDER = (
    # (phase, consumer, what draws)
    ("prepare/CheckCast", "Spell.cpp:3693 _cast -> 3816", "none of the targeting kind (CheckCast is draw-free for targeting)"),
    ("SelectExplicitTargets", "Spell.cpp:691-718", "redirect (GetMagicHitRedirectTarget) -- no draw"),
    ("SelectSpellTargets group k", "Spell.cpp:727-790",
     "for each effect-mask group in effect order: TargetA then TargetB selection draws "
     "(RandomResize, random dest CalcRadius/CalcDirectionAngle, script hook draws), each "
     "interleaved with the hit-roll draws AddUnitTarget consumes for every *new* unique target"),
    ("_cast after selection", "Spell.cpp:3839 CallScriptOnCastHandlers",
     "OnCast script handlers (script-specific draws; none bound to current reach with RNG)"),
    ("HandleLaunchPhase: LAUNCH-mode effect handlers", "Spell.cpp:8494-8500",
     "before any crit roll: EffectSummonType (SpellEffects.cpp:1867; default branch CalcRadius draw at 1999 and "
     "GetRandomPoint per extra summon at 2016; SummonGuardian 5035), EffectTransmitted (4454: rand_norm/urand); "
     "no current-player spell reaches these draws (rng.json launch_draw_census)"),
    ("HandleLaunchPhase: crit", "Spell.cpp:8502-8503 -> PreprocessSpellLaunch 8563",
     "one crit roll_chance per unique target whose MissCondition is NONE (or reflect-hit), "
     "in m_UniqueTargetInfo order; the whole launch phase is deferred when LaunchDelay > 0 (Spell.cpp:3880)"),
    ("hit/effect handlers", "Spell.cpp:3980 DoProcessTargetContainer",
     "payload and proc draws (out of targeting scope)"),
)


@dataclass
class CastDraws:
    """The draw sequence of one synthetic cast, built stage by stage."""
    sequence: list[dict[str, Any]] = field(default_factory=list)

    def add(self, phase: str, draw: str, value: Any, consumer: str, **kw: Any) -> None:
        self.sequence.append({"n": len(self.sequence), "phase": phase, "draw": draw, "value": value,
                              "consumer": consumer, **kw})


def area_cap_cast(world, candidates: Sequence[str], cap: int, hit_facts: dict[str, HitFacts],
                  crit_chance: float, trace: Trace | None = None) -> dict[str, Any]:
    """Worked ordering example: one area effect with a random cap, then hit, then crit.

    Mirrors the call sequence ``_cast`` (Spell.cpp:3816) -> ``SelectImplicitAreaTargets``
    (``RandomResize``, Spell.cpp:1448) -> ``AddUnitTarget`` per kept target in list
    order (``SpellHitResult``, Spell.cpp:2485) -> ``HandleLaunchPhase`` ->
    ``PreprocessSpellLaunch`` (crit, Spell.cpp:8563).  The miss/hit decision itself
    is payload (hit tables) and is taken from the fixture as ``world.raw["hit_outcomes"]``
    (``{target: "NONE" | "MISS" | ...}``); only the draw *positions* are the finding.
    """
    cd = CastDraws()
    local = Trace()
    kept = random_resize(world, candidates, cap, local, what="area cap RandomResize")
    for stage in local.stages:
        for k, v in enumerate(stage.draws):
            cd.add("select", f"urand(1,{len(candidates) - k})", v, "Containers.h:76")
    if trace is not None:
        trace.stages.extend(local.stages)
    outcomes = world.raw.get("hit_outcomes")
    if outcomes is None:
        raise FailClosed("fixture: area_cap_cast needs `hit_outcomes` (hit tables are payload)")
    for t in kept:
        for step in hit_draw_plan(hit_facts[t]):
            if step.get("if") == "reflect-failed" and outcomes[t] == "REFLECT":
                continue
            v = world.draw(f"{step['draw']} {step['for']} {t}")
            cd.add("select/AddUnitTarget", step["draw"], v, step["consumer"], target=t)
    for t in kept:
        if outcomes[t] == "NONE":
            v = world.draw(f"rand_chance crit {t}")
            cd.add("launch/PreprocessSpellLaunch", "rand_chance", v, "Spell.cpp:8563", target=t,
                   crit=crit_chance > v)
    return {"kept": kept, "draws": cd.sequence, "consumed": world.draws_consumed}


# ---------------------------------------------------------------------------
# engine RNG sites reachable from implicit target selection (static inventory)
# ---------------------------------------------------------------------------
ENGINE_SITES: tuple[dict[str, Any], ...] = (
    {"id": "area-cap", "consumer": "Spell.cpp:1448", "function": "SelectImplicitAreaTargets",
     "algorithm": "RandomResize(targets, MaxAffectedTargets)", "draws": "size if size > cap else 0",
     "candidate_order": "grid visit order after the area check and the script hook", "replacement": "without",
     "shortcut": "size <= cap: no draws", "owner_track": "C (caps), D (algorithm)"},
    {"id": "cone-cap", "consumer": "Spell.cpp:1311", "function": "SelectImplicitConeTargets",
     "algorithm": "RandomResize(targets, MaxAffectedTargets)", "draws": "size if size > cap else 0",
     "candidate_order": "grid visit order after the cone check and the script hook", "replacement": "without",
     "shortcut": "size <= cap: no draws", "owner_track": "C (caps), D (algorithm)"},
    {"id": "random-dest-radius", "consumer": "SpellInfo.cpp:820-825", "function": "SpellEffectInfo::CalcRadius",
     "algorithm": "radius.Max = (Max - Min) * sqrt(rand_norm())  [Min is not added back]",
     "draws": "1 rand_norm per CalcRadius call whose resolved slot (TargetB only if it has an entry, else TargetA) "
              "has a radius entry and selector 72/74/86; no entry returns first (800-801)", "candidate_order": "n/a",
     "replacement": "n/a", "shortcut": "none (drawn even when Max == Min)", "owner_track": "D"},
    {"id": "random-dest-angle", "consumer": "SpellInfo.cpp:128-129", "function": "SpellImplicitTargetInfo::CalcDirectionAngle",
     "algorithm": "rand_norm() * float(2*M_PI)", "draws": "1 rand_norm for TARGET_DIR_RANDOM ids 72,73,74,75,86,91,149",
     "candidate_order": "n/a", "replacement": "n/a", "shortcut": "none", "owner_track": "A (direction), D (draw order)"},
    {"id": "caster-dest-default-order", "consumer": "Spell.cpp:1608-1609", "function": "SelectImplicitCasterDestTargets",
     "algorithm": "dist = CalcRadius(...).Max [draw 1 if random] ; angle = CalcDirectionAngle() [draw 2 if random]",
     "draws": "TARGET_DEST_CASTER_RANDOM: radius then angle; TARGET_DEST_CASTER_RADIUS (73): angle only",
     "candidate_order": "n/a", "replacement": "n/a", "shortcut": "none", "owner_track": "D"},
    {"id": "target-dest-default-order", "consumer": "Spell.cpp:1672-1673", "function": "SelectImplicitTargetDestTargets",
     "algorithm": "angle = CalcDirectionAngle() [draw 1] ; dist = CalcRadius(nullptr).Max [draw 2 if 74]",
     "draws": "TARGET_DEST_TARGET_RANDOM: angle then radius (opposite of the caster path)",
     "candidate_order": "n/a", "replacement": "n/a", "shortcut": "none", "owner_track": "D"},
    {"id": "dest-dest-default-order", "consumer": "Spell.cpp:1724-1725", "function": "SelectImplicitDestDestTargets",
     "algorithm": "angle [draw 1] ; dist [draw 2 if 86]", "draws": "TARGET_DEST_DEST_RANDOM: angle then radius",
     "candidate_order": "n/a", "replacement": "n/a", "shortcut": "none", "owner_track": "D"},
    {"id": "grouping-radius-compare", "consumer": "Spell.cpp:773-774", "function": "SelectSpellTargets (effect-mask grouping)",
     "algorithm": "CalcRadius(TargetA) != CalcRadius(TargetA) || CalcRadius(TargetB) != ... for later effects j",
     "draws": "for a random-dest selector (72/74/86) in the pair of a NEARBY/CONE/AREA/LINE selector: 1 rand_norm per call "
              "(4 calls per compared pair when TargetA and TargetB are both evaluated); census: see rng.json grouping_draw_census",
     "candidate_order": "n/a", "replacement": "n/a", "shortcut": "only evaluated when shouldCheckRadius(TargetA|TargetB)",
     "owner_track": "D / F"},
    {"id": "nearby-entry-db-fallback", "consumer": "Spell.cpp:1167-1169, 1193, 1256", "function": "SelectImplicitNearbyTargets",
     "algorithm": "TARGET_DEST_NEARBY_ENTRY_OR_DB (142): MovePosition(randomRadius, CalcDirectionAngle())",
     "draws": "142 has TARGET_DIR_NONE -> no angle draw; radius is not a *_RANDOM target -> no draw",
     "candidate_order": "n/a", "replacement": "n/a", "shortcut": "n/a", "owner_track": "A/D"},
    {"id": "nearby-db-positions", "consumer": "Spell.cpp:1603", "function": "SelectImplicitCasterDestTargets TARGET_DEST_NEARBY_DB (106)",
     "algorithm": "SelectRandomContainerElement(positionsInRange)", "draws": "1 urand(0, n-1) (n >= 1; 0 positions fails the cast first)",
     "candidate_order": "spell_target_position multimap order filtered by IsInRange3d", "replacement": "n/a",
     "shortcut": "none (drawn for n == 1)", "owner_track": "E (world), D (draw)"},
    {"id": "fishing", "consumer": "Spell.cpp:1496-1498", "function": "TARGET_DEST_CASTER_FISHING (39)",
     "algorithm": "frand(minDist, maxDist) then rand_norm() angle", "draws": "2", "candidate_order": "n/a",
     "replacement": "n/a", "shortcut": "none", "owner_track": "D (not combat)"},
    {"id": "tap-list", "consumer": "Spell.cpp:1784", "function": "TARGET_UNIT_TARGET_TAP_LIST (124)",
     "algorithm": "SelectRandomContainerElement(creature tap list)", "draws": "1 (creature casters only)",
     "candidate_order": "tap list set order", "replacement": "n/a", "shortcut": "empty list: no draw",
     "owner_track": "D (creature-only; no player reach)"},
    {"id": "smart-injured", "consumer": "Spell.cpp:9513-9573", "function": "Trinity::SelectRandomInjuredTargets",
     "algorithm": "priority bits; std::ranges::sort (unstable); RandomShuffle of the boundary class",
     "draws": "0 if size <= max; else FY indices for the boundary class only (n_boundary - 1)",
     "candidate_order": "hook input order (grid visit order)", "replacement": "without",
     "shortcut": "size <= max: no draws", "owner_track": "D (smart.py)"},
    {"id": "priority-rules", "consumer": "Spell.cpp:9575-9613", "function": "Trinity::SortTargetsWithPriorityRules",
     "algorithm": "rule score bits; std::ranges::sort(greater) (unstable); RandomShuffle of the cutoff tie range",
     "draws": "0 if size <= max; 0 if no tie at the cutoff; else FY indices for the tie range",
     "candidate_order": "hook input order", "replacement": "without", "shortcut": "size <= max: no draws",
     "owner_track": "D (smart.py)"},
    {"id": "select-nearby-target", "consumer": "Unit.cpp:10920-10949", "function": "Unit::SelectNearbyTarget",
     "algorithm": "SelectRandomContainerElement over AnyUnfriendlyUnitInObjectRangeCheck results",
     "draws": "1 if any candidate else 0", "candidate_order": "Cell::VisitAllObjects order minus victim/exclude/LOS/totem/critter",
     "replacement": "n/a", "shortcut": "empty: no draw", "owner_track": "D"},
    {"id": "random-raid-member", "consumer": "Unit.cpp:6657-6705", "function": "Unit::GetNextRandomRaidMemberOrPet",
     "algorithm": "urand(0, n-1) over group members + guardian pets", "draws": "1 if n > 0",
     "candidate_order": "group member list order, each player followed by its pet", "replacement": "n/a",
     "shortcut": "no group: deterministic owner/pet", "owner_track": "D (no caller at the pin: dead code)"},
    {"id": "random-point", "consumer": "Object.cpp:681-689", "function": "WorldObject::GetRandomPoint",
     "algorithm": "angle = rand_norm()*2pi; dist = min + (d-min)*sqrt(rand_norm())", "draws": "2 if distance != 0",
     "candidate_order": "n/a", "replacement": "n/a", "shortcut": "distance == 0: no draws", "owner_track": "D"},
    {"id": "random-near-position", "consumer": "Object.cpp:2776-2780", "function": "WorldObject::GetRandomNearPosition",
     "algorithm": "MovePosition(pos, r*sqrt(rand_norm()), rand_norm()*2pi)",
     "draws": "2 (the two rand_norm() calls are arguments of one call: their evaluation order is "
              "unspecified in C++ -- see rng.json unknowns)",
     "candidate_order": "n/a", "replacement": "n/a", "shortcut": "none", "owner_track": "D"},
)

SPELL_HIT_ORDER = {
    "finding": ("Hit/miss draws are consumed during target selection, not at hit time: AddUnitTarget "
                "(Spell.cpp:2443) calls SpellHitResult (2485) for every *new* unique target, immediately "
                "after that target is chosen, so within one cast the stream reads: group-1 selection draws, "
                "hit draws of group-1's new targets (in AddUnitTarget order), group-2 selection draws, ... ; "
                "then HandleLaunchPhase crit draws (8563) for every target whose MissCondition is NONE (or a "
                "successful reflect), in m_UniqueTargetInfo order; then payload/proc draws. A target already "
                "in m_UniqueTargetInfo only ORs its effect mask (2463-2469): no second hit draw."),
    "evidence": "trinity-consumer",
    "consumers": ["Spell.cpp:2443-2556", "Object.cpp:1949-1996", "Object.cpp:1853-1942", "Unit.cpp:2621-2720",
                  "Spell.cpp:8490-8563", "Spell.cpp:3816", "Spell.cpp:3880-3884"],
    "reflect": ("SPELL_MISS_REFLECT computes ReflectResult with caster->SpellHitResult(caster, ..., canReflect=false) "
                "(Spell.cpp:2525-2528): this == victim returns NONE before any draw -> no extra draw."),
    "cross_refs": ["docs/research/proc-pipeline-archaeology.md §8 (one rand_chance per aura passing pre-RNG checks)",
                   "docs/research/auto-attack-weapon-archaeology.md §7.3 (melee/ranged spell: reflect, urand(0,9999), crit)"],
}


def resolve_order(items: Iterable[str], order: Sequence[str]) -> list[str]:
    """Order ``items`` by a fixture enumeration (helper for callers building candidates)."""
    pos = {a: i for i, a in enumerate(order)}
    missing = [i for i in items if i not in pos]
    if missing:
        raise FailClosed(f"rng: enumeration does not list {missing}")
    return sorted(items, key=pos.__getitem__)
