"""Authored proc topology: provider -> proc definition -> triggered spell.

Two edge kinds, kept apart:

* **proc** ``P -> T``: ``P`` has a resolved ``SpellProcEntry`` and an aura
  effect whose ``AuraEffect::HandleProc`` branch casts ``EffectTriggerSpell``
  (DUMMY, PROC_TRIGGER_SPELL, PROC_TRIGGER_SPELL_WITH_VALUE) and is not
  disabled by ``DisableEffectsMask``;
* **effect** ``S -> U``: any other ``SpellEffect.EffectTriggerSpell`` reference
  (TRIGGER_SPELL, TRIGGER_MISSILE, PERIODIC_TRIGGER_SPELL, ...).  These are
  ordinary spell relationships, listed so a chain like
  ``proc -> spell -> applies provider aura -> proc`` is visible.

This is authored topology, not executability: an edge says the data names a
spell, never that Trinity (or anything else) would do something useful with it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .definition import ProcDefinitions
from .enums import TRIGGER_SPELL_HANDLERS
from .spells import DIFFICULTY_NONE


@dataclass(frozen=True)
class Edge:
    source: int
    target: int
    kind: str          # "proc" | "effect"
    effect: int
    aura: int
    effect_type: int


class ProcGraph:
    def __init__(self, defs: ProcDefinitions, providers: Iterable[int]) -> None:
        self.defs = defs
        cat = defs.catalog
        self.providers = sorted(set(providers))
        provider_set = set(self.providers)
        self.out: dict[int, list[Edge]] = defaultdict(list)
        self.edges: list[Edge] = []
        for (spell, diff), effects in cat.effects.items():
            if diff != DIFFICULTY_NONE:
                continue
            is_provider = spell in provider_set
            d = defs.get(spell) if is_provider else None
            for eff in effects.values():
                if not eff.trigger_spell:
                    continue
                kind = "effect"
                if d is not None and d.entry is not None and eff.is_aura and eff.aura in TRIGGER_SPELL_HANDLERS:
                    role = next((r for r in d.effects if r.index == eff.index), None)
                    if role is not None and not role.disabled:
                        kind = "proc"
                edge = Edge(spell, eff.trigger_spell, kind, eff.index, eff.aura, eff.effect)
                self.out[spell].append(edge)
                self.edges.append(edge)

    # ------------------------------------------------------------------
    def proc_edges(self) -> list[Edge]:
        return [e for e in self.edges if e.kind == "proc"]

    def subgraph(self, roots: Iterable[int], max_depth: int = 64) -> dict[str, Any]:
        """Everything reachable from ``roots`` over both edge kinds."""
        seen = {}
        frontier = [(r, 0) for r in roots]
        edges = []
        while frontier:
            node, depth = frontier.pop()
            if node in seen and seen[node] <= depth:
                continue
            seen[node] = depth
            if depth >= max_depth:
                continue
            for e in self.out.get(node, ()):
                edges.append(e)
                frontier.append((e.target, depth + 1))
        uniq = sorted({(e.source, e.target, e.kind, e.effect) for e in edges})
        return {"nodes": sorted(seen), "edges": [dict(zip(("source", "target", "kind", "effect"), u)) for u in uniq]}

    def sccs(self) -> list[list[int]]:
        """Non-trivial strongly connected components (cycles), iterative Tarjan."""
        index: dict[int, int] = {}
        low: dict[int, int] = {}
        on_stack: set[int] = set()
        stack: list[int] = []
        out: list[list[int]] = []
        counter = 0
        nodes = sorted(set(self.out) | {e.target for e in self.edges})
        for start in nodes:
            if start in index:
                continue
            work = [(start, iter(self.out.get(start, ())))]
            index[start] = low[start] = counter
            counter += 1
            stack.append(start)
            on_stack.add(start)
            while work:
                node, it = work[-1]
                advanced = False
                for e in it:
                    t = e.target
                    if t not in index:
                        index[t] = low[t] = counter
                        counter += 1
                        stack.append(t)
                        on_stack.add(t)
                        work.append((t, iter(self.out.get(t, ()))))
                        advanced = True
                        break
                    if t in on_stack:
                        low[node] = min(low[node], index[t])
                if advanced:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    self_loop = any(e.target == node for e in self.out.get(node, ()))
                    if len(comp) > 1 or self_loop:
                        out.append(sorted(comp))
        return sorted(out)

    def cycles_with_proc_edge(self) -> list[dict[str, Any]]:
        result = []
        for comp in self.sccs():
            members = set(comp)
            kinds = sorted({e.kind for n in comp for e in self.out.get(n, ()) if e.target in members})
            if "proc" in kinds:
                result.append({"spells": comp, "edge_kinds": kinds,
                               "self_loop": len(comp) == 1})
        return result

    def max_proc_depth(self) -> dict[str, Any]:
        """Longest simple chain of *proc* edges, allowing effect edges in between.

        Computed on the condensation (SCCs collapsed) so cycles do not inflate
        it; a chain that enters a cycle is reported separately.
        """
        comp_of: dict[int, int] = {}
        comps = self.sccs()
        for i, comp in enumerate(comps):
            for n in comp:
                comp_of[n] = -(i + 1)
        def cid(n: int) -> int:
            return comp_of.get(n, n)
        dag: dict[int, set[tuple[int, int]]] = defaultdict(set)
        for e in self.edges:
            a, b = cid(e.source), cid(e.target)
            if a != b:
                dag[a].add((b, 1 if e.kind == "proc" else 0))
        memo: dict[int, tuple[int, list[int]]] = {}
        order: list[int] = []
        # iterative post-order
        visited: set[int] = set()
        for root in list(dag):
            if root in visited:
                continue
            stack = [(root, False)]
            while stack:
                node, done = stack.pop()
                if done:
                    order.append(node)
                    continue
                if node in visited:
                    continue
                visited.add(node)
                stack.append((node, True))
                for nxt, _ in dag.get(node, ()):
                    if nxt not in visited:
                        stack.append((nxt, False))
        for node in order:
            best = (0, [node])
            for nxt, w in dag.get(node, ()):
                sub = memo.get(nxt, (0, [nxt]))
                cand = (sub[0] + w, [node] + sub[1])
                if cand[0] > best[0]:
                    best = cand
            memo[node] = best
        depth, path = max(memo.values(), key=lambda v: (v[0], -len(v[1])), default=(0, []))
        return {"max_proc_edges_on_acyclic_path": depth,
                "witness_path": [p if p >= 0 else comps[-p - 1] for p in path]}
