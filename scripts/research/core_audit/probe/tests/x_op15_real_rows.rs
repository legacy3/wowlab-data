//! Track X probes: real-row reachability of the operation-15 heal gap (CSA-B-03 vs CSA-C-02).
//!
//! XC-X-001 / reconciliation of CSA-C-02 into CSA-B-03:
//! - the production Engine candidate (`PreparedSpellModifierCandidate`) publishes the exact
//!   Awestruck 417855 source rows (entry 102544, definition 107549, owner attributes 6 + 143,
//!   family 10, raw-108 op 15, +20, mask [0x4000_0000, 0x1_0000, 0x400, 0]) as a selected
//!   SpellModifier through the generic arity-1 composition; CSA-C-02's claimed "named-branch
//!   op-15 admission" gate does not exist at the pin;
//! - the real matched heal payload Flash of Light 19750 (exact SpellMisc attribute words, exact
//!   SpellEffect row with variance 0.05 and SP coefficient 3.156) is refused by the program
//!   builder, so the pinned 12.1.0 rows never reach the heal-crit consumer. The executable gap is
//!   reached only with a payload whose owner attributes and amount facts pass the gates
//!   (the attribute-stripped, neutral twin below crits for 200 instead of 220).

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatProgramErrorCode,
    CombatStateInput, CriticalStrikeBaseline, HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, CURRENT_TRAIT_SOURCE_SCHEMA_VERSION, DataVersion,
    EffectAmountFactsInput, EffectChainFactsInput, EffectInput, GameData, GameDataIdentity,
    GameDataInput, ResolvedActionCatalog, ResolvedActionRecipientInput, ResolvedActionTimingInput,
    ResolvedProgramStepInput, ResolvedRootTargetInput, SpellClassMaskInput, SpellFamilyId,
    SpellInput, TraitDefinitionInput, TraitNodeEntryInput, TraitSourceCatalog,
    TraitSourceCatalogInput,
};
use wowlab_engine::{EngineInput, PreparedSpellModifierCandidate, SelectedTraitEntry};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    absent_spell_external_policy, empty_resolved_action_catalog_input, resolved_action_program,
};

const AWESTRUCK: u32 = 417_855;
const AWESTRUCK_ENTRY: u32 = 102_544;
const AWESTRUCK_DEFINITION: u32 = 107_549;
const FLASH_OF_LIGHT: u32 = 19_750;
const FAMILY: u8 = 10;

/// SpellMisc 417855: Attributes_0 = 64 (raw 6 Passive), Attributes_4 = 32768 (raw 143).
const AWESTRUCK_ATTRIBUTES: [u32; 17] = [64, 0, 0, 0, 32_768, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
/// SpellMisc 19750: Attributes_0 = 65536 (raw 16), Attributes_6 = 0x0200_0000 (raw 217),
/// Attributes_8 = 0x0100_0000 (raw 280), Attributes_13 = 1 (raw 416).
const FLASH_ATTRIBUTES: [u32; 17] = [
    65_536,
    0,
    0,
    0,
    0,
    0,
    33_554_432,
    0,
    16_777_216,
    0,
    0,
    0,
    0,
    1,
    0,
    0,
    0,
];

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn words(raw: [u32; 17]) -> [i32; 17] {
    raw.map(|word| i32::from_ne_bytes(word.to_ne_bytes()))
}

fn spell(id: u32, attributes: [u32; 17]) -> SpellInput {
    SpellInput {
        id,
        family_id: SpellFamilyId::from_raw(FAMILY),
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: 2,
        defense_type: 0,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: Some(1),
        attributes: words(attributes),
    }
}

fn neutral_effect(spell_id: u32, kind: u32, aura: i32, target: i32, points: f64) -> EffectInput {
    EffectInput {
        spell_id,
        index: 1,
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

#[derive(Clone, Copy)]
enum Payload {
    /// Exact SpellMisc attributes and exact SpellEffect 10981 row (variance 0.05, SP 3.156, (21,0)).
    RealFlashOfLight,
    /// Same id/family/mask, attribute words cleared, neutral amount facts, caster-targeted, 100 points.
    GateCleanTwin,
}

fn catalogs(payload: Payload) -> (GameData, TraitSourceCatalog, ResolvedActionCatalog) {
    let mut provider = neutral_effect(AWESTRUCK, 6, 108, 1, 20.0);
    provider.misc_value_0 = 15;
    provider.spell_class_mask = [1_073_741_824, 65_536, 1_024, 0];

    let (heal_spell, heal_effect, root, recipient) = match payload {
        Payload::RealFlashOfLight => {
            let mut heal = neutral_effect(FLASH_OF_LIGHT, 10, 0, 21, 0.0);
            heal.spell_power_coefficient = 3.155_999_898_91;
            heal.amount_facts = EffectAmountFactsInput {
                variance: 0.050_000_000_75,
                ..EffectAmountFactsInput::NEUTRAL
            };
            (
                spell(FLASH_OF_LIGHT, FLASH_ATTRIBUTES),
                heal,
                ResolvedRootTargetInput::PrimaryTarget,
                ResolvedActionRecipientInput::Target,
            )
        }
        Payload::GateCleanTwin => (
            spell(FLASH_OF_LIGHT, [0; 17]),
            neutral_effect(FLASH_OF_LIGHT, 10, 0, 1, 100.0),
            ResolvedRootTargetInput::Caster,
            ResolvedActionRecipientInput::Caster,
        ),
    };

    let data = GameData::try_from_input(GameDataInput {
        identity: identity(),
        power_types: Vec::new(),
        spells: vec![spell(AWESTRUCK, AWESTRUCK_ATTRIBUTES), heal_spell],
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        spell_class_masks: vec![SpellClassMaskInput {
            // SpellClassOptions row 1527 (Flash of Light): [0x4000_0000, 0x4000, 0, 0].
            spell_id: FLASH_OF_LIGHT,
            spell_class_mask: [1_073_741_824, 16_384, 0, 0],
        }],
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects: vec![provider, heal_effect],
        effect_attributes: Vec::new(),
    })
    .expect("valid real-row game data");
    let traits = TraitSourceCatalog::try_from_input(
        TraitSourceCatalogInput {
            schema: CURRENT_TRAIT_SOURCE_SCHEMA_VERSION,
            identity: identity(),
            entries: vec![TraitNodeEntryInput {
                id: AWESTRUCK_ENTRY,
                definition_id: AWESTRUCK_DEFINITION,
                max_ranks: 1,
                node_entry_type: 2,
                trait_subtree_id: 0,
            }],
            definitions: vec![TraitDefinitionInput {
                id: AWESTRUCK_DEFINITION,
                spell_id: AWESTRUCK,
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
    let (action, mut program) = resolved_action_program(
        FLASH_OF_LIGHT,
        root,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        vec![ResolvedProgramStepInput::DirectHeal { effect_index: 1 }],
    );
    for impact in &mut program.impacts {
        impact.recipient = recipient;
    }
    actions.actions.push(action);
    actions.programs.push(program);

    // The production candidate publishes the selected declaration and the Player binding.
    let candidate = PreparedSpellModifierCandidate::try_prepare(
        &data,
        &traits,
        SelectedTraitEntry {
            entry_id: AWESTRUCK_ENTRY,
            effective_rank: 1,
        },
    )
    .expect("the production candidate admits exact Awestruck rows");
    let (actions, engine) = candidate
        .try_into_inputs(actions, EngineInput::new(Vec::new()))
        .expect("candidate publication");
    assert_eq!(engine.passive_spell_modifier_bindings().len(), 1);
    let actions = ResolvedActionCatalog::try_from_input(actions).expect("valid actions");

    (data, traits, actions)
}

fn build(payload: Payload) -> std::result::Result<CombatProgram, CombatProgramErrorCode> {
    let (data, traits, actions) = catalogs(payload);
    let external = absent_spell_external_policy(&data);

    CombatProgram::builder(&data, &actions)
        .trait_source(&traits)
        .external_spell_policy(&external)
        .build()
        .map_err(|error| error.code())
}

/// XC-X-001 (holds): the exact real Flash of Light payload never compiles next to Awestruck; the
/// global selected-owner attribute audit refuses its owner (raw 16 NotShapeshifted is Disabled).
#[gtest]
fn holds_real_flash_of_light_payload_is_refused_by_owner_attribute_gate() -> Result<()> {
    let error = build(Payload::RealFlashOfLight).err();

    verify_eq!(
        error,
        Some(CombatProgramErrorCode::UnsupportedSelectedSpellAttribute)
    )
}

/// XC-X-001 / CSA-B-03 (defect, reached only through a gate-clean payload): the production
/// candidate's Awestruck declaration compiles next to a heal-only payload with Flash of Light's
/// class mask; the heal crit stays 100 * 2 = 200 although Trinity SpellCriticalHealingBonus
/// applies CritDamageAndHealing (+20%) -> 220.
#[gtest]
fn defect_candidate_awestruck_skips_gate_clean_flash_of_light_twin() -> Result<()> {
    let program = build(Payload::GateCleanTwin).expect("gate-clean twin compiles");
    let enemy = ActorId::Enemy {
        index: EnemyIndex::new(0),
    };
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
    let binding = wowlab_combat::PassiveSpellModifierBinding::new(
        ActorId::Player,
        wowlab_model::SpellEffectRef::new(SpellId::new(AWESTRUCK).expect("id"), 1).expect("ref"),
    );
    let mut state = program
        .try_state(
            CombatStateInput::new(
                vec![actor(ActorId::Player), actor(enemy)],
                RandomStreamIdentity::new(15, 15),
            )
            .with_hostile_target(ActorId::Player, enemy)
            .with_critical_strike(
                CriticalStrikeBaseline::try_uniform(ActorId::Player, 1.0, 2.0).expect("crit"),
            )
            .with_passive_spell_modifier_binding(binding),
        )
        .expect("state");
    let mut observations = Vec::new();

    state
        .cast(
            CastRequest::new(
                ActorId::Player,
                ActorId::Player,
                SpellId::new(FLASH_OF_LIGHT).expect("id"),
            ),
            &mut observations,
        )
        .expect("cast");

    let healing: Vec<f64> = observations
        .iter()
        .filter_map(|observation| match observation {
            CombatObservation::HealingDone { healing, .. } => Some(healing.requested()),
            _ => None,
        })
        .collect();

    verify_eq!(healing, vec![200.0])
}
