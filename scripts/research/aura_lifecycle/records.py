"""Shared record shapes for every aura-lifecycle corpus.

Each track writes its own corpus but uses these shapes for the cross-cutting
lists so the lead can merge them mechanically:

* ``unknowns``            -- ``AL-U-<track>-NN``
* ``retail_experiments``  -- ``AL-X-<track>-NN``
* ``rules``               -- ``AL-R-<track>-NN`` candidate lifecycle rules
* ``falsification``       -- ``AL-F-<track>-NN`` counterexample / refinement history
* ``trinity_defects``     -- ``AL-D-<track>-NN``
* ``core_navigation``     -- ``AL-K-<track>-NN`` Core reopen opportunities (navigation only)

:func:`validate` raises on a malformed entry instead of dropping it.
"""

from __future__ import annotations

import re
from typing import Any

from . import EVIDENCE_CLASSES, PINS

TRACKS = tuple("ABCDEFGHIJKL") + ("R1", "R2", "R3", "X")

REQUIRED: dict[str, tuple[str, ...]] = {
    "unknowns": ("id", "subject", "question", "known", "why_unresolved", "evidence", "coords",
                 "blocker", "reopen_condition", "build_skew"),
    "retail_experiments": ("id", "question", "models", "setup", "observable", "discriminates",
                           "fidelity", "related"),
    "rules": ("id", "name", "definition", "population", "counterexamples", "status", "evidence"),
    "falsification": ("id", "rule", "attempt", "result", "action"),
    "trinity_defects": ("id", "coords", "description", "lifecycle_effect", "oracle_behaviour"),
    "core_navigation": ("id", "topic", "core_coords", "research_ref", "observation", "reopen_condition"),
}
PREFIX = {"unknowns": "U", "retail_experiments": "X", "rules": "R", "falsification": "F",
          "trinity_defects": "D", "core_navigation": "K"}
RULE_STATUS = ("proposed", "refined", "holds-on-census", "source-backed", "discarded", "trinity-only", "unknown")
FIDELITY = ("exact", "approximate", "insufficient")
_ID = re.compile(r"^AL-(?P<kind>[UXRFDK])-(?P<track>[A-L]|R[123]|X)-(?P<n>\d{2,3})$")


def provenance(command: str, **extra: Any) -> dict[str, Any]:
    """Provenance block for a corpus: pins + the regenerating command."""
    return {"pins": dict(PINS), "command": command, **extra}


def validate(kind: str, entries: list[dict[str, Any]]) -> None:
    need = REQUIRED[kind]
    seen: set[str] = set()
    for e in entries:
        missing = [k for k in need if k not in e]
        if missing:
            raise ValueError(f"{kind} entry {e.get('id')!r} lacks {missing}")
        m = _ID.match(e["id"])
        if not m or m["kind"] != PREFIX[kind]:
            raise ValueError(f"{kind} entry has malformed id {e['id']!r}")
        if e["id"] in seen:
            raise ValueError(f"duplicate id {e['id']!r}")
        seen.add(e["id"])
        ev = e.get("evidence")
        for cls in ([ev] if isinstance(ev, str) else ev or []):
            if isinstance(cls, str) and cls not in EVIDENCE_CLASSES:
                raise ValueError(f"{e['id']}: unknown evidence class {cls!r}")
        if kind == "rules" and e["status"] not in RULE_STATUS:
            raise ValueError(f"{e['id']}: bad rule status {e['status']!r}")
        if kind == "retail_experiments" and e["fidelity"] not in FIDELITY:
            raise ValueError(f"{e['id']}: bad fidelity {e['fidelity']!r}")


def validate_corpus(payload: dict[str, Any]) -> None:
    for kind in REQUIRED:
        if kind in payload:
            validate(kind, payload[kind])
