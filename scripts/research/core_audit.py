#!/usr/bin/env python3
"""Core semantic boundary audit CLI (read-only against pinned Core).

  core_audit.py pins                 pinned vs observed repository commits
  core_audit.py validate             registry schema, coords and cross-references
  core_audit.py coverage             mechanical denominators lacking an audited record
  core_audit.py corpora              regenerate docs/research/core-audit-corpora
  core_audit.py finding <ID>         print one finding with every record that cites it
  core_audit.py inventory <what>     rng | compilers | runtime | fields | catalogs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core_audit import extract, pins, registry  # noqa: E402
from core_audit.corpora import build, coverage, finding_cross_references, write  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pins")
    sub.add_parser("validate")
    sub.add_parser("coverage")
    sub.add_parser("corpora")
    finding = sub.add_parser("finding")
    finding.add_argument("id")
    inventory = sub.add_parser("inventory")
    inventory.add_argument("what", choices=("rng", "compilers", "runtime", "fields", "catalogs"))
    args = parser.parse_args(argv)

    if args.command == "pins":
        print(json.dumps({"pinned": pins.PINS, "observed": pins.observed()}, indent=1))
        pins.verify_core()
        return 0

    if args.command == "validate":
        records = registry.load_all()
        problems = registry.problems(records) + finding_cross_references(records)
        for problem in problems:
            print(problem)
        print(f"{sum(map(len, records.values()))} records, {len(problems)} problems")
        return 1 if problems else 0

    if args.command == "coverage":
        print(json.dumps(coverage(registry.load_all()), indent=1))
        return 0

    if args.command == "corpora":
        files = build()
        write(files)
        print(f"wrote {len(files)} corpus files")
        return 0

    if args.command == "finding":
        records = registry.load_all()
        for kind, rows in records.items():
            for record in rows:
                if record.get("id") == args.id or args.id in (record.get("finding_ids") or []):
                    print(f"--- {kind}")
                    print(json.dumps(record, indent=1))
        return 0

    table = {
        "rng": extract.rng_call_sites,
        "compilers": extract.compiler_modules,
        "runtime": extract.runtime_modules,
        "fields": extract.mutable_fields,
        "catalogs": extract.catalog_rows,
    }[args.what]
    print(json.dumps(table(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
