#!/usr/bin/env python3
"""Restricted, schema-driven replay of TrinityCore world-database SQL.

Shared by ``tdb_server_overlay.py`` (and reusable by tests).  The base TDB dump
is a ``mysqldump`` file; every tracked table's column order, key and defaults
are read from its ``CREATE TABLE`` statement, so nothing about a table's shape
is hand-typed here.  Updates under ``sql/updates/world/master`` are replayed in
``UpdateFetcher::PathCompare`` order (file name, lexicographic).

Supported statement shapes (anything else touching a tracked table is recorded
as *unparsed*, never guessed):

* ``INSERT [IGNORE] INTO t [(cols)] VALUES (...),(...)`` / ``REPLACE INTO``
* ``DELETE FROM t WHERE <conj of col = lit | col IN (...)> [OR ...]``
* ``UPDATE t SET col = lit[, ...] WHERE <same>``
* ``SET @var := <int>`` session variables used as literals

The lexer and WHERE parser are the ones proven on the proc overlay
(``tdb_proc_overlay.py``); they are duplicated here rather than imported so
that the proc tooling stays frozen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# lexing
# ---------------------------------------------------------------------------


def split_statements(text: str) -> list[str]:
    """Split on ``;`` outside strings, dropping ``--``/``#`` and ``/* */`` comments."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    quote = ""
    while i < n:
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                if i + 1 < n and text[i + 1] == quote:
                    buf.append(text[i + 1])
                    i += 2
                    continue
                quote = ""
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "-" and text.startswith("--", i) and (i + 2 >= n or text[i + 2] in " \t\r\n"):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


TOKEN = re.compile(r"""
    \s*(?:
      (?P<str>'(?:[^'\\]|\\.|'')*'|"(?:[^"\\]|\\.|"")*")
    | (?P<hex>0x[0-9A-Fa-f]+)
    | (?P<var>@\w+(?:\s*[-+]\s*\d+)?)
    | (?P<bor>\d+(?:\s*\|\s*\d+)+)
    | (?P<num>[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)
    | (?P<null>NULL\b)
    | (?P<punct>[(),])
    )""", re.VERBOSE | re.IGNORECASE)

SET_VAR = re.compile(r"^SET\s+(@\w+)\s*:?=\s*(.+)$", re.IGNORECASE | re.DOTALL)
IDENT = r"`?(\w+)`?"
INSERT = re.compile(
    rf"^(INSERT(?:\s+IGNORE)?|REPLACE)\s+INTO\s+{IDENT}\s*(\(([^)]*)\))?\s*VALUES?\s*(.*)$",
    re.IGNORECASE | re.DOTALL)
DELETE = re.compile(rf"^DELETE\s+FROM\s+{IDENT}\s+WHERE\s+(.*)$", re.IGNORECASE | re.DOTALL)
UPDATE = re.compile(rf"^UPDATE\s+{IDENT}\s+SET\s+(.*?)\s+WHERE\s+(.*)$", re.IGNORECASE | re.DOTALL)
_LIT = r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|0x[0-9A-Fa-f]+|@\w+(?:\s*[-+]\s*\d+)?|[-+]?\d+(?:\.\d+)?"
COND = re.compile(rf"^\s*{IDENT}\s*(?:(=)\s*({_LIT})|IN\s*\(([^)]*)\))\s*$", re.IGNORECASE)
ASSIGN = re.compile(rf"^\s*{IDENT}\s*=\s*({_LIT})\s*$")
ASSIGN_EXPR = re.compile(rf"^\s*{IDENT}\s*=\s*(.+)$", re.DOTALL)


class Session:
    """``SET @x := n`` variables of the update file being replayed."""

    def __init__(self) -> None:
        self.variables: dict[str, int] = {}

    def eval_var(self, text: str) -> int:
        m = re.fullmatch(r"(@\w+)(?:\s*([-+])\s*(\d+))?", text.strip())
        if not m or m.group(1) not in self.variables:
            raise ValueError(f"unknown variable expression {text!r}")
        value = self.variables[m.group(1)]
        if m.group(2):
            value = value + int(m.group(3)) if m.group(2) == "+" else value - int(m.group(3))
        return value

    def parse_literal(self, token: re.Match) -> object:
        if token.group("hex") is not None:
            return int(token.group("hex"), 16)
        if token.group("bor") is not None:
            value = 0
            for part in token.group("bor").split("|"):
                value |= int(part)
            return value
        if token.group("var") is not None:
            return self.eval_var(token.group("var"))
        if token.group("str") is not None:
            quote = token.group("str")[0]
            raw = token.group("str")[1:-1]
            return re.sub(r"\\(.)|''|\"\"", lambda m: m.group(1) if m.group(1) else quote, raw)
        if token.group("num") is not None:
            text = token.group("num")
            return float(text) if any(c in text for c in ".eE") else int(text)
        return None

    def literal(self, text: str) -> object:
        text = text.strip()
        m = TOKEN.match(text)
        if not m or m.group("punct") or m.end() != len(text):
            raise ValueError(f"bad literal {text!r}")
        return self.parse_literal(m)

    def parse_tuples(self, text: str) -> list[list[object]]:
        rows: list[list[object]] = []
        pos = 0
        text = text.strip()
        while pos < len(text):
            m = TOKEN.match(text, pos)
            if not m or m.group("punct") != "(":
                raise ValueError(f"expected '(' at {text[pos:pos + 30]!r}")
            pos = m.end()
            row: list[object] = []
            while True:
                m = TOKEN.match(text, pos)
                if not m or m.group("punct"):
                    raise ValueError(f"expected literal at {text[pos:pos + 30]!r}")
                row.append(self.parse_literal(m))
                pos = m.end()
                m = TOKEN.match(text, pos)
                if not m or m.group("punct") not in (",", ")"):
                    raise ValueError(f"expected ',' or ')' at {text[pos:pos + 30]!r}")
                pos = m.end()
                if m.group("punct") == ")":
                    break
            rows.append(row)
            m = re.compile(r"\s*,").match(text, pos)
            if m:
                pos = m.end()
            elif text[pos:].strip():
                raise ValueError(f"trailing text {text[pos:pos + 30]!r}")
            else:
                break
        return rows

    def parse_where(self, text: str) -> list[dict[str, set]]:
        text = " ".join(text.split())
        disjuncts = split_top(strip_parens(text), " OR ")
        if len(disjuncts) > 1:
            return [c for d in disjuncts for c in self.parse_where(d)]
        return [self.parse_conjunction(text)]

    def parse_conjunction(self, text: str) -> dict[str, set]:
        if re.search(r"\bOR\b", strip_parens(text), re.IGNORECASE):
            raise ValueError("nested OR in WHERE is not supported")
        clauses: dict[str, set] = {}
        for part in where_parts(text):
            m = COND.match(part)
            if not m:
                raise ValueError(f"unsupported WHERE clause {part.strip()!r}")
            col = m.group(1)
            if m.group(2):
                values = {self.literal(m.group(3))}
            else:
                values = {self.parse_literal(t) for t in TOKEN.finditer(m.group(4))
                          if t.group("punct") is None}
            clauses[col] = clauses[col] & values if col in clauses else values
        return clauses


def split_top(text: str, sep: str) -> list[str]:
    parts, depth, cur, quote = [], 0, [], ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            cur.append(ch)
            if ch == "\\":
                cur.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch == "'":
            quote = ch
            cur.append(ch)
        elif ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif depth == 0 and text[i:i + len(sep)].upper() == sep:
            parts.append("".join(cur))
            cur = []
            i += len(sep)
            continue
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def strip_parens(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        for i, ch in enumerate(text):
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0 and i != len(text) - 1:
                return text
        text = text[1:-1].strip()
    return text


def where_parts(text: str) -> list[str]:
    out = []
    for part in split_top(strip_parens(text), " AND "):
        inner = strip_parens(part)
        if inner != part.strip() and len(split_top(inner, " AND ")) > 1:
            out.extend(where_parts(inner))
        else:
            out.append(inner)
    return out


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

COLUMN_LINE = re.compile(r"^\s*`(\w+)`\s+(.*?),?\s*$")
DEFAULT_RE = re.compile(r"\bDEFAULT\s+(NULL|'(?:[^'\\]|\\.)*'|[-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
KEY_LINE = re.compile(r"^\s*(PRIMARY KEY|UNIQUE KEY\s+`\w+`)\s*\(([^)]*)\)", re.IGNORECASE)


@dataclass
class Schema:
    name: str
    columns: list[str]
    defaults: dict[str, object]
    key: tuple[str, ...]
    types: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"columns": self.columns, "key": list(self.key),
                "types": self.types}


def parse_create_tables(dump_text: str, wanted: set[str]) -> dict[str, Schema]:
    """Read the ``CREATE TABLE`` blocks of ``wanted`` tables from a mysqldump."""
    out: dict[str, Schema] = {}
    pos = 0
    while True:
        start = dump_text.find("CREATE TABLE `", pos)
        if start < 0:
            break
        end = dump_text.find("\n) ENGINE", start)
        block = dump_text[start:end if end > 0 else start + 20000]
        pos = start + 14
        name = block[len("CREATE TABLE `"):block.index("`", len("CREATE TABLE `"))]
        if name not in wanted:
            continue
        columns, defaults, types = [], {}, {}
        key: tuple[str, ...] | None = None
        for line in block.splitlines()[1:]:
            km = KEY_LINE.match(line)
            if km:
                cols = tuple(c.strip().strip("`") for c in km.group(2).split(","))
                if key is None or km.group(1).upper().startswith("PRIMARY"):
                    key = cols
                continue
            cm = COLUMN_LINE.match(line)
            if not cm:
                continue
            col, rest = cm.group(1), cm.group(2)
            columns.append(col)
            types[col] = rest.split(" ")[0]
            dm = DEFAULT_RE.search(rest)
            if dm:
                raw = dm.group(1)
                if raw.upper() == "NULL":
                    defaults[col] = None
                elif raw.startswith("'"):
                    inner = raw[1:-1]
                    try:
                        defaults[col] = float(inner) if "." in inner else int(inner)
                    except ValueError:
                        defaults[col] = inner
                else:
                    defaults[col] = float(raw) if "." in raw else int(raw)
            elif "NOT NULL" not in rest.upper():
                defaults[col] = None
        if key is None:
            key = tuple(columns)  # whole-row identity
        out[name] = Schema(name, columns, defaults, key, types)
    return out


# ---------------------------------------------------------------------------
# table state
# ---------------------------------------------------------------------------

class TableState:
    def __init__(self, schema: Schema) -> None:
        self.schema = schema
        self.name = schema.name
        self.columns = schema.columns
        self.key = schema.key
        self.rows: dict[tuple, dict] = {}
        self._lower = {c.lower(): c for c in self.columns}

    def col(self, name: str) -> str:
        """MySQL column names are case-insensitive; map to the schema spelling."""
        if name in self._lower:
            return self._lower[name]
        try:
            return self._lower[name.lower()]
        except KeyError:
            raise ValueError(f"{self.name}: unknown column {name}") from None

    def _row(self, columns: list[str], values: list[object]) -> dict:
        if len(columns) != len(values):
            raise ValueError(f"{self.name}: {len(columns)} columns vs {len(values)} values")
        row = {c: self.schema.defaults.get(c, 0) for c in self.columns}
        for c, v in zip(columns, values):
            row[self.col(c)] = v
        return row

    def insert(self, columns: list[str], tuples: list[list[object]], mode: str) -> int:
        count = 0
        for values in tuples:
            row = self._row(columns, values)
            key = tuple(row[k] for k in self.key)
            if key in self.rows and mode == "INSERT":
                raise ValueError(f"{self.name}: duplicate key {key} on INSERT")
            if key in self.rows and mode == "INSERT IGNORE":
                continue
            self.rows[key] = row
            count += 1
        return count

    def matches(self, row: dict, where: list[dict[str, set]]) -> bool:
        for conj in where:
            if all(row[self.col(col)] in values for col, values in conj.items()):
                return True
        return False

    def delete(self, where: list[dict[str, set]]) -> int:
        doomed = [k for k, r in self.rows.items() if self.matches(r, where)]
        for k in doomed:
            del self.rows[k]
        return len(doomed)

    def update(self, assigns: dict[str, object], where: list[dict[str, set]]) -> int:
        """``assigns`` values are literals or :class:`SetExpr` (evaluated per row)."""
        hit = [k for k, r in self.rows.items() if self.matches(r, where)]
        for k in hit:
            row = self.rows.pop(k)
            for col, value in assigns.items():
                name = self.col(col)
                row[name] = value.evaluate(row, self) if isinstance(value, SetExpr) else value
            self.rows[tuple(row[c] for c in self.key)] = row
        return len(hit)


class SetExpr:
    """A restricted ``SET col = <expr>`` right-hand side.

    Grammar: integers/floats/hex, ``@vars``, backticked column references,
    ``| & ~ + - * /`` and parentheses.  MySQL integer semantics are emulated as
    Python ints; ``/`` is true division (MySQL returns DECIMAL for ``16/7``).
    """

    TOK = re.compile(r"\s*(?:(?P<num>0x[0-9A-Fa-f]+|\d+\.\d*|\.\d+|\d+)|(?P<col>`\w+`|[A-Za-z_]\w*)|(?P<var>@\w+)|(?P<op>[-+*/|&~()]))")

    def __init__(self, text: str, session: "Session") -> None:
        self.text = text
        self.tokens: list[tuple[str, object]] = []
        pos = 0
        text = text.strip()
        while pos < len(text):
            m = self.TOK.match(text, pos)
            if not m or m.end() == pos:
                raise ValueError(f"unsupported SET expression {text!r}")
            pos = m.end()
            if m.group("num"):
                raw = m.group("num")
                self.tokens.append(("num", int(raw, 16) if raw.startswith("0x") else
                                    (float(raw) if "." in raw else int(raw))))
            elif m.group("col"):
                self.tokens.append(("col", m.group("col").strip("`")))
            elif m.group("var"):
                self.tokens.append(("num", session.eval_var(m.group("var"))))
            else:
                self.tokens.append(("op", m.group("op")))
        if not self.tokens:
            raise ValueError("empty SET expression")

    def evaluate(self, row: dict, table: "TableState") -> object:
        toks = self.tokens
        pos = [0]

        def peek():
            return toks[pos[0]] if pos[0] < len(toks) else ("end", None)

        def take():
            t = toks[pos[0]]
            pos[0] += 1
            return t

        def primary():
            kind, val = take()
            if kind == "num":
                return val
            if kind == "col":
                return row[table.col(val)]
            if kind == "op" and val == "(":
                v = expr_or()
                if take() != ("op", ")"):
                    raise ValueError("unbalanced parenthesis in SET expression")
                return v
            if kind == "op" and val == "~":
                return ~int(primary())
            if kind == "op" and val == "-":
                return -primary()
            raise ValueError(f"bad token {kind}:{val} in SET expression")

        def expr_mul():
            v = primary()
            while peek() in (("op", "*"), ("op", "/")):
                op = take()[1]
                r = primary()
                v = v * r if op == "*" else v / r
            return v

        def expr_add():
            v = expr_mul()
            while peek() in (("op", "+"), ("op", "-")):
                op = take()[1]
                r = expr_mul()
                v = v + r if op == "+" else v - r
            return v

        def expr_and():
            v = expr_add()
            while peek() == ("op", "&"):
                take()
                v = int(v) & int(expr_add())
            return v

        def expr_or():
            v = expr_and()
            while peek() == ("op", "|"):
                take()
                v = int(v) | int(expr_and())
            return v

        value = expr_or()
        if peek() != ("end", None):
            raise ValueError(f"trailing tokens in SET expression {self.text!r}")
        return value


class Replay:
    """Apply statements to a set of tracked tables."""

    def __init__(self, schemas: dict[str, Schema]) -> None:
        self.state = {name: TableState(s) for name, s in schemas.items()}
        self.touch = re.compile(r"`?(" + "|".join(re.escape(n) for n in schemas) + r")`?", re.IGNORECASE)
        self.session = Session()

    def touches(self, text: str) -> bool:
        return bool(self.touch.search(text))

    def apply(self, stmt: str) -> tuple[str, int] | None:
        """(verb, affected) for a tracked-table statement, None otherwise; ValueError if unsupported."""
        s = self.session
        var = SET_VAR.match(stmt)
        if var:
            expr = var.group(2).strip()
            try:
                s.variables[var.group(1)] = int(expr, 0) if not expr.startswith("@") else s.eval_var(expr)
            except ValueError:
                s.variables.pop(var.group(1), None)
            return None
        if not self.touch.search(stmt[:200]):
            return None
        m = INSERT.match(stmt)
        if m and m.group(2) in self.state:
            verb = " ".join(m.group(1).upper().split())
            table = self.state[m.group(2)]
            columns = ([c.strip().strip("`") for c in m.group(4).split(",")]
                       if m.group(3) else table.columns)
            return verb, table.insert(columns, s.parse_tuples(m.group(5)),
                                      "REPLACE" if verb == "REPLACE" else verb)
        m = DELETE.match(stmt)
        if m and m.group(1) in self.state:
            return "DELETE", self.state[m.group(1)].delete(s.parse_where(m.group(2)))
        m = UPDATE.match(stmt)
        if m and m.group(1) in self.state:
            assigns: dict[str, object] = {}
            for part in split_top(" ".join(m.group(2).split()), ","):
                a = ASSIGN.match(part)
                if a:
                    assigns[a.group(1)] = s.literal(a.group(2))
                    continue
                e = ASSIGN_EXPR.match(part)
                if not e:
                    raise ValueError(f"unsupported SET clause {part.strip()!r}")
                assigns[e.group(1)] = SetExpr(e.group(2), s)
            return "UPDATE", self.state[m.group(1)].update(assigns, s.parse_where(m.group(3)))
        if re.match(r"^(CREATE|DROP|LOCK|UNLOCK|SET)\b", stmt, re.IGNORECASE):
            return None
        alter = re.match(r"^ALTER\s+TABLE\s+`?(\w+)`?", stmt, re.IGNORECASE)
        if alter:
            if alter.group(1) in self.state:
                raise ValueError("ALTER TABLE on a tracked table (schema drift)")
            return None
        target = re.match(r"^(?:INSERT(?:\s+IGNORE)?\s+INTO|REPLACE\s+INTO|DELETE\s+FROM|UPDATE)\s+`?(\w+)`?",
                          stmt, re.IGNORECASE)
        if target and target.group(1) not in self.state:
            return None
        raise ValueError("unsupported statement shape")


def replay_dump_lines(replay: Replay, dump_text: str, unparsed: list[dict], counter, label: str) -> None:
    """Apply every ``INSERT INTO \\`tracked\\`` line of a mysqldump."""
    for line in dump_text.splitlines():
        if not line.startswith("INSERT INTO `"):
            continue
        name = line[len("INSERT INTO `"):line.index("`", len("INSERT INTO `"))]
        if name not in replay.state:
            continue
        for stmt in split_statements(line):
            try:
                result = replay.apply(stmt)
                if result:
                    counter[("base", name, result[0])] += result[1]
            except ValueError as exc:
                unparsed.append({"file": label, "error": str(exc), "statement": stmt[:300]})


def replay_updates(replay: Replay, update_dir: Path, unparsed: list[dict], counter) -> tuple[list[str], int]:
    files = sorted(update_dir.glob("*.sql"), key=lambda p: p.name)
    touched_files = []
    for path in files:
        replay.session.variables.clear()
        text = path.read_text(encoding="utf-8", errors="replace")
        if not replay.touches(text):
            continue
        touched = False
        for stmt in split_statements(text):
            try:
                result = replay.apply(stmt)
            except ValueError as exc:
                if replay.touches(stmt[:200]):
                    flat = " ".join(stmt.split())
                    unparsed.append({"file": path.name, "error": str(exc), "statement": flat[:300]})
                continue
            if result:
                touched = True
                counter[("update", result[0])] += result[1]
        if touched:
            touched_files.append(path.name)
    return touched_files, len(files)
