"""Server-side spell semantics archaeology (Dummy / ScriptEffect / script-bound).

Research tooling for ``docs/research/dummy-server-semantics-archaeology.md``.
It reads three evidence corpora plus the DB2 snapshot:

* ``data/tables`` (Wago CSV export of the client DB2 tables);
* ``docs/research/dummy-corpora/trinity-server-overlay.json`` -- TrinityCore
  world-database tables that supply behaviour (``tools/tdb_server_overlay.py``);
* ``docs/research/dummy-corpora/script-index.json`` -- structural index of
  ``src/server/scripts`` and engine hardcoded IDs (``tools/tc_script_index.py``);
* ``docs/research/dummy-corpora/dispatch-tables.json`` -- generic effect/aura
  handler tables and script hook call sites (``tools/tc_dispatch_tables.py``);
* ``docs/research/dummy-corpora/build-skew.json`` -- SpellIDs newer than the
  last client build the pinned Trinity revision supports.

Auditing contract
-----------------
Every function that reproduces engine behaviour names its direct TrinityCore
consumer in a ``Mirrors:`` line.  Structural facts from the script index are
*navigation*: a family membership is only "proved" when the report lists a
witness whose consumer code was read.  Unknown scripts, flags and tables are
reported as unknown (:class:`FailClosed`) and never given a default.

The package is standard-library only at runtime (the index generator needs
tree-sitter, see ``tools/tc_script_index.py``).
"""

from __future__ import annotations

from pathlib import Path

from gearing import SourceError, UnsupportedSource

ROOT = Path(__file__).resolve().parents[3]
CORPORA = ROOT / "docs" / "research" / "dummy-corpora"
PROC_CORPORA = ROOT / "docs" / "research" / "procs-corpora"
GEAR_CORPORA = ROOT / "docs" / "research" / "gearing-corpora"


class FailClosed(UnsupportedSource):
    """Raised (or recorded) when evidence does not decide a question."""


__all__ = ["SourceError", "UnsupportedSource", "FailClosed", "ROOT", "CORPORA", "PROC_CORPORA", "GEAR_CORPORA"]
