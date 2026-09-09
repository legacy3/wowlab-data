#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb==1.5.5"]
# ///
"""Reproducible inventory and build-diff driver for the remaining-data audit."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections import Counter
from pathlib import Path

import duckdb
from wago_research import Corpus, diff_corpora, diff_markdown, dump_json, git_head

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TABLES = ROOT / "data" / "tables"
REPORTS = ROOT / "docs" / "research" / "remaining-data-audit"


GROUP_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("spell", ("Spell",)),
    ("item", ("Item", "Gem", "Azerite", "Crafting", "ModifiedCrafting")),
    (
        "world",
        ("Map", "Area", "Phase", "Scene", "Seamless", "Taxi", "Transport", "Location"),
    ),
    ("character", ("Chr", "SkillLine", "Shapeshift", "Specialization")),
    ("combat", ("Combat", "Creature", "Faction", "Power", "ExpectedStat")),
    ("quest", ("Quest", "Journal", "Adventure")),
    ("presentation", ("Ui", "UI", "Sound", "Model", "Texture", "Camera", "Loading")),
)

FOCUS_TABLES = (
    "Spell",
    "SpellEffect",
    "SpellMisc",
    "SpellAuraOptions",
    "SpellProcsPerMinute",
    "SpellProcsPerMinuteMod",
    "SpellCategories",
    "SpellCategory",
    "SpellCooldowns",
    "SpellPower",
    "SpellPowerDifficulty",
    "SpellClassOptions",
    "SpellLabel",
    "SpellAuraRestrictions",
    "SpellTargetRestrictions",
    "SpellCastingRequirements",
    "SpellRange",
    "SpellRadius",
    "SpellDuration",
    "SpellScaling",
    "SpellShapeshift",
    "SpellShapeshiftForm",
    "SpellEquippedItems",
    "Item",
    "ItemSparse",
    "ItemEffect",
    "ItemXItemEffect",
    "SpellItemEnchantment",
    "GemProperties",
    "ItemBonus",
    "ItemBonusList",
    "ItemBonusTreeNode",
    "ItemXBonusTree",
    "ItemSet",
    "ItemSetSpell",
    "ItemBonusSequenceSpell",
    "Map",
    "MapDifficulty",
    "Phase",
    "PhaseXPhaseGroup",
    "SeamlessSite",
    "Location",
    "TaxiNodes",
    "TaxiPath",
    "TaxiPathNode",
    "TransportAnimation",
)

REFERENCE_TESTS = (
    ("SpellMisc", "CastingTimeIndex", "SpellCastTimes"),
    ("SpellMisc", "DurationIndex", "SpellDuration"),
    ("SpellMisc", "PvPDurationIndex", "SpellDuration"),
    ("SpellMisc", "RangeIndex", "SpellRange"),
    ("SpellEffect", "EffectRadiusIndex_0", "SpellRadius"),
    ("SpellEffect", "EffectRadiusIndex_1", "SpellRadius"),
    ("SpellEffect", "EffectTriggerSpell", "Spell"),
    ("SpellAuraOptions", "SpellProcsPerMinuteID", "SpellProcsPerMinute"),
    ("SpellCategories", "Category", "SpellCategory"),
    ("SpellCategories", "StartRecoveryCategory", "SpellCategory"),
    ("SpellCategories", "ChargeCategory", "SpellCategory"),
    ("SpellPower", "PowerDisplayID", "PowerDisplay"),
    ("SpellPower", "RequiredAuraSpellID", "Spell"),
    ("ItemXItemEffect", "ItemID", "Item"),
    ("ItemXItemEffect", "ItemEffectID", "ItemEffect"),
    ("ItemEffect", "SpellID", "Spell"),
    ("GemProperties", "Enchant_ID", "SpellItemEnchantment"),
    ("ItemSetSpell", "SpellID", "Spell"),
    ("ItemBonusSequenceSpell", "SpellID", "Spell"),
    ("SeamlessSite", "MapID", "Map"),
    ("PhaseXPhaseGroup", "PhaseID", "Phase"),
    ("TaxiPath", "FromTaxiNode", "TaxiNodes"),
    ("TaxiPath", "ToTaxiNode", "TaxiNodes"),
)


def table_group(name: str) -> str:
    for group, prefixes in GROUP_RULES:
        if name.startswith(prefixes):
            return group
    return "other"


def provenance(corpus: Corpus) -> dict[str, object]:
    metadata = sorted((ROOT / "changes" / "metadata").glob("*.json"))
    current = max(
        metadata,
        key=lambda path: tuple(int(part) for part in path.stem.split(".")),
    )
    build = json.loads(current.read_text())
    return {
        "wowlab_data": git_head(ROOT),
        "wago_build": build.get("version", current.stem),
        "tables_requested": build.get("tablesRequested"),
        "tables_downloaded": build.get("tablesDownloaded"),
        "tables_failed": build.get("tablesFailed"),
        "trinitycore": git_head(ROOT.parent / "TrinityCore"),
        "simc": git_head(ROOT.parent / "simc"),
        "core": git_head(ROOT.parent / "core"),
        "duckdb": duckdb.__version__,
        "python": platform.python_version(),
        "corpus": str(corpus.tables.resolve()),
    }


def inventory(corpus: Corpus) -> dict[str, object]:
    con = duckdb.connect(":memory:")
    manifest = corpus.table_manifest(con)
    groups = Counter(table_group(row["table"]) for row in manifest)
    return {
        "provenance": provenance(corpus),
        "summary": {
            "csv_tables": len(manifest),
            "rows": sum(row["rows"] for row in manifest),
            "bytes": sum(row["bytes"] for row in manifest),
            "groups": dict(sorted(groups.items())),
        },
        "tables": manifest,
    }


def current_facts(corpus: Corpus) -> dict[str, object]:
    """Compute reusable complete-population facts used across the reports."""
    con = duckdb.connect(":memory:")
    names = set(corpus.names())
    table_counts: dict[str, dict[str, int]] = {}
    for table in FOCUS_TABLES:
        if table not in names:
            continue
        columns = corpus.columns(con, table)
        selects = ["count(*) row_count"]
        if "SpellID" in columns:
            selects.append("count(DISTINCT SpellID) spell_count")
        if "DifficultyID" in columns:
            selects += [
                "count(DISTINCT DifficultyID) difficulty_count",
                "count(*) FILTER (WHERE DifficultyID<>0) nonzero_difficulty_rows",
            ]
        result = con.execute(
            f"SELECT {', '.join(selects)} FROM {corpus.relation(table)}"
        )
        table_counts[table] = dict(
            zip([x[0] for x in result.description], result.fetchone(), strict=True)
        )

    difficulty_tables: list[dict[str, object]] = []
    for table in corpus.names():
        columns = corpus.columns(con, table)
        if "DifficultyID" not in columns:
            continue
        select = [
            "count(*) row_count",
            "count(DISTINCT DifficultyID) difficulty_count",
            "count(*) FILTER (WHERE DifficultyID<>0) nonzero_difficulty_rows",
        ]
        if "SpellID" in columns:
            select += [
                "count(DISTINCT SpellID) spell_count",
                "count(DISTINCT SpellID) FILTER (WHERE DifficultyID<>0) nonzero_difficulty_spells",
            ]
        cursor = con.execute(
            f"SELECT {', '.join(select)} FROM {corpus.relation(table)}"
        )
        difficulty_tables.append(
            {
                "table": table,
                **dict(
                    zip(
                        [x[0] for x in cursor.description],
                        cursor.fetchone(),
                        strict=True,
                    )
                ),
            }
        )

    reference_tests: list[dict[str, object]] = []
    for child, field, parent in REFERENCE_TESTS:
        if child not in names or parent not in names:
            continue
        result = con.execute(
            f"""
              SELECT count(*) child_rows,
                count(*) FILTER (WHERE c.\"{field}\"=0) zero_rows,
                count(*) FILTER (WHERE c.\"{field}\"<>0) nonzero_rows,
                count(DISTINCT c.\"{field}\") FILTER (WHERE c.\"{field}\"<>0) nonzero_values,
                count(*) FILTER (WHERE c.\"{field}\"<>0 AND p.ID IS NOT NULL) matched_rows,
                count(DISTINCT c.\"{field}\") FILTER (WHERE c.\"{field}\"<>0 AND p.ID IS NOT NULL) matched_values
              FROM {corpus.relation(child)} c
              LEFT JOIN {corpus.relation(parent)} p ON p.ID=c.\"{field}\"
            """
        )
        reference_tests.append(
            {
                "child": child,
                "field": field,
                "parent": parent,
                **dict(
                    zip(
                        [x[0] for x in result.description],
                        result.fetchone(),
                        strict=True,
                    )
                ),
            }
        )
    return {
        "provenance": provenance(corpus),
        "table_counts": table_counts,
        "difficulty_tables": difficulty_tables,
        "reference_tests": reference_tests,
    }


def inventory_markdown(data: dict[str, object]) -> str:
    provenance_data = data["provenance"]
    summary = data["summary"]
    assert isinstance(provenance_data, dict)
    assert isinstance(summary, dict)
    lines = [
        "# Current Wago table inventory",
        "",
        f"- Build: `{provenance_data['wago_build']}`",
        f"- Tables: {summary['csv_tables']:,}",
        f"- Rows across CSV relations: {summary['rows']:,}",
        f"- Bytes: {summary['bytes']:,}",
        "",
        "| Group | Tables |",
        "|---|---:|",
    ]
    groups = summary["groups"]
    assert isinstance(groups, dict)
    for group, count in groups.items():
        lines.append(f"| {group} | {count} |")
    lines += [
        "",
        "The machine-readable inventory includes every table, row count, byte count, column list, and header hash.",
        "",
    ]
    return "\n".join(lines)


def write(path: Path | None, content: str) -> None:
    if path is None:
        print(content, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    inv = subparsers.add_parser("inventory")
    inv.add_argument("--corpus", type=Path, default=DEFAULT_TABLES)
    inv.add_argument("--json", type=Path)
    inv.add_argument("--markdown", type=Path)

    diff = subparsers.add_parser("diff")
    diff.add_argument("old", type=Path)
    diff.add_argument("new", type=Path)
    diff.add_argument("--json", type=Path)
    diff.add_argument("--markdown", type=Path)
    diff.add_argument("--counts-only", action="store_true")

    facts_parser = subparsers.add_parser("facts")
    facts_parser.add_argument("--corpus", type=Path, default=DEFAULT_TABLES)
    facts_parser.add_argument("--json", type=Path)

    validate = subparsers.add_parser("validate-reports")
    validate.add_argument("--directory", type=Path, default=REPORTS)

    args = parser.parse_args()
    if args.command == "inventory":
        data = inventory(Corpus(args.corpus))
        json_content = dump_json(data)
        markdown_content = inventory_markdown(data)
        if not args.json and not args.markdown:
            print(json_content, end="")
        else:
            if args.json:
                write(args.json, json_content)
            if args.markdown:
                write(args.markdown, markdown_content)
        return

    if args.command == "diff":
        data = diff_corpora(
            Corpus(args.old), Corpus(args.new), include_rows=not args.counts_only
        )
        json_content = dump_json(data)
        markdown_content = diff_markdown(data)
        if not args.json and not args.markdown:
            print(json_content, end="")
        else:
            if args.json:
                write(args.json, json_content)
            if args.markdown:
                write(args.markdown, markdown_content)
        return

    if args.command == "facts":
        content = dump_json(current_facts(Corpus(args.corpus)))
        write(args.json, content)
        return

    expected = [
        "00-index.md",
        "01-proc-and-aura-options.md",
        "02-cooldown-category-and-charge-topology.md",
        "03-spell-power-and-cost-semantics.md",
        "04-applicability-selectors.md",
        "05-mechanic-dispel-immunity-and-school-semantics.md",
        "06-difficulty-resolution.md",
        "07-scaling-and-coefficient-provenance.md",
        "08-duration-periodic-and-refresh-semantics.md",
        "09-target-range-radius-and-cast-restrictions.md",
        "10-shapeshift-form-and-equipment-requirements.md",
        "11-threat-aggro-and-combat-state-metadata.md",
        "12-item-spell-enchant-and-bonus-relationships.md",
        "13-movement-teleport-world-and-host-transition-data.md",
        "14-derived-static-spell-classification.md",
        "15-cross-surface-semantic-model.md",
        "16-unresolved-and-rejected-inferences.md",
        "17-current-build-coverage-and-future-diff-plan.md",
    ]
    markdown_files = sorted(args.directory.glob("*.md"))
    names = [path.name for path in markdown_files]
    missing = [name for name in expected if name not in names]
    if missing:
        raise SystemExit(f"missing ordered reports: {', '.join(missing)}")
    broken: list[str] = []
    index_text = (args.directory / "00-index.md").read_text()
    for path in markdown_files:
        if path.name != "00-index.md" and f"]({path.name})" not in index_text:
            broken.append(path.name)
    if broken:
        raise SystemExit(f"index does not link: {', '.join(broken)}")
    vocabulary = ("verified", "partial", "rejected", "unknown after exhaustive")
    incomplete = []
    for path in markdown_files:
        lower = path.read_text().lower()
        if any(term not in lower for term in vocabulary):
            incomplete.append(path.name)
    if incomplete:
        raise SystemExit(
            "reports missing terminal vocabulary: " + ", ".join(incomplete)
        )
    print(f"validated {len(markdown_files)} ordered Markdown reports")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
