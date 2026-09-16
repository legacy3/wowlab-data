"""Final census: shared infrastructure versus unique exceptions.

Buckets (mutually understandable, one primary per owner, overlaps reported)::

    generic-engine        the population effect is consumed by a generic engine consumer whose behaviour is
                          fully decided by data (spell_pet_auras, spell_linked_spell, SpellAuraRestrictions
                          marker read by CheckCast, HandleProc on a DUMMY with a trigger, corrections)
    script-family         executing hooks in reusable structural families (excluding the trigger / amount /
                          target buckets below, which are split out because the brief asks for them)
    marker-state          the owner executes nothing but is observed (marker/state only)
    target-adapter        primary script role is target selection
    amount-adapter        primary script role is computing a scalar for an ordinary action
    proc-adapter          primary script role is a proc filter (DoCheckProc / DoCheckEffectProc) with an
                          ordinary trigger cast
    world-data-policy     spell_linked_spell / spell_area / conditions / spell_pet_auras rows supply the behaviour
    source-correction     LoadSpellInfoCorrections / spell_custom_attr touch the owner
    unique                executing hooks include an unclassified shape (teleport / direct damage / unrecognised
                          action), or the owner is only reached by a hardcoded engine case that normalises to no
                          family -- "a consumer exists but the research cannot reduce it to a family"
    unresolved            no consumer at all (split into build-skew and other)

Effect counts, owner counts and provider (proc) counts are all reported.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .bindings import BindingMap
from .conditions import Conditions
from .corrections import Corrections
from .families import FamilyIndex
from .hardcoded import HardcodedIndex
from .loaders import Bundle
from .markers import MarkerIndex
from .population import Population
from .scope import Scope

BUCKETS = ["generic-engine", "script-family", "target-adapter", "amount-adapter", "proc-adapter", "marker-state",
           "world-data-policy", "source-correction", "unique", "script-drift", "unresolved-build-skew", "unresolved"]
TRIGGER_FAMILIES = {"cast-child", "choose-among-children", "random-child", "delayed-child", "pet-owner-forward-cast", "consume-and-cast"}


class FinalCensus:
    def __init__(self, bundle: Bundle, scope: Scope, pop: Population, bm: BindingMap, fi: FamilyIndex, mk: MarkerIndex,
                 hc: HardcodedIndex, cd: Conditions, corr: Corrections) -> None:
        self.b, self.scope, self.pop, self.bm, self.fi, self.mk, self.hc, self.cd, self.corr = bundle, scope, pop, bm, fi, mk, hc, cd, corr
        linked = bundle.world.linked(bundle.catalog)
        self.linked_spells = {t for (_, t) in linked}
        self.pet_aura_spells = {int(r["spell"]) for r in bundle.world.table("spell_pet_auras").dicts()}
        self.area_spells = {int(r["spell"]) for r in bundle.world.table("spell_area").dicts()}
        self.cond_spells = {e for st in (13, 17, 24) for e in cd.by_source.get(st, {})}
        self.corr_spells = set(corr.by_spell) | set(bundle.proc_overlay.custom_attributes)
        self.engine_gameplay = defaultdict(list)
        for s in hc.spell_sites():
            if s.role in ("gameplay-semantic", "amount-adapter", "target-adapter"):
                self.engine_gameplay[s.value].append(s)

    def owner(self, spell_id: int) -> dict[str, Any]:
        hooks = [h for h in self.fi.for_spell(spell_id) if h.executes]
        fams = Counter(h.family for h in hooks)
        marker = self.mk.classify(spell_id)
        tags: list[str] = []
        primary = None
        if hooks:
            fset = set(fams) - {"state-only"}
            if "unclassified" in fset:
                primary = "unique"
            elif fset and fset <= {"target-adapter", "proc-filter-adapter", "area-target-filter"} and "target-adapter" in fset:
                primary = "target-adapter"
            elif fset and fset <= {"amount-adapter", "cast-child-with-amount", "proc-filter-adapter"} and (fset & {"amount-adapter", "cast-child-with-amount"}):
                primary = "amount-adapter"
            elif fset and "proc-filter-adapter" in fset and fset <= TRIGGER_FAMILIES | {"proc-filter-adapter"}:
                primary = "proc-adapter"
            elif fset:
                primary = "script-family"
            else:
                primary = "marker-state" if marker["marker_consumed"] else "script-family"
            tags.append("script")
        if spell_id in self.pet_aura_spells or spell_id in self.linked_spells or spell_id in self.area_spells or spell_id in self.cond_spells:
            tags.append("world-data")
            primary = primary or "world-data-policy"
        if marker["strong_observers"] and "db2-aura-restriction" in marker["strong_observers"]:
            tags.append("db2-marker")
            primary = primary or "generic-engine"
        if marker["marker_consumed"]:
            tags.append("marker")
            primary = primary or "marker-state"
        if spell_id in self.engine_gameplay:
            tags.append("engine-hardcoded")
            primary = primary or ("unique" if any(not s.body_calls for s in self.engine_gameplay[spell_id]) else "script-family")
        if spell_id in self.corr_spells:
            tags.append("correction")
            primary = primary or "source-correction"
        if primary is None and self.bm.bindings(spell_id):
            tags.append("script-drift")
            primary = "script-drift"
        if primary is None:
            primary = "unresolved-build-skew" if marker["newer_than_trinity"] else "unresolved"
        return {"spell_id": spell_id, "primary": primary, "tags": tags, "families": dict(fams), "hooks": len(hooks),
                "observers": marker["observer_kinds"]}

    def run(self, spells: set[int] | None = None, label: str = "all", include_bound: bool = True) -> dict[str, Any]:
        """Owners = spells with population effects, plus (``include_bound``) every script-bound
        spell in scope: a script on an ordinary SCHOOL_DAMAGE effect is server-side behaviour
        the client data does not encode even though no Dummy effect exists."""
        owner_set = {s for s in self.pop.by_spell if spells is None or s in spells}
        if include_bound:
            owner_set |= {s for s in self.bm.by_spell if spells is None or s in spells}
        owners = sorted(owner_set)
        res = {s: self.owner(s) for s in owners}
        by_owner = Counter(r["primary"] for r in res.values())
        by_effect = Counter()
        for s in owners:
            by_effect[res[s]["primary"]] += len(self.pop.by_spell.get(s, []))
        # profile of the unresolved owners: what kind of thing has no consumer at all?
        unresolved = [s for s in owners if res[s]["primary"].startswith("unresolved")]
        prof = Counter()
        for s in unresolved:
            info = self.b.catalog.get(s)
            recs = [self.pop.records[r] for r in self.pop.by_spell.get(s, [])]
            classes = {c for r in recs for c in r.classes}
            if info is not None and info.is_passive:
                prof["passive"] += 1
            if classes == {"dummy-aura"}:
                prof["dummy-aura-only"] += 1
            if any(r.base_points for r in recs if "dummy-aura" in r.classes or "dummy-effect" in r.classes):
                prof["dummy-with-nonzero-basepoints"] += 1
            if "trait-node" in self.mk.observers.get(s, {}):
                prof["trait-definition-root"] += 1
            if "class-trait" in self.scope.roots.by_spell.get(s, ()):
                prof["class-trait-root"] += 1
            if "spec-spell" in self.scope.roots.by_spell.get(s, ()):
                prof["spec-spell-root"] += 1
            if {"current-gear", "current-set", "current-gem", "current-enchant"} & self.scope.roots.by_spell.get(s, set()):
                prof["gear-root"] += 1
            if info is not None and info.proc_flags:
                prof["proc-flags"] += 1
            if "script-validate" in self.mk.observers.get(s, {}):
                prof["named-in-some-script-Validate"] += 1
            if "trigger-no-trigger" in classes:
                prof["trigger-without-trigger"] += 1
            if "periodic-dummy" in classes:
                prof["periodic-dummy"] += 1
            if "dummy-effect" in classes:
                prof["dummy-effect"] += 1
        overlaps = Counter("+".join(r["tags"]) or "none" for r in res.values())
        # distinct reusable primitives: family × sub-kind among executing hooks of these owners
        hooks = [h for h in self.fi.hooks if h.executes and h.spell_id in set(owners)]
        prim = Counter(h.family for h in hooks)
        subprim = Counter(f"{h.family}/{h.sub}" for h in hooks)
        unique_owners = sorted(s for s in owners if res[s]["primary"] == "unique")
        return {
            "label": label, "owners": len(owners), "effects": sum(len(self.pop.by_spell.get(s, [])) for s in owners),
            "owners_by_bucket": {b: by_owner[b] for b in BUCKETS if by_owner[b]},
            "effects_by_bucket": {b: by_effect[b] for b in BUCKETS if by_effect[b]},
            "tag_overlaps": dict(overlaps.most_common(20)),
            "distinct_families_in_use": len([f for f in prim if f not in ("state-only", "unclassified")]),
            "family_hook_counts": dict(prim.most_common()),
            "distinct_family_subkinds": len(subprim), "subkind_hook_counts": dict(subprim.most_common(40)),
            "unique_owner_ids": unique_owners[:50], "unique_owner_count": len(unique_owners),
            "unresolved_profile": dict(prof), "unresolved_count": len(unresolved),
            "bound_owners_without_population_effect": sum(1 for s in owners if s not in self.pop.by_spell),
        }
