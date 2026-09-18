//! Reviewer R2: passive raw-396 threshold children for a dead pool owner (UNK-R2-002 / CSA-R2-01).
//!
//! Reuses Core's public fixture `resource_threshold_runtime_inputs(BoundInactive)` (one passive
//! gain threshold at 80 Rage whose child gains Rage), exactly as `crates/combat/tests/
//! resource_thresholds.rs::passive_flow_dispatches_at_the_exact_first_crossing_millisecond`
//! builds it (Rage 20/100, +10 Rage/s passive flow), except that the pool owner holds a health
//! pool at 0 (dead). Trinity regenerates only living units (Player.cpp:1025-1029), so no
//! crossing and no threshold child can occur for a corpse.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CombatObservation, CombatProgram, CombatStateInput, HasteMultipliers, HealthPool,
    OffensivePower, PassiveResourceFlow, PassiveResourceThresholdBinding, ResourcePool,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity};
use wowlab_model::{ActorId, DifficultyId, RecoveryRate, SimTime};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{ResourceThresholdRuntimeCase, resource_threshold_runtime_inputs};

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

/// UNK-R2-002: a dead (0-health) pool owner still regenerates to the passive gain threshold and
/// its raw-396 child executes at the exact crossing millisecond.
#[gtest]
fn defect_passive_threshold_child_executes_for_dead_owner() -> Result<()> {
    let (fixture, data, actions) =
        resource_threshold_runtime_inputs(identity(), ResourceThresholdRuntimeCase::BoundInactive);
    let program = CombatProgram::builder(&data, &actions).build()?;
    let pool = ResourcePool::try_new(fixture.resource, fixture.initial, fixture.maximum)?;
    let actor = ActorState::try_new(
        ActorId::Player,
        vec![pool],
        Some(HealthPool::try_new(0.0, 100.0)?),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )?;
    let bindings = fixture
        .passive_effects
        .iter()
        .copied()
        .map(|effect| PassiveResourceThresholdBinding::new(ActorId::Player, effect))
        .collect();
    let mut state = program.try_state(
        CombatStateInput::new(vec![actor], RandomStreamIdentity::new(396, 11))
            .with_passive_resource_threshold_bindings(bindings)
            .with_passive_resource_flow(PassiveResourceFlow::try_new(
                ActorId::Player,
                fixture.resource,
                10.0,
                RecoveryRate::Fixed,
            )?),
    )?;

    verify_false!(
        state
            .actor(ActorId::Player)
            .and_then(ActorState::health)
            .or_fail()?
            .is_alive()
    )?;
    verify_eq!(state.next_transition_at(), Some(SimTime::from_millis(6_000)))?;

    let mut crossing = Vec::new();
    state.advance_to_into(SimTime::from_millis(6_000), &mut crossing)?;

    let gains: Vec<_> = crossing
        .iter()
        .filter_map(|observation| match observation {
            CombatObservation::ResourceGained {
                at, actor, gain, ..
            } => Some((*at, *actor, gain.before(), gain.after())),
            _ => None,
        })
        .collect();

    // The raw-396 child (396002) gains +5 Rage for the dead Player at the crossing millisecond.
    verify_eq!(
        gains,
        vec![(SimTime::from_millis(6_000), ActorId::Player, 80.0, 85.0)]
    )
}
