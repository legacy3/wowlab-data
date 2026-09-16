"""Source-backed reconstruction of the Player character-stat pipeline.

Companion to :mod:`gearing`.  That package explains how an equipped item becomes
stat *contributions*; this one explains what those contributions are added to,
and how the result becomes combat-facing quantities.

Auditing contract is the same: every function that reproduces engine behaviour
carries a ``Mirrors:`` line naming the direct TrinityCore consumer.  Functions
that implement *consumer policy*, or that depend on data outside the checked-in
snapshot, say so explicitly and fail closed rather than guessing.

Dependency note: this package reuses ``gearing.tables`` (pure snapshot access),
``gearing.curves`` (the curve evaluator) and ``gearing.ratings`` (the
rating -> percentage conversion).  The last one is shared because
``Player::GetRatingBonusValue`` is literally the same consumer function on both
sides of the boundary; nothing else is shared.
"""

from __future__ import annotations

__all__ = ["CharacterSourceError", "MissingBaseStats"]


class CharacterSourceError(RuntimeError):
    """A required source fact is missing, unreadable or self-contradictory."""


class MissingBaseStats(CharacterSourceError):
    """Base primary stats are not available for this (class, level) or race.

    Base primary stats live in TrinityCore's **world database**
    (``player_classlevelstats`` + ``player_racestats``), not in the DB2
    snapshot, and the TrinityCore repository ships only their schema.  Supply
    them explicitly with ``--base-stats FILE`` rather than letting the tool
    invent them.
    """
