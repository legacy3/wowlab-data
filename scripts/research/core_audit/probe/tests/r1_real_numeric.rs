//! Reviewer R1 (false positives): real 12.1.0.69497 source-row witnesses for CSA-J-03 and
//! CSA-J-06 (the track probes use synthetic 92_7xx / 100 / 200 ids).
//!
//! - J-03: J cites Initiation 193878, but that row carries Disabled owner attributes 4, 122, 254,
//!   385 and is refused by the owner-attribute audit. The same-shaped Initiation 213539 is clean:
//!   SpellMisc attributes 6 (Passive), 143, 355 (Ignored); effect 0 raw-6/raw-183 +30, misc
//!   (0, 80), (1,0); effect 1 raw 0 None (Ignored) bp 80 — a raw-0 row is not a GameData effect
//!   (InvalidEffectKind), so it is omitted while SpellInput.source_effect_count stays the source
//!   row count 2. Payload: Wrath of the Titans 228917
//!   (raw-2 Nature 83.963, (6,0), DefenseType 1).
//! - J-06: Mana Confluence 1270845 (Passive + attribute 7, family 3, raw-6/raw-423 -5, misc 0,
//!   (1,0)); the spend is SpellPower row 23 of Time Step 111 (ManaCost 5). The spend is a host
//!   resolution of that row; 111's own raw-87 effect is not declared because the defect is in the
//!   cost path only.

use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatStateInput,
    CriticalStrikeBaseline, CriticalStrikeChances, HasteMultipliers, HealthPool, OffensivePower,
    PassiveCriticalModifierBinding, PassivePowerCostBinding, ResourcePool,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, CriticalChanceModifierActivationInput,
    CriticalChanceModifierCompositionInput, CriticalHealthConditionInput, DataVersion,
    EffectAmountFactsInput, EffectChainFactsInput, EffectInput, GameData, GameDataIdentity,
    GameDataInput, PowerCostActivationInput, PowerCostModifierInput, PowerTypeInput,
    ResolvedActionCatalog, ResolvedActionInput, ResolvedActionTimingInput,
    ResolvedCriticalChanceModifierInput, ResolvedProgramStepInput, ResolvedResourceSpendInput,
    ResolvedRootTargetInput, ResolvedSpellProgramInput, SpellFamilyId, SpellInput,
    TargetHealthComparisonInput,
};
use wowlab_model::{
    ActorId, DifficultyId, EnemyIndex, HitResult, ResourceType, SpellEffectRef, SpellId,
};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{empty_resolved_action_catalog_input, resolved_action_program};

const INITIATION: u32 = 213_539;
const WRATH: u32 = 228_917;
const MANA_CONFLUENCE: u32 = 1_270_845;
const TIME_STEP: u32 = 111;

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn words(bits: &[u32]) -> [i32; 17] {
    let mut words = [0_u32; 17];
    for bit in bits {
        words[(bit / 32) as usize] |= 1 << (bit % 32);
    }
    words.map(|word| i32::from_ne_bytes(word.to_ne_bytes()))
}

#[allow(clippy::too_many_arguments)]
fn spell(id: u32, family: u8, school: u32, defense: u8, effects: u8, attributes: [i32; 17]) -> SpellInput {
    SpellInput {
        id,
        family_id: SpellFamilyId::from_raw(family),
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: school,
        defense_type: defense,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: Some(effects),
        attributes,
    }
}

#[allow(clippy::too_many_arguments)]
fn effect(spell_id: u32, index: u8, kind: u32, aura: i32, misc: (i32, i32), points: f64, target: i32) -> EffectInput {
    EffectInput {
        spell_id,
        index,
        kind,
        aura_subtype: aura,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: misc.0,
        misc_value_1: misc.1,
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

fn initiation_program(
    initiation: u32,
    attributes: [i32; 17],
) -> Result<CombatProgram, wowlab_combat::CombatProgramBuildError> {
    let data = GameData::try_from_input(GameDataInput {
        identity: identity(),
        power_types: Vec::new(),
        spells: vec![
            spell(initiation, 6, 1, 0, 2, attributes),
            spell(WRATH, 0, 8, 1, 1, [0; 17]),
        ],
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects: vec![
            effect(initiation, 1, 6, 183, (0, 80), 30.0, 1),
            effect(WRATH, 1, 2, 0, (0, 0), 83.963_386_535_64, 6),
        ],
        effect_attributes: Vec::new(),
    })
    .expect("real rows are valid game data");
    let mut input = empty_resolved_action_catalog_input(identity());
    input.critical_chance_modifiers.push(ResolvedCriticalChanceModifierInput {
        spell_id: initiation,
        effect_index: 1,
        composition: CriticalChanceModifierCompositionInput::Additive,
        activation: CriticalChanceModifierActivationInput::Passive,
        target_health: Some(CriticalHealthConditionInput {
            comparison: TargetHealthComparisonInput::AtOrAbove,
            percent: 80,
        }),
    });
    let (action, program) = resolved_action_program(
        WRATH,
        ResolvedRootTargetInput::PrimaryTarget,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
    );
    input.actions.push(action);
    input.programs.push(program);
    let actions = ResolvedActionCatalog::try_from_input(input).expect("actions");
    CombatProgram::builder(&data, &actions).build()
}

/// R1 evidence for CSA-J-03: the Initiation row J cites (193878: Disabled owner attributes 4,
/// 122, 254, 385 besides Passive/Ignored 6, 8, 143) is refused by the owner-attribute audit, so
/// the real witness must be the clean duplicate 213539.
#[test]
fn holds_cited_initiation_193878_is_refused_by_owner_attribute_audit() {
    let error = initiation_program(193_878, words(&[4, 6, 8, 122, 143, 254, 385]))
        .expect_err("193878 is refused");
    assert!(format!("{error:?}").contains("SelectedSpellAttribute"), "{error:?}");
    assert!(initiation_program(INITIATION, words(&[6, 143, 355])).is_ok());
}

fn initiation_results(current: f64, maximum: f64, seeds: u64) -> Vec<HitResult> {
    let program = initiation_program(INITIATION, words(&[6, 143, 355]))
        .expect("real Initiation 213539 + 228917 compile");

    (0..seeds)
        .map(|seed| {
            let actors = vec![
                ActorState::try_new(
                    ActorId::Player,
                    Vec::new(),
                    Some(HealthPool::full(1_000.0).expect("health")),
                    OffensivePower::ZERO,
                    HasteMultipliers::UNHASTED,
                )
                .expect("player"),
                ActorState::try_new(
                    enemy(),
                    Vec::new(),
                    Some(HealthPool::try_new(current, maximum).expect("health")),
                    OffensivePower::ZERO,
                    HasteMultipliers::UNHASTED,
                )
                .expect("enemy"),
            ];
            let mut state = program
                .try_state(
                    CombatStateInput::new(actors, RandomStreamIdentity::new(seed, 183))
                        .with_hostile_target(ActorId::Player, enemy())
                        .with_critical_strike(
                            CriticalStrikeBaseline::try_new(
                                ActorId::Player,
                                CriticalStrikeChances {
                                    spell: 0.7,
                                    weapon: 0.7,
                                },
                                2.0,
                            )
                            .expect("baseline"),
                        )
                        .with_passive_critical_modifier_bindings(vec![
                            PassiveCriticalModifierBinding::new(
                                ActorId::Player,
                                SpellEffectRef::new(SpellId::new(INITIATION).expect("id"), 1)
                                    .expect("effect"),
                            ),
                        ]),
                )
                .expect("state");
            let mut output = Vec::new();
            state
                .cast(
                    CastRequest::new(ActorId::Player, enemy(), SpellId::new(WRATH).expect("id")),
                    &mut output,
                )
                .expect("cast");
            output
                .iter()
                .find_map(|observation| match observation {
                    CombatObservation::DamageDealt { result, .. } => Some(*result),
                    _ => None,
                })
                .expect("damage")
        })
        .collect()
}

/// CSA-J-03 real-row witness (R1 upholds LIVE): with real Initiation 213539 (+30, AtOrAbove 80)
/// and baseline 0.70, a target at 800/1001 is below Core's exact 800.8 threshold, so some seeds
/// Hit; Trinity's integer threshold uint64(1001.0f * 80 / 100) = 800 makes every seed Critical.
/// Control: at 801/1001 Core applies the +30 and every seed crits.
#[test]
fn defect_real_initiation_threshold_is_untruncated() {
    assert!(initiation_results(801.0, 1_001.0, 16)
        .iter()
        .all(|result| *result == HitResult::Critical));
    assert!(initiation_results(800.0, 1_001.0, 16).contains(&HitResult::Hit));
}

fn mana_confluence_spend() -> (f64, f64) {
    let data = GameData::try_from_input(GameDataInput {
        identity: identity(),
        power_types: vec![PowerTypeInput {
            power_type: i32::from(u8::from(ResourceType::Mana)),
            display_divisor: 1.0,
        }],
        spells: vec![
            spell(TIME_STEP, 0, 1, 1, 1, [0; 17]),
            spell(MANA_CONFLUENCE, 3, 1, 0, 1, words(&[6, 7])),
        ],
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects: vec![effect(MANA_CONFLUENCE, 1, 6, 423, (0, 0), -5.0, 1)],
        effect_attributes: Vec::new(),
    })
    .expect("real rows are valid game data");
    let mut input = empty_resolved_action_catalog_input(identity());
    input.power_cost_modifiers.push(PowerCostModifierInput {
        spell_id: MANA_CONFLUENCE,
        effect_index: 1,
        activation: PowerCostActivationInput::Passive,
    });
    input.actions.push(ResolvedActionInput {
        spell_id: TIME_STEP,
        root_program_id: TIME_STEP,
        target: ResolvedRootTargetInput::Caster,
        resource_spends: vec![ResolvedResourceSpendInput::fixed(0, ResourceType::Mana, 5.0)],
        cast_policy: wowlab_data::ResolvedCastPolicyInput::default(),
        timing: ResolvedActionTimingInput::default(),
    });
    input.programs.push(ResolvedSpellProgramInput {
        node_id: TIME_STEP,
        spell_id: TIME_STEP,
        activation_steps: Vec::new(),
        impacts: Vec::new(),
    });
    let actions = ResolvedActionCatalog::try_from_input(input).expect("actions");
    let program = CombatProgram::builder(&data, &actions)
        .build()
        .expect("real Mana Confluence 1270845 compiles");
    let actor = ActorState::try_new(
        ActorId::Player,
        vec![ResourcePool::try_new(ResourceType::Mana, 100.0, 100.0).expect("mana")],
        None,
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("actor");
    let mut state = program
        .try_state(
            CombatStateInput::new(vec![actor], RandomStreamIdentity::new(423, 2))
                .with_passive_power_cost_binding(PassivePowerCostBinding::new(
                    ActorId::Player,
                    SpellEffectRef::new(SpellId::new(MANA_CONFLUENCE).expect("id"), 1)
                        .expect("effect"),
                )),
        )
        .expect("state");
    let mut output = Vec::new();
    state
        .cast(
            CastRequest::new(ActorId::Player, ActorId::Player, SpellId::new(TIME_STEP).expect("id")),
            &mut output,
        )
        .expect("cast");
    let requested = output
        .iter()
        .find_map(|observation| match observation {
            CombatObservation::ResourceCostApplied { cost, .. } => Some(cost.requested()),
            _ => None,
        })
        .expect("cost");
    let balance = state
        .actor(ActorId::Player)
        .and_then(|actor| actor.resource(ResourceType::Mana))
        .expect("mana")
        .current();
    (requested, balance)
}

/// CSA-J-06 real-row witness (R1 upholds LIVE): real Mana Confluence -5 % on the real 5-mana
/// Time Step cost charges 4.75 (Trinity int32(5.0f * 0.95f) = 4, SpellInfo.cpp:4238-4240).
#[test]
fn defect_real_mana_confluence_leaves_fractional_cost() {
    let (requested, balance) = mana_confluence_spend();
    assert_eq!(requested, 4.75);
    assert_eq!(balance, 95.25);
    assert_eq!((5.0_f32 * (1.0_f32 + (-5.0_f32 / 100.0_f32))) as i32, 4);
}
