"""Track E: differential tests of the removal / death / dispel models against Trinity's compiled code.

The probe (tools/tc_aura_removal_probe) compiles verbatim ``Unit::RemoveAllAurasOnDeath``,
``RemoveOwnedAura``, ``_UnapplyAura``, ``Aura::_Remove``, ``Aura::Update``,
``AuraEffect::Update``, the ``_UpdateSpells`` aura loops, ``GetDispellableAuraList``,
``RemoveAurasDueToSpellByDispel``, ``ModStackAmount``/``ModCharges`` and the
``EffectDispel`` attempt loop, plus Trinity's ``urand``/``irand``/``rand_chance``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aura_lifecycle import TC_ROOT
from aura_lifecycle import dispel as D
from aura_lifecycle import lifetime as L

PROBE_DIR = Path(__file__).resolve().parents[1] / "tools" / "tc_aura_removal_probe"
GAME = TC_ROOT / "src" / "server" / "game"



@pytest.fixture(scope="session")
def probe() -> Path:
    if not (GAME / "Entities" / "Unit" / "Unit.cpp").is_file():
        pytest.skip("sibling TrinityCore checkout not present")
    if shutil.which("g++") is None:
        pytest.skip("g++ not available")
    subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True, capture_output=True)
    return PROBE_DIR / "probe"


def run(probe: Path, script: str) -> list[dict]:
    out = subprocess.run([str(probe)], check=True, capture_output=True, text=True, input=script).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


BASE = """unit v 1 1 1
unit c 1 1 2
unit ally 1 1 1
"""


def test_death_sweep_predicate_and_modes(probe: Path) -> None:
    """Rules out 'death removes everything' and 'recipients of the dying unit's area aura see DEFAULT'."""
    out = run(probe, BASE + """spell 100 0 0 0 0 0 0 0 1
spell 200 64 0 0 0 0 0 0 0
spell 300 0 0 0 1048576 0 0 0 1
spell 400 0 0 0 0 0 0 0 0
aura dot 100 v c 12000 12000 1 0 0 3000 0
aura pas 200 v v -1 -1 1 0 0 0 1
aura pers 300 v c 12000 12000 1 0 0 3000 0
aura area 400 v v 30000 30000 1 0 0 0 1
app area ally 1
death v
state
""")
    death = out[0]["log"]
    removed = [(e["aura"], e["target"], e["mode"]) for e in death if e["ev"] == "handle_effect"]
    assert removed == [("dot", "v", 6), ("area", "v", 6), ("area", "ally", 6)]
    # order inside one application: _Remove, then effect handlers, then specific mods (Unit.cpp:3648-3680)
    assert [e["ev"] for e in death[:3]] == ["app_remove", "handle_effect", "specific_mods"]
    state = {a["aura"]: a for a in out[1]["auras"]}
    assert not state["pas"]["removed"] and not state["pers"]["removed"]
    assert state["dot"]["removed"] and state["area"]["removed"]
    fx = L.DeathFixture(auras=[L.HeldAura("dot"), L.HeldAura("pas", passive=True), L.HeldAura("pers", death_persistent=True),
                               L.HeldAura("area", recipients=("ally",))])
    sweep = L.death_order(fx)[-1]
    assert [(r["aura"], r["mode"]) for r in sweep["removed"]] == [("dot", "DEATH"), ("area", "DEATH")]
    assert sweep["recipient_applications_removed_with_DEATH"] == {"area": ["ally"]}


def _timeline(probe: Path, updates: int, death_after: int | None) -> list[dict]:
    lines = [BASE, "spell 100 0 0 0 0 0 0 0 1\n", "aura dot 100 v c 12000 12000 1 0 0 3000 0\n"]
    for i in range(1, updates + 1):
        lines.append("update v 100\n")
        if death_after == i:
            lines.append("death v\n")
    lines.append("state\n")
    return run(probe, "".join(lines))


def test_expiry_vs_death_timeline_differential(probe: Path) -> None:
    """Rules out 'death == natural expiry': TL-E-01 (4 ticks, EXPIRE) vs TL-E-02 (3 ticks, DEATH), model and probe."""
    nat = _timeline(probe, 121, None)
    ticks = [e for c in nat for e in c["log"] if e.get("ev") == "tick"]
    modes = [e["mode"] for c in nat for e in c["log"] if e.get("ev") == "handle_effect"]
    assert len(ticks) == 4 and modes == [5]
    last = next(c for c in nat if any(e.get("ev") == "handle_effect" for e in c["log"]))["log"]
    assert [e["ev"] for e in last] == ["tick", "app_remove", "handle_effect", "specific_mods"]   # tick before sweep, same update
    died = _timeline(probe, 121, 119)
    ticks = [e for c in died for e in c["log"] if e.get("ev") == "tick"]
    modes = [e["mode"] for c in died for e in c["log"] if e.get("ev") == "handle_effect"]
    assert len(ticks) == 3 and modes == [6]
    tl = {t["id"]: t for t in L.standard_timelines()}
    assert (tl["TL-E-01"]["final"]["ticks_done"], tl["TL-E-01"]["final"]["removed"]) == (4, "EXPIRE")
    assert (tl["TL-E-02"]["final"]["ticks_done"], tl["TL-E-02"]["final"]["removed"]) == (3, "DEATH")


@pytest.mark.parametrize("diff", [100, 250, 700, 1000, 3000, 4000])
def test_tick_count_matches_model_for_update_granularity(probe: Path, diff: int) -> None:
    """The model's duration/tick/sweep order matches the verbatim bodies for coarse update steps too."""
    n = -(-12500 // diff)
    out = run(probe, BASE + "spell 100 0 0 0 0 0 0 0 1\naura dot 100 v c 12000 12000 1 0 0 3000 0\n" + "update v %d\n" % diff * n + "state\n")
    probe_ticks = [(i, e["n"]) for i, c in enumerate(out) for e in c["log"] if e.get("ev") == "tick"]
    a = L.SimAura(duration=12000, max_duration=12000, period=3000)
    tl = L.simulate(a, diff, diff * n)
    model = [(e["t"] // diff - 1, e["n"]) for e in tl.events if e["event"] == "tick"]
    assert probe_ticks == model
    assert tl.final["removed"] == "EXPIRE"


def test_death_persistent_aura_keeps_ticking_on_corpse(probe: Path) -> None:
    """TL-E-09: an ATTR3_ALLOW_AURA_WHILE_DEAD DoT survives, keeps counting ticks, expires with EXPIRE."""
    lines = [BASE, "spell 300 0 0 0 1048576 0 0 0 1\n", "aura pers 300 v c 12000 12000 1 0 0 3000 0\n"]
    for i in range(1, 121):
        lines.append("update v 100\n")
        if i == 40:
            lines.append("death v\n")
    out = run(probe, "".join(lines))
    ticks = [e for c in out for e in c["log"] if e.get("ev") == "tick"]
    assert [t["target_alive"] for t in ticks] == [True, False, False, False]
    assert [e["mode"] for c in out for e in c["log"] if e.get("ev") == "handle_effect"] == [5]


def test_caster_absent_ticks_continue(probe: Path) -> None:
    """TL-E-08: a despawned caster does not remove its DoT; later ticks see GetCaster() == nullptr."""
    lines = [BASE, "spell 100 0 0 0 0 0 0 0 1\n", "aura dot 100 v c 12000 12000 1 0 0 3000 0\n"]
    for i in range(1, 121):
        lines.append("update v 100\n")
        if i == 50:
            lines.append("setinworld c 0\n")
    out = run(probe, "".join(lines))
    ticks = [e["caster"] for c in out for e in c["log"] if e.get("ev") == "tick"]
    assert ticks == ["alive", "absent", "absent", "absent"]


def test_stack_dispel_keeps_timers(probe: Path) -> None:
    """Rules out 'a stack dispel refreshes the aura': no refresh_timers event, stack 3 -> 2 (AL-R-E-06)."""
    out = run(probe, BASE + "spell 100 0 0 0 0 0 0 5 1\naura dot 100 v c 12000 12000 3 0 0 3000 0\ndispel v 100 c 1\nstate\n")
    log = out[0]["log"]
    assert [e["ev"] for e in log] == ["on_dispel", "set_stack", "after_dispel"]
    model = D.apply_dispel(D.AuraState(D.DAura("dot", 100, "c", stacks=3, max_stacks=5), 3, 0), 1)
    assert model.stacks == 2 and not model.refreshed and model.log == ["OnDispel", "SetStackAmount(2)", "AfterDispel"]


def test_last_stack_dispel_removes_with_enemy_spell(probe: Path) -> None:
    out = run(probe, BASE + "spell 100 0 0 0 0 0 0 0 1\naura dot 100 v c 12000 12000 1 0 0 3000 0\ndispel v 100 c 1\n")
    log = out[0]["log"]
    assert log[0]["ev"] == "on_dispel" and log[-1]["ev"] == "after_dispel"
    assert [e["mode"] for e in log if e["ev"] == "handle_effect"] == [4]


def test_charge_dispel_without_charges_is_noop(probe: Path) -> None:
    """AL-D-E-04 shape: ATTR7 aura with 0 charges -> ModCharges does nothing and the aura is not a candidate."""
    out = run(probe, BASE + "unit d 1 1 2\nspell 500 0 0 0 0 0 1024 0 1\naura ch 500 v c 12000 12000 1 0 0 0 1\n"
                            "dispel v 500 c 1\ndispellist v d 2 0\n")
    assert [e["ev"] for e in out[0]["log"]] == ["on_dispel", "after_dispel"]
    assert out[1]["list"] == []
    s = D.apply_dispel(D.AuraState(D.DAura("ch", 500, "c", dispel_removes_charges=True), 1, 0), 1)
    assert s.removed is None
    assert D.dispellable_list([D.DAura("ch", 500, "c", positive=True, dispel_removes_charges=True)], 2, False) == []


@pytest.mark.parametrize("seed_words", [[0, 0, 0, 0, 0, 0], [0xFFFFFFFF, 0x80000000, 0x7FFFFFFF, 1, 0x40000000, 0xC0000000],
                                        [12345, 999999999, 3000000000, 42, 7, 4000000000]])
def test_dispel_loop_differential(probe: Path, seed_words: list[int]) -> None:
    """The Python attempt loop reproduces Trinity's given the same urand/irand results; each call costs one word."""
    script = BASE + """unit d 1 1 2
spell 10 0 0 0 0 0 0 0 1
spell 20 0 0 0 0 0 0 5 1
spell 30 0 1073741824 0 0 0 0 5 1
resist 10 40
aura a 10 v c 12000 12000 1 0 0 0 1
aura b 20 v c 12000 12000 4 0 0 0 1
aura s 30 v c 12000 12000 3 0 0 0 1
aura n 20 v c 12000 12000 1 0 0 0 0
words %d %s
dispelloop v d 2 3
""" % (len(seed_words), " ".join(map(str, seed_words)))
    out = run(probe, script)[0]
    calls = [e for e in out["log"] if "rng" in e]
    # one word per call except libstdc++'s rejection step (Lemire): a word w with (w * n) mod 2^32 < 2^32 mod n
    # is redrawn -- word 0 is always rejected for n = 100 (irand(0, 99)), never for n = 1 (urand(0, 0))
    assert all(c["words"] >= 1 for c in calls)
    assert all(c["words"] == 1 for c in calls if c["rng"] == "urand" and c["hi"] == 0)
    assert out["words"] == sum(c["words"] for c in calls)
    owned = [D.DAura("a", 10, "c", positive=True, resist_pct=40), D.DAura("b", 20, "c", positive=True, stacks=4, max_stacks=5),
             D.DAura("n", 20, "c", positive=False), D.DAura("s", 30, "c", positive=True, stacks=3, max_stacks=5, dispel_all_stacks=True)]
    cands = D.dispellable_list(owned, 2, target_friendly_to_dispeller=False)
    assert [(c.aura.key, c.charges) for c in cands] == [(x["aura"], x["charges"]) for x in out["list"]]
    rng = D.ScriptedRng(ints=[c["v"] for c in calls])
    res = D.effect_dispel(cands, 3, rng)
    assert [(k, n) for k, n in res.success] == [(x["aura"], x["charges"]) for x in out["success"]]
    assert res.failed_spells == out["failed"]
    assert [c[0] for c in rng.calls] == [c["rng"] for c in calls]


def test_absent_casters_collapse_success_groups(probe: Path) -> None:
    """AL-D-E-02: same spell, two different absent casters -> one success group with 2 charges."""
    script = BASE + """unit d 1 1 2
unit c2 1 1 2
spell 60 0 0 0 0 0 0 0 1
aura x1 60 v c 12000 12000 1 0 0 0 1
aura x2 60 v c2 12000 12000 1 0 0 0 1
setinworld c 0
setinworld c2 0
words 4 0 0 0 0
dispelloop v d 2 2
"""
    out = run(probe, script)[0]
    assert out["success"] == [{"aura": "x1", "charges": 2}]
    x = D.DAura("x1", 60, "c", positive=True, caster_present=False)
    y = D.DAura("x2", 60, "c2", positive=True, caster_present=False)
    calls = [e["v"] for e in out["log"] if "rng" in e]
    res = D.effect_dispel(D.dispellable_list([x, y], 2, False), 2, D.ScriptedRng(ints=calls))
    assert res.success == [("x1", 2)]


def test_rejected_word_is_redrawn(probe: Path) -> None:
    """irand(0, 99) rejects word 0 (Lemire threshold 2^32 mod 100 = 96) and draws again; urand(0, 0) never rejects."""
    out = run(probe, "unit v 1 1 1\nunit c 1 1 2\nunit d 1 1 2\nspell 10 0 0 0 0 0 0 0 1\n"
                     "aura a 10 v c 12000 12000 1 0 0 0 1\nwords 3 0 0 0\ndispelloop v d 2 1\n")[0]
    calls = [(c["rng"], c["words"]) for c in out["log"] if "rng" in c]
    assert calls[0] == ("urand", 1) and calls[1][0] == "irand" and calls[1][1] == 3


@pytest.mark.parametrize("kind,lo,hi", [("urand", 0, 0), ("urand", 0, 6), ("irand", 0, 99), ("roll_int", 100, 0), ("chance", 0, 0)])
def test_engine_words_per_call(probe: Path, kind: str, lo: int, hi: int) -> None:
    """Rules out 'a singleton urand / a 100 % roll consumes no draw' (Core's uniform_index) for Trinity on libstdc++."""
    out = run(probe, f"rngcount {kind} {lo} {hi} 500\n")[0]
    assert out["words"] == 500


def test_roll_chance_int_overload_is_irand(probe: Path) -> None:
    """The dispel roll is ``irand(0, 99)`` (int32 overload), not ``rand_chance`` (float) -- AL-F-E-10."""
    out = run(probe, BASE + "unit d 1 1 2\nspell 10 0 0 0 0 0 0 0 1\naura a 10 v c 12000 12000 1 0 0 0 1\n"
                            "dispelloop v d 2 1\n")[0]
    assert [c["rng"] for c in out["log"] if "rng" in c] == ["urand", "irand"]
    assert [c for c in out["log"] if c.get("rng") == "irand"][0]["hi"] == 99
