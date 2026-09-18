//! Track B probes: operation-15 (CritDamageAndHealing) SpellMod payload coverage.
//!
//! Proves CSA-B-03: a selected sole-effect raw-108 operation-15 class-mask provider (the source
//! shape of live one-rank talents such as Awestruck 417855, Intensity 1264649 and Profound
//! Rebuttal 392910, whose masks overlap Paladin/Druid/Monk heals) is admitted, but its
//! projection is compiled only for payload spells owning a crit-eligible *damage* impact
//! (`payload_spells.rs::critical_damage_spell_modifier_payload_spells`). A pure healing payload
//! with the same family/mask therefore crits without the bonus, although the runtime consumer
//! (`execution/planning/critical.rs::prepare_impact_critical`) applies the projection to healing
//! impacts whenever one exists, and Trinity `Unit::SpellCriticalHealingBonus` (Unit.cpp:7309-7317)
//! applies `SpellModOp::CritDamageAndHealing` to heals.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    CriticalStrikeBaseline, HasteMultipliers, HealthPool, OffensivePower,
    PassiveSpellModifierBinding,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, CURRENT_TRAIT_SOURCE_SCHEMA_VERSION, DataVersion,
    EffectAmountFactsInput, EffectChainFactsInput, EffectInput, GameData, GameDataIdentity,
    GameDataInput, ResolvedActionCatalog, ResolvedActionRecipientInput, ResolvedActionTimingInput,
    ResolvedImpactInput, ResolvedProgramStepInput, ResolvedRootTargetInput, SpellClassMaskInput,
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
const HEAL: u32 = 902_104;
const DAMAGE: u32 = 902_105;
const MIXED: u32 = 902_106;
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

struct Fixture {
    data: GameData,
    traits: TraitSourceCatalog,
    actions: ResolvedActionCatalog,
}

#[derive(Clone, Copy)]
struct Shape {
    provider_attributes: &'static [usize],
    provider_mask: [u32; 4],
    payload_mask: [u32; 4],
    operation: i32,
    heal_kind: u32,
    heal_points: f64,
}

const SYNTHETIC: Shape = Shape {
    provider_attributes: &[6, 7, 268],
    provider_mask: MASK,
    payload_mask: MASK,
    operation: 15,
    heal_kind: 10,
    heal_points: 100.0,
};

/// Awestruck 417855:1 source shape (Passive 6 + Ignored 143, family 10, raw-108 op 15, +20,
/// mask [0x4000_0000, 0x1_0000, 0x400, 0]) against Flash of Light 19750's class mask
/// [0x4000_0000, 0x4000, 0, 0] (one overlapping bit).
const AWESTRUCK: Shape = Shape {
    provider_attributes: &[6, 143],
    provider_mask: [1_073_741_824, 65_536, 1_024, 0],
    payload_mask: [1_073_741_824, 16_384, 0, 0],
    operation: 15,
    heal_kind: 10,
    heal_points: 100.0,
};

fn fixture_with(shape: Shape) -> Fixture {
    let mut provider = effect(PROVIDER, 1, 6, 108, 1, 20.0);
    provider.misc_value_0 = shape.operation;
    provider.spell_class_mask = shape.provider_mask;

    let data = GameData::try_from_input(GameDataInput {
        identity: identity(),
        power_types: Vec::new(),
        spells: vec![
            // Passive (6) plus catalog-Ignored 7 and 268, as the Core fixture provider.
            spell(PROVIDER, 1, words(shape.provider_attributes)),
            spell(HEAL, 1, [0; 17]),
            spell(DAMAGE, 1, [0; 17]),
            spell(MIXED, 2, [0; 17]),
        ],
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        spell_class_masks: [HEAL, DAMAGE, MIXED]
            .into_iter()
            .map(|spell_id| SpellClassMaskInput {
                spell_id,
                spell_class_mask: shape.payload_mask,
            })
            .collect(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects: vec![
            provider,
            // SpellEffectKind::Heal (10) on the caster.
            effect(HEAL, 1, shape.heal_kind, 0, 1, shape.heal_points),
            // SchoolDamage (2) on the enemy.
            effect(DAMAGE, 1, 2, 0, 6, 100.0),
            // One spell with both a damage impact and a caster heal impact.
            effect(MIXED, 1, 2, 0, 6, 100.0),
            effect(MIXED, 2, 10, 0, 1, 100.0),
        ],
        effect_attributes: Vec::new(),
    })
    .expect("valid op-15 fixture data");
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
    let (heal_action, mut heal_program) = resolved_action_program(
        HEAL,
        ResolvedRootTargetInput::Caster,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        vec![ResolvedProgramStepInput::DirectHeal { effect_index: 1 }],
    );
    let (damage_action, damage_program) = resolved_action_program(
        DAMAGE,
        ResolvedRootTargetInput::PrimaryTarget,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
    );
    let (mixed_action, mut mixed_program) = resolved_action_program(
        MIXED,
        ResolvedRootTargetInput::PrimaryTarget,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
    );
    for impact in &mut heal_program.impacts {
        impact.recipient = ResolvedActionRecipientInput::Caster;
    }
    mixed_program.impacts.push(ResolvedImpactInput {
        recipient: ResolvedActionRecipientInput::Caster,
        steps: vec![ResolvedProgramStepInput::DirectHeal { effect_index: 2 }],
    });

    actions.actions.extend([heal_action, damage_action, mixed_action]);
    actions.programs.extend([heal_program, damage_program, mixed_program]);
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
    actions.actions.sort_by_key(|action| action.spell_id);
    actions.programs.sort_by_key(|program| program.node_id);
    let actions = ResolvedActionCatalog::try_from_input(actions).expect("valid actions");

    Fixture {
        data,
        traits,
        actions,
    }
}

fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn state(program: &CombatProgram, bound: bool) -> CombatState {
    let actor = |id| {
        ActorState::try_new(
            id,
            Vec::new(),
            Some(HealthPool::try_new(1.0, 1_000_000.0).expect("health")),
            OffensivePower::ZERO,
            HasteMultipliers::UNHASTED,
        )
        .expect("actor")
    };
    let mut input = CombatStateInput::new(
        vec![actor(ActorId::Player), actor(enemy())],
        RandomStreamIdentity::new(15, 15),
    )
    .with_hostile_target(ActorId::Player, enemy())
    .with_critical_strike(
        CriticalStrikeBaseline::try_uniform(ActorId::Player, 1.0, 2.0).expect("crit"),
    );

    if bound {
        input = input.with_passive_spell_modifier_binding(PassiveSpellModifierBinding::new(
            ActorId::Player,
            SpellEffectRef::new(SpellId::new(PROVIDER).expect("id"), 1).expect("effect"),
        ));
    }

    program.try_state(input).expect("state")
}

/// Returns (damage, healing) requested amounts for one cast.
fn cast(bound: bool, spell: u32) -> (Vec<f64>, Vec<f64>) {
    cast_with(SYNTHETIC, bound, spell)
}

fn cast_with(shape: Shape, bound: bool, spell: u32) -> (Vec<f64>, Vec<f64>) {
    let fixture = fixture_with(shape);
    let external = absent_spell_external_policy(&fixture.data);
    let program = CombatProgram::builder(&fixture.data, &fixture.actions)
        .trait_source(&fixture.traits)
        .external_spell_policy(&external)
        .build()
        .expect("sole-effect raw-108 operation-15 selected provider compiles");
    let mut state = state(&program, bound);
    let mut observations = Vec::new();
    let target = if spell == HEAL { ActorId::Player } else { enemy() };

    state
        .cast(
            CastRequest::new(ActorId::Player, target, SpellId::new(spell).expect("id")),
            &mut observations,
        )
        .expect("cast");

    let mut damage = Vec::new();
    let mut healing = Vec::new();
    for observation in observations {
        match observation {
            CombatObservation::DamageDealt { damage: amount, .. } => {
                damage.push(amount.requested());
            }
            CombatObservation::HealingDone { healing: amount, .. } => {
                healing.push(amount.requested());
            }
            _ => {}
        }
    }
    (damage, healing)
}

/// CSA-B-03 control (holds): the same provider scales the critical excess of a matching damage
/// payload: 100 * (1 + (2 - 1) * 1.2) = 220.
#[gtest]
fn holds_operation15_scales_matching_damage_critical_excess() -> Result<()> {
    verify_eq!(cast(false, DAMAGE).0, vec![200.0])?;
    verify_that!(cast(true, DAMAGE).0, elements_are![near(220.0, 1e-4)])
}

/// CSA-B-03 (defect): a pure-healing payload with the identical family/mask crits for 200 with
/// or without the bound provider. Trinity `SpellCriticalHealingBonus` gives 220.
#[gtest]
fn defect_operation15_skips_matching_pure_healing_payload() -> Result<()> {
    verify_eq!(cast(false, HEAL).1, vec![200.0])?;
    verify_eq!(cast(true, HEAL).1, vec![200.0])
}

/// CSA-B-03 (defect, inconsistency): the heal impact of a spell that also owns a damage impact
/// *does* receive the operation-15 bonus (220), so heal-crit behaviour depends on an unrelated
/// sibling impact of the payload program rather than on the SpellMod applicability facts.
#[gtest]
fn defect_operation15_heal_bonus_depends_on_sibling_damage_impact() -> Result<()> {
    let (damage, healing) = cast(true, MIXED);

    verify_that!(damage, elements_are![near(220.0, 1e-4)])?;
    verify_that!(healing, elements_are![near(220.0, 1e-4)])
}

/// CSA-B-03 (defect) with the exact Awestruck 417855 provider shape and Flash of Light 19750
/// payload mask: the heal crit stays 200 while the matching damage payload receives 220.
#[gtest]
fn defect_awestruck_shaped_operation15_skips_flash_of_light_shaped_heal() -> Result<()> {
    verify_eq!(cast_with(AWESTRUCK, true, HEAL).1, vec![200.0])?;
    verify_that!(cast_with(AWESTRUCK, true, DAMAGE).0, elements_are![near(220.0, 1e-4)])
}

/// CSA-B-05 (holds executable, catalog inconsistent): a matching raw-108 operation-0 provider does
/// not scale a HealMissingOrMaximumHealth (raw effect 67) payload (caster-maximum basis 1e6, crit x2
/// = 2e6 with or without the provider), matching Trinity EffectHealMaxHealth (no
/// SpellHealingBonusDone), although the raw-108 catalog row lists "direct missing-or-maximum
/// healing" among the operation-0 targets and payload_spells.rs compiles a projection for it.
#[gtest]
fn holds_operation0_does_not_scale_missing_or_maximum_heal() -> Result<()> {
    let shape = Shape {
        operation: 0,
        heal_kind: 67,
        heal_points: 0.0,
        ..SYNTHETIC
    };
    let fixed = Shape {
        operation: 0,
        ..SYNTHETIC
    };
    verify_eq!(cast_with(shape, false, HEAL).1, vec![2_000_000.0])?;
    verify_eq!(cast_with(shape, true, HEAL).1, vec![2_000_000.0])?;
    verify_that!(cast_with(fixed, true, HEAL).1, elements_are![near(240.0, 1e-4)])
}
