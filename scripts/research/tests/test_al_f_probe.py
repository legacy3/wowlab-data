"""Track F: differential of the recipient model against verbatim Trinity code (tools/tc_aura_area_probe).

The probe compiles Trinity's own ``Aura::UpdateOwner``, ``Aura::Update``, ``Aura::UpdateTargetMap``,
``Aura::_Remove``, ``AuraEffect::Update`` / ``ResetPeriodic`` / ``GetTotalTicks`` / ``GetApplicationList``,
``Unit::RemoveOwnedAura`` and the aura loops of ``Unit::_UpdateSpells``.  ``FillTargetMap`` is the cut
point (driver membership).  Discriminated: the order duration -> map -> effects inside one owner update,
the map timer (``<= diff``, reset in UpdateTargetMap), shared per-effect ticks delivered to current
holders, tick caps, extra initial period, creation paths, and expiry after all owner updates.
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
from aura_lifecycle import recipients as R

PROBE_DIR = Path(__file__).resolve().parents[1] / "tools" / "tc_aura_area_probe"
PROBE = PROBE_DIR / "probe"


@pytest.fixture(scope="module")
def probe():
    if not (TC_ROOT / "src/server/game/Spells/Auras/SpellAuras.cpp").exists():
        pytest.skip("sibling TrinityCore checkout absent")
    if shutil.which("g++") is None and not PROBE.exists():
        pytest.skip("no compiler")
    subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True, capture_output=True)
    return PROBE


def _script(fx: dict) -> str:
    (a,) = fx["auras"]
    if a["owner"] != "o" or a["kind"] != "unit" or fx.get("events"):
        raise ValueError("probe runs one unit aura owned by 'o' without events")
    creation = {"spell-hit": 0, "add-aura": 1}[a.get("creation", "spell-hit")]
    extra = any(e.get("extra_initial_period") for e in a["effects"])
    lines = [f"world {fx['tick_ms']} {fx['until']}",
             f"aura {a['spell']} {a['max_duration']} {int(bool(a.get('passive')))} {int(extra)} {creation} {a.get('static_mask', 0)}"]
    for e in a["effects"]:
        periodic = int(R.is_periodic_aura(int(e["aura"])))
        lines.append(f"effect {e['index']} {periodic} {e.get('period', 0)} {int(e.get('area', True))}")
    for unit, ivs in sorted(a.get("in_area", {}).items()):
        for lo, hi in ivs:
            lines.append(f"area {unit} {lo} {-1 if hi is None else hi}")
    lines.append("run")
    return "\n".join(lines) + "\n"


def _probe_events(probe, fx: dict) -> set:
    out = subprocess.run([str(probe)], input=_script(fx), capture_output=True, text=True, check=True).stdout
    events = json.loads(out)["events"]
    got = set()
    for e in events:
        if e["ev"] == "apply":
            got.add((e["t"], "apply", e["unit"], e["mask"], e["duration"]))
        elif e["ev"] == "unapply":
            got.add((e["t"], "unapply", e["unit"], e["mode"]))
        elif e["ev"] == "tick":
            got.add((e["t"], "tick", e["unit"], e["eff"], e["n"]))
        elif e["ev"] == "mask":
            got.add((e["t"], "mask", e["unit"], e["mask"]))
    return got


def _model_events(fx: dict) -> set:
    got = set()
    for e in R.timeline(fx):
        if e["event"] == "apply":
            got.add((e["t"], "apply", e["unit"], sum(1 << i for i in e["mask"]), e["duration"]))
        elif e["event"] == "unapply":
            got.add((e["t"], "unapply", e["unit"], e["mode"]))
        elif e["event"] == "tick":
            for u in e["recipients"]:
                got.add((e["t"], "tick", u, e["effect"], e["tick"]))
        elif e["event"] == "mask-update":
            got.add((e["t"], "mask", e["unit"], sum(1 << i for i in e["mask"])))
    return got


@pytest.mark.parametrize("sc", [s for s in R.SCENARIOS if s["probe"]], ids=lambda s: s["id"])
def test_canonical_scenarios_match_verbatim_trinity(probe, sc):
    assert _model_events(sc["fixture"]) == _probe_events(probe, sc["fixture"])


_interval = st.tuples(st.integers(0, 40), st.one_of(st.none(), st.integers(1, 40))).map(
    lambda p: [p[0] * 100 + 50 * (p[0] % 2), None if p[1] is None else (p[0] + p[1]) * 100 + 30])


@settings(max_examples=150, deadline=None, database=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(tick=st.sampled_from([50, 100, 133, 250, 400]),
       max_duration=st.sampled_from([-1, 1000, 2500, 3000, 4000]),
       period=st.sampled_from([0, 300, 1000, 1500]),
       aura=st.sampled_from([3, 8, 22, 226]),
       extra=st.booleans(), passive=st.booleans(),
       creation=st.sampled_from(["spell-hit", "add-aura"]),
       static=st.booleans(),
       members=st.dictionaries(st.sampled_from(["o", "p2", "p3", "p4"]), st.lists(_interval, min_size=1, max_size=2), max_size=4))
def test_random_worlds_match_verbatim_trinity(probe, tick, max_duration, period, aura, extra, passive, creation, static, members):
    """Discriminates any reordering of duration / map / effects, timer off-by-one (< vs <=), tick caps."""
    effects = [{"index": 0, "aura": aura, "period": period, "extra_initial_period": extra}]
    if static:
        effects.append({"index": 1, "aura": 22, "period": 0, "area": False})
    ivs = {u: sorted(v, key=lambda x: x[0]) for u, v in members.items()}
    fx = {"tick_ms": tick, "until": 5000, "units": {u: {} for u in ["o", "p2", "p3", "p4"]}, "update_order": ["o"],
          "auras": [{"id": "A", "spell": 1, "kind": "unit", "owner": "o", "caster": "o", "max_duration": max_duration,
                     "created_at": 0, "creation": creation, "passive": passive, "static_mask": 2 if static else 0,
                     "effects": effects, "in_area": ivs}], "events": []}
    assert _model_events(fx) == _probe_events(probe, fx)
