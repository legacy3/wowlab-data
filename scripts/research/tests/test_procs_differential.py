"""Differential verification against Trinity's own proc code, compiled.

``tools/tc_proc_probe`` extracts CanSpellTriggerProcOnEvent, the default
generation block of LoadSpellProcs, CalcProcPPM (+ helpers),
CalcPPMProcChance, CalcProcChance, GetPPMProcChance and GetWeaponProcChance
verbatim and compiles them against stubs.  Floats come back as hex, so the
comparisons below are exact (binary32 bit equality), not approximate.

Build with ``make -C scripts/research/tools/tc_proc_probe``; otherwise skipped.

Scope limits (documented, not hidden): the probe's ``CalcValueAsInt`` returns
the integer the Python side passes, so the ``MOD_HIT_CHANCE`` generation input
is compared as data, not re-derived; spell mods are absent on both sides.
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

import pytest

from procs import enums as E
from procs.chance import (
    RppmInputs,
    aura_proc_chance,
    classic_ppm_chance,
    f32,
    rppm_chance,
    rppm_rate,
    weapon_proc_chance,
)
from procs.definition import ProcEntry, generate_default_entry
from procs.eligibility import can_trigger

PROBE = Path(__file__).resolve().parents[1] / "tools" / "tc_proc_probe" / "probe"


@pytest.fixture(scope="module")
def probe():
    if not PROBE.exists():
        pytest.skip(f"probe not built; run: make -C {PROBE.parent}")
    proc = subprocess.Popen([str(PROBE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, bufsize=1)

    def ask(request: str) -> str:
        proc.stdin.write(request + "\n")
        proc.stdin.flush()
        return proc.stdout.readline().strip()

    yield ask
    proc.stdin.close()
    proc.wait(timeout=10)


def hexf(text: str) -> float:
    return float.fromhex(text)


def s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def gen_request(info) -> str:
    parts = ["gen", info.id, s32(info.proc_flags & 0xFFFFFFFF), s32(info.proc_flags >> 32),
             info.proc_chance, info.proc_cooldown, info.proc_charges, repr(info.base_ppm),
             info.family, info.attributes[3], len(info.effects)]
    for e in info.effects:
        cm = [(e.class_mask >> (32 * i)) & 0xFFFFFFFF for i in range(4)]
        parts += [e.index, e.effect, e.aura, e.trigger_spell, e.calc_value_as_int_unscaled(), *cm]
    return " ".join(str(p) for p in parts)


def entry_tuple(e: ProcEntry) -> tuple:
    fm = [(e.family_mask >> (32 * i)) & 0xFFFFFFFF for i in range(4)]
    return (e.school_mask, e.family_name, *fm, e.proc_flags & 0xFFFFFFFF, e.proc_flags >> 32,
            e.spell_type_mask, e.spell_phase_mask, e.hit_mask, e.attributes_mask,
            e.disable_effects_mask, f32(e.procs_per_minute), f32(e.chance), e.cooldown_ms, e.charges)


def parse_gen(reply: str):
    if reply == "none":
        return None
    f = reply.split()
    ints = [int(x) for x in f[:13]]
    return (*ints, hexf(f[13]), hexf(f[14]), int(f[15]), int(f[16]))


@pytest.mark.snapshot
def test_default_generation_matches_trinity_for_every_real_proc_spell(probe, tables):
    from procs.source import Source
    from procs.spells import SpellCatalog
    catalog = SpellCatalog(Source(tables.root))
    ids = catalog.proc_flag_spells(0)
    assert len(ids) == 14176
    mismatches = []
    compared = 0
    for sid in ids:
        info = catalog.require(sid)
        py = generate_default_entry(info).entry
        cc = parse_gen(probe(gen_request(info)))
        compared += 1
        if (entry_tuple(py) if py else None) != cc:
            mismatches.append((sid, py and entry_tuple(py), cc))
    assert compared == len(ids)
    assert mismatches == []


def test_default_generation_matches_trinity_on_random_spells(probe, tmp_path):
    from proc_synthetic import Eff, Spell, world
    rng = random.Random(7)
    auras = sorted(E.TRIGGER_AURAS | {E.SPELL_AURA_ADD_PCT_MODIFIER, 3, 8, 22, 0})
    effects = [0, 6, 6, 6, 27, 35, 65, 174, 2, 3]
    spells = []
    for sid in range(1, 400):
        n = rng.randint(0, 3)
        effs = [Eff(index=i, effect=rng.choice(effects), aura=rng.choice(auras),
                    trigger=rng.choice([0, 0, 5]), base_points=rng.choice([0, -100, -150, 5]),
                    class_mask=(rng.choice([0, 1, 0x80000000]), 0, 0, rng.choice([0, 4])))
                for i in range(n)]
        flags = 0
        for _ in range(rng.randint(0, 3)):
            flags |= rng.choice(list(E.PROC_FLAG_NAMES))
        spells.append(Spell(sid, proc_flags=flags, effects=effs, proc_chance=rng.choice([0, 50, 100, 101]),
                            proc_cooldown=rng.choice([0, 0, 1000]), proc_charges=rng.choice([0, 0, 1, -1]),
                            family=rng.choice([0, 8]),
                            attributes={3: rng.choice([0, E.ATTR3_CAN_PROC_FROM_PROCS[1]])}))
    cat, _, _ = world(tmp_path, spells)
    for s in spells:
        info = cat.require(s.id)
        py = generate_default_entry(info).entry
        assert (entry_tuple(py) if py else None) == parse_gen(probe(gen_request(info))), s.id


def test_can_spell_trigger_matches_trinity(probe):
    rng = random.Random(11)
    flags = list(E.PROC_FLAG_NAMES)
    hits = list(E.HIT_NAMES) + [0, E.PROC_HIT_NORMAL | E.PROC_HIT_ABSORB]
    for _ in range(20000):
        pf = 0
        for _ in range(rng.randint(1, 3)):
            pf |= rng.choice(flags)
        tm = 0
        for _ in range(rng.randint(1, 3)):
            tm |= rng.choice(flags)
        if rng.random() < 0.3:
            tm |= pf & -pf
        e = ProcEntry(
            school_mask=rng.choice([0, 0, 1, 4, 0x7F]), family_name=rng.choice([0, 0, 8]),
            family_mask=rng.choice([0, 0x10, 1 << 70]), proc_flags=pf,
            spell_type_mask=rng.choice([0, 1, 2, 4, 7]), spell_phase_mask=rng.choice([0, 1, 2, 4, 7]),
            hit_mask=rng.choice([0, 0, rng.choice(hits)]),
            attributes_mask=rng.choice([0, 0, E.PROC_ATTR_REQ_EXP_OR_HONOR, E.PROC_ATTR_REQ_POWER_COST, 0x5]),
            disable_effects_mask=0, procs_per_minute=0.0, chance=0.0, cooldown_ms=0, charges=0,
            origin="generated", keyed_difficulty=0)
        ev = dict(spell_type=rng.choice([0, 1, 2, 4, 7]), phase=rng.choice([0, 1, 2, 4]),
                  hit=rng.choice(hits), school=rng.choice([0, 1, 4, 0x20]),
                  has_spell=rng.random() < 0.6, cost=rng.random() < 0.5,
                  has_info=rng.random() < 0.7, efam=rng.choice([0, 8, 9]),
                  eflags=rng.choice([0, 0x10, 0x20, 1 << 70]), player=rng.random() < 0.6,
                  target=rng.random() < 0.7, honor=rng.random() < 0.5)
        py = can_trigger(e, tm, ev["spell_type"], ev["phase"], ev["hit"], ev["school"],
                         has_proc_spell=ev["has_spell"], power_cost_positive=ev["cost"],
                         event_family=(ev["efam"], ev["eflags"]) if ev["has_info"] else None,
                         actor_is_player=ev["player"], has_action_target=ev["target"],
                         honor_target=ev["honor"])
        fm = [(e.family_mask >> (32 * i)) & 0xFFFFFFFF for i in range(4)]
        ef = [(ev["eflags"] >> (32 * i)) & 0xFFFFFFFF for i in range(4)]
        req = ["match", e.school_mask, e.family_name, *fm, s32(pf & 0xFFFFFFFF), s32(pf >> 32),
               e.spell_type_mask, e.spell_phase_mask, e.hit_mask, e.attributes_mask,
               s32(tm & 0xFFFFFFFF), s32(tm >> 32), ev["spell_type"], ev["phase"], ev["hit"], ev["school"],
               int(ev["has_spell"]), int(ev["cost"]), int(ev["has_info"]), ev["efam"], *ef,
               int(ev["player"]), int(ev["target"]), int(ev["honor"])]
        assert probe(" ".join(map(str, req))) == ("1" if py else "0"), (e, tm, ev)


@pytest.mark.parametrize("speed", [0, 1, 1500, 2000, 2600, 3600, 4000, 65535, 2**31 + 7])
@pytest.mark.parametrize("ppm", [0.0, -1.0, 0.5, 1.0, 3.3, 6.0, 20.0, 1e-7, 7.123456])
def test_classic_ppm_matches_trinity(probe, speed, ppm):
    assert hexf(probe(f"ppm {speed} {f32(ppm)!r}")) == classic_ppm_chance(speed, ppm).chance_percent


@pytest.mark.parametrize("args", [(1, 2600, 0, 0, 0), (0, 2600, 1, 1, 1500), (0, 2600, 1, 0, 1500),
                                  (1, 1, 0, 0, 0), (1, 3900, 1, 1, 2600)])
def test_weapon_proc_chance_matches_trinity(probe, args):
    a = [bool(args[0]), args[1], bool(args[2]), bool(args[3]), args[4]]
    assert hexf(probe("weapon " + " ".join(map(str, args)))) == weapon_proc_chance(*a)


def rppm_request(base, mods, i: RppmInputs, rpp: dict[int, float]):
    parts = ["rppm", repr(f32(base)), len(mods)]
    for m in mods:
        parts += [m["type"], m["param"], repr(f32(m["coeff"]))]
    parts += [repr(f32(i.mod_haste)), repr(f32(i.mod_ranged_haste)), repr(f32(i.mod_spell_haste)),
              repr(f32(i.mod_haste_regen)), int(i.is_player), repr(f32(i.crit_pct)),
              repr(f32(i.ranged_crit_pct)), repr(f32(i.spell_crit_pct)), i.class_id or 0,
              i.primary_spec or 0, i.race_id or 0, i.item_level, int(bool(i.in_battleground_or_arena)),
              len(i.auras or ())]
    parts += sorted(i.auras or ())
    parts.append(len(rpp))
    for level, points in sorted(rpp.items()):
        parts += [level, repr(f32(points))]
    return " ".join(map(str, parts))


def test_rppm_rate_matches_trinity_on_random_inputs(probe):
    rng = random.Random(3)
    rpp = {528: 101.5, 600: 177.25, 700: 311.0, 1: 3.0}
    for _ in range(3000):
        mods = []
        for k in range(rng.randint(0, 5)):
            t = rng.randint(1, 8)
            param = {1: rng.randint(0, 6), 2: rng.randint(0, 5), 3: rng.choice([1, 2, 8, 0x7FF, -1]),
                     4: rng.choice([62, 71, 259, 0]), 5: rng.choice([1, 1 << 11, 1 << 15, -1]),
                     6: rng.choice([528, 600, 1]), 7: 0, 8: rng.choice([5, 6])}[t]
            mods.append({"id": k, "type": t, "param": param,
                         "coeff": rng.choice([-1.0, -0.75, -0.5, 0.3, 1.0, 1.5, 2.5, 3.25])})
        i = RppmInputs(mod_haste=f32(1 / rng.uniform(1, 2)), mod_ranged_haste=f32(1 / rng.uniform(1, 2)),
                       mod_spell_haste=f32(1 / rng.uniform(1, 2)), mod_haste_regen=f32(1 / rng.uniform(1, 2)),
                       is_player=rng.random() < 0.8, crit_pct=f32(rng.uniform(0, 60)),
                       ranged_crit_pct=f32(rng.uniform(0, 60)), spell_crit_pct=f32(rng.uniform(0, 60)),
                       class_id=rng.randint(1, 13), primary_spec=rng.choice([62, 71, 259]),
                       race_id=rng.choice([1, 2, 12, 34, 52, 70, 91]),
                       item_level=rng.choice([528, 600, 700, 5]),
                       in_battleground_or_arena=rng.random() < 0.3, auras=frozenset(rng.sample([5, 6, 7], 1)))
        base = f32(rng.choice([0.0, 1.0, 1.01, 2.5, 6.0, 20.0, 50.0]))
        py = rppm_rate(base, mods, i, lambda lvl: rpp.get(lvl, 0.0)).chance_percent
        assert hexf(probe(rppm_request(base, mods, i, rpp))) == py, (base, mods, i)


def test_rppm_real_modifier_sets_match_trinity(probe, tables):
    from procs.source import Source
    from procs.spells import SpellCatalog
    catalog = SpellCatalog(Source(tables.root))
    rpp_table = tables("RandPropPoints").by("ID")
    rpp = {lvl: float(rpp_table[lvl]["SuperiorF_0"]) for lvl in (528, 600, 650, 700) if lvl in rpp_table}
    i = RppmInputs(mod_haste=f32(1 / 1.25), mod_ranged_haste=f32(1 / 1.2), mod_spell_haste=f32(1 / 1.3),
                   mod_haste_regen=f32(1 / 1.1), crit_pct=25.0, ranged_crit_pct=20.0, spell_crit_pct=30.0,
                   class_id=5, primary_spec=258, race_id=10, item_level=650,
                   in_battleground_or_arena=False, auras=frozenset())
    seen = 0
    for ppm_id, mods in catalog.ppm_mods.items():
        base = catalog.ppm.get(ppm_id, {}).get("base", 1.0)
        py = rppm_rate(base, mods, i, lambda lvl: rpp.get(lvl, 0.0)).chance_percent
        assert hexf(probe(rppm_request(base, mods, i, rpp))) == py, ppm_id
        seen += 1
    assert seen > 200


@pytest.mark.parametrize("ppm", [0.0, 0.5, 1.0, 1.01, 2.0, 6.0, 20.0, 60.0])
@pytest.mark.parametrize("attempt_ms", [0, 1, 999, 1000, 4321, 10000, 10001, 60000])
@pytest.mark.parametrize("success_ms", [0, 1500, 60000, 120000, 999999, 1000000, 5000000])
def test_rppm_chance_matches_trinity(probe, ppm, attempt_ms, success_ms):
    py = aura_proc_chance(0.0, 0.0, ppm, has_caster=True, has_damage_info=False,
                          rppm=rppm_chance(ppm, attempt_ms / 1000.0, success_ms / 1000.0)).chance_percent
    cc = hexf(probe(f"chance 0 0 {f32(ppm)!r} 1 0 2000 0 80 {attempt_ms} {success_ms}"))
    if ppm == 0.0:
        # Trinity only takes the RPPM branch when ProcBasePPM > 0.
        assert cc == 0.0
    else:
        assert cc == py


@pytest.mark.parametrize("case", [
    (30.0, 0.0, 1, 1, 0, 0, 80), (30.0, 6.0, 1, 1, 0, 0, 80), (30.0, 6.0, 1, 0, 0, 0, 80),
    (30.0, 6.0, 0, 1, 0, 0, 80), (30.0, 0.0, 1, 1, 0x80, 0, 75), (30.0, 0.0, 1, 1, 0x80, 0, 60),
    (101.0, 0.0, 0, 0, 0x80, 0, 120), (0.0, 3.3, 1, 1, 0x80, 0, 61)])
def test_calc_proc_chance_matches_trinity(probe, case):
    chance, ppm, caster, damage, attrs, _, level = case
    py = aura_proc_chance(chance, ppm, 0.0, has_caster=bool(caster), has_damage_info=bool(damage),
                          weapon_speed_ms=2600, reduce_60=bool(attrs & 0x80), actor_level=level).chance_percent
    cc = hexf(probe(f"chance {chance!r} {ppm!r} 0 {caster} {damage} 2600 {attrs} {level} 10000 120000"))
    assert cc == py
