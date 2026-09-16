"""Derived quantities: the stat pipeline, health, armor, AP, SP, crit."""

from __future__ import annotations

import pytest

from charstats.derived import (
    BASE_CRIT_PERCENT,
    DerivedStatEngine,
    StatStage,
)
from charstats.identity import (
    STAT_AGILITY,
    STAT_INTELLECT,
    STAT_STAMINA,
    STAT_STRENGTH,
    ClassInfo,
)


def klass(**kwargs) -> ClassInfo:
    base = dict(class_id=1, name="Test", primary_stat_priority=5,
                attack_power_per_strength=1, attack_power_per_agility=0,
                ranged_attack_power_per_agility=0, display_power=1,
                armor_type_mask=0, default_spec=0, has_strength_attack_bonus=1,
                damage_bonus_stat=0, spell_class_set=0, roles_mask=0)
    base.update(kwargs)
    return ClassInfo(**base)


# -- GetTotalStatValue ordering -------------------------------------------

def test_stat_stage_formula_order_is_discriminating():
    """create is added *between* BASE_PCT_EXCLUDE_CREATE and BASE_PCT."""
    stage = StatStage(stat=STAT_STRENGTH, create_value=100,
                      base_flat=200, base_pct_exclude_create=50.0,
                      base_pct=2.0, total_flat=10, total_pct=1.5)
    # ((200 * 0.5) + 100) * 2 + 10 = 410 ; * 1.5 = 615
    assert stage.value() == pytest.approx(615.0)
    # A naive "(base_flat + create) * base_pct_exclude_create" would give 450.
    assert stage.value() != pytest.approx(450.0)


def test_stat_stage_truncates_not_rounds():
    stage = StatStage(stat=STAT_STRENGTH, create_value=0, total_flat=9.99)
    assert stage.value() == pytest.approx(9.99)
    assert stage.rounded() == 9          # SetStat(stat, int32(value))


def test_base_pct_exclude_create_is_floored_at_minus_100():
    stage = StatStage(stat=STAT_STRENGTH, create_value=50, base_flat=100,
                      base_pct_exclude_create=-500.0)
    # max(-500, -100) -> -100% -> base_flat contributes -100
    assert stage.value() == pytest.approx(-50.0)


def test_total_pct_multiplies_everything():
    stage = StatStage(stat=STAT_STRENGTH, create_value=1000, total_flat=1000,
                      total_pct=1.05)
    assert stage.value() == pytest.approx(2100.0)
    assert stage.rounded() == 2100


# -- health ----------------------------------------------------------------

@pytest.mark.snapshot
def test_hp_per_sta_is_read_from_the_gametable(tables):
    engine = DerivedStatEngine(tables)
    assert engine.hp_per_sta(90) == tables.gametable("HpPerSta").column(90, 0)
    # A missing level row falls back to the consumer's literal 10.0f.
    assert engine.hp_per_sta(9999) == 10.0


@pytest.mark.snapshot
def test_max_health_is_stamina_times_the_ratio(tables):
    engine = DerivedStatEngine(tables)
    ratio = engine.hp_per_sta(90)
    health, from_stamina = engine.max_health(1234.0, 90)
    assert from_stamina == pytest.approx(1234.0 * ratio)
    assert health == int(1234.0 * ratio)


@pytest.mark.snapshot
def test_health_stage_order_is_discriminating(tables):
    engine = DerivedStatEngine(tables)
    ratio = engine.hp_per_sta(90)
    # BASE_PCT scales (BASE_VALUE + CreateHealth) only; the stamina term and
    # TOTAL_VALUE are added after it, and TOTAL_PCT scales everything.
    health, _ = engine.max_health(100.0, 90, create_health=500, base_flat=100,
                                 base_pct=2.0, total_flat=50, total_pct=1.5)
    expected = ((100 + 500) * 2.0 + 50 + 100.0 * ratio) * 1.5
    assert health == int(expected)
    # A version that scaled the stamina term by BASE_PCT would differ.
    assert health != int(((100 + 500 + 100.0 * ratio) * 2.0 + 50) * 1.5)


@pytest.mark.snapshot
def test_max_health_truncates(tables):
    engine = DerivedStatEngine(tables)
    health, _ = engine.max_health(0.0, 90, create_health=0, total_flat=9.99)
    assert health == 9


# -- armor -----------------------------------------------------------------

def test_armor_reports_gear_as_bonus_armor(tables):
    engine = DerivedStatEngine(tables)
    armor, bonus = engine.armor(1000.0, total_flat=5000.0)
    assert armor == 6000
    assert bonus == 5000


def test_armor_base_pct_does_not_scale_gear(tables):
    engine = DerivedStatEngine(tables)
    armor, bonus = engine.armor(1000.0, base_pct=2.0, total_flat=5000.0)
    assert armor == 7000          # 1000*2 + 5000
    assert bonus == 5000


def test_armor_pct_from_stat_counts_as_base(tables):
    engine = DerivedStatEngine(tables)
    armor, bonus = engine.armor(1000.0, total_flat=1000.0,
                                armor_pct_from_stat=500.0)
    assert armor == 2500
    assert bonus == 1000          # the stat-derived part is NOT bonus armor


def test_armor_total_pct_and_bonus_armor_pct_multiply(tables):
    engine = DerivedStatEngine(tables)
    armor, _ = engine.armor(1000.0, total_flat=1000.0, total_pct=1.5,
                            bonus_armor_pct=2.0)
    assert armor == 6000


# -- attack power ----------------------------------------------------------

def test_melee_attack_power_uses_both_class_coefficients(tables):
    engine = DerivedStatEngine(tables)
    warrior = klass(attack_power_per_strength=1, attack_power_per_agility=0)
    ap, from_str, from_agi = engine.attack_power(warrior, 5000.0, 3000.0, 90)
    assert (ap, from_str, from_agi) == (5000, 5000.0, 0.0)

    rogue = klass(attack_power_per_strength=0, attack_power_per_agility=1)
    ap, from_str, from_agi = engine.attack_power(rogue, 5000.0, 3000.0, 90)
    assert (ap, from_str, from_agi) == (3000, 0.0, 3000.0)


def test_negative_stats_are_clamped_per_term(tables):
    engine = DerivedStatEngine(tables)
    both = klass(attack_power_per_strength=1, attack_power_per_agility=1)
    ap, from_str, from_agi = engine.attack_power(both, -100.0, 50.0, 90)
    # max(..., 0) is applied to each term separately.
    assert (from_str, from_agi) == (0.0, 50.0)
    assert ap == 50


def test_ranged_attack_power_includes_level(tables):
    engine = DerivedStatEngine(tables)
    hunter = klass(ranged_attack_power_per_agility=1)
    ap, _, from_agi = engine.attack_power(hunter, 0.0, 4000.0, 90, ranged=True)
    assert from_agi == pytest.approx(90 + 4000.0)
    assert ap == 4090


def test_zero_coefficient_class_gets_no_attack_power(tables):
    engine = DerivedStatEngine(tables)
    mage = klass(attack_power_per_strength=0, attack_power_per_agility=0)
    assert engine.attack_power(mage, 9999.0, 9999.0, 90)[0] == 0


# -- spell power -----------------------------------------------------------

def test_spell_power_from_intellect_is_gated_on_primary_stat():
    sp, from_int = DerivedStatEngine.spell_power(
        5000.0, primary_stat_is_intellect=True)
    assert (sp, from_int) == (5000, 5000)
    sp, from_int = DerivedStatEngine.spell_power(
        5000.0, primary_stat_is_intellect=False)
    assert (sp, from_int) == (0, 0)


def test_spell_power_adds_the_item_bonus_regardless():
    sp, from_int = DerivedStatEngine.spell_power(
        5000.0, primary_stat_is_intellect=False, base_spell_power_bonus=700)
    assert (sp, from_int) == (700, 0)


def test_negative_intellect_contributes_nothing():
    sp, from_int = DerivedStatEngine.spell_power(
        -50.0, primary_stat_is_intellect=True)
    assert (sp, from_int) == (0, 0)


def test_spell_power_is_not_symmetric_with_attack_power(tables):
    """Intellect is 1:1 and gated; attack power is per-class-coefficient."""
    engine = DerivedStatEngine(tables)
    caster = klass(attack_power_per_strength=0, attack_power_per_agility=0)
    assert engine.attack_power(caster, 0.0, 0.0, 90)[0] == 0
    assert DerivedStatEngine.spell_power(
        1234.0, primary_stat_is_intellect=True)[0] == 1234


# -- crit ------------------------------------------------------------------

def test_crit_percentage_is_additive_over_a_flat_five():
    assert DerivedStatEngine.crit_percentage(0.0) == BASE_CRIT_PERCENT
    assert DerivedStatEngine.crit_percentage(12.5) == pytest.approx(17.5)
    assert DerivedStatEngine.crit_percentage(12.5, flat_mod=3.0) == pytest.approx(20.5)


def test_spell_crit_shares_the_same_base():
    assert DerivedStatEngine.spell_crit_percentage(0.0) == BASE_CRIT_PERCENT
    assert DerivedStatEngine.spell_crit_percentage(
        10.0, aura_bonus=2.0) == pytest.approx(17.0)
