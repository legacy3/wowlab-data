"""Server-semantic research graph.

Nodes (typed by prefix)::

    spell:<id>            a SpellID (owner, child, marker)
    script:<name>         a spell_script_names ScriptName
    class:<name@file>     a SpellScript/AuraScript class
    helper:<name@file>    a same-file free helper function
    family:<name>         a structural family label
    action:<kind>         ordinary semantic action class (cast, remove-aura, cooldown, energize, ...)
    world:<table>:<key>   a world-DB row (spell_linked_spell, spell_area, spell_pet_auras, conditions)
    engine:<function>     a hardcoded engine consumer

Edges (kind, evidence only -- never executability)::

    spell -bound-> script -implemented-by-> class -uses-> helper
    class -family-> family -emits-> action
    class -casts-> spell (referenced child)      class -queries-> spell (marker read)
    class -removes-> spell                        spell -db2-marker-> spell (SpellAuraRestrictions)
    spell -linked-> spell (spell_linked_spell)    spell -pet-aura-> spell
    spell -condition-> world:conditions:<key>     spell -engine-> engine:<function>
    spell -trigger-> spell (authored EffectTriggerSpell)  [optional, for depth]

Pure Python: components, degree/fanout, hubs, SCC cycles, depth from roots.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Any, Iterable

from .bindings import BindingMap
from .conditions import SPELL_SOURCES, Conditions
from .families import FamilyIndex
from .hardcoded import HardcodedIndex
from .loaders import Bundle
from .markers import MarkerIndex

FAMILY_ACTIONS = {
    "cast-child": ["cast"], "cast-child-with-amount": ["cast"], "choose-among-children": ["cast"],
    "random-child": ["cast"], "delayed-child": ["cast", "schedule"], "pet-owner-forward-cast": ["cast"],
    "consume-and-cast": ["modify-aura", "cast"], "linked-aura-mutation": ["modify-aura"],
    "cooldown-mutation": ["modify-cooldown"], "resource-mutation": ["modify-power"], "summon": ["summon"],
    "target-adapter": ["select-targets"], "proc-filter-adapter": ["gate-proc"], "area-target-filter": ["select-targets"],
    "cast-gate": ["gate-cast"], "amount-adapter": ["set-amount"], "suppress-default": ["no-action"],
    "state-only": ["marker"], "unclassified": [],
}


class SemanticGraph:
    def __init__(self, bundle: Bundle, bindings: BindingMap, families: FamilyIndex, markers: MarkerIndex,
                 hardcoded: HardcodedIndex, conditions: Conditions, include_trigger_edges: bool = False) -> None:
        self.b = bundle
        self.out: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.inc: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.nodes: set[str] = set()
        self.edge_count = 0
        self._build(bindings, families, markers, hardcoded, conditions, include_trigger_edges)

    def add(self, a: str, kind: str, b: str) -> None:
        self.nodes.add(a)
        self.nodes.add(b)
        self.out[a].append((kind, b))
        self.inc[b].append((kind, a))
        self.edge_count += 1

    def _build(self, bm: BindingMap, fi: FamilyIndex, mk: MarkerIndex, hc: HardcodedIndex, cd: Conditions, triggers: bool) -> None:
        cat = self.b.catalog
        for spell_id, lst in bm.by_spell.items():
            for bnd in lst:
                self.add(f"spell:{spell_id}", "bound", f"script:{bnd.script_name}")
                for c in bnd.classes:
                    ck = f"class:{c['name']}@{c['file'].split('/')[-1]}"
                    self.add(f"script:{bnd.script_name}", "implemented-by", ck)
                    for h in bnd.hooks:
                        if not h.executes:
                            continue
                        for via in h.facts.get("via", []):
                            if via.startswith("new "):
                                continue
                            self.add(ck, "uses", f"helper:{via}@{c['file'].split('/')[-1]}")
        for h in fi.hooks:
            if not h.executes:
                continue
            ck = f"class:{h.cls}@{h.file.split('/')[-1]}"
            self.add(ck, "family", f"family:{h.family}")
            for act in FAMILY_ACTIONS.get(h.family, []):
                self.add(f"family:{h.family}", "emits", f"action:{act}")
            for child in h.children:
                if cat.exists(child):
                    self.add(ck, "casts", f"spell:{child}")
            for q in h.queries_other_aura:
                if cat.exists(q):
                    self.add(ck, "queries", f"spell:{q}")
        for spell_id, obs in mk.observers.items():
            for o in obs.get("db2-aura-restriction", []):
                self.add(f"spell:{o['spell']}", "db2-marker", f"spell:{spell_id}")
            for o in obs.get("script-aura-remove", []):
                self.add(f"class:{o['class']}@{o['file'].split('/')[-1]}", "removes", f"spell:{spell_id}")
        for (typ, trigger), effects in self.b.world.linked(cat).items():
            for e in effects:
                self.add(f"spell:{trigger}", f"linked:{typ}", f"spell:{abs(e)}")
        for row in self.b.world.table("spell_pet_auras").dicts():
            self.add(f"spell:{row['spell']}", "pet-aura", f"spell:{row['aura']}")
        for st, name in SPELL_SOURCES.items():
            for entry, rows in cd.by_source.get(st, {}).items():
                key = f"world:conditions:{st}:{entry}"
                spell = entry if st != 18 else None
                if spell is not None and cat.exists(spell):
                    self.add(f"spell:{spell}", "condition", key)
        for s in hc.spell_sites():
            if s.role in ("gameplay-semantic", "amount-adapter", "target-adapter"):
                self.add(f"spell:{s.value}", "engine", f"engine:{s.function}")
        if triggers:
            for (spell, diff), effects in cat.effects.items():
                if diff == 0:
                    for e in effects.values():
                        if e.trigger_spell and cat.exists(e.trigger_spell):
                            self.add(f"spell:{spell}", "trigger", f"spell:{e.trigger_spell}")

    # ------------------------------------------------------------------
    def components(self) -> list[set[str]]:
        seen: set[str] = set()
        comps = []
        for n in self.nodes:
            if n in seen:
                continue
            comp = set()
            dq = deque([n])
            while dq:
                x = dq.popleft()
                if x in comp:
                    continue
                comp.add(x)
                for _, y in self.out.get(x, []):
                    if y not in comp:
                        dq.append(y)
                for _, y in self.inc.get(x, []):
                    if y not in comp:
                        dq.append(y)
            seen |= comp
            comps.append(comp)
        return comps

    def sccs(self) -> list[list[str]]:
        """Tarjan over spell/class nodes only (cycles among casts/queries/linked)."""
        index = 0
        stack: list[str] = []
        idx: dict[str, int] = {}
        low: dict[str, int] = {}
        on: set[str] = set()
        out: list[list[str]] = []
        nodes = [n for n in self.nodes if n.startswith(("spell:", "class:"))]
        adj = {n: [y for k, y in self.out.get(n, []) if y.startswith(("spell:", "class:", "script:")) or k == "implemented-by"] for n in self.nodes}

        def strong(v: str) -> None:
            nonlocal index
            idx[v] = low[v] = index
            index += 1
            stack.append(v)
            on.add(v)
            work = [(v, iter(adj.get(v, [])))]
            while work:
                node, it = work[-1]
                advanced = False
                for w in it:
                    if w not in idx:
                        idx[w] = low[w] = index
                        index += 1
                        stack.append(w)
                        on.add(w)
                        work.append((w, iter(adj.get(w, []))))
                        advanced = True
                        break
                    elif w in on:
                        low[node] = min(low[node], idx[w])
                if advanced:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == idx[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    if len(comp) > 1:
                        out.append(sorted(comp))

        for n in nodes:
            if n not in idx:
                strong(n)
        return out

    def depths(self, roots: Iterable[str]) -> dict[str, int]:
        d: dict[str, int] = {}
        dq = deque()
        for r in roots:
            if r in self.nodes:
                d[r] = 0
                dq.append(r)
        while dq:
            x = dq.popleft()
            for _, y in self.out.get(x, []):
                if y not in d:
                    d[y] = d[x] + 1
                    dq.append(y)
        return d

    def summary(self, player_spells: set[int] | None = None) -> dict[str, Any]:
        comps = self.components()
        sizes = sorted((len(c) for c in comps), reverse=True)
        kinds = Counter(k for lst in self.out.values() for k, _ in lst)
        node_kinds = Counter(n.split(":")[0] for n in self.nodes)
        hubs = Counter()
        for n in self.nodes:
            if n.startswith(("helper:", "script:", "class:")):
                hubs[n] = len(self.inc.get(n, [])) + len([1 for k, _ in self.out.get(n, []) if k in ("casts", "queries", "removes")])
        script_fanout = {n: len(self.inc.get(n, [])) for n in self.nodes if n.startswith("script:")}
        spell_fanout = {n: len([1 for k, _ in self.out.get(n, []) if k == "bound"]) for n in self.nodes if n.startswith("spell:")}
        out: dict[str, Any] = {
            "nodes": len(self.nodes), "edges": self.edge_count, "node_kinds": dict(node_kinds), "edge_kinds": dict(kinds.most_common()),
            "components": len(comps), "largest_components": sizes[:10],
            "singleton_components": sum(1 for s in sizes if s == 1),
            "helper_hubs": [(n, c) for n, c in hubs.most_common(15) if n.startswith("helper:")],
            "script_hubs (spells bound)": sorted(((n, c) for n, c in script_fanout.items() if c > 1), key=lambda x: -x[1])[:15],
            "class_hubs (casts+queries+removes)": [(n, c) for n, c in hubs.most_common(60) if n.startswith("class:")][:15],
            "spells_with_multiple_scripts": sum(1 for c in spell_fanout.values() if c > 1),
            "sccs": len(self.sccs()), "scc_examples": self.sccs()[:5],
        }
        if player_spells is not None:
            roots = [f"spell:{s}" for s in player_spells]
            d = self.depths(roots)
            sub_nodes = set(d)
            out["player_subgraph"] = {
                "nodes": len(sub_nodes), "node_kinds": dict(Counter(n.split(":")[0] for n in sub_nodes)),
                "max_depth": max(d.values()) if d else 0,
                "depth_histogram": dict(Counter(d.values())),
                "child_spells_outside_player_scope": sorted({int(n.split(":")[1]) for n in sub_nodes if n.startswith("spell:") and int(n.split(":")[1]) not in player_spells})[:60],
                "child_spells_outside_player_scope_count": sum(1 for n in sub_nodes if n.startswith("spell:") and int(n.split(":")[1]) not in player_spells),
            }
        return out
