//! Track B mutation probes over the selected SpellMod compiler (MUT-B-*).
//!
//! Baseline: a one-rank sole-effect selected raw-108 operation-0 +50% class-mask provider
//! (family 10, mask 0x800, Passive owner) and one SchoolDamage payload of the same family/mask.
//! Each test mutates exactly one source dimension and asserts the observed outcome.

use googletest::prelude::*;
use wowlab_combat::{CombatProgramBuildError, CombatProgramErrorCode, CombatStateErrorCode};
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



#[derive(Clone)]
struct Case {
    provider: EffectInput,
    provider_spell: SpellInput,
    payload_spell: SpellInput,
    payload_mask: [u32; 4],
    labels: Vec<wowlab_data::SpellLabelInput>,
    owner_mask: Option<[u32; 4]>,
}

fn baseline() -> Case {
    let mut provider = effect(PROVIDER, 1, 6, 108, 1, 50.0);
    provider.spell_class_mask = MASK;
    Case {
        provider,
        provider_spell: spell(PROVIDER, 1, words(&[6, 7, 268])),
        payload_spell: spell(DAMAGE, 1, [0; 17]),
        payload_mask: MASK,
        labels: Vec::new(),
        owner_mask: None,
    }
}

fn program(case: &Case) -> Result<(GameData, CombatProgram), CombatProgramBuildError> {
    let mut masks = vec![SpellClassMaskInput {
        spell_id: DAMAGE,
        spell_class_mask: case.payload_mask,
    }];
    if let Some(mask) = case.owner_mask {
        masks.push(SpellClassMaskInput {
            spell_id: PROVIDER,
            spell_class_mask: mask,
        });
    }
    masks.sort_by_key(|mask| mask.spell_id);
    let data = GameData::try_from_input(GameDataInput {
        identity: identity(),
        power_types: Vec::new(),
        spells: vec![case.provider_spell, case.payload_spell],
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        spell_class_masks: masks,
        spell_labels: case.labels.clone(),
        spell_missiles: Vec::new(),
        effects: vec![case.provider, effect(DAMAGE, 1, 2, 0, 6, 100.0)],
        effect_attributes: Vec::new(),
    })
    .expect("valid mutation fixture data");
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
    let (action, payload) = resolved_action_program(
        DAMAGE,
        ResolvedRootTargetInput::PrimaryTarget,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
    );
    actions.actions.push(action);
    actions.programs.push(payload);
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
    let external = absent_spell_external_policy(&data);
    let program = CombatProgram::builder(&data, &actions)
        .trait_source(&traits)
        .external_spell_policy(&external)
        .build()?;
    Ok((data, program))
}

fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn provider_effect() -> SpellEffectRef {
    SpellEffectRef::new(SpellId::new(PROVIDER).expect("id"), 1).expect("effect")
}

fn input(bindings: Vec<PassiveSpellModifierBinding>) -> CombatStateInput {
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
    CombatStateInput::new(
        vec![actor(ActorId::Player), actor(enemy())],
        RandomStreamIdentity::new(7, 7),
    )
    .with_hostile_target(ActorId::Player, enemy())
    .with_passive_spell_modifier_bindings(bindings)
}

fn damage_with(program: &CombatProgram, bindings: Vec<PassiveSpellModifierBinding>) -> Vec<f64> {
    let mut state: CombatState = program.try_state(input(bindings)).expect("state");
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

fn damage(case: &Case) -> Vec<f64> {
    let (_, program) = program(case).expect("mutation compiles");
    damage_with(&program, vec![PassiveSpellModifierBinding::new(ActorId::Player, provider_effect())])
}

fn code(case: &Case) -> CombatProgramErrorCode {
    program(case).err().expect("mutation is rejected").code()
}

/// MUT-B-001 baseline (holds): +50% operation 0 on a one-bit match gives 150.
#[gtest]
fn holds_mut_b_001_baseline_direct_class_mask_applies() -> Result<()> {
    verify_that!(damage(&baseline()), elements_are![near(150.0, 1e-9)])
}

/// MUT-B-002 (holds): two overlapping mask bits raise the factor per bit, 1.5^2 = 2.25.
#[gtest]
fn holds_mut_b_002_mask_bit_count_is_exponent() -> Result<()> {
    let mut case = baseline();
    case.provider.spell_class_mask = [2_048 | 4_096, 0, 0, 0];
    case.payload_mask = [2_048 | 4_096, 0, 0, 0];
    verify_that!(damage(&case), elements_are![near(225.0, 1e-9)])
}

/// MUT-B-003 (holds): overlap in word 3 alone is honoured (all four words participate).
#[gtest]
fn holds_mut_b_003_fourth_mask_word_participates() -> Result<()> {
    let mut case = baseline();
    case.provider.spell_class_mask = [0, 0, 0, 8];
    case.payload_mask = [0, 0, 0, 8];
    verify_that!(damage(&case), elements_are![near(150.0, 1e-9)])
}

/// MUT-B-004 (holds): a different payload family with overlapping bits is inert (100).
#[gtest]
fn holds_mut_b_004_family_mismatch_is_inert() -> Result<()> {
    let mut case = baseline();
    case.payload_spell.family_id = SpellFamilyId::from_raw(11);
    verify_eq!(damage(&case), vec![100.0])
}

/// MUT-B-005 (holds): provider family zero with a class-mask selector is rejected as an owner
/// fact (Trinity requires equal family names; Core refuses rather than treating 0 as universal).
#[gtest]
fn holds_mut_b_005_family_zero_class_mask_owner_rejected() -> Result<()> {
    let mut case = baseline();
    case.provider_spell.family_id = SpellFamilyId::NONE;
    case.payload_spell.family_id = SpellFamilyId::NONE;
    verify_eq!(code(&case), CombatProgramErrorCode::InvalidSpellModifierOwnerFact)
}

/// MUT-B-006 (holds): empty provider class mask on raw 108 is not classified (no universal
/// empty-mask selection for SpellMods).
#[gtest]
fn holds_mut_b_006_empty_class_mask_rejected() -> Result<()> {
    let mut case = baseline();
    case.provider.spell_class_mask = [0; 4];
    verify_eq!(code(&case), CombatProgramErrorCode::InvalidSpellModifierSourceFact)
}

/// MUT-B-007 (holds): nonzero secondary misc on class-mask raw 108 is rejected.
#[gtest]
fn holds_mut_b_007_class_mask_secondary_misc_rejected() -> Result<()> {
    let mut case = baseline();
    case.provider.misc_value_1 = 5;
    verify_eq!(code(&case), CombatProgramErrorCode::InvalidSpellModifierSourceFact)
}

/// MUT-B-008 (holds): unadmitted operation 1 (Duration) is rejected.
#[gtest]
fn holds_mut_b_008_unadmitted_operation_rejected() -> Result<()> {
    let mut case = baseline();
    case.provider.misc_value_0 = 1;
    verify_eq!(code(&case), CombatProgramErrorCode::InvalidSpellModifierSourceFact)
}

/// MUT-B-009 (holds): positive operation-11 cooldown percentage is rejected.
#[gtest]
fn holds_mut_b_009_positive_cooldown_percent_rejected() -> Result<()> {
    let mut case = baseline();
    case.provider.misc_value_0 = 11;
    verify_eq!(code(&case), CombatProgramErrorCode::InvalidSpellModifierSourceFact)
}

/// MUT-B-010 (holds): points below -100 are rejected.
#[gtest]
fn holds_mut_b_010_points_below_minus_100_rejected() -> Result<()> {
    let mut case = baseline();
    case.provider.base_points = -150.0;
    verify_eq!(code(&case), CombatProgramErrorCode::InvalidSpellModifierSourceFact)
}

/// MUT-B-011 (holds): non-self implicit target on the provider is rejected.
#[gtest]
fn holds_mut_b_011_non_self_provider_rejected() -> Result<()> {
    let mut case = baseline();
    case.provider.implicit_target_a = 6;
    verify_eq!(code(&case), CombatProgramErrorCode::InvalidSpellModifierSourceFact)
}

/// MUT-B-012 (holds): nonzero aura period on the provider is rejected.
#[gtest]
fn holds_mut_b_012_periodic_provider_rejected() -> Result<()> {
    let mut case = baseline();
    case.provider.aura_period_ms = 1_000;
    verify_eq!(code(&case), CombatProgramErrorCode::InvalidSpellModifierSourceFact)
}

/// MUT-B-013 (accepted_inert): a trigger spell on raw 108 is accepted as inert metadata.
#[gtest]
fn holds_mut_b_013_trigger_spell_on_raw108_is_inert() -> Result<()> {
    let mut case = baseline();
    case.provider.trigger_spell_id = Some(DAMAGE);
    verify_that!(damage(&case), elements_are![near(150.0, 1e-9)])
}

/// MUT-B-014 (holds): a nonempty owner (spell-level) class mask is rejected as an owner fact.
#[gtest]
fn holds_mut_b_014_owner_class_mask_rejected() -> Result<()> {
    let mut case = baseline();
    case.owner_mask = Some([1, 0, 0, 0]);
    verify_eq!(code(&case), CombatProgramErrorCode::InvalidSpellModifierOwnerFact)
}

/// MUT-B-015 (holds): raw-218 label selector applies by label membership, ignoring family/mask.
#[gtest]
fn holds_mut_b_015_label_selector_boolean_membership() -> Result<()> {
    let mut case = baseline();
    case.provider.aura_subtype = 218;
    case.provider.spell_class_mask = [0; 4];
    case.provider.misc_value_1 = 4_242;
    case.payload_spell.family_id = SpellFamilyId::from_raw(11);
    let without = damage(&case);
    case.labels = vec![wowlab_data::SpellLabelInput {
        spell_id: DAMAGE,
        label_id: 4_242,
    }];
    verify_eq!(without, vec![100.0])?;
    verify_that!(damage(&case), elements_are![near(150.0, 1e-9)])
}

/// MUT-B-016 (holds): an unbound compiled single-effect provider is inert; a non-Player binding
/// and a duplicate binding are rejected at state construction.
#[gtest]
fn holds_mut_b_016_binding_actor_and_multiplicity() -> Result<()> {
    let (_, program) = program(&baseline()).or_fail()?;
    verify_eq!(damage_with(&program, Vec::new()), vec![100.0])?;
    let enemy_binding = program
        .try_state(input(vec![PassiveSpellModifierBinding::new(enemy(), provider_effect())]))
        .err()
        .or_fail()?;
    verify_eq!(
        enemy_binding.code(),
        CombatStateErrorCode::UnsupportedPassiveSpellModifierActor
    )?;
    let duplicate = program
        .try_state(input(vec![
            PassiveSpellModifierBinding::new(ActorId::Player, provider_effect()),
            PassiveSpellModifierBinding::new(ActorId::Player, provider_effect()),
        ]))
        .err()
        .or_fail()?;
    verify_eq!(
        duplicate.code(),
        CombatStateErrorCode::DuplicatePassiveSpellModifierBinding
    )
}
