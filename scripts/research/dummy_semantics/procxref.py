"""Cross-reference with the proc archaeology (``docs/research/proc-pipeline-archaeology.md``).

The proc census (``procs.census.Census``) sorts every provider into shapes.  Three
of them are the "Trinity does nothing generic here" population this research
must resolve:

* ``inert-only`` -- every proccing effect is DUMMY / PROC_TRIGGER_SPELL with no trigger;
* ``mixed`` -- a generic action plus an inert effect;
* ``script`` -- an AuraScript proc hook is registered.

Each such provider is re-read through the server-side layers and classified as::

    marker-only          the aura is observed by another consumer (DB2 aura restriction, script query,
                         condition, spell_area, engine) and executes nothing itself
    reusable-family      the executing proc hooks all fall in structural families other than
                         cast-child / unclassified (amount, target, cooldown, resource, aura mutation...)
    target-adapter       primary family is target-adapter (target-select hooks)
    amount-adapter       primary family is amount-adapter / cast-child-with-amount
    ordinary-trigger     the script only casts fixed authored children on proc (cast-child, choose, random,
                         pet forward) -- the PROC_TRIGGER_SPELL shape with the trigger supplied by code
    genuinely-unique     the executing hooks include an unclassified shape
    unresolved-unbound   no script, no observer -- Trinity has no implementation
    build-skew           unresolved and the SpellID is newer than Trinity's supported build

Keyed by provider spell id **and** by (spell, effect index) so effect identity
stays distinct from the owner.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from procs.census import Census, classify as proc_classify
from procs.definition import ProcDefinitions
from procs.providers import OriginIndex

from .bindings import BindingMap
from .families import FamilyIndex
from .loaders import Bundle
from .markers import MarkerIndex

TRIGGER_FAMILIES = {"cast-child", "choose-among-children", "random-child", "delayed-child", "pet-owner-forward-cast", "consume-and-cast"}
AMOUNT_FAMILIES = {"amount-adapter", "cast-child-with-amount"}
REUSABLE_OTHER = {"linked-aura-mutation", "cooldown-mutation", "resource-mutation", "summon", "suppress-default", "proc-filter-adapter",
                  "cast-gate", "area-target-filter"}


class ProcCrossReference:
    def __init__(self, bundle: Bundle, bindings: BindingMap, families: FamilyIndex, markers: MarkerIndex) -> None:
        self.b = bundle
        self.bm = bindings
        self.fi = families
        self.mk = markers
        defs = ProcDefinitions(bundle.catalog, bundle.proc_overlay)
        origins = OriginIndex(bundle.source, defs)
        self.census = Census(defs, origins)
        self.player_providers, self.player_roots = self.census.player_scope()

    def proc_bucket(self, rec: dict[str, Any]) -> str:
        shape = rec["shape"]
        if shape.startswith("inert-only"):
            return "inert-only"
        if shape.startswith("mixed"):
            return "mixed"
        if shape.startswith("script:"):
            return "script"
        return "other"

    def resolve(self, spell_id: int) -> dict[str, Any]:
        rec = self.census.records[spell_id]
        d = self.census.defs.get(spell_id)
        bucket = self.proc_bucket(rec)
        hooks = [h for h in self.fi.for_spell(spell_id) if h.executes]
        fams = Counter(h.family for h in hooks)
        marker = self.mk.classify(spell_id)
        bindings = self.bm.bindings(spell_id)
        inert_effects = [r.index for r in d.effects if r.aura in (4, 42) and not r.trigger_spell] if d else []
        if hooks:
            fam_set = set(fams)
            if "unclassified" in fam_set:
                cls = "genuinely-unique"
            elif fam_set <= TRIGGER_FAMILIES | {"proc-filter-adapter", "state-only"} and fam_set & TRIGGER_FAMILIES:
                cls = "ordinary-trigger"
            elif fam_set & AMOUNT_FAMILIES and not fam_set & TRIGGER_FAMILIES:
                cls = "amount-adapter"
            elif fam_set <= {"target-adapter", "state-only", "proc-filter-adapter"} and "target-adapter" in fam_set:
                cls = "target-adapter"
            elif fam_set <= {"state-only"}:
                cls = "marker-only" if marker["marker_consumed"] else "state-only-script"
            else:
                cls = "reusable-family"
        elif bindings:
            cls = "unresolved-bound-no-executing-hook"
        elif marker["marker_consumed"]:
            cls = "marker-only"
        elif marker["newer_than_trinity"]:
            cls = "build-skew"
        else:
            cls = "unresolved-unbound"
        return {
            "spell_id": spell_id, "name": rec["name"], "proc_shape": rec["shape"], "proc_bucket": bucket,
            "player": spell_id in self.player_providers, "classification": cls,
            "families": dict(fams), "executing_hooks": len(hooks),
            "children": sorted({c for h in hooks for c in h.children}),
            "scripts": sorted({b.script_name for b in bindings}), "unresolved_scripts": sorted({b.script_name for b in bindings if not b.resolved}),
            "marker_observers": marker["observer_kinds"], "newer_than_trinity": marker["newer_than_trinity"],
            "inert_effect_indices": inert_effects,
            "effects": [{"effect": f"{spell_id}:{r.index}", "aura": r.aura, "trigger_spell": r.trigger_spell,
                         "handler": r.handler, "disabled": r.disabled} for r in (d.effects if d else [])],
        }

    def resolve_all(self, player_only: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {"providers": {}, "summary": {}}
        pop = [sid for sid, r in self.census.records.items() if self.proc_bucket(r) != "other"
               and (not player_only or sid in self.player_providers)]
        by_bucket: dict[str, Counter] = defaultdict(Counter)
        effects_by_bucket: dict[str, Counter] = defaultdict(Counter)
        for sid in pop:
            res = self.resolve(sid)
            out["providers"][sid] = res
            by_bucket[res["proc_bucket"]][res["classification"]] += 1
            effects_by_bucket[res["proc_bucket"]][res["classification"]] += len(res["inert_effect_indices"]) or 1
        out["summary"] = {
            "population": len(pop), "player_only": player_only,
            "proc_buckets": {b: sum(c.values()) for b, c in by_bucket.items()},
            "classification_by_bucket": {b: dict(c.most_common()) for b, c in by_bucket.items()},
            "classification_total": dict(sum(by_bucket.values(), Counter()).most_common()),
            "inert_effects_by_bucket": {b: dict(c.most_common()) for b, c in effects_by_bucket.items()},
            "proc_census_player_providers": len(self.player_providers),
        }
        return out
