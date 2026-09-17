"""Track C2: the character-preparation dependency graph.

Nodes are raw inputs (the identity / server rows of the input inventory), derived values (the compiler's
``/derived/*`` nodes), the research modules that compute them, and typed hooks owned by other tracks.
Edges read "``to`` is derived from ``from``" (or "computed by" for research -> derived).  The research modules
are imported lazily; an import failure or a missing corpus turns that node (and everything computed by it)
into ``unresolved`` instead of failing the graph.

The derived-from edges are the same edges ``Compiler.compile`` records per fixture in ``dependencies``; a test
keeps the two in sync.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import CORPORA, EVIDENCE_CLASSES, ROOT, SNAPSHOT_BUILD, TDB_RELEASE, TRINITY_COMMIT, SourceError
from .fixture import canonical_json

P = "Entities/Player/Player.cpp"

#: (node id, fixture pointer) of every raw input the compiler reads
RAW_INPUTS: list[tuple[str, str, str, str]] = [
    # id, label, kind, evidence
    ("/fixture/identity", "race / class / spec / level", "identity", "db2-fact"),
    ("/fixture/traits", "trait selections + hero sub-tree", "identity", "db2-fact"),
    ("/fixture/pvp_traits", "PvP talents (optional)", "identity", "db2-fact"),
    ("/fixture/learned_spells", "explicitly learned spells (optional, character_spell)", "identity", "trinity-consumer"),
    ("/fixture/equipment", "slot -> item entry (context, bonus lists, gems, enchants)", "identity", "db2-fact"),
    ("/fixture/controlled_units", "controlled-unit identity (hook section)", "identity", "structural-inference"),
    ("/server_inputs/base_stats", "player_classlevelstats + player_racestats", "server", "world-db-fact"),
]

#: derived node -> (owner, evidence class when resolved, consumer coordinate, computed-by research ids)
DERIVED: dict[str, dict[str, Any]] = {
    "/derived/identity": {"owner": "charstats.identity", "evidence": "db2-fact",
                          "consumer": f"{P}:387 Player::Create", "research": ["research:charstats"]},
    "/derived/traits": {"owner": "character_prep.compiler.TraitEngine", "evidence": "db2-fact",
                        "consumer": "Spells/TraitMgr.cpp:838-1015 ValidateConfig; Player.cpp:29380-29409 ApplyTraitConfig",
                        "research": ["research:character_prep.trait_engine"]},
    "/derived/pvp_traits": {"owner": "character_prep.compiler", "evidence": "db2-fact",
                            "consumer": f"{P}:28467 _LoadPvpTalents", "research": ["research:character_prep.trait_engine"]},
    "/derived/gear": {"owner": "gearing.loadout", "evidence": "db2-fact",
                      "consumer": f"{P}:7847 _ApplyItemBonuses; {P}:13417 ApplyEnchantment", "research": ["research:gearing"]},
    "/derived/spells": {"owner": "charstats.acquisition + character_prep.compiler (default skills)", "evidence": "trinity-consumer",
                        "consumer": f"{P}:30631-30648; {P}:25259-25441; {P}:3079-3104",
                        "research": ["research:charstats", "research:procs", "research:dummy_semantics.markers",
                                     "research:character_prep.default_skills"]},
    "/derived/armor_specialization": {"owner": "charstats.acquisition (rule) + character_prep.compiler (spell set, gate)",
                                      "evidence": "trinity-consumer", "consumer": f"{P}:26203-26213 HasItemFitToSpellRequirements",
                                      "research": ["research:charstats"]},
    "/derived/stats": {"owner": "charstats.character", "evidence": "trinity-consumer",
                       "consumer": "Entities/Unit/StatSystem.cpp:198-345 UpdateAllStats", "research": ["research:charstats"]},
    "/derived/controlled_units": {"owner": "Track A (controlled_units)", "evidence": "structural-inference",
                                  "consumer": "Entities/Pet/Pet.cpp (Track A)", "research": ["hook:controlled_units"]},
    "/derived/weapon": {"owner": "Track B/C (weapon_combat) + gearing weapon facts", "evidence": "structural-inference",
                        "consumer": "Entities/Unit/Unit.cpp:326 attack timers (Track B)", "research": ["hook:weapon_combat", "research:gearing"]},
    "/derived/initial_state": {"owner": "character_prep.compiler", "evidence": "trinity-consumer",
                               "consumer": f"{P}:2323-2498 InitStatsForLevel; {P}:490-502 Create; Spells/SpellHistory.cpp:147-179",
                               "research": ["research:character_prep.initial_state"]},
}

#: "to is derived from from" -- identical to the dep() calls of Compiler.compile
DERIVED_FROM: list[tuple[str, str]] = [
    ("/fixture/traits", "/derived/traits"),
    ("/derived/identity", "/derived/traits"),
    ("/fixture/pvp_traits", "/derived/pvp_traits"),
    ("/derived/identity", "/derived/pvp_traits"),
    ("/fixture/equipment", "/derived/gear"),
    ("/derived/identity", "/derived/gear"),
    ("/fixture/learned_spells", "/derived/spells"),
    ("/derived/traits", "/derived/spells"),
    ("/derived/gear", "/derived/spells"),
    ("/derived/pvp_traits", "/derived/spells"),
    ("/derived/identity", "/derived/spells"),
    ("/derived/gear", "/derived/armor_specialization"),
    ("/derived/spells", "/derived/armor_specialization"),
    ("/derived/gear", "/derived/stats"),
    ("/derived/armor_specialization", "/derived/stats"),
    ("/server_inputs/base_stats", "/derived/stats"),
    ("/derived/identity", "/derived/stats"),
    ("/fixture/controlled_units", "/derived/controlled_units"),
    ("/derived/spells", "/derived/controlled_units"),
    ("/derived/stats", "/derived/controlled_units"),
    ("/derived/gear", "/derived/weapon"),
    ("/derived/stats", "/derived/weapon"),
    ("/derived/stats", "/derived/initial_state"),
    ("/derived/spells", "/derived/initial_state"),
    ("/derived/gear", "/derived/initial_state"),
    ("/derived/identity", "/derived/initial_state"),
    ("/fixture/identity", "/derived/identity"),
]

#: research / hook nodes: (id, import target, required corpora, owner report)
RESEARCH: list[dict[str, Any]] = [
    {"id": "research:gearing", "module": "gearing.loadout", "corpora": [], "report": "docs/research/gearing-pipeline-archaeology.md"},
    {"id": "research:charstats", "module": "charstats.character", "corpora": [],
     "report": "docs/research/character-stat-pipeline-archaeology.md"},
    {"id": "research:procs", "module": "procs.spells", "corpora": ["docs/research/procs-corpora/census.json"],
     "report": "docs/research/proc-pipeline-archaeology.md"},
    {"id": "research:dummy_semantics.markers", "module": "dummy_semantics.loaders",
     "corpora": ["docs/research/dummy-corpora/markers.json", "docs/research/dummy-corpora/bindings.json"],
     "report": "docs/research/dummy-server-semantics-archaeology.md"},
    {"id": "research:character_prep.trait_engine", "module": "character_prep.compiler", "corpora": [],
     "report": "parts/E.md §5 (port of TraitMgr)"},
    {"id": "research:character_prep.default_skills", "module": "character_prep.compiler", "corpora": [],
     "report": "parts/E.md §3 (port of LearnDefaultSkills)"},
    {"id": "research:character_prep.initial_state", "module": "character_prep.inputs", "corpora": [],
     "report": "parts/E.md §3"},
    {"id": "hook:controlled_units", "module": "controlled_units.ownership", "attribute": "CHARACTER_PREP_HOOK", "corpora": [],
     "report": "Track A (parts/A.md, parts/B.md)"},
    {"id": "hook:weapon_combat", "module": "weapon_combat.weapon", "attribute": "CHARACTER_PREP_HOOK", "corpora": [],
     "report": "Track B (parts/C.md, parts/D.md)"},
]


@dataclass
class Graph:
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[dict[str, str]] = field(default_factory=list)

    def add_node(self, node_id: str, **attrs: Any) -> None:
        if node_id in self.nodes:
            raise SourceError(f"duplicate graph node {node_id}")
        self.nodes[node_id] = {"id": node_id, **attrs}

    def add_edge(self, src: str, dst: str, relation: str) -> None:
        for n in (src, dst):
            if n not in self.nodes:
                raise SourceError(f"edge {src} -> {dst} names unknown node {n}")
        self.edges.append({"from": src, "to": dst, "relation": relation})

    def topological_order(self) -> list[str]:
        """Kahn's algorithm, ties broken by node id; raises on a cycle."""
        indeg = {n: 0 for n in self.nodes}
        out: dict[str, list[str]] = {n: [] for n in self.nodes}
        for e in self.edges:
            indeg[e["to"]] += 1
            out[e["from"]].append(e["to"])
        ready = sorted(n for n, d in indeg.items() if d == 0)
        order = []
        while ready:
            n = ready.pop(0)
            order.append(n)
            for m in sorted(out[n]):
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
                    ready.sort()
        if len(order) != len(self.nodes):
            raise SourceError(f"dependency graph has a cycle among {sorted(set(self.nodes) - set(order))}")
        return order

    def upstream(self, node_id: str) -> list[str]:
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            n = stack.pop()
            for e in self.edges:
                if e["to"] == n and e["from"] not in seen:
                    seen.add(e["from"])
                    stack.append(e["from"])
        return sorted(seen)

    def to_dict(self) -> dict[str, Any]:
        order = self.topological_order()
        return {
            "provenance": {"snapshot_build": SNAPSHOT_BUILD, "trinitycore_commit": TRINITY_COMMIT, "world_database": TDB_RELEASE,
                           "generator": "python3 character_prep.py graph --json"},
            "acyclic": True,
            "node_count": len(self.nodes), "edge_count": len(self.edges),
            "topological_order": order,
            "nodes": [self.nodes[n] for n in order],
            "edges": sorted(self.edges, key=lambda e: (e["from"], e["to"], e["relation"])),
        }

    def to_dot(self) -> str:
        shape = {"input": "box", "derived": "ellipse", "research": "component", "hook": "hexagon"}
        lines = ["digraph character_prep {", "  rankdir=LR;"]
        for n in self.topological_order():
            node = self.nodes[n]
            style = ',style=dashed' if node.get("status") == "unresolved" else ""
            label = f"{n}\\n[{node.get('evidence_class')}]"
            lines.append(f'  "{n}" [shape={shape.get(node["kind"], "ellipse")},label="{label}"{style}];')
        for e in sorted(self.edges, key=lambda e: (e["from"], e["to"])):
            lines.append(f'  "{e["from"]}" -> "{e["to"]}" [label="{e["relation"]}"];')
        lines.append("}")
        return "\n".join(lines) + "\n"


def _probe_research(spec: dict[str, Any]) -> tuple[str, str]:
    try:
        module = importlib.import_module(spec["module"])
    except Exception as error:  # any import failure degrades the node
        return "unresolved", f"import {spec['module']} failed: {error.__class__.__name__}: {error}"
    if spec.get("attribute") and not hasattr(module, spec["attribute"]):
        return "unresolved", f"{spec['module']} has no {spec['attribute']}"
    missing = [c for c in spec["corpora"] if not (ROOT / c).exists()]
    if missing:
        return "unresolved", f"missing corpora {missing}"
    return "resolved", "ok"


def build_graph() -> Graph:
    g = Graph()
    for node_id, label, kind, evidence in RAW_INPUTS:
        g.add_node(node_id, kind="input", label=label, input_kind=kind, owner="caller" if kind == "identity" else "agent F (server)",
                   evidence_class=evidence, status="resolved")
    research_status: dict[str, str] = {}
    for spec in RESEARCH:
        status, why = _probe_research(spec)
        research_status[spec["id"]] = status
        g.add_node(spec["id"], kind="hook" if spec["id"].startswith("hook:") else "research", module=spec["module"],
                   owner=spec["report"], evidence_class="structural-inference" if status == "resolved" else "unresolved",
                   status=status, status_reason=why, attribute=spec.get("attribute"), corpora=spec["corpora"])
    for node_id, d in DERIVED.items():
        blocked = [r for r in d["research"] if research_status.get(r) != "resolved"]
        g.add_node(node_id, kind="derived", owner=d["owner"], consumer=d["consumer"],
                   evidence_class="unresolved" if blocked else d["evidence"],
                   status="unresolved" if blocked else "resolved",
                   status_reason=f"computed by unresolved {blocked}" if blocked else "ok")
    for src, dst in DERIVED_FROM:
        g.add_edge(src, dst, "derived-from")
    for node_id, d in DERIVED.items():
        for r in d["research"]:
            g.add_edge(r, node_id, "computed-by")
    for n in g.nodes.values():
        if n["evidence_class"] not in EVIDENCE_CLASSES:
            raise SourceError(f"node {n['id']} has evidence class {n['evidence_class']!r}")
    g.topological_order()
    return g


def cmd_graph(args: Any) -> int:
    g = build_graph()
    if args.dot:
        text = g.to_dot()
    elif args.json or args.output:
        text = canonical_json(g.to_dict())
    else:
        for n in g.topological_order():
            node = g.nodes[n]
            ups = [e["from"] for e in g.edges if e["to"] == n]
            print(f"{node['kind']:8} {node['status']:10} {node['evidence_class']:21} {n}  <- {ups}")
        return 0
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text, end="")
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("graph", help="the character-preparation dependency graph (C2)")
    p.add_argument("--dot", action="store_true", help="Graphviz output")
    p.add_argument("--json", action="store_true")
    p.add_argument("--output", default=None)
    p.set_defaults(func=cmd_graph)


def write_corpus() -> Path:
    CORPORA.mkdir(parents=True, exist_ok=True)
    path = CORPORA / "dependency-graph.json"
    path.write_text(canonical_json(build_graph().to_dict()), encoding="utf-8")
    return path
