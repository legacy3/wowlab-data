"""Marker / state-only Dummy auras: who reads an aura that executes nothing.

A DUMMY (or other inert) aura is *meaningful* when something else observes it.
Observers the corpora can show:

``db2-aura-restriction``  another spell's ``SpellAuraRestrictions.{Caster,Target}AuraSpell`` /
                          ``Exclude*AuraSpell`` names it (``SpellInfo::CheckCast`` gates) --
                          an authored, client-visible marker consumer;
``script-aura-query``     a script handler calls ``HasAura/GetAura/GetAuraEffect/...(<id>)`` on it; each hit carries a
                          ``role``: ``load-gate`` (inside ``Load()``: the whole script is inert without the aura),
                          ``proc-gate`` (inside a DoCheck* predicate), ``amount-read`` (``GetAuraEffect(...)->GetAmount``
                          in the same handler) or ``presence-read`` (any other handler);
``script-aura-remove``    a script removes it (``RemoveAurasDueToSpell/RemoveAura(<id>)``);
``script-own-amount``     the aura's own script reads ``aurEff->GetAmount()`` (tooltip-value holder);
``script-validate``       a script lists it in ``ValidateSpellInfo`` (weak: navigation only);
``condition-aura``        a ``conditions`` row of type CONDITION_AURA names it (any source type: a gossip / smart-script /
                          loot condition that reads a player aura is still a server-side state read);
``spell-area``            ``spell_area.aura_spell`` names it;
``spell-linked``          ``spell_linked_spell`` names it as trigger or effect;
``engine-hardcoded``      an engine ``HasAura(<id>)`` / case label in a gameplay-semantic function names it
                          (``engine-classification``: load-time classification sites such as _LoadSpellSpecific, weak);
``proc-provider``         it carries ProcFlags (the aura is a proc carrier; the proc research owns that);
``trait-node``            a trait definition points at it (acquisition marker, no runtime read).

A Dummy aura with no observer at all is *unobserved*: either build skew, dead
data, or a client-side consumer -- recorded as unresolved, never assumed inert.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from procs.spells import DIFFICULTY_NONE

from .bindings import BindingMap
from .hardcoded import HardcodedIndex
from .loaders import Bundle
from .population import Population

AURA_QUERY = {"HasAura", "GetAura", "GetAuraEffect", "HasAuraEffect", "GetAuraOfRankedSpell", "GetAuraEffectOfRankedSpell",
              "GetAuraApplication", "GetAuraCount", "GetAuraApplicationOfRankedSpell", "GetOwnedAura", "GetAuraEffectDummy"}
AURA_REMOVE = {"RemoveAura", "RemoveAurasDueToSpell", "RemoveOwnedAura", "RemoveAurasByType", "RemoveAuraFromStack"}
OWN_AMOUNT = {"GetAmount", "GetAmountAsInt", "GetBaseAmount"}


class MarkerIndex:
    def __init__(self, bundle: Bundle, population: Population, bindings: BindingMap, hardcoded: HardcodedIndex) -> None:
        self.b = bundle
        self.pop = population
        self.bm = bindings
        self.hc = hardcoded
        self.observers: dict[int, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
        self._build()

    def _build(self) -> None:
        src = self.b.source
        cat = self.b.catalog
        # DB2: SpellAuraRestrictions
        cols = ("SpellID", "DifficultyID", "CasterAuraSpell", "TargetAuraSpell", "ExcludeCasterAuraSpell", "ExcludeTargetAuraSpell")
        for row in src.iter_dicts("SpellAuraRestrictions", cols):
            for c in cols[2:]:
                v = int(row[c])
                if v:
                    self.observers[v]["db2-aura-restriction"].append({"spell": row["SpellID"], "field": c})
        # scripts
        idx = self.b.index
        for key, sc in idx.classes.items():
            if sc.kind not in ("SpellScript", "AuraScript", "AreaTriggerAI"):
                continue
            own = sorted(self.bm.script_spells.get(sc.name, set()))
            for mname, facts in sc.methods.items():
                calls = facts.get("calls", [])
                amount_read = any(c["callee"] in ("GetAmount", "GetAmountAsInt") for c in calls)
                if mname == "Load":
                    role = "load-gate"
                elif mname.startswith(("Check", "DoCheck")) or "Check" in mname:
                    role = "proc-gate"
                else:
                    role = "presence-read"
                for c in calls:
                    ints = [v for v in c.get("ints", []) if v >= 100 and cat.exists(v)]
                    if c["callee"] in AURA_QUERY:
                        r = "amount-read" if (amount_read and c["callee"] in ("GetAuraEffect", "GetAuraEffectOfRankedSpell", "GetAura")) else role
                        for v in ints:
                            self.observers[v]["script-aura-query"].append({"class": sc.name, "file": sc.file, "method": mname, "line": c["line"], "role": r, "bound_spells": own[:6]})
                    elif c["callee"] in AURA_REMOVE:
                        for v in ints:
                            self.observers[v]["script-aura-remove"].append({"class": sc.name, "file": sc.file, "method": mname, "line": c["line"], "bound_spells": own[:6]})
                    elif c["callee"] in OWN_AMOUNT and c.get("recv", "").startswith(("aurEff", "GetEffect", "aura")):
                        for s in own:
                            self.observers[s]["script-own-amount"].append({"class": sc.name, "method": mname, "line": c["line"]})
            if sc.validate:
                for v in sc.validate.get("spells", []):
                    if cat.exists(v):
                        self.observers[v]["script-validate"].append({"class": sc.name, "file": sc.file})
        # conditions CONDITION_AURA
        for st, entries in self.b.world.conditions_by_source().items():
            for entry, rows in entries.items():
                for r in rows:
                    if int(r["ConditionTypeOrReference"]) == 1:
                        self.observers[int(r["ConditionValue1"])]["condition-aura"].append({"source_type": st, "source_entry": entry})
        # spell_area
        for row in self.b.world.table("spell_area").dicts():
            if row["aura_spell"]:
                self.observers[abs(int(row["aura_spell"]))]["spell-area"].append({"spell": row["spell"], "area": row["area"]})
        # spell_linked_spell
        for (typ, trigger), effects in self.b.world.linked(cat).items():
            self.observers[trigger]["spell-linked"].append({"role": "trigger", "type": typ, "effects": effects})
            for e in effects:
                self.observers[abs(e)]["spell-linked"].append({"role": "effect", "type": typ, "trigger": trigger})
        # engine hardcoded: only gameplay/amount/target consumers are strong; classification sites are weak
        for s in self.hc.spell_sites():
            key = "engine-hardcoded" if s.role in ("gameplay-semantic", "amount-adapter", "target-adapter", "cast-validation") else "engine-classification"
            self.observers[s.value][key].append({"function": s.function, "line": s.line, "kind": s.kind, "role": s.role})
        # proc provider
        for sid in cat.proc_flag_spells(DIFFICULTY_NONE):
            self.observers[sid]["proc-provider"].append(True)
        # trait definitions
        for d, s in src.project("TraitDefinition", ("ID", "SpellID")):
            if s:
                self.observers[s]["trait-node"].append(d)

    # ------------------------------------------------------------------
    def observers_of(self, spell_id: int) -> dict[str, list[Any]]:
        return dict(self.observers.get(spell_id, {}))

    def classify(self, spell_id: int) -> dict[str, Any]:
        obs = self.observers_of(spell_id)
        kinds = {k for k, v in obs.items() if v}
        strong = kinds & {"db2-aura-restriction", "script-aura-query", "script-aura-remove", "condition-aura", "spell-area", "spell-linked", "engine-hardcoded"}
        roles = Counter(o.get("role", "?") for o in obs.get("script-aura-query", []))
        return {"spell_id": spell_id, "observer_kinds": sorted(kinds), "strong_observers": sorted(strong),
                "script_query_roles": dict(roles),
                "marker_consumed": bool(strong), "own_amount_read": "script-own-amount" in kinds,
                "proc_provider": "proc-provider" in kinds,
                "newer_than_trinity": self.b.skew.is_newer_than_trinity(spell_id)}

    def census(self, spells: set[int] | None = None) -> dict[str, Any]:
        recs = [r for r in self.pop.records.values() if "dummy-aura" in r.classes and (spells is None or r.spell_id in spells)]
        owners = sorted({r.spell_id for r in recs})
        kinds = Counter()
        roles = Counter()
        consumed = 0
        unobserved = []
        for s in owners:
            c = self.classify(s)
            for k in c["observer_kinds"]:
                kinds[k] += 1
            for r in c["script_query_roles"]:
                roles[r] += 1
            if c["marker_consumed"]:
                consumed += 1
            elif not self.bm.bindings(s):
                unobserved.append(s)
        return {"dummy_aura_effects": len(recs), "dummy_aura_owners": len(owners), "observer_kind_counts": dict(kinds.most_common()),
                "script_query_role_owner_counts": dict(roles.most_common()),
                "marker_consumed_owners": consumed,
                "unobserved_and_unbound_owners": len(unobserved),
                "unobserved_newer_than_trinity": sum(1 for s in unobserved if self.b.skew.is_newer_than_trinity(s)),
                "unobserved_examples": unobserved[:30]}
