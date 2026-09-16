"""Reproducible population census of proc providers and their semantic shapes.

Population
    Every SpellID with a DIFFICULTY_NONE ``SpellInfo`` whose ``ProcFlags`` are
    nonzero, plus every ``spell_proc`` row that resolves (a row can supply
    ``ProcFlags`` itself).  Other difficulties are counted separately.

Scopes
    ``all``      -- the whole population (mostly NPC/legacy content);
    ``player``   -- providers reachable from a current class talent tree, a
                    specialization spell, or current raid/M+ gear, directly or
                    through authored trigger edges (see :mod:`procs.graph`).

Shapes are *derived* from consumer behaviour, never from names.  A provider is
"generic" only when every fact the consumer uses is either immutable source
data this package ports, or an explicit combat/actor input -- no script hook,
no condition row, no in-code correction, no unknown flag bit, and every
proccing effect lands in a generic ``HandleProc`` branch.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .definition import (
    CHANCE_CLASSIC_PPM,
    CHANCE_RPPM,
    ProcDefinition,
    ProcDefinitions,
)
from .enums import (
    ATTR3_CAN_PROC_FROM_PROCS,
    ATTR3_NOT_A_PROC,
    PPM_MOD_NAMES,
    PROC_ATTR_NAMES,
    PROC_ATTR_TRIGGERED_CAN_PROC,
    PROC_ATTR_USE_STACKS_FOR_CHARGES,
    SPELL_AURA_DUMMY,
    SPELL_AURA_PROC_TRIGGER_DAMAGE,
    TAKEN_HIT_PROC_FLAG_MASK,
    TRIGGER_SPELL_HANDLERS,
    iter_bits,
)
from .graph import ProcGraph
from .providers import OriginIndex
from .spells import DIFFICULTY_NONE

PLAYER_ROOT_KINDS = {"class-trait", "spec-spell", "current-gear"}

EVENT_FAMILIES = {
    "melee-swing": 0x4 | 0x8 | 0x400000 | 0x800000,
    "ranged-auto": 0x40 | 0x80,
    "melee-ranged-ability": 0x10 | 0x20 | 0x100 | 0x200,
    "helpful-spell-ability": 0x400 | 0x800 | 0x4000 | 0x8000,
    "harmful-spell-ability": 0x1000 | 0x2000 | 0x10000 | 0x20000,
    "periodic": 0x40000 | 0x80000 | 0x200000 | 0x80000000,
    "any-damage-taken": 0x100000,
    "kill-death": 0x2 | 0x1000000 | (0x1 << 32),
    "heartbeat": 0x1,
    "cast-lifecycle": 0x20000000 | (0x4 << 32),
    "combat-encounter": 0x8000000 | 0x10000000,
    "misc-non-combat": 0x2000000 | 0x40000000 | 0x4000000 | (0x2 << 32) | (0x10 << 32) | (0x40 << 32),
}


def action_kinds(d: ProcDefinition) -> set[str]:
    kinds = set()
    for role in d.proccing_effects:
        if role.aura in TRIGGER_SPELL_HANDLERS:
            if role.trigger_spell and role.trigger_spell_exists:
                kinds.add("trigger-spell")
            elif role.trigger_spell:
                kinds.add("trigger-spell-missing")
            elif role.aura == SPELL_AURA_DUMMY:
                kinds.add("dummy-no-trigger")
            else:
                kinds.add("proc-trigger-spell-no-trigger")
        elif role.aura == SPELL_AURA_PROC_TRIGGER_DAMAGE:
            kinds.add("direct-damage")
        elif role.handler:
            kinds.add("breakable-cc")
        else:
            kinds.add("charge-or-cooldown-consumer")
    if not kinds:
        kinds.add("no-proccing-effect")
    return kinds


def classify(d: ProcDefinition) -> dict[str, Any]:
    """One provider -> a flat record of shape facts."""
    e = d.entry
    rec: dict[str, Any] = {
        "spell_id": d.spell_id, "name": d.name, "status": d.status,
        "generation_reason": d.generation_reason,
        "has_entry": e is not None,
        "unknown_flag_bits": bool(d.unknown_proc_flag_bits),
        "script_hooks": sorted((d.scripts or {}).get("proc_hooks", [])),
        "has_script_binding": bool((d.scripts or {}).get("script_names")),
        "has_conditions": bool(d.conditions),
        "has_code_correction": bool(d.corrections),
        "passive": d.info.is_passive,
    }
    if e is None:
        rec["generic"] = False
        rec["shape"] = f"no-entry:{d.generation_reason}"
        return rec
    model = d.chance["model"]
    mods = d.info.ppm_mods if model == CHANCE_RPPM else ()
    mod_types = sorted({m["type"] for m in mods})
    actions = action_kinds(d)
    families = sorted(name for name, bits in EVENT_FAMILIES.items() if e.proc_flags & bits)
    rec.update({
        "origin": e.origin,
        "chance_model": model,
        "rppm_mod_types": [PPM_MOD_NAMES.get(t, str(t)) for t in mod_types],
        "rppm_haste": 1 in mod_types,
        "icd": e.cooldown_ms > 0,
        "charges": e.charges > 0,
        "stack_charges": bool(e.attributes_mask & PROC_ATTR_USE_STACKS_FOR_CHARGES),
        "actions": sorted(actions),
        "event_families": families,
        "taken_side": bool(e.proc_flags & TAKEN_HIT_PROC_FLAG_MASK),
        "triggered_can_proc": bool(e.attributes_mask & PROC_ATTR_TRIGGERED_CAN_PROC) or d.info.has_attr(ATTR3_CAN_PROC_FROM_PROCS),
        "hit_mask_explicit": bool(e.hit_mask),
        "family_restricted": bool(e.family_name),
        "school_restricted": bool(e.school_mask),
        "type_restricted": e.spell_type_mask not in (0, 7),
        "attributes": [PROC_ATTR_NAMES[b] for b in iter_bits(e.attributes_mask) if b in PROC_ATTR_NAMES],
        "provider_attribute_roles": sorted({a["attribute"] for a in d.provider_attributes}),
        "equip_requirement": bool(d.equipped_requirement),
    })
    bespoke = (rec["script_hooks"] or rec["has_conditions"] or rec["has_code_correction"]
               or rec["unknown_flag_bits"])
    generic_actions = actions <= {"trigger-spell", "direct-damage", "breakable-cc",
                                  "charge-or-cooldown-consumer"}
    rec["generic"] = bool(not bespoke and generic_actions)
    if not rec["generic"]:
        if rec["unknown_flag_bits"]:
            shape = "unsupported:unknown-proc-flag-bits"
        elif rec["script_hooks"]:
            shape = "script:" + "+".join(rec["script_hooks"])
        elif rec["has_conditions"] or rec["has_code_correction"]:
            shape = "trinity-overlay:conditions-or-corrections"
        elif actions <= {"dummy-no-trigger", "proc-trigger-spell-no-trigger", "no-proccing-effect"}:
            shape = "inert-only: DUMMY/PROC_TRIGGER_SPELL without trigger (no Trinity action)"
        elif "dummy-no-trigger" in actions or "proc-trigger-spell-no-trigger" in actions:
            shape = "mixed: generic action + inert DUMMY/no-trigger effect"
        elif "trigger-spell-missing" in actions:
            shape = "trigger-spell-missing"
        else:
            shape = "unsupported:" + "+".join(sorted(actions))
    else:
        parts = []
        if model == CHANCE_RPPM:
            parts.append("rppm" + ("+haste" if rec["rppm_haste"] else "")
                         + ("+other-mods" if set(mod_types) - {1} else ""))
        elif model == CHANCE_CLASSIC_PPM:
            parts.append("classic-ppm")
        else:
            parts.append(model)
        parts.append("icd" if rec["icd"] else "no-icd")
        if rec["charges"] or rec["stack_charges"]:
            parts.append("charges")
        parts.append("+".join(sorted(actions)))
        shape = " / ".join(parts)
    rec["shape"] = shape
    return rec


class Census:
    def __init__(self, defs: ProcDefinitions, origins: OriginIndex) -> None:
        self.defs = defs
        self.origins = origins
        cat = defs.catalog
        ids = set(cat.proc_flag_spells(DIFFICULTY_NONE))
        if defs.overlay is not None:
            ids |= {sid for (sid, diff) in defs.store.db if diff == DIFFICULTY_NONE}
        self.population = sorted(ids)
        self.records = {sid: classify(defs.get(sid)) for sid in self.population}
        providers = [sid for sid, r in self.records.items() if r["has_entry"]]
        self.graph = ProcGraph(defs, providers)

    # ------------------------------------------------------------------
    def player_scope(self) -> tuple[set[int], dict[int, list[str]]]:
        roots: dict[int, list[str]] = {}
        for sid in self.origins._by_spell:
            kinds = set(self.origins.origins(sid).kinds()) & PLAYER_ROOT_KINDS
            if kinds:
                roots[sid] = sorted(kinds)
        reach = set(self.graph.subgraph(roots, max_depth=8)["nodes"])
        return {s for s in reach if s in self.records}, roots

    def summary(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        def count(key):
            c = Counter()
            for r in records:
                v = r.get(key)
                if isinstance(v, list):
                    for x in v:
                        c[x] += 1
                elif v is not None:
                    c[v] += 1
            return dict(sorted(c.items(), key=lambda kv: (-kv[1], str(kv[0]))))
        with_entry = [r for r in records if r["has_entry"]]
        return {
            "providers": len(records),
            "with_entry": len(with_entry),
            "status": count("status"),
            "generation_reason_without_entry": dict(Counter(r["generation_reason"] for r in records if not r["has_entry"])),
            "generic": sum(r["generic"] for r in records),
            "chance_model": count("chance_model"),
            "rppm_haste": sum(r.get("rppm_haste", False) for r in with_entry),
            "rppm_mod_types": count("rppm_mod_types"),
            "icd": sum(r.get("icd", False) for r in with_entry),
            "charges": sum(r.get("charges", False) for r in with_entry),
            "stack_charges": sum(r.get("stack_charges", False) for r in with_entry),
            "actions": count("actions"),
            "event_families": count("event_families"),
            "taken_side": sum(r.get("taken_side", False) for r in with_entry),
            "triggered_can_proc": sum(r.get("triggered_can_proc", False) for r in with_entry),
            "script_hooks": count("script_hooks"),
            "script_bound": sum(r["has_script_binding"] for r in records),
            "conditions": sum(r["has_conditions"] for r in records),
            "code_corrections": sum(r["has_code_correction"] for r in records),
            "unknown_flag_bits": sum(r["unknown_flag_bits"] for r in records),
            "passive": sum(r["passive"] for r in records),
            "equip_requirement": sum(r.get("equip_requirement", False) for r in with_entry),
            "attributes": count("attributes"),
            "provider_attribute_roles": count("provider_attribute_roles"),
            "shapes": count("shape"),
        }

    def overlaps(self, records: list[dict[str, Any]]) -> dict[str, int]:
        c = Counter()
        for r in records:
            if not r["has_entry"]:
                continue
            flags = [k for k in ("icd", "charges", "rppm_haste", "taken_side", "triggered_can_proc") if r.get(k)]
            flags.append(r["chance_model"])
            c[" & ".join(flags)] += 1
        return dict(c.most_common(25))

    def unknown_bits(self) -> dict[str, int]:
        c = Counter()
        for sid in self.population:
            d = self.defs.get(sid)
            for bit in iter_bits(d.unknown_proc_flag_bits):
                c[f"0x{bit:016X}"] += 1
        return dict(c)

    def topology(self, scope: set[int] | None = None) -> dict[str, Any]:
        g = self.graph
        proc_edges = g.proc_edges()
        if scope is not None:
            proc_edges = [e for e in proc_edges if e.source in scope]
        by_src = defaultdict(set)
        for e in proc_edges:
            by_src[e.source].add(e.target)
        providers = [s for s, r in self.records.items() if r["has_entry"] and (scope is None or s in scope)]
        cat = self.defs.catalog
        triggered = {t for ts in by_src.values() for t in ts}
        ordinary = 0
        for t in triggered:
            info = cat.get(t)
            if info is not None and not info.proc_flags:
                ordinary += 1
        cycles = g.cycles_with_proc_edge()
        if scope is not None:
            cycles = [c for c in cycles if set(c["spells"]) & scope]
        return {
            "providers_with_entry": len(providers),
            "proc_edges": len(proc_edges),
            "providers_with_trigger": len(by_src),
            "providers_without_trigger": len(providers) - len(by_src),
            "providers_with_multiple_triggers": sum(1 for v in by_src.values() if len(v) > 1),
            "unique_triggered_spells": len(triggered),
            "triggered_spells_missing": sum(1 for t in triggered if not cat.exists(t)),
            "triggered_spells_without_proc_flags": ordinary,
            "triggered_spells_that_are_providers": sum(1 for t in triggered if t in self.records and self.records[t]["has_entry"]),
            "triggered_spells_with_NOT_A_PROC": sum(1 for t in triggered if (cat.get(t) and cat.get(t).has_attr(ATTR3_NOT_A_PROC))),
            "cycles_with_proc_edge": len(cycles),
            "self_loops": sum(1 for c in cycles if c["self_loop"]),
            "cycle_examples": cycles[:12],
        }

    def origin_counts(self, records: list[dict[str, Any]]) -> dict[str, int]:
        c = Counter()
        for r in records:
            for k in self.origins.origins(r["spell_id"]).kinds():
                c[k] += 1
            if not self.origins.origins(r["spell_id"]).kinds():
                c["no-source-root"] += 1
        return dict(c.most_common())

    def lifecycle_counts(self, records: list[dict[str, Any]]) -> dict[str, int]:
        c = Counter()
        for r in records:
            c[self.origins.lifecycle(r["spell_id"])["lifecycle"]] += 1
        return dict(c)

    def difficulty_variants(self) -> dict[str, int]:
        cat = self.defs.catalog
        c = Counter()
        for (sid, diff) in cat.aura_options:
            if diff != DIFFICULTY_NONE:
                info = cat.get(sid, diff)
                if info is not None and info.difficulty == diff:
                    c["difficulty_rows"] += 1
                    if info.proc_flags:
                        c["difficulty_rows_with_proc_flags"] += 1
                        if self.defs.store.lookup(sid, diff) is not None:
                            c["difficulty_rows_with_entry"] += 1
        return dict(c)

    def report(self) -> dict[str, Any]:
        all_records = list(self.records.values())
        scope, roots = self.player_scope()
        player_records = [self.records[s] for s in sorted(scope)]
        prov = self.defs.overlay.provenance if self.defs.overlay else None
        return {
            "population_definition": __doc__.strip().splitlines()[2:6],
            "overlay_provenance": prov,
            "spell_proc_rows_skipped": {str(k): v for k, v in self.defs.store.db_skipped.items()},
            "unknown_proc_flag_bits": self.unknown_bits(),
            "difficulty_variants": self.difficulty_variants(),
            "all": {
                "summary": self.summary(all_records),
                "overlaps": self.overlaps(all_records),
                "origins": self.origin_counts(all_records),
                "lifecycle": self.lifecycle_counts(all_records),
                "topology": self.topology(),
                "max_proc_depth": self.graph.max_proc_depth(),
            },
            "player": {
                "root_spells": len(roots),
                "summary": self.summary(player_records),
                "overlaps": self.overlaps(player_records),
                "origins": self.origin_counts(player_records),
                "lifecycle": self.lifecycle_counts(player_records),
                "topology": self.topology(scope),
                "provider_ids": sorted(scope),
            },
        }
