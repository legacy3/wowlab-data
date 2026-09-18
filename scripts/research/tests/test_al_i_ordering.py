"""Track I: same-timestamp ordering (model, probe differential, scheduler anchors)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aura_lifecycle import TC_ROOT, ordering
from aura_lifecycle.ordering import REMOVE_DEATH, REMOVE_EXPIRE, SCENARIOS, simulate

PROBE_DIR = Path(__file__).resolve().parents[1] / "tools" / "tc_aura_order_probe"


@pytest.fixture(scope="module")
def probe() -> Path:
    if not (TC_ROOT / "src" / "server" / "game").is_dir():
        pytest.skip("sibling TrinityCore checkout absent")
    if shutil.which("g++") is None:
        pytest.skip("no g++")
    subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True, capture_output=True)
    return PROBE_DIR / "probe"


def _probe(probe: Path, script: str) -> dict:
    out = subprocess.run([str(probe)], input=script + "\n", capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _ev(result: dict, ev: str, aura: str | None = None) -> list[dict]:
    return [e for e in result["log"] if e["ev"] == ev and (aura is None or e["aura"] == aura)]


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_model_matches_verbatim_trinity(probe: Path, name: str) -> None:
    """Every timeline in the corpus is Trinity's own _UpdateSpells / UpdateOwner / AuraEffect::Update /
    EventProcessor / ModStackAmount text; rules out any drift between the Python mirror and the source."""
    script = SCENARIOS[name]["script"]
    assert simulate(script) == _probe(probe, script)


def test_scheduler_anchors_resolve() -> None:
    if not (TC_ROOT / "src" / "server" / "game").is_dir():
        pytest.skip("sibling TrinityCore checkout absent")
    steps = ordering.verify_anchors(TC_ROOT)
    assert len(steps) == len(ordering.SCHEDULER)
    orders = [s["order"] for s in steps]
    assert orders == sorted(orders)
    coords = {s["step"].split(":")[0]: s["coords"] for s in steps}
    assert coords["_UpdateSpells"].startswith("Unit.cpp:")


def test_tick_fires_before_expiry_removal() -> None:
    """Rules out 'expiry first': the third tick (6000 ms) precedes the expire removal at the same timestamp."""
    r = simulate(SCENARIOS["tick-vs-expiry"]["script"])
    ticks = _ev(r, "tick")
    removal = _ev(r, "unapply")
    assert [t["t"] for t in ticks] == [2000, 4000, 6000]
    assert removal[0]["t"] == 6000 and removal[0]["mode"] == REMOVE_EXPIRE
    assert r["log"].index(ticks[-1]) < r["log"].index(removal[0])


def test_catch_up_ticks_share_the_update_timestamp() -> None:
    """Rules out 'one tick per update' and 'ticks clipped by remaining duration'."""
    r = simulate(SCENARIOS["catch-up-large-diff"]["script"])
    assert [t["t"] for t in _ev(r, "tick")] == [5000, 5000, 10000]


def test_no_partial_final_tick() -> None:
    """7000 ms / 2000 ms: three ticks, none at expiry (rules out a partial-tick model)."""
    r = simulate(SCENARIOS["non-multiple-duration"]["script"])
    assert [t["t"] for t in _ev(r, "tick")] == [2000, 4000, 6000]
    assert _ev(r, "unapply")[0]["t"] == 7000


def test_update_order_changes_effective_lifetime() -> None:
    """Same 1000 ms aura at diff 400: packet-phase creation expires one diff earlier than a creation made by a
    unit updated after the owner (rules out an update-order-independent lifetime)."""
    early = simulate(SCENARIOS["session-creation-decrement"]["script"])
    late = simulate(SCENARIOS["late-owner-creation"]["script"])
    assert _ev(early, "create")[0]["t"] == _ev(late, "create")[0]["t"] == 400
    assert _ev(early, "unapply")[0]["t"] == 1200
    assert _ev(late, "unapply")[0]["t"] == 1600


def test_melee_sees_auras_of_same_tick() -> None:
    """The player's swing point follows its own aura update: the buff created by a creature after the player's update
    is absent at 400 and present at 800; expired at 1600 before the swing."""
    r = simulate(SCENARIOS["late-owner-creation"]["script"])
    swings = {e["t"]: e["auras"] for e in _ev(r, "swing-point")}
    assert swings == {400: [], 800: ["B"], 1200: ["B"], 1600: []}


def test_same_pass_creation_depends_on_advanced_iterator() -> None:
    """Rules out 'larger SpellId than the ticking aura => updated in the same pass': B(200) created by A(100)'s tick is
    not decremented when A was the last aura, but B(400) is when C(300) follows A."""
    before = simulate(SCENARIOS["trigger-insert-before-next"]["script"])
    after = simulate(SCENARIOS["trigger-insert-after-next"]["script"])
    assert before["auras"]["B"]["state"]["duration"] == 2500
    assert after["auras"]["B"]["state"]["duration"] == 2000


def test_death_order_follows_spell_id() -> None:
    """Rules out both 'all same-ms ticks resolve before death' and 'death cancels every same-ms tick'."""
    low = simulate(SCENARIOS["death-lower-id-kills"]["script"])
    high = simulate(SCENARIOS["death-higher-id-kills"]["script"])
    assert [t["aura"] for t in _ev(low, "tick")] == ["A"]
    assert [t["aura"] for t in _ev(high, "tick")] == ["A", "B"]
    assert all(u["mode"] == REMOVE_DEATH for u in _ev(low, "unapply"))


def test_refresh_vs_due_tick_is_update_order() -> None:
    """A resetting refresh delivered before the owner's update swallows the tick due at 2000; delivered by a later
    unit, the tick fires first (rules out an aura-intrinsic same-ms order)."""
    before = simulate(SCENARIOS["refresh-before-due-tick"]["script"])
    after = simulate(SCENARIOS["refresh-after-due-tick"]["script"])
    assert [t["t"] for t in _ev(before, "tick")] == [3900]
    assert [t["t"] for t in _ev(after, "tick")][:2] == [2000, 4000]


def test_pandemic_commit_reads_refreshed_duration() -> None:
    """Confirms AL-D-B-01 / AL-D-D-02 with the probe's verbatim text: 2000 ms remaining -> 13000, not 12000."""
    r = simulate(SCENARIOS["pandemic-refresh-reads-refreshed-duration"]["script"])
    assert _ev(r, "refresh")[0]["duration"] == 13000


def test_unique_pandemic_keeps_ticks_done_and_starves() -> None:
    """AL-D-I-03: unique branch carries the remaining 2000 ms (12000) but keeps _ticksDone=4, so only two more ticks
    fire in the 12000 ms and the last 8000 ms are silent."""
    r = simulate(SCENARIOS["pandemic-unique-keeps-remaining"]["script"])
    ref = _ev(r, "refresh")[0]
    assert ref["duration"] == 12000 and ref["effects"] == [[0, 4]]
    ticks = [t["t"] for t in _ev(r, "tick")]
    assert ticks[-2:] == [10000, 12000]
    assert _ev(r, "unapply")[0]["t"] == 20000


def test_phase_keeping_refresh_tick_bound() -> None:
    """Rules out floor((newMax + phase) / period): 4 of 5 timer-due ticks."""
    r = simulate(SCENARIOS["pandemic-tick-cap"]["script"])
    assert [t["t"] for t in _ev(r, "tick")] == [3000, 6000, 9000, 12000]
    assert _ev(r, "unapply")[0]["t"] == 15500


def test_extra_initial_period_ticks_at_first_owner_update() -> None:
    r = simulate(SCENARIOS["extra-initial-period"]["script"])
    assert [t["t"] for t in _ev(r, "tick")] == [100, 1000, 2000, 3000, 4000]


def test_hasted_period_is_binary32_product() -> None:
    """int32(3000 * 0.7692308f) = 2307, five ticks in 12000 (rules out the exact rational 3000/1.3 = 2307.69)."""
    r = simulate(SCENARIOS["hasted-period-truncation"]["script"])
    assert r["auras"]["A"]["state"]["effects"][0]["period"] == 2307
    assert len(_ev(r, "tick")) == 5


def test_pairs_reference_existing_scenarios() -> None:
    ids = [p["id"] for p in ordering.PAIRS]
    assert len(ids) == len(set(ids))
    for p in ordering.PAIRS:
        assert p["scenario"] is None or p["scenario"] in SCENARIOS
        assert p["order"] in ("tick-first", "all-ticks-first", "depends", "spellid-order", "auras-first",
                              "swing-first", "cast-first", "map-first", "drop-first", "fifo", "proven")
        assert p["path"]
