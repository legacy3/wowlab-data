//! Track C probes: selected rank amounts reach the final critical-chance compiler.
//!
//! Proves MUT-C-010 / PL-C-001 (holds): a two-rank raw-290 package shaped exactly like
//! Keen Eyesight (spell 378004, entry 126473, definition 131299, Set curve 77725: rank 1 -> 2,
//! rank 2 -> 4, authored +2) contributes the *selected* rank amount, not the authored points.
use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    CriticalStrikeBaseline, CriticalStrikeChances, HasteMultipliers, HealthPool, OffensivePower,
    PassiveCriticalModifierBinding,
};
use wowlab_data::{
    CriticalChanceModifierActivationInput, CriticalChanceModifierCompositionInput,
    EffectAmountFactsInput, EffectChainFactsInput, EffectInput, GameData,
    ResolvedActionCatalog, ResolvedActionTimingInput, ResolvedCriticalChanceModifierInput,
    ResolvedProgramStepInput, ResolvedRootTargetInput, SpellInput, TraitCurveInput,
    TraitCurvePointInput, TraitEffectPointInput, TraitSourceCatalog,
};
use wowlab_dbc::{DefenseType, ImplicitTargetKind, SpellEffectKind};
use wowlab_model::{ActorId, EnemyIndex, HitResult, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    absent_spell_external_policy, resolved_action_program, selected_canny_strikes_source_inputs,
};

const DAMAGE_RAW: u32 = 90_977;
const CURVE: u32 = 77_725;

fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn actor(id: ActorId) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(1_000_000.0).expect("valid health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

struct Built {
    program: CombatProgram,
    binding: PassiveCriticalModifierBinding,
}

fn build(effective_rank: u16) -> Built {
    let mut inputs = selected_canny_strikes_source_inputs();

    // Two-rank Set-curve shaping exactly as Keen Eyesight row 22726 / curve 77725.
    inputs.traits.entries[0].max_ranks = 2;
    inputs.traits.definitions[0].effect_point_row_count = 1;
    inputs.traits.effect_points = vec![TraitEffectPointInput {
        id: 22_726,
        definition_id: inputs.definition_id,
        effect_index: 0,
        operation_type: 0,
        curve_id: CURVE,
    }];
    inputs.traits.curves = vec![TraitCurveInput {
        id: CURVE,
        curve_type: 0,
        flags: 0,
        point_row_count: 2,
    }];
    inputs.traits.curve_points = vec![
        TraitCurvePointInput {
            id: 219_413,
            curve_id: CURVE,
            order_index: 0,
            x: 1.0,
            y: 2.0,
        },
        TraitCurvePointInput {
            id: 219_414,
            curve_id: CURVE,
            order_index: 1,
            x: 2.0,
            y: 4.0,
        },
    ];

    // One magic shadow direct-damage payload (spell channel).
    inputs.data.spells.push(SpellInput {
        id: DAMAGE_RAW,
        family_id: wowlab_data::SpellFamilyId::NONE,
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: 0x20,
        defense_type: u8::from(DefenseType::Magic),
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: None,
        attributes: [0; 17],
    });
    inputs.data.effects.push(EffectInput {
        spell_id: DAMAGE_RAW,
        index: 1,
        kind: u32::from(SpellEffectKind::SchoolDamage.raw()),
        aura_subtype: 0,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: 0,
        misc_value_1: 0,
        radius_index_0: 0,
        radius_index_1: 0,
        implicit_target_a: ImplicitTargetKind::UnitTargetEnemy.raw(),
        implicit_target_b: 0,
        spell_class_mask: [0; 4],
        base_points: 100.0,
        attack_power_coefficient: 0.0,
        spell_power_coefficient: 0.0,
        chain_facts: EffectChainFactsInput::NEUTRAL,
        amount_facts: EffectAmountFactsInput::NEUTRAL,
        trigger_spell_id: None,
    });

    let (action, program) = resolved_action_program(
        DAMAGE_RAW,
        ResolvedRootTargetInput::PrimaryTarget,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
    );

    inputs.actions.actions.push(action);
    inputs.actions.programs.push(program);
    inputs
        .actions
        .critical_chance_modifiers
        .push(ResolvedCriticalChanceModifierInput {
            spell_id: inputs.provider_effect.spell().get(),
            effect_index: inputs.provider_effect.effect_index(),
            composition: CriticalChanceModifierCompositionInput::Additive,
            activation: CriticalChanceModifierActivationInput::SelectedTrait {
                entry_id: inputs.entry_id,
                effective_rank,
            },
            target_health: None,
        });

    let provider = inputs.provider_effect;
    let data = GameData::try_from_input(inputs.data).expect("probe data valid");
    let traits = TraitSourceCatalog::try_from_input(inputs.traits, &data).expect("traits valid");
    let actions = ResolvedActionCatalog::try_from_input(inputs.actions).expect("actions valid");
    let policy = absent_spell_external_policy(&data);
    let program = CombatProgram::builder(&data, &actions)
        .trait_source(&traits)
        .external_spell_policy(&policy)
        .build()
        .expect("selected two-rank raw-290 package compiles");

    Built {
        program,
        binding: PassiveCriticalModifierBinding::new(ActorId::Player, provider),
    }
}

fn state(built: &Built, spell_chance: f64, seed: u64) -> CombatState {
    built
        .program
        .try_state(
            CombatStateInput::new(
                vec![actor(ActorId::Player), actor(enemy())],
                RandomStreamIdentity::new(seed, 0),
            )
            .with_hostile_target(ActorId::Player, enemy())
            .with_critical_strike(
                CriticalStrikeBaseline::try_new(
                    ActorId::Player,
                    CriticalStrikeChances {
                        spell: spell_chance,
                        weapon: 0.0,
                    },
                    2.0,
                )
                .expect("valid baseline"),
            )
            .with_passive_critical_modifier_binding(built.binding),
        )
        .expect("state builds")
}

fn result(built: &Built, spell_chance: f64, seed: u64) -> HitResult {
    let mut state = state(built, spell_chance, seed);
    let mut output = Vec::new();

    state
        .cast(
            CastRequest::new(
                ActorId::Player,
                enemy(),
                SpellId::new(DAMAGE_RAW).expect("spell"),
            ),
            &mut output,
        )
        .expect("damage cast commits");

    output
        .iter()
        .find_map(|observation| match observation {
            CombatObservation::DamageDealt { result, .. } => Some(*result),
            _ => None,
        })
        .expect("damage observed")
}

/// MUT-C-010 / PL-C-001: rank 2 contributes +4 points (97% -> 101% => guaranteed critical,
/// no draw), while rank 1 contributes +2 (97% -> 99% => a draw that sometimes lands normal).
#[gtest]
fn holds_selected_rank_two_amount_reaches_critical_chance() -> Result<()> {
    let rank_two = build(2);
    let rank_one = build(1);
    let mut rank_one_hits = 0;

    for seed in 0..1_000 {
        verify_eq!(result(&rank_two, 0.97, seed), HitResult::Critical)?;

        if result(&rank_one, 0.97, seed) == HitResult::Hit {
            rank_one_hits += 1;
        }
    }

    verify_that!(rank_one_hits, gt(0))
}
