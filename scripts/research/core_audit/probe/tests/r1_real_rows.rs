//! Reviewer R1 (false positives): real 12.1.0.69497 source-row witnesses for LIVE claims.
//!
//! Every spell here copies the exact DBC facts (SpellMisc attribute words, school, DefenseType,
//! SpellEffect kind/aura/base points/implicit targets/misc, effect count, duration presence).
//! Host-supplied values (health pools, critical baselines, relations) are free.
//!
//! - CSA-D-01: Light of Ata'luur 156021 (raw-133 +10, (1,0), single effect) + Sacrificial Aegis
//!   1257873 (raw-165 10 %, (1,0), Shadow, DefenseType Magic): a 1001 maximum becomes 1101.1 and
//!   the real raw-165 cast fails (`defect_real_raw133_raw165_rows_fail_the_cast`).
//! - CSA-C-01: the exact Stun 346197 crits from the enemy weapon channel with spell chance 0
//!   (`defect_exact_stun_crits_from_enemy_weapon_channel_with_zero_spell_chance`), so C-01 does not
//!   depend on an enemy spell critical chance (Trinity `Unit.cpp:7124-7125` gives creatures 0);
//!   a Caster-recipient self buff never crits (`holds_real_player_self_buff_impact_never_resolves_critical`).

use wowlab_combat::{
    ActorState, CastErrorCode, CastRequest, CombatObservation, CombatProgram, CombatState,
    CombatStateInput, CriticalStrikeBaseline, CriticalStrikeChances, HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, CurrentHealthPolicyInput, CurrentHealthTransitionInput, DataVersion,
    EffectAmountFactsInput, EffectChainFactsInput, EffectInput, GameData, GameDataIdentity,
    GameDataInput, MaxHealthCompositionInput, MaximumHealthActivationInput,
    MaximumHealthModifierInput, ResolvedActionCatalog, ResolvedActionRecipientInput,
    ResolvedActionTimingInput, ResolvedAuraInput, ResolvedAuraReapplicationInput,
    ResolvedImpactInput, ResolvedProgramStepInput, ResolvedSpellProgramInput, ResolvedRootTargetInput, SpellDurationPresenceInput, SpellFamilyId,
    SpellInput,
};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, HitResult, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{empty_resolved_action_catalog_input, resolved_action_program, stun_inputs};

const LIGHT_OF_ATALUUR: u32 = 156_021;
const SACRIFICIAL_AEGIS: u32 = 1_257_873;

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

/// Exact SpellMisc/SpellCategories facts: all attribute words 0 for both spells.
fn spell(id: u32, school: u32, defense: u8) -> SpellInput {
    SpellInput {
        id,
        family_id: SpellFamilyId::NONE,
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: school,
        defense_type: defense,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: Some(1),
        attributes: [0; 17],
    }
}

/// Exact SpellEffect row shape: index 0 (one-based 1), target (1,0), no coefficients/variance.
fn effect(spell_id: u32, kind: u32, aura: i32, points: f64) -> EffectInput {
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
        implicit_target_a: 1,
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

fn catalogs() -> (GameData, ResolvedActionCatalog) {
    let data = GameData::try_from_input(GameDataInput {
        identity: identity(),
        power_types: Vec::new(),
        // SpellMisc 156021: SchoolMask 1, DurationIndex 30 (1,800,000 ms); 1257873: SchoolMask 32,
        // SpellCategories DefenseType 1.
        spells: vec![spell(LIGHT_OF_ATALUUR, 1, 0), spell(SACRIFICIAL_AEGIS, 32, 1)],
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: vec![SpellDurationPresenceInput {
            spell_id: LIGHT_OF_ATALUUR,
        }],
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        // SpellEffect 216916 (156021:0 raw 6 / aura 133 / 10) and 1266009 (1257873:0 raw 165 / 10).
        effects: vec![
            effect(LIGHT_OF_ATALUUR, 6, 133, 10.0),
            effect(SACRIFICIAL_AEGIS, 165, 0, 10.0),
        ],
        effect_attributes: Vec::new(),
    })
    .expect("real rows are valid game data");

    let mut catalog = empty_resolved_action_catalog_input(identity());
    catalog.auras = vec![ResolvedAuraInput::finite(LIGHT_OF_ATALUUR, 1_800_000, 1)];
    catalog.maximum_health_modifiers = vec![MaximumHealthModifierInput {
        spell_id: LIGHT_OF_ATALUUR,
        effect_index: 1,
        activation: MaximumHealthActivationInput::Aura {
            application_effect_index: 1,
            composition: MaxHealthCompositionInput::Multiplicative,
            current_health_transition: CurrentHealthTransitionInput {
                on_increase: CurrentHealthPolicyInput::PreserveFraction,
                on_decrease: CurrentHealthPolicyInput::PreserveFraction,
            },
        },
    }];
    let (aura_action, aura_program) = resolved_action_program(
        LIGHT_OF_ATALUUR,
        ResolvedRootTargetInput::Caster,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        vec![ResolvedProgramStepInput::ApplyAura {
            recipient: ResolvedActionRecipientInput::Caster,
            effect_index: 1,
            aura_id: LIGHT_OF_ATALUUR,
            stacks: 1,
            reapplication: ResolvedAuraReapplicationInput::RestartLifetime,
        }],
    );
    let (mut damage_action, _) = resolved_action_program(
        SACRIFICIAL_AEGIS,
        ResolvedRootTargetInput::Caster,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        Vec::new(),
    );
    damage_action.root_program_id = SACRIFICIAL_AEGIS;
    let damage_program = ResolvedSpellProgramInput {
        node_id: SACRIFICIAL_AEGIS,
        spell_id: SACRIFICIAL_AEGIS,
        activation_steps: Vec::new(),
        impacts: vec![ResolvedImpactInput {
            recipient: ResolvedActionRecipientInput::Caster,
            steps: vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
        }],
    };
    catalog.actions.extend([aura_action, damage_action]);
    catalog.programs.extend([aura_program, damage_program]);
    let actions = ResolvedActionCatalog::try_from_input(catalog).expect("real-row actions");
    (data, actions)
}

fn actor(id: ActorId, maximum: f64) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::try_new(maximum, maximum).expect("valid pool")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

fn state(player_maximum: f64, critical: Option<f64>) -> CombatState {
    let (data, actions) = catalogs();
    let program = CombatProgram::builder(&data, &actions)
        .build()
        .expect("real 156021 + 1257873 rows compile");
    let mut input = CombatStateInput::new(
        vec![
            actor(ActorId::Player, player_maximum),
            actor(ActorId::External, 10_000.0),
        ],
        RandomStreamIdentity::new(1, 1),
    )
    .with_hostile_target(ActorId::Player, ActorId::External);
    if let Some(chance) = critical {
        input = input.with_critical_strike(
            CriticalStrikeBaseline::try_uniform(ActorId::Player, chance, 2.0).expect("baseline"),
        );
    }
    program.try_state(input).expect("state builds")
}

fn request(raw: u32) -> CastRequest {
    CastRequest::new(ActorId::Player, ActorId::Player, SpellId::new(raw).expect("nonzero"))
}

fn player(state: &CombatState) -> HealthPool {
    *state
        .actor(ActorId::Player)
        .and_then(ActorState::health)
        .expect("player health")
}

/// CSA-D-01 real-row witness (R1 upholds LIVE): exact 156021 and 1257873 rows compile; a 1001
/// maximum (any integral host maximum not divisible by 10 works) becomes 1101.1 under +10 %, and
/// the real raw-165 cast then fails with `DamageCalculation` (Trinity: uint32 1101 -> 110 damage).
#[test]
fn defect_real_raw133_raw165_rows_fail_the_cast() {
    let mut state = state(1_001.0, None);
    let mut output = Vec::new();
    state
        .cast(request(LIGHT_OF_ATALUUR), &mut output)
        .expect("real raw-133 applies");
    let maximum = player(&state).maximum();
    assert!((maximum - 1_101.1).abs() < 1e-9, "fractional maximum {maximum}");
    let mut rejected = Vec::new();
    let error = state
        .cast(request(SACRIFICIAL_AEGIS), &mut rejected)
        .expect_err("real raw-165 rejects the fractional maximum");
    assert_eq!(error.code(), CastErrorCode::DamageCalculation);
    assert!(rejected.is_empty());
}

/// Control for the D-01 witness: 1000 -> 1100 is integral and the same real raw-165 casts (110).
#[test]
fn holds_real_raw133_raw165_rows_cast_on_integral_maximum() {
    let mut state = state(1_000.0, None);
    let mut output = Vec::new();
    state.cast(request(LIGHT_OF_ATALUUR), &mut output).expect("applies");
    state.cast(request(SACRIFICIAL_AEGIS), &mut output).expect("casts");
    assert_eq!(player(&state).maximum(), 1_100.0);
    assert_eq!(player(&state).current(), 990.0);
}

fn stun_results(chances: CriticalStrikeChances) -> Vec<HitResult> {
    let stun_identity = GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 1_201_069_497,
        },
        difficulty: DifficultyId::BASE,
    };
    let (fixture, data, actions) = stun_inputs(stun_identity);
    let program = CombatProgram::builder(&data, &actions).build().expect("exact stun compiles");
    let enemy = ActorId::Enemy {
        index: EnemyIndex::new(0),
    };
    let mut state = program
        .try_state(
            CombatStateInput::new(
                vec![actor(ActorId::Player, 1_000.0), actor(enemy, 1_000.0)],
                RandomStreamIdentity::new(12, 0),
            )
            .with_hostile_target(enemy, ActorId::Player)
            .with_critical_strike(
                CriticalStrikeBaseline::try_new(enemy, chances, 2.0).expect("baseline"),
            ),
        )
        .expect("stun state");
    let mut output = Vec::new();
    state
        .cast(
            CastRequest::new(enemy, ActorId::Player, fixture.stun_spell()),
            &mut output,
        )
        .expect("stun commits");
    output
        .iter()
        .filter_map(|observation| match observation {
            CombatObservation::ImpactResolved { result, .. } => Some(*result),
            _ => None,
        })
        .collect()
}

/// R1 check on CSA-C-01's witness: a Player-cast Caster-recipient aura-only buff (real 156021)
/// is NOT critical-eligible (planning.rs:806-944 prepares criticals only for Target recipients,
/// healing and damage impacts), so C-01's scope is Target-recipient non-amount impacts only.
#[test]
fn holds_real_player_self_buff_impact_never_resolves_critical() {
    assert_eq!(self_buff_results(None), vec![HitResult::Hit]);
    assert_eq!(self_buff_results(Some(1.0)), vec![HitResult::Hit]);
}

/// CSA-C-01 under a Trinity-faithful host (R1 upholds LIVE): creatures have spell critical
/// chance 0 in Trinity (Unit.cpp:7124-7125) but may melee-crit, so the host gives the enemy
/// spell 0.0 / weapon 1.0. The exact Stun 346197 (DefenseType None) still resolves `Critical`,
/// because a None-defense payload selects the weapon channel through the hybrid maximum.
#[test]
fn defect_exact_stun_crits_from_enemy_weapon_channel_with_zero_spell_chance() {
    assert_eq!(
        stun_results(CriticalStrikeChances {
            spell: 0.0,
            weapon: 0.0
        }),
        vec![HitResult::Hit]
    );
    assert_eq!(
        stun_results(CriticalStrikeChances {
            spell: 0.0,
            weapon: 1.0
        }),
        vec![HitResult::Critical]
    );
}

fn self_buff_results(critical: Option<f64>) -> Vec<HitResult> {
    let mut state = state(1_000.0, critical);
    let mut output = Vec::new();
    state
        .cast(request(LIGHT_OF_ATALUUR), &mut output)
        .expect("real raw-133 applies");
    output
        .iter()
        .filter_map(|observation| match observation {
            CombatObservation::ImpactResolved { result, .. } => Some(*result),
            _ => None,
        })
        .collect()
}

