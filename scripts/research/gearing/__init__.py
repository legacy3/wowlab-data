"""Source-backed reconstruction of the retail item gearing pipeline.

This package is *research* tooling for the gearing archaeology pass.  It reads
the checked-in Wago CSV/GameTable snapshot under ``data/tables`` and reproduces,
step by step, the computation TrinityCore performs when it turns an equipped
item into combat-relevant numbers.

Auditing contract
-----------------
Every function that reproduces engine behaviour carries a ``Mirrors:`` line
naming the direct TrinityCore consumer it was ported from, so the port can be
re-checked line by line against a newer checkout.  Functions that implement
*consumer policy* rather than a generic source rule say so explicitly.

The package is deliberately dependency-free (standard library only).  Tests add
``pytest`` and ``hypothesis``; the package itself must stay importable from a
bare Python 3.12.

This is not a stable interface and must not be treated as authority for Core
semantics.
"""

from __future__ import annotations

__all__ = [
    "SourceError",
    "UnsupportedSource",
]


class SourceError(RuntimeError):
    """A required source row is missing, unreadable or self-contradictory.

    Raised instead of guessing.  Callers that see this have hit a real gap in
    the checked-in snapshot, not a tooling bug.
    """


class UnsupportedSource(SourceError):
    """The source data expresses something the direct consumer does not handle.

    Distinct from :class:`SourceError` so the census can separate "missing data"
    from "data present, consumer policy absent".
    """
