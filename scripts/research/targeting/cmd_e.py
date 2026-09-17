"""Track E commands: script target adapters and world-database target policy."""

from __future__ import annotations

from . import CORPORA
from .cli import emit


def _out(p) -> None:
    p.add_argument("--out", help="write JSON here (corpus path for the committed corpus)")


def _script_adapters(args) -> int:
    from . import adapters, context
    ctx = context.get()
    emit(adapters.corpus(ctx, "python3 targeting.py script-adapters --out docs/research/targeting-corpora/script-adapters.json"),
         args.out)
    return 0


def _adapter(args) -> int:
    from . import adapters, context
    ctx = context.get()
    inv = adapters.build_inventory(ctx)
    emit({"spell": args.spell, "name": ctx.name(args.spell), "tier": inv.tiers.tier(args.spell),
          "adapters": [r for r in inv.adapters if r["spell"] == args.spell],
          "helper_sites": [r for r in inv.helpers if r["spell"] == args.spell],
          "outside_scope": [r for r in inv.outside if r["spell"] == args.spell]}, args.out)
    return 0


def _world_policy(args) -> int:
    from . import context, world
    ctx = context.get()
    emit(world.corpus(ctx, "python3 targeting.py world-policy --out docs/research/targeting-corpora/world-policy.json"),
         args.out)
    return 0


def _world_spell(args) -> int:
    from . import context, world
    ctx = context.get()
    emit(world.spell_policy(ctx, args.spell), args.out)
    return 0


def _spell_args(p) -> None:
    p.add_argument("spell", type=int)
    _out(p)


def _all(args) -> int:
    from . import adapters, context, world
    from .cli import write_json
    ctx = context.get()
    write_json(CORPORA / "script-adapters.json",
               adapters.corpus(ctx, "python3 targeting.py script-adapters --out docs/research/targeting-corpora/script-adapters.json"))
    write_json(CORPORA / "world-policy.json",
               world.corpus(ctx, "python3 targeting.py world-policy --out docs/research/targeting-corpora/world-policy.json"))
    return 0


COMMANDS = {
    "script-adapters": ("track E: script target adapter inventory (corpus)", _out, _script_adapters),
    "adapter": ("track E: script adapters / helper sites of one spell", _spell_args, _adapter),
    "world-policy": ("track E: world-database target policy (corpus)", _out, _world_policy),
    "world-spell": ("track E: world-database target policy rows of one spell", _spell_args, _world_spell),
    "e-all": ("track E: write script-adapters.json and world-policy.json", lambda p: None, _all),
}
