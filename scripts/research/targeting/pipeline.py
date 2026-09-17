"""Executable mirror of ``Spell::SelectSpellTargets`` over a neutral fixture.

Mirrors: Spell.cpp:720-878 (pinned 7f3d43b).  :func:`evaluate` runs, in consumer
order:

1. ``SelectExplicitTargets`` (691) -- explicit redirect (track B
   ``explicit.select_explicit_targets``, or a fixture-stated ``explicit.redirect``);
2. per effect index (``IsEffect`` only): the effect-mask group of the lead effect
   (741-782, computed *lazily per lead* so RNG-consuming radii draw in consumer
   order) minus the processed mask (784); ``SelectEffectImplicitTargets`` for
   TargetA then TargetB with the *lead* effect's ``SpellEffectInfo`` (787-788); then
   for every effect ``SelectEffectTypeImplicitTargets`` (797), ``AddDestTarget``
   (799-800), ``SPELL_ATTR1_REQUIRE_ALL_TARGETS`` (802-822) and the channel check (824-843);
3. ``SPELL_ATTR2_FAIL_ON_ALL_TARGETS_IMMUNE`` (846-859) and the liquid dest check (861-874).

Output (:class:`Result`): per effect index the ordered recipient ids (the order of
``m_UniqueTargetInfo``, which ``DoProcessTargetContainer`` visits per effect,
Spell.cpp:3980), per-effect dests (``m_destTargets``), the unique target list with
effect masks, the cast result, the RNG draws consumed and the full
:class:`targeting.trace.Trace`.

Stage owners (imported, no local copies): selector vocabulary ``selectors`` (A);
explicit redirect, ``SpellInfo::CheckTarget`` and the relation part of
``WorldObjectSpellTargetCheck`` ``explicit`` / ``relations`` (B); searches, cone, line,
nearby, caps and distance sort ``area`` / ``caps`` / ``geometry`` (C); chain and
``RandomResize`` ``chain`` / ``rng`` (D); grouping script check, implicit-condition
identity and caster-object resolution ``recipients`` / ``groups`` (F).  Script target
hooks (E) run through a fixture-stated result (``script_results``) or a
``targeting.adapters.run_target_hook`` adapter when one exists; otherwise FailClosed.

``m_targets`` (unit / src / dst) changes during selection; the stage modules read it
from ``world.explicit``, so they are called with a :class:`_TargetsView` whose
``explicit`` is the *current* ``m_targets`` (everything else, including the RNG
stream, is the real world's).

Fixture facts read here beyond :mod:`targeting.fixture` (optional blocks; a stage
that needs an unstated one fails closed):

* ``precast: true`` -- run InitExplicitTargets + CheckCast (track B) first; ``explicit`` is then the
  client input (``unit`` / ``selection`` / ``victim``);
* ``explicit.redirect`` -- stated outcome of ``SelectExplicitTargets`` (``null`` = none);
  ``explicit.object`` (gameobject/corpse), ``explicit.item``, ``explicit.traj``
  (``null`` = no trajectory), ``explicit.dest_orientation`` / ``src_orientation``;
* ``spell.triggered_by_aura``: ``null`` or ``{"ignores_los": bool}`` (also read by track B);
* ``cast``: ``{"focus_object": id|null, "channel": {...}|null, "dest_on_liquid": bool}``
  (the original caster's kind comes from the fixture ``original_caster``);
* ``hit``: ``{"draws": int | {id|"default": int}, "results": {id: "NONE"|"IMMUNE"|...}}`` --
  ``SpellHitResult`` RNG consumption per *new* unique target (2486) and results;
* ``immunity``: ``{id | "default": [effect indices]}`` or actor fact ``immune_effects`` --
  ``IsImmunedToSpellEffect``;
* ``dest_positions``: ``{"<effect>:<A|B>": [x, y, z(, o)]}`` -- resolved positions of
  collision / height / DB dependent dest selectors;
* ``script_results``: ``{"<list>:<effect>:<target>": {"result": ..., "draws": n | "rng_free": true}}`` --
  post-hook result of a script target hook and the stream draws it consumes (prefer the modelled
  track-E adapter, which draws itself);
* ``effect_target_checks``: ``{"<id>:<effect>": bool}`` -- ``CheckEffectTarget`` results for
  branches that are not modelled (charm auras, corpse skinning);
* ``los`` pairs may name the pseudo-ids ``@dst`` / ``@src`` (LOS to m_targets positions).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

from procs.enums import effect as effect_by_name

from . import FailClosed, oracle
from . import area as C
from . import geometry as geo
from .fixture import World
from .oracle import EffectView, SpellView, f32
from .selectors import (
    TARGET_FLAG_CORPSE_MASK,
    TARGET_FLAG_GAMEOBJECT_MASK,
    TARGET_FLAG_ITEM_MASK,
    TARGET_FLAG_UNIT_MASK,
    effect_implicit_target_type,
)
from .selectors import info as selector_info
from .trace import Trace

S = "Spell.cpp"
UNIT_KINDS = oracle.UNIT_KINDS
OK = "SPELL_CAST_OK"
RADIUS_CATEGORIES = ("NEARBY", "CONE", "AREA", "LINE")
CHARM_AURAS = (2, 6, 128, 177)   # MOD_POSSESS, MOD_CHARM, MOD_POSSESS_PET, AOE_CHARM (SpellAuraDefines.h)
EFFECT_SUMMON_PLAYER = effect_by_name("SUMMON_PLAYER")
EFFECT_SUMMON_RAF_FRIEND = effect_by_name("SUMMON_RAF_FRIEND")
EFFECT_SKIN_PLAYER_CORPSE = effect_by_name("SKIN_PLAYER_CORPSE")


def _owner(module: str, fn: str):
    """An optional stage function (script adapters), or None when it has not landed."""
    try:
        mod = importlib.import_module(f"targeting.{module}")
    except ModuleNotFoundError:
        return None
    return getattr(mod, fn, None)


class _Finish(Exception):
    """A ``return`` out of ``SelectSpellTargets`` itself (or out of ``prepare`` for the pre-stage)."""

    def __init__(self, result: str, where: str) -> None:
        super().__init__(result)
        self.result = result
        self.where = where


class _TargetsView:
    """The world as the stage modules must see it: ``explicit`` = current ``m_targets``."""

    def __init__(self, world: World, explicit: dict[str, Any]) -> None:
        self._world = world
        self.explicit = explicit

    def __getattr__(self, name: str) -> Any:
        return getattr(self._world, name)


class _GuardedWorld:
    """The fixture world with an RNG guard: a selection draw after the first new unique target
    needs a stated ``hit.draws`` model, because ``SpellHitResult`` (Spell.cpp:2486) may have
    consumed engine RNG in between."""

    def __init__(self, world: World, ev: Evaluation) -> None:
        self._world = world
        self._ev = ev

    def draw(self, what: str) -> Any:
        ev = self._ev
        if ev.first_add_draw is not None and not ev._in_hit and \
                (self._world.raw.get("hit") or {}).get("draws") is None:
            raise FailClosed(f"fixture: {what} draws after a unique target was added; SpellHitResult "
                             "(Spell.cpp:2486) may consume RNG in between -> state `hit.draws`")
        return self._world.draw(what)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._world, name)


@dataclass
class Result:
    spell: int
    name: str
    cast_result: str
    recipients: dict[int, list[str]]
    unique_targets: list[dict[str, Any]]
    dests: dict[int, list[float | None]]
    draws_consumed: int
    draws: list[Any]
    trace: Trace
    channel_mask: int = 0
    defects: list[str] = field(default_factory=list)

    def to_json(self, with_trace: bool = True) -> dict[str, Any]:
        out = {"spell": self.spell, "name": self.name, "cast_result": self.cast_result,
               "recipients": {str(k): v for k, v in sorted(self.recipients.items())},
               "unique_targets": self.unique_targets,
               "dests": {str(k): v for k, v in sorted(self.dests.items())},
               "draws_consumed": self.draws_consumed, "draws": self.draws,
               "channel_mask": self.channel_mask, "defects": sorted(set(self.defects))}
        if with_trace:
            out["trace"] = self.trace.to_json()
        return out


def _pos(p) -> tuple[float, float, float, float | None]:
    if p is None or len(p) not in (3, 4):
        raise FailClosed(f"fixture: position must be [x, y, z(, o)], got {p!r}")
    o = f32(p[3]) if len(p) == 4 and p[3] is not None else None
    return (f32(p[0]), f32(p[1]), f32(p[2]), o)


class Evaluation:
    def __init__(self, world: World, sv: SpellView | None = None, trace: Trace | None = None) -> None:
        self._in_hit = False
        self.first_add_draw: int | None = None
        self.w = _GuardedWorld(world, self)
        self.sv = sv if sv is not None else oracle.for_world(world)
        self.t = trace if trace is not None else Trace()
        self.caster = world.caster
        ex = world.explicit
        self.unit_target: str | None = ex.get("unit")
        self.object_target: str | None = ex.get("object")
        self.item_target: str | None = ex.get("item")
        self.src = self._explicit_pos(ex, "src")
        self.dst = self._explicit_pos(ex, "dest")
        from .recipients import UniqueTargets
        n = len(self.sv.effects)
        self.targets = UniqueTargets(
            n, lambda k: self.sv.effects[k].is_effect,
            lambda t, k, los: self._effect_target_ok(t, k, los),
            lambda t, implicit: self.check_target(t, implicit) == OK,
            lambda t, k: self.sv.effects[k].is_effect and self.immune(t, self.sv.effects[k]),
            self.t)
        self._notes: list[str] = []
        self.dests: dict[int, tuple] = {}
        self.channel_mask = 0
        self.defects: list[str] = []
        self.cast = world.raw.get("cast") or {}
        self.finished: str | None = None

    @property
    def unique(self) -> list[tuple[str, int]]:
        """``m_UniqueTargetInfo`` as (guid, EffectMask) in list order (track F ``UniqueTargets``)."""
        return [(t.guid, t.effect_mask) for t in self.targets.items]

    @staticmethod
    def _explicit_pos(ex: dict, key: str):
        if ex.get(key) is None:
            return None
        p = list(ex[key])
        if len(p) == 3 and ex.get(f"{key}_orientation") is not None:
            p.append(ex[f"{key}_orientation"])
        return _pos(p)

    # -- helpers -------------------------------------------------------------
    def view(self) -> _TargetsView:
        ex: dict[str, Any] = {"unit": self.unit_target}
        for key, p in (("src", self.src), ("dest", self.dst)):
            if p is not None:
                ex[key] = [p[0], p[1], p[2]]
                ex[f"{key}_orientation"] = p[3]
        return _TargetsView(self.w, ex)

    def finish(self, result: str, where: str) -> None:
        """Mirrors: Spell.cpp:4356 ``Spell::finish`` -- only sets the spell state (the first call wins);
        selection goes on until ``SelectSpellTargets`` itself returns, ``_cast`` checks the state after
        it (Spell.cpp:3816-3819)."""
        first = self.finished is None
        if first:
            self.finished = result
        self.t.add("finish", where, output=result,
                   notes=["m_spellState = FINISHED; selection continues" if first
                          else "already FINISHED: finish() returns immediately (result unchanged)"])

    def stop(self, result: str, where: str) -> None:
        """``SendCastResult; finish(result); return;`` inside SelectSpellTargets (802-874)."""
        self.finish(result, where)
        raise _Finish(self.finished, where)

    def cast_fact(self, key: str) -> Any:
        if key not in self.cast:
            raise FailClosed(f"fixture: stage needs server/cast fact `cast.{key}`")
        return self.cast[key]

    def spell_fact(self, key: str) -> Any:
        if key not in self.w.spell:
            raise FailClosed(f"fixture: stage needs `spell.{key}`")
        return self.w.spell[key]

    def radius_mod(self) -> float:
        """``m_spellValue->RadiusMod``.

        ``spell_value`` lists the cast's SpellValue *overrides* (``CastSpellExtraArgs``,
        Spell.cpp:8720-8760; same convention as ``caps.effective_max_targets``); without an
        override the value is the SpellValue constructor's 1.0f (Spell.cpp:449).
        """
        if "radius_mod" not in self.w.spell_value:
            return 1.0
        return f32(float(self.w.spell_value["radius_mod"]))

    def actor_pos(self, actor_id: str):
        a = self.w.actor(actor_id)
        p = a.need("pos")
        return (f32(p[0]), f32(p[1]), f32(p[2]), geo.normalize_orientation(float(a.need("orientation"))))

    def is_unit(self, actor_id: str) -> bool:
        return self.w.actor(actor_id).kind in UNIT_KINDS

    def rand_norm(self, what: str) -> float:
        from .rng import rand_norm
        return rand_norm(self.w, what)

    def direction_angle(self, sel) -> float:
        """``CalcDirectionAngle`` -- TARGET_DIR_RANDOM consumes one rand_norm (SpellInfo.cpp:133)."""
        rn = self.rand_norm(f"CalcDirectionAngle {sel.name}") if sel.direction == "RANDOM" else None
        return sel.direction_angle(rn)

    def radius(self, eff: EffectView, idx: str, caster: str | None = "caster") -> tuple[float, float]:
        cid = self.caster if caster == "caster" else caster
        return oracle.calc_radius(self.w, self.sv, eff, idx, cid, self.t)

    def scaled_radius(self, eff: EffectView, idx: str) -> tuple[float, float]:
        """``CalcRadius(m_caster, idx) * m_spellValue->RadiusMod`` (SpellDefines.h:351, float)."""
        rmin, rmax = self.radius(eff, idx)
        mod = self.radius_mod()
        return (geo.mul(rmin, mod), geo.mul(rmax, mod))

    # -- script hooks (Spell.cpp:8991-9030) ----------------------------------
    def script_hook(self, lst: str, eff: EffectView, target: int, value: Any) -> Any:
        """Run the executing target hooks bound to (lead effect index, selector).

        Mirrors: Spell.cpp:8991 / 9004 / 9017 (``IsEffectAffected(effIndex)`` with the lead's
        index and ``targetType.GetTarget() == hook.GetTarget()``).  A fixture-stated result or a
        track-E adapter is required; otherwise FailClosed (a hook is never skipped).
        """
        hooks = self.sv.target_hooks(eff.index, target, lst)
        if not hooks:
            return value
        key = f"{lst}:{eff.index}:{target}"
        names = [f"{h['script']}::{h['handler']}" for h in hooks]
        stated = self.w.raw.get("script_results") or {}
        if key in stated:
            entry = stated[key]
            if not isinstance(entry, dict) or "result" not in entry or \
                    ("draws" not in entry and entry.get("rng_free") is not True):
                raise FailClosed(f"fixture: script_results[{key!r}] must be {{'result': ..., 'draws': n}} or "
                                 "{'result': ..., 'rng_free': true}: a hook may consume RNG (hostile review R2-05)")
            consumed = [self.w.draw(f"script hook {key}") for _ in range(int(entry.get("draws", 0)))]
            out = entry["result"]
            self.t.add("script." + lst, "Spell.cpp:8991-9030", output=out, inputs={"hooks": names, "in": value},
                       draws=consumed, evidence="script-consumer",
                       notes=["fixture-stated post-hook result and RNG consumption"])
            return list(out) if isinstance(out, list) else out
        adapter = _owner("adapters", "run_target_hook")
        if adapter is not None:
            return adapter(self.w, self.sv, eff, target, lst, hooks, value, self.t)
        raise FailClosed(f"script target hook(s) {names} run for effect {eff.index} target {target} ({lst}); "
                         f"no adapter and no `script_results[{key!r}]`")

    def area_hook(self, eff: EffectView, target: int):
        """Callable for track C/D stages: ``hook(targets[, hooks]) -> targets``."""
        def run(targets, _hooks=None):
            return self.script_hook("OnObjectAreaTargetSelect", eff, target, list(targets))
        return run

    # -- entry --------------------------------------------------------------
    def run(self) -> Result:
        self.t.add("input", "Spell.cpp:720", inputs={
            "spell": self.sv.id, "source": self.sv.source, "caster": self.caster,
            "explicit": dict(self.w.explicit), "visit_order": self.w.visit_order,
            "rng": (self.w.rng or {}).get("draws")})
        result = OK
        try:
            if self.w.raw.get("precast"):
                self.precast()
            self.select_spell_targets()
        except _Finish as fin:
            if self.finished is None:          # precast: prepare() failed before selection
                self.finished = fin.result
                self.t.add("finish", fin.where, output=fin.result)
        if self.finished is not None:
            result = self.finished
        recipients: dict[int, list[str]] = {}
        for e in self.sv.effects:
            if e.is_effect:
                recipients[e.index] = self.targets.recipients(e.index)
        self.t.add("final", "Spell.cpp:3980 DoProcessTargetContainer (per effect, unique-list order)",
                   output={"recipients": recipients, "cast_result": result})
        for s in self.t.stages:
            if s.defect:
                self.defects.append(s.defect)
        draws = list((self.w.rng or {}).get("draws") or [])[: self.w.draws_consumed]
        return Result(spell=self.sv.id, name=self.sv.name, cast_result=result, recipients=recipients,
                      unique_targets=[{"id": g, "mask": m} for g, m in self.unique],
                      dests={k: list(v) for k, v in self.dests.items()},
                      draws_consumed=self.w.draws_consumed, draws=draws, trace=self.t,
                      channel_mask=self.channel_mask, defects=self.defects)

    # -- optional: InitExplicitTargets + CheckCast before selection ------------
    def precast(self) -> None:
        """``Spell::InitExplicitTargets`` (Spell.cpp:621) and the target-dependent ``CheckCast`` calls of
        ``prepare`` (3492, strict) and ``_cast`` (3756) that precede ``SelectSpellTargets`` (track B stages).

        Opt-in (fixture ``"precast": true``): the fixture's ``explicit`` is then the *client* input,
        not the post-init ``m_targets``.  Mirrors the first half of ``explicit.validate``.
        """
        from .explicit import cast_eligibility, init_explicit_targets
        triggered = set(self.w.spell.get("triggered_flags", ()))
        unit = init_explicit_targets(self.w, self.sv, self.t)
        self.unit_target = unit
        view = self.view()
        prep = cast_eligibility(view, self.sv, unit, True, self.t)
        if "IGNORE_TARGET_CHECK" in triggered and prep == "SPELL_FAILED_BAD_TARGETS":
            prep = OK                                                   # Spell.cpp:3495
        if prep != OK:
            raise _Finish(str(prep), f"{S}:3492 prepare -> CheckCast(true)")
        cast_time = self.sv.need("cast_time_ms")
        if int(cast_time) == 0 or "CAST_DIRECTLY" in triggered:
            self.t.add("precast.cast_complete", f"{S}:3579/3611", output="skipped (_cast skipCheck=true)")
            return
        if self.w.raw.get("at_cast_complete"):
            raise FailClosed("precast: state changes between prepare and cast completion need a separate fixture")
        done = cast_eligibility(view, self.sv, unit, False, self.t)
        if done != OK:
            raise _Finish(str(done), f"{S}:3756 _cast -> CheckCast(false)")

    # -- SelectExplicitTargets (691) -----------------------------------------
    def select_explicit_targets(self) -> None:
        """Mirrors: Spell.cpp:691-717."""
        ex = self.w.explicit
        target = self.unit_target
        if "redirect" in ex:
            redirect = ex["redirect"]
            if redirect is not None:
                self.w.actor(redirect)
            if target is not None and redirect and redirect != target:
                self.unit_target = redirect
            self.t.add("explicit.redirect", f"{S}:694-716", output=self.unit_target,
                       inputs={"stated_redirect": redirect}, evidence="structural-inference",
                       notes=["fixture-stated redirect outcome"])
            return
        from .explicit import select_explicit_targets
        self.unit_target = select_explicit_targets(self.w, self.sv, target, self.t)

    # -- SelectSpellTargets (720) --------------------------------------------
    def select_spell_targets(self) -> None:
        self.select_explicit_targets()
        processed = 0
        for eff in self.sv.effects:
            if not eff.is_effect:                                      # 730
                continue
            sel_a, sel_b = selector_info(eff.target_a), selector_info(eff.target_b)
            raw = self.group_mask(eff)                                 # 741-782
            mask = raw & ~processed                                    # 784
            self.t.add("group", f"{S}:741-784", output=mask,
                       inputs={"effect": eff.index, "group_mask": raw, "processed": processed,
                               "target_a": sel_a.name, "target_b": sel_b.name})
            if mask:
                self.select_effect_implicit_targets(eff, sel_a, "A", mask)   # 787
                self.select_effect_implicit_targets(eff, sel_b, "B", mask)   # 788
                processed |= mask
            self.select_effect_type_implicit_targets(eff)              # 797
            if self.dst is not None:                                   # 799-800 AddDestTarget
                self.dests[eff.index] = self.dst
                self.t.add("add_dest_target", f"{S}:800", output=list(self.dst), inputs={"effect": eff.index})
            objs = (sel_a.object, sel_b.object)
            if mask and ("UNIT" in objs or "UNIT_AND_DEST" in objs) \
                    and self.sv.has_attr("SPELL_ATTR1_REQUIRE_ALL_TARGETS"):   # 802-808
                found = any(m & mask for _, m in self.unique)
                self.t.add("require_all_targets", f"{S}:808-820", output=found, inputs={"mask": mask},
                           notes=["checked per selection turn, right after this turn's selection"])
                if not found:
                    self.stop("SPELL_FAILED_BAD_IMPLICIT_TARGETS", f"{S}:817")
            if self.sv.is_channeled:                                   # 824
                focus = self.cast_fact("focus_object")
                if not focus and not self.unique and self.dst is None:
                    # m_UniqueGOTargetInfo / m_UniqueItemInfo: GO/item recipients fail closed before this point
                    self.stop("SPELL_FAILED_BAD_IMPLICIT_TARGETS", f"{S}:827-832")
                bit = 1 << eff.index
                if any(m & bit for _, m in self.unique):
                    self.channel_mask |= bit
                self.t.add("channel", f"{S}:824-843", output=self.channel_mask)
        if self.sv.has_attr("SPELL_ATTR2_FAIL_ON_ALL_TARGETS_IMMUNE"):  # 846
            results = (self.w.raw.get("hit") or {}).get("results")
            if results is None or any(g not in results for g, _ in self.unique):
                raise FailClosed("fixture: SPELL_ATTR2_FAIL_ON_ALL_TARGETS_IMMUNE reads every unique "
                                 "target's MissCondition; state `hit.results`")
            any_ok = any(results[g] not in ("IMMUNE", "IMMUNE2") for g, _ in self.unique)
            self.t.add("fail_on_all_immune", f"{S}:846-859", output=any_ok)
            if not any_ok:
                self.stop("SPELL_FAILED_IMMUNE", f"{S}:856")
        if self.dst is not None and self.sv.has_attr("SPELL_ATTR8_REQUIRES_LOCATION_TO_BE_ON_LIQUID_SURFACE"):
            if not self.cast_fact("dest_on_liquid"):
                self.stop("SPELL_FAILED_NO_LIQUID", f"{S}:870")

    # -- grouping (741-782) --------------------------------------------------
    def group_mask(self, lead: EffectView) -> int:
        """The lead's group mask before ``&= ~processed``.

        Mirrors: Spell.cpp:741-782.  Same criteria and radius call order as track F's
        ``recipients.selection_plan`` (differentially tested against it), evaluated lazily
        per lead so that ``CalcRadius`` RNG draws interleave with selection as in Trinity.
        """
        from .recipients import script_effect_check
        mask = 1 << lead.index
        sa, sb = selector_info(lead.target_a), selector_info(lead.target_b)
        check_radius = sa.category in RADIUS_CATEGORIES or sb.category in RADIUS_CATEGORIES
        script_check = None
        for other in self.sv.effects[lead.index + 1:]:
            if not (other.is_effect and lead.target_a == other.target_a and lead.target_b == other.target_b
                    and lead.conditions == other.conditions
                    and lead.has_attribute("PlayersOnly") == other.has_attribute("PlayersOnly")):
                continue
            if script_check is None:
                script_check = script_effect_check(self.sv)
            if not script_check(lead.index, other.index):
                continue
            if check_radius:
                before = self.w.draws_consumed
                split = self.radius(lead, "A") != self.radius(other, "A") or \
                    self.radius(lead, "B") != self.radius(other, "B")
                if self.w.draws_consumed != before:
                    self.t.add("group.random_radius", f"{S}:772-775", output=not split,
                               inputs={"lead": lead.index, "other": other.index},
                               draws=list((self.w.rng or {}).get("draws") or [])[before:self.w.draws_consumed],
                               defect="TG-G-D01/TG-F-D2/TG-D-DEF-20",
                               notes=["grouping compared random CalcRadius values and consumed rand_norm"])
                if split:
                    continue
            mask |= 1 << other.index
        return mask

    # -- SelectEffectImplicitTargets (945) -----------------------------------
    def select_effect_implicit_targets(self, eff: EffectView, sel, idx: str, mask: int) -> None:
        """Mirrors: Spell.cpp:945-1027."""
        if not sel.id:
            return
        cat = sel.category
        self.t.add("select", f"{S}:945", inputs={"effect": eff.index, "index": idx, "selector": sel.name,
                                                 "category": cat, "reference": sel.reference,
                                                 "object": sel.object, "check": sel.check, "mask": mask})
        if cat == "CHANNEL":
            self.select_channel(eff, sel, mask)
        elif cat == "NEARBY":
            self.select_nearby(eff, sel, idx, mask)
        elif cat == "CONE":
            self.select_cone(eff, sel, idx, mask)
        elif cat == "AREA":
            self.select_area(eff, sel, idx, mask)
        elif cat == "TRAJ":
            self.check_dst()                                           # 962
            self.select_traj(eff, sel)
        elif cat == "LINE":
            self.select_line(eff, sel, idx, mask)
        elif cat == "DEFAULT":
            if sel.object == "SRC":
                if sel.reference != "CASTER":
                    raise FailClosed(f"{S}:982 ABORT_MSG (SRC with reference {sel.reference})")
                self.src = self.actor_pos(self.caster)                 # m_targets.SetSrc(*m_caster)
                self.t.add("set_src", f"{S}:979", output=list(self.src))
            elif sel.object == "DEST":
                if sel.reference == "CASTER":
                    self.select_caster_dest(eff, sel, idx)
                elif sel.reference == "TARGET":
                    self.select_target_dest(eff, sel, idx)
                elif sel.reference == "DEST":
                    self.select_dest_dest(eff, sel, idx)
                else:
                    raise FailClosed(f"{S}:1000 ABORT_MSG (DEST with reference {sel.reference})")
            else:
                if sel.reference == "CASTER":
                    self.select_caster_object(eff, sel, mask)
                elif sel.reference == "TARGET":
                    self.select_target_object(eff, sel, mask)
                else:
                    raise FailClosed(f"{S}:1014 ABORT_MSG (object with reference {sel.reference})")
        elif cat == "NYI":
            self.t.add("nyi", f"{S}:1020", output=None, notes=["TC_LOG_DEBUG only; no targets"])
        else:
            raise FailClosed(f"{S}:1024 ABORT_MSG (category {cat})")

    # -- channel (1029) ------------------------------------------------------
    def select_channel(self, eff: EffectView, sel, mask: int) -> None:
        """Mirrors: Spell.cpp:1029-1087 (``cast.channel`` = null or
        ``{"objects": [...], "dest": [x,y,z,o] | null}`` of the original caster's channel)."""
        if sel.reference != "CASTER":
            raise FailClosed(f"{S}:1033 ABORT_MSG")
        ch = self.cast_fact("channel")
        if not ch:
            self.t.add("channel.none", f"{S}:1038-1042", output=None, notes=["no current channeled spell"])
            return
        if sel.id == 77:     # TARGET_UNIT_CHANNEL_TARGET
            for obj in ch.get("objects", []):
                target = self.script_hook("OnObjectTargetSelect", eff, sel.id, obj)
                if target is not None and self.is_unit(target):
                    self.add_unit_target(target, mask)
        elif sel.id == 76:   # TARGET_DEST_CHANNEL_TARGET
            if ch.get("dest") is not None:
                self.set_dst(_pos(ch["dest"]), f"{S}:1063")
            elif ch.get("objects"):
                target = self.script_hook("OnObjectTargetSelect", eff, sel.id, ch["objects"][0])
                if target is not None:
                    dest = self.facing(eff, self.actor_pos(target))
                    dest = self.script_hook("OnDestinationTargetSelect", eff, sel.id, dest)
                    self.set_dst(dest, f"{S}:1076")
        else:
            raise FailClosed(f"{S}:1084 ABORT_MSG")

    # -- nearby (1089) -------------------------------------------------------
    def select_nearby(self, eff: EffectView, sel, idx: str, mask: int) -> None:
        """Mirrors: Spell.cpp:1089-1271."""
        if sel.reference != "CASTER":
            raise FailClosed(f"{S}:1093 ABORT_MSG")
        chk = sel.check
        if chk == "ENEMY":
            rng = oracle.max_range(self.w, self.sv, False, self.caster)
        elif chk in ("ALLY", "PARTY", "RAID", "RAID_CLASS"):
            rng = oracle.max_range(self.w, self.sv, True, self.caster)
        elif chk in ("ENTRY", "DEFAULT"):
            rng = oracle.max_range(self.w, self.sv, self.sv.is_positive, self.caster)
        else:
            raise FailClosed(f"{S}:1113 ABORT_MSG (nearby check {chk})")
        if chk == "ENTRY" and eff.conditions is None and sel.object in ("GOBJ", "DEST"):
            raise FailClosed("TARGET_CHECK_ENTRY without conditions: RequiresSpellFocus / spell_target_position "
                             "emergency branches (Spell.cpp:1121-1179) are not modelled")
        target = C.search_nearby(self.view(), self.sv, eff, sel, rng, self.t)
        random_radius = 0.0
        if sel.id == 142 and target is None:   # TARGET_DEST_NEARBY_ENTRY_OR_DB caster fallback
            target = self.caster
            random_radius = self.radius(eff, idx)[1]
        if target is None:
            self.finish("SPELL_FAILED_BAD_IMPLICIT_TARGETS", f"{S}:1199")
            return                                  # leaves SelectImplicitNearbyTargets only
        target = self.script_hook("OnObjectTargetSelect", eff, sel.id, target)
        if target is None:
            self.finish("SPELL_FAILED_BAD_IMPLICIT_TARGETS", f"{S}:1213")
            return
        kind = self.w.actor(target).kind
        if sel.object == "UNIT":
            if kind not in UNIT_KINDS:
                self.finish("SPELL_FAILED_BAD_IMPLICIT_TARGETS", f"{S}:1224")
                return
            self.add_unit_target(target, mask, True, False)
        elif sel.object in ("GOBJ", "CORPSE"):
            raise FailClosed(f"{sel.object} recipients (AddGOTarget/AddCorpseTarget) are outside the unit-recipient oracle")
        elif sel.object == "DEST":
            dest = self.actor_pos(target)
            if random_radius > 0.0:
                self.direction_angle(sel)
                dest = self.stated_dest(eff, sel, idx, "MovePosition (collision)")
            dest = self.facing(eff, dest)
            dest = self.script_hook("OnDestinationTargetSelect", eff, sel.id, dest)
            self.set_dst(dest, f"{S}:1265")
        else:
            raise FailClosed(f"{S}:1267 ABORT_MSG")
        self.select_chain(eff, sel, target, mask)

    # -- cone / area / line / traj -------------------------------------------
    def select_cone(self, eff: EffectView, sel, idx: str, mask: int) -> None:
        """Mirrors: Spell.cpp:1273-1324 (track C ``area.select_cone``; AddUnitTarget(checkIfValid=false))."""
        radius = self.scaled_radius(eff, idx)
        targets = C.select_cone(self.view(), self.sv, eff.index, idx, self.t, radius=radius,
                                script_hook=self.area_hook(eff, sel.id))
        for t in targets:
            self.add_any(t, mask, check_if_valid=False)

    def select_area(self, eff: EffectView, sel, idx: str, mask: int) -> None:
        """Mirrors: Spell.cpp:1326-1463 (track C ``area.select_area``)."""
        unique = self.unique
        radius = self.scaled_radius(eff, idx) if self._area_has_referer(sel) else None
        res = C.select_area(self.view(), self.sv, eff.index, idx, self.t, radius=radius,
                            unique_targets=unique, script_hook=self.area_hook(eff, sel.id))
        if res["referer"] is None:
            return
        if res["dest_mod"] is not None:
            p = res["dest_mod"]["pos"]
            dest = (p[0], p[1], p[2], res["dest_mod"]["orientation"])
            self.mod_dst(dest, f"{S}:1434")
        los = {"SRC": "@src", "DEST": "@dst"}.get(sel.reference, res["referer"])
        for t in res["targets"]:
            self.add_any(t, mask, check_if_valid=False, implicit=True, los=los)

    def _area_has_referer(self, sel) -> bool:
        """``CalcRadius`` runs only after ``if (!referer) return`` (Spell.cpp:1357 vs 1379)."""
        return sel.reference != "TARGET" or self.unit_target is not None

    def select_line(self, eff: EffectView, sel, idx: str, mask: int) -> None:
        """Mirrors: Spell.cpp:1964-2021 (track C ``area.select_line``)."""
        radius = self.scaled_radius(eff, idx)
        targets = C.select_line(self.view(), self.sv, eff.index, idx, self.t, radius=radius,
                                script_hook=self.area_hook(eff, sel.id))
        for t in targets:
            self.add_any(t, mask, check_if_valid=False)

    def select_traj(self, eff: EffectView, sel) -> None:
        """Mirrors: Spell.cpp:1878 (only moves dest; never adds recipients)."""
        if "traj" in self.w.explicit and self.w.explicit["traj"] is None:
            self.t.add("traj.none", f"{S}:1880", output=None, notes=["m_targets has no trajectory: return"])
            return
        raise FailClosed("TARGET_DEST_TRAJ with a trajectory reads collision/creature type flags "
                         "(Spell.cpp:1878-1962); state explicit.traj = null")

    # -- dest selectors ------------------------------------------------------
    def facing(self, eff: EffectView, dest):
        if self.sv.has_attr("SPELL_ATTR4_USE_FACING_FROM_SPELL"):
            return (dest[0], dest[1], dest[2], geo.normalize_orientation(eff.pos_facing))
        return dest

    def stated_dest(self, eff: EffectView, sel, idx: str, reason: str):
        table = self.w.raw.get("dest_positions") or {}
        key = f"{eff.index}:{idx}"
        if key not in table:
            raise FailClosed(f"fixture: {sel.name} dest (effect {eff.index} {idx}) depends on {reason}; "
                             f"state `dest_positions[{key!r}]`")
        self.t.add("dest.stated", str(sel.name), output=table[key], evidence="structural-inference",
                   notes=[f"world-geometry dependent ({reason}); fixture-stated"])
        return _pos(table[key])

    def set_dst(self, dest, where: str) -> None:
        self.dst = dest
        self.t.add("set_dst", where, output=list(dest))

    def mod_dst(self, dest, where: str) -> None:
        if self.dst is None:
            raise FailClosed(f"{where}: ModDst ASSERT(HasDst) would fire (Spell.cpp:373-381)")
        self.dst = dest
        self.t.add("mod_dst", where, output=list(dest))

    def check_dst(self) -> None:
        """Mirrors: Spell.cpp:7268 ``CheckDst``."""
        if self.dst is None:
            self.dst = self.actor_pos(self.caster)
            self.t.add("check_dst", f"{S}:7270", output=list(self.dst), notes=["no dst: SetDst(*m_caster)"])

    def select_caster_dest(self, eff: EffectView, sel, idx: str) -> None:
        """Mirrors: Spell.cpp:1465-1651."""
        dest = self.actor_pos(self.caster)
        tid = sel.id
        if tid == 18:                                  # TARGET_DEST_CASTER
            pass
        elif tid in (9, 17, 39, 62, 125, 131, 106):    # HOME / DB / FISHING / GROUND / GROUND_2 / SUMMONER / NEARBY_DB
            dest = self.stated_dest(eff, sel, idx, "homebind / spell_target_position / map height / summoner")
        elif tid in (55, 137):                         # FRONT_LEAP / MOVEMENT_DIRECTION
            if self.is_unit(self.caster):
                self.radius(eff, idx)
                self.direction_angle(sel)
                dest = self.stated_dest(eff, sel, idx, "MovePosition (collision)")
        else:
            dist = self.radius(eff, idx)[1]
            self.direction_angle(sel)
            obj_size = C.combat_reach(self.w, self.caster)
            defect = None
            if tid == 32:                              # TARGET_DEST_CASTER_SUMMON
                dist = 3.0                             # PET_FOLLOW_DIST (PetDefines.h)
            elif tid == 72 and dist > obj_size:        # TARGET_DEST_CASTER_RANDOM
                dist = geo.add(obj_size, geo.sub(dist, obj_size))
                defect = "TG-A-D2/TG-C-D07"   # Spell.cpp:1618-1619 `objSize + (dist - objSize)` no-op
            elif tid in (41, 42, 43, 44) and eff.radius_entry(idx) is None:
                dist = 3.0                             # DefaultTotemDistance (1627)
            if dist < obj_size:                        # noqa: PLR1730 (mirrors Spell.cpp:1635-1636)
                dist = obj_size
            self.t.add("caster_dest.dist", f"{S}:1608-1637", output=dist, defect=defect)
            dest = self.stated_dest(eff, sel, idx, "MovePosition (collision)")
        dest = self.facing(eff, dest)
        dest = self.script_hook("OnDestinationTargetSelect", eff, tid, dest)
        self.set_dst(dest, f"{S}:1650")

    def select_target_dest(self, eff: EffectView, sel, idx: str) -> None:
        """Mirrors: Spell.cpp:1653-1688."""
        target = self.unit_target or self.object_target
        if target is None:
            if eff.has_attribute("DontFailSpellOnTargetingFailure"):
                return
            raise FailClosed(f"{S}:1655 ASSERT: no explicit object target")
        dest = self.actor_pos(target)
        if sel.id not in (53, 63, 132):                # DEST_TARGET_ENEMY / ANY / ALLY
            self.direction_angle(sel)                  # angle first (1672), then dist (1673)
            dist = self.radius(eff, idx, caster=None)[1]   # CalcRadius(nullptr, ...)
            self._flag_radius_max_without_caster(eff, idx, dist)
            dest = self.stated_dest(eff, sel, idx, "MovePosition (collision)")
        dest = self.facing(eff, dest)
        dest = self.script_hook("OnDestinationTargetSelect", eff, sel.id, dest)
        self.set_dst(dest, f"{S}:1687")

    def _flag_radius_max_without_caster(self, eff: EffectView, idx: str, dist: float) -> None:
        """TG-C-D08: TargetDest offsets use CalcRadius(nullptr) = RadiusMax (Spell.cpp:1673, SpellInfo.cpp:800-805).
        Flag it when the caster-computed Max (Radius + PerLevel*level, spell mods, movement bonus) would differ.
        The comparison is draw-free (random-target scaling removed) and skipped when caster facts are unstated."""
        from dataclasses import replace as _replace
        probe_eff = _replace(eff, target_a=0, target_b=0)
        try:
            with_caster = oracle.calc_radius(self.w, self.sv, probe_eff, idx, self.caster, None)[1]
        except FailClosed as exc:
            self.t.add("target_dest.radius_max", f"{S}:1673", output=dist,
                       notes=[f"caster radius not comparable ({exc})"])
            return
        if with_caster != dist and not (eff.target(idx) in (72, 74, 86)):
            self.t.add("target_dest.radius_max", f"{S}:1673", output=dist, inputs={"with_caster": with_caster},
                       defect="TG-C-D08", notes=["offset uses RadiusMax, not the caster-scaled radius"])

    def select_dest_dest(self, eff: EffectView, sel, idx: str) -> None:
        """Mirrors: Spell.cpp:1690-1740."""
        self.check_dst()
        dest = self.dst
        tid = sel.id
        if tid in (28, 29, 88, 87):                    # DYNOBJ_* / DEST_DEST
            pass
        elif tid == 138:                               # DEST_DEST_GROUND
            dest = self.stated_dest(eff, sel, idx, "GetMapHeight")
        elif tid == 148:                               # DEST_DEST_TARGET_TOWARDS_CASTER
            self.radius(eff, idx)
            dest = self.stated_dest(eff, sel, idx, "MovePosition (collision)")
        else:
            self.direction_angle(sel)                  # angle first (1724), then dist (1725)
            self.radius(eff, idx)
            dest = self.stated_dest(eff, sel, idx, "MovePosition (collision)")
        dest = self.facing(eff, dest)
        dest = self.script_hook("OnDestinationTargetSelect", eff, tid, dest)
        self.mod_dst(dest, f"{S}:1739")

    # -- object selectors ----------------------------------------------------
    def select_caster_object(self, eff: EffectView, sel, mask: int) -> None:
        """Mirrors: Spell.cpp:1742-1805 (target resolution: track F ``groups.caster_object_target``)."""
        tid = sel.id
        if 96 <= tid <= 103 or tid == 124:
            raise FailClosed(f"{sel.name}: vehicle seats / tap list are not modelled")
        if tid in (1, 5, 27, 92, 94, 150):
            from .groups import caster_object_target
            target, check = caster_object_target(self.w, tid, self.caster, self.t)
        else:
            target, check = None, True                 # default: break (1790)
        target = self.script_hook("OnObjectTargetSelect", eff, tid, target)
        if target is not None:
            self.add_any(target, mask, check_if_valid=check)

    def select_target_object(self, eff: EffectView, sel, mask: int) -> None:
        """Mirrors: Spell.cpp:1807-1830."""
        target = self.unit_target or self.object_target
        if target is None and self.item_target is None and not eff.has_attribute("DontFailSpellOnTargetingFailure"):
            raise FailClosed(f"{S}:1809 ASSERT: no explicit object or item target")
        target = self.script_hook("OnObjectTargetSelect", eff, sel.id, target)
        self.t.add("target_object", f"{S}:1812", output=target)
        if target is not None:
            self.add_any(target, mask, check_if_valid=True, implicit=False)
            self.select_chain(eff, sel, target, mask)  # outside the add: chains even if the add was rejected
        elif self.item_target is not None:
            raise FailClosed("item recipients (AddItemTarget) are outside the unit-recipient oracle")

    def add_any(self, target: str, mask: int, check_if_valid: bool = True, implicit: bool = True, los=None) -> None:
        kind = self.w.actor(target).kind
        if kind in UNIT_KINDS:
            self.add_unit_target(target, mask, check_if_valid, implicit, los)
        elif kind in ("gameobject", "corpse"):
            raise FailClosed(f"{kind} recipient {target!r}: AddGOTarget/AddCorpseTarget are outside the "
                             "unit-recipient oracle")
        else:
            self.t.add("add.ignored", f"{S}:1316", output=target, notes=[f"{kind} is neither unit, GO nor corpse"])

    # -- chain (1832) --------------------------------------------------------
    def select_chain(self, eff: EffectView, sel, target: str, mask: int) -> None:
        """Mirrors: Spell.cpp:1832-1866 (track D ``chain.select_implicit_chain_targets``)."""
        from .chain import select_implicit_chain_targets
        hook = self.area_hook(eff, sel.id) if self.sv.target_hooks(eff.index, sel.id, "OnObjectAreaTargetSelect") else None
        others = [e for e in self.sv.effects if e.index != eff.index and mask & (1 << e.index)
                  and e.chain_targets != eff.chain_targets and max(e.chain_targets, eff.chain_targets) > 1]
        self.t.add("chain.call", f"{S}:1270/1825", inputs={"effect": eff.index, "selector": sel.id, "initial": target,
                                                          "mask": mask},
                   defect="TG-F-D1" if others else None,
                   notes=[f"grouped effect {e.index} (ChainTargets {e.chain_targets}) uses the lead's "
                          f"ChainTargets {eff.chain_targets} (Spell.cpp:741-775, 1834)" for e in others])
        res = select_implicit_chain_targets(self.view(), self.sv, eff, sel, target, mask, self.t, area_hook=hook)
        for unit, los in res.add_calls:
            self.add_unit_target(unit, mask, False, True, los)

    # -- effect-type implicit targets (2023) --------------------------------
    def select_effect_type_implicit_targets(self, eff: EffectView) -> None:
        """Mirrors: Spell.cpp:2023-2121 (runs per effect, not per group)."""
        if eff.effect in (EFFECT_SUMMON_PLAYER, EFFECT_SUMMON_RAF_FRIEND):
            raise FailClosed("SUMMON_PLAYER / SUMMON_RAF_FRIEND far-callback path (Spell.cpp:2026-2059) not modelled")
        itt = effect_implicit_target_type(eff.effect)
        if not itt:
            return
        from .composition import missing_target_mask
        mask = missing_target_mask(eff)                              # SpellInfo.cpp:835 (defaults)
        if not mask:
            self.t.add("effect_type", f"{S}:2068-2071", output=None, inputs={"effect": eff.index, "missing_mask": 0})
            return
        target = None
        if itt == 1:      # EFFECT_IMPLICIT_TARGET_EXPLICIT
            if mask & (TARGET_FLAG_UNIT_MASK | TARGET_FLAG_CORPSE_MASK):
                if self.unit_target is not None:
                    target = self.unit_target
                elif mask & TARGET_FLAG_CORPSE_MASK:
                    obj = self.object_target
                    if obj is not None and self.w.actor(obj).kind == "corpse":
                        target = obj
                else:
                    target = self.caster
            if mask & TARGET_FLAG_ITEM_MASK:
                if self.item_target is not None:
                    raise FailClosed("item recipients (AddItemTarget) are outside the unit-recipient oracle")
                self.t.add("effect_type.item", f"{S}:2092-2097", output=None, inputs={"effect": eff.index})
                return
            if mask & TARGET_FLAG_GAMEOBJECT_MASK:
                obj = self.object_target
                target = obj if obj is not None and self.w.actor(obj).kind == "gameobject" else None
        elif itt == 2:    # EFFECT_IMPLICIT_TARGET_CASTER
            if mask & TARGET_FLAG_UNIT_MASK:
                target = self.caster
        # 2112: CallScriptObjectTargetSelectHandlers(target, idx, SpellImplicitTargetInfo()) -> target 0,
        # never matches a registered hook (TargetHook::CheckEffect returns false for 0).
        self.t.add("effect_type", f"{S}:2073-2120", output=target,
                   inputs={"effect": eff.index, "implicit_type": itt, "missing_mask": mask})
        if target is not None:
            self.add_any(target, 1 << eff.index, check_if_valid=False)

    # -- AddUnitTarget (2443) + CheckEffectTarget (8174) ----------------------
    def check_effect_target(self, target: str, eff: EffectView, los) -> tuple[bool, str]:
        """Mirrors: Spell.cpp:8174-8260 ``CheckEffectTarget(Unit const*, ...)``."""
        stated = self.w.raw.get("effect_target_checks") or {}
        key = f"{target}:{eff.index}"
        if key in stated:
            return bool(stated[key]), "fixture-stated"
        if eff.aura in CHARM_AURAS:                    # switch on ApplyAuraName (8176)
            raise FailClosed(f"CheckEffectTarget charm branch (8178-8191: vehicle/mount/charmer/level vs "
                             f"CalculateDamage) for {key}; state `effect_target_checks`")
        if self.sv.has_attr("SPELL_ATTR2_IGNORE_LINE_OF_SIGHT") or self.sv.need("los_disabled"):
            return True, "8194: ignore LOS (attribute / DisableMgr)"
        if self.w.actor(self.caster).kind == "gameobject":
            raise FailClosed("8198: gameobject caster GetRequireLOS not modelled")
        trig = self.spell_fact("triggered_by_aura")
        if trig is not None and not self.sv.has_attr("SPELL_ATTR5_ALWAYS_LINE_OF_SIGHT") and trig["ignores_los"]:
            return True, "8203: triggering aura ignores LOS"
        if eff.effect == EFFECT_SKIN_PLAYER_CORPSE:
            raise FailClosed("8212: SKIN_PLAYER_CORPSE LOS branch not modelled; state `effect_target_checks`")
        if los is None or self.sv.has_attr("SPELL_ATTR5_ALWAYS_AOE_LINE_OF_SIGHT") \
                or eff.has_attribute("AlwaysAoeLineOfSight"):
            if self.original_caster_is_go():
                raise FailClosed("8240: gameobject original caster LOS source not modelled")
            if target != self.caster and not self.spell_los(self.caster, target):
                return False, f"8249: no LOS caster->{target}"
        if los is not None:
            if not self.w.in_los(target, los):          # Spell::IsWithinLOS(source, Position) 9272
                return False, f"8253: no LOS {target}->{los}"
        return True, "ok"

    def spell_los(self, source: str, target: str) -> bool:
        """Mirrors: Spell.cpp:9256 ``Spell::IsWithinLOS(WorldObject, WorldObject, true, M2)``
        (attribute / DisableMgr handled by the caller; CanIgnoreLineOfSightWhenCastingOnMe here)."""
        t = self.w.actor(target)
        if t.kind in ("creature", "pet", "guardian", "totem", "minion", "vehicle"):
            flag = t.facts.get("ignore_los_on_me")
            if flag is None:
                raise FailClosed(f"fixture: creature {target!r} must state `ignore_los_on_me` "
                                 "(Creature::CanIgnoreLineOfSightWhenCastingOnMe)")
            if flag:
                return True
        return self.w.in_los(target, source)            # targetAsSourceLocation = true

    def original_caster_is_go(self) -> bool:
        """``m_originalCasterGUID.IsGameObject()`` (Spell.cpp:8241): fixture ``original_caster`` (absent = caster)."""
        oc = self.w.original_caster or self.caster
        if oc not in self.w.actors:
            raise FailClosed(f"fixture: original caster {oc!r} is not an actor")
        return self.w.actor(oc).kind == "gameobject"

    def immune(self, target: str, eff: EffectView) -> bool:
        """``Unit::IsImmunedToSpellEffect`` -- actor fact ``immune_effects`` (track B's name) or the world
        ``immunity`` block ({id | "default": [effect indices]})."""
        a = self.w.actor(target)
        if "immune_effects" in a.facts:
            return eff.index in a.facts["immune_effects"]
        table = self.w.raw.get("immunity")
        if table is None or (target not in table and "default" not in table):
            raise FailClosed(f"fixture: IsImmunedToSpellEffect for {target!r} not stated "
                             "(actor fact `immune_effects` or `immunity`)")
        return eff.index in table.get(target, table.get("default", []))

    def check_target(self, target: str, implicit: bool) -> str:
        from .explicit import check_target
        return check_target(self.w, self.sv, self.caster, target, implicit, self.t)

    def _effect_target_ok(self, target: str, k: int, los) -> bool:
        ok, why = self.check_effect_target(target, self.sv.effects[k], los)
        if not ok:
            self._notes.append(f"CheckEffectTarget e{k}: {why}")
        return ok

    def add_unit_target(self, target: str, mask: int, check_if_valid: bool = True, implicit: bool = True,
                        los=None) -> None:
        """Mirrors: Spell.cpp:2443-2557 ``AddUnitTarget`` (list/merge/immunity: track F ``UniqueTargets.add``;
        CheckEffectTarget, CheckTarget, immunity facts and the SpellHitResult draw position: here)."""
        self._notes = []
        before = self.w.draws_consumed
        outcome = self.targets.add(target, mask, check_if_valid, implicit, los)
        consumed: list[Any] = []
        defect = None
        if outcome == "added" and dict(self.unique).get(target) == 0:
            defect = "TG-F-D3"      # fully immune target inserted with EffectMask 0 (Spell.cpp:2450 vs 2457-2459)
        if outcome == "added":
            if self.first_add_draw is None:
                self.first_add_draw = before
            hit = self.w.raw.get("hit") or {}
            draws = hit.get("draws")
            if draws is not None:                                      # 2486 SpellHitResult (new targets only)
                n = draws.get(target, draws.get("default")) if isinstance(draws, dict) else draws
                if n is None:
                    raise FailClosed(f"fixture: `hit.draws` does not state {target!r}")
                self._in_hit = True
                try:
                    consumed = [self.w.draw(f"SpellHitResult {target}") for _ in range(int(n))]
                finally:
                    self._in_hit = False
        entry = dict(self.unique).get(target)
        self.t.add("add_unit_target", f"{S}:2443-2556", output={"outcome": outcome, "mask": entry},
                   inputs={"target": target, "mask": mask, "check_if_valid": check_if_valid,
                           "implicit": implicit, "los": los},
                   draws=consumed, notes=list(self._notes), defect=defect)


def evaluate(world: World, sv: SpellView | None = None) -> Result:
    """Run the pipeline on a fixture world; raises FailClosed on any unstated fact."""
    return Evaluation(world, sv).run()


def evaluate_file(path) -> Result:
    return evaluate(World.load(path))


__all__ = ["Evaluation", "Result", "evaluate", "evaluate_file"]
