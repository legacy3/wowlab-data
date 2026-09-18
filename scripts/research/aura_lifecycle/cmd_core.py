"""Core commands: pins and raw lifecycle source rows of one spell."""

from __future__ import annotations

from . import PINS
from .cli import emit
from .context import TABLES


def _add_spell(p) -> None:
    p.add_argument("spell", type=int)
    p.add_argument("--drift", action="store_true", help="read the dbc-resolver drift snapshot instead")
    p.add_argument("--out")


def _pins(args) -> int:
    emit(PINS, getattr(args, "out", None))
    return 0


def _rows(args) -> int:
    from . import FailClosed, context
    ctx = context.get()
    data = ctx.drift if args.drift else ctx.data
    if data is None:
        raise FailClosed("drift snapshot not fetched (tools/fetch_dbc_release.py)")
    misc = data.row("SpellMisc", args.spell)
    out = {"spell": args.spell, "name": ctx.name(args.spell), "build_skew": ctx.is_skew(args.spell),
           "snapshot": PINS["drift_snapshot" if args.drift else "data_snapshot"],
           "effects": data.effects(args.spell)}
    for table in TABLES:
        if table not in ("SpellEffect", "SpellDuration", "SpellProcsPerMinute"):
            out[table] = data.row(table, args.spell)
    if misc:
        out["duration"] = data.duration(misc["DurationIndex"]) if misc["DurationIndex"] else None
        out["pvp_duration"] = data.duration(misc["PvPDurationIndex"]) if misc["PvPDurationIndex"] else None
    out["absent_tables"] = sorted(data.absent)
    emit(out, args.out)
    return 0


COMMANDS = {
    "pins": ("pinned builds / revisions", lambda p: p.add_argument("--out"), _pins),
    "rows": ("raw lifecycle source rows of one spell", _add_spell, _rows),
}
