//! Track K probes: external spell-policy trust boundary and the selected raw-638 bypass.
//!
//! Records: EP-K-*, MUT-K-*; cites CSA-A-02 (raw-638 selected owner-policy bypass).

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CombatProgram, CombatStateInput, CriticalBlockAmountBinding, HasteMultipliers,
    HealthPool, OffensivePower, PassiveSpellModifierBinding,
};
use wowlab_data::{
    BlockAmountCompositionInput, CriticalBlockActivationInput, CriticalBlockAmountInput,
    DispelTypeId, GameData, ResolvedActionCatalog, SpellAuraOptionsInput,
    SpellExternalPolicyCatalog, SpellExternalPolicyPresence, SpellModifierActivationInput,
    SpellModifierInput, SpellPolicyErrorCode,
};
use wowlab_model::ActorId;
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    MartialExpertInputs, absent_spell_external_policy_input,
    selected_martial_expert_critical_block_effect, selected_martial_expert_critical_bonus_effect,
    selected_martial_expert_entry, selected_martial_expert_source_inputs,
};

const PROVIDER: u32 = 429_638;

fn bonus_declaration() -> SpellModifierInput {
    SpellModifierInput {
        spell_id: PROVIDER,
        effect_index: 1,
        activation: SpellModifierActivationInput::SelectedTrait {
            entry_id: selected_martial_expert_entry(),
            effective_rank: 1,
        },
    }
}

fn block_declaration(effect_index: u8) -> CriticalBlockAmountInput {
    CriticalBlockAmountInput {
        spell_id: PROVIDER,
        effect_index,
        activation: CriticalBlockActivationInput::SelectedTrait {
            entry_id: selected_martial_expert_entry(),
            effective_rank: 1,
        },
        composition: BlockAmountCompositionInput::Multiplicative,
    }
}

fn actor(id: ActorId) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(1_000.0).expect("fixture health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("fixture actor")
}

/// Policy for `data` with every provider-spell field set to `presence` in one column.
fn provider_policy(
    data: &GameData,
    edit: impl Fn(&mut wowlab_data::SpellExternalPolicyInput),
) -> SpellExternalPolicyCatalog {
    let mut input = absent_spell_external_policy_input(data);
    for row in &mut input.spells {
        if row.spell_id == PROVIDER {
            edit(row);
        }
    }
    SpellExternalPolicyCatalog::try_from_input(input, data).expect("complete policy")
}

/// Complete two-effect Martial Expert declarations (raw 108 + raw 638).
fn complete_martial(fixture_actions: wowlab_data::ResolvedActionCatalogInput) -> ResolvedActionCatalog {
    let mut actions = fixture_actions;
    actions.spell_modifier_family.modifiers.push(bonus_declaration());
    actions.critical_block_amount_modifiers.push(block_declaration(2));
    ResolvedActionCatalog::try_from_input(actions).expect("complete Martial declarations")
}

/// Source-valid mutation: the Martial provider keeps only its raw-638 effect (as effect 1),
/// giving a complete one-effect selected package (arity 1 is always admitted).
fn single_block_inputs() -> MartialExpertInputs {
    let mut inputs = selected_martial_expert_source_inputs();
    inputs
        .data
        .effects
        .retain(|effect| !(effect.spell_id == PROVIDER && effect.index == 1));
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

fn single_block_actions(fixture_actions: wowlab_data::ResolvedActionCatalogInput) -> ResolvedActionCatalog {
    let mut actions = fixture_actions;
    actions.critical_block_amount_modifiers.push(block_declaration(1));
    ResolvedActionCatalog::try_from_input(actions).expect("single raw-638 declaration")
}

/// EP-K-004 / MUT-K-001: the complete Martial package without any external policy fails closed
/// (the raw-108 sibling compiler runs the owner policy).
#[gtest]
fn holds_martial_package_requires_external_policy_catalog() -> Result<()> {
    let fixture = selected_martial_expert_source_inputs().freeze();
    let actions = complete_martial(fixture.actions.clone());

    let error = CombatProgram::builder(&fixture.data, &actions)
        .trait_source(&fixture.traits)
        .build()
        .expect_err("no external policy must reject the selected package");
    println!("no-policy error: {error:?}");

    CombatProgram::builder(&fixture.data, &actions)
        .trait_source(&fixture.traits)
        .external_spell_policy(&fixture.external_policy)
        .build()?;
    Ok(())
}

/// EP-K-005 / MUT-K-002: every one of the nine policy columns set to Present or Unknown on the
/// provider rejects the complete Martial package.
#[gtest]
fn holds_martial_package_rejects_each_present_or_unknown_column() -> Result<()> {
    let fixture = selected_martial_expert_source_inputs().freeze();
    let actions = complete_martial(fixture.actions.clone());
    let setters: [fn(&mut wowlab_data::SpellExternalPolicyInput, SpellExternalPolicyPresence); 9] = [
        |r, p| r.effective_proc_entry = p,
        |r, p| r.server_script_binding = p,
        |r, p| r.aura_interrupt_policy = p,
        |r, p| r.cooldown = p,
        |r, p| r.power_cost = p,
        |r, p| r.reagents_or_totems = p,
        |r, p| r.casting_requirements = p,
        |r, p| r.empower = p,
        |r, p| r.external_aura_stack_mutation = p,
    ];
    let mut rejected = 0;
    for set in setters {
        for presence in [SpellExternalPolicyPresence::Present, SpellExternalPolicyPresence::Unknown] {
            let policy = provider_policy(&fixture.data, |row| set(row, presence));
            let result = CombatProgram::builder(&fixture.data, &actions)
                .trait_source(&fixture.traits)
                .external_spell_policy(&policy)
                .build();
            if result.is_err() {
                rejected += 1;
            }
        }
    }
    verify_eq!(rejected, 18)?;
    Ok(())
}

/// EP-K-003 / MUT-K-003: a complete policy catalog bound to a different GameData identity is
/// treated as absent by the final compiler (fails closed, not silently trusted).
#[gtest]
fn holds_identity_mismatched_policy_fails_closed() -> Result<()> {
    let fixture = selected_martial_expert_source_inputs().freeze();
    let actions = complete_martial(fixture.actions.clone());
    let mut other = selected_martial_expert_source_inputs();
    other.data.identity.version.game_build += 1;
    other.actions.game_data.version.game_build += 1;
    other.traits.identity.version.game_build += 1;
    let other = other.freeze();
    verify_ne!(other.external_policy.identity(), fixture.data.identity())?;

    let error = CombatProgram::builder(&fixture.data, &actions)
        .trait_source(&fixture.traits)
        .external_spell_policy(&other.external_policy)
        .build()
        .expect_err("identity-mismatched policy must not satisfy owner policy");
    println!("mismatch error: {error:?}");
    Ok(())
}

/// EP-K-001 / MUT-K-004: policy source validation — missing coverage, unknown spell, duplicates,
/// wrong schema and wrong identity fail closed; Absent is never inferred.
#[gtest]
fn holds_policy_catalog_rejects_incomplete_or_foreign_input() -> Result<()> {
    let fixture = selected_martial_expert_source_inputs().freeze();
    let data = &fixture.data;

    let mut missing = absent_spell_external_policy_input(data);
    missing.spells.retain(|row| row.spell_id != PROVIDER);
    verify_eq!(
        SpellExternalPolicyCatalog::try_from_input(missing, data).unwrap_err().code(),
        SpellPolicyErrorCode::MissingSpell
    )?;

    let mut unknown = absent_spell_external_policy_input(data);
    let mut extra = unknown.spells[0];
    extra.spell_id = 9_999_999;
    unknown.spells.push(extra);
    verify_eq!(
        SpellExternalPolicyCatalog::try_from_input(unknown, data).unwrap_err().code(),
        SpellPolicyErrorCode::UnknownSpell
    )?;

    let mut duplicate = absent_spell_external_policy_input(data);
    let first = duplicate.spells[0];
    duplicate.spells.push(first);
    verify_eq!(
        SpellExternalPolicyCatalog::try_from_input(duplicate, data).unwrap_err().code(),
        SpellPolicyErrorCode::DuplicateSpell
    )?;

    let mut invalid = absent_spell_external_policy_input(data);
    invalid.spells[0].spell_id = 0;
    verify_eq!(
        SpellExternalPolicyCatalog::try_from_input(invalid, data).unwrap_err().code(),
        SpellPolicyErrorCode::InvalidSpell
    )?;

    let mut schema = absent_spell_external_policy_input(data);
    schema.schema += 1;
    verify_eq!(
        SpellExternalPolicyCatalog::try_from_input(schema, data).unwrap_err().code(),
        SpellPolicyErrorCode::UnsupportedSchema
    )?;

    let mut identity = absent_spell_external_policy_input(data);
    identity.identity.version.game_build += 1;
    verify_eq!(
        SpellExternalPolicyCatalog::try_from_input(identity, data).unwrap_err().code(),
        SpellPolicyErrorCode::WrongGameDataIdentity
    )?;

    // Empty input never means "all absent".
    let mut empty = absent_spell_external_policy_input(data);
    empty.spells.clear();
    verify_eq!(
        SpellExternalPolicyCatalog::try_from_input(empty, data).unwrap_err().code(),
        SpellPolicyErrorCode::MissingSpell
    )?;
    Ok(())
}

/// CSA-A-02 (LATENT; independently reproduced by track K) / EP-K-008 / MUT-K-005: a complete one-effect selected raw-638 package is
/// admitted by the final compiler without any external spell policy, with a Present
/// effective-proc/server-script policy, and with Unknown policy — the critical-block compiler
/// never calls `selected_trait_owner_policy_issue`. The same program then constructs state.
#[gtest]
fn defect_selected_raw638_single_package_skips_external_policy() -> Result<()> {
    let fixture = single_block_inputs().freeze();
    let actions = single_block_actions(fixture.actions.clone());

    // 1. No external policy catalog at all.
    let program = CombatProgram::builder(&fixture.data, &actions)
        .trait_source(&fixture.traits)
        .build();
    verify_that!(program.is_ok(), eq(true))?;
    let program = program.unwrap();

    // 2. Present effective proc entry + server script binding on the provider.
    let present = provider_policy(&fixture.data, |row| {
        row.effective_proc_entry = SpellExternalPolicyPresence::Present;
        row.server_script_binding = SpellExternalPolicyPresence::Present;
    });
    verify_that!(
        CombatProgram::builder(&fixture.data, &actions)
            .trait_source(&fixture.traits)
            .external_spell_policy(&present)
            .build()
            .is_ok(),
        eq(true)
    )?;

    // 3. Unknown in every column.
    let unknown = provider_policy(&fixture.data, |row| {
        row.effective_proc_entry = SpellExternalPolicyPresence::Unknown;
        row.server_script_binding = SpellExternalPolicyPresence::Unknown;
        row.aura_interrupt_policy = SpellExternalPolicyPresence::Unknown;
        row.external_aura_stack_mutation = SpellExternalPolicyPresence::Unknown;
    });
    verify_that!(
        CombatProgram::builder(&fixture.data, &actions)
            .trait_source(&fixture.traits)
            .external_spell_policy(&unknown)
            .build()
            .is_ok(),
        eq(true)
    )?;

    // The unvalidated selected definition becomes executable state.
    let block = wowlab_model::SpellEffectRef::new(
        wowlab_model::SpellId::new(PROVIDER).unwrap(),
        1,
    )
    .unwrap();
    let state = program.try_state(
        CombatStateInput::new(vec![actor(ActorId::Player)], RandomStreamIdentity::new(638, 1))
            .with_critical_block_amount_binding(CriticalBlockAmountBinding::new(
                ActorId::Player,
                block,
            )),
    );
    verify_that!(state.is_ok(), eq(true))?;
    Ok(())
}

/// Discriminating control for CSA-A-02: the SAME one-effect package shape carried by a role whose
/// compiler does run owner policy is rejected. Here: the complete two-effect Martial package with
/// Present proc/script policy is rejected (see holds_martial_package_rejects_each_present_or_unknown_column),
/// while the single-effect raw-638 package with the same policy is admitted. Additionally the
/// immutable owner-neutrality facts (dispel type, AuraOptions proc charges) that
/// `selected_trait_immutable_owner_policy_issue` enforces are also skipped for raw 638.
#[gtest]
fn defect_selected_raw638_single_package_skips_owner_neutrality() -> Result<()> {
    // Dispel-typed provider.
    let mut dispel = single_block_inputs();
    for spell in &mut dispel.data.spells {
        if spell.id == PROVIDER {
            spell.dispel_type = DispelTypeId::from_raw(1);
        }
    }
    let fixture = dispel.freeze();
    let actions = single_block_actions(fixture.actions.clone());
    let result = CombatProgram::builder(&fixture.data, &actions)
        .trait_source(&fixture.traits)
        .external_spell_policy(&fixture.external_policy)
        .build();
    println!("dispel-typed raw-638 single package: {:?}", result.as_ref().err());
    verify_that!(result.is_ok(), eq(true))?;

    // AuraOptions with proc charges / PPM (a proc lifecycle) on the provider.
    let mut procs = single_block_inputs();
    procs.data.spell_aura_options.push(SpellAuraOptionsInput {
        spell_id: PROVIDER,
        cumulative_aura: 0,
        proc_category_recovery_ms: 0,
        proc_chance: 100,
        proc_charges: 1,
        procs_per_minute_id: 0,
        proc_type_mask: [0, 0],
    });
    let fixture = procs.freeze();
    let actions = single_block_actions(fixture.actions.clone());
    let result = CombatProgram::builder(&fixture.data, &actions)
        .trait_source(&fixture.traits)
        .external_spell_policy(&fixture.external_policy)
        .build();
    println!("proc-charged raw-638 single package: {:?}", result.as_ref().err());
    verify_that!(result.is_ok(), eq(true))?;

    // Control: the same proc-charged provider carrying the complete two-effect Martial package
    // is rejected by the raw-108 sibling's owner policy.
    let mut control = selected_martial_expert_source_inputs();
    control.data.spell_aura_options.push(SpellAuraOptionsInput {
        spell_id: PROVIDER,
        cumulative_aura: 0,
        proc_category_recovery_ms: 0,
        proc_chance: 100,
        proc_charges: 1,
        procs_per_minute_id: 0,
        proc_type_mask: [0, 0],
    });
    let control = control.freeze();
    let actions = complete_martial(control.actions.clone());
    verify_that!(
        CombatProgram::builder(&control.data, &actions)
            .trait_source(&control.traits)
            .external_spell_policy(&control.external_policy)
            .build()
            .is_err(),
        eq(true)
    )?;
    Ok(())
}

/// Sanity: the Martial binding types used above are still the public ones (compile guard).
#[allow(dead_code)]
fn _binding_types() -> (PassiveSpellModifierBinding, wowlab_model::SpellEffectRef, wowlab_model::SpellEffectRef) {
    (
        PassiveSpellModifierBinding::new(ActorId::Player, selected_martial_expert_critical_bonus_effect()),
        selected_martial_expert_critical_bonus_effect(),
        selected_martial_expert_critical_block_effect(),
    )
}

/// EP-K-011 / MUT-K-006: the live Martial Expert raw-638 effect (429638:2) declared with ordinary
/// `Passive` activation compiles alone — without its raw-108 sibling, without a trait source and
/// without any external policy — and binds, although the raw-638 catalog row states that the
/// sibling "is now admitted only as the same complete selected Martial Expert package, and final
/// compilation plus state construction require both declarations and both same-actor bindings".
#[gtest]
fn defect_live_martial_block_admitted_alone_as_ordinary_passive() -> Result<()> {
    let fixture = selected_martial_expert_source_inputs().freeze();
    let mut actions = fixture.actions.clone();
    actions.critical_block_amount_modifiers.push(CriticalBlockAmountInput {
        spell_id: PROVIDER,
        effect_index: 2,
        activation: CriticalBlockActivationInput::Passive,
        composition: BlockAmountCompositionInput::Multiplicative,
    });
    let actions = ResolvedActionCatalog::try_from_input(actions)?;
    let result = CombatProgram::builder(&fixture.data, &actions).build();
    println!("ordinary passive Martial raw-638: {:?}", result.as_ref().err());
    verify_that!(result.is_ok(), eq(true))?;
    let block = selected_martial_expert_critical_block_effect();
    let state = result.unwrap().try_state(
        CombatStateInput::new(vec![actor(ActorId::Player)], RandomStreamIdentity::new(638, 2))
            .with_critical_block_amount_binding(CriticalBlockAmountBinding::new(ActorId::Player, block)),
    );
    println!("ordinary passive Martial raw-638 state: {:?}", state.as_ref().err());
    verify_that!(state.is_ok(), eq(true))?;
    Ok(())
}

/// EP-K-012 / MUT-K-007: the single-effect selected raw-638 package additionally carrying an
/// EffectTriggerSpell (accepted as "inert" for selected activation, critical_block_amount.rs:223-228)
/// compiles with a Present effective-proc policy — the inertness premise (no effective proc entry)
/// is never proved on this path.
#[gtest]
fn defect_selected_raw638_trigger_accepted_inert_with_present_proc() -> Result<()> {
    let mut inputs = single_block_inputs();
    for effect in &mut inputs.data.effects {
        if effect.spell_id == PROVIDER {
            effect.trigger_spell_id = Some(PROVIDER);
        }
    }
    let fixture = inputs.freeze();
    let actions = single_block_actions(fixture.actions.clone());
    let present = provider_policy(&fixture.data, |row| {
        row.effective_proc_entry = SpellExternalPolicyPresence::Present;
    });
    let result = CombatProgram::builder(&fixture.data, &actions)
        .trait_source(&fixture.traits)
        .external_spell_policy(&present)
        .build();
    println!("triggered raw-638 with Present proc: {:?}", result.as_ref().err());
    verify_that!(result.is_ok(), eq(true))?;
    Ok(())
}
