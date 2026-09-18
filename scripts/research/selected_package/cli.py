"""``selected_package.py`` command line.

Commands are contributed by ``selected_package/cmd_*.py`` modules, each defining::

    COMMANDS = {"name": (help_text, add_arguments(parser) -> None, run(args) -> int)}

so independent tracks never edit one shared dispatch table.  Output is JSON on stdout
(``--out`` writes a file instead); corpus writers use :func:`write_json`, which produces
byte-stable output (sorted keys, ``\\n`` terminated) so a regeneration diff is meaningful.

Every command fails closed: missing evidence exits 3 with the reason on stderr rather
than emitting a partial corpus.
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
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def emit(payload: Any, out: str | None) -> None:
    if out:
        write_json(out, payload)
    else:
        json.dump(payload, sys.stdout, indent=1, sort_keys=True, ensure_ascii=False)
        sys.stdout.write("\n")


def _commands() -> dict[str, tuple]:
    import selected_package
    found: dict[str, tuple] = {}
    for module_info in sorted(pkgutil.iter_modules(selected_package.__path__),
                              key=lambda m: m.name):
        if not module_info.name.startswith("cmd_"):
            continue
        module = importlib.import_module(f"selected_package.{module_info.name}")
        for name, spec in getattr(module, "COMMANDS", {}).items():
            if name in found:
                raise RuntimeError(f"duplicate command {name!r} ({module_info.name})")
            found[name] = spec
    return found


def main(argv: list[str] | None = None) -> int:
    commands = _commands()
    parser = argparse.ArgumentParser(prog="selected_package.py",
                                     description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in sorted(commands):
        help_text, add_args, _ = commands[name]
        add_args(sub.add_parser(name, help=help_text))
    args = parser.parse_args(argv)
    try:
        return commands[args.command][2](args) or 0
    except FailClosed as exc:
        print(f"fail-closed: {exc}", file=sys.stderr)
        return 3
