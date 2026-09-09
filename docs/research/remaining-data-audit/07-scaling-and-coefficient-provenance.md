# Scaling and coefficient provenance

## Scope and result

This report audits every current `SpellEffect` amount/scaling field and `SpellScaling`. The snapshot is
Wago `12.1.0.69497` at `2ddced452a6f9076de5c86bc92f73de5b60f8556`; comparison sources are Core
`9bd3b8ed6f0f57587f443b857204318e7339b038`, TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimulationCraft
`b48def9c26d7532db2e612d97d433ec66bd8eede`. DuckDB 1.5.5 scanned all 629,298 effect rows.

The principal result is that there is no single interchangeable “coefficient.” `Coefficient` and
`ScalingClass` participate in a level/item-level budget path; `EffectRealPointsPerLevel` is a legacy
alternative; `EffectBonusCoefficient` and `BonusCoefficientFromAP` are later caster-stat channels;
`EffectPointsPerResource`/`ResourceCoefficient` feed resource-point adjustment; `Variance` randomizes the
scaled base; and amplitude/chain amplitude are timing or propagation multipliers, not amount coefficients.
`PvpMultiplier` and `GroupSizeBasePointsCoefficient` are populated client fields for which the inspected
generic Trinity amount path provides no behavior.

## Complete population

| Field | Nonzero rows | Negative rows | Observed range | Terminal characterization |
|---|---:|---:|---:|---|
| `EffectBasePointsF` | 222,204 | 15,771 | -1,000,000,000 .. 1,000,000,000 | authored base or fallback amount |
| `EffectRealPointsPerLevel` | 483 | 5 | -5 .. 100,000 | legacy per-level adjustment |
| `EffectPointsPerResource` | 29 | 0 | 0 .. 63,034 | per-resource-point adjustment; sparse and outlier-heavy |
| `EffectBonusCoefficient` | 17,683 | 1 | -12.25 .. 63,034 | spell-power-style bonus channel |
| `BonusCoefficientFromAP` | 1,802 | 0 | 0 .. 10,000 | attack-power bonus channel |
| `Coefficient` | 9,441 | 194 | -60 .. 136,363.640625 | scaling-budget coefficient |
| `Variance` | 41,477 | 241 | -2 .. 345,905 | fractional base variation in Trinity's scaling path |
| `ResourceCoefficient` | 0 | 0 | exactly 0 | dormant in this current corpus |
| `PvpMultiplier` | 629,113 | 5 | -15 .. 75 | populated multiplier metadata; no generic Trinity amount consumer found |
| `GroupSizeBasePointsCoefficient` | 629,172 | 1 | -1 .. 8 | populated contextual metadata; no generic Trinity amount consumer found |
| `EffectAmplitude` | 10,291 | 134 | approximately -1e17 .. +1e17 | effect-specific value multiplier, not tick period |
| `EffectChainAmplitude` | 629,215 | 0 | 0 .. 100 | chain-target multiplier; only 83 zero rows |

None of the audited floating columns contains a negative-zero row in this build. That is a census result,
not permission for a future loader to canonicalize IEEE-754 `-0.0`: the raw CSV representation and parsed
bit pattern should remain available when a future diff first observes it.

`ScalingClass` is zero on 619,207 rows. Its nonzero/special distribution is: -1 on 3,290 rows, -2 on 828,
-3 on 297, -4 on 441, -5 on 40, -6 on 55, -7 on 1,986, -8 on 1,548, -9 on 1,581, and -10 on 25. There
are 9,419 rows with both a nonzero class and coefficient, 22 with coefficient but no class, and 672 with a
class but zero coefficient. `SpellScaling.csv` has 8,707 unique spell rows; minimum/maximum scaling levels
range from 0 through 235, and no row has an inverted nonzero interval.

Cross-field combinations matter. There are 40,941 rows with nonzero `Variance` but no active
class/coefficient pair, 579 with both an authored base and scaling coefficient, one with both a scaling
coefficient and legacy real-points-per-level, and 54 with both SP and AP coefficients. These populations
falsify a loader that selects one field merely by a fixed priority without preserving the rest.

## Executable amount path

**Strongly verified — budget scaling is contextual and precedes later modifiers.** Trinity's
`SpellEffectInfo::CalcBaseValue` enters the `Coefficient` branch when it is nonzero. It chooses a level from
spell, caster, target, and attributes; clamps it with `SpellScaling`; requires a nonzero `ScalingClass`; and
uses either class scaling or item-level random-property budgets. For item-level-scaled spells, -8 and -9
select direct damage-replacement and secondary-stat budgets, respectively; every other class starts from
a generic rare/chest random-property budget. After that selection, -7 applies a combat-rating
inventory-type adjustment and -6 applies a stamina inventory-type adjustment. It multiplies that budget
by `Coefficient`, forces a positive
sub-one result to one, and rounds unless the spell has the float-scaling attribute. Thus a coefficient-only
row with `ScalingClass = 0` currently produces zero in this generic path; this is an important 22-row
outlier, not evidence the rows are corrupt.

**Strongly verified — legacy level scaling is an alternative branch.** If no scaling coefficient is
active, `EffectBasePointsF` (or an expected-stat result for the relevant effect) is the starting value and
`EffectRealPointsPerLevel` contributes only in that legacy branch. Calling both fields generic additive
coefficients is rejected.

**Strongly verified — variance consumes RNG.** In `CalcValue`, Trinity draws uniformly from
`[-abs(Variance)/2, +abs(Variance)/2]` and adds the drawn fraction times the base. This draw occurs before
combo/resource points, mastery, and caster effect modifiers. The amount operation is therefore not pure
when variance is active, and moving this draw across another random operation can change deterministic
simulation streams. A negative source variance does not reverse the range because the executable path
takes its absolute value.

**Strongly verified — resource and caster-stat channels are distinct.** In the coefficient path Trinity
derives points-per-resource from `ResourceCoefficient`; otherwise it uses
`EffectPointsPerResource`. Combo/resource points, mastery, and effect modifiers are then applied by
`CalcValue`. The SP and AP fields are stored separately and consumed by damage/healing bonus paths rather
than being folded into the base budget coefficient. The current all-zero `ResourceCoefficient` population
means its branch cannot be empirically falsified from this build alone.

**Strongly verified — rounding is operation-sensitive.** Trinity rounds the final `CalcValue` only for an
enumerated set of direct damage, heal, power, and amount-bearing aura operations; other effects retain a
floating result until their consumer decides. `CalcBaseValue` also has its own scaling rounding rule. A
compiler cannot safely round every source field on ingestion.

**Strongly verified — amplitude fields have different owners.** `CalcValueMultiplier` starts from
`EffectAmplitude` and applies spell modifiers; chain damage uses `EffectChainAmplitude`. Period scheduling
uses `EffectAuraPeriod`, covered in report 08. Similar English names do not establish common units.

## Comparison-source boundaries

SimulationCraft independently preserves the main coefficient, delta/variance, and special scaling-class
families, but it implements player/item scaling against its own generated budget tables and simulation
context. Its generated record labels `ResourceCoefficient` as an unknown/unused field. This corroborates
field separation while explicitly not proving server order or host semantics. Differences between the
engines are evidence that generated simulation formulas must not be treated as the client contract.

Core currently preserves only the narrower scaling facts needed by its existing contracts. The evidence
supports retaining the exact source row/difficulty, source floating values, scaling class, and selected
runtime level/item context before any derived amount is emitted. It does not support adding a universal
“scaled amount” formula disconnected from effect kind and execution context.

## Partial, rejected, and unknown conclusions

**Partially characterized — `PvpMultiplier`.** The field is nearly universally populated, has 233 distinct
values and five negative rows, but the inspected generic Trinity `SpellEffectInfo` amount path does not
copy or consume it. The name and content clustering support PvP-context multiplier metadata only; they do
not establish application phase, mode predicate, or whether one means identity in every subsystem.

**Partially characterized — group-size scaling.** `GroupSizeBasePointsCoefficient` is nonzero on all but
126 effect rows and contains a -1 outlier. No generic consumer was found in the inspected Trinity effect
amount path, and SimC does not establish a universal operation. Group-size context is plausible; additive
versus multiplicative behavior and the meaning of -1 remain unknown.

**Rejected inference — every large numeric value is a real combat coefficient.** Values such as 63,034,
100,000, and 345,905 recur in sparse fields where their apparent unit conflicts with ordinary formulas.
They may be sentinels, repurposed payloads, or extraction/schema artifacts. Complete outlier preservation
is required; magnitude and spell name are not enough to reinterpret them.

**Rejected inference — coefficient fields compose by unconditional multiplication.** Executable source
shows alternative branches, later stat channels, contextual item scaling, and effect-specific rounding.
The 22 coefficient-without-class and 40,941 variance-without-active-scaling rows materially contradict a
simple product formula.

**Unknown after exhaustive available evidence — current semantics of the anonymous
`Node__Field_12_0_0_63534_001` column.** Its generated name is not semantic evidence. It must remain raw
until authoritative DB2 layout history or executable client use identifies it.

**Unknown after exhaustive available evidence — generic client order for PvP/group-size multipliers and
all special scaling classes.** Current server and simulation implementations establish useful subsets, not
the complete client rule. A client executable trace or authoritative layout/formula documentation is the
smallest resolving evidence.

## Reproduction notes

Population results come from complete DuckDB aggregates over `SpellEffect.csv` and
`SpellScaling.csv`, including sign-bit checks, cross-field `FILTER` counts, special-class grouping, and
minimum/maximum interval validation. Reusable table/schema and scaling-row diff coverage is in
`scripts/research/wago_research.py`; run the clean current census with
`uv run scripts/research/remaining_data_audit.py facts` and future comparisons with
`uv run scripts/research/remaining_data_audit.py diff OLD/data/tables NEW/data/tables`.

Executable order was traced through Trinity
`src/server/game/Spells/SpellInfo.cpp` (`SpellEffectInfo::{CalcBaseValue,CalcValue,CalcValueMultiplier}`)
and its damage/healing consumers. SimC's `engine/dbc/sc_spell_data.*`, generated spell-data structures,
and item/player scaling consumers were independently checked.
