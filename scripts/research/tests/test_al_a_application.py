"""Track A -- initial application (application.py): ordering timelines + real witnesses."""

from __future__ import annotations

import json

import pytest

from aura_lifecycle import CORPORA
from aura_lifecycle.application import partial_failures, plan, recipient_groups, same_cast_ordering, timeline


def _index(ev, needle: str) -> int:
    return next(e["n"] for e in ev if needle in e["event"])


def test_debuff_before_damage_index_still_applies_after_damage():
    """Rules out 'effects resolve fully in index order' (aura effect 0 active before damage effect 1)."""
    ev = timeline([{"index": 0, "kind": "aura"}, {"index": 1, "kind": "damage"}])
    assert _index(ev, "damage computed") < _index(ev, "DealSpellDamage") < _index(ev, "HandleEffect(REAL) effect 0")
    created = _index(ev, "new object")
    assert created < _index(ev, "damage computed")          # the object already exists when damage is computed ...
    assert ev[_index(ev, "damage computed")]["applied_effects"] == []   # ... but none of its effects is active


def test_procs_see_no_applied_aura_effects():
    ev = timeline([{"index": 0, "kind": "damage"}, {"index": 1, "kind": "aura"}])
    proc = ev[_index(ev, "ProcSkillsAndAuras")]
    assert proc["application"] and proc["applied_effects"] == []


def test_refresh_applies_nothing_new():
    ev = timeline([{"index": 0, "kind": "aura"}, {"index": 1, "kind": "damage"}], existing=True)
    assert not any("HandleEffect(REAL)" in e["event"] for e in ev)
    assert any("refresh existing object" in e["event"] for e in ev)


def test_dead_target_object_without_application():
    """Rules out 'aura creation implies an application'."""
    ev = timeline([{"index": 0, "kind": "aura"}], dead_target=True)
    assert ev[-1]["object"] and not ev[-1]["application"]
    ev2 = timeline([{"index": 0, "kind": "aura"}], dead_target=True, death_persistent=True)
    assert ev2[-1]["application"] and ev2[-1]["applied_effects"] == [0]


def test_immune_effect_not_applied():
    ev = timeline([{"index": 0, "kind": "aura"}, {"index": 1, "kind": "aura"}], immune=frozenset({1}))
    assert ev[-1]["applied_effects"] == [0]


def test_area_aura_only_has_no_hit_application():
    ev = timeline([{"index": 0, "kind": "area-aura"}])
    assert ev[-1]["object"] and not ev[-1]["application"]


@pytest.fixture(scope="module")
def pb(al_ctx):
    from aura_lifecycle.identity import PropsBuilder
    return PropsBuilder(al_ctx)


def test_haunt_ordering_witness(al_ctx, pb):
    p = pb(48181)
    assert 48181 in al_ctx.scope.reach
    o = same_cast_ordering(p)
    assert o["damage_before_aura_index"] == [0] and o["aura_created_at_effect"] == 1
    assert 1 in o["damage_taken_modifier_effects"] and o["same_cast_damage_sees_own_aura"] is False


def test_agony_initial_stacks_not_capacity(al_ctx, pb):
    from procs.definition import ProcEntryStore
    p = pb(980)
    out = plan(al_ctx, p, ProcEntryStore(al_ctx.catalog, al_ctx.bundle.proc_overlay))
    assert out["initial"]["stacks"]["value"] == 1 and out["initial"]["stacks"]["capacity_not_used"] == p.stack_amount > 1


def test_devotion_aura_mixed_area(al_ctx, pb):
    p = pb(465)
    out = plan(al_ctx, p)
    assert "area_note" in out and out["ordering"]["aura_created_at_effect"] == 1


def test_zero_aura_apply_effect_is_in_object(al_ctx, pb):
    p = pb(205022)
    assert any(e["aura_name_zero"] and e["aura_effect_object"] for e in plan(al_ctx, p)["effects"])
    assert any(f["case"] == "APPLY_AURA with EffectAura 0" for f in partial_failures(p))


def test_recipient_groups_single_target_spell(pb):
    assert recipient_groups(pb(48181)) == [{"targets": [6, 0], "effects": [1, 2, 3, 4], "create_at_effect": 1}]


def test_application_corpus_counts_consistent():
    path = CORPORA / "application.json"
    if not path.exists():
        pytest.skip("application.json not generated")
    c = json.loads(path.read_text(encoding="utf-8"))
    pl = c["counts"]["player"]
    assert pl["unit-aura spells"] + pl.get("dynobj-only", 0) + pl.get("no DIFFICULTY_NONE SpellInfo (fail-closed)", 0) == pl["spells"]
    assert pl["damage + own damage-taken modifier"] == len(c["player_damage_with_own_taken_modifier"])
    for w in c["witnesses"]:
        assert w["timeline_new"][0]["event"].startswith("PreprocessSpellHit")
