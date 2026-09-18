"""Core's own semantic catalogs, read out of the audited Core tree.

This module exists so the oracle cannot silently drift from Core.  Rather than
re-typing "which aura subtypes are implemented" or "which spell attributes are
ignorable", it parses the catalogs Core itself ships:

``crates/dbc/src/aura_subtype.rs``     -> ``AuraSubtypeKind`` raws + support class
``crates/dbc/src/spell_attribute.rs``  -> ``SpellAttributeKind`` raws + support class

Support classes are Core's: ``implemented`` / ``ignored`` / ``disabled`` /
``unimplemented``.  Evidence class for everything here is ``core``.

If Core's file layout changes, the parser fails closed rather than returning a
partial catalog -- a silently short catalog would turn into false admissions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from . import CORE, FailClosed

AURA_SUBTYPE_FILE = CORE / "crates/dbc/src/aura_subtype.rs"
SPELL_ATTRIBUTE_FILE = CORE / "crates/dbc/src/spell_attribute.rs"

_ENUM_MEMBER = re.compile(r"^\s{4,}([A-Z][A-Za-z0-9]*)\s*=\s*(\d+)\s*,\s*$")
#: Catalog rows are written both on one line and wrapped over several, so the
#: scan runs against whitespace-collapsed text rather than line by line.
_CATALOG_ROW = re.compile(
    r"\b(implemented|ignored|disabled|unimplemented)\(\s*"
    r"(?:AuraSubtypeKind|SpellAttributeKind)::([A-Za-z0-9]+)\s*,"
)

SUPPORT_CLASSES = ("implemented", "ignored", "disabled", "unimplemented")


@dataclass(frozen=True)
class Support:
    raw: int
    name: str
    support: str


def _parse(path: Path, enum_name: str) -> dict[int, Support]:
    if not path.exists():
        raise FailClosed(f"missing Core catalog {path}; is /home/dev/pallet/core checked out?")
    text = path.read_text(encoding="utf-8")

    start = text.find(f"enum {enum_name} {{")
    if start < 0:
        raise FailClosed(f"{path}: no `enum {enum_name}` block")
    end = text.find("\n    }\n", start)
    raws: dict[str, int] = {}
    for line in text[start:end].splitlines():
        match = _ENUM_MEMBER.match(line)
        if match:
            raws[match.group(1)] = int(match.group(2))
    if not raws:
        raise FailClosed(f"{path}: `enum {enum_name}` yielded no members")

    out: dict[int, Support] = {}
    for support, variant in _CATALOG_ROW.findall(re.sub(r"\s+", " ", text)):
        raw = raws.get(variant)
        if raw is None:
            raise FailClosed(f"{path}: catalog names {variant}, absent from {enum_name}")
        out[raw] = Support(raw, variant, support)
    if not out:
        raise FailClosed(f"{path}: support catalog yielded no rows")

    # Core documents these catalogs as complete slices of their enum.  A member with
    # no catalog row would otherwise read as "unknown" and quietly widen admission.
    uncatalogued = sorted(set(raws.values()) - set(out))
    if uncatalogued:
        raise FailClosed(
            f"{path}: {len(uncatalogued)} {enum_name} members have no support row "
            f"(first: {uncatalogued[:5]}); the catalog parser is out of date"
        )
    return out


@cache
def aura_subtypes() -> dict[int, Support]:
    """``AuraSubtypeKind`` raw -> support class, as Core declares it."""
    return _parse(AURA_SUBTYPE_FILE, "AuraSubtypeKind")


@cache
def spell_attributes() -> dict[int, Support]:
    """``SpellAttributeKind`` raw -> support class, as Core declares it."""
    return _parse(SPELL_ATTRIBUTE_FILE, "SpellAttributeKind")


def aura_support(raw: int) -> str:
    """``unknown`` for a subtype Core's catalog does not mention (build skew)."""
    entry = aura_subtypes().get(raw)
    return entry.support if entry else "unknown"


def attribute_support(raw: int) -> str:
    entry = spell_attributes().get(raw)
    return entry.support if entry else "unknown"


#: ``SpellAttributeKind::Passive``.
PASSIVE_ATTRIBUTE = 6
