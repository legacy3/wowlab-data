"""Track G commands: the executable oracle over fixtures, static per-spell descriptions,
the fixture library check and the cross-track probe differential.

* ``targeting.py evaluate <fixture.json> [--trace] [--out F]``  -- run :mod:`targeting.pipeline`
* ``targeting.py explain <fixture.json>``                       -- the full stage trace, as text
* ``targeting.py fixtures``                                     -- evaluate the curated library vs ``expect``
* ``targeting.py spell <id>`` / ``targeting.py effect <spell>:<effect>`` -- static targeting description
* ``targeting.py differential [--out F]``                       -- probe-vs-Python integration summary

FailClosed exits 3 (``targeting.cli``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import CORPORA, FailClosed
from .cli import emit

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: corpora that carry ``effect_classes`` (BRIEF §10), by owning track
CLASS_CORPORA = {
    "A": ("selectors.json",), "B": ("explicit-validation.json",), "C": ("area.json", "geometry.json", "caps.json"),
    "D": ("chains.json", "smart-selection.json", "rng.json"), "E": ("script-adapters.json", "world-policy.json"),
    "F": ("group-policy.json", "effect-recipients.json"),
}


# ---------------------------------------------------------------------------
# evaluate / explain / fixtures
# ---------------------------------------------------------------------------
def _fixture_arg(p) -> None:
    p.add_argument("fixture", help="fixture JSON (targeting-fixture/1)")
    p.add_argument("--out")


def _evaluate(args) -> int:
    from .pipeline import evaluate_file
    res = evaluate_file(args.fixture)
    out = res.to_json(with_trace=args.trace)
    expect = _expect_check(args.fixture, res)
    if expect is not None:
        out["expect"] = expect
    emit(out, args.out)
    return 0 if expect is None or expect["ok"] else 1


def _expect_check(path, res) -> dict[str, Any] | None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    exp = data.get("expect") or {}
    if not exp:
        return None
    got = res.to_json(with_trace=False)
    diffs = []
    for key in ("recipients", "unique_targets", "cast_result", "draws_consumed", "dests"):
        if key in exp and exp[key] != got[key]:
            diffs.append({"key": key, "expected": exp[key], "got": got[key]})
    return {"ok": not diffs, "diffs": diffs}


def _explain(args) -> int:
    from .pipeline import evaluate_file
    res = evaluate_file(args.fixture)
    lines = [f"spell {res.spell} {res.name!r}: cast_result={res.cast_result}",
             f"draws consumed: {res.draws_consumed} {res.draws}"]
    for i, st in enumerate(res.trace.to_json()):
        head = f"{i:3d} {st['name']:<28} [{st['mirrors']}]"
        if st.get("evidence", "trinity-consumer") != "trinity-consumer":
            head += f" ({st['evidence']})"
        lines.append(head)
        if st.get("inputs"):
            lines.append(f"      in : {json.dumps(st['inputs'], sort_keys=True, default=str)}")
        if "output" in st:
            lines.append(f"      out: {json.dumps(st['output'], sort_keys=True, default=str)}")
        for n in st.get("notes", []):
            lines.append(f"      - {n}")
        if st.get("draws"):
            lines.append(f"      rng: {st['draws']}")
        if st.get("defect"):
            lines.append(f"      DEFECT: {st['defect']}")
    lines.append("recipients:")
    for k, v in sorted(res.recipients.items()):
        lines.append(f"  effect {k}: {v}")
    lines.append(f"unique targets: {res.unique_targets}")
    if res.dests:
        lines.append(f"dests: {res.dests}")
    if args.explicit_stages:
        lines += _explicit_stages(args.fixture)
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def _explicit_stages(path) -> list[str]:
    """Track B's staged explicit-target verdicts (cast eligibility / recipient / application kept apart)."""
    from . import oracle
    from .explicit import validate
    from .fixture import World
    w = World.load(path)
    if w.explicit.get("unit") is None:
        return ["explicit stages: no explicit unit"]
    try:
        verdict = validate(w, oracle.for_world(w))
    except FailClosed as exc:
        return [f"explicit stages: fail-closed ({exc})"]
    return ["explicit stages (targeting.explicit.validate):"] + \
        [f"  {k}: {json.dumps(v, sort_keys=True, default=str)}" for k, v in verdict.items()]


def _explain_args(p) -> None:
    _fixture_arg(p)
    p.add_argument("--explicit-stages", action="store_true",
                   help="also print track B's staged explicit-target verdicts (init/prepare/cast/redirect/recipient/launch/hit)")


def library() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))


def check_library() -> dict[str, Any]:
    """Evaluate every curated fixture against its ``expect`` block (FailClosed fixtures expect it)."""
    from .fixture import World
    from .pipeline import evaluate
    rows = []
    for path in library():
        data = json.loads(path.read_text(encoding="utf-8"))
        exp = data.get("expect") or {}
        row = {"fixture": path.name, "discriminates": data.get("discriminates", "")}
        try:
            res = evaluate(World.from_dict(data))
        except FailClosed as exc:
            row["result"] = "fail-closed"
            row["ok"] = bool(exp.get("fail_closed")) and exp["fail_closed"] in str(exc)
            row["message"] = str(exc)
        else:
            row["result"] = res.to_json(with_trace=False)
            chk = _expect_check(path, res)
            row["ok"] = not exp.get("fail_closed") and chk is not None and chk["ok"]
            row["diffs"] = chk["diffs"] if chk else "no expect block"
        rows.append(row)
    return {"fixtures": rows, "passed": sum(r["ok"] for r in rows), "total": len(rows)}


def _fixtures(args) -> int:
    out = check_library()
    emit(out, args.out)
    return 0 if out["passed"] == out["total"] else 1


# ---------------------------------------------------------------------------
# static descriptions
# ---------------------------------------------------------------------------
def _load_classes() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for track, names in CLASS_CORPORA.items():
        for name in names:
            path = CORPORA / name
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            classes = data.get("effect_classes")
            if isinstance(classes, dict):
                out[f"{track}:{name}"] = classes
    return out


def describe_effect(spell: int, index: int, classes: dict | None = None) -> dict[str, Any]:
    """Static targeting description of one effect (fails soft to ``unknown`` entries)."""
    from . import oracle, selectors
    from .composition import classify, reads_writes
    key = f"{spell}:{index}"
    out: dict[str, Any] = {"key": key, "unknown": []}
    try:
        sv = oracle.from_data(spell, allow_corrections=True)
    except FailClosed as exc:
        out["unknown"].append({"id": key, "why": str(exc)})
        return out
    if index >= len(sv.effects) or not sv.effects[index].is_effect:
        out["unknown"].append({"id": key, "why": "no such IsEffect effect in the snapshot"})
        return out
    e = sv.effects[index]
    out["name"] = sv.name
    out["notes"] = list(sv.notes)
    out["effect"] = {"effect": e.effect, "aura": e.aura, "chain_targets": e.chain_targets,
                     "attributes": e.attributes, "conditions": e.conditions,
                     "radius_a": None if e.radius_a is None else [e.radius_a.min, e.radius_a.max],
                     "radius_b": None if e.radius_b is None else [e.radius_b.min, e.radius_b.max]}
    sel = {}
    for slot, tid in (("A", e.target_a), ("B", e.target_b)):
        try:
            info = selectors.info(tid)
            sel[slot] = {"id": tid, **info.to_json(), "handler": selectors.handler(tid), "m_targets": reads_writes(tid)}
        except FailClosed as exc:
            sel[slot] = {"id": tid, "unknown": str(exc)}
            out["unknown"].append({"id": f"{key}:{slot}", "why": str(exc)})
    out["selectors"] = sel
    try:
        out["pair"] = classify(e.target_a, e.target_b)
    except FailClosed as exc:
        out["unknown"].append({"id": f"{key}:pair", "why": str(exc)})
    out["script_hooks"] = [h for h in (sv.script_hooks or ()) if h["affected_mask"] and h["affected_mask"] & (1 << index)]
    out["group"] = _static_group(sv, index)
    classes = _load_classes() if classes is None else classes
    per_track = {}
    for corpus, rows in sorted(classes.items()):
        row = rows.get(key)
        per_track[corpus] = row if row is not None else "unknown"
    out["classes"] = per_track
    out["consumer_path"] = [
        "Spell.cpp:691 SelectExplicitTargets", "Spell.cpp:741-784 effect-mask grouping (lead effect)",
        f"Spell.cpp:787 TargetA -> {sel.get('A', {}).get('handler', {}).get('function')}",
        f"Spell.cpp:788 TargetB -> {sel.get('B', {}).get('handler', {}).get('function')}",
        "Spell.cpp:797 SelectEffectTypeImplicitTargets", "Spell.cpp:800 AddDestTarget",
        "Spell.cpp:2443 AddUnitTarget (unique list, EffectMask |=)",
        "Spell.cpp:3980 DoProcessTargetContainer (per effect, list order)"]
    return out


def _static_group(sv, index: int) -> dict[str, Any]:
    """Grouping with caster-free radii (DB2 RadiusMin/RadiusMax); caster level/mods/movement may split it."""
    from .recipients import script_effect_check, selection_plan

    def radius(k: int, slot: str):
        e = sv.effects[k]
        entry = e.radius_a
        if slot == "B" and e.radius_b is not None:
            entry = e.radius_b
        if entry is None:
            return (0.0, 0.0)
        if (e.target_b if slot == "B" and e.radius_b is not None else e.target_a) in (72, 74, 86):
            raise FailClosed("random radius (rand_norm) in grouping")
        return (entry.min, entry.max, entry.radius, entry.per_level)
    try:
        plan = selection_plan(sv.effects, radius, script_effect_check(sv))
    except FailClosed as exc:
        return {"unknown": str(exc)}
    for step in plan:
        if step.mask & (1 << index):
            return {"selected_on_turn_of": step.effect, "mask": step.mask,
                    "note": "radius equality compared on (Min, Max, Radius, PerLevel); caster-dependent"}
    return {"unknown": "effect not in any plan step"}


def _spell_arg(p) -> None:
    p.add_argument("spell", type=int)
    p.add_argument("--out")


def _spell(args) -> int:
    from . import context
    ctx = context.get()
    classes = _load_classes()
    effects = [e.index for e in ctx.data.effects(args.spell) if e.is_effect]
    out = {"spell": args.spell, "name": ctx.name(args.spell), "build_skew": ctx.is_skew(args.spell),
           "in_player_reach": args.spell in ctx.scope.reach,
           "effects": [describe_effect(args.spell, i, classes) for i in effects]}
    if not effects:
        out["unknown"] = [{"id": str(args.spell), "why": "no IsEffect DIFFICULTY_NONE rows in the snapshot"}]
    emit(out, args.out)
    return 0


def _effect_arg(p) -> None:
    p.add_argument("key", help="<spell>:<effect index>")
    p.add_argument("--out")


def _effect(args) -> int:
    try:
        spell, index = (int(x) for x in args.key.split(":"))
    except ValueError:
        print("effect key must be <spell>:<effect>", file=sys.stderr)
        return 2
    emit(describe_effect(spell, index), args.out)
    return 0


# ---------------------------------------------------------------------------
# differential
# ---------------------------------------------------------------------------
def _diff_arg(p) -> None:
    p.add_argument("--out")
    p.add_argument("--no-build", action="store_true", help="do not run make for the probes")


def _differential(args) -> int:
    from .differential import run
    out = run(build_probes=not args.no_build)
    emit(out, args.out)
    return 0 if out["summary"]["failed"] == 0 else 1


def _trace_args(p) -> None:
    _fixture_arg(p)
    p.add_argument("--trace", action="store_true", help="include the stage trace")


COMMANDS = {
    "evaluate": ("run the SelectSpellTargets oracle on a fixture", _trace_args, _evaluate),
    "explain": ("print the oracle's stage trace for a fixture", _explain_args, _explain),
    "fixtures": ("evaluate the curated fixture library against its expectations",
                 lambda p: p.add_argument("--out"), _fixtures),
    "spell": ("static targeting description of a spell (all effects)", _spell_arg, _spell),
    "effect": ("static targeting description of one effect (<spell>:<effect>)", _effect_arg, _effect),
    "differential": ("run every track's Trinity probe against the Python stages", _diff_arg, _differential),
}
