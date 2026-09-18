"""Read-only semantic boundary audit of WoW Lab Core.

Nothing here is production code and nothing here may be imported by Core. The
audit never modifies Core; every record points at pinned Core coordinates.

``pins``      exact repository/build pins and the fail-closed pin check
``schema``    record kinds, required fields and enumerations
``registry``  hand-audited records under ``registry/<kind>/<track>.json``
``extract``   mechanical inventories read out of the pinned Core tree
``corpora``   deterministic corpus generation and coverage checks
``probe/``    external Rust crate that exercises Core through its public API
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
RESEARCH = PACKAGE.parent
ROOT = RESEARCH.parents[1]
PALLET = ROOT.parent
CORE = PALLET / "core"
TABLES = ROOT / "data" / "tables"
CORPORA = ROOT / "docs" / "research" / "core-audit-corpora"
REGISTRY = PACKAGE / "registry"


class FailClosed(RuntimeError):
    """The audit refuses to produce a partial or unpinned result."""
