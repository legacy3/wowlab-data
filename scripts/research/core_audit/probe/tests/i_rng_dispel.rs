//! Track I probes: raw-38 dispel RNG accounting (RNG-I-005, RNG-I-001, RNG-I-012).
//!
//! The engine's `RandomStream` is `pub(crate)`, but `CombatState: Debug` prints it verbatim.
//! Each probe counts consumed u64 draws by advancing an independent
//! `RandomStreamIdentity::stream()` with `occurs_forced(Probability::ZERO)` (exactly one u64
//! draw per call) until its Debug text equals the state's `random` field.

use googletest::prelude::*;
use wowlab_combat::{
    ActiveDefenseBaseline, ActiveDefenseCapabilities, ActorState, AttackNonLandingChances,
    BlockDefense, CastBeginOutcome, CastReadiness, CastRequest, CastTargetRelation,
    CombatObservation, CombatProgram, CombatState, CombatStateInput, DefenseFamilyChances,
    HasteMultipliers, HealthPool, OffensivePower, TargetDisposition,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex};
use wowlab_sim::{Probability, RandomStreamIdentity};
use wowlab_test_support::{DispelFixture, dispel_runtime_source_inputs};

const ITERATION: RandomStreamIdentity = RandomStreamIdentity::new(38, 0);

fn random_text(state: &CombatState) -> String {
    let text = format!("{state:?}");
    let start = text
        .find("random: RandomStream {")
        .expect("CombatState Debug exposes its random stream")
        + "random: ".len();
    let end = start + text[start..].find('}').expect("stream Debug closes") + 1;

    text[start..end].to_owned()
}

/// Number of u64 draws the state's stream is past `identity`'s first draw.
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

fn program() -> (DispelFixture, CombatProgram) {
    let (fixture, inputs) = dispel_runtime_source_inputs(identity());
    let (data, actions) = inputs.freeze();
    let program = CombatProgram::builder(&data, &actions)
        .build()
        .expect("dispel fixture compiles");

    (fixture, program)
}

fn state(program: &CombatProgram, random: RandomStreamIdentity, magic_miss: f64) -> CombatState {
    let miss = DefenseFamilyChances::try_new(magic_miss, 0.0, 0.0).expect("valid miss");
    let input = CombatStateInput::new(
        vec![actor(ActorId::Player), actor(ActorId::External), actor(enemy())],
        random,
    )
    .with_relations(vec![
        CastTargetRelation::new(ActorId::Player, enemy(), TargetDisposition::Hostile),
        CastTargetRelation::new(ActorId::External, enemy(), TargetDisposition::Hostile),
    ])
    .with_active_defense(ActiveDefenseBaseline::new(
        enemy(),
        AttackNonLandingChances::try_new(miss, 0.0, 0.0).expect("valid table"),
        BlockDefense::NONE,
        ActiveDefenseCapabilities::new(),
    ));

    program.try_state(input).expect("dispel state builds")
}

fn apply_candidates(state: &mut CombatState, fixture: DispelFixture, count: usize) {
    for spell in [fixture.blind_faith(), fixture.mage_armor()]
        .into_iter()
        .take(count)
    {
        state
            .cast(CastRequest::new(enemy(), enemy(), spell), &mut Vec::new())
            .expect("candidate aura applies");
    }
}

fn dispel(state: &mut CombatState, fixture: DispelFixture) -> Vec<CombatObservation> {
    let mut observations = Vec::new();
    let outcome = state
        .begin_cast(
            CastRequest::new(ActorId::Player, enemy(), fixture.spell()),
            &mut observations,
        )
        .expect("dispel begins");

    if let CastBeginOutcome::Pending(started) = outcome {
        state
            .advance_to_into(started.completes_at(), &mut observations)
            .expect("dispel completes");
    }

    observations
}

fn actor(id: ActorId) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(10_000.0).expect("valid health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

const fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
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

/// RNG-I-005: the committed raw-38 selection consumes exactly the canonical uniform-index draws
/// (0 candidates: none, 1: none, 2: one u64) and the impact with zero non-landing chance and no
/// critical eligibility consumes none. Aura applications consume none.
#[gtest]
fn holds_dispel_commit_draw_count_matches_canonical_uniform_index() -> Result<()> {
    let (fixture, program) = program();

    for (active, expected) in [(0_usize, 0_usize), (1, 0), (2, 1)] {
        let mut state = state(&program, ITERATION, 0.0);

        apply_candidates(&mut state, fixture, active);
        verify_that!(consumed(&state, ITERATION), some(eq(0)))?;
        dispel(&mut state, fixture);
        verify_that!(consumed(&state, ITERATION), some(eq(expected)))?;
    }

    Ok(())
}

/// RNG-I-005: a missed raw-38 impact consumes exactly the one ordered-outcome draw and no
/// selection draw, although the quote-time preview also ran the selection branch on its clone
/// for landed outcomes.
#[gtest]
fn holds_dispel_miss_consumes_only_the_impact_draw() -> Result<()> {
    let (fixture, program) = program();

    for iteration in 0..32 {
        let identity = RandomStreamIdentity::new(38, iteration);
        let mut state = state(&program, identity, 0.5);

        apply_candidates(&mut state, fixture, 2);
        let observations = dispel(&mut state, fixture);
        let missed = observations.iter().any(|observation| {
            matches!(
                observation,
                CombatObservation::ImpactResolved {
                    result: wowlab_model::HitResult::Miss,
                    ..
                }
            )
        });
        let expected = if missed { 1 } else { 2 };

        verify_that!(consumed(&state, identity), some(eq(expected)))?;
    }

    Ok(())
}

/// RNG-I-012: quote purity (gate 7). Hard-cast acceptance and repeated `cast_readiness` of the
/// raw-38 action (which clones the stream for its random-pure preview) leave the stream untouched;
/// a rejected begin_cast consumes nothing.
#[gtest]
fn holds_dispel_quotes_acceptance_and_rejection_consume_no_rng() -> Result<()> {
    let (fixture, program) = program();
    let mut state = state(&program, ITERATION, 0.5);

    apply_candidates(&mut state, fixture, 2);

    for _ in 0..8 {
        let readiness =
            state.cast_readiness(CastRequest::new(ActorId::Player, enemy(), fixture.spell()));

        verify_eq!(readiness, CastReadiness::Ready)?;
    }

    verify_that!(consumed(&state, ITERATION), some(eq(0)))?;

    let mut sink = Vec::new();
    let rejected = state
        .begin_cast(
            CastRequest::new(enemy(), ActorId::Player, fixture.spell()),
            &mut sink,
        )
        .is_err();

    verify_that!(rejected, eq(true))?;
    verify_that!(consumed(&state, ITERATION), some(eq(0)))?;

    let mut sink = Vec::new();
    let pending = matches!(
        state
            .begin_cast(
                CastRequest::new(ActorId::Player, enemy(), fixture.spell()),
                &mut sink,
            )
            .expect("hard cast accepted"),
        CastBeginOutcome::Pending(_)
    );

    verify_that!(pending, eq(true))?;
    verify_that!(consumed(&state, ITERATION), some(eq(0)))
}

/// RNG-I-010 / RNG-I-011: reset replays from the identity's first draw and reset_for_iteration
/// adopts the new identity's first draw, both after nonzero consumption.
#[gtest]
fn holds_dispel_reset_and_iteration_reset_restart_the_stream() -> Result<()> {
    let (fixture, program) = program();
    let mut state = state(&program, ITERATION, 0.5);

    apply_candidates(&mut state, fixture, 2);
    let first = dispel(&mut state, fixture);

    verify_that!(consumed(&state, ITERATION), some(gt(0)))?;
    state.reset();
    verify_that!(consumed(&state, ITERATION), some(eq(0)))?;
    apply_candidates(&mut state, fixture, 2);
    let replay = dispel(&mut state, fixture);

    verify_eq!(format!("{replay:?}"), format!("{first:?}"))?;

    let next = RandomStreamIdentity::new(38, 99);

    state.reset_for_iteration(next);
    verify_that!(consumed(&state, next), some(eq(0)))
}

/// Everything in the `CombatState` Debug text before its trailing `execution_scratch` field.
fn semantic_text(state: &CombatState) -> String {
    let text = format!("{state:?}");
    let end = text
        .find("execution_scratch:")
        .expect("CombatState Debug ends with its transient execution scratch");

    text[..end].to_owned()
}

/// RNG-I-012 / REJ-I-008: whole-state quote purity. Every non-scratch `CombatState` field
/// (actors, authorities, auras, timers, costs, and the stream) is byte-identical after repeated
/// `cast_readiness` of the preview-cloning raw-38 action and after a rejected `begin_cast`.
/// The transient `execution_scratch` DOES retain the readiness quote's program frame and impact
/// quote (observed), but every execution entry re-prepares it, so a following cast commits the
/// identical observations and draws as a state that never quoted.
#[gtest]
fn holds_dispel_readiness_and_rejection_leave_semantic_state_identical() -> Result<()> {
    let (fixture, program) = program();
    let mut quoted = state(&program, ITERATION, 0.5);
    let mut control = state(&program, ITERATION, 0.5);

    for state in [&mut quoted, &mut control] {
        apply_candidates(state, fixture, 2);
    }

    let before = semantic_text(&quoted);
    let whole_before = format!("{quoted:?}");

    for _ in 0..4 {
        let _ = quoted.cast_readiness(CastRequest::new(ActorId::Player, enemy(), fixture.spell()));
    }

    verify_eq!(semantic_text(&quoted), before.clone())?;
    verify_that!(format!("{quoted:?}") == whole_before, eq(false))?;
    verify_that!(
        format!("{quoted:?}").contains("program_frames: [ProgramFrame"),
        eq(true)
    )?;

    let mut sink = Vec::new();
    let rejected = quoted
        .begin_cast(
            CastRequest::new(enemy(), ActorId::Player, fixture.spell()),
            &mut sink,
        )
        .is_err();

    verify_that!(rejected, eq(true))?;
    verify_that!(sink.is_empty(), eq(true))?;
    verify_eq!(semantic_text(&quoted), before)?;

    let after_quotes = dispel(&mut quoted, fixture);
    let without_quotes = dispel(&mut control, fixture);

    verify_eq!(format!("{after_quotes:?}"), format!("{without_quotes:?}"))?;
    verify_eq!(semantic_text(&quoted), semantic_text(&control))
}
