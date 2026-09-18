"""Track F commands: area aura recipient lifecycle.

    aura_lifecycle.py recipients <spell>                   lifecycle profile of one spell
    aura_lifecycle.py recipients --timeline FIXTURE.json   run one World fixture (JSON log)
    aura_lifecycle.py recipients --scenario S01            one canonical timeline
    aura_lifecycle.py recipients --corpus --out docs/research/aura-lifecycle-corpora/area-lifecycle.json
"""

from __future__ import annotations

import json
from pathlib import Path

from . import FailClosed
from .cli import emit


def _add(p) -> None:
    p.add_argument("spell", type=int, nargs="?")
    p.add_argument("--timeline", help="World fixture JSON (see aura_lifecycle.recipients.World)")
    p.add_argument("--scenario", help="canonical scenario id prefix (S01..S18)")
    p.add_argument("--corpus", action="store_true", help="build area-lifecycle.json")
    p.add_argument("--out")


def _run(args) -> int:
    from . import recipients as R
    if args.timeline:
        emit(R.timeline(json.loads(Path(args.timeline).read_text(encoding="utf-8"))), args.out)
        return 0
    if args.scenario:
        for sc in R.SCENARIOS:
            if sc["id"].startswith(args.scenario):
                emit({"id": sc["id"], "question": sc["question"], "rules_out": sc["rules_out"], **R.run_scenario(sc)}, args.out)
                return 0
        for sc in R.AT_SCENARIOS:
            if sc["id"].startswith(args.scenario):
                emit({"id": sc["id"], "question": sc["question"], "log": R.at_timeline(sc["fixture"])}, args.out)
                return 0
        raise FailClosed(f"no scenario {args.scenario!r}")
    from . import context
    ctx = context.get()
    if args.corpus:
        emit(R.build_corpus(ctx), args.out)
        return 0
    if args.spell is None:
        raise FailClosed("recipients: give a spell id, --scenario, --timeline or --corpus")
    emit(R.profile(ctx, args.spell), args.out)
    return 0


COMMANDS = {
    "recipients": ("area aura recipient lifecycle (profile / timelines / corpus)", _add, _run),
}
