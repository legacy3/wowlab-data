"""Per-specialization and gear census of server-driven effects.

For every current ``ChrSpecialization`` (one that loads a talent tree) the
reachable spells come from :class:`scope.Scope` (spec-attributed roots plus
their authored descendants).  Each reachable owner of a population effect is
sorted into exactly one status bucket by precedence::

    script-family        an executing hook with a structural family other than state-only/unclassified
    script-unique        an executing hook classified unclassified
    marker-only          no executing hook, but a strong observer reads the aura
    script-state-only    executing hooks that only keep state
    engine-hardcoded     a gameplay-semantic engine case names the spell
    world-data           spell_linked_spell / spell_pet_auras / conditions rows only
    build-skew           no consumer at all and newer than Trinity's supported build
    unresolved           no consumer at all

Effects and owners are both counted; gear/set/gem/enchant roots are reported in
the same shape using the acquisition detail recorded by the scope.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .bindings import BindingMap
from .conditions import Conditions
from .families import FamilyIndex
from .hardcoded import HardcodedIndex
from .loaders import Bundle
from .markers import MarkerIndex
from .population import Population
from .scope import Scope

STATUS_ORDER = ["script-family", "script-unique", "script-state-only", "marker-only", "engine-hardcoded", "world-data",
                "build-skew", "unresolved"]


class StatusResolver:
    def __init__(self, bundle: Bundle, pop: Population, bm: BindingMap, fi: FamilyIndex, mk: MarkerIndex,
                 hc: HardcodedIndex, cd: Conditions) -> None:
        self.b = bundle
        self.pop = pop
        self.bm = bm
        self.fi = fi
        self.mk = mk
        self.cd = cd
        self.engine_spells = {s.value for s in hc.spell_sites() if s.role in ("gameplay-semantic", "amount-adapter", "target-adapter")}
        linked = bundle.world.linked(bundle.catalog)
        self.world_spells = {t for (_, t) in linked} | {abs(e) for lst in linked.values() for e in lst}
        self.world_spells |= {int(r["spell"]) for r in bundle.world.table("spell_pet_auras").dicts()}
        self.world_spells |= {e for st in (13, 17, 24) for e in cd.by_source.get(st, {})}
        self._cache: dict[int, dict[str, Any]] = {}

    def status(self, spell_id: int) -> dict[str, Any]:
        if spell_id in self._cache:
            return self._cache[spell_id]
        hooks = [h for h in self.fi.for_spell(spell_id) if h.executes]
        fams = {h.family for h in hooks}
        marker = self.mk.classify(spell_id)
        if hooks and fams - {"state-only", "unclassified"}:
            st = "script-family"
        elif "unclassified" in fams:
            st = "script-unique"
        elif hooks:
            st = "script-state-only"
        elif marker["marker_consumed"]:
            st = "marker-only"
        elif spell_id in self.engine_spells:
            st = "engine-hardcoded"
        elif spell_id in self.world_spells:
            st = "world-data"
        elif marker["newer_than_trinity"]:
            st = "build-skew"
        else:
            st = "unresolved"
        rec = {"status": st, "families": sorted(fams), "observers": marker["observer_kinds"],
               "bound_scripts": sorted({b.script_name for b in self.bm.bindings(spell_id)})}
        self._cache[spell_id] = rec
        return rec

    def census_for(self, spells: set[int], label: str) -> dict[str, Any]:
        owners = [s for s in spells if s in self.pop.by_spell]
        effects = [self.pop.records[r] for s in owners for r in self.pop.by_spell[s]]
        st_owner = Counter(self.status(s)["status"] for s in owners)
        st_effect = Counter(self.status(r.spell_id)["status"] for r in effects)
        fam_owner = Counter(f for s in owners for f in self.status(s)["families"])
        cls_effect = Counter(c for r in effects for c in r.classes)
        return {
            "label": label, "reachable_spells": len(spells), "owners_with_population_effects": len(owners),
            "population_effects": len(effects), "effect_classes": dict(cls_effect.most_common()),
            "owner_status": {k: st_owner[k] for k in STATUS_ORDER if st_owner[k]},
            "effect_status": {k: st_effect[k] for k in STATUS_ORDER if st_effect[k]},
            "owner_families": dict(fam_owner.most_common()),
            "major_blocking": {
                "unresolved_owners": sorted(s for s in owners if self.status(s)["status"] == "unresolved")[:25],
                "script_unique_owners": sorted(s for s in owners if self.status(s)["status"] == "script-unique"),
                "build_skew_owners": sum(1 for s in owners if self.status(s)["status"] == "build-skew"),
            },
        }


def spec_census(scope: Scope, resolver: StatusResolver) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for spec, spells in sorted(scope.specs_reach.items()):
        cls = scope.roots.class_of_spec.get(spec)
        label = f"{scope.roots.class_names.get(cls, cls)} / {scope.roots.spec_names.get(spec, spec)}"
        out[str(spec)] = resolver.census_for(spells, label)
    return out


def gear_census(scope: Scope, resolver: StatusResolver) -> dict[str, Any]:
    kinds = ("current-gear", "current-set", "current-gem", "current-enchant")
    out: dict[str, Any] = {}
    for kind in kinds:
        roots = {s for s, ks in scope.roots.by_spell.items() if kind in ks}
        reach: set[int] = set()
        stack = list(roots)
        while stack:
            n = stack.pop()
            if n in reach:
                continue
            reach.add(n)
            for child, _ in scope.edges.get(n, []):
                if child in scope.reach:
                    stack.append(child)
        c = resolver.census_for(reach, kind)
        c["roots"] = len(roots)
        out[kind] = c
    return out
