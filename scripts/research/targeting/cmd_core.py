"""Core commands: pins and raw per-spell targeting rows."""

from __future__ import annotations

from . import PINS
from .cli import emit


def _add_spell(p) -> None:
    p.add_argument("spell", type=int)
    p.add_argument("--out")


def _pins(args) -> int:
    emit(PINS, getattr(args, "out", None))
    return 0


def _rows(args) -> int:
    from dataclasses import asdict

    from . import context
    ctx = context.get()
    d = ctx.data
    emit({"spell": args.spell, "name": ctx.name(args.spell), "build_skew": ctx.is_skew(args.spell),
          "effects": [asdict(e) for e in d.effects(args.spell)],
          "restrictions": d.restrictions(args.spell), "misc": d.misc(args.spell),
          "aura_restrictions": d.aura_restrictions(args.spell),
          "casting_requirements": d.casting_requirements(args.spell)}, args.out)
    return 0


COMMANDS = {
    "pins": ("pinned builds / revisions", lambda p: p.add_argument("--out"), _pins),
    "rows": ("raw targeting source rows of one spell", _add_spell, _rows),
}
