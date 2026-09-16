"""Projected source loading with a consumption ledger.

The proc tooling reads several very large DB2 exports (``SpellEffect`` alone is
629k rows).  Loading them through :class:`gearing.tables.Table` would build a
dict per row; instead this module reads only the columns a consumer asks for,
coerces them with the gearing rules, and records every (table, column) pair it
touched.  The ledger is the answer to "which fields must a dbc-resolver export
provide to rerun this research?" (``proc_research.py sources``).

Nothing here interprets values.  The Wago CSV layout is an *input format*: the
only assumptions are header-named columns and gearing's empty-cell-is-zero rule
(:func:`gearing.tables.coerce`).  Column names follow the DB2 field names that
TrinityCore's ``DB2LoadInfo`` uses; array fields are ``Name_<index>``.
"""

from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gearing.tables import DEFAULT_TABLES, Tables, coerce

from . import SourceError

ROOT = Path(__file__).resolve().parents[3]
OVERLAY_PATH = ROOT / "docs" / "research" / "procs-corpora" / "trinity-world-overlay.json"


@dataclass
class Ledger:
    """(table -> columns) consumed, plus file hashes computed on demand."""

    columns: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    paths: dict[str, Path] = field(default_factory=dict)

    def note(self, table: str, path: Path, cols: Iterable[str]) -> None:
        self.columns[table].update(cols)
        self.paths[table] = path

    def report(self, with_hashes: bool = True) -> list[dict[str, Any]]:
        out = []
        for table in sorted(self.columns):
            entry: dict[str, Any] = {"table": table, "columns": sorted(self.columns[table])}
            if with_hashes:
                entry["sha256"] = hashlib.sha256(self.paths[table].read_bytes()).hexdigest()
            out.append(entry)
        return out


class Source:
    """One snapshot: projected CSV readers plus the shared gearing ``Tables``."""

    def __init__(self, root: Path | str = DEFAULT_TABLES) -> None:
        self.tables = Tables(root)
        self.root = self.tables.root
        self.ledger = Ledger()
        self._cache: dict[tuple[str, tuple[str, ...]], list[tuple]] = {}

    def path(self, table: str) -> Path:
        path = self.root / f"{table}.csv"
        if not path.exists():
            raise SourceError(f"missing source table {table}.csv under {self.root}")
        return path

    def has(self, table: str) -> bool:
        return (self.root / f"{table}.csv").exists()

    def project(self, table: str, columns: Sequence[str]) -> list[tuple]:
        """Rows of ``table`` restricted to ``columns``, in file (ascending ID) order."""
        key = (table, tuple(columns))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        path = self.path(table)
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                raise SourceError(f"{path} is empty") from None
            missing = [c for c in columns if c not in header]
            if missing:
                raise SourceError(f"{table}.csv lacks columns {missing}")
            idx = [header.index(c) for c in columns]
            rows = [tuple(coerce(row[i]) for i in idx) for row in reader]
        self.ledger.note(table, path, columns)
        self._cache[key] = rows
        return rows

    def iter_dicts(self, table: str, columns: Sequence[str]) -> Iterator[dict[str, Any]]:
        for row in self.project(table, columns):
            yield dict(zip(columns, row))

    def full(self, table: str):
        """The gearing ``Table`` (all columns); recorded as fully consumed."""
        tab = self.tables(table)
        self.ledger.note(table, tab.path, tab.columns)
        return tab
