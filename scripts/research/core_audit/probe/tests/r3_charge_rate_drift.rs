//! Reviewer R3 (lifecycle / numeric): CSA-R3-02, NUM-R3-003, MUT-R3-002.
//!
//! A raw-148 charge-recharge-rate aura rebases the active serial charge recovery by scaling BOTH
//! the active remainder and the single retained `full_interval` by `old_speed / new_speed`,
//! truncating each time (cooldown/charge_rate.rs:95-158). The truncated full interval is never
//! re-derived from the pool's base recharge, and every charge consumed while the chain is still
//! recovering reuses it (cooldown.rs:1600-1625 only creates a fresh recovery when none is active).
//!
//! The consumer keeps one entry per consumed charge; a rate change rescales only the queued entries
//! (SpellHistory.cpp:873-899) and every later consumption appends a fresh
//! `GetChargeRecoveryTime` (SpellHistory.cpp:839-846, 975-1009).
//!
//! Source shape: Trueshot 288613 raw-148 +40 on charge category 1715 (Aimed Shot, 2 charges,
//! 15000 ms), 15 s aura. The probe uses synthetic ids with exactly those facts.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastRequest, CombatProgram, CombatState, CombatStateInput, HasteMultipliers,
    OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, EffectAmountFactsInput, EffectChainFactsInput,
    EffectInput, GameData, GameDataIdentity, GameDataInput, ResolvedActionCatalog,
    ResolvedActionInput, ResolvedActionRecipientInput, ResolvedActionTimingInput,
    ResolvedAuraInput, ResolvedAuraReapplicationInput, ResolvedChargePoolInput,
    ResolvedChargeRateInput, ResolvedImpactInput, ResolvedProgramStepInput,
    ResolvedRootTargetInput, ResolvedSpellProgramInput, SpellInput,
};
use wowlab_dbc::{AuraSubtypeKind, SpellEffectKind};
use wowlab_model::{
    ActorId, CooldownCategoryId, DifficultyId, RecoveryRate, RecoveryRatePolicy, SimTime, SpellId,
};
use wowlab_sim::RandomStreamIdentity;

const RATE_AURA: u32 = 148_701;
const CONSUME: u32 = 148_702;
const CATEGORY: u32 = 148_703;
const BASE_RECHARGE_MS: u32 = 15_000;

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

fn rate_effect(percent_points: f64) -> EffectInput {
    EffectInput {
        spell_id: RATE_AURA,
        index: 1,
        kind: u32::from(SpellEffectKind::ApplyAura.raw()),
        aura_subtype: i32::from(AuraSubtypeKind::ModChargeCooldownRechargeRateCategory.raw()),
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: i32::try_from(CATEGORY).expect("category fits i32"),
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

fn action(spell_id: u32, category: Option<u32>) -> ResolvedActionInput {
    ResolvedActionInput {
        spell_id,
        root_program_id: spell_id,
        target: ResolvedRootTargetInput::Caster,
        resource_spends: Vec::new(),
        cast_policy: wowlab_data::ResolvedCastPolicyInput::default(),
        timing: ResolvedActionTimingInput {
            charge_category_id: category,
            ..ResolvedActionTimingInput::default()
        },
    }
}

fn program(percent_points: f64) -> Result<CombatProgram> {
    program_with(percent_points, false)
}

fn program_with(percent_points: f64, independent_stacks: bool) -> Result<CombatProgram> {
    let mut rate_spell = spell_input(RATE_AURA);

    if independent_stacks {
        let raw = usize::from(wowlab_dbc::SpellAttributeKind::AsynchronousStackingBuff.raw());
        rate_spell.attributes[raw / 32] |= i32::from_ne_bytes((1_u32 << (raw % 32)).to_ne_bytes());
    }

    let data = GameData::try_from_input(GameDataInput {
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        identity: identity(),
        power_types: Vec::new(),
        spells: vec![rate_spell, spell_input(CONSUME)],
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects: vec![rate_effect(percent_points)],
        effect_attributes: Vec::new(),
    })?;
    let mut input = wowlab_test_support::empty_resolved_action_catalog_input(identity());

    input.auras.push(ResolvedAuraInput::finite(
        RATE_AURA,
        15_000,
        if independent_stacks { 3 } else { 1 },
    ));
    input.charge_recharge_rates.push(ResolvedChargeRateInput {
        aura_id: RATE_AURA,
        effect_index: 1,
        application_effect_index: 1,
    });
    input.charge_pools.push(ResolvedChargePoolInput {
        category_id: CATEGORY,
        type_mask: 0,
        max_charges: 2,
        base_recharge_ms: BASE_RECHARGE_MS,
        rate: RecoveryRate::Fixed,
        rate_policy: RecoveryRatePolicy::Snapshot,
    });
    input.actions.push(action(RATE_AURA, None));
    input.actions.push(action(CONSUME, Some(CATEGORY)));
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
        node_id: CONSUME,
        spell_id: CONSUME,
        activation_steps: Vec::new(),
        impacts: Vec::new(),
    });
    let actions = ResolvedActionCatalog::try_from_input(input)?;

    Ok(CombatProgram::builder(&data, &actions).build()?)
}

fn state(program: &CombatProgram) -> Result<CombatState> {
    let actor = ActorState::try_new(
        ActorId::Player,
        Vec::new(),
        None,
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )?;

    Ok(program.try_state(CombatStateInput::new(
        vec![actor],
        RandomStreamIdentity::new(148, 1),
    ))?)
}

fn cast(state: &mut CombatState, spell: u32) -> Result<()> {
    state.cast(
        CastRequest::new(ActorId::Player, ActorId::Player, SpellId::new(spell).or_fail()?),
        &mut Vec::new(),
    )?;

    Ok(())
}

fn category() -> Result<CooldownCategoryId> {
    CooldownCategoryId::new(CATEGORY).or_fail()
}

/// Consumer model: one queued entry per consumed charge; a rate change rescales queued entries
/// (truncating, as `duration_cast<Milliseconds>` does) and a later consumption appends a fresh
/// base recharge after the last queued end.
fn consumer_third_charge_ready_at() -> u32 {
    let ratio_apply = 1.0_f64 / 1.4;
    let ratio_remove = 1.4_f64;
    // t=0: two consumptions queue [0,15000] and [15000,30000]; aura applied at t=0.
    let first_end = (15_000.0 * ratio_apply) as u32; // 10714
    let second_len = (15_000.0 * ratio_apply) as u32; // 10714
    let second_end = first_end + second_len; // 21428
    // t=15000: aura expires; the first entry is gone, the second is rescaled from now.
    let second_end = 15_000 + ((f64::from(second_end - 15_000)) * ratio_remove) as u32; // 23999
    // t=15000: a new consumption appends a fresh 15000 ms recharge.
    second_end + BASE_RECHARGE_MS
}

/// CSA-R3-02 / NUM-R3-003: after a +40% charge-rate aura comes and goes during an active serial
/// recovery, the retained full interval is 14999 ms, and a charge consumed after the aura ended
/// inherits it (ready at 38998) where the consumer appends a fresh 15000 ms recharge (38999).
#[gtest]
fn defect_charge_rate_rebase_leaves_truncated_full_interval_for_later_consumptions() -> Result<()> {
    let program = program(40.0)?;
    let mut state = state(&program)?;

    cast(&mut state, CONSUME)?;
    cast(&mut state, CONSUME)?;
    cast(&mut state, RATE_AURA)?;

    let during = state.charge_snapshot(ActorId::Player, category()?).or_fail()?;

    verify_eq!(during.next_ready_at(), Some(SimTime::from_millis(10_714)))?;
    verify_eq!(during.full_recovery_interval(), Some(SimTime::from_millis(10_714)))?;

    state.advance_to(SimTime::from_millis(15_000)).or_fail()?;

    let after = state.charge_snapshot(ActorId::Player, category()?).or_fail()?;

    verify_eq!(after.next_ready_at(), Some(SimTime::from_millis(23_999)))?;
    verify_eq!(after.full_recovery_interval(), Some(SimTime::from_millis(14_999)))?;

    // The aura is gone; a charge consumed now should recover in the base 15000 ms.
    cast(&mut state, CONSUME)?;
    state.advance_to(SimTime::from_millis(23_999)).or_fail()?;

    let third = state.charge_snapshot(ActorId::Player, category()?).or_fail()?;

    verify_eq!(consumer_third_charge_ready_at(), 38_999)?;
    verify_eq!(third.next_ready_at(), Some(SimTime::from_millis(38_998)))?;
    verify_eq!(third.full_recovery_interval(), Some(SimTime::from_millis(14_999)))
}

/// MUT-R3-002 (control): with no rate aura the same consumption pattern keeps the base interval,
/// and a fresh recovery (pool refilled first) re-derives 15000 ms, so the drift is confined to
/// continuous recovery chains that saw a non-integral rebase.
#[gtest]
fn holds_charge_interval_without_rate_aura_and_after_refill() -> Result<()> {
    let program = program(40.0)?;
    let mut state = state(&program)?;

    cast(&mut state, CONSUME)?;
    cast(&mut state, CONSUME)?;
    state.advance_to(SimTime::from_millis(15_000)).or_fail()?;
    cast(&mut state, CONSUME)?;
    state.advance_to(SimTime::from_millis(30_000)).or_fail()?;

    let control = state.charge_snapshot(ActorId::Player, category()?).or_fail()?;

    verify_eq!(control.next_ready_at(), Some(SimTime::from_millis(45_000)))?;
    verify_eq!(control.full_recovery_interval(), Some(SimTime::from_millis(15_000)))?;

    // Drift, then let the pool refill completely: the next recovery is fresh.
    let mut drifted = self::state(&program)?;

    cast(&mut drifted, CONSUME)?;
    cast(&mut drifted, RATE_AURA)?;
    drifted.advance_to(SimTime::from_millis(15_000)).or_fail()?;
    drifted.advance_to(SimTime::from_millis(60_000)).or_fail()?;
    cast(&mut drifted, CONSUME)?;

    let refilled = drifted.charge_snapshot(ActorId::Player, category()?).or_fail()?;

    verify_eq!(refilled.full_recovery_interval(), Some(SimTime::from_millis(15_000)))
}

fn state_for(program: &CombatProgram, random: RandomStreamIdentity) -> Result<CombatState> {
    let actor = ActorState::try_new(
        ActorId::Player,
        Vec::new(),
        None,
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )?;

    Ok(program.try_state(CombatStateInput::new(vec![actor], random))?)
}

fn drive_independent(state: &mut CombatState) -> Result<Vec<String>> {
    let mut trace = Vec::new();

    for (at, spell) in [(0, CONSUME), (0, RATE_AURA), (2_000, CONSUME), (5_000, RATE_AURA), (9_000, RATE_AURA)] {
        state.advance_to(SimTime::from_millis(at)).or_fail()?;
        let mut output = Vec::new();
        let _ = state.cast(
            CastRequest::new(ActorId::Player, ActorId::Player, SpellId::new(spell).or_fail()?),
            &mut output,
        );
        trace.push(format!("{at} {spell} {output:?} {:?}", state.charge_snapshot(ActorId::Player, category()?)));
    }
    trace.push(format!("{:?}", state.advance_to(SimTime::from_millis(12_000)).or_fail()?));

    Ok(trace)
}

/// LC-R3-002: the independent-stack authority (raw-490 AsynchronousStackingBuff), which no Track H
/// fingerprint fixture reaches, is left mid-flight with pending cohorts and a rebased charge chain;
/// `reset` equals a fresh state and replays identically, and `reset_for_iteration` equals a fresh
/// state for the new identity.
#[gtest]
fn holds_independent_stack_authority_reset_and_iteration_reset() -> Result<()> {
    let program = program_with(40.0, true)?;
    let first = RandomStreamIdentity::new(148, 1);
    let second = RandomStreamIdentity::new(148, 2);
    let mut state = state_for(&program, first)?;
    let fresh = format!("{state:#?}");
    let trace = drive_independent(&mut state)?;

    verify_that!(format!("{state:?}"), contains_substring("IndependentStackAuthority"))?;
    verify_that!(trace.join("\n"), contains_substring("after_stacks: 3"))?;

    state.reset();
    verify_eq!(format!("{state:#?}"), fresh)?;
    verify_eq!(drive_independent(&mut state)?, trace)?;

    state.reset_for_iteration(second);
    verify_eq!(format!("{state:#?}"), format!("{:#?}", state_for(&program, second)?))
}
