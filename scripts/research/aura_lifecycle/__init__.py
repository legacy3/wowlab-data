"""Aura lifecycle archaeology.

Research tooling for ``docs/research/aura-lifecycle-archaeology.md``.  The
question it answers: given an authored aura-producing spell effect and a
concrete application, which mutable aura/application object exists, what it
captures, how duration / stacks / charges / periodic deadlines start, and what
refresh, stack mutation, removal, death and recipient-map changes do to it.

Layout
------
* :mod:`aura_lifecycle.context`  -- shared loaded evidence (snapshot catalog, world
  overlay, script index, current-player scope, optional drift build)
* :mod:`aura_lifecycle.records`  -- shared record shapes (unknowns, experiments,
  rules, falsification history) and their validation
* topic modules (identity, duration, stacks, periodic, removal, recipients,
  passive, overlays, ordering, census, coremap, experiments) -- one owner each
* :mod:`aura_lifecycle.cli`      -- ``scripts/research/aura_lifecycle.py``

Auditing contract (same as the Dummy/targeting passes)
------------------------------------------------------
Every function that reproduces engine behaviour names its direct consumer in a
``Mirrors:`` line with ``file:line`` at the pinned revision.  Anything the
evidence does not determine is **not** defaulted: raise :class:`FailClosed`.
Likely Trinity defects are reproduced *and* marked, never silently corrected.
Trinity is a detailed consumer oracle, not Retail truth.

Standard library only at runtime.
"""

from __future__ import annotations

from pathlib import Path

from gearing import SourceError, UnsupportedSource

ROOT = Path(__file__).resolve().parents[3]
RESEARCH = Path(__file__).resolve().parents[1]
CORPORA = ROOT / "docs" / "research" / "aura-lifecycle-corpora"
TC_ROOT = ROOT.parent / "TrinityCore"
CORE_ROOT = ROOT.parent / "core"
SIMC_ROOT = ROOT.parent / "simc"
# Build-drift snapshot published by the dbc-resolver Dump workflow; fetched by
# tools/fetch_dbc_release.py into the workspace scratch dir (never committed).
DRIFT_TABLES = ROOT.parent.parent / "bag" / "dbc-releases" / "wow-12.1.0.69814-4aec2a19e0a9"

PINS = {
    "data_snapshot": "12.1.0.69497",
    "drift_snapshot": "12.1.0.69814",
    "drift_release": "wowlabdev/dbc-resolver wow-12.1.0.69814-4aec2a19e0a9",
    "drift_archive_sha256": "7087b64d0855c2f5fbf762eafa2b9e2d9852c4bb9951f1c9e022b30d826bdcca",
    "trinity_commit": "7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f",
    "trinity_supported_build_max": "12.0.7.68453",
    "core_commit": "63f3a49124cecf73dee2c34f2b531b1de546c836",
    "sidecar_commit": "350de53ef783f6415f7d2e7c86959e22f6d8403a",
    "simc_commit": "b48def9c26d7532db2e612d97d433ec66bd8eede",
    "tdb": "TDB_full_world_1200.26021_2026_02_06.sql",
    "tdb_sha256": "54ddf4c12d6034a3c61e9b5683c2578de82d826d9090041a5e0477a164f5172d",
    "tdb_updates": "sql/updates/world/master replay (522 files, via tools/tdb_server_overlay.py)",
}

EVIDENCE_CLASSES = (
    "db2-fact", "world-db-fact", "trinity-consumer", "trinity-probe", "differential",
    "script-consumer", "simc-consumer", "core-navigation", "structural-inference",
    "legacy-only", "build-skew", "build-drift", "retail-unknown", "unresolved",
)

# Where a lifecycle input comes from (brief §Snapshot vs dynamic).
INPUT_TIMING = (
    "application-snapshot", "first-tick-snapshot", "recalculated-on-refresh",
    "recalculated-on-stack-change", "explicit-recalculation", "dynamic-each-tick",
    "caster-live-lookup", "target-live-lookup", "script-controlled", "unknown",
)

# Where a lifecycle behaviour is decided (brief §Scripts / world overlays).
POLICY_SOURCE = ("db2", "trinity-default", "world-overlay", "script", "combined", "unknown")

# Numeric-boundary evidence (brief §Numeric boundaries).
NUMERIC_EVIDENCE = ("direct-consumer-reproduced", "source-backed", "trinity-only",
                    "known-divergence", "retail-unknown")


class FailClosed(UnsupportedSource):
    """The evidence does not determine the answer."""


__all__ = ["CORPORA", "EVIDENCE_CLASSES", "FailClosed", "INPUT_TIMING", "NUMERIC_EVIDENCE",
           "PINS", "POLICY_SOURCE", "ROOT", "SourceError", "TC_ROOT"]
