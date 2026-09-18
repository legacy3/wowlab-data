"""Track H commands: ``overlays <spell>`` and ``overlays --corpus``.

``aura_lifecycle.py overlays 774``            external lifecycle facts of one spell
``aura_lifecycle.py overlays --corpus --out docs/research/aura-lifecycle-corpora/external-policy.json``
"""

from __future__ import annotations

from . import FailClosed
from .cli import emit

CORPUS_COMMAND = "aura_lifecycle.py overlays --corpus --out docs/research/aura-lifecycle-corpora/external-policy.json"


def _args(p) -> None:
    p.add_argument("spell", type=int, nargs="?")
    p.add_argument("--corpus", action="store_true", help="write the external-policy corpus")
    p.add_argument("--out")


def _run(args) -> int:
    from . import context, overlays
    ctx = context.get()
    if args.corpus:
        emit(overlays.corpus(ctx, CORPUS_COMMAND), args.out)
        return 0
    if args.spell is None:
        raise FailClosed("overlays: give a spell id or --corpus")
    emit(overlays.explain(ctx, args.spell), args.out)
    return 0


COMMANDS = {
    "overlays": ("external lifecycle policy (world overlays, corrections, engine ids, scripts)", _args, _run),
}
