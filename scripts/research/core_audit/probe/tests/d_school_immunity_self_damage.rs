//! Track D probes: raw-39 school immunity vs caster-recipient damage payloads.
//!
//! The raw-39 compiler audits only programs with a Target impact
//! (crates/combat/src/program/school_immunity.rs:236-266); runtime suppression is consulted only for
//! the exact applicands (crates/combat/src/school_immunity.rs:22-35). The raw-165 caster-delivered
//! payload is documented as assuming "absent immunity" (crates/dbc/src/spell_effect.rs:262), yet
//! both compile together, so the raw-39 holder takes covered-school self damage. Trinity
//! `Unit::IsImmunedToDamage` (school immunity mask covers the spell school) zeroes it.

use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, EffectAmountFactsInput, EffectChainFactsInput,
    EffectInput, GameData, GameDataIdentity, ResolvedActionCatalog, ResolvedActionRecipientInput,
    ResolvedActionTimingInput, ResolvedImpactInput, ResolvedProgramStepInput,
    ResolvedRootTargetInput, SpellInput,
};
use wowlab_dbc::{DefenseType, SpellEffectKind};
use wowlab_model::{ActorId, AuraKey, DifficultyId, EnemyIndex, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    SchoolImmunityFixture, resolved_action_program, school_immunity_action_input,
    school_immunity_game_data_input,
};

/// Shape of source row 1257873:0 (Effect 165, 10 %, ImplicitTarget (1,0), Shadow 32, Magic,
/// sole effect, no attributes); synthetic identity to keep the probe independent of catalogs.
const SELF_DAMAGE: u32 = 930_165;

const fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 930_165,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn program() -> (SchoolImmunityFixture, CombatProgram) {
    let mut data = school_immunity_game_data_input(identity());
    let mut actions = school_immunity_action_input(identity());

    data.spells.push(SpellInput {
        id: SELF_DAMAGE,
        family_id: wowlab_data::SpellFamilyId::NONE,
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: 32,
        defense_type: u8::from(DefenseType::Magic),
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: Some(1),
        attributes: [0; 17],
    });
    data.effects.push(EffectInput {
        spell_id: SELF_DAMAGE,
        index: 1,
        kind: u32::from(SpellEffectKind::DamageFromMaximumHealthPercent.raw()),
        aura_subtype: 0,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: 0,
        misc_value_1: 0,
        radius_index_0: 0,
        radius_index_1: 0,
        implicit_target_a: 1,
        implicit_target_b: 0,
        spell_class_mask: [0; 4],
        base_points: 10.0,
        attack_power_coefficient: 0.0,
        spell_power_coefficient: 0.0,
        chain_facts: EffectChainFactsInput::NEUTRAL,
        amount_facts: EffectAmountFactsInput::NEUTRAL,
        trigger_spell_id: None,
    });
    let (action, mut program) = resolved_action_program(
        SELF_DAMAGE,
        ResolvedRootTargetInput::Caster,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        Vec::new(),
    );
    program.impacts = vec![ResolvedImpactInput {
        recipient: ResolvedActionRecipientInput::Caster,
        steps: vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
    }];
    actions.actions.push(action);
    actions.programs.push(program);

    let data = GameData::try_from_input(data).expect("game data is valid");
    let actions = ResolvedActionCatalog::try_from_input(actions).expect("catalog is valid");
    let program = CombatProgram::builder(&data, &actions)
        .build()
        .expect("raw-39 school immunity and raw-165 self damage compile together");

    (SchoolImmunityFixture, program)
}

fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn actor(id: ActorId) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::try_new(1_000.0, 1_000.0).expect("valid pool")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

fn cast(state: &mut CombatState, actor: ActorId, spell: SpellId) -> Vec<CombatObservation> {
    let mut output = Vec::new();
    state
        .cast(CastRequest::new(actor, actor, spell), &mut output)
        .expect("cast succeeds");
    output
}

/// CSA-D-05 (LIVE): with the raw-39 (mask 126) provider active on its holder, a covered Shadow
/// raw-165 self-damage payload still removes 10 % of maximum health.
#[test]
fn defect_school_immune_holder_takes_covered_caster_delivered_damage() {
    let (fixture, program) = program();
    let mut state = program
        .try_state(
            CombatStateInput::new(vec![actor(ActorId::Player), actor(enemy())], RandomStreamIdentity::new(39, 165))
                .with_hostile_target(ActorId::Player, enemy()),
        )
        .expect("valid state");

    cast(&mut state, enemy(), fixture.provider_spell());
    assert!(state.active_aura(AuraKey::new(fixture.provider_aura(), enemy(), enemy())).is_some());
    let output = cast(&mut state, enemy(), SpellId::new(SELF_DAMAGE).expect("nonzero"));

    assert!(output.iter().any(|observation| matches!(observation, CombatObservation::DamageDealt { .. })));
    assert_eq!(
        state.actor(enemy()).and_then(ActorState::health).map(|pool| pool.current()),
        Some(900.0),
        "raw-39 active, yet 100 covered Shadow damage landed"
    );
    assert!(state.active_aura(AuraKey::new(fixture.provider_aura(), enemy(), enemy())).is_some());
}
