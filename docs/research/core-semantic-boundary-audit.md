# Core semantic boundary audit (read-only)

Status: complete 2026-09-18. Core was never modified. The registry JSON and generated corpora are the
source of truth; this report explains them. Every claim cites pinned Core coordinates, a probe, a DBC
row, or a Trinity/simc line. Where it cannot, it is labelled inferred or unknown.

## 1. Pins and baseline

| Input | Pin |
|---|---|
| Core | `63f3a49124cecf73dee2c34f2b531b1de546c836` (`wip/engine-port`, clean before and after) |
| wowlab-data | branch `research`, base `9448f80c6f2d87c47a93e89b1946f7aaa72e9384` |
| DBC client build | `12.1.0.69497` (`data/tables/`, 1,104 tables) |
| Sidecar | `350de53ef783f6415f7d2e7c86959e22f6d8403a` (navigation only) |
| Trinity | `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` (supports client <= 12.0.7; data is 12.1.0, so the build skew is explicit) |
| simc | `b48def9c26d7532db2e612d97d433ec66bd8eede` |
| TDB | `TDB_full_world_1200.26021_2026_02_06.sql` (sha256 `54ddf4c12d6034a3…`), consulted only by K for the `spell_proc`/`spell_script_names` census |
| Rust | 1.96.1 (Core `rust-toolchain.toml`) |

Baseline quality: `cargo test --workspace --locked` gives 4,632 passed, 0 failed, 2 ignored; `cargo quality`
exits 0 (the allocation suite is 735/735 in debug and release). `core-source-hashes.json` records the sha256
of every Core file any record cites, so a Core change shows up as corpus drift.

Current source was authoritative throughout. Earlier research, both ledgers and the continuation prompt were
used only for navigation, and every consumed claim was reopened against the pinned tree.

## 2. Method

Twelve parallel tracks (A–L) audited disjoint authority sets. A cross-authority agent (X) then compared the
tracks, and three hostile reviewers followed:

- **R1** tried to disprove every finding. It applied one uniform LIVE rule (below) and built real-row
  witnesses.
- **R2** independently re-traced a seeded random sample of 14 "clean" authorities plus 5 shallow ones, and
  attacked 11 rejections.
- **R3** attacked lifecycle, reset/replay, RNG draw counts, numeric recomputation and allocation claims.

Every review correction is applied through `core_audit/registry/reconciliation.json`. That overlay keeps each
track's original values under `original` and records the review trail.

**Uniform LIVE rule.** A finding is LIVE only if both hold:

- a probe reaches the wrong behaviour through public construction (Engine, or the public
  `CombatProgram`/`CombatState` API) and asserts it;
- every source row the witness depends on exists in that shape in 12.1.0 and is admitted by today's
  compilers.

Host-supplied runtime values (stats, haste, health, armor, topology) may take any valid value. A finding
whose witness needs synthetic source rows is LATENT, with the admitting gate named.

**Coverage denominators.** These are extracted mechanically from Core source; `coverage.json` shows zero
uncovered items for each.

| Denominator | Count |
|---|---|
| Production RNG leaf call sites | 9 |
| Combat compiler modules | 112 |
| Runtime modules (inventoried) | 123 |
| `CombatState` + `OptionalCombatAuthorities` fields | 27 |
| Catalog rows across the five catalogs | 555 (154 of them Implemented) |

**Volume.**

| Record kind | Count |
|---|---|
| Executable authorities | 190 |
| Fact-flow traces | 46 (301 stage records) |
| Provenance checks | 60 |
| Cross-compiler concepts | 60 |
| Single-dimension mutations | 305 |
| Numeric boundaries | 98 |
| RNG path records | 25 |
| Mutable-lifecycle owners | 50 |
| Catalog identities | 190 |
| External-policy claims | 15 |
| Allocation contracts | 14 |
| Rejected suspicions | 159 |
| Unknowns | 33 (6 closed by later evidence) |

The probe crate holds 60 files with 198 Rust tests, all passing.

## 3. Headline

39 canonical findings survive after 5 merges and 1 rejection. There are no critical or high findings.

| Reachability | medium | low | info |
|---|---|---|---|
| LIVE | 3 | 15 | 0 |
| LATENT | 2 | 7 | 2 |
| INCONSISTENT | 0 | 5 | 5 |

The one-line conclusions:

1. **Death is not a lifecycle event in Core.** A lethal commit only emits `ActorDied`. Nothing removes auras,
   stops resource flow, cancels pending casts, or blocks casting by or onto the dead. Four canonical findings
   share this gap: CSA-G-01, CSA-E-01, CSA-H-02 and CSA-R2-01.
2. **The fixed-travel launch can panic.** A source-valid, public-API configuration (Illusionary Bolt against a
   magic-block-capable defender) consumes the launch draw and then panics at arrival (CSA-I-02). This is the
   only crash-class defect found.
3. **The selected-package machinery is sound on today's data.** Its two real gaps are an unchecked binding
   family (CSA-A-01, low LIVE through a mis-bound host input) and a final compiler that skips owner policy
   (CSA-A-02, LATENT). The centralized authority-family iterator is safe today (§6).
4. **Numeric divergences are all ±1 unit, ms or basis point.** Seven are LIVE on real rows or host values
   (CSA-D-01/02/04, J-01/02/03/06); CSA-J-07 is a Core-internal inconsistency.
5. **External-policy boundary.** It holds on the SelectedTrait route. The ordinary-Passive route admits the
   same provider rows with no policy (CSA-K-05, LATENT because the current TDB has no proc/script row for any
   of the 549 matching carriers).
6. **Quote purity, failure purity, reset/iteration-reset determinism and warm zero-allocation all hold.** They
   were probed across 53 fixtures and 64 fresh iteration seeds, plus 608 warm iterations in R3.

## 4. Corrective queue

Ranked by severity, then reachability, then blast radius. Coordinates are Core paths. The correction
boundary is the smallest owner. "Do not widen" is what the corrective must not turn into. The full text is in
`findings.json` (`correction_boundary`, `must_not_widen`, `why_tests_missed`, `reproducer`, `evidence`).

### 4.1 LIVE

| # | ID | Sev | Defect | Coordinates | Correction boundary | Do not widen |
|---|---|---|---|---|---|---|
| 1 | CSA-I-02 | medium | Fixed-travel launch resolves a block that arrival's captured-impact contract refuses; `advance_to` panics after the launch draw was committed | `impact.rs:262-280`, `execution/commit.rs:893-906`, `program/fixed_travel_time.rs:140-146` | Make the captured launch table block-ineligible for Magic-defense fixed travel (Trinity never blocks magic), or reject the combination at quote before any draw | Do not relax the `captured()` assertion; do not generalize fixed travel |
| 2 | CSA-G-01 (+H-03) | medium | Dead caster passes readiness and commits casts; a pending hard cast survives caster death and completes (also through Engine) | `cast.rs:412-580`, `execution/health.rs:190-211` | One caster-life predicate in `plan_resolved_cast_core` for Instant/HardAcceptance/HardCompletion, plus generation-safe cancel of the dead actor's pending cast | No ActorState-wide liveness; already-launched impacts and aura-owned ticks keep running |
| 3 | CSA-D-01 | medium | Raw-133 produces a fractional maximum health (1001×1.5 → 1501.5); the admitted raw-165 consumer rejects the domain and the whole cast fails (real 156021 + 1257873) | `state/maximum_health_modifiers.rs:88-103,165-190`, `maximum_health_damage.rs:39-48` | Producer emits the integral maximum (consumer truncation), or raw-165 applies the same truncation | Keep raw-165 fail-closed range checks and binary32 arithmetic |
| 4 | CSA-E-01 (+H-01) | low | Auras persist on a corpse (real 6873). The raw-495 on-expire branch runs on the dead recipient but is LATENT: no admitted real on-expire carrier executes | `expiry_execution.rs:9-40`, `state.rs:1368-1397`, `execution/commit/health.rs:200-235` | One death-lifecycle transition at the lethal health commit that removes affected auras and cancels their periodic cells and expiry deadlines without running natural-expiry drivers | No per-driver ad-hoc liveness that leaves the aura active; no raw 116/226 |
| 5 | CSA-H-02 (+G-04) | low | Aura-only (and raw-68 interrupt) actions commit on a dead target while damage on the same corpse returns `DeadTarget` (real 6873 vs 228917; 159006) | `execution/planning/health.rs:881-889`, `execution/planning/interrupt.rs:28-32` | One action-level target-liveness policy in root planning for every recipient-bound mutation family | No corpse targeting / ALLOW_DEAD_TARGET |
| 6 | CSA-R2-01 | low | Resource authorities ignore death: passive flow accrues on a corpse, Energize commits onto the dead, power is kept at death, raw-396 fires for a dead owner | `execution/resource.rs:281-340`, `execution/commit/health.rs:229` | Same death transition as #4 (settle/stop flows, zero primary power) plus a liveness rule in `quote_gain` | Drains are already gated; no health reads in the warm projection path |
| 7 | CSA-G-02 | low | Interrupting a hard cast (raw 68, Stun, Silence, Pacify) keeps the GCD committed at acceptance (Player caster; real 465275 + 159006 + 58912; CombatState API only) | `cast.rs:223-229`, `cast_authority.rs:423-431` | The cancellation commit restores the cancelled cast's start-recovery cell | Keep ordinary/shared and attr-407 cooldowns; no reset on completion failure |
| 8 | CSA-K-01 | low | Aura remove/stack lowering (raw 164/203/289) checks only the effect-kind family; the source aura identity and the signed amount are never bound to the host step (a row naming aura B removes A). The Set-mode clause was rejected on real data | `program.rs:1595-1690` | `validate_aura_mutation_source`: require `aura == EffectTriggerSpell`, misc 0, and a declared count equal to the signed base points | No Set-stack runtime op; no runtime inference from trigger ids |
| 9 | CSA-A-01 | low | The package binding check flattens families to `(actor, effect)`: a raw-638 member bound only in the discarded auto-attack family satisfies completeness with no owning binding (no white main hand) | `state/construction.rs:244-322,838-858` | Tag each binding with its family; each required member must appear exactly once in its role's family | No generic package store or runtime |
| 10 | CSA-C-01 | low | Hostile impacts with no damage or healing are crit-eligible: the real stun resolves `Critical` from the attacker's weapon crit | `program/payload_policy.rs:33-47`, `program.rs:1180-1205` | At impact-policy finalization, clear crit when the impact has no damage, healing or periodic-health operation | Keep one shared result for mixed payloads; the draw at 0% is not a divergence (Trinity draws too) |
| 11 | CSA-D-02 | low | Only raw-2 rounds authored base points; raw-10 heal and raw-9 leech keep fractions (15278 heals 1.5, not 2; 3243 leeches 5.56, not 6) | `damage.rs:30-37`, `program/damage.rs:157-163`, `healing.rs:71-74` | Round at the raw-10 / raw-9 formula construction, where raw-2 already rounds | No rounding of percent heals or post-modifier amounts |
| 12 | CSA-D-04 | low | Absorb capacity boosted only on the caster side is never rounded (real 256374 + 334538 + 228917: 52.5 vs 53) | `absorb/transaction.rs:902-925` | `application_capacity` rounds the source stage regardless of the recipient factor | Not Mana Shield or healing absorbs |
| 13 | CSA-D-03 (open) | low | Equal-priority absorbs are consumed oldest-first; Trinity newest-first, simc smallest-first (real 53678 + 334538 + 57411) | `absorb/transaction.rs:968-1000` | **Core owner decision**: adopt the consumer tie-break, or record it as an explicit Core choice | Keep funding-first and priority ordering |
| 14 | CSA-J-01 | low | White crit adds raw-290 after the host-summed binary32 basis: `(5+r)+p` vs Trinity `(p+5)+r` (825 vs 824 bp) | `white_swing.rs:32-44,296-313` | Carry the binary32 terms in consumer association, or declare a host contract | Bump the white semantics version; no rating/gear admission |
| 15 | CSA-J-02 | low | Hasted cast/GCD/cooldown/periodic durations divide by an f64 frequency while Trinity multiplies by a binary32 factor: ±1 ms (e.g. 12000 ms at 134.65% → 5113 vs 5114) | `recovery.rs:113-116`, `cooldown/deadline_rate.rs:233-240`, `periodic.rs:551-562` | The timing-multiplier contract: compute `trunc(f32(ms)×mod)` as `white_swing::effective_period` already does | Version deadline/work identity |
| 16 | CSA-J-03 | low | Raw-183 "at or above" uses the exact threshold; Trinity truncates `CountPctFromMaxHealth` (real 213539; max 1001, current 800: Hit vs Critical) | `program/critical_modifier.rs:127-141` | `CriticalTargetHealthPredicate::matches` uses the integral threshold | No new predicates or wakes |
| 17 | CSA-J-06 | low | Raw-423 leaves fractional mana costs (real 1270845 on spell 111: 4.75 vs 4) | `state/power_cost.rs:98-132` | Truncate per component before the clamp, **or** declare continuous costs intentional (UNK-J-001; then info) | No rounding on other resource paths |
| 18 | CSA-J-07 | low | The rotation resource wake uses a different f64 expression from the combat projection and wakes 1 ms late (combat holds 50.0 at 5000 ms; action fires at 5001) | `rotation/src/projection.rs:89-100` vs `combat/src/passive_resource_flow.rs:973-1000` | Rotation evaluates with the exact combat expression and basis | No polling or per-ms wakes |

### 4.2 LATENT (invalid state admitted internally; current upstream does not reach it)

| # | ID | Sev | Defect | Protecting gate today | Correction boundary |
|---|---|---|---|---|---|
| 19 | CSA-A-02 | medium | The selected raw-638 final compiler never runs `selected_trait_owner_policy_issue`. A singleton package compiles with no policy catalog, a Present proc, or a Melee-defense owner. Includes EP-K-012 (trigger "inert" while proc is Present) | The only real raw-638 trait (Martial Expert 117409) is two-effect, and its spell-modifier sibling runs the policy on the same spell | Thread identity-matched policy into `critical_block_amount.rs:256-302` and call the shared check |
| 20 | CSA-K-05 | medium | External proc/script policy is enforced only on SelectedTrait activation. The same provider rows (raw 290, raw 638) compile as ordinary Passive with no policy, trait source, rank or package atomicity (absorbs UNK-B-002) | None of the 549 matching passive carriers has a TDB `spell_proc`/`spell_script_names` row | Route every immutable passive owner admission through one owner-policy predicate |
| 21 | CSA-B-03 (+C-02) | low | Op-15 crit bonus is compiled only for damage payloads but applied to heals at runtime: a matching pure heal crits 200 vs 220; a mixed program's heal gets 220 | Every real heal-only payload matched by a real op-15 provider carries an owner attribute Core refuses (`program/spell_attribute.rs:17-81`) | Compile CriticalBonus over crit-eligible heals, or fail closed |
| 22 | CSA-B-01 | low | Class-mask op-3 PercentFirstEffect ignores mask-overlap multiplicity (15 vs 22.5) | No 12.1.0 pair overlaps on more than one bit | Use `selector_match_count` as the Percent branch does |
| 23 | CSA-B-04 | low | Op-3 PointsIndex0 providers bind but are inert on matching first effects outside raw-87/raw-20 | Every real provider is multi-effect and refused | Typed build error for matched-but-unadmitted target shells |
| 24 | CSA-C-03 | low | HybridMax adds raw-308 to the spell channel before the max (Core 30% vs Trinity 50%) | The only carrier (Vital Clarity 1266748) is nature-only | Add raw-308 after the family max |
| 25 | CSA-E-02 | low | Dispel and mechanic-dispel removals never quote periodic cancellation | Both dispel compilers refuse dispellable auras with periodic effects (`dispel.rs:243-247`, `dispel_mechanic.rs:387-395`); **those gates must stay** | Count removals in `periodic_quote_count` |
| 26 | CSA-G-05 | low | The allow-while-stunned early return also skips Silence/Pacify | The only raw-163 carrier (Full Heal 25840) has an empty prevention mask | Fall through to the silence/pacify fold |
| 27 | CSA-R3-02 | low | Charge-rate rebase truncates and keeps the shared full interval after the aura ends; the drift compounds 1–2 ms per cycle (Trueshot shape: 38998 vs 38999) | Synthetic ids; real raw-148 carrier admission not demonstrated. Core's own test pins the drifted 9_999 | Retain the unscaled base interval, or model queued charges individually |
| 28 | CSA-J-04 | info | Mechanic-duration percent floors; Trinity rounds up (937 vs 938) | The only provider is −10 on an exact 5 s silence | `authored + trunc(authored×p/100)` |
| 29 | CSA-J-05 | info | Raw-286/charge rates divide by `1+p/100`; single-source results can be 1 ms low | No real admitted input reaches it; the two-source witness was wrong (both give 24999) | Consumer order `base × Π(100/(p+100))` |

### 4.3 INCONSISTENT (internal disagreement, no wrong executable state)

| # | ID | Sev | Disagreement |
|---|---|---|---|
| 30 | CSA-A-03 | info | Shared selected owner policy admits raw-416 on a Passive owner; the raw-638 private check rejects it (fails closed) |
| 31 | CSA-K-03 (+B-02) | low | Catalog text for raw 334/290/79/118 claims exact-carrier / one-rank admission, but the generic single-effect path admits more. Values match Trinity; fix the wording or re-gate |
| 32 | CSA-K-04 | low | Ordinary actions silently drop undeclared Unimplemented/uncataloged sibling effects, contradicting `spell_effect.rs:32-39` |
| 33 | CSA-K-02 | low | Catalog-Disabled raw 468 is executable through the public API |
| 34 | CSA-F-01 | low | Raw-319 melee-speed bindings are validated only by white admission and discarded otherwise |
| 35 | CSA-G-03 | low | Stun short-circuits the prevention fold: `recheck_at` returns the Stun end while a longer Pacify blocks |
| 36 | CSA-B-05 | info | Raw-108 catalog row and op-0 payload set claim missing-or-maximum heals; runtime correctly never applies them |
| 37 | CSA-I-01 | info | Immune impacts draw on the ordinary path but not on fixed-travel launch (replay alignment only) |
| 38 | CSA-R3-01 | info | Source-trial draw policy differs per site (raw-38 skips the singleton and RollDispel trial; white and raw-108 force theirs); needs one versioned draw-identity policy |
| 39 | CSA-L-01 | info | A cold 16-byte package-provenance header sits inline in the warm-read `PassiveAmountModifierPlan`; the ledger scopes that cost only to selected SpellModifier programs |

**Merged:** CSA-B-02 → K-03; CSA-C-02 → B-03; CSA-H-01 → E-01; CSA-H-03 → G-01; CSA-G-04 → H-02.
**Rejected:** CSA-D-05. Trinity applies school immunity only to negative effects (`Unit.cpp:7797-7800,7890-7891`),
and caster raw-165 is positive (`SpellInfo.cpp:5062`), so Core matches it. Only the wording at `spell_effect.rs:262`
is stale.

**Suggested order for the Core agent:** #1 alone (crash class, local fix). Then the death lifecycle as one
milestone (#2, #4, #5, #6), because four findings share one correction point: the lethal health commit. Then
#3 with #16 (both are integer health domains, XC-X-013). Then #7–#10. The numeric family (#11–#18) needs one
policy decision on integral amounts (UNK-D-002 / UNK-J-001) before piecemeal fixes. Then the LATENT pair
#19/#20, before any new selected or passive family is admitted.

## 5. Why the tests missed them (patterns)

- **Death is never a precondition.** Death tests kill targets and recipients and check that damage stops. No
  test casts from a dead actor, applies an aura to one, lets an aura on a corpse expire, or regenerates on one.
- **Fixtures sit on exact values.** Integral base points (heals 30/100), baselines of 1000, round haste (1.25,
  1.5, 2.0), 20/80 mana costs and +100% rates all hide every ±1 truncation divergence.
- **A sibling masks the gap.** The live two-effect Martial Expert package runs owner policy through its
  spell-modifier sibling, so the raw-638 compiler's omission never shows (CSA-A-02). Stun/Silence/Pacify test
  states have no crit baseline, so the crit-eligibility gap never shows (CSA-C-01).
- **Only one mutation dimension was exercised.** The b24359bd corrective mutated presence, multiplicity and
  actor, but not the binding family (CSA-A-01). The fixed-travel tests never gave the defender a block
  capability (CSA-I-02).
- **Catalog snapshot tests compare text, not the admitted population** (CSA-K-02/K-03).

## 6. Selected-package atomicity verdict (track A, reviewed by X/R1/R2)

Each requirement from the mission, with its current verdict:

| Requirement | Verdict | Evidence |
|---|---|---|
| Every source sibling represented or whole package rejects | holds | `admit_composition` classifies the complete effect set; any `None` rejects (`selected_trait_package.rs:789-808`) |
| Unknown siblings cannot be filtered away | holds | probe `a_package_contract.rs` (unknown-subtype sibling rejects) |
| Order cannot determine role | holds | per-effect classification; multiset composition; source-order permutation probes |
| Provenance lossless on the supported domain | holds | `(entry_id,effective_rank)` retained in the cold resolved catalog and the package-effect box; all nine selected final compilers consume the selected-rank amount (raw-638 class re-checked for every authority, PL-A-*) |
| Declarations atomic | holds | `selected_trait_effect_is_declared` for every member with exact `SelectedTrait(selection)` activation |
| Binding none-or-complete per actor/package | **fails in one dimension** | CSA-A-01: family is not checked |
| Duplicates reject | holds | `DuplicateSelectedPassivePackageBinding` (b24359bd) |
| Split actors reject where required | holds | per-actor lookup |
| Unrelated packages cannot satisfy completeness | holds | per-source effect sets; one effect belongs to at most one source (REJ-A-*) |
| One valid actor cannot mask another orphan | holds | per-(actor, effect) counting |
| Ordinary authorities remain owners | holds | no package runtime, driver, deadline, RNG or `CombatState` field (L confirms no state-side provenance) |
| No generic mutable trait/package runtime | holds | |

**The centralized iterator** (`state/construction.rs:284-322`, 5 families): safe today. Multi-effect
compositions admit only the 10 role kinds in `composition_role_slot`, and all 10 map onto the 5 iterated
families. Every other role kind (HasteAll, AllCriticalChance, raw-308, MeleeAutoAttackSpeed,
DamageDone/Taken, AbsorbReceived and the unslotted modifier value kinds) can only be admitted as a
single-effect package, and single-effect packages are never recorded as packages. `selected_passive_package_source` returns the first match (`find`), but one exact effect cannot
belong to two sources: declarations are unique per effect and per `(entry, rank)`, and the owner-role count
must match.

**The future-omission risk is real but not current.** A new composition slot mapped to a role whose binding
family is not in the iterator would make that package impossible to bind (it over-rejects). It would not
silently admit a partial package. The composition gate and the iterator are two lists that must change
together. No refactor is demanded. The corrective for CSA-A-01 should key the check on role → family, which
makes that coupling explicit.

## 7. Cross-compiler matrix (60 concepts)

Classification counts:

| Class | Meaning | Count |
|---|---|---|
| A | direct-consumer difference | 8 |
| B | intentional architecture | 15 |
| C | redundant harmless | 7 |
| D | accidental inconsistency | 22 |
| E | masked by unrelated rejection | 5 |
| F | unknown | 3 |

The D and E entries that carry semantic consequence are the findings above. The rest of the D entries are
divergent validation of the same fact with no reachable wrong state. The main ones:

- dead-target checks per operation (damage/heal/instakill reject, aura/interrupt/energize accept);
- base-point rounding per effect kind;
- owner-attribute policy on raw 638 vs the shared check;
- binding-family validation on the white path vs the non-white path;
- haste scaling as binary32 multiply (white) vs f64 divide (cast/cooldown/periodic);
- dispel vs expiry removal periodic cancellation.

See `cross-compiler-matrix.json` and `reviews/X-reconciliation.md` in the team workspace.

## 8. Provenance loss (60 checks)

| Pattern | Count |
|---|---|
| lossless | 23 |
| selected_amount_to_authored | 11 |
| retained_never_consumed | 9 |
| dropped_then_reconstructed | 7 |
| retained_never_validated | 4 |
| exact_effect_to_provider | 2 |
| package_to_authority_family | 2 |
| premature_f64_promotion | 2 |

Fourteen records carry the `finding` verdict:

- **selected_amount_to_authored:** every selected final compiler now re-resolves the selected rank, so the
  only live form of this pattern is the ordinary-Passive route of CSA-K-05, where no rank exists.
- **dropped_then_reconstructed:** the aura-mutation lowering reconstructs the aura from host input instead of
  the source trigger (CSA-K-01).
- **package_to_authority_family:** CSA-A-01.
- **retained_never_consumed:** CSA-L-01 (cold header inline), CSA-F-01 (melee-speed bindings discarded).
- **premature_f64_promotion:** the rotation and combat projection disagreement (CSA-J-07). J found the curve
  and rank evaluation bit-identical to Trinity (REJ-J-004).

## 9. Mutation outcomes (305 single-dimension mutations)

| Outcome | Count |
|---|---|
| rejected at intended boundary | 156 |
| accepted and semantically inert | 65 |
| accepted and suspicious | 71 |
| unreachable | 7 |
| rejected accidentally by an unrelated gate | 4 |
| inconclusive | 2 |

Every accepted-suspicious mutation maps to a finding or to a rejected suspicion with its reason recorded. The
four accidental rejections are the class-E masks:

- op-15 heals masked by owner attributes (B-03);
- negative raw-193/319 masked by the package points policy (REJ-F slow formula);
- dispel periodic masked by the dispel compilers (E-02);
- raw-638 policy masked by its sibling (A-02).

**Latent defects hidden by unrelated gates.** Do not widen those gates until the masked defect is corrected.

## 10. Numeric boundaries (98)

| Evidence class | Count |
|---|---|
| direct_consumer_reproduced | 33 |
| core_choice | 32 |
| known_divergence | 26 |
| source_backed | 4 |
| assumed | 2 |
| unknown | 1 |

**Matches Trinity bit for bit (probed):**

- armor mitigation;
- white outcome table (strict `roll < cumulative`);
- raw-344 factor;
- white period;
- SpellMod factors;
- cooldown SpellMod;
- school cost percent;
- max mana;
- max-health damage;
- rank curve evaluation;
- crit doubling after armor;
- 30% capped refresh carryover.

**Known divergences:**

- ±1 ms haste division (J-02);
- raw-290 association (J-01);
- raw-183 truncation (J-03);
- mana-cost truncation (J-06);
- max-health truncation (D-01);
- base-point rounding (D-02);
- absorb source rounding (D-04);
- mechanic-duration rounding (J-04, latent);
- charge interval drift (R3-02, latent).

**Discriminating witnesses:** each divergence has a probe asserting Core's value next to the consumer's
formula.

**Reviewer correction (R3/R1).** Trinity forms the recovery-rate product before scaling, so J's original
two-source witness (25000) was wrong. The probe is now `holds_recovery_rate_two_source_amounts_match_consumer_product_order`,
and CSA-J-05 dropped to info/LATENT.

**Retail vs Trinity.** Trinity is an emulator of an older client. UNK-J-002 records that retail arithmetic
has not been independently established.

## 11. RNG / quote / commit

Nine production leaf sites:

| Site | Call |
|---|---|
| `attack/resolution.rs:471,497,503,511` | attack table: `ordered_outcome` + `occurs` for crit/block/crit-block |
| `execution/commit.rs:228` | raw-38 dispel selection |
| `execution/commit.rs:460` | forced mechanic-dispel trials |
| `execution/planning/dispel.rs:116` | dispel preview |
| `white_swing/resolution.rs:78,81` | forced weapon draw, then outcome draw |

**Proved per site:**

- draw count and order (re-derived independently by R3);
- quotes and hard-cast acceptance draw nothing;
- rejected casts draw nothing (quotes use cloned streams);
- no fallible calculation after a draw, except CSA-I-02, where arrival panics after the launch draw;
- actor input order cannot change draw order (actors sorted by id);
- reset and iteration reset restart the stream exactly (`RandomStreamIdentity::for_iteration`);
- no cleanup path consumes RNG.

**Caveats:**

- "Exactly one draw" is "at least one": rejection sampling can redraw with probability ≈8.8e-17 (R3).
- Draw-occurrence policy differs by site (CSA-I-01, CSA-R3-01). Outcome-neutral, but relevant to gate 4
  replay alignment.

## 12. Mutable lifecycle

Lifecycle records cover all 27 root fields plus 23 records for nested owners and root fields that needed
separate reset entries:

- **Nested owners:** CastAuthority, WhiteMainHandRuntime, IndependentStackAuthority, StartRecoveryState,
  SchoolLockAuthority, ResourceThresholdRuntime, PeriodicCell, PassiveResourceFlowStorage.
- **Construction and reset:** no reset topology drift between construction, `reset`, `reset_for_iteration` and
  `reset_runtime` (H fingerprint probes over 29 fixtures + 20 scenarios; R3 extended to 24 more fixtures ×
  2,999 casts, including independent stacks and the max-health, absorb, crit and defense draws H had not
  exercised).
- **Purity:** quotes and failed host calls mutate nothing observable. The only leftovers are absorb scratch,
  which is inert.
- **No runtime lookups:** there are no catalog or world lookups at runtime.
- **Death:** the lifecycle gap is §3 item 1.

## 13. Five-catalog consistency

190 catalog records:

| Verdict | Count |
|---|---|
| consistent | 175 |
| finding | 13 |
| unknown | 2 |

- **Implemented rows without an executable consumer:** none.
- **Disabled rows leaking through generic admission:** raw 468 (CSA-K-02).
- **Exact-carrier wording wider than admission:** raw 334/290/79/118 (CSA-K-03).
- **Catalog claims stronger than the code:** raw-108 missing-or-maximum heal (CSA-B-05).
- **Uncataloged identities silently accepted:** ordinary-action siblings (CSA-K-04).
- **Ignored facts affecting state:** none (every use is an allow-list). UNK-K-001 asks whether Ignored raw 103
  (DotStackingRule) should matter.
- **Implicit-target axes:** match Trinity.

## 14. External-policy trust boundary

**Holds (11 claims):**

- the catalog rejects incomplete or foreign input;
- a missing row is never read as Absent;
- a catalog bound to other game data counts as absent;
- Present or Unknown in any of the 9 columns rejects (18/18);
- candidates only publish untrusted structural inputs, and the final `CombatProgram` compiler decides;
- nothing retains the catalog warm or looks it up at runtime;
- Disabled raw-416 is accepted only when all 9 columns are Absent.

**Breaks:**

- the selected raw-638 route (CSA-A-02 + EP-K-012);
- the ordinary-Passive route (CSA-K-05).

**No generator exists** for `SpellPolicyCatalogInput` in any repo (UNK-K-002), so the host side of the trust
boundary cannot be audited yet. That is the separate canonical-evidence project, not this audit.

## 15. Allocation / pay-for-play

**Holds:**

- all 735 allocation-suite tests pass in debug and release;
- the Precision Strikes and Martial Expert budgets reproduce exactly;
- the package compiler allocates nothing without a package;
- the binding check allocates nothing;
- `CombatProgram` clones allocate nothing;
- 99 reset functions allocate nothing;
- 64 fresh seeds and 608 warm iterations are allocation-free.

**Only deviation:** CSA-L-01 (info, 16 cold bytes).

**Open:** whether Engine observation storage can grow in a later iteration (UNK-L-001/UNK-R3-002).

## 16. Hostile-review corrections applied

| Review | Correction |
|---|---|
| X | 5 merges; B-03 LIVE→LATENT (real heals refused by owner attributes); NUM-E-001/B-005/B-006/G-001 are the J-02 conversion (bidirectional ±1 ms); 6 unknowns closed |
| R1 | D-05 rejected; C-01 narrowed (self buffs never crit; draw-at-0% not a divergence; witness rebuilt without creature spell crit); E-01 on-expire branch LATENT; K-01 Set-mode clause rejected; K-04 LIVE→INCONSISTENT; B-01/B-04 LIVE→LATENT; severity down on A-01, A-03, E-01, G-02, I-01, K-01; real-row witnesses built for D-01, D-03, D-04, E-01, G-01, G-02, H-02, J-03 (J's 193878 is refused; 213539 used), J-06, R2-01 |
| R2 | New CSA-R2-01 (resources vs death); 11 rejections attacked, all upheld; B's synthetic-only mask claim confirmed on real data |
| R3 | I-02 correction boundary rewritten (Trinity never blocks magic); J-05 → info LATENT and its probe corrected; L-01 → info INCONSISTENT; H's coverage claim was overstated and is now extended; new CSA-R3-01 and CSA-R3-02 (R3-02 set to LATENT by the lead under the uniform rule) |

## 17. Notable rejected suspicions (159 total)

- **REJ-A-*** (5 binding families vs 12 roles; `find`-first source lookup; package recording only from the
  spell-modifier compiler): safe today (§6).
- **REJ-F-011:** a white-admitted program can never kill its source, so "a dead source keeps swinging" is
  unreachable (R2/X).
- **REJ-C-004:** raw 638 read from the defender is supported by the Martial Expert tooltip.
- **REJ-J-004:** the rank curve matches Trinity exactly.
- **REJ-J-006:** SpellMod product order is pointer-ordered in Trinity, so there is no source order to violate.
- **REJ-L-001:** compiled definitions carry no per-definition `(entry, rank)`; it lives only in the cold
  resolved catalog. The continuation prompt's wording is accurate only for that catalog.
- **REJ-X-003:** direct Energize amounts are documented host-authoritative (ledger 1643-1649). Aura-mutation
  amounts have no such note, which is why CSA-K-01 stands.

## 18. Unknowns and reopen conditions (27 open)

The decisions most likely to change the queue:

- **UNK-D-002 / UNK-J-001:** is Core's continuous f64 amount and cost domain an explicit architecture choice?
  If yes, D-02, D-04 and J-06 drop to info. If no, one integral-domain corrective covers D-01, D-02, D-04,
  J-03 and J-06.
- **UNK-D-001:** the absorb tie-break (CSA-D-03).
- **UNK-J-002:** retail arithmetic vs Trinity for every ±1 divergence.
- **UNK-K-002:** a trustworthy policy-catalog generator.
- **UNK-K-003:** other generic-passive families on the ordinary-Passive route.
- **UNK-C-001 / UNK-C-004:** crit on DefenseType::None damage and on raw-67 heals. Conflicting evidence.
- **UNK-E-001..003:** refresh cadence and same-millisecond tick vs cast.
- **UNK-F-001..003:** swing vs cast-start ties, binary32 attack time in retail, and the Dark Bite
  InterruptFlags audit.
- **UNK-X-001:** the recipient of (0,0) implicit-target effects.

All 33 are in `unknowns.json` with `reopen` conditions. The 6 closed ones carry `closed_by`.

## 19. Reproduction

From `scripts/research`:

```console
python3 tools/regen_core_audit.py --check   # regenerate all 18 corpora; exit 1 on any drift
python3 tools/core_audit_tests.py           # uv pytest (tests/test_ca_registry.py) + cargo test of the probe crate
python3 core_audit.py finding CSA-I-02      # one finding with every record citing it
python3 core_audit.py coverage              # mechanical denominators lacking an audited record
```

**Regeneration** is offline. It fails closed if Core is not at the pin with a clean tree, if any registry
record breaks the schema, or if any record cites a Core coordinate that does not exist. Two consecutive builds
must also be byte-identical.

**The probe crate** `core_audit/probe/` path-depends on the sibling `../../../../../core` crates. It uses
public APIs only, has its own `Cargo.lock` and `target/` (gitignored), and was built offline with the pinned
toolchain.

**Artifacts:**

| What | Where |
|---|---|
| Registry | `scripts/research/core_audit/registry/<kind>/<TRACK>.json` (14 kinds, tracks A–L, X, R1–R3) |
| Lead overlay | `scripts/research/core_audit/registry/reconciliation.json` |
| Corpora | `docs/research/core-audit-corpora/` (18 files, 1.2 MB) |
| Team workspace (not tracked) | `/home/dev/bag/core-audit-pass/`: BRIEF, ASSIGNMENTS, per-track sections and handoffs, reviews (X-reconciliation, R1, R2, R3), and the scratch generators that produced each track's registry JSON |
