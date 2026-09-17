"""Effect-local recipients: effect-mask grouping, the unique target list, per-effect dests (track F).

Three consumer mechanisms decide *which effects* a selected unit receives and in
which order recipients are processed:

1. **Effect-mask grouping** (``Spell::SelectSpellTargets``, Spell.cpp:720-800).  For each
   effect ``i`` (``IsEffect``) the selection runs once for the mask
   ``{i} ∪ {j > i : same TargetA, same TargetB, same ImplicitTargetConditions pointer,
   same PlayersOnly bit, CheckScriptEffectImplicitTargets(i, j), and -- only when TargetA or
   TargetB of i is NEARBY/CONE/AREA/LINE -- equal CalcRadius for both indices}``
   minus the effects already processed.  The selector code reads the **lead** effect's
   ``SpellEffectInfo`` only (radius, ChainTargets, PositionFacing, ...), so every grouped
   effect shares one candidate list, one ``RandomResize`` draw set and one chain.
2. **The unique target list** (``Spell::AddUnitTarget``, Spell.cpp:2443-2560): one entry per
   GUID, ``EffectMask`` OR-merged on re-add, insertion order kept forever (never sorted;
   the delayed-hit ``remove_if`` at Spell.cpp:4129 is stable).  Hit processing is
   per effect, then per target in list order (``DoProcessTargetContainer``, Spell.cpp:3980),
   and ``GetUnitTargetIndexForEffect`` (Spell.cpp:2700) is the rank among ``MISS_NONE``
   entries that carry the effect bit.
3. **Per-effect destinations** (Spell.cpp:797-800 / 2695): after an effect's turn, the
   spell-wide ``m_targets`` dst (if any) is copied into ``m_destTargets[eff]``.  A later
   group can move ``m_targets`` dst, so earlier effects keep the older dst.

Stable names: :func:`group_effect_masks`, :func:`selection_plan`,
:func:`script_effect_check`, :func:`implicit_condition_identity`, :class:`UniqueTargets`,
:func:`select_spell_targets` (RandomResize lives in ``targeting.rng``).
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import FailClosed
from .trace import Trace

RADIUS_CHECKED_CATEGORIES = ("NEARBY", "CONE", "AREA", "LINE")   # Spell.cpp:759-767
PLAYERS_ONLY = 0x00004000                                         # DBCEnums.h:2417
RANDOM_RADIUS_TARGETS = (72, 74, 86)                              # SpellInfo.cpp:822-825
TARGET_HOOK_LISTS = ("OnObjectTargetSelect", "OnObjectAreaTargetSelect")  # Spell.cpp:9086-9097


def category(target: int) -> str:
    """``SpellImplicitTargetInfo::GetSelectionCategory`` (track A table)."""
    from .selectors import info
    return info(target).category


def _is_effect(e: Any) -> bool:
    return getattr(e, "effect", 0) != 0


def _players_only(e: Any) -> bool:
    return bool(getattr(e, "attributes", 0) & PLAYERS_ONLY)


def _should_check_radius(e: Any) -> bool:
    """Mirrors: Spell.cpp:759-772 ``shouldCheckRadius(TargetA) || shouldCheckRadius(TargetB)`` (lead only)."""
    return category(e.target_a) in RADIUS_CHECKED_CATEGORIES or category(e.target_b) in RADIUS_CHECKED_CATEGORIES


# ---------------------------------------------------------------------------
# grouping
# ---------------------------------------------------------------------------

@dataclass
class PlanStep:
    effect: int              # the effect whose turn this is
    mask: int                # effects selected together on this turn (0 = already processed)
    raw_mask: int            # the lambda result before ``&= ~processed``
    split: dict[int, str] = field(default_factory=dict)   # j -> first failing criterion

    def to_json(self) -> dict[str, Any]:
        return {"effect": self.effect, "mask": self.mask, "raw_mask": self.raw_mask,
                "split": {str(k): v for k, v in sorted(self.split.items())}}


def selection_plan(effects: Sequence[Any], radius_fn: Callable[[int, str], tuple[float, float]] | None,
                   script_check: Callable[[int, int], bool], trace: Trace | None = None,
                   radius_equal: Callable[[int, int], bool] | None = None) -> list[PlanStep]:
    """One step per ``IsEffect`` effect, in effect order, with its selection mask.

    Mirrors: Spell.cpp:726-788.  ``effects`` is dense (``effects[k].index == k``; gaps have
    ``effect == 0``) and each element exposes ``index, effect, target_a, target_b,
    attributes, conditions`` (``conditions`` = identity of the shared condition container,
    None = nullptr).  ``radius_fn(k, "A"|"B")`` returns ``CalcRadius(m_caster, index)`` of
    effect ``k`` as ``(Min, Max)`` and is called lazily, in the consumer's order and with
    the ``||`` short-circuit, so an RNG-consuming radius (``*_RANDOM`` targets) draws exactly
    as often as Trinity does.  Operand order of ``!=`` is unspecified in C++; lead-first is
    assumed (only observable with RNG-consuming radii).  ``radius_equal(i, j)`` replaces the
    four calls with a precomputed relation (static census use only).
    """
    for k, e in enumerate(effects):
        if e.index != k:
            raise FailClosed(f"selection_plan: effects must be dense (position {k} holds index {e.index})")
    processed = 0
    plan: list[PlanStep] = []
    for e in effects:
        if not _is_effect(e):
            continue
        i = e.index
        mask = 1 << i
        split: dict[int, str] = {}
        for j in range(i + 1, len(effects)):
            f = effects[j]
            if not _is_effect(f):
                split[j] = "not-effect"
                continue
            if e.target_a != f.target_a:
                split[j] = "target-a"
                continue
            if e.target_b != f.target_b:
                split[j] = "target-b"
                continue
            if e.conditions != f.conditions:
                split[j] = "conditions"
                continue
            if _players_only(e) != _players_only(f):
                split[j] = "players-only"
                continue
            if not script_check(i, j):
                split[j] = "script-hooks"
                continue
            if _should_check_radius(e):
                if radius_equal is not None:
                    differs = not radius_equal(i, j)
                elif radius_fn is None:
                    raise FailClosed("selection_plan: radius comparison reached without radius_fn")
                else:
                    differs = radius_fn(i, "A") != radius_fn(j, "A") or radius_fn(i, "B") != radius_fn(j, "B")
                if differs:
                    split[j] = "radius"
                    continue
            mask |= 1 << j
        raw = mask
        mask &= ~processed
        processed |= mask
        step = PlanStep(effect=i, mask=mask, raw_mask=raw, split=split)
        plan.append(step)
        if trace is not None:
            trace.add("recipients.selection_plan", "Spell.cpp:741-785", output=step.to_json())
    return plan


def group_effect_masks(spell_view: Any, radius_fn: Callable[[int, str], tuple[float, float]],
                       script_check: Callable[[int, int], bool] | None = None,
                       trace: Trace | None = None) -> list[int]:
    """The non-empty selection masks of a spell, in processing order.

    Mirrors: Spell.cpp:741-785.  ``script_check`` defaults to :func:`script_effect_check`
    over ``spell_view.script_hooks`` (fails closed when those are unknown).
    """
    effects = spell_view.effects
    if script_check is None:
        script_check = script_effect_check(spell_view)
    return [s.mask for s in selection_plan(effects, radius_fn, script_check, trace) if s.mask]


def hook_loads(spell_id: int, hook: dict[str, Any]) -> bool:
    """Is the hook's script in ``m_loadedScripts``?  A hook dict may state ``loads``; otherwise
    track E's ``adapters.script_loads`` evaluates ``Validate()`` on the 12.1 rows (FailClosed when it
    cannot).  Added by track G for hostile review R1-07 (track F finished)."""
    if "loads" in hook:
        return bool(hook["loads"])
    from .adapters import script_loads
    return script_loads(spell_id, hook.get("script", ""))


def script_effect_check(spell_view: Any) -> Callable[[int, int], bool]:
    """``Spell::CheckScriptEffectImplicitTargets`` over the view's executing target hooks.

    Mirrors: Spell.cpp:9066-9100.  Per loaded SpellScript (hooks grouped by ``script``):
    for OnObjectTargetSelect and OnObjectAreaTargetSelect, every hook affecting ``i`` must
    have a hook with the same function (``HasSameTargetFunctionAs`` = ``ImplStorage``
    equality, modelled as ``(script, handler)``) affecting ``j``, and vice versa.
    OnDestinationTargetSelect hooks are not compared.  The hook's own registered target type
    is not compared (only its ``GetAffectedEffectsMask``).
    """
    hooks = getattr(spell_view, "script_hooks", None)
    if hooks is None:
        raise FailClosed(f"spell {getattr(spell_view, 'id', '?')}: script target hooks unknown")
    by_script: dict[str, list[dict[str, Any]]] = {}
    spell_id = getattr(spell_view, "id", 0)
    for h in hooks:
        if h["list"] not in TARGET_HOOK_LISTS:
            continue
        if not hook_loads(spell_id, h):
            # Validate() fails on the 12.1 rows -> the script is never in m_loadedScripts
            # (SpellScriptLoader / _Validate, SpellScript.cpp:26-34); its hooks cannot split a group.
            continue
        if h.get("affected_mask") is None:
            raise FailClosed(f"spell {getattr(spell_view, 'id', '?')}: unresolved target hook {h.get('handler')}")
        by_script.setdefault(h.get("script", ""), []).append(h)

    def shared(lst: list[dict[str, Any]], a: int, b: int) -> bool:
        for h in lst:
            if not h["affected_mask"] & (1 << a):
                continue
            if not any(o["affected_mask"] & (1 << b) and o.get("handler") == h.get("handler") for o in lst):
                return False
        return True

    def check(i: int, j: int) -> bool:
        for script_hooks in by_script.values():
            for name in TARGET_HOOK_LISTS:
                lst = [h for h in script_hooks if h["list"] == name]
                if not shared(lst, i, j) or not shared(lst, j, i):
                    return False
        return True

    return check


def implicit_condition_identity(effects: Sequence[Any], groups: Iterable[tuple[int, int | None]]) -> list[int | None]:
    """Identity of each effect's ``ImplicitTargetConditions`` container after loading.

    ``groups``: one ``(SourceGroup, ConditionValue1_if_OBJECT_ENTRY_GUID_else_None)`` per
    condition row of CONDITION_SOURCE_TYPE_SPELL_IMPLICIT_TARGET for the spell.  Returns a
    container id per effect (None = nullptr).  ``ConditionStore`` is an ``unordered_map``,
    so the load order of distinct ``ConditionId``s is unspecified: every order is tried and
    an order-dependent result fails closed.

    Mirrors: ConditionMgr.cpp:1575-1680 (``addToSpellImplicitTargetConditions``).
    OBJECT_ENTRY_GUID type validation (1588-1611) is not ported: such rows fail closed.
    """
    rows = list(groups)
    if any(v is not None for _, v in rows):
        raise FailClosed("implicit_condition_identity: CONDITION_OBJECT_ENTRY_GUID validation not ported")
    distinct = sorted({g for g, _ in rows})
    n = len(effects)

    def load(order: Sequence[int]) -> list[int | None]:
        ptr: list[int | None] = [None] * n
        new_id = 0
        for cond_mask in order:
            shared_masks: list[int] = []
            for k in range(n):
                if any(m & (1 << k) for m in shared_masks):
                    continue
                m = 1 << k
                for x in range(k + 1, n):
                    if ptr[x] == ptr[k]:
                        m |= 1 << x
                shared_masks.append(m)
            for eff_mask in shared_masks:
                common = eff_mask & cond_mask
                if not common:
                    continue
                first = next(k for k in range(n) if eff_mask & (1 << k))
                if ptr[first] is not None:
                    if cond_mask != eff_mask:
                        pass  # overlapping masks: condition ignored (1652-1658)
                    break
                new_id += 1
                for k in range(first, n):
                    if common & (1 << k):
                        ptr[k] = new_id
                break
        # canonical relabel by first occurrence
        relabel: dict[int, int] = {}
        return [None if p is None else relabel.setdefault(p, len(relabel) + 1) for p in ptr]

    if not distinct:
        return [None] * n
    results = {tuple(load(order)) for order in itertools.permutations(distinct)}
    if len(results) != 1:
        raise FailClosed(f"implicit_condition_identity: container sharing depends on unordered_map order "
                         f"(SourceGroups {distinct})")
    return list(results.pop())


# ---------------------------------------------------------------------------
# unique target list
# ---------------------------------------------------------------------------

@dataclass
class TargetInfo:
    guid: str
    effect_mask: int
    first_los_position: Any = None
    adds: int = 1


class UniqueTargets:
    """``Spell::m_UniqueTargetInfo`` for units.

    ``effect_target_ok(target, eff_index, los_position) -> bool`` is ``CheckEffectTarget``;
    ``check_target(target, implicit) -> bool`` is ``SpellInfo::CheckTarget == SPELL_CAST_OK``;
    ``immune(target, eff_index) -> bool`` is ``IsImmunedToSpellEffect``.  ``n_effects`` is
    ``GetEffects().size()``; ``is_effect(k)`` is ``SpellEffectInfo::IsEffect``.
    """

    def __init__(self, n_effects: int, is_effect: Callable[[int], bool],
                 effect_target_ok: Callable[[str, int, Any], bool],
                 check_target: Callable[[str, bool], bool],
                 immune: Callable[[str, int], bool], trace: Trace | None = None) -> None:
        self.n = n_effects
        self.is_effect = is_effect
        self.effect_target_ok = effect_target_ok
        self.check_target = check_target
        self.immune = immune
        self.trace = trace
        self.items: list[TargetInfo] = []

    def add(self, target: str, effect_mask: int, check_if_valid: bool = True, implicit: bool = True,
            los_position: Any = None) -> str:
        """Returns ``rejected-effects`` / ``rejected-check`` / ``merged`` / ``added``.

        Mirrors: Spell.cpp:2443-2472 (+ 2559 ``emplace_back``).  Notes reproduced:
        CheckEffectTarget runs for every effect index (bits outside the mask are no-ops);
        CheckTarget is skipped on ``checkIfValid=false``; immunity clears bits **after**
        the empty test, so a fully immune target is still added / merged with mask 0; a
        re-add ORs the mask and keeps the first entry's hit result, delay and position.
        """
        for k in range(self.n):
            if not self.is_effect(k) or not self.effect_target_ok(target, k, los_position):
                effect_mask &= ~(1 << k)
        if not effect_mask:
            return self._out("rejected-effects", target, effect_mask)
        if check_if_valid and not self.check_target(target, implicit):
            return self._out("rejected-check", target, effect_mask)
        for k in range(self.n):
            if self.immune(target, k):
                effect_mask &= ~(1 << k)
        for item in self.items:
            if item.guid == target:
                item.effect_mask |= effect_mask
                item.adds += 1
                return self._out("merged", target, effect_mask)
        self.items.append(TargetInfo(target, effect_mask, los_position))
        return self._out("added", target, effect_mask)

    def _out(self, result: str, target: str, mask: int) -> str:
        if self.trace is not None:
            self.trace.add("recipients.add_unit_target", "Spell.cpp:2443-2559", output=result,
                           inputs={"target": target, "mask": mask})
        return result

    # -- readers -----------------------------------------------------------
    def order(self) -> list[str]:
        return [t.guid for t in self.items]

    def masks(self) -> dict[str, int]:
        return {t.guid: t.effect_mask for t in self.items}

    def recipients(self, eff_index: int) -> list[str]:
        """Targets carrying the effect bit, in list order (processing order of that effect)."""
        return [t.guid for t in self.items if t.effect_mask & (1 << eff_index)]

    def hit_order(self, missed: Callable[[str], bool] = lambda _t: False) -> list[tuple[int, str]]:
        """``DoProcessTargetContainer``: for each effect, each target in list order.

        Mirrors: Spell.cpp:3980-3992 (``DoTargetSpellHit`` is reached for every entry with the bit;
        a missed target is skipped inside the hit, which ``missed`` models).
        """
        return [(k, t.guid) for k in range(self.n) for t in self.items
                if t.effect_mask & (1 << k) and not missed(t.guid)]

    def target_index_for_effect(self, target: str, eff_index: int,
                                missed: Callable[[str], bool] = lambda _t: False) -> int:
        """Mirrors: Spell.cpp:2700-2715 ``GetUnitTargetIndexForEffect`` (-1 when absent)."""
        idx = 0
        for t in self.items:
            if not missed(t.guid) and t.effect_mask & (1 << eff_index):
                if t.guid == target:
                    return idx
                idx += 1
        return -1


# ---------------------------------------------------------------------------
# selection loop
# ---------------------------------------------------------------------------

@dataclass
class SelectionResult:
    plan: list[PlanStep]
    targets: UniqueTargets
    dests: dict[int, Any]
    dst_history: list[tuple[int, Any]]


def select_spell_targets(effects: Sequence[Any], plan: list[PlanStep], targets: UniqueTargets,
                         select: Callable[[Any, str, int, dict[str, Any]], None],
                         effect_type_select: Callable[[Any, dict[str, Any]], None],
                         state: dict[str, Any], trace: Trace | None = None) -> SelectionResult:
    """The per-effect loop skeleton of ``Spell::SelectSpellTargets``.

    Mirrors: Spell.cpp:726-801.  For each ``IsEffect`` effect in order: when its plan mask is
    non-zero, ``select(lead_effect, "A", mask, state)`` then ``select(lead, "B", mask, state)``
    (the callable skips an empty selector like Spell.cpp:947); then
    ``effect_type_select(effect, state)`` for **every** effect (grouped or not); then, if
    ``state["dst"]`` is set, the current dst is recorded for this effect only.  ``state`` is
    the spell-wide ``m_targets`` (keys ``dst``, ``src``, ``unit``) the callables may change.
    REQUIRE_ALL_TARGETS / channel / immune checks (802-866) are not part of this skeleton.
    """
    steps = {s.effect: s for s in plan}
    dests: dict[int, Any] = {}
    history: list[tuple[int, Any]] = []
    for e in effects:
        if not _is_effect(e):
            continue
        step = steps.get(e.index)
        if step is None:
            raise FailClosed(f"select_spell_targets: plan has no step for effect {e.index}")
        if step.mask:
            select(e, "A", step.mask, state)
            select(e, "B", step.mask, state)
        effect_type_select(e, state)
        if state.get("dst") is not None:
            dests[e.index] = state["dst"]
            history.append((e.index, state["dst"]))
        if trace is not None:
            trace.add("recipients.effect_turn", "Spell.cpp:784-800",
                      output={"effect": e.index, "mask": step.mask, "dst": state.get("dst")})
    return SelectionResult(plan, targets, dests, history)
