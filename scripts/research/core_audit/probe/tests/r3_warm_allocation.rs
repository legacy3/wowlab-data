//! Reviewer R3 (lifecycle / allocation): warm `reset_for_iteration` and warm replay across fresh
//! iteration identities allocate nothing, over the 24 public fixtures of `r3_reset_coverage.rs`
//! and a stochastic state input whose outcome tables actually vary per identity.
//! Records: AL-R3-001 (holds), UNK-L-001 narrowed (AL-R3-002).

use std::{
    alloc::{GlobalAlloc, Layout, System},
    cell::Cell,
};

use wowlab_combat::{
    ActiveDefenseBaseline, ActiveDefenseCapabilities, ActorState, AttackAccuracyBaseline,
    AttackNonLandingChances, BlockDefense, CastRequest, CombatObservation, CombatProgram,
    CombatState, CombatStateInput, CriticalStrikeBaseline, DefenseFamilyChances,
    HasteMultipliers, HealthPool, OffensivePower, ResourcePool,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, GameData, GameDataIdentity, GameDataInput,
    ResolvedActionCatalog, ResolvedActionCatalogInput,
};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, ResourceType, SimTime};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support as ts;

thread_local! {
    static ENABLED: Cell<bool> = const { Cell::new(false) };
    static EVENTS: Cell<(usize, usize, usize)> = const { Cell::new((0, 0, 0)) };
}

struct Counting;

#[global_allocator]
static ALLOCATOR: Counting = Counting;

fn bump(kind: usize, bytes: usize) {
    let _ = ENABLED.try_with(|enabled| {
        if enabled.get() {
            let _ = EVENTS.try_with(|events| {
                let (count, reallocs, requested) = events.get();
                events.set(if kind == 0 {
                    (count + 1, reallocs, requested + bytes)
                } else {
                    (count, reallocs + 1, requested + bytes)
                });
            });
        }
    });
}

// SAFETY: every operation delegates to `System` with the original arguments; counter updates
// touch only const-initialized thread-local cells and never allocate.
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        // SAFETY: delegated unchanged.
        let pointer = unsafe { System.alloc(layout) };
        if !pointer.is_null() {
            bump(0, layout.size());
        }
        pointer
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        // SAFETY: delegated unchanged.
        let pointer = unsafe { System.alloc_zeroed(layout) };
        if !pointer.is_null() {
            bump(0, layout.size());
        }
        pointer
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        // SAFETY: delegated unchanged.
        unsafe { System.dealloc(ptr, layout) };
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        // SAFETY: delegated unchanged.
        let pointer = unsafe { System.realloc(ptr, layout, new_size) };
        if !pointer.is_null() {
            bump(1, new_size);
        }
        pointer
    }
}

/// Returns `(alloc+alloc_zeroed events, realloc events, requested bytes)` of `workload`.
fn measure<R>(workload: impl FnOnce() -> R) -> (R, (usize, usize, usize)) {
    EVENTS.with(|events| events.set((0, 0, 0)));
    ENABLED.with(|enabled| enabled.set(true));
    let output = workload();
    ENABLED.with(|enabled| enabled.set(false));

    (output, EVENTS.with(Cell::get))
}

const fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

const fn enemy() -> ActorId {
    ActorId::Enemy {
        index: EnemyIndex::new(0),
    }
}

fn pools() -> Vec<ResourcePool> {
    [
        (ResourceType::Mana, 50_000.0, 100_000.0),
        (ResourceType::Rage, 50.0, 100.0),
        (ResourceType::Energy, 60.0, 100.0),
        (ResourceType::Focus, 60.0, 100.0),
        (ResourceType::RunicPower, 40.0, 100.0),
    ]
    .into_iter()
    .map(|(kind, current, maximum)| ResourcePool::try_new(kind, current, maximum).expect("pool"))
    .collect()
}

fn actor(id: ActorId) -> ActorState {
    ActorState::try_new_with_active_resource(
        id,
        pools(),
        Some(ResourceType::Mana),
        Some(HealthPool::try_new(60_000.0, 100_000.0).expect("health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("actor")
}

fn plain_input(random: RandomStreamIdentity) -> CombatStateInput {
    CombatStateInput::new(vec![actor(ActorId::Player), actor(enemy())], random)
        .with_hostile_target(ActorId::Player, enemy())
        .with_critical_strike(CriticalStrikeBaseline::try_uniform(ActorId::Player, 0.35, 2.0).expect("crit"))
        .with_critical_strike(CriticalStrikeBaseline::try_uniform(enemy(), 0.35, 2.0).expect("crit"))
}

fn defense(actor: ActorId) -> ActiveDefenseBaseline {
    ActiveDefenseBaseline::new(
        actor,
        AttackNonLandingChances::try_new(
            DefenseFamilyChances::try_new(0.1, 0.1, 0.1).expect("miss"),
            0.1,
            0.1,
        )
        .expect("table"),
        BlockDefense::try_new(0.2, 0.3, 0.7, 0.4).expect("block"),
        ActiveDefenseCapabilities::new().with_parry().with_block(),
    )
}

fn stochastic_input(random: RandomStreamIdentity) -> CombatStateInput {
    plain_input(random)
        .with_hostile_target(enemy(), ActorId::Player)
        .with_attack_accuracy(AttackAccuracyBaseline::new(ActorId::Player, DefenseFamilyChances::NONE))
        .with_attack_accuracy(AttackAccuracyBaseline::new(enemy(), DefenseFamilyChances::NONE))
        .with_active_defense(defense(ActorId::Player))
        .with_active_defense(defense(enemy()))
}

fn frozen(pair: (GameDataInput, ResolvedActionCatalogInput)) -> (GameData, ResolvedActionCatalog) {
    (
        GameData::try_from_input(pair.0).expect("fixture data"),
        ResolvedActionCatalog::try_from_input(pair.1).expect("fixture actions"),
    )
}

fn t<F>(x: (F, GameData, ResolvedActionCatalog)) -> (GameData, ResolvedActionCatalog) {
    (x.1, x.2)
}

fn scenarios() -> Vec<(&'static str, (GameData, ResolvedActionCatalog))> {
    let id = identity();
    vec![
        ("max_health Multiplicative", t(ts::maximum_health_modifier_runtime_inputs(id, ts::MaximumHealthRuntimeTopology::Multiplicative))),
        ("max_health MixedFamilies", t(ts::maximum_health_modifier_runtime_inputs(id, ts::MaximumHealthRuntimeTopology::MixedFamilies))),
        ("power_burn", t(ts::power_burn_inputs(id))),
        ("power_drain x2", t(ts::power_drain_inputs(id, 2))),
        ("periodic_mana_leech", t(ts::periodic_mana_leech_inputs(id))),
        ("absorb OverlapPair", t(ts::absorb_inputs(id, ts::AbsorbFixtureTopology::OverlapPair))),
        ("healing_absorb OverlapPair", t(ts::healing_absorb_inputs(id, ts::AbsorbFixtureTopology::OverlapPair))),
        ("mechanic_resistance", t(ts::mechanic_resistance_inputs(id))),
        ("crit_damage ActiveAndPassive", ts::critical_damage_modifier_inputs(id, ts::CritDamageFixtureKind::ActiveAndPassive)),
        ("incoming_melee_critical_runtime", ts::incoming_melee_critical_runtime_inputs(id)),
        ("attacker_spell_weapon_critical_runtime", ts::attacker_spell_weapon_critical_runtime_inputs(id)),
        ("mod_maximum_resource_percent", t(ts::mod_maximum_resource_percent_inputs(id))),
        ("haste_affected_duration Exact", t(ts::haste_affected_duration_inputs(id, ts::HasteAffectedDurationTopology::Exact))),
        ("target_absorb_bypass", t(ts::target_absorb_bypass_inputs(id))),
        ("raw_165", t(ts::raw_165_inputs(id, 10.0))),
        ("mana_shield_runtime", t(ts::mana_shield_runtime_inputs(id, 500.0))),
        ("base_mana_percentage", t(ts::base_mana_percentage_inputs(id))),
        ("mechanic_immunity_rng", t(ts::mechanic_immunity_rng_inputs(id))),
        ("school_immunity_rng", t(ts::school_immunity_rng_inputs(id))),
        ("passive_flow_flat_runtime", t(ts::passive_flow_flat_runtime_inputs(id))),
        ("enemy_dodge", frozen(ts::enemy_dodge_inputs(id))),
        ("aura_unique AuraUnique x2", frozen(ts::aura_unique_scale_inputs(id, ts::AuraUniqueScaleTopology::AuraUnique, 2).into_catalog_inputs())),
        ("aura_unique StackableFallback x2", frozen(ts::aura_unique_scale_inputs(id, ts::AuraUniqueScaleTopology::StackableFallback, 2).into_catalog_inputs())),
        ("periodic_cadence SpellHasted x2", frozen(ts::periodic_cadence_rate_inputs(id, ts::PeriodicCadenceRateTopology::SpellHasted, 2).into_catalog_inputs())),
    ]
}


fn requests(program: &CombatProgram, actions: &ResolvedActionCatalog) -> Vec<CastRequest> {
    let mut out = Vec::new();
    for spell in actions.actions().map(wowlab_data::ResolvedAction::spell) {
        for (caster, target) in [
            (ActorId::Player, enemy()),
            (ActorId::Player, ActorId::Player),
            (enemy(), ActorId::Player),
        ] {
            if let Ok(request) = program.cast_request(caster, target, spell) {
                out.push(request);
            }
        }
    }
    out
}

/// Allocation-free drive: output storage is caller-owned and pre-reserved.
fn drive(state: &mut CombatState, requests: &[CastRequest], output: &mut Vec<CombatObservation>) -> usize {
    let mut committed = 0;
    for _round in 0..14 {
        output.clear();
        for &request in requests {
            let _ = state.cast_readiness(request);
            if state.begin_cast(request, output).is_ok() {
                committed += 1;
            }
        }
        if state.next_transition_at().is_some() {
            state.advance_into(output);
        } else {
            let to = state.now() + SimTime::from_millis(1_000);
            let _ = state.advance_to_into(to, output);
        }
    }
    committed
}

/// AL-R3-001: after one warm-up iteration, `reset_for_iteration` plus a full replay under 16 new
/// identities performs zero allocation events for every fixture that commits casts.
#[test]
fn holds_warm_iteration_reset_and_replay_are_allocation_free_on_uncovered_fixtures() {
    let mut measured = 0;
    let mut offenders = Vec::new();
    for (name, (data, actions)) in scenarios() {
        for (label, input) in [
            ("plain", plain_input as fn(RandomStreamIdentity) -> CombatStateInput),
            ("stochastic", stochastic_input),
        ] {
            let program = CombatProgram::builder(&data, &actions).build().expect("program");
            let Ok(mut state) = program.try_state(input(RandomStreamIdentity::new(77, 0))) else {
                continue;
            };
            let requests = requests(&program, &actions);
            let mut output: Vec<CombatObservation> = Vec::with_capacity(1 << 16);
            if drive(&mut state, &requests, &mut output) == 0 {
                continue;
            }
            for iteration in 1..=16_u64 {
                let identity = RandomStreamIdentity::new(77, iteration);
                let (committed, events) = measure(|| {
                    state.reset_for_iteration(identity);
                    drive(&mut state, &requests, &mut output)
                });
                measured += 1;
                if events != (0, 0, 0) {
                    offenders.push(format!("{name} [{label}] iteration {iteration}: {events:?} committed {committed}"));
                }
            }
        }
    }
    eprintln!("measured {measured} warm iterations; offenders {offenders:#?}");
    assert!(measured > 500, "enough warm iterations were measured");
    assert!(offenders.is_empty(), "warm iteration allocated: {offenders:#?}");
}
