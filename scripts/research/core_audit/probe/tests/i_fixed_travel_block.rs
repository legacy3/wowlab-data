//! Track I probe: fixed-travel launch draws a block result that its own arrival contract refuses.
//! (CSA-I-02, RNG-I-015, MUT-I-015)
//!
//! Illusionary Bolt (exact 373932, DefenseType::Magic) resolves its complete attack table at
//! launch, including the magic-block draw when the target's public `ActiveDefenseCapabilities`
//! admit magic block. Arrival rebuilds the impact with `PreparedImpactQuote::captured`, which
//! asserts `BlockResult::Unblocked` and a unit landed multiplier (impact.rs:282-296).

use std::panic::{AssertUnwindSafe, catch_unwind};

use googletest::prelude::*;
use wowlab_combat::{
    ActiveDefenseBaseline, ActiveDefenseCapabilities, ActorState, AttackAccuracyBaseline,
    AttackNonLandingChances, BlockDefense, CastRequest, CombatObservation, CombatProgram,
    CombatState, CombatStateInput, DefenseFamilyChances, HasteMultipliers, HealthPool,
    OffensivePower,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex};
use wowlab_sim::{Probability, RandomStreamIdentity};
use wowlab_test_support::{FixedTravelTimeFixture, fixed_travel_time_inputs};

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

    for count in 0..10_000 {
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

fn actor(id: ActorId) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(100.0).expect("valid health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

const fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn state(block: BlockDefense, random: RandomStreamIdentity) -> CombatState {
    let (_, data, actions) = fixed_travel_time_inputs(identity());
    let program = CombatProgram::builder(&data, &actions)
        .build()
        .expect("fixed travel compiles");
    let input = CombatStateInput::new(vec![actor(ActorId::Player), actor(enemy())], random)
        .with_hostile_target(ActorId::Player, enemy())
        .with_attack_accuracy(AttackAccuracyBaseline::new(
            ActorId::Player,
            DefenseFamilyChances::NONE,
        ))
        .with_active_defense(ActiveDefenseBaseline::new(
            enemy(),
            AttackNonLandingChances::try_new(DefenseFamilyChances::NONE, 0.0, 0.0)
                .expect("valid table"),
            block,
            ActiveDefenseCapabilities::new().with_magic_block(),
        ));

    program.try_state(input).expect("state with a magic-blocking target builds")
}

/// CSA-I-02: with a public, validated magic-block capability (block chance 0.5), the carrier's
/// launch commit consumes the block draw and may capture `Blocked`; arrival then panics inside
/// `PreparedImpactQuote::captured` ("the exact fixed-travel contract admits no block result").
/// Iterations whose block draw fails arrive normally.
#[gtest]
fn defect_fixed_travel_launch_block_draw_panics_at_arrival() -> Result<()> {
    let fixture = FixedTravelTimeFixture;
    let block = BlockDefense::try_new(0.5, 0.0, 0.7, 0.4).expect("valid block");
    let mut panicked = 0;
    let mut arrived = 0;

    for iteration in 0..16 {
        let random = RandomStreamIdentity::new(15_292, iteration);
        let mut state = state(block, random);

        state
            .begin_cast(
                CastRequest::new(ActorId::Player, enemy(), fixture.spell()),
                &mut Vec::new(),
            )
            .expect("carrier accepted: quote admits the magic-block table");
        state
            .advance_to(fixture.cast_duration())
            .expect("launch commits");

        // Launch consumed exactly the block draw (no miss, no critical baseline).
        verify_that!(consumed(&state, random), some(eq(1)))?;

        let mut expected = random.stream();
        let blocked = expected.occurs(Probability::new(0.5).expect("probability"));
        let arrival = catch_unwind(AssertUnwindSafe(|| {
            state
                .advance_to(fixture.arrival_at())
                .map(|observations: Vec<CombatObservation>| observations.len())
        }));

        match arrival {
            Err(payload) => {
                let message = payload
                    .downcast_ref::<String>()
                    .cloned()
                    .or_else(|| payload.downcast_ref::<&str>().map(|text| (*text).to_owned()))
                    .unwrap_or_default();

                verify_that!(blocked, eq(true))?;
                verify_that!(
                    message.as_str(),
                    contains_substring("the exact fixed-travel contract admits no block result")
                )?;
                panicked += 1;
            }

            Ok(result) => {
                verify_that!(blocked, eq(false))?;
                verify_that!(result.is_ok(), eq(true))?;
                arrived += 1;
            }
        }
    }

    verify_that!(panicked, gt(0))?;
    verify_that!(arrived, gt(0))
}

/// CSA-I-02 (deterministic witness): block chance 1.0 needs no draw at launch but always
/// captures `Blocked`, so every arrival panics.
#[gtest]
fn defect_fixed_travel_certain_magic_block_always_panics_at_arrival() -> Result<()> {
    let fixture = FixedTravelTimeFixture;
    let block = BlockDefense::try_new(1.0, 0.0, 0.7, 0.4).expect("valid block");
    let random = RandomStreamIdentity::new(15_292, 99);
    let mut state = state(block, random);

    state
        .begin_cast(
            CastRequest::new(ActorId::Player, enemy(), fixture.spell()),
            &mut Vec::new(),
        )
        .expect("carrier accepted");
    state
        .advance_to(fixture.cast_duration())
        .expect("launch commits");
    verify_that!(consumed(&state, random), some(eq(0)))?;

    let arrival = catch_unwind(AssertUnwindSafe(|| {
        let _ = state.advance_to(fixture.arrival_at());
    }));

    verify_that!(arrival.is_err(), eq(true))
}
