"""Source table loading and indexing.

Two shapes exist in the snapshot:

* ``data/tables/<Name>.csv`` -- a DB2 export from wago.tools.  Column names and
  order match TrinityCore's ``DB2LoadInfo`` field order exactly for every table
  this package reads (verified separately; see the research document's
  reproducibility section for the renames Trinity applies).
* ``data/tables/<Name>.txt`` -- a tab separated GameTable, keyed by its first
  column.  Mirrors: ``GameTable<T>`` in
  ``src/server/game/DataStores/GameTables.h``.

Nothing here interprets values; it only provides deterministic access.
"""

from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

from . import SourceError

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TABLES = ROOT / "data" / "tables"


def coerce(text: str) -> Any:
    """CSV cell -> int, float or str.

    Empty cells become ``0``: the DB2 export writes an empty field for a zero
    in some column types, and every consumer in this package treats the two
    identically.
    """
    if text == "":
        return 0
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


class Table:
    """One CSV table, loaded once, with lazily built indexes."""

    __slots__ = ("name", "path", "columns", "rows", "_by", "_multi")

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = path
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                self.columns = next(reader)
            except StopIteration:
                raise SourceError(f"{path} is empty") from None
            cols = self.columns
            self.rows: list[dict[str, Any]] = [
                {c: coerce(v) for c, v in zip(cols, row)} for row in reader
            ]
        self._by: dict[str, dict[Any, dict[str, Any]]] = {}
        self._multi: dict[str, dict[Any, list[dict[str, Any]]]] = {}

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Table {self.name} rows={len(self.rows)}>"

    def by(self, column: str = "ID") -> dict[Any, dict[str, Any]]:
        """Unique index.  A later row wins, matching DB2Storage overwrite order."""
        index = self._by.get(column)
        if index is None:
            self._require_column(column)
            index = {row[column]: row for row in self.rows}
            self._by[column] = index
        return index

    def group(self, column: str) -> dict[Any, list[dict[str, Any]]]:
        """Multi index preserving CSV row order, which is ascending ID."""
        index = self._multi.get(column)
        if index is None:
            self._require_column(column)
            acc: dict[Any, list[dict[str, Any]]] = defaultdict(list)
            for row in self.rows:
                acc[row[column]].append(row)
            index = dict(acc)
            self._multi[column] = index
        return index

    def lookup(self, key: Any, column: str = "ID") -> dict[str, Any] | None:
        return self.by(column).get(key)

    def require(self, key: Any, column: str = "ID") -> dict[str, Any]:
        row = self.by(column).get(key)
        if row is None:
            raise SourceError(f"{self.name} has no row with {column}={key}")
        return row

    def _require_column(self, column: str) -> None:
        if column not in self.columns:
            raise SourceError(
                f"{self.name}.csv has no column {column!r}; "
                f"columns are {', '.join(self.columns)}")

    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


class GameTable:
    """One tab separated GameTable, keyed by its first column.

    Mirrors: ``GameTable<T>::GetRow`` -- row lookup is by the key column and a
    missing key yields ``None`` (Trinity returns ``nullptr``), which every
    caller must handle rather than substituting a default.
    """

    __slots__ = ("name", "path", "key_column", "columns", "rows")

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = path
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            raise SourceError(f"{path} is empty")
        header = lines[0].split("\t")
        self.key_column = header[0]
        self.columns = header[1:]
        self.rows: dict[int, list[float]] = {}
        for line in lines[1:]:
            if not line.strip():
                continue
            parts = line.split("\t")
            self.rows[int(parts[0])] = [float(x) for x in parts[1:]]

    def row(self, index: int) -> list[float] | None:
        return self.rows.get(int(index))

    def column(self, index: int, column: int) -> float | None:
        row = self.row(index)
        if row is None:
            return None
        if column >= len(row):
            raise SourceError(
                f"{self.name}.txt row {index} has {len(row)} columns, "
                f"asked for column {column}")
        return row[column]

    def column_index(self, name: str) -> int:
        try:
            return self.columns.index(name)
        except ValueError:
            raise SourceError(
                f"{self.name}.txt has no column {name!r}; "
                f"columns are {', '.join(self.columns)}") from None

    def row_count(self) -> int:
        """Trinity's ``GetTableRowCount`` counts the implicit zero row too."""
        return len(self.rows) + 1

    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


class Tables:
    """Lazy accessor over one ``data/tables`` snapshot."""

    def __init__(self, root: Path | str = DEFAULT_TABLES) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise SourceError(f"table directory not found: {self.root}")
        self._csv: dict[str, Table] = {}
        self._gt: dict[str, GameTable] = {}

    def __call__(self, name: str) -> Table:
        table = self._csv.get(name)
        if table is None:
            path = self.root / f"{name}.csv"
            if not path.exists():
                raise SourceError(
                    f"missing source table {name}.csv under {self.root}")
            table = Table(name, path)
            self._csv[name] = table
        return table

    def optional(self, name: str) -> Table | None:
        """Like ``__call__`` but returns ``None`` for a table absent from the snapshot.

        Used only where the direct consumer also tolerates the table being
        absent; never to paper over a missing required relationship.
        """
        if (self.root / f"{name}.csv").exists():
            return self(name)
        return None

    def gametable(self, name: str) -> GameTable:
        table = self._gt.get(name)
        if table is None:
            path = self.root / f"{name}.txt"
            if not path.exists():
                raise SourceError(
                    f"missing game table {name}.txt under {self.root}")
            table = GameTable(name, path)
            self._gt[name] = table
        return table

    def loaded(self) -> list[str]:
        return sorted([*self._csv, *(f"{n}.txt" for n in self._gt)])

    def hashes(self, names: list[str]) -> dict[str, str]:
        """sha256 of the named tables, for reproducibility stamps."""
        out: dict[str, str] = {}
        for name in names:
            path = self.root / f"{name}.csv"
            if not path.exists():
                path = self.root / f"{name}.txt"
            if not path.exists():
                raise SourceError(f"cannot hash missing table {name}")
            out[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return out
