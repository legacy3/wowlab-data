//! R3 re-derivation of Track J CSA-J-05 (NUM-R3-001, REJ-R3-002): raw-286 cooldown recovery-rate millisecond conversion.
//! Core divides the base by the speed `max(1 + points/100, 0.01)` in f64 and truncates
//! (cooldown/deadline_rate.rs:233-240, cooldown_recovery_rate.rs:850-852). The Trinity consumer
//! multiplies by the double rate `100.0 / (max(amount, -99.0) + 100.0)` and truncates
//! (SpellHistory.cpp:432-455 `cooldown = Duration(int64(cooldown.count() * recoveryRate))`).
//!
//! Trinity composes every raw-286 rate into ONE `double recoveryRate` first
//! (`recoveryRate *= calcRecoveryRate(...)` per aura effect, SpellHistory.cpp:443-450) and only then
//! multiplies the cooldown (`int64(cooldown.count() * recoveryRate)`, :452-455). Track J's replica
//! `60000 * (100/150) * (100/160)` multiplies the base first, which is a different association.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatProgram, CombatStateInput, HasteMultipliers, OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, EffectAmountFactsInput, EffectChainFactsInput,
    EffectInput, GameData, GameDataIdentity, GameDataInput, ResolvedActionCatalog,
    ResolvedActionInput, ResolvedActionRecipientInput, ResolvedActionTimingInput,
    ResolvedAuraInput, ResolvedAuraReapplicationInput, ResolvedCooldownInput,
    ResolvedCooldownRecoveryRateInput, ResolvedCooldownSlotInput, ResolvedImpactInput,
    ResolvedProgramStepInput, ResolvedRecoveryInput, ResolvedRootTargetInput,
    ResolvedSpellProgramInput, SpellInput,
};
use wowlab_dbc::{AuraSubtypeKind, SpellEffectKind};
use wowlab_model::{ActorId, CooldownSlotId, DifficultyId, RecoveryRate, RecoveryRatePolicy, SimTime, SpellId};
use wowlab_sim::RandomStreamIdentity;

const RATE_AURA: u32 = 286_001;
const COOLDOWN_ACTION: u32 = 286_002;
const ACTION_SLOT: u32 = 286_002;

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
        school_mask: 1,
        defense_type: 0,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: None,
        attributes: [0; 17],
    }
}

fn rate_effect(index: u8, percent_points: f64) -> EffectInput {
    EffectInput {
        spell_id: RATE_AURA,
        index,
        kind: u32::from(SpellEffectKind::ApplyAura.raw()),
        aura_subtype: i32::from(AuraSubtypeKind::ModCooldownRechargeRatePercent.raw()),
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: 0,
        misc_value_1: 0,
        radius_index_0: 0,
        radius_index_1: 0,
        implicit_target_a: 1,
        implicit_target_b: 0,
        spell_class_mask: [0; 4],
        base_points: percent_points,
        attack_power_coefficient: 0.0,
        spell_power_coefficient: 0.0,
        trigger_spell_id: None,
        amount_facts: EffectAmountFactsInput::NEUTRAL,
        chain_facts: EffectChainFactsInput::NEUTRAL,
    }
}

fn plain_action(spell_id: u32, cooldowns: Vec<ResolvedCooldownInput>) -> ResolvedActionInput {
    ResolvedActionInput {
        spell_id,
        root_program_id: spell_id,
        target: ResolvedRootTargetInput::Caster,
        resource_spends: Vec::new(),
        cast_policy: wowlab_data::ResolvedCastPolicyInput::default(),
        timing: ResolvedActionTimingInput {
            ordinary_category_id: None,
            cooldowns,
            ..ResolvedActionTimingInput::default()
        },
    }
}

fn program(percent_points: &[f64], cooldown_ms: u32) -> Result<CombatProgram> {
    let data = GameData::try_from_input(GameDataInput {
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        identity: identity(),
        power_types: Vec::new(),
        spells: vec![spell_input(RATE_AURA), spell_input(COOLDOWN_ACTION)],
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects: percent_points
            .iter()
            .enumerate()
            .map(|(offset, &points)| rate_effect(u8::try_from(offset + 1).expect("index"), points))
            .collect(),
        effect_attributes: Vec::new(),
    })?;
    let mut input = wowlab_test_support::empty_resolved_action_catalog_input(identity());

    input
        .auras
        .push(ResolvedAuraInput::finite(RATE_AURA, 600_000, 1));
    for offset in 0..percent_points.len() {
        input
            .cooldown_recovery_rates
            .push(ResolvedCooldownRecoveryRateInput {
                aura_id: RATE_AURA,
                effect_index: u8::try_from(offset + 1).expect("index"),
                application_effect_index: 1,
            });
    }
    input.cooldown_slots.push(ResolvedCooldownSlotInput {
        slot_id: ACTION_SLOT,
    });
    input.actions.push(plain_action(RATE_AURA, Vec::new()));
    input.actions.push(plain_action(
        COOLDOWN_ACTION,
        vec![ResolvedCooldownInput::Private {
            slot_id: ACTION_SLOT,
            start: Some(ResolvedRecoveryInput {
                base_duration_ms: cooldown_ms,
                rate: RecoveryRate::Fixed,
                rate_policy: RecoveryRatePolicy::Snapshot,
            }),
        }],
    ));
    input.programs.push(ResolvedSpellProgramInput {
        node_id: RATE_AURA,
        spell_id: RATE_AURA,
        activation_steps: Vec::new(),
        impacts: vec![ResolvedImpactInput {
            recipient: ResolvedActionRecipientInput::Caster,
            steps: vec![ResolvedProgramStepInput::ApplyAura {
                recipient: ResolvedActionRecipientInput::Caster,
                effect_index: 1,
                aura_id: RATE_AURA,
                stacks: 1,
                reapplication: ResolvedAuraReapplicationInput::RestartLifetime,
            }],
        }],
    });
    input.programs.push(ResolvedSpellProgramInput {
        node_id: COOLDOWN_ACTION,
        spell_id: COOLDOWN_ACTION,
        activation_steps: Vec::new(),
        impacts: Vec::new(),
    });
    let actions = ResolvedActionCatalog::try_from_input(input)?;

    Ok(CombatProgram::builder(&data, &actions).build()?)
}

fn ready_after_rate(percent_points: &[f64], cooldown_ms: u32) -> Result<Option<SimTime>> {
    let program = program(percent_points, cooldown_ms)?;
    let actor = ActorState::try_new(
        ActorId::Player,
        Vec::new(),
        None,
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )?;
    let mut state = program.try_state(CombatStateInput::new(
        vec![actor],
        RandomStreamIdentity::new(286, 1),
    ))?;
    let mut output = Vec::new();

    for spell in [RATE_AURA, COOLDOWN_ACTION] {
        state.cast(
            CastRequest::new(ActorId::Player, ActorId::Player, SpellId::new(spell).or_fail()?),
            &mut output,
        )?;
    }

    Ok(state.cooldown_ready_at(ActorId::Player, CooldownSlotId::new(ACTION_SLOT).or_fail()?))
}


/// Trinity's raw-286 consumer: the rate product is formed before touching the cooldown.
fn trinity_cooldown(base_ms: u32, percent_points: &[f64]) -> i64 {
    let mut recovery_rate = 1.0_f64;

    for &points in percent_points {
        recovery_rate *= 100.0 / (points.max(-99.0) + 100.0);
    }

    (f64::from(base_ms) * recovery_rate) as i64
}

/// REJ-R3-002 / NUM-R3-001: CSA-J-05's source witness (60 s, +50 and +60) is not a divergence.
/// With the consumer's real association Trinity also yields 24999, the same as Core; J's replica
/// (base-first association) yields 25000.
#[gtest]
fn holds_recovery_rate_sixty_second_witness_matches_consumer_association() -> Result<()> {
    verify_eq!(trinity_cooldown(60_000, &[50.0, 60.0]), 24_999)?;
    verify_eq!(trinity_cooldown(60_000, &[60.0, 50.0]), 24_999)?;
    verify_eq!((60_000_f64 * (100.0 / 150.0) * (100.0 / 160.0)) as i64, 25_000)?;
    verify_eq!(ready_after_rate(&[50.0, 60.0], 60_000)?, Some(SimTime::from_millis(24_999)))?;
    // 15 s and 30 s, also cited by J as diverging, agree as well.
    verify_eq!(trinity_cooldown(15_000, &[50.0, 60.0]), 6_249)?;
    verify_eq!(ready_after_rate(&[50.0, 60.0], 15_000)?, Some(SimTime::from_millis(6_249)))?;
    verify_eq!(trinity_cooldown(30_000, &[50.0, 60.0]), 12_499)?;
    verify_eq!(ready_after_rate(&[50.0, 60.0], 30_000)?, Some(SimTime::from_millis(12_499)))
}

/// NUM-R3-001: the division-vs-product mechanism is real for the (+50, +60) pair at 1.5 s
/// (Core 624, consumer 625). The pair is Ancient Ankh Talisman 287777 (Reincarnation) and
/// Trueshot 288613 (Hunter) in 12.1.0, which never modify one action together, so the
/// divergence is not source-valid (see REJ-R3-002).
#[gtest]
fn defect_recovery_rate_mechanism_diverges_only_on_non_coapplicable_pair() -> Result<()> {
    verify_eq!(trinity_cooldown(1_500, &[50.0, 60.0]), 625)?;
    verify_eq!(ready_after_rate(&[50.0, 60.0], 1_500)?, Some(SimTime::from_millis(624)))
}
