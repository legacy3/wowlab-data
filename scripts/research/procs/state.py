"""Minimal proc timing/state reference.  Not a combat simulator.

It tracks only source-proven per-``Aura`` state and replays a synthetic,
ordered stream of events with **predetermined** ``rand_chance()`` values.

Mirrors:
* ``Aura::Aura`` -- initial ``m_procCharges`` (``CalcMaxCharges``),
  ``m_procCooldown = TimePoint::min()``, ``m_lastProcAttemptTime = now - 10s``,
  ``m_lastProcSuccessTime = now - 120s``;
* ``Aura::ModStackAmount`` refresh branch -- ``SetCharges(CalcMaxCharges())``;
* ``Unit::GetProcAurasTriggeredOnEvent`` -- per-aura: pre-RNG checks, roll,
  ``SetLastProcAttemptTime``, ``PrepareProcToTrigger`` on success, and the
  ``SPELL_ATTR0_PROC_FAILURE_BURNS_CHARGE`` / ``SPELL_ATTR2_PROC_COOLDOWN_ON_FAILURE``
  failure branches;
* ``Unit::TriggerAurasProcOnEvent`` -- *all* actor-side auras are collected
  (and rolled) before any target-side aura, and every roll happens before any
  trigger executes; then actor triggers, then target triggers;
* ``Aura::TriggerProcOnEvent`` / ``AuraEffect::HandleProc`` -- which effects
  run and what they would cast (identity only), then ``ConsumeProcCharges``;
* ``Unit::GetAppliedAuras`` iteration order -- ``std::multimap<uint32, ...>``
  keyed by SpellID: ascending SpellID, insertion order within a SpellID.

Damage, healing, aura application by spells, spell casting and event
generation are **external**: triggered spells are reported, never executed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from . import SourceError, UnsupportedSource
from .chance import (
    ChanceResult,
    RppmInputs,
    aura_proc_chance,
    roll_succeeds,
    rppm_chance,
    rppm_rate,
)
from .definition import ProcDefinition
from .eligibility import ActorFacts, Evaluator, HolderState
from .enums import (
    ATTR0_PROC_FAILURE_BURNS_CHARGE,
    ATTR2_PROC_COOLDOWN_ON_FAILURE,
    ATTR6_DO_NOT_CONSUME_RESOURCES,
    ATTR8_TARGET_PROCS_ON_CASTER,
    PROC_ATTR_REDUCE_PROC_60,
    PROC_ATTR_USE_STACKS_FOR_CHARGES,
    SPELL_AURA_PROC_TRIGGER_DAMAGE,
    TAKEN_HIT_PROC_FLAG_MASK,
    TRIGGER_SPELL_HANDLERS,
)
from .events import ProcEvent

ATTEMPT_OFFSET_MS = 10_000
SUCCESS_OFFSET_MS = 120_000
TIMEPOINT_MIN = float("-inf")


@dataclass
class AuraState:
    """The mutable proc members of one ``Aura`` (shared by all its applications)."""

    charges: int
    using_charges: bool
    stack_amount: int
    proc_cooldown_until: float
    last_attempt_ms: float
    last_success_ms: float
    removed: bool = False

    def snapshot(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Provider:
    """One aura instance on one holder."""

    definition: ProcDefinition
    holder: str                 # "actor" or "target" for the events in the stream
    sequence: int = 0           # multimap insertion order among equal SpellIDs
    has_caster: bool = True     # Aura::GetCaster() != nullptr
    rppm_inputs: RppmInputs = field(default_factory=RppmInputs)
    weapon_speed_ms: dict[str, int] = field(default_factory=dict)  # attack type -> base attack time
    random_property_points: Callable[[int], float] | None = None
    max_charges_override: int | None = None      # SpellModOp::ProcCharges result
    cooldown_override_ms: int | None = None      # SpellModOp::ProcCooldown result
    prepare_proc_result: bool = True             # AuraScript DoPrepareProc (if any)
    state: AuraState | None = None

    @property
    def spell_id(self) -> int:
        return self.definition.spell_id

    def max_charges(self) -> int:
        """``Aura::CalcMaxCharges`` -> ``uint8``."""
        entry = self.definition.entry
        raw = self.definition.info.proc_charges if entry is None else entry.charges
        if self.max_charges_override is not None:
            raw = self.max_charges_override
        return int(raw) & 0xFF

    def cooldown_ms(self) -> int:
        entry = self.definition.entry
        base = entry.cooldown_ms if entry else 0
        return int(self.cooldown_override_ms if self.cooldown_override_ms is not None else base)


@dataclass(frozen=True)
class StreamItem:
    time_ms: int
    kind: str                                   # "event" | "apply" | "refresh" | "remove"
    event: ProcEvent | None = None
    rolls: Sequence[float] = ()
    facts: ActorFacts = field(default_factory=ActorFacts)
    spell_id: int | None = None                 # for apply/refresh/remove
    stack_amount: int = 1


@dataclass
class ProviderStep:
    spell_id: int
    holder: str
    reached: bool
    verdict: str
    reason: str
    chance: ChanceResult | None
    roll: float | None
    success: bool | None
    state_before: dict[str, Any]
    state_after: dict[str, Any]
    failure_side_effects: list[str]
    triggered: list[dict[str, Any]]
    checks: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["chance"] = self.chance.to_dict() if self.chance else None
        return d


def iteration_order(providers: Sequence[Provider]) -> list[Provider]:
    """``TriggerAurasProcOnEvent`` collection order: actor side, then target side;
    within a side ``std::multimap`` order (SpellID, then insertion)."""
    side = {"actor": 0, "target": 1}
    return sorted(providers, key=lambda p: (side[p.holder], p.spell_id, p.sequence))


class Timeline:
    def __init__(self, evaluator: Evaluator, providers: Sequence[Provider]) -> None:
        self.evaluator = evaluator
        self.providers = list(providers)
        self.log: list[dict[str, Any]] = []

    # -- lifecycle ---------------------------------------------------------
    def _find(self, spell_id: int) -> Provider:
        for p in self.providers:
            if p.spell_id == spell_id:
                return p
        raise SourceError(f"no provider {spell_id} in this timeline")

    def apply(self, spell_id: int, now: int, stack_amount: int = 1) -> None:
        p = self._find(spell_id)
        charges = p.max_charges()
        p.state = AuraState(charges=charges, using_charges=charges != 0,
                            stack_amount=stack_amount, proc_cooldown_until=TIMEPOINT_MIN,
                            last_attempt_ms=now - ATTEMPT_OFFSET_MS,
                            last_success_ms=now - SUCCESS_OFFSET_MS)

    def refresh(self, spell_id: int) -> None:
        """``ModStackAmount`` refresh branch: charges reset, proc timers untouched."""
        p = self._find(spell_id)
        if p.state is None or p.state.removed:
            raise SourceError("refresh of an absent aura (would be a new Aura)")
        charges = p.max_charges()
        p.state.charges = charges
        p.state.using_charges = charges != 0

    def remove(self, spell_id: int) -> None:
        p = self._find(spell_id)
        if p.state is not None:
            p.state.removed = True

    # -- replay ------------------------------------------------------------
    def run(self, stream: Sequence[StreamItem]) -> list[dict[str, Any]]:
        last_time = None
        for item in stream:
            if last_time is not None and item.time_ms < last_time:
                raise SourceError("stream must be ordered by time")
            last_time = item.time_ms
            if item.kind == "apply":
                self.apply(item.spell_id, item.time_ms, item.stack_amount)
                self.log.append({"time_ms": item.time_ms, "kind": "apply", "spell_id": item.spell_id,
                                 "state": self._find(item.spell_id).state.snapshot()})
            elif item.kind == "refresh":
                self.refresh(item.spell_id)
                self.log.append({"time_ms": item.time_ms, "kind": "refresh", "spell_id": item.spell_id,
                                 "state": self._find(item.spell_id).state.snapshot()})
            elif item.kind == "remove":
                self.remove(item.spell_id)
                self.log.append({"time_ms": item.time_ms, "kind": "remove", "spell_id": item.spell_id})
            elif item.kind == "event":
                self.log.append(self._event(item))
            else:
                raise SourceError(f"unknown stream item kind {item.kind!r}")
        return self.log

    def _event(self, item: StreamItem) -> dict[str, Any]:
        ev = item.event
        now = item.time_ms
        rolls = list(item.rolls)
        consumed: list[float] = []
        steps: list[ProviderStep] = []
        collected: dict[str, list[tuple[Provider, int, ProviderStep]]] = {"actor": [], "target": []}

        # ProcSkillsAndAuras chain limit stops everything before any aura is visited.
        chain_blocked = ev.has_proc_spell and ev.proc_chain_length >= 10

        for p in iteration_order(self.providers):
            st = p.state
            if st is None or st.removed:
                continue
            before = st.snapshot()
            side_mask = ev.actor_mask if p.holder == "actor" else ev.target_mask
            reached = (not chain_blocked and bool(side_mask)
                       and (p.holder == "actor" or ev.has_action_target))
            if not reached:
                steps.append(ProviderStep(p.spell_id, p.holder, False, "not-reached",
                                          "chain limit" if chain_blocked else "side mask empty / no action target",
                                          None, None, None, before, st.snapshot(), [], [], []))
                continue
            hstate = HolderState(charges=st.charges, using_charges=st.using_charges,
                                 proc_cooldown_until_ms=st.proc_cooldown_until, now_ms=now)
            elig = self.evaluator.evaluate(p.definition, ev, p.holder, hstate, item.facts)
            if elig.verdict == "unknown":
                raise UnsupportedSource(f"provider {p.spell_id} at {now} ms: {elig.reason}")

            chance = None
            roll = None
            success = False
            mask = 0
            if elig.verdict == "eligible":
                chance = self._chance(p, ev, item.facts, now)
                if not rolls:
                    raise SourceError(f"event at {now} ms needs another roll for provider {p.spell_id}")
                roll = rolls.pop(0)
                consumed.append(roll)
                success = roll_succeeds(chance.chance_percent, roll)
                st.last_attempt_ms = now          # SetLastProcAttemptTime(now), success or not
                mask = elig.effect_mask if success else 0

            side_effects: list[str] = []
            event_info = self.evaluator.catalog.get(ev.spell_id) if ev.spell_id else None
            no_charge = bool(event_info and event_info.has_attr(ATTR6_DO_NOT_CONSUME_RESOURCES))
            entry = p.definition.entry
            info = p.definition.info
            if mask:
                if p.prepare_proc_result:
                    self._prepare_charge_drop(p, no_charge, side_effects)
                    st.proc_cooldown_until = now + p.cooldown_ms()
                    side_effects.append(f"proc cooldown until {st.proc_cooldown_until}")
                    st.last_success_ms = now
                else:
                    side_effects.append("DoPrepareProc vetoed: no charge drop, cooldown or success time")
                step = ProviderStep(p.spell_id, p.holder, True, elig.verdict, elig.reason, chance, roll,
                                    True, before, {}, side_effects, [], [c.to_dict() for c in elig.checks])
                collected[p.holder].append((p, mask, step))
            else:
                if entry is not None and info.has_attr(ATTR0_PROC_FAILURE_BURNS_CHARGE):
                    self._prepare_charge_drop(p, no_charge, side_effects)
                    step = ProviderStep(p.spell_id, p.holder, True, elig.verdict, elig.reason, chance,
                                        roll, success if roll is not None else None, before, {},
                                        side_effects, [], [c.to_dict() for c in elig.checks])
                    collected[p.holder].append((p, 0, step))
                else:
                    step = ProviderStep(p.spell_id, p.holder, True, elig.verdict, elig.reason, chance,
                                        roll, success if roll is not None else None, before, {},
                                        side_effects, [], [c.to_dict() for c in elig.checks])
                if entry is not None and info.has_attr(ATTR2_PROC_COOLDOWN_ON_FAILURE):
                    st.proc_cooldown_until = now + p.cooldown_ms()
                    side_effects.append(f"cooldown-on-failure until {st.proc_cooldown_until}")
            steps.append(step)

        # trigger phase: actor list, then target list
        for side in ("actor", "target"):
            for p, mask, step in collected[side]:
                st = p.state
                if st.removed:
                    step.failure_side_effects.append("aura removed before trigger: skipped")
                    continue
                if mask:
                    step.triggered = self._actions(p, mask, ev)
                self._consume(p, step)

        for step in steps:
            p = next(x for x in self.providers if x.spell_id == step.spell_id and x.holder == step.holder)
            step.state_after = p.state.snapshot()
        if rolls:
            raise SourceError(f"event at {now} ms left {len(rolls)} unused roll(s): {rolls}")
        return {"time_ms": now, "kind": "event", "label": ev.label, "rolls_consumed": consumed,
                "steps": [s.to_dict() for s in steps]}

    # -- helpers -----------------------------------------------------------
    def _prepare_charge_drop(self, p: Provider, event_no_consume: bool, notes: list[str]) -> None:
        """``Aura::PrepareProcChargeDrop``."""
        entry = p.definition.entry
        st = p.state
        if (not (entry.attributes_mask & PROC_ATTR_USE_STACKS_FOR_CHARGES)
                and st.using_charges and not event_no_consume):
            st.charges = (st.charges - 1) & 0xFF
            notes.append(f"charge dropped -> {st.charges}")

    def _consume(self, p: Provider, step: ProviderStep) -> None:
        """``Aura::ConsumeProcCharges``."""
        entry = p.definition.entry
        st = p.state
        if entry.attributes_mask & PROC_ATTR_USE_STACKS_FOR_CHARGES:
            st.stack_amount -= 1
            step.failure_side_effects.append(f"stack consumed -> {st.stack_amount}")
            if st.stack_amount <= 0:
                st.removed = True
                step.failure_side_effects.append("aura removed (no stacks)")
        elif st.using_charges and st.charges == 0:
            st.removed = True
            step.failure_side_effects.append("aura removed (no charges)")

    def _chance(self, p: Provider, ev: ProcEvent, facts: ActorFacts, now: int) -> ChanceResult:
        d = p.definition
        entry = d.entry
        rppm = None
        if p.has_caster and d.info.base_ppm > 0.0:
            rate = rppm_rate(d.info.base_ppm, d.info.ppm_mods, p.rppm_inputs, p.random_property_points)
            since_attempt = (now - p.state.last_attempt_ms) / 1000.0
            since_proc = (now - p.state.last_success_ms) / 1000.0
            rppm = rppm_chance(rate.chance_percent, since_attempt, since_proc)
            rppm.stages = rate.stages + rppm.stages
        weapon = p.weapon_speed_ms.get(ev.attack_type)
        return aura_proc_chance(
            entry.chance, entry.procs_per_minute, d.info.base_ppm,
            has_caster=p.has_caster, has_damage_info=ev.has_damage_info,
            weapon_speed_ms=weapon, rppm=rppm,
            reduce_60=bool(entry.attributes_mask & PROC_ATTR_REDUCE_PROC_60),
            actor_level=facts.actor_level)

    def _actions(self, p: Provider, mask: int, ev: ProcEvent) -> list[dict[str, Any]]:
        """``AuraEffect::HandleProc`` identities for the effects in ``mask``."""
        d = p.definition
        hooks = (d.scripts or {}).get("proc_hooks", [])
        out = []
        for role in d.effects:
            if not mask & (1 << role.index):
                continue
            # HandleProc*: triggerCaster = aura target (holder);
            # triggerTarget = the other party; TARGET_PROCS_ON_CASTER forces the actor on taken events.
            other = "action_target" if p.holder == "actor" else "actor"
            side_mask = ev.actor_mask if p.holder == "actor" else ev.target_mask
            if d.info.has_attr(ATTR8_TARGET_PROCS_ON_CASTER) and side_mask & TAKEN_HIT_PROC_FLAG_MASK:
                other = "actor"
            action: dict[str, Any] = {"effect": role.index, "aura": role.aura_name,
                                      "handler": role.handler, "caster": f"holder ({p.holder})"}
            if role.aura in TRIGGER_SPELL_HANDLERS:
                if role.trigger_spell and role.trigger_spell_exists:
                    action.update({"cast_spell": role.trigger_spell, "explicit_target": other,
                                   "trigger_flags": "TRIGGERED_FULL_MASK & ~(IGNORE_POWER_COST | IGNORE_REAGENT_COST)",
                                   "triggering_aura": d.spell_id,
                                   "triggering_spell": ev.spell_id if ev.has_proc_spell else None})
                else:
                    action["cast_spell"] = None
            elif role.aura == SPELL_AURA_PROC_TRIGGER_DAMAGE:
                action.update({"direct_damage_from": d.spell_id, "victim": other})
            if hooks:
                action["script_hooks_may_override"] = hooks
            out.append(action)
        return out


def replace_state(provider: Provider, **changes: Any) -> Provider:
    return replace(provider, state=replace(provider.state, **changes))
