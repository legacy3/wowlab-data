"""Reusable DuckDB helpers for research over checked-in Wago CSV corpora.

The module is intentionally small.  It centralizes corpus discovery, safe CSV
relations, provenance, and deterministic semantic-surface diffs used by the
remaining-data audit.  It never writes game data.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

ATTR_WORDS = 17


def git_head(path: Path) -> str:
    """Return the exact commit for a Git worktree."""
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def sql_quote(value: str | Path) -> str:
    return str(value).replace("'", "''")


def csv_sql(path: Path) -> str:
    """Return a DuckDB CSV relation expression with stable CSV settings."""
    return f"read_csv_auto('{sql_quote(path)}', header=true, sample_size=-1)"


def rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cursor = con.execute(sql)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


@dataclass(frozen=True)
class Corpus:
    """A directory containing one CSV per Wago DB2 table."""

    tables: Path

    def __post_init__(self) -> None:
        if not self.tables.is_dir():
            raise FileNotFoundError(self.tables)

    def names(self) -> list[str]:
        return sorted(path.stem for path in self.tables.glob("*.csv"))

    def path(self, table: str) -> Path:
        path = self.tables / f"{table}.csv"
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def relation(self, table: str) -> str:
        return csv_sql(self.path(table))

    def columns(self, con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
        return [
            row["column_name"]
            for row in rows(con, f"DESCRIBE SELECT * FROM {self.relation(table)}")
        ]

    def table_manifest(self, con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
        manifest = []
        for name in self.names():
            path = self.path(name)
            result = rows(
                con,
                f"SELECT count(*) row_count FROM {self.relation(name)}",
            )[0]
            header = path.open(errors="replace").readline().rstrip("\n\r")
            manifest.append(
                {
                    "table": name,
                    "rows": result["row_count"],
                    "bytes": path.stat().st_size,
                    "header_sha256": hashlib.sha256(header.encode()).hexdigest(),
                    "columns": header.split(",") if header else [],
                }
            )
        return manifest


def attribute_bits_sql(corpus: Corpus) -> str:
    """Flatten signed CSV attribute words to stable raw bit identities."""
    words = ", ".join(f"Attributes_{index}" for index in range(ATTR_WORDS))
    return f"""
      SELECT DISTINCT SpellID, DifficultyID, word_index * 32 + bit_index AS raw
      FROM {corpus.relation("SpellMisc")},
        range(0, {ATTR_WORDS}) words(word_index), range(0, 32) bits(bit_index)
      WHERE ((list_extract([{words}], word_index + 1)::BIGINT & 4294967295)
             & (1::BIGINT << bit_index)) <> 0
    """


def effect_attribute_bits_sql(corpus: Corpus) -> str:
    return f"""
      SELECT DISTINCT SpellID, DifficultyID, EffectIndex,
        bit_index AS raw
      FROM {corpus.relation("SpellEffect")}, range(0, 32) bits(bit_index)
      WHERE ((EffectAttributes::BIGINT & 4294967295)
             & (1::BIGINT << bit_index)) <> 0
    """


def _normalized_row_sql(
    corpus: Corpus,
    con: duckdb.DuckDBPyConnection,
    table: str,
    keys: tuple[str, ...],
) -> str:
    columns = corpus.columns(con, table)
    missing = set(keys) - set(columns)
    if missing:
        raise ValueError(f"{table} lacks diff keys {sorted(missing)}")
    payload = [column for column in columns if column not in keys]
    key_columns = ", ".join(f'"{column}"' for column in keys)
    if payload:
        payload_expr = (
            "to_json(struct_pack("
            + ", ".join(f'"{column}" := "{column}"' for column in payload)
            + "))"
        )
    else:
        payload_expr = "'{}'"
    return f"SELECT {key_columns}, {payload_expr} payload FROM {corpus.relation(table)}"


def _diff_relations(
    con: duckdb.DuckDBPyConnection,
    old_sql: str,
    new_sql: str,
    keys: tuple[str, ...],
    include_rows: bool,
) -> dict[str, Any]:
    using = ", ".join(f'"{key}"' for key in keys)
    first = keys[0]
    base = f"""
      WITH old_rows AS ({old_sql}), new_rows AS ({new_sql}), joined AS (
        SELECT {", ".join(f'coalesce(o."{k}", n."{k}") "{k}"' for k in keys)},
          o.payload old_payload, n.payload new_payload,
          CASE
            WHEN o."{first}" IS NULL THEN 'added'
            WHEN n."{first}" IS NULL THEN 'removed'
            WHEN o.payload IS DISTINCT FROM n.payload THEN 'changed'
            ELSE 'same'
          END status
        FROM old_rows o FULL OUTER JOIN new_rows n USING ({using})
      )
    """
    counts = rows(
        con,
        base
        + "SELECT status, count(*) count FROM joined GROUP BY status ORDER BY status",
    )
    result: dict[str, Any] = {"counts": {row["status"]: row["count"] for row in counts}}
    if include_rows:
        result["rows"] = rows(
            con,
            base + f"SELECT * FROM joined WHERE status<>'same' ORDER BY {using}",
        )
    return result


TABLE_SURFACES: dict[str, tuple[str, tuple[str, ...]]] = {
    "spell_effects": ("SpellEffect", ("SpellID", "DifficultyID", "EffectIndex")),
    "mechanics_and_categories": ("SpellCategories", ("SpellID", "DifficultyID")),
    "category_definitions": ("SpellCategory", ("ID",)),
    "cooldowns": ("SpellCooldowns", ("SpellID", "DifficultyID")),
    "spell_power": ("SpellPower", ("SpellID", "OrderIndex", "ID")),
    "spell_power_difficulty": ("SpellPowerDifficulty", ("ID",)),
    "proc_tuples": ("SpellAuraOptions", ("SpellID", "DifficultyID")),
    "ppm_definitions": ("SpellProcsPerMinute", ("ID",)),
    "ppm_modifiers": ("SpellProcsPerMinuteMod", ("ID",)),
    "labels": ("SpellLabel", ("SpellID", "LabelID", "ID")),
    "class_masks": ("SpellClassOptions", ("SpellID", "ID")),
    "spell_misc": ("SpellMisc", ("SpellID", "DifficultyID")),
    "duration_definitions": ("SpellDuration", ("ID",)),
    "radius_definitions": ("SpellRadius", ("ID",)),
    "scaling": ("SpellScaling", ("SpellID", "ID")),
    "target_restrictions": ("SpellTargetRestrictions", ("SpellID", "DifficultyID")),
    "shapeshift_requirements": ("SpellShapeshift", ("SpellID", "ID")),
    "equipment_requirements": ("SpellEquippedItems", ("SpellID", "ID")),
    "difficulty_definitions": ("Difficulty", ("ID",)),
    "item_effects": ("ItemEffect", ("SpellID", "ID")),
    "item_effect_edges": ("ItemXItemEffect", ("ItemID", "ItemEffectID", "ID")),
    "gem_enchantment_edges": ("GemProperties", ("ID",)),
    "item_bonus_entries": ("ItemBonus", ("ID",)),
    "item_bonus_tree_nodes": ("ItemBonusTreeNode", ("ID",)),
    "item_bonus_tree_edges": ("ItemXBonusTree", ("ItemID", "ID")),
    "item_bonus_sequence_spells": ("ItemBonusSequenceSpell", ("ItemID", "ID")),
    "item_set_spells": ("ItemSetSpell", ("ItemSetID", "SpellID", "ID")),
    "item_enchantments": ("SpellItemEnchantment", ("ID",)),
}


def diff_corpora(
    old: Corpus,
    new: Corpus,
    *,
    include_rows: bool = True,
) -> dict[str, Any]:
    """Deterministically compare required semantic surfaces between builds."""
    con = duckdb.connect(":memory:")
    output: dict[str, Any] = {
        "old": str(old.tables.resolve()),
        "new": str(new.tables.resolve()),
        "surfaces": {},
    }
    common = set(old.names()) & set(new.names())
    output["tables"] = {
        "added": sorted(set(new.names()) - set(old.names())),
        "removed": sorted(set(old.names()) - set(new.names())),
    }
    for name, (table, keys) in TABLE_SURFACES.items():
        if table not in common:
            output["surfaces"][name] = {"unavailable": table}
            continue
        output["surfaces"][name] = _diff_relations(
            con,
            _normalized_row_sql(old, con, table, keys),
            _normalized_row_sql(new, con, table, keys),
            keys,
            include_rows,
        )

    derived = {
        "aura_raws": (
            f"SELECT EffectAura raw, '{{}}' payload FROM {old.relation('SpellEffect')} WHERE EffectAura<>0 GROUP BY 1",
            f"SELECT EffectAura raw, '{{}}' payload FROM {new.relation('SpellEffect')} WHERE EffectAura<>0 GROUP BY 1",
            ("raw",),
        ),
        "spell_attribute_bits": (
            f"SELECT raw, '{{}}' payload FROM ({attribute_bits_sql(old)}) GROUP BY 1",
            f"SELECT raw, '{{}}' payload FROM ({attribute_bits_sql(new)}) GROUP BY 1",
            ("raw",),
        ),
        "effect_attribute_bits": (
            f"SELECT raw, '{{}}' payload FROM ({effect_attribute_bits_sql(old)}) GROUP BY 1",
            f"SELECT raw, '{{}}' payload FROM ({effect_attribute_bits_sql(new)}) GROUP BY 1",
            ("raw",),
        ),
        "implicit_targets": (
            f"SELECT target_id raw, '{{}}' payload FROM (SELECT ImplicitTarget_0 target_id FROM {old.relation('SpellEffect')} UNION ALL SELECT ImplicitTarget_1 FROM {old.relation('SpellEffect')}) GROUP BY 1",
            f"SELECT target_id raw, '{{}}' payload FROM (SELECT ImplicitTarget_0 target_id FROM {new.relation('SpellEffect')} UNION ALL SELECT ImplicitTarget_1 FROM {new.relation('SpellEffect')}) GROUP BY 1",
            ("raw",),
        ),
    }
    for name, (old_sql, new_sql, keys) in derived.items():
        required = "SpellMisc" if name == "spell_attribute_bits" else "SpellEffect"
        if required not in common:
            output["surfaces"][name] = {"unavailable": required}
            continue
        output["surfaces"][name] = _diff_relations(
            con, old_sql, new_sql, keys, include_rows
        )

    difficulty_tables = [
        table
        for table in (
            "SpellEffect",
            "SpellMisc",
            "SpellAuraOptions",
            "SpellCategories",
            "SpellCooldowns",
            "SpellTargetRestrictions",
        )
        if table in common
    ]
    if difficulty_tables:

        def difficulty_sql(corpus: Corpus) -> str:
            parts = [
                f"SELECT '{table}' table_name, SpellID, DifficultyID, ID row_id, '{{}}' payload FROM {corpus.relation(table)}"
                for table in difficulty_tables
            ]
            return " UNION ALL ".join(parts)

        output["surfaces"]["difficulty_topology"] = _diff_relations(
            con,
            difficulty_sql(old),
            difficulty_sql(new),
            ("table_name", "SpellID", "DifficultyID", "row_id"),
            include_rows,
        )

    reference_requirements = {
        "SpellEffect",
        "SpellMisc",
        "SpellAuraOptions",
        "SpellCategories",
        "SpellPower",
    }
    if reference_requirements <= common:

        def references_sql(corpus: Corpus) -> str:
            parts = [
                f"SELECT 'SpellRadius' domain_name, EffectRadiusIndex_0 raw FROM {corpus.relation('SpellEffect')}",
                f"SELECT 'SpellRadius' domain_name, EffectRadiusIndex_1 raw FROM {corpus.relation('SpellEffect')}",
                f"SELECT 'Spell' domain_name, EffectTriggerSpell raw FROM {corpus.relation('SpellEffect')}",
                f"SELECT 'SpellDuration' domain_name, DurationIndex raw FROM {corpus.relation('SpellMisc')}",
                f"SELECT 'SpellRange' domain_name, RangeIndex raw FROM {corpus.relation('SpellMisc')}",
                f"SELECT 'SpellProcsPerMinute' domain_name, SpellProcsPerMinuteID raw FROM {corpus.relation('SpellAuraOptions')}",
                f"SELECT 'SpellCategory' domain_name, Category raw FROM {corpus.relation('SpellCategories')}",
                f"SELECT 'SpellCategory' domain_name, StartRecoveryCategory raw FROM {corpus.relation('SpellCategories')}",
                f"SELECT 'SpellCategory' domain_name, ChargeCategory raw FROM {corpus.relation('SpellCategories')}",
                f"SELECT 'PowerDisplay' domain_name, PowerDisplayID raw FROM {corpus.relation('SpellPower')}",
                f"SELECT 'Spell' domain_name, RequiredAuraSpellID raw FROM {corpus.relation('SpellPower')}",
            ]
            union = " UNION ALL ".join(parts)
            return f"SELECT domain_name, raw, '{{}}' payload FROM ({union}) WHERE raw<>0 GROUP BY 1, 2"

        output["surfaces"]["referenced_identities"] = _diff_relations(
            con,
            references_sql(old),
            references_sql(new),
            ("domain_name", "raw"),
            include_rows,
        )

    # A schema-level signal for fields that became populated from an all-zero or
    # all-empty old population. This is intentionally generic and table-scoped.
    newly_populated: list[dict[str, Any]] = []
    population_tables = {
        table for table, _keys in TABLE_SURFACES.values() if table in common
    } | {
        table
        for table in (
            "SpellEffect",
            "SpellMisc",
            "SpellTargetRestrictions",
            "SpellPowerDifficulty",
            "ItemSetSpell",
            "SpellItemEnchantment",
        )
        if table in common
    }
    for table in sorted(population_tables):
        old_columns = old.columns(con, table)
        new_columns = new.columns(con, table)
        columns = sorted(set(old_columns) & set(new_columns))
        aggregates = ", ".join(
            f"count(*) FILTER (WHERE coalesce(CAST(\"{column}\" AS VARCHAR), '') NOT IN ('', '0', '0.0')) c{index}"
            for index, column in enumerate(columns)
        )
        old_counts = rows(con, f"SELECT {aggregates} FROM {old.relation(table)}")[0]
        new_counts = rows(con, f"SELECT {aggregates} FROM {new.relation(table)}")[0]
        for index, column in enumerate(columns):
            old_nonzero = old_counts[f"c{index}"]
            if old_nonzero:
                continue
            new_nonzero = new_counts[f"c{index}"]
            if new_nonzero:
                newly_populated.append(
                    {"table": table, "column": column, "new_nonzero_rows": new_nonzero}
                )
    output["newly_populated_fields"] = newly_populated
    return output


def diff_markdown(diff: dict[str, Any]) -> str:
    lines = [
        "# Wago semantic corpus diff",
        "",
        f"- Old corpus: `{diff['old']}`",
        f"- New corpus: `{diff['new']}`",
        "",
        "## Semantic surfaces",
        "",
        "| Surface | Added | Removed | Changed | Same |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, result in sorted(diff["surfaces"].items()):
        counts = result.get("counts", {})
        if "unavailable" in result:
            lines.append(f"| {name} | unavailable: {result['unavailable']} |  |  |  |")
        else:
            lines.append(
                f"| {name} | {counts.get('added', 0)} | {counts.get('removed', 0)} | "
                f"{counts.get('changed', 0)} | {counts.get('same', 0)} |"
            )
    lines += [
        "",
        "## Table topology",
        "",
        f"- Added tables: {', '.join(diff['tables']['added']) or 'none'}",
        f"- Removed tables: {', '.join(diff['tables']['removed']) or 'none'}",
        f"- Newly populated fields: {len(diff['newly_populated_fields'])}",
        "",
        "The JSON output contains deterministic changed-row keys and old/new payloads when row output is enabled.",
        "",
    ]
    return "\n".join(lines)


def dump_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str) + "\n"
