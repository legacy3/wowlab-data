//! Track J probes: hasted cast-time / cooldown millisecond conversion (f64 frequency division
//! then truncation) versus the Trinity consumer's binary32 period-multiplier product.

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

/// NUM-J-010 / CSA-J-02: 2.02% binary32 haste (f32 0x40014...: 2.0199999809).
/// Trinity: 30000 * f32(100/102.02) = 29405.9998 -> rounds to 29406.0f -> 29406 ms.
/// Core (host frequency `1 + pct/100` or the exact reciprocal of Trinity's binary32 multiplier):
/// 30000 / 1.0202 = 29405.9988 -> 29405 ms, for both the cast time and the hasted cooldown.
#[gtest]
fn defect_hasted_duration_f64_division_truncates_one_ms_below_binary32_consumer() -> Result<()> {
    let pct = 2.02_f32;
    let trinity = trinity_binary32(BASE_MS, pct);

    verify_eq!(trinity, 29_406)?;

    let percent_speed = 1.0 + f64::from(pct) / 100.0;
    let reciprocal_speed = 1.0 / f64::from(100.0_f32 / (100.0_f32 + pct));

    for speed in [percent_speed, reciprocal_speed] {
        let (cast_completes, cooldown_ready) = run(speed)?;

        verify_eq!(cast_completes, SimTime::from_millis(29_405))?;
        // Cooldown starts at the (already truncated) completion and adds its own truncation.
        verify_eq!(cooldown_ready, Some(SimTime::from_millis(29_405 + 29_405)))?;
    }

    Ok(())
}
