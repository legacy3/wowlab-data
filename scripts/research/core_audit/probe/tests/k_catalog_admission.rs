//! Track K probes: catalog support rows versus executable admission.
//!
//! Records: CSA-K-02 (Disabled raw-468 executes), CSA-K-03 (raw-334 exact-carrier wording vs
//! generic selected admission), CSA-K-05 (ordinary Passive route), MUT-K-008/014/015/016.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    HasteMultipliers, HealthPool, OffensivePower, PassiveCriticalModifierBinding,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, CritChanceActivationInput, CriticalChanceModifierCompositionInput, DataVersion,
    GameDataIdentity, HealthThresholdDirectionInput, ResolvedActionCatalog,
    ResolvedCriticalChanceModifierInput,
};
use wowlab_dbc::{AuraSubtypeKind, AuraSubtypeSupport, aura_subtype_requirement};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, SpellEffectRef, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    HeartCrusaderInputs, health_threshold_runtime_inputs,
    selected_heart_of_the_crusader_source_inputs,
};

const AURA_SPELL: u32 = 468_901;
const CHILD_SPELL: u32 = 468_902;

fn enemy_id() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn spell(raw: u32) -> SpellId {
    SpellId::new(raw).unwrap()
}

/// CSA-K-02: AURA_SUBTYPE raw 468 is catalog `Disabled` ("remains inert until its complete
/// mechanic is implemented"), yet the public CombatProgram API compiles a raw-468 aura and its
/// threshold driver executes a child that mutates health.
#[gtest]
fn defect_disabled_raw468_threshold_trigger_executes() -> Result<()> {
    let requirement = aura_subtype_requirement(AuraSubtypeKind::TriggerSpellBasedOnHealthPercent.raw())
        .expect("cataloged");
    verify_eq!(requirement.support(), AuraSubtypeSupport::Disabled)?;

    let identity = GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 468_900,
        },
        difficulty: DifficultyId::BASE,
    };
    let (data, actions) =
        health_threshold_runtime_inputs(identity, HealthThresholdDirectionInput::Above, 50.0, 10.0);
    // The applied aura really is a raw-468 carrier.
    let raw468_carriers = data
        .effect_auras()
        .iter()
        .filter(|facts| facts.subtype().raw() == 468)
        .count();
    verify_that!(raw468_carriers, ge(1))?;

    let program = CombatProgram::builder(&data, &actions).build()?;
    let player = ActorState::try_new(
        ActorId::Player,
        Vec::new(),
        Some(HealthPool::full(100.0).unwrap()),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .unwrap();
    let enemy = ActorState::try_new(
        enemy_id(),
        Vec::new(),
        Some(HealthPool::try_new(51.0, 100.0).unwrap()),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .unwrap();
    let mut state: CombatState = program.try_state(
        CombatStateInput::new(vec![player, enemy], RandomStreamIdentity::new(468, 1))
            .with_hostile_target(ActorId::Player, enemy_id()),
    )?;
    let mut output = Vec::new();
    state
        .cast(CastRequest::new(enemy_id(), enemy_id(), spell(AURA_SPELL)), &mut output)
        .expect("aura cast");
    let child_damage = output
        .iter()
        .filter(|o| matches!(o, CombatObservation::DamageDealt { execution, .. } if execution.spell() == spell(CHILD_SPELL)))
        .count();
    verify_eq!(child_damage, 1)?;
    let health = state.actor(enemy_id()).and_then(ActorState::health).unwrap().current();
    verify_eq!(health, 41.0)?;
    Ok(())
}

/// Heart of the Crusader source mutated to a complete one-effect selected package that keeps only
/// its raw-334 effect (reindexed to Core effect 1 / source index 0) and that effect's curve.
fn single_raw334_inputs() -> HeartCrusaderInputs {
    let mut inputs = selected_heart_of_the_crusader_source_inputs();
    let provider = inputs.provider_effects[0].spell().get();
    inputs
        .data
        .effects
        .retain(|e| !(e.spell_id == provider && e.aura_subtype != i32::from(AuraSubtypeKind::ModAutoAttackCritChance.raw())));
    for e in &mut inputs.data.effects {
        if e.spell_id == provider {
            e.index = 1;
        }
    }
    for s in &mut inputs.data.spells {
        if s.id == provider {
            s.source_effect_count = Some(1);
        }
    }
    // Keep only the source-index-1 (raw-334) effect point, renumbered to source index 0.
    inputs.traits.effect_points.retain(|p| p.effect_index == 1);
    for p in &mut inputs.traits.effect_points {
        p.effect_index = 0;
    }
    let curve = inputs.traits.effect_points[0].curve_id;
    inputs.traits.curves.retain(|c| c.id == curve);
    inputs.traits.curve_points.retain(|p| p.curve_id == curve);
    inputs.traits.definitions[0].effect_point_row_count = 1;
    inputs
}

/// CSA-K-03: the raw-334 catalog row says the admitted source is exact spell 406154 effect 2 in
/// the complete four-effect Heart package and that "independent or multiple raw-334 providers ...
/// and every other carrier remain fail closed". A source-valid one-effect raw-334 selected package
/// (same provider shell, owner policy all-Absent) is admitted by the final compiler and binds.
#[gtest]
fn defect_single_raw334_selected_package_admitted_despite_exact_carrier_wording() -> Result<()> {
    let inputs = single_raw334_inputs();
    let entry = inputs.entry_id;
    let fixture = inputs.freeze();
    let provider = fixture.provider_effects[0].spell();
    let effect = SpellEffectRef::new(provider, 1).unwrap();
    let mut actions = fixture.actions.clone();
    actions.critical_chance_modifiers.push(ResolvedCriticalChanceModifierInput {
        spell_id: provider.get(),
        effect_index: 1,
        composition: CriticalChanceModifierCompositionInput::Additive,
        activation: CritChanceActivationInput::SelectedTrait {
            entry_id: entry,
            effective_rank: 2,
        },
        target_health: None,
    });
    let actions = ResolvedActionCatalog::try_from_input(actions)?;
    let result = CombatProgram::builder(&fixture.data, &actions)
        .trait_source(&fixture.traits)
        .external_spell_policy(&fixture.external_policy)
        .build();
    println!("single raw-334 package: {:?}", result.as_ref().err());
    verify_that!(result.is_ok(), eq(true))?;
    let program = result.unwrap();
    let player = ActorState::try_new(
        ActorId::Player,
        Vec::new(),
        Some(HealthPool::full(100.0).unwrap()),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .unwrap();
    let state = program.try_state(
        CombatStateInput::new(vec![player], RandomStreamIdentity::new(334, 1))
            .with_passive_critical_modifier_binding(PassiveCriticalModifierBinding::new(
                ActorId::Player,
                effect,
            )),
    );
    println!("single raw-334 state: {:?}", state.as_ref().err());
    verify_that!(state.is_ok(), eq(true))?;
    Ok(())
}

/// CSA-K-03 (second witness): the raw-290 ModAllCritChance catalog row says "raw 416 incoming-proc
/// provenance and unselected or multi-rank traits remain fail closed". A source-valid two-rank
/// Canny Strikes mutation (Set curve 2 -> 4) compiles at effective rank 2 and binds.
#[gtest]
fn defect_two_rank_raw290_selected_package_admitted_despite_one_rank_wording() -> Result<()> {
    use wowlab_data::{TraitCurveInput, TraitCurvePointInput, TraitEffectPointInput};
    let mut inputs = wowlab_test_support::selected_canny_strikes_source_inputs();
    let definition = inputs.definition_id;
    inputs.traits.entries[0].max_ranks = 2;
    inputs.traits.definitions[0].effect_point_row_count = 1;
    inputs.traits.effect_points.push(TraitEffectPointInput {
        id: 990_001,
        definition_id: definition,
        effect_index: 0,
        operation_type: 0,
        curve_id: 990_002,
    });
    inputs.traits.curves.push(TraitCurveInput {
        id: 990_002,
        curve_type: 0,
        flags: 0,
        point_row_count: 2,
    });
    inputs.traits.curve_points.push(TraitCurvePointInput { id: 990_003, curve_id: 990_002, order_index: 0, x: 1.0, y: 2.0 });
    inputs.traits.curve_points.push(TraitCurvePointInput { id: 990_004, curve_id: 990_002, order_index: 1, x: 2.0, y: 4.0 });
    let entry = inputs.entry_id;
    let effect = inputs.provider_effect;
    let fixture = inputs.freeze();
    let policy = wowlab_test_support::absent_spell_external_policy(&fixture.data);
    let mut actions = fixture.actions.clone();
    actions.critical_chance_modifiers.push(ResolvedCriticalChanceModifierInput {
        spell_id: effect.spell().get(),
        effect_index: effect.effect_index(),
        composition: CriticalChanceModifierCompositionInput::Additive,
        activation: CritChanceActivationInput::SelectedTrait {
            entry_id: entry,
            effective_rank: 2,
        },
        target_health: None,
    });
    let actions = ResolvedActionCatalog::try_from_input(actions)?;
    let result = CombatProgram::builder(&fixture.data, &actions)
        .trait_source(&fixture.traits)
        .external_spell_policy(&policy)
        .build();
    println!("two-rank raw-290 package: {:?}", result.as_ref().err());
    verify_that!(result.is_ok(), eq(true))?;
    let player = ActorState::try_new(
        ActorId::Player,
        Vec::new(),
        Some(HealthPool::full(100.0).unwrap()),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .unwrap();
    let state = result.unwrap().try_state(
        CombatStateInput::new(vec![player], RandomStreamIdentity::new(290, 2))
            .with_passive_critical_modifier_binding(PassiveCriticalModifierBinding::new(
                ActorId::Player,
                effect,
            )),
    );
    println!("two-rank raw-290 state: {:?}", state.as_ref().err());
    verify_that!(state.is_ok(), eq(true))?;
    Ok(())
}

/// EP-K-011 / MUT-K-008: the live-shaped Canny Strikes trait carrier (raw 290, spell 1250359,
/// entry 112519) declared with ordinary `Passive` crit-chance activation compiles WITHOUT trait
/// source and WITHOUT external policy, and binds to Player; the selected-owner external-policy
/// boundary is bypassed by the ordinary passive activation route. Heart of the Crusader's raw-334
/// effect declared as ordinary passive is recorded for comparison.
#[gtest]
fn defect_trait_carrier_admitted_as_ordinary_passive_without_policy() -> Result<()> {
    let canny = wowlab_test_support::selected_canny_strikes_source_inputs().freeze();
    let mut actions = canny.actions.clone();
    actions.critical_chance_modifiers.push(ResolvedCriticalChanceModifierInput {
        spell_id: canny.provider_effect.spell().get(),
        effect_index: canny.provider_effect.effect_index(),
        composition: CriticalChanceModifierCompositionInput::Additive,
        activation: CritChanceActivationInput::Passive,
        target_health: None,
    });
    let actions = ResolvedActionCatalog::try_from_input(actions)?;
    let result = CombatProgram::builder(&canny.data, &actions).build();
    println!("ordinary passive raw-290 Canny: {:?}", result.as_ref().err());
    verify_that!(result.is_ok(), eq(true))?;
    let player = ActorState::try_new(
        ActorId::Player,
        Vec::new(),
        Some(HealthPool::full(100.0).unwrap()),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .unwrap();
    let state = result.unwrap().try_state(
        CombatStateInput::new(vec![player], RandomStreamIdentity::new(290, 9))
            .with_passive_critical_modifier_binding(PassiveCriticalModifierBinding::new(
                ActorId::Player,
                canny.provider_effect,
            )),
    );
    println!("ordinary passive raw-290 Canny state: {:?}", state.as_ref().err());
    verify_that!(state.is_ok(), eq(true))?;

    let heart = selected_heart_of_the_crusader_source_inputs().freeze();
    let mut actions = heart.actions.clone();
    actions.critical_chance_modifiers.push(ResolvedCriticalChanceModifierInput {
        spell_id: heart.provider_effects[1].spell().get(),
        effect_index: heart.provider_effects[1].effect_index(),
        composition: CriticalChanceModifierCompositionInput::Additive,
        activation: CritChanceActivationInput::Passive,
        target_health: None,
    });
    let actions = ResolvedActionCatalog::try_from_input(actions)?;
    let result = CombatProgram::builder(&heart.data, &actions).build();
    println!("ordinary passive raw-334 Heart: {:?}", result.as_ref().err());
    // Heart's raw-334 ordinary-passive form is rejected (aura options / attributes / subtype policy).
    verify_that!(result.is_err(), eq(true))?;
    Ok(())
}
