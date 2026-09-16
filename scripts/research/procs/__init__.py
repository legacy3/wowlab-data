"""Source-backed archaeology of TrinityCore's proc system.

Research tooling for ``docs/research/proc-pipeline-archaeology.md``.  It reads
the checked-in DB2 snapshot under ``data/tables`` plus the TrinityCore
world-database overlay extracted by ``tools/tdb_proc_overlay.py`` and answers
deterministic questions about one proc provider:

* what immutable source facts define it (:mod:`procs.definition`);
* whether a synthetic event is eligible, stopping before RNG
  (:mod:`procs.eligibility`);
* what probability the proved chance models produce (:mod:`procs.chance`);
* how source-proven per-application state evolves over a synthetic event
  stream with predetermined roll values (:mod:`procs.state`).

It is **not** a combat simulator and never draws randomness.

Auditing contract
-----------------
Every function that reproduces engine behaviour names its direct TrinityCore
consumer in a ``Mirrors:`` line.  Anything the consumer does not decide from
source data (scripts, conditions, actor state) is surfaced as an explicit
unknown or external input, never guessed.

Table loading and the ``SourceError`` family are shared with the gearing
package; the package is standard-library only and importable from bare
Python 3.12.
"""

from __future__ import annotations

from gearing import SourceError, UnsupportedSource

__all__ = ["SourceError", "UnsupportedSource"]
