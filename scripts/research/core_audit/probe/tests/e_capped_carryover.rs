//! Track E probes: capped-carryover (raw 436) periodic damage through the shared test-support
//! fixture (duration 250 ms, period 100 ms, base 10 + 0.5 AP + 0.25 SP, preserve cadence,
//! proportional natural-expiry partial).
//!
//! Records: FF-E-003, NUM-E-002, NUM-E-003, REJ-E-005.

use wowlab_combat::{
    ActorState, CastRequest, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    HasteMultipliers, HealthPool, OffensivePower,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, GameDataIdentity, ResolvedPeriodicInitialInput,
    periodic_damage,
};
use wowlab_dbc::{AuraSubtypeKind, SpellAttributeKind};
use wowlab_model::{ActorId, DifficultyId, SimTime, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support::{
    PeriodicDamageAuraSubtypes, PeriodicDamageFixtureCatalog, PeriodicDamageKind,
    PeriodicDamageSpellAttributes, periodic_damage_apply_spell_raw,
    periodic_damage_compilation_inputs,
};

fn program() -> CombatProgram {
    let catalog = PeriodicDamageFixtureCatalog::new(
        GameDataIdentity {
            version: DataVersion {
                schema: CURRENT_SCHEMA_VERSION,
                game_build: 69_497,
            },
            difficulty: DifficultyId::BASE,
        },
        PeriodicDamageAuraSubtypes::new(
            AuraSubtypeKind::PeriodicDamage.raw(),
            AuraSubtypeKind::PeriodicMaxHealthDamage.raw(),
        ),
        PeriodicDamageSpellAttributes::new()
            .with_periodic_can_crit(SpellAttributeKind::PeriodicCanCrit.raw())
            .with_refresh_extends_duration(SpellAttributeKind::PeriodicRefreshExtendsDuration.raw())
            .with_tick_on_application(SpellAttributeKind::TickOnApplication.raw()),
    );
    let (data, actions) = periodic_damage_compilation_inputs(
        catalog,
        &[PeriodicDamageKind::Fixed],
        ResolvedPeriodicInitialInput::AfterFirstPeriod,
        periodic_damage::CriticalInput::Never,
    );

    CombatProgram::builder(&data, &actions)
        .build()
        .expect("capped-carryover fixture compiles")
}

fn state(program: &CombatProgram) -> CombatState {
    let power = OffensivePower::try_new(100.0, 0.0).expect("valid power");
    let actor = |id| {
        ActorState::try_new(
            id,
            Vec::new(),
            Some(HealthPool::full(1.0e9).expect("valid health")),
            power,
            HasteMultipliers::UNHASTED,
        )
        .expect("valid actor")
    };

    program
        .try_state(
            CombatStateInput::new(
                vec![actor(ActorId::External), actor(ActorId::Player)],
                RandomStreamIdentity::new(3, 9),
            )
            .with_hostile_target(ActorId::Player, ActorId::External),
        )
        .expect("valid state")
}

fn apply(state: &mut CombatState) {
    state
        .cast(
            CastRequest::new(
                ActorId::Player,
                ActorId::External,
                SpellId::new(periodic_damage_apply_spell_raw()).expect("spell"),
            ),
            &mut Vec::new(),
        )
        .expect("application succeeds");
}

fn damage(observations: &[CombatObservation]) -> Vec<(u32, f64)> {
    observations
        .iter()
        .filter_map(|o| match o {
            CombatObservation::DamageDealt { at, damage, .. } => {
                Some((at.as_millis(), damage.requested()))
            }
            _ => None,
        })
        .collect()
}

/// Holds: without refresh, ticks at 100/200 full (60) and a 50/100 partial (30) at expiry 250.
#[test]
fn holds_natural_expiry_partial_is_proportional() {
    let program = program();
    let mut state = state(&program);

    apply(&mut state);
    let observed = damage(&state.advance_to(SimTime::from_millis(1_000)).expect("advance"));
    assert_eq!(observed, vec![(100, 60.0), (200, 60.0), (250, 30.0)]);
}

/// Holds (NUM-E-002 / FF-E-003): refresh at 150 carries min(100, floor(0.3*250)=75) ->
/// expiry 475; cadence preserved (200, 300, 400) and the 75/100 remainder lands at 475.
#[test]
fn holds_capped_carryover_preserves_cadence_and_moves_partial() {
    let program = program();
    let mut state = state(&program);

    apply(&mut state);
    let mut all = state.advance_to(SimTime::from_millis(150)).expect("advance");
    apply(&mut state);
    all.extend(state.advance_to(SimTime::from_millis(1_000)).expect("advance"));
    assert_eq!(
        damage(&all),
        vec![(100, 60.0), (200, 60.0), (300, 60.0), (400, 60.0), (475, 45.0)]
    );
}
