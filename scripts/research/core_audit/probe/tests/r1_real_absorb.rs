//! Reviewer R1 (false positives): real 12.1.0.69497 source-row witnesses for CSA-D-03 and
//! CSA-D-04 (the track probes use synthetic 910_xxx / 920_xxx ids).
//!
//! Exact rows (SpellMisc school/attributes/duration, SpellCategories DefenseType/DispelType,
//! SpellEffect kind/aura/base points/misc/targets; all attribute words are 0 except 334538 and
//! 256374 attribute 268 "Aura Points On Client", catalog Ignored):
//! - Herbalist's Ward 53678:0 raw-69 capacity 1, misc 8 (Nature), (1,0), 30 000 ms.
//! - Deaden Magic 334538:0 raw-69 capacity 50, misc 126, (1,0), 12 000 ms, DispelType 1.
//! - Entropic Embrace 256374 (4 effects: raw 79, raw 257, raw 136, raw 421 +5), 12 000 ms — Core's
//!   own reproduced raw-421 carrier witness (aura_subtype.rs:561).
//! - Storm Cloud 57411:0 raw-2 Nature 2.455 (rounds to 2), (6,0); Wrath of the Titans 228917:0
//!   raw-2 Nature 83.963 (rounds to 84), (6,0); both DefenseType 1.
//!
//! Host-side deviations: all actions are declared instant (334538's cast time does not bear on
//! absorb order/capacity); the Magic hit table has no miss input (guaranteed landing).

use wowlab_combat::{
    ActorState, CastRequest, CastTargetRelation, CombatObservation, CombatProgram, CombatState,
    CombatStateInput, HasteMultipliers, HealthPool, OffensivePower, TargetDisposition,
};
use wowlab_data::{
    AbsorbCapacityCompositionInput, AbsorbCapacityModifierInput, CURRENT_SCHEMA_VERSION,
    DataVersion, DispelTypeId, EffectAmountFactsInput, EffectChainFactsInput, EffectInput,
    GameData, GameDataIdentity, GameDataInput, HealingModifierActivationInput, ResolvedAbsorbInput,
    ResolvedDamageModifierInput, ResolvedHealingModifierInput, ResolvedActionCatalog,
    ResolvedActionRecipientInput, ResolvedActionTimingInput, ResolvedAuraInput,
    ResolvedAuraReapplicationInput, ResolvedProgramStepInput, ResolvedRootTargetInput,
    SpellDurationPresenceInput, SpellFamilyId, SpellInput,
};
use wowlab_model::{ActorId, AuraId, AuraKey, DifficultyId, EnemyIndex, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{empty_resolved_action_catalog_input, resolved_action_program};

const HERBALISTS_WARD: u32 = 53_678;
const DEADEN_MAGIC: u32 = 334_538;
const ENTROPIC_EMBRACE: u32 = 256_374;
const STORM_CLOUD: u32 = 57_411;
const WRATH_OF_THE_TITANS: u32 = 228_917;
const ATTRIBUTE_268: [i32; 17] = [0, 0, 0, 0, 0, 0, 0, 0, 4096, 0, 0, 0, 0, 0, 0, 0, 0];

fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

fn spell(id: u32, school: u32, defense: u8, dispel: u8, effects: u8, attributes: [i32; 17]) -> SpellInput {
    SpellInput {
        id,
        family_id: SpellFamilyId::NONE,
        dispel_type: DispelTypeId::from_raw(dispel),
        school_mask: school,
        defense_type: defense,
        mechanic: 0,
        all_effect_mechanic_mask: [0; 2],
        source_effect_count: Some(effects),
        attributes,
    }
}

#[allow(clippy::too_many_arguments)]
fn effect(spell_id: u32, index: u8, kind: u32, aura: i32, misc: i32, points: f64, target: i32) -> EffectInput {
    EffectInput {
        spell_id,
        index,
        kind,
        aura_subtype: aura,
        aura_period_ms: 0,
        mechanic: 0,
        misc_value_0: misc,
        misc_value_1: 0,
        radius_index_0: 0,
        radius_index_1: 0,
        implicit_target_a: target,
        implicit_target_b: 0,
        spell_class_mask: [0; 4],
        base_points: points,
        attack_power_coefficient: 0.0,
        spell_power_coefficient: 0.0,
        chain_facts: EffectChainFactsInput::NEUTRAL,
        amount_facts: EffectAmountFactsInput::NEUTRAL,
        trigger_spell_id: None,
    }
}

fn apply(aura: u32, effect_index: u8) -> ResolvedProgramStepInput {
    ResolvedProgramStepInput::ApplyAura {
        recipient: ResolvedActionRecipientInput::Caster,
        effect_index,
        aura_id: aura,
        stacks: 1,
        reapplication: ResolvedAuraReapplicationInput::RestartLifetime,
    }
}

fn program() -> CombatProgram {
    let data = GameData::try_from_input(GameDataInput {
        identity: identity(),
        power_types: Vec::new(),
        spells: vec![
            spell(HERBALISTS_WARD, 8, 1, 0, 1, [0; 17]),
            spell(DEADEN_MAGIC, 32, 0, 1, 1, ATTRIBUTE_268),
            spell(ENTROPIC_EMBRACE, 48, 0, 0, 4, ATTRIBUTE_268),
            spell(STORM_CLOUD, 8, 1, 1, 1, [0; 17]),
            spell(WRATH_OF_THE_TITANS, 8, 1, 0, 1, [0; 17]),
        ],
        spell_aura_options: Vec::new(),
        spell_aura_restrictions: Vec::new(),
        spell_duration_presence: [HERBALISTS_WARD, DEADEN_MAGIC, ENTROPIC_EMBRACE]
            .map(|spell_id| SpellDurationPresenceInput { spell_id })
            .into(),
        spell_shapeshifts: Vec::new(),
        spell_equipment_requirements: Vec::new(),
        spell_class_masks: Vec::new(),
        spell_labels: Vec::new(),
        spell_missiles: Vec::new(),
        effects: vec![
            effect(HERBALISTS_WARD, 1, 6, 69, 8, 1.0, 1),
            effect(DEADEN_MAGIC, 1, 6, 69, 126, 50.0, 1),
            effect(ENTROPIC_EMBRACE, 1, 6, 79, 127, 5.0, 1),
            effect(ENTROPIC_EMBRACE, 2, 6, 257, 839, 0.0, 1),
            effect(ENTROPIC_EMBRACE, 3, 6, 136, 127, 5.0, 1),
            effect(ENTROPIC_EMBRACE, 4, 6, 421, 0, 5.0, 1),
            effect(STORM_CLOUD, 1, 2, 0, 0, 2.455_285_549_16, 6),
            effect(WRATH_OF_THE_TITANS, 1, 2, 0, 0, 83.963_386_535_64, 6),
        ],
        effect_attributes: Vec::new(),
    })
    .expect("real rows are valid game data");

    let mut input = empty_resolved_action_catalog_input(identity());
    input.auras = vec![
        ResolvedAuraInput::finite(HERBALISTS_WARD, 30_000, 1),
        ResolvedAuraInput::finite(DEADEN_MAGIC, 12_000, 1),
        ResolvedAuraInput::finite(ENTROPIC_EMBRACE, 12_000, 1),
    ];
    for aura_id in [HERBALISTS_WARD, DEADEN_MAGIC] {
        input.absorb_family.providers.push(ResolvedAbsorbInput {
            aura_id,
            effect_index: 1,
            application_effect_index: 1,
        });
    }
    // Entropic Embrace siblings declared as the same aura's ordinary modifiers (effects 1 and 3).
    input.damage_modifiers.push(ResolvedDamageModifierInput::Aura {
        aura_id: ENTROPIC_EMBRACE,
        effect_index: 1,
        application_effect_index: 4,
    });
    input.healing_modifiers.push(ResolvedHealingModifierInput {
        spell_id: ENTROPIC_EMBRACE,
        effect_index: 3,
        activation: HealingModifierActivationInput::Aura {
            application_effect_index: 4,
        },
    });
    input
        .absorb_family
        .source_capacity_modifiers
        .push(AbsorbCapacityModifierInput {
            aura_id: ENTROPIC_EMBRACE,
            effect_index: 4,
            application_effect_index: 4,
            composition: AbsorbCapacityCompositionInput::Multiplicative,
        });
    for (raw, target, step) in [
        (HERBALISTS_WARD, ResolvedRootTargetInput::Caster, apply(HERBALISTS_WARD, 1)),
        (DEADEN_MAGIC, ResolvedRootTargetInput::Caster, apply(DEADEN_MAGIC, 1)),
        (ENTROPIC_EMBRACE, ResolvedRootTargetInput::Caster, apply(ENTROPIC_EMBRACE, 4)),
        (
            STORM_CLOUD,
            ResolvedRootTargetInput::PrimaryTarget,
            ResolvedProgramStepInput::DirectDamage { effect_index: 1 },
        ),
        (
            WRATH_OF_THE_TITANS,
            ResolvedRootTargetInput::PrimaryTarget,
            ResolvedProgramStepInput::DirectDamage { effect_index: 1 },
        ),
    ] {
        let (action, program) = resolved_action_program(
            raw,
            target,
            Vec::new(),
            ResolvedActionTimingInput::default(),
            vec![step],
        );
        input.actions.push(action);
        input.programs.push(program);
    }
    let actions = ResolvedActionCatalog::try_from_input(input).expect("real-row actions");
    CombatProgram::builder(&data, &actions)
        .build()
        .expect("real absorb rows compile")
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
        Some(HealthPool::try_new(1_000.0, 1_000.0).expect("pool")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("actor")
}

fn state() -> CombatState {
    program()
        .try_state(
            CombatStateInput::new(
                vec![actor(ActorId::Player), actor(enemy())],
                RandomStreamIdentity::new(31, 1),
            )
            .with_relations(vec![CastTargetRelation::new(
                enemy(),
                ActorId::Player,
                TargetDisposition::Hostile,
            )]),
        )
        .expect("state")
}

fn cast(state: &mut CombatState, source: ActorId, target: ActorId, raw: u32) -> Vec<CombatObservation> {
    let mut output = Vec::new();
    state
        .cast(
            CastRequest::new(source, target, SpellId::new(raw).expect("nonzero")),
            &mut output,
        )
        .expect("cast succeeds");
    output
}

fn key(raw: u32) -> AuraKey {
    AuraKey::new(AuraId::new(raw).expect("nonzero"), ActorId::Player, ActorId::Player)
}

fn player_health(state: &CombatState) -> f64 {
    state
        .actor(ActorId::Player)
        .and_then(ActorState::health)
        .map(|pool| pool.current())
        .expect("health")
}

/// CSA-D-03 real-row witness (R1: LIVE confirmed on real rows; status stays open for the lead's
/// order-policy decision). Ward (1) then Deaden Magic (50), both priority 0, then a real Nature
/// hit of 2: Core depletes the older Ward (removed) and takes 1 from Deaden; Trinity's
/// push_front + stable_sort by MiscValueB (Unit.cpp:3738, 1899-1900) would take 2 from Deaden
/// and leave the Ward active.
#[test]
fn defect_real_equal_priority_absorbs_deplete_oldest_first() {
    let mut state = state();
    cast(&mut state, ActorId::Player, ActorId::Player, HERBALISTS_WARD);
    cast(&mut state, ActorId::Player, ActorId::Player, DEADEN_MAGIC);
    cast(&mut state, enemy(), ActorId::Player, STORM_CLOUD);
    assert!(state.active_aura(key(HERBALISTS_WARD)).is_none(), "older Ward depleted first");
    assert!(state.active_aura(key(DEADEN_MAGIC)).is_some());
    assert_eq!(player_health(&state), 1_000.0);
}

/// CSA-D-04 real-row witness (R1 upholds LIVE): Entropic Embrace raw-421 +5 on the source, then
/// Deaden Magic 50 -> continuous capacity 52.5; a real 84 Nature hit removes 31.5 health. Trinity
/// SpellAbsorbBonusDone rounds the source stage (Unit.cpp:7684-7686): capacity 53, 31 health.
#[test]
fn defect_real_source_only_absorb_capacity_is_not_rounded() {
    let mut state = state();
    cast(&mut state, ActorId::Player, ActorId::Player, ENTROPIC_EMBRACE);
    cast(&mut state, ActorId::Player, ActorId::Player, DEADEN_MAGIC);
    cast(&mut state, enemy(), ActorId::Player, WRATH_OF_THE_TITANS);
    assert_eq!(player_health(&state), 1_000.0 - (84.0 - 52.5));
}

/// Control for the D-04 witness: without the raw-421 source the same rows remove exactly 34.
#[test]
fn holds_real_absorb_without_source_modifier_is_integral() {
    let mut state = state();
    cast(&mut state, ActorId::Player, ActorId::Player, DEADEN_MAGIC);
    cast(&mut state, enemy(), ActorId::Player, WRATH_OF_THE_TITANS);
    assert_eq!(player_health(&state), 1_000.0 - 34.0);
}
