"""Track E: world-database target policy and the minimal condition evaluator."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from targeting import CORPORA, FailClosed
from targeting import world as W


def row(ctype=21, target=0, v1=0x400, negative=0, else_group=0, source=17, error=0, script=""):
    return {"SourceTypeOrReferenceId": source, "SourceGroup": 0, "SourceEntry": 36554, "SourceId": 0,
            "ElseGroup": else_group, "ConditionTypeOrReference": ctype, "ConditionTarget": target,
            "ConditionValue1": v1, "ConditionValue2": 0, "ConditionValue3": 0, "NegativeCondition": negative,
            "ErrorType": error, "ScriptName": script}


ROOTED = {"is_unit": True, "unit_state": 0x400}
FREE = {"is_unit": True, "unit_state": 0x8}
GO = {"is_unit": False}


def test_unit_state_meets():
    assert W.meets(row(), [ROOTED]) is True
    assert W.meets(row(), [FREE]) is False
    assert W.meets(row(negative=1), [FREE]) is True
    assert W.meets(row(), [GO]) is False            # ToUnit() null -> false
    assert W.meets(row(negative=1), [GO]) is True


def test_missing_object_is_false_before_negation():
    """ConditionMgr.cpp:281-287; rules out 'negate the missing-object result' (dummy_semantics.conditions)."""
    assert W.meets(row(target=1, negative=1), [ROOTED, None]) is False
    assert W.meets(row(ctype=0, target=1, negative=1), [ROOTED, None]) is False   # NONE needs no object; negated


def test_unsupported_and_scripted_rows_fail_closed():
    with pytest.raises(FailClosed):
        W.meets(row(ctype=1), [ROOTED])
    with pytest.raises(FailClosed):
        W.meets(row(script="cond_script"), [ROOTED])
    with pytest.raises(FailClosed):
        W.meets_list([row(source=-5)], [ROOTED])
    with pytest.raises(FailClosed):
        W.meets(row(target=3), [ROOTED])


def test_else_groups_and_last_failed():
    rows = [row(negative=1, else_group=0), row(v1=0x8, else_group=1)]
    assert W.meets_list(rows, [ROOTED])[0] is False
    assert W.meets_list(rows, [FREE])[0] is True
    ok, failed = W.meets_list([row(negative=1), row(v1=0x8)], [ROOTED])
    assert ok is False and failed["NegativeCondition"] == 1      # group stops after the first failure
    assert W.meets_list([], [None]) == (True, None)


def test_shadowstep_cast_gate():
    """Spell.cpp:5917-5933: ErrorType wins; 103 is SPELL_FAILED_NO_ENDURANCE in the pinned enum."""
    r = row(negative=1, error=103)
    assert W.cast_check([r], FREE, None) == "SPELL_CAST_OK"
    assert W.cast_check([r], ROOTED, None) == "SPELL_FAILED_NO_ENDURANCE"
    assert W.cast_check([row(negative=1)], ROOTED, None) == "SPELL_FAILED_CASTER_AURASTATE"
    assert W.cast_check([row(target=1)], FREE, FREE) == "SPELL_FAILED_BAD_TARGETS"
    # missing explicit object: Meets returns false without setting mLastFailedCondition -> CASTER_AURASTATE
    assert W.cast_check([row(target=1)], FREE, None) == "SPELL_FAILED_CASTER_AURASTATE"
    with pytest.raises(FailClosed):
        W.cast_check([row(negative=1, error=214)], ROOTED, None)


def test_load_validity_unit_state():
    assert W.load_valid(row(v1=0x400))
    assert not W.load_valid(row(v1=0x2000))          # ISOLATED_DEPRECATED is not supported -> row skipped
    assert W.cast_check([row(v1=0x2000)], FREE, None) == "SPELL_CAST_OK"


def test_implicit_target_check_uses_candidate_then_caster():
    assert W.implicit_target_check([row(source=13, target=0)], ROOTED, FREE) is True
    assert W.implicit_target_check([row(source=13, target=1)], ROOTED, FREE) is False


def test_searcher_type_mask():
    """ConditionMgr.cpp:983-1019."""
    assert W.searcher_type_mask([]) == W.GRID_MAP_TYPE_MASK_ALL
    cp = W.GRID_MAP_TYPE_MASK_CREATURE | W.GRID_MAP_TYPE_MASK_PLAYER
    assert W.searcher_type_mask([row()]) == cp
    assert W.searcher_type_mask([row(negative=1)]) == W.GRID_MAP_TYPE_MASK_ALL
    assert W.searcher_type_mask([row(), row(ctype=0, else_group=1)]) == W.GRID_MAP_TYPE_MASK_ALL
    with pytest.raises(FailClosed):
        W.searcher_type_mask([row(ctype=29)])


def E(i, effect=6, a=0, b=0, chain=0):
    return SimpleNamespace(index=i, effect=effect, target_a=a, target_b=b, chain_targets=chain)


def test_source_group_validation_strips_single_target_effects():
    """ConditionMgr.cpp:1851-1924: area/nearby/cone/traj/line, chain or area-aura effects keep their bit."""
    effects = [E(0, a=6), E(1, a=16), E(2, a=6, chain=3), E(3, effect=35, a=1), E(4, a=1), E(5, a=1, b=2)]
    assert W.valid_implicit_target_group(effects, 0b111111) == 0b101110
    assert W.valid_implicit_target_group(effects, 0b010001) == 0
    assert W.valid_implicit_target_group(effects, 0) == 0


def test_implicit_target_lists_share_and_reject_overlaps():
    """ConditionMgr.cpp:1575-1682."""
    effects = [E(0, a=16), E(1, a=16), E(2, a=16)]
    r1 = dict(row(source=13), _group=0b011)
    r2 = dict(row(source=13, v1=0x8), _group=0b011)
    r3 = dict(row(source=13, v1=0x20), _group=0b001)   # overlaps the {0,1} list with a different mask -> ignored
    lists = W.implicit_target_lists(effects, [r1, r2, r3])
    assert lists[0] is lists[1] and 2 not in lists
    assert [x["ConditionValue1"] for x in lists[0]] == [0x400, 0x8]
    r4 = dict(row(source=13), _group=0b100)
    lists = W.implicit_target_lists(effects, [r1, r4])
    assert lists[0] is lists[1] and lists[2] is not lists[0]


def test_real_policy_census(tg_ctx):
    by_source = W._conditions_by_source(tg_ctx)
    tiers = W._tiers(tg_ctx)
    scoped = tiers.reach | tiers.controlled_unit | tiers.script_reach
    assert not [r for r in by_source[W.SOURCE_IMPLICIT_TARGET] if int(r["SourceEntry"]) in scoped]
    p = W.spell_policy(tg_ctx, 36554, tiers, by_source)
    assert p["cast_conditions"]["rows"][0]["value1_name"] == "UNIT_STATE_ROOT"
    assert p["cast_conditions"]["rows"][0]["error_type"] == 103
    p = W.spell_policy(tg_ctx, 451485, tiers, by_source)
    assert p["world_selectors"][0]["spell_target_position_rows"] == 0


def test_committed_world_corpus_matches_generator(tg_ctx):
    path = CORPORA / "world-policy.json"
    if not path.exists():
        pytest.skip("corpus not generated")
    committed = json.loads(path.read_text())
    fresh = json.loads(json.dumps(W.corpus(tg_ctx, committed["provenance"]["command"]), sort_keys=True))
    assert fresh["totals"] == committed["totals"]
    assert fresh["effect_classes"] == committed["effect_classes"]
    assert committed["totals"]["dummy_conditions_spellclick_false_positives"] == [46598, 408907]
