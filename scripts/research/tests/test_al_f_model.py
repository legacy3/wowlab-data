"""Track F: synthetic timelines of the area-aura recipient model (aura_lifecycle.recipients).

Each test pins one lifecycle behaviour and names the competing model it rules out.  No snapshot
is loaded: fixtures are explicit (recipient *selection* is an input; targeting owns it).
"""

from __future__ import annotations

import copy

import pytest

from aura_lifecycle import FailClosed
from aura_lifecycle import recipients as R


def _sc(prefix: str) -> dict:
    return next(s for s in R.SCENARIOS if s["id"].startswith(prefix))


def _run(prefix: str, **kw) -> dict:
    return R.run_scenario(_sc(prefix), **kw)


def test_late_joiner_gets_remaining_parent_duration():
    """Rules out: per-recipient duration started at application (p3 applied 3100, expires with the parent at 6000)."""
    r = _run("S01")
    assert r["intervals"]["A"]["p3"] == [[3100, 6000]]
    apply = next(e for e in r["log"] if e["event"] == "apply" and e["unit"] == "p3")
    assert apply["duration"] == 2900


def test_leave_is_quantised_to_the_500ms_map():
    """Rules out: continuous reevaluation.  p2 leaves at 1250, unapplied at the 1600 map update (BY_DEFAULT)."""
    r = _run("S01")
    assert r["intervals"]["A"]["p2"] == [[100, 1600]]
    assert {"t": 1600, "aura": "A", "unit": "p2", "mode": "BY_DEFAULT", "why": "left-target-map"} in r["removals"]


def test_shared_tick_reaches_current_holders_only():
    """Rules out: per-recipient periodic timer.  p3 joins 3100 and ticks at 4000; p2 (100-1600) never ticks;
    p4 left at 1950 but is still mapped at the 2000 tick."""
    r = _run("S01")
    assert r["ticks"]["A"] == {"o": [2000, 4000, 6000], "p3": [4000, 6000], "p4": [2000]}


def test_last_tick_precedes_expiry_removal():
    """Rules out: expiry removal before the duration-0 tick (UpdateOwner runs effects, _UpdateSpells removes after)."""
    log = _run("S01")["log"]
    t6000 = [e["event"] for e in log if e["t"] == 6000 and e["event"] in ("tick", "aura-removed")]
    assert t6000 == ["tick", "aura-removed"]


def test_extra_initial_period_ticks_everyone_mapped_in_the_first_update():
    """Rules out: effects updated before the target map (then the first tick would reach nobody)."""
    r = _run("S02")
    assert r["ticks"]["A"]["p2"][0] == 100 and r["ticks"]["A"]["o"][0] == 100
    assert len(r["ticks"]["A"]["o"]) == 4          # GetTotalTicks = 3000/1000 + 1


def test_permanent_periodic_is_uncapped():
    r = _run("S03")
    assert r["ticks"]["A"]["o"] == [1000, 2000, 3000, 4000]
    assert r["ticks"]["A"]["p2"] == [2000, 3000, 4000]


def test_add_aura_maps_at_creation_and_then_every_500ms():
    """Rules out: every unit aura first maps at its first owner update (AddAura calls ApplyForTargets)."""
    r = _run("S04")
    assert r["intervals"]["A"] == {"o": [[0, None]], "p2": [[500, None]]}


def test_static_effects_apply_at_hit_area_effects_at_first_map():
    r = _run("S05")
    log = r["log"]
    first = next(e for e in log if e["event"] == "apply" and e["unit"] == "o")
    assert first["t"] == 0 and first["mask"] == [1]
    upd = next(e for e in log if e["event"] == "mask-update")
    assert upd["t"] == 100 and upd["mask"] == [0, 1]


def test_owner_death_removes_non_persistent_parent_by_death():
    """Rules out: recipients keep it until the next map update."""
    r = _run("S06")
    assert [(x["t"], x["unit"], x["mode"]) for x in r["removals"]] == [(1000, "o", "DEATH"), (1000, "p2", "DEATH")]


def test_death_persistent_parent_survives_but_dead_owner_leaves_its_map():
    """Rules out: owner death removes the aura; and: the dead owner keeps its own application."""
    r = _run("S07")
    assert r["intervals"]["A"] == {"o": [[100, 1100], [3100, None]], "p2": [[100, None]]}


def test_disable_while_dead_empties_the_map_at_next_update():
    r = _run("S08")
    assert r["intervals"]["A"]["p2"] == [[100, 1100], [3100, None]]
    assert all(x["mode"] == "BY_DEFAULT" for x in r["removals"])


def test_passive_parent_keeps_serving_living_recipients_after_owner_death():
    r = _run("S09")
    assert r["intervals"]["A"]["p2"] == [[100, None]]


def test_resurrected_recipient_gets_new_application_with_remaining_duration():
    """Rules out: fresh duration after resurrection; recipient keeps the application while dead."""
    r = _run("S10")
    assert r["intervals"]["A"]["p2"] == [[100, 1000], [4100, None]]
    reapply = [e for e in r["log"] if e["event"] == "apply" and e["unit"] == "p2"][-1]
    assert reapply["duration"] == 10000 - 4100


def test_parent_refresh_resets_schedule_for_every_recipient():
    """Rules out: refresh touching only the owner's application."""
    r = _run("S11")
    assert r["ticks"]["A"]["o"] == r["ticks"]["A"]["p2"] == [2000, 4900, 6900, 8900]


def test_pandemic_refresh_keeps_the_tick_phase():
    sc = copy.deepcopy(_sc("S11"))
    sc["fixture"]["events"][0]["pandemic"] = True
    r = R.run_scenario(sc)
    assert r["ticks"]["A"]["p2"][:3] == [2000, 4000, 6000]


def test_recipient_cancel_and_dispel_are_ignored_owner_ones_remove_all():
    for prefix, mode in (("S12", "CANCEL"), ("S13", "ENEMY_SPELL")):
        r = _run(prefix)
        assert [(x["t"], x["mode"]) for x in r["removals"]] == [(2000, mode), (2000, mode)]


def test_dynobj_applies_at_cast_and_expires_by_default_mode():
    """Rules out: dynobj recipients waiting for the first update; BY_EXPIRE at dynobj expiry."""
    r = _run("S14")
    assert r["intervals"]["A"] == {"p1": [[0, 2000]]}
    assert r["removals"][0]["mode"] == "BY_DEFAULT"


def test_same_caster_dynobjs_do_not_stack_and_take_over_late():
    r = _run("S15")
    assert r["intervals"]["D1"]["p1"] == [[0, 2000]]
    assert r["intervals"]["D2"]["p1"] == [[2400, 2900]]


def test_two_owner_contest_depends_on_update_order():
    """Rules out: take-over latency independent of owner update order / newer aura replaces older."""
    sc = _sc("S16")
    a = R.run_scenario(sc, order=["o1", "o2"])
    b = R.run_scenario(sc, order=["o2", "o1"])
    assert a["intervals"]["A"]["p"] == [[100, 2100]] and a["intervals"]["B"]["p"] == [[2100, None]]
    assert "p" not in b["intervals"]["A"] and b["intervals"]["B"]["p"] == [[100, None]]


def test_owner_leave_world_strips_other_recipients_only():
    r = _run("S17")
    assert r["intervals"]["A"] == {"o": [[100, None]], "p2": [[100, 1000]]}


def test_areatrigger_auras_are_per_unit_and_exit_removes_by_caster():
    """Rules out: AT auras sharing one parent; leaving one of two same-caster ATs keeping the aura (AL-D-F-01)."""
    log = R.at_timeline(R.AT_SCENARIOS[0]["fixture"])
    applies = {e["unit"]: e["t"] for e in log if e["event"] == "apply"}
    assert applies == {"p1": 100, "p3": 100, "p2": 300}
    p3 = [e for e in log if e.get("unit") == "p3" and e["event"] == "unapply"]
    assert p3 and p3[0]["t"] == 1500 and p3[0]["aura_from_at"] == "at2"
    assert any(e["event"] == "expire" and e["unit"] == "p1" and e["t"] == 2100 for e in log)


def test_fail_closed_inputs():
    with pytest.raises(FailClosed):
        R.World({"tick_ms": 100, "until": 100, "units": {}, "update_order": [],
                 "auras": [{"id": "A", "kind": "areatrigger"}]})
    with pytest.raises(FailClosed):
        R.World({"tick_ms": 100, "until": 100, "units": {}, "update_order": [],
                 "auras": [{"id": "A", "kind": "unit", "highest_exclusive": True}]})
    sc = copy.deepcopy(_sc("S06"))
    sc["fixture"]["events"].append({"t": 1200, "kind": "refresh", "aura": "A", "duration": 1000})
    with pytest.raises(FailClosed):
        R.run_scenario(sc)


def test_effect_pipeline_classification():
    assert R.effect_pipeline(65, 87) == "unit-area"
    assert R.effect_pipeline(27, 3) == "dynobj"
    assert R.effect_pipeline(27, 0) is None
    assert R.effect_pipeline(179, 0) == "areatrigger"
    assert R.effect_pipeline(6, 395) == "areatrigger"
    assert R.effect_pipeline(6, 8) == "static"
    assert R.OWNER_RECEIVES[129] == "no" and R.OWNER_RECEIVES[119] == "always"
