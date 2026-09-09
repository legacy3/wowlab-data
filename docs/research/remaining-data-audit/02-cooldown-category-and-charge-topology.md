# Cooldown, category, and charge topology

## Result

The current cooldown surface is four related mechanisms, not one duration: an individual spell
timer (`RecoveryTime`), a timer keyed by `SpellCategories.Category`
(`CategoryRecoveryTime`), a global/start-recovery timer keyed independently by
`StartRecoveryCategory` (`StartRecoveryTime`), and a queue of consumed charges keyed by
`ChargeCategory`. `SpellCooldowns.AuraSpellID` adds a fifth, non-timer admission lock while that aura
is present. The three category fields all reference `SpellCategory.ID`, but they have different
owners and runtime consumers and are not interchangeable.

This topology is **strongly verified** from complete current populations and TrinityCore's
`SpellHistory` implementation. Several individual `SpellCategory` fields are weaker:
`UsesPerWeek` has no current TrinityCore or SimC runtime consumer, four category-flag identities are
NYI/unknown, and a charge-category reference with zero effective maximum or recovery is inert unless
another aura supplies the missing quantity. Those conclusions are intentionally partial or unknown.

Evidence versions are Wago `12.1.0.69497`
(`2ddced452a6f9076de5c86bc92f73de5b60f8556`), TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimC
`b48def9c26d7532db2e612d97d433ec66bd8eede`. DuckDB 1.5.5 scanned every row. File line counts and an
independent grouping query rechecked the table totals and uniqueness.

## Complete population

### Cooldown rows

`SpellCooldowns` has 36,331 unique `(SpellID,DifficultyID)` rows for 36,259 spells. Fifty spells
have multiple difficulty rows (maximum five); 36,259 rows are base difficulty and 72 are spread
across 13 nonbase difficulties. There are no duplicate spell/difficulty coordinates.

| Nonzero fields | Rows | Distinct spells |
|---|---:|---:|
| category recovery only | 10,631 | 10,607 |
| start recovery only | 9,780 | 9,773 |
| individual recovery only | 9,364 | 9,355 |
| individual + start | 2,996 | 2,992 |
| category + start | 1,218 | 1,217 |
| all three durations | 1,209 | 1,208 |
| individual + category | 1,097 | 1,097 |
| no duration and no aura lock | 32 | 28 |
| aura lock only | 4 | 4 |

All three duration fields are nonnegative milliseconds in the current CSV. Ranges are
`RecoveryTime` 0..999,998,976, `CategoryRecoveryTime` 0..604,800,000, and `StartRecoveryTime`
0..2,000,000. The near-billion individual timers belong to two `Molt` spells; week-long values
include `Ogre Brewing Kit`, `Solving Puzzle`, and `Jubling Cooldown`. These are real populated
outliers, not grounds for choosing a narrower integer type or imposing a combat-scale cap.

The 32 all-zero-duration rows are not all equivalent. Four carry an aura lock:

| Spell | Locking aura |
|---:|---:|
| 336135 `Adaptation` | 336139 `Adapted` |
| 423991 `Menu Common Q` | 417863 `Chest Active` |
| 1259915 `Killing Spree` | 1259915 `Killing Spree` |
| 1292543 `Unchaining` | 1287647 `Unchaining` |

Every nonzero `AuraSpellID` resolves to `SpellName.ID`. The other 28 rows have no authored timer or
aura lock; six of those recur as completely empty rows in other singleton spell tables and look
like retained/test coordinates. Their presence must not be converted into an invented cooldown.

### Category links and definitions

`SpellCategories` has 93,201 unique `(SpellID,DifficultyID)` rows for 93,116 spells. Sixty-six
spells have multiple rows (maximum three), 93,093 rows are base, and 108 are nonbase. Its populated
link census is:

| Link role | Nonzero rows | Distinct `SpellCategory.ID` values |
|---|---:|---:|
| `Category` | 17,525 | 552 |
| `StartRecoveryCategory` | 19,637 | 50 |
| `ChargeCategory` | 904 | 304 |

All nonzero references join `SpellCategory.ID`; there are no orphans. The union is 820 category
definitions, leaving 289 of the 1,109 definitions unreferenced by these three roles in current
spell rows. IDs 133 (`Global`) and 1152 (`Creature Special 1`) demonstrate why role is part of the
identity: each appears in category, start-recovery, and charge positions, but that does not collapse
the positions into a single operation. Category 133 appears on 18,494 spells across the three roles;
1152 appears on 10,024.

The definition population is heterogeneous:

| `MaxCharges` | `ChargeRecoveryTime` | `UsesPerWeek` | Definitions |
|---|---|---|---:|
| zero | zero | zero | 694 |
| nonzero | nonzero | zero | 372 |
| zero | nonzero | zero | 26 |
| nonzero | zero | zero | 11 |
| zero | zero | nonzero | 4 |
| nonzero | nonzero | nonzero | 1 |
| nonzero | zero | nonzero | 1 |

`MaxCharges` ranges 0..40 and `ChargeRecoveryTime` 0..1,410,065,407 ms. The largest values are
current Prop Hunt categories (1,410,065,407 and 1,215,752,191 ms), followed by two Tortollan
categories at 99,999,999 ms and profession categories at one day. They survive a complete-name
outlier pass and are not integer decoding errors. `UsesPerWeek` is populated on only six definitions
(values 1, 3, 5, and 7).

Of the 904 spell rows with `ChargeCategory`, 376 point to a definition with base `MaxCharges = 0`,
364 point to one where both maximum and recovery are zero, and 12 point to positive recovery with a
zero maximum. This is not necessarily malformed: Trinity explicitly allows max charges to be added
by an aura and comments on untalented charge spells. It does mean `ChargeCategory != 0` alone does
not prove that a spell currently has usable charges.

### Difficulty topology

Cooldown and category coordinates are independently optional. Only 29,284 row coordinates join
exactly; 7,047 cooldown rows lack an exact category row and 63,917 category rows lack an exact
cooldown row. Trinity fills each singleton component independently along the difficulty fallback
graph, so these anti-join numbers are not missing-data errors. Of the nonbase cooldown rows, 59 can
inherit a base category row; 20 nonbase category rows can inherit a base cooldown row.

After applying the direct exact-or-base portion of that fallback, 623 nonzero
`CategoryRecoveryTime` rows still have category zero. Trinity gives those spells a per-spell timer
ending at the category duration; no shared category key is created. Conversely, a nonzero category
without a category duration can still classify the spell and be consumed by category modifiers or
other logic. This rejects a mandatory one-to-one category-duration model.

## Executable semantics

**Strongly verified — source assembly.** `SpellInfo.cpp:1424-1449` copies
`Category`, `StartRecoveryCategory`, and `ChargeCategory` from `SpellCategories`, but the three
durations and `AuraSpellID` from `SpellCooldowns`. `SpellMgr.cpp:2588-2600` and the component
fallback loop preserve the independent difficulty coordinates. A durable model needs selected
source difficulty for each component rather than a fused row.

**Strongly verified — individual and shared timers.** `SpellHistory::GetCooldownDurations`
(`SpellHistory.cpp:1105-1141`) loads `RecoveryTime`, `Category`, and `CategoryRecoveryTime` (or
item-effect overrides). `StartCooldown` stores a spell end and a separate category end; readiness
first checks the spell ID and then the category map (`SpellHistory.cpp:386-525, 678-700`). An
individual timer blocks that spell. A nonzero category key allows one spell's category timer to
block every spell resolving to that same key.

When individual recovery is zero, the category end becomes the spell end as well. Therefore
`CategoryRecoveryTime` is not semantically dead when `Category = 0`: it acts as the only per-spell
duration for 623 current rows. `SpellInfo::GetRecoveryTime` separately returns the maximum of the
two durations (`SpellInfo.cpp:4018-4021`), but this helper is not the storage algorithm.

**Partially characterized — 37 reversed duration rows.** Recovery is greater than category on
1,778 rows, equal on 491, but category is greater on 37. All 37 have a nonzero effective category;
examples include spell 27619 `Ice Block` (20,000 vs 300,000 ms) and 216805 `Potion of Trivial
Invisibility` (60,000 vs 300,000). Current Trinity stores those ends separately, yet its periodic
cleanup erases the category entry when the shorter spell entry ends
(`SpellHistory.cpp:226-236,1084-1088`). Thus the implementation does not independently sustain the
longer category end in this shape. Available evidence cannot establish whether those 37 rows encode
a client-only/category expectation, stale data, or a Trinity defect. A contract claiming that the
larger authored category duration always wins is rejected.

**Strongly verified — start/global cooldown.** `Spell::TriggerGlobalCooldown`
(`Spell.cpp:9177-9230`) requires both nonzero `StartRecoveryTime` and
`StartRecoveryCategory`, and `SpellHistory` keys its global-cooldown map by the latter. Durations in
750..1,500 ms can receive spell/haste adjustments; authored values outside that interval are still
used but bypass that adjustment block. The dominant pair is 1,500 ms/category 133 (11,347 exact
rows), followed by 1,000/133 (2,323). Calling every start recovery value “1.5-second GCD” is too
narrow.

**Strongly verified — aura lock.** `SpellHistory::HasCooldown` checks
`CooldownAuraSpellId` independently of stored timers and returns blocked while the owner has the
aura (`SpellHistory.cpp:678-700`). It does not turn the aura ID into a duration or category.

**Strongly verified — charge queue.** A successful cooldown handling pass calls
`ConsumeCharge(ChargeCategoryId)` before normal cooldown handling. `HasCharge` admits a cast while
consumed entries are fewer than the effective maximum. Consumption appends a recharge end after
the prior queued recharge, so charges refill serially, oldest first
(`SpellHistory.cpp:245-274,827-960`). Base `MaxCharges` is additively modified by
`SPELL_AURA_MOD_MAX_CHARGES`; base recovery receives additive, multiplicative, haste, time-rate,
category-ID, and type-mask adjustments (`SpellHistory.cpp:964-1009`). A charge reference with
effective maximum or recovery at most zero is treated as not using charges.

**Strongly verified — `TypeMask` is a selector mask, not a power of two identity.** Current values
use bits 0, 2, 3, 5, and 6, including composites. Trinity intersects the definition mask with aura
misc masks for charge-recovery modifiers. No source evidence assigns an independent public meaning
to each bit, so their individual labels remain **unknown after exhaustive available evidence**.

## `SpellCategory.Flags`

Current flags use masks 2, 4, 8, 16, 32, 64, and 128; bit 0 is unused. Complete per-bit populations
are 2, 6, 77, 48, 44, 2, and 33 definitions respectively.

| Mask | Trinity identity | Disposition |
|---:|---|---|
| `0x04` | cooldown event on leaving combat | **Strongly verified**: delays/starts the category cooldown at combat exit |
| `0x08` | cooldown in days | **Strongly verified**: category end is replaced with next daily reset; also excluded from short time-rate scaling |
| `0x20` | reset cooldown on encounter end | **Strongly verified** in unit encounter cleanup |
| `0x40` | ignore for mod-time-rate | **Strongly verified** for charge recovery |
| `0x01` | cooldown modifies item | **Unknown for current data**: NYI and currently unset |
| `0x02` | cooldown is global | **Partially characterized**: named but NYI; start-recovery fields implement the generic server GCD instead |
| `0x10` | reset charges on encounter end | **Partially characterized**: named and populated but marked NYI; no generic consumer found |
| `0x80` | unknown | **Unknown after exhaustive available evidence**: populated on 33 definitions and NYI |

`UsesPerWeek` has no reference outside DB2 loading/storage in current TrinityCore and no SimC
runtime consumer. Even suggestive names such as `Vantus Rune` do not establish whether the client,
account service, profession system, or some other owner enforces it. Its generic operation is
**unknown after exhaustive available evidence**.

## SimulationCraft boundary

SimC independently supports a useful combat slice. Its generator copies individual, start, and
category durations plus charge maximum/recovery/type/flags. In `action.cpp:710-724`, positive
charge recovery turns the action cooldown into a charge-aware cooldown; otherwise positive
individual recovery supplies the action cooldown. It models recharge multipliers and category-aware
effects.

The generated `_category` identity is deliberately collapsed: the generator takes
`ChargeCategory` when nonzero, otherwise `Category` (`generator.py:3443-3459`). It does not retain
`StartRecoveryCategory`, and generic `action_t` initialization does not use
`category_cooldown()` as a shared-lock duration. Therefore SimC is corroboration for charge
recovery and selected action timing, not evidence for the full server category topology.

## Rejected interpretations and missing evidence

**Rejected — all “category” columns are one foreign key.** The joins share a target table, but the
runtime maps and phases differ. Equality on 50 spell rows (`Category = ChargeCategory`), 324
(`Category = StartRecoveryCategory`), 22 (`ChargeCategory = StartRecoveryCategory`), and only 12
where all three are equal is correlation, not identity.

**Rejected — every `SpellCategory` defines charges.** Only 385 definitions have nonzero maximum and
399 nonzero recovery; 694 have neither. Conversely, aura modifiers can make a zero-base category
effective. Preserve the raw link and both base quantities.

**Rejected — category names define behavior.** Names such as `Global`, `Default`, `Creature
Special 1`, and content-specific labels are clustering evidence only. Executable fields, flags, and
the link role define the behavior.

**Unknown — client handling of the 37 longer category durations and giant event recharge values.**
The available data and server/simulation sources establish exact values and current implementations,
but not the authoritative client intent. Client executable traces or documentation would resolve
whether the reversed-duration rows should retain category lock after individual expiry.

## Reproduction notes

The census loaded `SpellCooldowns.csv`, `SpellCategories.csv`, `SpellCategory.csv`, and
`SpellName.csv` as DuckDB views; grouped exact source coordinates; expanded category flags and type
masks bitwise; anti-joined all three link roles; and separately applied exact-then-base component
fallback before testing missing links. Outlier queries used complete ordering, not samples.
