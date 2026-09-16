"""Command line for the Dummy / server-side semantics archaeology."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import CORPORA
from .bindings import BindingMap
from .census import FinalCensus
from .conditions import Conditions
from .corrections import Corrections
from .families import FamilyIndex
from .graph import SemanticGraph
from .hardcoded import HardcodedIndex
from .hooks import vocabulary
from .loaders import Bundle
from .markers import MarkerIndex
from .population import Population
from .procxref import ProcCrossReference
from .scope import Scope
from .specs import StatusResolver, gear_census, spec_census
from .witnesses import WITNESSES, witness_ids


class Context:
    def __init__(self) -> None:
        t = time.time()
        self.b = Bundle()
        self.scope = Scope(self.b)
        self.pop = Population(self.b)
        self.bm = BindingMap(self.b)
        self.fi = FamilyIndex(self.bm)
        self.hc = HardcodedIndex(self.b)
        self.mk = MarkerIndex(self.b, self.pop, self.bm, self.hc)
        self.cd = Conditions(self.b)
        self.corr = Corrections(self.b)
        self.load_seconds = round(time.time() - t, 1)

    @property
    def player(self) -> set[int]:
        return self.scope.reach


def provenance(ctx: Context) -> dict[str, Any]:
    import subprocess
    root = CORPORA.parents[2]
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return {
        "wowlab_data_head": head, "snapshot": json.loads((root / "changes/metadata/12.1.0.69497.json").read_text())["version"],
        "trinity_commit": ctx.b.world.provenance["trinitycore_commit"],
        "server_overlay": ctx.b.world.provenance, "script_index": ctx.b.index.provenance,
        "dispatch": ctx.b.dispatch.provenance, "build_skew": ctx.b.skew.provenance,
        "load_seconds": ctx.load_seconds,
    }


def cmd_population(ctx: Context, args) -> Any:
    return {"all": ctx.pop.counts(), "other_difficulty_rows": dict(ctx.pop.other_difficulty),
            "player": ctx.pop.counts(ctx.player), "scope": ctx.scope.summary(),
            "unimplemented_player": ctx.pop.unimplemented_breakdown(ctx.player),
            "unimplemented_all": ctx.pop.unimplemented_breakdown()}


def cmd_bindings(ctx: Context, args) -> Any:
    out = {"all": ctx.bm.summary(), "player": ctx.bm.summary(ctx.player), "unresolved_script_names": dict(ctx.bm.unresolved_names.most_common())}
    if args.with_records:
        out["player_records"] = {sid: [b.to_dict() for b in lst] for sid, lst in ctx.bm.by_spell.items() if sid in ctx.player}
    return out


def cmd_hooks(ctx: Context, args) -> Any:
    return vocabulary(ctx.b.dispatch)


def cmd_families(ctx: Context, args) -> Any:
    out = {"all": ctx.fi.census(), "player": ctx.fi.census(ctx.player)}
    if args.with_records:
        out["player_hooks"] = [h.to_dict() for h in ctx.fi.hooks if h.spell_id in ctx.player]
    return out


def cmd_hardcoded(ctx: Context, args) -> Any:
    return {"all": ctx.hc.census(), "player": ctx.hc.census(ctx.player),
            "player_sites": [s.to_dict() for s in ctx.hc.spell_sites(ctx.player)]}


def cmd_corrections(ctx: Context, args) -> Any:
    return {"all": ctx.corr.census(), "player": ctx.corr.census(ctx.player),
            "player_fixes": [f for f in ctx.corr.fixes if any(i in ctx.player for i in f["ids"])],
            "all_fixes": ctx.corr.fixes if args.with_records else None}


def cmd_conditions(ctx: Context, args) -> Any:
    return {"all": ctx.cd.census(), "player": ctx.cd.census(ctx.player)}


def cmd_markers(ctx: Context, args) -> Any:
    out = {"all": ctx.mk.census(), "player": ctx.mk.census(ctx.player)}
    if args.with_records:
        out["player_dummy_aura_owners"] = {s: ctx.mk.classify(s) for s in sorted({r.spell_id for r in ctx.pop.records.values()
                                                                                if "dummy-aura" in r.classes and r.spell_id in ctx.player})}
    return out


def cmd_graph(ctx: Context, args) -> Any:
    g = SemanticGraph(ctx.b, ctx.bm, ctx.fi, ctx.mk, ctx.hc, ctx.cd, include_trigger_edges=args.triggers)
    return g.summary(ctx.player)


def cmd_procxref(ctx: Context, args) -> Any:
    x = ProcCrossReference(ctx.b, ctx.bm, ctx.fi, ctx.mk)
    return x.resolve_all(player_only=not args.all)


def cmd_specs(ctx: Context, args) -> Any:
    r = StatusResolver(ctx.b, ctx.pop, ctx.bm, ctx.fi, ctx.mk, ctx.hc, ctx.cd)
    return {"specs": spec_census(ctx.scope, r), "gear": gear_census(ctx.scope, r), "player_total": r.census_for(ctx.player, "player")}


def cmd_census(ctx: Context, args) -> Any:
    fc = FinalCensus(ctx.b, ctx.scope, ctx.pop, ctx.bm, ctx.fi, ctx.mk, ctx.hc, ctx.cd, ctx.corr)
    return {"player": fc.run(ctx.player, "player"), "all": fc.run(None, "all"), "provenance": provenance(ctx)}


def cmd_spell(ctx: Context, args) -> Any:
    sid = args.spell_id
    info = ctx.b.info(sid)
    if info is None:
        return {"spell_id": sid, "error": "not in snapshot"}
    fc = FinalCensus(ctx.b, ctx.scope, ctx.pop, ctx.bm, ctx.fi, ctx.mk, ctx.hc, ctx.cd, ctx.corr)
    return {
        "spell_id": sid, "name": info.name, "in_player_scope": sid in ctx.player, "chain": ctx.scope.chain(sid) if sid in ctx.player else None,
        "effects": [{"index": e.index, "label": ctx.b.effect_label(e), "trigger": e.trigger_spell, "misc": [e.misc0, e.misc1],
                     "bp": e.base_points, "targets": [e.target_a, e.target_b],
                     "population_classes": ctx.pop.records[e.row_id].classes if e.row_id in ctx.pop.records else [],
                     "generic_handler": ctx.pop.records[e.row_id].generic_handler if e.row_id in ctx.pop.records else None} for e in info.effects],
        "bindings": [b.to_dict() for b in ctx.bm.bindings(sid)],
        "families": ctx.fi.spell_families(sid), "hooks": [h.to_dict() for h in ctx.fi.for_spell(sid)],
        "markers": ctx.mk.classify(sid), "observers": ctx.mk.observers_of(sid),
        "hardcoded": [s.to_dict() for s in ctx.hc.spell_sites() if s.value == sid],
        "corrections": ctx.corr.by_spell.get(sid, []), "conditions": ctx.cd.rows_for_spell(sid),
        "linked": ctx.b.world.linked_for(ctx.b.catalog, sid),
        "census_bucket": fc.owner(sid), "newer_than_trinity": ctx.b.skew.is_newer_than_trinity(sid),
    }


def cmd_witnesses(ctx: Context, args) -> Any:
    out = {"witnesses": WITNESSES, "structural_cross_check": {}}
    for w in WITNESSES:
        sid = w["spell"]
        out["structural_cross_check"][f"{sid}:{w['script']}"] = {
            "families_indexed": [(h.list, h.family, h.sub) for h in ctx.fi.for_spell(sid) if h.executes and (w["script"] is None or h.script == w["script"])],
            "in_player_scope": sid in ctx.player,
        }
    return out


def cmd_sources(ctx: Context, args) -> Any:
    return {"db2": ctx.b.source.ledger.report(with_hashes=True), "corpora": provenance(ctx)}


def cmd_all(ctx: Context, args) -> int:
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jobs = {
        "population.json": lambda: cmd_population(ctx, args),
        "bindings.json": lambda: cmd_bindings(ctx, argparse.Namespace(with_records=True)),
        "hooks.json": lambda: cmd_hooks(ctx, args),
        "families.json": lambda: cmd_families(ctx, argparse.Namespace(with_records=True)),
        "hardcoded.json": lambda: cmd_hardcoded(ctx, args),
        "corrections.json": lambda: cmd_corrections(ctx, argparse.Namespace(with_records=True)),
        "conditions.json": lambda: cmd_conditions(ctx, args),
        "markers.json": lambda: cmd_markers(ctx, argparse.Namespace(with_records=True)),
        "graph.json": lambda: cmd_graph(ctx, argparse.Namespace(triggers=False)),
        "proc-xref.json": lambda: cmd_procxref(ctx, argparse.Namespace(all=False)),
        "specs.json": lambda: cmd_specs(ctx, args),
        "census.json": lambda: cmd_census(ctx, args),
        "witnesses.json": lambda: cmd_witnesses(ctx, args),
        "sources.json": lambda: cmd_sources(ctx, args),
    }
    for name, fn in jobs.items():
        t = time.time()
        payload = fn()
        (out / name).write_text(json.dumps(payload, indent=1, default=_default) + "\n", encoding="utf-8")
        print(f"wrote {out / name} ({(out / name).stat().st_size / 1e3:.0f} kB, {time.time() - t:.1f}s)", file=sys.stderr)
    return 0


def _default(o: Any) -> Any:
    if isinstance(o, set):
        return sorted(o)
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "__dataclass_fields__"):
        return asdict(o)
    raise TypeError(f"not serialisable: {type(o)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dummy_semantics", description=__doc__)
    ap.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("population", "effect population census"), ("hooks", "hook vocabulary and ordering"),
                        ("hardcoded", "engine hardcoded SpellID sites"), ("conditions", "ConditionMgr spell sources"),
                        ("specs", "per-spec and gear census"), ("census", "final bucket census"), ("witnesses", "hand-verified lifecycles"),
                        ("sources", "DB2 tables/columns consumed, with hashes")):
        sub.add_parser(name, help=help_)
    for name, help_ in (("bindings", "script binding map"), ("families", "structural families"), ("corrections", "LoadSpellInfoCorrections audit"),
                        ("markers", "marker/state Dummy auras")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--with-records", action="store_true")
    p = sub.add_parser("graph", help="server-semantic graph")
    p.add_argument("--triggers", action="store_true", help="add authored EffectTriggerSpell edges")
    p = sub.add_parser("procxref", help="proc census cross-reference")
    p.add_argument("--all", action="store_true", help="all providers, not only player scope")
    p = sub.add_parser("spell", help="everything about one SpellID")
    p.add_argument("spell_id", type=int)
    p = sub.add_parser("all", help="write every corpus into a directory")
    p.add_argument("--out-dir", default=str(CORPORA))
    args = ap.parse_args(argv)
    ctx = Context()
    if args.cmd == "all":
        return cmd_all(ctx, args)
    fn = globals()[f"cmd_{args.cmd}"]
    payload = fn(ctx, args)
    text = json.dumps(payload, indent=1, default=_default)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0
