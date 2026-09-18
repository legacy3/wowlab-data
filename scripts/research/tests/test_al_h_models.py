"""Track H synthetic tests: pure mirrors of external lifecycle policy (no context load).

Each test names the wrong model it rules out.
"""

from __future__ import annotations

import pytest

from aura_lifecycle import FailClosed
from aura_lifecycle import overlays as ov

LINKS = {(ov.LINK_AURA, 100): [200, -300], (ov.LINK_REMOVE, 100): [400, -500]}


def test_linked_on_remove_cast_suppressed_by_death_but_removal_is_not():
    """Rules out 'death == natural expiry' for spell_linked_spell on-remove actions."""
    expire = ov.linked_actions(LINKS, 100, "remove", "AURA_REMOVE_BY_EXPIRE")
    death = ov.linked_actions(LINKS, 100, "remove", "AURA_REMOVE_BY_DEATH")
    assert {"action": "target-cast", "spell": 400, "original_caster": "aura caster", "line": 1427} in expire
    assert any(a["action"] == "suppressed-by-death" and a["spell"] == 400 for a in death)
    # negative on-remove target: removal happens for both modes
    for acts in (expire, death):
        assert any(a["action"] == "target-remove-auras-due-to-spell" and a["spell"] == 500 for a in acts)


def test_linked_aura_forwards_remove_mode_same_caster():
    """Rules out 'linked child is removed with a fixed mode / from every caster'."""
    acts = ov.linked_actions(LINKS, 100, "remove", "AURA_REMOVE_BY_CANCEL")
    child = [a for a in acts if a["action"] == "target-remove-aura"]
    assert child == [{"action": "target-remove-aura", "spell": 200, "same_caster_only": True,
                      "remove_mode": "AURA_REMOVE_BY_CANCEL", "line": 1439}]
    assert any(a["action"] == "remove-immunity" and a["spell"] == 300 for a in acts)


def test_linked_aura_apply_needs_caster_and_keeps_own_duration():
    """Rules out 'linked child always co-applied with parent's duration'."""
    with_caster = ov.linked_actions(LINKS, 100, "apply")
    assert {"action": "caster-add-aura", "spell": 200, "own_duration": True, "line": 1413} in with_caster
    no_caster = ov.linked_actions(LINKS, 100, "apply", caster_present=False)
    assert any(a["action"] == "skipped-no-caster" and a["spell"] == 200 for a in no_caster)
    assert any(a["action"] == "apply-immunity" and a["spell"] == 300 for a in no_caster)


def test_linked_stack_change_syncs_child_stacks_only():
    """Rules out 'stack change re-applies or re-casts linked auras'."""
    acts = ov.linked_actions(LINKS, 100, "stack-change", parent_stacks=3)
    assert acts == [{"action": "sync-stack-amount", "spell": 200, "to": 3, "same_caster_only": True, "line": 1451}]


def test_linked_unknown_inputs_fail_closed():
    with pytest.raises(FailClosed):
        ov.linked_actions(LINKS, 100, "expire")
    with pytest.raises(FailClosed):
        ov.linked_actions(LINKS, 100, "remove", "AURA_REMOVE_BY_MAGIC")


@pytest.mark.parametrize("token,mask", [
    ("AURA_EFFECT_HANDLE_REAL", 0x1),
    ("AURA_EFFECT_HANDLE_REAL_OR_REAPPLY_MASK", 0x9),
    ("AuraEffectHandleModes(AURA_EFFECT_HANDLE_REAL | AURA_EFFECT_HANDLE_REAPPLY)", 0x9),
    ("AURA_EFFECT_HANDLE_CHANGE_AMOUNT_MASK", 0x5),
])
def test_parse_handle_mode(token, mask):
    assert ov.parse_handle_mode(token) == mask


def test_parse_handle_mode_unknown_fails_closed():
    with pytest.raises(FailClosed):
        ov.parse_handle_mode("AURA_EFFECT_HANDLE_WHATEVER")


def test_refresh_runs_only_reapply_registered_handlers():
    """Rules out both 'refresh runs no script apply/remove handler' and 'refresh runs all of them'."""
    real, real_reapply, amount_mask = 0x1, 0x9, 0x5
    assert ov.apply_remove_hook_fires(real, "real")
    assert not ov.apply_remove_hook_fires(real, "stack-or-refresh", amount_changed=True)
    assert ov.apply_remove_hook_fires(real_reapply, "stack-or-refresh")
    # CHANGE_AMOUNT-registered handler runs on refresh only when the recomputed amount differs
    assert not ov.apply_remove_hook_fires(amount_mask, "stack-or-refresh", amount_changed=False)
    assert ov.apply_remove_hook_fires(amount_mask, "stack-or-refresh", amount_changed=True)
    assert not ov.apply_remove_hook_fires(amount_mask, "amount-change", amount_changed=False)


def test_stack_change_runs_remove_half_of_specific_mods():
    """Reproduces AL-D-H-01; rules out 'only the linked stack sync runs on a stack change'."""
    remove_half = ov.specific_mods_blocks(apply=False, on_reapply=True)
    assert "family_remove" in remove_half and "creature_ai_on_aura_removed" in remove_half
    assert "spell_area" in remove_half
    assert "linked_remove" not in remove_half and "linked_stack_sync" not in remove_half
    assert "linked_stack_sync" in ov.specific_mods_blocks(apply=True, on_reapply=True)
    assert "linked_remove" in ov.specific_mods_blocks(apply=False, on_reapply=False)


def test_stack_change_sequence_order():
    """All remove halves (per effect) precede apply halves; REAL-only remove handler absent."""
    seq = ov.stack_change_sequence([{"index": 0, "hooks": [("AfterEffectApply", 0x9), ("AfterEffectRemove", 0x1)]},
                                    {"index": 1, "hooks": [("OnEffectRemove", 0x9)]}])
    assert seq[0].startswith("specific_mods(remove-half)")
    assert seq[-1].startswith("specific_mods(apply-half)")
    assert "eff0:AfterEffectRemove" not in seq
    assert seq.index("eff0:remove:default_handler") < seq.index("eff0:apply:default_handler") < seq.index("eff0:AfterEffectApply")
    assert seq.index("eff0:AfterEffectApply") < seq.index("eff1:OnEffectRemove")


@pytest.mark.parametrize("line,expected", [
    (1465, ("application", "refresh")), (1500, ("removal", "refresh")), (1547, ("removal", "refresh")),
    (1559, ("application", "removal", "refresh")),
])
def test_specific_mods_site_surfaces(line, expected):
    assert ov.specific_mods_site_surfaces(line) == expected


def test_specific_mods_site_outside_blocks_fails_closed():
    with pytest.raises(FailClosed):
        ov.specific_mods_site_surfaces(1380)


def test_spell_group_rules():
    """Nested common group is skipped; first nonzero rule wins; groups 5..1000 ignored by the loader."""
    rows = [(1001, 10), (1001, 20), (1001, -1002), (1002, 10), (1002, 20), (1003, 10), (1003, 30), (7, 10), (7, 30)]
    members = ov.group_members(rows)
    assert 7 not in members and members[1001] == {10, 20}
    groups_of = {}
    for g, ss in members.items():
        for s in ss:
            groups_of.setdefault(s, set()).add(g)
    nested = {1001: {1002}}
    rules = {1001: 1, 1002: 0, 1003: 2}
    # 10/20 share 1001 but also its nested 1002 -> 1001 skipped; 1002 has rule 0 -> DEFAULT
    assert ov.group_stack_rule(groups_of, members, rules, nested, 10, 20) == "DEFAULT"
    assert ov.group_stack_rule(groups_of, members, rules, nested, 10, 30) == "EXCLUSIVE_FROM_SAME_CASTER"


def test_classify_policy_sources():
    """Rules out 'any external touch replaces the default' (augment -> combined)."""
    assert ov.classify("duration", []) == "db2"
    assert ov.classify("persistence", []) == "trinity-default"
    rep = ov.touch("charges", "spell_proc", "replace", "x", "a:1", "trinity-consumer")
    aug = ov.touch("charges", "aura-script", "augment", "y", "b:2", "script-consumer")
    unk = ov.touch("charges", "script-unresolved", "augment", "z", "c:3", "unresolved")
    assert ov.classify("charges", [rep]) == "world-overlay"
    assert ov.classify("charges", [rep, aug]) == "combined"
    assert ov.classify("charges", [rep, unk]) == "unknown"
