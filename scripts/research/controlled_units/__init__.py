"""Controlled-unit (pet / guardian / summon / charm) research package.

Research only.  Standard library at runtime.  Every module either derives a
fact from the checked-in snapshot (``data/tables``), the pinned TrinityCore
checkout, or a committed corpus, or reports the fact as unresolved.  Nothing
here guesses.

Evidence classes (shared across the combat-preparation research passes):
``db2-fact`` (current snapshot row), ``world-db-fact`` (pinned TDB row),
``trinity-consumer`` (direct consumer read in the pinned checkout),
``trinity-probe`` (executable extracted C++), ``differential`` (Python oracle
vs probe), ``structural-inference``, ``legacy-only``, ``build-skew``,
``unresolved``.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TABLES = ROOT / "data" / "tables"
CORPORA = ROOT / "docs" / "research" / "controlled-unit-corpora"
WORLD_DB_CORPORA = ROOT / "docs" / "research" / "world-db-corpora"
TRINITY_COMMIT = "7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f"
SNAPSHOT_BUILD = "12.1.0.69497"
TDB_RELEASE = "TDB_full_world_1200.26021_2026_02_06.sql"

EVIDENCE_CLASSES = (
    "db2-fact", "world-db-fact", "trinity-consumer", "trinity-probe", "differential",
    "structural-inference", "legacy-only", "build-skew", "unresolved",
)


class SourceError(Exception):
    """A required source fact is absent or malformed; nothing is guessed."""
