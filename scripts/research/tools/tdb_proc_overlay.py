#!/usr/bin/env python3
"""Extract TrinityCore's world-database proc overlay into a small JSON corpus.

TrinityCore does not take proc policy from DB2 alone.  Five world-database
tables change or extend it (consumers in brackets):

  spell_proc               [SpellMgr::LoadSpellProcs]         proc entry override
  spell_enchant_proc_data  [SpellMgr::LoadSpellEnchantProcData] enchant proc policy
  item_template_addon      [ObjectMgr::LoadItemTemplateAddon]  SpellPPMChance (legacy item PPM)
  spell_custom_attr        [SpellMgr::LoadSpellInfoCustomAttributes] AttributesCu
  conditions (source 24)   [Aura::GetProcEffectMask]           CONDITION_SOURCE_TYPE_SPELL_PROC
  spell_script_names       [ObjectMgr::LoadSpellScriptNames]   script binding (proc hooks)

None of this lives in ``data/tables``.  The checkout pins its base world
database in ``revision_data.h.in.cmake`` (``DATABASE_FULL_DATABASE``); this tool
reads that dump (published as a TDB GitHub release) and replays every
``sql/updates/world/master`` file in the order ``UpdateFetcher`` applies them
(``PathCompare``: file name, lexicographic).

The replay is a deliberately *restricted* SQL interpreter: INSERT/REPLACE with
or without a column list, DELETE ... WHERE and UPDATE ... SET ... WHERE with
conjunctions of ``col = literal`` / ``col IN (...)``.  Any statement that
touches a tracked table in any other shape is recorded under ``unparsed`` and
counted -- never guessed.

The script-hook scan reads ``src/server/scripts/**/*.cpp`` and maps each
registered script name to the proc hooks its classes install
(``DoCheckProc``, ``OnEffectProc``, ...).  This is lexical evidence, not
execution: it answers "does a bespoke consumer exist?", never "what does it do?".

Usage::

    # the dump named in revision_data.h.in.cmake, from the TDB release
    python3 scripts/research/tools/tdb_proc_overlay.py \\
        --tdb ../bag/tdb/TDB_full_world_1200.26021_2026_02_06.sql
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

HERE = Path(__file__).resolve()
WOWLAB_DATA = HERE.parents[3]
WORKSPACE_PARENT = WOWLAB_DATA.parent
DEFAULT_OUT = WOWLAB_DATA / "docs/research/procs-corpora/trinity-world-overlay.json"

TABLES = {
    "spell_proc": ["SpellId", "SchoolMask", "SpellFamilyName", "SpellFamilyMask0",
                   "SpellFamilyMask1", "SpellFamilyMask2", "SpellFamilyMask3",
                   "ProcFlags", "ProcFlags2", "SpellTypeMask", "SpellPhaseMask",
                   "HitMask", "AttributesMask", "DisableEffectsMask",
                   "ProcsPerMinute", "Chance", "Cooldown", "Charges"],
    "spell_enchant_proc_data": ["EnchantID", "Chance", "ProcsPerMinute", "HitMask",
                                "AttributesMask"],
    "spell_custom_attr": ["entry", "attributes"],
    "item_template_addon": ["Id", "FlagsCu", "FoodType", "MinMoneyLoot",
                            "MaxMoneyLoot", "SpellPPMChance",
                            "RandomBonusListTemplateId", "QuestLogItemId"],
    "spell_script_names": ["spell_id", "ScriptName"],
    "conditions": ["SourceTypeOrReferenceId", "SourceGroup", "SourceEntry",
                   "SourceId", "ElseGroup", "ConditionTypeOrReference",
                   "ConditionTarget", "ConditionValue1", "ConditionValue2",
                   "ConditionValue3", "ConditionStringValue1",
                   "NegativeCondition", "ErrorType", "ErrorTextId",
                   "ScriptName", "Comment"],
}
#: Primary key per table (None = whole-row identity).  From the dump's
#: ``PRIMARY KEY``/``UNIQUE KEY`` clauses.
KEYS = {
    "spell_proc": ("SpellId",),
    "spell_enchant_proc_data": ("EnchantID",),
    "spell_custom_attr": ("entry",),
    "item_template_addon": ("Id",),
    "spell_script_names": ("spell_id", "ScriptName"),
    "conditions": ("SourceTypeOrReferenceId", "SourceGroup", "SourceEntry",
                   "SourceId", "ElseGroup", "ConditionTypeOrReference",
                   "ConditionTarget", "ConditionValue1", "ConditionValue2",
                   "ConditionValue3", "ConditionStringValue1"),
}
DEFAULTS = {"Comment": None, "ConditionStringValue1": "", "ScriptName": ""}
CONDITION_SOURCE_TYPE_SPELL_PROC = 24  # ConditionMgr.h


# ---------------------------------------------------------------------------
# lexing
# ---------------------------------------------------------------------------

def split_statements(text: str) -> list[str]:
    """Split on ``;`` outside strings, dropping ``--``/``#`` and ``/* */`` comments.

    MySQL conditional comments (``/*!40000 ... */``) are dropped too: in the
    dump they only toggle keys/charsets.
    """
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
                if i + 1 < n and text[i + 1] == quote:  # doubled quote
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


#: Session variables from ``SET @X := <int>`` statements, per update file.
VARIABLES: dict[str, int] = {}
SET_VAR = re.compile(r"^SET\s+(@\w+)\s*:?=\s*(.+)$", re.IGNORECASE | re.DOTALL)


def eval_var(text: str) -> int:
    m = re.fullmatch(r"(@\w+)(?:\s*([-+])\s*(\d+))?", text.strip())
    if not m or m.group(1) not in VARIABLES:
        raise ValueError(f"unknown variable expression {text!r}")
    value = VARIABLES[m.group(1)]
    if m.group(2):
        value = value + int(m.group(3)) if m.group(2) == "+" else value - int(m.group(3))
    return value


def parse_literal(token: re.Match) -> object:
    if token.group("hex") is not None:
        return int(token.group("hex"), 16)
    if token.group("bor") is not None:
        value = 0
        for part in token.group("bor").split("|"):
            value |= int(part)
        return value
    if token.group("var") is not None:
        return eval_var(token.group("var"))
    if token.group("str") is not None:
        quote = token.group("str")[0]
        raw = token.group("str")[1:-1]
        return re.sub(r"\\(.)|''|\"\"", lambda m: m.group(1) if m.group(1) else quote, raw)
    if token.group("num") is not None:
        text = token.group("num")
        return float(text) if any(c in text for c in ".eE") else int(text)
    return None


def parse_tuples(text: str) -> list[list[object]]:
    """``(a,b),(c,d)`` -> [[a,b],[c,d]]; raises ValueError on anything else."""
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
            row.append(parse_literal(m))
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


IDENT = r"`?(\w+)`?"
INSERT = re.compile(
    rf"^(INSERT(?:\s+IGNORE)?|REPLACE)\s+INTO\s+{IDENT}\s*(\(([^)]*)\))?\s*VALUES?\s*(.*)$",
    re.IGNORECASE | re.DOTALL)
DELETE = re.compile(rf"^DELETE\s+FROM\s+{IDENT}\s+WHERE\s+(.*)$", re.IGNORECASE | re.DOTALL)
UPDATE = re.compile(rf"^UPDATE\s+{IDENT}\s+SET\s+(.*?)\s+WHERE\s+(.*)$",
                    re.IGNORECASE | re.DOTALL)
_LIT = r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|0x[0-9A-Fa-f]+|@\w+(?:\s*[-+]\s*\d+)?|[-+]?\d+(?:\.\d+)?"
COND = re.compile(
    rf"^\s*{IDENT}\s*(?:(=)\s*({_LIT})|IN\s*\(([^)]*)\))\s*$",
    re.IGNORECASE)
ASSIGN = re.compile(rf"^\s*{IDENT}\s*=\s*({_LIT})\s*$")
TOUCH = re.compile(r"`?(" + "|".join(TABLES) + r")`?", re.IGNORECASE)


def literal(text: str) -> object:
    text = text.strip()
    m = TOKEN.match(text)
    if not m or m.group("punct") or m.end() != len(text):
        raise ValueError(f"bad literal {text!r}")
    return parse_literal(m)


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


def parse_where(text: str) -> list[dict[str, set]]:
    """WHERE -> disjunction of conjunctions (only top-level ``OR`` of ``AND`` groups)."""
    text = " ".join(text.split())
    disjuncts = split_top(strip_parens(text), " OR ")
    if len(disjuncts) > 1:
        return [c for d in disjuncts for c in parse_where(d)]
    return [parse_conjunction(text)]


def parse_conjunction(text: str) -> dict[str, set]:
    if re.search(r"\bOR\b", strip_parens(text), re.IGNORECASE):
        raise ValueError("nested OR in WHERE is not supported")
    clauses: dict[str, set] = {}
    for part in where_parts(text):
        m = COND.match(part)
        if not m:
            raise ValueError(f"unsupported WHERE clause {part.strip()!r}")
        col = m.group(1)
        if m.group(2):
            values = {literal(m.group(3))}
        else:
            values = {parse_literal(t) for t in TOKEN.finditer(m.group(4))
                      if t.group("punct") is None}
        if col in clauses:
            clauses[col] &= values
        else:
            clauses[col] = values
    return clauses


# ---------------------------------------------------------------------------
# table state
# ---------------------------------------------------------------------------

class TableState:
    def __init__(self, name: str) -> None:
        self.name = name
        self.columns = TABLES[name]
        self.key = KEYS[name]
        self.rows: dict[tuple, dict] = {}

    def _row(self, columns: list[str], values: list[object]) -> dict:
        if len(columns) != len(values):
            raise ValueError(f"{self.name}: {len(columns)} columns vs {len(values)} values")
        row = {c: DEFAULTS.get(c, 0) for c in self.columns}
        for c, v in zip(columns, values):
            if c not in row:
                raise ValueError(f"{self.name}: unknown column {c}")
            row[c] = v
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
            for col in conj:
                if col not in row:
                    raise ValueError(f"{self.name}: unknown WHERE column {col}")
            if all(row[col] in values for col, values in conj.items()):
                return True
        return False

    def delete(self, where: list[dict[str, set]]) -> int:
        doomed = [k for k, r in self.rows.items() if self.matches(r, where)]
        for k in doomed:
            del self.rows[k]
        return len(doomed)

    def update(self, assigns: dict[str, object], where: list[dict[str, set]]) -> int:
        hit = [k for k, r in self.rows.items() if self.matches(r, where)]
        for k in hit:
            row = self.rows.pop(k)
            row.update(assigns)
            self.rows[tuple(row[c] for c in self.key)] = row
        return len(hit)


def may_affect_output(stmt: str) -> bool:
    """Conservative: only a ``conditions`` statement that pins its source type
    to something other than 24 is known not to affect the emitted corpus."""
    target = re.match(r"^\w+(?:\s+IGNORE)?\s+(?:INTO|FROM)?\s*`?(\w+)`?", stmt, re.IGNORECASE)
    if target and target.group(1) == "conditions":
        types = re.findall(r"`?SourceTypeOrReferenceId`?\s*=\s*(\d+)", stmt)
        if types and all(int(x) != CONDITION_SOURCE_TYPE_SPELL_PROC for x in types):
            return False
        m = re.search(r"\bVALUES?\b", stmt, re.IGNORECASE)
        if stmt.upper().startswith(("INSERT", "REPLACE")) and m:
            values = stmt[m.end():]
            firsts = re.findall(r"\(\s*(-?\d+)\s*,", values)
            if firsts and all(int(x) != CONDITION_SOURCE_TYPE_SPELL_PROC for x in firsts):
                return False
    return True


def apply_statement(stmt: str, state: dict[str, TableState]) -> tuple[str, int] | None:
    """Apply one statement if it touches a tracked table.

    Returns (verb, affected) or None if the statement does not touch a tracked
    table.  Raises ValueError for a tracked-table statement of unsupported shape.
    """
    var = SET_VAR.match(stmt)
    if var:
        expr = var.group(2).strip()
        try:
            VARIABLES[var.group(1)] = int(expr, 0) if not expr.startswith("@") else eval_var(expr)
        except ValueError:
            VARIABLES.pop(var.group(1), None)
        return None
    head = stmt[:200]
    touched = {m.group(1).lower() for m in TOUCH.finditer(head)}
    if not touched:
        return None
    m = INSERT.match(stmt)
    if m and m.group(2) in state:
        verb = " ".join(m.group(1).upper().split())
        table = state[m.group(2)]
        columns = ([c.strip().strip("`") for c in m.group(4).split(",")]
                   if m.group(3) else table.columns)
        return verb, table.insert(columns, parse_tuples(m.group(5)),
                                  "REPLACE" if verb == "REPLACE" else verb)
    m = DELETE.match(stmt)
    if m and m.group(1) in state:
        return "DELETE", state[m.group(1)].delete(parse_where(m.group(2)))
    m = UPDATE.match(stmt)
    if m and m.group(1) in state:
        assigns = {}
        for part in split_top(" ".join(m.group(2).split()), ","):
            a = ASSIGN.match(part)
            if not a:
                raise ValueError(f"unsupported SET clause {part.strip()!r}")
            assigns[a.group(1)] = literal(a.group(2))
        return "UPDATE", state[m.group(1)].update(assigns, parse_where(m.group(3)))
    if re.match(r"^(CREATE|DROP|ALTER|LOCK|UNLOCK|SET)\b", stmt, re.IGNORECASE):
        return None
    # A statement that merely *mentions* a tracked table in a subquery of an
    # untracked table (e.g. INSERT INTO other SELECT ... FROM spell_proc) does
    # not modify it; anything whose target is tracked and not parsed above is
    # an error.
    target = re.match(r"^(?:INSERT(?:\s+IGNORE)?\s+INTO|REPLACE\s+INTO|DELETE\s+FROM|UPDATE)\s+`?(\w+)`?",
                      stmt, re.IGNORECASE)
    if target and target.group(1) not in state:
        return None
    raise ValueError("unsupported statement shape")


# ---------------------------------------------------------------------------
# script hook scan
# ---------------------------------------------------------------------------

HOOKS = ("DoCheckProc", "DoCheckEffectProc", "DoPrepareProc", "OnProc",
         "AfterProc", "OnEffectProc", "AfterEffectProc")
HOOK_RE = re.compile(r"\b(" + "|".join(HOOKS) + r")\s*\+=\s*(\w+)\(([^;]*)\)\s*;")
CLASS_RE = re.compile(r"\b(?:class|struct)\s+(\w+)\s*(?:final\s*)?:\s*public\s+([\w:<>]+)[^{;]*\{")
REG_RE = re.compile(
    r"\b(RegisterSpellScript|RegisterSpellScriptWithArgs|RegisterSpellAndAuraScriptPair|"
    r"RegisterSpellAndAuraScriptPairWithArgs)\s*\((.*?)\)\s*;", re.DOTALL)
NEW_LOADER_RE = re.compile(r"\bnew\s+(\w+)\s*\(\s*\"([^\"]+)\"")


def brace_body(text: str, open_index: int) -> str:
    depth = 0
    for pos in range(open_index, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return text[open_index:pos + 1]
    return text[open_index:]


def strip_cpp_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text)


def bare_class(token: str) -> str:
    token = token.strip()
    while token.startswith("(") and token.endswith(")"):
        token = token[1:-1].strip()
    return re.sub(r"<.*", "", token).strip()


def scan_scripts(tc_root: Path) -> dict:
    classes: dict[str, dict] = {}
    registrations: list[tuple[str, list[str], str]] = []
    root = tc_root / "src/server/scripts"
    for path in sorted(root.rglob("*.cpp")):
        text = strip_cpp_comments(path.read_text(encoding="utf-8", errors="replace"))
        rel = str(path.relative_to(tc_root))
        for m in CLASS_RE.finditer(text):
            body = brace_body(text, m.end() - 1)
            hooks = []
            for h in HOOK_RE.finditer(body):
                args = [a.strip() for a in h.group(3).split(",")]
                hooks.append({"hook": h.group(1), "args": args[1:]})
            info = classes.setdefault(m.group(1), {"file": rel, "base": m.group(2),
                                                    "hooks": [], "prevents_default": False})
            info["hooks"].extend(hooks)
            info["prevents_default"] |= "PreventDefaultAction" in body
        for m in REG_RE.finditer(text):
            args = split_top(m.group(2), ",")
            macro = m.group(1)
            if macro == "RegisterSpellScript":
                cls = [bare_class(args[0])]
                name = cls[0]
            elif macro == "RegisterSpellScriptWithArgs":
                cls = [bare_class(args[0])]
                name = args[1].strip().strip('"')
            elif macro == "RegisterSpellAndAuraScriptPair":
                cls = [bare_class(args[0]), bare_class(args[1])]
                name = cls[0]
            else:
                cls = [bare_class(args[0]), bare_class(args[1])]
                name = args[2].strip().strip('"')
            registrations.append((name, cls, rel))
        for m in NEW_LOADER_RE.finditer(text):
            registrations.append((m.group(2), [m.group(1)], rel))

    scripts: dict[str, dict] = {}
    for name, cls, rel in registrations:
        entry = scripts.setdefault(name, {"classes": [], "files": [], "hooks": [],
                                          "effect_proc_auras": [], "prevents_default": False,
                                          "resolved": True})
        for c in cls:
            if c == "void":
                continue
            info = classes.get(c)
            if info is None:
                entry["resolved"] = False
                continue
            if c not in entry["classes"]:
                entry["classes"].append(c)
            if info["file"] not in entry["files"]:
                entry["files"].append(info["file"])
            for h in info["hooks"]:
                if h["hook"] not in entry["hooks"]:
                    entry["hooks"].append(h["hook"])
                if h["hook"] in ("OnEffectProc", "AfterEffectProc", "DoCheckEffectProc") and len(h["args"]) >= 2:
                    pair = f"{h['args'][0]}:{h['args'][1]}"
                    if pair not in entry["effect_proc_auras"]:
                        entry["effect_proc_auras"].append(pair)
            entry["prevents_default"] |= info["prevents_default"]
    for entry in scripts.values():
        entry["hooks"].sort()
        entry["effect_proc_auras"].sort()
    return scripts


# ---------------------------------------------------------------------------
# in-code SpellInfo corrections
# ---------------------------------------------------------------------------

FIX_RE = re.compile(r"\bApplySpellFix\(\s*\{([^}]*)\}\s*,")
MEMBER_RE = re.compile(r"\b(spellInfo|spellEffectInfo)->(\w+)")


def scan_corrections(tc_root: Path) -> dict:
    """``SpellMgr::LoadSpellInfoCorrections``: spell id -> SpellInfo members written.

    Lexical: every ``spellInfo->X`` / ``spellEffectInfo->X`` token inside the
    fix lambda is reported (reads and writes alike).  Consumers decide which
    members matter; nothing here applies a correction.
    """
    path = tc_root / "src/server/game/Spells/SpellMgr.cpp"
    text = strip_cpp_comments(path.read_text(encoding="utf-8"))
    start = text.index("void SpellMgr::LoadSpellInfoCorrections()")
    body = brace_body(text, text.index("{", start))
    out: dict[str, set] = {}
    for m in FIX_RE.finditer(body):
        ids = [int(x) for x in re.findall(r"\d+", m.group(1))]
        lam = body.index("{", m.end())
        members = {f"{a}->{b}" for a, b in MEMBER_RE.findall(brace_body(body, lam))}
        for spell_id in ids:
            out.setdefault(str(spell_id), set()).update(members)
    return {k: sorted(v) for k, v in sorted(out.items(), key=lambda kv: int(kv[0]))}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def git(tc_root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tc_root), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tdb", type=Path, required=True,
                        help="TDB_full_world_*.sql named by revision_data.h.in.cmake")
    parser.add_argument("--tc-root", type=Path, default=WORKSPACE_PARENT / "TrinityCore")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    tc_root = args.tc_root.resolve()
    pinned = re.search(r'DATABASE_FULL_DATABASE\s+"([^"]+)"',
                       (tc_root / "revision_data.h.in.cmake").read_text()).group(1)
    if args.tdb.name != pinned:
        print(f"refusing: checkout pins {pinned}, got {args.tdb.name}", file=sys.stderr)
        return 2

    state = {name: TableState(name) for name in TABLES}
    unparsed: list[dict] = []
    verbs: Counter = Counter()

    base_text = args.tdb.read_text(encoding="utf-8", errors="replace")
    base_sha = hashlib.sha256(base_text.encode("utf-8", errors="replace")).hexdigest()
    for line in base_text.splitlines():
        if not line.startswith("INSERT INTO `"):
            continue
        name = line[len("INSERT INTO `"):line.index("`", len("INSERT INTO `"))]
        if name not in state:
            continue
        for stmt in split_statements(line):
            try:
                result = apply_statement(stmt, state)
                if result:
                    verbs[("base", name, result[0])] += result[1]
            except ValueError as exc:
                unparsed.append({"file": args.tdb.name, "error": str(exc), "statement": stmt[:300]})
    base_counts = {name: len(t.rows) for name, t in state.items()}
    del base_text

    update_dir = tc_root / "sql/updates/world/master"
    files = sorted(update_dir.glob("*.sql"), key=lambda p: p.name)
    touched_files = []
    for path in files:
        VARIABLES.clear()
        text = path.read_text(encoding="utf-8", errors="replace")
        if not TOUCH.search(text):
            continue
        touched = False
        for stmt in split_statements(text):
            try:
                result = apply_statement(stmt, state)
            except ValueError as exc:
                if TOUCH.search(stmt[:200]):
                    flat = " ".join(stmt.split())
                    unparsed.append({"file": path.name, "error": str(exc),
                                     "may_affect_output": may_affect_output(flat),
                                     "statement": flat[:300]})
                continue
            if result:
                touched = True
                verbs[("update", result[0])] += result[1]
        if touched:
            touched_files.append(path.name)

    proc_rows = sorted((r for r in state["spell_proc"].rows.values()), key=lambda r: r["SpellId"])
    enchant_rows = sorted(state["spell_enchant_proc_data"].rows.values(), key=lambda r: r["EnchantID"])
    custom = sorted([r["entry"], r["attributes"]] for r in state["spell_custom_attr"].rows.values())
    item_ppm = sorted([r["Id"], r["SpellPPMChance"]] for r in state["item_template_addon"].rows.values()
                       if r["SpellPPMChance"])
    names = sorted([r["spell_id"], r["ScriptName"]] for r in state["spell_script_names"].rows.values())
    conditions = sorted(
        [r["SourceEntry"], r["SourceGroup"], r["ElseGroup"], r["ConditionTypeOrReference"],
          r["ConditionTarget"], r["ConditionValue1"], r["ConditionValue2"], r["ConditionValue3"],
          r["NegativeCondition"]]
         for r in state["conditions"].rows.values()
         if r["SourceTypeOrReferenceId"] == CONDITION_SOURCE_TYPE_SPELL_PROC)
    used_scripts = {n for _, n in names}
    scripts = {k: v for k, v in scan_scripts(tc_root).items() if k in used_scripts}

    payload = {
        "provenance": {
            "trinitycore_commit": git(tc_root, "rev-parse", "HEAD"),
            "base_world_database": pinned,
            "base_world_database_sha256": base_sha,
            "tdb_release": "TDB1200.26021 (TDB_full_1200.26021_2026_02_06.7z, "
                           "sha256 48f0e2af7620ca70ec7054ff19d356255bc50e11d7829932015f28918f195588)",
            "update_directory": "sql/updates/world/master",
            "update_files_total": len(files),
            "update_files_touching_tracked_tables": touched_files,
            "base_row_counts": base_counts,
            "final_row_counts": {name: len(t.rows) for name, t in state.items()},
            "applied": {" / ".join(map(str, k)): v for k, v in sorted(verbs.items(), key=str)},
            "unparsed_statement_count": len(unparsed),
            "unparsed_may_affect_output": sum(1 for u in unparsed if u.get("may_affect_output", True)),
            "note": "restricted replay; see tools/tdb_proc_overlay.py",
        },
        "unparsed": unparsed,
        "spell_proc_columns": TABLES["spell_proc"],
        "spell_proc": [[r[c] for c in TABLES["spell_proc"]] for r in proc_rows],
        "spell_enchant_proc_data_columns": TABLES["spell_enchant_proc_data"],
        "spell_enchant_proc_data": [[r[c] for c in TABLES["spell_enchant_proc_data"]] for r in enchant_rows],
        "spell_custom_attr": custom,
        "item_template_addon_spell_ppm": item_ppm,
        "spell_script_names": names,
        "spell_proc_conditions_columns": ["SourceEntry", "SourceGroup", "ElseGroup",
                                          "ConditionTypeOrReference", "ConditionTarget",
                                          "ConditionValue1", "ConditionValue2",
                                          "ConditionValue3", "NegativeCondition"],
        "spell_proc_conditions": conditions,
        "scripts": dict(sorted(scripts.items())),
        "spell_info_corrections": scan_corrections(tc_root),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=False) + "\n",
                        encoding="utf-8")
    print(f"wrote {args.out}")
    print(json.dumps(payload["provenance"]["final_row_counts"]))
    print(f"unparsed tracked statements: {len(unparsed)} "
          f"(may affect output: {payload['provenance']['unparsed_may_affect_output']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
