"""Track D witness proposals evaluate end-to-end through the oracle pipeline (track G)."""

from __future__ import annotations

import pytest

from targeting.fixture import World


@pytest.fixture(scope="module")
def witnesses(tg_ctx):
    from targeting import cmd_d
    return {w["spell"]: w for w in cmd_d.witnesses()}


def test_witnesses_are_current_player_and_evaluated(witnesses, tg_ctx):
    assert set(witnesses) == {1064, 188443, 48438, 50286}
    for spell, w in witnesses.items():
        assert spell in tg_ctx.scope.reach
        assert w["pipeline"]["status"] == "evaluated", w["pipeline"]
        assert w["pipeline"]["cast_result"] == "SPELL_CAST_OK"


def test_witness_expectations(witnesses):
    """Chain heal: deficit order (not %); chain damage: previous-target reference; smart heal: injured first."""
    assert witnesses[1064]["expected_recipients"]["0"] == ["p", "a", "b", "c"]
    assert witnesses[188443]["expected_recipients"]["0"] == ["P", "X", "Y"]
    wg = witnesses[48438]
    assert wg["expected_recipients"]["0"][:3] == ["h1", "h2", "o"]
    assert witnesses[50286]["expected_recipients"]["0"] == ["e2", "e3"]


def test_smart_checks_reproduce(witnesses):
    from targeting import rng, smart
    wg = witnesses[48438]["fixture"]
    chk = wg["smart_check"]
    w = World.from_dict({**wg, "rng": {"draws": chk["hook_draws"]}})
    got = smart.select_random_injured_targets(w, chk["input"], chk["max_targets"], chk["prioritize_players"], chk["group_of"])
    assert got == chk["output"] == wg["script_results"]["OnObjectAreaTargetSelect:0:31"]["result"]
    sf = witnesses[50286]["fixture"]
    chk = sf["smart_check"]
    w = World.from_dict({**sf, "rng": {"draws": chk["hook_draws"]}})
    assert rng.random_resize(w, chk["input"], chk["n"]) == chk["output"]
