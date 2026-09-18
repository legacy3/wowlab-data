//! Reviewer R1 (false positives): real 12.1.0.69497 source-row witness for CSA-G-02 (the track
//! probe adds a Fireball-shaped GCD to synthetic effectless hard casts 900_001/900_002).
//!
//! Exact rows:
//! - Spell Lock 159006 (Core's interrupt fixture: raw 68, (6,0), Magic, mechanic 26; 6 s lockout
//!   from DurationIndex 32).
//! - Blood Feast 465275:0 raw-9 Health Leech Physical 24, amplitude 1, (6,0); CastingTimeIndex 5
//!   (2000 ms), attributes 0; SpellCategories DefenseType 2, PreventionType 7 (includes Silence),
//!   StartRecoveryCategory 133; SpellCooldowns StartRecoveryTime 1500, RecoveryTime 8000; no
//!   SpellInterrupts row. Program/interrupt.rs:65-73 refuses every hard cast that has a
//!   SpellInterrupts row when a raw-68 program exists, so the admitted real population is the
//!   hard casts without one; Trinity SpellInfo::CanBeInterrupted accepts it through
//!   PreventionType & SILENCE.
//! - Shadow readiness probe 58912:0 raw-2 Shadow 499.6, (25,0); cast 1000; attributes 0;
//!   StartRecoveryCategory 133, StartRecoveryTime 1000; no SpellInterrupts row.
//! Rejected on the way (evidence): Smite 295678 (InterruptFlags 15) -> UnsupportedTargetCastPolicy.
//! Host: cast/start-recovery rates Fixed (no haste), relations, pools. The 8000 ms recovery of
//! Blood Feast is omitted because it never starts (the cast is interrupted).

use wowlab_combat::{
    ActorState, CastErrorCode, CastRequest, CombatObservation, CombatProgram, CombatState,
    CombatStateInput, HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, EffectAmountFactsInput, EffectChainFactsInput,
    EffectInput, GameData, GameDataIdentity, ResolvedActionCatalog, ResolvedActionTimingInput,
    ResolvedCastPolicyInput, ResolvedCastTimeInput, ResolvedProgramStepInput,
    ResolvedRootTargetInput, ResolvedStartRecoveryInput, SpellFamilyId, SpellInput,
};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, RecoveryRate, SimTime, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{interrupt_action_input, interrupt_game_data_input, resolved_action_program};

const SPELL_LOCK: u32 = 159_006;
const BLOOD_FEAST: u32 = 465_275;
const SHADOW_BOLT: u32 = 58_912;

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

fn spell(id: u32, school: u32, defense: u8, attributes: [i32; 17]) -> SpellInput {
    SpellInput {
        id,
        family_id: SpellFamilyId::NONE,
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: school,
        defense_type: defense,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: Some(1),
        attributes,
    }
}

fn damage(spell_id: u32, kind: u32, points: f64, target: i32, amplitude: f32) -> EffectInput {
    EffectInput {
        spell_id,
        index: 1,
        kind,
        aura_subtype: 0,
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
        amount_facts: EffectAmountFactsInput {
            amplitude,
            ..EffectAmountFactsInput::NEUTRAL
        },
        trigger_spell_id: None,
    }
}

fn timing(cast: u32, gcd: u32) -> ResolvedActionTimingInput {
    ResolvedActionTimingInput {
        cast: Some(ResolvedCastTimeInput {
            base_duration_ms: cast,
            rate: RecoveryRate::Fixed,
            minimum_duration_ms: 0,
        }),
        start_recovery: Some(ResolvedStartRecoveryInput {
            category_id: 133,
            base_duration_ms: gcd,
            rate: RecoveryRate::Fixed,
            minimum_duration_ms: 0,
        }),
        ..ResolvedActionTimingInput::default()
    }
}

fn state() -> CombatState {
    let mut data = interrupt_game_data_input(identity());
    data.spells.push(spell(BLOOD_FEAST, 1, 2, [0; 17]));
    data.spells.push(spell(SHADOW_BOLT, 32, 1, [0; 17]));
    data.effects.push(damage(BLOOD_FEAST, 9, 24.0, 6, 1.0));
    data.effects.push(damage(SHADOW_BOLT, 2, 499.600_128_173_83, 25, 0.0));
    let data = GameData::try_from_input(data).expect("real rows are valid game data");

    let mut input = interrupt_action_input(identity());
    for (raw, prevention, cast, gcd, step) in [
        (BLOOD_FEAST, 7, 2_000, 1_500, ResolvedProgramStepInput::DirectDamage { effect_index: 1 }),
        (SHADOW_BOLT, 0, 1_000, 1_000, ResolvedProgramStepInput::DirectDamage { effect_index: 1 }),
    ] {
        let (mut action, program) = resolved_action_program(
            raw,
            ResolvedRootTargetInput::PrimaryTarget,
            Vec::new(),
            timing(cast, gcd),
            vec![step],
        );
        action.cast_policy = ResolvedCastPolicyInput {
            prevention_mask: prevention,
            spell_interrupts: None,
        };
        input.actions.push(action);
        input.programs.push(program);
    }
    let actions = ResolvedActionCatalog::try_from_input(input).expect("real-row actions");
    CombatProgram::builder(&data, &actions)
        .build()
        .expect("real Blood Feast / 58912 / Spell Lock rows compile")
        .try_state(
            CombatStateInput::new(
                vec![actor(ActorId::Player), actor(enemy())],
                RandomStreamIdentity::new(68, 3),
            )
            .with_hostile_target(ActorId::Player, enemy())
            .with_hostile_target(enemy(), ActorId::Player),
        )
        .expect("state")
}

fn actor(id: ActorId) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(1_000.0).expect("health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("actor")
}

fn request(source: ActorId, target: ActorId, raw: u32) -> CastRequest {
    CastRequest::new(source, target, SpellId::new(raw).expect("nonzero"))
}

/// CSA-G-02 real-row witness (R1 upholds LIVE): the Player hard-casts real Blood Feast (GCD 1500
/// at acceptance), the enemy interrupts it with real Spell Lock at 500 ms (Physical locked), and
/// the Player's real Shadow 58912 (not school-locked) is still refused with StartRecovery until
/// 1500 ms. Trinity Spell::cancel in PREPARING calls CancelGlobalCooldown (Spell.cpp:3617-3632,
/// 9227-9240), so the Player could act at 500 ms. The Player is the caster on purpose:
/// CanHaveGlobalCooldown gives uncontrolled creatures no GCD at all in Trinity.
#[test]
fn defect_real_interrupted_hard_cast_keeps_its_gcd() {
    let mut state = state();
    let mut output = Vec::new();
    state
        .begin_cast(request(ActorId::Player, enemy(), BLOOD_FEAST), &mut output)
        .expect("Blood Feast begins");
    state.advance_to(SimTime::from_millis(500)).expect("advance");
    output.clear();
    state
        .cast(request(enemy(), ActorId::Player, SPELL_LOCK), &mut output)
        .expect("Spell Lock commits");
    assert!(
        output
            .iter()
            .any(|observation| matches!(observation, CombatObservation::CastInterrupted { .. }))
    );
    assert!(state.active_cast(ActorId::Player).is_none());
    let readiness = state.cast_readiness(request(ActorId::Player, enemy(), SHADOW_BOLT));
    assert_eq!(readiness.error_code(), Some(CastErrorCode::StartRecovery));
    assert_eq!(readiness.recheck_at(), Some(SimTime::from_millis(1_500)));
}

/// R1 evidence for CSA-G-02 reachability: a raw-68 program makes every hard cast that carries a
/// SpellInterrupts row fail compilation (program/interrupt.rs:65-73), e.g. real Smite 295678
/// (InterruptFlags 15). The admitted real interruptible population is therefore hard casts with
/// no SpellInterrupts row (such as Blood Feast 465275).
#[test]
fn holds_real_hard_cast_with_spell_interrupts_row_is_refused_beside_raw68() {
    let mut data = interrupt_game_data_input(identity());
    data.spells.push(spell(295_678, 2, 1, [0; 17]));
    data.effects.push(damage(295_678, 2, 10.0, 6, 0.0));
    let data = GameData::try_from_input(data).expect("valid game data");
    let mut input = interrupt_action_input(identity());
    let (mut action, program) = resolved_action_program(
        295_678,
        ResolvedRootTargetInput::PrimaryTarget,
        Vec::new(),
        timing(1_500, 1_500),
        vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
    );
    action.cast_policy = ResolvedCastPolicyInput {
        prevention_mask: 1,
        spell_interrupts: Some(wowlab_data::ResolvedSpellInterruptsInput {
            interrupt_flags: 15,
            channel_interrupt_flags: [0, 0],
        }),
    };
    input.actions.push(action);
    input.programs.push(program);
    let actions = ResolvedActionCatalog::try_from_input(input).expect("actions");
    let error = CombatProgram::builder(&data, &actions)
        .build()
        .expect_err("Smite beside raw 68 is refused");
    assert!(format!("{error:?}").contains("UnsupportedTargetCastPolicy"), "{error:?}");
}
