"""Load and validate hand-audited records from ``registry/<kind>/<track>.json``."""

from __future__ import annotations

import json
import re
from functools import cache

from . import CORE, REGISTRY, FailClosed
from .schema import KINDS, validate

_COORD = re.compile(r"^(?P<path>[^:]+):(?P<first>\d+)(?:-(?P<last>\d+))?$")


def load(kind: str) -> list[dict]:
    if kind not in KINDS:
        raise FailClosed(f"unknown record kind {kind}")
    records: list[dict] = []
    directory = REGISTRY / kind
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            chunk = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise FailClosed(f"{path}: {error}") from error
        if not isinstance(chunk, list):
            raise FailClosed(f"{path}: expected a JSON list of records")
        for record in chunk:
            record = dict(record)
            record["_file"] = path.name
            records.append(record)
    return records


def load_all() -> dict[str, list[dict]]:
    return {kind: load(kind) for kind in KINDS}


@cache
def _line_count(path: str) -> int | None:
    file = CORE / path
    if not file.is_file():
        return None
    return file.read_text(encoding="utf-8", errors="replace").count("\n") + 1


def coord_problems(coord: str) -> list[str]:
    match = _COORD.match(coord)
    if not match:
        return [f"coord {coord!r} is not <path>:<line>[-<line>]"]
    lines = _line_count(match["path"])
    if lines is None:
        return [f"coord {coord!r}: no such Core file"]
    last = int(match["last"] or match["first"])
    if int(match["first"]) < 1 or last > lines or last < int(match["first"]):
        return [f"coord {coord!r}: outside 1..{lines}"]
    return []


def record_coords(record: dict) -> list[str]:
    coords = list(record.get("coords", []))
    for compiler in record.get("compilers", []) or []:
        coords.extend(compiler.get("coords", []))
    for stage in record.get("stages", []) or []:
        coords.extend(stage.get("coords", []))
    return coords


def problems(records: dict[str, list[dict]]) -> list[str]:
    out: list[str] = []
    seen: dict[str, str] = {}
    for kind, rows in records.items():
        for record in rows:
            rid = record.get("id", "<no id>")
            where = f"{kind}/{record.get('_file')}:{rid}"
            out.extend(f"{where}: {p}" for p in validate(kind, record))
            key = f"{kind}:{rid}"
            if key in seen:
                out.append(f"{where}: duplicate id (also in {seen[key]})")
            seen[key] = where
            for coord in record_coords(record):
                out.extend(f"{where}: {p}" for p in coord_problems(coord))
    return out
