"""Track J: differential of ``targeting.auratargets`` against verbatim Trinity code (tools/tc_target_auramap_probe).

The probe compiles Trinity's own ``Aura::UpdateTargetMap``, ``UnitAura::FillTargetMap``,
``DynObjAura::FillTargetMap``, ``CanBeAppliedOn``, ``BuildEffectMaskForOwner``, ``AddStaticApplication``,
``SpellEffectInfo::CalcRadius``, ``Spell::GetSearcherTypeMask``, ``WorldObjectSpellAreaTargetCheck`` and
``WorldObject::IsInRange2d/3d``.  The relation part of ``WorldObjectSpellTargetCheck`` is a cut point: the
driver feeds the Python value of ``area.target_check`` for every (candidate, check type), so the differential
covers the per-type switch, owner/caster/referer identity, radius, geometry, container masks, phases,
static applications and the update filters -- not Track B's relation predicates.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_tg_j_auramap import PARTY, RAID0, RAID1, Scene, dyn_scene, effect, raid_scene

from targeting import TC_ROOT, FailClosed, groups
from targeting import area as A
from targeting import auratargets as J
from targeting.oracle import for_world
from targeting.selectors import info

PROBE_DIR = Path(__file__).resolve().parents[1] / "tools" / "tc_target_auramap_probe"
PROBE = PROBE_DIR / "probe"
CHECKS = ("DEFAULT", "ENTRY", "ENEMY", "ALLY", "PARTY", "RAID", "RAID_CLASS", "PASSENGER", "SUMMONED")
REFS = ("NONE", "CASTER", "TARGET", "LAST", "SRC", "DEST")


@pytest.fixture(scope="module")
def probe():
    if not (TC_ROOT / "src/server/game/Spells/Auras/SpellAuras.cpp").exists():
        pytest.skip("sibling TrinityCore checkout absent")
    if shutil.which("g++") is None and not PROBE.exists():
        pytest.skip("no compiler")
    subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True, capture_output=True)
    return PROBE


def _b(v) -> str:
    return "1" if v else "0"


def _f(v: float) -> str:
    return float(v).hex()


def encode(world, sv, commands: list[str]) -> str:
    """Serialise a fixture into the probe protocol (see probe.cpp)."""
    owner = J._aura_key(world, "owner")
    kind = J.aura_type(world, owner)
    caster = J.aura_caster(world)
    ref = caster if caster is not None else owner
    words = " ".join(str(sv.attributes[i]) for i in (3, 5, 7, 8, 9))
    lines = ["reset", f"spell {sv.id} {words} 0"]
    for e in sv.effects:
        ra = e.radius_a
        a, b = info(e.target_a), info(e.target_b)
        lines.append(" ".join(map(str, [
            "effect", e.index, e.effect, _b(ra is not None), _f(ra.radius if ra else 0), _f(ra.per_level if ra else 0),
            _f(ra.min if ra else 0), _f(ra.max if ra else 0), e.target_a, CHECKS.index(a.check), REFS.index(a.reference),
            CHECKS.index(b.check), REFS.index(b.reference), _b(e.has_attribute("PlayersOnly"))])))
    units = [a for a in world.actors.values() if a.kind in A.UNIT_KINDS]
    for u in units:
        master = groups.charmer_or_owner(world, u.id)
        pet = u.facts.get("pet")
        same_phase = world.relations.get((u.id, master), {}).get("in_same_phase", True) if master else True
        phase = 0
        if kind == "dynobj":
            phase = 0 if world.relation(owner, u.id, "in_same_phase") else 1
        raw = J._raw_actor(world, u.id)
        foreign = [x for x in raw.get("auras", []) if x.get("owner") != owner or x.get("spell") != sv.id]
        stack = -1
        highest = True
        if foreign:
            highest = bool(u.facts["highest_exclusive"])
            try:
                stack = int(J.can_stack_on(world, sv, owner, u.id))
            except FailClosed:
                stack = 1           # unreachable for this scene (the oracle would raise if it were reached)
        lines.append(" ".join(map(str, [
            "unit", u.id, "P" if u.kind == "player" else "C", *map(_f, u.pos), _f(u.combat_reach), _b(u.alive),
            _b(u.facts["in_world"]), 0, _b(u.facts["banished"]), int(u.facts.get("level", 1)),
            _b(u.facts["range_movement_bonus"]), master or "-", pet or "-", _b(u.facts.get("in_owner_map", True)),
            _b(same_phase), phase, _b(u.facts.get("in_flight", False)), _b(u.facts["aura_immune"]),
            _b(u.facts["aura_immune_existing"]), J._mask(u.facts["aura_immune_effects"]), stack, _b(highest),
            _b(u.facts["suppressed_by_label"]), _b("AoETarget" in u.facts["spell_other_immunity"])])))
    for u in units:
        for ci, check in enumerate(CHECKS):
            if check not in ("ENEMY", "ALLY", "PARTY", "RAID", "SUMMONED"):
                continue
            chk_caster = world.actor(owner).fact("caster") if kind == "dynobj" else ref
            referer = chk_caster if kind == "dynobj" else owner
            try:
                ok = A.target_check(world, sv, chk_caster, u.id, J._Sel(check), referer, None)
            except FailClosed:
                continue            # never evaluated by the oracle for this scene (it would raise); probe default rejects
            lines.append(f"rel {u.id} {ci} {_b(ok)}")
    order = [x for x in world.enumeration() if world.actor(x).kind in A.UNIT_KINDS]
    lines.append(" ".join(["visit", str(len(order)), *order]))
    if kind == "dynobj":
        d = world.actor(owner)
        lines.append(" ".join(["aura", "dyn", owner, caster or "-", *map(_f, d.pos), _f(d.fact("radius")), "0"]))
    else:
        lines.append(" ".join(["aura", "unit", owner, caster or "-"]))
        for uid, idx in J._aura_key(world, "static_applications").items():
            lines.append(f"static {uid} {J._mask(idx)}")
    for uid, idx in J._aura_key(world, "applications").items():
        lines.append(f"app {uid} {J._mask(idx)}")
    return "\n".join(lines + commands) + "\n"


def run(probe_path, scene_or_world, commands):
    world = scene_or_world.world() if isinstance(scene_or_world, Scene) else scene_or_world
    sv = for_world(world)
    out = subprocess.run([str(probe_path)], input=encode(world, sv, commands), capture_output=True, text=True, check=True)
    return world, sv, [json.loads(line) for line in out.stdout.splitlines()]


def scenarios() -> dict[str, Scene]:
    out: dict[str, Scene] = {}
    for eff in (35, 65, 128, 129, 202, 271):
        s = raid_scene(eff)
        s.unit("e1", (3, 0, 0), kind="creature").foes("p1", "e1")
        s.unit("w1", (8, 0, 0), kind="guardian", owner="p1", summoner="p1").friends("p1", "w1")
        out[f"type{eff}"] = s
    s = raid_scene()
    s.doc["spell"]["effects"][0]["attributes"] = ["PlayersOnly"]
    out["players-only"] = s
    s = Scene([effect(0, 65, 40.0)], attributes=["SPELL_ATTR5_NOT_ON_PLAYER"])
    s.unit("p1", (0, 0, 0), group=RAID0).unit("pet", (2, 0, 0), kind="pet", owner="p1").friends("p1", "pet")
    s.aura("p1", "p1")
    out["not-on-player"] = s
    for x in (41.49, 41.5):
        s = Scene([effect(0, 65, 40.0)])
        s.unit("p1", (0, 0, 0), group=RAID0).unit("p2", (x, 0, 0), group=RAID1).unit("p3", (0, 0, 40.01), group=RAID0)
        s.friends("p1", "p2", "p3").aura("p1", "p1")
        out[f"edge{x}"] = s
    s = Scene([effect(0, 35, None)])
    s.unit("p1", (0, 0, 0), group=PARTY).unit("p2", (1.0, 0, 0), group=PARTY).unit("p3", (1.0, 0, 0.001), group=PARTY)
    s.friends("p1", "p2", "p3").aura("p1", "p1")
    out["radius0"] = s
    s = Scene([effect(0, 35, 10.0, per_level=0.5, rmin=0.0)])
    s.doc["spell"]["effects"][0]["radius_a"] = {"radius": 2.0, "per_level": 0.25, "min": 0.0, "max": 12.0}
    s.unit("p1", (0, 0, 0), group=PARTY).unit("tank", (20, 0, 0), group=PARTY, range_movement_bonus=True, level=30)
    s.unit("p2", (10.9, 0, 0), group=PARTY).unit("p4", (6.5, 0, 0), group=PARTY)
    s.friends("tank", "p1", "p2", "tank", "p4").aura("p1", "tank")
    out["radius-from-caster"] = s
    s = raid_scene()
    s.aura("p1", None)
    out["no-caster"] = s
    for x in (10.9, 11.5):
        s = Scene([effect(0, 119, 10.0), effect(1, 143, 10.0)])
        s.unit("pet", (0, 0, 0), kind="pet", owner="h", reach=1.0).unit("h", (x, 0, 0))
        s.rel("pet", "h", in_same_phase=True).aura("pet", "h")
        out[f"pet-owner{x}"] = s
    s = Scene([effect(0, 143, 10.0)])
    s.unit("pet", (0, 0, 0), kind="pet", owner="h").unit("h", (3, 0, 0)).rel("pet", "h", in_same_phase=False)
    s.aura("pet", "h")
    out["owner-phase"] = s
    s = Scene([effect(0, 174, 5.0), effect(1, 6, None)])
    s.unit("w", (0, 0, 0), pet="imp").unit("imp", (500, 0, 0), kind="pet", owner="w", in_owner_map=True, visit=False)
    s.aura("w", "w", statics={"w": [1]})
    out["on-pet"] = s
    s = Scene([effect(0, 35), effect(1, 6, None)], attributes=["SPELL_ATTR7_DISABLE_AURA_WHILE_DEAD"])
    s.unit("p1", (0, 0, 0), group=PARTY, alive=False).aura("p1", "p1", statics={"p1": [1]})
    out["dead-owner"] = s
    s = Scene([effect(0, 35), effect(1, 6, None)])
    s.unit("p1", (0, 0, 0), group=PARTY, banished=True).unit("p2", (1, 0, 0), group=PARTY).friends("p1", "p2")
    s.aura("p1", "p1", statics={"p1": [1]})
    out["banished-owner"] = s
    s = Scene([effect(0, 6, None, target_a=6), effect(1, 35, None, target_a=6)])
    s.unit("rogue", (5, 0, 0)).unit("enemy", (0, 0, 0), kind="creature").foes("rogue", "enemy")
    s.aura("enemy", "rogue", statics={"enemy": [0]})
    out["hostile-owner"] = s
    # update filters
    s = raid_scene()
    s.actor("p2")["pos"] = [60, 0, 0]
    s.actor("p3")["facts"]["aura_immune_existing"] = True
    s.actor("pet3")["facts"]["aura_immune_effects"] = [0]
    s.aura("p1", "p1", apps={"p1": [0], "p2": [0], "p3": [0], "pet3": [0]})
    out["update-remove"] = s
    s = raid_scene()
    s.actor("p2")["facts"]["aura_immune"] = True
    s.actor("p3")["facts"]["suppressed_by_label"] = True
    s.actor("x")["facts"]["in_world"] = False
    out["update-new-gates"] = s
    s = Scene([effect(0, 65), effect(1, 65)])
    s.unit("p1", (0, 0, 0), group=RAID0).unit("p2", (3, 0, 0), group=RAID0, auras=[{"spell": 1, "caster": "z"}],
                                                highest_exclusive=False)
    s.unit("p3", (4, 0, 0), group=RAID0, auras=[{"spell": 1, "caster": "z"}], highest_exclusive=True, can_stack=False)
    s.unit("p4", (5, 0, 0), group=RAID0, auras=[{"spell": 1, "caster": "z"}], highest_exclusive=True, can_stack=True)
    s.friends("p1", "p2", "p3", "p4").aura("p1", "p1", apps={"p1": [0, 1], "p2": [0], "p4": [0]})
    out["update-exclusive-stack"] = s
    s = raid_scene()
    s.actor("p1")["auras"] = [{"spell": 5, "caster": "q", "owner": "q"}]
    s.actor("p1")["facts"].update(highest_exclusive=True, can_stack=False)
    out["owner-skips-stack"] = s
    for tb in (28, 29):
        out[f"dynobj{tb}"] = dyn_scene(tb)
    s = dyn_scene(28)
    s.doc["spell"]["effects"][0]["attributes"] = ["PlayersOnly"]
    s.actor("e1")["auras"] = [{"spell": 990001, "caster": "w", "owner": "d_old"}]
    s.actor("e1")["facts"]["highest_exclusive"] = True
    s.unit("e3", (4, 0, 0), kind="creature", in_flight=True).foes("w", "e3").rel("d", "e3", in_same_phase=True)
    out["dynobj-stack-flight"] = s
    return out


SCENARIOS = scenarios()


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_fill_target_map_matches_trinity(probe, name):
    """The whole ``targets`` map (static applications + every effect) equals Trinity's."""
    world, sv, (res,) = run(probe, SCENARIOS[name], ["fill"])
    owner = J._aura_key(world, "owner")
    assert res["fill"] == J.target_map(world, sv, owner), name


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_check_construction_matches_trinity(probe, name):
    """Search centre, check caster/referer, check type, radius and search radius equal Trinity's construction."""
    world, sv, (res,) = run(probe, SCENARIOS[name], ["fill"])
    owner = J._aura_key(world, "owner")
    kind = J.aura_type(world, owner)
    w = world.actor(owner)
    if kind == "unit" and (not w.facts["in_world"] or w.facts["banished"] or
                           (sv.has_attr("SPELL_ATTR7_DISABLE_AURA_WHILE_DEAD") and not w.alive)):
        assert res["checks"] == []
        return
    expected, searches = [], []
    for e in sv.effects:
        if kind == "dynobj":
            if e.effect != 27:
                continue
            caster = w.fact("caster")
            a, b = info(e.target_a), info(e.target_b)
            check = b.check if b.reference == "DEST" else a.check
            radius = float(w.fact("radius"))
            expected.append({"caster": caster, "referer": caster, "check": CHECKS.index(check), "min": 0.0,
                             "max": radius, "pos": owner, "always_visible": False})
            searches.append({"mask": -1, "radius": radius, "pos": owner})
            continue
        sel = J.SELECTION.get(e.effect)
        if sel is None:
            continue
        caster = J.aura_caster(world) or owner
        rmin, rmax = J.unit_aura_radius(world, sv, e, caster, None)
        expected.append({"caster": caster, "referer": owner, "check": CHECKS.index(sel), "min": rmin, "max": rmax,
                         "pos": owner, "always_visible": True})
        extra = J.EXTRA_CELL_SEARCH_RADIUS if e.effect == 129 and rmax > 0 else 0.0
        searches.append({"mask": A.searcher_type_mask(sv, e, "UNIT"), "radius": rmax + extra, "pos": owner})
    got = [{**c, "min": float.fromhex(c["min"]), "max": float.fromhex(c["max"])} for c in res["checks"]]
    assert got == expected, name
    assert [{**x, "radius": float.fromhex(x["radius"])} for x in res["searches"]] == searches, name


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_update_target_map_matches_trinity(probe, name):
    """``UpdateTargetMap(caster, true)``: created / updated / unapplied sets equal Trinity's events."""
    world, sv, (res,) = run(probe, SCENARIOS[name], ["update 1"])
    owner = J._aura_key(world, "owner")
    py = J.update_target_map(world, sv, owner)
    ev = res["events"]
    created = {e["unit"]: e["mask"] for e in ev if e["ev"] == "create"}
    removed = sorted(e["unit"] for e in ev if e["ev"] == "remove")
    updated = {e["unit"]: e["mask"] for e in ev if e["ev"] == "update" and e["unit"] not in removed}
    applied = {e["unit"]: e["mask"] for e in ev if e["ev"] == "apply"}
    assert created == {u: J._mask(i) for u, i in py["create"].items()}, name
    assert removed == py["remove"], name
    assert updated == {u: J._mask(i) for u, i in py["update"].items()}, name
    # existing applications are erased from `targets` after UpdateApplyEffectMask (764-765): only new ones reach _ApplyAura
    assert applied == created, name
    assert res["interval"] == J.UPDATE_TARGET_MAP_INTERVAL


@pytest.mark.parametrize("interval,diff", [(0, 16), (500, 100), (100, 100), (101, 100), (-5, 0), (0, 0), (500, 600)])
def test_update_cadence_matches_trinity(probe, interval, diff):
    _, _, (res,) = run(probe, raid_scene(), [f"timer {interval} {diff}"])
    assert (res["ran"], res["interval"]) == J.next_update(interval, diff)


def test_witness_worlds_match_trinity(probe):
    """The three pinned witnesses evaluate identically in the probe."""
    from targeting.fixture import World
    for path in J.witness_paths():
        doc = json.loads(path.read_text(encoding="utf-8"))
        world = World.from_dict(doc["fixture"])
        world2, sv, (fill, upd) = run(probe, world, ["fill", "update 1"])
        owner = J._aura_key(world2, "owner")
        assert fill["fill"] == J.target_map(world2, sv, owner), path.name
        created = sorted(e["unit"] for e in upd["events"] if e["ev"] == "create" and e["mask"] & (1 << doc["effect"]))
        assert created == doc["expect"]["recipients"], path.name
