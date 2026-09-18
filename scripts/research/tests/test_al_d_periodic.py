"""Track D: periodic timer lifecycle -- synthetic discriminating tests and real-data checks.

Synthetic tests replay exact ms timelines through ``aura_lifecycle.periodic.run`` (the Trinity mirror);
each docstring names the competing model it rules out.  The verbatim-Trinity differential lives in
``test_al_d_probe.py``.
"""

from __future__ import annotations

import pytest

from aura_lifecycle import FailClosed
from aura_lifecycle import periodic as P

DOT = {"base_period": 3000, "base_duration": 12000}


def ticks(spell, events, every=100, until=25000, **kw):
    return P.run({"spell": spell, "update_every": every, "until": until, "events": events, **kw})["tick_times"]


def test_final_tick_fires_at_expiry():
    """Rules out 'expiry removal precedes the occurrence due in the same update'."""
    assert ticks(DOT, [{"t": 0, "op": "create"}]) == [3000, 6000, 9000, 12000]


def test_extra_initial_period_ticks_at_first_owner_update_not_at_application():
    """Rules out 'raw 169 occurrence happens inside the application call' and 'no budget increase'."""
    t = ticks(dict(DOT, attrs={"SPELL_ATTR5_EXTRA_INITIAL_PERIOD": True}), [{"t": 0, "op": "create"}], every=250)
    assert t == [250, 3000, 6000, 9000, 12000]


@pytest.mark.parametrize("flags,expected", [
    (0, [100, 3000, 6000, 7100, 10000, 13000, 16000, 19000]),
    (P.TRIGGERED_FULL_MASK, [100, 3000, 6000, 9000, 12000, 15000, 18000]),
    (P.TRIGGERED_DONT_RESET_PERIODIC_TIMER, [100, 3000, 6000, 9000, 12000, 15000, 18000]),
])
def test_unk_e_002_extra_occurrence_depends_on_reapplying_cast(flags, expected):
    """UNK-E-002: rules out both 'raw 169 never re-ticks on reapply' (untriggered restart re-ticks at 7100)
    and 'raw 169 always re-ticks' (fully triggered reapply preserves)."""
    spell = dict(DOT, attrs={"SPELL_ATTR5_EXTRA_INITIAL_PERIOD": True})
    assert ticks(spell, [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply", "trigger_flags": flags}]) == expected


def test_unk_e_001_subtype_23_cadence_is_not_a_spell_property():
    """UNK-E-001: the same periodic-trigger spell restarts (untriggered) or preserves (fully triggered)."""
    spell = dict(DOT, aura_type=P.aura("PERIODIC_TRIGGER_SPELL"))
    restart = ticks(spell, [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply"}])
    keep = ticks(spell, [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply", "trigger_flags": P.TRIGGERED_FULL_MASK}])
    assert restart[2] == 10000 and keep[2] == 9000


@pytest.mark.parametrize("attrs,stack", [({"SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION": True}, 0), ({}, 2), ({}, 5)])
def test_preserve_paths(attrs, stack):
    """Rules out 'untriggered reapply always restarts': pandemic and StackAmount>=2 keep the phase."""
    t = ticks(dict(DOT, attrs=attrs, stack_amount=stack), [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply"}])
    assert 9000 in t and 10000 not in t


def test_pandemic_duration_is_130_percent_regardless_of_remaining():
    """AL-D-D-02: rules out 'base + min(remaining, 30%)' -- 1950 ms remaining still yields 15600."""
    res = P.run({"spell": dict(DOT, attrs={"SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION": True}),
                 "update_every": 100, "until": 11000, "events": [{"t": 0, "op": "create"}, {"t": 10050, "op": "reapply"}]})
    row = next(r for r in res["rows"] if r["event"] == "reapply")
    assert row["state"]["max_duration"] == 15600 and row["state"]["effects"][0]["timer"] == 1000


def test_pandemic_budget_drops_last_boundary():
    """AL-D-D-04: rules out 'every boundary inside the new duration ticks' -- the 24000 occurrence is dropped."""
    t = ticks(dict(DOT, attrs={"SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION": True}),
              [{"t": 0, "op": "create"}, {"t": 8950, "op": "reapply"}], until=26000)
    assert t[-1] == 21000 and 24000 not in t


def test_late_updates_burst_and_cap():
    """Rules out one-occurrence-per-update and unbounded catch-up."""
    burst = P.run({"spell": DOT, "updates": [1000, 8000, 13000], "events": [{"t": 0, "op": "create"}]})["tick_times"]
    assert burst == [8000, 8000, 13000, 13000]
    capped = P.run({"spell": DOT, "updates": [60000], "events": [{"t": 0, "op": "create"}]})["tick_times"]
    assert capped == [60000] * 4


def test_early_credit_between_owner_updates():
    """AL-D-D-03: rules out 'the timer starts at application' (created at 50 ms, first occurrence at 3000)."""
    assert ticks(DOT, [{"t": 50, "op": "create"}], until=13000) == [3000, 6000, 9000, 12000]


@pytest.mark.parametrize("phase,expected", [("before", [3000]), ("after", [3000, 6000])])
def test_removal_same_ms_follows_processing_order(phase, expected):
    """Rules out both 'due occurrence always first' and 'removal always first'."""
    assert ticks(DOT, [{"t": 0, "op": "create"}, {"t": 6000, "op": "remove", "phase": phase}], until=13000) == expected


@pytest.mark.parametrize("phase,expected", [("before", [3000, 8900, 11900, 14900, 17900]),
                                             ("after", [3000, 6000, 9000, 12000, 15000, 18000])])
def test_unk_e_003_cast_vs_due_occurrence(phase, expected):
    """UNK-E-003: owner==caster (cast in WorldObject::Update, 'before') drops the due occurrence; a caster updated
    after the owner ('after') observes it first.  Rules out a fixed caster-independent rank."""
    assert ticks(DOT, [{"t": 0, "op": "create"}, {"t": 6000, "op": "reapply", "phase": phase}], until=20000) == expected


def test_haste_change_waits_for_refresh():
    """Rules out 'haste rescales the pending period immediately'."""
    spell = dict(DOT, attrs={"SPELL_ATTR5_SPELL_HASTE_AFFECTS_PERIODIC": True})
    t = ticks(spell, [{"t": 0, "op": "create"}, {"t": 4000, "op": "haste", "cast_speed": 0.8},
                      {"t": 7050, "op": "reapply"}])
    assert t[:3] == [3000, 6000, 9400]


def test_uncovered_tail_has_no_partial_occurrence():
    """Rules out the simc partial last tick."""
    assert ticks({"base_period": 3000, "base_duration": 10000}, [{"t": 0, "op": "create"}], until=11000) == [3000, 6000, 9000]


def test_permanent_periodic_never_expires():
    t = P.run({"spell": {"base_period": 1000, "base_duration": -1}, "update_every": 500, "until": 5000,
               "events": [{"t": 0, "op": "create"}]})
    assert t["tick_times"] == [1000, 2000, 3000, 4000, 5000] and t["final"]["removed"] is None


def test_weapon_percent_damage_never_ticks():
    """AL-D-D-01: rules out 'every aura type PeriodicTick dispatches is periodic'."""
    spell = dict(DOT, aura_type=P.aura("PERIODIC_WEAPON_PERCENT_DAMAGE"))
    assert ticks(spell, [{"t": 0, "op": "create"}]) == []


def test_period_arithmetic_is_binary32_truncation():
    """int32(_period * float) with the product rounded to binary32: 3000*f32(0.7) = 2099.99996 in double but 2100.0f.
    Rules out a double-precision model (which would truncate to 2099); the probe confirms the float path."""
    attrs = [0] * 17
    w, b = P.A5_SPELL_HASTE
    attrs[w] |= b
    assert P.calc_period(3000, attrs, cast_speed=0.7) == 2100
    assert P.calc_period(3000, attrs, cast_speed=0.769) == 2307
    assert int(P.f32(3000.0) * P.f32(0.7)) == 2099          # the ruled-out double model


def test_channel_melee_haste_uses_cast_speed():
    """Rules out 'raw 278 on a channel reads melee haste' (Object.cpp:1838)."""
    attrs = [0] * 17
    for key in (P.A1_CHANNEL, P.A8_MELEE_HASTE):
        attrs[key[0]] |= key[1]
    assert P.haste_mode(attrs) == "channel-cast-speed"
    assert P.calc_period(1000, attrs, cast_speed=0.5, melee_haste=0.9) == 500


def test_refresh_cadence_policy_names_runtime_input():
    c = P.refresh_cadence([0] * 17, 0, None)
    assert c["untriggered"] == "restart" and c["fully_triggered"].startswith("preserve")


def test_bad_schedule_fails_closed():
    with pytest.raises(FailClosed):
        P.run({"spell": DOT, "updates": [100, 100], "events": []})


# --------------------------------------------------------------------------- real data

def test_real_profiles(al_ctx):
    swp = P.profile(al_ctx, 589)
    e = next(x for x in swp["effects"] if x["trinity_periodic"])
    assert (e["base_period"], e["haste_mode"], e["authored_ticks"]) == (2000, "spell-haste", 8)
    assert swp["refresh_cadence"]["untriggered"] == "preserve (pandemic)"
    rejuv = P.profile(al_ctx, 774)
    assert rejuv["attributes"]["raw169_EXTRA_INITIAL_PERIOD"] and rejuv["attributes"]["raw436_PERIODIC_REFRESH_EXTENDS_DURATION"]
    fr = P.profile(al_ctx, 22842)
    assert any(x["compute_points_only_at_cast"] for x in fr["effects"])


def test_census_invariants(al_ctx):
    c = P.census(al_ctx)["counts"]
    for pop in ("all", "player"):
        n = c[pop]
        assert n["trinity_periodic"]["effects"] == sum(v["effects"] for k, v in n.items() if k.startswith("haste:"))
        assert n["trinity_periodic"]["effects"] == sum(v["effects"] for k, v in n.items() if k.startswith("cadence:"))
    assert c["all"]["weapon_percent_damage_never_ticks"]["effects"] > 0
    assert "weapon_percent_damage_never_ticks" not in c["player"]
    assert c["player"]["unk_e_001_subtype23_24_by_trigger_flags"]["effects"] > 0


@pytest.mark.parametrize("path,extra,expected_third", [
    ("spell-hit", {}, 9000), ("add-aura", {}, 10000), ("modify-stacks", {"value": 1}, 10000)])
def test_cadence_depends_on_delivery_path(path, extra, expected_third):
    """R1-05/R2-04: rules out 'StackAmount>=2 keeps the phase' as a spell-level rule -- only the Spell-hit path
    computes reset=false; AddAura and effect 289 pass the default reset=true."""
    t = ticks(dict(DOT, stack_amount=2), [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply", "path": path, **extra}])
    assert t[2] == expected_third


def test_effect_289_value_zero_is_full_refresh():
    res = P.run({"spell": dict(DOT, stack_amount=5), "update_every": 100, "until": 8000,
                 "events": [{"t": 0, "op": "create"}, {"t": 7050, "op": "reapply", "path": "modify-stacks", "value": 0}]})
    row = next(r for r in res["rows"] if r["event"] == "reapply")
    assert row["state"]["stack"] == 1 and row["state"]["duration"] == 12000 and row["state"]["effects"][0]["timer"] == 0


def test_script_set_duration_extension_is_silent():
    """R2-05 / AL-D-D-05: rules out 'extended duration ticks' (budget from unchanged MaxDuration)."""
    res = P.run({"spell": {"base_period": 2000, "base_duration": 16000}, "update_every": 100, "until": 21000,
                 "events": [{"t": 0, "op": "create"}, {"t": 10000, "op": "set_duration", "delta": 4000}]})
    assert res["tick_times"][-1] == 16000 and len(res["tick_times"]) == 8
    assert [r["t"] for r in res["rows"] if r.get("state", {}).get("removed") == "expire"][0] == 20000


def test_script_set_max_duration_collapses_budget():
    """R2-05 / AL-D-D-06: rules out 'extension keeps the remaining occurrences'."""
    res = P.run({"spell": {"base_period": 2000, "base_duration": 16000}, "update_every": 100, "until": 18000,
                 "events": [{"t": 0, "op": "create"}, {"t": 10000, "op": "set_max_duration", "delta": 1000},
                            {"t": 10000, "op": "set_duration", "delta": 1000}]})
    assert res["tick_times"] == [2000, 4000, 6000, 8000]


def test_modify_stacks_census(al_ctx):
    s = P.modify_stacks_rows(al_ctx)["summary"]
    assert s["all"]["effect_289_rows"] == 201 and s["all"]["adds_stacks_to_stacking_periodic_aura"] == 18
    assert s["player"]["adds_stacks_to_stacking_periodic_aura"] == 0
