//! Track D probes: raw-300 Share Damage Percent attacker scope.
//!
//! Core copies only damage *from the aura source* (execution/planning/health.rs:1117-1122,
//! `key.source() != context.origin.source()` returns no share). Trinity `Unit::DealDamage`
//! (Unit.cpp:886-908) copies every attacker's school-matching damage on the victim to the
//! aura caster (`(*i)->GetCaster()`), with no attacker filter.

use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity};
use wowlab_model::{ActorId, AuraKey, DifficultyId, EnemyIndex, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{ShareDamageFixture, share_damage_inputs};

const fn identity() -> GameDataIdentity {
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
        Some(HealthPool::full(1_000.0).expect("valid pool")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

fn cast(state: &mut CombatState, source: ActorId, target: ActorId, spell: SpellId) -> Vec<CombatObservation> {
    let mut output = Vec::new();
    state
        .cast(CastRequest::new(source, target, spell), &mut output)
        .expect("cast succeeds");
    output
}

fn health(state: &CombatState, id: ActorId) -> f64 {
    state.actor(id).and_then(ActorState::health).map(|pool| pool.current()).expect("health")
}

/// REJ-D-005: the attacker-scope divergence is unreachable. With the exact Venomshade 154349
/// program compiled, state construction rejects any topology that could let a third actor
/// damage the aura holder (UnsupportedShareDamageTopology, 3 actors / 2 relations).
#[test]
fn holds_share_damage_topology_rejects_third_party_attackers() {
    let (_, data, actions) = share_damage_inputs(identity());
    let program = CombatProgram::builder(&data, &actions).build().expect("fixture compiles");
    let error = program
        .try_state(
            CombatStateInput::new(
                vec![actor(ActorId::Player), actor(ActorId::External), actor(enemy())],
                RandomStreamIdentity::new(300, 1),
            )
            .with_hostile_target(ActorId::Player, enemy())
            .with_hostile_target(ActorId::External, enemy()),
        )
        .expect_err("share-damage topology admits only the source/target pair");
    let rendered = format!("{error:?}");

    assert!(rendered.contains("UnsupportedShareDamageTopology"), "{rendered}");
}

/// Control: in the admitted two-actor topology the source's own hit is copied (100 -> Player).
#[test]
fn holds_share_damage_copies_the_sources_own_damage() {
    let fixture = ShareDamageFixture;
    let (_, data, actions) = share_damage_inputs(identity());
    let program = CombatProgram::builder(&data, &actions).build().expect("fixture compiles");
    let mut state = program
        .try_state(
            CombatStateInput::new(vec![actor(ActorId::Player), actor(enemy())], RandomStreamIdentity::new(300, 1))
                .with_hostile_target(ActorId::Player, enemy()),
        )
        .expect("valid state");

    cast(&mut state, ActorId::Player, enemy(), fixture.provider_spell());
    assert!(state.active_aura(AuraKey::new(fixture.provider_aura(), ActorId::Player, enemy())).is_some());
    cast(&mut state, ActorId::Player, enemy(), fixture.damage_spell());
    assert_eq!(health(&state, enemy()), 900.0);
    assert_eq!(health(&state, ActorId::Player), 900.0);
}
