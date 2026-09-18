//! Track X probe: direction of the hasted-duration divergence (CSA-J-02 / NUM-E-001 reconciliation).
//! Harness copied from j_haste_duration.rs (Track J) so Track J files stay untouched.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastBeginOutcome, CastRequest, CombatProgram, CombatStateInput, HasteMultipliers,
    HasteMultipliersInput, HealthPool, OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, GameData, GameDataIdentity, ResolvedActionCatalog,
    ResolvedCastTimeInput, ResolvedCooldownInput, ResolvedRecoveryInput,
};
use wowlab_dbc::SpellAttributeKind;
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, RecoveryRate, RecoveryRatePolicy, SimTime};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    CooldownStartFixture, cooldown_start_action_input, cooldown_start_game_data_input,
};

const BASE_MS: u32 = 30_000;

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

fn actor(id: ActorId, haste: HasteMultipliers) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(1_000.0).expect("health")),
        OffensivePower::ZERO,
        haste,
    )
    .expect("actor")
}

/// Trinity: `Duration(int64(ms * ModSpellHaste))` / `int32(float(castTime) * ModCastingSpeed)`
/// with `ModX = 1.0f * (100.0f / (100.0f + pct))` (Unit.cpp:10978-10981, SpellHistory.cpp:421).
fn trinity_binary32(base_ms: u32, pct: f32) -> u32 {
    let modifier = 1.0_f32 * (100.0_f32 / (100.0_f32 + pct));

    (base_ms as f32 * modifier) as u32
}

fn run(speed: f64) -> Result<(SimTime, Option<SimTime>)> {
    // Ordinary (non raw-407) variant: clear StartCooldownOnCastStart so the carrier's exact
    // three-second shape is not required; the cooldown then starts at cast completion.
    let mut data_input = cooldown_start_game_data_input(identity());
    let raw = usize::from(SpellAttributeKind::StartCooldownOnCastStart.raw());
    data_input.spells[0].attributes[raw / 32] = 0;
    let data = GameData::try_from_input(data_input)?;
    let mut input = cooldown_start_action_input(identity());
    let action = &mut input.actions[0];
    let recovery = ResolvedRecoveryInput {
        base_duration_ms: BASE_MS,
        rate: RecoveryRate::SpellHaste,
        rate_policy: RecoveryRatePolicy::Snapshot,
    };
    let Some(ResolvedCooldownInput::Shared { start, .. }) = action.timing.cooldowns.first_mut()
    else {
        panic!("fixture shape changed: expected a shared cooldown");
    };
    *start = Some(recovery);
    action.timing.cast = Some(ResolvedCastTimeInput {
        base_duration_ms: BASE_MS,
        rate: RecoveryRate::SpellCastSpeed,
        minimum_duration_ms: 1,
    });
    let actions = ResolvedActionCatalog::try_from_input(input)?;
    let program = CombatProgram::builder(&data, &actions).build()?;
    let haste = HasteMultipliers::try_from_input(HasteMultipliersInput {
        spell_haste: speed,
        spell_cast_speed: speed,
        attack_haste: 1.0,
        auto_attack_speed: 1.0,
        haste_regeneration: 1.0,
    })?;
    let mut state = program.try_state(
        CombatStateInput::new(
            vec![actor(ActorId::Player, haste), actor(enemy(), HasteMultipliers::UNHASTED)],
            RandomStreamIdentity::new(407, 0),
        )
        .with_hostile_target(ActorId::Player, enemy()),
    )?;
    let fixture = CooldownStartFixture;
    let mut output = Vec::new();
    let outcome = state.begin_cast(
        CastRequest::new(ActorId::Player, enemy(), fixture.spell()),
        &mut output,
    )?;
    let CastBeginOutcome::Pending(started) = outcome else {
        panic!("expected a hard cast");
    };
    let completes = started.completes_at();

    state.advance_to(completes)?;

    Ok((
        completes,
        state.shared_cooldown_ready_at(ActorId::Player, fixture.category()),
    ))
}

/// XC-X-002 (reconciles CSA-J-02 with NUM-E-001): the f64-division vs binary32-product divergence
/// is bidirectional. At 92% haste, 30000 ms: Core trunc(30000 / 1.92) = 15625 exactly, while the
/// consumer's binary32 factor f32(100/192) = 0.52083331.. gives 15624.9993 -> 15624, so Core lands
/// 1 ms *later* than the consumer. With the host passing the reciprocal of the consumer's binary32
/// factor, Core agrees (15624); CSA-J-02's "1 ms lower" direction describes only one half.
#[gtest]
fn defect_hasted_duration_divergence_is_bidirectional_core_one_ms_later_at_92_percent() -> Result<()> {
    let pct = 92.0_f32;
    let trinity = trinity_binary32(BASE_MS, pct);

    verify_eq!(trinity, 15_624)?;

    let (percent_cast, _) = run(1.0 + f64::from(pct) / 100.0)?;
    verify_eq!(percent_cast, SimTime::from_millis(15_625))?;

    let (reciprocal_cast, _) = run(1.0 / f64::from(100.0_f32 / (100.0_f32 + pct)))?;
    verify_eq!(reciprocal_cast, SimTime::from_millis(15_624))
}

