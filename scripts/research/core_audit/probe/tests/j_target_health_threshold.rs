//! Track J probe: raw-183 target-health critical predicate threshold precision.
//! Core compares recipient health against the exact real threshold `maximum * (percent / 100)`;
//! the Trinity consumer compares against the binary32-computed, truncated integer threshold
//! `CountPctFromMaxHealth(pct) = uint64(float(maxHealth) * pct / 100.0f)` (Unit.h:791,796;
//! Util.h:72-75; Unit.cpp:2917-2920 `!HealthBelowPct(MiscValueB)`).

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatStateInput,
    CriticalStrikeBaseline, CriticalStrikeChances, HasteMultipliers, HealthPool, OffensivePower,
    PassiveCriticalModifierBinding,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, HitResult, SpellEffectRef, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    target_health_critical_damage_raw, target_health_critical_modifier_raw,
    target_health_critical_runtime_inputs,
};

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn target() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn spell(raw: u32) -> SpellId {
    SpellId::new(raw).expect("nonzero")
}

/// Casts the fixture's direct damage against a target at `current / maximum` health with a
/// zero baseline and one +100-point AtOrAbove-80 raw-183 passive (fixture Runtime kind).
fn hit_against(current: f64, maximum: f64) -> Result<HitResult> {
    let (data, actions) = target_health_critical_runtime_inputs(identity());
    let program = CombatProgram::builder(&data, &actions).build()?;
    let actors = vec![
        ActorState::try_new(
            ActorId::Player,
            Vec::new(),
            Some(HealthPool::try_new(900.0, 1_000.0)?),
            OffensivePower::ZERO,
            HasteMultipliers::UNHASTED,
        )?,
        ActorState::try_new(
            target(),
            Vec::new(),
            Some(HealthPool::try_new(current, maximum)?),
            OffensivePower::ZERO,
            HasteMultipliers::UNHASTED,
        )?,
    ];
    let binding = PassiveCriticalModifierBinding::new(
        ActorId::Player,
        SpellEffectRef::new(spell(target_health_critical_modifier_raw(0)), 1).or_fail()?,
    );
    let input = CombatStateInput::new(actors, RandomStreamIdentity::new(251, 257))
        .with_hostile_target(ActorId::Player, target())
        .with_critical_strike(CriticalStrikeBaseline::try_new(
            ActorId::Player,
            CriticalStrikeChances {
                spell: 0.0,
                weapon: 0.0,
            },
            2.0,
        )?)
        .with_passive_critical_modifier_bindings(vec![binding]);
    let mut state = program.try_state(input)?;
    let mut output = Vec::new();

    state.cast(
        CastRequest::new(
            ActorId::Player,
            target(),
            spell(target_health_critical_damage_raw()),
        ),
        &mut output,
    )?;

    output
        .iter()
        .find_map(|observation| match observation {
            CombatObservation::DamageDealt { result, .. } => Some(*result),
            _ => None,
        })
        .or_fail()
}

/// Trinity reference threshold for `!HealthBelowPct(pct)`.
fn trinity_threshold(maximum: u64, pct: f32) -> u64 {
    (maximum as f32 * pct / 100.0_f32) as u64
}

/// NUM-J-020 / CSA-J-03: max 1001, current 800, AtOrAbove 80.
/// Core: 800 >= 1001 * 0.8 = 800.8 is false -> no +100 points -> Hit.
/// Trinity: 800 >= uint64(1001.0f * 80.0f / 100.0f) = 800 is true -> guaranteed Critical.
#[gtest]
fn defect_target_health_at_or_above_uses_untruncated_threshold() -> Result<()> {
    verify_eq!(trinity_threshold(1_001, 80.0), 800)?;
    verify_true!(800 >= trinity_threshold(1_001, 80.0))?;

    verify_eq!(hit_against(800.0, 1_001.0)?, HitResult::Hit)?;
    // Controls: one point higher, and an exactly divisible maximum, both satisfy Core too.
    verify_eq!(hit_against(801.0, 1_001.0)?, HitResult::Critical)?;
    verify_eq!(hit_against(800.0, 1_000.0)?, HitResult::Critical)
}
