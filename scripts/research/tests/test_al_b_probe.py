"""Track B differential: aura_lifecycle.duration/refresh vs verbatim Trinity (tools/tc_aura_duration_probe).

The probe compiles Trinity's own RefreshDuration / RefreshTimers / CalcMaxDuration / ModStackAmount /
AuraEffect::{CalculatePeriodic,ResetPeriodic,GetTotalTicks,Update} / Aura::Update and the Spell.cpp
duration commit (ModSpellDuration .. pandemic .. SetDuration).  Skipped when TrinityCore or g++ is absent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aura_lifecycle import TC_ROOT
from aura_lifecycle.duration import (
    ATTR1_AURA_UNIQUE,
    ATTR1_IS_CHANNELLED,
    ATTR5_EXTRA_INITIAL_PERIOD,
    ATTR5_SPELL_HASTE_AFFECTS_PERIODIC,
    ATTR8_HASTE_AFFECTS_DURATION,
    ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION,
    Caster,
    DurationEntry,
    HitInputs,
    SpellFacts,
    SpellMods,
    calculate_pct,
)
from aura_lifecycle.refresh import apply_hit, pandemic_models, update

PROBE_DIR = Path(__file__).resolve().parents[1] / "tools" / "tc_aura_duration_probe"
PROBE = PROBE_DIR / "probe"


@pytest.fixture(scope="module")
def probe() -> Path:
    if not (TC_ROOT / "src/server/game/Spells/Auras/SpellAuras.cpp").exists():
        pytest.skip("TrinityCore checkout absent")
    if shutil.which("g++") is None:
        pytest.skip("g++ absent")
    subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True)
    return PROBE


def run(probe: Path, lines: list[str]) -> list[dict]:
    out = subprocess.run([str(probe)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def facts(dur, mx, per, stack, periods, attrs) -> SpellFacts:
    a = [0] * 17
    for w, m in attrs:
        a[w] |= m
    return SpellFacts(0, tuple(a), DurationEntry(0, dur, mx, per), stack_amount=stack, periods=tuple(periods))


def probe_lines(f: SpellFacts, periodic: tuple[bool, ...], caster: Caster, script: list[tuple]) -> list[str]:
    a = f.attributes
    lines = [f"spell {f.duration_entry.duration} {f.duration_entry.max_duration} {f.duration_entry.per_resource} 1 "
             f"{f.stack_amount} 1 0 {a[0]} {a[1]} {a[2]} {a[3]} {a[5]} {a[8]} {a[13]}"]
    for p, is_per in zip(f.periods, periodic):
        lines.append(f"effect {3 if is_per else 0} {p}")
    lines.append(f"caster 6 {caster.mod_casting_speed!r} {caster.mod_haste!r} {caster.duration_mods.flat} "
                 f"{caster.duration_mods.mul!r} {caster.period_mods.flat} {caster.period_mods.mul!r} "
                 f"{caster.change_cast_time_mods.flat} {caster.change_cast_time_mods.mul!r} 0")
    for op in script:
        lines.append(f"apply {op[1]}" if op[0] == "apply" else f"update {op[1]}")
    return lines


def model(f: SpellFacts, periodic: tuple[bool, ...], caster: Caster, script: list[tuple]) -> list[dict]:
    """Same driver as probe.cpp main(), on the Python oracle."""
    out, st, now = [], None, 0
    for op in script:
        if op[0] == "apply":
            cp = None if op[1] < 0 else op[1]
            st, _ = apply_hit(f, st, HitInputs(caster=caster, combo_points=cp), periodic_effects=periodic)
            out.append({"apply": _state(st)})
        else:
            now += op[1]
            if st is None:
                out.append({"events": [], "state": None})
                continue
            ev, expired = update(f, st, op[1], now)
            out.append({"events": [{"event": e["event"], **({"n": e["n"], "total": e["total"], "effect": e["effect"]}
                                                                if e["event"] == "tick" else {})}
                                   for e in ev if e["event"] in ("tick", "expire")],
                        "state": _state(st)})
            if expired:
                st = None
    return out


def _state(st) -> dict:
    return {"duration": st.duration, "max_duration": st.max_duration, "stacks": st.stacks,
            "effects": [{"period": e.period, "timer": e.timer, "ticks": e.ticks, "periodic": e.periodic} for e in st.effects]}


def normalise_probe(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if "apply" in r:
            out.append({"apply": r["apply"]})
        elif "update" in r:
            out.append({"events": [{k: v for k, v in e.items()} for e in r["events"]], "state": r["state"]})
    return out


def compare(probe_path: Path, f, periodic, caster, script) -> None:
    p = normalise_probe(run(probe_path, probe_lines(f, periodic, caster, script)))
    m = model(f, periodic, caster, script)
    assert p == m


P13 = ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION


def ms_script(first: int, second: int, end: int, step: int, cp: int = -1) -> list[tuple]:
    script, t = [("apply", cp)], 0
    while t + step <= second:
        script.append(("update", step))
        t += step
    script.append(("apply", cp))
    while t < end:
        script.append(("update", step))
        t += step
    return script


def test_pandemic_reads_refreshed_duration(probe):
    """Rules out 'carry-before-refresh' (and simc/Core) for pinned Trinity: the ATTR13 branch sees the
    already-refreshed duration, so 4 s remaining and 17 s remaining give the same 23 400 ms."""
    f = facts(18000, 18000, 0, 0, (3000,), [P13])
    for refresh_at in (1000, 14000):
        rows = run(probe, probe_lines(f, (True,), Caster(), ms_script(0, refresh_at, refresh_at + 100, 100)))
        second = [r for r in rows if "apply" in r][1]
        assert second["refresh"] is True
        assert second["after_try_refresh"]["duration"] == 18000  # r already overwritten
        assert second["apply"]["duration"] == 23400
        assert pandemic_models(18000, 18000 - refresh_at, 18000)["trinity-7f3d43b"]["value"] == 23400


def test_k_tl_01_250ms(probe):
    """Track K TL-K-01: 250 ms ATTR13 aura refreshed at 225 ms expires at 550 ms in pinned Trinity (Core: 500)."""
    f = facts(250, 250, 0, 0, (0,), [P13])
    rows = run(probe, probe_lines(f, (False,), Caster(), ms_script(0, 225, 700, 25)))
    t, expire_at = 0, None
    applies = 0
    for r in rows:
        if "apply" in r:
            applies += 1
        elif "update" in r:
            t += r["update"]
            if any(e["event"] == "expire" for e in r["events"]):
                expire_at = t
                break
    assert expire_at == 550
    assert pandemic_models(250, 25, 250)["core-63f3a49"]["value"] + 225 == 500


def test_rupture_combo_point_carry_is_minimum_duration(probe):
    """Rupture shape (4..24 s, +4 s/CP, ATTR13): refresh with 5 CP always yields 24000 + 4000 (the no-power-cost
    CalcMaxDuration in RefreshTimers), ruling out any remaining-dependent model."""
    f = facts(4000, 24000, 4000, 0, (2000,), [P13])
    for refresh_at in (4000, 23000):
        compare(probe, f, (True,), Caster(), ms_script(0, refresh_at, refresh_at + 200, 100, cp=5))
        rows = run(probe, probe_lines(f, (True,), Caster(), ms_script(0, refresh_at, refresh_at, 100, cp=5)))
        assert [r for r in rows if "apply" in r][1]["apply"]["duration"] == 28000


def test_unique_non_stacking_reads_live_remaining(probe):
    """ATTR1_AURA_UNIQUE + ATTR13: no RefreshTimers -> the carry uses the live remaining (22000 at r=4000)."""
    f = facts(18000, 18000, 0, 0, (3000,), [P13, ATTR1_AURA_UNIQUE])
    compare(probe, f, (True,), Caster(), ms_script(0, 14000, 40000, 100))
    rows = run(probe, probe_lines(f, (True,), Caster(), ms_script(0, 14000, 14000, 100)))
    second = [r for r in rows if "apply" in r][1]
    assert second["apply"]["duration"] == 22000 and second["apply"]["effects"][0]["ticks"] == 4


def test_unique_same_duration_is_noop(probe):
    """ATTR1_AURA_UNIQUE without ATTR13: hit == max -> duration kept (rules out 'recast always restarts')."""
    f = facts(18000, 18000, 0, 0, (3000,), [ATTR1_AURA_UNIQUE])
    compare(probe, f, (True,), Caster(), ms_script(0, 10000, 30000, 100))
    rows = run(probe, probe_lines(f, (True,), Caster(), ms_script(0, 10000, 10000, 100)))
    assert [r for r in rows if "apply" in r][1]["apply"]["duration"] == 8000


def test_stack_two_preserves_phase(probe):
    """StackAmount >= 2 (no ATTR13): refresh keeps the periodic phase, resets ticksDone (Spell.cpp:3240)."""
    f = facts(12000, 12000, 0, 3, (2000,), [])
    compare(probe, f, (True,), Caster(), ms_script(0, 5000, 20000, 100))


def test_extra_initial_period_refresh(probe):
    f = facts(10000, 10000, 0, 0, (2000,), [ATTR5_EXTRA_INITIAL_PERIOD])
    compare(probe, f, (True,), Caster(), ms_script(0, 5000, 20000, 250))


def test_calculate_pct_binary32_boundary(probe):
    """trunc(float(D)*130f/100f) diverges from integer floor(13D/10) first at 516 250 ms (Core uses integers)."""
    for base in (0, 1, 7, 18000, 129055, 516249, 516250, 1_000_000, 2_000_000):
        got = run(probe, [f"calcpct {base} 130"])[0]["calcpct"]
        assert got == calculate_pct(base, 130)
    assert calculate_pct(516250, 130) != (13 * 516250) // 10


_attrs = st.sets(st.sampled_from(["p13", "uniq", "a8", "a5", "extra", "chan"]), max_size=4)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(dur=st.integers(1, 400).map(lambda x: x * 100), period=st.sampled_from([0, 500, 1000, 1500, 2000, 3000]),
       stack=st.sampled_from([0, 1, 3]), attrs=_attrs, speed=st.sampled_from([1.0, 0.8, 0.7692307829856873, 1.25]),
       dflat=st.sampled_from([0, 2000, -500]), dmul=st.sampled_from([1.0, 1.1, 0.9]),
       refresh_frac=st.floats(0.0, 1.2), step=st.sampled_from([1, 50, 100, 137]), cp=st.sampled_from([-1, 0, 3, 5]),
       per=st.sampled_from([0, 1000]))
def test_random_differential(probe, dur, period, stack, attrs, speed, dflat, dmul, refresh_frac, step, cp, per):
    table = {"p13": P13, "uniq": ATTR1_AURA_UNIQUE, "a8": ATTR8_HASTE_AFFECTS_DURATION,
             "a5": ATTR5_SPELL_HASTE_AFFECTS_PERIODIC, "extra": ATTR5_EXTRA_INITIAL_PERIOD, "chan": ATTR1_IS_CHANNELLED}
    f = facts(dur, dur * 2 if per else dur, per, stack, (period,), [table[a] for a in attrs])
    caster = Caster(mod_casting_speed=speed, duration_mods=SpellMods(dflat, dmul))
    refresh_at = int(dur * refresh_frac)
    compare(probe, f, (bool(period),), caster, ms_script(0, refresh_at, refresh_at + dur * 2, step if step > 1 else 50, cp=cp))
