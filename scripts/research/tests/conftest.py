"""Shared fixtures.

The real-data fixtures are session scoped because loading the snapshot costs a
couple of seconds; the synthetic fixtures build tiny in-memory tables so that a
test can pin one behaviour without a real item getting in the way.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

from charstats.basestats import BaseStatTable  # noqa: E402
from charstats.character import CharacterResolver  # noqa: E402
from gearing.resolver import GearResolver  # noqa: E402
from gearing.roster import RosterDiscovery  # noqa: E402
from gearing.tables import DEFAULT_TABLES, Tables  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "snapshot: needs the checked-in data/tables")


@pytest.fixture(scope="session")
def tables() -> Tables:
    if not DEFAULT_TABLES.is_dir():
        pytest.skip(f"no table snapshot at {DEFAULT_TABLES}")
    return Tables(DEFAULT_TABLES)


@pytest.fixture(scope="session")
def resolver(tables: Tables) -> GearResolver:
    return GearResolver(tables)


@pytest.fixture(scope="session")
def discovery(tables: Tables) -> RosterDiscovery:
    return RosterDiscovery(tables)


@pytest.fixture(scope="session")
def synthetic_base_stats() -> BaseStatTable:
    """Deliberately invented base stats; real ones need the TDB world DB."""
    return BaseStatTable.from_json_file(FIXTURES / "synthetic_base_stats.json")


@pytest.fixture(scope="session")
def character_resolver(tables: Tables, synthetic_base_stats: BaseStatTable
                       ) -> CharacterResolver:
    return CharacterResolver(tables, synthetic_base_stats)


@pytest.fixture(scope="session")
def bare_character_resolver(tables: Tables) -> CharacterResolver:
    """No base-stat table: exercises the fail-closed path."""
    return CharacterResolver(tables)


@pytest.fixture(scope="session")
def witnesses() -> dict:
    return json.loads((FIXTURES / "witnesses.json").read_text(encoding="utf-8"))


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def write_gametable(path: Path, header: list[str], rows: list[list]) -> None:
    lines = ["\t".join(header)]
    lines.extend("\t".join(str(c) for c in row) for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# dummy_semantics (server-side semantics archaeology) -- session scoped, ~1 min
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def dummy_ctx():
    from dummy_semantics import CORPORA
    from dummy_semantics.cli import Context
    for name in ("trinity-server-overlay.json", "script-index.json", "dispatch-tables.json", "build-skew.json"):
        if not (CORPORA / name).exists():
            pytest.skip(f"missing corpus {name}")
    if not DEFAULT_TABLES.is_dir():
        pytest.skip(f"no table snapshot at {DEFAULT_TABLES}")
    return Context()


@pytest.fixture(scope="session")
def tg_ctx():
    """Targeting research context (snapshot + Dummy-pass evidence + current-player scope)."""
    from gearing.tables import DEFAULT_TABLES as _tables
    if not _tables.is_dir():
        pytest.skip(f"no table snapshot at {_tables}")
    from targeting import context
    return context.get()
