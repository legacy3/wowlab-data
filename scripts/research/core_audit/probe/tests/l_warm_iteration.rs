//! Track L — allocation / pay-for-play probes (read-only against Core).
//!
//! A process-wide counting allocator with thread-local, opt-in measurement mirrors
//! `tools/allocation-tests/tests/allocation_budgets.rs`. Every test asserts the observed
//! behaviour so it fails if Core drifts.

use std::{
    alloc::{GlobalAlloc, Layout, System},
    cell::Cell,
    hint::black_box,
};

use wowlab_combat::{
    ActorState, CombatProgram, CombatState, CriticalStrikeBaseline, HasteMultipliers, HealthPool,
    OffensivePower, WhiteEngagementGeometry, WhiteMainHandChances, WhiteMainHandInput,
    WhitePhysicalBasis,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, GameData, GameDataIdentity, ResolvedActionCatalog,
    TraitSourceCatalog,
};
use wowlab_engine::{
    Engine, EngineInput, EngineProgram, PreparedAutoDamageCandidate, PreparedCriticalPackage,
    SelectedTraitEntry,
};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, SimTime};
use wowlab_rotation::{Action, Identifier, Rotation};
use wowlab_sim::{RandomStream, RandomStreamIdentity};
use wowlab_test_support::{
    PassiveSchoolReductionCarrier, absent_spell_external_policy,
    empty_resolved_action_catalog_input, selected_passive_damage_source_inputs,
    selected_martial_expert_entry, selected_martial_expert_source_inputs,
    selected_trait_auto_attack_damage_entry, selected_trait_auto_attack_damage_source_inputs,
};

// ---------------------------------------------------------------------------------------------
// Counting allocator
// ---------------------------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------------------------
// Fixtures (mirroring tools/allocation-tests, with non-degenerate outcome chances)
// ---------------------------------------------------------------------------------------------

fn identity() -> GameDataIdentity {
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

fn actor(id: ActorId, health: f64) -> ActorState {
    ActorState::try_new(
        id,
        Vec::new(),
        Some(HealthPool::full(health).expect("health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("actor")
}

fn white_input(chances: WhiteMainHandChances, enemy_health: f64) -> EngineInput {
    EngineInput::new(vec![actor(ActorId::Player, 100.0), actor(enemy(), enemy_health)])
        .with_initial_white_main_hand(WhiteMainHandInput {
            source: ActorId::Player,
            target: enemy(),
            prepared_damage: (15, 25),
            base_period: SimTime::from_millis(2_000),
            effective_levels: (1, 1),
            critical_percent_before_compiled_modifier: None,
            chances,
            physical: WhitePhysicalBasis {
                target_armor: 50.0,
                armor_constant: 116.0,
            },
            geometry: WhiteEngagementGeometry {
                in_range: true,
                source_facing: true,
                defender_facing: true,
                defender_standing: true,
                physical_susceptible: true,
                defender_not_evading: true,
            },
        })
}

const MIXED_CHANCES: WhiteMainHandChances = WhiteMainHandChances {
    miss: 1_500,
    dodge: 1_500,
    parry: 0,
    block: 0,
    critical: 3_000,
};

fn precision_engine(chances: WhiteMainHandChances, enemy_health: f64) -> Engine {
    let source = selected_trait_auto_attack_damage_source_inputs(identity());
    let data = GameData::try_from_input(source.data).expect("data");
    let traits = TraitSourceCatalog::try_from_input(source.traits, &data).expect("traits");
    let external_policy = absent_spell_external_policy(&data);
    let candidate = PreparedAutoDamageCandidate::try_prepare(
        &data,
        &traits,
        SelectedTraitEntry {
            entry_id: selected_trait_auto_attack_damage_entry(),
            effective_rank: 1,
        },
    )
    .expect("candidate");
    let (actions, input) = candidate
        .try_into_inputs(
            empty_resolved_action_catalog_input(identity()),
            white_input(chances, enemy_health),
        )
        .expect("fold");
    let actions = ResolvedActionCatalog::try_from_input(actions).expect("actions");
    let combat = CombatProgram::builder(&data, &actions)
        .trait_source(&traits)
        .external_spell_policy(&external_policy)
        .build()
        .expect("combat");
    let program = EngineProgram::try_new(
        combat,
        &Rotation::new("l precision".to_owned()),
        &|_: &Identifier| None,
    )
    .expect("rotation");

    Engine::try_new(
        program,
        input,
        ActorId::Player,
        enemy(),
        RandomStreamIdentity::new(44, 8),
    )
    .expect("engine")
}

fn martial_engine(critical_chance: f64, enemy_health: f64) -> Engine {
    let fixture = selected_martial_expert_source_inputs().freeze();
    let candidate = PreparedCriticalPackage::try_prepare(
        &fixture.data,
        &fixture.traits,
        SelectedTraitEntry {
            entry_id: selected_martial_expert_entry(),
            effective_rank: 1,
        },
    )
    .expect("candidate");
    let input = EngineInput::new(vec![
        actor(ActorId::Player, 10_000.0),
        actor(enemy(), enemy_health),
    ])
    .with_critical_strike(
        CriticalStrikeBaseline::try_uniform(ActorId::Player, critical_chance, 2.0)
            .expect("critical baseline"),
    );
    let (actions, input) = candidate
        .try_into_inputs(fixture.actions, input)
        .expect("fold");
    let actions = ResolvedActionCatalog::try_from_input(actions).expect("actions");
    let combat = CombatProgram::builder(&fixture.data, &actions)
        .trait_source(&fixture.traits)
        .external_spell_policy(&fixture.external_policy)
        .build()
        .expect("combat");
    let mut rotation = Rotation::new("l martial".to_owned());

    rotation.actions.push(Action::Cast {
        spell: Identifier::new("cast").expect("identifier"),
        empower_rank: None,
        enabled: true,
        condition: None,
        target: None,
    });
    let matching = fixture.matching_damage;
    let program = EngineProgram::try_new(combat, &rotation, &|name: &Identifier| {
        (name.as_str() == "cast").then_some(matching)
    })
    .expect("rotation");

    Engine::try_new(
        program,
        input,
        ActorId::Player,
        enemy(),
        RandomStreamIdentity::new(108, 15),
    )
    .expect("engine")
}

/// Runs up to `wakes` engine advances, stopping at the first error; returns completed wakes.
fn run_advances(engine: &mut Engine, wakes: usize) -> usize {
    let mut completed = 0;
    for _ in 0..wakes {
        if black_box(engine.advance()).is_err() {
            break;
        }
        completed += 1;
    }
    completed
}

fn run_steps(engine: &mut Engine, steps: usize) -> usize {
    let mut completed = 0;
    for _ in 0..steps {
        if black_box(engine.step()).is_err() {
            break;
        }
        completed += 1;
    }
    completed
}

// ---------------------------------------------------------------------------------------------
// AL-L-001: immutable CombatProgram clones allocate nothing (distributed arch §3.8 claim).
// ---------------------------------------------------------------------------------------------

/// AL-L-001: `CombatProgram::clone` of a selected-package program is allocation-free.
#[test]
fn holds_combat_program_clone_is_allocation_free() {
    let source = selected_trait_auto_attack_damage_source_inputs(identity());
    let data = GameData::try_from_input(source.data).expect("data");
    let traits = TraitSourceCatalog::try_from_input(source.traits, &data).expect("traits");
    let external_policy = absent_spell_external_policy(&data);
    let candidate = PreparedAutoDamageCandidate::try_prepare(
        &data,
        &traits,
        SelectedTraitEntry {
            entry_id: selected_trait_auto_attack_damage_entry(),
            effective_rank: 1,
        },
    )
    .expect("candidate");
    let (actions, _input) = candidate
        .try_into_inputs(
            empty_resolved_action_catalog_input(identity()),
            white_input(MIXED_CHANCES, 1_000.0),
        )
        .expect("fold");
    let actions = ResolvedActionCatalog::try_from_input(actions).expect("actions");
    let combat = CombatProgram::builder(&data, &actions)
        .trait_source(&traits)
        .external_spell_policy(&external_policy)
        .build()
        .expect("combat");

    let (clones, counts) = measure(|| {
        let first = black_box(combat.clone());
        let second = black_box(first.clone());
        (first, second)
    });

    assert_eq!(counts, (0, 0, 0), "CombatProgram clone must not allocate");
    drop(clones);
}

// ---------------------------------------------------------------------------------------------
// AL-L-002: tracked inline sizes of public runtime roots.
// ---------------------------------------------------------------------------------------------

/// AL-L-002: `CombatState` is 648 bytes (Core test pins 648; architecture doc §3.8 still says 640),
/// `CombatProgram` 16, `ActorState` 104, `RandomStream` 48.
#[test]
fn holds_public_runtime_root_inline_sizes() {
    assert_eq!(size_of::<CombatState>(), 648);
    assert_eq!(size_of::<CombatProgram>(), 16);
    assert_eq!(size_of::<ActorState>(), 104);
    assert_eq!(size_of::<RandomStream>(), 48);
}

// ---------------------------------------------------------------------------------------------
// AL-L-003: warm Precision (selected raw-108/raw-344 package) white occurrences stay
// allocation-free across *different* global iterations with a non-degenerate outcome table.
// Core's own budget replays one seed with an all-zero outcome table.
// ---------------------------------------------------------------------------------------------

/// AL-L-003: selected Precision white path; warm-up one iteration, then 64 fresh iterations with
/// distinct random identities and a mixed miss/dodge/critical table (Core admits parry = block = 0 only) allocate nothing.
#[test]
fn holds_precision_white_fresh_iterations_allocation_free() {
    let mut engine = precision_engine(MIXED_CHANCES, 400.0);
    let warm = run_advances(&mut engine, 64);
    assert!(warm > 8, "warm-up must execute several white wakes, got {warm}");

    let (completed, counts) = measure(|| {
        let mut completed = 0;
        for iteration in 0..64_u64 {
            engine.reset_for_iteration(RandomStreamIdentity::new(900 + iteration, iteration));
            completed += run_advances(&mut engine, 64);
        }
        completed
    });

    assert!(completed > 64 * 8, "fresh iterations executed {completed} wakes");
    assert_eq!(
        counts,
        (0, 0, 0),
        "warm selected white iterations must stay allocation-free"
    );
}

/// AL-L-004: selected Martial Expert critical cast path with a 50% baseline; fresh iterations
/// (distinct random identities, so crit/non-crit order varies) allocate nothing after warm-up.
#[test]
fn holds_martial_fresh_iterations_allocation_free() {
    let mut engine = martial_engine(0.5, 1.0e9);
    let warm = run_steps(&mut engine, 32);
    assert!(warm >= 1, "warm-up must execute a cast");

    let (completed, counts) = measure(|| {
        let mut completed = 0;
        for iteration in 0..64_u64 {
            engine.reset_for_iteration(RandomStreamIdentity::new(1_000 + iteration, iteration));
            completed += run_steps(&mut engine, 32);
        }
        completed
    });

    assert!(completed >= 64, "fresh iterations executed {completed} steps");
    assert_eq!(counts, (0, 0, 0), "warm Martial iterations must stay allocation-free");
}

// ---------------------------------------------------------------------------------------------
// CSA-L-01 / AL-L-005: a program with NO spell modifier and NO multi-effect package still pays
// the 16-byte `selected_package_effects` box header, because `PassiveAmountModifierPlan` inlines
// `Option<PassiveSpellModifierPlan>` (program/passive_outgoing_modifier.rs:22-28) and the cold-only
// package provenance lives in that plan (program/passive_spell_modifier.rs:128).
// Attribution: mutant build removing only that field drops this exact compile from 1,752 to
// 1,736 requested bytes (scratch/L/mutant_no_package_field.diff); the pinned Core budget is 1,752.
// ---------------------------------------------------------------------------------------------

/// CSA-L-01: selected passive school-damage program (Elemental Warding rank 1) has no spell
/// modifier yet its cold compile requests 1,752 bytes (1,736 without the package field).
#[test]
fn defect_package_provenance_header_taxes_program_without_spell_modifiers() {
    let (_fixture, data, actions, traits) =
        selected_passive_damage_source_inputs(identity(), PassiveSchoolReductionCarrier::ElementalWarding, 1)
            .freeze();
    assert!(
        actions.spell_modifiers().is_empty(),
        "fixture must contain no spell modifier and therefore no package provenance"
    );
    let external_policy = absent_spell_external_policy(&data);

    let (program, counts) = measure(|| {
        CombatProgram::builder(black_box(&data), black_box(&actions))
            .trait_source(black_box(&traits))
            .external_spell_policy(black_box(&external_policy))
            .build()
            .expect("selected passive damage program")
    });

    assert_eq!(counts, (22, 0, 1_752), "exact cold compile (alloc+zeroed, realloc, bytes)");
    drop(program);
}
