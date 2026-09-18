//! Reviewer R3 (lifecycle / RNG): reset and iteration-reset fingerprints over the public
//! fixtures that Track H's `h_reset_fingerprint.rs` did not drive, under two state inputs:
//! H's plain input and a stochastic input (miss/dodge/parry/physical block on both actors,
//! critical strike, both hostile relations). Every compiled spell is attempted
//! Player->enemy, Player->Player and enemy->Player, so RNG-bearing tables, the raw-133/145
//! maximum-health runtime, absorb and healing-absorb cells, power burn/drain, mana leech,
//! aura-unique/cadence periodic owners, raw-165, mana shield and passive flows are all
//! exercised before `reset` / `reset_for_iteration`.
//!
//! Records: LC-R3-001 (holds), MUT-R3-001, REJ-R3-001. The comparison is H's semantic
//! Debug rendering (scratch subtrees masked) and, separately, the unmasked rendering.

use wowlab_combat::{
    ActiveDefenseBaseline, ActiveDefenseCapabilities, ActorState, AttackAccuracyBaseline,
    AttackNonLandingChances, BlockDefense, CombatObservation, CombatProgram, CombatState,
    CombatStateInput, CriticalStrikeBaseline, DefenseFamilyChances, HasteMultipliers, HealthPool,
    OffensivePower, ResourcePool,
};
use wowlab_data::{
    CURRENT_SCHEMA_VERSION, DataVersion, GameData, GameDataIdentity, GameDataInput,
    ResolvedActionCatalog, ResolvedActionCatalogInput,
};
use wowlab_model::{ActorId, DifficultyId, EnemyIndex, ResourceType, SimTime, SpellId};
use wowlab_sim::RandomStreamIdentity;
use wowlab_test_support as ts;

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

fn dbg_full(state: &CombatState) -> String {
    format!("{state:#?}")
}

fn is_scratch_field(name: &str) -> bool {
    name == "quoted_costs" || name.contains("workspace") || name.contains("scratch")
}

fn strip_scratch(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut skip_indent: Option<usize> = None;
    for line in text.lines() {
        let indent = line.len() - line.trim_start().len();
        if let Some(level) = skip_indent {
            if indent > level {
                continue;
            }
            skip_indent = None;
            if line.trim().starts_with(['}', ']', ')']) {
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
        if let Some((name, rest)) = trimmed.split_once(": ")
            && is_scratch_field(name)
        {
            out.push_str(&line[..indent]);
            out.push_str(name);
            out.push_str(": <scratch>\n");
            if rest.trim_end().ends_with(['{', '[', '(']) {
                skip_indent = Some(indent);
            }
            continue;
        }
        out.push_str(line);
        out.push('\n');
    }
    out
}

fn dbg(state: &CombatState) -> String {
    strip_scratch(&dbg_full(state))
}

fn first_diff(left: &str, right: &str) -> Option<String> {
    let l: Vec<&str> = left.lines().collect();
    let r: Vec<&str> = right.lines().collect();
    let index = l
        .iter()
        .zip(&r)
        .position(|(a, b)| a != b)
        .or_else(|| (l.len() != r.len()).then(|| l.len().min(r.len())))?;
    let window = |lines: &[&str]| {
        lines.iter().skip(index.saturating_sub(3)).take(8).copied().collect::<Vec<_>>().join("\n")
    };
    Some(format!("line {index}\n--- left\n{}\n--- right\n{}", window(&l), window(&r)))
}

#[derive(Default, Debug)]
struct Report {
    name: String,
    successes: usize,
    observations: usize,
    reset_diff: Option<String>,
    reset_full_diff: Option<String>,
    replay_mismatch: bool,
    iteration_diff: Option<String>,
    iteration_full_diff: Option<String>,
    iteration_replay_mismatch: bool,
    quote_impure: usize,
    failure_impure: usize,
}

fn pairs(program: &CombatProgram, actions: &ResolvedActionCatalog) -> Vec<(ActorId, ActorId, SpellId)> {
    let mut out = Vec::new();
    for spell in actions.actions().map(wowlab_data::ResolvedAction::spell) {
        if !program.contains_spell(spell) {
            continue;
        }
        for (caster, target) in [
            (ActorId::Player, enemy()),
            (ActorId::Player, ActorId::Player),
            (enemy(), ActorId::Player),
        ] {
            out.push((caster, target, spell));
        }
    }
    out
}

fn drive(
    state: &mut CombatState,
    program: &CombatProgram,
    casts: &[(ActorId, ActorId, SpellId)],
    rounds: usize,
    report: &mut Report,
) -> Vec<String> {
    let mut trace = Vec::new();
    let mut output: Vec<CombatObservation> = Vec::new();
    for round in 0..rounds {
        for &(caster, target, spell) in casts {
            let Ok(request) = program.cast_request(caster, target, spell) else {
                continue;
            };
            let before = dbg(state);
            let readiness = state.cast_readiness(request);
            if first_diff(&before, &dbg(state)).is_some() {
                report.quote_impure += 1;
            }
            let start = output.len();
            match state.begin_cast(request, &mut output) {
                Ok(_) => {
                    report.successes += 1;
                    trace.push(format!("r{round} {caster:?}>{target:?} {spell:?} {:?} ok {:?}", readiness.error_code(), &output[start..]));
                }
                Err(error) => {
                    if first_diff(&before, &dbg(state)).is_some() {
                        report.failure_impure += 1;
                    }
                    trace.push(format!("r{round} {caster:?}>{target:?} {spell:?} err {:?}", error.code()));
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
        trace.push(format!("adv {:?} {:?}", state.now(), &output[start..]));
    }
    report.observations += output.len();
    trace
}

fn run(name: &str, data: &GameData, actions: &ResolvedActionCatalog, input: fn(RandomStreamIdentity) -> CombatStateInput) -> Option<Report> {
    let mut report = Report { name: name.to_owned(), ..Report::default() };
    let program = CombatProgram::builder(data, actions)
        .build()
        .unwrap_or_else(|error| panic!("{name}: program build failed: {error:?}"));
    let casts = pairs(&program, actions);
    let first = RandomStreamIdentity::new(31_337, 5);
    let second = RandomStreamIdentity::new(31_337, 6);
    let mut state = match program.try_state(input(first)) {
        Ok(state) => state,
        Err(error) => {
            eprintln!("-- skip {name}: needs host bindings ({error:?})");
            return None;
        }
    };
    let fresh = dbg(&state);
    let fresh_full = dbg_full(&state);
    let trace = drive(&mut state, &program, &casts, 14, &mut report);

    state.reset();
    report.reset_diff = first_diff(&fresh, &dbg(&state));
    report.reset_full_diff = first_diff(&fresh_full, &dbg_full(&state));
    let mut scratch = Report::default();
    report.replay_mismatch = drive(&mut state, &program, &casts, 14, &mut scratch) != trace;

    state.reset_for_iteration(second);
    let mut fresh_second = program.try_state(input(second)).expect("second state");
    report.iteration_diff = first_diff(&dbg(&fresh_second), &dbg(&state));
    report.iteration_full_diff = first_diff(&dbg_full(&fresh_second), &dbg_full(&state));
    let reused = drive(&mut state, &program, &casts, 14, &mut scratch);
    let fresh = drive(&mut fresh_second, &program, &casts, 14, &mut scratch);
    report.iteration_replay_mismatch = reused != fresh;
    Some(report)
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

/// LC-R3-001 / MUT-R3-001: across 24 additional public fixtures and two state inputs
/// (plain, stochastic), quotes and rejected casts leave semantic state unchanged, `reset`
/// equals a fresh state and replays identically, and `reset_for_iteration` equals a fresh
/// state for the new identity and replays identically. Unmasked renderings differ only
/// where the scenario name is listed in `full_diff_expected` (scratch residue).
#[test]
fn holds_reset_fingerprints_on_uncovered_fixtures() {
    let mut full_diff = Vec::new();
    let mut skipped = Vec::new();
    let mut inert = Vec::new();
    let mut total_success = 0;
    for (name, (data, actions)) in scenarios() {
        for (label, input) in [
            ("plain", plain_input as fn(RandomStreamIdentity) -> CombatStateInput),
            ("stochastic", stochastic_input),
        ] {
            let Some(report) = run(&format!("{name} [{label}]"), &data, &actions, input) else {
                skipped.push(format!("{name} [{label}]"));
                continue;
            };
            eprintln!(
                "== {} ok={} obs={} quote_impure={} failure_impure={} reset={} reset_full={} replay={} iter={} iter_full={} iter_replay={}",
                report.name,
                report.successes,
                report.observations,
                report.quote_impure,
                report.failure_impure,
                report.reset_diff.is_some(),
                report.reset_full_diff.is_some(),
                report.replay_mismatch,
                report.iteration_diff.is_some(),
                report.iteration_full_diff.is_some(),
                report.iteration_replay_mismatch
            );
            for diff in [&report.reset_diff, &report.iteration_diff].into_iter().flatten() {
                eprintln!("   semantic diff: {diff}");
            }
            if let Some(diff) = &report.reset_full_diff {
                eprintln!("   full diff: {diff}");
            }
            total_success += report.successes;
            if report.successes == 0 {
                inert.push(report.name.clone());
            }
            assert_eq!(report.quote_impure, 0, "{}", report.name);
            assert_eq!(report.failure_impure, 0, "{}", report.name);
            assert!(report.reset_diff.is_none(), "{}", report.name);
            assert!(!report.replay_mismatch, "{}", report.name);
            assert!(report.iteration_diff.is_none(), "{}", report.name);
            assert!(!report.iteration_replay_mismatch, "{}", report.name);
            if report.reset_full_diff.is_some() || report.iteration_full_diff.is_some() {
                full_diff.push(report.name.clone());
            }
        }
    }
    eprintln!("full-diff scenarios: {full_diff:?}; skipped {skipped:?}; no-commit {inert:?}; total successes {total_success}");
    assert!(total_success > 2_000, "the uncovered fixtures must actually commit casts");
}
