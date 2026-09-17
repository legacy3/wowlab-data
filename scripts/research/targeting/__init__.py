"""Spell targeting / recipient-selection archaeology.

Research tooling for ``docs/research/targeting-recipient-policy-archaeology.md``.
The question it answers: given an authored spell effect, a caster, an explicit
target, the surrounding units and their relations, how does the pinned
consumer (TrinityCore) derive the exact set and order of recipients for each
effect?

Layout
------
* :mod:`targeting.context`  -- shared loaded evidence (snapshot, scope, script index)
* :mod:`targeting.data`     -- targeting columns of the DB2 snapshot (radius, chain,
  restrictions, ranges, effect attributes)
* :mod:`targeting.fixture`  -- the neutral synthetic-world fixture (no Unit/Map graph)
* :mod:`targeting.trace`    -- explain/evaluate stage records
* stage modules (selectors, relations, geometry, area, chain, smart, groups,
  recipients, adapters, world, ...) -- pure functions named after the consumer
  they mirror
* :mod:`targeting.cli`      -- ``scripts/research/targeting.py``

Auditing contract (same as the Dummy pass)
------------------------------------------
Every function that reproduces engine behaviour names its direct Trinity
consumer in a ``Mirrors:`` line with ``file:line`` at the pinned revision.
Anything the fixture does not state is **not** defaulted: stages raise
:class:`FailClosed`.  Likely Trinity defects are reproduced *and* marked
(``defect=`` in the trace) rather than silently corrected.

Evidence classes: ``db2-fact``, ``world-db-fact``, ``trinity-consumer``,
``trinity-probe``, ``differential``, ``script-consumer``,
``structural-inference``, ``legacy-only``, ``build-skew``, ``unresolved``.

Standard library only at runtime.
"""

from __future__ import annotations

from pathlib import Path

from gearing import SourceError, UnsupportedSource

ROOT = Path(__file__).resolve().parents[3]
RESEARCH = Path(__file__).resolve().parents[1]
CORPORA = ROOT / "docs" / "research" / "targeting-corpora"
DUMMY_CORPORA = ROOT / "docs" / "research" / "dummy-corpora"
CU_CORPORA = ROOT / "docs" / "research" / "controlled-unit-corpora"
TC_ROOT = ROOT.parent / "TrinityCore"

PINS = {
    "data_snapshot": "12.1.0.69497",
    "trinity_commit": "7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f",
    "trinity_supported_build_max": "12.0.7.68453",
    "tdb": "TDB_full_world_1200.26021_2026_02_06.sql",
    "tdb_sha256": "54ddf4c12d6034a3c61e9b5683c2578de82d826d9090041a5e0477a164f5172d",
    "tdb_updates": "sql/updates/world/master replay (522 files, via tools/tdb_server_overlay.py)",
}

EVIDENCE_CLASSES = (
    "db2-fact", "world-db-fact", "trinity-consumer", "trinity-probe", "differential",
    "script-consumer", "structural-inference", "legacy-only", "build-skew", "unresolved",
)


class FailClosed(UnsupportedSource):
    """The fixture or the evidence does not determine the answer."""


__all__ = ["CORPORA", "EVIDENCE_CLASSES", "FailClosed", "PINS", "ROOT", "SourceError", "TC_ROOT"]
