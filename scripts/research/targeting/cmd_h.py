"""Lead commands: census, merged unknowns, witness corpus."""

from __future__ import annotations

from .cli import emit


def _out(p) -> None:
    p.add_argument("--out")


def _census(args) -> int:
    from . import census, context
    emit(census.build(context.get()), args.out)
    return 0


def _unknowns(args) -> int:
    from . import unknowns
    emit(unknowns.build(), args.out)
    return 0


def _witnesses(args) -> int:
    from . import witnesses
    doc = witnesses.build()
    emit(doc, args.out)
    return 0 if doc["summary"]["failed"] == 0 else 1


def _dummy_ids(args) -> int:
    """Key set for the creature_template_difficulty extract (tdb stage of tools/regen_targeting.py)."""
    import json

    from . import CORPORA
    doc = json.loads((CORPORA / "inputs" / "training-dummies.json").read_text(encoding="utf-8"))
    ids = sorted({row[0] for row in doc["tables"]["creature_template"]["rows"]})
    emit({"ids": ids, "source": "inputs/training-dummies.json creature_template.entry"}, args.out)
    return 0


COMMANDS = {
    "training-dummy-ids": ("entry ids of the training-dummy extract", _out, _dummy_ids),
    "census": ("current-player targeting census (joins track effect_classes)", _out, _census),
    "unknowns": ("merged unknowns / reopen conditions", _out, _unknowns),
    "witnesses": ("witness corpus: evaluate every curated witness fixture", _out, _witnesses),
}
