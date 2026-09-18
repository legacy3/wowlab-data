//! Track G probes: cast admission, interruption, and start-recovery lifecycle.
//!
//! Every test asserts the behaviour observed at the pinned Core commit, so a Core change
//! that corrects (or moves) the behaviour makes the probe fail loudly.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastBeginOutcome, CastCompletion, CastErrorCode, CastRequest, CombatObservation,
    CombatProgram, CombatState, CombatStateInput, HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, EffectAmountFactsInput, EffectChainFactsInput,
    EffectInput, GameData, GameDataIdentity, GameDataInput, ResolvedActionCatalog,
    ResolvedActionTimingInput, ResolvedCastTimeInput, ResolvedProgramStepInput,
    ResolvedRootTargetInput, ResolvedStartRecoveryInput, SpellInput,
};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, RecoveryRate, SimTime, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    StunFixture, empty_resolved_action_catalog_input, interrupt_action_input,
    interrupt_game_data_input, resolved_action_program, stun_action_input, stun_game_data_input,
};

const LETHAL_RAW: u32 = 700_001;
const HARD_DAMAGE_RAW: u32 = 700_002;
const FIRE_CAST_RAW: u32 = 900_001;
const NATURE_CAST_RAW: u32 = 900_002;
const ORDINARY_HARD_CAST_RAW: u32 = 946_197;
/// Fireball 133 source shape: SpellCooldowns row 29 StartRecoveryTime 1500,
/// SpellCategories row 60 StartRecoveryCategory 133, PreventionType 1.
const GCD: ResolvedStartRecoveryInput = ResolvedStartRecoveryInput {
    category_id: 133,
    base_duration_ms: 1_500,
    rate: RecoveryRate::Fixed,
    minimum_duration_ms: 0,
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

fn spell(raw: u32) -> SpellId {
    SpellId::new(raw).expect("probe spell identity is nonzero")
}

fn actor(id: ActorId, health: f64) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(health).expect("probe health is valid")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("probe actor is valid")
}

fn health(state: &CombatState, id: ActorId) -> f64 {
    state
        .actor(id)
        .and_then(ActorState::health)
        .map(|pool| pool.current())
        .expect("probe actor has health")
}

fn fire_spell(id: u32) -> SpellInput {
    SpellInput {
        id,
        family_id: wowlab_data::SpellFamilyId::NONE,
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: 0x4,
        defense_type: 1,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: None,
        attributes: [0; 17],
    }
}

fn damage_effect(spell_id: u32, base_points: f64) -> EffectInput {
    EffectInput {
        spell_id,
        index: 1,
        kind: 2,
        aura_subtype: 0,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: 0,
        misc_value_1: 0,
        radius_index_0: 0,
        radius_index_1: 0,
        implicit_target_a: 6,
        implicit_target_b: 0,
        spell_class_mask: [0; 4],
        base_points,
        attack_power_coefficient: 0.0,
        spell_power_coefficient: 0.0,
        chain_facts: EffectChainFactsInput::NEUTRAL,
        amount_facts: EffectAmountFactsInput::NEUTRAL,
        trigger_spell_id: None,
    }
}

/// Two fire damage actions: an instant lethal nuke and a one-second hard-cast nuke.
fn damage_program() -> CombatProgram {
    let data = GameData::try_from_input(GameDataInput {
        identity: identity(),
        power_types: Vec::new(),
        spells: vec![fire_spell(LETHAL_RAW), fire_spell(HARD_DAMAGE_RAW)],
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        spell_missiles: Vec::new(),
        effects: vec![
            damage_effect(LETHAL_RAW, 250.0),
            damage_effect(HARD_DAMAGE_RAW, 10.0),
        ],
        effect_attributes: Vec::new(),
    })
    .expect("probe game data is valid");
    let mut input = empty_resolved_action_catalog_input(identity());

    for (raw, cast) in [
        (LETHAL_RAW, None),
        (
            HARD_DAMAGE_RAW,
            Some(ResolvedCastTimeInput {
                base_duration_ms: 1_000,
                rate: RecoveryRate::Fixed,
                minimum_duration_ms: 0,
            }),
        ),
    ] {
        let (action, program) = resolved_action_program(
            raw,
            ResolvedRootTargetInput::PrimaryTarget,
            Vec::new(),
            ResolvedActionTimingInput {
                cast,
                ..ResolvedActionTimingInput::default()
            },
            vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
        );

        input.actions.push(action);
        input.programs.push(program);
    }

    let actions = ResolvedActionCatalog::try_from_input(input).expect("probe catalog is valid");

    CombatProgram::builder(&data, &actions)
        .build()
        .expect("probe damage program compiles")
}

fn mutual_state(program: &CombatProgram, player_health: f64) -> CombatState {
    program
        .try_state(
            CombatStateInput::new(
                vec![actor(ActorId::Player, player_health), actor(enemy(), 1_000.0)],
                RandomStreamIdentity::new(7, 7),
            )
            .with_hostile_target(ActorId::Player, enemy())
            .with_hostile_target(enemy(), ActorId::Player),
        )
        .expect("probe state builds")
}

/// CSA-G-01 witness: a caster whose health is zero still passes readiness and commits an
/// instant damaging cast; no caster-life gate exists outside caster-health-writing programs.
#[gtest]
fn defect_dead_caster_commits_instant_damage() -> Result<()> {
    let program = damage_program();
    let mut state = mutual_state(&program, 100.0);
    let mut output = Vec::new();

    state
        .cast(CastRequest::new(enemy(), ActorId::Player, spell(LETHAL_RAW)), &mut output)
        .or_fail()?;
    verify_that!(
        output
            .iter()
            .any(|o| matches!(o, CombatObservation::ActorDied { actor: ActorId::Player, .. })),
        eq(true)
    )?;
    verify_eq!(health(&state, ActorId::Player), 0.0)?;

    let request = CastRequest::new(ActorId::Player, enemy(), spell(LETHAL_RAW));

    verify_eq!(state.cast_readiness(request).error_code(), None)?;
    output.clear();
    state.cast(request, &mut output).or_fail()?;
    verify_that!(health(&state, enemy()), lt(1_000.0))?;
    verify_that!(
        output.iter().any(|o| matches!(
            o,
            CombatObservation::DamageDealt { source: ActorId::Player, target, .. } if *target == enemy()
        )),
        eq(true)
    )
}

/// CSA-G-01 witness: a pending hard cast survives its caster's death, is still reported as
/// active, and completes successfully (payload committed) at its original deadline.
#[gtest]
fn defect_pending_hard_cast_survives_caster_death_and_succeeds() -> Result<()> {
    let program = damage_program();
    let mut state = mutual_state(&program, 100.0);
    let mut output = Vec::new();
    let started = state
        .begin_cast(
            CastRequest::new(ActorId::Player, enemy(), spell(HARD_DAMAGE_RAW)),
            &mut output,
        )
        .or_fail()?;
    let CastBeginOutcome::Pending(started) = started else {
        return fail!("hard damage must be pending");
    };

    verify_eq!(started.completes_at(), SimTime::from_millis(1_000))?;
    state
        .cast(CastRequest::new(enemy(), ActorId::Player, spell(LETHAL_RAW)), &mut output)
        .or_fail()?;
    verify_eq!(health(&state, ActorId::Player), 0.0)?;
    verify_that!(state.active_cast(ActorId::Player), some(anything()))?;

    let completion = state.advance_to(SimTime::from_millis(1_000)).or_fail()?;

    verify_that!(
        completion.iter().any(|o| matches!(
            o,
            CombatObservation::CastCompleted {
                caster: ActorId::Player,
                completion: CastCompletion::Succeeded,
                ..
            }
        )),
        eq(true)
    )?;
    verify_that!(health(&state, enemy()), lt(1_000.0))
}

/// Interrupt fixture with Fireball-shaped start recovery (category 133, 1500 ms) on the
/// Fire and Nature hard casts.
fn interrupt_with_gcd_state() -> CombatState {
    let data = GameData::try_from_input(interrupt_game_data_input(identity()))
        .expect("interrupt data is valid");
    let mut input = interrupt_action_input(identity());

    for action in &mut input.actions {
        if action.spell_id == FIRE_CAST_RAW || action.spell_id == NATURE_CAST_RAW {
            action.timing.start_recovery = Some(GCD);
        }
    }

    let actions = ResolvedActionCatalog::try_from_input(input).expect("interrupt catalog is valid");
    let program = CombatProgram::builder(&data, &actions)
        .build()
        .expect("interrupt program with start recovery compiles");

    program
        .try_state(
            CombatStateInput::new(
                vec![actor(ActorId::Player, 1_000.0), actor(enemy(), 1_000.0)],
                RandomStreamIdentity::new(68, 1),
            )
            .with_hostile_target(ActorId::Player, enemy()),
        )
        .expect("interrupt state builds")
}

/// CSA-G-02 witness: after a raw-68 interrupt cancels a pending hard cast, the start recovery
/// committed at acceptance still blocks an unlocked-school action. Trinity `Spell::cancel`
/// (PREPARING -> `CancelGlobalCooldown`) and simc `action_t::interrupt_action` both reset it.
#[gtest]
fn defect_interrupt_retains_accepted_start_recovery() -> Result<()> {
    let mut state = interrupt_with_gcd_state();
    let mut output = Vec::new();

    state
        .begin_cast(
            CastRequest::new(enemy(), enemy(), spell(FIRE_CAST_RAW)),
            &mut output,
        )
        .or_fail()?;
    verify_eq!(state.advance_to(SimTime::from_millis(500)).or_fail()?.len(), 0)?;
    output.clear();
    state
        .cast(
            CastRequest::new(ActorId::Player, enemy(), spell(159_006)),
            &mut output,
        )
        .or_fail()?;
    verify_that!(
        output
            .iter()
            .any(|o| matches!(o, CombatObservation::CastInterrupted { .. })),
        eq(true)
    )?;
    verify_that!(state.active_cast(enemy()), none())?;

    let nature = state.cast_readiness(CastRequest::new(enemy(), enemy(), spell(NATURE_CAST_RAW)));

    verify_eq!(nature.error_code(), Some(CastErrorCode::StartRecovery))?;
    verify_eq!(nature.recheck_at(), Some(SimTime::from_millis(1_500)))?;
    verify_eq!(
        state.advance_to(SimTime::from_millis(1_500)).or_fail()?.len(),
        0
    )?;
    verify_eq!(
        state
            .cast_readiness(CastRequest::new(enemy(), enemy(), spell(NATURE_CAST_RAW)))
            .error_code(),
        None
    )
}

/// CSA-G-02 witness: the same retention after a raw-12 stun cancels an ordinary hard cast.
#[gtest]
fn defect_stun_interruption_retains_accepted_start_recovery() -> Result<()> {
    let fixture = StunFixture;
    let data = GameData::try_from_input(stun_game_data_input(identity())).expect("stun data");
    let mut input = stun_action_input(identity());

    for action in &mut input.actions {
        if action.spell_id == ORDINARY_HARD_CAST_RAW {
            action.timing.start_recovery = Some(GCD);
        }
    }

    let actions = ResolvedActionCatalog::try_from_input(input).expect("stun catalog");
    let program = CombatProgram::builder(&data, &actions)
        .build()
        .expect("stun program with start recovery compiles");
    let mut state = program
        .try_state(
            CombatStateInput::new(
                vec![actor(ActorId::Player, 1_000.0), actor(enemy(), 1_000.0)],
                RandomStreamIdentity::new(12, 1),
            )
            .with_hostile_target(enemy(), ActorId::Player),
        )
        .expect("stun state builds");
    let mut output = Vec::new();
    let own = CastRequest::new(ActorId::Player, ActorId::Player, fixture.ordinary_hard_cast());

    state.begin_cast(own, &mut output).or_fail()?;
    output.clear();
    state
        .cast(
            CastRequest::new(enemy(), ActorId::Player, fixture.stun_spell()),
            &mut output,
        )
        .or_fail()?;
    verify_that!(
        output
            .iter()
            .any(|o| matches!(o, CombatObservation::CastStunned { .. })),
        eq(true)
    )?;
    // Stun is 100 ms; advance past its expiry.
    state.advance_to(SimTime::from_millis(100)).or_fail()?;
    verify_that!(state.active_cast(ActorId::Player), none())?;

    let retry = state.cast_readiness(own);

    verify_eq!(retry.error_code(), Some(CastErrorCode::StartRecovery))?;
    verify_eq!(retry.recheck_at(), Some(SimTime::from_millis(1_500)))
}
