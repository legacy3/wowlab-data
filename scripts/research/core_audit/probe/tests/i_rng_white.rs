//! Track I probes: white main-hand RNG sites (RNG-I-007 weapon forced draw, RNG-I-008 outcome draw).
//!
//! The Engine's stream is observed through `CombatState: Debug`; expectations are produced by an
//! independent `RandomStreamIdentity::stream()` replaying the documented call sequence.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CombatObservation, CombatProgram, CombatState, HasteMultipliers, HealthPool,
    OffensivePower, WhiteEngagementGeometry, WhiteMainHandChances, WhiteMainHandInput,
    WhitePhysicalBasis, WhiteSwingOutcome,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity};
use wowlab_engine::{Engine, EngineInput, EngineProgram};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, SimTime};
use wowlab_rotation::{Identifier, Rotation};
use wowlab_sim::{Probability, RandomStreamIdentity};
use wowlab_test_support::{actionless_game_data, actionless_resolved_actions};

fn random_text(state: &CombatState) -> String {
    let text = format!("{state:?}");
    let start = text
        .find("random: RandomStream {")
        .expect("CombatState Debug exposes its random stream")
        + "random: ".len();
    let end = start + text[start..].find('}').expect("stream Debug closes") + 1;

    text[start..end].to_owned()
}

fn consumed(state: &CombatState, identity: RandomStreamIdentity) -> Option<usize> {
    let target = random_text(state);
    let mut stream = identity.stream();

    for count in 0..100_000 {
        if format!("{stream:?}") == target {
            return Some(count);
        }

        stream.occurs_forced(Probability::ZERO);
    }

    None
}

fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn actor(id: ActorId, health: f64) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(health).expect("valid health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

fn program() -> EngineProgram {
    let identity = GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    };
    let data = actionless_game_data(identity);
    let actions = actionless_resolved_actions(identity);
    let combat = CombatProgram::builder(&data, &actions)
        .build()
        .expect("actionless program");

    EngineProgram::try_new(
        combat,
        &Rotation::new("white rng probe".to_owned()),
        &|_: &Identifier| None,
    )
    .expect("driver-only engine program")
}

fn white(prepared_damage: (u32, u32), chances: WhiteMainHandChances) -> WhiteMainHandInput {
    WhiteMainHandInput {
        source: ActorId::Player,
        target: enemy(),
        prepared_damage,
        base_period: SimTime::from_millis(2_000),
        effective_levels: (1, 1),
        critical_percent_before_compiled_modifier: None,
        chances,
        physical: WhitePhysicalBasis {
            target_armor: 0.0,
            armor_constant: 116.0,
        },
        geometry: WhiteEngagementGeometry {
            in_range: true,
            source_facing: true,
            defender_facing: true,
            defender_standing: true,
            physical_susceptible: true,
            defender_not_evading: true,
        },
    }
}

fn engine(input: WhiteMainHandInput, health: f64, identity: RandomStreamIdentity) -> Engine {
    Engine::try_new(
        program(),
        EngineInput::new(vec![actor(ActorId::Player, 100.0), actor(enemy(), health)])
            .with_initial_white_main_hand(input),
        ActorId::Player,
        enemy(),
        identity,
    )
    .expect("white encounter builds")
}

const ZERO: WhiteMainHandChances = WhiteMainHandChances {
    miss: 0,
    dodge: 0,
    parry: 0,
    block: 0,
    critical: 0,
};

fn swings(observations: &[CombatObservation]) -> Vec<(WhiteSwingOutcome, u32)> {
    observations
        .iter()
        .filter_map(|observation| match observation {
            CombatObservation::WhiteMainHandSwing {
                outcome,
                weapon_damage,
                ..
            } => Some((*outcome, *weapon_damage)),
            _ => None,
        })
        .collect()
}

/// RNG-I-007 / RNG-I-008: a singleton weapon domain with an all-zero outcome table still
/// consumes exactly two u64 draws per swing (forced weapon draw + fixed 10_000 outcome draw).
#[gtest]
fn holds_white_swing_consumes_two_draws_even_when_fully_deterministic() -> Result<()> {
    let identity = RandomStreamIdentity::new(44, 8);
    let mut engine = engine(white((20, 20), ZERO), 1_000_000.0, identity);
    let mut total_swings = 0;

    for _ in 0..4 {
        total_swings += swings(engine.advance().expect("advance").observations()).len();
    }

    verify_that!(total_swings, gt(0))?;
    verify_that!(consumed(engine.combat_state(), identity), some(eq(2 * total_swings)))
}

/// RNG-I-007 / RNG-I-008: independent replay of the documented call order — weapon
/// `uniform_index_forced(width)` then outcome `uniform_index(10_000)` over miss/dodge/crit shares —
/// reproduces every observed (outcome, weapon damage) pair; reset and iteration reset replay.
#[gtest]
fn holds_white_swing_matches_independent_replay_of_documented_draw_order() -> Result<()> {
    let chances = WhiteMainHandChances {
        miss: 1_500,
        dodge: 1_000,
        parry: 0,
        block: 0,
        critical: 3_000,
    };

    for iteration in [8_u64, 9, 10] {
        let identity = RandomStreamIdentity::new(44, iteration);
        let mut engine = engine(white((10, 30), chances), 1_000_000.0, identity);
        let mut observed = Vec::new();

        for _ in 0..6 {
            observed.extend(swings(engine.advance().expect("advance").observations()));
        }

        let mut replay = identity.stream();
        let expected: Vec<_> = observed
            .iter()
            .map(|_| {
                let damage = 10 + replay.uniform_index_forced(21).expect("nonempty");
                let roll = replay.uniform_index(10_000).expect("nonempty");
                let outcome = if roll < 1_500 {
                    WhiteSwingOutcome::Miss
                } else if roll < 2_500 {
                    WhiteSwingOutcome::Dodge
                } else if roll < 5_500 {
                    WhiteSwingOutcome::Critical
                } else {
                    WhiteSwingOutcome::Hit
                };

                (outcome, damage)
            })
            .collect();

        verify_eq!(observed.clone(), expected)?;
        verify_eq!(random_text(engine.combat_state()), format!("{replay:?}"))?;

        engine.reset();
        let mut replayed = Vec::new();

        for _ in 0..6 {
            replayed.extend(swings(engine.advance().expect("advance").observations()));
        }

        verify_eq!(replayed, observed)?;
    }

    Ok(())
}

/// RNG-I-007: once the fixed target is dead, due main-hand deadlines terminate without any draw
/// (no cleanup consumes RNG).
#[gtest]
fn holds_white_swing_after_target_death_consumes_no_draw() -> Result<()> {
    let identity = RandomStreamIdentity::new(44, 11);
    let mut engine = engine(white((20, 20), ZERO), 30.0, identity);
    let mut total = 0;

    for _ in 0..8 {
        let Ok(outcome) = engine.advance() else {
            break;
        };

        total += swings(outcome.observations()).len();
    }

    verify_eq!(total, 2)?;
    verify_that!(consumed(engine.combat_state(), identity), some(eq(4)))
}
