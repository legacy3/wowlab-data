//! Track D probes: school-absorb creation capacity rounding with a source raw-421 modifier.
//!
//! Core: `application_capacity` (crates/combat/src/absorb/transaction.rs:910-925) keeps the
//! continuous `base * source_multiplier` when the recipient raw-422 factor is exactly 1.0, and
//! rounds twice only when a non-identity raw-422 factor participates. Trinity
//! `SpellAbsorbBonusDone` returns `int32(std::round(...))` (Unit.cpp:7684-7686) on every creation,
//! before `SpellAbsorbBonusTaken` rounds again (Unit.cpp:7715-7719); Core's own ledger (~L25988-26003)
//! states the direct source rounds the source stage and defers the source-only path.

use wowlab_combat::{
    ActorState, CastRequest, CastTargetRelation, CombatObservation, CombatProgram, CombatState,
    CombatStateInput, HasteMultipliers, HealthPool, OffensivePower, TargetDisposition,
};
use wowlab_data::{
    AbsorbCapacityCompositionInput, AbsorbCapacityModifierInput, CURRENT_SCHEMA_VERSION,
    DataVersion, EffectAmountFactsInput, EffectChainFactsInput, EffectInput, GameData,
    GameDataIdentity, GameDataInput, ResolvedAbsorbInput, ResolvedActionCatalog,
    ResolvedActionRecipientInput, ResolvedActionTimingInput, ResolvedAuraInput,
    ResolvedAuraReapplicationInput, ResolvedProgramStepInput, ResolvedRootTargetInput, SpellInput,
};
use wowlab_dbc::{AuraSubtypeKind, ImplicitTargetKind, SpellEffectKind};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{empty_resolved_action_catalog_input, resolved_action_program};

const DAMAGE: u32 = 920_001;
const ABSORB: u32 = 920_100;
const DONE_MODIFIER: u32 = 920_200;

const fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 920_001,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn spell(id: u32, defense_type: u8) -> SpellInput {
    SpellInput {
        id,
        family_id: wowlab_data::SpellFamilyId::NONE,
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: 0x4,
        defense_type,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: None,
        attributes: [0; 17],
    }
}

fn effect(spell_id: u32, kind: SpellEffectKind, subtype: i32, misc0: i32, base: f64, target: ImplicitTargetKind) -> EffectInput {
    EffectInput {
        spell_id,
        index: 1,
        kind: u32::from(kind.raw()),
        aura_subtype: subtype,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: misc0,
        misc_value_1: 0,
        radius_index_0: 0,
        radius_index_1: 0,
        implicit_target_a: target.raw(),
        implicit_target_b: 0,
        spell_class_mask: [0; 4],
        base_points: base,
        attack_power_coefficient: 0.0,
        spell_power_coefficient: 0.0,
        chain_facts: EffectChainFactsInput::NEUTRAL,
        amount_facts: EffectAmountFactsInput::NEUTRAL,
        trigger_spell_id: None,
    }
}

fn apply(raw: u32) -> ResolvedProgramStepInput {
    ResolvedProgramStepInput::ApplyAura {
        recipient: ResolvedActionRecipientInput::Caster,
        effect_index: 1,
        aura_id: raw,
        stacks: 1,
        reapplication: ResolvedAuraReapplicationInput::RestartLifetime,
    }
}

fn program(capacity: f64, done_percent: f64) -> CombatProgram {
    let data = GameData::try_from_input(GameDataInput {
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        identity: identity(),
        power_types: Vec::new(),
        spells: vec![spell(DAMAGE, 1), spell(ABSORB, 0), spell(DONE_MODIFIER, 0)],
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects: vec![
            effect(DAMAGE, SpellEffectKind::SchoolDamage, 0, 0, 100.0, ImplicitTargetKind::UnitTargetEnemy),
            effect(ABSORB, SpellEffectKind::ApplyAura, i32::from(AuraSubtypeKind::AbsorbDamage.raw()), 0x4, capacity, ImplicitTargetKind::UnitCaster),
            effect(DONE_MODIFIER, SpellEffectKind::ApplyAura, i32::from(AuraSubtypeKind::ModAbsorbDonePercent.raw()), 0, done_percent, ImplicitTargetKind::UnitCaster),
        ],
        effect_attributes: Vec::new(),
    })
    .expect("game data is valid");
    let mut input = empty_resolved_action_catalog_input(identity());
    input.auras = vec![
        ResolvedAuraInput::finite(ABSORB, 12_000, 1),
        ResolvedAuraInput::finite(DONE_MODIFIER, 12_000, 1),
    ];
    input.absorb_family.providers.push(ResolvedAbsorbInput {
        aura_id: ABSORB,
        effect_index: 1,
        application_effect_index: 1,
    });
    input.absorb_family.source_capacity_modifiers.push(AbsorbCapacityModifierInput {
        aura_id: DONE_MODIFIER,
        effect_index: 1,
        application_effect_index: 1,
        composition: AbsorbCapacityCompositionInput::Multiplicative,
    });
    for (raw, target, step) in [
        (DAMAGE, ResolvedRootTargetInput::PrimaryTarget, ResolvedProgramStepInput::DirectDamage { effect_index: 1 }),
        (ABSORB, ResolvedRootTargetInput::Caster, apply(ABSORB)),
        (DONE_MODIFIER, ResolvedRootTargetInput::Caster, apply(DONE_MODIFIER)),
    ] {
        let (action, program) =
            resolved_action_program(raw, target, Vec::new(), ResolvedActionTimingInput::default(), vec![step]);
        input.actions.push(action);
        input.programs.push(program);
    }
    let actions = ResolvedActionCatalog::try_from_input(input).expect("catalog is valid");

    CombatProgram::builder(&data, &actions).build().expect("raw-69 + raw-421 program compiles")
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
        Some(HealthPool::try_new(1_000.0, 1_000.0).expect("valid pool")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("valid actor")
}

fn cast(state: &mut CombatState, source: ActorId, target: ActorId, raw: u32) -> Vec<CombatObservation> {
    let mut output = Vec::new();
    state
        .cast(CastRequest::new(source, target, SpellId::new(raw).expect("nonzero")), &mut output)
        .expect("cast succeeds");
    output
}

fn player_health_after_hit(capacity: f64, done_percent: f64) -> f64 {
    let program = program(capacity, done_percent);
    let mut state = program
        .try_state(
            CombatStateInput::new(vec![actor(ActorId::Player), actor(enemy())], RandomStreamIdentity::new(9, 2))
                .with_relations(vec![CastTargetRelation::new(enemy(), ActorId::Player, TargetDisposition::Hostile)]),
        )
        .expect("valid state");

    cast(&mut state, ActorId::Player, ActorId::Player, DONE_MODIFIER);
    cast(&mut state, ActorId::Player, ActorId::Player, ABSORB);
    cast(&mut state, enemy(), ActorId::Player, DAMAGE);
    state.actor(ActorId::Player).and_then(ActorState::health).map(|pool| pool.current()).expect("health")
}

/// CSA-D-04 (LIVE): 55 capacity x (1 + 5 %) creates a continuous 57.75 capacity, so a 100 hit
/// removes 42.25 health. The source-stage rounding Core documents for the direct source
/// (round(57.75) = 58) would remove 42.
#[test]
fn defect_source_only_absorb_capacity_is_not_rounded() {
    let health = player_health_after_hit(55.0, 5.0);
    assert_eq!(health, 1_000.0 - (100.0 - 55.0 * 1.05));
    assert_ne!(health, 1_000.0 - (100.0 - (55.0_f64 * 1.05).round()));
}
