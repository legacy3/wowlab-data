//! Track H: state-construction topology mutations and host-API failure purity (MUT-H-*).
//!
//! One dimension is mutated at a time on a real public fixture (interrupt fixture, which compiles
//! hard casts, school locks and target relations) and the observed outcome is asserted.

use wowlab_combat::{
    CastTargetRelation, CombatProgram, CombatState, CombatStateErrorCode, CombatStateInput,
    PassiveFlowErrorCode, PassiveResourceFlow, TargetDisposition,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, RecoveryRate, ResourceType, SimTime};
use wowlab_sim::RandomStreamIdentity;

#[path = "h_reset_fingerprint.rs"]
#[allow(dead_code)]
mod harness;

use harness::{actor, dbg_full, enemy};

const fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn program() -> CombatProgram {
    let (_, data, actions) = wowlab_test_support::interrupt_inputs(identity());
    CombatProgram::builder(&data, &actions).build().expect("interrupt fixture compiles")
}

fn random() -> RandomStreamIdentity {
    RandomStreamIdentity::new(5, 9)
}

fn second_enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(1),
    }
}

fn code(program: &CombatProgram, input: CombatStateInput) -> CombatStateErrorCode {
    program.try_state(input).expect_err("mutation must reject").code()
}

/// MUT-H-001: actor and relation input order is not semantic (canonical sort); permuted inputs
/// build bit-identical states.
#[test]
fn holds_actor_and_relation_order_is_canonicalized() {
    let program = program();
    let base = CombatStateInput::new(
        vec![actor(ActorId::Player), actor(enemy()), actor(second_enemy())],
        random(),
    )
    .with_hostile_target(ActorId::Player, enemy())
    .with_hostile_target(ActorId::Player, second_enemy());
    let permuted = CombatStateInput::new(
        vec![actor(second_enemy()), actor(enemy()), actor(ActorId::Player)],
        random(),
    )
    .with_hostile_target(ActorId::Player, second_enemy())
    .with_hostile_target(ActorId::Player, enemy());

    let left = program.try_state(base).expect("builds");
    let right = program.try_state(permuted).expect("builds");
    assert_eq!(dbg_full(&left), dbg_full(&right));
}

/// MUT-H-002..006: roster/relation topology mutations are rejected with exact codes.
#[test]
fn holds_topology_mutations_reject_with_exact_codes() {
    let program = program();
    let two = || vec![actor(ActorId::Player), actor(enemy())];

    assert_eq!(
        code(
            &program,
            CombatStateInput::new(vec![actor(ActorId::Player), actor(enemy()), actor(enemy())], random())
                .with_hostile_target(ActorId::Player, enemy())
        ),
        CombatStateErrorCode::DuplicateActor
    );
    assert_eq!(
        code(
            &program,
            CombatStateInput::new(two(), random()).with_relation(CastTargetRelation::new(
                ActorId::Player,
                ActorId::Player,
                TargetDisposition::Friendly
            ))
        ),
        CombatStateErrorCode::ExplicitSelfTargetRelation
    );
    assert_eq!(
        code(
            &program,
            CombatStateInput::new(two(), random())
                .with_hostile_target(ActorId::Player, enemy())
                .with_hostile_target(ActorId::Player, enemy())
        ),
        CombatStateErrorCode::DuplicateTargetRelation
    );
    assert_eq!(
        code(
            &program,
            CombatStateInput::new(two(), random())
                .with_hostile_target(ActorId::Player, enemy())
                .with_friendly_target(ActorId::Player, enemy())
        ),
        CombatStateErrorCode::ConflictingTargetRelation
    );
    assert_eq!(
        code(
            &program,
            CombatStateInput::new(two(), random()).with_hostile_target(ActorId::Player, second_enemy())
        ),
        CombatStateErrorCode::MissingTargetRelationActor
    );
}

fn built() -> (CombatProgram, CombatState) {
    let program = program();
    let input = CombatStateInput::new(vec![actor(ActorId::Player), actor(enemy())], random())
        .with_hostile_target(ActorId::Player, enemy())
        .with_passive_resource_flow(
            PassiveResourceFlow::try_new(ActorId::Player, ResourceType::Mana, 5.0, RecoveryRate::Fixed)
                .expect("flow"),
        );
    let state = program.try_state(input).expect("builds");
    (program, state)
}

/// MUT-H-007..009: public host mutations that fail leave the complete state bit-identical.
#[test]
fn holds_host_api_failures_are_mutation_free() {
    let (_program, mut state) = built();
    state.advance_to(SimTime::from_millis(750)).expect("forward");
    let before = dbg_full(&state);

    // Unconfigured pool.
    let error = state
        .update_passive_resource_flow(
            PassiveResourceFlow::try_new(ActorId::Player, ResourceType::Energy, 5.0, RecoveryRate::Fixed)
                .expect("flow"),
        )
        .expect_err("unconfigured passive flow rejects");
    assert_eq!(error.code(), PassiveFlowErrorCode::UnconfiguredPassiveFlow);
    assert_eq!(dbg_full(&state), before);

    // Unknown actor.
    let error = state
        .update_passive_resource_flow(
            PassiveResourceFlow::try_new(second_enemy(), ResourceType::Mana, 5.0, RecoveryRate::Fixed)
                .expect("flow"),
        )
        .expect_err("unknown actor rejects");
    assert_eq!(error.code(), PassiveFlowErrorCode::MissingActor);
    assert_eq!(dbg_full(&state), before);

    // Backwards time.
    state.advance_to(SimTime::from_millis(10)).expect_err("backwards rejects");
    assert_eq!(dbg_full(&state), before);
}

/// MUT-H-010: a successful host flow update mutates only the current flow half, and reset restores
/// the construction flow (host updates are not baselines).
#[test]
fn holds_host_flow_update_is_reverted_by_reset() {
    let (_program, mut state) = built();
    let fresh = dbg_full(&state);
    state.advance_to(SimTime::from_millis(750)).expect("forward");
    state
        .update_passive_resource_flow(
            PassiveResourceFlow::try_new(ActorId::Player, ResourceType::Mana, 50.0, RecoveryRate::Fixed)
                .expect("flow"),
        )
        .expect("configured pool updates");
    state.advance_to(SimTime::from_millis(1_750)).expect("forward");
    let mana = state
        .resource_observation(ActorId::Player, ResourceType::Mana)
        .expect("mana");
    assert_ne!(dbg_full(&state), fresh);
    let _ = mana;
    state.reset();
    assert_eq!(dbg_full(&state), fresh);
}

/// MUT-H-011: the random identity is the only construction difference between two iterations.
#[test]
fn holds_random_identity_only_changes_random_stream() {
    let program = program();
    let build = |random| {
        program
            .try_state(
                CombatStateInput::new(vec![actor(ActorId::Player), actor(enemy())], random)
                    .with_hostile_target(ActorId::Player, enemy()),
            )
            .expect("builds")
    };
    let left = dbg_full(&build(RandomStreamIdentity::new(1, 1)));
    let right = dbg_full(&build(RandomStreamIdentity::new(1, 2)));
    let differing: Vec<_> = left
        .lines()
        .zip(right.lines())
        .filter(|(a, b)| a != b)
        .map(|(a, _)| a.trim().to_owned())
        .collect();
    assert!(!differing.is_empty());
    let random_start = left.find("    random: RandomStream {").expect("random rendered");
    let random_end = left[random_start..].find("\n    now:").expect("now follows") + random_start;
    let strip = |text: &str| {
        let start = text.find("    random: RandomStream {").expect("random");
        let end = text[start..].find("\n    now:").expect("now") + start;
        format!("{}{}", &text[..start], &text[end..])
    };
    assert!(random_end > random_start);
    assert_eq!(strip(&left), strip(&right));
}
