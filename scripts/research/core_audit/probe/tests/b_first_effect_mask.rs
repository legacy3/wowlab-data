//! Track B probes: operation-3 (PointsIndex0) class-mask SpellMod applicability multiplicity.
//!
//! Proves CSA-B-01: a selected raw-108 operation-3 class-mask percentage modifier is applied
//! once per payload even when the provider mask overlaps several payload class-mask bits, while
//! every other class-mask SpellMod path in Core (raw-108 operations 0/11/15/22 and raw-107
//! operations 3/11) raises or multiplies by the overlapping-bit population (Trinity
//! `SpellInfo::IsAffectedBySpellMod` popcount + `Player::GetSpellModValues` `pow(.., applyCount)`).

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    HasteMultipliers, HealthPool, OffensivePower, PassiveSpellModifierBinding,
};
use wowlab_data::SpellClassMaskInput;
use wowlab_dbc::AuraSubtypeKind;
use wowlab_model::{ActorId, SimTime, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    FlatSpellModifierFixture, FlatSpellModifierInputs, selected_periodic_percent_heal_source_inputs,
};

/// Provider raw-108 (percent) or raw-107 (flat) operation 3 with a two-bit class mask; the
/// applicand (synthetic raw-20 periodic percent heal, family 8) has `payload_mask`.
fn inputs(subtype: AuraSubtypeKind, points: f64, payload_mask: u32) -> FlatSpellModifierInputs {
    let mut inputs = selected_periodic_percent_heal_source_inputs(true);
    let provider = inputs.provider_effect.spell().get();
    let apply = inputs.apply_spell_id;

    let source = inputs
        .data
        .effects
        .iter_mut()
        .find(|effect| effect.spell_id == provider)
        .expect("provider effect");
    source.aura_subtype = i32::from(subtype.raw());
    source.misc_value_0 = 3;
    source.misc_value_1 = 0;
    source.spell_class_mask = [0b11, 0, 0, 0];
    source.base_points = points;

    let applicand = inputs
        .data
        .effects
        .iter_mut()
        .find(|effect| effect.spell_id == apply)
        .expect("applicand effect");
    // 1% of 1,000 maximum health per tick before modifiers.
    applicand.base_points = 1.0;

    inputs.data.spell_labels.clear();
    inputs.data.spell_class_masks.retain(|mask| mask.spell_id != apply);
    inputs.data.spell_class_masks.push(SpellClassMaskInput {
        spell_id: apply,
        spell_class_mask: [payload_mask, 0, 0, 0],
    });
    inputs.data.spell_class_masks.sort_by_key(|mask| mask.spell_id);
    inputs
}

fn program(fixture: &FlatSpellModifierFixture) -> CombatProgram {
    CombatProgram::builder(&fixture.data, &fixture.actions)
        .trait_source(&fixture.traits)
        .external_spell_policy(&fixture.external_policy)
        .build()
        .expect("selected operation-3 class-mask provider compiles")
}

fn state(program: &CombatProgram, fixture: &FlatSpellModifierFixture) -> CombatState {
    let input = CombatStateInput::new(
        vec![
            ActorState::try_new(
                ActorId::Player,
                Vec::new(),
                Some(HealthPool::try_new(100.0, 1_000.0).expect("valid health")),
                OffensivePower::ZERO,
                HasteMultipliers::UNHASTED,
            )
            .expect("valid Player"),
        ],
        RandomStreamIdentity::new(93, 17),
    )
    .with_passive_spell_modifier_binding(PassiveSpellModifierBinding::new(
        ActorId::Player,
        fixture.provider_effect,
    ));

    program.try_state(input).expect("valid state")
}

fn first_tick(subtype: AuraSubtypeKind, points: f64, payload_mask: u32) -> Vec<f64> {
    let fixture = inputs(subtype, points, payload_mask).freeze();
    let program = program(&fixture);
    let mut state = state(&program, &fixture);
    let mut observations = Vec::new();

    state
        .cast(
            CastRequest::new(
                ActorId::Player,
                ActorId::Player,
                SpellId::new(fixture.apply_spell_id).expect("spell"),
            ),
            &mut observations,
        )
        .expect("self aura application");

    state
        .advance_to(SimTime::from_millis(100))
        .expect("advance")
        .into_iter()
        .filter_map(|event| match event {
            CombatObservation::HealingDone { healing, .. } => Some(healing.requested()),
            _ => None,
        })
        .collect()
}

/// CSA-B-01 (defect): the raw-108 operation-3 class-mask factor is applied once even when two
/// provider bits overlap the payload. Trinity applies `pow(1.5, 2) = 2.25` (22.5 healing).
#[gtest]
fn defect_percent_first_effect_class_mask_ignores_overlapping_bit_count() -> Result<()> {
    let one_bit = first_tick(AuraSubtypeKind::AddPercentModifier, 50.0, 0b01);
    let two_bits = first_tick(AuraSubtypeKind::AddPercentModifier, 50.0, 0b11);

    verify_eq!(one_bit, vec![15.0])?;
    // Defective: identical to one bit; source-backed direct consumer gives 22.5.
    verify_eq!(two_bits, vec![15.0])
}

/// CSA-B-01 contrast (holds): the flat raw-107 operation-3 class-mask sibling path counts both
/// overlapping bits (1 point x 2 bits = 2%), matching Trinity `flat += value * applyCount`.
#[gtest]
fn holds_flat_first_effect_class_mask_counts_overlapping_bits() -> Result<()> {
    let one_bit = first_tick(AuraSubtypeKind::AddFlatModifier, 1.0, 0b01);
    let two_bits = first_tick(AuraSubtypeKind::AddFlatModifier, 1.0, 0b11);

    verify_eq!(one_bit, vec![20.0])?;
    verify_eq!(two_bits, vec![30.0])
}

/// CSA-B-02 (defect, wording/admission): a *sole-effect* one-rank raw-218 operation-3 label
/// provider compiles and executes although the raw-218 catalog row admits operation 3 only for
/// the exact three-effect Ephemeral Bond package; the generic arity-1 composition admits it.
#[gtest]
fn defect_sole_effect_raw218_operation3_label_is_admitted_beyond_catalog_row() -> Result<()> {
    let mut inputs = selected_periodic_percent_heal_source_inputs(true);
    let provider = inputs.provider_effect.spell().get();
    let apply = inputs.apply_spell_id;
    let source = inputs
        .data
        .effects
        .iter_mut()
        .find(|effect| effect.spell_id == provider)
        .expect("provider effect");

    source.aura_subtype = i32::from(AuraSubtypeKind::AddPercentLabelModifier.raw());
    source.misc_value_0 = 3;
    source.base_points = 50.0;
    inputs
        .data
        .effects
        .iter_mut()
        .find(|effect| effect.spell_id == apply)
        .expect("applicand effect")
        .base_points = 1.0;

    let fixture = inputs.freeze();
    let program = program(&fixture);
    let mut state = state(&program, &fixture);
    let mut observations = Vec::new();

    state
        .cast(
            CastRequest::new(ActorId::Player, ActorId::Player, SpellId::new(apply).or_fail()?),
            &mut observations,
        )
        .or_fail()?;
    let ticks: Vec<f64> = state
        .advance_to(SimTime::from_millis(100))
        .or_fail()?
        .into_iter()
        .filter_map(|event| match event {
            CombatObservation::HealingDone { healing, .. } => Some(healing.requested()),
            _ => None,
        })
        .collect();

    verify_eq!(ticks, vec![15.0])
}
