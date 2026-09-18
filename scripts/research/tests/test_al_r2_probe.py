"""R2 hostile review (snapshot / timing): refresh entry points and duration mutations vs the tick budget.

The probe (tools/tc_aura_r2_probe) compiles the same verbatim Trinity periodic core as Track D's probe plus
verbatim statements from the refresh / duration-mutation entry points the pass did not model:
``Spell::EffectModifyAuraStacks`` (effect 289), the ``AuraCreateInfo::ResetPeriodicTimer`` default used by
``Unit::AddAura`` / steal / vehicle refreshes, ``spell_pri_painful_punishment`` and ``spell_pri_mental_decay``.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aura_lifecycle import TC_ROOT

RESEARCH = Path(__file__).resolve().parents[1]
PROBE_DIR = RESEARCH / "tools" / "tc_aura_r2_probe"
PROBE = PROBE_DIR / "probe"
TABLES = RESEARCH.parents[1] / "data" / "tables"
PERIODIC_AURAS = {3, 8, 20, 21, 23, 24, 48, 53, 62, 64, 89, 162, 226, 227}


@pytest.fixture(scope="module")
def probe():
    if not (TC_ROOT / "src/server/game/Spells/Auras/SpellAuraEffects.cpp").exists():
        pytest.skip("sibling TrinityCore checkout absent")
    if shutil.which("g++") is None and not PROBE.exists():
        pytest.skip("no compiler")
    subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True, capture_output=True)
    return PROBE


def _run(probe, script: list[str]) -> list[tuple[int, dict]]:
    """``upto a b`` expands to 100 ms owner updates; returns (t, row) pairs."""
    cmds: list[str] = []
    for c in script:
        if c.startswith("upto"):
            _, a, b = c.split()
            cmds += ["update 100"] * ((int(b) - int(a)) // 100)
        else:
            cmds.append(c)
    out = subprocess.run([str(probe)], input="\n".join(cmds) + "\n", capture_output=True, text=True, check=True).stdout
    t, rows = 0, []
    for line in out.splitlines():
        row = json.loads(line)
        if row["event"] == "update":
            t += row["diff"]
        rows.append((t, row))
    return rows


def _ticks(rows):
    return [t for t, r in rows if r["event"] == "update" for _ in r["ticks"]]


def _expiry(rows):
    return [t for t, r in rows if r["event"] == "update" and r["removed"]]


STACKING_DOT = ["spell 1 0 0 0 0 0 0 0 5 12000 1", "effect 1 3 3000", "create 1", "upto 0 4000"]


@pytest.mark.parametrize("entry,expected_timer", [
    ("reapply 1 0", 1000),   # Spell path: StackAmount>=2 -> resetPeriodicTimer=false (Spell.cpp:3240)
    ("modstacks 1 1", 0),    # SPELL_EFFECT_MODIFY_AURA_STACKS 289 (SpellEffects.cpp:6138) -> default reset=true
    ("addaura 1", 0),        # Unit::AddAura refresh: AuraCreateInfo default ResetPeriodicTimer=true (SpellAuras.h:135)
])
def test_phase_reset_depends_on_entry_point_not_on_stackamount(probe, entry, expected_timer):
    """Rules out AL-R-D-05 as stated ('restart iff StackAmount<2 AND no DONT_RESET flag AND no ATTR13'):
    the same StackAmount=5 aura keeps its phase on the Spell path but restarts it via effect 289 / AddAura."""
    rows = _run(probe, STACKING_DOT + [entry])
    aura = rows[-1][1]["aura"]
    assert aura["stack"] == 2
    assert aura["effects"][0]["timer"] == expected_timer
    assert aura["effects"][0]["ticks_done"] == 0


def test_modstacks_zero_is_a_full_refresh(probe):
    """Effect 289 with value 0 (num=0 -> refresh = stack >= stack) restarts duration and phase without adding a stack."""
    rows = _run(probe, STACKING_DOT + ["upto 4000 5000", "modstacks 1 0"])
    aura = rows[-1][1]["aura"]
    assert (aura["stack"], aura["duration"], aura["effects"][0]["timer"]) == (1, 12000, 0)


def test_painful_punishment_extension_is_silent(probe):
    """Rules out 'a script duration extension adds ticks': SetDuration without SetMaxDuration keeps the budget
    (GetTotalTicks = MaxDuration/period), so the 4000 ms extension of a 16 s / 2 s DoT has no tick."""
    rows = _run(probe, ["spell 2 0 0 0 0 0 0 0 0 16000 1", "effect 2 3 2000", "create 2", "upto 0 10000",
                        "painful 2 4000", "upto 10000 21000"])
    assert _ticks(rows) == list(range(2000, 16001, 2000))
    assert _expiry(rows) == [20000]


def test_mental_decay_extension_collapses_budget(probe):
    """Rules out 'extension keeps the remaining ticks': SetMaxDuration(remaining + x) with ticks_done kept
    gives total_ticks 3 < ticks_done 5, so every remaining tick is lost although the aura lives 7 s longer."""
    rows = _run(probe, ["spell 3 0 0 0 0 0 0 0 0 16000 1", "effect 3 3 2000", "create 3", "upto 0 10000",
                        "mentaldecay 3 1000", "upto 10000 18000"])
    eff = [r for _, r in rows if r["event"] == "mentaldecay"][0]["aura"]["effects"][0]
    assert (eff["ticks_done"], eff["total_ticks"]) == (5, 3)
    assert _ticks(rows) == [2000, 4000, 6000, 8000, 10000]
    assert _expiry(rows) == [17000]


def test_effect_289_adds_stacks_to_stacking_periodic_auras():
    """DB2 fact: the phase-restarting effect-289 path is authored (all population) on stacking periodic auras."""
    if not (TABLES / "SpellEffect.csv").exists():
        pytest.skip("no table snapshot")
    eff: dict[int, list[dict]] = {}
    with open(TABLES / "SpellEffect.csv", newline="") as f:
        for r in csv.DictReader(f):
            if r["DifficultyID"] == "0":
                eff.setdefault(int(r["SpellID"]), []).append(r)
    stack = {}
    with open(TABLES / "SpellAuraOptions.csv", newline="") as f:
        for r in csv.DictReader(f):
            if r["DifficultyID"] == "0":
                stack[int(r["SpellID"])] = int(r["CumulativeAura"])
    hits = set()
    for sid, rows in eff.items():
        for e in rows:
            if e["Effect"] != "289" or e["EffectMiscValue_0"] != "0" or float(e["EffectBasePointsF"]) <= 0:
                continue
            tgt = int(e["EffectTriggerSpell"])
            periodic = any(int(x["EffectAura"] or 0) in PERIODIC_AURAS and int(x["EffectAuraPeriod"] or 0) > 0
                           for x in eff.get(tgt, []))
            if periodic and stack.get(tgt, 0) >= 2:
                hits.add((sid, int(e["EffectIndex"]), tgt))
    assert len(hits) == 18
    assert (451373, 0, 451179) in hits
