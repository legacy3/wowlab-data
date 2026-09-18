//! Track D probes: raw-40 damage-immunity plan vs raw-62 Power Burn damage (REJ-D evidence).
//!
//! Suspicion: `damage_immunity::audit_compiled_damage_payloads`
//! (crates/combat/src/program/damage_immunity.rs:537-580) does not enumerate `ProgramOp::PowerBurn`,
//! whose receipt damage uses the same `prepare_damage_quote` immunity check
//! (execution/planning/health.rs:899-917). Disproved: `compile_pool_plans`
//! (crates/combat/src/program/compile.rs:833-855) rejects Power Burn whenever any immunity family
//! (damage, effect, mechanic, school) is declared.

use wowlab_combat::CombatProgram;
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, GameData, GameDataIdentity, ResolvedActionCatalog,
};
use wowlab_model::DifficultyId;
use wowlab_test_support::{
    damage_immunity_action_input, damage_immunity_game_data_input, power_burn_source_inputs,
};

const fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

/// REJ-D-004: exact Ethereal (163671:1) + Burn Mana (46266:1) is rejected at compile time with
/// the Power Burn `UnsupportedImmunityPlan { family: "damage" }` gate.
#[test]
fn holds_power_burn_rejects_any_declared_immunity_family() {
    let mut data = damage_immunity_game_data_input(identity());
    let mut actions = damage_immunity_action_input(identity());
    let (_, burn) = power_burn_source_inputs(identity());

    data.spells.extend(burn.data.spells);
    data.effects.extend(burn.data.effects);
    actions.actions.extend(burn.actions.actions);
    actions.programs.extend(burn.actions.programs);

    let data = GameData::try_from_input(data).expect("merged game data is valid");
    let actions = ResolvedActionCatalog::try_from_input(actions).expect("merged catalog is valid");
    let error = CombatProgram::builder(&data, &actions)
        .build()
        .expect_err("Power Burn must not coexist with a damage-immunity plan");
    let rendered = format!("{error:?}");

    assert!(rendered.contains("UnsupportedImmunityPlan"), "{rendered}");
    assert!(rendered.contains("\"damage\""), "{rendered}");
}
