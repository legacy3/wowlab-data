//! Track J probes: white main-hand binary32 conversions (critical percent association,
//! armor-zero binary32 bypass). Read-only against Core's public API.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CombatObservation, CombatProgram, HasteMultipliers, HealthPool, OffensivePower,
    WhiteEngagementGeometry, WhiteMainHandChances, WhiteMainHandInput, WhitePhysicalBasis,
    WhiteSwingOutcome,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity, ResolvedActionCatalog};
use wowlab_engine::{
    Engine, EngineInput, EngineProgram, PreparedTraitCriticalCandidate, SelectedTraitEntry,
};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, SimTime};
use wowlab_rotation::{Identifier, Rotation};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    actionless_game_data, actionless_resolved_actions, selected_canny_strikes_fixture,
};

const IDENTITY: RandomStreamIdentity = RandomStreamIdentity::new(211, 1);
const WEAPON_MINIMUM: u32 = 20;
const WEAPON_WIDTH: u32 = 8;

fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn actor(id: ActorId, health: f64) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(health).expect("valid health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

fn geometry() -> WhiteEngagementGeometry {
    WhiteEngagementGeometry {
        in_range: true,
        source_facing: true,
        defender_facing: true,
        defender_standing: true,
        physical_susceptible: true,
        defender_not_evading: true,
    }
}

fn white(
    prepared_damage: (u32, u32),
    chances: WhiteMainHandChances,
    critical_percent_before_compiled_modifier: Option<f32>,
    physical: WhitePhysicalBasis,
) -> WhiteMainHandInput {
    WhiteMainHandInput {
        source: ActorId::Player,
        target: enemy(),
        prepared_damage,
        base_period: SimTime::from_secs(2),
        effective_levels: (1, 1),
        chances,
        critical_percent_before_compiled_modifier,
        physical,
        geometry: geometry(),
    }
}

fn selected_canny_engine(chances: WhiteMainHandChances, percent: f32) -> Result<Engine> {
    let fixture = selected_canny_strikes_fixture();
    let candidate = PreparedTraitCriticalCandidate::try_prepare(
        &fixture.data,
        &fixture.traits,
        &[SelectedTraitEntry {
            entry_id: fixture.entry_id,
            effective_rank: 1,
        }],
    )?;
    let base = EngineInput::new(vec![
        actor(ActorId::Player, 1_000.0),
        actor(enemy(), 1_000.0),
    ])
    .with_initial_white_main_hand(white(
        (WEAPON_MINIMUM, WEAPON_MINIMUM + WEAPON_WIDTH - 1),
        chances,
        Some(percent),
        WhitePhysicalBasis {
            target_armor: 0.0,
            armor_constant: 116.0,
        },
    ));
    let (actions, input) = candidate.try_into_inputs(fixture.actions, base)?;
    let actions = ResolvedActionCatalog::try_from_input(actions)?;
    let external_policy = wowlab_test_support::absent_spell_external_policy(&fixture.data);
    let combat = CombatProgram::builder(&fixture.data, &actions)
        .trait_source(&fixture.traits)
        .external_spell_policy(&external_policy)
        .build()?;
    let program = EngineProgram::try_new(
        combat,
        &Rotation::new("J probe Canny white".to_owned()),
        &|_: &Identifier| None,
    )?;

    Ok(Engine::try_new(program, input, ActorId::Player, enemy(), IDENTITY)?)
}

fn actionless_program() -> EngineProgram {
    let identity = GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    };
    let data = actionless_game_data(identity);
    let actions = actionless_resolved_actions(identity);
    let combat = CombatProgram::builder(&data, &actions)
        .build()
        .expect("actionless program");

    EngineProgram::try_new(
        combat,
        &Rotation::new("J probe white".to_owned()),
        &|_: &Identifier| None,
    )
    .expect("engine program")
}

fn first_white(observations: &[CombatObservation]) -> Option<(u32, WhiteSwingOutcome, u32)> {
    observations.iter().find_map(|observation| match observation {
        CombatObservation::WhiteMainHandSwing {
            weapon_damage,
            outcome,
            mitigated_damage,
            ..
        } => Some((*weapon_damage, *outcome, *mitigated_damage)),
        _ => None,
    })
}

fn first_outcome_roll() -> Result<u32> {
    let mut random = IDENTITY.stream();
    let _weapon = random.uniform_index_forced(WEAPON_WIDTH);

    random.uniform_index(10_000).or_fail()
}

/// NUM-J-002 / CSA-J-01: Core adds the compiled raw-290 points to the host's already
/// summed binary32 percent `(5 + rating) + 2`, while the Trinity consumer sums
/// `(flat_auras + 5) + rating` (Player::UpdateCritPercentage + UpdateWeaponDependentCritAuras).
/// Witness: rating bonus f32 0x3f9ffffb (1.2499994); host basis f32(5 + r) = 0x40c7ffff
/// (6.2499995, truncates to 624). Core: 6.2499995 + 2 = 8.25 -> 825 basis points.
/// Trinity order: (2 + 5) + 1.2499994 = 8.249999 -> 824 basis points.
/// The roll placed at `miss + 824` is Critical in Core, Hit under Trinity's association.
#[gtest]
fn defect_white_critical_adds_passive_after_host_sum_binary32_association() -> Result<()> {
    let rating = f32::from_bits(0x3f9f_fffb);
    let basis = 5.0_f32 + rating;

    verify_eq!(basis.to_bits(), 0x40c7_ffff)?;
    verify_eq!((basis * 100.0) as u32, 624)?;
    verify_eq!(((basis + 2.0) * 100.0) as u32, 825)?;
    verify_eq!((((2.0_f32 + 5.0) + rating) * 100.0) as u32, 824)?;

    let roll = first_outcome_roll()?;
    let miss = roll.checked_sub(824).or_fail()?;
    let chances = WhiteMainHandChances {
        miss,
        dodge: 0,
        parry: 0,
        block: 0,
        critical: 624,
    };
    let mut engine = selected_canny_engine(chances, basis)?;
    let swing = first_white(engine.advance()?.observations()).or_fail()?;

    // Core admits 825 basis points: roll == miss + 824 is a Critical.
    verify_eq!(swing.1, WhiteSwingOutcome::Critical)?;

    // Control: the roll one past Core's threshold is an ordinary Hit (threshold is exactly 825).
    let mut above = selected_canny_engine(
        WhiteMainHandChances {
            miss: roll.checked_sub(825).or_fail()?,
            ..chances
        },
        basis,
    )?;
    verify_eq!(
        first_white(above.advance()?.observations()).or_fail()?.1,
        WhiteSwingOutcome::Hit
    )
}

/// NUM-J-004: armor exactly zero returns the integer weapon damage without the binary32
/// narrowing that the positive-armor path (and Trinity's MeleeDamageBonusDone/Taken float
/// round trip) applies. Above 2^24 this is observable: 16,777,217 stays exact at zero armor.
#[gtest]
fn holds_white_zero_armor_skips_binary32_narrowing_above_2_pow_24() -> Result<()> {
    let damage = 16_777_217_u32;
    let chances = WhiteMainHandChances {
        miss: 0,
        dodge: 0,
        parry: 0,
        block: 0,
        critical: 0,
    };
    let run = |armor: f32| -> Result<(u32, WhiteSwingOutcome, u32)> {
        let input = EngineInput::new(vec![
            actor(ActorId::Player, 1.0e12),
            actor(enemy(), 1.0e12),
        ])
        .with_initial_white_main_hand(white(
            (damage, damage),
            chances,
            None,
            WhitePhysicalBasis {
                target_armor: armor,
                armor_constant: 1.0e30,
            },
        ));
        let mut engine = Engine::try_new(
            actionless_program(),
            input,
            ActorId::Player,
            enemy(),
            IDENTITY,
        )?;

        first_white(engine.advance()?.observations()).or_fail()
    };

    let zero = run(0.0)?;
    // armor/(armor+1e30) underflows to a reduction far below binary32 epsilon: factor 1.0f.
    let tiny = run(1.0)?;

    verify_eq!(zero.2, 16_777_217)?;
    verify_eq!(tiny.2, 16_777_216)
}
