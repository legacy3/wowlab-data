//! Track I probes: the four spell attack-table RNG leaf sites (RNG-I-001..RNG-I-004) through a
//! public instant direct-damage cast, independently replayed with `RandomStreamIdentity`.
//! Also the mutation grid over probability boundaries (MUT-I-001..MUT-I-004) and roster order
//! (MUT-I-006).

use googletest::prelude::*;
use wowlab_combat::{
    ActiveDefenseBaseline, ActiveDefenseCapabilities, ActorState, AttackNonLandingChances,
    BlockDefense, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    CriticalStrikeBaseline, DefenseFamilyChances, HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity};
use wowlab_model::{ActorId, BlockResult, DifficultyId, EnemyIndex, HitResult};
use wowlab_sim::{Probability, RandomStream, RandomStreamIdentity};
use wowlab_test_support::{SchoolImmunityFixture, school_immunity_rng_inputs};

fn random_text(state: &CombatState) -> String {
    let text = format!("{state:?}");
    let start = text
        .find("random: RandomStream {")
        .expect("CombatState Debug exposes its random stream")
        + "random: ".len();
    let end = start + text[start..].find('}').expect("stream Debug closes") + 1;

    text[start..end].to_owned()
}

fn consumed(state: &CombatState, identity: RandomStreamIdentity) -> Option<usize> {
    let target = random_text(state);
    let mut stream = identity.stream();

    for count in 0..10_000 {
        if format!("{stream:?}") == target {
            return Some(count);
        }

        stream.occurs_forced(Probability::ZERO);
    }

    None
}

fn program() -> CombatProgram {
    let (_, data, actions) = school_immunity_rng_inputs(identity());

    CombatProgram::builder(&data, &actions)
        .build()
        .expect("school-immunity RNG fixture compiles")
}

#[derive(Clone, Copy, Debug)]
struct Chances {
    miss: f64,
    critical: f64,
    block: f64,
    critical_block: f64,
}

fn state(
    program: &CombatProgram,
    random: RandomStreamIdentity,
    chances: Chances,
    reverse_roster: bool,
) -> CombatState {
    let miss = DefenseFamilyChances::try_new(chances.miss, 0.0, 0.0).expect("valid miss");
    let mut actors = vec![actor(ActorId::Player), actor(ActorId::External), actor(enemy())];

    if reverse_roster {
        actors.reverse();
    }

    let input = CombatStateInput::new(actors, random)
        .with_hostile_target(ActorId::Player, enemy())
        .with_critical_strike(
            CriticalStrikeBaseline::try_uniform(ActorId::Player, chances.critical, 2.0)
                .expect("valid critical baseline"),
        )
        .with_active_defense(ActiveDefenseBaseline::new(
            enemy(),
            AttackNonLandingChances::try_new(miss, 0.0, 0.0).expect("valid table"),
            BlockDefense::try_new(chances.block, chances.critical_block, 0.7, 0.4)
                .expect("valid block"),
            ActiveDefenseCapabilities::new().with_magic_block(),
        ));

    program.try_state(input).expect("state builds")
}

fn probe(state: &mut CombatState) -> Option<(HitResult, BlockResult)> {
    try_probe(state).expect("probe commits")
}

fn try_probe(
    state: &mut CombatState,
) -> Result<Option<(HitResult, BlockResult)>, wowlab_combat::CastError> {
    let mut output = Vec::new();

    state.cast(
        CastRequest::new(ActorId::Player, enemy(), SchoolImmunityFixture.rng_probe_spell()),
        &mut output,
    )?;

    Ok(output.iter().find_map(|observation| match observation {
        CombatObservation::ImpactResolved { result, block, .. } => Some((*result, *block)),
        _ => None,
    }))
}

/// Independent replay of `ResolvedAttackTable::resolve` (attack/resolution.rs:464-532).
fn replay(stream: &mut RandomStream, chances: Chances) -> (HitResult, BlockResult) {
    let p = |value: f64| Probability::new(value.min(1.0)).expect("probability");

    if stream.ordered_outcome(&[p(chances.miss), Probability::ZERO, Probability::ZERO]) == Some(0)
    {
        return (HitResult::Miss, BlockResult::Unblocked);
    }

    let hit = if chances.critical > 0.0 && stream.occurs(p(chances.critical)) {
        HitResult::Critical
    } else {
        HitResult::Hit
    };

    if !stream.occurs(p(chances.block)) {
        return (hit, BlockResult::Unblocked);
    }

    if stream.occurs(p(chances.critical_block)) {
        (hit, BlockResult::Critical)
    } else {
        (hit, BlockResult::Blocked)
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
        Some(HealthPool::full(1_000_000.0).expect("valid health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

const fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

const GRID: [f64; 3] = [0.0, 0.5, 1.0];

/// RNG-I-001..004, MUT-I-001..004: for every boundary/interior combination of miss, critical,
/// block and critical-block chance, three consecutive casts expose exactly the outcome and the
/// exact stream position of an independent replay of the documented draw order
/// (ordered miss draw -> critical `occurs` -> block `occurs` -> critical-block `occurs`,
/// each skipped at probability 0 or 1). A certain miss (1.0) is rejected at quote time as
/// NonProgressing and consumes no draw (MUT-I-005).
#[gtest]
fn holds_attack_table_draw_order_and_boundary_skips_match_independent_replay() -> Result<()> {
    let program = program();
    let mut iteration = 0;

    for miss in GRID {
        for critical in GRID {
            for block in GRID {
                for critical_block in GRID {
                    let chances = Chances {
                        miss,
                        critical,
                        block,
                        critical_block,
                    };
                    let identity = RandomStreamIdentity::new(4_001, iteration);
                    let mut state = state(&program, identity, chances, false);
                    let mut expected_stream = identity.stream();

                    iteration += 1;

                    if miss >= 1.0 {
                        let code = try_probe(&mut state).map(|_| ()).map_err(|error| error.code());

                        verify_eq!(code, Err(wowlab_combat::CastErrorCode::NonProgressing))?;
                        verify_that!(consumed(&state, identity), some(eq(0)))?;

                        continue;
                    }

                    for _ in 0..3 {
                        let observed = probe(&mut state);
                        let expected = replay(&mut expected_stream, chances);

                        verify_that!(observed, some(eq(expected)))
                            .with_failure_message(|| format!("{chances:?}"))?;
                        verify_eq!(random_text(&state), format!("{expected_stream:?}"))
                            .with_failure_message(|| format!("{chances:?}"))?;
                    }
                }
            }
        }
    }

    Ok(())
}

/// RNG-I-001, MUT-I-001: a fully deterministic table (all boundary chances) consumes zero draws.
#[gtest]
fn holds_all_boundary_table_consumes_no_draw() -> Result<()> {
    let program = program();
    let identity = RandomStreamIdentity::new(4_002, 0);
    let chances = Chances {
        miss: 0.0,
        critical: 1.0,
        block: 1.0,
        critical_block: 0.0,
    };
    let mut state = state(&program, identity, chances, false);

    for _ in 0..4 {
        verify_that!(
            probe(&mut state),
            some(eq((HitResult::Critical, BlockResult::Blocked)))
        )?;
    }

    verify_that!(consumed(&state, identity), some(eq(0)))
}

/// MUT-I-006: reversing the actor roster input order leaves outcomes and stream identical
/// (actors are canonicalized by ActorId at construction.rs:1018).
#[gtest]
fn holds_roster_input_order_does_not_change_draws() -> Result<()> {
    let program = program();
    let chances = Chances {
        miss: 0.5,
        critical: 0.5,
        block: 0.5,
        critical_block: 0.5,
    };

    for iteration in 0..8 {
        let identity = RandomStreamIdentity::new(4_003, iteration);
        let mut ordinary = state(&program, identity, chances, false);
        let mut reversed = state(&program, identity, chances, true);

        for _ in 0..3 {
            verify_eq!(probe(&mut ordinary), probe(&mut reversed))?;
        }

        verify_eq!(random_text(&ordinary), random_text(&reversed))?;
    }

    Ok(())
}
