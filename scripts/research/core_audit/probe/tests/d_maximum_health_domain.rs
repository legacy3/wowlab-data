//! Track D probes: maximum-health producer vs raw-165 consumer domain.
//! Read-only against Core; uses only public API and test-support fixtures.

use wowlab_combat::{
    ActorState, CastErrorCode, CastRequest, CombatObservation, CombatProgram, CombatState,
    CombatStateInput, HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity};
use wowlab_model::{ActorId, DifficultyId, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::direct_maximum_health_damage_inputs;

const fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 165_001,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn actor(id: ActorId, maximum: f64) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::try_new(maximum, maximum).expect("valid pool")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

fn state(program: &CombatProgram, player_maximum: f64) -> CombatState {
    program
        .try_state(
            CombatStateInput::new(
                vec![actor(ActorId::Player, player_maximum), actor(ActorId::External, 10_000.0)],
                RandomStreamIdentity::new(165, 1),
            )
            .with_hostile_target(ActorId::Player, ActorId::External)
            .with_hostile_target(ActorId::External, ActorId::Player),
        )
        .expect("valid state")
}

fn request(raw: u32) -> CastRequest {
    CastRequest::new(ActorId::Player, ActorId::Player, SpellId::new(raw).expect("nonzero"))
}

fn player(state: &CombatState) -> HealthPool {
    *state.actor(ActorId::Player).and_then(ActorState::health).expect("player health")
}

/// CSA-D-01 (LIVE): an integral baseline maximum (1001) plus Core's own admitted raw-133
/// Increase Health Percent authority (+50 %) yields a fractional maximum (1501.5, Trinity would
/// truncate to uint32 1501), and the admitted raw-165 self damage then fails the whole cast
/// with `DamageCalculation` instead of dealing 10 % of maximum health.
#[test]
fn defect_raw165_rejects_fractional_maximum_produced_by_raw133() {
    let (fixture, data, actions) = direct_maximum_health_damage_inputs(identity(), 10.0);
    let program = CombatProgram::builder(&data, &actions).build().expect("fixture compiles");
    let mut state = state(&program, 1_001.0);
    let mut output: Vec<CombatObservation> = Vec::new();

    state.cast(request(fixture.maximum_health_aura), &mut output).expect("raw-133 applies");
    let pool = player(&state);
    assert_eq!(pool.maximum(), 1_501.5, "raw-133 keeps an untruncated fractional maximum");
    assert_eq!(pool.current(), 1_501.5);

    let mut rejected = Vec::new();
    let error = state
        .cast(request(fixture.damage), &mut rejected)
        .expect_err("raw-165 rejects the fractional maximum it was handed by raw-133");
    assert_eq!(error.code(), CastErrorCode::DamageCalculation);
    assert!(rejected.is_empty());
    assert_eq!(player(&state).current(), 1_501.5, "no damage was dealt");
}

/// Control for CSA-D-01: the same program with an even baseline (1000 -> 1500) casts fine and
/// deals 150, proving the rejection is caused solely by the fractional maximum.
#[test]
fn holds_raw165_accepts_integral_maximum_produced_by_raw133() {
    let (fixture, data, actions) = direct_maximum_health_damage_inputs(identity(), 10.0);
    let program = CombatProgram::builder(&data, &actions).build().expect("fixture compiles");
    let mut state = state(&program, 1_000.0);
    let mut output = Vec::new();

    state.cast(request(fixture.maximum_health_aura), &mut output).expect("raw-133 applies");
    state.cast(request(fixture.damage), &mut output).expect("raw-165 casts");
    assert_eq!(player(&state).maximum(), 1_500.0);
    assert_eq!(player(&state).current(), 1_350.0);
}
