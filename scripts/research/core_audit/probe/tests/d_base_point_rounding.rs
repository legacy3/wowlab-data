//! Track D probes: base-point rounding across the direct health payload families.
//!
//! Source rows (wowlab-data 12.1.0.69497 `SpellEffect.csv`, base difficulty):
//! - 15278:0 "Seal of Reckoning Effect": Effect 10 (Heal), EffectBasePointsF 1.5, ImplicitTarget (1,0),
//!   neutral amount facts, SchoolMask 2, no attributes, one effect.
//! - 3243:0 "Life Harvest": Effect 9 (Health Leech), EffectBasePointsF 5.55555582047,
//!   EffectAmplitude 1, ImplicitTarget (6,0), SchoolMask 1, no attributes, one effect.
//! Trinity `SpellEffectInfo::CalcValue` (SpellInfo.cpp ~L611-627) applies `std::round` to
//! SCHOOL_DAMAGE, HEALTH_LEECH and HEAL alike; Core rounds only raw-2 School Damage.

use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, EffectAmountFactsInput, EffectChainFactsInput,
    EffectInput, GameData, GameDataIdentity, GameDataInput, ResolvedActionCatalog,
    ResolvedActionRecipientInput, ResolvedActionTimingInput, ResolvedProgramStepInput,
    ResolvedRootTargetInput, SpellInput,
};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{empty_resolved_action_catalog_input, resolved_action_program};

const HEAL: u32 = 15_278;
const LEECH: u32 = 3_243;
/// Synthetic raw-2 twin of the 15278 row (same 1.5 base, Holy, caster-irrelevant shape) used only
/// as the discriminating contrast.
const SCHOOL_DAMAGE_TWIN: u32 = 900_002;

const fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn spell(id: u32, school_mask: u32) -> SpellInput {
    SpellInput {
        id,
        family_id: wowlab_data::SpellFamilyId::NONE,
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask,
        defense_type: 0,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: Some(1),
        attributes: [0; 17],
    }
}

fn effect(spell_id: u32, kind: u32, base_points: f64, target_a: i32, amplitude: f32) -> EffectInput {
    EffectInput {
        amount_facts: EffectAmountFactsInput {
            amplitude,
            ..EffectAmountFactsInput::NEUTRAL
        },
        chain_facts: EffectChainFactsInput::NEUTRAL,
        spell_id,
        index: 1,
        kind,
        aura_subtype: 0,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: 0,
        misc_value_1: 0,
        radius_index_0: 0,
        radius_index_1: 0,
        implicit_target_a: target_a,
        implicit_target_b: 0,
        spell_class_mask: [0; 4],
        base_points,
        attack_power_coefficient: 0.0,
        spell_power_coefficient: 0.0,
        trigger_spell_id: None,
    }
}

fn program() -> CombatProgram {
    let data = GameData::try_from_input(GameDataInput {
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        power_types: Vec::new(),
        identity: identity(),
        spells: vec![spell(LEECH, 1), spell(HEAL, 2), spell(SCHOOL_DAMAGE_TWIN, 2)],
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effect_attributes: Vec::new(),
        effects: vec![
            effect(LEECH, 9, f64::from(5.555_555_820_47_f32), 6, 1.0),
            effect(HEAL, 10, 1.5, 1, 0.0),
            effect(SCHOOL_DAMAGE_TWIN, 2, 1.5, 6, 0.0),
        ],
    })
    .expect("source-shaped game data is valid");
    let mut input = empty_resolved_action_catalog_input(identity());
    let mut add = |spell_id, target, step| {
        let (action, program) = resolved_action_program(
            spell_id,
            target,
            Vec::new(),
            ResolvedActionTimingInput::default(),
            vec![step],
        );
        input.actions.push(action);
        input.programs.push(program);
    };
    add(
        HEAL,
        ResolvedRootTargetInput::Caster,
        ResolvedProgramStepInput::DirectHeal { effect_index: 1 },
    );
    add(
        LEECH,
        ResolvedRootTargetInput::PrimaryTarget,
        ResolvedProgramStepInput::DirectDamage { effect_index: 1 },
    );
    add(
        SCHOOL_DAMAGE_TWIN,
        ResolvedRootTargetInput::PrimaryTarget,
        ResolvedProgramStepInput::DirectDamage { effect_index: 1 },
    );
    // The heal program targets its caster: re-point its impact recipient at the caster.
    for program in &mut input.programs {
        if program.spell_id == HEAL {
            for impact in &mut program.impacts {
                impact.recipient = ResolvedActionRecipientInput::Caster;
            }
        }
    }
    let actions = ResolvedActionCatalog::try_from_input(input).expect("catalog is valid");

    CombatProgram::builder(&data, &actions)
        .build()
        .expect("source-shaped direct health programs compile")
}

fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn actor(id: ActorId, current: f64, maximum: f64) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::try_new(current, maximum).expect("valid pool")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

fn state(program: &CombatProgram) -> CombatState {
    program
        .try_state(
            CombatStateInput::new(
                vec![actor(ActorId::Player, 50.0, 100.0), actor(enemy(), 1_000.0, 1_000.0)],
                RandomStreamIdentity::new(3, 243),
            )
            .with_hostile_target(ActorId::Player, enemy()),
        )
        .expect("valid state")
}

fn health(state: &CombatState, id: ActorId) -> f64 {
    state.actor(id).and_then(ActorState::health).map(|pool| pool.current()).expect("health")
}

fn cast(state: &mut CombatState, raw: u32, target: ActorId) -> Vec<CombatObservation> {
    let mut output = Vec::new();
    state
        .cast(
            CastRequest::new(ActorId::Player, target, SpellId::new(raw).expect("nonzero")),
            &mut output,
        )
        .expect("cast succeeds");
    output
}

/// CSA-D-02 (LIVE): raw-10 Heal 15278:1 with authored 1.5 heals exactly 1.5 (Trinity: round -> 2).
#[test]
fn defect_direct_heal_keeps_fractional_base_points() {
    let program = program();
    let mut state = state(&program);

    cast(&mut state, HEAL, ActorId::Player);
    assert_eq!(health(&state, ActorId::Player), 51.5);
}

/// CSA-D-02 (LIVE): raw-9 Health Leech 3243:1 with authored 5.55555582047 removes and returns the
/// unrounded amount (Trinity: round -> 6 damage, 6 healing at amplitude 1).
#[test]
fn defect_direct_health_leech_keeps_fractional_base_points() {
    let program = program();
    let mut state = state(&program);
    let authored = f64::from(5.555_555_820_47_f32);

    cast(&mut state, LEECH, enemy());
    assert_eq!(health(&state, enemy()), 1_000.0 - authored);
    assert_eq!(health(&state, ActorId::Player), 50.0 + authored);
}

/// Contrast for CSA-D-02: the raw-2 twin with the identical 1.5 base deals 2 (Core rounds raw-2
/// base points in `DamageFormula::new_direct_school_damage`).
#[test]
fn holds_direct_school_damage_rounds_the_same_base_points() {
    let program = program();
    let mut state = state(&program);

    cast(&mut state, SCHOOL_DAMAGE_TWIN, enemy());
    assert_eq!(health(&state, enemy()), 998.0);
}
