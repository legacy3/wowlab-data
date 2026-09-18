"""Track D: snapshot-vs-dynamic periodic inputs (matrix well-formedness + discriminating amount timelines)."""

from __future__ import annotations

import pytest

from aura_lifecycle import FailClosed, INPUT_TIMING, POLICY_SOURCE
from aura_lifecycle import snapshot as S


def test_matrix_vocabulary_and_coords():
    fams = {r["family"] for r in S.MATRIX}
    assert set(S.FAMILIES) - {"power-drain"} <= fams | {"power-drain"}
    for r in S.MATRIX:
        assert r["trinity_timing"] in INPUT_TIMING and r["policy_source"] in POLICY_SOURCE
        assert r["coords"], r


def test_spell_power_gained_mid_dot_changes_later_ticks():
    """Rules out the full application snapshot (pre-WoD) model for Trinity."""
    res = S.tick_amounts(S.SNAPSHOT_SPECS[0]["scenario"])["ticks"]
    assert [t["trinity"] for t in res] == [600, 850, 850, 850]
    assert [t["full-snapshot"] for t in res] == [600, 600, 600, 600]


def test_pct_done_buff_expiry_mid_dot():
    """Rules out capture of %done at application."""
    res = S.tick_amounts(S.SNAPSHOT_SPECS[1]["scenario"])["ticks"]
    assert [t["trinity"] for t in res] == [660, 660, 600, 600]


def test_stack_scales_base_and_bonus():
    """Rules out stack-independent amounts: base*stack (recalculated on stack change) + SP*coeff*stack at tick."""
    res = S.tick_amounts(S.SNAPSHOT_SPECS[2]["scenario"])["ticks"]
    assert [t["trinity"] for t in res] == [600, 600, 1200, 1200, 1200, 1200]


def test_bonus_done_binary32():
    assert S.damage_bonus_done(100, spell_power=1000, coeff=0.1, stack=1) == 200
    assert S.damage_bonus_done(100, spell_power=0, coeff=0.5, stack=1, pct_done=1.15) == 115   # f32 product 115.0f


def test_caster_state_must_start_at_zero():
    sc = dict(S.SNAPSHOT_SPECS[0]["scenario"], caster_state=[{"t": 5, "spell_power": 1}])
    with pytest.raises(FailClosed):
        S.tick_amounts(sc)


def test_snapshot_command_real(al_ctx, capsys):
    import json
    from aura_lifecycle.cli import main
    assert main(["snapshot", "22842"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["effects"][0]["compute_points_only_at_cast"] is True
    assert main(["periodic", "589"]) == 0
