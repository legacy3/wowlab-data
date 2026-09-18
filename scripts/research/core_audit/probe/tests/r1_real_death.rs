//! Reviewer R1 (false positives): real 12.1.0.69497 source-row witnesses for the death cluster
//! (CSA-E-01/H-01, CSA-G-01/H-03, CSA-H-02/G-04, CSA-R2-01). The track probes use synthetic ids.
//!
//! Exact rows (SpellMisc school/attributes/duration, SpellCategories, SpellEffect facts):
//! (No 12.1.0 raw-495 carrier reaches execution: the three carriers whose owner rows pass the
//! catalog gates (433258, 1252143, 1279208) have children Instakill (1,0) — refused by
//! program/instakill.rs:130 UnsupportedImplicitTargets — Script Effect 77, or an infinite
//! (DurationIndex 21) stacking owner; so the driver-after-death clause of E-01/H-01 is LATENT.)
//! - Wrath of the Titans 228917:0 raw-2 Nature 83.963 (rounds to 84), (6,0), DefenseType 1.
//! - Foul Chill 6873:0 ApplyAura raw 87 +50 Frost (misc 16), (6,0), 120 000 ms, DefenseType 1,
//!   DispelType 1, no attributes, no aura options.
//! Host values: health pools, relations, passive flow, resource pools.

use wowlab_combat::{
    ActorState, CastErrorCode, CastRequest, CombatObservation, CombatProgram, CombatState,
    CombatStateInput, HasteMultipliers, HealthPool, OffensivePower, PassiveResourceFlow,
    ResourcePool,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, DispelTypeId, EffectAmountFactsInput,
    EffectChainFactsInput, EffectInput, GameData, GameDataIdentity, GameDataInput,
    ResolvedActionCatalog, ResolvedActionRecipientInput, ResolvedActionTimingInput,
    ResolvedAuraInput, ResolvedAuraReapplicationInput, ResolvedDamageModifierInput,
    ResolvedProgramStepInput, ResolvedRootTargetInput,
    SpellDurationPresenceInput, SpellFamilyId, SpellInput,
};
use wowlab_model::{
    ActorId, AuraId, AuraKey, DifficultyId, EnemyIndex, RecoveryRate, ResourceType, SimTime, SpellId,
};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{empty_resolved_action_catalog_input, resolved_action_program};

const WRATH: u32 = 228_917;
const FOUL_CHILL: u32 = 6_873;

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

fn spell(id: u32, school: u32, defense: u8, dispel: u8, attributes: [i32; 17]) -> SpellInput {
    SpellInput {
        id,
        family_id: SpellFamilyId::NONE,
        dispel_type: DispelTypeId::from_raw(dispel),
        school_mask: school,
        defense_type: defense,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: Some(1),
        attributes,
    }
}

#[allow(clippy::too_many_arguments)]
fn effect(spell_id: u32, kind: u32, aura: i32, misc: i32, points: f64, target: i32, trigger: Option<u32>) -> EffectInput {
    EffectInput {
        spell_id,
        index: 1,
        kind,
        aura_subtype: aura,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: misc,
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
        trigger_spell_id: trigger,
    }
}

fn program() -> CombatProgram {
    let data = GameData::try_from_input(GameDataInput {
        identity: identity(),
        power_types: Vec::new(),
        spells: vec![
            spell(WRATH, 8, 1, 0, [0; 17]),
            spell(FOUL_CHILL, 16, 1, 1, [0; 17]),
        ],
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: [FOUL_CHILL]
            .map(|spell_id| SpellDurationPresenceInput { spell_id })
            .into(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects: vec![
            effect(WRATH, 2, 0, 0, 83.963_386_535_64, 6, None),
            effect(FOUL_CHILL, 6, 87, 16, 50.0, 6, None),
        ],
        effect_attributes: Vec::new(),
    })
    .expect("real rows are valid game data");

    let mut input = empty_resolved_action_catalog_input(identity());
    input.auras = vec![
        ResolvedAuraInput::finite(FOUL_CHILL, 120_000, 1),
    ];
    input.damage_modifiers.push(ResolvedDamageModifierInput::Aura {
        aura_id: FOUL_CHILL,
        effect_index: 1,
        application_effect_index: 1,
    });
    for (raw, target, step) in [
        (
            WRATH,
            ResolvedRootTargetInput::PrimaryTarget,
            ResolvedProgramStepInput::DirectDamage { effect_index: 1 },
        ),
        (
            FOUL_CHILL,
            ResolvedRootTargetInput::PrimaryTarget,
            ResolvedProgramStepInput::ApplyAura {
                recipient: ResolvedActionRecipientInput::Target,
                effect_index: 1,
                aura_id: FOUL_CHILL,
                stacks: 1,
                reapplication: ResolvedAuraReapplicationInput::RestartLifetime,
            },
        ),
    ] {
        let (action, program) = resolved_action_program(
            raw,
            target,
            Vec::new(),
            ResolvedActionTimingInput::default(),
            vec![step],
        );
        input.actions.push(action);
        input.programs.push(program);
    }
    let actions = ResolvedActionCatalog::try_from_input(input).expect("real-row actions");
    CombatProgram::builder(&data, &actions)
        .build()
        .expect("real death-cluster rows compile")
}

fn actor(id: ActorId, health: f64, pools: Vec<ResourcePool>) -> ActorState {
    ActorState::try_new(
        id,
        pools,
        Some(HealthPool::full(health).expect("health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("actor")
}

fn state(player_health: f64, enemy_health: f64) -> CombatState {
    program()
        .try_state(
            CombatStateInput::new(
                vec![
                    actor(ActorId::Player, player_health, Vec::new()),
                    actor(
                        enemy(),
                        enemy_health,
                        vec![ResourcePool::try_new(ResourceType::Mana, 0.0, 1_000.0).expect("mana")],
                    ),
                ],
                RandomStreamIdentity::new(47, 53),
            )
            .with_hostile_target(ActorId::Player, enemy())
            .with_hostile_target(enemy(), ActorId::Player)
            .with_passive_resource_flow(
                PassiveResourceFlow::try_new(enemy(), ResourceType::Mana, 10.0, RecoveryRate::Fixed)
                    .expect("flow"),
            ),
        )
        .expect("state")
}

fn cast(
    state: &mut CombatState,
    source: ActorId,
    target: ActorId,
    raw: u32,
) -> Result<Vec<CombatObservation>, CastErrorCode> {
    let mut output = Vec::new();
    state
        .cast(
            CastRequest::new(source, target, SpellId::new(raw).expect("nonzero")),
            &mut output,
        )
        .map_err(|error| error.code())?;
    Ok(output)
}

fn alive(state: &CombatState, actor: ActorId) -> bool {
    state
        .actor(actor)
        .and_then(ActorState::health)
        .expect("health")
        .is_alive()
}

fn died(output: &[CombatObservation]) -> bool {
    output
        .iter()
        .any(|observation| matches!(observation, CombatObservation::ActorDied { .. }))
}

fn key(raw: u32, source: ActorId, target: ActorId) -> AuraKey {
    AuraKey::new(AuraId::new(raw).expect("aura"), source, target)
}

/// CSA-E-01 / CSA-H-01 real-row witness for the persistence clause (R1 upholds LIVE): the real
/// Foul Chill debuff stays active on the enemy after the real lethal hit, with its deadline still
/// scheduled. Trinity removes it at death (Unit::setDeathState -> RemoveAllAurasOnDeath,
/// Unit.cpp:9179, 4472-4493; 6873 has no death-persistent attribute).
#[test]
fn defect_real_debuff_survives_recipient_death() {
    let mut state = state(10_000.0, 50.0);
    cast(&mut state, ActorId::Player, enemy(), FOUL_CHILL).expect("debuff applies");
    assert!(died(&cast(&mut state, ActorId::Player, enemy(), WRATH).expect("lethal")));
    assert!(!alive(&state, enemy()));
    assert!(
        state
            .active_aura(key(FOUL_CHILL, ActorId::Player, enemy()))
            .is_some(),
        "aura persists on the corpse"
    );
    assert_eq!(state.pending_timer_count(), 1, "its expiry deadline stays scheduled");
}

/// CSA-G-01 / CSA-H-03 real-row witness (R1 upholds LIVE): after the real lethal hit, the dead
/// Player's real Wrath of the Titans passes admission and deals 84 (Trinity CheckCast:
/// SPELL_FAILED_CASTER_DEAD, Spell.cpp:5744-5746).
#[test]
fn defect_real_dead_caster_commits_damage() {
    let mut state = state(50.0, 10_000.0);
    assert!(died(&cast(&mut state, enemy(), ActorId::Player, WRATH).expect("lethal")));
    let output = cast(&mut state, ActorId::Player, enemy(), WRATH).expect("dead caster casts");
    assert!(
        output
            .iter()
            .any(|observation| matches!(observation, CombatObservation::DamageDealt { .. }))
    );
    let enemy_health = state
        .actor(enemy())
        .and_then(ActorState::health)
        .expect("health")
        .current();
    assert_eq!(enemy_health, 10_000.0 - 84.0);
}

/// CSA-H-02 real-row witness (R1 upholds LIVE): after the real lethal hit on the enemy, the real
/// Foul Chill debuff commits a live aura on the corpse while the same real damage row is refused
/// with DeadTarget (Trinity SpellInfo::CheckTarget TARGETS_DEAD, SpellInfo.cpp:2452-2453).
#[test]
fn defect_real_debuff_application_to_dead_target_commits() {
    let mut state = state(10_000.0, 50.0);
    assert!(died(&cast(&mut state, ActorId::Player, enemy(), WRATH).expect("lethal")));
    assert_eq!(
        cast(&mut state, ActorId::Player, enemy(), WRATH).expect_err("damage refused"),
        CastErrorCode::DeadTarget
    );
    cast(&mut state, ActorId::Player, enemy(), FOUL_CHILL).expect("debuff admitted onto corpse");
    assert!(
        state
            .active_aura(key(FOUL_CHILL, ActorId::Player, enemy()))
            .is_some()
    );
}

/// CSA-R2-01 real-row witness (R1 upholds LIVE for the flow / retained-power clauses): the enemy
/// killed by the real hit keeps its host passive Mana flow (0 -> 50 after 5 s dead). Trinity
/// regenerates only while alive (Player.cpp:1025-1029) and zeroes power at death (Unit.cpp:9197).
#[test]
fn defect_real_passive_flow_accrues_on_corpse() {
    let mut state = state(10_000.0, 50.0);
    assert!(died(&cast(&mut state, ActorId::Player, enemy(), WRATH).expect("lethal")));
    state.advance_to(SimTime::from_millis(5_000)).expect("advance");
    assert!(!alive(&state, enemy()));
    let mana = state
        .resource_observation(enemy(), ResourceType::Mana)
        .expect("mana")
        .projected();
    assert_eq!(mana, 50.0);
}

/// R1 evidence for the E-01/H-01 driver clause (LATENT on real data): the admissible-owner
/// raw-495 carrier Explosion Timer 433258 (misc 0, trigger 433259, 20 000 ms, attributes 7+8)
/// cannot reach execution because its child Instakill 433259 at (1,0) is refused by
/// program/instakill.rs:130 (UnsupportedImplicitTargets). The other two owner-clean carriers are
/// 1252143 (children Instakill (1,0) and Script Effect 77) and 1279208 (indefinite stacking owner).
#[test]
fn holds_real_raw495_carrier_child_is_refused() {
    let words = [384, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    let data = GameData::try_from_input(GameDataInput {
        identity: identity(),
        power_types: Vec::new(),
        spells: vec![spell(433_258, 1, 0, 0, words), spell(433_259, 1, 0, 0, words)],
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: vec![SpellDurationPresenceInput { spell_id: 433_258 }],
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects: vec![
            effect(433_258, 6, 495, 0, 0.0, 1, Some(433_259)),
            effect(433_259, 1, 0, 0, 0.0, 1, None),
        ],
        effect_attributes: Vec::new(),
    })
    .expect("real rows are valid game data");
    let mut input = empty_resolved_action_catalog_input(identity());
    input.auras = vec![ResolvedAuraInput::finite(433_258, 20_000, 1)];
    input.expiry_triggers.push(wowlab_data::ResolvedExpiryTriggerInput {
        aura_id: 433_258,
        effect_index: 1,
        child_node_id: 433_259,
        source: wowlab_data::ResolvedExpirySourceInput::AuraRecipient,
        target: wowlab_data::ResolvedExpiryTargetInput::AuraRecipient,
        condition: wowlab_data::ResolvedExpiryConditionInput::NaturalExpiration,
        snapshot: wowlab_data::ResolvedExpirySnapshotInput::FreshAtExpiration,
    });
    input.programs.push(wowlab_data::ResolvedSpellProgramInput {
        node_id: 433_259,
        spell_id: 433_259,
        activation_steps: Vec::new(),
        impacts: vec![wowlab_data::ResolvedImpactInput {
            recipient: ResolvedActionRecipientInput::Target,
            steps: vec![ResolvedProgramStepInput::Instakill { effect_index: 1 }],
        }],
    });
    let (action, program) = resolved_action_program(
        433_258,
        ResolvedRootTargetInput::Caster,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        vec![ResolvedProgramStepInput::ApplyAura {
            recipient: ResolvedActionRecipientInput::Caster,
            effect_index: 1,
            aura_id: 433_258,
            stacks: 1,
            reapplication: ResolvedAuraReapplicationInput::RestartLifetime,
        }],
    );
    input.actions.push(action);
    input.programs.push(program);
    let actions = ResolvedActionCatalog::try_from_input(input).expect("actions");
    let error = CombatProgram::builder(&data, &actions)
        .build()
        .expect_err("433259 Instakill (1,0) is refused");
    assert!(format!("{error:?}").contains("UnsupportedImplicitTargets"), "{error:?}");
}
