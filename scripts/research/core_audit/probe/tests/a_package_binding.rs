//! Track A probes: selected-passive package binding atomicity at state construction.
//!
//! Proves CSA-A-01 (family-agnostic package validator lets a package member be "bound" through
//! the discarded passive auto-attack-damage family) and the boundaries it is measured against
//! (MUT-A-001..MUT-A-006).

use wowlab_combat::{
    ActorState, CombatObservation, CombatProgram, CombatStateErrorCode, CriticalBlockAmountBinding,
    CriticalStrikeBaseline, HasteMultipliers, HealthPool, OffensivePower, PassiveAutoDamageBinding,
    PassiveSpellModifierBinding,
};
use wowlab_data::ResolvedActionCatalog;
use wowlab_engine::{
    Engine, EngineBuildError, EngineInput, EngineProgram, PreparedCriticalPackage,
    SelectedTraitEntry, StepOutcome,
};
use wowlab_model::{ActorId, EnemyIndex, HitResult, SpellEffectRef, SpellId};
use wowlab_rotation::{Action, Identifier, Rotation};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    MartialExpertFixture, selected_martial_expert_critical_block_effect,
    selected_martial_expert_critical_bonus_effect, selected_martial_expert_entry,
    selected_martial_expert_source_inputs,
};

const IDENTITY: RandomStreamIdentity = RandomStreamIdentity::new(108, 15);

const fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn actor(id: ActorId) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(10_000.0).expect("fixture health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("fixture actor")
}

/// Compiles the real Martial Expert package program (both declarations published by the candidate).
fn martial_program() -> (MartialExpertFixture, CombatProgram) {
    let fixture = selected_martial_expert_source_inputs().freeze();
    let selected = PreparedCriticalPackage::try_prepare(
        &fixture.data,
        &fixture.traits,
        SelectedTraitEntry {
            entry_id: selected_martial_expert_entry(),
            effective_rank: 1,
        },
    )
    .expect("Martial Expert candidate");
    let (actions, _) = selected
        .try_into_inputs(fixture.actions.clone(), base_input())
        .expect("candidate publication");
    let actions = ResolvedActionCatalog::try_from_input(actions).expect("actions");
    let program = CombatProgram::builder(&fixture.data, &actions)
        .trait_source(&fixture.traits)
        .external_spell_policy(&fixture.external_policy)
        .build()
        .expect("Martial Expert program");

    (fixture, program)
}

fn base_input() -> EngineInput {
    EngineInput::new(vec![actor(ActorId::Player), actor(enemy())]).with_critical_strike(
        CriticalStrikeBaseline::try_uniform(ActorId::Player, 1.0, 2.0).expect("baseline"),
    )
}

fn engine_program(combat: CombatProgram, spell: SpellId) -> EngineProgram {
    let mut rotation = Rotation::new("track A probe".to_owned());

    rotation.actions = vec![Action::Cast {
        spell: Identifier::new("cast").expect("identifier"),
        empower_rank: None,
        enabled: true,
        condition: None,
        target: None,
    }];

    EngineProgram::try_new(combat, &rotation, &|name: &Identifier| {
        (name.as_str() == "cast").then_some(spell)
    })
    .expect("engine program")
}

fn build(
    fixture: &MartialExpertFixture,
    program: &CombatProgram,
    input: EngineInput,
) -> Result<Engine, EngineBuildError> {
    Engine::try_new(
        engine_program(program.clone(), fixture.matching_damage),
        input,
        ActorId::Player,
        enemy(),
        IDENTITY,
    )
}

fn state_code(error: &EngineBuildError) -> CombatStateErrorCode {
    error
        .combat_state_error()
        .expect("combat-state failure")
        .code()
}

fn bonus() -> SpellEffectRef {
    selected_martial_expert_critical_bonus_effect()
}

fn block() -> SpellEffectRef {
    selected_martial_expert_critical_block_effect()
}

fn first_damage(engine: &mut Engine) -> (SpellId, HitResult, f64) {
    let outcome = engine.step().expect("step");
    let StepOutcome::Cast(cast) = outcome else {
        panic!("expected a cast outcome");
    };

    cast.observations()
        .iter()
        .find_map(|observation| {
            let CombatObservation::DamageDealt {
                execution,
                result,
                damage,
                ..
            } = observation
            else {
                return None;
            };

            Some((execution.root_spell(), *result, damage.requested()))
        })
        .expect("damage observation")
}

/// MUT-A-001 control: the complete package (spell-mod family + critical-block family) builds and
/// the critical-bonus half is live (100 * (1 + 1.1f32)).
#[test]
fn holds_complete_martial_package_builds() {
    let (fixture, program) = martial_program();
    let input = base_input()
        .with_passive_spell_modifier(PassiveSpellModifierBinding::new(ActorId::Player, bonus()))
        .with_critical_block_amount(CriticalBlockAmountBinding::new(ActorId::Player, block()));
    let mut engine = build(&fixture, &program, input).expect("complete package");
    let (_, result, amount) = first_damage(&mut engine);

    assert_eq!(result, HitResult::Critical);
    assert_eq!(amount, 100.0 * (1.0 + f64::from(1.1_f32)));
}

/// MUT-A-002: omitting the critical-block member rejects the whole package (incomplete).
#[test]
fn holds_missing_block_member_rejects() {
    let (fixture, program) = martial_program();
    let input = base_input()
        .with_passive_spell_modifier(PassiveSpellModifierBinding::new(ActorId::Player, bonus()));
    let error = build(&fixture, &program, input).expect_err("partial package must reject");

    assert_eq!(
        state_code(&error),
        CombatStateErrorCode::IncompleteSelectedPassivePackageBinding
    );
}

/// MUT-A-003: the critical-block member bound on a different actor (split actors) rejects.
#[test]
fn holds_split_actor_package_rejects() {
    let (fixture, program) = martial_program();
    let input = base_input()
        .with_passive_spell_modifier(PassiveSpellModifierBinding::new(ActorId::Player, bonus()))
        .with_critical_block_amount(CriticalBlockAmountBinding::new(enemy(), block()));
    let error = build(&fixture, &program, input).expect_err("split package must reject");

    assert_eq!(
        state_code(&error),
        CombatStateErrorCode::IncompleteSelectedPassivePackageBinding
    );
}

/// MUT-A-004: duplicating the critical-block member through the auto-attack family is counted by
/// the package validator (duplicate). This proves the validator treats the auto-attack family entry
/// for a non-auto-attack effect as a package member binding.
#[test]
fn holds_block_member_in_block_and_auto_attack_family_is_duplicate() {
    let (fixture, program) = martial_program();
    let input = base_input()
        .with_passive_spell_modifier(PassiveSpellModifierBinding::new(ActorId::Player, bonus()))
        .with_critical_block_amount(CriticalBlockAmountBinding::new(ActorId::Player, block()))
        .with_passive_auto_attack_damage_binding(PassiveAutoDamageBinding::new(
            ActorId::Player,
            block(),
        ));
    let error = build(&fixture, &program, input).expect_err("duplicate member must reject");

    assert_eq!(
        state_code(&error),
        CombatStateErrorCode::DuplicateSelectedPassivePackageBinding
    );
}

/// CSA-A-01 / MUT-A-005: binding the raw-638 critical-block member ONLY through the passive
/// auto-attack-damage family (no white main hand) satisfies the family-agnostic package validator,
/// the auto-attack binding is then discarded unvalidated, and the Engine builds with the critical
/// bonus half live and no critical-block binding at all: a partial package executes.
#[test]
fn defect_block_member_in_auto_attack_family_admits_partial_package() {
    let (fixture, program) = martial_program();
    let input = base_input()
        .with_passive_spell_modifier(PassiveSpellModifierBinding::new(ActorId::Player, bonus()))
        .with_passive_auto_attack_damage_binding(PassiveAutoDamageBinding::new(
            ActorId::Player,
            block(),
        ));

    assert!(input.critical_block_amount_bindings().is_empty());

    let mut engine = build(&fixture, &program, input)
        .expect("DEFECT: package validator accepts a wrong-family member binding");
    let (spell, result, amount) = first_damage(&mut engine);

    assert_eq!(spell, fixture.matching_damage);
    assert_eq!(result, HitResult::Critical);
    // The spell-modifier half of the package is executing.
    assert_eq!(amount, 100.0 * (1.0 + f64::from(1.1_f32)));
}

/// MUT-A-007: a complete package on Player cannot mask an orphan member on the Enemy actor.
#[test]
fn holds_complete_actor_does_not_mask_orphan_actor() {
    let (fixture, program) = martial_program();
    let input = base_input()
        .with_passive_spell_modifier(PassiveSpellModifierBinding::new(ActorId::Player, bonus()))
        .with_critical_block_amount(CriticalBlockAmountBinding::new(ActorId::Player, block()))
        .with_critical_block_amount(CriticalBlockAmountBinding::new(enemy(), block()));
    let error = build(&fixture, &program, input).expect_err("orphan must reject");

    assert_eq!(
        state_code(&error),
        CombatStateErrorCode::IncompleteSelectedPassivePackageBinding
    );
}

/// MUT-A-008: binding the complete package on the Enemy actor too passes the package validator
/// (per-actor complete) but the ordinary spell-modifier authority is Player-only and rejects it.
#[test]
fn holds_complete_package_on_enemy_rejected_by_spell_modifier_authority() {
    let (fixture, program) = martial_program();
    let input = base_input()
        .with_passive_spell_modifier(PassiveSpellModifierBinding::new(ActorId::Player, bonus()))
        .with_critical_block_amount(CriticalBlockAmountBinding::new(ActorId::Player, block()))
        .with_passive_spell_modifier(PassiveSpellModifierBinding::new(enemy(), bonus()))
        .with_critical_block_amount(CriticalBlockAmountBinding::new(enemy(), block()));
    let error = build(&fixture, &program, input).expect_err("Player-only spell modifiers");
    let rendered = format!("{error:?}");

    assert!(rendered.contains("SpellModifier(UnsupportedActor"), "{rendered}");
}

/// MUT-A-006: the same wrong-family trick is caught when the target family validates definitions:
/// putting the spell-modifier member into the critical-block family is rejected by that authority.
#[test]
fn holds_spell_modifier_member_in_block_family_rejects() {
    let (fixture, program) = martial_program();
    let input = base_input()
        .with_critical_block_amount(CriticalBlockAmountBinding::new(ActorId::Player, bonus()))
        .with_critical_block_amount(CriticalBlockAmountBinding::new(ActorId::Player, block()));
    let error = build(&fixture, &program, input).expect_err("wrong family must reject");

    assert_eq!(
        state_code(&error),
        CombatStateErrorCode::MissingCriticalBlockAmountDefinition
    );
}
