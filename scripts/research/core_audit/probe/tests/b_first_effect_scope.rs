//! Track B probes: operation-3 (PointsIndex0) SpellMod target scope.
//!
//! Proves CSA-B-04: a selected sole-effect raw-107 operation-3 class-mask provider compiles and
//! binds, but it is projected only onto declared raw-87 damage-taken or raw-20 percent-heal first
//! effects (`passive_spell_modifier.rs::compile_flat_first_effect_projections`). Any other
//! matching payload first effect (here an ordinary SchoolDamage effect 1) is silently left at its
//! authored value, although the raw-107 catalog row states that "unrelated first-effect lowerers
//! ... remain fail-closed", and Trinity `WorldObject::ApplyEffectModifiers` (Object.cpp:1678-1690)
//! applies PointsIndex0 to every effect-0 value of an affected spell.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    HasteMultipliers, HealthPool, OffensivePower,
    PassiveSpellModifierBinding,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, CURRENT_TRAIT_SOURCE_SCHEMA_VERSION, DataVersion,
    EffectAmountFactsInput, EffectChainFactsInput, EffectInput, GameData, GameDataIdentity,
    GameDataInput, ResolvedActionCatalog, ResolvedActionTimingInput,
    ResolvedProgramStepInput, ResolvedRootTargetInput, SpellClassMaskInput,
    SpellFamilyId, SpellInput, SpellModifierActivationInput, SpellModifierInput,
    TraitDefinitionInput, TraitNodeEntryInput, TraitSourceCatalog, TraitSourceCatalogInput,
};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, SpellEffectRef, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    absent_spell_external_policy, empty_resolved_action_catalog_input, resolved_action_program,
};

const ENTRY: u32 = 902_101;
const DEFINITION: u32 = 902_102;
const PROVIDER: u32 = 902_103;
const DAMAGE: u32 = 902_105;
const FAMILY: u8 = 10;
const MASK: [u32; 4] = [2_048, 0, 0, 0];

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn words(raw: &[usize]) -> [i32; 17] {
    let mut words = [0_i32; 17];
    for &bit in raw {
        words[bit / 32] |= i32::from_ne_bytes((1_u32 << (bit % 32)).to_ne_bytes());
    }
    words
}

fn spell(id: u32, effects: u8, attributes: [i32; 17]) -> SpellInput {
    SpellInput {
        id,
        family_id: SpellFamilyId::from_raw(FAMILY),
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: 2,
        defense_type: 0,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: Some(effects),
        attributes,
    }
}

fn effect(spell_id: u32, index: u8, kind: u32, aura: i32, target: i32, points: f64) -> EffectInput {
    EffectInput {
        spell_id,
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


fn build(provider_points: f64, operation: i32, aura: i32) -> (GameData, TraitSourceCatalog, ResolvedActionCatalog) {
    let mut provider = effect(PROVIDER, 1, 6, aura, 1, provider_points);
    provider.misc_value_0 = operation;
    provider.spell_class_mask = MASK;

    let data = GameData::try_from_input(GameDataInput {
        identity: identity(),
        power_types: Vec::new(),
        spells: vec![spell(PROVIDER, 1, words(&[6, 7, 268])), spell(DAMAGE, 1, [0; 17])],
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        spell_class_masks: vec![SpellClassMaskInput {
            spell_id: DAMAGE,
            spell_class_mask: MASK,
        }],
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects: vec![provider, effect(DAMAGE, 1, 2, 0, 6, 100.0)],
        effect_attributes: Vec::new(),
    })
    .expect("valid op-3 fixture data");
    let traits = TraitSourceCatalog::try_from_input(
        TraitSourceCatalogInput {
            schema: CURRENT_TRAIT_SOURCE_SCHEMA_VERSION,
            identity: identity(),
            entries: vec![TraitNodeEntryInput {
                id: ENTRY,
                definition_id: DEFINITION,
                max_ranks: 1,
                node_entry_type: 2,
                trait_subtree_id: 0,
            }],
            definitions: vec![TraitDefinitionInput {
                id: DEFINITION,
                spell_id: PROVIDER,
                overrides_spell_id: 0,
                visible_spell_id: 0,
                effect_point_row_count: 0,
            }],
            effect_points: Vec::new(),
            curves: Vec::new(),
            curve_points: Vec::new(),
        },
        &data,
    )
    .expect("valid trait source");
    let mut actions = empty_resolved_action_catalog_input(identity());
    let (action, program) = resolved_action_program(
        DAMAGE,
        ResolvedRootTargetInput::PrimaryTarget,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
    );

    actions.actions.push(action);
    actions.programs.push(program);
    actions
        .spell_modifier_family
        .modifiers
        .push(SpellModifierInput {
            spell_id: PROVIDER,
            effect_index: 1,
            activation: SpellModifierActivationInput::SelectedTrait {
                entry_id: ENTRY,
                effective_rank: 1,
            },
        });
    let actions = ResolvedActionCatalog::try_from_input(actions).expect("valid actions");

    (data, traits, actions)
}

fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn damage(provider_points: f64, operation: i32, aura: i32) -> Vec<f64> {
    let (data, traits, actions) = build(provider_points, operation, aura);
    let external = absent_spell_external_policy(&data);
    let program = CombatProgram::builder(&data, &actions)
        .trait_source(&traits)
        .external_spell_policy(&external)
        .build()
        .expect("sole-effect selected operation-3 provider compiles");
    let actor = |id| {
        ActorState::try_new(
            id,
            Vec::new(),
            Some(HealthPool::full(1_000_000.0).expect("health")),
            OffensivePower::ZERO,
            HasteMultipliers::UNHASTED,
        )
        .expect("actor")
    };
    let input = CombatStateInput::new(
        vec![actor(ActorId::Player), actor(enemy())],
        RandomStreamIdentity::new(3, 3),
    )
    .with_hostile_target(ActorId::Player, enemy())
    .with_passive_spell_modifier_binding(PassiveSpellModifierBinding::new(
        ActorId::Player,
        SpellEffectRef::new(SpellId::new(PROVIDER).expect("id"), 1).expect("effect"),
    ));
    let mut state: CombatState = program.try_state(input).expect("bound state constructs");
    let mut observations = Vec::new();

    state
        .cast(
            CastRequest::new(ActorId::Player, enemy(), SpellId::new(DAMAGE).expect("id")),
            &mut observations,
        )
        .expect("cast");

    observations
        .into_iter()
        .filter_map(|observation| match observation {
            CombatObservation::DamageDealt { damage, .. } => Some(damage.requested()),
            _ => None,
        })
        .collect()
}

/// CSA-B-04 (defect): +10 flat PointsIndex0 on a matching SchoolDamage effect 1 is accepted,
/// bound, and silently inert (100, not 110).
#[gtest]
fn defect_flat_first_effect_provider_is_silently_inert_on_ordinary_damage_payload() -> Result<()> {
    verify_eq!(damage(10.0, 3, 107), vec![100.0])
}

/// CSA-B-04 (defect): the same for a +50% raw-108 operation-3 provider (100, not 150).
#[gtest]
fn defect_percent_first_effect_provider_is_silently_inert_on_ordinary_damage_payload() -> Result<()> {
    verify_eq!(damage(50.0, 3, 108), vec![100.0])
}

/// CSA-B-04 control (holds): operation 0 on the identical provider/payload applies (150).
#[gtest]
fn holds_operation0_on_same_payload_applies() -> Result<()> {
    verify_that!(damage(50.0, 0, 108), elements_are![near(150.0, 1e-9)])
}
