//! Track H: reset topology / quote purity / failure purity fingerprints.
//!
//! Every scenario builds a real `CombatState` through the public API from a Core
//! test-support fixture, drives every compiled spell deterministically, and
//! compares the complete `Debug` rendering of the state (all mutable owners)
//! across reset, iteration reset, quotes and rejected casts.
//!
//! Records: LC-H-* (lifecycle), MUT-H-* (mutations), REJ-H-* (rejected).

use wowlab_combat::{
    ActorState, CombatObservation, CombatProgram, CombatState, CombatStateInput,
    CriticalStrikeBaseline, HasteMultipliers, HealthPool, OffensivePower, ResourcePool,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, GameData, GameDataIdentity, ResolvedActionCatalog,
};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, ResourceType, SimTime, SpellId};
use wowlab_sim::RandomStreamIdentity;

pub const fn identity() -> GameDataIdentity {
    GameDataIdentity {
        version: DataVersion {
            schema: CURRENT_SCHEMA_VERSION,
            game_build: 69_497,
        },
        difficulty: DifficultyId::BASE,
    }
}

pub const fn enemy() -> ActorId {
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
    .map(|(kind, current, maximum)| {
        ResourcePool::try_new(kind, current, maximum).expect("probe pool is valid")
    })
    .collect()
}

pub fn actor(id: ActorId) -> ActorState {
    ActorState::try_new_with_active_resource(
        id,
        pools(),
        Some(ResourceType::Mana),
        Some(HealthPool::try_new(60_000.0, 100_000.0).expect("probe health is valid")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("probe actor is valid")
}

pub fn default_input(random: RandomStreamIdentity) -> CombatStateInput {
    CombatStateInput::new(vec![actor(ActorId::Player), actor(enemy())], random)
        .with_hostile_target(ActorId::Player, enemy())
        .with_critical_strike(
            CriticalStrikeBaseline::try_uniform(ActorId::Player, 0.35, 2.0)
                .expect("probe crit is valid"),
        )
        .with_critical_strike(
            CriticalStrikeBaseline::try_uniform(enemy(), 0.35, 2.0).expect("probe crit is valid"),
        )
}

/// Full rendering, including transaction scratch.
pub fn dbg_full(state: &CombatState) -> String {
    format!("{state:#?}")
}

/// Field names whose subtrees are transaction scratch (leased workspaces, quote buffers).
/// Their residue is excluded from semantic comparisons; the full rendering is still compared
/// for reset where the contract is stronger (warm reset restores everything).
fn is_scratch_field(name: &str) -> bool {
    name == "quoted_costs" || name.contains("workspace") || name.contains("scratch")
}

/// Semantic rendering: pretty Debug with scratch subtrees removed.
pub fn dbg(state: &CombatState) -> String {
    strip_scratch(&dbg_full(state))
}

pub fn strip_scratch(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut skip_indent: Option<usize> = None;
    for line in text.lines() {
        let indent = line.len() - line.trim_start().len();
        if let Some(level) = skip_indent {
            if indent > level {
                continue;
            }
            skip_indent = None;
            let trimmed = line.trim();
            if trimmed.starts_with(['}', ']', ')']) {
                continue;
            }
        }
        let trimmed = line.trim_start();
        if let Some(head) = trimmed.strip_suffix(" {")
            && head.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
            && head.contains("Workspace")
        {
            out.push_str(&line[..indent]);
            out.push_str("<scratch workspace>\n");
            skip_indent = Some(indent);
            continue;
        }
        if let Some((name, rest)) = trimmed.split_once(": ") {
            if is_scratch_field(name) {
                out.push_str(&line[..indent]);
                out.push_str(name);
                out.push_str(": <scratch>\n");
                if rest.trim_end().ends_with(['{', '[', '(']) {
                    skip_indent = Some(indent);
                }
                continue;
            }
        }
        out.push_str(line);
        out.push('\n');
    }
    out
}

/// Returns the first differing line index and a small window of both renderings.
pub fn first_diff(left: &str, right: &str) -> Option<String> {
    let l: Vec<&str> = left.lines().collect();
    let r: Vec<&str> = right.lines().collect();
    let index = l
        .iter()
        .zip(&r)
        .position(|(a, b)| a != b)
        .or_else(|| (l.len() != r.len()).then(|| l.len().min(r.len())))?;
    // Walk back to find the enclosing field names for context.
    let mut path = Vec::new();
    let mut indent = usize::MAX;
    for line in l[..index.min(l.len())].iter().rev() {
        let this = line.len() - line.trim_start().len();
        if this < indent && line.trim_end().ends_with(['{', '[', '(']) {
            path.push(line.trim().to_owned());
            indent = this;
        }
    }
    path.reverse();
    let window = |lines: &[&str]| {
        lines
            .iter()
            .skip(index.saturating_sub(2))
            .take(8)
            .copied()
            .collect::<Vec<_>>()
            .join("\n")
    };
    Some(format!(
        "line {index}\npath: {}\n--- left\n{}\n--- right\n{}",
        path.join(" > "),
        window(&l),
        window(&r)
    ))
}

#[derive(Default, Debug)]
pub struct Report {
    pub name: String,
    pub spells: usize,
    pub successes: usize,
    pub failures: usize,
    pub advances: usize,
    pub quote_impure: Vec<String>,
    pub failure_impure: Vec<String>,
    pub reset_diff: Option<String>,
    pub reset_full_diff: Option<String>,
    pub replay_mismatch: bool,
    pub iteration_diff: Option<String>,
    pub iteration_full_diff: Option<String>,
    pub iteration_replay_mismatch: bool,
}

pub struct Scenario<'a> {
    pub name: &'a str,
    pub data: GameData,
    pub actions: ResolvedActionCatalog,
    pub input: &'a dyn Fn(RandomStreamIdentity) -> CombatStateInput,
    pub rounds: usize,
    /// Optional host action executed at the start of each round (e.g. passive-flow updates).
    pub host: Option<&'a dyn Fn(&mut CombatState, usize)>,
}

fn spells(actions: &ResolvedActionCatalog, program: &CombatProgram) -> Vec<SpellId> {
    actions
        .actions()
        .map(wowlab_data::ResolvedAction::spell)
        .filter(|spell| program.contains_spell(*spell))
        .collect()
}

/// Deterministic driver: each round attempts every spell once (Player -> enemy), then consumes
/// one timer transition or advances one second.
fn drive(
    state: &mut CombatState,
    program: &CombatProgram,
    spells: &[SpellId],
    rounds: usize,
    report: &mut Report,
    check_purity: bool,
    host: Option<&dyn Fn(&mut CombatState, usize)>,
) -> Vec<String> {
    let mut trace = Vec::new();
    let mut output: Vec<CombatObservation> = Vec::new();

    for round in 0..rounds {
        if let Some(host) = host {
            host(state, round);
        }
        for &spell in spells {
            let Ok(request) = program.cast_request(ActorId::Player, enemy(), spell) else {
                continue;
            };
            let before = check_purity.then(|| dbg(state));
            let readiness = state.cast_readiness(request);

            if let Some(before) = &before {
                let after = dbg(state);
                if let Some(diff) = first_diff(before, &after) {
                    report
                        .quote_impure
                        .push(format!("round {round} spell {spell:?}: {diff}"));
                }
            }

            let start = output.len();
            match state.begin_cast(request, &mut output) {
                Ok(_) => {
                    report.successes += 1;
                    trace.push(format!(
                        "r{round} {spell:?} ready={:?} ok {:?}",
                        readiness.error_code(),
                        &output[start..]
                    ));
                }
                Err(error) => {
                    report.failures += 1;
                    trace.push(format!(
                        "r{round} {spell:?} ready={:?} err {:?}",
                        readiness.error_code(),
                        error.code()
                    ));
                    if let Some(before) = &before {
                        let after = dbg(state);
                        if let Some(diff) = first_diff(before, &after) {
                            report
                                .failure_impure
                                .push(format!("round {round} spell {spell:?} {:?}: {diff}", error.code()));
                        }
                        assert_eq!(output.len(), start, "failed cast appended observations");
                    }
                }
            }
        }

        let start = output.len();
        if state.next_transition_at().is_some() {
            state.advance_into(&mut output);
        } else {
            let to = state.now() + SimTime::from_millis(1_000);
            state.advance_to_into(to, &mut output).expect("forward");
        }
        report.advances += 1;
        trace.push(format!("adv {:?} {:?}", state.now(), &output[start..]));
    }

    trace
}

pub fn run(scenario: &Scenario<'_>) -> Report {
    let mut report = Report {
        name: scenario.name.to_owned(),
        ..Report::default()
    };
    let program = CombatProgram::builder(&scenario.data, &scenario.actions)
        .build()
        .unwrap_or_else(|error| panic!("{}: program build failed: {error:?}", scenario.name));
    let spells = spells(&scenario.actions, &program);
    report.spells = spells.len();

    let first = RandomStreamIdentity::new(7_001, 3);
    let second = RandomStreamIdentity::new(7_001, 11);
    let mut state = program
        .try_state((scenario.input)(first))
        .unwrap_or_else(|error| panic!("{}: state build failed: {error:?}", scenario.name));
    let fresh = dbg(&state);
    let fresh_full = dbg_full(&state);
    let first_trace = drive(&mut state, &program, &spells, scenario.rounds, &mut report, true, scenario.host);

    state.reset();
    report.reset_diff = first_diff(&fresh, &dbg(&state));
    report.reset_full_diff = first_diff(&fresh_full, &dbg_full(&state));
    let mut scratch = Report::default();
    let replay = drive(&mut state, &program, &spells, scenario.rounds, &mut scratch, false, scenario.host);
    report.replay_mismatch = replay != first_trace;
    if report.replay_mismatch {
        if let Some(index) = replay.iter().zip(&first_trace).position(|(a, b)| a != b) {
            eprintln!(
                "[{}] replay diverges at {index}:\n first:  {}\n replay: {}",
                scenario.name, first_trace[index], replay[index]
            );
        }
    }

    // Iteration reset: a state that ran iteration `first` and then reset to `second` must be
    // indistinguishable from a fresh state constructed for `second`.
    state.reset_for_iteration(second);
    let mut fresh_second = program
        .try_state((scenario.input)(second))
        .expect("second state builds");
    report.iteration_diff = first_diff(&dbg(&fresh_second), &dbg(&state));
    report.iteration_full_diff = first_diff(&dbg_full(&fresh_second), &dbg_full(&state));
    let reused = drive(&mut state, &program, &spells, scenario.rounds, &mut scratch, false, scenario.host);
    let fresh = drive(&mut fresh_second, &program, &spells, scenario.rounds, &mut scratch, false, scenario.host);
    report.iteration_replay_mismatch = reused != fresh;

    report
}

/// Asserts the semantic reset/quote/failure contract for one scenario.
pub fn assert_clean(report: &Report) {
    assert!(report.successes > 0, "{}: scenario must commit at least one cast", report.name);
    assert!(report.quote_impure.is_empty(), "{}: quote mutated semantic state", report.name);
    assert!(report.failure_impure.is_empty(), "{}: failed cast mutated semantic state", report.name);
    assert!(report.reset_diff.is_none(), "{}: reset differs from fresh", report.name);
    assert!(!report.replay_mismatch, "{}: reset replay diverges", report.name);
    assert!(report.iteration_diff.is_none(), "{}: iteration reset differs from fresh", report.name);
    assert!(!report.iteration_replay_mismatch, "{}: iteration replay diverges", report.name);
}

pub fn print(report: &Report) {
    eprintln!(
        "== {} spells={} ok={} err={} adv={} quote_impure={} failure_impure={} reset_diff={} reset_full_diff={} replay_mismatch={} iter_diff={} iter_full_diff={} iter_replay_mismatch={}",
        report.name,
        report.spells,
        report.successes,
        report.failures,
        report.advances,
        report.quote_impure.len(),
        report.failure_impure.len(),
        report.reset_diff.is_some(),
        report.reset_full_diff.is_some(),
        report.replay_mismatch,
        report.iteration_diff.is_some(),
        report.iteration_full_diff.is_some(),
        report.iteration_replay_mismatch,
    );
    for item in report.quote_impure.iter().take(2) {
        eprintln!("  quote impure: {item}");
    }
    for item in report.failure_impure.iter().take(2) {
        eprintln!("  failure impure: {item}");
    }
    if let Some(diff) = &report.reset_diff {
        eprintln!("  reset diff: {diff}");
    }
    if let Some(diff) = &report.reset_full_diff {
        eprintln!("  reset FULL diff: {diff}");
    }
    if let Some(diff) = &report.iteration_full_diff {
        eprintln!("  iteration FULL diff: {diff}");
    }
    if let Some(diff) = &report.iteration_diff {
        eprintln!("  iteration diff: {diff}");
    }
}

pub trait IntoPair {
    fn into_pair(self) -> (GameData, ResolvedActionCatalog);
}

impl IntoPair for (GameData, ResolvedActionCatalog) {
    fn into_pair(self) -> (GameData, ResolvedActionCatalog) {
        self
    }
}

impl<F> IntoPair for (F, GameData, ResolvedActionCatalog) {
    fn into_pair(self) -> (GameData, ResolvedActionCatalog) {
        (self.1, self.2)
    }
}

macro_rules! scenario {
    ($fn:ident, $rounds:expr) => {{
        let (data, actions) = IntoPair::into_pair(wowlab_test_support::$fn(identity()));
        Scenario {
            name: stringify!($fn),
            data,
            actions,
            input: &default_input,
            rounds: $rounds,
            host: None,
        }
    }};
}

/// LC-H-001..027 / REJ-H-*: across 29 public fixtures, cast_readiness and rejected begin_cast
/// leave every semantic owner unchanged, reset equals a fresh state, reset replays bit-identically,
/// and reset_for_iteration equals a fresh state for the new identity. The full (scratch-included)
/// reset rendering differs only for the absorb-workspace fixtures (inert residue, REJ-H-003).
#[test]
fn holds_reset_fingerprints() {
    let scenarios = vec![
        scenario!(fixed_travel_time_inputs, 12),
        scenario!(interrupt_inputs, 12),
        scenario!(stun_inputs, 12),
        scenario!(silence_inputs, 12),
        scenario!(pacify_inputs, 12),
        scenario!(mana_shield_inputs, 12),
        scenario!(non_bypassable_absorb_inputs, 12),
        scenario!(dispel_runtime_inputs, 12),
        scenario!(share_damage_inputs, 12),
        scenario!(health_threshold_runtime_control_inputs, 12),
        scenario!(school_immunity_runtime_inputs, 12),
        scenario!(mechanic_duration_inputs, 12),
        scenario!(cooldown_start_inputs, 12),
        scenario!(periodic_maximum_power_percent_inputs, 12),
        scenario!(maximum_resource_modifier_mixed_runtime_inputs, 12),
        scenario!(thermal_battery_runtime_inputs, 12),
        scenario!(additional_power_cost_hard_cast_inputs, 12),
        scenario!(mana_cost_percent_hard_cast_inputs, 12),
        scenario!(school_lockout_bypass_inputs, 12),
        scenario!(aura_state_modifier_inputs, 12),
        scenario!(mechanic_dispel_inputs, 12),
        scenario!(raw_1_inputs, 12),
        scenario!(outgoing_mechanic_runtime_inputs, 12),
        scenario!(target_health_critical_runtime_inputs, 12),
        scenario!(effect_immunity_rng_inputs, 12),
        scenario!(damage_immunity_inputs, 12),
        scenario!(dispel_duration_inputs, 12),
        scenario!(passive_flow_suppression_runtime_inputs, 12),
        scenario!(passive_flow_percentage_runtime_inputs, 12),
    ];

    let mut full_diff = Vec::new();
    for scenario in &scenarios {
        let report = run(scenario);
        print(&report);
        assert_clean(&report);
        if report.reset_full_diff.is_some() || report.iteration_full_diff.is_some() {
            full_diff.push(report.name.clone());
        }
    }
    assert_eq!(full_diff, ["mana_shield_inputs", "non_bypassable_absorb_inputs"]);
}

fn threshold_input(
    fixture: &wowlab_test_support::ResourceThresholdRuntimeFixture,
    flow: Option<f64>,
    random: RandomStreamIdentity,
) -> CombatStateInput {
    let player = ActorState::try_new(
        ActorId::Player,
        vec![
            ResourcePool::try_new(fixture.resource, fixture.initial, fixture.maximum)
                .expect("fixture pool"),
        ],
        Some(HealthPool::full(100_000.0).expect("health")),
        OffensivePower::ZERO,
        HasteMultipliers::UNHASTED,
    )
    .expect("player");
    let bindings = fixture
        .passive_effects
        .iter()
        .copied()
        .map(|effect| wowlab_combat::PassiveResourceThresholdBinding::new(ActorId::Player, effect))
        .collect();
    let mut input = CombatStateInput::new(vec![player, actor(enemy())], random)
        .with_hostile_target(ActorId::Player, enemy())
        .with_passive_resource_threshold_bindings(bindings);
    if let Some(rate) = flow {
        input = input.with_passive_resource_flow(
            wowlab_combat::PassiveResourceFlow::try_new(
                ActorId::Player,
                fixture.resource,
                rate,
                wowlab_model::RecoveryRate::Fixed,
            )
            .expect("flow"),
        );
    }
    input
}

fn run_threshold_case(case: wowlab_test_support::ResourceThresholdRuntimeCase, flow: Option<f64>, host_rate: Option<f64>) -> Report {
    let (fixture, data, actions) =
        wowlab_test_support::resource_threshold_runtime_inputs(identity(), case);
    let resource = fixture.resource;
    let input = move |random| threshold_input(&fixture, flow, random);
    let host = move |state: &mut CombatState, round: usize| {
        if let Some(rate) = host_rate
            && round == 3
        {
            let flow = wowlab_combat::PassiveResourceFlow::try_new(
                ActorId::Player,
                resource,
                rate,
                wowlab_model::RecoveryRate::Fixed,
            )
            .expect("host flow");
            state
                .update_passive_resource_flow(flow)
                .expect("host flow update binds");
        }
    };
    let name = format!("threshold {case:?} flow={flow:?} host={host_rate:?}");
    let scenario = Scenario {
        name: &name,
        data,
        actions,
        input: &input,
        rounds: 14,
        host: Some(&host),
    };
    run(&scenario)
}

/// LC-H-025 / LC-H-029: resource-threshold runtimes (startup, crossings, passive-flow deadlines,
/// host `update_passive_resource_flow` at round 3), active casting speed and cooldown/charge rate
/// runtimes satisfy the same reset / quote / failure contract.
#[test]
fn holds_targeted_reset_fingerprints() {
    use wowlab_test_support::ResourceThresholdRuntimeCase as Case;

    let cases = [
        (Case::FeatureAbsent, Some(10.0), Some(-4.0)),
        (Case::BoundInactive, Some(10.0), Some(25.0)),
        (Case::StartupMatching, None, None),
        (Case::SingleCrossing, Some(10.0), None),
        (Case::NestedCrossing, Some(10.0), Some(3.0)),
        (Case::PassiveFlowFanout, Some(10.0), Some(40.0)),
        (Case::MixedPassiveFlowDirections, Some(-5.0), Some(8.0)),
        (Case::PassiveFlowCoincidentHardCast, Some(10.0), None),
        (Case::LossDirectionSpend, Some(-5.0), None),
        (Case::LossDirectionGainRebase, Some(-5.0), Some(-9.0)),
    ];
    for (case, flow, host) in cases {
        let report = run_threshold_case(case, flow, host);
        print(&report);
        assert_clean(&report);
        assert!(report.reset_full_diff.is_none() && report.iteration_full_diff.is_none());
    }

    let frozen: Vec<(&str, (GameData, ResolvedActionCatalog))> = vec![
        (
            "active_casting_speed_runtime",
            wowlab_test_support::active_casting_speed_runtime_inputs(identity()).freeze(),
        ),
        (
            "active_casting_speed_overlap_runtime",
            wowlab_test_support::active_casting_speed_overlap_runtime_inputs(identity()).freeze(),
        ),
        (
            "haste_spells_runtime",
            wowlab_test_support::haste_spells_runtime_inputs(identity()).freeze(),
        ),
        (
            "cooldown_rate ApplyOnly",
            wowlab_test_support::cooldown_rate_inputs(
                identity(),
                wowlab_test_support::CooldownRateFixtureMode::ApplyOnly,
            ),
        ),
        (
            "cooldown_rate SameSegmentStart",
            wowlab_test_support::cooldown_rate_inputs(
                identity(),
                wowlab_test_support::CooldownRateFixtureMode::SameSegmentStart,
            ),
        ),
        (
            "cooldown_rate ApplyAndExplicitRemove",
            wowlab_test_support::cooldown_rate_inputs(
                identity(),
                wowlab_test_support::CooldownRateFixtureMode::ApplyAndExplicitRemove,
            ),
        ),
        (
            "cooldown_rate ApplyExtendRemove",
            wowlab_test_support::cooldown_rate_inputs(
                identity(),
                wowlab_test_support::CooldownRateFixtureMode::ApplyExtendRemove,
            ),
        ),
        (
            "charge_rate ApplyAndConsume",
            wowlab_test_support::charge_recharge_rate_inputs(
                identity(),
                wowlab_test_support::ChargeRateFixtureMode::ApplyAndConsume,
            ),
        ),
        (
            "charge_rate ApplyAndExplicitRemove",
            wowlab_test_support::charge_recharge_rate_inputs(
                identity(),
                wowlab_test_support::ChargeRateFixtureMode::ApplyAndExplicitRemove,
            ),
        ),
    ];
    for (name, (data, actions)) in frozen {
        let scenario = Scenario {
            name,
            data,
            actions,
            input: &default_input,
            rounds: 14,
            host: None,
        };
        let report = run(&scenario);
        print(&report);
        assert_clean(&report);
        assert!(report.reset_full_diff.is_none() && report.iteration_full_diff.is_none());
    }
}
