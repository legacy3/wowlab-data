//! Track C probe: raw-67 missing-or-maximum healing shares the impact critical result.
//!
//! Documents UNK-C-004 (holds as designed by Core, development-ledger.md:2719-2723): Core's exact
//! Full Heal fixture (spell 25840, effect 67, nonzero discriminator -> recipient missing health)
//! resolves a critical heal and doubles the request when the caster has a critical baseline, and
//! the caster-maximum branch (all source facts of First Aid 7162) doubles committed healing.
//! Trinity never crits a pure raw-67 spell (not CU_CAN_CRIT); live Lay on Hands 633/471195 carry
//! CannotCrit explicitly, so retail behaviour for raw 67 without CannotCrit is unresolved.
use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastBeginOutcome, CastRequest, CombatObservation, CombatProgram, CombatStateInput,
    CriticalStrikeBaseline, HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity};
use wowlab_data::{GameData, ResolvedActionCatalog};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, HitResult, PetIndex, SimTime};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{stun_action_input, stun_game_data_input, stun_inputs};

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 1_201_069_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn actor(id: ActorId, health: HealthPool) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(health),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

/// Returns (result, requested, applied) of the Full Heal self-cast.
fn full_heal(baseline: Option<f64>) -> (HitResult, f64, f64) {
    let (fixture, data, actions) = stun_inputs(identity());
    let program = CombatProgram::builder(&data, &actions)
        .build()
        .expect("exact fixture compiles");
    let mut input = CombatStateInput::new(
        vec![
            actor(
                ActorId::Player,
                HealthPool::try_new(500.0, 1_000.0).expect("valid health"),
            ),
            actor(enemy(), HealthPool::full(1_000.0).expect("valid health")),
        ],
        RandomStreamIdentity::new(12, 0),
    )
    .with_hostile_target(ActorId::Player, enemy());

    if let Some(chance) = baseline {
        input = input.with_critical_strike(
            CriticalStrikeBaseline::try_uniform(ActorId::Player, chance, 2.0)
                .expect("valid baseline"),
        );
    }

    let mut state = program.try_state(input).expect("state builds");
    let mut output = Vec::new();
    let started = state
        .begin_cast(
            CastRequest::new(ActorId::Player, ActorId::Player, fixture.full_heal()),
            &mut output,
        )
        .expect("Full Heal starts");

    assert!(matches!(started, CastBeginOutcome::Pending(_)));

    let observations = state
        .advance_to(SimTime::from_millis(1_000))
        .expect("Full Heal completes");

    observations
        .iter()
        .find_map(|observation| match observation {
            CombatObservation::HealingDone {
                result, healing, ..
            } => Some((*result, healing.requested(), healing.applied())),
            _ => None,
        })
        .expect("healing observed")
}

/// UNK-C-004: with no baseline the raw-67 heal requests exactly the 500 missing health; with a
/// guaranteed critical baseline it resolves `Critical` and requests 1,000 (x2.0), applying 500.
#[gtest]
fn holds_missing_health_heal_shares_impact_critical() -> Result<()> {
    verify_eq!(full_heal(None), (HitResult::Hit, 500.0, 500.0))?;
    verify_eq!(full_heal(Some(1.0)), (HitResult::Critical, 1_000.0, 500.0))
}

/// Returns (result, requested, applied) of a raw-67 cast carrying every source fact of the live
/// single-effect First Aid 7162 (effect 67, bp 0 -> caster maximum health, target 21, no
/// attributes) from a 400-health Player onto a friendly pet missing 1,000 health.
fn caster_maximum_heal(baseline: Option<f64>) -> (HitResult, f64, f64) {
    let (fixture, _, _) = stun_inputs(identity());
    let mut data = stun_game_data_input(identity());
    let heal = data
        .effects
        .iter_mut()
        .find(|effect| effect.spell_id == fixture.full_heal().get())
        .expect("raw-67 effect");

    heal.base_points = 0.0;

    // Remaining owner facts copied from live First Aid 7162 (no attributes, school Holy,
    // DefenseType Magic; SpellEffect row: effect 67, bp 0, target 21, sole effect).
    let owner = data
        .spells
        .iter_mut()
        .find(|spell| spell.id == fixture.full_heal().get())
        .expect("raw-67 owner");

    owner.attributes = [0; 17];
    owner.school_mask = 2;
    owner.defense_type = 1;

    let data = GameData::try_from_input(data).expect("data valid");
    let actions =
        ResolvedActionCatalog::try_from_input(stun_action_input(identity())).expect("actions");
    let program = CombatProgram::builder(&data, &actions)
        .build()
        .expect("caster-maximum raw-67 compiles");
    let pet = ActorId::Pet {
        index: PetIndex::new(0),
    };
    let mut input = CombatStateInput::new(
        vec![
            actor(ActorId::Player, HealthPool::full(400.0).expect("valid health")),
            actor(pet, HealthPool::try_new(1_000.0, 2_000.0).expect("valid health")),
        ],
        RandomStreamIdentity::new(12, 0),
    )
    .with_friendly_target(ActorId::Player, pet);

    if let Some(chance) = baseline {
        input = input.with_critical_strike(
            CriticalStrikeBaseline::try_uniform(ActorId::Player, chance, 2.0)
                .expect("valid baseline"),
        );
    }

    let mut state = program.try_state(input).expect("state builds");
    let mut output = Vec::new();

    state
        .begin_cast(
            CastRequest::new(ActorId::Player, pet, fixture.full_heal()),
            &mut output,
        )
        .expect("heal starts");

    let observations = state
        .advance_to(SimTime::from_millis(1_000))
        .expect("heal completes");

    observations
        .iter()
        .find_map(|observation| match observation {
            CombatObservation::HealingDone {
                result, healing, ..
            } => Some((*result, healing.requested(), healing.applied())),
            _ => None,
        })
        .expect("healing observed")
}

/// UNK-C-004: the caster-maximum branch applies 400 normally but 800 when it crits, so the
/// critical result changes committed health, not only the reported request.
#[gtest]
fn holds_caster_maximum_heal_critical_doubles_applied_health() -> Result<()> {
    verify_eq!(caster_maximum_heal(None), (HitResult::Hit, 400.0, 400.0))?;
    verify_eq!(
        caster_maximum_heal(Some(1.0)),
        (HitResult::Critical, 800.0, 800.0)
    )
}
