#!/usr/bin/env python3
"""Generic, schema-driven extraction of TrinityCore world-database tables.

Generalisation of ``tdb_server_overlay.py`` / ``tdb_proc_overlay.py``: the same
restricted replay (:mod:`tdb_replay`) of the pinned TDB dump plus every
``sql/updates/world/master`` file, but for an arbitrary list of tables, so that
the controlled-unit, weapon-combat and character-preparation research passes
share one extraction path and one provenance block instead of three parsers.

Nothing here interprets a row.  Rows are Trinity's authoring, reported as
such.  Every requested table that is absent from the dump is recorded under
``tables_missing_from_dump`` (never silently skipped) and every statement the
replay could not parse is recorded under ``unparsed``.

Usage::

    python3 tools/tdb_world_extract.py \\
        --tdb ../../bag/tdb/TDB_full_world_1200.26021_2026_02_06.sql \\
        --tables player_classlevelstats,player_racestats \\
        --out ../../docs/research/world-db-corpora/player-base-stats.json

    # projection / row filter (repeatable):
    #   --project creature_template=entry,name,unit_class,family,type
    #   --keep creature_template.entry=@ids.json       (JSON list, or an object with an "ids" list)
    #   --keep creature_template.family=1,2,3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tdb_replay import (Replay, SetExpr, TableState, parse_create_tables,  # noqa: E402
                        replay_dump_lines, replay_updates)

HERE = Path(__file__).resolve()
WOWLAB_DATA = HERE.parents[3]
WORKSPACE_PARENT = WOWLAB_DATA.parent
TDB_RELEASE = ("TDB1200.26021 (TDB_full_1200.26021_2026_02_06.7z, "
               "sha256 48f0e2af7620ca70ec7054ff19d356255bc50e11d7829932015f28918f195588)")


class IndexedTableState(TableState):
    """``TableState`` with an incremental index on the first primary-key column.

    ``tdb_replay.TableState`` answers every ``DELETE``/``UPDATE`` with a scan of
    the whole table, which makes tables with tens of thousands of update
    statements (``creature_template_difficulty``: 22,774) take hours.  This
    subclass narrows the scan to the rows whose first key column is named by
    the WHERE clause, and falls back to the full scan otherwise.  Row order is
    tracked so that the observable result (including which row wins a key
    collision during ``UPDATE``) is identical to the plain scan;
    ``tests/test_tdb_world_extract.py`` checks that equivalence.
    ``tdb_replay`` itself is left unchanged (it is frozen for the earlier passes).
    """

    def __init__(self, schema) -> None:
        super().__init__(schema)
        self._k0 = self.key[0]
        self._index: dict[object, set[tuple]] = {}
        self._seq: dict[tuple, int] = {}
        self._counter = 0

    def _add(self, key: tuple, row: dict) -> None:
        if key not in self._seq:
            self._seq[key] = self._counter
            self._counter += 1
        self._index.setdefault(row[self._k0], set()).add(key)

    def _drop(self, key: tuple, row: dict) -> None:
        self._seq.pop(key, None)
        bucket = self._index.get(row[self._k0])
        if bucket is not None:
            bucket.discard(key)
            if not bucket:
                del self._index[row[self._k0]]

    def insert(self, columns, tuples, mode):
        count = 0
        for values in tuples:
            row = self._row(columns, values)
            key = tuple(row[k] for k in self.key)
            if key in self.rows and mode == "INSERT":
                raise ValueError(f"{self.name}: duplicate key {key} on INSERT")
            if key in self.rows and mode == "INSERT IGNORE":
                continue
            old = self.rows.get(key)
            if old is not None:
                # an overwritten dict key keeps its position: keep its sequence number
                seq = self._seq[key]
                self._drop(key, old)
                self._seq[key] = seq
            self.rows[key] = row
            self._add(key, row)
            count += 1
        return count

    def _candidates(self, where) -> list[tuple] | None:
        keys: set[tuple] = set()
        for conj in where:
            values = None
            for col, vals in conj.items():
                if self.col(col) == self._k0:
                    values = vals
                    break
            if values is None:
                return None
            for value in values:
                keys |= self._index.get(value, set())
        return sorted(keys, key=self._seq.__getitem__)

    def delete(self, where) -> int:
        candidates = self._candidates(where)
        pool = self.rows.items() if candidates is None else ((k, self.rows[k]) for k in candidates)
        doomed = [k for k, r in pool if self.matches(r, where)]
        for k in doomed:
            self._drop(k, self.rows.pop(k))
        return len(doomed)

    def update(self, assigns, where) -> int:
        candidates = self._candidates(where)
        pool = self.rows.items() if candidates is None else ((k, self.rows[k]) for k in candidates)
        hit = [k for k, r in pool if self.matches(r, where)]
        for k in hit:
            row = self.rows.pop(k)
            self._drop(k, row)
            for col, value in assigns.items():
                name = self.col(col)
                row[name] = value.evaluate(row, self) if isinstance(value, SetExpr) else value
            new_key = tuple(row[c] for c in self.key)
            clobbered = self.rows.get(new_key)
            if clobbered is not None:
                # plain dict assignment keeps the clobbered row's position
                seq = self._seq[new_key]
                self._drop(new_key, clobbered)
                self._seq[new_key] = seq
            self.rows[new_key] = row
            self._add(new_key, row)
        return len(hit)


def indexed(replay: Replay) -> Replay:
    """Swap every table of a fresh ``Replay`` for :class:`IndexedTableState`."""
    if any(t.rows for t in replay.state.values()):
        raise RuntimeError("indexed() must be applied before any statement is replayed")
    replay.state = {name: IndexedTableState(t.schema) for name, t in replay.state.items()}
    return replay


def git(tc_root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tc_root), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def _sort_key(value: object) -> tuple:
    return (0, value) if isinstance(value, (int, float)) else (1, str(value))


def parse_keep(spec: str) -> tuple[str, str, set]:
    target, _, values = spec.partition("=")
    table, _, column = target.partition(".")
    if not (table and column and values):
        raise SystemExit(f"--keep expects table.column=v1,v2 or table.column=@file.json, got {spec!r}")
    if values.startswith("@"):
        raw = json.loads(Path(values[1:]).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            # a corpus object must carry its id list under "ids"; never read its keys as ids
            if not isinstance(raw.get("ids"), list):
                raise SystemExit(f"--keep file {values[1:]} is an object without an 'ids' list")
            raw = raw["ids"]
        if not isinstance(raw, list):
            raise SystemExit(f"--keep file {values[1:]} must be a JSON list or an object with 'ids'")
        keep = set(raw)
    else:
        keep = set()
        for token in values.split(","):
            token = token.strip()
            try:
                keep.add(int(token))
            except ValueError:
                keep.add(token)
    # world-DB integer keys arrive as ints from the replay; accept both spellings
    keep |= {int(v) for v in keep if isinstance(v, str) and re.fullmatch(r"-?\d+", v)}
    keep |= {str(v) for v in list(keep) if isinstance(v, int)}
    return table, column, keep


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tdb", type=Path, required=True)
    parser.add_argument("--tc-root", type=Path, default=WORKSPACE_PARENT / "TrinityCore")
    parser.add_argument("--tables", required=True, help="comma separated table names")
    parser.add_argument("--project", action="append", default=[],
                        help="table=col1,col2,... keep only these columns (key columns are always kept)")
    parser.add_argument("--keep", action="append", default=[],
                        help="table.column=v1,v2 | table.column=@file.json  keep only rows whose column is in the set")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--skip-updates", action="store_true",
                        help="do not replay sql/updates/world/master (recorded in provenance)")
    args = parser.parse_args()

    tc_root = args.tc_root.resolve()
    pinned = re.search(r'DATABASE_FULL_DATABASE\s+"([^"]+)"',
                       (tc_root / "revision_data.h.in.cmake").read_text()).group(1)
    if args.tdb.name != pinned:
        print(f"refusing: checkout pins {pinned}, got {args.tdb.name}", file=sys.stderr)
        return 2

    wanted = {t.strip() for t in args.tables.split(",") if t.strip()}
    projections: dict[str, list[str]] = {}
    for spec in args.project:
        table, _, cols = spec.partition("=")
        projections[table] = [c.strip() for c in cols.split(",") if c.strip()]
    keeps: dict[str, list[tuple[str, set]]] = {}
    for spec in args.keep:
        table, column, values = parse_keep(spec)
        keeps.setdefault(table, []).append((column, values))

    base_text = args.tdb.read_text(encoding="utf-8", errors="replace")
    base_sha = hashlib.sha256(base_text.encode("utf-8", errors="replace")).hexdigest()
    schemas = parse_create_tables(base_text, wanted)
    missing = sorted(wanted - set(schemas))
    replay = indexed(Replay(schemas))
    unparsed: list[dict] = []
    counter: Counter = Counter()
    replay_dump_lines(replay, base_text, unparsed, counter, args.tdb.name)
    base_counts = {name: len(t.rows) for name, t in replay.state.items()}
    del base_text

    if args.skip_updates:
        touched_files, total_files = [], 0
    else:
        touched_files, total_files = replay_updates(
            replay, tc_root / "sql/updates/world/master", unparsed, counter)

    tables: dict[str, dict] = {}
    for name in sorted(replay.state):
        st = replay.state[name]
        rows = list(st.rows.values())
        filters = []
        for column, values in keeps.get(name, []):
            if column not in st.columns:
                raise SystemExit(f"--keep column {column!r} not in {name} columns {st.columns}")
            rows = [r for r in rows if r[column] in values]
            filters.append({"column": column, "values": len(values)})
        rows.sort(key=lambda r: tuple(_sort_key(r[k]) for k in st.key))
        cols = st.columns
        if name in projections:
            cols = list(dict.fromkeys(list(st.key) + projections[name]))
            unknown = [c for c in cols if c not in st.columns]
            if unknown:
                raise SystemExit(f"--project columns {unknown} not in {name} columns {st.columns}")
        tables[name] = {
            "columns": cols, "key": list(st.key),
            "projected": name in projections,
            "row_filters": filters,
            "row_count_total": len(st.rows),
            "row_count_emitted": len(rows),
            "rows": [[r[c] for c in cols] for r in rows],
        }

    payload = {
        "provenance": {
            "tool": "scripts/research/tools/tdb_world_extract.py",
            "trinitycore_commit": git(tc_root, "rev-parse", "HEAD"),
            "base_world_database": pinned,
            "base_world_database_sha256": base_sha,
            "tdb_release": TDB_RELEASE,
            "update_directory": None if args.skip_updates else "sql/updates/world/master",
            "update_files_total": total_files,
            "update_files_touching_tracked_tables": touched_files,
            "base_row_counts": base_counts,
            "final_row_counts": {name: len(t.rows) for name, t in replay.state.items()},
            "applied": {" / ".join(k): v for k, v in sorted(counter.items())},
            "unparsed_statement_count": len(unparsed),
            "tables_requested": sorted(wanted),
            "tables_missing_from_dump": missing,
            "note": "schema-driven restricted replay; see tools/tdb_replay.py. Rows are Trinity "
                    "authoring for the pinned TDB, not client data and not Retail truth.",
        },
        "schemas": {name: s.to_dict() for name, s in sorted(schemas.items())},
        "unparsed": unparsed,
        "tables": tables,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n",
                        encoding="utf-8")
    size = args.out.stat().st_size
    print(f"wrote {args.out} ({size / 1e6:.2f} MB); unparsed={len(unparsed)}; missing={missing}; "
          f"rows={ {n: (t['row_count_total'], t['row_count_emitted']) for n, t in tables.items()} }")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
