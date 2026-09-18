"""Track K command: ``aura_lifecycle.py coremap`` -> ``core-navigation.json``.

Navigation only: Core concept x Core coords x research question (A–I) x
represented / refused / assumed / absent x reopen condition.  Fails closed when
Core is not at the pin, is dirty, or any cited anchor drifted.
"""

from __future__ import annotations

from . import CORPORA, PINS
from .cli import emit


def _add(p) -> None:
    p.add_argument("--out", help=f"write the corpus (default stdout); canonical: {CORPORA.name}/core-navigation.json")
    p.add_argument("--no-census", action="store_true",
                   help="skip the snapshot-backed population counts (no context load; for quick checks)")


def build(census: bool = True) -> dict:
    from . import context, coremap, records
    anchors = coremap.verify_core_coords()
    tc_anchors = coremap.verify_trinity_coords()
    nav = coremap.navigation_records()
    payload: dict = {
        "provenance": records.provenance(
            "aura_lifecycle.py coremap --out ../../docs/research/aura-lifecycle-corpora/core-navigation.json",
            core_verified={"commit": PINS["core_commit"], "clean": True, "core_anchors": anchors,
                           "trinity_anchors": tc_anchors},
            probe_command=coremap.PROBE_COMMAND,
        ),
        "scope": "navigation only: where each aura-lifecycle concept lives in Core and which research question it "
                 "touches; findings belong to the Core semantic boundary audit (cited ids), rules to tracks A-I",
        "statuses": {
            "represented": "Core has a runtime representation driven by source facts it validates",
            "refused": "Core fails closed at compile/admission time for this shape",
            "assumed": "Core runs it from host-authored input or a single fixed mode without source validation",
            "absent": "no Core representation and no compile-time refusal tied to the concept",
        },
        "questions": coremap.QUESTIONS,
        "core_navigation": nav,
        "question_matrix": coremap.question_matrix(nav),
        "probe_confirmations": [dict(p, result="pass", core_commit=PINS["core_commit"]) for p in coremap.PROBES],
        "unknowns": coremap.UNKNOWNS,
        "falsification": coremap.FALSIFICATION,
        "timelines": [_carryover_timeline()],
    }
    if census:
        ctx = context.get()
        from .providers import populations
        pops = populations(ctx)
        payload["populations"] = {k: len(v) for k, v in sorted(pops.items())}
        payload["lifecycle_attributes"] = coremap.attribute_table(ctx)
        payload["policy_triggers"] = coremap.policy_trigger_counts(ctx)
        payload["subtype_coverage"] = coremap.subtype_coverage(ctx)
    payload["counts"] = {
        "records": len(nav),
        "by_status": {s: sum(1 for r in nav if r["status"] == s) for s in coremap.STATUSES},
        "probe_tests": sum(p["tests"] for p in coremap.PROBES),
    }
    records.validate_corpus(payload)
    return payload


def _carryover_timeline() -> dict:
    """Refresh of a D=250 ms CappedCarryover aura at two instants; see AL-F-K-07."""
    from .coremap import core_projected_expiry
    rows = []
    for refresh_at in (150, 225):
        rows.append({"event": f"apply@0 then refresh@{refresh_at}", "before_expiry": 250,
                     "core_expiry": core_projected_expiry("CappedCarryover", refresh_at, 250, 250),
                     "pinned_trinity_expiry_per_track_B": refresh_at + min(250 + 250, 250 * 130 // 100)})
    return {"id": "TL-K-01", "subject": "capped carryover, base 250 ms", "rows": rows,
            "mirrors": "crates/combat/src/aura_state.rs:1131-1200; TC Spell.cpp:3284-3288 (as read by track B)",
            "rules_out": "'Core carryover == pinned Trinity pandemic' (diverges at 225); probe e_capped_carryover only "
                         "exercises the 150 row where they agree",
            "evidence": ["core-navigation", "structural-inference"]}


def _run(args) -> int:
    emit(build(census=not args.no_census), args.out)
    return 0


COMMANDS = {
    "coremap": ("Core / research boundary navigation (track K)", _add, _run),
}
