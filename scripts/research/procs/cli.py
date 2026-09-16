"""Command line interface for the proc archaeology oracle.

Every command prints JSON (the oracle has no table renderer); ``--output``
writes to a file.  Nothing here draws randomness: ``timeline`` needs the roll
values in its input file.
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import cached_property
from pathlib import Path
from typing import Any

from . import SourceError
from .census import Census
from .chance import RppmInputs, classic_ppm_chance, rppm_chance, rppm_rate
from .definition import ProcDefinitions
from .eligibility import ActorFacts, Evaluator, HolderState
from .enums import (
    HIT_NAMES,
    PROC_ATTR_NAMES,
    PROC_ATTRIBUTE_CONSUMERS,
    PROC_FLAG_NAMES,
    SPELL_PHASE_NAMES,
    SPELL_TYPE_NAMES,
    UNCONSUMED_PROC_NAMED_ATTRIBUTES,
    attr_name,
)
from .events import (
    EVENT_PRODUCERS,
    UNPRODUCED_FLAGS,
    melee_swing,
    periodic_damage,
    periodic_heal,
    simple_event,
    spell_cast,
    spell_finish,
    spell_hit,
)
from .providers import OriginIndex
from .source import Source
from .spells import SpellCatalog
from .state import Provider, StreamItem, Timeline
from .trinity import TrinityOverlay
from .vocabulary import SPEC, WORLD_DB_SPEC
from .vocabulary import census as vocabulary_census


class Context:
    def __init__(self, tables: str | None, overlay: str | None, no_overlay: bool) -> None:
        self.source = Source(tables) if tables else Source()
        self.overlay = None if no_overlay else (TrinityOverlay(overlay) if overlay else TrinityOverlay())
        self.catalog = SpellCatalog(self.source, self.overlay.custom_attributes if self.overlay else None)
        self.defs = ProcDefinitions(self.catalog, self.overlay)
        self.evaluator = Evaluator(self.catalog)

    @cached_property
    def origins(self) -> OriginIndex:
        return OriginIndex(self.source, self.defs)


def _emit(payload: Any, args: argparse.Namespace) -> None:
    text = json.dumps(payload, indent=2, default=str, sort_keys=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(text)


def _definition(ctx: Context, spell_id: int, difficulty: int) -> dict[str, Any]:
    d = ctx.defs.get(spell_id, difficulty)
    if d is None:
        raise SourceError(f"no SpellInfo for {spell_id} at difficulty {difficulty}")
    out = d.to_dict()
    out["origins"] = ctx.origins.origins(spell_id).to_dict()
    out["lifecycle"] = ctx.origins.lifecycle(spell_id)
    out["mutable_state"] = mutable_state(d)
    return out


def mutable_state(d) -> list[str]:
    """What per-Aura state this provider actually exercises (source-derived)."""
    if d.entry is None:
        return []
    e = d.entry
    info = d.info
    state = []
    if e.cooldown_ms > 0 or info.has_attr((2, 0x00800000)):
        state.append("Aura::m_procCooldown (per Aura, shared across applications)")
    if e.charges:
        state.append("Aura::m_procCharges (reset to max on refresh)")
    if e.attributes_mask & 0x10:
        state.append("Aura::m_stackAmount (USE_STACKS_FOR_CHARGES)")
    if info.base_ppm > 0:
        state.append("Aura::m_lastProcAttemptTime / m_lastProcSuccessTime (RPPM)")
    if not state:
        state.append("none beyond aura existence (stateless roll per eligible event)")
    return state


def cmd_provider(ctx: Context, args) -> Any:
    return _definition(ctx, args.spell_id, args.difficulty)


def cmd_effect(ctx: Context, args) -> Any:
    spell, _, index = args.target.partition(":")
    d = ctx.defs.get(int(spell), args.difficulty)
    if d is None:
        raise SourceError(f"no spell {spell}")
    role = next((r for r in d.effects if r.index == int(index)), None)
    if role is None:
        raise SourceError(f"spell {spell} has no effect {index}")
    trig = ctx.defs.get(role.trigger_spell) if role.trigger_spell else None
    return {"provider": int(spell), "effect": role.to_dict(),
            "entry_disables_effect": role.disabled,
            "triggered": trig.to_dict() if trig else None}


def cmd_graph(ctx: Context, args) -> Any:
    census = Census(ctx.defs, ctx.origins)
    return census.graph.subgraph([args.spell_id], max_depth=args.depth)


def cmd_census(ctx: Context, args) -> Any:
    report = Census(ctx.defs, ctx.origins).report()
    if not args.with_ids:
        report["player"].pop("provider_ids", None)
    return report


def cmd_item(ctx: Context, args) -> Any:
    return ctx.origins.describe_item(args.item_id)


def cmd_enchant(ctx: Context, args) -> Any:
    return ctx.origins.describe_enchant(args.enchant_id)


def cmd_vocabulary(ctx: Context, args) -> Any:
    return {
        "proc_flags": {f"0x{k:016X}": v for k, v in PROC_FLAG_NAMES.items()},
        "spell_type": SPELL_TYPE_NAMES, "spell_phase": SPELL_PHASE_NAMES,
        "hit": HIT_NAMES, "proc_attributes": PROC_ATTR_NAMES,
        "spell_attributes": [
            {"attribute": attr_name(k), "side": v[0], "role": v[1], "consumer": v[2]}
            for k, v in PROC_ATTRIBUTE_CONSUMERS.items()],
        "unconsumed_attributes": {attr_name(k): v for k, v in UNCONSUMED_PROC_NAMED_ATTRIBUTES.items()},
        "event_producers": EVENT_PRODUCERS,
        "unproduced_flags": UNPRODUCED_FLAGS,
        "fields": vocabulary_census(ctx.source, ctx.catalog) if args.census else [s.__dict__ for s in SPEC],
        "world_db_fields": WORLD_DB_SPEC,
    }


def cmd_sources(ctx: Context, args) -> Any:
    Census(ctx.defs, ctx.origins)
    vocabulary_census(ctx.source, ctx.catalog)
    return {"tables": ctx.source.ledger.report(with_hashes=not args.no_hash),
            "overlay": ctx.overlay.path.name if ctx.overlay else None,
            "overlay_provenance": ctx.overlay.provenance if ctx.overlay else None}


EVENT_KINDS = ("melee", "spell-hit", "spell-cast", "spell-finish", "periodic-damage",
               "periodic-heal", "heartbeat", "enter_combat", "encounter_start", "kill",
               "target_dies", "death", "jump", "looted", "dispel", "knockback")


def build_event(ctx: Context, spec: dict[str, Any]):
    kind = spec["kind"]
    facts = {k: v for k, v in spec.items() if k.startswith("spell_") or k == "proc_chain_length"}
    if "spell_applied_mod_auras" in facts:
        facts["spell_applied_mod_auras"] = frozenset(facts["spell_applied_mod_auras"])
    if kind == "melee":
        return melee_swing(spec.get("hand", "base"), spec.get("outcome", "normal"),
                           damage=spec.get("damage", 1))
    info = ctx.catalog.require(spec["spell"]) if "spell" in spec else None
    if kind == "spell-hit":
        return spell_hit(info, damage=spec.get("damage", 0), healing=spec.get("healing", 0),
                         all_hit_effects_positive=spec.get("positive"), miss=spec.get("miss", "none"),
                         crit=spec.get("crit", False), attack_type=spec.get("hand", "base"), **facts)
    if kind == "spell-cast":
        return spell_cast(info, positive=spec["positive"], **facts)
    if kind == "spell-finish":
        return spell_finish(info, positive=spec["positive"], spell_type_mask=spec.get("spell_type_mask"),
                            hit_mask=spec.get("hit_mask"), **facts)
    if kind == "periodic-damage":
        return periodic_damage(info, damage=spec.get("damage", 1), crit=spec.get("crit", False))
    if kind == "periodic-heal":
        return periodic_heal(info, crit=spec.get("crit", False))
    return simple_event(kind)


def cmd_event(ctx: Context, args) -> Any:
    d = ctx.defs.get(args.provider)
    if d is None:
        raise SourceError(f"no spell {args.provider}")
    spec = json.loads(args.event)
    ev = build_event(ctx, spec)
    facts = ActorFacts(**spec.get("facts", {}))
    state = HolderState(**spec.get("state", {}))
    result = ctx.evaluator.evaluate(d, ev, args.holder, state, facts)
    return {"provider": args.provider, "holder": args.holder, "event": ev.to_dict(),
            "eligibility": result.to_dict()}


def cmd_rppm(ctx: Context, args) -> Any:
    info = ctx.catalog.require(args.spell_id)
    if info.base_ppm <= 0:
        raise SourceError(f"spell {args.spell_id} has no RPPM (BaseProcRate {info.base_ppm})")
    inputs = RppmInputs(
        mod_haste=1.0 / (1.0 + args.haste), mod_ranged_haste=1.0 / (1.0 + args.haste),
        mod_spell_haste=1.0 / (1.0 + args.haste), mod_haste_regen=1.0 / (1.0 + args.haste),
        crit_pct=args.crit, ranged_crit_pct=args.crit, spell_crit_pct=args.crit,
        class_id=args.class_id, primary_spec=args.spec, race_id=args.race,
        item_level=args.item_level, in_battleground_or_arena=args.battleground,
        auras=frozenset(args.aura) if args.aura is not None else None)
    rpp = None
    if any(m["type"] == 6 for m in info.ppm_mods):
        from gearing.scaling import rand_prop_quality_column  # reuse the gearing port
        table = ctx.source.tables("RandPropPoints").by("ID")
        column = f"{rand_prop_quality_column(3)}_0"   # ITEM_QUALITY_RARE, INVTYPE_CHEST -> index 0

        def rpp(level: int) -> float:
            row = table.get(level)
            return float(row[column]) if row else 0.0
    rate = rppm_rate(info.base_ppm, info.ppm_mods, inputs, rpp)
    chance = rppm_chance(rate.chance_percent, args.since_attempt, args.since_proc)
    return {"spell_id": args.spell_id, "base": info.base_ppm, "flags_unconsumed": info.ppm_flags,
            "rate": rate.to_dict(), "chance": chance.to_dict()}


def cmd_ppm(ctx: Context, args) -> Any:
    return classic_ppm_chance(args.weapon_speed_ms, args.ppm).to_dict()


def cmd_timeline(ctx: Context, args) -> Any:
    spec = json.loads(Path(args.file).read_text(encoding="utf-8"))
    providers = []
    for i, p in enumerate(spec["providers"]):
        d = ctx.defs.get(p["spell_id"])
        providers.append(Provider(
            definition=d, holder=p.get("holder", "actor"), sequence=i,
            has_caster=p.get("has_caster", True),
            rppm_inputs=RppmInputs(**p.get("rppm_inputs", {})),
            weapon_speed_ms=p.get("weapon_speed_ms", {}),
            cooldown_override_ms=p.get("cooldown_override_ms"),
            max_charges_override=p.get("max_charges_override"),
            prepare_proc_result=p.get("prepare_proc_result", True)))
    stream = []
    for item in spec["stream"]:
        if item["kind"] == "event":
            stream.append(StreamItem(time_ms=item["time_ms"], kind="event",
                                     event=build_event(ctx, item["event"]),
                                     rolls=item.get("rolls", ()),
                                     facts=ActorFacts(**item.get("facts", {}))))
        else:
            stream.append(StreamItem(time_ms=item["time_ms"], kind=item["kind"],
                                     spell_id=item["spell_id"], stack_amount=item.get("stack_amount", 1)))
    return Timeline(ctx.evaluator, providers).run(stream)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proc_research.py", description=__doc__)
    parser.add_argument("--tables", default=None, help="data/tables directory")
    parser.add_argument("--overlay", default=None, help="trinity-world-overlay.json")
    parser.add_argument("--no-overlay", action="store_true",
                        help="DB2 only: ignore spell_proc/scripts/conditions (Trinity defaults only)")
    parser.add_argument("--output", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("provider", help="immutable proc definition of one spell")
    p.add_argument("spell_id", type=int)
    p.add_argument("--difficulty", type=int, default=0)
    p.set_defaults(func=cmd_provider)

    p = sub.add_parser("effect", help="one provider effect, e.g. 1250564:0")
    p.add_argument("target")
    p.add_argument("--difficulty", type=int, default=0)
    p.set_defaults(func=cmd_effect)

    p = sub.add_parser("graph", help="authored trigger topology reachable from a spell")
    p.add_argument("spell_id", type=int)
    p.add_argument("--depth", type=int, default=6)
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("census", help="population census and shapes")
    p.add_argument("--with-ids", action="store_true")
    p.set_defaults(func=cmd_census)

    p = sub.add_parser("item", help="proc routes of an ItemID")
    p.add_argument("item_id", type=int)
    p.set_defaults(func=cmd_item)

    p = sub.add_parser("enchant", help="proc routes of a SpellItemEnchantment")
    p.add_argument("enchant_id", type=int)
    p.set_defaults(func=cmd_enchant)

    p = sub.add_parser("vocabulary", help="event/flag/field vocabulary")
    p.add_argument("--census", action="store_true", help="add per-field value counts")
    p.set_defaults(func=cmd_vocabulary)

    p = sub.add_parser("sources", help="every table/column the tooling consumed, with hashes")
    p.add_argument("--no-hash", action="store_true")
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("event", help="pre-RNG eligibility of one provider for one synthetic event")
    p.add_argument("provider", type=int)
    p.add_argument("event", help='JSON, e.g. {"kind":"spell-hit","spell":133,"damage":10}')
    p.add_argument("--holder", choices=("actor", "target"), default="actor")
    p.set_defaults(func=cmd_event)

    p = sub.add_parser("rppm", help="RPPM rate and chance for explicit inputs")
    p.add_argument("spell_id", type=int)
    p.add_argument("--haste", type=float, default=0.0, help="0.2 = 20%% haste")
    p.add_argument("--crit", type=float, default=0.0, help="crit percent")
    p.add_argument("--class-id", type=int, default=None)
    p.add_argument("--spec", type=int, default=None)
    p.add_argument("--race", type=int, default=None)
    p.add_argument("--item-level", type=int, default=-1)
    p.add_argument("--battleground", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--aura", type=int, action="append", default=None)
    p.add_argument("--since-attempt", type=float, default=10.0)
    p.add_argument("--since-proc", type=float, default=120.0)
    p.set_defaults(func=cmd_rppm)

    p = sub.add_parser("ppm", help="classic PPM chance")
    p.add_argument("weapon_speed_ms", type=int)
    p.add_argument("ppm", type=float)
    p.set_defaults(func=cmd_ppm)

    p = sub.add_parser("timeline", help="replay a synthetic event stream with given rolls")
    p.add_argument("file")
    p.set_defaults(func=cmd_timeline)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = Context(args.tables, args.overlay, args.no_overlay)
        _emit(args.func(ctx, args), args)
    except SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0
