"""Current-player targeting census (lead).

Joins the per-effect ``effect_classes`` maps of every track corpus (BRIEF §10)
over the current-player effect population and counts, globally and per spec:

* effects by selector (TargetA, TargetB, pair);
* simple understood targeting (every track verdict ``understood`` and no
  complex tag);
* area / group / chain / smart / script-adapter / random / controlled-unit /
  world-condition / destination / geometry-dependent families;
* build skew and unresolved consumers;
* targeting closure x payload proxy, so a reader can tell "payload understood,
  targeting blocked" from "targeting understood, payload blocked".

Targeting closure of one effect = the worst verdict any track gives it
(``unresolved`` > ``blocked`` > ``fixture-dependent`` > ``understood-with-defect``
> ``understood``; ``n/a`` is ignored).  A track that does not list an effect
has no opinion on it.  Track A lists every effect, so every effect has at least
one verdict; a missing A row is a census defect (FailClosed).

The payload axis is a **proxy**, not payload research: it says whether the
pinned Trinity has a non-null generic handler for the effect / aura type, and,
for Dummy / ScriptEffect / Dummy-aura effects, the Dummy-pass owner bucket.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from . import CORPORA, PINS, FailClosed
from .context import Context

VERDICT_ORDER = ("understood", "understood-with-defect", "fixture-dependent", "blocked", "unresolved")
TRACK_CORPORA = {
    "A": ("selectors.json",),
    "B": ("explicit-validation.json",),
    "C": ("area.json", "geometry.json", "caps.json"),
    "D": ("chains.json", "smart-selection.json", "rng.json"),
    "E": ("script-adapters.json", "world-policy.json"),
    "F": ("group-policy.json", "effect-recipients.json"),
    "I": ("areatriggers.json",),
    "J": ("aura-targets.json",),
}
#: every build-skew row cites this reopen pointer (hostile review R1-01)
BUILD_SKEW_UNKNOWN = "TG-H-04"
#: population-extension layer (script / AreaTrigger / linked children outside reach); never merged into the main census
EXTENDED_CORPORA = {
    "I": ("extended-scope.json", "extended_effect_classes"),
    "E-adapters": ("script-adapters.json", "effect_classes_extended"),
    "E-world": ("world-policy.json", "effect_classes_extended"),
}
FAMILY_TAGS = {
    "area": {"area", "cone", "line", "traj"},
    "cone-line-traj": {"cone", "line", "traj"},
    "group": {"party", "raid", "raid-class", "group", "caster-and-summons", "ally-or-raid"},
    "chain": {"chain", "chain-heal"},
    "smart": {"smart"},
    "script-adapter": {"script-adapter"},
    "random": {"random-cap", "random", "random-dest", "rng"},
    "controlled-unit": {"controlled-unit", "pet", "master", "summoner", "owner"},
    "world-condition": {"condition", "world-db", "spell-target-position", "linked-spell"},
    "destination": {"dest", "src"},
    "world-geometry": {"world-geometry", "collision", "los"},
    "areatrigger": {"areatrigger"},
    "aura-target-map": {"aura-target-map"},
}
DUMMY_EFFECTS = {3, 77}  # SPELL_EFFECT_DUMMY, SPELL_EFFECT_SCRIPT_EFFECT
DUMMY_AURA = 4  # SPELL_AURA_DUMMY
NULL_HANDLERS = {"EffectNULL", "EffectUnused", "HandleNULL", "HandleUnused", "?"}


def _worst(verdicts: list[str]) -> str:
    ranked = [v for v in verdicts if v in VERDICT_ORDER]
    unknown = [v for v in verdicts if v not in VERDICT_ORDER and v != "n/a"]
    if unknown:
        raise FailClosed(f"census: unknown verdict(s) {unknown}")
    if not ranked:
        return "n/a"
    return max(ranked, key=VERDICT_ORDER.index)


def load_effect_classes() -> dict[str, dict[str, list[dict[str, Any]]]]:
    """``{track: {"spell:eff": [row, ...]}}`` from the committed corpora."""
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for track, files in TRACK_CORPORA.items():
        merged: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for name in files:
            path = CORPORA / name
            if not path.exists():
                raise FailClosed(f"census: missing corpus {name} (track {track})")
            doc = json.loads(path.read_text(encoding="utf-8"))
            for key, row in doc.get("effect_classes", {}).items():
                merged[key].append({**row, "_corpus": name})
        out[track] = dict(merged)
    return out


def extended_layer() -> dict[str, Any]:
    """Children outside ``ctx.scope.reach`` (TG-D-13 / TG-I-10): counted separately, by source tier."""
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for label, (name, key) in EXTENDED_CORPORA.items():
        path = CORPORA / name
        if not path.exists():
            raise FailClosed(f"census: missing corpus {name}")
        doc = json.loads(path.read_text(encoding="utf-8"))
        for k, row in doc.get(key, {}).items():
            rows[k].append({**row, "_source": label})
    per: dict[str, dict[str, Any]] = {}
    for k, rs in rows.items():
        tags = sorted({t for r in rs for t in r.get("tags", [])})
        per[k] = {"closure": _worst([r["class"] for r in rs]), "sources": sorted({r["_source"] for r in rs}),
                  "tiers": sorted({r.get("tier", "extended-scope") for r in rs}), "tags": tags,
                  "families": sorted(f for f, ft in FAMILY_TAGS.items() if set(tags) & ft),
                  "build_skew": any(r.get("build_skew") is True for r in rs)}
    keys = sorted(per, key=lambda k: tuple(int(x) for x in k.split(":")))
    return {
        "note": "population extension only; NOT part of `global` / `per_spec` (see TG-D-13, TG-I-10)",
        "effects": len(keys),
        "spells": len({k.split(":")[0] for k in keys}),
        "by_tier": dict(sorted(Counter(t for k in keys for t in per[k]["tiers"]).items())),
        "by_closure": dict(sorted(Counter(per[k]["closure"] for k in keys).items())),
        "families": dict(sorted(Counter(f for k in keys for f in per[k]["families"]).items())),
        "build_skew": sum(per[k]["build_skew"] for k in keys),
        "rows": {k: per[k] for k in keys},
    }


def payload_proxy(ctx: Context, dummy_buckets: dict[int, str], spell: int, eff) -> str:
    """Mirrors (navigation only): ``SpellEffectHandlers`` / ``AuraEffectHandler`` tables
    extracted by ``tools/tc_dispatch_tables.py``; Dummy owners by the Dummy census."""
    disp = ctx.bundle.dispatch
    if eff.effect in DUMMY_EFFECTS or (eff.aura == DUMMY_AURA and _is_aura(ctx, eff)):
        bucket = dummy_buckets.get(spell)
        if bucket is None:
            return "dummy-no-owner-row"
        return "dummy-no-consumer" if bucket.startswith("unresolved") else f"dummy-{bucket}"
    handler = disp.effect_handler(eff.effect)
    if handler in NULL_HANDLERS:
        return "effect-null-handler"
    if _is_aura(ctx, eff):
        ah = disp.aura_handler(eff.aura)["handler"]
        if ah in NULL_HANDLERS:
            return "aura-null-handler"
        return "aura-handler"
    return "effect-handler"


def _is_aura(ctx: Context, eff) -> bool:
    from procs.enums import is_aura_effect
    return is_aura_effect(eff.effect, eff.aura)


def _dummy_buckets(ctx: Context) -> dict[int, str]:
    """Per-owner primary bucket, recomputed with the Dummy census code (not persisted per owner).

    Uses the Dummy pass objects over *this* process's bundle and scope, so the
    ``TargetHook::CheckEffect`` port (track E) is reflected."""
    from dummy_semantics.bindings import BindingMap
    from dummy_semantics.census import FinalCensus
    from dummy_semantics.conditions import Conditions
    from dummy_semantics.corrections import Corrections
    from dummy_semantics.families import FamilyIndex
    from dummy_semantics.hardcoded import HardcodedIndex
    from dummy_semantics.markers import MarkerIndex
    from dummy_semantics.population import Population
    b = ctx.bundle
    pop = Population(b)
    bm = BindingMap(b)
    fi = FamilyIndex(bm)
    hc = HardcodedIndex(b)
    mk = MarkerIndex(b, pop, bm, hc)
    fc = FinalCensus(b, ctx.scope, pop, bm, fi, mk, hc, Conditions(b), Corrections(b))
    owners = (set(pop.by_spell) | set(bm.by_spell)) & ctx.scope.reach
    return {s: fc.owner(s)["primary"] for s in owners}


def population(ctx: Context) -> list[tuple[int, Any]]:
    rows = []
    for spell in sorted(ctx.scope.reach):
        for eff in ctx.data.effects(spell):
            if eff.is_effect:
                rows.append((spell, eff))
    return rows


def build(ctx: Context) -> dict[str, Any]:
    classes = load_effect_classes()
    dummy_buckets = _dummy_buckets(ctx)
    pop = population(ctx)
    spec_of: dict[int, set[int]] = defaultdict(set)
    for spec, spells in ctx.scope.specs_reach.items():
        for s in spells:
            spec_of[s].add(spec)

    per_effect: dict[str, dict[str, Any]] = {}
    for spell, eff in pop:
        key = f"{spell}:{eff.index}"
        if key not in classes["A"]:
            raise FailClosed(f"census: track A has no row for {key}")
        verdicts: dict[str, str] = {}
        tags: set[str] = set()
        unknowns: set[str] = set()
        for track, rows in classes.items():
            if key not in rows:
                continue
            verdicts[track] = _worst([r["class"] for r in rows[key]])
            for r in rows[key]:
                tags.update(r.get("tags", []))
                unknowns.update(r.get("unknowns", []))
        skew = ctx.is_skew(spell)
        if skew:
            unknowns.add(BUILD_SKEW_UNKNOWN)
        closure = _worst(list(verdicts.values()))
        families = sorted(f for f, ftags in FAMILY_TAGS.items() if tags & ftags)
        complex_tags = set().union(*FAMILY_TAGS.values())
        simple = closure == "understood" and not (tags & complex_tags)
        per_effect[key] = {
            "spell": spell, "effect": eff.index, "selectors": [eff.target_a, eff.target_b],
            "closure": closure, "verdicts": verdicts, "families": families, "tags": sorted(tags),
            "unknowns": sorted(unknowns), "build_skew": skew, "simple": simple,
            "payload_proxy": payload_proxy(ctx, dummy_buckets, spell, eff),
            "specs": sorted(spec_of.get(spell, ())),
        }

    def summarize(keys: list[str]) -> dict[str, Any]:
        rows = [per_effect[k] for k in keys]
        fam = Counter(f for r in rows for f in r["families"])
        payload_x_target = Counter(f"{r['payload_proxy']}|{r['closure']}" for r in rows)
        return {
            "effects": len(rows),
            "spells": len({r["spell"] for r in rows}),
            "by_closure": dict(sorted(Counter(r["closure"] for r in rows).items())),
            "simple_understood": sum(r["simple"] for r in rows),
            "families": dict(sorted(fam.items())),
            "build_skew": sum(r["build_skew"] for r in rows),
            "unresolved": sum(r["closure"] == "unresolved" for r in rows),
            "with_unknown_ids": sum(bool(r["unknowns"]) for r in rows),
            "by_target_a": dict(sorted(Counter(str(r["selectors"][0]) for r in rows).items(), key=lambda kv: int(kv[0]))),
            "by_target_b": dict(sorted(Counter(str(r["selectors"][1]) for r in rows).items(), key=lambda kv: int(kv[0]))),
            "by_pair": dict(sorted(Counter(f"{a},{b}" for a, b in (r["selectors"] for r in rows)).items(),
                                   key=lambda kv: tuple(int(x) for x in kv[0].split(",")))),
            "payload_proxy_x_targeting": dict(sorted(payload_x_target.items())),
        }

    keys = sorted(per_effect, key=lambda k: tuple(int(x) for x in k.split(":")))
    per_spec = {}
    for spec in sorted(ctx.scope.specs_reach):
        spec_keys = [k for k in keys if spec in per_effect[k]["specs"]]
        s = summarize(spec_keys)
        for noisy in ("by_target_a", "by_target_b", "payload_proxy_x_targeting"):
            s.pop(noisy)
        per_spec[str(spec)] = {"name": ctx.scope.roots.spec_names.get(spec, ""),
                               "class": ctx.scope.roots.class_names.get(ctx.scope.roots.class_of_spec.get(spec, 0), ""),
                               **s}
    return {
        "provenance": {**PINS, "command": "python3 targeting.py census --out ../../docs/research/targeting-corpora/census.json",
                       "population": "IsEffect() SpellEffect rows at DIFFICULTY_NONE of ctx.scope.reach "
                                     "(dummy_semantics.scope.Scope default: class trees, spec spells, current gear/sets/gems/enchants + authored reach)",
                       "closure_rule": "worst track verdict; " + " < ".join(VERDICT_ORDER),
                       "payload_proxy": "generic handler presence (dispatch tables) / Dummy-pass owner bucket; NOT payload research",
                       "corpora": {t: list(f) for t, f in TRACK_CORPORA.items()}},
        "global": summarize(keys),
        "extended": extended_layer(),
        "per_spec": per_spec,
        "effects": {k: per_effect[k] for k in keys},
    }
