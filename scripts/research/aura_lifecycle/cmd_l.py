"""Track L commands: the Retail experiment catalogue and its observation surface."""

from __future__ import annotations

import json
from pathlib import Path

from .cli import emit


def _add(p) -> None:
    p.add_argument("--out", help="write the corpus here (retail-experiments.json)")
    p.add_argument("--candidates", nargs="*", default=[],
                   help="corpus JSON files whose retail_experiments are merged (deduped) into the catalogue")
    p.add_argument("--scan", action="store_true",
                   help="merge retail_experiments from every other corpus in docs/research/aura-lifecycle-corpora")
    p.add_argument("--surface-only", action="store_true",
                   help="print the observation surface and fidelity grid only (no snapshot load)")


def _candidate_files(args) -> list[Path]:
    from . import CORPORA
    files = [Path(f) for f in args.candidates]
    if args.scan and CORPORA.is_dir():
        files += [f for f in sorted(CORPORA.glob("*.json")) if f.name != "retail-experiments.json"]
    return files


def _run(args) -> int:
    from . import experiments as X
    if args.surface_only:
        grid = {ctx: {q: X.best_channel(q, ctx, margin_ms=1000)[0] for q in X.QUANTITIES} for ctx in X.CONTEXTS}
        emit({"surface_pin": X.SURFACE_PIN, "sources": X.SOURCES, "channels": X.CHANNELS,
              "fidelity_grid_margin_1000ms": grid}, args.out)
        return 0
    candidates = []
    for f in _candidate_files(args):
        payload = json.loads(f.read_text(encoding="utf-8"))
        candidates += [c for c in payload.get("retail_experiments", []) if not c["id"].startswith("AL-X-L-")]
    from . import context
    emit(X.corpus(context.get(), candidates), args.out)
    return 0


COMMANDS = {
    "experiments": ("Retail experiment catalogue (observation surface, fidelity, models, witnesses)", _add, _run),
}
