//! Track A probes: selected-passive owner policy at the final raw-638 critical-block compiler.
//!
//! Proves CSA-A-02 (a single-effect selected raw-638 package compiles without any immutable
//! owner-policy proof: no external policy catalog, a Present proc entry, or a non-neutral owner)
//! and the controls MUT-A-010..MUT-A-013.

use wowlab_combat::{CombatProgram, CombatProgramBuildError};
use wowlab_data::{
    BlockAmountCompositionInput, CriticalBlockActivationInput, CriticalBlockAmountInput,
    ResolvedActionCatalog, SpellExternalPolicyCatalog, SpellExternalPolicyPresence,
};
use wowlab_dbc::DefenseType;
use wowlab_test_support::{
    MartialExpertFixture, MartialExpertInputs, absent_spell_external_policy_input,
    selected_martial_expert_entry, selected_martial_expert_source_inputs,
};

const PROVIDER: u32 = 429_638;

/// Martial Expert reduced to a single-effect raw-638 selected package (the spell-modifier sibling
/// removed; the block effect renumbered to index 1; source effect count 1).
fn singleton_block_inputs() -> MartialExpertInputs {
    let mut inputs = selected_martial_expert_source_inputs();

    inputs.data.effects.retain(|effect| {
        !(effect.spell_id == PROVIDER && effect.index == 1)
    });
    for effect in &mut inputs.data.effects {
        if effect.spell_id == PROVIDER {
            effect.index = 1;
        }
    }
    for spell in &mut inputs.data.spells {
        if spell.id == PROVIDER {
            spell.source_effect_count = Some(1);
        }
    }
    inputs
}

fn declare_selected_block(actions: &mut wowlab_data::ResolvedActionCatalogInput, index: u8) {
    actions
        .critical_block_amount_modifiers
        .push(CriticalBlockAmountInput {
            spell_id: PROVIDER,
            effect_index: index,
            activation: CriticalBlockActivationInput::SelectedTrait {
                entry_id: selected_martial_expert_entry(),
                effective_rank: 1,
            },
            composition: BlockAmountCompositionInput::Multiplicative,
        });
}

fn policy_with_present_proc(fixture: &MartialExpertFixture) -> SpellExternalPolicyCatalog {
    let mut input = absent_spell_external_policy_input(&fixture.data);

    for spell in &mut input.spells {
        if spell.spell_id == PROVIDER {
            spell.effective_proc_entry = SpellExternalPolicyPresence::Present;
            spell.server_script_binding = SpellExternalPolicyPresence::Unknown;
        }
    }
    SpellExternalPolicyCatalog::try_from_input(input, &fixture.data).expect("policy")
}

fn build(
    fixture: &MartialExpertFixture,
    policy: Option<&SpellExternalPolicyCatalog>,
) -> Result<CombatProgram, CombatProgramBuildError> {
    let actions = ResolvedActionCatalog::try_from_input(fixture.actions.clone()).expect("actions");
    let builder = CombatProgram::builder(&fixture.data, &actions).trait_source(&fixture.traits);

    match policy {
        Some(policy) => builder.external_spell_policy(policy).build(),
        None => builder.build(),
    }
}

/// MUT-A-010 control: the singleton raw-638 package compiles with a clean immutable owner.
#[test]
fn holds_singleton_selected_block_with_clean_owner_compiles() {
    let mut inputs = singleton_block_inputs();

    declare_selected_block(&mut inputs.actions, 1);
    let fixture = inputs.freeze();

    build(&fixture, Some(&fixture.external_policy)).expect("clean singleton compiles");
}

/// CSA-A-02 / MUT-A-011: with NO external spell-policy catalog at all, the singleton selected
/// raw-638 package still compiles although the builder documents fail-closed selected admission.
#[test]
fn defect_singleton_selected_block_compiles_without_external_policy() {
    let mut inputs = singleton_block_inputs();

    declare_selected_block(&mut inputs.actions, 1);
    let fixture = inputs.freeze();

    build(&fixture, None).expect("DEFECT: selected raw-638 admitted without external policy");
}

/// CSA-A-02 / MUT-A-012: a Present effective proc entry and Unknown server script on the owner
/// do not reject the singleton selected raw-638 package.
#[test]
fn defect_singleton_selected_block_ignores_present_proc_policy() {
    let mut inputs = singleton_block_inputs();

    declare_selected_block(&mut inputs.actions, 1);
    let fixture = inputs.freeze();
    let policy = policy_with_present_proc(&fixture);

    build(&fixture, Some(&policy)).expect("DEFECT: proc/script owner admitted");
}

/// CSA-A-02 / MUT-A-013: a Melee-defense owner (non-neutral owner, rejected by every other
/// selected compiler through selected_trait_immutable_owner_policy_issue) is admitted.
#[test]
fn defect_singleton_selected_block_ignores_owner_defense() {
    let mut inputs = singleton_block_inputs();

    for spell in &mut inputs.data.spells {
        if spell.id == PROVIDER {
            spell.defense_type = DefenseType::Melee.into();
        }
    }
    declare_selected_block(&mut inputs.actions, 1);
    let fixture = inputs.freeze();

    build(&fixture, Some(&fixture.external_policy)).expect("DEFECT: non-neutral owner admitted");
}

/// MUT-A-014 control: the live two-effect Martial Expert package with the same missing external
/// policy is rejected, because the spell-modifier sibling compiler proves the owner policy.
#[test]
fn holds_two_effect_martial_rejects_without_external_policy() {
    let mut inputs = selected_martial_expert_source_inputs();

    inputs
        .actions
        .spell_modifier_family
        .modifiers
        .push(wowlab_data::SpellModifierInput {
            spell_id: PROVIDER,
            effect_index: 1,
            activation: wowlab_data::SpellModifierActivationInput::SelectedTrait {
                entry_id: selected_martial_expert_entry(),
                effective_rank: 1,
            },
        });
    declare_selected_block(&mut inputs.actions, 2);
    let fixture = inputs.freeze();

    build(&fixture, Some(&fixture.external_policy)).expect("clean two-effect package compiles");
    let error = build(&fixture, None).expect_err("two-effect package rejects without policy");
    let rendered = format!("{error}");

    assert!(
        rendered.contains("external proc, script"),
        "unexpected rejection: {rendered}"
    );
}

fn set_owner_attribute_416(inputs: &mut MartialExpertInputs) {
    for spell in &mut inputs.data.spells {
        if spell.id == PROVIDER {
            // Raw attribute 416 (AllowClassAbilityProcs) = word 13, bit 0.
            spell.attributes[13] |= 1;
        }
    }
}

/// CSA-A-03 / MUT-A-015: raw-416 on a Passive owner is accepted by the shared selected owner policy
/// (selected_trait_package.rs:513-517) for a singleton selected spell-modifier package.
#[test]
fn holds_singleton_selected_spell_modifier_accepts_owner_attribute_416() {
    let mut inputs = selected_martial_expert_source_inputs();

    inputs
        .data
        .effects
        .retain(|effect| !(effect.spell_id == PROVIDER && effect.index == 2));
    for spell in &mut inputs.data.spells {
        if spell.id == PROVIDER {
            spell.source_effect_count = Some(1);
        }
    }
    set_owner_attribute_416(&mut inputs);
    inputs
        .actions
        .spell_modifier_family
        .modifiers
        .push(wowlab_data::SpellModifierInput {
            spell_id: PROVIDER,
            effect_index: 1,
            activation: wowlab_data::SpellModifierActivationInput::SelectedTrait {
                entry_id: selected_martial_expert_entry(),
                effective_rank: 1,
            },
        });
    let fixture = inputs.freeze();

    build(&fixture, Some(&fixture.external_policy)).expect("shared policy admits 416");
}

/// CSA-A-03 / MUT-A-016: the same raw-416 owner is rejected by the raw-638 compiler's private
/// owner-attribute check, so the two-effect package fails although the shared policy admits it.
#[test]
fn holds_two_effect_package_with_owner_attribute_416_rejected_by_block_compiler() {
    let mut inputs = selected_martial_expert_source_inputs();

    set_owner_attribute_416(&mut inputs);
    inputs
        .actions
        .spell_modifier_family
        .modifiers
        .push(wowlab_data::SpellModifierInput {
            spell_id: PROVIDER,
            effect_index: 1,
            activation: wowlab_data::SpellModifierActivationInput::SelectedTrait {
                entry_id: selected_martial_expert_entry(),
                effective_rank: 1,
            },
        });
    declare_selected_block(&mut inputs.actions, 2);
    let fixture = inputs.freeze();
    let error = build(&fixture, Some(&fixture.external_policy)).expect_err("block compiler rejects");
    let rendered = format!("{error:?}");

    assert!(
        rendered.contains("BlockAmount") && rendered.contains("UnsupportedSpellAttribute"),
        "{rendered}"
    );
}

/// MUT-A-017 (declaration atomicity): declaring only the SpellModifier member of the live
/// two-effect package fails the whole program build.
#[test]
fn holds_only_spell_modifier_member_declared_rejects() {
    let mut inputs = selected_martial_expert_source_inputs();

    inputs
        .actions
        .spell_modifier_family
        .modifiers
        .push(wowlab_data::SpellModifierInput {
            spell_id: PROVIDER,
            effect_index: 1,
            activation: wowlab_data::SpellModifierActivationInput::SelectedTrait {
                entry_id: selected_martial_expert_entry(),
                effective_rank: 1,
            },
        });
    let fixture = inputs.freeze();

    build(&fixture, Some(&fixture.external_policy)).expect_err("partial declaration rejects");
}

/// MUT-A-018 (declaration atomicity): declaring only the raw-638 member of the live two-effect
/// package fails the whole program build.
#[test]
fn holds_only_block_member_declared_rejects() {
    let mut inputs = selected_martial_expert_source_inputs();

    declare_selected_block(&mut inputs.actions, 2);
    let fixture = inputs.freeze();

    build(&fixture, Some(&fixture.external_policy)).expect_err("partial declaration rejects");
}
