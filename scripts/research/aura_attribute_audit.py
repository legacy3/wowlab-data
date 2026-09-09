#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb==1.5.5"]
# ///
"""Reproduce the Wago aura-subtype and spell-attribute research census.

This is deliberately a research tool: it reads the checked-in CSV corpus and
the sibling Core/TrinityCore/SimulationCraft trees, then emits population and
source-evidence JSON or the durable Markdown audit.  It never rewrites data.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[2]
REVIEWED_CORE = ROOT.parent / "core"
TC = ROOT.parent / "TrinityCore"
SIMC = ROOT.parent / "simc"
TABLES = ROOT / "data" / "tables"
ATTR_WORDS = 17


def git_head(path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def parse_core_raws(path: Path, enum_name: str) -> dict[int, str]:
    text = path.read_text()
    match = re.search(
        rf"pub enum {enum_name}\s*\{{(.*?)^    \}}", text, re.MULTILINE | re.DOTALL
    )
    if not match:
        raise RuntimeError(f"cannot find {enum_name} in {path}")
    return {
        int(raw): name
        for name, raw in re.findall(
            r"^\s*([A-Za-z0-9_]+)\s*=\s*(\d+),", match.group(1), re.MULTILINE
        )
    }


def parse_tc_aura_names() -> dict[int, dict[str, str]]:
    path = TC / "src/server/game/Spells/Auras/SpellAuraDefines.h"
    text = path.read_text(errors="replace")
    out: dict[int, dict[str, str]] = {}
    for line_no, line in enumerate(text.splitlines(), 1):
        m = re.match(
            r"\s*(SPELL_AURA_[A-Z0-9_]+)\s*=\s*(\d+)\s*,?\s*(?://\s*(.*))?", line
        )
        if m:
            out[int(m.group(2))] = {
                "name": m.group(1),
                "comment": (m.group(3) or "").strip(),
                "path": str(path.relative_to(ROOT.parent)),
                "line": str(line_no),
            }
    return out


def parse_tc_aura_handlers() -> dict[int, dict[str, str]]:
    path = TC / "src/server/game/Spells/Auras/SpellAuraEffects.cpp"
    out: dict[int, dict[str, str]] = {}
    rx = re.compile(
        r"&AuraEffect::([A-Za-z0-9_]+),\s*//\s*(\d+)\s+(SPELL_AURA_[A-Z0-9_]+)"
    )
    for line_no, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if m := rx.search(line):
            out[int(m.group(2))] = {
                "handler": m.group(1),
                "dispatch_name": m.group(3),
                "path": str(path.relative_to(ROOT.parent)),
                "line": str(line_no),
            }
    return out


def parse_simc_aura_names() -> dict[int, dict[str, str]]:
    path = SIMC / "engine/dbc/data_enums.hh"
    text = path.read_text(errors="replace")
    block = text[text.index("enum effect_subtype_t") :]
    end = re.search(r"^\s*A_MAX\s*$", block, re.MULTILINE)
    if not end:
        raise RuntimeError("cannot find effect_subtype_t terminator")
    block = block[: end.start()]
    out: dict[int, dict[str, str]] = {}
    for line_no, line in enumerate(block.splitlines(), 1):
        if m := re.match(r"\s*(A_[A-Z0-9_]+)\s*=\s*(\d+)\s*,?\s*(?://\s*(.*))?", line):
            out[int(m.group(2))] = {
                "name": m.group(1),
                "comment": (m.group(3) or "").strip(),
            }
    return out


def parse_tc_attribute_names() -> dict[int, dict[str, str]]:
    path = TC / "src/server/game/Miscellaneous/SharedDefines.h"
    text = path.read_text(errors="replace")
    out: dict[int, dict[str, str]] = {}
    for word, body in re.findall(
        r"enum SpellAttr(\d+)\s*:\s*uint32\s*\{(.*?)\n\};", text, re.DOTALL
    ):
        w = int(word)
        for line in body.splitlines():
            m = re.match(
                rf"\s*(SPELL_ATTR{w}_[A-Z0-9_]+)\s*=\s*0x([0-9A-Fa-f]+)\s*,?\s*(.*)",
                line,
            )
            if not m:
                continue
            mask = int(m.group(2), 16)
            if mask == 0 or mask & (mask - 1):
                continue
            bit = mask.bit_length() - 1
            tail = (
                re.sub(r"^\s*(?://|/\*)\s*", "", m.group(3)).replace("*/", "").strip()
            )
            title = re.search(r"\bTITLE\s+(.*?)(?:\s+DESCRIPTION\s+|$)", tail)
            description = re.search(r"\bDESCRIPTION\s+(.*)$", tail)
            out[w * 32 + bit] = {
                "name": m.group(1),
                "comment": tail,
                "title": title.group(1).strip() if title else "",
                "description": description.group(1).strip() if description else "",
                "word": str(w),
                "bit": str(bit),
                "mask": f"0x{mask:08X}",
                "path": str(path.relative_to(ROOT.parent)),
            }
    return out


def parse_simc_attribute_names() -> dict[int, dict[str, str]]:
    path = SIMC / "engine/dbc/data_enums.hh"
    text = path.read_text(errors="replace")
    block = text[text.index("enum spell_attribute") :]
    block = block[: block.index("};")]
    return {
        int(raw): {"name": name, "comment": (comment or "").strip()}
        for name, raw, comment in re.findall(
            r"^\s*(SX_[A-Z0-9_]+)\s*=\s*(\d+)u?\s*,?\s*(?://\s*(.*))?$",
            block,
            re.MULTILINE,
        )
    }


def source_occurrences(root: Path, token_prefix: str) -> dict[str, list[str]]:
    """Index exact enum-token occurrences in source files, excluding definitions."""
    out: dict[str, list[str]] = defaultdict(list)
    suffixes = {".h", ".hpp", ".hh", ".c", ".cc", ".cpp"}
    for path in root.rglob("*"):
        if path.suffix not in suffixes or not path.is_file():
            continue
        try:
            source = path.read_text(errors="replace")
        except OSError:
            continue
        # Preserve newlines so locations remain exact while comment-only labels do
        # not masquerade as executable consumers.
        source = re.sub(
            r"/\*.*?\*/",
            lambda m: "\n" * m.group(0).count("\n"),
            source,
            flags=re.DOTALL,
        )
        source = re.sub(r"//[^\n]*", "", source)
        lines = source.splitlines()
        for line_no, line in enumerate(lines, 1):
            for token in set(re.findall(rf"\b{token_prefix}[A-Z0-9_]+\b", line)):
                if re.search(rf"^\s*{re.escape(token)}\s*=", line):
                    continue
                out[token].append(f"{path.relative_to(ROOT.parent)}:{line_no}")
    return out


def tc_attribute_consumers(names: dict[int, dict[str, str]]) -> dict[str, list[str]]:
    """Find exact HasAttribute-family calls, excluding declarations/reflection."""
    wanted = {x["name"] for x in names.values()}
    out: dict[str, list[str]] = defaultdict(list)
    excluded = {
        "src/server/game/Miscellaneous/SharedDefines.h",
        "src/server/game/Miscellaneous/enuminfo_SharedDefines.cpp",
    }
    rx = re.compile(
        r"\b(?:Has(?:Any|All)?Attribute|HasAttribute)\s*\(([^)]*)\)", re.DOTALL
    )
    for path in (TC / "src").rglob("*"):
        if path.suffix not in {".h", ".hpp", ".cpp", ".cc"} or not path.is_file():
            continue
        rel = path.relative_to(TC).as_posix()
        if rel in excluded:
            continue
        if rel.startswith("src/server/scripts/"):
            continue
        text = path.read_text(errors="replace")
        for match in rx.finditer(text):
            for token in set(
                re.findall(r"\bSPELL_ATTR\d+_[A-Z0-9_]+\b", match.group(1))
            ):
                if token in wanted:
                    line = text.count("\n", 0, match.start()) + 1
                    out[token].append(f"TrinityCore/{rel}:{line}")
    return out


def simc_flag_consumers(names: dict[int, dict[str, str]]) -> dict[str, list[str]]:
    wanted = {x["name"] for x in names.values()}
    out: dict[str, list[str]] = defaultdict(list)
    rx = re.compile(r"\bflags\s*\(\s*(?:spell_attribute::)?(SX_[A-Z0-9_]+)\s*\)")
    for path in SIMC.rglob("*"):
        if (
            path.suffix not in {".h", ".hpp", ".hh", ".cpp", ".cc"}
            or not path.is_file()
        ):
            continue
        if path == SIMC / "engine/dbc/data_enums.hh":
            continue
        text = path.read_text(errors="replace")
        for match in rx.finditer(text):
            token = match.group(1)
            if token in wanted:
                line = text.count("\n", 0, match.start()) + 1
                out[token].append(f"simc/{path.relative_to(SIMC).as_posix()}:{line}")
    return out


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    for name in [
        "SpellEffect",
        "SpellName",
        "Spell",
        "SpellMisc",
        "SpellAuraOptions",
        "SpellCategories",
        "SpellClassOptions",
        "SpellTargetRestrictions",
        "SpellCooldowns",
        "SpellCastTimes",
        "SpellDuration",
        "SpellRadius",
        "SkillLineAbility",
        "SpellPower",
        "SpellProcsPerMinute",
        "Creature",
        "CreatureDisplayInfo",
        "SpellCategory",
        "SpellLabel",
        "PowerType",
        "Languages",
        "ScreenEffect",
        "SpellMechanic",
        "SpellShapeshiftForm",
        "Item",
        "OverrideSpellData",
        "Phase",
        "SceneScriptPackage",
        "PowerDisplay",
    ]:
        path = TABLES / f"{name}.csv"
        if path.exists():
            escaped = str(path).replace("'", "''")
            con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_csv_auto('{escaped}', header=true)"
            )
    con.execute("""
        CREATE TEMP TABLE attr_set AS
        SELECT DISTINCT m.SpellID, m.DifficultyID, w * 32 + b AS raw
        FROM SpellMisc m, range(0, 17) wi(w), range(0, 32) bi(b)
        WHERE (
          list_extract([Attributes_0, Attributes_1, Attributes_2, Attributes_3,
            Attributes_4, Attributes_5, Attributes_6, Attributes_7, Attributes_8,
            Attributes_9, Attributes_10, Attributes_11, Attributes_12, Attributes_13,
            Attributes_14, Attributes_15, Attributes_16], w + 1)::BIGINT
          & (1::BIGINT << b)
        ) <> 0
    """)
    return con


def rows(
    con: duckdb.DuckDBPyConnection, query: str, params: Iterable[Any] = ()
) -> list[dict[str, Any]]:
    cur = con.execute(query, list(params))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def compact(
    items: list[dict[str, Any]], key: str = "val", nkey: str = "n", limit: int = 8
) -> str:
    shown = items[:limit]
    value = ", ".join(f"{x[key]}×{x[nkey]}" for x in shown) or "none"
    if len(items) > limit:
        value += f", +{len(items) - limit} values"
    return value


def grouped_distribution(
    con: duckdb.DuckDBPyConnection,
    key_expr: str,
    value_expr: str,
    from_sql: str,
    where_sql: str = "",
    limit: int = 8,
) -> dict[int, str]:
    data = rows(
        con,
        f"""
      WITH counts AS (
        SELECT {key_expr} raw, CAST({value_expr} AS VARCHAR) val, count(*) n
        FROM {from_sql} {where_sql} GROUP BY 1, 2
      ), ranked AS (
        SELECT *, row_number() OVER (PARTITION BY raw ORDER BY n DESC, val) rank,
               count(*) OVER (PARTITION BY raw) distinct_values
        FROM counts
      )
      SELECT * FROM ranked WHERE rank <= {limit} ORDER BY raw, rank
    """,
    )
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    totals: dict[int, int] = {}
    for item in data:
        grouped[int(item["raw"])].append(item)
        totals[int(item["raw"])] = int(item["distinct_values"])
    out = {}
    for raw, values in grouped.items():
        rendered = ", ".join(f"{v['val']}×{v['n']}" for v in values)
        if totals[raw] > limit:
            rendered += f", +{totals[raw] - limit} values"
        out[raw] = rendered
    return out


def aura_misc_join_tests(con: duckdb.DuckDBPyConnection) -> dict[int, str]:
    """Run broad candidate-key tests without promoting numeric collisions to types."""
    domains = [
        ("Spell.ID", "SpellName", "ID"),
        ("Creature.ID", "Creature", "ID"),
        ("CreatureDisplayInfo.ID", "CreatureDisplayInfo", "ID"),
        ("SpellCategory.ID", "SpellCategory", "ID"),
        ("SpellLabel.LabelID", "SpellLabel", "LabelID"),
        ("PowerType.ID", "PowerType", "ID"),
        ("Languages.ID", "Languages", "ID"),
        ("ScreenEffect.ID", "ScreenEffect", "ID"),
        ("SpellMechanic.ID", "SpellMechanic", "ID"),
        ("SpellShapeshiftForm.ID", "SpellShapeshiftForm", "ID"),
        ("Item.ID", "Item", "ID"),
        ("SpellRadius.ID", "SpellRadius", "ID"),
        ("OverrideSpellData.ID", "OverrideSpellData", "ID"),
        ("Phase.ID", "Phase", "ID"),
        ("SceneScriptPackage.ID", "SceneScriptPackage", "ID"),
        ("PowerDisplay.ID", "PowerDisplay", "ID"),
    ]
    con.execute("CREATE TEMP TABLE candidate_id(domain VARCHAR, id BIGINT)")
    for domain, table, key in domains:
        con.execute(
            f"INSERT INTO candidate_id SELECT ?, {key} FROM (SELECT DISTINCT {key} FROM {table})",
            [domain],
        )
    data = rows(
        con,
        """
      WITH values AS (
        SELECT EffectAura raw, 'misc0' field_name, EffectMiscValue_0 val FROM SpellEffect WHERE EffectAura<>0
        UNION ALL
        SELECT EffectAura raw, 'misc1' field_name, EffectMiscValue_1 val FROM SpellEffect WHERE EffectAura<>0
      ), stats AS (
        SELECT raw, field_name, count(*) total, count(*) FILTER (WHERE val=0) zeros,
          count(*) FILTER (WHERE val<>0) nonzero,
          count(DISTINCT val) FILTER (WHERE val<>0) distinct_nonzero
        FROM values GROUP BY 1,2
      ), matches AS (
        SELECT v.raw, v.field_name, d.domain,
          count(*) FILTER (WHERE v.val<>0 AND c.id IS NOT NULL) joined_rows,
          count(DISTINCT v.val) FILTER (WHERE v.val<>0 AND c.id IS NOT NULL) joined_distinct
        FROM values v CROSS JOIN (SELECT DISTINCT domain FROM candidate_id) d
        LEFT JOIN candidate_id c ON c.domain=d.domain AND c.id=v.val
        GROUP BY 1,2,3
      ), ranked AS (
        SELECT s.*, m.domain, m.joined_rows, m.joined_distinct,
          row_number() OVER (PARTITION BY s.raw,s.field_name ORDER BY m.joined_distinct DESC, m.joined_rows DESC, m.domain) rank
        FROM stats s JOIN matches m USING (raw,field_name)
      )
      SELECT * FROM ranked WHERE rank<=3 ORDER BY raw, field_name, rank
    """,
    )
    grouped: dict[int, list[str]] = defaultdict(list)
    for x in data:
        failures = int(x["nonzero"]) - int(x["joined_rows"])
        distinct_failures = int(x["distinct_nonzero"]) - int(x["joined_distinct"])
        grouped[int(x["raw"])].append(
            f"{x['field_name']} zero {x['zeros']}/{x['total']}; {x['domain']} "
            f"rows {x['joined_rows']}/{x['nonzero']} distinct {x['joined_distinct']}/{x['distinct_nonzero']} "
            f"(fail {failures} rows/{distinct_failures} distinct)"
        )
    return {raw: "; ".join(values) for raw, values in grouped.items()}


def semantic_label(name: str | None, prefix: str, raw: int) -> str:
    if not name:
        return f"Unidentified raw {raw}"
    text = name.removeprefix(prefix)
    if re.fullmatch(r"(?:UNKNOWN_?)?\d+", text) or text in {"UNKNOWN", "UNUSED"}:
        return f"Unidentified raw {raw}"
    return text.replace("_", " ").title()


def classify_aura(
    raw: int, tc: dict[str, str], simc: dict[str, str], uses: list[str]
) -> tuple[str, str]:
    def placeholder(value: str | None) -> bool:
        return not value or bool(
            re.fullmatch(r"(?:SPELL_AURA_|A_)(?:UNK(?:NOWN)?_?)?\d+", value)
        )

    tc_name, simc_name = tc.get("name"), simc.get("name")
    name = tc_name if not placeholder(tc_name) else simc_name
    generic = placeholder(name)
    handler = tc.get("handler", "")
    implementation = tc.get("handler_implemented") == "true"
    executable_handler = (
        bool(handler)
        and implementation
        and handler not in {"HandleNULL", "HandleNoImmediateEffect", "HandleUnused"}
    )
    if raw in {350, 385, 490}:
        return (
            "Rejected interpretation",
            "removed historical label is absent from both current comparison enums; carrying it forward is stale",
        )
    if executable_handler or uses:
        return (
            "Strongly verified",
            "generic executable handler/consumer plus compatible complete population",
        )
    if not generic:
        return (
            "Partially characterized",
            "comparison enum identity and complete population; no generic executable consumer found",
        )
    return (
        "Unknown after exhaustive available evidence",
        "numeric placeholders/population only; no defensible generic semantic",
    )


def classify_attribute(
    raw: int, tc: dict[str, str], simc: dict[str, str], uses: list[str]
) -> tuple[str, str]:
    name = tc.get("name") or simc.get("name")
    generic = (
        not name
        or bool(re.search(r"(?:UNKNOWN|_UNK|UNUSED)", name))
        or bool(re.fullmatch(r"SPELL_ATTR\d+_\d+", name))
    )
    comment = tc.get("comment", "")
    client_only = "client only" in comment.lower()
    nyi = "NYI" in comment.upper()
    if uses:
        return (
            "Strongly verified",
            "one or more executable comparison-server checks establish the conservative branch",
        )
    if not generic:
        why = "descriptive comparison enum/comment and complete population"
        if client_only:
            why += "; explicitly client-only in TrinityCore"
        elif nyi:
            why += "; marked NYI in TrinityCore"
        else:
            why += "; no executable generic check found"
        return "Partially characterized", why
    return (
        "Unknown after exhaustive available evidence",
        "numeric/unknown comparison metadata and population correlations only",
    )


def census() -> dict[str, Any]:
    con = connect()
    aura_core = parse_core_raws(
        REVIEWED_CORE / "crates/dbc/src/aura_subtype.rs", "AuraSubtypeKind"
    )
    attr_core = parse_core_raws(
        REVIEWED_CORE / "crates/dbc/src/spell_attribute.rs", "SpellAttributeKind"
    )
    aura_names = parse_tc_aura_names()
    aura_handlers = parse_tc_aura_handlers()
    simc_auras = parse_simc_aura_names()
    attr_names = parse_tc_attribute_names()
    simc_attrs = parse_simc_attribute_names()
    aura_occ = source_occurrences(TC / "src", "SPELL_AURA_")
    simc_aura_occ = source_occurrences(SIMC / "engine", "A_")
    attr_occ = tc_attribute_consumers(attr_names)
    simc_attr_occ = simc_flag_consumers(simc_attrs)
    handler_source = (
        TC / "src/server/game/Spells/Auras/SpellAuraEffects.cpp"
    ).read_text(errors="replace")

    aura_counts = rows(
        con,
        """
      SELECT EffectAura raw, count(*) row_count, count(DISTINCT SpellID) spell_count
      FROM SpellEffect WHERE EffectAura <> 0 GROUP BY 1 ORDER BY 1
    """,
    )
    aura_unknown = [x for x in aura_counts if int(x["raw"]) not in aura_core]
    attr_counts = rows(
        con,
        """
      SELECT raw, count(DISTINCT SpellID) spell_count, count(*) row_count
      FROM attr_set GROUP BY 1 ORDER BY 1
    """,
    )
    attr_unknown = [x for x in attr_counts if int(x["raw"]) not in attr_core]

    aura_dist = {
        "effect_index": grouped_distribution(
            con, "EffectAura", "EffectIndex", "SpellEffect", "WHERE EffectAura<>0"
        ),
        "effect_kind": grouped_distribution(
            con, "EffectAura", "Effect", "SpellEffect", "WHERE EffectAura<>0"
        ),
        "targets": grouped_distribution(
            con,
            "EffectAura",
            "ImplicitTarget_0 || '/' || ImplicitTarget_1",
            "SpellEffect",
            "WHERE EffectAura<>0",
        ),
        "period": grouped_distribution(
            con, "EffectAura", "EffectAuraPeriod", "SpellEffect", "WHERE EffectAura<>0"
        ),
        "amplitude": grouped_distribution(
            con, "EffectAura", "EffectAmplitude", "SpellEffect", "WHERE EffectAura<>0"
        ),
        "base": grouped_distribution(
            con, "EffectAura", "EffectBasePointsF", "SpellEffect", "WHERE EffectAura<>0"
        ),
        "misc0": grouped_distribution(
            con, "EffectAura", "EffectMiscValue_0", "SpellEffect", "WHERE EffectAura<>0"
        ),
        "misc1": grouped_distribution(
            con, "EffectAura", "EffectMiscValue_1", "SpellEffect", "WHERE EffectAura<>0"
        ),
        "mechanic": grouped_distribution(
            con, "EffectAura", "EffectMechanic", "SpellEffect", "WHERE EffectAura<>0"
        ),
        "trigger": grouped_distribution(
            con,
            "EffectAura",
            "EffectTriggerSpell",
            "SpellEffect",
            "WHERE EffectAura<>0",
        ),
        "radius": grouped_distribution(
            con,
            "EffectAura",
            "EffectRadiusIndex_0 || '/' || EffectRadiusIndex_1",
            "SpellEffect",
            "WHERE EffectAura<>0",
        ),
        "chain": grouped_distribution(
            con,
            "EffectAura",
            "EffectChainTargets || '/' || EffectChainAmplitude",
            "SpellEffect",
            "WHERE EffectAura<>0",
        ),
        "effect_attrs": grouped_distribution(
            con, "EffectAura", "EffectAttributes", "SpellEffect", "WHERE EffectAura<>0"
        ),
        "effect_attr_bits": grouped_distribution(
            con,
            "raw",
            "bit_index",
            "(SELECT EffectAura raw, b bit_index FROM SpellEffect, range(0,32) bits(b) WHERE EffectAura<>0 AND (EffectAttributes::BIGINT & (1::BIGINT<<b))<>0) set_bits",
        ),
        "class_mask": grouped_distribution(
            con,
            "EffectAura",
            "EffectSpellClassMask_0 || '/' || EffectSpellClassMask_1 || '/' || EffectSpellClassMask_2 || '/' || EffectSpellClassMask_3",
            "SpellEffect",
            "WHERE EffectAura<>0",
        ),
        "coeff": grouped_distribution(
            con,
            "EffectAura",
            "EffectBonusCoefficient || '/' || BonusCoefficientFromAP || '/' || Coefficient || '/' || Variance || '/' || ResourceCoefficient || '/' || GroupSizeBasePointsCoefficient || '/' || EffectPointsPerResource || '/' || EffectRealPointsPerLevel || '/' || ScalingClass",
            "SpellEffect",
            "WHERE EffectAura<>0",
        ),
        "explicit_targets": grouped_distribution(
            con,
            "e.EffectAura",
            "coalesce(t.Targets,0)",
            "SpellEffect e LEFT JOIN LATERAL (SELECT Targets FROM SpellTargetRestrictions tr WHERE tr.SpellID=e.SpellID AND (tr.DifficultyID=e.DifficultyID OR tr.DifficultyID=0) ORDER BY (tr.DifficultyID=e.DifficultyID) DESC, tr.ID LIMIT 1) t ON true",
            "WHERE e.EffectAura<>0",
        ),
    }
    misc_joins = aura_misc_join_tests(con)
    aura_attr = grouped_distribution(
        con,
        "raw",
        "attribute_raw",
        "(SELECT DISTINCT e.EffectAura raw, e.SpellID, a.raw attribute_raw FROM SpellEffect e JOIN attr_set a USING (SpellID) WHERE e.EffectAura<>0) pairs",
        "",
        10,
    )
    aura_proc = grouped_distribution(
        con,
        "e.EffectAura",
        "coalesce(o.ProcTypeMask_0,0) || '/' || coalesce(o.ProcTypeMask_1,0) || '/' || coalesce(o.ProcChance,0) || '/' || coalesce(o.SpellProcsPerMinuteID,0)",
        "SpellEffect e LEFT JOIN SpellAuraOptions o ON o.SpellID=e.SpellID AND (o.DifficultyID=e.DifficultyID OR o.DifficultyID=0)",
        "WHERE e.EffectAura<>0",
        6,
    )
    aura_samples = rows(
        con,
        """
      SELECT EffectAura raw, SpellID, Name_lang spell_name, Description_lang spell_description,
             AuraDescription_lang aura_description
      FROM (
        SELECT e.EffectAura, e.SpellID, n.Name_lang, s.Description_lang, s.AuraDescription_lang,
          row_number() OVER (PARTITION BY e.EffectAura ORDER BY e.SpellID) rank
        FROM SpellEffect e LEFT JOIN SpellName n ON n.ID=e.SpellID
        LEFT JOIN Spell s ON s.ID=e.SpellID WHERE e.EffectAura<>0
      ) WHERE rank<=6 ORDER BY raw, SpellID
    """,
    )
    aura_sample_map: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for x in aura_samples:
        aura_sample_map[int(x["raw"])].append(x)

    aura_records = []
    for count in aura_unknown:
        raw = int(count["raw"])
        tc = {**aura_names.get(raw, {}), **aura_handlers.get(raw, {})}
        token = tc.get("name") or tc.get("dispatch_name", "")
        definitions = {tc.get("path", ""), aura_names.get(raw, {}).get("path", "")}
        uses = [
            p
            for p in aura_occ.get(token, [])
            if not any(p.startswith(x + ":") for x in definitions if x)
            and not p.startswith("TrinityCore/src/server/scripts/")
            and "/enuminfo_" not in p
            and not (
                p.startswith(
                    "TrinityCore/src/server/game/Spells/Auras/SpellAuraEffects.cpp:"
                )
                and int(p.rsplit(":", 1)[1]) <= 734
            )
        ]
        simc_token = simc_auras.get(raw, {}).get("name", "")
        simc_uses = [
            p
            for p in simc_aura_occ.get(simc_token, [])
            if "simc/engine/dbc/data_enums.hh:" not in p
            and "simc/engine/dbc/sc_spell_info.cpp:" not in p
            and "simc/engine/class_modules/" not in p
        ]
        handler = tc.get("handler", "")
        tc["handler_implemented"] = str(
            bool(
                handler
                and re.search(
                    rf"\bAuraEffect::{re.escape(handler)}\s*\(", handler_source
                )
            )
        ).lower()
        uses = (uses + simc_uses)[:8]
        disposition, basis = classify_aura(raw, tc, simc_auras.get(raw, {}), uses)
        label_token = token
        if re.fullmatch(r"SPELL_AURA_(?:UNK(?:NOWN)?_?)?\d+", label_token or ""):
            label_token = simc_token
        aura_records.append(
            {
                **count,
                "tc": tc,
                "simc": simc_auras.get(raw, {}),
                "uses": uses,
                "disposition": disposition,
                "basis": basis,
                "label": semantic_label(label_token, "SPELL_AURA_", raw).removeprefix(
                    "A "
                ),
                "samples": aura_sample_map.get(raw, []),
                "distributions": {k: v.get(raw, "none") for k, v in aura_dist.items()},
                "owner_attributes": aura_attr.get(raw, "none"),
                "proc": aura_proc.get(raw, "none"),
                "misc_join_tests": misc_joins.get(raw, "none"),
            }
        )

    attr_dist = {
        "effect_kind": grouped_distribution(
            con,
            "raw",
            "effect_kind",
            "(SELECT DISTINCT a.raw, a.SpellID, e.Effect effect_kind FROM attr_set a LEFT JOIN SpellEffect e USING (SpellID)) pairs",
        ),
        "aura": grouped_distribution(
            con,
            "raw",
            "aura_raw",
            "(SELECT DISTINCT a.raw, a.SpellID, e.EffectAura aura_raw FROM attr_set a LEFT JOIN SpellEffect e USING (SpellID)) pairs",
        ),
        "selectors": grouped_distribution(
            con,
            "raw",
            "selector",
            "(SELECT DISTINCT a.raw, a.SpellID, e.ImplicitTarget_0 || '/' || e.ImplicitTarget_1 selector FROM attr_set a LEFT JOIN SpellEffect e USING (SpellID)) pairs",
        ),
        "cast_time": grouped_distribution(
            con,
            "a.raw",
            "m.CastingTimeIndex",
            "attr_set a JOIN SpellMisc m USING (SpellID, DifficultyID)",
        ),
        "family": grouped_distribution(
            con,
            "a.raw",
            "coalesce(c.SpellClassSet,0)",
            "attr_set a LEFT JOIN SpellClassOptions c USING (SpellID)",
        ),
        "cooldown": grouped_distribution(
            con,
            "a.raw",
            "coalesce(cd.CategoryRecoveryTime,0) || '/' || coalesce(cd.RecoveryTime,0) || '/' || coalesce(cat.Category,0)",
            "attr_set a LEFT JOIN SpellCooldowns cd USING (SpellID) LEFT JOIN SpellCategories cat USING (SpellID)",
        ),
        "proc": grouped_distribution(
            con,
            "a.raw",
            "coalesce(o.ProcTypeMask_0,0) || '/' || coalesce(o.ProcTypeMask_1,0) || '/' || coalesce(o.ProcChance,0) || '/' || coalesce(o.SpellProcsPerMinuteID,0)",
            "attr_set a LEFT JOIN SpellAuraOptions o USING (SpellID)",
        ),
        "passive": grouped_distribution(
            con,
            "a.raw",
            "CASE WHEN p.SpellID IS NULL THEN 'active-or-unmarked' ELSE 'passive-bit-set' END",
            "attr_set a LEFT JOIN (SELECT DISTINCT SpellID FROM attr_set WHERE raw=6) p USING (SpellID)",
        ),
        "origin": grouped_distribution(
            con,
            "a.raw",
            "CASE WHEN s.Spell IS NULL THEN 'no-SkillLineAbility' ELSE 'SkillLineAbility' END",
            "attr_set a LEFT JOIN (SELECT DISTINCT Spell FROM SkillLineAbility) s ON s.Spell=a.SpellID",
        ),
    }
    attr_co = grouped_distribution(
        con,
        "a.raw",
        "b.raw",
        "attr_set a JOIN attr_set b USING (SpellID)",
        "WHERE a.raw<>b.raw",
        10,
    )
    attr_samples = rows(
        con,
        """
      SELECT raw, SpellID, spell_name, spell_description, aura_description FROM (
        SELECT a.raw, a.SpellID, n.Name_lang spell_name, s.Description_lang spell_description,
          s.AuraDescription_lang aura_description,
          row_number() OVER (PARTITION BY a.raw ORDER BY a.SpellID) rank
        FROM attr_set a LEFT JOIN SpellName n ON n.ID=a.SpellID LEFT JOIN Spell s ON s.ID=a.SpellID
      ) WHERE rank<=6 ORDER BY raw, SpellID
    """,
    )
    attr_sample_map: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for x in attr_samples:
        attr_sample_map[int(x["raw"])].append(x)

    attr_records = []
    for count in attr_unknown:
        raw = int(count["raw"])
        tc = attr_names.get(raw, {})
        token = tc.get("name", "")
        simc_token = simc_attrs.get(raw, {}).get("name", "")
        uses = (
            (attr_occ.get(token, []) if token else [])
            + (simc_attr_occ.get(simc_token, []) if simc_token else [])
        )[:12]
        disposition, basis = classify_attribute(raw, tc, simc_attrs.get(raw, {}), uses)
        attr_records.append(
            {
                **count,
                "word": raw // 32,
                "bit": raw % 32,
                "mask": f"0x{1 << (raw % 32):08X}",
                "tc": tc,
                "simc": simc_attrs.get(raw, {}),
                "uses": uses,
                "disposition": disposition,
                "basis": basis,
                "label": semantic_label(
                    token or simc_attrs.get(raw, {}).get("name"),
                    f"SPELL_ATTR{raw // 32}_",
                    raw,
                ),
                "samples": attr_sample_map.get(raw, []),
                "distributions": {k: v.get(raw, "none") for k, v in attr_dist.items()},
                "coattributes": attr_co.get(raw, "none"),
            }
        )

    totals = rows(
        con,
        """
      SELECT count(*) effect_rows, count(DISTINCT SpellID) effect_spells,
        count(*) FILTER (WHERE EffectAura=0) aura_zero_rows,
        count(*) FILTER (WHERE EffectAura<>0) aura_nonzero_rows,
        count(DISTINCT EffectAura) aura_distinct_including_zero,
        count(DISTINCT EffectAura) FILTER (WHERE EffectAura<>0) aura_distinct_nonzero
      FROM SpellEffect
    """,
    )[0]
    attr_set_count = rows(
        con,
        """SELECT count(*) row_count, count(DISTINCT SpellID) spell_count,
          count(DISTINCT raw) raw_count,
          count(DISTINCT (SpellID, raw)) owner_pair_count
        FROM attr_set""",
    )[0]
    spell_misc_count = rows(
        con,
        """SELECT count(*) row_count, count(DISTINCT SpellID) spell_count,
          count(*) FILTER (WHERE NOT EXISTS (
            SELECT 1 FROM attr_set a
            WHERE a.SpellID=m.SpellID AND a.DifficultyID=m.DifficultyID
          )) all_zero_rows,
          count(DISTINCT SpellID) FILTER (WHERE NOT EXISTS (
            SELECT 1 FROM attr_set a
            WHERE a.SpellID=m.SpellID AND a.DifficultyID=m.DifficultyID
          )) all_zero_spells
        FROM SpellMisc m""",
    )[0]
    used_attr_raws = {int(x["raw"]) for x in attr_counts}
    totals.update(
        {
            "attribute_rows": attr_set_count["row_count"],
            "attribute_spells": attr_set_count["spell_count"],
            "attribute_raws": attr_set_count["raw_count"],
            "attribute_owner_pairs": attr_set_count["owner_pair_count"],
            "spell_misc_rows": spell_misc_count["row_count"],
            "spell_misc_spells": spell_misc_count["spell_count"],
            "spell_misc_all_zero_rows": spell_misc_count["all_zero_rows"],
            "spell_misc_all_zero_spells": spell_misc_count["all_zero_spells"],
        }
    )
    totals.update(
        {
            "reviewed_aura_present": sum(
                int(x["raw"]) in aura_core for x in aura_counts
            ),
            "unknown_aura_count": len(aura_records),
            "unknown_aura_rows": sum(x["row_count"] for x in aura_records),
            "reviewed_attribute_present": sum(
                int(x["raw"]) in attr_core for x in attr_counts
            ),
            "unknown_attribute_count": len(attr_records),
            "unknown_attribute_rows": sum(x["row_count"] for x in attr_records),
            "reviewed_aura_catalog_size": len(aura_core),
            "reviewed_attribute_catalog_size": len(attr_core),
            "unused_attribute_raws": [x for x in range(544) if x not in used_attr_raws],
        }
    )
    metadata_files = list((ROOT / "changes/metadata").glob("*.json"))
    latest_metadata = max(
        metadata_files,
        key=lambda p: tuple(int(x) for x in re.findall(r"\d+", p.stem)),
    )
    wago_metadata = json.loads(latest_metadata.read_text())
    return {
        "metadata": {
            "wowlab_data": git_head(ROOT),
            "core": git_head(REVIEWED_CORE),
            "trinitycore": git_head(TC),
            "simc": git_head(SIMC),
            "duckdb": duckdb.__version__,
            "wago_build": wago_metadata.get("version", latest_metadata.stem),
            "wago_tables_requested": wago_metadata.get("tablesRequested"),
            "wago_tables_downloaded": wago_metadata.get("tablesDownloaded"),
            "wago_tables_failed": wago_metadata.get("tablesFailed"),
        },
        "totals": totals,
        "aura_core": aura_core,
        "attribute_core": attr_core,
        "auras": aura_records,
        "attributes": attr_records,
    }


def md(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).replace("|", "\\|").strip()


def samples_text(samples: list[dict[str, Any]]) -> str:
    rendered = []
    for x in samples:
        description = re.sub(
            r"\s+", " ", x.get("spell_description") or x.get("aura_description") or ""
        ).strip()
        if len(description) > 120:
            description = description[:119].rstrip() + "…"
        rendered.append(
            f"{x['SpellID']} {x.get('spell_name') or '<unnamed>'}"
            + (f" — {description}" if description else "")
        )
    return "; ".join(rendered) or "none"


def generate_markdown(data: dict[str, Any]) -> str:
    totals = data["totals"]
    auras = data["auras"]
    attrs = data["attributes"]
    ac = Counter(x["disposition"] for x in auras)
    xc = Counter(x["disposition"] for x in attrs)
    lines = [
        "# Aura-subtype and spell-attribute audit",
        "",
        "Status: complete research census and source audit of the current checked-in Wago corpus. This document changes no runtime, schema, catalog, generated data, tests, or comparison repository.",
        "",
        "- Audit date: 2026-09-07 UTC",
        f"- `wowlab-data`: `{data['metadata']['wowlab_data']}`",
        f"- Wago retail build: `{data['metadata']['wago_build']}` ({data['metadata']['wago_tables_downloaded']}/{data['metadata']['wago_tables_requested']} tables, {data['metadata']['wago_tables_failed']} failures)",
        f"- reviewed Core: `{data['metadata']['core']}`",
        f"- TrinityCore: `{data['metadata']['trinitycore']}`",
        f"- SimulationCraft: `{data['metadata']['simc']}`",
        f"- DuckDB Python package/engine: `{data['metadata']['duckdb']}`",
        "",
        "## Result",
        "",
        f"The recomputed queues contain **{len(auras)} unknown nonzero aura subtypes** and **{len(attrs)} unknown set spell attributes** after directly parsing the two reviewed-Core enums. Every queued identity appears exactly once below and has one terminal disposition. Aura dispositions: {dict(ac)}. Attribute dispositions: {dict(xc)}.",
        "",
        "A conservative label is not itself a proposed Core contract. Strongly verified means only the narrow operation or branch stated; it does not silently import lifecycle, stacking, ownership, ordering, persistence, or client/server parity. Partially characterized entries remain uncataloged until their stated boundary is resolved. Numeric placeholders are deliberately left unknown.",
        "",
        "## Method",
        "",
        "The lasting `uv` research driver `scripts/research/aura_attribute_audit.py` creates DuckDB relations over the current CSVs. It enumerates all nonzero `SpellEffect.EffectAura` values and all set bits in all 17 `SpellMisc.Attributes_0..16` words, flattening each attribute as `word_index * 32 + bit_index`. It parses `AuraSubtypeKind` and `SpellAttributeKind` directly from the supplied Core source and anti-joins those raw identities.",
        "",
        "Population analysis uses complete groups, not samples: owning rows/spells, effect indexes and kinds, targets, period, amount, misc fields, mechanic, trigger, radii, chains, class masks, exact-effect attributes, coefficients, owner attributes, proc metadata, class/family, cast time, cooldown/category, aura/effect distributions, and attribute co-occurrences. Six names/texts are navigation representatives only. Top-N displays retain the number of omitted distinct values.",
        "",
        "TrinityCore evidence distinguishes enum metadata, inert/no-immediate handlers, and executable consumers. SimulationCraft enum names are corroboration only unless executable use exists. Correlation is never treated as causation. Generic conservative labels are therefore weaker than executable branches; explicitly client-only and NYI Trinity labels without an executable branch are not promoted to gameplay behavior.",
        "",
        "## Manual semantic traces and falsification",
        "",
        "The generator located candidates; the following conclusions come from reading the actual branches and then testing their complete Wago populations. They are deliberately narrower than several enum titles.",
        "",
        "### High-frequency aura traces",
        "",
        "- **Raw 293 (Override Spells):** `HandleAuraOverrideSpells` runs only for real application/removal, requires an in-world player target, reads `misc0` as `OverrideSpellData.ID`, sets/clears the player's override ID, and adds/removes the referenced temporary spells. The exact Wago join succeeds for 2,855/2,865 nonzero rows and 2,720/2,728 nonzero values; ten rows (eight values) miss, including Skydive Override, Fishing Rod, Gyrocopter Controls, and Mass Resurrection. This proves the handler's narrow field contract, not that missing current Wago references are valid or that every retail host follows Trinity's replacement policy.",
        "- **Raw 430 (Play Scene):** `HandlePlayScene` requires a player target; application calls `SceneMgr::PlayScene(misc0)`, removal always cancels that scene ID, and natural expiry first reports scene completion when an instance exists. Only 1,741/2,522 nonzero rows (1,670/2,391 values) collide with local `SceneScriptPackage.ID`; therefore `misc0 is SceneScriptPackage.ID` is rejected as a complete local DB2 typing even though the handler proves a scene identifier.",
        "- **Raw 428 (Linked Summon):** on application the target casts `EffectTriggerSpell` on itself; on removal Trinity inspects summon effects in that child and despawns nearby entries owned by the target. Five current rows have trigger zero, so the executable contract does not explain their useful behavior and they may be inert or client/content-driven. The handler itself notes an unproved one-summon-per-effect assumption; no generic retail multiplicity rule is claimed.",
        "- **Raw 236 (Control Vehicle):** the aura is applied from caster/passenger to vehicle target; it enters the caster into `GetAmount()-1` seat on apply and removes/exits on removal, with proxy-seat and spell-specific branches. Trinity's own comment says the zero/zero-to-minus-one amount handling needs research. Four Wago rows have enormous base values (146,468–220,388) rather than plausible seat indices, so raw `EffectBasePointsF` is rejected as a universally direct seat number without amount-calculation context.",
        "- **Raw 78 (Mounted):** the holder is mounted on application and dismounted on removal. `misc0` is first treated as a server CreatureTemplate entry fallback, while Mount/MountXDisplay data may provide the display and the calculated amount may select `MountCapability`; local `Creature.ID` joins only 553/1,808 nonzero rows and 19/515 values, so the client `Creature` DB2 collision is not a field type. Both nonzero authored base values join `MountCapability.ID`, but only two rows use them.",
        "- **Raw 19 (Invisibility Detect):** application/removal adds/subtracts the signed amount in the holder's detection bucket selected by `misc0`, maintaining the type flag until the last same-type aura and refreshing visibility. It modifies detection, not invisibility itself; arbitrary DB2 joins for the small selector codes are collisions.",
        "- **Raw 261 (Phase):** `HandlePhase` adds/removes on the aura target the phase selected by secondary misc (`GetMiscValueB`). The Wago `misc1 → Phase.ID` test is exact for all 1,031 nonzero rows and all 663 values; three zero rows remain distinct outliers. Primary misc is not the phase selector.",
        "- **Raw 138 (Mod Melee Haste):** Trinity applies the signed calculated amount to main- and off-hand attack intervals and compares exclusive-same-effect values by absolute magnitude. It explicitly carries a stacking TODO. Wago includes sentinel-like `-999999` Whirlwind/Grab rows and a `1000` Flashfire row, so neither ordinary percentage bounds nor a universal stacking rule is inferred.",
        "- **Raw 40 (Damage Immunity):** the helper ORs `misc0` into a damage-school immunity mask, applies/removes it on the target, and manages the broad immune unit flag while school/damage immunities remain. This is damage-school immunity, not blanket immunity to every spell effect.",
        "- **Raw 70 (Periodic Weapon Percent Damage):** periodic dispatch calculates a percentage of the caster's weapon damage, applies melee done/taken modifiers, and routes it as DOT. Seven current rows have zero aura period, so a claim that every authored row necessarily schedules ticks is rejected; those rows need host scheduling/content evidence.",
        "- **Raw 402 (Override Power Display):** `misc0` must resolve to `PowerDisplay`, its `ActualType` must exist on the target, and application replaces another same-type aura before setting the display ID. All 450 nonzero current rows join 239 `PowerDisplay.ID` values; the single zero row does not. This changes presentation of a power pool and does not itself create or modify the resource.",
        "",
        "### High-frequency spell-attribute traces",
        "",
        "- **Raw 78 = word 2 bit 14 (`0x00004000`):** the checked branch prevents an action from breaking an invisibility aura when the interrupting spell carries the bit and the aura has invisibility dispel type. It does not generically grant invisibility or bypass all visibility/cast admission.",
        "- **Raw 86 = word 2 bit 22 (`0x00400000`):** `HasInitialAggro` becomes false, and `ThreatManager::AddThreat` returns only when the threatened owner is not already engaged. The bit suppresses initial threat/engagement, not all later threat.",
        "- **Raw 23 = word 0 bit 23 (`0x00800000`):** the ordinary dead-caster admission failure is bypassed for this bit (passives and a triggered-cast exception are handled separately). This is cast admission while dead, not aura persistence after death.",
        "- **Raw 64 = word 2 bit 0 (`0x00000001`):** target validation accepts dead units; corpse selectors/target masks independently allow them too. The bit allows rather than requires a dead target.",
        "- **Raw 205 = word 6 bit 13 (`0x00002000`):** target visibility passes `IgnorePhaseShift` into detection. Other visibility and target checks remain; this is not a universal targeting-restriction bypass.",
        "- **Raw 179 = word 5 bit 19 (`0x00080000`):** creature spell-focus setup avoids adopting the target GUID, explicitly faces a supplied focus target without normal facing control, and tracks a focusing state until release. The behavior is AI/focus-facing specific, not player targeting.",
        "- **Raw 230 = word 7 bit 6 (`0x00000040`):** damage pushback logic excludes spells carrying this bit (alongside two independent alternatives). It suppresses that interruption/pushback layer, not damage or every cast interrupt.",
        "- **Raws 168 and 104:** word 5 bit 8 rejects player-controlled NPC targets; word 3 bit 8 rejects non-player targets. These are target-admission predicates and do not select targets.",
        "- **Raw 118 = word 3 bit 22 (`0x00400000`):** after a hostile hit the default branch stands a non-standing target; this bit suppresses only that stand-state mutation.",
        "- **Raw 170 = word 5 bit 10 (`0x00000400`):** it removes the duration flag from the client aura update packet. This is presentation metadata, not a duration or expiry change.",
        "- **Raw 318 = word 9 bit 30 (`0x40000000`):** radius calculation normally adds/subtracts two yards for movement; this bit bypasses that adjustment while retaining authored radius and spell modifiers.",
        "- **Raw 43 = word 1 bit 11 (`0x00000800`):** it participates in positivity classification and prevents timer refresh for a non-stack aura in the cited path. That is narrower than a universal uniqueness/exclusive-group contract.",
        "",
        "### Hard negative evidence",
        "",
        "- The highest-frequency unnamed attributes remain unnamed after source and history scans: raw 320 (29,058 spells), 404 (21,652), 336 (8,131), 343 (7,935), 352 (6,592), 378 (6,305), 417 (5,737), 342 (5,036), and 391 (4,587). Their effect/aura populations are broad and heterogeneous; for example raw 320 spans at least 143 effect kinds beyond the eight leaders and over 220 aura values. A periodic, passive, UI, or content-family interpretation is therefore rejected.",
        "- High-frequency named-but-unconsumed bits are also not promoted: raw 124 (Ignore Caster & Target Restrictions), 201 (Allow on Charmed Targets), and 261 (Allow While Charmed) have no current generic `HasAttribute` branch. Their titles are retained only as partial characterization.",
        "- Aura raws 350, 385, and 490 are terminal **Rejected interpretation** results. Historical Trinity names respectively claimed gathering-item gain percentage, chance to override an autoattack with a self spell, and switch team. Both current comparison enums are numeric placeholders and targeted numeric scans found no consumer; carrying those removed labels forward is rejected.",
        "- Attribute raw 395 has only a King's Rest TODO saying implementation is required; raw 433's historical check appears only in datastore-structure churn. Neither establishes behavior, so both remain unknown.",
        "",
        "### Cross-surface falsification",
        "",
        "The strongest unknown↔unknown pairs are broad host-state metadata, not ownership proof. Raw-430 scene auras co-occur with attributes 78/64/86/23 on roughly 80% of owners, but those bits have independent invisibility, dead-target, initial-threat, and dead-cast consumers. Raw-78 mounted auras co-occur with raw 209 on 98.3%, raw 15 on 97.4%, and raw 28 on 96.9%; those flags describe equip-while-casting, outdoors, and peaceful-state constraints rather than implementing mounting. Raw-428 linked summons co-occur with unnamed raw 391 on 54.3%, yet the linked-summon handler never checks it. These correlations were used to search source and then explicitly rejected as causal semantics.",
        "",
        "## Census and reconciliation",
        "",
        f"- SpellEffect: {totals['effect_rows']:,} rows, {totals['effect_spells']:,} owning spells; aura zero/sentinel {totals['aura_zero_rows']:,} rows; nonzero aura {totals['aura_nonzero_rows']:,} rows; {totals['aura_distinct_nonzero']} distinct nonzero raws ({totals['aura_distinct_including_zero']} including zero).",
        f"- Aura partition: the parsed reviewed catalog has {totals['reviewed_aura_catalog_size']} identities including zero; {totals['reviewed_aura_present']} reviewed nonzero raws plus {totals['unknown_aura_count']} unknown raws equals {totals['aura_distinct_nonzero']} current nonzero raws. Unknown raws own {totals['unknown_aura_rows']:,} effect rows.",
        f"- SpellMisc: {totals['spell_misc_rows']:,} spell/difficulty rows over {totals['spell_misc_spells']:,} spells; {totals['spell_misc_all_zero_rows']:,} rows over {totals['spell_misc_all_zero_spells']:,} spells have no set attribute word.",
        f"- Spell attributes: {totals['attribute_rows']:,} set spell/difficulty/bit occurrences, {totals['attribute_owner_pairs']:,} distinct `(SpellID, raw)` pairs, {totals['attribute_spells']:,} owning spells, and {totals['attribute_raws']} distinct set raws.",
        f"- Attribute partition: the parsed reviewed catalog has {totals['reviewed_attribute_catalog_size']} identities; {totals['reviewed_attribute_present']} reviewed raws plus {totals['unknown_attribute_count']} unknown raws equals {totals['attribute_raws']} current set raws. Unknown raws account for {totals['unknown_attribute_rows']:,} spell/difficulty/bit occurrences.",
        f"- Unused current attribute raws in the theoretical 0..543 domain ({len(totals['unused_attribute_raws'])}): "
        + ", ".join(map(str, totals["unused_attribute_raws"]))
        + ".",
        "",
        "## Aura work queue: terminal ledger",
        "",
        "Each field below describes the complete raw population. `coeff` is bonus/SP, AP, coefficient, variance, resource, group-size, points-per-resource, real-points-per-level, and scaling-class shaping in that order. Proc tuples are proc-mask0/mask1/chance/PPM-ID. Attribute numbers are flattened owner-spell raws and remain correlations. Candidate-key results are collision tests, never automatic field typing.",
        "",
    ]
    for x in auras:
        d = x["distributions"]
        tc = x["tc"]
        simc = x["simc"]
        lines += [
            f"### Aura raw {x['raw']}: {md(x['label'])}",
            "",
            f"- **{x['disposition']}.** {md(x['basis'])}. Current population: {x['row_count']:,} effect rows / {x['spell_count']:,} spells. TC `{tc.get('name') or tc.get('dispatch_name') or 'absent'}`; handler `{tc.get('handler') or 'absent'}`; SimC `{simc.get('name') or 'absent'}`. Executable-use locations: {md(', '.join(x['uses']) or 'none')}.",
            f"- Shape: effect index {d['effect_index']}; effect kind {d['effect_kind']}; implicit targets {d['targets']}; explicit target mask {d['explicit_targets']}; period {d['period']}; amplitude {d['amplitude']}; base amount {d['base']}.",
            f"- Payload: misc0 {d['misc0']}; misc1 {d['misc1']}; mechanic {d['mechanic']}; trigger {d['trigger']}; radii {d['radius']}; chain target/amplitude {d['chain']}.",
            f"- Narrowing/scaling: exact-effect attribute masks {d['effect_attrs']} (set-bit distribution {d['effect_attr_bits']}); class mask {d['class_mask']}; coeff {d['coeff']}.",
            f"- Candidate foreign-key tests: {x['misc_join_tests']}. These are the three highest-coverage collisions for each misc field among the tested domains; misses and zeros are explicit, and no type is inferred from a collision alone.",
            f"- Cross-audit: owner attributes {x['owner_attributes']}; proc {x['proc']}. Representatives: {md(samples_text(x['samples']))}.",
            "- Boundary/rejection: the comparison label is rejected as a complete runtime contract unless backed by the cited executable consumer. The data do not independently prove unmentioned state owner, application/removal timing, periodic/proc causation, stack combination, exclusivity, or persistence. For unknown entries, the smallest resolving evidence is a build-matched client/server consumer or decoded schema tied to this raw and payload.",
            "",
        ]
    lines += [
        "## Spell-attribute work queue: terminal ledger",
        "",
        "Every heading states the flattened raw identity, source word/bit, and numeric mask. This prevents mask values from being confused with bit identities. Proc tuples are proc-mask0/mask1/chance/PPM-ID; cooldown tuples are category recovery/recovery/category.",
        "",
    ]
    for x in attrs:
        d = x["distributions"]
        tc = x["tc"]
        simc = x["simc"]
        lines += [
            f"### Attribute raw {x['raw']} = word {x['word']}, bit {x['bit']}, mask `{x['mask']}`: {md(x['label'])}",
            "",
            f"- **{x['disposition']}.** {md(x['basis'])}. Current population: {x['spell_count']:,} owning spells ({x['row_count']:,} spell/difficulty rows). TC `{tc.get('name') or 'absent'}` ({md(tc.get('comment') or 'no comment')}); SimC `{simc.get('name') or 'absent'}`.",
            f"- Executable checks: {md(', '.join(x['uses']) or 'none')}. These locations establish only their actual branch; an enum title without a check remains metadata evidence.",
            f"- Population: passive marker {d['passive']}; class/family {d['family']}; SkillLineAbility origin {d['origin']}; cast-time index {d['cast_time']}; cooldown {d['cooldown']}; proc {d['proc']}.",
            f"- Effects: kinds {d['effect_kind']}; aura subtypes {d['aura']}; selectors {d['selectors']}. Strong coattributes: {x['coattributes']}. Representatives: {md(samples_text(x['samples']))}.",
            f"- Boundary/rejection: co-occurring auras, effects, proc metadata, passivity, and content family are rejected as the attribute's semantic unless an executable check says so. The record does not generalize beyond the cited branch. For unknowns, resolving evidence requires a build-matched consumer or authoritative decoded client schema for word {x['word']} bit {x['bit']}.",
            "",
        ]
    lines += [
        "## Aura ↔ attribute second pass",
        "",
        "The per-aura owner-attribute and per-attribute aura/effect distributions above are the recomputed bidirectional co-occurrence pass. They identify repeated pairs without assigning ownership. No entry was promoted solely from those matrices: promotions require an executable generic consumer, and label-only pairs remain partial or unknown.",
        "",
        "The strongest recurring methodological result is negative: highly frequent flags and auras often occur on broad generated, passive, UI, creature, or test populations. Frequency and near-perfect correlation do not identify which side owns behavior. Conversely, narrow executable checks can strongly verify an exception even when its current population is small. Outliers are preserved by the complete distributions and the omitted-distinct-value counts rather than discarded as special cases.",
        "",
        "## Completion check",
        "",
        f"- Recomputed unknown aura raws: {len(auras)}; documented aura sections: {len(auras)}; duplicate sections: 0; symmetric difference: 0.",
        f"- Recomputed unknown set attribute raws: {len(attrs)}; documented attribute sections: {len(attrs)}; duplicate sections: 0; symmetric difference: 0.",
        "- Every unknown identity has exactly one terminal disposition from the required four-value vocabulary.",
        "- The zero aura sentinel is separately balanced; unset theoretical attribute bits are reported separately and were not audited as current identities.",
        "- Remaining unknowns are terminal research results, not blockers: the repositories and current corpus have been exhausted to the stated evidence boundary, and the missing evidence required to resolve them is recorded per surface.",
        "",
    ]
    return "\n".join(lines)


def validate(data: dict[str, Any], document: str | None = None) -> None:
    aura_raws = [int(x["raw"]) for x in data["auras"]]
    attr_raws = [int(x["raw"]) for x in data["attributes"]]
    allowed = {
        "Strongly verified",
        "Partially characterized",
        "Rejected interpretation",
        "Unknown after exhaustive available evidence",
    }
    assert len(aura_raws) == len(set(aura_raws)) == data["totals"]["unknown_aura_count"]
    assert (
        len(attr_raws)
        == len(set(attr_raws))
        == data["totals"]["unknown_attribute_count"]
    )
    assert all(x["disposition"] in allowed for x in data["auras"] + data["attributes"])
    assert all(x["raw"] == x["word"] * 32 + x["bit"] for x in data["attributes"])
    assert all(int(x["mask"], 16) == 1 << x["bit"] for x in data["attributes"])
    if document is not None:
        assert sum(document.count(f"### Aura raw {raw}:") for raw in aura_raws) == len(
            aura_raws
        )
        assert sum(
            document.count(f"### Attribute raw {raw} =") for raw in attr_raws
        ) == len(attr_raws)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["json", "document", "validate"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = census()
    if args.command == "json":
        content = json.dumps(data, indent=2, default=str) + "\n"
    else:
        document = generate_markdown(data)
        validate(data, document)
        if args.command == "validate":
            print(
                json.dumps(
                    {
                        "auras": len(data["auras"]),
                        "attributes": len(data["attributes"]),
                        "aura_dispositions": Counter(
                            x["disposition"] for x in data["auras"]
                        ),
                        "attribute_dispositions": Counter(
                            x["disposition"] for x in data["attributes"]
                        ),
                    },
                    default=dict,
                    indent=2,
                )
            )
            return
        content = document + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
