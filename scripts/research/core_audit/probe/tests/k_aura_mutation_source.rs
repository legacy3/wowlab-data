//! Track K probes: aura-mutation operations (raw 164/203/289) validate only the effect-kind
//! family; the source row's aura identity (EffectTriggerSpell), stack mode (EffectMiscValue_0:
//! 0 = add signed value, 1 = set) and signed amount are never bound to the host-resolved step.
//!
//! Records: CSA-K-01, MUT-K-009..MUT-K-012, CAT-K rows for raw 164/203/289.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatProgram, CombatState, CombatStateInput, HasteMultipliers,
    HealthPool, OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, EffectAmountFactsInput, EffectChainFactsInput,
    EffectInput, GameData, GameDataIdentity, GameDataInput, ResolvedActionCatalog,
    ResolvedActionRecipientInput, ResolvedActionTimingInput, ResolvedAuraInput,
    ResolvedAuraReapplicationInput, ResolvedProgramStepInput, ResolvedRootTargetInput, SpellInput,
};
use wowlab_dbc::SpellEffectKind;
use wowlab_model::{ActorId, AuraId, AuraKey, DifficultyId, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{empty_resolved_action_catalog_input, resolved_action_program};

const AURA_A: u32 = 7_164_001;
const AURA_B: u32 = 7_164_002;
const REMOVE: u32 = 7_164_003;
const STACKS: u32 = 7_164_004;

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 7_164,
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

fn caster_effect(spell_id: u32, kind: SpellEffectKind, misc: i32, points: f64, trigger: Option<u32>) -> EffectInput {
    EffectInput {
        spell_id,
        index: 1,
        kind: u32::from(kind.raw()),
        aura_subtype: 0,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: misc,
        misc_value_1: 0,
        radius_index_0: 0,
        radius_index_1: 0,
        implicit_target_a: 1,
        implicit_target_b: 0,
        spell_class_mask: [0; 4],
        base_points: points,
        attack_power_coefficient: 0.0,
        spell_power_coefficient: 0.0,
        chain_facts: EffectChainFactsInput::NEUTRAL,
        amount_facts: EffectAmountFactsInput::NEUTRAL,
        trigger_spell_id: trigger,
    }
}

/// `remove_kind` is CancelAura (164) or RemoveAura (203); its source row names `remove_trigger`.
/// `stack_misc`/`stack_points` shape the raw-289 row declared as `stack_step`.
fn inputs(
    remove_kind: SpellEffectKind,
    remove_trigger: Option<u32>,
    stack_misc: i32,
    stack_points: f64,
    stack_trigger: Option<u32>,
    stack_step: ResolvedProgramStepInput,
) -> (GameData, ResolvedActionCatalog) {
    let data = GameData::try_from_input(GameDataInput {
        identity: identity(),
        power_types: Vec::new(),
        spells: [AURA_A, AURA_B, REMOVE, STACKS].map(spell_input).to_vec(),
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        spell_missiles: Vec::new(),
        effects: vec![
            caster_effect(AURA_A, SpellEffectKind::ApplyAura, 0, 0.0, None),
            caster_effect(AURA_B, SpellEffectKind::ApplyAura, 0, 0.0, None),
            caster_effect(REMOVE, remove_kind, 0, 0.0, remove_trigger),
            caster_effect(STACKS, SpellEffectKind::ModifyAuraStacks, stack_misc, stack_points, stack_trigger),
        ],
        effect_attributes: Vec::new(),
    })
    .expect("source data valid");
    let mut actions = empty_resolved_action_catalog_input(identity());
    actions.auras.push(ResolvedAuraInput::finite(AURA_A, 60_000, 5));
    actions.auras.push(ResolvedAuraInput::finite(AURA_B, 60_000, 5));
    for (spell, step) in [
        (
            AURA_A,
            ResolvedProgramStepInput::ApplyAura {
                recipient: ResolvedActionRecipientInput::Caster,
                effect_index: 1,
                aura_id: AURA_A,
                stacks: 1,
                reapplication: ResolvedAuraReapplicationInput::RestartLifetime,
            },
        ),
        (
            AURA_B,
            ResolvedProgramStepInput::ApplyAura {
                recipient: ResolvedActionRecipientInput::Caster,
                effect_index: 1,
                aura_id: AURA_B,
                stacks: 1,
                reapplication: ResolvedAuraReapplicationInput::RestartLifetime,
            },
        ),
        (
            REMOVE,
            ResolvedProgramStepInput::RemoveAura {
                recipient: ResolvedActionRecipientInput::Caster,
                effect_index: 1,
                aura_id: AURA_A,
            },
        ),
        (STACKS, stack_step),
    ] {
        let (action, program) = resolved_action_program(
            spell,
            ResolvedRootTargetInput::Caster,
            Vec::new(),
            ResolvedActionTimingInput::default(),
            vec![step],
        );
        actions.actions.push(action);
        actions.programs.push(program);
    }
    let actions = ResolvedActionCatalog::try_from_input(actions).expect("actions valid");
    (data, actions)
}

fn add_stacks_a(count: u16) -> ResolvedProgramStepInput {
    ResolvedProgramStepInput::AddAuraStacks {
        recipient: ResolvedActionRecipientInput::Caster,
        effect_index: 1,
        aura_id: AURA_A,
        count,
    }
}

fn state(program: &CombatProgram) -> CombatState {
    let player = ActorState::try_new(
        ActorId::Player,
        Vec::new(),
        Some(HealthPool::full(100.0).unwrap()),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .unwrap();
    program
        .try_state(CombatStateInput::new(vec![player], RandomStreamIdentity::new(164, 1)))
        .expect("state")
}

fn cast(state: &mut CombatState, spell: u32) {
    let mut output = Vec::new();
    state
        .cast(
            CastRequest::new(ActorId::Player, ActorId::Player, SpellId::new(spell).unwrap()),
            &mut output,
        )
        .expect("cast");
}

fn key(aura: u32) -> AuraKey {
    AuraKey::new(AuraId::new(aura).unwrap(), ActorId::Player, ActorId::Player)
}

/// CSA-K-01 / MUT-K-009: a raw-203 RemoveAura row whose source EffectTriggerSpell names aura B
/// is accepted as a step removing aura A, and at runtime removes A while B stays active.
/// Trinity `Spell::EffectRemoveAura` removes `effectInfo->TriggerSpell` (SpellEffects.cpp:5152-5161).
#[gtest]
fn defect_remove_aura_ignores_source_trigger_identity() -> Result<()> {
    for kind in [SpellEffectKind::RemoveAura, SpellEffectKind::CancelAura] {
        let (data, actions) = inputs(kind, Some(AURA_B), 0, 1.0, Some(AURA_A), add_stacks_a(1));
        let program = CombatProgram::builder(&data, &actions).build()?;
        let mut state = state(&program);
        cast(&mut state, AURA_A);
        cast(&mut state, AURA_B);
        verify_that!(state.active_aura(key(AURA_A)).is_some(), eq(true))?;
        verify_that!(state.active_aura(key(AURA_B)).is_some(), eq(true))?;
        cast(&mut state, REMOVE);
        // Source row names B; Core removes the host-declared A and leaves B.
        verify_that!(state.active_aura(key(AURA_A)).is_none(), eq(true))?;
        verify_that!(state.active_aura(key(AURA_B)).is_some(), eq(true))?;
    }
    Ok(())
}

/// CSA-K-01 / MUT-K-010: a raw-289 row in Set mode (EffectMiscValue_0 = 1, "set stacks to 2",
/// the shape of 21 live rows e.g. 1253147 "[DNT] Health Aura - Set to 3") is admitted as an
/// additive step; from 1 stack it yields 3 where the source semantic (Trinity
/// `Aura::SetStackAmount`, SpellEffects.cpp:6135-6141) yields 2.
#[gtest]
fn defect_modify_aura_stacks_set_mode_admitted_as_add() -> Result<()> {
    let (data, actions) = inputs(SpellEffectKind::RemoveAura, Some(AURA_A), 1, 2.0, Some(AURA_A), add_stacks_a(2));
    let program = CombatProgram::builder(&data, &actions).build()?;
    let mut state = state(&program);
    cast(&mut state, AURA_A);
    let mut output = Vec::new();
    // From 1 stack: Core's add-2 gives 3; the source Set-2 semantic gives 2.
    state
        .cast(CastRequest::new(ActorId::Player, ActorId::Player, SpellId::new(STACKS).unwrap()), &mut output)
        .expect("cast");
    verify_eq!(state.active_aura(key(AURA_A)).map(|a| a.stacks()), Some(3))?;
    Ok(())
}

/// CSA-K-01 / MUT-K-011: the same raw-289 source row (add mode, +1 points, trigger A) compiles
/// as a RemoveAuraStacks step of 4 on aura B: neither direction/sign, count, nor aura identity is
/// bound to the source row.
#[gtest]
fn defect_modify_aura_stacks_direction_count_identity_unbound() -> Result<()> {
    let step = ResolvedProgramStepInput::RemoveAuraStacks {
        recipient: ResolvedActionRecipientInput::Caster,
        effect_index: 1,
        aura_id: AURA_B,
        count: 4,
    };
    let (data, actions) = inputs(SpellEffectKind::RemoveAura, Some(AURA_A), 0, 1.0, Some(AURA_A), step);
    let result = CombatProgram::builder(&data, &actions).build();
    println!("unbound raw-289 step: {:?}", result.as_ref().err());
    verify_that!(result.is_ok(), eq(true))?;
    Ok(())
}

/// MUT-K-012 (context for CSA-K-01): an ApplyAura row of spell A declared as applying aura B
/// compiles and creates aura B. Semantic consumers (e.g. damage_modifier.rs:617-625,
/// periodic.rs:1121) bind aura identity to the effect's own spell, but a bare application is
/// host-identified only (selected_owner.rs:37: "a program may apply an aura owned by another spell").
#[gtest]
fn accepted_apply_aura_row_of_a_applies_foreign_aura_b() -> Result<()> {
    let (data, _) = inputs(SpellEffectKind::RemoveAura, Some(AURA_A), 0, 1.0, Some(AURA_A), add_stacks_a(1));
    let mut actions = empty_resolved_action_catalog_input(identity());
    actions.auras.push(ResolvedAuraInput::finite(AURA_B, 60_000, 5));
    let (action, program) = resolved_action_program(
        AURA_A,
        ResolvedRootTargetInput::Caster,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        vec![ResolvedProgramStepInput::ApplyAura {
            recipient: ResolvedActionRecipientInput::Caster,
            effect_index: 1,
            aura_id: AURA_B,
            stacks: 1,
            reapplication: ResolvedAuraReapplicationInput::RestartLifetime,
        }],
    );
    actions.actions.push(action);
    actions.programs.push(program);
    let actions = ResolvedActionCatalog::try_from_input(actions)?;
    let program = CombatProgram::builder(&data, &actions).build()?;
    let mut state = state(&program);
    cast(&mut state, AURA_A);
    verify_that!(state.active_aura(key(AURA_B)).is_some(), eq(true))?;
    verify_that!(state.active_aura(key(AURA_A)).is_none(), eq(true))?;
    Ok(())
}
