# Unresolved and rejected inference ledger

## Purpose and scoring

This is the centralized pessimistic ledger for reports 01 through 15. It contains every material
partial, rejected, or unknown semantic boundary that could otherwise be lost behind a strong local
finding. A row is not a request to guess: opaque preservation with source provenance is the safe
terminal result until the listed evidence exists.

The evidence snapshot is Wago retail `12.1.0.69497` at wowlab-data
`2ddced452a6f9076de5c86bc92f73de5b60f8556`, Core
`9bd3b8ed6f0f57587f443b857204318e7339b038`, TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimulationCraft
`b48def9c26d7532db2e612d97d433ec66bd8eede`. The source reports record their DuckDB 1.5.5
population queries and executable-source provenance.

Priorities are deliberately conservative:

- **P0** — a tempting inference can change admission, targets, amounts, state mutation, ownership,
  or persistence and can silently produce a wrong result.
- **P1** — an active current identity or relation lacks enough semantics for execution; dropping or
  normalizing it loses fidelity, although retaining it opaquely is safe.
- **P2** — the gap is mainly presentation, cross-runtime parity, or future-policy scope and does not
  currently justify a generic operation.

For final reporting, **high-risk means P0 + P1**. This ledger has **89 entries: 56 P0, 31 P1, and
2 P2, hence exactly 87 high-risk entries**. Counts are based on ledger rows, not the number of Wago
records grouped into a row.

## Ledger

### 01 — proc and aura options

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 1 | Proc event | `SpellAuraOptions`, proc raw 26 | Partially characterized | Proc-clone is a fully defined event | Trinity has a name but no generic producer; two current spells do not define event timing | One executable producer or retail event trace | Untriggerable or wrongly timed proc | P1 |
| 2 | Proc event | `ProcTypeMask_1`, raw 35 / bit 3 | Unknown after exhaustive available evidence | Infer meaning from adjacent bits or `Photo Finisher` | Only one current spell; neither comparison source defines or emits it | Current client enum or controlled trigger trace | Wrong event admission | P0 |
| 3 | Proc event | `ProcTypeMask_1`, raw 37 / bit 5 | Unknown after exhaustive available evidence | Item proc, periodic proc, or other dominant cohort | 178 spells are heterogeneous and both comparison runtimes omit the bit | Executable producer distinguishing the event | Broad proc misfires or suppression | P0 |
| 4 | Proc event | `SpellAuraOptions`, proc raw 38 | Partially characterized | “Do emote” name is a complete trigger contract | Trinity names it but has no generic producer | Producer call site plus actor/target trace | Missing presentation/content procs | P1 |
| 5 | Proc chance | `ProcChance` 101/105/109 | Rejected interpretation | Distinct probabilities above 100% | Trinity's `[0,100)` roll makes all values at least 100 guaranteed; distinct authoring purpose is lost | Retail/client interpretation of the sentinel values | Incorrect probability and RNG use | P0 |
| 6 | Proc counters | `ProcCharges`; `CumulativeAura` extremes | Rejected interpretation | Every integer is a literal live charge/stack maximum | Current values include -1, 999999, and 65000 while Trinity's live counters are bytes and wrap | Client/host counter representation and sentinel definitions | Corrupted lifecycle/counter state | P0 |
| 7 | RPPM | `SpellProcsPerMinute.Flags` bits 0/1 | Unknown after exhaustive available evidence | Assign bad-luck or scaling semantics from frequency | Values 1 and 3 dominate, but neither Trinity nor SimC reads the flags | Current executable flag consumer | Dropped active RPPM mode | P1 |

### 02 — cooldowns, categories, and charges

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 8 | Cooldown | 37 `SpellCooldowns` rows with category recovery greater than spell recovery | Partially characterized | The larger category end always remains active | Trinity cleanup can erase the longer category when the shorter spell end expires | Client trace of category lock after spell expiry | Early recast admission | P0 |
| 9 | Charges | `SpellCategory.TypeMask` bits 0/2/3/5/6 | Unknown after exhaustive available evidence | Give each bit a stable public charge type | Executable code proves only mask intersection; no source names individual bits | Authoritative bit catalog or independent consumers | Modifier applied to wrong charge family | P1 |
| 10 | Cooldown | `SpellCategory.Flags` `0x02` | Partially characterized | This bit implements the global cooldown | Named “global” but NYI; start-recovery fields implement Trinity's GCD separately | Client consumer of this exact category bit | Duplicate or missing shared lock | P1 |
| 11 | Charges/category | `SpellCategory.Flags` `0x10`, `0x80` | Unknown after exhaustive available evidence | Encounter reset and a harmless reserved bit | `0x10` is named but NYI; populated `0x80` has no identity or consumer | Encounter traces and current client enum | Stale charges across encounter or lost mode | P1 |
| 12 | Weekly limits | `SpellCategory.UsesPerWeek` | Unknown after exhaustive available evidence | Per-character weekly cast cap | Loaded but unconsumed; account/service ownership is unknown | Host/service enforcement trace | Wrong owner and reset policy | P1 |
| 13 | Category topology | Category, charge-category, start-recovery-category | Rejected interpretation | One interchangeable foreign key or one max-duration lock | The three roles use different maps/phases and rarely coincide; reversed rows defeat max-only logic | No new evidence needed; retain typed edges | Systemic cooldown corruption | P0 |

### 03 — power and cost

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 14 | Cost formula | `SpellPower.ManaCostPerLevel` | Unknown after exhaustive available evidence | `base + level * field` | 128 rows exist, but Trinity never reads it and SimC calls the slot unknown | Current client formula or authoritative layout | Wrong resource payment | P0 |
| 15 | Cost formula | `SpellPower.PowerCostMaxPct` | Partially characterized | Verified maximum-percent spend | Trinity's only branch is internally contradictory for ordinary values; SimC behavior is not game proof | Retail cost trace for positive and negative rows | Wrong cap/grant and affordability | P0 |
| 16 | Resource display | `PowerDisplayID`, `AltPowerBarID` | Partially characterized | Display identity replaces `PowerType` and owns payment | Joins establish UI context, but generic cost code does not consume either selector | Client/host ownership consumer | Cost charged to wrong resource | P1 |
| 17 | Cost row | 145 zero-numeric `SpellPower` rows | Partially characterized | Empty/no-op rows safe to drop | Rows carry display, aura, difficulty, vehicle, and encounter context | Client use of a zero-payload row | Lost conditional/display semantics | P1 |
| 18 | Power identity | `SpellPower.PowerType` | Rejected interpretation | Foreign key to `PowerType.ID` | Complete joins fail; enum raw and table record ID are distinct namespaces | No new evidence needed; preserve enum raw | Widespread resource misidentification | P0 |
| 19 | Payment composition | Multiple spell-power rows; signed fields | Rejected interpretation | Pay all rows simultaneously and reject negatives as malformed | Difficulty rows are alternatives; negative values can grant; bypasses and required auras alter execution | No new evidence needed; resolve then evaluate typed rows | Double charge or inverted grant | P0 |

### 04 — applicability selectors

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 20 | Label modifiers | Aura raws 470, 648, 649, and 320 | Partially characterized | SimC's label behavior is the universal operation | Trinity handlers are null; aura 320's sources disagree on misc0 versus misc1 | Generic client/server consumer for each raw | Modifier hits wrong spells | P1 |
| 21 | Class masks | 1,661 observed family-bit pairs | Unknown after exhaustive available evidence | Give raw bits stable global ability names | All 128 positions are reused across family namespaces and NPC/history cohorts | Versioned authoritative per-family catalog | Misbound modifier selectors | P1 |
| 22 | Class masks | `SpellClassMask_0..3` | Rejected interpretation | Bits are global spell properties | Executable checks require family equality or operation-specific wildcard/multiplicity | No new evidence needed; retain family and four words | Broad cross-class applicability | P0 |
| 23 | Selector families | Class mask, label, category | Rejected interpretation | Interchangeable whitelists | Populations diverge and operations use different equality, intersection, and multiplicity rules | No new evidence needed; keep typed predicates | Wrong modifier membership | P0 |
| 24 | Label namespace | Effect misc fields named “label” | Rejected interpretation | Every such value joins `SpellLabel` | Effects 316/317 and creature/game-object labels use different namespaces | Operation-specific enum/schema | Wrong foreign-key binding | P0 |

### 05 — mechanic, dispel, immunity, and school

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 25 | Diminishing returns | `SpellCategories.DiminishType` | Unknown after exhaustive available evidence | Decode powers/composites as known DR flags | Trinity derives DR separately and SimC has no generic field consumer | Current client enum and application/reset consumer | Wrong CC duration/DR bucket | P1 |
| 26 | Dispel | Effect 38 and aura 41 `misc0=-1` | Partially characterized | Normalize to “all,” raw 7, or raw 0 | Generic Trinity shifting/casting does not explain 26 negative rows | Authoritative negative-mode enum or retail dispel trace | Overbroad/underbroad removal | P0 |
| 27 | Dispel | Dispel raw 11 | Rejected interpretation | Old Trinity “unused” label means ignorable | Wago names Bleed and 886 rows / 880 spells use it | No new evidence needed for identity; removability still separate | Bleed class silently dropped | P0 |
| 28 | Immunity | Auras 39/40/77 high `misc0` payloads | Partially characterized | Always simple school/mechanic enum | Values far exceed the enums and generic shifts cannot represent them | Client layout or executable packed-mode decoder | Incorrect immunity admission | P0 |
| 29 | Immunity | Aura 147 `misc0` to `CreatureImmunities.ID` | Rejected interpretation | Current Wago table is a complete foreign-key target | Only 2 of 606 rows join; Trinity uses a different server supplement | Authoritative composite-immunity dataset | Almost all composite immunities lost | P0 |
| 30 | Immunity | `CreatureImmunities` flags/allowed words and multi-source lifecycle | Partially characterized | Decode bit positions and apply one universal stacking policy | Five client records and divergent server schema do not establish allowed/flags or simultaneous-source removal | Client ingestion plus apply/remove trace | Leaked or prematurely removed immunity | P1 |

### 06 — difficulty resolution

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 31 | Difficulty | All difficulty-bearing rows | Rejected interpretation | Add all variants together | Mutually exclusive component/index coordinates and differing signatures duplicate semantics | No new evidence needed; route per component | Duplicate effects and masks | P0 |
| 32 | Difficulty | Nonbase spell rows | Rejected interpretation | One nonbase row replaces the whole spell | Effects and singleton components fall back independently | No new evidence needed; preserve source difficulty per component | Missing inherited data | P0 |
| 33 | Difficulty | `Difficulty.FallbackDifficultyID` | Rejected interpretation | Requested difficulty then base only | Current chains reach length five | No new evidence needed; traverse validated graph | Wrong raid/dungeon values | P0 |
| 34 | Difficulty | Universal client parity / future DB2 policy | Unknown after exhaustive available evidence | Apply Trinity's route generically to every client table | No evidence every subsystem or future table shares one policy | Client implementation or table-specific authoritative docs | Scope/parity drift | P2 |

### 07 — scaling and coefficient provenance

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 35 | Scaling | `SpellEffect.PvpMultiplier` | Partially characterized | Universal PvP multiplier at a known phase | 233 values and five negatives; Trinity's generic amount path does not copy it | Client PvP formula and mode predicate | Wrong combat amount | P0 |
| 36 | Scaling | `GroupSizeBasePointsCoefficient` | Partially characterized | Obvious additive group-size scaling | Nearly universal population, `-1` outlier, and no generic consumer leave operation/order unknown | Executable group-size formula | Wrong raid scaling | P0 |
| 37 | Scaling | Sparse large coefficient-like values | Rejected interpretation | Magnitude proves a real coefficient | Values 63,034, 100,000, and 345,905 conflict with ordinary units and may be sentinels/schema payloads | Field-specific decoder | Catastrophic amount inflation | P0 |
| 38 | Scaling | All coefficient fields | Rejected interpretation | Unconditionally multiply every populated field | Executable branches, rounding, stat channels, and 40,941 variance-without-scaling rows contradict a product | No new evidence needed; operation-specific formula | Systemic damage/heal error | P0 |
| 39 | Scaling schema | `Node__Field_12_0_0_63534_001` | Unknown after exhaustive available evidence | Infer from generated position or neighbor | Anonymous extraction name has no semantics or consumer | Authoritative DB2 layout history | Unknown payload discarded | P1 |
| 40 | Scaling order | PvP/group-size and special scaling classes | Unknown after exhaustive available evidence | Trinity/SimC subsets define universal client order | Neither implements the complete client surface | Client formula trace with intermediate values | Wrong ordering and rounding | P0 |

### 08 — duration, periodic, and refresh

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 41 | Duration | Negative `SpellDuration.Duration` | Rejected interpretation | Every negative value is permanent | Only `-1` is Trinity's sentinel; `-600000` becomes positive 600 seconds | No new evidence needed for Trinity; client reason for signed row remains | Permanent aura leak | P0 |
| 42 | PvP duration | `SpellMisc.PvPDurationIndex` | Partially characterized | Select this record whenever in PvP | 417 rows resolve, but neither Trinity nor SimC selects the field; mode and zero-minimum behavior unknown | Client PvP selection trace | Wrong CC/aura duration | P0 |
| 43 | Travel time | `SpellMisc.MinDuration` | Rejected interpretation | Minimum aura duration | Trinity uses it as projectile hit-delay floor | No new evidence needed; keep travel-time domain | Wrong aura lifetime | P0 |
| 44 | Periodic scheduling | Nonzero `EffectAuraPeriod` | Rejected interpretation | Guarantees a tick and integer authored tick count | 57 periods exceed duration; scripts, operation compatibility, refresh, and partial ticks intervene | Runtime trace for unresolved families | Extra/missing periodic effects | P0 |
| 45 | Effect fields | `EffectAmplitude` versus `EffectAuraPeriod` | Rejected interpretation | Amplitude is the Wago aura period | Distinct columns; only 1,305 overlap; Trinity uses amplitude for jump speed/logging | No new evidence needed; retain both | Cross-domain field corruption | P0 |
| 46 | Effect fields | `EffectAmplitude` outside jump/log uses | Unknown after exhaustive available evidence | Apply one generic “amplitude” formula | 48 other effect kinds lack a generic consumer | Operation-specific client consumers | Lost movement/presentation payload | P1 |
| 47 | Refresh | Spell attribute raw 489 | Partially characterized | Recast is wholly ignored with no side effects | New SimC disabled-refresh model has no Trinity counterpart; application/stack behavior unproved | Independent runtime refresh trace | Wrong aura lifetime/state | P0 |

### 09 — targets, ranges, radii, and restrictions

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 48 | Targeting | Implicit selector raw 0 | Rejected interpretation | No target | Other selector, explicit payload, effect kind, or authored mask can supply/require one | No new evidence needed; evaluate full target contract | Missing or misdirected effect | P0 |
| 49 | Radius | Radius index/value zero | Rejected interpretation | Global or unlimited area | Trinity treats missing radius as no area radius; global behavior comes from another operation | No new evidence needed | Global accidental targeting | P0 |
| 50 | Target cap | `SpellTargetRestrictions.MaxTargets=0` | Rejected interpretation | Select zero targets | Trinity applies the cap only when nonzero | No new evidence needed | Suppress every target | P0 |
| 51 | Range | `SpellRange.Flags`, polarity pairs | Partially characterized | One min/max pair and known meanings for flags 0/1/2 | Friendly/hostile pairs and special reach are executable; full client flags/presentation are not | Client range/presentation consumer | Wrong admission at range boundary | P1 |
| 52 | Target selector | Raws 10,14,111,139,141,143,144,145,147,152 | Unknown after exhaustive available evidence | Coerce reserved selectors to caster/target/area | Current populations exist but no generic algorithm is defined | Current target-enum docs or client execution | Arbitrary target selection | P0 |
| 53 | Cast restrictions | Faction, reputation, aura-vision, `MaxTargetLevel` | Unknown after exhaustive available evidence | Names imply obvious comparison direction | Trinity does not execute them and SimC omits them | Independent predicate with bypass rules | Wrong cast admission | P1 |

### 10 — shapeshift and equipment requirements

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 54 | Forms | `ShapeshiftMask` / `ExcludeShapeshiftMask` | Rejected interpretation | First 32-bit word is the whole form set | 33 current second-word inclusions and 94 exclusions are direct counterexamples | No new evidence needed; retain 64 bits | Wrong form admission | P0 |
| 55 | Form lifecycle | Aura persistence across form change | Partially characterized | Admission mask also fully determines aura removal | Separate removal checks, attributes, and exceptions exist | Apply/change-form/remove trace | Aura leaks or premature removal | P1 |
| 56 | Form UI | `SpellShapeshiftForm.StanceBarOrder` | Unknown after exhaustive available evidence | Stable gameplay priority/order | Signed -1..6 field is loaded but unconsumed by both runtimes | Client action-bar consumer | Presentation/action-bar only | P2 |
| 57 | Equipment identity | `EquippedItemClass` | Rejected interpretation | Joins `ItemClass.ID` record key | It joins `ItemClass.ClassID`; namespaces differ | No new evidence needed | Wrong equipment admission | P0 |
| 58 | Equipment execution | Non-weapon/non-armor equipped requirements | Partially characterized | Trinity's weapon/armor proc rules generalize to every item class | Generic source implements those classes and explicit item-target relaxations only | Client/server consumer for other classes | Incorrect cast/proc eligibility | P1 |

### 11 — threat, aggro, and combat state

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 59 | Threat effect | Effect raw 91 | Partially characterized | “Threat All” is multi-target effect 63 | Five heterogeneous rows and Trinity `EffectNULL` provide no operation | Executable handler or retail trace | Wrong threat-list mutation | P1 |
| 60 | Threat redirect | Effect raw 130, `misc0` | Partially characterized | 100% redirect lasts for `misc0` milliseconds | Registration is executable, but handler ignores misc0 and no generic unregister caller exists | Apply/expiry/removal trace | Persistent or prematurely ended redirect | P0 |
| 61 | Combat aura | Aura raw 311 | Partially characterized | Prevent engagement, clear combat, or suppress threat | 87 rows fit a family, but Trinity marks NYI and SimC offers only a label | Enter/apply/remove trace in retail | Broken combat/AI state | P0 |
| 62 | Historical aura | Aura raw 183 | Rejected interpretation | “Critical threat” modifier | Trinity renamed and executes health-threshold critical chance; current misc1 thresholds fit | No new evidence needed; update identity | Wrong combat subsystem entirely | P0 |
| 63 | Threat attributes | Raws 53,94,152,156 | Unknown after exhaustive available evidence | Names fully define threat-on-miss, active threat, ranged combat, timer ignore | Populated bits have no generic consumers | Executable check for each exact flattened raw | Missing combat exceptions | P1 |
| 64 | Historical attribute | Raw 22 / SimC `SX_NO_COMBAT` | Rejected interpretation | No-combat flag | SimC display and Trinity identify client-only target tracking | No new evidence needed; token is stale | Wrong cast/combat classification | P0 |
| 65 | Threat composition | No-threat variants and aura 11 taunt | Rejected interpretation | Collapse flags; taunt alone matches top threat | Four suppression flags have disjoint cases; numeric matching is a sibling effect on only 167/254 taunt spells | No new evidence needed; preserve layers | Incorrect threat and target choice | P0 |
| 66 | Combat transitions | Channel interrupt flags and entry/exit ordering | Partially characterized | Aura and channel bits share one symmetric lifecycle | Separate fields/call paths; enter has proc raw 27 and leave has none | Full channel transition trace | Wrong interruption ordering | P1 |

### 12 — item, enchant, and bonus relationships

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 67 | Item effect | `ItemEffect.TriggerType` raw 15 | Unknown after exhaustive available evidence | Spell-trigger role inferred from neighbors | All 218 rows have zero spell payload and no generic consumer/name | Current layout or client consumer | Active item role discarded | P1 |
| 68 | Enchant | `SpellItemEnchantment.Effect_n` raw 14 | Unknown after exhaustive available evidence | Extend neighboring enchant enum | 58 slots exist beyond Trinity's enum; arguments do not identify type | Current enum or executable slot consumer | Lost enchant operation | P1 |
| 69 | Enchant | Effect type 0 with companion arguments | Rejected interpretation | Zero type means entire slot is empty | 186 zero-type slots retain nonzero arguments | Authoritative tuple rule for those rows | Silent payload loss | P0 |
| 70 | Enchant | `EffectArg/Points/ScalingPoints` | Rejected interpretation | Flatten every argument as spell or stat ID | Type-specific consumers use multiple namespaces and units | No new evidence needed; keep typed tuples | Wrong item mutation | P0 |
| 71 | Item bonus | Bonus types 52 and 53 | Partially characterized | SimC crafting-quality/item-level logic is universal | SimC supports them, Trinity does not; priority and disable rules remain incomplete | Independent client/server consumer | Wrong crafted/item level | P1 |
| 72 | Bonus graph | `ItemXBonusTree`, unknown bonus types | Rejected interpretation | All reachable bonuses active; unknown types ignorable | Concrete instance context selects lists; newer SimC support proves missing Trinity cases are meaningful | No new evidence needed for rejection; host instance data for activation | Systemic item-stat corruption | P0 |
| 73 | Inventory lifecycle | Crafting/upgrade/sequence graph | Partially characterized | Static graph proves transactional upgrade, rollback, refund, persistence | Repositories do not jointly expose the host mutation lifecycle | Inventory-authoritative transaction trace | Duplicated/lost items or bonuses | P1 |

### 13 — movement, world, and host transitions

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 74 | Jump/charge | Effect raw 254 `misc0` | Partially characterized | Wago contains all movement parameters | 1,527 keys refer to Trinity's external `jump_charge_params`, absent from Wago | Authoritative supplemental parameter table | Cannot reconstruct movement | P0 |
| 75 | Scene | Effect raw 198 `misc0` | Rejected interpretation | Always `SceneScriptPackage.ID` | Only 137 of 244 nonzero values join | Current namespace discriminator/consumer | Wrong scene or host action | P0 |
| 76 | Seamless world | `SeamlessSite.MapID` | Rejected interpretation | Total `Map.ID` foreign key and transfer contract | 15 nonzero rows miss; no generic transfer consumer exists | Client site-selection/transition consumer | Incomplete world handoff | P1 |
| 77 | Transport | Animation/physics/rotation tables | Partially characterized | Static rows fully reconstruct passenger motion | Attachment, collision, interpolation clock, and authority remain host state | Host transport trace/protocol | Desynchronized position | P1 |
| 78 | Transition taxonomy | Teleport/jump/taxi/scene/phase/transport and numeric joins | Rejected interpretation | Every transition is teleport; any numeric join proves payload type | Operations have different owners/protocols and documented partial joins; effect 50 creates a game object rather than transferring the actor, and none of its 1,031 references joins current `GameObjects.ID` | No new evidence needed; preserve operation identity | Wrong state owner and movement | P0 |
| 79 | World operations | Effects 191/227; failed cross-map transaction | Unknown after exhaustive available evidence | Names define client action and failures roll back atomically | Effect 227 is `EffectNULL`, effect 191 lacks a generic implementation, and server transfer paths do not prove client semantics or rollback | Protocol/host traces for success and each failure stage | State split across maps/services | P1 |

### 14 — derived static classification

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 80 | Classification | Ranged weapon, attack type, allow-dead, group-buff, hit-delay composites | Partially characterized | Treat composites as mutually exclusive universal spell categories | Each is a purpose-specific executable predicate with exceptions and overlapping inputs | Independent client classification contract | Misleading static taxonomy | P1 |
| 81 | Positivity | Effect amount sign | Rejected interpretation | Positive amount means beneficial; negative means harmful | Effect/aura operation changes sign meaning; zero and forced classifications dominate | No new evidence needed; run versioned derivation | Wrong assist/attack admission | P0 |
| 82 | Positivity | Hostile implicit selector | Rejected interpretation | One hostile selector makes whole spell negative | Passive/force flags, sibling effects, per-effect bits, and recursion change the result | No new evidence needed | Wrong polarity and aura behavior | P0 |
| 83 | Positivity | Trinity `NegativeEffects` as authoritative client truth | Unknown after exhaustive available evidence | Store Trinity result as lossless Wago fact | Wago has inputs only; Trinity has versioned hardcodes and no independent generic parity | Current client/server polarity implementation | Cross-subsystem polarity errors | P0 |
| 84 | Explicit targets | Selector raw zero | Rejected interpretation | No explicit/required target | Other selector, effect object kind, and authored restrictions still contribute | No new evidence needed; derive after resolution | Cast accepts/rejects wrong payload | P0 |
| 85 | Explicit targets | Derived explicit/required masks | Unknown after exhaustive available evidence | Compute authoritatively from CSV before corrections/difficulty | Exact outputs need resolved effects, versioned target-object table, and server corrections | Versioned correction set plus executable derivation | Wrong required target mask | P0 |

### 15 — cross-surface model

| # | Subsystem | Table / field / raw | Terminal disposition | Tempting interpretation | Conflict / reason rejected | Smallest missing evidence | Risk | Priority |
|---:|---|---|---|---|---|---|---|---|
| 86 | Ownership | Any high-correlation aura/attribute/effect pair | Rejected interpretation | Correlation transfers semantics or state ownership | Proc, aura, effect, item, and host operations retain distinct owners and phases | No new evidence needed; require executable edge | Cross-surface semantic contamination | P0 |
| 87 | RNG | Variance/proc/PPM and subsystem streams | Partially characterized | One deterministic retail RNG stream/order | Trinity and SimC prove local orders, not cross-client/server/host draw parity | Instrumented retail subsystem traces | Replay/simulation divergence | P1 |
| 88 | Failure/rollback | Cast, proc, cost, item, movement, world operations | Rejected interpretation | Every failed operation is pure and rolls back | Mutation and failure points differ by subsystem | No new evidence needed; model phase-specific commits | Duplication, resource loss, state split | P0 |
| 89 | Universal lifecycle | All CSV metadata and host systems | Unknown after exhaustive available evidence | One compiler can execute a complete atomic spell lifecycle from Wago alone | Client-only fields, server supplements, inventory/world services, presentation, and persistence are outside one source | Subsystem-specific client/host protocol and executable traces | System-wide false completeness | P0 |

## Durable use rule

P0 rows must block any semantic lowering that would execute the tempting interpretation. P1 rows
must remain typed and opaque with source table, row, identity, selected difficulty, and owner
provenance; they may be ignored only by an explicitly scoped consumer, never erased as meaningless.
P2 rows may remain metadata-only, but must not be reported as established gameplay semantics.

The smallest-evidence column is intentionally specific. More spell-name correlation or an adjacent
enum label does not close these entries. A row changes disposition only after the missing executable,
protocol, authoritative-layout, or controlled-trace evidence is obtained and its complete current
population—including outliers—is rechecked.
