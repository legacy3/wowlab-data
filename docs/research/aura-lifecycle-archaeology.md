# Aura lifecycle archaeology

Status: complete 2026-09-18, after three hostile reviews and one correction round. This is research only: Core was never modified and no Core milestone is selected.

TrinityCore is the detailed consumer oracle here, not Retail truth. Where Trinity is the only evidence, the claim is labelled `trinity-only` and has a Retail experiment.

The corpora under `docs/research/aura-lifecycle-corpora/` are the source of truth. This report explains them. Every count names its population.

## 1. Pins

| input | pin |
|---|---|
| wowlab-data | branch `research`, base `c8958bc` (clean at start) |
| primary DB2 snapshot | Wago `12.1.0.69497` (`data/tables/`, 1,104 tables; SpellEffect `157f4d94…`, SpellMisc `3ef1315b…`, SpellAuraOptions `6063ce2a…`, SpellDuration `ba8dd9df…`) |
| drift DB2 snapshot | dbc-resolver Dump release `wow-12.1.0.69814-4aec2a19e0a9` (archive sha256 `7087b64d…`, 157 tables). `tools/fetch_dbc_release.py` fetches it into the workspace scratch dir; it is never committed |
| TrinityCore | `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` (supports client ≤ 12.0.7.68453, so build skew is explicit) |
| TDB | `TDB_full_world_1200.26021_2026_02_06.sql` (sha256 `54ddf4c1…`) + 522 updates, via the existing overlays |
| Core | `63f3a49124cecf73dee2c34f2b531b1de546c836` (`wip/engine-port`), read-only navigation; `git status` empty before and after |
| Sidecar | `350de53ef783f6415f7d2e7c86959e22f6d8403a` (not needed) |
| simc | `b48def9c26d7532db2e612d97d433ec66bd8eede` (second consumer) |
| client API docs (track L) | Gethe/wow-ui-source `7828252` (client 12.1.0.69875), cited in the `retail-experiments.json` provenance |

## 2. Infrastructure reused and added

Reused unchanged:
- The Dummy-pass `Bundle`: snapshot catalog, TDB server overlay, tree-sitter script index, dispatch tables and build skew.
- The targeting context (`Scope`, `TargetingData`, the aura-target-map port and its probe).
- The proc pipeline (`procs/`).
- `selected_package` and the controlled-unit spell list.
- The Core audit registry and probe crate. Track K re-ran its 22 lifecycle probe tests; all pass.

Added under `scripts/research/`:
- `aura_lifecycle/`: the lead skeleton (`context`, `providers`, `records`, `cli`, `oracle`, `model`, `cmd_core`, `cmd_x`) and the modules of twelve tracks. The CLI is `aura_lifecycle.py`.
- **Eight verbatim-Trinity differential probes**: `tools/tc_aura_{identity,duration,stack,periodic,removal,area,order,r2}_probe/`. Each extracts Trinity function bodies unchanged, compiles them against minimal stubs, and is compared against the Python model by tests.
  - Probe scope (R2-13): a probe checks the extracted components. The call order between components is written by hand in each probe driver, so cross-component order claims carry `trinity-consumer` as primary evidence and the probe as support only.
- `tools/fetch_dbc_release.py` measures build drift against the newer client build published by the dbc-resolver Dump pipeline.
- `tools/regen_aura_lifecycle.py --check` is the single regeneration path.

## 3. Denominator and populations

A **provider effect** is a DIFFICULTY_NONE `SpellEffect` row for which `SpellEffectInfo::IsAura` holds. The snapshot has **253,257 provider effects in 189,140 provider spells** (`providers.py`).

| population | provider spells | definition |
|---|---:|---|
| all | 189,140 | every provider |
| player | 4,218 | `Scope.reach`: class trees, spec spells, current gear/sets/gems/enchants and their authored trigger reach. This is the population every earlier pass used. |
| player+class-skills | 4,520 | player, plus the spells of *class* skill lines and their trigger reach. A class skill line is a `SkillLine.CategoryID = 7` line whose `SkillRaceClassInfo` rows name exactly one player class. |
| controlled | 141 | current controlled-unit abilities |

Notes on `player+class-skills`:
- It adds baseline class abilities such as Power Word: Shield 17, Shadow Word: Pain 589 and Bear Form 5487.
- The first version used `Scope(include_class_skills=True)` (6,343 spells). R1-01 showed that 82% of its additions came from the Mounts skill line. The corrected definition drops Mounts, Companions, Internal and the pet-family lines structurally, with no names involved.
- Renew 139 and Immolate 157736 are on no 12.1.0 skill line, so they are not in this population.

Other population facts:
- One passive definition is used everywhere (R1-02): `passive.effective_passive`, which is `SPELL_ATTR0_PASSIVE` plus the SpellMgr load-time corrections (flight icon 135754, spell 59630). It gives 16,812 passives in all and 3,453 in player.
- Two provider spells (182749, 1281745) have no DIFFICULTY_NONE SpellInfo and fail closed everywhere.

Census membership never implies executable support in Trinity, Core or anywhere else.

## 4. Headline results

1. **"Reapplying a SpellId refreshes it" is not a rule.**
   - Passives never refresh (3,453 player): same caster without an item replaces the old aura, and an item-sourced one coexists (64 player).
   - On non-passive player auras (AL-R-X-13), 47 of 763 are counterexamples: 41 self-channels, whose recast cancels the old channel before the hit, and 6 AURA_UNIQUE auras that skip the timer refresh.
   - Other casters coexist, replace, are blocked (area auras), or share one object. That depends on stack capacity, `ATTR3_DOT_STACKING_RULE`, spell groups and the area pipeline (§5).
2. **Pandemic in pinned Trinity ignores the remaining duration on the spell-hit path** (AL-D-D-02, merged with AL-D-B-01).
   - The ATTR13 branch reads the aura duration after `RefreshTimers` has already reset it. The commit is `min(hit + M, trunc(1.3·hit))`, with M the recalculated max.
   - For combo-point records M is the minimum duration, so Rupture at 5 CP gives 28,000 ms whatever time remains.
   - It is reached by 505 of 699 ATTR13 providers in all, and 50 of 61 in player (`carryover.json` → `pandemic_population`). The exclusions are non-positive durations, self-channels, and 6 unique non-stacking auras, which read the live remaining time.
   - Non-spell refresh paths (AddAura, steal, vehicle, effect 289, linked REAPPLY, scripts) never reach the branch and end at duration = max.
   - This is a defect relative to Trinity's own intent, its pre-2025 code, simc and Core. Retail is unknown (AL-U-B-01).
   - The earlier "130% of remaining carry, strongly verified" claim (`remaining-data-audit/08`) is withdrawn (§17).
3. **Death is not natural expiry.** The probe covers the components; the order between them is established from source.
   - The mode differs, and there is no final tick: expiry fires 4 ticks, a death 50 ms earlier fires 3.
   - EXPIRE-gated handlers and scripts are skipped. Linked remove-casts are skipped only on death.
   - Before the death sweep, leaving combat removes LeavingCombat-flagged auras with INTERRUPT, channels end with CANCEL, and the unit exits its vehicle.
   - A dying caster does remove some auras it put on others: dynamic objects of the spell being cast, charm auras, the vehicle control aura, and totem auras.
   - Player death classes: 712 removed; 44 (+9) survive through ATTR3; 3,384 (+69) survive as passives. The bracketed figures are those that are also disabled while dead.
4. **Tick amounts are live; many non-periodic amounts are snapshots** (`snapshot-matrix.json`, 60 rows).
   - Base points and stacks are captured at creation, reapply, stack change or explicit recalculation.
   - Spell power, attack power, versatility, %done, periodic spell mods, target modifiers and the crit roll are read at every tick.
   - Mastery points are snapshotted for auras owned by others, which covers every DoT on an enemy.
   - School absorbs snapshot caster-done and holder-taken bonuses at creation. Crowd-control auras with proc flags snapshot 10% of the holder's max health.
   - Effect attribute 0x8000 ("compute at cast") is ignored by Trinity and honoured by simc, a known divergence.
5. **Refresh cadence depends on (spell, delivery path, trigger flags).**
   - On the spell-hit path, the tick phase restarts iff StackAmount < 2, there is no ATTR13, and the cast lacks `TRIGGERED_DONT_RESET_PERIODIC_TIMER`, which is part of `TRIGGERED_FULL_MASK`.
   - AddAura, effect 289, linked sync, spell steal and AuraScript stack changes restart the phase unless ATTR13, whatever the StackAmount (AL-T-D-21..23).
   - A restart-reapply of a raw-169 aura adds an extra tick.
   - This answers Core UNK-E-001/002 for Trinity's spell-hit path only. Effect 289 is itself authored in DB2, so "not a DB2 property" is too strong; the correct statement is that it is not a property of the aura spell's own rows.
6. **Trinity's scheduler order, established from source call paths** (track I; the probe checks components).
   - Session packets run first. Then each player is updated, followed by the unvisited cells around it.
   - Within a unit: its own events, then its owned auras in SpellId order (duration, target map, ticks), then expiry removal.
   - A tick due at expiry fires.
   - A lethal tick denies same-ms ticks of higher-SpellId auras only for auras the death sweep removes.
   - A unit whose cell is not visited freezes, and the lost time is never credited back.
   - B's refresh timelines assumed "update, then hit". That holds only for hits delivered after the owner update.
7. **Stacks, charges and proc state are separate counters**: aura stacks, aura proc charges, spell category charges, independent objects, and proc clocks. They have exactly two couplings: a refresh resets charges, and `PROC_ATTR_USE_STACKS_FOR_CHARGES` makes a proc remove a stack.
   - Initial stacks are never the capacity. They are 1, plus Doses mods or `SPELLVALUE_AURA_STACK` overrides.
   - Phalanx 1269312 has capacity 2, starts at 1 and never stacks.
   - Creation does not clamp to capacity; refresh does (AL-D-A-03).
8. **Area auras are one parent object with per-recipient applications.**
   - Duration, stacks, charges, amount and tick timer belong to the parent.
   - Membership is rechecked every 500 ms.
   - A recipient that removes a foreign-owned area aura (interrupt, remove-by-spell) gets it back at the next recheck (R3-10).
   - Dynamic-object expiry reaches recipients as DEFAULT, not EXPIRE (AL-D-F-03).
9. **"Passive means immutable" is false.** 1,049 of 3,453 player passives carry mutable state (track G definition). The lead rule AL-R-X-03 counts only stack, charge or periodic state (87). Pinned Trinity permanently loses passives that carry implemented interrupt flags, e.g. Veteran of the Third War 48263 after every login (AL-D-G-01, trinity-only).
10. **Lifecycle is not decided by DB2 alone.** 372 player (4,628 all) providers have a lifecycle surface touched by world data or scripts. In addition:
    - `spell_proc` rewrites charges.
    - Linked spells act at apply and remove.
    - Every refresh runs removal-side code (AL-D-H-01).
    - Scripts set remove modes themselves (33 sites), so EXPIRE does not always mean the duration ran out.
11. **Authored attributes with no Trinity consumer**: raw 489/490, `PvPDurationIndex`, IGNORE_OWNERS_DEATH, HEARTBEAT_RESIST, FINISHING_MOVE_DURATION, and 8 interrupt-flag bits (27 player occurrences). Each is an unknown paired with an experiment.
12. **State space on real data.** The ten policy axes give:

    | population | distinct tuples | of which singletons |
    |---|---:|---:|
    | player | 402 | 283 |
    | player+class-skills | 458 | — |
    | all | 3,616 | — |

    - One tuple (passive, permanent, nothing mutable) covers 2,843 player spells.
    - The only near-dependency between axes on player spells is RefreshPolicy → StackPolicy, with 25 violations. Both are computed from the same inputs.
13. **34 likely Trinity defects** were reproduced and marked, never corrected (§15).

## 5. Identity model (track A; `identity.json`, probe `tc_aura_identity_probe`)

**Stage 1, the refresh lookup** (`Unit::_TryStackingOrRefreshingExistingAura`, Unit.cpp:3386-3445), keyed on:
- owner;
- SpellId;
- caster, unless the spell is stackable on one slot with different casters (StackAmount>1 ∧ ¬channeled ∧ ¬ATTR3_DOT_STACKING_RULE);
- cast item, only with `CU_ENCHANT_PROC`;
- an equal aura effect mask.

Difficulty, CastId and generation are not keys.

**Stage 2, on a miss:** a new object is created, and `CanStackWith` plus `_RemoveNoStackAurasDueToAura` decide which existing objects go.

| second application | player | all | witness |
|---|---:|---:|---|
| other caster coexists | 3,615 | 61,698 | Agony 980 (per caster via DOT_STACKING_RULE) |
| other caster replaces | 491 | 114,632 | Mark of the Wild 1126, Power Infusion 10060 |
| one shared object across casters | 90 | 10,616 | Divine Hymn 64844 |
| area aura: second owner blocked / coexists on recipients | 17 / 5 | 1,051 / 312 | Devotion Aura 465, Beacon of the Savior 1270083 |
| same caster: refresh / replace / coexist | 765 / 3,384 / 69 | — | coexist = item-sourced passives |

**Ownership details:**
- A shared object keeps the first caster's GUID but takes the latest applier's base points (Unit.cpp:3409-3419).
- A stolen copy keeps the enemy caster.
- Effect 289 targets any caster's aura.

**Storage vs identity.** The multimap, `AuraKey` and the client slot are storage or presentation, not identity. Retail's observable identity candidate is the client-assigned `auraInstanceID`; whether a refresh keeps it is AL-X-L-01.

**Differential.** `SpellSpecific`, `CanStackWith` and the refresh lookup agree with verbatim Trinity on 188,309 unit-aura providers. The area-aura class rests on source reading: `_RemoveNoStackAurasDueToAura` and `UpdateTargetMap` are not in that probe.

## 6. Application model (tracks A, F)

- The object is created at the first unit-owned aura effect and holds all of them.
- Effects take hold only after the same spell's heal, damage and HIT procs (Spell.cpp:2896/2938/2980, then `_ApplyAura` at 3040). A spell's direct damage never sees its own debuff (witness Haunt 48181).
- Partial construction exists: immune effect, dead recipient (object without application), DR to 0, no-stack removal inside `_AddAura`, script removal mid-apply, slot ≥ 300.
- Area auras have no application at hit. Every application appears at the owner's next update.

## 7. Snapshot vs dynamic (track D; `snapshot-matrix.json`, 60 rows)

| input | Trinity timing |
|---|---|
| base/rank points, Points mods, combo points, variance | application-snapshot; recalculated on refresh and stack change; explicit recalculation for Points mods on own passive/permanent auras |
| stack count | recalculated on stack change (× stacks unless SuppressPointsStacking) |
| SP/AP × coefficient, versatility, %done, BonusCoefficient/PeriodicHealingAndDamage mods | dynamic each tick (caster-live-lookup); an absent caster gives 0; a dead caster is still present |
| taken modifiers | dynamic each tick (target-live-lookup) |
| crit | rolled each tick from the spell-mod owner's live chance (pets and guardians use the owner's) |
| mastery points (ATTR8) | recalculated for auras the caster owns on itself; **snapshotted on auras owned by others** |
| haste → period; SpellMod Period; channel cast-time mods; SpellMod Duration; target duration mods and DR | application-snapshot, recalculated on refresh only (script exceptions: Arcane Tempest, zone scripts, DoEffectCalcPeriodic) |
| school absorb amount | snapshot of caster-done + holder-taken at creation (`m_canBeRecalculated = false`, but SetStackAmount recalculates anyway: AL-D-C-05) |
| crowd-control amount with proc flags | 10% of the holder's max health, snapshot |
| target max health/power (Damage%, ObsMod*) | target-live-lookup |
| periodic trigger / dummy | computed in the triggered cast / script-controlled |
| effect attribute 0x8000 | ignored by Trinity; simc snapshots it (known divergence, AL-U-D-06) |

## 8. Duration families (track B; `duration.json`)

**Authored records** (all): fixed 89,488; permanent `-1` 83,399; passive without a record (→ -1) 16,167; zero 40; active without a record (→ 0) 13; per-resource 30; negative non-sentinel 1 (101822 → 600 s).

**What they mean at runtime:**
- Only exactly `-1` is permanent. `0` expires at the first update.
- `MinDuration` is a missile-travel floor, not an aura floor.
- `PvPDurationIndex` has no consumer (391 all / 37 player).
- Per-resource duration needs a minimum > 0. AL-D-B-02: Envenom 32645 always gets 0 ms.
- ATTR8 floors to whole hastened periods.
- **AL-D-B-04 (latent):** the ATTR8 branch for auras without a period dereferences a possibly null original caster. 23 all / 2 player providers reach it.

## 9. Stack and charge families (track C; `stacks.json`, `charges.json`)

| stack family | all | player | controlled |
|---|---:|---:|---:|
| single-refresh (capacity 0) | 151,556 | 616 | 94 |
| multislot, no reapply path | 16,678 | 3,441 | 45 |
| stacking shared across casters | 10,641 | 91 | 0 |
| single, no timer refresh (AURA_UNIQUE) | 6,331 | 6 | 0 |
| capacity-1 refresh | 3,363 | 39 | 0 |
| stacking per caster | 433 | 13 | 2 |
| multislot with dormant capacity (Phalanx) | 136 | 12 | 0 |

**Charge families** (all): none 187,761; proc charges 945; charges without a proc entry 398; stacks-as-charges 22 (+12 with dormant charges).

**Reapply behaviour** (probe-confirmed): a reapply is `ModStackAmount(createInfo.StackAmount)`. The cap applies only to increases. It refreshes iff the count does not drop and (capacity ≠ 0 or not AURA_UNIQUE). A refresh resets charges to the live `CalcMaxCharges`.

**Asynchronous stacking** (Attributes_15 0x400 / raw 490) has no Trinity consumer (70 all / 6 player).

## 10. Periodic and refresh families (tracks B, D)

| spell-hit branch | all | player | controlled |
|---|---:|---:|---:|
| new object (passive + 3 hardcoded ids) | 16,814 | 3,453 | 45 |
| refresh | 133,312 | 576 | 83 |
| stack and refresh | 14,119 | 142 | 2 |
| self-channel: cancel, then create (R2-07) | 19,276 | 41 | 11 |
| unique, no timer refresh | 5,619 | 6 | 0 |

**Mechanics** (D, probe components):
- Fourteen aura types tick; aura 70 never does (AL-D-D-01).
- The period is binary32, computed at create and refresh only.
- The tick budget `MaxDuration/period (+1 with raw169)` is re-read at every update:
  - there is no partial final tick;
  - script duration changes break it (AL-D-D-05 silent tail, AL-D-D-06 lost ticks);
  - after a phase-keeping refresh the budget restarts from zero, so the last tick can be dropped (AL-D-D-04; 321 all / 38 player effect rows at haste 1.0).
- Time is credited in whole owner updates: early credit, a candidate artefact (AL-D-D-03), and catch-up bursts capped by the budget.

**Census:** 40,304 ticking effects in all, 257 in player. Delivery paths are listed in `periodic.json` → `delivery_paths`.

## 11. Removal reasons (track E; `removal.json`, 47 sites)

| reason | mode | final tick | notes |
|---|---|---|---|
| natural expiry | EXPIRE | yes, before removal | unit auras |
| failed periodic trigger cast (duration set to 0) | EXPIRE | — | R2-10, TL-E-11; EXPIRE does not mean the duration ran out |
| script-set modes | as set | no | 33 sites (Painbringer EXPIRE decay, Alter Time EXPIRE) |
| dynamic-object expiry | DEFAULT | yes | AL-D-F-03 |
| death | DEATH | no | §4 item 3 (order, caster-side exceptions) |
| client cancel | CANCEL | no | only if not NO_AURA_CANCEL, the spell is positive and not passive |
| interrupt flags | INTERRUPT | no | no passive exemption; 8 bits have no consumer |
| mount / travel form / vehicle | INTERRUPT (flagless at Unit.cpp:8466) / DEFAULT | no | R3-03 |
| dispel / steal / mechanic dispel / absorb depletion | ENEMY_SPELL | no | charge or stack when present; never refreshes |
| stack or charge to zero | caller's mode | no | last proc charge DEFAULT, spell-mod charge EXPIRE; `SetStackAmount(0)` leaves a live aura |
| single-target cap eviction | DEFAULT | no | R3-01; moving Lifebloom 33763 → the old one does not bloom; 29 player |
| strongest-wins group displacement / self-removal | DEFAULT | no | R3-02 |
| aura-state loss | DEFAULT | no | 4 player passives toggle |
| logout / pet dismiss (save, remove, rebuild) | DEFAULT | no | new object on login; positive auras do not count down offline |
| totem unsummon; spell cancel removes its dynamic objects | DEFAULT | no | R3-13/14 |
| negative-id linked removal | DEFAULT | no | any caster; runs on death |
| non-stackable replacement, shapeshift, unequip, evade, arena entry, group leave, target-map loss, periodic cost unpaid, spell_area | DEFAULT | no | — |
| ghost-only sweep | DEFAULT | no | at login only; resurrection removes only 8326/20584 |
| map change | — | — | the unit keeps its own auras; cleanup/despawn/logout use RemoveAllAuras |

**Cross-cutting facts:**
- No removal path ever runs a tick.
- A caster's DoTs survive its death or despawn; each tick re-reads the caster.
- On a corpse, surviving damage/heal ticks are silent, but trigger and dummy ticks still act. Removing a creature corpse ends the survivors with DEFAULT.

**Dispel:** each attempt draws `urand` and then an integer `irand(0,99)`. Selection gives each aura identity one entry. A resisted roll spends the attempt, and removals wait until every attempt has rolled. Core's periodic-dispel refusal is kept as a negative witness (AL-K-E-01).

## 12. Same-timestamp ordering (track I; `ordering.json`, 19 probe scenarios)

- **Fixed by code:** tick then expiry; a charge drop before expiry; the target map before ticks; event-queue ties run FIFO.
- **Fixed by SpellId:** a lethal tick against same-ms ticks of higher-SpellId auras, only for auras the death sweep removes.
- **Decided by the delivery channel:** refresh, dispel or stack change vs a due tick; a cast by a caster that is not the owner vs a tick; a refresh vs the old expiry.
- **Decided by map update order:** cross-unit pairs, and AreaTrigger lists vs unit countdowns.
- **Generation:** remove-then-reapply always creates a new object; triggered spells hold value copies. 13 aura-pointer holders are classified, including a dangling AreaTrigger pointer (AL-D-I-02).

## 13. External-policy dependencies (track H; `external-policy.json`)

Player surfaces touched by world data or scripts:
- proc 244
- application 53
- removal 44
- amount 35
- periodic 32
- charges 20
- stacks 19
- identity 17
- duration 14
- recipients 11
- refresh 6

Other external rules:
- Linked spells act at apply and remove.
- `spell_proc` overrides charges for 36 spells and stacks-for-charges for 34.
- Reapply-flagged script hooks run on refresh (91 all / 4 player).

Coverage limit (AL-U-H-02): an untouched surface means no indexed input, not "decided by DB2".

## 14. Numeric boundaries (`numeric-boundaries.json`; complements the Core audit map)

| boundary | all / player | evidence |
|---|---|---|
| hasted period `int32(f32(period) × speed)` | 463 / 57 spell-haste effects | trinity-probe; Core ±1 ms is CSA-J-02 |
| pandemic commit `min(hit+M, trunc(f32(hit)·1.3))` reached | 505 / 50 | source + probe components; Core integer carry diverges from 516,250 ms |
| last tick dropped after a phase-keeping refresh (haste 1.0, no duration mods) | 321 / 38 | trinity-probe |
| durations > 2^24 ms | 1,171 / 0 | source-backed |
| stack uint8 wrap / negative Doses / negative ProcCharges UB | 158 / 0; player witnesses | trinity-probe |
| ATTR8 duration floor | 302 / 37 | trinity-probe |
| tick on application (+1 budget) | 6,124 / 56 | trinity-probe |
| per-second cost timer stores 0; zero hasted period loops | 41 / 1; latent | source-backed |
| dispel roll integer `irand(0,99)` with rejection sampling | all dispels | trinity-probe |

## 15. Trinity defects (34; `unknowns.json` → `trinity_defects`)

Reproduced and marked, never corrected.

**Identity**
- A-01: PRESENCE ids → Death's Advance removes Veteran of the Third War.
- A-02: WARLOCK_ARMOR family flags → Burning Rush and Soul Leech are mutually exclusive.
- A-03: creation does not clamp stacks.

**Duration and refresh**
- D-02 (= B-01): pandemic reads the refreshed duration.
- B-02: per-resource duration needs a minimum > 0.
- B-03: rolling periodic compounds the done bonus.
- B-04: null original caster (latent).

**Stacks and charges**
- C-01..05: uint8 wrap, negative max stacks, negative ProcCharges UB, Set 0 leaves a live aura, SetStackAmount ignores `m_canBeRecalculated`.

**Periodic**
- D-01: aura 70 never ticks.
- D-03: early credit (candidate).
- D-04: budget reset while the phase is kept.
- D-05/06: script duration changes vs the budget.

**Removal**
- E-01: Lifebloom null caster.
- E-02: dispel groups by caster pointer.
- E-03: aura 70 null caster, latent.
- E-04: ATTR7 zero-charge aura never dispellable.

**Area**
- F-01: overlapping AreaTriggers.
- F-02: per-recipient cooldown events.
- F-03: dynamic-object expiry reaches recipients as DEFAULT.

**Passive**
- G-01: interrupt flags lose passives.
- G-02: set-bonus/mastery removal is caster-blind.
- G-03: ChangeTalent never fires.

**Overlays**
- H-01: removal-side code on every stack change.
- H-02: linked-spell load log.

**Ordering**
- I-02: dangling AreaTrigger pointer.
- I-03: unique+ATTR13 keeps its tick counter.
- I-04: zero period loops.
- I-05: per-second cost timer stall.

## 16. Semantic axes and falsification (lead; `semantic-axes.json`, `falsification.json`)

Each axis is computed per provider by the owning track's classifier, never re-implemented.

| axis | owner | player values | what it separates |
|---|---|---:|---|
| AuraIdentity | A | 11 | lookup scope × second-caster outcome (incl. area blocked/coexist, dynobj) |
| ApplicationPolicy | A, F, G | 13 | passive / channel / active × pipeline (static, unit-area, dynobj, areatrigger) |
| AmountPolicy | C, D | 12 | base-captured/bonuses-live, CC snapshot, absorb snapshot, suppress/aura-points stacking, rolling, compute-at-cast |
| DurationPolicy | B | 4 | authored duration family |
| StackPolicy | C | 7 | §9 families |
| ChargePolicy | C | 5 | which counter a proc or dispel decrements |
| PeriodicPolicy | D | 14 | finite/permanent × haste mode × tick-on-apply × uncovered tail |
| RefreshPolicy | B | 9 | spell-hit branch × timer × carry |
| RemovalPolicy | E | 36 | death class × interruptible × dispellable × no-cancel × form-bound × item requirement |
| ExternalPolicy | H | 60 | touched surface:source sets |

Track J's lifecycle signatures are a separate partition: 8,536 path / 23,816 full classes in all; 540 / 1,038 in player. 13 of 219 multi-member player full classes contain more than one axis tuple. Signatures and axes are therefore different models, not the same model twice.

**Lead rules.** Predicates read axis values only, so a rule cannot hide a spell list.

| id | candidate rule | status | player applies / counterexamples |
|---|---|---|---|
| X-01 | same SpellId + same caster refreshes (all owned auras) | discarded | 4,216 / 3,500 |
| X-13 | … non-passive only | discarded | 763 / 47 (41 self-channels, 6 unique) |
| X-02 | passives have no refresh path | source-backed (the classifier fixes it) | 3,453 / 0 |
| X-03 | passives carry no stack/charge/periodic state | discarded | 3,453 / 87 |
| X-04 | passives are permanent | refined (0 player, 9 all) | 3,453 / 0 |
| X-05 | death removes every non-passive aura | discarded | 765 / 53 |
| X-06 | a refreshed periodic aura restarts its rhythm | discarded | 169 / 77 |
| X-07 | capacity ≥ 2 stacks per caster | discarded | 104 / 91 |
| X-08 | lifecycle decided by DB2 alone | discarded | 4,218 / 372 |
| X-09 | charges and stacks are one counter | discarded | 91 / 80 |
| X-10 | amount = base × stacks, bonuses live, uniformly | discarded | 4,218 / 177 |
| X-11 | pandemic carry reads the remaining (finite auras) | source-backed; census 50 / 50 | 50 / 50 |
| X-12 | periodic duration is a whole number of periods | discarded | 158 / 4 |

Every rule also reports its build-skew share.

**Track rules.** There are 117, with 127 falsification entries, and their full history is kept. After R1, 21 track rules that describe code paths rather than data regularities are labelled `source-backed`; the track's original label is kept under `track_status` (`oracle.STATUS_OVERRIDES`).
- Final track statuses: trinity-only 61, source-backed 21, holds-on-census 16, discarded 8, refined 8, proposed 3.
- Rules that died include: "raw169 only on first application", "haste change reschedules", "ATTR13 only on periodic auras", "per-caster identity", "EXPIRE means the duration ran out", "dynamic objects survive caster death", and "the phase is kept iff StackAmount ≥ 2" (true on the spell-hit path only).

**What survives as a compact model.**
- Identity, refresh branch, stack family and charge family are Trinity-default functions of:
  - DB2 plus a small attribute set;
  - spell groups, hardcoded ids and SpellMgr corrections;
  - live spell mods (MaxAuraStacks, Doses, ProcCharges);
  - `spell_proc`.
  They become combined policies wherever H's surfaces touch them.
- These are **not** functions of the aura spell's rows, and must be explicit inputs or fail closed:
  - periodic cadence on refresh (delivery path + trigger flags);
  - the removal mode (path-dependent; scripts set modes);
  - same-ms order (delivery channel, map order);
  - anything ExternalPolicy touches.

## 17. Errata to earlier research

- `remaining-data-audit/08`: the ATTR13 "130% of remaining carry, strongly verified" claim does not hold at runtime for pinned Trinity (AL-F-B-01; AL-D-D-02).
- `core-audit-corpora` NUM-E-002 reads Trinity's pandemic as remaining-based; at runtime Trinity reads the refreshed duration (AL-K-I-03). Core's CappedCarryover follows simc and Trinity's evident intent (AL-K-B-01).
- Core audit UNK-E-001/002 are answered for Trinity's spell-hit path (§4 item 5) and remain open for Retail and other delivery paths.
- Core audit UNK-E-003 is answered for owner = caster. For caster ≠ owner, the answer depends on map order.
- `targeting/caps.py:42` maps aura 220 (`MOD_ABILITY_SCHOOL_MASK`) as label-pct; Trinity's label-pct aura is 218 (`SpellAuraDefines.h:305-307`). Reported by track C. The targeting pass's code and corpora were not changed here.

## 18. Core reopen opportunities (navigation only; `core-navigation.json`, 32 records + 63 track notes)

- **Identity.** Core keys auras by `(spell, source, target)`: no shared any-caster slot, no replace-on-other-caster, no area-blocked class, no per-effect-mask key, fixed recipients. UNK-K-001 now has Trinity's answer (§5).
- **Host-authored inputs** (AL-U-K-01 = Core UNK-K-002): initial stacks, max stacks, base duration and cadence have no source check.
- **Refresh.**
  - CappedCarryover follows simc, not pinned Trinity (AL-K-B-01).
  - TL-K-01: a 250 ms aura refreshed at 225 ms expires at 500 ms in Core and 550 ms in Trinity (AL-K-B-04).
  - The integer carry diverges from 516,250 ms (AL-K-B-02).
  - Raw 489/490 carry simc meanings (AL-K-B-05).
  - Core has no delivery-path dimension for cadence.
- **Periodic.**
  - Authored per-action cadence (AL-K-D-01).
  - Tick-on-apply on fresh application only (AL-K-D-02).
  - Per-tick haste and a partial final tick (AL-K-D-03).
  - Same-ms ranks match Trinity only for owner = caster (AL-K-D-04).
- **Removal.**
  - Core records no removal reason.
  - Death removes no auras (CSA-E-01/G-01).
  - No cancel, interrupt or leave-combat path.
  - Dispel is limited to single-stack, non-periodic Magic (CSA-E-02, AL-K-E-01).
- **Stacks.** No Doses-driven initial stacks, no uint8, no shared slot (AL-K-C-01..06).

None of these is a prescription or a milestone.

## 19. Retail experiment candidates (`retail-experiments.json`, 68: 32 exact / 30 approximate / 6 insufficient)

**Observation surface of the 12.x client:**
- Addon aura data (`C_UnitAuras`, `UNIT_AURA`) becomes secret during combat, encounters, Mythic+ and PvP unless a spell is flagged never-secret.
- Addons cannot register the combat-log event (12.0.0).
- In combat, `WoWCombatLog.txt` is the only exact channel, and it has no aura instance id and no remaining duration.

**Core set:**
- AL-X-L-01: `auraInstanceID` across a refresh.
- AL-X-A-01/02: second-caster replace or keep; the shared-slot caster.
- AL-X-B-01/02: pandemic remaining (Corruption, Rupture 5 CP).
- AL-X-D-01/02: cadence and tick-on-apply on reapply.
- AL-X-E-01/05: expiry payoff on death; a moved Lifebloom.
- AL-X-E-06/07/08: area-aura flicker, offline countdown, interrupt bits with no consumer.
- AL-X-G-01: passives vs interrupt flags.
- AL-X-L-04: `GetRefreshExtendedDuration` vs the server.

Every experiment carries its models' predictions, the observable, its fidelity, and the per-population generalisation of its answer.

## 20. Unknowns (`unknowns.json`)

- There are 88 unknowns. 7 are merged into canonical ids (`oracle.RECONCILIATION`); merged records stay in the corpus with `merged_into`.
- Every unknown has a stable id, source coordinates, an evidence class, a blocker and a reopen condition.
- `explain <spell>` lists the unknowns whose axis values match the spell (`oracle.AXIS_UNKNOWNS`), not only those that name it.
- Largest open groups:
  - Retail pandemic/cadence (AL-U-B-01, D-01..08);
  - Retail death/removal (E-01..22);
  - cross-caster identity (A-01/02, C-04);
  - attributes with no consumer (K-02, J-01/02, B-02, E-09);
  - the 12.x observation surface (L-01..08).

## 21. Research oracle

From `scripts/research`:

    python3 aura_lifecycle.py explain <spell>          # every track's view + axes + matching unknowns/experiments
    python3 aura_lifecycle.py census [--spell N]      |  identity <spell> [--vs S]  |  application <spell>
    python3 aura_lifecycle.py duration|refresh|stacks|charges|periodic|snapshot|removal|dispel|lifetime|recipients|passive|overlays <spell>
    python3 aura_lifecycle.py periodic-timeline <scenario.json>
    python3 aura_lifecycle.py signatures | drift | ordering | numeric | generation | coremap | experiments
    python3 aura_lifecycle.py model --corpus | unknowns --corpus | falsification --corpus
    python3 aura_lifecycle.py rows <spell> [--drift]

Per-spell commands fail closed: exit 3 with the reason.

## 22. Regeneration and tests

    cd scripts/research
    python3 tools/fetch_dbc_release.py                       # once: drift snapshot into the workspace scratch dir
    python3 tools/regen_aura_lifecycle.py --check            # 8 probes + every corpus in dependency order, then byte comparison
    uv run --no-project --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/test_al_*.py -q -p no:cacheprovider

## 23. Hostile reviews

Three reviewers tried to disprove the draft. Reviews are in `bag/aura-lifecycle-pass/reviews/R{1,2,3}.md`; their tests are `tests/test_al_r{1,2,3}_*.py` and the `tc_aura_r2_probe`. Every confirmed item was corrected by the owning track, then everything was regenerated.

**R1, overgeneralization** (21 items: 7 confirmed, 9 weakened, 5 survived).
- Confirmed and fixed:
  - the class-skill population was 82% mounts;
  - two passive definitions were in use;
  - the amount axis lacked the CC and absorb snapshots;
  - the pandemic rule was a tautology and included permanent auras;
  - cadence ignored the delivery path;
  - channels were not separated;
  - J's attribute list was missing seven consumed attributes.
- Weakened, now reworded:
  - the headline 1 split;
  - item-sourced passives;
  - the "small attribute set" claim;
  - exact-only redundancy;
  - 21 code-path rules relabelled `source-backed`.

**R2, snapshot and timing** (19 items: 5 confirmed, 8 weakened, 6 survived).
- Confirmed and fixed:
  - the report's 684/61 pandemic count appeared in no corpus;
  - script duration changes vs the tick budget;
  - the dropped-tick population used the wrong formula and included self-channels;
  - failed trigger casts end the aura as EXPIRE;
  - the null-caster ATTR8 branch.
- Weakened:
  - cadence scope;
  - pandemic scope;
  - channels in refresh censuses;
  - missing snapshot rows (period, cast time, duration);
  - mastery snapshot on foreign auras;
  - the lethal-tick scope;
  - frozen cells;
  - probe-scope labels.
- Survived:
  - the pandemic formula on the spell-hit path;
  - the binary32 boundary at 516,250 ms;
  - four independently re-derived timelines;
  - the B-vs-D settlement.

**R3, ownership and removal** (28 items).
- Confirmed and fixed:
  - nine missing removal paths (single-target eviction, strongest-wins groups, mount/vehicle, aura state, save/reload, totems, spell cancel, negative linked ids, and, from R2, the failed trigger cast);
  - RM-19 was wrong;
  - dynamic objects do not survive caster death, and a dying caster removes charm, vehicle and totem auras;
  - "other caster replaces" was wrong for area auras;
  - removals of a foreign area aura are temporary.
- Weakened: interrupt bits with no consumer, map change, the script census (mode setters), corpse ticking, shared-object base points, the absorb-overkill kill path.
- Survived: dispel selection and draws, stack/charge-to-zero modes, area DEFAULT vs EXPIRE, "no removal path runs a tick", "death is not expiry", the player death-class counts.

**Still unfalsifiable as stated**, recorded so nobody relies on them:
- early credit as a "defect";
- the cross-unit half of the scheduler claim (the probe driver encodes it);
- `auraInstanceID` identity against Trinity;
- "no consumer ≠ no Retail behaviour" lines without an experiment.
