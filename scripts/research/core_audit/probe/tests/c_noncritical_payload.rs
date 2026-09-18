//! Track C probes: critical resolution for impacts that carry no damage or healing.
//!
//! Proves CSA-C-01: a hostile aura-only impact (the exact Stun fixture) is critical-eligible,
//! so a caster critical baseline turns its ImpactResolved result into `Critical` and, below
//! 100%, consumes one critical draw although no amount can be affected.
use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    CriticalStrikeBaseline, HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, HitResult};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{StunFixture, stun_inputs};

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 1_201_069_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn actor(id: ActorId) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(1_000.0).expect("valid health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

fn state(baseline: Option<f64>, seed: u64) -> (StunFixture, CombatState) {
    let (fixture, data, actions) = stun_inputs(identity());
    let program = CombatProgram::builder(&data, &actions)
        .build()
        .expect("exact stun fixture compiles");
    let mut input = CombatStateInput::new(
        vec![actor(ActorId::Player), actor(enemy())],
        RandomStreamIdentity::new(seed, 0),
    )
    .with_hostile_target(enemy(), ActorId::Player);

    if let Some(chance) = baseline {
        input = input.with_critical_strike(
            CriticalStrikeBaseline::try_uniform(enemy(), chance, 2.0).expect("valid baseline"),
        );
    }

    let state = program.try_state(input).expect("stun state builds");

    (fixture, state)
}

fn stun_results(baseline: Option<f64>, seed: u64) -> Vec<HitResult> {
    let (fixture, mut state) = state(baseline, seed);
    let mut output = Vec::new();

    state
        .cast(
            CastRequest::new(enemy(), ActorId::Player, fixture.stun_spell()),
            &mut output,
        )
        .expect("stun cast commits");

    output
        .iter()
        .filter_map(|observation| match observation {
            CombatObservation::ImpactResolved { result, .. } => Some(*result),
            _ => None,
        })
        .collect()
}

/// CSA-C-01 (MUT-C-001): a guaranteed caster critical baseline makes a pure Stun impact `Critical`.
#[gtest]
fn defect_aura_only_hostile_impact_reports_critical_result() -> Result<()> {
    verify_eq!(stun_results(None, 12), vec![HitResult::Hit])?;
    verify_eq!(stun_results(Some(0.0), 12), vec![HitResult::Hit])?;
    verify_eq!(stun_results(Some(1.0), 12), vec![HitResult::Critical])
}

/// CSA-C-01 (MUT-C-002): at a fractional chance the pure Stun impact samples a critical draw,
/// so across seeds both `Hit` and `Critical` are observed for an impact with no amount.
#[gtest]
fn defect_aura_only_hostile_impact_samples_critical_draw() -> Result<()> {
    let mut critical = 0;
    let mut hit = 0;

    for seed in 0..64 {
        match stun_results(Some(0.5), seed).as_slice() {
            [HitResult::Critical] => critical += 1,
            [HitResult::Hit] => hit += 1,
            other => return fail!("unexpected impact results {other:?}"),
        }
    }

    verify_that!(critical, gt(0))?;
    verify_that!(hit, gt(0))
}
