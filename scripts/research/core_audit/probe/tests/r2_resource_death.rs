//! Reviewer R2 (false negatives): resource authorities after actor death (CSA-R2-01).
//!
//! Source-shaped public-API program:
//! - NUKE (62_003): direct school damage (kind 2) of 20_000 on the primary target (lethal).
//! - ENERGIZE (62_004): Energize (kind 30) +100 Mana to the primary target (GainResource Target).
//! The External target owns a Mana pool (0 / 1000) with a passive flow of +10 Mana/s.
//!
//! Direct consumer: Trinity regenerates power only while alive (`Player::Update` `if (IsAlive())
//! RegenerateAll()`, Player.cpp:1025-1029; creatures regenerate only in the ALIVE death state) and
//! `Spell::EffectEnergize` returns for a dead unit target (SpellEffects.cpp:1579-1580).
//! Core: `ActorDied` is pushed at the lethal commit but no resource authority observes death; the
//! passive-flow projection keeps accruing on the corpse and `ExecutionResourceProjection::quote_gain`
//! (execution/resource.rs:271-311) has no liveness gate (only `quote_drain` has one, :336-340).

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    HasteMultipliers, HealthPool, OffensivePower, PassiveResourceFlow, ResourcePool,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, EffectInput, GameData, GameDataIdentity, GameDataInput,
    ResolvedActionCatalog, ResolvedActionInput, ResolvedActionRecipientInput,
    ResolvedActionTimingInput, ResolvedProgramStepInput, ResolvedRootTargetInput,
    ResolvedSpellProgramInput, ResourceGainAmountInput, SpellInput,
};
use wowlab_model::{ActorId, DifficultyId, RecoveryRate, ResourceType, SimTime, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::partition_program_steps;

const NUKE_RAW: u32 = 62_003;
const ENERGIZE_RAW: u32 = 62_004;

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn spell_input(id: u32) -> SpellInput {
    SpellInput {
        id,
        family_id: wowlab_data::SpellFamilyId::NONE,
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: 0x4,
        defense_type: 0,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: None,
        attributes: [0; 17],
    }
}

fn effect(spell_id: u32, kind: u32, misc: i32, base_points: f64) -> EffectInput {
    EffectInput {
        amount_facts: wowlab_data::EffectAmountFactsInput::NEUTRAL,
        chain_facts: wowlab_data::EffectChainFactsInput::NEUTRAL,
        spell_id,
        index: 1,
        kind,
        aura_subtype: 0,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: misc,
        misc_value_1: 0,
        radius_index_0: 0,
        radius_index_1: 0,
        implicit_target_a: 0,
        implicit_target_b: 0,
        spell_class_mask: [0; 4],
        base_points,
        attack_power_coefficient: 0.0,
        spell_power_coefficient: 0.0,
        trigger_spell_id: None,
    }
}

fn program(node: u32, steps: Vec<ResolvedProgramStepInput>) -> ResolvedSpellProgramInput {
    let (activation_steps, impacts) = partition_program_steps(steps);
    ResolvedSpellProgramInput {
        node_id: node,
        spell_id: node,
        activation_steps,
        impacts,
    }
}

fn action(spell_id: u32) -> ResolvedActionInput {
    ResolvedActionInput {
        spell_id,
        root_program_id: spell_id,
        target: ResolvedRootTargetInput::PrimaryTarget,
        resource_spends: Vec::new(),
        cast_policy: wowlab_data::ResolvedCastPolicyInput::default(),
        timing: ResolvedActionTimingInput::default(),
    }
}

fn build() -> Result<CombatProgram> {
    let identity = identity();
    let data = GameData::try_from_input(GameDataInput {
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        power_types: Vec::new(),
        identity,
        spells: vec![spell_input(NUKE_RAW), spell_input(ENERGIZE_RAW)],
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effect_attributes: Vec::new(),
        effects: vec![
            effect(NUKE_RAW, 2, 0, 20_000.0),
            effect(ENERGIZE_RAW, 30, i32::from(u8::from(ResourceType::Mana)), 100.0),
        ],
    })?;
    let mut input = wowlab_test_support::empty_resolved_action_catalog_input(identity);
    input.actions = vec![action(NUKE_RAW), action(ENERGIZE_RAW)];
    input.programs = vec![
        program(
            NUKE_RAW,
            vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
        ),
        program(
            ENERGIZE_RAW,
            vec![ResolvedProgramStepInput::GainResource {
                recipient: ResolvedActionRecipientInput::Target,
                effect_index: 1,
                resource: ResourceType::Mana,
                amount: ResourceGainAmountInput::Fixed { amount: 100.0 },
            }],
        ),
    ];
    let actions = ResolvedActionCatalog::try_from_input(input)?;

    Ok(CombatProgram::builder(&data, &actions).build()?)
}

fn state(program: &CombatProgram) -> Result<CombatState> {
    state_with_mana(program, 0.0)
}

fn state_with_mana(program: &CombatProgram, mana: f64) -> Result<CombatState> {
    let player = ActorState::try_new(
        ActorId::Player,
        Vec::new(),
        Some(HealthPool::full(10_000.0)?),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )?;
    let target = ActorState::try_new(
        ActorId::External,
        vec![ResourcePool::try_new(ResourceType::Mana, mana, 1_000.0)?],
        Some(HealthPool::full(10_000.0)?),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )?;

    Ok(program.try_state(
        CombatStateInput::new(vec![player, target], RandomStreamIdentity::new(41, 43))
            .with_hostile_target(ActorId::Player, ActorId::External)
            .with_passive_resource_flow(PassiveResourceFlow::try_new(
                ActorId::External,
                ResourceType::Mana,
                10.0,
                RecoveryRate::Fixed,
            )?),
    )?)
}

fn cast(state: &mut CombatState, raw: u32) -> Result<Vec<CombatObservation>> {
    let mut output = Vec::new();
    state
        .cast(
            CastRequest::new(ActorId::Player, ActorId::External, SpellId::new(raw).or_fail()?),
            &mut output,
        )?;
    Ok(output)
}

fn target_mana(state: &CombatState) -> Result<f64> {
    Ok(state
        .resource_observation(ActorId::External, ResourceType::Mana)
        .or_fail()?
        .projected())
}

fn target_alive(state: &CombatState) -> Result<bool> {
    Ok(state
        .actor(ActorId::External)
        .and_then(ActorState::health)
        .or_fail()?
        .is_alive())
}

/// CSA-R2-01 (passive flow): the killed External keeps regenerating Mana on its corpse.
#[gtest]
fn defect_passive_resource_flow_keeps_accruing_after_death() -> Result<()> {
    let program = build()?;
    let mut state = state(&program)?;

    let output = cast(&mut state, NUKE_RAW)?;
    verify_true!(
        output
            .iter()
            .any(|observation| matches!(observation, CombatObservation::ActorDied { .. }))
    )?;
    verify_false!(target_alive(&state)?)?;
    let at_death = target_mana(&state)?;
    verify_eq!(at_death, 0.0)?;

    state.advance_to(SimTime::from_millis(5_000))?;

    verify_false!(target_alive(&state)?)?;
    // Trinity: 0 (no regeneration while dead). Core: 5 s * 10/s = 50 on the corpse.
    verify_eq!(target_mana(&state)?, 50.0)
}

/// CSA-R2-01 (gain): Energize onto the dead primary target is admitted and committed.
#[gtest]
fn defect_resource_gain_commits_onto_dead_target() -> Result<()> {
    let program = build()?;
    let mut state = state(&program)?;

    cast(&mut state, NUKE_RAW)?;
    verify_false!(target_alive(&state)?)?;

    let output = cast(&mut state, ENERGIZE_RAW)?;
    verify_false!(target_alive(&state)?)?;
    // Trinity: EffectEnergize returns for !IsAlive (and CheckTarget rejects the dead target).
    // Core: +100 Mana is committed to the corpse.
    verify_eq!(target_mana(&state)?, 100.0)?;
    verify_false!(output.is_empty())
}

/// Control: the lethal nuke reports exactly one `ActorDied`, so the corpse state above is reached
/// through the ordinary death observation (drains, unlike gains, gate on liveness at
/// execution/resource.rs:336-340).
#[gtest]
fn holds_actor_died_is_reported_once_for_the_lethal_nuke() -> Result<()> {
    let program = build()?;
    let mut state = state(&program)?;
    let output = cast(&mut state, NUKE_RAW)?;

    verify_eq!(
        output
            .iter()
            .filter(|observation| matches!(observation, CombatObservation::ActorDied { .. }))
            .count(),
        1
    )
}

/// CSA-R2-01 (death transition): the primary power is retained on the corpse. Trinity's
/// `Unit::setDeathState(JUST_DIED)` sets the power-type value to 0 (Unit.cpp:9197-9198).
#[gtest]
fn defect_primary_power_is_retained_at_death() -> Result<()> {
    let program = build()?;
    let mut state = state_with_mana(&program, 500.0)?;

    cast(&mut state, NUKE_RAW)?;
    verify_false!(target_alive(&state)?)?;
    verify_eq!(target_mana(&state)?, 500.0)
}

/// MUT-R2-004 / UNK-R2-001: Energize for a pool the living recipient does not own fails the whole
/// cast with `MissingResource` (execution/resource.rs:281-321), while Trinity `Spell::EffectEnergize`
/// silently skips the effect when `GetMaxPower(power) == 0` (SpellEffects.cpp:1586-1587) and the rest
/// of the spell proceeds. Construction does not reject the missing pool.
#[gtest]
fn holds_energize_for_absent_pool_rejects_whole_cast_with_missing_resource() -> Result<()> {
    let program = build()?;
    let actor = |id| {
        ActorState::try_new(
            id,
            Vec::new(),
            Some(HealthPool::full(10_000.0).expect("health")),
            OffensivePower::ZERO,
            HasteMultipliers::UNHASTED,
        )
    };
    let mut state = program.try_state(
        CombatStateInput::new(
            vec![actor(ActorId::Player)?, actor(ActorId::External)?],
            RandomStreamIdentity::new(41, 43),
        )
        .with_hostile_target(ActorId::Player, ActorId::External),
    )?;
    let mut output = Vec::new();
    let result = state.cast(
        CastRequest::new(
            ActorId::Player,
            ActorId::External,
            SpellId::new(ENERGIZE_RAW).or_fail()?,
        ),
        &mut output,
    );

    verify_eq!(
        result.map(|_| ()).map_err(|error| error.code()),
        Err(wowlab_combat::CastErrorCode::MissingResource)
    )
}
