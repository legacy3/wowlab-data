//! Track I probes: does a delivery-suppressed (immune) impact consume its attack-table draws?
//! (RNG-I-001 immune branch, RNG-I-013, XC-I-001, CSA-I-01)
//!
//! Draws are counted by matching `CombatState`'s Debug-printed `random` field against an
//! independently advanced `RandomStreamIdentity::stream()` (one `occurs_forced` = one u64 draw).

use googletest::prelude::*;
use wowlab_combat::{
    ActiveDefenseBaseline, ActiveDefenseCapabilities, ActorState, AttackAccuracyBaseline,
    AttackNonLandingChances, BlockDefense, CastRequest, CombatObservation, CombatProgram,
    CombatState, CombatStateInput, DefenseFamilyChances, HasteMultipliers, HealthPool,
    OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, GameData, GameDataIdentity, GameDataInput,
    ResolvedActionCatalog,
};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, HitResult};
use wowlab_sim::{Probability, RandomStreamIdentity};
use wowlab_test_support::{
    FixedTravelTimeFixture, SchoolImmunityFixture, fixed_travel_time_action_input,
    fixed_travel_time_game_data_input, school_immunity_action_input,
    school_immunity_game_data_input,
};

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

fn append_game_data(destination: &mut GameDataInput, source: GameDataInput) {
    destination.power_types.extend(source.power_types);
    destination.spells.extend(source.spells);
    destination
        .spell_aura_options
        .extend(source.spell_aura_options);
    destination
        .spell_equipment_requirements
        .extend(source.spell_equipment_requirements);
    destination
        .spell_class_masks
        .extend(source.spell_class_masks);
    destination.spell_missiles.extend(source.spell_missiles);
    destination.effects.extend(source.effects);
    destination
        .effect_attributes
        .extend(source.effect_attributes);
}

/// Illusionary Bolt (fixed travel) + Felbreaker Rune (school immunity mask 126) + Pacify.
fn program() -> CombatProgram {
    let identity = identity();
    let mut data_input = fixed_travel_time_game_data_input(identity);

    append_game_data(&mut data_input, school_immunity_game_data_input(identity));

    let data = GameData::try_from_input(data_input).expect("fixtures compose");
    let mut action_input = fixed_travel_time_action_input(identity);
    let mut immunity_actions = school_immunity_action_input(identity);

    action_input.auras.append(&mut immunity_actions.auras);
    action_input
        .immunity_family
        .schools
        .append(&mut immunity_actions.immunity_family.schools);
    action_input
        .action_preventions
        .pacifies
        .append(&mut immunity_actions.action_preventions.pacifies);
    action_input.actions.append(&mut immunity_actions.actions);
    action_input.programs.append(&mut immunity_actions.programs);

    let actions = ResolvedActionCatalog::try_from_input(action_input).expect("actions compose");

    CombatProgram::builder(&data, &actions)
        .build()
        .expect("fixed travel and school immunity compile together")
}

fn state(program: &CombatProgram, random: RandomStreamIdentity, magic_miss: f64) -> CombatState {
    let miss = DefenseFamilyChances::try_new(magic_miss, 0.0, 0.0).expect("valid miss");
    let input = CombatStateInput::new(vec![actor(ActorId::Player), actor(enemy())], random)
        .with_hostile_target(ActorId::Player, enemy())
        .with_attack_accuracy(AttackAccuracyBaseline::new(
            ActorId::Player,
            DefenseFamilyChances::NONE,
        ))
        .with_active_defense(ActiveDefenseBaseline::new(
            enemy(),
            AttackNonLandingChances::try_new(miss, 0.0, 0.0).expect("valid table"),
            BlockDefense::NONE,
            ActiveDefenseCapabilities::new(),
        ));

    program.try_state(input).expect("state builds")
}

fn make_immune(state: &mut CombatState) {
    state
        .cast(
            CastRequest::new(enemy(), enemy(), SchoolImmunityFixture.provider_spell()),
            &mut Vec::new(),
        )
        .expect("provider applies");
}

fn impact(observations: &[CombatObservation]) -> Option<HitResult> {
    observations.iter().find_map(|observation| match observation {
        CombatObservation::ImpactResolved { result, .. } => Some(*result),
        _ => None,
    })
}

fn actor(id: ActorId) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(100.0).expect("valid health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

const fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
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

/// RNG-I-013 / CSA-I-01: an ordinary (instant) Magic impact against a school-immune target
/// still resolves its underlying attack table and consumes the ordered-outcome draw.
#[gtest]
fn holds_ordinary_immune_impact_consumes_its_attack_table_draw() -> Result<()> {
    let program = program();
    let identity = RandomStreamIdentity::new(292, 900);
    let mut state = state(&program, identity, 0.5);

    make_immune(&mut state);
    verify_that!(consumed(&state, identity), some(eq(0)))?;

    let mut output = Vec::new();

    state
        .cast(
            CastRequest::new(ActorId::Player, enemy(), SchoolImmunityFixture.pacify_spell()),
            &mut output,
        )
        .expect("pacify commits");
    verify_that!(impact(&output), some(eq(HitResult::Immune)))?;
    verify_that!(consumed(&state, identity), some(eq(1)))
}

/// RNG-I-013 / CSA-I-01: the fixed-travel launch of the same-policy Magic table against a
/// launch-immune target consumes NO draw (commit.rs delayed-launch suppression short-circuit),
/// while the same launch against a non-immune target consumes exactly one.
#[gtest]
fn defect_fixed_travel_immune_launch_skips_the_draw_ordinary_immune_consumes() -> Result<()> {
    let program = program();
    let fixture = FixedTravelTimeFixture;
    let identity = RandomStreamIdentity::new(292, 901);

    let mut immune = state(&program, identity, 0.5);

    make_immune(&mut immune);
    immune
        .begin_cast(
            CastRequest::new(ActorId::Player, enemy(), fixture.spell()),
            &mut Vec::new(),
        )
        .expect("carrier accepted");
    immune.advance_to(fixture.cast_duration()).expect("launch");
    verify_that!(consumed(&immune, identity), some(eq(0)))?;
    let arrival = immune.advance_to(fixture.arrival_at()).expect("arrival").to_vec();

    verify_that!(impact(&arrival), some(eq(HitResult::Immune)))?;
    verify_that!(consumed(&immune, identity), some(eq(0)))?;

    let mut open = state(&program, identity, 0.5);

    open.begin_cast(
        CastRequest::new(ActorId::Player, enemy(), fixture.spell()),
        &mut Vec::new(),
    )
    .expect("carrier accepted");
    open.advance_to(fixture.cast_duration()).expect("launch");
    verify_that!(consumed(&open, identity), some(eq(1)))
}
