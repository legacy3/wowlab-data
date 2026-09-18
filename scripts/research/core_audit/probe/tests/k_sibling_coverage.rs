//! Track K probe: ordinary action programs silently ignore undeclared sibling source effects of
//! any catalog status (Unimplemented Dummy raw 3, Disabled raw 17, uncataloged raw 999, and an
//! ApplyAura carrying Disabled subtype raw 4). Records: CSA-K-04, MUT-K-013.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatStateInput, HasteMultipliers,
    HealthPool, OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, EffectAmountFactsInput, EffectChainFactsInput,
    EffectInput, GameData, GameDataIdentity, GameDataInput, ResolvedActionCatalog,
    ResolvedActionTimingInput, ResolvedProgramStepInput, ResolvedRootTargetInput, SpellInput,
};
use wowlab_dbc::{SpellEffectKind, SpellEffectSupport, spell_effect_requirement};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{empty_resolved_action_catalog_input, resolved_action_program};

const SPELL: u32 = 7_300_001;

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion { schema: CURRENT_SCHEMA_VERSION, game_build: 7_300 },
        difficulty: DifficultyId::BASE,
    }
}

fn effect(index: u8, kind: u32, aura: i32, points: f64, target: i32) -> EffectInput {
    EffectInput {
        spell_id: SPELL,
        index,
        kind,
        aura_subtype: aura,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: 0,
        misc_value_1: 0,
        radius_index_0: 0,
        radius_index_1: 0,
        implicit_target_a: target,
        implicit_target_b: 0,
        spell_class_mask: [0; 4],
        base_points: points,
        attack_power_coefficient: 0.0,
        spell_power_coefficient: 0.0,
        chain_facts: EffectChainFactsInput::NEUTRAL,
        amount_facts: EffectAmountFactsInput::NEUTRAL,
        trigger_spell_id: None,
    }
}

fn enemy() -> ActorId {
    ActorId::Enemy { index: EnemyIndex::new(0) }
}

/// MUT-K-013: a two-effect SchoolDamage spell whose second (undeclared) source effect is Dummy
/// (Unimplemented), WeaponDamageNoSchool (Disabled), uncataloged raw 999, or ApplyAura with the
/// Disabled raw-4 Dummy aura compiles; the cast deals only the declared damage. The source
/// completeness (`source_effect_count = 2`) is known to Core and not audited for ordinary actions.
#[gtest]
fn accepted_undeclared_sibling_effects_are_silently_ignored() -> Result<()> {
    verify_eq!(spell_effect_requirement(3).unwrap().support(), SpellEffectSupport::Unimplemented)?;
    verify_eq!(spell_effect_requirement(17).unwrap().support(), SpellEffectSupport::Disabled)?;
    verify_that!(spell_effect_requirement(999).is_err(), eq(true))?;
    for (kind, aura) in [(3_u32, 0_i32), (17, 0), (999, 0), (u32::from(SpellEffectKind::ApplyAura.raw()), 4)] {
        let data = GameData::try_from_input(GameDataInput {
            identity: identity(),
            power_types: Vec::new(),
            spells: vec![SpellInput {
                id: SPELL,
                family_id: wowlab_data::SpellFamilyId::NONE,
                dispel_type: wowlab_data::DispelTypeId::NONE,
                school_mask: 0x4,
                defense_type: 1,
                mechanic: 0,
                all_effect_mechanic_mask: [0; 2],
                source_effect_count: Some(2),
                attributes: [0; 17],
            }],
            spell_class_masks: Vec::new(),
            spell_labels: Vec::new(),
            spell_aura_options: Vec::new(),
            spell_aura_restrictions: Vec::new(),
            spell_duration_presence: Vec::new(),
            spell_shapeshifts: Vec::new(),
            spell_equipment_requirements: Vec::new(),
            spell_missiles: Vec::new(),
            effects: vec![
                effect(1, u32::from(SpellEffectKind::SchoolDamage.raw()), 0, 10.0, 6),
                effect(2, kind, aura, 5.0, 6),
            ],
            effect_attributes: Vec::new(),
        })?;
        let mut actions = empty_resolved_action_catalog_input(identity());
        let (action, program) = resolved_action_program(
            SPELL,
            ResolvedRootTargetInput::PrimaryTarget,
            Vec::new(),
            ResolvedActionTimingInput::default(),
            vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
        );
        actions.actions.push(action);
        actions.programs.push(program);
        let actions = ResolvedActionCatalog::try_from_input(actions)?;
        let result = CombatProgram::builder(&data, &actions).build();
        println!("sibling kind {kind} aura {aura}: {:?}", result.as_ref().err());
        verify_that!(result.is_ok(), eq(true))?;
        let program = result.unwrap();
        let actor = |id| {
            ActorState::try_new(id, Vec::new(), Some(HealthPool::full(100.0).unwrap()), OffensivePower::ZERO, HasteMultipliers::UNHASTED).unwrap()
        };
        let mut state = program.try_state(
            CombatStateInput::new(vec![actor(ActorId::Player), actor(enemy())], RandomStreamIdentity::new(3, 1))
                .with_hostile_target(ActorId::Player, enemy()),
        )?;
        let mut output = Vec::new();
        state.cast(CastRequest::new(ActorId::Player, enemy(), SpellId::new(SPELL).unwrap()), &mut output).expect("cast");
        let damage_events = output.iter().filter(|o| matches!(o, CombatObservation::DamageDealt { .. })).count();
        verify_that!(damage_events, le(1))?;
    }
    Ok(())
}
