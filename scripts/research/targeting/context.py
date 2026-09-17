"""Shared loaded evidence, built once per process.

``Context`` reuses the Dummy pass loaders (snapshot catalog, world overlay,
script index, dispatch tables, build skew) and its current-player
:class:`dummy_semantics.scope.Scope`; it adds :class:`targeting.data.TargetingData`.
Loading costs ~12 s; tests share one instance through the ``tg_ctx`` fixture.
"""

from __future__ import annotations

from functools import cached_property

from dummy_semantics.loaders import Bundle
from dummy_semantics.scope import Scope

from .data import TargetingData

_CTX: Context | None = None


class Context:
    def __init__(self) -> None:
        self.bundle = Bundle()
        self.data = TargetingData(self.bundle.source)

    @cached_property
    def scope(self) -> Scope:
        """Current-player scope (class trees, spec spells, current gear/sets/gems/enchants + authored reach)."""
        return Scope(self.bundle)

    @property
    def catalog(self):
        return self.bundle.catalog

    def name(self, spell: int) -> str:
        return self.bundle.name(spell)

    def is_skew(self, spell: int) -> bool:
        return self.bundle.skew.is_newer_than_trinity(spell)


def get() -> Context:
    global _CTX
    if _CTX is None:
        _CTX = Context()
    return _CTX
