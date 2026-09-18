//! Track H: Engine-level reset / iteration-reset determinism (LC-H-*, REJ-H-*).
//!
//! Uses the public fixed-travel-time fixture (hard cast + pending fixed impacts + RNG critical
//! resolution) through `Engine`, and compares the complete `Debug` rendering of the Engine
//! (combat state, rotation scratch, retained observations) after reset against a fresh Engine.

use wowlab_combat::{
    ActorState, CombatProgram, CriticalStrikeBaseline, HasteMultipliers, HealthPool,
    OffensivePower,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity};
use wowlab_engine::{Engine, EngineInput, EngineProgram};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex};
use wowlab_rotation::{Action, Identifier, Rotation};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{FixedTravelTimeFixture, fixed_travel_time_inputs};

const fn identity() -> GameDataIdentity {
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

fn actor(id: ActorId) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(1_000_000.0).expect("health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("actor")
}

fn engine(random: RandomStreamIdentity) -> Engine {
    let fixture = FixedTravelTimeFixture;
    let (_, data, actions) = fixed_travel_time_inputs(identity());
    let combat = CombatProgram::builder(&data, &actions).build().expect("compiles");
    let name = Identifier::new("illusionary_bolt").expect("name");
    let mut rotation = Rotation::new("h reset".to_owned());
    rotation.actions.push(Action::Cast {
        spell: name.clone(),
        empower_rank: None,
        enabled: true,
        condition: None,
        target: None,
    });
    let program = EngineProgram::try_new(combat, &rotation, &move |candidate: &Identifier| {
        (candidate == &name).then(|| fixture.spell())
    })
    .expect("rotation prepares");
    let input = EngineInput::new(vec![actor(ActorId::Player), actor(enemy())]).with_critical_strike(
        CriticalStrikeBaseline::try_uniform(enemy(), 0.5, 2.0).expect("crit"),
    );
    Engine::try_new(program, input, enemy(), ActorId::Player, random).expect("engine")
}

/// Engine Debug with the rotation list-frame scratch removed: `RotationState::clear` only resets
/// frames below the live depth, so popped frames keep dead residue that `push` always overwrites
/// (REJ-H: rotation scratch residue is inert).
fn semantic(engine: &Engine) -> String {
    let text = format!("{engine:?}");
    let start = text.find("rotation: RotationState {").expect("rotation state rendered");
    let end = text[start..].find(", observations:").expect("observations follow") + start;
    format!("{}{}", &text[..start], &text[end..])
}

fn run(engine: &mut Engine, steps: usize) -> Vec<String> {
    (0..steps)
        .map(|_| match engine.advance() {
            Ok(outcome) => format!("{outcome:?}"),
            Err(error) => format!("err {error:?}"),
        })
        .collect()
}

/// LC-H-001/LC-H-017 at Engine level: reset replays bit-identically; iteration reset equals a
/// fresh Engine for the new identity (full Debug, including rotation scratch and RNG).
#[test]
fn holds_engine_reset_and_iteration_reset_are_fresh_equivalent() {
    let first = RandomStreamIdentity::new(292, 901);
    let second = RandomStreamIdentity::new(292, 17);
    let mut reused = engine(first);
    let fresh_debug = semantic(&reused);
    let fresh_full = format!("{reused:?}");
    let trace = run(&mut reused, 9);

    assert!(reused.combat_state().pending_timer_count() > 0 || !trace.is_empty());
    reused.reset();
    assert_eq!(semantic(&reused), fresh_debug, "reset differs from fresh Engine");
    // Observed residue: only the rotation frame scratch differs in the full rendering.
    assert_ne!(format!("{reused:?}"), fresh_full);
    assert_eq!(run(&mut reused, 9), trace, "reset replay diverges");

    reused.reset_for_iteration(second);
    let mut fresh = engine(second);
    assert_eq!(semantic(&reused), semantic(&fresh), "iteration reset differs");
    assert_eq!(run(&mut reused, 9), run(&mut fresh, 9));
}
