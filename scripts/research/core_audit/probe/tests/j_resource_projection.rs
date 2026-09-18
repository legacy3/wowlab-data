//! Track J probe: rotation resource-threshold wake arithmetic vs combat passive-flow arithmetic.
//! Rotation extrapolates from the value observed at `now`: `value + rate * (delta_ms / 1000)`
//! (rotation/src/projection.rs:89-100, execution.rs:943-958). Combat projects from the flow basis:
//! `basis + rate / 1000 * elapsed_ms` (combat/src/passive_resource_flow.rs:973-1000). The two
//! f64 evaluations disagree at exact integral crossings, so the rotation wakes 1 ms after the
//! millisecond at which combat already holds the threshold.

use googletest::prelude::*;
use wowlab_combat::{
    ActorState, CombatProgram, CombatStateInput, HasteMultipliers, HealthPool, OffensivePower,
    PassiveResourceFlow, ResourcePool,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, GameData, GameDataIdentity, GameDataInput,
    ResolvedActionCatalog, ResolvedActionInput, ResolvedActionTimingInput, ResolvedCooldownInput,
    ResolvedCooldownSlotInput, ResolvedRecoveryInput, ResolvedRootTargetInput,
    ResolvedSpellProgramInput, SpellInput,
};
use wowlab_engine::{AdvanceStatus, Engine, EngineInput, EngineProgram, StepOutcome};
use wowlab_model::{
    ActorId, DifficultyId, EnemyIndex, RecoveryRate, RecoveryRatePolicy, ResourceType, SimTime,
    SpellId,
};
use wowlab_rotation::{Action, ComparisonOperator, Expression, FieldRead, Identifier, Rotation};
use wowlab_sim::RandomStreamIdentity;

const PRIMARY: u32 = 101;
const FALLBACK: u32 = 102;
const PRIMARY_SLOT: u32 = 101;
const FALLBACK_SLOT: u32 = 102;

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn target() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn spell_input(id: u32) -> SpellInput {
    SpellInput {
        id,
        family_id: wowlab_data::SpellFamilyId::NONE,
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: 1,
        defense_type: 0,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: None,
        attributes: [0; 17],
    }
}

fn action(spell_id: u32, cooldowns: Vec<ResolvedCooldownInput>) -> ResolvedActionInput {
    ResolvedActionInput {
        spell_id,
        root_program_id: spell_id,
        target: ResolvedRootTargetInput::Caster,
        resource_spends: Vec::new(),
        cast_policy: wowlab_data::ResolvedCastPolicyInput::default(),
        timing: ResolvedActionTimingInput {
            ordinary_category_id: None,
            cooldowns,
            ..ResolvedActionTimingInput::default()
        },
    }
}

fn program() -> Result<CombatProgram> {
    let data = GameData::try_from_input(GameDataInput {
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        identity: identity(),
        power_types: Vec::new(),
        spells: vec![spell_input(PRIMARY), spell_input(FALLBACK)],
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects: Vec::new(),
        effect_attributes: Vec::new(),
    })?;
    let mut input = wowlab_test_support::empty_resolved_action_catalog_input(identity());

    for (spell_id, slot_id) in [(PRIMARY, PRIMARY_SLOT), (FALLBACK, FALLBACK_SLOT)] {
        input
            .cooldown_slots
            .push(ResolvedCooldownSlotInput { slot_id });
        input.actions.push(action(
            spell_id,
            vec![ResolvedCooldownInput::Private {
                slot_id,
                start: Some(ResolvedRecoveryInput {
                    base_duration_ms: 1,
                    rate: RecoveryRate::Fixed,
                    rate_policy: RecoveryRatePolicy::Snapshot,
                }),
            }],
        ));
    }
    for spell_id in [PRIMARY, FALLBACK] {
        input.programs.push(ResolvedSpellProgramInput {
            node_id: spell_id,
            spell_id,
            activation_steps: Vec::new(),
            impacts: Vec::new(),
        });
    }
    let actions = ResolvedActionCatalog::try_from_input(input)?;

    Ok(CombatProgram::builder(&data, &actions).build()?)
}

fn energy(operator: ComparisonOperator, value: f64) -> Expression {
    Expression::Compare {
        operator,
        left: Box::new(Expression::Read {
            field: FieldRead {
                domain: Identifier::new("resource").expect("domain"),
                field: Identifier::new("current").expect("field"),
                key: Some(Identifier::new("energy").expect("key")),
            },
        }),
        right: Box::new(Expression::Number { value }),
    }
}

fn cast(name: &str, condition: Expression) -> Action {
    Action::Cast {
        spell: Identifier::new(name).expect("name"),
        empower_rank: None,
        enabled: true,
        condition: Some(condition),
        target: None,
    }
}

fn actors() -> Vec<ActorState> {
    vec![
        ActorState::try_new(
            ActorId::Player,
            vec![ResourcePool::try_new(ResourceType::Energy, 0.0, 100.0).expect("energy")],
            Some(HealthPool::full(100.0).expect("health")),
            OffensivePower::ZERO,
            HasteMultipliers::UNHASTED,
        )
        .expect("player"),
        ActorState::try_new(
            target(),
            Vec::new(),
            Some(HealthPool::full(100.0).expect("health")),
            OffensivePower::ZERO,
            HasteMultipliers::UNHASTED,
        )
        .expect("target"),
    ]
}

fn flow() -> PassiveResourceFlow {
    PassiveResourceFlow::try_new(ActorId::Player, ResourceType::Energy, 10.0, RecoveryRate::Fixed)
        .expect("flow")
}

/// NUM-J-060 / CSA-J-07: energy 0/100 regenerating 10/s from t=0. Rotation:
/// `[fallback if energy >= 50, primary if energy < 0.005]`; primary has a 1 ms cooldown, so the
/// final evaluation before the crossing happens at now = 1 ms (energy 0.01).
/// Combat: 0 + 10/1000 * 5000 = 50.0 at t = 5000 (threshold held).
/// Rotation: 0.01 + 10 * (4999/1000) = 49.99999999999999 < 50, so it wakes at 5001.
#[gtest]
fn defect_rotation_resource_wake_is_one_ms_after_combat_crossing() -> Result<()> {
    // Combat truth at 5000 ms through the public CombatState API.
    let mut state = program()?.try_state(
        CombatStateInput::new(actors(), RandomStreamIdentity::new(7, 11))
            .with_passive_resource_flow(flow()),
    )?;
    state.advance_to(SimTime::from_millis(5_000))?;
    let at_5000 = state
        .resource_observation(ActorId::Player, ResourceType::Energy)
        .or_fail()?
        .projected();
    verify_true!(at_5000 >= 50.0)?;
    verify_true!(0.01 + 10.0 * (4_999.0 / 1_000.0) < 50.0)?;

    let mut rotation = Rotation::new("J resource projection".to_owned());
    rotation.actions = vec![
        cast("fallback", energy(ComparisonOperator::GreaterThanOrEqual, 50.0)),
        cast("primary", energy(ComparisonOperator::LessThan, 0.005)),
    ];
    let engine_program = EngineProgram::try_new(program()?, &rotation, &|name: &Identifier| {
        match name.as_str() {
            "primary" => SpellId::new(PRIMARY),
            "fallback" => SpellId::new(FALLBACK),
            _ => None,
        }
    })?;
    let mut engine = Engine::try_new(
        engine_program,
        EngineInput::new(actors()).with_passive_resource_flow(flow()),
        ActorId::Player,
        target(),
        RandomStreamIdentity::new(7, 11),
    )?;

    let StepOutcome::Cast(first) = engine.step()? else {
        return fail!("primary must cast at t=0");
    };
    verify_eq!(first.decision().spell(), SpellId::new(PRIMARY).or_fail()?)?;

    let mut fallback_at = None;
    for _ in 0..8 {
        let outcome = engine.advance()?;
        let finished = outcome.finished_at();

        if let AdvanceStatus::Cast(decision) = outcome.status()
            && decision.spell() == SpellId::new(FALLBACK).or_fail()?
        {
            fallback_at = Some(finished);
            break;
        }
    }

    verify_eq!(fallback_at, Some(SimTime::from_millis(5_001)))?;

    // The in-engine flow basis was never rebased (costless casts): its value at 5001 is exactly
    // the one-step basis projection, so the same combat projection already held 50.0 at 5000.
    let at_5001 = engine
        .combat_state()
        .resource_observation(ActorId::Player, ResourceType::Energy)
        .or_fail()?
        .projected();

    verify_eq!(at_5001.to_bits(), (10.0_f64 / 1_000.0 * 5_001.0).to_bits())
}
