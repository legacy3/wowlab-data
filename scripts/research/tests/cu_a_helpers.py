"""Track A (controlled units: population / ownership / lifecycle) shared test helpers.

``Context()`` loads the dummy-semantics bundle and the default scope (~11 s),
so it is built once per test session and shared by the three Track A test
files through ``functools.lru_cache``.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import pytest

TRINITY = Path(__file__).resolve().parents[4] / "TrinityCore"
TRINITY_COMMIT = "7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f"


@functools.lru_cache(maxsize=1)
def _context():
    from controlled_units.population import Context
    return Context()


@functools.lru_cache(maxsize=1)
def _census():
    from controlled_units.census import Census
    return Census(_context())


def context():
    from dummy_semantics import CORPORA
    from gearing.tables import DEFAULT_TABLES
    if not DEFAULT_TABLES.is_dir():
        pytest.skip(f"no table snapshot at {DEFAULT_TABLES}")
    for name in ("trinity-server-overlay.json", "script-index.json", "dispatch-tables.json", "build-skew.json"):
        if not (CORPORA / name).exists():
            pytest.skip(f"missing corpus {name}")
    return _context()


def census():
    context()
    return _census()


def trinity_root() -> Path:
    """The pinned TrinityCore checkout, or skip."""
    if not (TRINITY / "src/server/game").is_dir():
        pytest.skip("no TrinityCore checkout next to wowlab-data")
    return TRINITY


def trinity_line(path: str, line: int) -> str:
    root = trinity_root()
    lines = (root / path).read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[line - 1] if 0 < line <= len(lines) else ""


_COORD = re.compile(r"(src/server/[A-Za-z0-9_/]+\.(?:cpp|h)):(\d+)(?:-(\d+))?")


def coordinates_in(text: str) -> list[tuple[str, int, int]]:
    """Every ``src/server/...:a[-b]`` coordinate inside ``text``."""
    return [(m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))) for m in _COORD.finditer(text)]
