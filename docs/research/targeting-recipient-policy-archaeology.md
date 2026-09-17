# Targeting and recipient-policy archaeology

## Scope and result

This report answers one question for the pinned consumer (TrinityCore):

> Given an authored spell effect, its caster, the explicit target, the surrounding units, their
> party/raid relationships and the runtime state, how are the exact set and order of recipients
> derived for each effect?

This is research only. It does not modify Core, select Core milestones or design a production
targeting architecture. It also does not port Trinity's `Spell` / `SpellInfo` / `SpellScript`
object model. Where Core vocabulary appears (§22), it is a mapping, not a proposal.

The report is a navigation and synthesis document. The machine-readable corpora in
[`targeting-corpora/`](targeting-corpora/) and the executable oracle
(`scripts/research/targeting.py`) hold the reproducible details. Each section names the command
that regenerates its numbers.

    current client data     != content supported by pinned Trinity
    no consumer in Trinity  != no Retail behaviour
    structural similarity   != semantic equivalence
    Trinity is a detailed consumer oracle, not unquestionable Retail truth

**Headline.** Targeting in the pinned consumer does **not** collapse into one clean
"candidates → filters → order → cap" pipeline per effect. The recurring shape exists, but it is
spell-scoped rather than effect-scoped, and several stages are coupled in ways a per-effect model
would miss:

1. **Selection runs per effect group, not per effect.** `SelectSpellTargets` (Spell.cpp:741-788)
   groups later effects with the first effect when their TargetA/TargetB, condition container,
   PlayersOnly bit, script target hooks and (for searched selectors) radius are all equal. The group
   is selected once, with the *lead* effect's values: one search, one random-cap draw set, and the
   lead's ChainTargets. 2,235 current-player spells have a grouped selecting group.
2. **Recipients merge into one spell-wide unique list.** An effect's recipients are the list
   entries carrying its bit, in first-insertion order. The per-effect search order does not decide
   it (§12).
3. **TargetA and TargetB are a union, never a refinement.** They couple only through the
   spell-wide `m_targets` src/dst, which later effects also read (§2).
4. **Hit/miss RNG is drawn during target selection**, per new unique target and interleaved with
   later groups' cap draws. There is one shared RNG stream (§11).
5. **"Smart healing" is not a generic engine feature.** It is two shared helpers called by
   scripts, plus the chain-heal deficit rule. Their ranking uses binary injured/player/grouped
   bits, not health deficit (§7).
6. **Script target adapters run before the engine cap**, and units a script inserts bypass the
   searcher checks (§14).
7. **AreaTriggers are a second, independent recipient pipeline** that uses world-DB shapes (§5.4).
8. **Area auras are a third pipeline.** The spell only picks the aura *owner*; the area-aura
   recipients are recomputed by the aura itself every 500 ms around that owner (§5.5). The first
   draft missed this pipeline; hostile review R1 found it.

Over 10,754 current-player effects on 4,836 spells, targeting closes as follows:

| verdict | effects |
|---|---:|
| understood | 10,179 (10,029 "simple": no area/group/chain/smart/script/random/controlled-unit/world/destination/geometry/areatrigger/aura-map family) |
| understood with a marked likely Trinity defect | 47 |
| fixture-dependent (needs a stated world fact: visit order, LOS, collision, runtime state) | 464 |
| blocked | 42 |
| unresolved | 22 |

The heterogeneity is real: 30 semantic variants across 16 script-adapter families, and three
separate recipient pipelines (spell selection, AreaTriggers, aura target maps). The report does
not force it into a smaller vocabulary than the direct consumers support (§14, §22).

## Pins

| pin | value |
|---|---|
| data snapshot | Wago `12.1.0.69497` (`data/tables/`) |
| wowlab-data base | `71a3fe8` (branch `research`; this pass is uncommitted working-tree state) |
| TrinityCore | `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` (client builds ≤ 12.0.7.68453) |
| world DB | `TDB_full_world_1200.26021_2026_02_06.sql` sha256 `54ddf4c12d6034a3c61e9b5683c2578de82d826d9090041a5e0477a164f5172d` + the 522-file `sql/updates/world/master` replay. It is used through `dummy-corpora/trinity-server-overlay.json` and the new extracts in `targeting-corpora/inputs/` (training dummies + difficulty flags, `disables`, AreaTrigger polygon/spline/orbit rows + conditions source 28) |
| toolchain | Python 3.12.3, `uv run --with pytest==8.4.2 --with hypothesis==6.140.3`, `g++ 13.3.0 -std=c++20 -O0 -ffp-contract=off` (libstdc++ 13; sort/shuffle tie behaviour is pinned to it) |
| Core (read-only vocabulary) | `../core` @ `999f25fe` |

Material source tables (sha256):

| table | sha256 |
|---|---|
| SpellEffect | `157f4d949964b05531f75dca8b5e9c45a91637104125a7f9e86474b4a3ec196f` |
| SpellMisc | `3ef1315badafd00ceec0640b6859831893cba47a428bdbf869f2ecf0682ce4fa` |
| SpellTargetRestrictions | `d661b991ebc1f833cddc741db1ba5cfd510f71ade6494b0171f6db7da8340761` |
| SpellRadius | `ab4b56313597b825f18225a63078c629ecfb19bc85e1e69f1d464f4c9c87a863` |
| SpellRange | `97ca333390180ea36c30ed8b50af360f913a516857eabf0d7c12fa39d1701092` |
| SpellAuraRestrictions | `b6597468f87e99112ba08a98d537b4287ab9f2c134b4c133776ca2e17ba9ad91` |
| SpellCastingRequirements | `92405214bbbe2c515fca6ff6659a5668594217aae447fde2ec139ead471f46a4` |
| SpellShapeshift | `e66b6b5bc19aef280492f9a29e1ce3e55fb1f6916639b1ad01e41904bbac2de0` |
| SpellName | `d715dbf11027d5baa981e833099014ea19e38eaf1b27815af69c079bc94dbee7` |
| Difficulty | `5b5909ea3319a9596ba4472256ef8901e24c67d2b10638259bfabc652268a23e` |

Evidence classes, used in every corpus row:
- `db2-fact`, `world-db-fact`
- `trinity-consumer` (with `file:line`)
- `trinity-probe` (verbatim Trinity text compiled and run)
- `differential` (Python oracle vs probe)
- `script-consumer`, `structural-inference`, `legacy-only`, `build-skew`, `unresolved`

All `file:line` references are under `TrinityCore/src/server/game/` unless another root is named.

Scope: **current-player** = `dummy_semantics.scope.Scope`. That is class trait trees, spec spells,
current gear, sets, gems and enchants, plus authored trigger/learn/override reach: 4,836 spells in
40 specs. Controlled-unit abilities (`controlled-unit-corpora`) and the children cast by scripts,
AreaTriggers and linked spells are counted in separate layers (§17.3).

---

## 0. Where to start

| question | command (from `scripts/research`) | corpus |
|---|---|---|
| what does selector N do? | `python3 targeting.py selector N` | `selectors.json` |
| how is spell S / effect S:E targeted? | `python3 targeting.py spell S`, `targeting.py effect S:E`, `targeting.py effect-groups S`, `targeting.py adapter S`, `targeting.py world-spell S`, `targeting.py rows S` | all |
| exact recipients for a situation | write a fixture (schema in `targeting/fixture.py`), then `targeting.py evaluate F` / `explain F [--explicit-stages]`; for AreaTriggers use `targeting.areatriggers.evaluate_tick` | `witnesses.json` |
| explicit-target validation stages | `targeting.py validate F` | `explicit-validation.json` |
| relation predicates | `targeting.py relation F from to` | `relations.json` |
| census / per spec | `targeting.py census` | `census.json` |
| what is unknown and how to reopen it | `targeting.py unknowns` | `unknowns.json` |
| does the oracle still match Trinity? | `targeting.py differential` | `differential.json` |
| regenerate / prove freshness | `python3 tools/regen_targeting.py [--check]` (~6 min) | all |

The oracle **fails closed** (exit 3) whenever the fixture does not state a fact the consumer reads.
Examples: map visit order, LOS, collision-dependent positions, hit-roll draws, unmodelled script
hooks, conditions, positivity for load-order-dependent spells.

---

## 1. Implicit-target vocabulary

`targeting/selectors.py`; corpus `selectors.json` (`targeting.py track-a-all`); probe
`tools/tc_target_selector_probe`.

- **The catalog is probe-verified, not hand-transcribed.** `SpellImplicitTargetInfo::_data`
  (SpellInfo.cpp:246) and `SpellEffectInfo::_data` (:959) are compiled verbatim, together with
  `CalcDirectionAngle`, `IsArea`, `GetTargetFlagMask`, `GetExplicitTargetMask` and
  `_InitializeExplicitTargetMask`.
  - `TOTAL_SPELL_TARGETS` = 153 and `TOTAL_SPELL_EFFECTS` = 356.
  - All five axes, the binary32 direction angle and the explicit masks match the Python catalog
    for every id, and match the explicit masks of all 4,836 current-player spells (differential).
- **Routing.** `SelectEffectImplicitTargets` (Spell.cpp:945) dispatches by selection category:
  - CHANNEL → 1029, NEARBY → 1089, CONE → 1273, AREA → 1326, TRAJ → 1878, LINE → 1964;
  - DEFAULT → CasterDest 1465 / TargetDest 1653 / DestDest 1690 / CasterObject 1742 /
    TargetObject 1807 by object type and reference;
  - NYI → debug log only.
  - No current id reaches an `ABORT_MSG`.
- **Behaviour that does not follow from the axes alone.** The id selects a routine, and some
  routines have selector-specific branches:
  - 54 CONE_180, whose default is dead: TG-A-D1;
  - 115 FURTHEST: sort and truncate;
  - 118 ALLY_OR_RAID;
  - 120 CASTER_AND_SUMMONS;
  - 105/122/123 passengers/threat/tap: no radius, no check;
  - 45 CHAINHEAL: the chain-heal branch;
  - 17 DEST_DB: effect-type dependent;
  - 72 DEST_CASTER_RANDOM: a no-op distance line (TG-A-D2).

  `selectors.json → catalog[].handler` records each branch. `SelectEffectTypeImplicitTargets` adds
  an effect-type-dependent fallback for every effect (325 current effects have no implicit
  selector at all).
- **A known selector id is not a complete target policy.** The rest comes from grouping (§12),
  the other slot (§2), explicit validation (§3), per-candidate and per-effect checks (§4-5), caps
  (§10), chains (§8), conditions (§15) and scripts (§14).
- **Census.**
  - 10,754 effects, 85 raw (A,B) pairs (84 after load corrections).
  - TargetA: 1 (9,135), 6 (578), 0 (327), 21 (137), 87 (101), 18 (93), 22 (85), 53 (52),
    104 (50).
  - TargetB: 0 (10,493), 16 (105), 15 (53), 31 (31).
  - Per-spec and per-root-kind counts are in `selectors.json`.
  - Controlled units: 837 spells / 1,312 effects / 55 pairs. TRAJ 89 and the ENTRY areas (22,7)
    and (18,8) appear only there.
- **Core catalog cross-check** (read-only, `core/crates/dbc/src/implicit_target.rs`):
  - Core has 153 rows, and **all five axes agree for every id**.
  - There is one semantic naming difference: id 57 is `UnitCasterTargetRaid` in Core, but both
    catalogs give it reference TARGET.
  - No selector id ≥ 153 occurs in 12.1 player or pet data.
  - Core's admitted exact pairs (0,0), (1,0), (6,0), (21,0), (25,0) cover 10,190 player effects;
    564 are not covered.

## 2. TargetA / TargetB composition

`targeting/composition.py`; corpus `composition.json`. Evidence: trinity-consumer + differential over all 153² pairs.

1. **Order and inputs.** A runs, then B, with the *same* lead `SpellEffectInfo` and the same
   grouped effect mask (Spell.cpp:787-788).
2. **Union, never refinement.**
   - Recipients go into `m_UniqueTargetInfo`; a duplicate ORs its mask (2466-2470). **B never
     filters A.**
   - `SpellEffectInfo::CalcRadius(TargetB)` falls back to TargetA's radius entry *and TargetA's
     selector* when B has none (SpellInfo.cpp:790-795).
3. **Coupling only through the spell-wide `m_targets`.**
   - DEFAULT DEST/SRC selectors call `SetDst`/`SetSrc`.
   - DEST/SRC-referenced area and line selectors read `GetDstPos`/`GetSrcPos`. These never return
     null; `RemoveDst` only clears the flag.
   - DEST_DEST and TRAJ call `CheckDst()` (caster fallback), then `ModDst`.
   - UNIT_AND_DEST calls `ModDst(referer)`, which ASSERTs HasDst (TG-A-D3; 0 current uses).
   - LAST reads the last unique target carrying the group's first effect bit.
4. **No reset between effects.**
   - With the exact effect-group plan, 17 effects in 15 spells read a dst after an earlier turn
     wrote it; in 11 effects / 10 spells the value really comes from an earlier turn (in the other
     6 the effect's own TargetA rewrites it first). Example: Mass Entanglement 102359:1. An earlier
     pair-only count (8) undercounted (hostile review R1-05).
   - `AddDestTarget` snapshots the dst per effect at the end of that effect's turn (799-800).
5. **The explicit mask is built across A then B of all effects**, with shared `srcSet`/`dstSet`
   (SpellInfo.cpp:4572). An earlier DEST writer therefore suppresses a later DEST request, and
   `InitExplicitTargets` then **drops the client's ground point** (Spell.cpp:680).
6. **Relation classes of the current pairs.**

   | relation | effects |
   |---|---:|
   | a-only | 10,169 |
   | empty (effect-type fallback) | 325 |
   | b-uses-a-dest | 145 |
   | b-uses-a-src | 70 |
   | a-src-b-independent | 11 |
   | b-moves-a-dest | 10 |
   | a-recipients-b-dest | 6 |
   | independent-union | 6 |
   | a-dest-b-independent | 5 |
   | a-src-b-reads-spell-dest | 3 |
   | b-only | 2 |
   | b-overwrites-a-dest | 1 |
   | a-recipients-b-reads-spell-dest | 1 |

- Witnesses:
  - 1714:0 (53,16): the B centre is A's target dst.
  - 1160:0 (22,15): the src is the caster, not the client src.
  - 46968:0 (104,0): the cone ignores the dst.
  - 61882:2 (87,16): around the client dst. Effects 2 and 3 are *not* grouped because the
    TargetA radius differs.
- Fixtures rule out:
  - "B refines A";
  - "B's centre is A's unit";
  - "CASTER_TO_DEST cones orient to the dst" (TG-A-D5, structural);
  - "effects select independently".

## 3. Explicit-target validation

`targeting/explicit.py` (staged `validate`); corpus `explicit-validation.json` (74-row predicate × stage
matrix, `targeting.py explicit-census`). An explicitly selected unit meets six distinct stages:

| stage | consumer | rejects with |
|---|---|---|
| init | `InitExplicitTargets` Spell.cpp:621 | silent drop by mask kind, selection/victim/self fallback (650-658) |
| cast eligibility (prepare / completion) | `CheckCast(strict)` 3492 / `CheckCast(false)` 3756 → `CheckExplicitTarget` (uses **m_originalCaster**), `CheckTarget(implicit=false)`, LOS, `CheckRange` 7274 | `SpellCastResult` |
| redirect | `SelectExplicitTargets` 691, **after** all cast checks | replacement. Magic path (Object.cpp:2602): the magnet's range/LOS are never checked. Melee path (Unit.cpp:6581): LOS + CheckTarget |
| recipient selection | TargetObject 1807 → `AddUnitTarget(checkIfValid, implicit=false)` 2443 | silent (`CheckEffectTarget` per effect bit, `CheckTarget`); **no relation re-check** |
| launch | `PreprocessSpellLaunch` 8526 | IMMUNE (full mask) |
| hit | `PreprocessTarget` 2749, `PreprocessSpellHit` 3109, `DoTargetSpellHit` 2796 | IMMUNE/EVADE; silent drop on alive-state change or sanctuary. `IsValidAttackTarget` here is **a branch, not a rejection** (3141) |

Findings (trinity-consumer unless marked):
- **The relation is checked only at cast eligibility.** `TRIGGERED_IGNORE_TARGET_CHECK` turns
  only a first-failure BAD_TARGETS into OK (3495). Because CheckCast has already returned, it
  also skips LOS, range, power and script checks: a harmful triggered cast lands on a friend
  500 yd away (test).
- **Dead enemies fail as BAD_TARGETS**, via `IsValidAttackTarget` in `CheckExplicitTarget`, not as
  TARGETS_DEAD.
- **Range column.** The range column follows `!IsHostileTo` (Object.cpp:1674), so a neutral target
  uses the friendly column. Only 10 SpellRange rows differ between the columns, and none belongs
  to a current explicit-unit spell.
- **Melee range.** SPELL_RANGE_MELEE ignores RangeMin/RangeMax: the maximum is
  `max(reachA + reachB + 4/3, 5)` (7344; 88 of the 511 explicit-unit spells).
- **Selector 118 (ALLY_OR_RAID)** yields only `TARGET_FLAG_UNIT_RAID`, so an ungrouped friend
  fails BAD_TARGETS at cast eligibility. For player casts, the selector's own "not in raid → only
  the target" branch (Spell.cpp:1396) is therefore reachable only when
  m_originalCaster ≠ m_caster or with IGNORE_TARGET_CHECK.
- **Crowd control.** `ATTR6_DO_NOT_CHAIN_TO_CROWD_CONTROLLED_TARGETS` applies to implicit
  targets only.
- **Facing.** `FacingCasterFlags` INFRONT is checked only in `CheckRange`, only for player
  casters, and with a boundary-radius exemption (7300).
- **Class/spec.** There is no class/spec restriction on explicit targets.
- **Census.** 511 current explicit-unit spells (9 build-skew). Unit mask flags:
  - ENEMY 389, ALLY 84, UNIT 29, RAID 12, DEAD 1;
  - range kinds: default 423, melee 88.
- **Likely Trinity defects:**
  - TG-B-D1: the sanctuary polarity is inverted (Object.cpp:2469/2584).
  - TG-B-D2: FAIL_ON_ALL_TARGETS_IMMUNE is evaluated before launch immunity (Spell.cpp:846;
    witnesses 1022, 204018).
  - TG-B-D3: corpse aura-state exemption (latent).
  - TG-B-D4: null==null friendly reaction (latent).

## 4. Friendly / hostile / attack / assist

`targeting/relations.py`; corpus `relations.json`; probe `tools/tc_target_relation_probe`.

- **Where the predicates live.** At this pin they are `WorldObject` methods in Object.cpp:
  - `GetReactionTo` 2035, `GetFactionReactionTo` 2141;
  - `IsHostileTo` 2188 (`<= HOSTILE`), `IsFriendlyTo` 2193 (`>= FRIENDLY`);
  - `IsValidAttackTarget` 2331, `IsValidAssistTarget` 2489.

  `relations.json → fact_inventory` lists the 31 fact rows they read.
- **Differential.** The verbatim probe compared against the Python mirror over 3 seeds × 1,000
  random worlds (pets, guardians, totems, traps, charmers, forced reactions, reputation, duel,
  PvP/FFA/sanctuary, GM, flight, groups, invisibility, bypass attributes): **0 mismatches** in
  212,940 fields, with every return path reached.
- **`WorldObjectSpellTargetCheck` (Spell.cpp:9306-9396).** It has 9 check types; THREAT and TAP
  do not exist at this pin.
  - `_caster` is always `m_caster`, with no owner substitution. Owner resolution happens inside
    the predicates.
  - Membership uses the **referer**.
  - Corpses resolve to their owner player and skip the attack/assist test (the TODO at 9331).
  - Totems are excluded for ENEMY/ALLY/PARTY/RAID.
- **Minimum subset for combat simulation.**
  - Needed:
    - DB2 faction templates of the player race and the target;
    - one-level owner/charmer resolution;
    - PLAYER_CONTROLLED and the creature unit flags;
    - alive state + `IsAllowingDeadTarget`;
    - group membership (§6);
    - TREAT_AS_RAID_UNIT / CAN_ASSIST type flags;
    - reputation, only for rep-capable factions;
    - a stated or ported positivity (§16).
  - Declared absent by the fixture profile `combat-sim`, each with a reopen condition: duel,
    FFA/PvP/sanctuary, contested guards, forced reactions, GM, taxi, mounted pets, vehicles,
    stealth. The creature type flags TREAT_AS_RAID_UNIT and CAN_ASSIST are world-DB data, so the
    profile does **not** default them: a fixture must state them or read them from the TDB
    (hostile review R1-03).
- **Real data: 326 training-dummy templates**, against all 15 player-race faction templates.
  - Selection: ScriptName `npc_training_dummy`, or a name/subname matching
    `(training|target|combat|sparring|practice|healing|tank|tanking|damage|cleave) dummy`, minus
    test/bunny/kill-credit names. The rule is recorded in `relations.json → dummy_selector`. The
    first draft used ScriptName only, which selects only legacy dummies (R1-02).
  - **Load rewrite.** Trinity turns a faction with no FactionTemplate row (e.g. 0) into 35 at load
    (ObjectMgr.cpp:1022-1027). CAN_ASSIST (TypeFlags 0x1000) comes from the difficulty-0 row
    (Creature.cpp:263-277).
  - Verdicts (identical for every player race; `per_dummy_verdicts`):

    | verdict | dummies |
    |---|---:|
    | attackable, not assistable, friendly range column | 146 |
    | not attackable, not assistable | 141 |
    | assistable, not attackable | 32 |
    | attackable, hostile range column | 6 |
    | fails closed (needs a player reputation fact) | 1 (5723) |

  - **Most current 12.x damage dummies are not attackable by players at this pin**: 194643/4/8,
    197833, 242190, 242758, 243166/7/8, 243207. Their faction 0 becomes 35, which is friendly to
    players. This is a Trinity data artefact, not Retail behaviour (witness `witness-B-133-0-3`:
    Fireball on 194648 fails BAD_TARGETS).
  - Current healing dummies (194645/6, 197834, 219251, 225979, 225980, 242760/1) are assistable
    only. The template-7 dummies (219250, 225976/7/8, 225982/3/4, 242759) are attackable.
  - Legacy: 27 template-7 dummies are neutral and attackable; 30527 / 31143 (template 35) are
    friendly; 17578 has IMMUNE_TO_PC.

## 5. Area targeting and geometry

`targeting/area.py`, `targeting/geometry.py`; corpora `area.json`, `geometry.json`; probe
`tools/tc_target_geom_probe` (27,000 differential cases, 0 mismatches).

### 5.1 Area (`SelectImplicitAreaTargets`, Spell.cpp:1326-1463)

- **Referer and centre.**
  - CASTER/SRC/DEST → referer `m_caster`.
  - TARGET → the explicit unit; without one, the selection returns with no targets.
  - LAST → the last unique target carrying the effect, else the caster.
  - Centre: the src/dst position or the referer.
- **Radius.** `CalcRadius(m_caster, idx) * RadiusMod` gives `{Min, Max}`:
  - `Max = min(Radius + PerLevel·level, RadiusMax)`, or `RadiusMax` with no caster;
  - `SpellModOp::Radius` applies to Max only;
  - a movement bonus of ±2 applies unless ATTR9_NO_MOVEMENT_RADIUS_BONUS.
- **Searcher mask** (2125-2160): object type, ONLY_ON_PLAYER / PlayersOnly → players + corpses,
  ONLY_ON_GHOSTS, NOT_ON_PLAYER, condition masks.
- **Enumeration.** World container, then grid container. Within each, cells go standing cell
  first, then x-then-y; inside a cell, newest first. There is **no phase filter**
  (`GetAlwaysVisiblePhaseShift`, 2217). The concrete order is map state, so the fixture's
  `visit_order` always supplies it (TG-C-U07, TG-G-04).
- **Predicate: a vertical cylinder.**
  - 2D squared binary32 distance, with the **target's** combat reach added to both bounds.
  - `distsq < (Max + reach)²` is strict; `!(distsq < (Min + reach)²)` applies when Min > 0, so
    RadiusMin is an annulus (TG-C-U01).
  - `|dz| <= Max` is inclusive and has no reach term.
  - Evidence: trinity-probe.
  - GameObjects use their geobox, which is a world fact.
- **AoE immunity** (`SpellOtherImmunity::AoETarget`) rejects unless ATTR8_CAN_HIT_AOE_UNTARGETABLE.
  Chain searches use `ChainTarget` instead.
- **The caster is an ordinary candidate** (witness Divine Hymn 64844: the caster heals itself).
- **After the search:**
  1. UNIT_AND_DEST `ModDst(referer)`;
  2. `OnObjectAreaTargetSelect` hooks;
  3. FURTHEST sort (115 only);
  4. cap (§10);
  5. `AddUnitTarget(checkIfValid=false, losPosition=centre)`.
- **Census.** 287 current area effects.
  - By reference: DEST 172, SRC 70, CASTER 43, TARGET 2.
  - By check: ENEMY 173, ALLY 54, RAID 20, PARTY 15, SUMMONED 24, ENTRY 1.
  - 10 are players-only, 10 have bound area hooks, 3 are annulus (1263566:0-2), 3 are build-skew.

### 5.2 Cones, lines, trajectories, destinations

- **Angles** are binary32 radians: `DegToRad(d) = d * (2f·float(π)/360f)`.
  - `NormalizeOrientation` returns [0, 2π], and a tiny negative input yields exactly `2·float(π)`
    (differential finding).
  - `HasInArc(arc)` takes the **full** arc and accepts `[-arc/2, +arc/2]` inclusive.
  - `DegToRad(360) == 2·float(π)` normalises to 0, so a 360° cone hits only straight ahead
    (TG-C-D04; probe-verified; no current user).
- **Cone** (1273, 9459):
  - The apex and orientation are always `*m_caster`. The CASTER_TO_DEST ids 104/136 never read the
    dst (TG-A-D5 / TG-C-D05 family).
  - `ConeAngle` defaults to 90 at load for every cone spell (SpellMgr.cpp:5281-5283). This makes
    selector 54's 0→180 default unreachable (TG-A-D1 / TG-C-D01), and 6 current cone spells rely
    on the 90 default.
  - `Width != 0` → CU_CONE_LINE → a rectangle test.
  - **Units inside the caster's boundary radius bypass the arc test.**
  - The area cylinder test follows, and the caster's phase filter applies.
- **Line** (1964, 9505) skips the area check: no radius, no vertical bound, no AoE immunity
  (TG-C-D02). The cap sorts nearest-first and truncates. There are 0 current line effects.
- **Traj** clips the destination against world geometry; there are 0 current effects.
- **Destinations.** `MovePosition*` adds the anchor's orientation. The XY step is float.
  Height, MMAP/VMAP raycasts and ground are world data, so the fixture must state the final
  position (52 current moving-destination effects, all classified fixture-dependent).
  - DEST_DEST offsets anchor on the caster (TG-C-D05).
  - TargetDest offsets use `CalcRadius(nullptr)` = RadiusMax (TG-C-D08).
  - `*_RANDOM` radius = `(Max-Min)·sqrt(rand_norm)` without re-adding Min (TG-C-D06 = D-DEF-03).
- **2D/3D and boundaries** (probe-verified):

  | test | dimensions | boundary |
  |---|---|---|
  | area | 2D cylinder, vertical band inclusive | max strict (`<`), min not-`<` |
  | nearby | 3D, minus the target's reach | strict (`<`) |
  | boundary radius | 3D | — |
  | distance sorts | 3D squared | — |
  | arcs | 2D | inclusive (`<=`) |
  | lines | 2D | width strict (`<`) |

- **Census.** 66 cones: ConeDegrees 60 ×22, 120 ×16, 90 ×10, 0→90 ×7, other ×11; 6 rectangles.
  0 lines, 0 trajectories, 11 random-direction destinations.

### 5.3 Nearby

`WorldObjectSpellNearbyTargetCheck` (9402) uses `dist < range`, shrinking the range on each hit,
with a `WorldObjectLastSearcher`. The nearest candidate wins; **on equal distance the first
visited wins**; there is no phase filter. There is 1 current NEARBY effect. The chain stage can
follow (§8).

### 5.4 AreaTriggers: a second pipeline

`targeting/areatriggers.py`; corpus `areatriggers.json`; probe `tools/tc_target_at_probe` (2,400
cases, 0 mismatches).

- **Creation.** One AT is created per `SPELL_EFFECT_CREATE_AREATRIGGER` effect per cast,
  whatever that effect's unit selectors are (Spell.cpp:8499, SpellEffects.cpp:5410).
  - **Shapes, templates and actions come only from the world DB.** The client
    `AreaTriggerCreateProperties` table is not read. A missing create-properties row means no AT.
- **Update.** `Update` removes an expired AT *before* updating targets (AreaTrigger.cpp:361).
  Then `UpdateTargetList` (641) runs, then `SearchUnits` (717):
  - distance `<` radius + unit combat reach (strict);
  - **dead units are candidates**;
  - sphere is 3D; box, polygon, cylinder and disk are 2D;
  - cylinder and disk use an inclusive z band; the disk's inner radius has no reach term;
  - the box height is Z/2 (TG-I-05);
  - polygon vertices are relative to the AT and rotated by its orientation.
- **Filters and ordering.**
  - Template filters apply only when a template exists.
  - Enter hooks follow grid visit order, and all enters run before exits.
  - **Exit order is unspecified** (unordered set; TG-I-03).
- **Template actions** (`UnitFitToActionRequirement`): FRIEND/ENEMY/RAID/PARTY/CASTER map to the
  §4/§6 predicates.
  - CAST re-selects through the child spell. ADDAURA does not select.
- **Position-only ATs.** Blizzard 190357, Earthquake 77478, Consecration damage 81297, Lunar Beam,
  Firestorm, the sigils, Emerald Blossom and Tar Trap cast a *child* at the AT position. **The
  child's own DEST area selectors pick the recipients, not the AT shape** (witness Blizzard).
- **Census.** 73 effects on 61 spells (51 × effect 179, 2 × 353, 20 × aura 395); shapes: sphere 32,
  cylinder 10.

  | verdict | effects |
  |---|---:|
  | understood | 17 |
  | understood-with-defect | 1 (Tar Trap) |
  | fixture-dependent | 3 (Song of Chi-Ji path; Rain of Fire 5740:1 and Binding Shot 109248:0 cast once per unit of an unordered set, TG-I-03 — R1-06) |
  | blocked | 31 (no create-properties row; 3 of them build-skew) |
  | unresolved | 21 (13 ATs nothing reads, e.g. Anti-Magic Zone, Solar Beam, Ursol's Vortex, Ring of Peace, Rain of Fire 1214467; 8 Halo effects) |

- **Likely Trinity defects:**
  - TG-I-D02: `at->Remove()` inside the enter hook does not stop the enter loop, so two hostiles
    trigger two Tar Trap activations (code reading).
  - TG-I-D03: flat radius mods are ignored, and a percentage mod replaces the scale curve.
  - TG-I-D04: the Halo selector expects an older effect layout, so up to four ATs spawn
    (structural; see TG-H-03).
- Rows whose only unordered iteration is an exit loop that removes one aura per unit (Consecration,
  Darkness, Power Word: Barrier) stay understood, with the reason recorded
  (`unordered_iteration`).

### 5.5 Aura target maps: a third pipeline

`targeting/auratargets.py`; corpus `aura-targets.json` (`targeting.py aura-targets`); probe
`tools/tc_target_auramap_probe` (29 scenarios × 3 comparisons + 7 cadence cases + the witnesses, all
agree; `tests/test_tg_j_*.py`, 134 tests).

- **The spell pipeline picks only the aura owner.** `Spell::DoSpellEffectHit` creates the aura on
  each unit hit by any unit-owned aura effect, and the aura carries **all** of the spell's
  unit-owned aura effects (Spell.cpp:3241). The hit handler of the area-aura effect types is
  `EffectUnused`, and `AddStaticApplication` keeps only plain `APPLY_AURA` bits
  (SpellAuras.cpp:2650-2663). **Every area-aura recipient, the owner included, comes from
  `UnitAura::FillTargetMap`.** Consequence: Deathmark 360194:1, a PARTY aura, searches around the
  enemy it was cast on.
- **Cadence.** The target map is refreshed on the first owner update, then every 500 ms
  (`UPDATE_TARGET_MAP_INTERVAL`, SpellAuras.cpp:839-842). Units that left are removed.
- **`UnitAura::FillTargetMap`** (SpellAuras.cpp:2540-2648):
  - The search is centred on the **owner**.
  - The relation caster is the **aura caster**, or the owner when the caster is gone (2547, 2636).
  - The referer for PARTY/RAID is the owner.
  - The radius is `CalcRadius(ref)` of the TargetA entry. It is recomputed at every refresh, so
    the caster's movement leeway resizes auras owned by other units (TG-J-D02).
  - No LOS check. The same cylinder predicate as §5.1.
  - Per type (pinned enum): 35 / 271 PARTY, 65 RAID, 128 ALLY, 129 ENEMY (the only one that widens
    the cell search, TG-J-D01), 202 SUMMONS (owner + units summoned by the aura caster), 119 PET
    (owner + master), 143 OWNER (master only, 3D range with only the owner's reach),
    174 APPLY_AURA_ON_PET (the owner's pet by GUID, with no range/phase/alive gate).
  - **RadiusIndex 0 gives `{0,0}`**: the owner plus any unit within its own reach at exactly equal
    z (11 current effects).
- **`DynObjAura::FillTargetMap`** (2709-2737, persistent area auras):
  - The check type is TargetB's when TargetB references DEST (Rain of Fire 5740:0 → ENEMY).
  - Radius `{0, dynobj Radius}`, frozen at cast.
  - The dynobj's phase applies.
  - **No searcher mask**, so `PlayersOnly` is ignored (TG-J-D05).
- **`Aura::UpdateTargetMap` filters** (658-792): immunity, `CanBeAppliedOn`, highest-exclusive and
  stacking. The dynobj rule is that the same caster and spell do not stack. An existing
  application that becomes immune to all its effects stays applied with mask 0 (TG-J-D04).
  Application order follows an `unordered_map`.
- **Load corrections.** The area-aura target rewrite (SpellMgr.cpp:5285-5290) touches no current
  effect. The client TargetA/B of a unit area aura matters only through the owner selection and
  the TargetA radius.
- **Census.** 35 effects on 28 spells in 40 specs:
  - by type: RAID 13, PARTY 12, ON_PET 6, persistent 2, FRIEND 1, SUMMONS 1;
  - none of 119/129/143/271;
  - verdicts: 30 fixture-dependent, 4 understood, 1 understood-with-defect;
  - 1 build-skew (1291885:2).
- **Likely Trinity defects:**
  - TG-J-D01: cell coverage; 29 effects can miss units near cell edges.
  - TG-J-D02: moving-caster radius; 16 effects.
  - **TG-J-D03: Deathmark 360194:1 never applies to anyone** (structural).
  - TG-J-D04: immune application kept with mask 0.
  - TG-J-D05: persistent auras ignore PlayersOnly.
- **Witnesses:**
  - Devotion Aura 465:0 → [p1, p2, p4, pet2]: raid, not party; pets via owner membership; reach
    edge; cylinder.
  - Forestwalk 400129:0 → [p1, p2]: radius 0 is not owner-only.
  - Rain of Fire 5740:0 → [e1]: TargetB check, dynobj phase, same-caster no-stack.

## 6. Party / raid / group targeting

`targeting/groups.py`; corpus `group-policy.json`.

- **Order inside the check** (Spell.cpp:9306-9374):
  1. CheckTarget;
  2. corpse → owner;
  3. referer must be a Unit;
  4. totem excluded;
  5. **`m_caster->IsValidAssistTarget`**;
  6. **then** `referer->IsInPartyWith/IsInRaidWith`.

  Assist is judged from the caster and membership from the referer:
  - the caster for CASTER/SRC/DEST/NEARBY references;
  - the explicit target for TARGET;
  - the last target for LAST.

  RAID_CLASS compares **the referer's** class (selector 61: the explicit target's).
- **`IsInPartyWith`** means the same group *and* the same subgroup. **`IsInRaidWith`** means the
  same group only (Unit.cpp:12184-12218, Player.cpp:2070-2080). **No consumer reads whether a group
  is a raid.**
- **Owner resolution** goes one level (`GetCharmerOrOwnerOrSelf`). Pets and guardians of members
  pass; a pet's guardian does not, not even with that pet.
- **Controlled units are ordinary candidates and count toward caps.** Only PlayersOnly /
  ONLY_ON_PLAYER removes them (Rallying Cry 97462: pets are never enumerated).
- **An ungrouped player's party** is itself plus the units it owns or charms (the check then
  drops totems) plus TREAT_AS_RAID_UNIT creatures.
- **Explicit-target group selectors.** `TARGET_UNIT_TARGET_PARTY/RAID/PASSENGER` (35/57/95) apply
  no membership filter at selection. Membership is enforced only by `CheckExplicitTarget` (§3).
- **ALLY_OR_RAID (118)** does one of two things:
  - caster not in a raid with the target → exactly the target (unchecked at selection);
  - otherwise → a RAID area centred on the target, with referer = target; the target is **not**
    force-included.

  §3 explains why player casts rarely reach the first branch.
- **CASTER_AND_SUMMONS (120).** The caster is pushed first, then a SUMMONED search runs (every
  TempSummon of `m_caster`, totems included, no assist test). The cap can drop the caster.
- **Census.** 93 effects on 50 spells use group or relationship selectors:
  - TARGET_RAID 25, CASTER_AND_SUMMONS 24, DEST_AREA_PARTY 14, CASTER_AREA_RAID 12, PET 9,
    CORPSE_SRC_AREA_RAID 6, ALLY_OR_RAID 2, SRC_AREA_PARTY 1.
  - No MASTER, SUMMONER, NEARBY_PARTY/RAID or RAID_CLASS selector is in reach.

## 7. Smart / injured selection

`targeting/smart.py`; corpus `smart-selection.json`; probe `tools/tc_target_chain_probe`.

- **There is no generic smart-heal attribute in the pinned consumer.** Health-, group- or
  state-dependent recipient choice comes only from:
  - (a) the chain-heal deficit rule (§8);
  - (b) two helpers called from script area hooks;
  - (c) script-local code;
  - (d) `Unit::SelectNearbyTarget`.
- **`Trinity::SelectRandomInjuredTargets`** (Spell.cpp:9513):
  - If the list fits (size ≤ max), it is unchanged and nothing is drawn.
  - Priority bits: **NOT_INJURED (4) > NOT_PLAYER (2) > NOT_GROUPED (1)**. Injured means
    `!IsFullHealth`; this is **binary**, with **no deficit or health-% ranking**, and an injured
    stranger outranks a full-health group member.
  - `std::ranges::sort` is unstable; it is exact for ≤ 16 elements under libstdc++ 13
    (insertion sort), and the oracle fails closed above that with ties (TG-D-12).
  - The first priority class that reaches the cap is `RandomShuffle`d, **even when it fills the
    cap exactly** (D-DEF-10). Then the list is truncated.
  - Evidence: trinity-probe + differential. Current user: Wild Growth 48438.
- **`SortTargetsWithPriorityRules`** (9575): rule *i* contributes bit N-1-i, and candidates are
  sorted descending. When the cut splits a tie, the **whole** equal-score range is shuffled.
  - Power Word: Radiance 194509 has five rules: explicit, no own Atonement, injured,
    player/raid-unit, in raid with caster.
  - The explicit target wins only if the area search returned it.
- **Random helpers.**
  - `RandomShuffle` = libstdc++ 13 forward Fisher-Yates, `swap(a[i], a[j_i])` with j_i ∈ [0, i]
    (swap log from the probe). libstdc++ draws the indices **in pairs**: one
    `uniform_int_distribution` call per pair of positions, plus one first when n is even, so
    floor(n/2) calls (R2-03). The fixture states the j_i, which are swap indices, not engine calls.
    Engine-word consumption (TG-D-20) and the index model itself (TG-D-24; libc++ and MSVC differ)
    are standard-library specific.
  - `Unit::SelectNearbyTarget` (Blade Flurry; see the draw-only note below) draws uniformly among alive, non-friendly units
    within 5 yd + reaches in LOS. It has no IsValidAttackTarget check, and its one draw happens in
    `DoCheckProc` before the proc roll.
- **Current-player families** (each row in `smart-selection.json` has construction, metric,
  tie-break, determinism, cap, explicit guarantee, full-health and pet eligibility, and per-effect
  independence):
  - chain-heal deficit (1064)
  - select-random-injured (48438)
  - priority rules (194509)
  - random-resize hook (Starfall 50286; Seed of Corruption 27243 — effects 1/2 are *one* group on
    12.1 rows, so its hook runs once)
  - proc-random-adjacent (13877) — **draw-only** on 12.1 rows: the draw and the proc gate happen,
    but the hook that would cast 22482 on the chosen unit has mask 0 (hostile review R3-01)
  - Shooting Stars (202342)
  - Killing Spree (51690) — **dead** on 12.1 rows: the hooks that fill its GUID list bind to an
    aura effect (mask 0), so it never draws or picks (R3-06)
  - Molten Assault (60103)
  - Divine Procession (472361)
- **Scope gap.** Most Trinity smart-heal hooks bind to spells that only scripts or AreaTriggers
  reach: Circle of Healing 204883, Healing Rain 73921, Prayer of Mending jump 155793,
  Efflorescence 81269, Healing Stream Totem 52042, and others. They are counted in the extended
  layer (§17.3; TG-H-01).
- **Retail.** Tooltips describe these spells as smart heals, but no client data encodes the
  ranking, so the Retail metric is an unknown (TG-D-11), not a finding.

## 8. Chain targeting

`targeting/chain.py`; corpus `chains.json`; differential 800 cases, 0 mismatches.

- **Callers.** Only **Nearby** (1270) and **TargetObject** (1825) call the chain stage.
  TargetObject chains even if `AddUnitTarget` rejected the explicit target.
- **Jump count.** `maxTargets = ChainTargets` + `SpellModOp::ChainTargets`; the stage runs only if
  the result is > 1. An authored 0/1 plus a flat mod creates a chain.
  - **Grouping ignores ChainTargets**, so grouped effects run with the lead's value: Avenger's
    Shield 31935:1-2 are authored −10 but run with 3 (TG-F-D1). 9 current effects inherit another
    effect's chain count.
- **Jump radius** (2224-2247):
  - by DmgClass: RANGED 7.5, MELEE 5, NONE/MAGIC 10;
  - 12.5 for selector 45;
  - then `ChainJumpDistance` mods.
- **Pre-filter population.** An area search (reason Chain) of radius:
  - `jump × jumps` by default;
  - `jump` with ChainFromInitialTarget;
  - `GetMinMaxRange(false).Max` with CHAIN_FROM_CASTER, a runtime value (TG-D-02).

  The search is centred on the chain source and uses the same relation and ChainTarget-immunity
  checks. **Only the initial target is removed, so the caster can be jumped to.**
  MELEE_CHAIN_TARGETING keeps only the caster's front half.
- **Chain heal** (2283-2298) picks the largest `uint32` deficit within `jump + both reaches`
  (3D, strict) and in LOS of the source. Ties keep the earlier candidate. The first candidate also
  needs range and LOS. A full-health unit is chosen only when nothing injured qualifies.
  EnforceLineOfSightToChainTargets is ignored on this path.
- **Other chains** pick the candidate nearest to the **current source** (`GetDistanceOrder`:
  centre distance², 3D, binary32, strict, ties → earlier).
  - **D-DEF-01:** only the first accepted candidate is tested against the jump radius. A later,
    closer replacement is not re-tested; since `IsWithinDist` adds reaches and
    `GetDistanceOrder` does not, the chosen unit can lie outside the jump radius. The
    differential hit this twice.
- **Source movement.** The source moves to each pick, except with CHAIN_FROM_CASTER (source is the
  caster) or ChainFromInitialTarget (source stays the initial target). Picked units leave the
  pool. The chain stops at the first empty jump, so fewer targets than the cap is normal.
- **Script hook** (`OnObjectAreaTargetSelect`) runs **after all jumps** (1850). Then
  `AddUnitTarget(checkIfValid=false, losPosition = previous list element)` runs.
- **Damage multipliers** (`ChainAmplitude`) are applied in unique-list order at launch
  (8505-8519). They are separate from selection, coupled only through that order.
- **Census.** 43 current effects can chain, counted only when ChainTargets after every positive
  modifier is > 1 (Spell.cpp:1838; hostile review R2-04):
  - 10 authored > 1, 31 only through modifiers (e.g. Sweeping Strikes 260708), 2 only through
    grouping (31935:1-2);
  - 3 effects with a matching modifier stay inert: 204157:0, and 44425:0-1 (Arcane Charge base 0);
  - jump radius base: 5 yd ×28, 10 yd ×14, 12.5 yd ×1;
  - verdicts: 1 understood (chain heal), 42 understood-with-defect (D-DEF-01);
  - none has a target script hook.
- **Witnesses.**
  - Chain Heal 1064:0 [p, a, b, c]: deficit, not health %.
  - Chain Lightning 188443:0 [P, X, Y]: nearest to the previous target, not to the primary or the
    caster.
  - `grouped-effects-inherit-lead-chain`.

## 9. Geometry summary

See §5.2 and `geometry.json`. Everything that depends on world collision, height, pathing or
vehicle seats is classified `fixture-dependent` with the tag `world-geometry` (446 current effects
carry that tag). None of it is reproduced. The oracle takes the resulting positions and the LOS
table as fixture facts.

## 10. Target caps

`targeting/caps.py`; corpus `caps.json`.

- **Value per cast.**
  1. `SpellInfo::MaxAffectedTargets`, which is `SpellTargetRestrictions.MaxTargets` (LIMIT_N turns
     0 into 1; corrections may overwrite).
  2. `SpellModOp::MaxTargets`, applied once in the Spell constructor as
     `uint32((double(base) + flat) * pct)`. **A flat mod on an uncapped spell creates a cap.**
  3. `SPELLVALUE_MAX_TARGETS` **replaces** the result (Spell.cpp:8738).

  0 means uncapped.
- **Not recipient caps:**
  - `LoadSpellInfoTargetCaps` (SpellMgr.cpp:5407) is sqrt damage/heal diminishing, a payload
    edge;
  - Unit.cpp:3477 is a single-target aura limit.
- **Order per category:**

  | category | order | truncation |
  |---|---|---|
  | AREA | candidates → [ModDst] → **script hook** → [FURTHEST sort] → cap → AddUnitTarget | RandomResize; FURTHEST truncates |
  | CONE | candidates → hook → cap → add | RandomResize |
  | LINE | candidates → hook → cap → add | stable nearest-first sort, then truncate |
  | NEARBY | single nearest | — |
  | CHAIN | ChainTargets (§8) | MaxAffectedTargets unused |

- **Scope of a cap.** One value per cast, applied independently at *every* AREA/CONE/LINE
  selector call, so TargetA and TargetB each get the full cap. **Grouped effects share one
  selection and one draw set** (Howl of Terror 5484: 7 draws, not 14).
- **FURTHEST ties.** The FURTHEST comparator `!(d1 < d2)` is non-strict, so libstdc++
  `list::sort` reverses ties (TG-C-D03; probe-verified; no current user).
- **Census.**
  - DB2 MaxTargets on 47 reach spells (23 of them on searched selectors).
  - 2 `SpellModOp::MaxTargets` sources: 1270255:3 (+5 on Howl of Terror) and 1256938:1.
  - 58 structural script cap sites (classified in §14).
  - **Legacy:** `SpellMgr.cpp:3649` caps current Divine Storm 53385 at 4 although the 12.1 row has
    no cap (TG-C-D09, `legacy-only`).
- **Shared draw sets.** 7 current spells have grouped effects that share one RandomResize draw set:
  5484, 64844, 374227, 385060, 395152, 413984, 1263566.

## 11. Target-selection RNG and ordering

`targeting/rng.py`; corpus `rng.json`.

- **One generator.** Trinity has one thread-local SFMT behind `urand`, `irand`, `frand`,
  `rand_norm`, `roll_chance` and `RandomEngine` (Random.cpp). **There is no separate targeting
  stream.** The oracle consumes explicit `world.draw` values in consumer order.
- **Container helpers** (all probe-verified):
  - `RandomResize(c, n)`: **0 draws** if size ≤ n. Otherwise **exactly size** draws of
    `urand(1, remaining)`, which continue after the quota is full. It is selection sampling, so
    the kept order is preserved.
  - `SelectRandomContainerElement`: one `urand(0, size-1)` draw, **even for size 1**.
  - `RandomShuffle`: see §7.
- **Order within one cast:**
  1. group-1 selection draws (random dest, RandomResize, hook draws — a fixture-stated script
     result must state its draws, R2-05);
  2. **one hit-result evaluation per new unique target of group 1, during selection**
     (`AddUnitTarget` → `SpellHitResult`, 2485). Duplicates only OR their mask;
  3. group-2 selection draws, and so on;
  4. `CallScriptOnCastHandlers` (3839), then the launch-mode effect handlers (8494-8500: e.g. summon
     random points, the extra `CalcRadius` draw in the summon default branch at
     SpellEffects.cpp:1999, fishing `rand_norm`). `rng.json → launch_draw_census` is empty for
     current players (R2-02);
  5. `HandleLaunchPhase`: one crit `roll_chance` per MISS_NONE target in unique-list order
     (8563), deferred for LaunchDelay > 0;
  6. payload and proc draws.

  `rng.json → worked_example` shows an area cap of 2 over 5 candidates: 5 `urand` draws, then the
  hit rolls of the 2 kept targets, then 2 crit rolls.
- **`SpellHitResult` draws** (Object.cpp:1853-1996, Unit.cpp:2621-2720):
  - no draw: damage-only immunity, a positive spell on a non-hostile target, self, evade,
    DmgClass NONE;
  - reflect `roll_chance` only if reflect is possible and > 0;
  - MELEE/RANGED: one `urand(0,9999)`;
  - MAGIC: one `irand(0,9999)` (none for a dead non-player target).

  Combined whole-cast draw accounting with the proc and attack-table passes remains TG-H-02 /
  TG-G-03.
- **Random destinations.** `CalcDirectionAngle` draws for DIR_RANDOM selectors. `CalcRadius`
  draws for 72/74/86 **only when a radius entry exists**; with RadiusIndex 0 it returns before the
  random branch (SpellInfo.cpp:800-801), and a TargetB without an entry tests TargetA's selector.
  - CasterDest draws **radius then angle**; TargetDest and DestDest draw **angle then radius**.
  - 11 current random-dest slots: 6 draw radius + angle, 5 draw the angle only (e.g. 1263077:0,
    R2-01). The final point is world geometry.
- **Grouping itself can draw.** The radius comparison calls `CalcRadius` for `*_RANDOM` selectors
  (TG-F-D2 / D-DEF-20 / TG-G-D01). No current player spell reaches this. G's pipeline computes
  groups lazily for exactly this reason.
- **Script RNG.** 189 script RNG sites, 59 bound to reach spells; 9 of those choose targets.

## 12. Effect-local recipients and duplication

`targeting/recipients.py`, `targeting/pipeline.py`; corpus `effect-recipients.json`.

- **Grouping keys** (Spell.cpp:741-785):
  - equal TargetA/TargetB ids;
  - the same `ImplicitTargetConditions` **pointer**;
  - the same PlayersOnly bit;
  - `CheckScriptEffectImplicitTargets`: object and area-object hooks compared by function
    identity, both ways, destination hooks not compared; this can be non-transitive. Identity is
    a compare of the stored handler bytes (SpellScript.h:522-549, 584-587). A *static* handler writes only
    8 of the 16 bytes, so grouping two registrations of the same static function is unspecified.
    The oracle fails closed there (TD-E-24, TG-E-04: Thunder Blast 435607 {1,2}, Frenzied Enrage
    184362 {0,1}; recipients are the same either way, R3-05);
  - equal radii, only if the lead selector is NEARBY/CONE/AREA/LINE.
  - **Not** keys: ChainTargets, PositionFacing, effect type.
  - The mask is ANDed with `~processed`. Selection uses the lead's `SpellEffectInfo`.
- **Recipient list** (`AddUnitTarget` 2443):
  - `CheckEffectTarget` clears bits per effect, for all effects;
  - `CheckTarget` runs if `checkIfValid`; **a re-add is re-validated**;
  - immunity clears bits *after* the empty-mask test, so an entry with mask 0 is possible
    (TG-F-D3 / TG-G G5);
  - a duplicate ORs its mask and keeps its first hit result, delay and LOS position;
  - **the list is never re-sorted.**

  An effect's recipient order is therefore list order (fixture `merge-duplicate-recipient`), and
  hit order is effect index, then list order.
- **A failed selector does not stop selection.** `Spell::finish()` only records the result and
  state (Spell.cpp:4356). The `return` after it leaves only the current `SelectImplicit*`
  function, so TargetB, the effect-type fallback, later effects, their hooks and their RNG draws
  still run. Only the checks at 817, 827-832, 856 and 870 return from `SelectSpellTargets`
  itself (R1-04; fixture `nearby-finish-continues-selection`: 3 draws, effect 1 → [t2, t3], cast
  result BAD_IMPLICIT_TARGETS).
- **Destinations are per-effect snapshots** of the spell-wide dst after each effect's turn.
  `SelectEffectTypeImplicitTargets` runs for every effect. REQUIRE_ALL_TARGETS is judged per turn
  against that turn's mask. The channel mask is computed per turn.
- **Census** (4,836 spells, 2,475 multi-effect):
  - selection turns per multi-effect spell: 1 → 1,994; 2 → 428; 3 → 47; 4 → 4; 5 → 2;
  - 216 spells have ≥ 2 unit-selecting groups (merged-mask candidates);
  - 22 spells are split despite equal selectors (script hooks 11, radius 7, PlayersOnly 4);
  - **only loaded scripts split groups**: hooks of a script whose `Validate()` fails on 12.1 rows
    are not registered (R1-07), so Tranquility 740 forms one group {0,2,3,4,5,6};
  - 7 spells have shared cap draw sets;
  - 15 spells read a dst after an earlier write;
  - 0 current spells have implicit-target conditions (so every condition identity is nullptr).
- **Verdicts.** understood 10,741; understood-with-defect 2 (31935); blocked 11 (three spells whose
  targeting members `LoadSpellInfoCorrections` rewrites: 53385, 197214, 373427; TG-F-U4).
- **Retail.** Trinity's own comment at 747-748 ("some spells appear to need this, however this
  requires more research") marks the whole grouping mechanism as unverified against Retail
  (TG-F-U5).

## 13. Caster / holder / owner relationships

`groups.py` / `relations.py`; `group-policy.json`. This section joins with
[`controlled-unit-archaeology.md`](controlled-unit-archaeology.md).

- **`m_caster`** is the object `CastSpell` was called on, except with ATTR6_ORIGINATE_FROM_CONTROLLER
  (no current player spell carries it). Spell mods come from the *original* object's
  `GetSpellModOwner` (Spell.cpp:504). Guardians that are not pets have no mod owner.
- **`m_originalCaster`** is the passed GUID, else `m_caster`. It is used only by channel
  selectors, `SpellHitResult`, GameObject LOS and `CheckExplicitTarget`. All other selection and
  filtering uses `m_caster`.
- **Relationship selectors** (Spell.cpp:1742-1806):
  - MASTER = charmer, else owner (one level);
  - PET = **PetGUID only** (a Pet or a Control=PET guardian), not "any owned unit";
  - SUMMONER only for a TempSummon caster;
  - OWN_CRITTER = CritterGUID;
  - CASTER is added without CheckTarget; every other relationship target is added with it.
- **Periodic triggers** (SpellAuraEffects.cpp:5583-5605). The trigger is cast by the aura caster
  only when `NeedsToBeTriggeredByCaster`; otherwise the **holder** casts it. The explicit target
  is always the holder, and `OriginalCaster` is not set. A holder-cast trigger's party/raid
  therefore resolves from the holder.
- **Pet casters** use the pet as referer, so membership resolves through the owner. Assist is
  judged from the pet. SUMMONED matches units the *pet* summoned.
- **Controlled-unit abilities.** Of 188 spells (181 with effects), only three are
  relationship-sensitive:
  - 264667 Primal Rage (pet-cast CASTER_AREA_RAID);
  - 160007 (MASTER);
  - 264735 (PET).

## 14. Script target adapters

`targeting/adapters.py` (`run_target_hook`, `HOOK_ADAPTERS`); corpus `script-adapters.json`. Every
executing body was hand-read.

- **`TargetHook::CheckEffect` is now ported exactly** (SpellScript.cpp:203-254) in
  `dummy_semantics/bindings.py`, which closes the Dummy pass's deferred limitation.
  - **Dispatch rule** (Spell.cpp:8991/9004/9017): a hook runs iff it affects the **first effect of
    the effect-mask group** and its target equals the selector being processed (A *or* B). A hook
    registered for a later effect of the group does not run.
  - **Dead calls:** the calls at Spell.cpp:2035 and 2112 pass target 0 and can never match a hook.
- **Scope tiers:**
  - reach: 4,836 spells;
  - controlled-unit: 179 spells;
  - script-reach: 307 spells cast by *executing* hooks of scripts that load (`Validate()`
    evaluated on 12.1 rows; R3-08).

  449 target-hook registrations on other bound spells lie outside every tier (TG-E-01).
- **Inventory:** 69 in-scope registrations, of which 67 are attached (registration state is
  per row; R3-04).

  | status | registrations |
  |---|---:|
  | execute | 53 (reach 27) |
  | mask 0 on 12.1 rows | 9 |
  | never registered (Register()-time condition: Holy Prism 114852 target 16, 114871 target 31) | 2 |
  | never attach, because `Validate()` fails on 12.1 (740 Inner Peace) or already on 12.0.7 (184362, 357209) | 5 |

  4 registrations depend on caster state at Register() time (Mass Entanglement's area hook only
  for 102359; Halo, mask 0 either way).

  Also: 0 `DoCheckAreaTarget` in scope; 20 reach spells carry an executing adapter; 0 mask drift
  between 12.0.7 and 12.1.
- **Semantic families.** 45 distinct functions reduce to **30 semantic variants in 16 families**
  (registrations / functions / variants):

  | family | counts | notes |
  |---|---|---|
  | object-suppression (target = nullptr) | 22 / 17 / 6 | the six variants are six different gates: unconditional, Load-gated, caster aura present/absent, spell-mod consumed, caster spec |
  | aura-presence filter | 6 / 5 / 5 | **no two equivalent** |
  | smart-injured | 6 / 6 / 5 | |
  | smart-sorted-cap | 3 / 2 / 2 | |
  | area-clear | 3 / 3 / 1 | |
  | explicit-removal | 2 / 2 / 1 | |
  | explicit-preserving-random (Seed) | 2 / 1 / 1 | |

  Nine further families have one function each: explicit-only (Storm Bolt), explicit-removal +
  random, random-cap, priority-rules, cross-effect-share, guid-exclusion, state-capture,
  destination-offset, variant-redirect.

  **The "small family" hypothesis was falsified.** Structural family ≠ production primitive.
- **Engine context that changes an adapter's meaning** (trinity-consumer):
  - Area, cone and line hooks run **before** the engine `MaxAffectedTargets` RandomResize, so a
    smart script cut can be re-randomised by a smaller engine cap.
  - **Units a script inserts bypass** the searcher and `CheckTarget` (`AddUnitTarget(…,
    checkIfValid=false)`, 1455).
  - A **nearby** hook returning nullptr fails the cast with BAD_IMPLICIT_TARGETS (1206-1213);
    selection of the remaining selectors still runs (§12).
  - Load() gates mostly mean "loaded only when the caster lacks the talent aura" (Storm Bolt).
- **Helper sites.** 128 target-relevant calls sit in non-target hooks of loaded scripts. The scan
  is pattern-based, so this is a **lower bound**; R3-03 added AreaTrigger / inside-unit /
  single-cast-aura / stored-GUID / `GetScript<>` lookups (109 → 128).
  - 18 choose the recipients of *other* casts, e.g.:
    - Fire Nova / Primordial Wave: every unit with the caster's Flame Shock in range, with **no
      hostility or LOS check**;
    - Divine Image: copies the proc spell's targets;
    - Rain of Fire 5740:2: every non-friendly unit inside the caster's ATs;
    - Consecration: the dst is the caster's first Consecration AT;
    - Light's Beacon 53651: the Beacon of Light target;
    - Trail of Light 200128: the target from two heals back;
    - Earthen Rage 170377: the stored proc target;
    - Atonement 81749.
  - 21 read the recipient count, and 20 read recipient order through `GetUnitTargetIndexForEffect`.
  - **Script-layout drift kills some hooks on 12.1 rows** (recorded as defects TD-E-20..23):
    - Blade Flurry 13877 keeps only its proc gate + one `SelectNearbyTarget` draw, and never casts
      22482;
    - Killing Spree 51690's list writer has mask 0, so nothing happens;
    - Intimidating Shout 5246's primary target stays in the effect 4-6 area list;
    - Sundering 197214's hook names a non-existent EFFECT_3.
  - Primordial Wave's `Register()` is spec-conditional: Lava Surge is prevented only for
    non-Elemental casters (R3-02).
- **Validate() audit** (R3-08). `Validate()` is evaluated mechanically from the pinned source.
  - 18 hooks that the Dummy bindings count as executing never attach (15 spell/script pairs;
    `dummy_validate_erratum`).
  - 53 constructs cannot be evaluated mechanically and are assumed to pass (TG-E-06).
  - `targeting.adapters.script_loads(spell, script)` is the shared predicate (strict mode fails
    closed).
- **Register()-time conditional registration** (TG-H-03). 17 script classes register hooks inside
  `if/switch/for` in `Register()`:
  - Holy Prism selector, both Halo selectors, Blade Flurry, Primordial Wave, Entropic Rift,
    Entangling Roots, among others.
  - The structural index records these hooks as unconditional, so masks and counts for these
    classes are upper bounds.
  - The file `spell_priest.cpp` is fully indexed (140/140 registrations, 197/197 hooks); its
    recorded parse errors are local.

## 15. World-database target policy

`targeting/world.py`; corpus `world-policy.json`.

- **Implicit-target conditions (source 13):** **zero rows** for any of the 5,324 scoped spells.
  All 3,282 source-13 rows are encounter or world content (world-db-fact). The loader
  (`ConditionMgr.cpp:1575-1682, 1851-1924`) is still mirrored with synthetic tests. The per-candidate
  condition targets are `[candidate, caster]`.
- **Cast conditions (source 17).** Two spells have them, Shadowstep 36554 and Ghoul Leap 47482:
  negated `UNIT_STATE_ROOT`, a character-level cast gate.
  - **Likely world-DB defect (TD-E-12):** their ErrorType 103 was ROOTED in the 3.3.5 enum but is
    `SPELL_FAILED_NO_ENDURANCE` in the pinned enum.
- **Evaluator** (`targeting/world.py`) covers exactly {NONE, UNIT_STATE} and fails closed
  otherwise. A missing object returns false **before** NegativeCondition (ConditionMgr.cpp:284-288).
- **`spell_linked_spell`:** 13 rows touch scope, each with its recipient rule:
  - CAST: caster → unit target, else self;
  - HIT: the hit unit casts on itself;
  - AURA: `AddAura(linked, target)`;
  - REMOVE.
- **TARGET_CHECK_ENTRY selectors without conditions** (5 in scope). Trinity takes its "emergency
  case" and applies no entry filter at all (understood-with-defect, TD-E-10 / TG-E-11).
- **TARGET_DEST_DB** 451485:1 has no `spell_target_position` row, so the dst becomes the explicit
  object position, else the caster.
- **Not used in scope:** no `spell_area` rows, no effective `serverside_spell` rows, and no
  creature/faction template data read by any in-scope selector. The training-dummy relation data
  is §4.
- **`disables`:** 54 spell rows, none in current scope. `SpellView.los_disabled` is read from the
  extract (DisableMgr.cpp:360: DEPRECATED rows also disable LOS; TG-G-D02).
- **World vs character targeting.** Encounter and world targeting (conditions, spell_area, target
  positions, entry filters) are separated from character and spell targeting throughout. A generic
  ConditionMgr was deliberately not built.

## 16. Attributes and restrictions

`targeting/attributes.py`; corpus `attributes.json`. A consumer scan of the targeting functions in
Spell.cpp / SpellInfo.cpp / SpellMgr.cpp / Unit.cpp / Object.cpp found 199 token reads, each tagged
by stage.

- **Candidate search:** ONLY_ON_PLAYER (10 spells), NOT_ON_PLAYER (1), PlayersOnly effect
  attribute (4 effects).
- **Per candidate:**
  - EXCLUDE_CASTER (15), DO_NOT_CHAIN_TO_CROWD_CONTROLLED_TARGETS (26), CANNOT_CAST_ON_TAPPED (7),
    NOT_ON_PLAYER_CONTROLLED_NPC (5), IGNORE_PHASE_SHIFT (4), CAN_HIT_AOE_UNTARGETABLE (0).
- **Chain:** CHAIN_FROM_CASTER (40 spells / 17 chaining effects), MELEE_CHAIN_TARGETING,
  ChainFromInitialTarget, EnforceLineOfSightToChainTargets.
- **Per-effect LOS:** IGNORE_LINE_OF_SIGHT (90), ALWAYS_AOE_LINE_OF_SIGHT (27),
  ALWAYS_LINE_OF_SIGHT (20), AlwaysAoeLineOfSight effect attribute (11).
- **Post-selection:** REQUIRE_ALL_TARGETS (27), FAIL_ON_ALL_TARGETS_IMMUNE (7).
- **Cap:** LIMIT_N (31).
- **Explicit mask:** DO_NOT_FAIL_IF_NO_TARGET, DontFailSpellOnTargetingFailure.
- **Custom attributes:** CONE_LINE (8, from Width); CONE_BACK / REQ_* / ALLOW_INFLIGHT on 0 player
  spells.
- **Restrictions:**
  - ConeDegrees: 38 spells (6 rely on the 0→90 default).
  - MaxTargets: 47.
  - Targets: 81.
  - TargetCreatureType: 17.
  - TargetAuraState: 10 (skipped for vehicle casters and the caster's owner).
  - ExcludeTargetAuraSpell: 16.
  - **MaxTargetLevel is loaded but never read** (TG-A-06).
  - **FacingCasterFlags** (192 spells) is read only in `CheckRange`, only for player casters and
    only for the explicit unit. Value 6 is unknown to Trinity (TG-A-07).
  - Shapeshift is checked against the caster only.
  - 35 spells allow dead targets.
- **Effect attributes that look targeting-related but have no Trinity reader** (TG-A-04):
  AddTargetCombatReachToAOE (35 effects), AreaEffectsUseTargetRadius (36),
  PositionIsFacingRelative (4), plus unknown bits 0x400000 / 0x10000000 (TG-A-05).
  **No consumer in Trinity ≠ no Retail behaviour.**
- **Load order** (World.cpp:1375-1390):
  1. `LoadSpellInfoCorrections`;
  2. custom attributes (the explicit mask is built here);
  3. `LoadSpellInfoTargetCaps`.
- **Positivity** (`SpellInfo::IsPositive`) feeds relation checks, redirect and hit results. It is
  now ported (`targeting/positivity.py`, SpellInfo.cpp:4609-5099, probe `tc_target_positivity_probe`,
  12,369 cases, 0 mismatches) and decided for 4,831 / 4,836 current spells.
  - **Load-order dependent** (TG-G-01): 44614 Flurry, 188499 Blade Dance, 190411 Whirlwind,
    385059 Odyn's Fury. Trinity's own result changes with hash-container iteration order; the
    compiled code yields 2-4 distinct masks.
  - **Blocked:** 111400 Burning Rush (an unapplied correction).
- **Core cross-reference.** Core's five catalogs and its admitted selector pairs are read-only
  vocabulary here (§22). The attribute-by-stage table in `attributes.json` is the join point.

## 17. Current-player census

`targeting/census.py`; corpus `census.json` (`targeting.py census`). Each track publishes a verdict
for the effects its question applies to. An effect's closure is the **worst** track verdict:
understood < understood-with-defect < fixture-dependent < blocked < unresolved.

### 17.1 Global (10,754 effects / 4,836 spells)

| measure | effects |
|---|---:|
| understood (of which simple) | 10,179 (10,029) |
| understood-with-defect | 47 |
| fixture-dependent | 464 |
| blocked | 42 |
| unresolved | 22 |
| build-skew (spell newer than Trinity's last supported build; every row cites TG-H-04) | 188 |
| rows citing at least one unknown id | 407 |
| family: area | 382 |
| family: cone/line/traj | 66 |
| family: destination | 404 |
| family: world-geometry | 445 |
| family: world/condition | 101 |
| family: group | 109 |
| family: script adapter (target hooks + recipient-choosing helper sites) | 93 |
| family: areatrigger | 73 |
| family: random | 57 |
| family: chain | 43 |
| family: aura target map | 35 |
| family: controlled-unit | 15 |
| family: smart | 12 (reach only; most smart-heal payload spells are in the extended layer) |

Effects by selector (A, B and pair) are in `census.json → global.by_target_a/by_target_b/by_pair`.

**Targeting closure × payload proxy.** This separates "payload understood, targeting blocked" from
"targeting understood, payload blocked". The payload axis is a *proxy*, not payload research: it
records generic handler presence, and for Dummy/ScriptEffect/Dummy-aura effects the Dummy-pass
owner bucket.

| payload proxy | understood | defect | fixture-dependent | blocked | unresolved |
|---|---:|---:|---:|---:|---:|
| effect handler | 590 | 25 | 197 | 22 | 16 |
| aura handler | 5,520 | 10 | 148 | 15 | 6 |
| null effect handler | 32 | 1 | 19 | – | – |
| null aura handler | 482 | – | 4 | – | – |
| Dummy with a server consumer (all buckets) | 549 | 9 | 43 | 5 | – |
| **Dummy with no Trinity consumer** | 3,006 | 2 | 53 | – | – |

3,006 effects therefore have fully understood targeting and **no payload consumer** in pinned
Trinity. Conversely, 59 handler-backed effects (38 effect handler, 21 aura handler) are blocked or
unresolved on targeting alone.

### 17.2 Per spec

Columns: spells, effects, simple-understood, understood-with-defect, fixture-dependent, blocked,
unresolved, and the family counts area / group / chain / smart / script / random / areatrigger /
aura target map.

| spec | spells | effects | simple | defect | fixture | blocked | unres. | area | group | chain | smart | script | random | AT | aura map |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Death Knight Blood | 337 | 653 | 617 | 4 | 27 | 3 | 2 | 20 | 3 | 7 | 0 | 2 | 7 | 4 | 2 |
| Death Knight Frost | 348 | 667 | 633 | 4 | 24 | 5 | 1 | 20 | 2 | 4 | 0 | 1 | 4 | 6 | 2 |
| Death Knight Unholy | 338 | 683 | 645 | 7 | 19 | 3 | 1 | 15 | 4 | 7 | 0 | 2 | 5 | 4 | 2 |
| Demon Hunter Devourer | 331 | 587 | 562 | 0 | 18 | 4 | 0 | 13 | 2 | 0 | 0 | 4 | 3 | 6 | 2 |
| Demon Hunter Havoc | 364 | 652 | 622 | 0 | 24 | 4 | 0 | 20 | 2 | 0 | 0 | 4 | 3 | 6 | 2 |
| Demon Hunter Vengeance | 344 | 615 | 578 | 0 | 21 | 3 | 0 | 16 | 2 | 1 | 0 | 7 | 3 | 9 | 2 |
| Druid Balance | 358 | 683 | 631 | 0 | 37 | 3 | 5 | 32 | 9 | 0 | 2 | 5 | 8 | 8 | 7 |
| Druid Feral | 366 | 699 | 660 | 0 | 33 | 3 | 2 | 30 | 9 | 0 | 1 | 3 | 5 | 5 | 7 |
| Druid Guardian | 360 | 721 | 678 | 0 | 35 | 3 | 2 | 31 | 10 | 0 | 1 | 4 | 6 | 6 | 8 |
| Druid Restoration | 356 | 674 | 624 | 0 | 41 | 3 | 3 | 31 | 11 | 0 | 1 | 9 | 7 | 7 | 8 |
| Evoker Augmentation | 296 | 527 | 479 | 0 | 37 | 4 | 0 | 35 | 6 | 0 | 0 | 1 | 11 | 5 | 2 |
| Evoker Devastation | 322 | 540 | 500 | 0 | 32 | 3 | 0 | 30 | 4 | 0 | 0 | 1 | 6 | 4 | 2 |
| Evoker Preservation | 330 | 603 | 556 | 0 | 36 | 7 | 0 | 34 | 6 | 0 | 0 | 0 | 7 | 7 | 4 |
| Hunter Beast Mastery | 320 | 546 | 517 | 1 | 21 | 3 | 0 | 17 | 11 | 0 | 0 | 3 | 3 | 5 | 2 |
| Hunter Marksmanship | 326 | 563 | 534 | 1 | 22 | 3 | 0 | 18 | 10 | 0 | 0 | 3 | 3 | 5 | 2 |
| Hunter Survival | 321 | 550 | 521 | 1 | 19 | 5 | 0 | 15 | 10 | 0 | 0 | 3 | 3 | 7 | 2 |
| Mage Arcane | 321 | 583 | 543 | 0 | 28 | 4 | 1 | 23 | 2 | 0 | 0 | 4 | 3 | 5 | 2 |
| Mage Fire | 320 | 576 | 533 | 1 | 31 | 3 | 0 | 23 | 2 | 1 | 0 | 6 | 3 | 3 | 2 |
| Mage Frost | 324 | 589 | 544 | 6 | 30 | 4 | 1 | 22 | 2 | 6 | 0 | 6 | 3 | 7 | 2 |
| Monk Brewmaster | 411 | 767 | 721 | 1 | 26 | 8 | 3 | 22 | 6 | 1 | 0 | 2 | 4 | 12 | 2 |
| Monk Mistweaver | 411 | 797 | 748 | 0 | 31 | 8 | 3 | 24 | 14 | 0 | 0 | 2 | 4 | 12 | 2 |
| Monk Windwalker | 430 | 805 | 759 | 0 | 26 | 9 | 3 | 19 | 6 | 0 | 0 | 2 | 5 | 13 | 2 |
| Paladin Holy | 384 | 779 | 719 | 6 | 38 | 3 | 0 | 35 | 26 | 2 | 1 | 1 | 3 | 3 | 12 |
| Paladin Protection | 371 | 700 | 645 | 12 | 31 | 5 | 0 | 28 | 26 | 7 | 0 | 0 | 4 | 5 | 12 |
| Paladin Retribution | 376 | 759 | 689 | 9 | 42 | 8 | 0 | 41 | 25 | 5 | 0 | 4 | 5 | 5 | 13 |
| Priest Discipline | 333 | 603 | 565 | 0 | 26 | 7 | 0 | 19 | 5 | 0 | 3 | 8 | 4 | 4 | 2 |
| Priest Holy | 332 | 609 | 571 | 0 | 26 | 4 | 4 | 21 | 5 | 0 | 1 | 8 | 6 | 8 | 2 |
| Priest Shadow | 347 | 650 | 613 | 0 | 21 | 7 | 4 | 16 | 4 | 0 | 0 | 9 | 3 | 7 | 2 |
| Rogue Assassination | 338 | 600 | 579 | 1 | 15 | 3 | 0 | 10 | 5 | 0 | 0 | 0 | 3 | 3 | 3 |
| Rogue Outlaw | 329 | 578 | 556 | 6 | 9 | 3 | 0 | 7 | 4 | 0 | 0 | 7 | 8 | 3 | 2 |
| Rogue Subtlety | 342 | 624 | 599 | 0 | 19 | 3 | 0 | 11 | 4 | 2 | 0 | 0 | 3 | 3 | 2 |
| Shaman Elemental | 389 | 773 | 729 | 0 | 28 | 3 | 0 | 18 | 2 | 2 | 2 | 3 | 3 | 5 | 2 |
| Shaman Enhancement | 388 | 775 | 721 | 0 | 36 | 7 | 0 | 21 | 2 | 2 | 4 | 8 | 5 | 4 | 2 |
| Shaman Restoration | 374 | 740 | 688 | 0 | 32 | 4 | 0 | 18 | 3 | 2 | 1 | 5 | 4 | 4 | 2 |
| Warlock Affliction | 376 | 709 | 661 | 0 | 33 | 6 | 0 | 28 | 9 | 0 | 0 | 3 | 9 | 6 | 9 |
| Warlock Demonology | 384 | 655 | 605 | 0 | 36 | 7 | 0 | 26 | 13 | 0 | 0 | 0 | 6 | 8 | 5 |
| Warlock Destruction | 379 | 704 | 654 | 0 | 31 | 6 | 1 | 25 | 7 | 0 | 1 | 1 | 5 | 8 | 10 |
| Warrior Arms | 395 | 746 | 671 | 8 | 52 | 5 | 0 | 49 | 17 | 9 | 0 | 3 | 8 | 5 | 3 |
| Warrior Fury | 416 | 803 | 719 | 3 | 60 | 5 | 0 | 57 | 17 | 4 | 0 | 9 | 12 | 5 | 3 |
| Warrior Protection | 388 | 779 | 703 | 3 | 57 | 5 | 0 | 54 | 18 | 4 | 0 | 5 | 6 | 5 | 4 |

Build-skew effects per spec: 131-144. Most are shared current-season gear, enchant and set spells
reachable from every spec.

### 17.3 Layers outside the main population

- **Extended layer** (`census.json → extended`, TG-H-01): 770 effects on 419 spells.
  - Where they come from: children of executing script hooks (literal and variable ids),
    AreaTrigger scripts and actions, and `spell_linked_spell`, at up to 3 hops; plus
    controlled-unit and script-reach rows of the script-adapter corpus.
  - Closure: understood 679, fixture-dependent 90, understood-with-defect 1.
  - 52 of the children re-select through their own area/cone/nearby/line/chain selectors.
  - Script-hook edges are `structural-inference` unless a body was read.
  - 38 children are reachable only through hooks that never run on 12.1 rows (e.g. Healing Rain
    73921).
- **Controlled-unit abilities.** Counted separately in `selectors.json` (837 spells) and
  `group-policy.json` (188 current-player pet/guardian spells).

## 18. Witness corpus

`targeting/witnesses.py`; corpus `witnesses.json` (`targeting.py witnesses`, exit 1 on any failure).
**67 witnesses, all passing, on 40 distinct real spells** (55 real-spell witnesses, 12 synthetic
pipeline fixtures). They are evaluated by three entry points: the spell pipeline, the
AreaTrigger tick and the aura target map. **All 22 required shapes are closed.**

Each row pins:
- spell and effect;
- selectors (from the snapshot) with each selector's handler routine and consumer line;
- the fixture path (the synthetic population);
- expected and actual recipients per effect, cast result, destinations and draws consumed;
- what competing model the witness rules out.

| shape | witnesses (fixture names) |
|---|---|
| hostile single target | witness-B-133-0 (Fireball vs neutral dummy), witness-B-53-0 (Backstab melee range) |
| training-dummy relation | witness-B-133-0 (legacy template 7: attackable), witness-B-133-0-2 (template 35: BAD_TARGETS), witness-B-133-0-3 (current dummy 194648, faction 0 → 35: BAD_TARGETS) |
| friendly single target | witness-B-3411-0 (Intervene), witness-A-1022-0 (TARGET_RAID is single-target) |
| self | witness-F-871-0 (Shield Wall), witness-B-2061-0 (Flash Heal self fallback) |
| hostile AoE | witness-C-6343-0 (Thunder Clap cylinder), merge-duplicate-recipient |
| friendly AoE | witness-C-64844-0 (Divine Hymn, grouped, caster included) |
| party/raid heal | witness-F-1219209-0 (13 grouped DEST_AREA_PARTY effects), witness-C-97462-0, witness-F-97462-0 |
| smart heal | witness-D-48438-0 (Wild Growth: binary injured bit outranks grouping) |
| chain heal / chain damage | witness-D-1064-0, witness-D-188443-0, witness-F-31935-1, grouped-effects-inherit-lead-chain |
| cone | witness-C-228478-0 (full vs half angle; boundary bypass), witness-C-384391-0 (cone + cap), witness-A-46968-0 |
| explicit-target-centred / caster-centred area | witness-C-1126-0, witness-A-1714-0, targetb-area-around-targeta-dest / witness-C-6343-0, witness-A-1160-0 |
| target cap | witness-C-5484-0 (+ -2 with SpellMod), witness-C-53385-0 (legacy cap 4) |
| random target | witness-D-50286-0 (Starfall: selection sampling), witness-F-413984-0, grouped-effects-share-random-resize |
| scripted target filter | witness-E-404358-0 (Blade of Justice excludes explicit), witness-E-27243-1 (Seed re-target) |
| pet/owner relation | witness-F-264735-1 (PetGUID vs any owned unit) |
| dead / corpse | consecration-26573-sphere (dead units are AT candidates but get no debuff) |
| ground / destination | witness-C-2120-0 (Flamestrike), witness-A-61882-2 (Earthquake radius split), blizzard-190356-cylinder |
| multi-effect with different recipient sets | witness-F-97462-0, merge-duplicate-recipient, per-effect-dest-snapshot, witness-E-45438-0 (Ice Block grouping by function identity) |
| AreaTrigger recipients | consecration-26573-sphere, pw-barrier-62618-action, tar-trap-187699-double-entry (TG-I-D02) |
| area-aura recipients | witness-J-465-0 (Devotion Aura), witness-J-400129-0 (radius 0), witness-J-5740-0 (Rain of Fire dynobj) |

Two witness proposals were wrong and are stored corrected:
- **102359:1** is gated by `spell_dru_entangling_roots` on the Curious Bramblepatch talent.
- **97462** is ONLY_ON_PLAYER, so pets are never candidates.

## 19. Executable targeting oracle

`targeting/fixture.py` (neutral fixture), `oracle.py` (`SpellView` from DB2 or a synthetic block),
`pipeline.py` (mirror of `SelectSpellTargets`, Spell.cpp:720-878), stage modules per section,
`areatriggers.py`; CLI `scripts/research/targeting.py`.

- **The fixture** states only what a decision reads:
  - actors: kind, position, orientation, alive, health, reach, owner/creator/summoner/charmer,
    group {id, subgroup}, auras with caster, free-form facts;
  - directed relations (never symmetric by default);
  - explicit unit/src/dst;
  - `visit_order` (map enumeration);
  - LOS table;
  - `rng.draws` (consumed in consumer order);
  - `spell_value` / `modifiers` / `override`;
  - `expect`.

  The fixture profile `combat-sim` declares the PvP/world machinery absent.
- **The pipeline**:
  - computes groups lazily (because grouping can draw RNG);
  - runs TargetA then TargetB with the lead effect;
  - dispatches by category to the owning track's stage;
  - merges through `UniqueTargets`;
  - runs `CheckEffectTarget`, the effect-type fallback, per-effect dst snapshots, REQUIRE_ALL_TARGETS
    per turn and the channel mask;
  - calls script adapters through `adapters.run_target_hook` (44 of 53 executing registrations
    modelled; the rest fail closed). A fixture-stated script result must state its draws or
    `rng_free` (R2-05);
  - continues selection after a failed selector exactly as `finish()` does (R1-04);
  - marks every reproduced likely Trinity defect that decides the outcome, on the stage and in
    `Result.defects` (R2-06);
  - optionally runs B's `InitExplicitTargets` + `CheckCast` first (`precast: true`).
- **Outputs.** `evaluate` returns ordered recipients per effect, per-effect dsts, the unique list
  with masks, the cast result, draws consumed and reproduced defects. `explain` prints every
  stage: reference, candidates, each filter with the reason for each reject, ordering, RNG, cap,
  final recipients.
- **Fail-closed inventory** (`differential.json → integration_boundary`):
  - unstated visit order, LOS, collision/height/DB destinations;
  - unmodelled script hooks; implicit-target conditions;
  - charm-aura CheckEffectTarget, SKIN_PLAYER_CORPSE;
  - GameObject casters/recipients, items, vehicles, threat/tap lists, trajectories;
  - non-NONE difficulty; unapplied corrections without an override;
  - spell mods without a `modifiers` entry;
  - load-order-dependent positivity.

## 20. C++ differential probes

`targeting.py differential` → `differential.json` (seed 20260917, byte-stable). Every probe compiles
**verbatim** Trinity text extracted by its `extract.py` into ignored `.inc` files.

| probe | what is verbatim | cases | failed |
|---|---|---:|---:|
| `tc_target_selector_probe` | `_data` tables, direction angle, explicit masks (+ 4,836 real spells) | 6,051 | 0 |
| `tc_target_relation_probe` | reaction, IsValidAttack/AssistTarget, owner helpers, IsInParty/RaidWith | 13,380 (+212,940 fields in `relations.json`) | 0 |
| `tc_target_geom_probe` | Position/Object distance, HasInArc/HasInLine, boundary radius, area/cone/line/traj/nearby checks, list::sort | 27,000 | 0 |
| `tc_target_chain_probe` | SearchChainTargets loop, GetDistanceOrder, ApplySpellMod, SelectRandomInjuredTargets, SortTargetsWithPriorityRules, Radiance rules, RandomResize / SelectRandomContainerElement / RandomShuffle with a scripted RNG | 800 | 0 |
| `tc_target_positivity_probe` | `_isPositiveEffectImpl` / `_InitializeSpellPositivity` under permuted load orders | 12,369 | 0 |
| `tc_target_at_probe` | AreaTrigger shape containment (sphere/box/polygon/cylinder/disk) | 2,400 | 0 |
| pipeline wiring | area candidates and chain jumps as wired by the pipeline over the fixture library | 173 | 0 |
| **total (`differential.json`)** | | **62,173** | **0** (5 real spells fail closed in positivity) |
| `tc_target_auramap_probe` (in `tests/test_tg_j_probe.py`) | `Aura::UpdateTargetMap`, both `FillTargetMap`s, `CanBeAppliedOn`, `CheckAreaTarget`, `BuildEffectMaskForOwner`, `AddStaticApplication`, `CalcRadius`, `GetSearcherTypeMask`, `WorldObjectSpellAreaTargetCheck`, the update timer | 29 scenarios × 3 comparisons + 7 cadence + 3 witnesses | 0 |

**Integration boundary:**
- *Probe-verified:* selector tables and masks; binary32 geometry and every area/cone/line/nearby
  predicate; list sort; chain jumps; smart helpers; relation predicates; positivity; AT shapes.
- *Consumer-read only* (Python mirror, not compiled): the `SelectSpellTargets` loop and grouping
  (tested against F's plan on > 4,000 spells), `AddUnitTarget`, `CheckEffectTarget`,
  `CheckTarget`, the dest/object/channel dispatch, and the `CalcRadius` / `GetMaxRange` arithmetic.
- *Fixture-stated:* visit order, LOS, destinations, hit-roll draws, immunity, visibility, phase.

## 21. Numeric and ordering fidelity

| topic | consumer behaviour (reproduced, not cleaned up) | where |
|---|---|---|
| float vs double | distances, angles, radii binary32; `ApplySpellMod` = `T((double(base)+flat)*float pct)`; `DegToRad` float; chain-heal deficit `uint32` (D-DEF-02 truncation) | §5, §8, §10 |
| squared distances | area `distsq < (R+reach)²`; `GetDistanceOrder` centre distance² 3D without reach; `IsInDist` `distSq < d*d` | §5, §8 |
| angle normalisation | `[0, 2π]` closed; tiny negative → `2·float(π)`; 360° → 0 | §5.2 |
| `<` vs `<=` | area max `<`, vertical `<=`, arc `<=`, line width `<`, nearby `<`, chain-heal deficit `>`, chain distance `<` | §5, §8 |
| stable vs unstable sort | `list::sort` stable (LINE, HealthPct users); FURTHEST comparator non-strict → ties reversed; `ranges::sort` in the smart helpers unstable (exact ≤ 16 under libstdc++ 13, else fail closed); `std::partition` unstable (Molten Assault) | §7, §10 |
| GUID / actor-id tie-breaks | **none** in any generic path: ties resolve by container (visit / insertion) order | §5, §8 |
| container order | grid visit order (map state, fixture fact); unique list = insertion order; AT exits unordered | §5, §12 |
| random before / after sort | smart helpers: sort then shuffle boundary class; FURTHEST: sort then truncate (no RNG); area/cone: hook → RandomResize (order preserved) | §7, §10 |
| cap truncation | RandomResize keeps relative order; LINE/FURTHEST truncate after sort | §10 |
| health-percent arithmetic | `HealthPctOrderPred` = `float(health)/float(max)` (0 for max 0); the smart helpers use `IsFullHealth` only | §7 |

The edge fixtures where plausible models disagree are in `tests/test_tg_c_geometry.py`,
`test_tg_c_area.py`, `test_tg_d_chain.py`, `test_tg_d_smart.py`, `test_tg_f_recipients.py`,
`test_tg_a_composition.py` and the fixture library (§18).

## 22. Core-axis synthesis (vocabulary mapping, not a design)

Core terms are cited from `core/docs/architecture.md` (read-only). A shared word does not mean Core
supports a finding.

| Core term (architecture.md) | findings that use the same vocabulary |
|---|---|
| implicit-target identities as a five-axis source vocabulary; complete dense catalog (83-89) | §1: the five axes agree with Trinity for all 153 ids; one naming difference (57) |
| "admission is consumer-specific… audits exact selector pairs" (86-88, 103-109) | §2 composition classes; the admitted pairs cover 10,190 / 10,754 current effects |
| encounter membership and directed actor relationships in immutable topology; disposition separate from party/raid/ownership (90-101) | §4 relations are directed and owner-resolved; §6 membership is referer-based and distinct from assist validity; §13 relationship selectors |
| "a delivery resolves its complete deterministic recipient order before any recipient commits" (91-93) | §12: Trinity also builds the unique list before hits, **but** hit/miss draws are taken during that construction (§11) and later groups read the updated `m_targets` |
| exact-effect identity (21-22, 74-81) | §12: selection is per effect *group*; recipients are recorded per effect bit; grouped effects inherit the lead's values |

**The broad recurring shape**, tested against the direct consumers:

    immutable selector/policy + runtime actor set + relationship/geometry facts
      -> deterministic candidate/filter stages
      -> optional ordered/RNG selection
      -> exact effect-local recipient IDs

It **holds per selection turn**, with four required amendments that the direct consumers force:

1. **The unit of selection is the effect group** (`SelectSpellTargets` grouping), not the single
   effect. Recipient identity is still recorded per effect bit.
2. **There is spell-scoped mutable state across turns**: `m_targets` src/dst, the unique list
   (order, re-validation, LAST reference) and the draw stream.
3. **Hit-result RNG interleaves with selection**, so "resolve recipients, then draw" is not
   Trinity's order.
4. **Script adapters sit between filtering and the engine cap**, and can insert units that
   bypass filtering.

Plus two further tick-driven pipelines with their own reference actors: AreaTriggers (§5.4)
and aura target maps (§5.5, refreshed every 500 ms around the aura owner, with the aura caster
as relation caster).

Classification of facts:

| category | facts |
|---|---|
| immutable spell/effect preparation | selector pair, radius entries, ChainTargets, EffectAttributes, restrictions, attributes (after load corrections), explicit mask, positivity (except the load-order-dependent spells), grouping keys that do not depend on the caster |
| actor / provenance relation | m_caster vs m_originalCaster, aura caster vs holder, owner/charmer (one level), PetGUID, summoner |
| runtime world / encounter state | positions, orientation, visit order, LOS, group/subgroup, alive, health, auras, unit flags, immunity masks, AreaTrigger poses and curves, aura-map refresh time |
| target-policy declaration | selector axes + check type + referer rule + cap source + chain parameters |
| candidate generator | searcher mask + visit order + shape predicate (area cylinder, cone, line, nearby, chain pre-filter, AT shape) |
| deterministic filter | CheckTarget, relation check, membership, conditions, CheckEffectTarget, immunity bit-clearing |
| ordered selector | FURTHEST sort, LINE sort, chain nearest / deficit, smart-helper sort |
| RNG-consuming selector | RandomResize, SelectRandomContainerElement, RandomShuffle, random dest, grouping radius draw, SelectNearbyTarget |
| effect-local recipient set | effect bit in the unique list; per-effect dst snapshot |
| script / server adapter | 30 semantic variants in 16 families (§14); AT scripts and actions; linked spells; entry-without-condition emergency path |

**Existing Core axes that appear reusable** (vocabulary only): the five-axis selector catalog;
directed relationships with disposition; the exact-effect identity; deterministic recipient order.

**Semantic concepts that direct evidence says may eventually be needed** and that
architecture.md does not name:
- effect-group selection sharing;
- a spell-scoped target state (src/dst) read across effects;
- referer-relative membership;
- a shared RNG stream spanning selection and hit resolution;
- script insertion that bypasses filters;
- separate AreaTrigger and aura-target-map recipient pipelines, with their own owner/caster
  identities and refresh cadence.

Whether, when or how Core expresses any of them is not addressed here. **No target IR is
proposed.**

## 23. Boundaries with adjacent systems

Targeting does **not** own any of the following. The dependency edges are listed instead:

| adjacent system | edge |
|---|---|
| spell acquisition | selects the population (scope), never recipients |
| action scheduling / cast time / GCD / cooldown | `CheckCast(false)` timing and `_cast(skipCheck)` decide *whether* cast-eligibility checks rerun (§3); instant casts skip the completion check |
| payload value | chain amplitude and AoE target-index scaling read recipient **order** (`GetUnitTargetIndexForEffect`, Spell.cpp:2700; SpellEffects.cpp:666/751); `LoadSpellInfoTargetCaps` is payload |
| mitigation / hit table | SpellHitResult is *called from* selection (§11); its outcome table belongs to the attack-table research ([`auto-attack-weapon-archaeology.md`](auto-attack-weapon-archaeology.md) §7) |
| aura lifecycle | area-aura recipients are owned by the aura (`Aura::UpdateTargetMap`, §5.5): targeting provides the owner, the aura re-selects; AreaTrigger enter/exit, aura-attached ATs (395), periodic-trigger caster/holder (§13); `DoCheckAreaTarget` (0 in scope) |
| proc chance / state | Blade Flurry's `SelectNearbyTarget` draw happens in `DoCheckProc` before the proc roll; proc targets feed script casts ([`proc-pipeline-archaeology.md`](proc-pipeline-archaeology.md)) |
| encounter actor spawning | training-dummy templates (§4), summon populations ([`controlled-unit-archaeology.md`](controlled-unit-archaeology.md)) |

Cases where selection legitimately depends on runtime state:
- **aura state:** smart helpers, aura-presence filters, Seed of Corruption, TargetAuraState;
- **proc context:** Divine Image copies the proc spell's targets; Blade Flurry's draw (no recipient on 12.1 rows);
- **previous recipients:** chain source; LAST reference; unique-list re-validation;
- **channel state:** channel selectors read `m_originalCaster`'s current channel targets
  (Spell.cpp:1041-1054);
- **caster talents:** Load() gates and Register()-time conditions.

## 24. Retail-validation preparation

Every uncertain policy has an experiment row in its corpus (`retail_experiments`: question,
model A = Trinity, model B, setup, observable, fidelity). The highest-value experiments are below.
Fidelity values: **exact** = combat log / Lua shows the recipient set and order; **approximate** =
set only; **insufficient** = needs a live client or server.

| id | question | model A (Trinity) | model B | fidelity |
|---|---|---|---|---|
| TG-D-X01 | chain damage jump order | nearest to the previous target, ties → earlier | nearest to the primary / to the caster | exact (combat-log order) |
| TG-D-X10/X11 | smart-heal ranking | binary injured > player > grouped, shuffle | deficit or health-% ranking | approximate (set), exact with controlled health |
| TG-F-X4 / TG-C-X0x | target-cap order with grouped effects | one draw set shared by the grouped effects | independent per effect | exact (compare recipient sets of both effects over many casts) |
| TG-F-X1 / X3 | pets in party caps / ALLY_OR_RAID with a distant member | pets are ordinary candidates; no force-include | players first / target always included | exact |
| TG-C-X01..X03 | cone / radius boundaries, vertical band, 360° cone | full angle, reach-inclusive strict radius, `|dz| <= R` | half angle / `<=` / sphere | approximate (needs precise positions) |
| RX-A-01 | CASTER_TO_DEST cone orientation | caster facing | toward the destination | exact |
| TG-D-X20 / TG-G-03 | random-selection draw order | selection draws, then hit draws per group | draw after full list | insufficient (combat log has no RNG) |
| RX-E-01..03 | script adapters (Wild Growth group preference, Seed re-target, Radiance priorities) | §7 / §14 | tooltip semantics | approximate |
| TG-I-X04 | Tar Trap double activation (TG-I-D02) | two activations | one | exact |
| TG-B-R1..R3 | neutral range column; relation re-check at hit; dead-enemy error text | friendly column; no re-check; BAD_TARGETS | hostile column; re-check; TARGETS_DEAD | exact (error text) / approximate |

The fixtures are designed so a future Retail/WASM/V8 oracle can attach observations without
redesign. A witness row already separates `expected` (Trinity) from `actual` (oracle), so a
third `observed` column needs no schema change. No invasive live-client extraction was
implemented.

## 25. Unknowns and reopen conditions

`unknowns.json` (`targeting.py unknowns`) has **77 entries**, merged and validated. Each entry
states: id, subject, evidence, known, unknown, why unresolved, reopen condition, build-skew
status, source corpora.
- Two duplicates were merged: TG-I-11 → TG-H-03 and TG-D-13 → TG-H-01.
- By evidence: trinity-consumer 34, structural-inference 18, trinity-probe 7, unresolved 5,
  world-db-fact 5, db2-fact 4, build-skew 2, script-consumer 2. The file itself carries the exact current
  counts.

The most consequential unknowns:
- **TG-F-U5:** whether Retail groups effects at all.
- **TG-D-11:** Retail smart-heal ranking.
- **TG-C-U07 / TG-G-04:** concrete grid visit order (always a fixture fact).
- **TG-H-01:** the extended children's full policy.
- **TG-H-03:** Register()-time conditional hooks.
- **TG-A-04:** NYI targeting effect attributes (AddTargetCombatReachToAOE and others).
- **TG-G-01:** load-order-dependent positivity.
- **TG-I-01 / TG-I-02:** ATs without a consumer or without create properties.
- **TG-E-01:** script-cast edges outside every tier.
- **TG-E-06:** `Validate()` constructs that cannot be evaluated mechanically (assumed to pass).
- **TG-H-04:** build-skew effects; every one of the 188 rows cites it.
- **TG-H-05:** the oracle does not yet chain spell selection → aura target map / AreaTrigger
  ticks over time.
- **TG-J-01..08:** area-aura cell coverage and order, radius 0, Deathmark, moving-caster radius,
  refresh cadence.

## 26. Tooling and regeneration

- **One command:** `python3 tools/regen_targeting.py [--check]`.
  - Stages: `tdb` (world-DB inputs, needs `--tdb`), `dummy` (re-derives Dummy corpora after the
    bindings/conditions fixes), `probes` (make), `derived` (every corpus in dependency order).
  - `--check` fails if any tracked corpus byte changes.
  - **Result** (2026-09-17, after all hostile-review fixes):
    `--stage tdb --stage dummy --stage probes --stage derived --check` → **`ok: 45 corpus files
    unchanged`**. That covers the targeting corpora, the world-DB inputs rebuilt from the pinned
    TDB, and every Dummy-pass corpus rebuilt with the ported bindings/conditions code.
- **Determinism.** Corpora are written with sorted keys and sorted lists. They contain no
  timestamps and no absolute paths. The differential seed is fixed.
- **Build outputs.** Probes follow the repository convention: tracked `Makefile`, `extract.py`,
  `probe.cpp`, `.gitignore`; ignored `probe` binary and `*.inc`.
- **Tests.** `uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/ -q -p no:cacheprovider`
  (see the final report for counts).

---

## Likely Trinity defects (consolidated; each is reproduced by the oracle and marked, never corrected)

| id | file:line | effect on recipients | current witness |
|---|---|---|---|
| TG-A-D1 / TG-C-D01 | Spell.cpp:1284 vs SpellMgr.cpp:5281 | CONE_180 default dead; 0° cones become 90° | 6 cone spells rely on 90 |
| TG-A-D2 / TG-C-D07 | Spell.cpp:1619 | CASTER_RANDOM distance no-op | 4 effects (selector 72) |
| TG-A-D3 | Spell.cpp:1434 | UNIT_AND_DEST ModDst ASSERT without dst | none |
| TG-A-D5 / TG-C-D05 | Spell.cpp:1301, 1690-1735 | CASTER_TO_DEST cones ignore dst; DEST_DEST offsets anchored on caster | 50+ cone effects (structural) |
| TG-B-D1 | Object.cpp:2469, 2584 | sanctuary polarity inverted | latent |
| TG-B-D2 | Spell.cpp:846 | FAIL_ON_ALL_TARGETS_IMMUNE before launch immunity | 1022, 204018 |
| TG-B-D3 / D4 | SpellInfo.cpp:2494; Object.cpp:2061 | corpse aura-state exemption; null==null friendly | latent |
| TG-C-D02 | Spell.cpp:9505 | line check skips range, vertical bound and AoE immunity | none (0 lines) |
| TG-C-D03 | GridNotifiers.h ObjectDistanceOrderPred | FURTHEST ties reversed | none |
| TG-C-D04 | Position.cpp:173 + Util.cpp:882 | 360° cone → 0 | none |
| TG-C-D06 / D-DEF-03 | SpellInfo.cpp:825 | random radius drops Min | random-dest effects |
| TG-C-D08 | Spell.cpp:1673 | TargetDest offsets use RadiusMax | — |
| TG-C-D09 (legacy-only) | SpellMgr.cpp:3649 | WotLK cap 4 on current Divine Storm | 53385 |
| D-DEF-01 | Spell.cpp:2303 | chain replacement not re-tested against the jump radius | 45 chain effects |
| D-DEF-02 | Spell.cpp:2291 | uint32 deficit truncation | none |
| D-DEF-10 | Spell.cpp:9559-9567 | boundary class shuffled even when it fills exactly | Wild Growth (order/draws) |
| TG-F-D1 | Spell.cpp:741-775 | grouping ignores ChainTargets | 31935 |
| TG-F-D2 / D-DEF-20 / TG-G-D01 | Spell.cpp:772-775 | grouping comparison consumes RNG | 12 non-player effects |
| TG-F-D3 | Spell.cpp:2450-2470 | fully immune target kept with mask 0 | fixture |
| TG-F-D4 | ConditionMgr.cpp:1636-1680 | condition sharing depends on hash order | none in scope |
| TG-G-D02 | DisableMgr.cpp:360 | DEPRECATED disables rows disable LOS | none in scope |
| TD-E-01..03 | Spell.cpp:9599; spell_shaman.cpp:2262; spell_warlock.cpp:1129 | UB for 0 cap; size_t wrap; unchecked explicit target | callers safe / latent |
| TD-E-10 | Spell.cpp:1120-1180 | ENTRY selectors without conditions: no entry filter | 5 effects |
| TD-E-12 (world DB) | conditions ErrorType 103 | wrong client error for rooted Shadowstep / Ghoul Leap | 36554, 47482 |
| TG-I-D02 | spell_hunter.cpp:1351-1360 | Tar Trap activates once per hostile entering on one tick | witness |
| TG-I-D03 | AreaTrigger.cpp (Create / CalcCurrentScale) | flat radius mods ignored; % mod replaces the scale curve | — |
| TG-I-D04 | spell_priest.cpp:2259-2300 | Halo selector expects an older effect layout | 120517/120644 (structural) |
| TD-E-20 | spell_warrior.cpp:1341-1342 (Intimidating Shout) | hooks on effects 2/3 dead: primary target stays in the effect 4-6 area list | 5246 |
| TD-E-21 | spell_shaman.cpp:2343 (Sundering) | hook names a non-existent EFFECT_3 | 197214 |
| TD-E-22 | spell_rogue.cpp:283-301 | Blade Flurry HandleProc mask 0: proc gate + draw, never 22482 | 13877 |
| TD-E-23 | spell_rogue.cpp:704-719 | Killing Spree list writer on an aura effect: no casts, no draws | 51690 |
| TD-E-24 | SpellScript.h:149-178, 522-549, 584-587 | grouping compares 16 handler bytes, static handlers write 8 | 435607, 184362 (recipients unchanged) |
| TG-J-D01 | SpellAuras.cpp:2599, 2638, 2730 | only ENEMY area auras widen the cell search | 29 effects |
| TG-J-D02 | SpellAuras.cpp:2581 | caster movement leeway resizes auras owned by other units | 16 effects |
| TG-J-D03 | SpellAuras.cpp:2587-2589, 2636 | Deathmark 360194:1 (PARTY aura owned by the enemy) never applies | 360194:1 (structural) |
| TG-J-D04 | SpellAuras.cpp:691-697, 762-765 | fully immune existing aura application kept with mask 0 | probe |
| TG-J-D05 | SpellAuras.cpp:2728-2730 | persistent area auras ignore PlayersOnly | none in scope |
| data artefact | ObjectMgr.cpp:1022-1027 + TDB | current 12.x damage dummies have faction 0 → 35: not attackable by players | 194643, 194648, 197833, 242190, … |

## Build skew

- **Snapshot vs pinned Trinity.** The snapshot (12.1.0.69497) is newer than the pinned Trinity
  (≤ 12.0.7.68453). 188 current effects belong to spells added after 12.0.7
  (`dummy-corpora/build-skew.json`). They are classified `build-skew` wherever the verdict would
  otherwise read as "no consumer".
- **Script drift on 12.1 rows** (no ID skew, but the layout changed):
  - Halo (TG-I-D04);
  - Inner Peace 740: `Validate()` fails on 12.1;
  - Seed of Corruption: EffectAttributes 0x8000 is not PlayersOnly, so its effects group;
  - 11 target-hook registrations whose effect masks are 0 on 12.1.
- **AreaTriggers.** 3 of the 31 blocked effects are 12.1-only.

## Errata to earlier reports

- **Dummy report §13 / bindings.** `TargetHook::CheckEffect` is now ported exactly. No count
  changed; 39 player hook notes lost their "approximate" note. `test_tg_e_checkeffect.py`
  enforces zero mask change.
- **Dummy report §12 / `dummy_semantics/conditions.py`.**
  - `SPELL_CLICK_EVENT` conditions were keyed by SourceGroup (a creature entry) instead of
    SourceEntry (ConditionMgr.cpp:1187). This produced false-positive player rows (14 rows /
    2 spells: 46598, 408907), now 0.
  - A missing condition object is now false before negation (ConditionMgr.cpp:284-288).
  - Only `dummy-corpora/conditions.json` changed; the 122 Dummy + E tests pass.
- **Dummy report §12 "In player scope: one row, Shadowstep 36554".** Also Ghoul Leap 47482 in the
  controlled-unit tier. The ErrorType 103 meaning differs between enums (TD-E-12).
- **Dummy bindings do not evaluate `Validate()`.** In targeting scope, 5 registrations never attach
  on 12.1 rows (740; 184362 and 357209 already fail on 12.0.7). A Dummy-wide Validate audit has
  not been done.
- **Dummy bindings count 18 hooks as executing that never attach** (15 spell/script pairs whose
  `Validate()` fails on 12.1 rows; `script-adapters.json → dummy_validate_erratum`). The Dummy
  corpora were not re-counted for this; the list is the reopen pointer.
- **Dummy script index.** It records Register()-time conditional hooks as unconditional (17
  classes, TG-H-03). `spell_priest.cpp`'s `parse_errors` entry is local; all its classes and hooks
  are indexed.

## Hostile closeout

Three independent major-only hostile reviews ran in fresh contexts (reports in the session
workspace, `bag/targeting-pass/reviews/R{1,2,3}.md`). Each finding was verified by the owning track
against the pinned source. Every accepted finding was fixed and all dependent corpora were
regenerated. The final `regen_targeting.py --check` over all four stages is clean (§26).

| id | review | verdict | finding | resolution |
|---|---|---|---|---|
| R1-08 | consumer completeness | **DEFECT (blocker)** | the aura-side recipient pipeline (`UnitAura/DynObjAura::FillTargetMap`) was not modelled; area-aura effects counted "simple understood" | new track J (§5.5, `auratargets.py`, `aura-targets.json`, probe, 134 tests, 3 witnesses); 35 effects reclassified |
| R1-02 | consumer completeness | DEFECT | training dummies selected by ScriptName only (legacy); faction 0 → 35 rewrite and CAN_ASSIST missing | 326-template selector, load rewrite, per-dummy verdicts; current damage dummies are not attackable in Trinity (§4) |
| R1-03 | consumer completeness | DEFECT | `combat-sim` profile silently defaulted two world-DB type flags | no default; fail closed or read from the TDB |
| R1-04 | consumer completeness | DEFECT | the oracle stopped all selection after a failed nearby selector | `finish()` semantics mirrored; new fixture (§12) |
| R1-01 | consumer completeness | WEAKEN | build-skew rows had no reopen pointer | TG-H-04 attached to all 188 |
| R1-05 | consumer completeness | WEAKEN | cross-effect dst reads counted with pair-only grouping (8) | exact grouping: 17 effects / 15 spells (§2) |
| R1-06 | consumer completeness | WEAKEN | unordered AT iteration counted understood; Rain of Fire 5740:2 untagged | 2 AT rows → fixture-dependent; helper-site owners get rows |
| R1-07 | consumer completeness | WEAKEN | grouping considered hooks of scripts that never load | `script_loads` gate; Tranquility 740 one group |
| R2-01 | ordering / RNG / numeric | DEFECT | random-dest draw census keyed by selector, not by the `CalcRadius` path | 6 radius+angle / 5 angle-only |
| R2-04 | ordering / RNG / numeric | DEFECT | chain census counted effects whose ChainTargets stays ≤ 1 | 46 → 43 chain effects |
| R2-06 | ordering / RNG / numeric | DEFECT | reproduced Trinity defects not flagged in traces | `defect=` on the deciding stage and in `Result.defects`, with tests (A/C/D/F/G ids) |
| R2-02 | ordering / RNG / numeric | WEAKEN | cast draw order omitted OnCast and launch-handler draws | order extended; current census empty |
| R2-03 | ordering / RNG / numeric | WEAKEN | shuffle engine consumption misdescribed | floor(n/2) paired calls; TG-D-24 (stdlib-specific index model) |
| R2-05 | ordering / RNG / numeric | WEAKEN | fixture-stated script results consumed no RNG | draws must be stated or `rng_free`; witnesses run the modelled hooks |
| R3-01 / R3-06 | script adapters / skew | DEFECT | Blade Flurry and Killing Spree treated as recipient-choosing; their acting hooks have mask 0 on 12.1 | draw-only / dead; TD-E-22/23 |
| R3-02 | script adapters / skew | DEFECT | Primordial Wave Register() is spec-conditional | verdict made conditional |
| R3-03 | script adapters / skew | DEFECT | helper scan missed AreaTrigger / stored-GUID / aura-list lookups | 109 → 128 sites, 12 → 18 recipient-choosing; declared a lower bound |
| R3-04 | script adapters / skew | WEAKEN | conditional Register() list incomplete; Holy Prism rows are never registered | 17 classes (TG-H-03); 67 attached registrations |
| R3-05 | script adapters / skew | WEAKEN | "function identity" is a byte compare that is unspecified for static handlers | fail closed; TD-E-24 / TG-E-04 |
| R3-07 | script adapters / skew | WEAKEN | stale hooks not recorded as defects | TD-E-20..23 + Retail experiments |
| R3-08 | script adapters / skew | WEAKEN | more in-scope scripts fail `Validate()` | mechanical Validate audit; Dummy erratum (18 hooks) |

Two further corrections came from the lead's own reconciliation:
- **Seed of Corruption 27243.** Track D had its effects 1/2 hooks running independently. They
  are one group (EffectAttributes 0x8000 is not PlayersOnly), so the hook runs once.
- **Script index.** Track I claimed `spell_priest.cpp` was missing from the index. The file is
  fully indexed; the real gap is conditional `Register()`.

Attacks that **held** (tried and failed to falsify):
- TargetA/TargetB union semantics; the TargetB radius fallback.
- Candidate-search vs `AddUnitTarget` check order (area, cone, line, nearby).
- Party/raid membership and ALLY_OR_RAID / CASTER_AND_SUMMONS.
- Smart-heal bit order, binary "injured", exact-fill shuffle, ≤ 16-element stable sort with fail
  closed beyond.
- Chain nearest-to-previous with strict `<`, D-DEF-01 reachable (only when the current pick has
  the larger combat reach).
- Cap order and FURTHEST tie reversal (an independent C++ test: 0 mismatches in 3,000 cases).
- Every `SpellHitResult` draw gate; random-dest draw order.
- The 360° and tiny-negative-angle edges.
- The `CheckEffect` port (153/153) and its dispatch rule; the Dummy "no count changed" claim; both
  `conditions.py` fixes.
- World-policy claims (source 13 empty; Shadowstep / Ghoul Leap; ErrorType 103).
- AreaTrigger claims (client create-properties never read; shapes; position-only children; Tar
  Trap double activation; blocked/unresolved rows are not build skew).
- Legacy Divine Storm cap; load-correction completeness; no ABORT path.
- **Determinism:** census twice byte-identical; `differential` twice byte-identical; seven corpora
  identical across two `PYTHONHASHSEED` values; 15 track corpora regenerated to scratch matched.

The strongest conclusion survived review, but review **widened** it. Targeting is more
heterogeneous than one pipeline:
- spell selection per effect group;
- AreaTrigger ticks;
- aura target maps;
- script adapters that can bypass filters and read state from earlier casts.

Each of the three pipelines does reduce to "candidates (visit order) → deterministic filters →
optional order/RNG → cap → recipients". What differs between them is who owns the candidates,
the reference actor, the cadence and the caster identity.
