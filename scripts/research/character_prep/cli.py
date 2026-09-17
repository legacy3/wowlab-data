"""Command-line entry point for the character_prep research package.

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
COMMAND_MODULES: list[str] = [
    "basestats_tdb",  # F
    "differential",  # F
    "server_inputs",  # F
    "compiler",  # E: compile / compile-all / explain
    "fixture",  # E: validate / schema
    "graph",  # E: graph
    "inputs",  # E: inventory / initial-state
    "report",  # E: synth-fixtures / fixtures-index / all
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="character_prep", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMAND_MODULES:
        module = importlib.import_module(f"character_prep.{name}")
        module.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)
