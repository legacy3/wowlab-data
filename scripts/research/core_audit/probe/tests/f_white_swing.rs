//! Track F probes: white main-hand runtime (admission, draw order, outcome thresholds,
//! armor/crit order, period arithmetic, cast suppression and Dark Bite launch reset,
//! lethal stop and reset replay). Read-only against Core's public API.
//!
//! Every test names the registry record it proves. `holds_*` tests pin a boundary that
//! currently holds; they fail if Core drifts.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CastBeginOutcome, CastRequest, CombatObservation, CombatProgram, CombatState,
    CombatStateInput, HasteMultipliers, HasteMultipliersInput, HealthPool, OffensivePower,
    PassiveMeleeSpeedBinding, WhiteAdmissionRejection, WhiteEngagementGeometry,
    WhiteMainHandChances, WhiteMainHandInput, WhitePhysicalBasis, WhiteSwingOutcome,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, GameData, GameDataIdentity, ResolvedActionCatalog,
    TraitCurveInput, TraitCurvePointInput, TraitEffectPointInput,
    HasteModifierInput,
};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, SimTime};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    actionless_game_data, actionless_resolved_actions, cooldown_start_inputs,
    selected_furious_blows_source_inputs,
};

const IDENTITY: RandomStreamIdentity = RandomStreamIdentity::new(6_603, 1);

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

const fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn actor_with(id: ActorId, health: f64, haste: HasteMultipliers) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(health).expect("valid health")),
        OffensivePower::ZERO,
        haste,
    )
    .expect("valid actor")
}

fn actor(id: ActorId, health: f64) -> ActorState {
    actor_with(id, health, HasteMultipliers::UNHASTED)
}

const fn chances(miss: u32, dodge: u32, critical: u32) -> WhiteMainHandChances {
    WhiteMainHandChances {
        miss,
        dodge,
        parry: 0,
        block: 0,
        critical,
    }
}

fn white(prepared_damage: (u32, u32), base_period: SimTime, c: WhiteMainHandChances) -> WhiteMainHandInput {
    WhiteMainHandInput {
        source: ActorId::Player,
        target: enemy(),
        prepared_damage,
        base_period,
        effective_levels: (80, 80),
        chances: c,
        critical_percent_before_compiled_modifier: None,
        physical: WhitePhysicalBasis {
            target_armor: 50.0,
            armor_constant: 116.0,
        },
        geometry: WhiteEngagementGeometry {
            in_range: true,
            source_facing: true,
            defender_facing: true,
            defender_standing: true,
            physical_susceptible: true,
            defender_not_evading: true,
        },
    }
}

fn idle_program() -> CombatProgram {
    let data = actionless_game_data(identity());
    let actions = actionless_resolved_actions(identity());

    CombatProgram::builder(&data, &actions)
        .build()
        .expect("actionless program")
}

fn state_input(white: WhiteMainHandInput, target_health: f64, random: RandomStreamIdentity) -> CombatStateInput {
    CombatStateInput::new(
        vec![actor(ActorId::Player, 1_000.0), actor(enemy(), target_health)],
        random,
    )
    .with_hostile_target(ActorId::Player, enemy())
    .with_white_main_hand(white)
}

fn idle_state(white: WhiteMainHandInput, target_health: f64, random: RandomStreamIdentity) -> CombatState {
    idle_program()
        .try_state(state_input(white, target_health, random))
        .expect("isolated white admits")
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct Swing {
    at: SimTime,
    outcome: WhiteSwingOutcome,
    weapon: u32,
    mitigated: u32,
    requested: Option<f64>,
}

fn swings(observations: &[CombatObservation]) -> Vec<Swing> {
    observations
        .iter()
        .filter_map(|observation| match observation {
            CombatObservation::WhiteMainHandSwing {
                at,
                outcome,
                weapon_damage,
                mitigated_damage,
                damage,
                ..
            } => Some(Swing {
                at: *at,
                outcome: *outcome,
                weapon: *weapon_damage,
                mitigated: *mitigated_damage,
                requested: damage.map(|d| d.requested()),
            }),
            _ => None,
        })
        .collect()
}

/// Draw model: per swing one forced weapon draw (even for a singleton) then one outcome draw.
fn forced_model(random: RandomStreamIdentity, width: u32, swings: usize) -> Vec<(u32, u32)> {
    let mut stream = random.stream();

    (0..swings)
        .map(|_| {
            let weapon = stream.uniform_index_forced(width).expect("nonempty");
            let roll = stream.uniform_index(10_000).expect("nonempty");
            (weapon, roll)
        })
        .collect()
}

/// Alternative (rejected) model: a singleton weapon domain consumes no draw.
fn unforced_model(random: RandomStreamIdentity, swings: usize) -> Vec<u32> {
    let mut stream = random.stream();

    (0..swings)
        .map(|_| stream.uniform_index(10_000).expect("nonempty"))
        .collect()
}

/// RNG-F-001 / RNG-F-002: the singleton weapon domain still consumes one forced draw before the
/// fixed-domain outcome draw, on every swing including misses. Discriminated against the
/// "skip singleton draw" model by choosing a seed where the two models classify differently.
#[gtest]
fn holds_white_forced_singleton_weapon_draw_precedes_outcome_draw() -> Result<()> {
    let mut chosen = None;

    for seed in 1..200_u64 {
        let random = RandomStreamIdentity::new(6_603, seed);
        let forced = forced_model(random, 1, 3);
        let unforced = unforced_model(random, 3);
        // miss share splits the first forced roll from the second.
        let miss = forced[0].1 + 1;

        if forced[1].1 >= miss {
            let forced_outcomes: Vec<bool> = forced.iter().map(|(_, r)| *r < miss).collect();
            let unforced_outcomes: Vec<bool> = unforced.iter().map(|r| *r < miss).collect();

            if forced_outcomes != unforced_outcomes {
                chosen = Some((random, miss, forced_outcomes));
                break;
            }
        }
    }

    let (random, miss, expected_miss) = chosen.or_fail()?;
    let mut state = idle_state(
        white((20, 20), SimTime::from_millis(2_000), chances(miss, 0, 0)),
        1.0e9,
        random,
    );
    let observed = swings(&state.advance_to(SimTime::from_millis(4_000))?);

    verify_eq!(observed.len(), 3)?;
    let observed_miss: Vec<bool> = observed
        .iter()
        .map(|s| s.outcome == WhiteSwingOutcome::Miss)
        .collect();
    verify_eq!(observed_miss, expected_miss)?;
    verify_that!(observed.iter().all(|s| s.weapon == 20), eq(true))
}

/// MUT-F-010..013 / NUM-F-004: outcome thresholds are strict (`roll < cumulative`) and
/// cumulative in Miss > Dodge > Critical order, identical to Trinity RollMeleeOutcomeAgainst.
#[gtest]
fn holds_white_outcome_thresholds_are_strict_and_cumulative() -> Result<()> {
    let random = RandomStreamIdentity::new(6_603, 7);
    let (_, roll) = forced_model(random, 8, 1)[0];
    let first = |c: WhiteMainHandChances| -> Result<WhiteSwingOutcome> {
        let mut state = idle_state(white((20, 27), SimTime::from_millis(2_000), c), 1.0e9, random);
        let observed = swings(&state.advance_to(SimTime::ZERO)?);

        Ok(observed.first().or_fail()?.outcome)
    };

    verify_that!(roll, gt(0))?;
    // roll == miss share is not a miss; one more basis point is.
    verify_eq!(first(chances(roll, 0, 0))?, WhiteSwingOutcome::Hit)?;
    verify_eq!(first(chances(roll + 1, 0, 0))?, WhiteSwingOutcome::Miss)?;
    // dodge stacks on top of miss.
    verify_eq!(first(chances(roll, 1, 0))?, WhiteSwingOutcome::Dodge)?;
    verify_eq!(first(chances(roll - 1, 1, 0))?, WhiteSwingOutcome::Hit)?;
    // critical stacks after miss and dodge.
    verify_eq!(first(chances(0, 0, roll))?, WhiteSwingOutcome::Hit)?;
    verify_eq!(first(chances(0, 0, roll + 1))?, WhiteSwingOutcome::Critical)?;
    verify_eq!(first(chances(roll - 1, 0, 2))?, WhiteSwingOutcome::Critical)?;
    // an overlapping earlier share keeps precedence (sum above 10,000 admitted).
    verify_eq!(first(chances(10_000, 0, 10_000))?, WhiteSwingOutcome::Miss)
}

/// NUM-F-002 / NUM-F-003: critical doubling applies to the already-truncated binary32 armor
/// result (Trinity CalculateMeleeDamage order), not to the pre-truncation product.
/// Weapon 20, armor 50 vs constant 116: trunc(20 * (1 - 50/166)) = 13; crit = 26 (not 27).
#[gtest]
fn holds_white_critical_doubles_truncated_armor_result() -> Result<()> {
    let mut state = idle_state(
        white((20, 20), SimTime::from_millis(2_000), chances(0, 0, 10_000)),
        1.0e9,
        IDENTITY,
    );
    let observed = swings(&state.advance_to(SimTime::ZERO)?);
    let first = observed.first().or_fail()?;

    verify_eq!(first.outcome, WhiteSwingOutcome::Critical)?;
    verify_eq!(first.mitigated, 13)?;
    verify_eq!(first.requested, Some(26.0))
}

/// LC-F-003 / MUT-F-020: lethal white damage stops the runtime (no further deadline), emits
/// WhiteMainHandDeath, and both reset paths replay the identical lifecycle.
#[gtest]
fn holds_white_lethal_stop_and_reset_replay() -> Result<()> {
    let mut state = idle_state(
        white((20, 20), SimTime::from_millis(2_000), chances(0, 0, 0)),
        30.0,
        IDENTITY,
    );
    let first = state.advance_to(SimTime::from_millis(60_000))?;
    let observed = swings(&first);

    verify_eq!(observed.len(), 3)?;
    verify_eq!(
        observed.iter().map(|s| s.at).collect::<Vec<_>>(),
        vec![SimTime::ZERO, SimTime::from_millis(2_000), SimTime::from_millis(4_000)]
    )?;
    verify_that!(
        first
            .iter()
            .filter(|o| matches!(o, CombatObservation::WhiteMainHandDeath { .. }))
            .count(),
        eq(1)
    )?;
    verify_eq!(state.next_transition_at(), None)?;
    verify_eq!(state.pending_timer_count(), 0)?;

    state.reset();
    verify_eq!(state.advance_to(SimTime::from_millis(60_000))?, first.clone())?;
    state.reset_for_iteration(IDENTITY);
    verify_eq!(state.advance_to(SimTime::from_millis(60_000))?, first)
}

fn furious_program(
    points: f64,
    rank_two_curve: Option<(f32, f32)>,
    rank: u16,
) -> Result<(CombatProgram, wowlab_model::SpellEffectRef)> {
    let mut inputs = selected_furious_blows_source_inputs();

    inputs.data.effects[0].base_points = points;

    if let Some((one, two)) = rank_two_curve {
        inputs.traits.entries[0].max_ranks = 2;
        inputs.traits.definitions[0].effect_point_row_count = 1;
        inputs.traits.effect_points.push(TraitEffectPointInput {
            id: 1,
            definition_id: inputs.definition_id,
            effect_index: 0,
            operation_type: 0,
            curve_id: 78_492,
        });
        inputs.traits.curves.push(TraitCurveInput {
            id: 78_492,
            curve_type: 0,
            flags: 0,
            point_row_count: 2,
        });
        inputs.traits.curve_points.push(TraitCurvePointInput {
            id: 221_313,
            curve_id: 78_492,
            order_index: 0,
            x: 1.0,
            y: one,
        });
        inputs.traits.curve_points.push(TraitCurvePointInput {
            id: 221_314,
            curve_id: 78_492,
            order_index: 1,
            x: 2.0,
            y: two,
        });
    }

    match &mut inputs.actions.haste_modifiers[0] {
        HasteModifierInput::SelectedTraitPassiveMeleeAutoAttackSpeed { effective_rank, .. } => {
            *effective_rank = rank;
        }
        _ => fail!("Furious Blows declaration shape changed")?,
    }

    let effect = inputs.provider_effect;
    let fixture = inputs.freeze();
    let actions = ResolvedActionCatalog::try_from_input(fixture.actions)?;
    let program = CombatProgram::builder(&fixture.data, &actions)
        .trait_source(&fixture.traits)
        .external_spell_policy(&fixture.external_policy)
        .build()?;

    Ok((program, effect))
}

fn second_swing_at(program: &CombatProgram, effect: wowlab_model::SpellEffectRef, base: SimTime) -> Result<SimTime> {
    let input = state_input(white((20, 20), base, chances(0, 0, 0)), 1.0e9, IDENTITY)
        .with_passive_melee_auto_attack_speed_binding(PassiveMeleeSpeedBinding::new(
            ActorId::Player,
            effect,
        ));
    let mut state = program.try_state(input)?;
    let observed = swings(&state.advance_to(SimTime::ZERO)?);

    verify_eq!(observed.len(), 1)?;

    state.next_transition_at().or_fail()
}

/// NUM-F-001: the white period is `trunc(binary32(base_ms) * binary32(100 / (100 + p)))`,
/// the uint32 x binary32 attack-time consumer order (Trinity resetAttackTimer / ApplyPercentModFloatVar).
/// Witness (synthetic p = 15 on the raw-319 Furious Blows shell): base 2300 ms -> 1999 ms,
/// where exact/f64 arithmetic gives 2000 ms. The live Furious Blows p = 5 is also pinned.
#[gtest]
fn holds_white_period_uses_binary32_attack_time_order() -> Result<()> {
    let (live, effect) = furious_program(5.0, None, 1)?;

    verify_eq!(
        second_swing_at(&live, effect, SimTime::from_millis(2_000))?,
        SimTime::from_millis(1_904)
    )?;

    let (synthetic, effect) = furious_program(15.0, None, 1)?;
    let exact = 2_300.0_f64 * 100.0 / 115.0;

    verify_that!(exact.trunc(), eq(2_000.0))?;
    verify_eq!(
        second_swing_at(&synthetic, effect, SimTime::from_millis(2_300))?,
        SimTime::from_millis(1_999)
    )
}

/// FF-F-002 / MUT-F-030: the white period consumes the selected-rank amount (Zealot's Fervor
/// shaped Set curve 78492: rank 1 = 20, rank 2 = 40), not authored points.
#[gtest]
fn holds_white_period_consumes_selected_rank_amount() -> Result<()> {
    let (rank_one, effect) = furious_program(20.0, Some((20.0, 40.0)), 1)?;
    let (rank_two, _) = furious_program(20.0, Some((20.0, 40.0)), 2)?;

    // 2800 * (100/120) = 2333.33 ; 2800 * (100/140) = 2000.
    verify_eq!(
        second_swing_at(&rank_one, effect, SimTime::from_millis(2_800))?,
        SimTime::from_millis(2_333)
    )?;
    verify_eq!(
        second_swing_at(&rank_two, effect, SimTime::from_millis(2_800))?,
        SimTime::from_millis(2_000)
    )
}

fn dark_bite_state(period: SimTime) -> Result<(CombatState, wowlab_model::SpellId)> {
    let (fixture, data, actions): (_, GameData, ResolvedActionCatalog) = cooldown_start_inputs(identity());
    let program = CombatProgram::builder(&data, &actions).build()?;
    let state = program.try_state(state_input(
        white((20, 20), period, chances(0, 0, 0)),
        1.0e9,
        IDENTITY,
    ))?;

    Ok((state, fixture.spell()))
}

/// FF-F-003 / LC-F-002 / MUT-F-040: a hard cast suppresses the main hand without catch-up;
/// the Dark Bite successful launch resets the deadline to completion + period.
/// Period 500 < cast 2000: swing at 0, none in (0, 2000], then 2500, 3000, ...
/// (a catch-up model would swing at 2000; a start-reset model would swing at 2000 too).
#[gtest]
fn holds_white_cast_suppression_without_catch_up_and_launch_reset() -> Result<()> {
    let (mut state, spell) = dark_bite_state(SimTime::from_millis(500))?;
    let initial = swings(&state.advance_to(SimTime::ZERO)?);

    verify_eq!(initial.len(), 1)?;

    let mut output = Vec::new();
    let begun = state.begin_cast(CastRequest::new(ActorId::Player, enemy(), spell), &mut output);
    let completes_at = match begun {
        Ok(CastBeginOutcome::Pending(start)) => start.completes_at(),
        _ => {
            fail!("Dark Bite must begin a hard cast")?;
            unreachable!()
        }
    };

    verify_eq!(completes_at, SimTime::from_millis(2_000))?;
    // while casting, the next transition is the cast completion, not the overdue swing.
    verify_eq!(state.next_transition_at(), Some(SimTime::from_millis(2_000)))?;

    let during = state.advance_to(SimTime::from_millis(2_000))?;

    verify_that!(swings(&during), is_empty())?;
    verify_that!(
        during
            .iter()
            .any(|o| matches!(o, CombatObservation::CastCompleted { .. })),
        eq(true)
    )?;
    verify_eq!(state.next_transition_at(), Some(SimTime::from_millis(2_500)))?;

    let after = swings(&state.advance_to(SimTime::from_millis(3_000))?);

    verify_eq!(
        after.iter().map(|s| s.at).collect::<Vec<_>>(),
        vec![SimTime::from_millis(2_500), SimTime::from_millis(3_000)]
    )
}

fn rejection(program: &CombatProgram, input: CombatStateInput) -> Option<WhiteAdmissionRejection> {
    program
        .try_state(input)
        .err()
        .and_then(|error| error.white_main_hand_reason())
}

/// MUT-F-050..066: one-dimension admission mutations of the isolated main hand.
#[gtest]
fn holds_white_admission_mutations_fail_closed() -> Result<()> {
    let program = idle_program();
    let base = white((20, 27), SimTime::from_millis(2_000), chances(500, 300, 1_000));
    let admit = |w: WhiteMainHandInput| rejection(&program, state_input(w, 1_000.0, IDENTITY));

    verify_eq!(admit(base), None)?;

    use WhiteAdmissionRejection::{
        InvalidPreparedBasis, UnsupportedComposition, UnsupportedEligibility, UnsupportedEngagement,
    };

    let cases: Vec<(&str, WhiteMainHandInput, Option<WhiteAdmissionRejection>)> = vec![
        ("parry share", WhiteMainHandInput { chances: WhiteMainHandChances { parry: 1, ..base.chances }, ..base }, Some(InvalidPreparedBasis)),
        ("block share", WhiteMainHandInput { chances: WhiteMainHandChances { block: 1, ..base.chances }, ..base }, Some(InvalidPreparedBasis)),
        ("critical > 10000", WhiteMainHandInput { chances: chances(0, 0, 10_001), ..base }, Some(InvalidPreparedBasis)),
        ("unequal levels", WhiteMainHandInput { effective_levels: (80, 83), ..base }, Some(UnsupportedEligibility)),
        ("zero level", WhiteMainHandInput { effective_levels: (0, 0), ..base }, Some(UnsupportedEligibility)),
        ("out of range", WhiteMainHandInput { geometry: WhiteEngagementGeometry { in_range: false, ..base.geometry }, ..base }, Some(UnsupportedEligibility)),
        ("defender sitting", WhiteMainHandInput { geometry: WhiteEngagementGeometry { defender_standing: false, ..base.geometry }, ..base }, Some(UnsupportedEligibility)),
        ("behind with no parry/block", WhiteMainHandInput { geometry: WhiteEngagementGeometry { defender_facing: false, ..base.geometry }, ..base }, None),
        ("zero minimum", WhiteMainHandInput { prepared_damage: (0, 5), ..base }, Some(InvalidPreparedBasis)),
        ("reversed range", WhiteMainHandInput { prepared_damage: (6, 5), ..base }, Some(InvalidPreparedBasis)),
        ("maximum above u32::MAX/2", WhiteMainHandInput { prepared_damage: (1, u32::MAX / 2 + 1), ..base }, Some(InvalidPreparedBasis)),
        ("zero period", WhiteMainHandInput { base_period: SimTime::ZERO, ..base }, Some(InvalidPreparedBasis)),
        ("one-ms period", WhiteMainHandInput { base_period: SimTime::from_millis(1), ..base }, None),
        ("mitigated minimum zero", WhiteMainHandInput { prepared_damage: (1, 5), physical: WhitePhysicalBasis { target_armor: 1.0e9, armor_constant: 1.0 }, ..base }, Some(InvalidPreparedBasis)),
        ("negative armor", WhiteMainHandInput { physical: WhitePhysicalBasis { target_armor: -1.0, armor_constant: 116.0 }, ..base }, Some(InvalidPreparedBasis)),
        ("zero armor constant", WhiteMainHandInput { physical: WhitePhysicalBasis { target_armor: 1.0, armor_constant: 0.0 }, ..base }, Some(InvalidPreparedBasis)),
        ("NaN armor", WhiteMainHandInput { physical: WhitePhysicalBasis { target_armor: f32::NAN, armor_constant: 116.0 }, ..base }, Some(InvalidPreparedBasis)),
        ("uncompiled crit basis", WhiteMainHandInput { critical_percent_before_compiled_modifier: Some(10.0), ..base }, Some(UnsupportedComposition)),
        ("enemy source", WhiteMainHandInput { source: enemy(), target: ActorId::Player, ..base }, Some(UnsupportedEngagement)),
    ];

    for (name, input, expected) in cases {
        verify_eq!((name, admit(input)), (name, expected))?;
    }

    // auto-attack-speed actor channel is not consumed; any non-UNHASTED actor fails closed.
    let hasted = HasteMultipliers::try_from_input(HasteMultipliersInput {
        spell_haste: 1.0,
        spell_cast_speed: 1.0,
        attack_haste: 1.0,
        auto_attack_speed: 1.1,
        haste_regeneration: 1.0,
    })
    .or_fail()?;
    let hasted_input = CombatStateInput::new(
        vec![actor_with(ActorId::Player, 1_000.0, hasted), actor(enemy(), 1_000.0)],
        IDENTITY,
    )
    .with_hostile_target(ActorId::Player, enemy())
    .with_white_main_hand(base);

    verify_eq!(rejection(&program, hasted_input), Some(UnsupportedComposition))?;

    // missing hostile relation.
    let unrelated = CombatStateInput::new(
        vec![actor(ActorId::Player, 1_000.0), actor(enemy(), 1_000.0)],
        IDENTITY,
    )
    .with_white_main_hand(base);

    verify_eq!(rejection(&program, unrelated), Some(UnsupportedEngagement))
}

/// CSA-F-01 / MUT-F-070..073 / XC-F-002: passive melee auto-attack-speed bindings are consumed
/// only by white admission and discarded by construction (`stat_bindings.into_parts()` `_`).
/// Without a main hand, an uncompiled effect, a foreign (Enemy) actor and a duplicate binding are
/// all accepted; the sibling raw-193 all-haste binding family fails closed for an uncompiled effect.
#[gtest]
fn defect_melee_speed_bindings_unvalidated_without_main_hand() -> Result<()> {
    use wowlab_combat::{CombatStateErrorCode, PassiveHasteAllBinding};

    let base = || {
        CombatStateInput::new(
            vec![actor(ActorId::Player, 1_000.0), actor(enemy(), 1_000.0)],
            IDENTITY,
        )
        .with_hostile_target(ActorId::Player, enemy())
    };
    let (furious, effect) = furious_program(5.0, None, 1)?;
    let bogus = wowlab_model::SpellEffectRef::new(wowlab_model::SpellId::new(12_345).or_fail()?, 1)
        .or_fail()?;
    let idle = idle_program();
    let outcome = |p: &CombatProgram, i: CombatStateInput| p.try_state(i).err().map(|e| e.code());

    // accepted (and silently discarded):
    verify_eq!(
        outcome(
            &idle,
            base().with_passive_melee_auto_attack_speed_binding(PassiveMeleeSpeedBinding::new(
                ActorId::Player,
                bogus
            ))
        ),
        None
    )?;
    verify_eq!(
        outcome(
            &furious,
            base().with_passive_melee_auto_attack_speed_binding(PassiveMeleeSpeedBinding::new(
                enemy(),
                effect
            ))
        ),
        None
    )?;
    verify_eq!(
        outcome(
            &furious,
            base().with_passive_melee_auto_attack_speed_bindings(vec![
                PassiveMeleeSpeedBinding::new(ActorId::Player, effect);
                2
            ])
        ),
        None
    )?;
    // sibling raw-193 binding family fails closed for the same uncompiled effect:
    verify_eq!(
        outcome(
            &idle,
            base().with_passive_haste_all_binding(PassiveHasteAllBinding::new(ActorId::Player, bogus))
        ),
        Some(CombatStateErrorCode::MissingPassiveHasteAllDefinition)
    )
}
