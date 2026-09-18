//! Track D probes: equal-priority damage-absorb consumption order.
//!
//! Core orders candidates by funding, signed MiscValueB priority, then *earlier* application
//! sequence (crates/combat/src/absorb/transaction.rs:991-1000). Trinity registers each applied
//! absorb effect with `push_front` (Unit.cpp:3738) and `stable_sort`s by MiscValueB only
//! (Unit.cpp:1899-1900), so among equal priorities the *newest* application absorbs first.

use wowlab_combat::{
    ActorState, CastRequest, CastTargetRelation, CombatObservation, CombatProgram, CombatState,
    CombatStateInput, HasteMultipliers, HealthPool, OffensivePower, TargetDisposition,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, EffectAmountFactsInput, EffectChainFactsInput,
    EffectInput, GameData, GameDataIdentity, GameDataInput, ResolvedAbsorbInput,
    ResolvedActionCatalog, ResolvedActionRecipientInput, ResolvedActionTimingInput,
    ResolvedAuraInput, ResolvedAuraReapplicationInput, ResolvedProgramStepInput,
    ResolvedRootTargetInput, SpellInput,
};
use wowlab_dbc::{AuraSubtypeKind, ImplicitTargetKind, SpellEffectKind};
use wowlab_model::{ActorId, AuraId, AuraKey, DifficultyId, EnemyIndex, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{empty_resolved_action_catalog_input, resolved_action_program};

const DAMAGE: u32 = 910_001;
const FIRST: u32 = 910_100;
const SECOND: u32 = 910_101;
const FIRE: u8 = 0x04;

const fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 910_001,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn spell(id: u32, defense_type: u8) -> SpellInput {
    SpellInput {
        id,
        family_id: wowlab_data::SpellFamilyId::NONE,
        dispel_type: wowlab_data::DispelTypeId::NONE,
        school_mask: u32::from(FIRE),
        defense_type,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: None,
        attributes: [0; 17],
    }
}

fn effect(spell_id: u32, index: u8, kind: SpellEffectKind, subtype: i32, misc0: i32, base: f64, target: ImplicitTargetKind) -> EffectInput {
    EffectInput {
        spell_id,
        index,
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

fn program() -> CombatProgram {
    let mut spells = vec![spell(DAMAGE, 1)];
    let mut effects = vec![effect(DAMAGE, 1, SpellEffectKind::SchoolDamage, 0, 0, 60.0, ImplicitTargetKind::UnitTargetEnemy)];
    let mut input = empty_resolved_action_catalog_input(identity());
    let (action, program) = resolved_action_program(
        DAMAGE,
        ResolvedRootTargetInput::PrimaryTarget,
        Vec::new(),
        ResolvedActionTimingInput::default(),
        vec![ResolvedProgramStepInput::DirectDamage { effect_index: 1 }],
    );
    input.actions.push(action);
    input.programs.push(program);

    for raw in [FIRST, SECOND] {
        spells.push(spell(raw, 0));
        effects.push(effect(raw, 1, SpellEffectKind::ApplyAura, 0, 0, 0.0, ImplicitTargetKind::UnitCaster));
        // Equal MiscValueB priority (0) for both providers, capacity 50 each.
        effects.push(effect(
            raw,
            2,
            SpellEffectKind::ApplyAura,
            i32::from(AuraSubtypeKind::AbsorbDamage.raw()),
            i32::from(FIRE),
            50.0,
            ImplicitTargetKind::UnitCaster,
        ));
        input.auras.push(ResolvedAuraInput::finite(raw, 12_000, 1));
        input.absorb_family.providers.push(ResolvedAbsorbInput {
            aura_id: raw,
            effect_index: 2,
            application_effect_index: 1,
        });
        let (action, program) = resolved_action_program(
            raw,
            ResolvedRootTargetInput::Caster,
            Vec::new(),
            ResolvedActionTimingInput::default(),
            vec![ResolvedProgramStepInput::ApplyAura {
                recipient: ResolvedActionRecipientInput::Caster,
                effect_index: 1,
                aura_id: raw,
                stacks: 1,
                reapplication: ResolvedAuraReapplicationInput::RestartLifetime,
            }],
        );
        input.actions.push(action);
        input.programs.push(program);
    }

    let data = GameData::try_from_input(GameDataInput {
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: Vec::new(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        identity: identity(),
        power_types: Vec::new(),
        spells,
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects,
        effect_attributes: Vec::new(),
    })
    .expect("game data is valid");
    let actions = ResolvedActionCatalog::try_from_input(input).expect("catalog is valid");

    CombatProgram::builder(&data, &actions).build().expect("absorb program compiles")
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

fn key(raw: u32) -> AuraKey {
    AuraKey::new(AuraId::new(raw).expect("nonzero"), ActorId::Player, ActorId::Player)
}

/// CSA-D-03 (LIVE, direct-consumer divergence): with equal priority, Core depletes the FIRST
/// applied provider (oldest-first). Trinity's push_front + stable_sort would deplete SECOND.
#[test]
fn defect_equal_priority_absorbs_consume_oldest_application_first() {
    let program = program();
    let mut state = program
        .try_state(
            CombatStateInput::new(vec![actor(ActorId::Player), actor(enemy())], RandomStreamIdentity::new(9, 1))
                .with_relations(vec![CastTargetRelation::new(enemy(), ActorId::Player, TargetDisposition::Hostile)]),
        )
        .expect("valid state");

    cast(&mut state, ActorId::Player, ActorId::Player, FIRST);
    cast(&mut state, ActorId::Player, ActorId::Player, SECOND);
    cast(&mut state, enemy(), ActorId::Player, DAMAGE);

    assert!(state.active_aura(key(FIRST)).is_none(), "oldest provider depleted and removed");
    assert!(state.active_aura(key(SECOND)).is_some(), "newest provider survives with 40 left");
    assert_eq!(
        state.actor(ActorId::Player).and_then(ActorState::health).map(|pool| pool.current()),
        Some(1_000.0)
    );
}
