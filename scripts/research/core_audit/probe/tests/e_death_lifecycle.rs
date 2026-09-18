//! Track E probes: aura / periodic / expiry-driver lifecycle across recipient death.
//!
//! Records: CSA-E-01 (expiry driver runs for an aura whose recipient already died),
//! MUT-E-* / LC-E-* cite these tests.

use wowlab_combat::{
    ActorState, AuraChangeKind, CastRequest, CombatObservation, CombatProgram, CombatState,
    CombatStateInput, HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{
    CURRENT_RESOLVED_ACTION_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION, DataVersion, EffectInput,
    EffectKind, GameData, GameDataIdentity, GameDataInput, ResolvedActionCatalog,
    ResolvedActionCatalogInput, ResolvedActionInput, ResolvedActionRecipientInput,
    ResolvedActionTimingInput, ResolvedAuraInput, ResolvedAuraReapplicationInput,
    ResolvedExpiryConditionInput, ResolvedExpirySnapshotInput, ResolvedExpirySourceInput,
    ResolvedExpiryTargetInput, ResolvedExpiryTriggerInput, ResolvedPeriodicCadenceInput,
    ResolvedPeriodicConditionInput, ResolvedPeriodicSnapshotInput, ResolvedPeriodicTargetInput,
    ResolvedPeriodicTriggerInput, ResolvedProgramStepInput, ResolvedRootTargetInput,
    ResolvedSpellProgramInput, SpellInput,
};
use wowlab_dbc::AuraSubtypeKind;
use wowlab_model::{ActorId, AuraId, AuraKey, DifficultyId, SimTime, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::partition_program_steps;

const APPLY_RAW: u32 = 71_001;
const KILL_RAW: u32 = 71_002;
const AURA_RAW: u32 = 71_100;
const EXPIRY_CHILD_RAW: u32 = 71_201;
const PERIODIC_CHILD_RAW: u32 = 71_202;
const DURATION: SimTime = SimTime::from_millis(100);
const PERIOD: SimTime = SimTime::from_millis(20);

#[derive(Clone, Copy, PartialEq)]
enum ChildPayload {
    Damage(f64),
    ReapplyOwningAura,
}

struct Fixture {
    program: CombatProgram,
}

impl Fixture {
    fn build(expiry_child: Option<ChildPayload>, periodic_child: Option<ChildPayload>) -> Self {
        let identity = identity();
        let mut spells = vec![
            spell_input(APPLY_RAW),
            spell_input(KILL_RAW),
            spell_input(AURA_RAW),
        ];
        let mut effects = vec![
            plain_effect(APPLY_RAW, 1, EffectKind::APPLY_AURA.get(), 0.0),
            plain_effect(KILL_RAW, 1, 2, 1_000_000.0),
        ];
        let mut programs = vec![
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
            child_program(KILL_RAW, ChildPayload::Damage(1_000_000.0)),
        ];
        let mut expiry_triggers = Vec::new();
        let mut periodic_triggers = Vec::new();

        if let Some(payload) = expiry_child {
            spells.push(spell_input(EXPIRY_CHILD_RAW));
            let mut carrier = plain_effect(AURA_RAW, 2, EffectKind::APPLY_AURA.get(), 0.0);
            carrier.aura_subtype = i32::from(AuraSubtypeKind::TriggerSpellOnExpire.raw());
            carrier.misc_value_0 = 1;
            carrier.trigger_spell_id = Some(EXPIRY_CHILD_RAW);
            effects.push(carrier);
            effects.push(payload_effect(EXPIRY_CHILD_RAW, payload));
            expiry_triggers.push(ResolvedExpiryTriggerInput {
                aura_id: AURA_RAW,
                effect_index: 2,
                child_node_id: EXPIRY_CHILD_RAW,
                source: ResolvedExpirySourceInput::OriginalAuraSource,
                target: ResolvedExpiryTargetInput::AuraRecipient,
                condition: ResolvedExpiryConditionInput::NaturalExpiration,
                snapshot: ResolvedExpirySnapshotInput::FreshAtExpiration,
            });
            programs.push(child_program(EXPIRY_CHILD_RAW, payload));
        }

        if let Some(payload) = periodic_child {
            spells.push(spell_input(PERIODIC_CHILD_RAW));
            let mut carrier = plain_effect(AURA_RAW, 3, EffectKind::APPLY_AURA.get(), 0.0);
            carrier.aura_subtype = i32::from(AuraSubtypeKind::PeriodicTriggerSpell.raw());
            carrier.aura_period_ms = PERIOD.as_millis() as i32;
            carrier.trigger_spell_id = Some(PERIODIC_CHILD_RAW);
            effects.push(carrier);
            effects.push(payload_effect(PERIODIC_CHILD_RAW, payload));
            periodic_triggers.push(ResolvedPeriodicTriggerInput {
                aura_id: AURA_RAW,
                effect_index: 3,
                child_node_id: PERIODIC_CHILD_RAW,
                target: ResolvedPeriodicTargetInput::ApplicationPrimaryTarget,
                condition: ResolvedPeriodicConditionInput::WhileAuraActive,
                snapshot: ResolvedPeriodicSnapshotInput::FreshAtTick,
                initial_tick: wowlab_data::ResolvedPeriodicInitialInput::AfterFirstPeriod,
                cadence_reapplication: ResolvedPeriodicCadenceInput::RestartCadence,
            });
            programs.push(child_program(PERIODIC_CHILD_RAW, payload));
        }

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
            auras: vec![ResolvedAuraInput::finite(AURA_RAW, DURATION.as_millis(), 1)],
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
            periodic_triggers,
            periodic_energizes: Vec::new(),
            expiry_triggers,
            periodic_mana_leeches: Vec::new(),
            health_threshold_triggers: Vec::new(),
            resource_threshold_triggers: Vec::new(),
            cooldown_slots: Vec::new(),
            charge_pools: Vec::new(),
            actions: vec![action(APPLY_RAW), action(KILL_RAW)],
            programs,
        })
        .expect("probe action catalog is valid");
        let program = CombatProgram::builder(&data, &actions)
            .build()
            .expect("probe program compiles");

        Self { program }
    }

    fn state(&self) -> CombatState {
        let power = OffensivePower::try_new(100.0, 0.0).expect("valid power");
        self.program
            .try_state(
                CombatStateInput::new(
                    vec![actor(ActorId::External, power), actor(ActorId::Player, power)],
                    RandomStreamIdentity::new(31, 37),
                )
                .with_hostile_target(ActorId::Player, ActorId::External)
                .with_hostile_target(ActorId::External, ActorId::Player),
            )
            .expect("probe state is valid")
    }
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

fn target_is_dead(state: &CombatState) -> bool {
    state
        .actor(ActorId::External)
        .and_then(ActorState::health)
        .is_some_and(|health| !health.is_alive())
}

fn key() -> AuraKey {
    AuraKey::new(aura(AURA_RAW), ActorId::Player, ActorId::External)
}

/// CSA-E-01: the recipient dies while the aura is active; the aura is not removed at
/// death, and at its natural deadline the TriggerSpellOnExpire driver runs its child
/// against the corpse (here: re-applying the aura to the dead actor, which succeeds and
/// arms a fresh deadline). Trinity removes auras on death (AURA_REMOVE_BY_DEATH), so the
/// expire trigger (which requires AURA_REMOVE_BY_EXPIRE) never runs.
#[test]
fn defect_expiry_driver_runs_after_recipient_death_and_reapplies_to_corpse() {
    let fixture = Fixture::build(Some(ChildPayload::ReapplyOwningAura), None);
    let mut state = fixture.state();

    cast(&mut state, APPLY_RAW);
    state.advance_to(SimTime::from_millis(10)).expect("advance");
    let kill = cast(&mut state, KILL_RAW);
    assert!(
        kill.iter()
            .any(|o| matches!(o, CombatObservation::ActorDied { actor: ActorId::External, .. })),
        "kill cast must be lethal: {kill:?}"
    );
    assert!(target_is_dead(&state));
    // Aura survives recipient death.
    assert!(state.active_aura(key()).is_some(), "aura survives death");

    let observations = state.advance_to(DURATION).expect("advance to expiry");
    println!("{observations:#?}");
    assert!(observations.iter().any(|o| matches!(
        o,
        CombatObservation::AuraExpired { at, .. } if *at == DURATION
    )));
    // The expiry child re-applies the aura to the dead recipient.
    assert!(observations.iter().any(|o| matches!(
        o,
        CombatObservation::AuraChanged { at, key: k, change, .. }
            if *at == DURATION && *k == key() && change.kind() == AuraChangeKind::Applied
    )));
    assert!(target_is_dead(&state));
    assert!(state.active_aura(key()).is_some(), "aura re-applied to corpse");
    assert_eq!(state.pending_timer_count(), 1, "corpse aura arms a fresh deadline");
}

/// CSA-E-01 (damage child variant): same lifecycle, damage child fizzles with DeadTarget
/// at natural expiry after death — an observable ExecutionFizzled that Trinity never emits.
#[test]
fn defect_expiry_driver_fizzles_dead_target_after_recipient_death() {
    let fixture = Fixture::build(Some(ChildPayload::Damage(10.0)), None);
    let mut state = fixture.state();

    cast(&mut state, APPLY_RAW);
    state.advance_to(SimTime::from_millis(10)).expect("advance");
    cast(&mut state, KILL_RAW);
    assert!(target_is_dead(&state));

    let observations = state.advance_to(DURATION).expect("advance to expiry");
    println!("{observations:#?}");
    assert!(observations.iter().any(|o| matches!(
        o,
        CombatObservation::ExecutionFizzled { reason: wowlab_combat::CastErrorCode::DeadTarget, .. }
    )));
}

/// CSA-E-01 context: a fresh application onto an already-dead recipient is admitted
/// (no liveness gate on aura application), so a periodic schedule is armed on a corpse.
#[test]
fn defect_fresh_periodic_application_onto_dead_recipient_is_admitted() {
    let fixture = Fixture::build(None, Some(ChildPayload::Damage(10.0)));
    let mut state = fixture.state();

    cast(&mut state, KILL_RAW);
    assert!(target_is_dead(&state));
    let applied = cast(&mut state, APPLY_RAW);
    println!("{applied:#?}");
    assert!(applied.iter().any(|o| matches!(
        o,
        CombatObservation::AuraChanged { change, .. } if change.kind() == AuraChangeKind::Applied
    )));
    assert!(state.active_aura(key()).is_some());
    // aura expiry + periodic cadence both armed on the corpse
    assert_eq!(state.pending_timer_count(), 2);
    // first tick: dead recipient cancels the schedule silently, aura remains until expiry
    let first = state.advance_to(PERIOD).expect("advance");
    println!("{first:#?}");
    assert!(first.is_empty());
    assert_eq!(state.pending_timer_count(), 1);
    assert!(state.active_aura(key()).is_some());
}

/// Holds: a periodic trigger on an aura whose recipient died stops at the next due tick
/// (periodic_execution.rs:449-457) and emits nothing.
#[test]
fn holds_periodic_trigger_cancels_silently_after_recipient_death() {
    let fixture = Fixture::build(None, Some(ChildPayload::Damage(10.0)));
    let mut state = fixture.state();

    cast(&mut state, APPLY_RAW);
    state.advance_to(SimTime::from_millis(10)).expect("advance");
    cast(&mut state, KILL_RAW);
    assert_eq!(state.pending_timer_count(), 2);
    let observations = state.advance_to(DURATION).expect("advance");
    println!("{observations:#?}");
    assert!(!observations.iter().any(|o| matches!(o, CombatObservation::DamageDealt { .. })));
    assert!(!observations.iter().any(|o| matches!(o, CombatObservation::ExecutionFizzled { .. })));
    assert_eq!(state.pending_timer_count(), 0);
}

/// REJ-E-014 (holds, inferred retail parity): the aura SOURCE dies while its periodic trigger
/// is active on a living recipient. Every later tick still executes and its damage child
/// still lands (no caster-health preflight for a triggered direct-damage child), matching the
/// retail/Trinity behaviour that a dead caster's periodic effects keep ticking
/// (Trinity HandlePeriodicDamageAurasTick gates only on target liveness,
/// SpellAuraEffects.cpp:5632-5635).
#[test]
fn holds_dead_source_periodic_trigger_keeps_ticking() {
    let fixture = Fixture::build(None, Some(ChildPayload::Damage(10.0)));
    let mut state = fixture.state();

    cast(&mut state, APPLY_RAW);
    state.advance_to(SimTime::from_millis(10)).expect("advance");
    let mut output = Vec::new();
    state
        .cast(
            CastRequest::new(ActorId::External, ActorId::Player, spell(KILL_RAW)),
            &mut output,
        )
        .expect("kill source");
    assert!(state
        .actor(ActorId::Player)
        .and_then(ActorState::health)
        .is_some_and(|health| !health.is_alive()));
    let observations = state.advance_to(DURATION).expect("advance");
    println!("{observations:#?}");
    let fizzles = observations
        .iter()
        .filter(|o| matches!(o, CombatObservation::ExecutionFizzled { .. }))
        .count();
    let damage = observations
        .iter()
        .filter(|o| matches!(o, CombatObservation::DamageDealt { .. }))
        .count();
    assert_eq!((fizzles, damage), (0, 5), "every tick 20..100 still lands");
}

fn child_program(child_spell: u32, payload: ChildPayload) -> ResolvedSpellProgramInput {
    let step = match payload {
        ChildPayload::Damage(_) => ResolvedProgramStepInput::DirectDamage { effect_index: 1 },
        ChildPayload::ReapplyOwningAura => ResolvedProgramStepInput::ApplyAura {
            recipient: ResolvedActionRecipientInput::Target,
            effect_index: 1,
            aura_id: AURA_RAW,
            stacks: 1,
            reapplication: ResolvedAuraReapplicationInput::RestartLifetime,
        },
    };
    let (activation_steps, impacts) = partition_program_steps(vec![step]);

    ResolvedSpellProgramInput {
        node_id: child_spell,
        spell_id: child_spell,
        activation_steps,
        impacts,
    }
}

fn payload_effect(spell_id: u32, payload: ChildPayload) -> EffectInput {
    match payload {
        ChildPayload::Damage(base) => plain_effect(spell_id, 1, 2, base),
        ChildPayload::ReapplyOwningAura => {
            plain_effect(spell_id, 1, EffectKind::APPLY_AURA.get(), 0.0)
        }
    }
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

fn actor(id: ActorId, power: OffensivePower) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(10_000.0).expect("valid health")),
        power,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

fn spell(raw: u32) -> SpellId {
    SpellId::new(raw).expect("non-zero spell")
}

fn aura(raw: u32) -> AuraId {
    AuraId::new(raw).expect("non-zero aura")
}
