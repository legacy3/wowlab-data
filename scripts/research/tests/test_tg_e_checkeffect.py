"""Track E: SpellScript::TargetHook::CheckEffect port and target-hook dispatch rules."""

from __future__ import annotations

import re

import pytest

from dummy_semantics.bindings import (TARGET_HOOK_FLAGS, TOTAL_SPELL_TARGETS, TargetHookUndecidable, _TC_SELECTOR_DATA,
                                      target_hook_check_effect, target_hook_equality_only)
from targeting import TC_ROOT, FailClosed
from targeting.adapters import HookRef, check_area_target, hook_fires, script_allows_grouping

AREA = TARGET_HOOK_FLAGS["OnObjectAreaTargetSelect"]
OBJ = TARGET_HOOK_FLAGS["OnObjectTargetSelect"]
DEST = TARGET_HOOK_FLAGS["OnDestinationTargetSelect"]


def test_hook_flags_match_constructors():
    """SpellScript.h:523/561/599: (area, dest) = (true,false) / (false,false) / (false,true)."""
    assert AREA == (True, False) and OBJ == (False, False) and DEST == (False, True)


def test_private_selector_table_matches_pinned_source():
    path = TC_ROOT / "src/server/game/Spells/SpellInfo.cpp"
    if not path.exists():
        pytest.skip("sibling TrinityCore not present")
    lines = path.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if "SpellImplicitTargetInfo::_data =" in l) + 2
    rows = []
    for l in lines[start:start + TOTAL_SPELL_TARGETS]:
        m = re.match(r"\s*\{TARGET_OBJECT_TYPE_(\w+),\s*TARGET_REFERENCE_TYPE_(\w+),\s*TARGET_SELECT_CATEGORY_(\w+),", l)
        assert m, l
        rows.append((m.group(3), m.group(2), m.group(1)))
    assert rows == [_TC_SELECTOR_DATA[i] for i in range(TOTAL_SPELL_TARGETS)]


def test_private_selector_table_matches_track_a():
    """Differential against targeting.selectors (track A) -- request: switch bindings to it."""
    selectors = pytest.importorskip("targeting.selectors")
    for t in range(TOTAL_SPELL_TARGETS):
        info = selectors.info(t)
        assert (info.category, info.reference, info.object) == _TC_SELECTOR_DATA[t], t


@pytest.mark.parametrize("target,flags,expected", [
    (1, AREA, False),    # TARGET_UNIT_CASTER (DEFAULT/CASTER): single -> !area
    (1, OBJ, True),
    (1, DEST, True),     # !area holds for a dest hook too (SpellScript.cpp:239-240)
    (6, AREA, True),     # TARGET_UNIT_TARGET_ENEMY (DEFAULT/TARGET): both
    (18, OBJ, False),    # TARGET_DEST_CASTER: DEST -> dest only
    (18, DEST, True),
    (18, AREA, False),
    (22, DEST, False),   # TARGET_SRC_CASTER: SRC -> never
    (15, AREA, True),    # TARGET_UNIT_SRC_AREA_ENEMY
    (15, OBJ, False),
    (15, DEST, False),
    (116, DEST, True),   # TARGET_UNIT_AND_DEST_LAST_ENEMY: area || dest
    (116, OBJ, False),
    (2, AREA, True), (2, OBJ, True), (2, DEST, True),   # NEARBY: both
    (77, AREA, False), (77, OBJ, True), (76, DEST, True),  # CHANNEL: !area
    (24, AREA, True), (24, OBJ, False),                  # CONE
    (134, AREA, True), (134, DEST, False),               # LINE
    (89, AREA, False), (89, DEST, False),                # TRAJ: default -> false
    (126, AREA, False),                                  # NYI
    (0, OBJ, False),                                     # target 0 never matches (SpellScript.cpp:205)
])
def test_category_rule(target, flags, expected):
    """Rules out the pre-fix 'TargetA/TargetB equality only' model wherever expected is False."""
    assert target_hook_check_effect(target, *flags, target, 0) is expected
    if target:
        assert target_hook_equality_only(target, target, 0) is True


def test_equality_prerequisite_and_size_check():
    assert target_hook_check_effect(15, *AREA, 16, 0) is False           # neither A nor B
    assert target_hook_check_effect(15, *AREA, 1, 15) is True            # B matches
    assert target_hook_check_effect(15, *AREA, None, None) is False      # index >= GetEffects().size()
    assert target_hook_check_effect(15, *AREA, 0, 0) is False            # blank (gap) effect
    with pytest.raises(TargetHookUndecidable):
        target_hook_check_effect(200, *AREA, 200, 0)


def test_no_bound_hook_changes_mask_under_category_rule(tg_ctx):
    """Erratum invariant: over every 12.1 bound spell the category rule and the equality
    approximation give the same masks (so the Dummy counts did not change)."""
    from dummy_semantics.bindings import BindingMap
    bm = BindingMap(tg_ctx.bundle)
    changed = [(s, h.list, h.handler) for s, lst in bm.by_spell.items() for b in lst for h in b.hooks
               if h.note and h.note.startswith("target-hook-category-rule")]
    assert changed == []
    assert not any(h.note and "approximate" in h.note for lst in bm.by_spell.values() for b in lst for h in b.hooks)


def test_hook_fires_uses_group_first_effect_and_processed_selector():
    """Spell.cpp:8996: IsEffectAffected(first effect of group) && processed selector == hook target."""
    assert hook_fires(0b10, 16, 1, 16)
    assert not hook_fires(0b10, 16, 0, 16)       # group led by effect 0: effect-1-only hook does not run
    assert not hook_fires(0b10, 16, 1, 15)       # TargetA hook does not run while TargetB (other selector) is processed


def test_script_grouping_rule():
    """Spell.cpp:9066-9097; discriminates 'same handler name' from 'same function'."""
    havoc = [HookRef("OnObjectTargetSelect", "PreventEffect<Havoc>", 0b0011),
             HookRef("OnObjectTargetSelect", "PreventEffect<Devourer>", 0b1100)]
    assert script_allows_grouping([havoc], 0, 1)
    assert script_allows_grouping([havoc], 2, 3)
    assert not script_allows_grouping([havoc], 0, 2)
    # one-sided hook: effect 0 hooked, effect 1 not -> no grouping (both directions checked)
    assert not script_allows_grouping([[HookRef("OnObjectAreaTargetSelect", "f", 0b01)]], 0, 1)
    assert not script_allows_grouping([[HookRef("OnObjectAreaTargetSelect", "f", 0b10)]], 0, 1)
    # destination hooks are not compared
    assert script_allows_grouping([[HookRef("OnDestinationTargetSelect", "g", 0b01)]], 0, 1)
    # target type is not compared: same function registered for both indices groups
    assert script_allows_grouping([[HookRef("OnObjectAreaTargetSelect", "f", 0b01),
                                    HookRef("OnObjectAreaTargetSelect", "f", 0b10)]], 0, 1)
    # two registrations of one STATIC function: HasSameTargetFunctionAs is indeterminate (TD-E-24)
    tb = [HookRef("OnObjectTargetSelect", "g", 0b010, static=True, registration=1),
          HookRef("OnObjectTargetSelect", "g", 0b100, static=True, registration=2)]
    with pytest.raises(FailClosed):
        script_allows_grouping([tb], 1, 2)
    # one static registration covering both effects compares with itself: deterministic
    assert script_allows_grouping([[HookRef("OnObjectTargetSelect", "g", 0b110, static=True, registration=1)]], 1, 2)
    # a second script can veto
    assert not script_allows_grouping([[], [HookRef("OnObjectTargetSelect", "h", 0b01)]], 0, 1)


def test_check_area_target_is_and_without_short_circuit():
    """SpellAuras.cpp:2067: result &= hook(target) for every hook."""
    calls = []

    def gen():
        for r in (False, True):
            calls.append(r)
            yield r
    assert check_area_target(gen()) is False and calls == [False, True]
    assert check_area_target([]) is True
