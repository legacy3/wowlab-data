//! Track J probe: raw-423 mana-cost percentage leaves fractional mana costs.
//! Core scales each mana component by `1 + points/100` in f64 with no integerization
//! (state/power_cost.rs:98-117, 120-132). The Trinity consumer stores the power cost as int32:
//! `powerCost = float(powerCost) * (1.0f + ManaCostMultiplier)` (SpellInfo.cpp:4238-4239,
//! implicit float -> int32 truncation) with ManaCostMultiplier += amount / 100.0f
//! (SpellAuraEffects.cpp:4257).

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatStateInput, HasteMultipliers,
    OffensivePower, PassivePowerCostBinding, ResourcePool,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, EffectAmountFactsInput, EffectChainFactsInput,
    EffectInput, GameData, GameDataIdentity, GameDataInput, PowerCostActivationInput,
    PowerCostModifierInput, PowerTypeInput, ResolvedActionCatalog, ResolvedActionInput,
    ResolvedActionTimingInput, ResolvedResourceSpendInput, ResolvedRootTargetInput,
    ResolvedSpellProgramInput, SpellFamilyId, SpellInput,
};
use wowlab_model::{ActorId, DifficultyId, ResourceType, SpellEffectRef, SpellId};
use wowlab_sim::RandomStreamIdentity;

const CAST_SPELL: u32 = 100;
const PERCENT_PASSIVE: u32 = 200;

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn spell(id: u32, passive: bool) -> SpellInput {
    let mut attributes = [0; 17];

    if passive {
        attributes[0] = 1 << 6;
    }

    SpellInput {
        id,
        family_id: SpellFamilyId::NONE,
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: 4,
        defense_type: 0,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: None,
        attributes,
    }
}

fn percent_effect(points: f64) -> EffectInput {
    EffectInput {
        amount_facts: EffectAmountFactsInput::NEUTRAL,
        chain_facts: EffectChainFactsInput::NEUTRAL,
        spell_id: PERCENT_PASSIVE,
        index: 1,
        kind: 6,
        aura_subtype: 423,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: 0,
        misc_value_1: 0,
        radius_index_0: 0,
        radius_index_1: 0,
        implicit_target_a: 1,
        implicit_target_b: 0,
        spell_class_mask: [0; 4],
        base_points: points,
        attack_power_coefficient: 0.0,
        spell_power_coefficient: 0.0,
        trigger_spell_id: None,
    }
}

fn spend(points: f64, cost: f64) -> Result<(f64, f64)> {
    let data = GameData::try_from_input(GameDataInput {
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        identity: identity(),
        power_types: vec![PowerTypeInput {
            power_type: i32::from(u8::from(ResourceType::Mana)),
            display_divisor: 1.0,
        }],
        spells: vec![spell(CAST_SPELL, false), spell(PERCENT_PASSIVE, true)],
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects: vec![percent_effect(points)],
        effect_attributes: Vec::new(),
    })?;
    let mut input = wowlab_test_support::empty_resolved_action_catalog_input(identity());

    input.power_cost_modifiers.push(PowerCostModifierInput {
        spell_id: PERCENT_PASSIVE,
        effect_index: 1,
        activation: PowerCostActivationInput::Passive,
    });
    input.actions.push(ResolvedActionInput {
        spell_id: CAST_SPELL,
        root_program_id: CAST_SPELL,
        target: ResolvedRootTargetInput::Caster,
        resource_spends: vec![ResolvedResourceSpendInput::fixed(0, ResourceType::Mana, cost)],
        cast_policy: wowlab_data::ResolvedCastPolicyInput::default(),
        timing: ResolvedActionTimingInput::default(),
    });
    input.programs.push(ResolvedSpellProgramInput {
        node_id: CAST_SPELL,
        spell_id: CAST_SPELL,
        activation_steps: Vec::new(),
        impacts: Vec::new(),
    });
    let actions = ResolvedActionCatalog::try_from_input(input)?;
    let program = CombatProgram::builder(&data, &actions).build()?;
    let actor = ActorState::try_new(
        ActorId::Player,
        vec![ResourcePool::try_new(ResourceType::Mana, 100.0, 100.0)?],
        None,
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )?;
    let mut state = program.try_state(
        CombatStateInput::new(vec![actor], RandomStreamIdentity::new(423, 1))
            .with_passive_power_cost_binding(PassivePowerCostBinding::new(
                ActorId::Player,
                SpellEffectRef::new(SpellId::new(PERCENT_PASSIVE).or_fail()?, 1).or_fail()?,
            )),
    )?;
    let mut output = Vec::new();

    state.cast(
        CastRequest::new(
            ActorId::Player,
            ActorId::Player,
            SpellId::new(CAST_SPELL).or_fail()?,
        ),
        &mut output,
    )?;
    let requested = output
        .iter()
        .find_map(|observation| match observation {
            CombatObservation::ResourceCostApplied { cost, .. } => Some(cost.requested()),
            _ => None,
        })
        .or_fail()?;
    let balance = state
        .actor(ActorId::Player)
        .and_then(|actor| actor.resource(ResourceType::Mana))
        .or_fail()?
        .current();

    Ok((requested, balance))
}

/// NUM-J-050 / CSA-J-06: Mana Confluence-shaped passive raw-423 -5% on a 13-mana cost.
/// Trinity: int32(13.0f * (1.0f - 0.05f)) = int32(12.35) = 12. Core charges 12.35 and leaves 87.65.
#[gtest]
fn defect_mana_cost_percent_leaves_fractional_cost() -> Result<()> {
    let trinity = (13.0_f32 * (1.0_f32 + (-5.0_f32 / 100.0_f32))) as i32;

    verify_eq!(trinity, 12)?;

    let (requested, balance) = spend(-5.0, 13.0)?;

    verify_that!(requested, near(12.35, 1e-9))?;
    verify_that!(balance, near(87.65, 1e-9))
}
