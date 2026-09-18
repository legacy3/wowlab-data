"""``aura_lifecycle.py`` command line.

Commands are contributed by ``aura_lifecycle/cmd_*.py`` modules.  Each defines::

    COMMANDS = {
        "name": (help_text, add_arguments(parser) -> None, run(args) -> int),
    }

so independent tracks never edit one shared dispatch table.  Output is JSON on
stdout (``--out`` writes a file instead); corpus writers use :func:`write_json`,
which produces byte-stable output (sorted keys, ``\\n`` terminated).
"""

from __future__ import annotations

import argparse
import importlib
import json
import pkgutil
import sys
from pathlib import Path
from typing import Any

from . import FailClosed


def write_json(path: Path | str, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def emit(payload: Any, out: str | None) -> None:
    if out:
        write_json(out, payload)
    else:
        json.dump(payload, sys.stdout, indent=1, sort_keys=True, ensure_ascii=False)
        sys.stdout.write("\n")


def _commands() -> dict[str, tuple]:
    import aura_lifecycle
    found: dict[str, tuple] = {}
    for mod in sorted(pkgutil.iter_modules(aura_lifecycle.__path__), key=lambda m: m.name):
        if not mod.name.startswith("cmd_"):
            continue
        module = importlib.import_module(f"aura_lifecycle.{mod.name}")
        for name, spec in getattr(module, "COMMANDS", {}).items():
            if name in found:
                raise RuntimeError(f"duplicate aura_lifecycle command {name!r} ({mod.name})")
            found[name] = spec
    return found


def main(argv: list[str] | None = None) -> int:
    commands = _commands()
    parser = argparse.ArgumentParser(prog="aura_lifecycle.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in sorted(commands):
        help_text, add_args, _ = commands[name]
        p = sub.add_parser(name, help=help_text)
        add_args(p)
    args = parser.parse_args(argv)
    try:
        return commands[args.command][2](args) or 0
    except FailClosed as exc:
        print(f"fail-closed: {exc}", file=sys.stderr)
        return 3
