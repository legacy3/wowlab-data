//! Track A probes: complete-package classification contract (`selected_trait_package_contract`)
//! under one-dimension source mutations of the live-shaped Martial Expert package.
//!
//! Records: MUT-A-020..MUT-A-031.

use wowlab_combat::{SelectedTraitEffectRole, selected_trait_package_contract};
use wowlab_data::{EffectInput, GameData, TraitSourceCatalog, TraitSpellErrorCode};
use wowlab_model::{SpellEffectRef, SpellId};
use wowlab_test_support::{
    MartialExpertInputs, selected_martial_expert_entry, selected_martial_expert_source_inputs,
};

const PROVIDER: u32 = 429_638;

fn effect_mut(inputs: &mut MartialExpertInputs, index: u8) -> &mut EffectInput {
    inputs
        .data
        .effects
        .iter_mut()
        .find(|effect| effect.spell_id == PROVIDER && effect.index == index)
        .expect("provider effect")
}

fn provider_effect_count(inputs: &mut MartialExpertInputs, count: u8) {
    inputs
        .data
        .spells
        .iter_mut()
        .find(|spell| spell.id == PROVIDER)
        .expect("provider")
        .source_effect_count = Some(count);
}

fn freeze(inputs: MartialExpertInputs) -> (GameData, TraitSourceCatalog) {
    let fixture = inputs.freeze();

    (fixture.data, fixture.traits)
}

/// Returns `Ok(Some(roles in source order))`, `Ok(None)` for a contract refusal, or the source error.
fn roles(
    inputs: MartialExpertInputs,
    rank: u16,
) -> Result<Option<Vec<(u8, SelectedTraitEffectRole)>>, TraitSpellErrorCode> {
    let (data, traits) = freeze(inputs);
    let resolved = traits
        .try_selected_spell_effect_amounts(&data, selected_martial_expert_entry(), rank)
        .map_err(|error| error.code())?;

    Ok(selected_trait_package_contract(&data, &resolved).map(|package| {
        package
            .effects()
            .map(|effect| (effect.effect().effect_index(), effect.role()))
            .collect()
    }))
}

fn is_critical_bonus(role: SelectedTraitEffectRole) -> bool {
    matches!(
        role,
        SelectedTraitEffectRole::SpellModifier(wowlab_combat::SelectedSpellModifierValue::Percent {
            operation: wowlab_combat::SelectedSpellModifierOperation::CriticalBonus,
            ..
        })
    )
}

/// MUT-A-020 control: the live-shaped package classifies as {critical bonus, critical block}.
#[test]
fn holds_live_martial_classifies() {
    let roles = roles(selected_martial_expert_source_inputs(), 1)
        .expect("source")
        .expect("admitted");

    assert_eq!(roles.len(), 2);
    assert!(is_critical_bonus(roles[0].1));
    assert_eq!(roles[1].1, SelectedTraitEffectRole::CriticalBlockAmount);
}

/// MUT-A-021 (source order): swapping effect indices keeps each role on its own effect.
#[test]
fn holds_source_order_does_not_determine_role() {
    let mut inputs = selected_martial_expert_source_inputs();

    effect_mut(&mut inputs, 1).index = 9;
    effect_mut(&mut inputs, 2).index = 1;
    effect_mut(&mut inputs, 9).index = 2;
    inputs.data.effects.sort_by_key(|effect| (effect.spell_id, effect.index));

    let roles = roles(inputs, 1).expect("source").expect("admitted");

    assert_eq!(roles[0], (1, SelectedTraitEffectRole::CriticalBlockAmount));
    assert_eq!(roles[1].0, 2);
    assert!(is_critical_bonus(roles[1].1));
}

/// MUT-A-022 (sibling set): an unsupported third sibling (raw-29 ModStat) rejects the package.
#[test]
fn holds_unknown_sibling_rejects_package() {
    let mut inputs = selected_martial_expert_source_inputs();
    let mut extra = *effect_mut(&mut inputs, 2);

    extra.index = 3;
    extra.aura_subtype = 29;
    extra.base_points = 5.0;
    inputs.data.effects.push(extra);
    inputs.data.effects.sort_by_key(|effect| (effect.spell_id, effect.index));
    provider_effect_count(&mut inputs, 3);

    assert_eq!(roles(inputs, 1), Ok(None));
}

/// MUT-A-023 (sibling set): a source-declared sibling not retained by GameData cannot be
/// filtered away; the source resolver refuses the incomplete package.
#[test]
fn holds_unretained_sibling_rejects_source() {
    let mut inputs = selected_martial_expert_source_inputs();

    provider_effect_count(&mut inputs, 3);

    assert_eq!(roles(inputs, 1), Err(TraitSpellErrorCode::IncompleteEffects));
}

/// MUT-A-024 (sibling set / same authority): a second raw-638 sibling makes an unreviewed
/// three-effect composition and rejects.
#[test]
fn holds_repeated_block_sibling_rejects() {
    let mut inputs = selected_martial_expert_source_inputs();
    let mut extra = *effect_mut(&mut inputs, 2);

    extra.index = 3;
    inputs.data.effects.push(extra);
    inputs.data.effects.sort_by_key(|effect| (effect.spell_id, effect.index));
    provider_effect_count(&mut inputs, 3);

    assert_eq!(roles(inputs, 1), Ok(None));
}

/// MUT-A-025 (operation): SpellMod op 15 -> 16 on the bonus effect rejects the package.
#[test]
fn holds_operation_mutation_rejects() {
    let mut inputs = selected_martial_expert_source_inputs();

    effect_mut(&mut inputs, 1).misc_value_0 = 16;

    assert_eq!(roles(inputs, 1), Ok(None));
}

/// MUT-A-026 (mask/selector): empty class mask on a class-mask SpellMod rejects the package.
#[test]
fn holds_empty_class_mask_rejects() {
    let mut inputs = selected_martial_expert_source_inputs();

    effect_mut(&mut inputs, 1).spell_class_mask = [0; 4];

    assert_eq!(roles(inputs, 1), Ok(None));
}

/// MUT-A-027 (sign/range): critical-block points below -100 reject the package.
#[test]
fn holds_block_points_below_minus_hundred_rejects() {
    let mut inputs = selected_martial_expert_source_inputs();

    effect_mut(&mut inputs, 2).base_points = -150.0;

    assert_eq!(roles(inputs, 1), Ok(None));
}

/// MUT-A-028 (rank): effective rank 2 on a one-rank entry is refused by the source resolver.
#[test]
fn holds_rank_above_max_rejects() {
    assert_eq!(
        roles(selected_martial_expert_source_inputs(), 2),
        Err(TraitSpellErrorCode::UnsupportedRank)
    );
}

/// MUT-A-029 (period): a nonzero aura period on the block effect rejects the package.
#[test]
fn holds_block_period_rejects() {
    let mut inputs = selected_martial_expert_source_inputs();

    effect_mut(&mut inputs, 2).aura_period_ms = 1_000;

    assert_eq!(roles(inputs, 1), Ok(None));
}

/// MUT-A-030 (target): implicit target (1,0) -> (6,0) on the block effect rejects the package.
#[test]
fn holds_block_target_mutation_rejects() {
    let mut inputs = selected_martial_expert_source_inputs();

    effect_mut(&mut inputs, 2).implicit_target_a = 6;

    assert_eq!(roles(inputs, 1), Ok(None));
}

/// MUT-A-031 (trigger): a trigger spell on the raw-638 effect is accepted as inert by the
/// classifier (documented `trigger_is_inert` list); recorded as accepted_inert.
#[test]
fn holds_block_trigger_spell_is_accepted_as_inert() {
    let mut inputs = selected_martial_expert_source_inputs();
    let trigger = SpellId::new(1_131_001).expect("existing payload spell");

    effect_mut(&mut inputs, 2).trigger_spell_id = Some(trigger.get());

    let roles = roles(inputs, 1).expect("source").expect("admitted");

    assert_eq!(roles[1], (2, SelectedTraitEffectRole::CriticalBlockAmount));
    let _ = SpellEffectRef::new(trigger, 1);
}
