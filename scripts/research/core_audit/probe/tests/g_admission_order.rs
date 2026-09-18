//! Track G probes: cast admission gate order and blocker aggregation.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastErrorCode, CastRequest, CombatObservation, CombatProgram, CombatState,
    CombatStateInput, HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, GameData, GameDataIdentity, ResolvedActionCatalog,
};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, SimTime};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    PacifyFixture, StunFixture, interrupt_action_input, interrupt_game_data_input,
    interrupt_inputs, pacify_action_input, pacify_game_data_input, stun_action_input,
    stun_game_data_input,
};

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

fn actor(id: ActorId, health: HealthPool) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(health),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("probe actor is valid")
}

/// Same composition as Core's `stuns.rs::combined_pacify_fixture` (exact 346197 Stun carrier
/// plus exact 10730 Pacify carrier), with the ordinary hard cast Pacify-prevented.
fn stun_pacify_state() -> CombatState {
    let mut data_input = stun_game_data_input(identity());
    let pacify_data = pacify_game_data_input(identity());

    data_input.spells.extend(pacify_data.spells);
    data_input.effects.extend(pacify_data.effects);

    let stun = StunFixture;
    let mut action_input = stun_action_input(identity());
    let pacify_actions = pacify_action_input(identity());

    action_input.auras.extend(pacify_actions.auras);
    action_input
        .action_preventions
        .pacifies
        .extend(pacify_actions.action_preventions.pacifies);
    action_input.actions.extend(pacify_actions.actions);
    action_input.programs.extend(pacify_actions.programs);
    action_input
        .actions
        .iter_mut()
        .find(|action| action.spell_id == stun.ordinary_hard_cast().get())
        .expect("ordinary hard cast exists")
        .cast_policy
        .prevention_mask = 2;

    let data = GameData::try_from_input(data_input).expect("combined data is valid");
    let actions = ResolvedActionCatalog::try_from_input(action_input).expect("combined catalog");
    let program = CombatProgram::builder(&data, &actions)
        .build()
        .expect("combined program compiles");

    program
        .try_state(
            CombatStateInput::new(
                vec![
                    actor(ActorId::Player, HealthPool::full(1_000.0).expect("health")),
                    actor(enemy(), HealthPool::full(1_000.0).expect("health")),
                ],
                RandomStreamIdentity::new(12, 2),
            )
            .with_hostile_target(enemy(), ActorId::Player),
        )
        .expect("combined state builds")
}

/// CSA-G-03 witness: with Pacify active until 4000 ms and Stun active until 100 ms, readiness
/// reports only the Stun blocker and its 100 ms recheck (the Stun branch returns before the
/// Silence/Pacify blockers are folded by `dominant_timed_blocker`). At the 100 ms recheck the
/// action is still blocked (Pacified until 4000 ms).
#[gtest]
fn defect_stun_precedence_reports_early_recheck_under_longer_pacify() -> Result<()> {
    let stun = StunFixture;
    let pacify = PacifyFixture;
    let mut state = stun_pacify_state();
    let mut output = Vec::new();
    let ordinary = CastRequest::new(ActorId::Player, ActorId::Player, stun.ordinary_hard_cast());

    state
        .cast(
            CastRequest::new(enemy(), ActorId::Player, pacify.pacify_spell()),
            &mut output,
        )
        .or_fail()?;
    let pacified = state.cast_readiness(ordinary);

    verify_eq!(pacified.error_code(), Some(CastErrorCode::Pacified))?;
    verify_eq!(pacified.recheck_at(), Some(SimTime::from_millis(4_000)))?;

    state
        .cast(
            CastRequest::new(enemy(), ActorId::Player, stun.stun_spell()),
            &mut output,
        )
        .or_fail()?;
    let stunned = state.cast_readiness(ordinary);

    verify_eq!(stunned.error_code(), Some(CastErrorCode::Stunned))?;
    verify_eq!(stunned.recheck_at(), Some(SimTime::from_millis(100)))?;

    state.advance_to(SimTime::from_millis(100)).or_fail()?;
    let after = state.cast_readiness(ordinary);

    verify_eq!(after.error_code(), Some(CastErrorCode::Pacified))?;
    verify_eq!(after.recheck_at(), Some(SimTime::from_millis(4_000)))
}

/// CSA-G-04 witness: the exact raw-68 interrupt carrier (159006) is admitted and committed
/// against a dead explicit target as a successful no-op cast (SpellCast observation, Ok
/// outcome). Direct consumer `SpellInfo::CheckTarget` rejects with SPELL_FAILED_TARGETS_DEAD.
#[gtest]
fn defect_interrupt_carrier_commits_against_dead_target() -> Result<()> {
    let (fixture, data, actions) = interrupt_inputs(identity());
    let program = CombatProgram::builder(&data, &actions).build().or_fail()?;
    let mut state = program
        .try_state(
            CombatStateInput::new(
                vec![
                    actor(ActorId::Player, HealthPool::full(1_000.0).or_fail()?),
                    actor(enemy(), HealthPool::try_new(0.0, 1_000.0).or_fail()?),
                ],
                RandomStreamIdentity::new(68, 3),
            )
            .with_hostile_target(ActorId::Player, enemy()),
        )
        .or_fail()?;
    let request = CastRequest::new(ActorId::Player, enemy(), fixture.interrupt_spell());

    verify_eq!(state.cast_readiness(request).error_code(), None)?;

    let mut output = Vec::new();
    let outcome = state.cast(request, &mut output).or_fail()?;

    verify_that!(
        outcome
            .observations()
            .iter()
            .any(|o| matches!(o, CombatObservation::SpellCast { .. })),
        eq(true)
    )?;
    verify_that!(
        outcome
            .observations()
            .iter()
            .any(|o| matches!(o, CombatObservation::CastInterrupted { .. })),
        eq(false)
    )
}

/// MUT-G-022 boundary: raw 68 cancels only pending casts whose prevention mask contains
/// Silence; a cast without it stays pending, no school lock is installed, and the interrupt
/// cast itself still commits (live idle-target kick parity).
#[gtest]
fn holds_interrupt_ignores_cast_without_silence_prevention() -> Result<()> {
    let data = GameData::try_from_input(interrupt_game_data_input(identity())).or_fail()?;
    let mut input = interrupt_action_input(identity());

    for action in &mut input.actions {
        if action.spell_id == 900_001 {
            action.cast_policy.prevention_mask = 0;
        }
    }

    let actions = ResolvedActionCatalog::try_from_input(input).or_fail()?;
    let program = CombatProgram::builder(&data, &actions).build().or_fail()?;
    let mut state = program
        .try_state(
            CombatStateInput::new(
                vec![
                    actor(ActorId::Player, HealthPool::full(1_000.0).or_fail()?),
                    actor(enemy(), HealthPool::full(1_000.0).or_fail()?),
                ],
                RandomStreamIdentity::new(68, 4),
            )
            .with_hostile_target(ActorId::Player, enemy()),
        )
        .or_fail()?;
    let fire = wowlab_model::SpellId::new(900_001).or_fail()?;
    let mut output = Vec::new();

    state
        .begin_cast(CastRequest::new(enemy(), enemy(), fire), &mut output)
        .or_fail()?;
    output.clear();
    state
        .cast(
            CastRequest::new(ActorId::Player, enemy(), wowlab_model::SpellId::new(159_006).or_fail()?),
            &mut output,
        )
        .or_fail()?;
    verify_that!(
        output
            .iter()
            .any(|o| matches!(o, CombatObservation::CastInterrupted { .. })),
        eq(false)
    )?;
    verify_that!(state.active_cast(enemy()), some(anything()))
}
