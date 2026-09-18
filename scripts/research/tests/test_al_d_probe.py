"""Track D: differential of ``aura_lifecycle.periodic`` against verbatim Trinity code (tools/tc_aura_periodic_probe).

The probe compiles Trinity's own ``AuraEffect::{GetTotalTicks,ResetPeriodic,CalculatePeriodic,Update}``,
``Aura::{UpdateOwner,Update,SetDuration,RefreshDuration,RefreshTimers,CalcMaxStackAmount,ModStackAmount}``,
``WorldObject::ModSpellDurationTime``, the ``Spell::DoSpellEffectHit`` reset-flag statement and duration-override
blocks, and the ``Unit::_UpdateSpells`` owned-aura update + expiry loops.  Cut points (no spell modifiers, no
amounts, self aura) are listed in ``probe.cpp``.
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
from aura_lifecycle import periodic as P

PROBE_DIR = Path(__file__).resolve().parents[1] / "tools" / "tc_aura_periodic_probe"
PROBE = PROBE_DIR / "probe"


@pytest.fixture(scope="module")
def probe():
    if not (TC_ROOT / "src/server/game/Spells/Auras/SpellAuraEffects.cpp").exists():
        pytest.skip("sibling TrinityCore checkout absent")
    if shutil.which("g++") is None and not PROBE.exists():
        pytest.skip("no compiler")
    subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True, capture_output=True)
    return PROBE


def _updates(sc):
    return list(sc["updates"]) if "updates" in sc else list(range(sc["update_every"], sc["until"] + 1, sc["update_every"]))


def _run_probe(probe, commands: str) -> list[dict]:
    out = subprocess.run([str(probe)], input=commands, capture_output=True, text=True, check=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _compare(probe, sc):
    rows = _run_probe(probe, P.probe_commands(sc))
    ups = [r for r in rows if r["event"] == "update"]
    updates = _updates(sc)
    assert len(ups) == len(updates)
    probe_ticks = [t for t, r in zip(updates, ups) for _ in r["ticks"]]
    model = P.run(sc, trace=True)
    assert model["tick_times"] == probe_ticks
    # state after every owner update while the aura lives
    by_t = {r["t"]: r for r in model["rows"] if r["event"] == "owner-update"}
    for t, r in zip(updates, ups):
        if not r["auras"]:
            continue
        a = r["auras"][0]
        m = by_t[t]["state"]
        assert (a["duration"], a["max_duration"], a["stack"]) == (m["duration"], m["max_duration"], m["stack"]), t
        for pe, me in zip(a["effects"], m["effects"]):
            assert (pe["period"], pe["timer"], pe["ticks_done"], pe["total_ticks"], pe["is_periodic"]) == \
                   (me["period"], me["timer"], me["ticks_done"], me["total_ticks"], me["is_periodic"]), t
    expired = [t for t, r in zip(updates, ups) if r["removed"]]
    model_expired = [r["t"] for r in model["rows"] if r["event"] == "owner-update" and r["state"].get("removed") == "expire"]
    assert expired[:1] == model_expired[:1]


@pytest.mark.parametrize("spec", [s for s in P.TIMELINE_SPECS if s["probe"]], ids=lambda s: s["id"])
def test_timelines_match_verbatim_trinity(probe, spec):
    """Every probe-marked corpus timeline is reproduced by compiled Trinity code, state by state."""
    _compare(probe, spec["scenario"])


def test_pandemic_reads_duration_after_refresh(probe):
    """Rules out 'pandemic = base + min(remaining, 30%)': verbatim Spell.cpp:3284-3288 after RefreshTimers yields 1.3x."""
    sc = {"spell": {"base_period": 3000, "base_duration": 12000,
                    "attrs": {"SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION": True}},
          "update_every": 100, "until": 11000, "events": [{"t": 0, "op": "create"}, {"t": 10050, "op": "reapply"}]}
    rows = _run_probe(probe, P.probe_commands(sc))
    reapply = next(r for r in rows if r["event"] == "reapply")
    assert reapply["aura"]["duration"] == 15600 and reapply["aura"]["max_duration"] == 15600
    assert reapply["reset"] is True and reapply["aura"]["effects"][0]["timer"] == 1000   # reset requested, pandemic keeps it


def test_owned_aura_update_order_and_expiry_after_all_ticks(probe):
    """Two auras due in the same owner update: ticks in m_ownedAuras (SpellId) order, expiry removal after both.
    Rules out insertion-order processing and per-aura interleaved expiry."""
    cmds = "\n".join([
        "spell 200 0 0 0 0 0 0 0 0 6000 1", "effect 200 3 3000",
        "spell 100 0 0 0 0 0 0 0 0 6000 1", "effect 100 8 2000",
        "caster 1.0 1.0", "create 200", "create 100",
        *["update 1000"] * 6]) + "\n"
    ups = [r for r in _run_probe(probe, cmds) if r["event"] == "update"]
    last = ups[-1]
    assert [t["spell"] for t in last["ticks"]] == [100, 200]
    assert sorted(x["spell"] for x in last["removed"]) == [100, 200] and last["auras"] == []


ATTR_CHOICES = ("SPELL_ATTR5_EXTRA_INITIAL_PERIOD", "SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION",
                "SPELL_ATTR5_SPELL_HASTE_AFFECTS_PERIODIC", "SPELL_ATTR8_MELEE_HASTE_AFFECTS_PERIODIC",
                "SPELL_ATTR8_HASTE_AFFECTS_DURATION", "SPELL_ATTR1_AURA_UNIQUE")


@st.composite
def scenarios(draw):
    period = draw(st.sampled_from([500, 1000, 1500, 2000, 3000, 5000]))
    duration = draw(st.sampled_from([-1, 0, 1000, 4000, 6000, 10000, 12000, 18000]))
    attrs = {a: True for a in draw(st.lists(st.sampled_from(ATTR_CHOICES), unique=True, max_size=3))}
    stack = draw(st.sampled_from([0, 1, 2, 5]))
    step = draw(st.sampled_from([37, 50, 100, 250, 1000]))
    until = 25000
    events = [{"t": draw(st.integers(0, 2000)), "op": "create"}]
    for _ in range(draw(st.integers(0, 3))):
        op = draw(st.sampled_from(["reapply", "reapply", "haste", "remove", "set_duration", "set_max_duration"]))
        ev = {"t": draw(st.integers(events[-1]["t"], 20000)), "op": op}
        if op == "reapply":
            ev["trigger_flags"] = draw(st.sampled_from([0, P.TRIGGERED_FULL_MASK, P.TRIGGERED_DONT_RESET_PERIODIC_TIMER]))
            ev["path"] = draw(st.sampled_from(["spell-hit", "spell-hit", "add-aura", "modify-stacks"]))
            if ev["path"] == "modify-stacks":
                ev["value"] = draw(st.sampled_from([0, 1, 3]))
        if op in ("set_duration", "set_max_duration"):
            ev["delta"] = draw(st.sampled_from([-500, 1000, 4000]))
        if op == "haste":
            ev["cast_speed"] = draw(st.sampled_from([0.5, 0.769, 0.8, 1.0]))
            ev["melee_haste"] = draw(st.sampled_from([0.5, 0.9, 1.0]))
        events.append(ev)
    return {"spell": {"base_period": period, "base_duration": duration, "attrs": attrs, "stack_amount": stack},
            "caster": {"cast_speed": draw(st.sampled_from([1.0, 0.7, 0.8333])), "melee_haste": 1.0},
            "update_every": step, "until": until, "events": events}


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(sc=scenarios())
def test_random_scenarios_match_probe(probe, sc):
    """Property: the Python mirror equals compiled Trinity for random periods, durations (incl. permanent),
    attribute mixes, stack capacities, update steps, haste changes, reapply trigger flags and removals.

    Scenarios the mirror refuses (FailClosed, e.g. a script max-duration delta that makes a
    permanent aura's max negative) are discarded, not compared."""
    from hypothesis import assume

    from aura_lifecycle import FailClosed
    try:
        P.run(sc)
    except FailClosed:
        assume(False)
    _compare(probe, sc)
