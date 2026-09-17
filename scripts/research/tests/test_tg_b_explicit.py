"""Track B: staged explicit-target validation (cast eligibility / recipient / application kept apart)."""

from __future__ import annotations

import copy

import pytest

from targeting import FailClosed
from targeting import explicit as X
from targeting.fixture import World
from targeting.trace import Trace

from test_tg_b_relations import FACTIONS, TEMPLATES, actor

ENEMY = X.TARGET_FLAG["UNIT_ENEMY"]
ALLY = X.TARGET_FLAG["UNIT_ALLY"]
UNIT = X.TARGET_FLAG["UNIT"]

BOLT = {  # a magic single-target nuke with separate hostile/friendly range columns
    "id": 1, "attributes": [], "is_positive": False, "is_affecting_area": False,
    "is_allowing_dead_target": False, "is_passive": False,
    "explicit_target_mask": ENEMY, "required_explicit_target_mask": ENEMY,
    "dmg_class": 1, "target_creature_type": 0, "family": 0, "category": 0, "mechanic": 0,
    "aura_restrictions": None, "range": {"flags": 0, "min": [0, 0], "max": [30, 40]},
    "facing_caster_flags": 1, "effects": [{"index": 0, "effect": 2, "aura": 0, "explicit": True}],
    "cast_time_ms": 2000, "has_hit_delay": True, "is_next_melee_swing": False, "resurrect": False,
    "has_only_damage_effects": True, "los_disabled": False,
}


def fixture(spell_view, target_tpl=14, dist=20.0, target_facts=None, target_kw=None, **extra):
    tf = {"moving": False, "immune_effects": [], "immune_to_spell": False, "damage_immune": False,
          "hit_roll": "SPELL_MISS_NONE", "ignore_los_on_me": False}
    tf.update(target_facts or {})
    data = {
        "schema": "targeting-fixture/1", "name": "x",
        "spell": {"can_reflect": False, **extra.pop("spell", {})},
        "caster": "p",
        "actors": [
            actor("p", "player", 1, group=None, pos=[0, 0, 0], orientation=0.0, combat_reach=1.5,
                  bounding_radius=0.4, facts={"moving": False}),
            actor("t", "creature", target_tpl, [], pos=[dist, 0, 0], orientation=3.14159, combat_reach=1.5,
                  bounding_radius=1.0, facts=tf, **(target_kw or {})),
        ],
        "explicit": {"unit": "t"},
        "los": {"default": "clear"},
        "modifiers": {"range": {"flat": 0.0, "pct": 1.0}},
        "faction_templates": copy.deepcopy(TEMPLATES), "factions": copy.deepcopy(FACTIONS),
    }
    for k, v in extra.items():
        data[k] = v
    return World.from_dict(data), dict(spell_view)


def test_happy_path_all_stages():
    w, s = fixture(BOLT)
    v = X.validate(w, s)
    assert v["init"] == "t"
    assert v["cast_prepare"]["result"] == X.OK and v["cast_complete"]["result"] == X.OK
    assert v["redirect"] == "t"
    assert v["recipient"]["effect_mask"] == 1
    assert v["hit"]["applied"]


def test_neutral_dummy_uses_friendly_range_column():
    """Rules out 'harmful spells use the hostile range column': GetSpellMinMaxRangeForTarget keys on
    !IsHostileTo (Object.cpp:1674), so a NEUTRAL training dummy (template 7) at 35 yd is in range
    (friendly max 40) while a HOSTILE boss (template 14) at the same distance is not (max 30)."""
    w, s = fixture(BOLT, target_tpl=7, dist=35.0)
    t = Trace()
    assert X.check_range(w, s, "p", "t", True, t) == X.OK
    assert "1674:range-column-friendly" in t.stages[-1].notes
    w, s = fixture(BOLT, target_tpl=14, dist=35.0)
    assert X.check_range(w, s, "p", "t", True) == "SPELL_FAILED_OUT_OF_RANGE"


def test_range_includes_combat_reach_and_tolerance_only_after_cast():
    """Max range gains both combat reaches (Spell.cpp:7365); the non-strict completion check adds
    min(3, 10%) (7284) -- a target at 33.5 yd fails at prepare and passes at completion."""
    w, s = fixture(BOLT, dist=32.9)
    assert X.check_range(w, s, "p", "t", True) == X.OK          # 30 + 1.5 + 1.5 = 33
    w, s = fixture(BOLT, dist=35.5)
    assert X.check_range(w, s, "p", "t", True) == "SPELL_FAILED_OUT_OF_RANGE"
    assert X.check_range(w, s, "p", "t", False) == X.OK          # 33 + min(3, 3.3) = 36
    w, s = fixture(BOLT, dist=36.5)
    assert X.check_range(w, s, "p", "t", False) == "SPELL_FAILED_OUT_OF_RANGE"


def test_instant_skips_completion_range():
    s = dict(BOLT, cast_time_ms=0)
    w, _ = fixture(s, dist=100.0)
    assert X.check_range(w, s, "p", "t", False) == X.OK
    assert X.check_range(w, s, "p", "t", True) == "SPELL_FAILED_OUT_OF_RANGE"


def test_facing_flag():
    w, s = fixture(BOLT, dist=-20.0)
    assert X.check_range(w, s, "p", "t", True) == "SPELL_FAILED_UNIT_NOT_INFRONT"
    assert X.check_range(w, dict(s, facing_caster_flags=0), "p", "t", True) == X.OK


def test_melee_range_flag_ignores_range_entry_values():
    """SPELL_RANGE_MELEE: GetMinMaxRange leaves Min/Max at 0 and uses GetMeleeRange as the whole max
    (Spell.cpp:7344-7349) -- rules out 'melee entries use RangeMax + reach'."""
    s = dict(BOLT, range={"flags": 1, "min": [0, 0], "max": [40, 40]}, dmg_class=2)
    w, _ = fixture(s, dist=4.9)
    assert X.check_range(w, s, "p", "t", True) == X.OK          # max(1.5+1.5+1.333, 5) = 5
    w, _ = fixture(s, dist=5.1)
    assert X.check_range(w, s, "p", "t", True) == "SPELL_FAILED_OUT_OF_RANGE"


def test_dead_target_rejected_at_cast_but_allowed_spell_dropped_at_hit_when_revived():
    """One predicate, two stages: a not-dead-allowing spell fails CheckTarget at cast eligibility
    (TARGETS_DEAD); a dead-allowing spell passes cast and recipient but is silently dropped at
    DoTargetSpellHit when the alive state changed in flight (Spell.cpp:2811), unless
    SPELL_ATTR9_FORCE_CORPSE_TARGET."""
    w, s = fixture(BOLT, target_kw={"alive": False})
    v = X.validate(w, s)
    # ENEMY-flag spells fail earlier, inside CheckExplicitTarget -> IsValidAttackTarget (dead), as BAD_TARGETS
    assert v["cast_prepare"]["result"] == "SPELL_FAILED_BAD_TARGETS"
    plain = dict(BOLT, explicit_target_mask=UNIT, required_explicit_target_mask=UNIT)
    w, s = fixture(plain, target_kw={"alive": False})
    assert X.validate(w, s)["cast_prepare"]["result"] == "SPELL_FAILED_TARGETS_DEAD"
    dead_ok = dict(BOLT, is_allowing_dead_target=True)
    w, s = fixture(dead_ok, target_kw={"alive": False}, target_facts={"hit": {"alive_at_hit": True}})
    v = X.validate(w, s)
    assert v["cast_prepare"]["result"] == X.OK and v["recipient"]["added"]
    assert v["hit"]["silent_drop"] == "2811:alive-state-changed" and not v["hit"]["applied"]
    forced = dict(dead_ok, attributes=["SPELL_ATTR9_FORCE_CORPSE_TARGET"])
    w, s = fixture(forced, target_kw={"alive": False}, target_facts={"hit": {"alive_at_hit": True}})
    assert X.validate(w, s)["hit"]["applied"]


def test_friendly_target_fails_cast_but_ignore_target_check_lands_on_friend():
    """Relation is checked only at cast eligibility: TRIGGERED_IGNORE_TARGET_CHECK maps BAD_TARGETS
    to OK (Spell.cpp:3495), the recipient stage (AddUnitTarget) never re-checks the relation, and
    PreprocessSpellHit takes the assist branch -- a harmful triggered spell applies to a friend.
    Rules out 'recipient selection re-validates enemy relation'."""
    w, s = fixture(BOLT, target_tpl=35)
    v = X.validate(w, s)
    assert v["cast_prepare"]["result"] == "SPELL_FAILED_BAD_TARGETS"
    w, s = fixture(dict(BOLT, cast_time_ms=0), target_tpl=35,
                   spell={"triggered_flags": ["IGNORE_TARGET_CHECK"], "can_reflect": False})
    t = Trace()
    v = X.validate(w, s, t)
    assert v["cast_prepare"] == {"raw": "SPELL_FAILED_BAD_TARGETS", "result": X.OK,
                                 "unevaluated_after_mapping": True}
    assert v["recipient"]["added"]
    assert v["hit"]["applied"]
    assert "3143:assist-branch" in [n for st in t.stages if st.name == "explicit.hit" for n in st.notes]


def test_ignore_target_check_skips_every_later_check():
    """CheckCast returns at the first failure, and prepare maps only that BAD_TARGETS to OK
    (Spell.cpp:3495) -- so a friendly (BAD_TARGETS) target far out of range is accepted while a
    hostile out-of-range one is not.  Rules out 'IGNORE_TARGET_CHECK only disables relation checks'."""
    trig = {"triggered_flags": ["IGNORE_TARGET_CHECK"]}
    w, s = fixture(dict(BOLT, cast_time_ms=0), target_tpl=35, dist=500.0, spell=trig)
    v = X.validate(w, s)
    assert v["cast_prepare"]["result"] == X.OK and v["cast_prepare"]["unevaluated_after_mapping"]
    assert v["hit"]["applied"]


def test_ignore_target_check_does_not_mask_other_codes():
    w, s = fixture(dict(BOLT, cast_time_ms=0), dist=80.0, spell={"triggered_flags": ["IGNORE_TARGET_CHECK"]})
    assert X.validate(w, s)["cast_prepare"]["result"] == "SPELL_FAILED_OUT_OF_RANGE"


def test_magic_redirect_happens_after_range_checks():
    """SelectExplicitTargets runs after CheckCast (Spell.cpp:3756 then 3816->723); the magnet's range
    and LOS are never checked on the magic path (Object.cpp:2613) -- rules out 'redirect before
    cast validation'."""
    w, s = fixture(BOLT, target_facts={"magnet_auras": [{"caster": "g", "kind": "magic"}]})
    w.actors["g"] = World.from_dict({"schema": "targeting-fixture/1", "caster": "g", "actors": [
        actor("g", "totem", 2, ["PLAYER_CONTROLLED"], pos=[500, 0, 0], combat_reach=0.5, bounding_radius=0.5,
              facts={"moving": False, "immune_effects": [], "immune_to_spell": False, "damage_immune": False,
                     "hit_roll": "SPELL_MISS_NONE", "pvp_flags": ["PVP"],
                     "ignore_los_on_me": False})]}).actors["g"]
    w.actors["p"].facts["pvp_flags"] = ["PVP"]
    w.los = {"default": "clear", "blocked": [["p", "g"]]}
    v = X.validate(w, s)
    assert v["cast_complete"]["result"] == X.OK
    assert v["redirect"] == "g"
    # recipient: CheckEffectTarget LOS to the magnet fails -> no effects left
    assert not v["recipient"]["added"]


def test_melee_redirect_needs_los():
    melee = dict(BOLT, dmg_class=2)
    w, s = fixture(melee, target_facts={"magnet_auras": [{"caster": "t", "kind": "melee"}]})
    assert X.select_explicit_targets(w, s, "t") == "t"


def test_crowd_control_check_is_implicit_only():
    """SPELL_ATTR6_DO_NOT_CHAIN_TO_CROWD_CONTROLLED_TARGETS rejects only implicit targets (SpellInfo.cpp:2456)."""
    s = dict(BOLT, attributes=["SPELL_ATTR6_DO_NOT_CHAIN_TO_CROWD_CONTROLLED_TARGETS"])
    w, _ = fixture(s, target_facts={"cc_breakable_by_damage": True})
    assert X.check_target(w, s, "p", "t", False) == X.OK
    assert X.check_target(w, s, "p", "t", True) == "SPELL_FAILED_BAD_TARGETS"


def test_only_on_player_and_creature_type():
    s = dict(BOLT, attributes=["SPELL_ATTR3_ONLY_ON_PLAYER"])
    w, _ = fixture(s)
    assert X.check_target(w, s, "p", "t", False) == "SPELL_FAILED_TARGET_NOT_PLAYER"
    s = dict(BOLT, target_creature_type=1 << (7 - 1))       # humanoid only
    w, _ = fixture(s, target_facts={"creature_type": 9})     # mechanical
    assert X.check_target(w, s, "p", "t", False) == "SPELL_FAILED_BAD_TARGETS"
    w, _ = fixture(s, target_facts={"creature_type": 0})     # no type -> passes
    assert X.check_target(w, s, "p", "t", False) == X.OK
    with pytest.raises(FailClosed):
        X.check_target(fixture(s)[0], s, "p", "t", False)


def test_target_aura_state_and_aura_spell():
    s = dict(BOLT, aura_restrictions={"target_aura_state": 2, "exclude_target_aura_state": 0,
                                      "target_aura_spell": 0, "exclude_target_aura_spell": 99,
                                      "target_aura_type": 0, "exclude_target_aura_type": 0})
    w, _ = fixture(s, target_facts={"aura_states": [2]})
    assert X.check_target(w, s, "p", "t", False) == X.OK
    w, _ = fixture(s, target_facts={"aura_states": []})
    assert X.check_target(w, s, "p", "t", False) == "SPELL_FAILED_TARGET_AURASTATE"
    w, _ = fixture(s, target_facts={"aura_states": [2]}, target_kw={"auras": [{"spell": 99, "caster": "x"}]})
    assert X.check_target(w, s, "p", "t", False) == "SPELL_FAILED_TARGET_AURASTATE"


def test_los_blocked_and_ignore_attr():
    w, s = fixture(BOLT, los={"default": "clear", "blocked": [["p", "t"]]})
    assert X.cast_eligibility(w, s, "t", True) == "SPELL_FAILED_LINE_OF_SIGHT"
    s2 = dict(BOLT, attributes=["SPELL_ATTR2_IGNORE_LINE_OF_SIGHT"])
    assert X.cast_eligibility(w, s2, "t", True) == X.OK
    w, s = fixture(BOLT, los={"default": "clear", "blocked": [["p", "t"]]},
                   target_facts={"ignore_los_on_me": True})
    assert X.cast_eligibility(w, s, "t", True) == X.OK


def test_explicit_check_uses_original_caster():
    """CheckCast's CheckExplicitTarget uses m_originalCaster (Spell.cpp:5947) while CheckTarget uses
    m_caster: a totem (caster) casting for a player whose target is friendly to the totem's template
    but hostile to the player passes the explicit check."""
    w, s = fixture(BOLT, target_tpl=14)
    t = Trace()
    X.cast_eligibility(w, s, "t", True, t)
    assert all("5947" not in n for st in t.stages for n in st.notes)
    w.original_caster = "p"
    w.caster = "p"
    t = Trace()
    X.cast_eligibility(w, s, "t", True, t)
    assert any("5947:explicit-check-uses-original-caster" in n for st in t.stages for n in st.notes)


def test_immune_effect_mask_and_hit_evade():
    w, s = fixture(dict(BOLT, effects=[{"index": 0, "effect": 2, "aura": 0, "explicit": True},
                                       {"index": 1, "effect": 6, "aura": 3, "explicit": True}]),
                   target_facts={"immune_effects": [0]})
    rec = X.recipient(w, s, "t")
    assert rec["effect_mask"] == 2
    w, s = fixture(BOLT, target_facts={"evading": True})
    v = X.validate(w, s)
    assert v["recipient"]["miss"] == "SPELL_MISS_EVADE" and not v["hit"]["applied"]
    w, s = fixture(BOLT, target_facts={"hit": {"evading_at_hit": True}})
    v = X.validate(w, s)
    assert v["recipient"]["miss"] == "SPELL_MISS_NONE" and v["hit"]["miss"] == "SPELL_MISS_EVADE"


def test_fully_immune_target_is_still_added():
    """AddUnitTarget checks `!effectMask` only before the immunity pass (Spell.cpp:2450 vs 2458):
    a fully effect-immune target still becomes a TargetInfo with EffectMask 0."""
    w, s = fixture(BOLT, target_facts={"immune_effects": [0]})
    rec = X.recipient(w, s, "t")
    assert rec["added"] and rec["effect_mask"] == 0


def test_init_selection_and_self_fallback():
    helpful = dict(BOLT, is_positive=True, explicit_target_mask=ALLY, required_explicit_target_mask=ALLY)
    w, s = fixture(helpful)
    w.explicit = {}
    assert X.init_explicit_targets(w, s) == "p"               # no selection -> self (ALLY mask)
    w.explicit = {"selection": "t"}
    assert X.init_explicit_targets(w, s) == "p"               # hostile selection fails explicit check
    w, s = fixture(BOLT)
    w.explicit = {}
    assert X.init_explicit_targets(w, s) is None              # ENEMY mask: no self fallback
    w.explicit = {"selection": "t"}
    assert X.init_explicit_targets(w, s) == "t"


def test_raid_flag_rejects_ungrouped_friend():
    """TARGET_UNIT_TARGET_ALLY_OR_RAID (118, e.g. Mark of the Wild 1126) contributes only
    TARGET_FLAG_UNIT_RAID, so CheckExplicitTarget requires IsInRaidWith (SpellInfo.cpp:2551): an
    ungrouped friendly player is BAD_TARGETS, which makes the selector's 'not in raid -> explicit
    target only' branch (Spell.cpp:1396) unreachable for normal player casts.  Rules out
    'ALLY_OR_RAID accepts any ally'."""
    raid = X.TARGET_FLAG["UNIT_RAID"]
    motw = dict(BOLT, is_positive=True, explicit_target_mask=raid, required_explicit_target_mask=raid,
                has_only_damage_effects=False, facing_caster_flags=0, cast_time_ms=0)
    for group, expected in ((None, "SPELL_FAILED_BAD_TARGETS"), ({"id": "g", "subgroup": 3}, X.OK)):
        w, s = fixture(motw)
        w.actors["p"].group = {"id": "g", "subgroup": 1}
        next(a for a in w.raw["actors"] if a["id"] == "p")["group"] = {"id": "g", "subgroup": 1}
        raw = actor("f", "player", 1, group=group, pos=[10, 0, 0], orientation=0.0, combat_reach=1.5,
                    bounding_radius=0.4, facts={"moving": False, "ignore_los_on_me": False})
        w.actors["f"] = World.from_dict({"schema": "targeting-fixture/1", "caster": "f", "actors": [raw]}).actors["f"]
        w.raw["actors"].append(raw)
        w.explicit = {"unit": "f"}
        assert X.cast_eligibility(w, s, "f", True) == expected
    w.explicit = {}
    assert X.init_explicit_targets(w, s) == "p"            # self fallback (mask lacks ENEMY/DEAD/...)
