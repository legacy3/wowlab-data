# Spell power and cost semantics

## Result

`SpellPower` is an ordered, difficulty-resolved list of conditional resource terms. A spell may have
up to five order slots; rows can be unconditional or gated by a caster aura, and rows of the same
power type are summed. Positive final amounts are costs, negative final amounts grant power, and
health (`PowerType = -2`) is admitted and paid through a distinct health path. Flat, percentage,
periodic, and optional-extra amounts have different bases and phases.

The main calculation, aura gate, negative generation, periodic drain, optional-spend cap, and
difficulty overlay are **strongly verified** by TrinityCore and independently supported in part by
SimulationCraft. Two deceptively named fields are not safely implemented as their Wago headings
suggest: `ManaCostPerLevel` is populated but has no current generic consumer, while
`PowerCostMaxPct` reaches only a contradictory branch in TrinityCore. They are explicitly left
partial rather than assigned invented formulas.

Evidence versions are Wago `12.1.0.69497`
(`2ddced452a6f9076de5c86bc92f73de5b60f8556`), TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimC
`b48def9c26d7532db2e612d97d433ec66bd8eede`. DuckDB 1.5.5 scanned every current row and repeated
foreign-key and coordinate counts independently.

## Complete population

`SpellPower.csv` contains 5,713 rows for 5,434 spells. Cost-row cardinality per spell is:

| Rows per spell | Spells |
|---:|---:|
| 1 | 5,250 |
| 2 | 119 |
| 3 | 39 |
| 4 | 22 |
| 5 | 4 |

Order indices are 0 (5,458 rows), 1 (170), 2 (55), 3 (26), and 4 (4). There are 15 nonunique
`(SpellID,OrderIndex)` coordinates: five contain two rows and ten contain three. Their 25 rows beyond
the one base row per coordinate are resolved by `SpellPowerDifficulty`, whose 25 rows join
`SpellPower.ID` exactly and assign one of nine nonbase difficulties. The base and difficulty rows are
alternatives in one order slot, not additive costs.
Examples include `Animate Bones` (base 3 energy, difficulties 5/6 use 2), `Seaswell` (base 100 mana,
difficulties 2/4 use 1,000), and `Ravenous Feast` (difficulty 14 has flat 100 energy while its base
row uses 100% maximum).

### Populated fields

| Field | Nonzero rows | Current range |
|---|---:|---:|
| `ManaCost` | 3,942 | -300..175,000 |
| `ManaCostPerLevel` | 128 | 0..30 |
| `ManaPerSecond` | 36 | 0..10,000 |
| `PowerCostPct` | 1,476 | -8..100 |
| `PowerCostMaxPct` | 142 | -100..100 |
| `OptionalCost` | 56 | 0..2,900,000 |
| `OptionalCostPct` | 0 | always zero |
| `PowerPctPerSecond` | 11 | 0..5 |
| `RequiredAuraSpellID` | 276 | 0..1,277,162 |
| `PowerDisplayID` | 180 | 39 IDs |
| `AltPowerBarID` | 27 | 14 IDs |

The dominant payloads are flat-only (3,722 rows) and percentage-only (1,456). There are 145 rows
with no flat, percentage, periodic, or optional amount. Sixty-eight of those carry
`PowerDisplayID`, two carry an aura gate, and one carries `AltPowerBarID`; zero numeric payload is
therefore not proof that the row is meaningless metadata or safe to discard.

All 39 nonzero `PowerDisplayID` values join `PowerDisplay.ID`, and all 14 nonzero `AltPowerBarID`
values join `UnitPowerBar.ID`. Nine of the 276 gated rows fail to resolve their absolute
`RequiredAuraSpellID` to current `SpellName`; this is retained/stale-reference evidence, not a basis
for changing the predicate. The common resolved gates are specialization/form auras such as
`Discipline Priest` (23 rows), `Holy Priest` (21), `Mistweaver Monk` (19), and `Shadow Priest`
(16).

### Power identities

There are 19 populated `PowerType` raws:

| Raw | Identity | Rows |
|---:|---|---:|
| 0 | mana | 4,024 |
| 3 | energy | 1,107 |
| 5 | runes | 103 |
| 6 | runic power | 78 |
| 2 | focus | 65 |
| 7 | soul shards | 65 |
| 1 | rage | 56 |
| 10 | alternate | 48 |
| 4 | combo points | 40 |
| 17 | fury | 31 |
| -2 | health | 26 |
| 9 | holy power | 23 |
| 12 | chi | 17 |
| 11 | maelstrom | 10 |
| 8 | lunar power | 9 |
| 19 | essence | 8 |
| 15 | demonic fury (obsolete) | 1 |
| 13 | insanity | 1 |
| 25 | alternate mount | 1 |

For nonnegative values, the correct current join is `PowerType.PowerTypeEnum`, not `PowerType.ID`.
It resolves every populated type except obsolete raw 15. Raw -2 is intentionally outside the table
and is strongly identified as `POWER_HEALTH` by Trinity's signed enum and executable health branch.
The `PowerType` table itself has current definitions for 0..13, 16..19, 23..25; its IDs are record
keys and differ from the enum raws (for example essence raw 19 is table ID 130).

## Executable calculation

**Strongly verified — conditional ordered assembly.** Trinity loads power rows by order slot and
uses `SpellPowerDifficulty` to move its 25 variant rows to the mapped difficulty
(`SpellMgr.cpp:2588-2601`). Missing slots inherit independently along the difficulty route. At cast
time, every selected row is evaluated; `RequiredAuraSpellID != 0` suppresses only that row when the
caster lacks the aura. Results with the same `PowerType` are summed
(`SpellInfo.cpp:4040-4049,4249-4284`). This is neither “choose the first row” nor “sum every CSV row.”

**Strongly verified — flat and percentage base cost.** Nonoptional evaluation begins with signed
`ManaCost`. `PowerCostPct` adds a percentage of caster maximum health for health, caster create-mana
for mana, or `PowerType.MaxBasePower` for other supported resource types
(`SpellInfo.cpp:4068-4103`). The field is a percentage number, not a 0..1 fraction. Alternate-power
raw 10 is explicitly rejected for this percent path by Trinity; that limitation must remain visible.

**Strongly verified — positive spends and negative grants.** Admission compares current power with
the calculated amount; for health it requires health strictly greater than the cost. Payment applies
`ModifyHealth(-amount)` or `ModifyPower(type,-amount)` (`Spell.cpp:7390-7424,5464-5510`). A negative
amount therefore grants power. Fifty runic-power rows and one fury row have negative `ManaCost`; two
lunar-power rows (`Star Burst`) have `PowerCostPct = -8`. Trinity's miss-discount path deliberately
skips negative grants on a miss. “All signed values are malformed costs” is rejected.

**Strongly verified — modifiers preserve authored sign domain.** Flat school/power-type aura
modifiers, percent school modifiers, order-slot spell modifiers for slots 0..2, creature-level mana
scaling, and a mana multiplier can alter the amount. Trinity records whether the authored
pre-modifier total was negative and clamps to zero if modifiers cross sign
(`SpellInfo.cpp:4140-4246`). Order indices 3 and 4 are populated, but the generic spell-mod switch
has no `PowerCost3/4`; they still calculate and aggregate without those order-specific modifiers.

**Strongly verified — optional extra spend.** Optional evaluation starts at `OptionalCost` plus
`OptionalCostPct`, receives additional-power-cost auras and percentage-only order modifiers, then
the aggregator adds at most the positive power remaining after base cost
(`SpellInfo.cpp:4105-4141,4273-4282`). This establishes `OptionalCost` as a cap on additional spend,
not a second mandatory cost. The DB2 structure comment independently says the actual spend ranges
from base to base-plus-optional and scales effect points per resource. Current
`OptionalCostPct` is entirely zero, so its coded formula is verified but its present-data behavior
is unexercised.

**Strongly verified — periodic cost belongs to an active aura.** When any selected row has
`ManaPerSecond` or positive `PowerPctPerSecond`, aura construction records it. Once per second,
the aura checks the same required-aura gate, adds the percentage of current maximum resource (or
maximum health), and deducts it. Insufficient nonhealth power removes the aura; health must remain
strictly above the payment or the aura is removed (`SpellAuras.cpp:486-493,858-902`). This is not an
up-front cast cost and does not establish behavior for non-aura spells carrying such a row.

**Strongly verified — all-power exception.** `SPELL_ATTR1_USE_ALL_MANA` overrides ordinary row
amounts: the nonoptional term becomes all current health or all current power. The historical name
is narrower than implementation; the checked branch is generic across the row's power type.

## Fields that do not support their obvious reading

**Partially characterized — `ManaCostPerLevel`.** It is nonzero on 128 current rows, always paired
with nonzero `ManaCost`. Current TrinityCore loads the field but never reads it in spell code.
SimulationCraft's current format calls the same position `unk_4` and does not export it to
`spellpower_data_t`. Repository history found structure/name migrations but no current or historical
generic spell-cost consumer in the inspected spell paths. A formula such as
`ManaCost + level * ManaCostPerLevel` is **rejected** absent client executable evidence.

**Rejected interpretation — `PowerCostMaxPct` is a verified maximum-percent cost.** The field is
nonzero on 142 rows, including health-cost abilities and two negative outliers (`Stocked House`
-100 on a mana row and `Energize` -25 on an energy row). Trinity references it only in the health branch nested under
`if (PowerCostPct)`, then tests `PowerCostPct` as fuzzy-equal to zero before using
`PowerCostMaxPct` (`SpellInfo.cpp:4073-4083`). For ordinary finite values the outer and inner
conditions cannot both hold. Blame shows this shape dates to the 2018/2021 cost rewrites; history
does not repair the contradiction. SimC exposes the field as `pct_cost_max`/`max_cost`, but that is
simulation behavior, not proof of the game's generic maximum-spend rule. The field is **partially
characterized** as a percent-like authored quantity; its authoritative runtime contract is unknown.

**Partially characterized — display selectors.** `PowerDisplayID` consistently joins
`PowerDisplay.ID` and its definitions carry an `ActualType` and display tag; `AltPowerBarID`
consistently joins `UnitPowerBar.ID`. Trinity loads both in `SpellPowerEntry` but its generic cost
calculator never reads either. Their joined tables establish display/alternate-bar context, but available
server source does not establish whether or how they change resource ownership. Preserve both raw
references; do not replace `PowerType` with `PowerDisplay.ActualType`.

**Partially characterized — zero-payload rows.** The 145 rows cluster heavily around vehicle and
encounter power displays, old death-knight resources, and test spells. Some can carry selectors or
conditional structure. The current generic calculator contributes zero from such a row, but that
does not prove the client/UI contract is a no-op.

## SimulationCraft boundary

SimulationCraft's generated `spellpower_data_t` retains power type, required aura, flat cost,
optional maximum, per-tick flat cost, and the three percent fields. Its names line up as:
Wago `ManaCost` -> SimC `cost`, `ManaPerSecond` -> `cost_per_second`, `PowerCostPct` -> `pct_cost`,
`PowerCostMaxPct` -> `pct_cost_max`, `PowerPctPerSecond` -> `pct_cost_per_second`, and Wago
`OptionalCost` -> SimC `cost_max`. This last mapping and SimC's `max_base_costs` usage independently
support optional/maximum spend; `ManaCostPerLevel` and `OptionalCostPct` remain generator unknowns.

At action construction (`action.cpp:737-778`), SimC includes a row only if its required aura is a
recognized passive specialization/talent spell, chooses flat cost when nonzero and otherwise percent
of base resource, and stores maximum and per-tick cost separately. This is simulation-relevant
corroboration, but its aura test is static talent/spec discovery rather than Trinity's runtime
`HasAura`, so it must not be promoted to the full server predicate.

## Outliers, rejected interpretations, and missing evidence

`Arcane Surge` has flat mana cost 1 and `OptionalCost = 2,900,000`, demonstrating that optional cost
can dwarf the base and must not be summed unconditionally. Twenty-six health rows include flat,
percent-current-field, and max-percent-field shapes. Raw 15's sole row is `Chaos Wave` with flat 80;
the power enum calls it obsolete Demonic Fury but current `PowerType` has no raw-15 definition.
Raw 25's sole `Surge Forward` row has zero numeric payload, an alternate power bar, and a valid
`ALTERNATE_MOUNT` identity. These outliers reject narrowing the enum to modern player resources.

**Rejected — `PowerType` is a foreign key to `PowerType.ID`.** The complete join fails systematically;
`PowerTypeEnum` is the semantic raw. The record ID is a separate table key.

**Rejected — all rows for a spell are simultaneously payable.** Difficulty variants occupy the
same order slot and are alternatives. Selection precedes aggregation.

**Rejected — a positive-looking field name guarantees consumption.** Negative flat/percent rows
are executable grants; zero-payload rows exist; required-aura rows may be absent; item casts and
game-object casts bypass cost; and `SPELL_ATTR6_DO_NOT_CONSUME_RESOURCES` suppresses payment.

**Unknown after exhaustive available evidence — authoritative `ManaCostPerLevel`,
`PowerCostMaxPct`, and display-selector contracts.** Resolving these requires current client
executable behavior or authoritative DB2 field documentation. The current names, SimC reductions,
and server gaps are insufficient to claim a universal formula.

## Reproduction notes

The census loaded `SpellPower`, `SpellPowerDifficulty`, `PowerType`, `PowerDisplay`, `UnitPowerBar`,
and `SpellName` as DuckDB views. It grouped spell/order coordinates, resolved every difficulty row
by `SpellPower.ID`, expanded every field-presence combination, anti-joined candidate keys, and
listed every negative and extreme value. Source searches covered exact field reads plus payment,
admission, aura-update, generator, and action-construction consumers; `git blame`/`git log -S`
checked the two suspicious unconsumed branches.
