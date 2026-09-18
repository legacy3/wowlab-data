"""Lead oracle: one-spell explanation and the merged cross-cutting lists.

``explain`` composes every track's per-spell view in one process (the context
is loaded once) and keeps each section's own evidence.  A section that fails
closed is reported as ``{"fail_closed": reason}``; nothing is defaulted.

``merged`` gathers ``unknowns`` / ``retail_experiments`` / ``rules`` /
``falsification`` / ``trinity_defects`` / ``core_navigation`` from every track
corpus, validates them with :mod:`records`, and applies the lead's
reconciliation (merges of duplicate findings, per-track confirmations,
supersessions) from :data:`RECONCILIATION`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from typing import Any

from . import CORPORA, FailClosed, records

#: (section, argv) run for ``explain``; ``{s}`` is the spell id.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rows", ("rows", "{s}")),
    ("census", ("census", "--spell", "{s}")),
    ("identity", ("identity", "{s}")),
    ("application", ("application", "{s}")),
    ("duration", ("duration", "{s}")),
    ("refresh", ("refresh", "{s}")),
    ("stacks", ("stacks", "{s}")),
    ("charges", ("charges", "{s}")),
    ("periodic", ("periodic", "{s}")),
    ("snapshot", ("snapshot", "{s}")),
    ("removal", ("removal", "{s}")),
    ("dispel", ("dispel", "{s}")),
    ("lifetime", ("lifetime", "{s}")),
    ("recipients", ("recipients", "{s}")),
    ("passive", ("passive", "{s}")),
    ("external", ("overlays", "{s}")),
)

#: Track corpora carrying cross-cutting lists (lead-owned outputs excluded).
TRACK_CORPORA = (
    "identity.json", "application.json", "duration.json", "refresh.json", "carryover.json", "stacks.json",
    "charges.json", "periodic.json", "snapshot-matrix.json", "removal.json", "dispel.json", "lifetime.json",
    "area-lifecycle.json", "passive-active.json", "external-policy.json", "ordering.json",
    "numeric-boundaries.json", "generation.json", "provider-census.json", "lifecycle-signatures.json", "drift.json",
    "core-navigation.json", "retail-experiments.json",
)

CATALOGUE = "retail-experiments.json"

#: Track rules whose "holds-on-census" describes a consumer code path, not a data
#: regularity the census could falsify (R1 review).  The override keeps the
#: track's value under ``track_status``.
STATUS_OVERRIDES: dict[str, tuple[str, str]] = {
    **{rid: ("source-backed", "R1: describes a Trinity code path; the census cannot falsify it")
       for rid in ("AL-R-C-01", "AL-R-C-02", "AL-R-C-03", "AL-R-C-04", "AL-R-C-05", "AL-R-C-06", "AL-R-C-07",
                   "AL-R-C-08", "AL-R-C-11", "AL-R-C-21", "AL-R-C-24", "AL-R-C-25", "AL-R-E-01", "AL-R-E-06",
                   "AL-R-D-04", "AL-R-F-01", "AL-R-F-02", "AL-R-F-04", "AL-R-F-05", "AL-R-G-01", "AL-R-I-08")},
    "AL-R-H-06": ("proposed", "R1: the rule's own counterexample field says it is not a proof"),
}

#: Lead reconciliation of duplicate / superseded records across tracks.
#: canonical id -> {"merged": [ids], "note": str}.  Merged ids stay in the
#: output with ``"merged_into"`` so no track record is lost.
RECONCILIATION: dict[str, dict[str, Any]] = {
    "AL-D-D-02": {"merged": ["AL-D-B-01"],
                  "note": "same defect (pandemic reads the already-refreshed duration). D found it with the periodic "
                          "probe; B refined it: the commit is min(hit + M, trunc(1.3 hit)) with M the recalculated max, "
                          "so combo-point spells get their minimum duration, and unique non-stacking ATTR13 spells "
                          "(AL-D-I-03) do read the live remaining."},
    "AL-U-B-01": {"merged": ["AL-U-I-05"],
                  "note": "one Retail question: the ATTR13 commit formula (remaining-based vs Trinity's runtime 130%)."},
    "AL-U-D-03": {"merged": ["AL-U-B-09"],
                  "note": "refresh vs due tick in the same ms. Trinity side proven by track I (delivery channel "
                          "decides; ordering.json settlement_b_vs_d); Retail open. B's timelines assumed "
                          "update-before-hit, which holds only for hits delivered after the owner update."},
    "AL-U-D-04": {"merged": ["AL-U-B-07"],
                  "note": "partial / final occurrence after a carried refresh is the same Retail question as a "
                          "proportional last occurrence."},
    "AL-U-B-02": {"merged": ["AL-U-J-03"],
                  "note": "PvPDurationIndex selection (no consumer in Trinity or simc)."},
    "AL-U-K-02": {"merged": ["AL-U-B-03", "AL-U-C-09"],
                  "note": "raw 489 / raw 490 (Attributes_15 0x200 / 0x400): Trinity has no consumer, simc and Core give "
                          "them preserve-expiry / asynchronous-stack meanings."},
    "AL-U-C-06": {"merged": ["AL-U-E-08"],
                  "note": "MODIFY_AURA_STACKS (raw 289) Set 0: a live aura with 0 stacks in Trinity (AL-D-C-04)."},
}


#: Unknowns that apply to every spell whose semantic-axis values match
#: (axis, substring).  Keeps ``explain`` from listing only unknowns that name
#: the spell id literally.
AXIS_UNKNOWNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("RefreshPolicy", "carry=pandemic", ("AL-U-B-01", "AL-U-D-04")),
    ("RefreshPolicy", "timer=", ("AL-U-D-03",)),
    ("PeriodicPolicy", "finite", ("AL-U-D-01", "AL-U-D-05", "AL-U-D-07", "AL-U-I-01")),
    ("PeriodicPolicy", "permanent", ("AL-U-D-05", "AL-U-D-07")),
    ("PeriodicPolicy", "tick-on-apply", ("AL-U-D-02",)),
    ("PeriodicPolicy", "uncovered-tail", ("AL-U-D-04",)),
    ("StackPolicy", "stacking-shared-across-casters", ("AL-U-C-04", "AL-U-A-02")),
    ("AuraIdentity", "/replace", ("AL-U-A-01",)),
    ("AuraIdentity", "shared-refresh", ("AL-U-A-02",)),
    ("ApplicationPolicy", "unit-area", ("AL-U-A-06", "AL-U-F-05", "AL-U-F-07")),
    ("ApplicationPolicy", "dynobj", ("AL-U-F-05", "AL-U-F-07", "AL-U-E-20")),
    ("ApplicationPolicy", "passive", ("AL-U-G-02", "AL-U-G-03")),
    ("RemovalPolicy", "removed-death", ("AL-U-E-01", "AL-U-E-04")),
    ("RemovalPolicy", "dispellable", ("AL-U-E-10", "AL-U-E-11")),
    ("AmountPolicy", "rolling-periodic", ("AL-U-B-08",)),
    ("AmountPolicy", "compute-points-at-cast", ("AL-U-D-06",)),
    ("ChargePolicy", "charges", ("AL-U-E-07",)),
    ("ExternalPolicy", ":script", ("AL-U-H-02", "AL-U-H-05")),
    ("ExternalPolicy", ":world-overlay", ("AL-U-H-01",)),
)


def axis_unknown_ids(axes: dict[str, str]) -> list[str]:
    ids = {u for axis, token, us in AXIS_UNKNOWNS if token in axes.get(axis, "") for u in us}
    if axes.get("ApplicationPolicy", "").startswith("passive") and "interruptible" in axes.get("RemovalPolicy", ""):
        ids.add("AL-U-G-01")
    return sorted(ids)


def _run_section(commands: dict, argv: list[str]) -> Any:
    name = argv[0]
    help_text, add_args, run = commands[name]
    parser = argparse.ArgumentParser(prog=name)
    add_args(parser)
    args = parser.parse_args(argv[1:])
    if hasattr(args, "out"):
        args.out = None
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = run(args)
    except FailClosed as exc:
        return {"fail_closed": str(exc)}
    if code not in (0, None):
        return {"fail_closed": f"{name} exited {code}"}
    text = buf.getvalue().strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


def explain(spell: int) -> dict[str, Any]:
    from .cli import _commands
    commands = _commands()
    out: dict[str, Any] = {"spell": spell, "sections": {}}
    for section, argv in SECTIONS:
        if argv[0] not in commands:
            out["sections"][section] = {"fail_closed": f"command {argv[0]!r} not available"}
            continue
        out["sections"][section] = _run_section(commands, [a.format(s=spell) for a in argv])
    lists = merged()
    needle = str(spell)
    from . import context, model
    try:
        out["semantic_axes"] = model.axes(context.get(), spell)
        linked = set(axis_unknown_ids(out["semantic_axes"]))
    except FailClosed as exc:
        out["semantic_axes"] = {"fail_closed": str(exc)}
        linked = set()
    out["unknowns"] = [{**u, "matched_by": "spell-id" if needle in json.dumps(u, sort_keys=True) else "axis"}
                       for u in lists["unknowns"]
                       if u["id"] in linked or needle in json.dumps(u, sort_keys=True)]
    out["retail_experiments"] = [x for x in lists["retail_experiments"] if needle in json.dumps(x, sort_keys=True)]
    out["reopen_conditions"] = sorted({u["reopen_condition"] if isinstance(u["reopen_condition"], str)
                                       else json.dumps(u["reopen_condition"], sort_keys=True)
                                       for u in out["unknowns"]})
    out["caveat"] = ("Trinity-derived sections describe the pinned consumer oracle, not Retail truth; "
                     "unknowns list the matching reopen conditions.")
    return out


def _load(name: str) -> dict[str, Any]:
    path = CORPORA / name
    if not path.exists():
        raise FailClosed(f"missing corpus {name}; run tools/regen_aura_lifecycle.py")
    return json.loads(path.read_text(encoding="utf-8"))


def merged() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in records.REQUIRED}
    seen: dict[str, str] = {}
    # Track L's catalogue re-grades every track's experiment under the same id
    # (`experiments --scan`), so its copy is canonical for that kind.
    catalogue = {e["id"] for e in _load(CATALOGUE).get("retail_experiments", [])}
    for name in (CATALOGUE,) + tuple(n for n in TRACK_CORPORA if n != CATALOGUE):
        payload = _load(name)
        for kind in records.REQUIRED:
            for entry in payload.get(kind, []):
                if kind == "retail_experiments" and name != CATALOGUE:
                    if entry["id"] not in catalogue:
                        raise ValueError(f"experiment {entry['id']} ({name}) missing from {CATALOGUE}; "
                                         "run `experiments --scan`")
                    continue
                prior = seen.get(entry["id"])
                if prior is not None:
                    if prior == json.dumps(entry, sort_keys=True):
                        continue  # the same record carried by two corpora of one track
                    raise ValueError(f"id {entry['id']} differs between corpora (second in {name})")
                seen[entry["id"]] = json.dumps(entry, sort_keys=True)
                out[kind].append({**entry, "corpus": name})
    for kind, entries in out.items():
        records.validate(kind, [{k: v for k, v in e.items() if k != "corpus"} for e in entries])
        entries.sort(key=lambda e: e["id"])
    by_id = {e["id"]: e for entries in out.values() for e in entries}
    for rid, (status, why) in STATUS_OVERRIDES.items():
        if rid not in by_id:
            raise ValueError(f"status override names unknown rule {rid}")
        rule = by_id[rid]
        if rule["status"] != status:
            rule["track_status"], rule["status"], rule["status_note"] = rule["status"], status, why
    for canonical, rec in RECONCILIATION.items():
        if canonical not in by_id:
            raise ValueError(f"reconciliation names unknown canonical id {canonical}")
        by_id[canonical].setdefault("merged_from", [])
        for other in rec["merged"]:
            if other not in by_id:
                raise ValueError(f"reconciliation names unknown id {other}")
            by_id[other]["merged_into"] = canonical
            by_id[canonical]["merged_from"].append(other)
        by_id[canonical]["reconciliation_note"] = rec["note"]
    return out
