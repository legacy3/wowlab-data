"""Track J: lifecycle signatures -- group providers by lifecycle shape, not by name.

A *lifecycle signature* is the canonical tuple of a provider spell's lifecycle
axes (:data:`census.SPELL_AXES`) plus the sorted multiset of its provider
effects' axes (:data:`census.EFFECT_AXES`).  The *exact* granularity adds the raw
``EffectAura`` of every provider effect.

Three granularities (:data:`GRANULARITIES`): ``path`` (``LP-``, coarse authored-input
grouping), ``full`` (``LS-``) and ``exact`` (``LX-``, raw subtypes).
Signature ids are derived from the tuple only: prefix + the first 12
hex digits of the SHA-256 of the canonical JSON (sorted keys, no whitespace).
Ids are therefore stable across runs and independent of enumeration order; a
change of any axis value changes the id (that is what :mod:`drift` detects).

Core columns are **navigation only** (``core-navigation`` evidence): they read
Core's own catalogs (``crates/dbc/src/aura_subtype.rs``,
``crates/dbc/src/spell_attribute.rs`` via :mod:`selected_package.coreref`) and
say nothing about Core's lifecycle behaviour.  Core's ``implemented`` rows are
often narrowed to single witnesses (e.g. ``HasteAffectsDuration`` = spell 391428
only), so "catalog-admissible" is an upper bound on representation, never support.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any

from . import census


def canonical(sig: dict[str, Any]) -> str:
    return json.dumps(sig, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _norm(v: Any) -> Any:
    if isinstance(v, tuple):
        return [_norm(x) for x in v]
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v


GRANULARITIES = {"path": "LP-", "full": "LS-", "exact": "LX-"}

#: Spell axes kept by the coarse ``path`` granularity.  ``path`` is a *coarse authored-input
#: grouping*, not a claim that members share a Trinity code path (R1-07: the lead semantic
#: axes split 53/127 multi-member player path classes); value-level detail such as dispel
#: type, restriction columns or AuraOptions shape is folded.
PATH_AXES = ("passive", "duration", "channel", "stack_capacity", "proc_charges", "attrs")


def signature(rec: dict[str, Any], granularity: str = "full") -> dict[str, Any]:
    """The canonical signature dict of a :func:`census.spell_record`.

    * ``full``  -- every spell axis + the sorted multiset of effect tuples
    * ``exact`` -- ``full`` with the raw ``EffectAura`` appended to each effect tuple
    * ``path``  -- :data:`PATH_AXES` + aura-interrupt / dispellable booleans + the
      externally touched surface *names* (policy source folded) + the
      *set* of (kind, class, period, points) effect tuples (implicit target and multiplicity folded)
    """
    a = rec["axes"]
    if granularity == "path":
        return {"axes": {**{k: _norm(a[k]) for k in PATH_AXES},
                         "aura_interrupt": "aura" in a["interrupts"],
                         "dispellable": bool(a["dispel"][0]),
                         "external": sorted({x.split(":", 1)[0] for x in a["external"]})},
                "effects": [list(t) for t in sorted({(e[0], e[1], e[2], e[4]) for e in rec["effects"]})]}
    if granularity not in ("full", "exact"):
        raise ValueError(granularity)
    return {"axes": {k: _norm(a[k]) for k in census.SPELL_AXES},
            "effects": _norm(rec["effects_exact" if granularity == "exact" else "effects"])}


def signature_id(sig: dict[str, Any], granularity: str = "full") -> str:
    digest = hashlib.sha256(canonical(sig).encode("ascii")).hexdigest()[:12]
    return GRANULARITIES[granularity] + digest


def record_ids(rec: dict[str, Any]) -> dict[str, str]:
    return {g: signature_id(signature(rec, g), g) for g in GRANULARITIES}


# ---------------------------------------------------------------------------
# Core navigation (catalog reads, never support claims)
# ---------------------------------------------------------------------------


def core_catalogs() -> tuple[dict[int, str], dict[int, str]] | None:
    """(aura subtype raw -> support, attribute raw -> support) or ``None`` when Core is absent."""
    try:
        from selected_package import coreref
        subs = {raw: s.support for raw, s in coreref.aura_subtypes().items()}
        attrs = {raw: s.support for raw, s in coreref.spell_attributes().items()}
    except Exception:   # noqa: BLE001 -- Core checkout absent / catalog moved: navigation is optional
        return None
    return subs, attrs


ADMISSIBLE_ATTR_SUPPORT = ("implemented", "ignored")


def core_view(rec: dict[str, Any], catalogs: tuple[dict[int, str], dict[int, str]]) -> dict[str, Any]:
    """Core catalog classification of one provider spell (``core-navigation``).

    * ``subtypes``: support class of every provider subtype (``unknown`` = not in Core's catalog)
    * ``attr_support``: support class of every lifecycle attribute present
    * ``catalog_admissible``: every subtype ``implemented`` and every lifecycle attribute
      ``implemented``/``ignored`` -- an upper bound, not support
    * ``lifecycle_only_blocked``: every subtype ``implemented`` but at least one lifecycle
      attribute is ``disabled``/``unimplemented``/``unknown`` (the blocker is lifecycle, not the subtype)
    """
    subs, attrs = catalogs
    sub_support = sorted({subs.get(e[-1], "unknown") for e in rec["effects_exact"]})
    # server-derived labels (CU_*) have no DB2 bit / Core raw: they are not Core catalog questions
    attr_support = {label: attrs.get(census.ATTR_BY_LABEL[label].core_raw, "unknown")
                    for label in rec["axes"]["attrs"] if label in census.ATTR_BY_LABEL}
    subtypes_ok = sub_support == ["implemented"]
    attr_block = sorted(k for k, v in attr_support.items() if v not in ADMISSIBLE_ATTR_SUPPORT)
    return {"subtypes": sub_support, "attr_support": attr_support,
            "catalog_admissible": subtypes_ok and not attr_block,
            "lifecycle_only_blocked": subtypes_ok and bool(attr_block),
            "lifecycle_blockers": attr_block}


# ---------------------------------------------------------------------------
# grouping
# ---------------------------------------------------------------------------


def group(records: list[dict[str, Any]], pops: dict[str, frozenset[int]], *, skew=lambda s: False,
          catalogs=None, granularity: str = "full", member_pops: tuple[str, ...] = ("player", "controlled"),
          witnesses: int = 5) -> list[dict[str, Any]]:
    """Signature catalog: one entry per distinct signature, sorted by id.

    Members are listed in full for ``member_pops``; for ``all`` only counts and
    the lowest ``witnesses`` spell ids are kept (the census is regenerable).
    """
    by: dict[str, dict[str, Any]] = {}
    members: dict[str, list[int]] = defaultdict(list)
    for rec in records:
        sig = signature(rec, granularity)
        sid = signature_id(sig, granularity)
        if sid not in by:
            by[sid] = {"id": sid, "signature": sig}
        members[sid].append(rec["spell"])
    view = {rec["spell"]: core_view(rec, catalogs) for rec in records} if catalogs else {}
    durations = {rec["spell"]: rec["duration_ms"] for rec in records}
    out = []
    for sid in sorted(by):
        spells = sorted(members[sid])
        entry = by[sid]
        entry["counts"] = {p: sum(1 for s in spells if s in pop) for p, pop in pops.items()}
        entry["build_skew"] = sum(1 for s in spells if skew(s))
        entry["witnesses"] = spells[:witnesses]
        for p in member_pops:
            entry[p + "_members"] = [s for s in spells if s in pops[p]]
        dur = Counter(str(durations[s]) for s in spells)
        entry["duration_ms_top"] = dict(sorted(dur.most_common(5)))
        if view:
            v = [view[s] for s in spells]
            entry["core"] = {
                "catalog_admissible": {p: sum(1 for s in spells if s in pop and view[s]["catalog_admissible"])
                                       for p, pop in pops.items()},
                "lifecycle_only_blocked": {p: sum(1 for s in spells if s in pop and view[s]["lifecycle_only_blocked"])
                                           for p, pop in pops.items()},
                "attr_support": dict(sorted(v[0]["attr_support"].items())),   # identical across members
                "subtype_support": dict(sorted(Counter("+".join(x["subtypes"]) for x in v).items())),
            }
        out.append(entry)
    return out


def distribution(catalog: list[dict[str, Any]], pop: str) -> dict[str, Any]:
    """Common/rare structure of a signature catalog within one population."""
    sizes = sorted((e["counts"][pop] for e in catalog if e["counts"][pop]), reverse=True)
    total = sum(sizes)
    if not total:
        return {"population": pop, "spells": 0, "signatures": 0}

    def cover(k: int) -> float:
        return round(sum(sizes[:k]) / total, 4)
    need = {}
    for frac in (0.5, 0.8, 0.9, 0.95):
        acc = 0
        for i, n in enumerate(sizes, 1):
            acc += n
            if acc >= frac * total:
                need[str(frac)] = i
                break
    return {"population": pop, "spells": total, "signatures": len(sizes),
            "singletons": sum(1 for n in sizes if n == 1),
            "spells_in_singletons": sum(1 for n in sizes if n == 1),
            "largest": sizes[:10], "top10_cover": cover(10), "top50_cover": cover(50),
            "signatures_to_cover": need}


def top(catalog: list[dict[str, Any]], pop: str, k: int = 25) -> list[dict[str, Any]]:
    rows = sorted((e for e in catalog if e["counts"][pop]), key=lambda e: (-e["counts"][pop], e["id"]))[:k]
    return [{"id": e["id"], "count": e["counts"][pop], "witnesses": (e.get(pop + "_members") or e["witnesses"])[:5],
             "signature": e["signature"]} for e in rows]
