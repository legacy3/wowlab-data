"""Track I: lifecycle numeric boundaries (arithmetic reproductions + 69497 census)."""

from __future__ import annotations

import pytest

from aura_lifecycle import FailClosed, numeric


def test_hasted_period_binary32_not_exact_division() -> None:
    """int32(3000 * f32(1/1.3)) = 2307 (the exact quotient is 2307.69; a rounding model would give 2308)."""
    assert numeric.hasted_period(3000, 1 / 1.3) == 2307


def test_duration_above_2p24_changes_at_factor_one() -> None:
    """Rules out 'DurationMul 1.0 is an identity': 16777217 is not representable in binary32."""
    assert numeric.int_times_float(16777216, 1.0) == 16777216
    assert numeric.int_times_float(16777217, 1.0) == 16777216


def test_pct130_binary32() -> None:
    assert numeric.calculate_pct_int(10000, 130) == 13000
    assert numeric.calculate_pct_int(18000, 130) == 23400


def test_duration_decrement_floor() -> None:
    assert numeric.aura_duration_after(150, 100) == 50
    assert numeric.aura_duration_after(50, 100) == 0
    assert numeric.aura_duration_after(-1, 100) == -1           # permanent untouched
    assert numeric.aura_duration_after(0, 100) == 0


def test_total_ticks_floor_and_extra_initial() -> None:
    assert numeric.total_ticks(7000, 2000, extra_initial=False) == 3
    assert numeric.total_ticks(4000, 1000, extra_initial=True) == 5
    assert numeric.total_ticks(-1, 1000, extra_initial=False) == 0
    with pytest.raises(FailClosed):
        numeric.total_ticks(-5, 1000, extra_initial=False)


def test_zero_period_outcomes() -> None:
    assert numeric.hasted_period(1, 0.5) == 0
    assert numeric.zero_period_outcome(permanent=False) == "never-ticks"
    assert numeric.zero_period_outcome(permanent=True) == "unbounded-loop"


def test_stack_uint8_wrap() -> None:
    """Confirms AL-D-C-01: capacity 300, 255 + 1 stores 0."""
    r = numeric.stack_after_mod(255, 1, 300, 300)
    assert r["int32"] == 256 and r["stored"] == 0 and r["wrapped"]
    assert numeric.stack_after_mod(3, 1, 0, 0)["stored"] == 1           # non-stacking -> 1
    assert numeric.stack_after_mod(1, -1, 5, 5)["removed"]
    assert numeric.charges_stored(999999) == 63


def test_periodic_cost_timer_stall() -> None:
    """AL-D-I-05: diff == m_timeCla + 1000 stores 0 and the branch never charges again."""
    t, charged = numeric.periodic_cost_timer(500, 1500)
    assert (t, charged) == (0, True)
    assert numeric.periodic_cost_timer(0, 100) == (0, False)
    assert numeric.periodic_cost_timer(1000, 1000) == (1000, True)     # exact 1 s cadence, no drift
    assert numeric.periodic_cost_timer(300, 2000) == (-700, True)      # negative: charge every update


def test_tick_cap_loss_threshold() -> None:
    """13000 / 3000: remainder 1000, so phases >= 2000 lose a tick."""
    assert not numeric.tick_cap_loss(13000, 3000, 1999)
    assert numeric.tick_cap_loss(13000, 3000, 2000)
    assert numeric.tick_cap_loss(13000, 3000, 2500)
    assert not numeric.tick_cap_loss(12000, 3000, 2999)


def test_periodic_aura_set_matches_trinity_switch() -> None:
    from aura_lifecycle import TC_ROOT
    src = TC_ROOT / "src/server/game/Spells/Auras/SpellAuraEffects.cpp"
    if not src.exists():
        pytest.skip("sibling TrinityCore checkout absent")
    text = src.read_text(encoding="utf-8")
    body = text[text.index("void AuraEffect::CalculatePeriodic("):]
    body = body[:body.index("m_isPeriodic = true;")]
    import re
    names = tuple(re.findall(r"case SPELL_AURA_([A-Z_]+):", body))
    assert names == numeric.PERIODIC_AURA_NAMES


@pytest.fixture(scope="module")
def census(al_ctx):
    from aura_lifecycle import providers
    pops = providers.populations(al_ctx, with_class_skills=True)
    rows = numeric.census(al_ctx.data, pops, providers.provider_effects(al_ctx.data), al_ctx.name, al_ctx.is_skew)
    return {r["id"]: r for r in rows}


def test_census_shape(census) -> None:
    assert sorted(census) == [f"NB-I-{n:02d}" for n in range(1, 11)]
    for r in census.values():
        assert set(r["counts"]) == {"all", "player", "controlled", "player+class-skills"}
        assert r["counts"]["player"] <= r["counts"]["player+class-skills"] <= r["counts"]["all"]


def test_census_values(census) -> None:
    """Snapshot 69497 facts: no current-player >255 stacks or >2^24 durations; hasted periods never reach 0."""
    assert census["NB-I-01"]["counts"]["player"] == 0 and census["NB-I-01"]["counts"]["all"] > 0
    assert max(census["NB-I-01"]["authored_values"]) == 65000
    assert census["NB-I-03"]["counts"]["player"] == 0
    assert census["NB-I-03"]["max_finite_authored_ms"] == 2 ** 31 - 1
    assert census["NB-I-06"]["counts"]["all"] == 0
    assert census["NB-I-08"]["counts"]["player"] > 0


def test_tick_cap_witness_agony(census) -> None:
    """Agony 980 (non-CP): hit = M = 18000, newMax = min(36000, 23400) = 23400 -> remainder 1400, phases >= 600 lose a tick."""
    rows = [r for r in census["NB-I-08"]["rows"] if r["spell"] == 980]
    assert rows and rows[0]["player"]
    case = rows[0]["cases"][0]
    assert (case["cp"], case["new_max"], case["phase_threshold"]) == (None, 23400, 600)


def test_tick_cap_combo_point_uses_min_plus_hit(census) -> None:
    """R2-06: Rupture 1943 is evaluated per CP with newMax = min(hit + M, trunc(1.3*hit)), not 1.3 x 4000."""
    rows = [r for r in census["NB-I-08"]["rows"] if r["spell"] == 1943]
    for r in rows:
        assert all(c["cp"] in (1, 2, 3, 4, 5) and c["new_max"] != 5200 for c in r["cases"])


def test_tick_cap_excludes_channels(census) -> None:
    """R2-06: self-channels (e.g. Tranquility 740) are cancel-then-create on recast and are not counted."""
    nb = census["NB-I-08"]
    assert 740 in nb["channel_player_spells"]
    assert all(r["spell"] != 740 for r in nb["rows"])
    assert any("haste 1.0" in a for a in nb["assumptions"])
