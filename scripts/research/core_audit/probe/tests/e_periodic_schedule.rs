//! Track E probes: periodic-trigger schedule arithmetic through the public API.
//!
//! Records: MUT-E-*, NUM-E-*, REJ-E-* cite these tests by name.

use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    HasteMultipliers, HasteMultipliersInput, HealthPool, OffensivePower,
};
use wowlab_data::{
    CURRENT_RESOLVED_ACTION_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION, DataVersion, EffectInput,
    EffectKind, GameData, GameDataIdentity, GameDataInput, ResolvedActionCatalog,
    ResolvedActionCatalogInput, ResolvedActionInput, ResolvedActionRecipientInput,
    ResolvedActionTimingInput, ResolvedAuraInput, ResolvedAuraReapplicationInput,
    ResolvedPeriodicCadenceInput, ResolvedPeriodicConditionInput, ResolvedPeriodicInitialInput,
    ResolvedPeriodicSnapshotInput, ResolvedPeriodicTargetInput, ResolvedPeriodicTriggerInput,
    ResolvedProgramStepInput, ResolvedRootTargetInput, ResolvedSpellProgramInput, SpellInput,
};
use wowlab_dbc::AuraSubtypeKind;
use wowlab_model::{ActorId, DifficultyId, SimTime, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::partition_program_steps;

const APPLY_RAW: u32 = 72_001;
const REMOVE_RAW: u32 = 72_002;
const AURA_RAW: u32 = 72_100;
const CHILD_RAW: u32 = 72_201;

const TICK_ON_APPLICATION_RAW: u32 = 169;
const SPELL_HASTE_AFFECTS_PERIODIC_RAW: u32 = 173;

#[derive(Clone, Copy)]
struct Shape {
    duration_ms: u32,
    period_ms: i32,
    cadence: ResolvedPeriodicCadenceInput,
    initial: ResolvedPeriodicInitialInput,
    hasted: bool,
    spell_cast_speed: f64,
}

impl Shape {
    const fn base(duration_ms: u32, period_ms: i32) -> Self {
        Self {
            duration_ms,
            period_ms,
            cadence: ResolvedPeriodicCadenceInput::RestartCadence,
            initial: ResolvedPeriodicInitialInput::AfterFirstPeriod,
            hasted: false,
            spell_cast_speed: 1.0,
        }
    }
}

fn set_attribute(attributes: &mut [i32; 17], raw: u32) {
    attributes[(raw / 32) as usize] |= (1_u32 << (raw % 32)) as i32;
}

fn build(shape: Shape) -> CombatProgram {
    let identity = identity();
    let mut aura_spell = spell_input(AURA_RAW);

    if matches!(shape.initial, ResolvedPeriodicInitialInput::OnFreshApplication) {
        set_attribute(&mut aura_spell.attributes, TICK_ON_APPLICATION_RAW);
    }
    if shape.hasted {
        set_attribute(&mut aura_spell.attributes, SPELL_HASTE_AFFECTS_PERIODIC_RAW);
    }

    let mut carrier = plain_effect(AURA_RAW, 1, EffectKind::APPLY_AURA.get(), 0.0);
    carrier.aura_subtype = i32::from(AuraSubtypeKind::PeriodicTriggerSpell.raw());
    carrier.aura_period_ms = shape.period_ms;
    carrier.trigger_spell_id = Some(CHILD_RAW);

    let spells = vec![
        spell_input(APPLY_RAW),
        spell_input(REMOVE_RAW),
        aura_spell,
        spell_input(CHILD_RAW),
    ];
    let effects = vec![
        plain_effect(APPLY_RAW, 1, EffectKind::APPLY_AURA.get(), 0.0),
        plain_effect(REMOVE_RAW, 1, 164, 0.0),
        carrier,
        plain_effect(CHILD_RAW, 1, 2, 10.0),
    ];
    let (child_activation, child_impacts) =
        partition_program_steps(vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }]);
    let programs = vec![
        ResolvedSpellProgramInput {
            node_id: APPLY_RAW,
            spell_id: APPLY_RAW,
            activation_steps: Vec::new(),
            impacts: vec![wowlab_data::ResolvedImpactInput {
                recipient: ResolvedActionRecipientInput::Target,
                steps: vec![ResolvedProgramStepInput::ApplyAura {
                    recipient: ResolvedActionRecipientInput::Target,
                    effect_index: 1,
                    aura_id: AURA_RAW,
                    stacks: 1,
                    reapplication: ResolvedAuraReapplicationInput::RestartLifetime,
                }],
            }],
        },
        ResolvedSpellProgramInput {
            node_id: REMOVE_RAW,
            spell_id: REMOVE_RAW,
            activation_steps: Vec::new(),
            impacts: vec![wowlab_data::ResolvedImpactInput {
                recipient: ResolvedActionRecipientInput::Target,
                steps: vec![ResolvedProgramStepInput::RemoveAura {
                    recipient: ResolvedActionRecipientInput::Target,
                    effect_index: 1,
                    aura_id: AURA_RAW,
                }],
            }],
        },
        ResolvedSpellProgramInput {
            node_id: CHILD_RAW,
            spell_id: CHILD_RAW,
            activation_steps: child_activation,
            impacts: child_impacts,
        },
    ];

    let data = GameData::try_from_input(GameDataInput {
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        power_types: Vec::new(),
        identity,
        spells,
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effect_attributes: Vec::new(),
        effects,
    })
    .expect("probe game data is valid");
    let actions = ResolvedActionCatalog::try_from_input(ResolvedActionCatalogInput {
        schema: CURRENT_RESOLVED_ACTION_SCHEMA_VERSION,
        game_data: identity,
        auras: vec![ResolvedAuraInput::finite(AURA_RAW, shape.duration_ms, 1)],
        power_cost_modifiers: Vec::new(),
        spell_power_from_attack_power: Vec::new(),
        haste_modifiers: Vec::new(),
        active_defense_capabilities: wowlab_data::ActiveDefenseCapabilitiesInput::default(),
        defense_chance_modifiers: Vec::new(),
        hit_chance_modifiers: Vec::new(),
        versatility_benefits: Vec::new(),
        versatility_modifiers: Vec::new(),
        maximum_health_modifiers: Vec::new(),
        maximum_resource_modifiers: Vec::new(),
        passive_flow_flats: Vec::new(),
        passive_flow_percentages: Vec::new(),
        passive_flow_suppressions: Vec::new(),
        damage_modifiers: Vec::new(),
        share_damages: Vec::new(),
        spell_modifier_family: wowlab_data::SpellModifierFamilyInput::default(),
        target_aura_state_modifiers: Vec::new(),
        absorb_family: wowlab_data::ResolvedAbsorbFamilyInput {
            providers: Vec::new(),
            source_capacity_modifiers: Vec::new(),
            recipient_capacity_modifiers: Vec::new(),
            target_bypasses: Vec::new(),
        },
        interrupts: Vec::new(),
        immunity_family: wowlab_data::ResolvedImmunityFamilyInput::default(),
        action_preventions: wowlab_data::ResolvedActionPreventionInput::default(),
        healing_modifiers: Vec::new(),
        critical_healing_modifiers: Vec::new(),
        mechanic_damage_done_modifiers: Vec::new(),
        critical_chance_modifiers: Vec::new(),
        critical_damage_modifiers: Vec::new(),
        critical_block_amount_modifiers: Vec::new(),
        cooldown_recovery_rates: Vec::new(),
        dispel_duration_modifiers: Vec::new(),
        mechanic_duration_modifiers: Vec::new(),
        mechanic_resistances: Vec::new(),
        ordinary_category_duration_modifiers: Vec::new(),
        charge_duration_modifiers: Vec::new(),
        charge_capacity_modifiers: Vec::new(),
        charge_recovery_haste: Vec::new(),
        charge_recharge_rates: Vec::new(),
        periodic_damages: Vec::new(),
        periodic_heals: Vec::new(),
        periodic_triggers: vec![ResolvedPeriodicTriggerInput {
            aura_id: AURA_RAW,
            effect_index: 1,
            child_node_id: CHILD_RAW,
            target: ResolvedPeriodicTargetInput::ApplicationPrimaryTarget,
            condition: ResolvedPeriodicConditionInput::WhileAuraActive,
            snapshot: ResolvedPeriodicSnapshotInput::FreshAtTick,
            initial_tick: shape.initial,
            cadence_reapplication: shape.cadence,
        }],
        periodic_energizes: Vec::new(),
        expiry_triggers: Vec::new(),
        periodic_mana_leeches: Vec::new(),
        health_threshold_triggers: Vec::new(),
        resource_threshold_triggers: Vec::new(),
        cooldown_slots: Vec::new(),
        charge_pools: Vec::new(),
        actions: vec![action(APPLY_RAW), action(REMOVE_RAW)],
        programs,
    })
    .expect("probe action catalog is valid");

    CombatProgram::builder(&data, &actions)
        .build()
        .expect("probe program compiles")
}

fn state(program: &CombatProgram, spell_cast_speed: f64) -> CombatState {
    let power = OffensivePower::try_new(100.0, 0.0).expect("valid power");
    let haste = HasteMultipliers::try_from_input(HasteMultipliersInput {
        spell_haste: 1.0,
        spell_cast_speed,
        attack_haste: 1.0,
        auto_attack_speed: 1.0,
        haste_regeneration: 1.0,
    })
    .expect("valid haste");
    let player = ActorState::try_new(
        ActorId::Player,
        Vec::new(),
        Some(HealthPool::full(1.0e9).expect("valid health")),
        power,
        haste,
    )
    .expect("valid player");
    let target = ActorState::try_new(
        ActorId::External,
        Vec::new(),
        Some(HealthPool::full(1.0e9).expect("valid health")),
        power,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid target");

    program
        .try_state(
            CombatStateInput::new(vec![target, player], RandomStreamIdentity::new(5, 7))
                .with_hostile_target(ActorId::Player, ActorId::External),
        )
        .expect("probe state is valid")
}

fn cast(state: &mut CombatState, raw: u32) -> Vec<CombatObservation> {
    let mut output = Vec::new();
    state
        .cast(
            CastRequest::new(ActorId::Player, ActorId::External, spell(raw)),
            &mut output,
        )
        .expect("probe cast succeeds");
    output
}

fn tick_times(observations: &[CombatObservation]) -> Vec<u32> {
    observations
        .iter()
        .filter_map(|o| match o {
            CombatObservation::DamageDealt { at, .. } => Some(at.as_millis()),
            _ => None,
        })
        .collect()
}

fn run(shape: Shape, script: &[(u32, u32)], until: u32) -> (Vec<u32>, Vec<CombatObservation>) {
    let program = build(shape);
    let mut state = state(&program, shape.spell_cast_speed);
    let mut all = Vec::new();

    for &(at, raw) in script {
        all.extend(
            state
                .advance_to(SimTime::from_millis(at))
                .expect("advance"),
        );
        all.extend(cast(&mut state, raw));
    }
    all.extend(
        state
            .advance_to(SimTime::from_millis(until))
            .expect("advance"),
    );

    (tick_times(&all), all)
}

/// Holds (REJ-E same-time ordering): a full tick due exactly at the aura deadline runs
/// before the aura expires (periodic rank 5 < expiry rank 6, state/transition.rs:25,70).
#[test]
fn holds_exact_expiry_tick_runs_before_aura_expiry() {
    let (ticks, all) = run(Shape::base(100, 20), &[(0, APPLY_RAW)], 200);
    assert_eq!(ticks, vec![20, 40, 60, 80, 100]);
    let last_damage = all
        .iter()
        .rposition(|o| matches!(o, CombatObservation::DamageDealt { .. }))
        .unwrap();
    let expiry = all
        .iter()
        .position(|o| matches!(o, CombatObservation::AuraExpired { .. }))
        .unwrap();
    assert!(last_damage < expiry);
}

/// Holds (MUT-E cadence): restart-cadence refresh discards the partial period (Trinity
/// ResetPeriodic(true) semantics, SpellAuraEffects.cpp:949-958).
#[test]
fn holds_restart_cadence_refresh_discards_partial_period() {
    let (ticks, _) = run(Shape::base(100, 20), &[(0, APPLY_RAW), (50, APPLY_RAW)], 400);
    assert_eq!(ticks, vec![20, 40, 70, 90, 110, 130, 150]);
}

/// Holds (MUT-E cadence): preserve-cadence refresh under RestartLifetime keeps the anchor;
/// the aura deadline moves, cadence does not.
#[test]
fn holds_preserve_cadence_refresh_keeps_anchor() {
    let mut shape = Shape::base(100, 20);
    shape.cadence = ResolvedPeriodicCadenceInput::PreserveCadence;
    let (ticks, _) = run(shape, &[(0, APPLY_RAW), (50, APPLY_RAW)], 400);
    assert_eq!(ticks, vec![20, 40, 60, 80, 100, 120, 140]);
}

/// Holds (REJ-E TickOnApplication): initial occurrence only on fresh application; an
/// active restart-cadence refresh does not re-tick (matches simc dot.cpp:972-975
/// tick_on_application semantics; differs from Trinity ResetPeriodic).
#[test]
fn holds_tick_on_application_only_on_fresh_application() {
    let mut shape = Shape::base(100, 20);
    shape.initial = ResolvedPeriodicInitialInput::OnFreshApplication;
    let (ticks, _) = run(shape, &[(0, APPLY_RAW), (50, APPLY_RAW)], 400);
    assert_eq!(ticks, vec![0, 20, 40, 70, 90, 110, 130, 150]);
}

/// Holds (MUT-E removal): explicit removal cancels the pending deadline; no stale tick.
#[test]
fn holds_explicit_removal_leaves_no_stale_tick() {
    let (ticks, _) = run(Shape::base(100, 20), &[(0, APPLY_RAW), (50, REMOVE_RAW)], 400);
    assert_eq!(ticks, vec![20, 40]);
}

/// NUM-E-001: SpellCastSpeed period = trunc(authored / speed) in f64
/// (recovery.rs:113-117). 1000 / 1.3 = 769.23 -> 769 ms cadence; aura duration unchanged.
#[test]
fn holds_spell_cast_speed_period_truncates() {
    let mut shape = Shape::base(4000, 1000);
    shape.hasted = true;
    shape.spell_cast_speed = 1.3;
    let (ticks, _) = run(shape, &[(0, APPLY_RAW)], 10_000);
    assert_eq!(ticks, vec![769, 1538, 2307, 3076, 3845]);
}

/// NUM-E-001 witness: 1100 / 1.1 = 999.9999999999999 in f64, so Core schedules a 999 ms
/// cadence where exact arithmetic (and Trinity's int32(period * f32 ModCastingSpeed),
/// SpellAuraEffects.cpp:1006-1007, and simc's reciprocal multiply) give 1000 ms.
/// Synthetic period: no real raw-173 period (100/250/1000/1500/2000/2500/3000/12000)
/// produces such a witness at 0.01% haste granularity.
#[test]
fn holds_division_truncation_witness_1100_over_1_1_is_999() {
    let mut shape = Shape::base(3000, 1100);
    shape.hasted = true;
    shape.spell_cast_speed = 1.1;
    let (ticks, _) = run(shape, &[(0, APPLY_RAW)], 10_000);
    assert_eq!(ticks, vec![999, 1998, 2997]);
}

fn plain_effect(spell_id: u32, index: u8, kind: u32, base_points: f64) -> EffectInput {
    EffectInput {
        amount_facts: wowlab_data::EffectAmountFactsInput::NEUTRAL,
        chain_facts: wowlab_data::EffectChainFactsInput::NEUTRAL,
        spell_id,
        index,
        kind,
        aura_subtype: 0,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: 0,
        misc_value_1: 0,
        radius_index_0: 0,
        radius_index_1: 0,
        implicit_target_a: 0,
        implicit_target_b: 0,
        spell_class_mask: [0; 4],
        base_points,
        attack_power_coefficient: 0.0,
        spell_power_coefficient: 0.0,
        trigger_spell_id: None,
    }
}

fn action(spell_id: u32) -> ResolvedActionInput {
    ResolvedActionInput {
        spell_id,
        root_program_id: spell_id,
        target: ResolvedRootTargetInput::PrimaryTarget,
        resource_spends: Vec::new(),
        cast_policy: wowlab_data::ResolvedCastPolicyInput::default(),
        timing: ResolvedActionTimingInput::default(),
    }
}

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn spell_input(id: u32) -> SpellInput {
    SpellInput {
        id,
        family_id: wowlab_data::SpellFamilyId::NONE,
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: 0x4,
        defense_type: 0,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: None,
        attributes: [0; 17],
    }
}

fn spell(raw: u32) -> SpellId {
    SpellId::new(raw).expect("non-zero spell")
}
