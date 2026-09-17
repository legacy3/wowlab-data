"""Command-line entry point for the controlled_units research package.

Subcommands are contributed by modules: every module listed in COMMAND_MODULES
exposes register(subparsers) which adds one or more subparsers, each with
set_defaults(func=callable) where callable(args) -> int.  Keeping the
registration per module lets the modules be developed independently.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

#: module names (relative to this package) that contribute subcommands; keep sorted
COMMAND_MODULES: list[str] = []
COMMAND_MODULES += ["vocabulary", "population", "census", "ownership", "lifecycle", "witnesses_a"]  # Track A
COMMAND_MODULES += ["stats", "inheritance", "spells", "witnesses_b"]  # Track B


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="controlled_units", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMAND_MODULES:
        module = importlib.import_module(f"controlled_units.{name}")
        module.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)
