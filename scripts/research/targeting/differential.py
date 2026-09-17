"""Cross-track differential integration: every targeting probe vs the Python stages.

``targeting.py differential --out docs/research/targeting-corpora/differential.json``

Each track's probe compiles verbatim Trinity code (extracted from the sibling checkout)
against minimal stubs.  This module builds every probe (``make -s -C tools/<probe>``),
feeds it **deterministic generated cases** (fixed seeds, ``random.Random``; floats are
binary32 values written as C99 hex) and compares each answer with the Python stage the
pipeline actually calls.  Output is byte-stable (no timings, no absolute paths); per probe
and per check it records case / pass / fail counts and the first failures.

Probes and what is probe-verified (see :data:`BOUNDARY` for the integration boundary):

* ``tc_target_selector_probe`` (A): ``SpellImplicitTargetInfo::_data`` (5 axes, direction
  angle, IsArea, GetTargetFlagMask, GetExplicitTargetMask), ``SpellEffectInfo::_data`` +
  ``GetMissingTargetMask``, ``_InitializeExplicitTargetMask`` on generated and on every
  current-player spell;
* ``tc_target_geom_probe`` (C): binary32 position predicates, the area / cone / line checks,
  the nearby last-searcher and ``std::list::sort(ObjectDistanceOrderPred)``;
* ``tc_target_chain_probe`` (D): ``Spell::SearchChainTargets`` jump selection;
* ``tc_target_relation_probe`` (B): reaction, ``IsValidAttackTarget`` / ``IsValidAssistTarget``
  (track B's own generator ``cmd_b.differential``);
* ``pipeline``: the candidate lists the *pipeline* produced for every library fixture are
  re-checked with the geometry probe's area predicate, and every chain stage with the chain
  probe -- i.e. the stage inputs the pipeline wires (centre, referer, radius x RadiusMod,
  reason, lead effect) agree with Trinity's predicate on the same inputs.
"""

from __future__ import annotations

import json
import random
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any

from . import PINS, RESEARCH, TC_ROOT, FailClosed

TOOLS = RESEARCH / "tools"
SEED = 20260917
PROBES = ("tc_target_selector_probe", "tc_target_geom_probe", "tc_target_chain_probe", "tc_target_relation_probe",
          "tc_target_positivity_probe", "tc_target_at_probe")

#: what the probes verify vs what is only consumer-read (the integration boundary)
BOUNDARY = {
    "probe-verified": [
        "SpellImplicitTargetInfo::_data axes / CalcDirectionAngle / GetExplicitTargetMask (A)",
        "SpellEffectInfo::_data, GetMissingTargetMask, SpellInfo::_InitializeExplicitTargetMask (A)",
        "Position/WorldObject binary32 distance, arc, line, range predicates (C)",
        "WorldObjectSpellAreaTargetCheck / ConeTargetCheck / LineTargetCheck geometry + immunity part (C)",
        "WorldObjectLastSearcher + WorldObjectSpellNearbyTargetCheck (C)",
        "std::list::sort with ObjectDistanceOrderPred (non-strict descending) (C)",
        "Spell::SearchChainTargets jump loop incl. search radius (D)",
        "SpellInfo::_InitializeSpellPositivity / _isPositiveEffectImpl under driver load orders (G)",
        "AreaTrigger::SearchUnitIn* shapes + max search radius (I)",
        "Containers::RandomResize / SelectRandomContainerElement draw counts (D, own tests)",
        "WorldObject::GetReactionTo / IsValidAttackTarget / IsValidAssistTarget / IsInPartyWith / IsInRaidWith (B)",
        "pipeline-produced area candidates and chain jumps re-checked against the C/D probes (G)",
    ],
    "consumer-read-only": [
        ("Spell::SelectSpellTargets loop, effect-mask grouping and the per-turn REQUIRE_ALL_TARGETS / channel checks "
         "(Spell.cpp:720-878) -- Python mirror, differentially tested against track F's plan, not compiled"),
        "Spell::AddUnitTarget merge / immunity order (Spell.cpp:2443-2559) -- track F UniqueTargets, not compiled",
        "Spell::CheckEffectTarget (Spell.cpp:8174) and SpellInfo::CheckTarget (SpellInfo.cpp:2329) -- consumer-read",
        "SelectImplicit{Caster,Target,Dest}DestTargets / CasterObject / TargetObject / Channel dispatch -- consumer-read",
        "SpellEffectInfo::CalcRadius / SpellInfo::GetMaxRange / Player::ApplySpellMod arithmetic -- consumer-read",
        "SpellEffectInfo::CalcValue()/CalcBaseValue() at load (only its sign feeds positivity) -- consumer-read",
        "InitExplicitTargets / CheckCast pre-stage (opt-in `precast`) -- track B, relation part probe-verified",
        "script target hooks (E), implicit-target conditions (ConditionMgr) -- fixture-stated or fail closed",
    ],
    "world-state (fixture-stated, never computed)": [
        "grid visit order (Cell::Visit / GridRefManager order)", "line of sight (VMAP)",
        "MovePositionToFirstCollision / map height / spell_target_position destinations",
        "SpellHitResult RNG consumption and results", "IsImmunedToSpellEffect", "CanSeeOrDetect",
        "phase membership", "DisableMgr rows (world `disables` not in the overlay)",
    ],
}


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def _rel(p: Path) -> str:
    return str(p.relative_to(RESEARCH))


def available() -> bool:
    return (TC_ROOT / "src/server/game/Spells/Spell.cpp").is_file() and shutil.which("g++") is not None


def build(name: str) -> Path:
    subprocess.run(["make", "-s", "-C", str(TOOLS / name)], check=True, capture_output=True)
    return TOOLS / name / "probe"


class Check:
    def __init__(self, name: str, mirrors: str) -> None:
        self.name = name
        self.mirrors = mirrors
        self.cases = 0
        self.failed: list[Any] = []
        self.fail_closed = 0

    def record(self, ok: bool, detail: Any = None) -> None:
        self.cases += 1
        if not ok:
            self.failed.append(detail)

    def to_json(self) -> dict[str, Any]:
        return {"mirrors": self.mirrors, "cases": self.cases, "passed": self.cases - len(self.failed),
                "failed": len(self.failed), "fail_closed": self.fail_closed, "first_failures": self.failed[:3]}


def _probe_json(checks: list[Check], dirname: str, extra: dict | None = None) -> dict[str, Any]:
    out = {"probe": f"tools/{dirname}", "status": "ok", "checks": {c.name: c.to_json() for c in checks}}
    out.update(extra or {})
    return out


# ---------------------------------------------------------------------------
# A: selector vocabulary probe
# ---------------------------------------------------------------------------
def selector_probe(ctx=None) -> dict[str, Any]:
    from . import composition as C
    from . import selectors as S
    probe = build("tc_target_selector_probe")

    def run(*args: str, stdin: str | None = None) -> str:
        return subprocess.run([str(probe), *args], check=True, capture_output=True, text=True, input=stdin).stdout

    axes = Check("selector_axes", "SpellInfo.cpp:83-133, 140-244 (SpellImplicitTargetInfo)")
    for rn in ("0", "0.5", "0.999999"):
        doc = json.loads(run("selectors", rn))
        rnd = S.f32(float(rn))
        for t in doc["targets"]:
            i = S.info(t["id"])
            got = [i.object, i.reference, i.category, i.check, i.direction, S.f32_hex(i.direction_angle(rnd)),
                   i.is_area, i.target_flag_mask,
                   [list(i.explicit_target_mask(bool(e["src_in"]), bool(e["dst_in"]))) for e in t["explicit"]]]
            want = [S.OBJECT[t["object"]], S.REFERENCE[t["reference"]], S.CATEGORY[t["category"]],
                    S.CHECK[t["check"]], S.DIRECTION[t["direction"]], t["angle"], t["is_area"], t["target_flag_mask"],
                    [[e["mask"], bool(e["src_out"]), bool(e["dst_out"])] for e in t["explicit"]]]
            axes.record(got == want, {"target": t["id"], "rand_norm": rn})
    eff = Check("effect_table", "SpellInfo.cpp:835-866, 959 (SpellEffectInfo::_data, GetMissingTargetMask)")
    for r in json.loads(run("effects"))["effects"]:
        got = (S.effect_implicit_target_type(r["id"]), S.effect_used_object(r["id"]),
               C.missing_target_mask(C.EffectSlots(0, r["id"], 0, 0)))
        eff.record(got == (r["implicit_target_type"], r["used_object"], r["missing_mask_no_targets"]), r["id"])

    gen = Check("explicit_mask_generated", "SpellInfo.cpp:4572 _InitializeExplicitTargetMask")
    rng = random.Random(SEED)
    lines, expected = [], []
    for sid in range(400):
        n = rng.randint(1, 4)
        effs = []
        for _ in range(n):
            e = rng.choice([0, 2, 3, 6, 10, 24, 28, 35, 41, 64, 77, 97, 138] + [rng.randrange(S.TOTAL_SPELL_EFFECTS)])
            a = rng.choice([0, 1, 6, 15, 16, 18, 21, 22, 25, 30, 45, 53, 87, 89, 116] + [rng.randrange(S.TOTAL_SPELL_TARGETS)])
            b = rng.choice([0, 0, 16, 30, 15, rng.randrange(S.TOTAL_SPELL_TARGETS)])
            effs.append((e, a, b, rng.choice([0, 0, 0x100000])))
        has_range = rng.random() < 0.8
        r0, r1 = rng.choice([0.0, 5.0, 40.0]), rng.choice([0.0, 30.0])
        targets = rng.choice([0, 0, 0x2, 0x40, 0x10, 0x8000])
        a13 = rng.choice([0, 0x8000])
        slots = [C.EffectSlots(i, *x) for i, x in enumerate(effs)]
        expected.append(C.explicit_target_mask(slots, max_range_negative=r0 if has_range else 0.0,
                                               max_range_positive=r1 if has_range else 0.0,
                                               targets=targets, attributes13=a13))
        parts = [str(sid), str(int(has_range)), repr(r0), repr(r1), str(targets), str(a13), str(n)]
        for x in effs:
            parts += [str(v) for v in x]
        lines.append(" ".join(parts))
    for o in (json.loads(ln) for ln in run("explicit", stdin="\n".join(lines) + "\n").splitlines()):
        gen.record("error" not in o and (o["explicit"], o["required"]) == expected[o["spell"]], o)

    real = Check("explicit_mask_player_spells", "SpellInfo.cpp:4572 on every current-player spell")
    if ctx is not None:
        spells = sorted(s for s in ctx.scope.reach if any(e.is_effect for e in ctx.data.effects(s)))
        out = run("explicit", stdin="\n".join(C.probe_input_line(ctx, s) for s in spells) + "\n")
        for o in (json.loads(ln) for ln in out.splitlines()):
            real.record("error" not in o and (o["explicit"], o["required"]) == C.explicit_mask_for(ctx, o["spell"]),
                        o)
    return _probe_json([axes, eff, gen, real], "tc_target_selector_probe")


# ---------------------------------------------------------------------------
# C: geometry probe (line protocol)
# ---------------------------------------------------------------------------
class GeomProbe:
    def __init__(self) -> None:
        exe = build("tc_target_geom_probe")
        self.p = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def ask(self, *parts) -> list[str]:
        line = " ".join(p if isinstance(p, str) else p.hex() if isinstance(p, float) else str(int(p)) for p in parts)
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        return self.p.stdout.readline().split()

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait(timeout=30)


def _coord(rng: random.Random, lim: float = 30.0) -> float:
    # quarter-yard grid plus occasional binary32 neighbours of grid values (boundary cases)
    v = round(rng.uniform(-lim, lim) * 4) / 4
    if rng.random() < 0.2 and v != 0.0:
        # adjacent binary32 magnitude (never crosses zero, never produces NaN/inf for |v| <= lim)
        bits = struct.unpack("<I", struct.pack("<f", abs(v)))[0] + rng.choice([-1, 1])
        v = struct.unpack("<f", struct.pack("<I", bits))[0] * (1.0 if v > 0 else -1.0)
    return f32(v)


def _pos(rng: random.Random, lim: float = 30.0) -> tuple[float, float, float]:
    return (_coord(rng, lim), _coord(rng, lim), f32(rng.choice([0.0, 0.0, 1.0, -2.5, _coord(rng, 10.0)])))


def geom_probe(n: int = 3000) -> dict[str, Any]:
    from . import caps
    from . import geometry as g
    from .area import fuzzy_eq32
    probe = GeomProbe()
    rng = random.Random(SEED + 1)
    h = float.fromhex
    checks = {k: Check(k, m) for k, m in (
        ("has_in_arc", "Position.cpp:173 HasInArc"), ("has_in_line", "Position.cpp:192 HasInLine"),
        ("distance_order", "Object.cpp:569 GetDistanceOrder"),
        ("boundary_radius", "Unit.cpp IsWithinBoundaryRadius"),
        ("area_check", "Spell.cpp:9418 WorldObjectSpellAreaTargetCheck"),
        ("cone_check", "Spell.cpp:9459 WorldObjectSpellConeTargetCheck"),
        ("line_check", "Spell.cpp:9497-9510 WorldObjectSpellLineTargetCheck"),
        ("nearby_last_searcher", "Spell.cpp:2194, 9402 SearchNearbyTarget"),
        ("list_sort_distance", "Object.h:643 + bits/list.tcc:488 list::sort"))}
    try:
        for _ in range(n):
            p, t = _pos(rng), _pos(rng)
            o = f32(rng.uniform(-7.0, 7.0))
            arc = f32(rng.choice([0.0, 1.0, 1.5707963, 3.1415927, 6.2831855, rng.uniform(-7, 7)]))
            r = probe.ask("A", *p, o, arc, *t)
            checks["has_in_arc"].record((r[1] == "1") == g.has_in_arc(p, g.normalize_orientation(o), arc, t),
                                        [p, o, arc, t])
            size, width = f32(rng.choice([0.0, 1.5, 3.0])), f32(rng.choice([0.0, 1.0, 2.5, 5.0]))
            r = probe.ask("L", *p, o, *t, size, width)
            checks["has_in_line"].record((r[1] == "1") == g.has_in_line(p, g.normalize_orientation(o), t, size, width),
                                         [p, o, t, size, width])
            q = _pos(rng)
            is3d = rng.random() < 0.5
            checks["distance_order"].record((probe.ask("O", *p, *t, *q, is3d)[1] == "1") == g.distance_order(p, t, q, is3d))
            br = f32(rng.choice([0.0, 0.389, 1.0, 2.5]))
            checks["boundary_radius"].record((probe.ask("W", *p, *t, br)[1] == "1") == g.is_within_boundary_radius(p, t, br))
            # area
            treach, immune, rel = f32(rng.choice([0.0, 1.0, 1.5])), rng.randint(0, 3), rng.random() < 0.8
            mn, mx = f32(rng.choice([0.0, 0.0, 3.0])), f32(rng.choice([5.0, 8.0, 10.0, 30.0]))
            canhit, reason = rng.random() < 0.3, rng.randint(0, 1)
            r = probe.ask("AREA", *t, treach, immune, rel, *p, mn, mx, canhit, reason)
            imm = {nm for bit, nm in ((1, "AoETarget"), (2, "ChainTarget")) if immune & bit}
            ok = g.area_unit_in_cylinder(t, treach, p, mn, mx)
            if ok and ((reason == 0 and not canhit and "AoETarget" in imm) or (reason == 1 and "ChainTarget" in imm)):
                ok = False
            checks["area_check"].record((r[1] == "1") == (ok and rel), [t, treach, immune, rel, p, mn, mx])
            # cone
            deg = f32(rng.choice([0.0, 45.0, 90.0, 180.0, 360.0, -90.0, rng.uniform(-400, 400)]))
            cwidth = f32(rng.choice([0.0, 0.0, 2.5]))
            cu = 4 if cwidth else (2 if rng.random() < 0.1 else 0)
            creach = f32(rng.choice([1.5, 0.5]))
            r = probe.ask("CONE", *p, o, creach, deg, cwidth, mn, mx, cu, *t, treach, br, 0, rel)
            con, rad = g.normalize_orientation(o), g.deg_to_rad(deg)
            if cu == 4:
                geo_ok = g.has_in_line(p, con, t, treach, cwidth)
            elif cu == 2:
                geo_ok = g.cone_back_ok(p, con, rad, t)
            else:
                geo_ok = g.cone_arc_ok(p, con, rad, t, g.is_within_boundary_radius(p, t, br))
            want = geo_ok and g.area_unit_in_cylinder(t, treach, p, mn, mx) and rel
            checks["cone_check"].record((r[1] == "1") == want, [p, o, deg, cwidth, cu, t])
            # line
            hasdst = rng.randint(0, 2)
            d, do = _pos(rng), f32(rng.uniform(-7.0, 7.0))
            if hasdst == 1 and rng.random() < 0.2:
                d, do = p, o
            r = probe.ask("LINE", *p, o, creach, hasdst, *d, do, width, mn, mx, *t, treach, 1)
            w = width if width else creach
            if hasdst == 1:
                same = all(fuzzy_eq32(a, b) for a, b in zip((*p, con), (*d, g.normalize_orientation(do))))
                lo = g.line_orientation(p, con, d, same)
            else:
                lo = con
            checks["line_check"].record(h(r[2]) == lo and (r[1] == "1") == g.has_in_line(p, lo, t, treach, w),
                                        [p, o, hasdst, d, do, width, t])
            # nearby
            k = rng.randint(1, 6)
            ts = [(_pos(rng, 15.0), f32(rng.choice([0.0, 1.5])), rng.random() < 0.7) for _ in range(k)]
            if rng.random() < 0.3 and k > 1:
                ts[1] = (ts[0][0], ts[0][1], ts[1][2])      # exact tie
            rr = f32(rng.choice([5.0, 10.0, 40.0]))
            args = [v for tp, rch, rl in ts for v in (*tp, rch, rl)]
            r = probe.ask("NEAR", rr, *p, creach, k, *args)
            best, found = rr, -1
            for i, (tp, rch, rl) in enumerate(ts):
                dd = g.nearby_distance(tp, rch, p)
                if dd < best and rl:
                    best, found = dd, i
            checks["nearby_last_searcher"].record(int(r[1]) == found, [rr, p, ts])
            # sort
            m = rng.randint(0, 8)
            pts = [_pos(rng, 10.0) for _ in range(m)]
            for i in range(min(rng.randint(0, 3), m)):
                pts.append(pts[i])
            asc = rng.random() < 0.5
            r = probe.ask("SORT", asc, *p, len(pts), *[c for pt in pts for c in pt])
            ours = caps.list_sort(list(range(len(pts))),
                                  lambda a, b, p=p, pts=pts, asc=asc: g.distance_order(p, pts[a], pts[b]) == asc)
            checks["list_sort_distance"].record([int(x) for x in r[1:]] == ours, [asc, p, pts])
    finally:
        probe.close()
    return _probe_json(list(checks.values()), "tc_target_geom_probe", {"seed": SEED + 1, "iterations": n})


# ---------------------------------------------------------------------------
# D: chain probe
# ---------------------------------------------------------------------------
def _chain_world(rng: random.Random) -> tuple[dict, dict]:
    units = []
    coords = [0.0, 1.0, 2.5, 5.0, 7.5, 9.0, 10.0, 11.0, 12.5, -3.0, -10.0, 0.1, 13.999999]
    base_facts = {"profile": "combat-sim", "spell_other_immunity": [], "in_caster_phase": True,
                  "range_movement_bonus": False, "level": 80, "ignore_los_on_me": False}

    def unit(i, pos, reach=1.5, hp=10, mhp=10, kind="player"):
        return {"id": i, "kind": kind, "pos": list(pos), "orientation": 0.0, "combat_reach": reach,
                "bounding_radius": 0.389, "alive": True, "health": hp, "max_health": mhp,
                "facts": dict(base_facts)}
    actors = [unit("c", (0.0, 0.0, 0.0), hp=100, mhp=100), unit("p", (4.0, 4.0, 0.0), hp=90, mhp=100)]
    k = rng.randint(1, 7)
    for i in range(k):
        actors.append(unit(f"u{i}", (rng.choice(coords), rng.choice(coords), rng.choice([0.0, 0.0, 1.0])),
                           reach=rng.choice([0.0, 0.5, 1.0, 1.5, 3.0]), hp=10 - rng.randint(0, 4)))
        units.append(f"u{i}")
    names = [a["id"] for a in actors]
    blocked = []
    for _ in range(rng.randint(0, 4)):
        a, b = rng.sample(names, 2)
        blocked.append([a, b])
    heal = rng.random() < 0.5
    cfc, melee = rng.random() < 0.25, rng.random() < 0.25
    attrs = (["SPELL_ATTR2_CHAIN_FROM_CASTER"] if cfc else []) + (["SPELL_ATTR5_MELEE_CHAIN_TARGETING"] if melee else [])
    jumps = rng.randint(1, 4)
    world = {
        "schema": "targeting-fixture/1", "name": "chain-diff", "caster": "c", "actors": actors,
        "relations": [{"from": "c", "to": x, "valid_attack": not heal, "valid_assist": heal} for x in names],
        "visit_order": ["p"] + units + ["c"], "los": {"default": "clear", "blocked": blocked},
        "rng": {"draws": []}, "spell_value": {"chain_from_caster_max_range": 30.0},
        "modifiers": {"chain_targets": 0, "chain_jump_distance": 0, "range": 0, "radius": 0},
        "spell": {"synthetic": True, "id": 900100, "dmg_class": rng.choice([0, 1, 2, 3]), "range": {"min": [0, 0], "max": [30, 30]},
                  "attributes": attrs, "attributes_cu": [], "script_hooks": [], "is_positive": heal,
                  "effects": [{"index": 0, "effect": "HEAL" if heal else "SCHOOL_DAMAGE",
                               "target_a": "TARGET_UNIT_TARGET_CHAINHEAL_ALLY" if heal else "TARGET_UNIT_TARGET_ENEMY",
                               "chain_targets": jumps + 1,
                               "attributes": rng.choice([0, 0x80, 0x10000])}]},
    }
    return world, {"heal": heal, "jumps": jumps}


def chain_probe(n: int = 400) -> dict[str, Any]:
    from . import chain as ch
    from . import cmd_d
    from .fixture import World
    from .oracle import from_fixture
    from .trace import Trace
    build("tc_target_chain_probe")
    rng = random.Random(SEED + 2)
    jumps_check = Check("search_chain_targets", "Spell.cpp:2221-2327 SearchChainTargets")
    radius_check = Check("chain_search_radius", "Spell.cpp:2250-2259")
    cases, meta = [], []
    for _ in range(n):
        data, info = _chain_world(rng)
        w = World.from_dict(data)
        sv = from_fixture(w.spell)
        eff = sv.effect(0)
        tr = Trace()
        sel = 45 if info["heal"] else 6
        try:
            got = ch.search_chain_targets(w, sv, eff, sel, "p", info["jumps"], info["heal"], tr)
        except FailClosed:
            jumps_check.fail_closed += 1
            continue
        cand = next(s for s in tr.stages if s.name == "area.search").output
        radius = next(s for s in tr.stages if s.name == "chain.search_radius").output
        case, idx = cmd_d.chain_case(w, sv, eff, "p", info["jumps"], info["heal"], cand)
        cases.append(case)
        meta.append((got, radius, idx, data))
    for (got, radius, idx, data), res in zip(meta, cmd_d.run_probe(cases)):
        inv = {v: k for k, v in idx.items()}
        ok = "error" not in res and [inv[i] for i in res["targets"]] == got
        jumps_check.record(ok, {"python": got, "probe": res, "world": data["name"]})
        radius_check.record("error" not in res and float.fromhex(res["search_radius"]) == radius, res)
    return _probe_json([jumps_check, radius_check], "tc_target_chain_probe",
                       {"seed": SEED + 2, "worlds": n,
                        "note": "probe stub SearchAreaTargets returns the Python pre-filter list (area predicate "
                                "is verified by the geometry probe)"})


# ---------------------------------------------------------------------------
# B: relation probe (track B's generator)
# ---------------------------------------------------------------------------
def relation_probe(ctx, worlds: int = 200) -> dict[str, Any]:
    from . import cmd_b
    build("tc_target_relation_probe")
    res = cmd_b.differential(ctx.bundle.source, worlds, SEED + 3)
    st = res["stats"]
    c = Check("reaction_attack_assist", "Object.cpp:2035-2193, 2331, 2489; Unit.cpp:12184-12218")
    c.cases = st["fields_compared"]
    c.failed = list(res["mismatches"])[: st["mismatches"]] + [None] * max(0, st["mismatches"] - len(res["mismatches"]))
    c.fail_closed = st["fail_closed"]
    out = _probe_json([c], "tc_target_relation_probe", {"seed": SEED + 3, "worlds": worlds,
                                                         "queries": st["queries"]})
    out["checks"]["reaction_attack_assist"]["first_failures"] = res["mismatches"][:3]
    return out


# ---------------------------------------------------------------------------
# G: load-time positivity probe
# ---------------------------------------------------------------------------
class _ValueLoader:
    """positivity.Loader whose CalcValue is the driver value stored in ``base_points``."""

    def __new__(cls, spells: dict):
        from .positivity import Loader

        class L(Loader):
            def calc_value(self, sp, e):
                return e.base_points
        return L(lambda sid: spells.get(sid))


def _pos_lines(spells: dict, ids: list[int]) -> list[str]:
    from .selectors import CHECK
    lines = []
    for sid in ids:
        sp = spells[sid]
        a = sp.attributes
        seed = sum(1 << i for i in sp.seed)
        lines.append(f"S {sid} {sp.family} {sp.family_flags0} {sp.mechanic} {a[0]} {a[1]} {a[4]} {seed} {len(sp.effects)}")
        for e in sp.effects:
            lines.append(" ".join(str(x) for x in (
                "E", e.index, e.effect, e.aura, e.target_a, CHECK.index(e.check_a), e.target_b, CHECK.index(e.check_b),
                e.attributes, float(f32(e.real_points_per_level)).hex(), e.misc0, e.trigger, float(e.base_points).hex())))
    return lines


def _random_pos_world(rng: random.Random) -> dict:
    from procs.enums import attr, aura, effect

    from . import positivity as P
    n = rng.randint(1, 4)
    ids = rng.sample([900500 + i for i in range(10)] + [32645, 40268, 24732], n)
    effects_pool = [effect(x) for x in ("APPLY_AURA", "SCHOOL_DAMAGE", "HEAL", "DUMMY", "TRIGGER_SPELL", "DISPEL",
                                        "THREAT", "KNOCK_BACK", "ENERGIZE", "INSTAKILL", "PERSISTENT_AREA_AURA",
                                        "DISPEL_MECHANIC", "APPLY_AREA_AURA_PARTY")]
    aura_pool = [0] + sorted(set().union(*[P.AURA_NEG_IF_BP_OR_LEVEL_NEG, P.AURA_NEG_IF_TARGET_OR_BP_NEG,
                                           P.AURA_NEG_IF_BP_POS, P.AURA_PCT_TAKEN, P.AURA_REGEN_PCT,
                                           P.AURA_ADD_TARGET_TRIGGER, P.AURA_PERIODIC_TRIGGER_VALUE,
                                           P.AURA_CHECK_TARGET, P.AURA_NEGATIVE, P.AURA_MECHANIC_IMMUNITY,
                                           P.AURA_SPELLMOD, P.OTHER_AURA_POSITIVE, P.OTHER_AURA_NEGATIVE]))
    checks = ["DEFAULT", "ENEMY", "ALLY", "ENTRY", "PARTY"]
    spells = {}
    for sid in ids:
        effs = []
        for i in range(rng.randint(1, 4)):
            if rng.random() < 0.1:
                effs.append(P.PEffect(index=i))
                continue
            eff = rng.choice(effects_pool)
            au = rng.choice(aura_pool) if eff in (effect("APPLY_AURA"), effect("PERSISTENT_AREA_AURA"),
                                                  effect("APPLY_AREA_AURA_PARTY")) else rng.choice([0, 0, 0, aura("DUMMY")])
            effs.append(P.PEffect(
                index=i, effect=eff, aura=au, target_a=rng.choice([1, 6, 21, 16, 30]),
                target_b=rng.choice([0, 0, 16]), check_a=rng.choice(checks), check_b=rng.choice(["DEFAULT", "ENEMY"]),
                attributes=rng.choice([0, 0, 0, 0x1000]), real_points_per_level=rng.choice([0.0, 0.0, -1.0, 1.0]),
                misc0=rng.choice([0, 1, 5, 9, 10, 11, 16, 17, 19, 25, 27, 3, 8]),
                trigger=rng.choice([0, 0] + ids), base_points=rng.choice([0.0, 5.0, -5.0, 100.0, -1.0])))
        words = [0] * 17
        for nm in ("SPELL_ATTR0_PASSIVE", "SPELL_ATTR0_AURA_IS_DEBUFF", "SPELL_ATTR1_AURA_UNIQUE",
                   "SPELL_ATTR4_AURA_IS_BUFF"):
            if rng.random() < 0.08:
                w, bit = attr(nm)
                words[w] |= bit
        spells[sid] = P.PSpell(id=sid, attributes=tuple(words), family=rng.choice([0, 0, 4, 8, 3]),
                               family_flags0=rng.choice([0, 0x20000000, 0x00200000, 1]),
                               mechanic=rng.choice([0, 0, 29, 5]), effects=tuple(effs),
                               seed=frozenset({rng.randrange(len(effs))}) if rng.random() < 0.15 else frozenset())
    return spells


def positivity_probe(ctx=None, worlds: int = 600, real_limit: int | None = None) -> dict[str, Any]:
    import itertools
    from dataclasses import replace

    from . import positivity as P
    exe = build("tc_target_positivity_probe")
    rng = random.Random(SEED + 4)
    gen = Check("positivity_generated", "SpellInfo.cpp:4609-5099 under every/sampled load order")
    order_dep = {"fail_closed_worlds": 0, "fail_closed_confirmed_order_dependent": 0,
                 "fail_closed_order_independent_in_sample": 0}
    blocks, meta = [], []
    for _ in range(worlds):
        spells = _random_pos_world(rng)
        ids = sorted(spells)
        orders = [list(o) for o in itertools.permutations(ids)] if len(ids) <= 4 else \
            [rng.sample(ids, len(ids)) for _ in range(12)]
        text = _pos_lines(spells, ids)
        for o in orders:
            text += ["O " + " ".join(map(str, o)), "G"]
        text.append("X")
        blocks.append("\n".join(text))
        meta.append((spells, ids, len(orders)))
    out = subprocess.run([str(exe)], input="\n".join(blocks) + "\n", capture_output=True, text=True,
                         check=True).stdout.splitlines()
    pos = 0
    for spells, ids, k in meta:
        runs = [json.loads(x)["neg"] for x in out[pos:pos + k]]
        pos += k
        loader = _ValueLoader(spells)
        for sid in ids:
            seen = {r[str(sid)] for r in runs}
            try:
                mine = sum(1 << i for i in loader.negative_effects(sid))
            except FailClosed as exc:
                if "load-order" not in str(exc):
                    gen.fail_closed += 1
                    continue
                order_dep["fail_closed_worlds"] += 1
                if len(seen) > 1:
                    order_dep["fail_closed_confirmed_order_dependent"] += 1
                else:
                    order_dep["fail_closed_order_independent_in_sample"] += 1
                continue
            gen.record(seen == {mine}, {"spell": sid, "python": mine, "probe": sorted(seen),
                                        "world": {str(k): repr(v) for k, v in spells.items()}})
    order_real: dict[str, Any] = {}
    real = Check("positivity_player_spells", "SpellInfo.cpp:4609-5099 on current-player spells + trigger closure "
                                             "(orders: spell first / spell last / 4 random)")
    if ctx is not None:
        loader = P.data_loader()
        spells_all = sorted(ctx.scope.reach)
        if real_limit is not None:
            spells_all = spells_all[:real_limit]
        blocks, meta = [], []
        order_dependent_real: list[int] = []
        for sid in spells_all:
            closure, frontier = {}, [sid]
            try:
                while frontier and len(closure) < 40:
                    x = frontier.pop()
                    if x in closure:
                        continue
                    sp = loader.get(x)
                    if sp is None:
                        continue
                    vals = []
                    for e in sp.effects:
                        try:
                            v = loader.calc_value(sp, e) if e.is_effect else 0.0
                        except FailClosed:
                            v = None
                        vals.append(v)
                    closure[x] = (sp, vals)
                    frontier += [e.trigger for e in sp.effects if e.trigger]
                mine = sum(1 << i for i in loader.negative_effects(sid))
            except FailClosed as exc:
                real.fail_closed += 1
                if "load-order" in str(exc):
                    order_dependent_real.append(sid)
                continue
            if any(v is None for _, vals in closure.values() for v in vals):
                real.fail_closed += 1
                continue
            world = {x: replace(sp, effects=tuple(replace(e, base_points=v) for e, v in zip(sp.effects, vals)))
                     for x, (sp, vals) in closure.items()}
            ids = sorted(world)
            others = [x for x in ids if x != sid]
            orders = [[sid] + others, others + [sid]] + [rng.sample(ids, len(ids)) for _ in range(4)]
            text = _pos_lines(world, ids)
            for o in orders:
                text += ["O " + " ".join(map(str, o)), "G"]
            text.append("X")
            blocks.append("\n".join(text))
            meta.append((sid, mine, len(orders)))
        out = subprocess.run([str(exe)], input="\n".join(blocks) + "\n", capture_output=True, text=True,
                             check=True).stdout.splitlines()
        pos = 0
        for sid, mine, k in meta:
            seen = {json.loads(x)["neg"][str(sid)] for x in out[pos:pos + k]}
            pos += k
            real.record(seen == {mine}, {"spell": sid, "python": mine, "probe": sorted(seen)})
        # the real spells Python refuses: show that the compiled code really gives several answers
        for sid in order_dependent_real:
            closure, frontier = {}, [sid]
            while frontier:
                x = frontier.pop()
                if x in closure or loader.get(x) is None:
                    continue
                sp = loader.get(x)
                vals = [loader.calc_value(sp, e) if e.is_effect else 0.0 for e in sp.effects]
                closure[x] = replace(sp, effects=tuple(replace(e, base_points=v) for e, v in zip(sp.effects, vals)))
                frontier += [e.trigger for e in sp.effects if e.trigger]
            ids = sorted(closure)
            others = [x for x in ids if x != sid]
            orders = [[sid] + others, others + [sid]] + [rng.sample(ids, len(ids)) for _ in range(10)]
            text = _pos_lines(closure, ids)
            for o in orders:
                text += ["O " + " ".join(map(str, o)), "G"]
            res = subprocess.run([str(exe)], input="\n".join(text) + "\n", capture_output=True, text=True,
                                 check=True).stdout.splitlines()
            masks = sorted({json.loads(x)["neg"][str(sid)] for x in res})
            order_real[str(sid)] = {"closure": ids, "negative_effect_masks_seen": masks,
                                    "confirmed_order_dependent": len(masks) > 1}
    return _probe_json([gen, real], "tc_target_positivity_probe",
                       {"seed": SEED + 4, "worlds": worlds, "load_order": order_dep,
                        "load_order_player_spells": order_real,
                        "note": "CalcValue() is a cut point: the probe receives the Python load-time value; "
                                "its sign logic (SpellInfo.cpp:521-752) is consumer-read"})


# ---------------------------------------------------------------------------
# I: AreaTrigger shape probe
# ---------------------------------------------------------------------------
class _LineProbe(GeomProbe):
    def __init__(self, name: str) -> None:
        exe = build(name)
        self.p = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)


def areatrigger_probe(n: int = 400) -> dict[str, Any]:
    from . import areatriggers as at
    from . import geometry as g
    probe = _LineProbe("tc_target_at_probe")
    rng = random.Random(SEED + 5)

    def fv(lo: float, hi: float) -> float:
        return f32(round(rng.uniform(lo, hi) * 8) / 8 if rng.random() < 0.7 else rng.uniform(lo, hi))

    shapes = {0: 2, 1: 6, 3: 2, 4: 6, 5: 8, 6: 4}
    checks = {k: Check(f"shape_{k}", "AreaTrigger.cpp:717-852 SearchUnitIn* + AreaTriggerShapeInfo::GetMaxSearchRadius")
              for k in shapes}
    try:
        for _ in range(n):
            for shape_type, width in shapes.items():
                if shape_type == 3:
                    raw = [fv(-3, 10), fv(-3, 10)] + [0.0] * 6
                elif shape_type == 4:
                    raw = [fv(0, 20) for _ in range(4)] + [fv(-2, 2), fv(-2, 2), 0.0, 0.0]
                else:
                    raw = [fv(0, 20) for _ in range(width)] + [0.0] * (8 - width)
                verts, tverts = [], []
                if shape_type == 3:
                    verts = [(fv(-15, 15), fv(-15, 15)) for _ in range(rng.randint(3, 6))]
                    if rng.random() < 0.5:
                        tverts = [(fv(-15, 15), fv(-15, 15)) for _ in verts]
                progress, scale = fv(0, 1), fv(0.1, 3.0)
                flags = rng.choice([0, 1]) if shape_type in (4, 5) else 0
                pose = (fv(-30, 30), fv(-30, 30), fv(-30, 30), fv(-7, 7))
                units = [(fv(-30, 30), fv(-30, 30), fv(-15, 15), fv(0, 3), rng.random() < 0.8)
                         for _ in range(rng.randint(0, 8))]
                args = ["S", shape_type, *map(float, raw), len(verts)]
                for v in verts:
                    args += [float(v[0]), float(v[1])]
                args.append(len(tverts))
                for v in tverts:
                    args += [float(v[0]), float(v[1])]
                args += [float(progress), float(scale), flags, *map(float, pose), len(units)]
                for u in units:
                    args += [float(u[0]), float(u[1]), float(u[2]), float(u[3]), int(u[4])]
                r = probe.ask(*args)
                want = (float.fromhex(r[1]), [int(x) for x in r[2:]]) if r and r[0] == "S" else None
                shape = at.shape_from_row(shape_type, raw, verts, tverts)
                pos, o = g.vec(pose[:3]), g.f32(pose[3])
                bounds = at.max_search_radius(shape)
                state = at.evaluate_shape(shape, pos, o, g.f32(progress), g.f32(scale), bounds_radius_2d=bounds,
                                          field_flags=flags)
                kept = [i for i, u in enumerate(units) if at.unit_in_shape(state, pos, o, u[:3], u[3])]
                got = (g.mul(bounds, g.f32(scale)), kept)
                checks[shape_type].record(got == want, {"args": [str(a) for a in args[:12]], "python": got,
                                                        "probe": want})
    finally:
        probe.close()
    return _probe_json(list(checks.values()), "tc_target_at_probe", {"seed": SEED + 5, "iterations": n})


# ---------------------------------------------------------------------------
# G: pipeline stage inputs vs probes
# ---------------------------------------------------------------------------
def pipeline_integration() -> dict[str, Any]:
    from . import cmd_d
    from . import geometry as g
    from .cmd_g import library
    from .fixture import World
    from .pipeline import Evaluation
    probe = GeomProbe()
    area = Check("pipeline_area_candidates", "Spell.cpp:2207 + 9418 on the pipeline's own search inputs")
    chains = Check("pipeline_chain_jumps", "Spell.cpp:2221 on the pipeline's own chain inputs")
    evaluated = []
    try:
        for path in library():
            data = json.loads(path.read_text(encoding="utf-8"))
            if (data.get("expect") or {}).get("fail_closed"):
                continue
            w = World.from_dict(data)
            ev = Evaluation(w)
            try:
                ev.run()
            except FailClosed:
                continue
            evaluated.append(path.name)
            for st in ev.t.stages:
                if st.name != "area.search":
                    continue
                inp = st.inputs
                center = inp["center"]
                rmin, rmax = inp["radius"]
                reason = 0 if inp["reason"] == "Area" else 1
                canhit = ev.sv.has_attr("SPELL_ATTR8_CAN_HIT_AOE_UNTARGETABLE")
                for cand in w.enumeration():
                    a = w.actor(cand)
                    if a.kind not in ("player", "creature", "pet", "guardian", "totem", "minion", "vehicle"):
                        continue
                    imm = set(a.facts.get("spell_other_immunity", []))
                    immune = (1 if "AoETarget" in imm else 0) | (2 if "ChainTarget" in imm else 0)
                    tpos = g.vec(a.need("pos"))
                    reach = g.f32(float(a.need("combat_reach")))
                    r = probe.ask("AREA", *tpos, reach, immune, 1, *map(float, center), g.f32(rmin), g.f32(rmax),
                                  canhit, reason)
                    geometric = r[1] == "1"
                    # the pipeline's output = geometry AND relation/CheckTarget; a candidate kept by the
                    # pipeline must pass the probe's geometric check, and a geometric reject must be absent
                    in_out = cand in st.output
                    area.record(not in_out or geometric, {"fixture": path.name, "candidate": cand})
            stages = ev.t.stages
            for i, st in enumerate(stages):
                if st.name != "chain.call":
                    continue
                # the next chain stages belong to this call (select_implicit_chain_targets traces in order)
                nxt = next((j for j in range(i + 1, len(stages)) if stages[j].name == "chain.call"), len(stages))
                block = stages[i + 1:nxt]
                cand = [s for s in block if s.name == "area.search" and s.inputs.get("reason") == "Chain"]
                if not cand:
                    continue
                eff = ev.sv.effect(st.inputs["effect"])
                jumps = next(s for s in block if s.name == "chain.max_targets").output - 1
                heal = st.inputs["selector"] == 45
                view = ev.view()
                case, idx = cmd_d.chain_case(view, ev.sv, eff, st.inputs["initial"], jumps, heal, cand[0].output)
                res = cmd_d.run_probe([case])[0]
                inv = {v: k for k, v in idx.items()}
                jump_st = [s.output for s in block if s.name == "chain.jump"]
                chains.record("error" not in res and [inv[i] for i in res["targets"]] == jump_st,
                              {"fixture": path.name, "probe": res, "python": jump_st})
    finally:
        probe.close()
    return _probe_json([area, chains], "(pipeline)", {"fixtures_evaluated": evaluated})


# ---------------------------------------------------------------------------
TRINITY_DEFECTS = [
    {"id": "TG-G-D01", "file_line": "src/server/game/Spells/Spell.cpp:772-775 + SpellInfo.cpp:822-827",
     "description": "effect-mask grouping compares CalcRadius(m_caster, A/B) of the lead and each candidate; for "
                    "TARGET_DEST_{CASTER,TARGET,DEST}_RANDOM the radius is (Max-Min)*sqrt(rand_norm()), so the "
                    "comparison consumes RNG and compares two random values",
     "effect_on_recipients": "effects that should share one selection are split (independent searches / caps) "
                             "and extra rand_norm draws shift later RNG; 12 DIFFICULTY_NONE effects in the 12.1 "
                             "snapshot (none in current-player reach), e.g. 194687:0-2, 288507:0",
     "oracle_behaviour": "reproduced: pipeline.group_mask calls oracle.calc_radius lazily in consumer order "
                         "(lead A, other A, then B), consuming fixture rand_norm draws"},
    {"id": "TG-G-D02", "file_line": "src/server/game/Conditions/DisableMgr.cpp:360-363",
     "description": "IsDisabledFor(DISABLE_TYPE_SPELL, id, nullptr, SPELL_DISABLE_LOS) returns true for any row with "
                    "SPELL_DISABLE_DEPRECATED_SPELL even without the LOS bit",
     "effect_on_recipients": "deprecated-flagged spells skip every LOS test in CheckEffectTarget (Spell.cpp:8194) "
                             "and Spell::IsWithinLOS (9261)",
     "oracle_behaviour": "reproduced in oracle._los_disabled (needs the world `disables` table)"},
]

UNKNOWNS = [
    {"id": "TG-G-01", "subject": "SpellInfo::IsPositive for 5 current-player spells", "evidence": "trinity-probe",
     "known": "targeting.positivity ports SpellInfo.cpp:4609-5099; probe-equal for 4831/4836 current-player spells "
              "under several load orders; load order is unspecified (boost hashed container, SpellMgr.cpp:48-64, 3031)",
     "unknown": "IsPositive of 44614 Flurry, 188499 Blade Dance, 190411 Whirlwind, 385059 Odyn's Fury (result depends "
                "on whether triggered spells were initialised first) and 111400 Burning Rush (AttributesEx4 correction "
                "not applied)",
     "why_unresolved": "Trinity itself is order dependent for the four; the correction port is outside G",
     "reopen_condition": "fixture states `override.positive`, or a Trinity build fixes the iteration order",
     "build_skew": False},
    {"id": "TG-G-02", "subject": "DisableMgr SPELL_DISABLE_LOS rows", "evidence": "world-db-fact",
     "known": "TDB `disables` has 5318 rows, 54 spell rows (sourceType 0)",
     "unknown": "table not in dummy-corpora/trinity-server-overlay.json -> SpellView.los_disabled is None for DB2 views",
     "why_unresolved": "overlay owned by the lead", "reopen_condition": "overlay gains `disables`", "build_skew": "n/a"},
    {"id": "TG-G-03", "subject": "SpellHitResult RNG consumption during selection (Spell.cpp:2486)",
     "evidence": "trinity-consumer", "known": "each *new* unique target rolls hit/resist at AddUnitTarget time, "
     "interleaved with later selectors' RandomResize / rand_norm draws",
     "unknown": "the number of engine draws per target (depends on hit tables, auras, target type)",
     "why_unresolved": "hit machinery out of scope (payload/mitigation); fixtures state `hit.draws`",
     "reopen_condition": "a SpellHitResult draw-plan oracle (track D rng.hit_draw_plan) is wired into fixtures",
     "build_skew": "n/a"},
    {"id": "TG-G-04", "subject": "map candidate enumeration order", "evidence": "trinity-consumer",
     "known": "Spell::SearchTargets visits world then grid containers per cell (Spell.cpp:2163-2192)",
     "unknown": "per-cell object order is runtime insertion history (GridRefManager push_front)",
     "why_unresolved": "no grid model by design", "reopen_condition": "never (fixture-stated `visit_order`)",
     "build_skew": "n/a"},
    {"id": "TG-G-05", "subject": "world-geometry dependent destinations", "evidence": "trinity-consumer",
     "known": "MovePositionToFirstCollision / GetMapHeight / spell_target_position feed dest selectors "
              "(Spell.cpp:1465-1740, 9283-9290)", "unknown": "the resolved positions",
     "why_unresolved": "VMAP/MMAP not modelled", "reopen_condition": "never (fixture-stated `dest_positions`)",
     "build_skew": "n/a"},
]

RETAIL_EXPERIMENTS = [
    {"id": "RE-G-01", "question": "Do two effects of one spell with identical selectors hit the same random subset?",
     "model_a": "Trinity: one grouped selection, one RandomResize for the group (Spell.cpp:741-788)",
     "model_b": "independent per-effect selection",
     "setup": "capped area spell with two recipient effects (e.g. damage + debuff) on > cap enemies, repeated casts",
     "observable": "combat log: SPELL_DAMAGE and SPELL_AURA_APPLIED destination sets per cast",
     "fidelity": "exact"},
    {"id": "RE-G-02", "question": "Is REQUIRE_ALL_TARGETS evaluated per selection turn?",
     "model_a": "Trinity: fails when the current turn's mask found nobody even if an earlier effect found a target",
     "model_b": "fails only when the whole spell found nobody",
     "setup": "spell with ATTR1_REQUIRE_ALL_TARGETS whose later effect's area is empty", "observable": "cast error / cast success event",
     "fidelity": "approximate"},
    {"id": "RE-G-03", "question": "Does each effect keep the destination that existed after its own turn?",
     "model_a": "Trinity: m_destTargets[eff] snapshot after each effect (Spell.cpp:799-800)",
     "model_b": "every effect uses the final destination",
     "setup": "spell whose later effect moves the destination (dest_dest offset)", "observable": "ground effect / summon positions",
     "fidelity": "approximate"},
]


def run(build_probes: bool = True) -> dict[str, Any]:
    from . import context
    out: dict[str, Any] = {
        "provenance": {"pins": PINS, "command": "python3 targeting.py differential --out "
                                                "../../docs/research/targeting-corpora/differential.json",
                       "seed": SEED, "probes": [f"tools/{p}" for p in PROBES],
                       "toolchain": "g++ -std=c++20 -O0 -ffp-contract=off (probe Makefiles)"},
        "integration_boundary": BOUNDARY,
        "trinity_defects": TRINITY_DEFECTS,
        "unknowns": UNKNOWNS,
        "retail_experiments": RETAIL_EXPERIMENTS,
        "evidence": "trinity-probe (per-check) / differential (summary)",
    }
    if not available():
        out["probes"] = {p: {"status": "skipped", "reason": "sibling TrinityCore or g++ absent"} for p in PROBES}
        out["summary"] = {"cases": 0, "failed": 0, "skipped": len(PROBES)}
        return out
    ctx = context.get()
    probes = {
        "A_selector": selector_probe(ctx),
        "B_relation": relation_probe(ctx),
        "C_geometry": geom_probe(),
        "D_chain": chain_probe(),
        "G_positivity": positivity_probe(ctx, worlds=3000),
        "I_areatrigger": areatrigger_probe(),
        "G_pipeline": pipeline_integration(),
    }
    out["probes"] = probes
    cases = sum(c["cases"] for p in probes.values() for c in p["checks"].values())
    failed = sum(c["failed"] for p in probes.values() for c in p["checks"].values())
    fc = sum(c["fail_closed"] for p in probes.values() for c in p["checks"].values())
    out["summary"] = {"cases": cases, "failed": failed, "fail_closed": fc,
                      "per_probe": {k: {"cases": sum(c["cases"] for c in p["checks"].values()),
                                        "failed": sum(c["failed"] for c in p["checks"].values())}
                                    for k, p in probes.items()}}
    return out
