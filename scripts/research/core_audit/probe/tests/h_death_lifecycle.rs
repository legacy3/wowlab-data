//! Track H: actor death vs aura lifecycle (CSA-H-01 / LC-H-010 / MUT-H-*).
//!
//! Builds a minimal source-shaped program through the public API:
//! - APPLY (61_001): ApplyAura AURA (61_100, 100 ms) on the primary target;
//! - NUKE (61_003): direct school damage (kind 2) of 20_000 on the primary target;
//! - AURA effect 1: SPELL_AURA_TRIGGER_SPELL_ON_EXPIRE (misc 1 => original aura source) whose child
//!   (61_201) energizes the caster (+10 Energy).
//!
//! Trinity (`AuraEffect::HandleTriggerSpellOnExpire`, SpellAuraEffects.cpp:5422-5439) fires only for
//! `AURA_REMOVE_BY_EXPIRE`, and `Unit::RemoveAllAurasOnDeath` (Unit.cpp:4472-4493) removes every
//! non-passive, non-death-persistent aura with `AURA_REMOVE_BY_DEATH` when the unit dies.
//! Core keeps the aura on the dead recipient and fires the natural-expiry driver later.

use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    HasteMultipliers, HealthPool, OffensivePower, ResourcePool,
};
use wowlab_data::{
    CURRENT_RESOLVED_ACTION_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION, DataVersion, EffectInput,
    EffectKind, GameData, GameDataIdentity, GameDataInput, ResolvedActionCatalog,
    ResolvedActionCatalogInput, ResolvedActionInput, ResolvedActionRecipientInput,
    ResolvedActionTimingInput, ResolvedAuraInput, ResolvedAuraReapplicationInput,
    ResolvedExpiryConditionInput, ResolvedExpirySnapshotInput, ResolvedExpirySourceInput,
    ResolvedExpiryTargetInput, ResolvedExpiryTriggerInput, ResolvedProgramStepInput,
    ResolvedRootTargetInput, ResolvedSpellProgramInput, ResourceGainAmountInput, SpellInput,
};
use wowlab_dbc::AuraSubtypeKind;
use wowlab_model::{ActorId, AuraId, AuraKey, DifficultyId, ResourceType, SimTime, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::partition_program_steps;

const APPLY_RAW: u32 = 61_001;
const NUKE_RAW: u32 = 61_003;
const AURA_RAW: u32 = 61_100;
const CHILD_RAW: u32 = 61_201;
const DURATION: SimTime = SimTime::from_millis(100);

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

fn effect(
    spell_id: u32,
    index: u8,
    kind: u32,
    subtype: i32,
    misc: i32,
    base_points: f64,
    trigger: Option<u32>,
) -> EffectInput {
    EffectInput {
        amount_facts: wowlab_data::EffectAmountFactsInput::NEUTRAL,
        chain_facts: wowlab_data::EffectChainFactsInput::NEUTRAL,
        spell_id,
        index,
        kind,
        aura_subtype: subtype,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: misc,
        misc_value_1: 0,
        radius_index_0: 0,
        radius_index_1: 0,
        implicit_target_a: 0,
        implicit_target_b: 0,
        spell_class_mask: [0; 4],
        base_points,
        attack_power_coefficient: 0.0,
        spell_power_coefficient: 0.0,
        trigger_spell_id: trigger,
    }
}

fn program(node: u32, steps: Vec<ResolvedProgramStepInput>) -> ResolvedSpellProgramInput {
    let (activation_steps, impacts) = partition_program_steps(steps);
    ResolvedSpellProgramInput {
        node_id: node,
        spell_id: node,
        activation_steps,
        impacts,
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

fn build() -> CombatProgram {
    let identity = identity();
    let spells = vec![
        spell_input(APPLY_RAW),
        spell_input(NUKE_RAW),
        spell_input(AURA_RAW),
        spell_input(CHILD_RAW),
    ];
    let effects = vec![
        effect(APPLY_RAW, 1, EffectKind::APPLY_AURA.get(), 0, 0, 0.0, None),
        effect(NUKE_RAW, 1, 2, 0, 0, 20_000.0, None),
        effect(
            AURA_RAW,
            1,
            EffectKind::APPLY_AURA.get(),
            i32::from(AuraSubtypeKind::TriggerSpellOnExpire.raw()),
            1,
            0.0,
            Some(CHILD_RAW),
        ),
        effect(CHILD_RAW, 1, 30, 0, 3, 10.0, None),
    ];
    let programs = vec![
        program(
            APPLY_RAW,
            vec![ResolvedProgramStepInput::ApplyAura {
                recipient: ResolvedActionRecipientInput::Target,
                effect_index: 1,
                aura_id: AURA_RAW,
                stacks: 1,
                reapplication: ResolvedAuraReapplicationInput::RestartLifetime,
            }],
        ),
        program(
            NUKE_RAW,
            vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
        ),
        program(
            CHILD_RAW,
            vec![ResolvedProgramStepInput::GainResource {
                recipient: ResolvedActionRecipientInput::Caster,
                effect_index: 1,
                resource: ResourceType::Energy,
                amount: ResourceGainAmountInput::Fixed { amount: 10.0 },
            }],
        ),
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
    .expect("death probe game data is valid");
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
        periodic_triggers: Vec::new(),
        periodic_energizes: Vec::new(),
        expiry_triggers: vec![ResolvedExpiryTriggerInput {
            aura_id: AURA_RAW,
            effect_index: 1,
            child_node_id: CHILD_RAW,
            source: ResolvedExpirySourceInput::OriginalAuraSource,
            target: ResolvedExpiryTargetInput::AuraRecipient,
            condition: ResolvedExpiryConditionInput::NaturalExpiration,
            snapshot: ResolvedExpirySnapshotInput::FreshAtExpiration,
        }],
        periodic_mana_leeches: Vec::new(),
        health_threshold_triggers: Vec::new(),
        resource_threshold_triggers: Vec::new(),
        cooldown_slots: Vec::new(),
        charge_pools: Vec::new(),
        actions: vec![action(APPLY_RAW), action(NUKE_RAW)],
        programs,
    })
    .expect("death probe action catalog is valid");

    CombatProgram::builder(&data, &actions)
        .build()
        .expect("death probe program compiles")
}

fn state(program: &CombatProgram) -> CombatState {
    let player = ActorState::try_new(
        ActorId::Player,
        vec![ResourcePool::try_new(ResourceType::Energy, 0.0, 100.0).expect("energy")],
        Some(HealthPool::full(10_000.0).expect("health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("player");
    let target = ActorState::try_new(
        ActorId::External,
        Vec::new(),
        Some(HealthPool::full(10_000.0).expect("health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("target");

    program
        .try_state(
            CombatStateInput::new(vec![player, target], RandomStreamIdentity::new(31, 37))
                .with_hostile_target(ActorId::Player, ActorId::External),
        )
        .expect("death probe state is valid")
}

fn spell(raw: u32) -> SpellId {
    SpellId::new(raw).expect("nonzero")
}

fn key() -> AuraKey {
    AuraKey::new(AuraId::new(AURA_RAW).expect("aura"), ActorId::Player, ActorId::External)
}

fn energy(state: &CombatState) -> f64 {
    state
        .actor(ActorId::Player)
        .and_then(|actor| actor.resource(ResourceType::Energy))
        .map(ResourcePool::current)
        .expect("player energy")
}

fn cast(state: &mut CombatState, raw: u32) -> Result<Vec<CombatObservation>, wowlab_combat::CastErrorCode> {
    let mut output = Vec::new();
    state
        .cast(CastRequest::new(ActorId::Player, ActorId::External, spell(raw)), &mut output)
        .map(|_| ())
        .map_err(|error| error.code())?;
    Ok(output)
}

/// CSA-H-01 (LIVE): the recipient dies while an on-expire aura is active; Core keeps the aura on
/// the dead actor and later fires its natural-expiry driver, energizing the living source.
#[test]
fn defect_on_expire_driver_fires_from_aura_on_dead_recipient() {
    let program = build();
    let mut state = state(&program);

    cast(&mut state, APPLY_RAW).expect("apply succeeds");
    let nuke = cast(&mut state, NUKE_RAW).expect("nuke succeeds");
    assert!(
        nuke.iter().any(|observation| matches!(
            observation,
            CombatObservation::ActorDied { actor: ActorId::External, .. }
        )),
        "nuke kills the recipient: {nuke:?}"
    );
    assert_eq!(
        state
            .actor(ActorId::External)
            .and_then(ActorState::health)
            .map(|health| health.current()),
        Some(0.0)
    );

    // Observed: the aura survives death (Trinity removes it with AURA_REMOVE_BY_DEATH).
    assert!(state.active_aura(key()).is_some(), "aura persists on dead actor");
    assert_eq!(state.pending_timer_count(), 1, "expiry deadline stays scheduled");

    let observations = state.advance_to(DURATION).expect("forward");
    eprintln!("expiry observations after death: {observations:#?}");

    assert!(observations.iter().any(|observation| matches!(
        observation,
        CombatObservation::AuraExpired { key: expired, .. } if *expired == key()
    )));
    // Observed defect: the natural-expiry trigger energizes the source 100 ms after the
    // recipient died. Trinity never fires TRIGGER_SPELL_ON_EXPIRE for a death removal.
    assert!(observations.iter().any(|observation| matches!(
        observation,
        CombatObservation::ResourceGained { .. }
    )));
    assert_eq!(energy(&state), 10.0);
}

/// CSA-H-02 (LIVE): an aura-only action targeting a dead actor commits (no dead-target gate for
/// aura mutations), re-arming the on-expire driver on a corpse.
#[test]
fn defect_aura_application_to_dead_target_commits() {
    let program = build();
    let mut state = state(&program);

    cast(&mut state, NUKE_RAW).expect("nuke succeeds");
    assert_eq!(
        state
            .actor(ActorId::External)
            .and_then(ActorState::health)
            .map(|health| health.is_alive()),
        Some(false)
    );

    // A damage action on the corpse is rejected ...
    assert_eq!(
        cast(&mut state, NUKE_RAW).expect_err("damage on dead target rejects"),
        wowlab_combat::CastErrorCode::DeadTarget
    );

    // ... but the aura-only action is accepted and schedules an expiry on the corpse.
    let applied = cast(&mut state, APPLY_RAW).expect("aura application on dead target commits");
    eprintln!("apply on dead target: {applied:#?}");
    assert!(state.active_aura(key()).is_some());

    state.advance_to(DURATION).expect("forward");
    assert_eq!(energy(&state), 10.0, "on-expire driver fires from the corpse aura");
}

/// Control: with a living recipient the natural-expiry driver fires exactly once (+10 energy),
/// so the defect probes above differ only by the recipient's death.
#[test]
fn holds_on_expire_driver_fires_for_living_recipient() {
    let program = build();
    let mut state = state(&program);

    cast(&mut state, APPLY_RAW).expect("apply succeeds");
    state.advance_to(DURATION).expect("forward");
    assert_eq!(energy(&state), 10.0);
    assert!(state.active_aura(key()).is_none());
}

/// CSA-H-03 (LIVE): a dead caster keeps acting. External kills the Player, then the dead Player
/// successfully casts APPLY (aura on External) and NUKE (20,000 damage to External).
/// Trinity: Spell::CheckCast returns SPELL_FAILED_CASTER_DEAD for a dead caster unless the spell
/// has SPELL_ATTR0_ALLOW_CAST_WHILE_DEAD (Spell.cpp CheckCast), and Unit::setDeathState interrupts
/// any non-channel cast in progress.
#[test]
fn defect_dead_caster_keeps_casting() {
    let program = build();
    let player = ActorState::try_new(
        ActorId::Player,
        vec![ResourcePool::try_new(ResourceType::Energy, 0.0, 100.0).expect("energy")],
        Some(HealthPool::full(10_000.0).expect("health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("player");
    let external = ActorState::try_new(
        ActorId::External,
        Vec::new(),
        Some(HealthPool::full(100_000.0).expect("health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("external");
    let mut state = program
        .try_state(
            CombatStateInput::new(vec![player, external], RandomStreamIdentity::new(31, 37))
                .with_hostile_target(ActorId::Player, ActorId::External)
                .with_hostile_target(ActorId::External, ActorId::Player),
        )
        .expect("two-way hostile state builds");

    let mut output = Vec::new();
    state
        .cast(CastRequest::new(ActorId::External, ActorId::Player, spell(NUKE_RAW)), &mut output)
        .expect("external kills player");
    assert!(output.iter().any(|observation| matches!(
        observation,
        CombatObservation::ActorDied { actor: ActorId::Player, .. }
    )));

    let mut output = Vec::new();
    let damage = state.cast(
        CastRequest::new(ActorId::Player, ActorId::External, spell(NUKE_RAW)),
        &mut output,
    );
    eprintln!("dead caster nuke: {:?}", damage.as_ref().map(|_| ()).map_err(|e| e.code()));
    assert!(damage.is_ok(), "dead Player's damage cast commits");
    assert!(output.iter().any(|observation| matches!(
        observation,
        CombatObservation::DamageDealt { source: ActorId::Player, target: ActorId::External, .. }
    )));
    assert_eq!(
        state
            .actor(ActorId::External)
            .and_then(ActorState::health)
            .map(|health| health.current()),
        Some(80_000.0)
    );
}
