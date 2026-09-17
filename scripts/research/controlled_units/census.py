"""Census: exact counts and identities over the controlled-unit population.

Everything here is a projection of :mod:`controlled_units.population` records;
no new facts are introduced.  ``census --spec ID`` prints the per-spec view.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from . import CORPORA
from .population import CATEGORY_ORDER, Context, Record, provenance
from .vocabulary import SUMMON_PROPERTIES_FLAGS, decode_summon_flags


def _ordered(counter: Counter) -> dict[str, int]:
    keys = [k for k in CATEGORY_ORDER if k in counter] + sorted(k for k in counter if k not in CATEGORY_ORDER)
    return {k: counter[k] for k in keys}


class Census:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.default: list[Record] = ctx.records(ctx.scope, "default")
        self.class_skill: list[Record] = ctx.records(ctx.extended, "class-skill", exclude=ctx.scope.reach)
        self.script_cast: list[Record] = ctx.script_cast_records()
        self.serverside = ctx.serverside_records()

    # ------------------------------------------------------------------
    def counts(self, records: list[Record]) -> dict[str, Any]:
        cat = Counter(r.category for r in records)
        cls = Counter(r.branch.get("cxx_class", "-") for r in records)
        handler = Counter(r.handler for r in records)
        tdb = Counter(r.tdb_status for r in records)
        client = Counter("present" if r.client_creature else ("absent" if r.creature_entry else "not-applicable") for r in records)
        skew = sum(1 for r in records if r.build_skew_added)
        classes: Counter = Counter()
        for r in records:
            for c in r.classes:
                classes[self.ctx.scope.roots.class_names.get(c, str(c))] += 1
        flags: Counter = Counter()
        nyi: Counter = Counter()
        slots: Counter = Counter()
        tst: Counter = Counter()
        ctrl: Counter = Counter()
        for r in records:
            if r.summon_properties:
                d = decode_summon_flags(r.summon_properties["flags"]["value"])
                for n in d["names"]:
                    flags[n] += 1
                for n in d["nyi"]:
                    nyi[n] += 1
                slots[r.summon_properties["slot_name"]] += 1
                ctrl[f"{r.summon_properties['control_name']}/{r.summon_properties['title_name']}"] += 1
            if r.branch.get("tempsummon_type"):
                tst[r.branch["tempsummon_type"]] += 1
        return {
            "records": len(records), "spells": len({r.spell_id for r in records}),
            "creature_entries": len({r.creature_entry for r in records if r.creature_entry}),
            "by_category": _ordered(cat), "by_cxx_class": dict(sorted(cls.items())), "by_handler": dict(sorted(handler.items())),
            "by_class": dict(sorted(classes.items())), "tdb_template": dict(sorted(tdb.items())), "client_creature": dict(sorted(client.items())),
            "build_skew_added_spells": skew, "control_title_pairs": dict(sorted(ctrl.items())),
            "summon_flags_seen": dict(sorted(flags.items())), "summon_flags_seen_nyi_in_trinity": dict(sorted(nyi.items())),
            "slots": dict(sorted(slots.items())), "tempsummon_types": dict(sorted(tst.items())),
        }

    def per_spec(self, records: list[Record]) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        roots = self.ctx.scope.roots
        by_spec: dict[int, list[Record]] = defaultdict(list)
        for r in records:
            for s in r.specs:
                by_spec[s].append(r)
        for spec in sorted(by_spec):
            recs = by_spec[spec]
            out[spec] = {"spec": spec, "name": roots.spec_names.get(spec, ""), "class_id": roots.class_of_spec.get(spec),
                         "class_name": roots.class_names.get(roots.class_of_spec.get(spec, 0), ""),
                         "by_category": _ordered(Counter(r.category for r in recs)),
                         "units": [{"spell": r.spell_id, "name": r.spell_name, "effect_index": r.effect_index, "category": r.category,
                                    "creature_entry": r.creature_entry, "creature_name": (r.client_creature or {}).get("name", ""),
                                    "tdb": r.tdb_status, "class": r.branch.get("cxx_class", "-"), "duration_ms": r.duration_ms,
                                    "root_kinds": r.root_kinds, "build_skew": r.build_skew_added} for r in recs]}
        return out

    def identities(self, records: list[Record]) -> list[dict[str, Any]]:
        return [{"spell": r.spell_id, "name": r.spell_name, "effect_index": r.effect_index, "category": r.category,
                 "class": r.branch.get("cxx_class", "-"), "creature_entry": r.creature_entry,
                 "creature_name": (r.client_creature or {}).get("name", ""), "summon_properties_id": r.summon_properties_id,
                 "control_title": f"{r.summon_properties['control_name']}/{r.summon_properties['title_name']}" if r.summon_properties else "",
                 "slot": r.summon_properties["slot_name"] if r.summon_properties else "", "tdb": r.tdb_status,
                 "tempsummon_type": r.branch.get("tempsummon_type"), "duration_ms": r.duration_ms, "specs": r.specs,
                 "root_kinds": r.root_kinds, "build_skew": r.build_skew_added, "evidence_class": r.evidence_class} for r in records]

    def unresolved(self, records: list[Record]) -> list[dict[str, Any]]:
        out = []
        for r in records:
            if r.category in ("unresolved", "no-consumer") or r.tdb_status == "absent":
                out.append({"spell": r.spell_id, "name": r.spell_name, "effect_index": r.effect_index, "category": r.category,
                            "creature_entry": r.creature_entry, "tdb": r.tdb_status, "build_skew": r.build_skew_added,
                            "reason": r.branch.get("notes", []) + [n for n in r.notes if n not in r.branch.get("notes", [])],
                            "absence_class": ("build-skew" if r.build_skew_added else "world-db-gap" if r.tdb_status == "absent" else "no-consumer"),
                            "reopen_condition": ("a TrinityCore commit that supports the 12.1 client (spell/creature present)" if r.build_skew_added
                                                 else "a creature_template row for the entry in a later TDB (spell predates 12.0.7; creature not authored)" if r.tdb_status == "absent"
                                                 else "a handler other than EffectNULL / a SummonProperties row for the effect")})
        return out

    def corpus(self) -> dict[str, Any]:
        return {
            "provenance": provenance("python3 controlled_units.py census", world_db=self.ctx.tdb.present),
            "default": {"counts": self.counts(self.default), "identities": self.identities(self.default),
                        "per_spec": self.per_spec(self.default), "unresolved": self.unresolved(self.default)},
            "script_cast": {"counts": self.counts(self.script_cast), "identities": self.identities(self.script_cast)},
            "class_skill": {"counts": self.counts(self.class_skill), "unresolved_count": len(self.unresolved(self.class_skill)),
                            "unresolved_by_absence_class": dict(sorted(Counter(u["absence_class"] for u in self.unresolved(self.class_skill)).items())),
                            "note": "extended scope (SkillLineAbility class lines): mounts, companions and pet-family abilities; reported separately per the brief"},
            "serverside": {"summon_effects": len(self.serverside), "by_category": _ordered(Counter(s["category"] for s in self.serverside)),
                           "referenced_from_player_scope": [s for s in self.serverside if s["referenced_by_player_hook"] or s["linked_from_player_scope"]],
                           "class_pet_rows": [s for s in self.serverside if s["effect"] == 56]},
            "summon_flags_vocabulary": {k: v[0] for k, v in SUMMON_PROPERTIES_FLAGS.items()},
        }


def register(sub) -> None:
    p = sub.add_parser("census", help="counts/identities of controlled units in current-player scope; --spec for one spec")
    p.add_argument("--spec", type=int, default=None)
    p.add_argument("--write", action="store_true", help="write controlled-unit-corpora/census.json")
    p.set_defaults(func=cmd_census)


def cmd_census(args) -> int:
    ctx = Context()
    c = Census(ctx)
    if args.spec is not None:
        view = c.per_spec(c.default).get(args.spec)
        if view is None:
            print(json.dumps({"spec": args.spec, "error": "spec not current or no controlled unit reachable", "specs": sorted(ctx.scope.roots.class_of_spec)}, indent=1))
            return 1
        print(json.dumps(view, indent=1))
        return 0
    corpus = c.corpus()
    if args.write:
        CORPORA.mkdir(parents=True, exist_ok=True)
        path = CORPORA / "census.json"
        path.write_text(json.dumps(corpus, indent=1) + "\n", encoding="utf-8")
        print(path)
    else:
        print(json.dumps({"default": corpus["default"]["counts"], "script_cast": corpus["script_cast"]["counts"],
                          "class_skill": corpus["class_skill"]["counts"], "serverside": {k: v for k, v in corpus["serverside"].items() if k != "class_pet_rows"}}, indent=1))
    return 0
