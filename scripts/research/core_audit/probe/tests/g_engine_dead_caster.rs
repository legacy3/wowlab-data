//! Track G probe: Engine-level reachability of CSA-G-01 (dead caster keeps acting).
//!
//! Uses Core's own exact share-damage fixture: the Player applies the damage-sharing provider
//! to the enemy, then each damage cast copies its damage onto the Player.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CombatObservation, CombatProgram, HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity};
use wowlab_engine::{ContentResolver, Engine, EngineInput, EngineProgram, StepOutcome};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex};
use wowlab_rotation::{Action, Expression, FieldRead, Identifier, Rotation};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{ShareDamageFixture, share_damage_inputs};

const fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

const fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 1_201_069_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn identifier(name: &str) -> Identifier {
    Identifier::new(name).expect("probe identifier is canonical")
}

fn actor(id: ActorId, health: f64) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(health).expect("probe health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("probe actor")
}

fn cast_action(name: &str, condition: Option<Expression>) -> Action {
    Action::Cast {
        spell: identifier(name),
        empower_rank: None,
        enabled: true,
        condition,
        target: None,
    }
}

struct Resolver(ShareDamageFixture);

impl ContentResolver for Resolver {
    fn resolve_spell(&self, spell: &Identifier) -> Option<wowlab_model::SpellId> {
        match spell.as_str() {
            "provider" => Some(self.0.provider_spell()),
            "damage" => Some(self.0.damage_spell()),
            _ => None,
        }
    }

    fn resolve_aura(&self, aura: &Identifier) -> Option<wowlab_model::AuraId> {
        (aura.as_str() == "provider").then(|| self.0.provider_aura())
    }
}

fn player_health(engine: &Engine) -> f64 {
    engine
        .combat_state()
        .actor(ActorId::Player)
        .and_then(ActorState::health)
        .map(|pool| pool.current())
        .expect("player has health")
}

/// CSA-G-01 witness at Engine level: after copied damage kills the Player, the Engine's
/// rotation keeps casting for the dead Player and the damage still lands on the enemy.
#[gtest]
fn defect_engine_rotation_keeps_casting_for_dead_player() -> Result<()> {
    let fixture = ShareDamageFixture;
    let (_, data, actions) = share_damage_inputs(identity());
    let combat = CombatProgram::builder(&data, &actions).build().or_fail()?;
    let mut rotation = Rotation::new("probe share damage".to_owned());

    rotation.actions = vec![
        cast_action(
            "provider",
            Some(Expression::Read {
                field: FieldRead {
                    domain: identifier("target_aura"),
                    field: identifier("is_inactive"),
                    key: Some(identifier("provider")),
                },
            }),
        ),
        cast_action("damage", None),
    ];

    let program = EngineProgram::try_new(combat, &rotation, &Resolver(fixture)).or_fail()?;
    let mut engine = Engine::try_new(
        program,
        EngineInput::new(vec![actor(ActorId::Player, 50.0), actor(enemy(), 10_000.0)]),
        ActorId::Player,
        enemy(),
        RandomStreamIdentity::new(300, 77),
    )
    .or_fail()?;

    // provider, then the first damage kills the Player through the copied damage.
    let mut player_died = false;
    let mut casts_after_death = 0_usize;
    let mut enemy_damage_after_death = 0_usize;

    for _ in 0..4 {
        let StepOutcome::Cast(_) = engine.step().or_fail()? else {
            break;
        };
        let observations = engine.last_observations();

        if player_died {
            casts_after_death += observations
                .iter()
                .filter(|o| matches!(o, CombatObservation::SpellCast { caster: ActorId::Player, .. }))
                .count();
            enemy_damage_after_death += observations
                .iter()
                .filter(|o| matches!(
                    o,
                    CombatObservation::DamageDealt { source: ActorId::Player, target, .. } if *target == enemy()
                ))
                .count();
        }

        player_died |= observations
            .iter()
            .any(|o| matches!(o, CombatObservation::ActorDied { actor: ActorId::Player, .. }));
    }

    verify_that!(player_died, eq(true))?;
    verify_eq!(player_health(&engine), 0.0)?;
    verify_that!(casts_after_death, ge(1))?;
    verify_that!(enemy_damage_after_death, ge(1))
}
