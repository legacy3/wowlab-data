//! Track J probe: raw-232 mechanic-duration percentage rounding direction.
//! Core floors `authored_ms * (100 + points) / 100` in integers; the Trinity consumer applies
//! `AddPct(int32 duration, int32 mod)` = `duration += int32(float(duration) * mod / 100.0f)`,
//! truncating the negative delta toward zero (Object.cpp:1753-1760; Util.h:72-88), i.e. it rounds
//! the reduced duration up.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CombatProgram, CombatStateInput, HasteMultipliers, HealthPool, OffensivePower,
    PassiveMechanicDurationBinding,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, GameData, GameDataIdentity, ResolvedActionCatalog,
    ResolvedAuraInput,
};
use wowlab_model::{ActorId, AuraId, AuraKey, DifficultyId, EnemyIndex, SimTime};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    MechanicDurationFixture, MechanicDurationProgramShape, mechanic_duration_action_input_for,
    mechanic_duration_source_input_for,
};

const AURA_MS: u32 = 1_250;
/// The only admitted raw-232 provider (55366) is gated to exactly -10 points.
const POINTS: f64 = -10.0;

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

fn actor(id: ActorId) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(10_000.0).expect("health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("actor")
}

fn trinity_add_pct(duration: i32, points: i32) -> i32 {
    duration + (duration as f32 * points as f32 / 100.0_f32) as i32
}

type Built = std::result::Result<CombatProgram, wowlab_combat::CombatProgramBuildError>;

fn build(aura_ms: u32) -> Result<Built> {
    let fixture = MechanicDurationFixture;
    let mut source =
        mechanic_duration_source_input_for(identity(), MechanicDurationProgramShape::Raw232);
    let provider = fixture.additive_provider_effect();
    let provider_effect = source
        .effects
        .iter_mut()
        .find(|effect| effect.spell_id == provider.spell().get())
        .or_fail()?;
    provider_effect.base_points = POINTS;
    let mut actions =
        mechanic_duration_action_input_for(identity(), MechanicDurationProgramShape::Raw232);
    let applicand = fixture.applicand_effect().spell();
    let aura = actions
        .auras
        .iter_mut()
        .find(|aura| aura.aura_id == applicand.get())
        .or_fail()?;
    *aura = ResolvedAuraInput::finite(applicand.get(), aura_ms, 1);

    let data = GameData::try_from_input(source)?;
    let actions = ResolvedActionCatalog::try_from_input(actions)?;

    Ok(CombatProgram::builder(&data, &actions).build())
}

fn expiry(program: &CombatProgram) -> Result<SimTime> {
    let fixture = MechanicDurationFixture;
    let applicand = fixture.applicand_effect().spell();
    let mut state = program.try_state(
        CombatStateInput::new(
            vec![actor(ActorId::Player), actor(enemy())],
            RandomStreamIdentity::new(232, 10),
        )
        .with_hostile_target(ActorId::Player, enemy())
        .with_passive_mechanic_duration_binding(PassiveMechanicDurationBinding::new(
            enemy(),
            fixture.additive_provider_effect(),
        )),
    )?;
    let mut output = Vec::new();

    state.begin_cast(
        wowlab_combat::CastRequest::new(ActorId::Player, enemy(), applicand),
        &mut output,
    )?;
    state.advance_to(SimTime::from_millis(3_000))?;

    let key = AuraKey::new(
        AuraId::new(applicand.get()).or_fail()?,
        ActorId::Player,
        enemy(),
    );

    state
        .active_aura(key)
        .and_then(wowlab_combat::ActiveAura::expires_at)
        .or_fail()
}

/// NUM-J-030 / CSA-J-04 (LATENT): Core floors `authored * (100 + p) / 100`; Trinity rounds the
/// reduced duration up (negative delta truncated toward zero). A source-valid 1250 ms
/// (SpellDuration 568) applicand with -25% (second most common raw-232 amount) would be 937 in
/// Core vs 938 in Trinity, but the admitted provider is gated to exactly -10 (55366) or -50
/// (60209, raw 234) and the only admitted applicand (silence 326836) to an exact five-second
/// lifetime, where every admitted percentage is exact (5000 * 90 / 100 = 4500).
#[gtest]
fn holds_mechanic_duration_rounding_divergence_is_gated_by_exact_five_second_carrier() -> Result<()> {
    verify_eq!(trinity_add_pct(1_250, -25), 938)?;
    verify_eq!(1_250_u64 * 75 / 100, 937)?;
    verify_eq!(trinity_add_pct(5_000, -10), 4_500)?;

    // Upstream gate: the 1250 ms applicand is rejected at program build.
    verify_true!(build(AURA_MS)?.is_err())?;

    // Admitted carrier: exact in both consumers.
    let program = build(5_000)?.or_fail()?;

    verify_eq!(expiry(&program)?, SimTime::from_millis(3_000 + 4_500))
}
