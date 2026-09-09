# Difficulty resolution

## Result

Difficulty is a fallback graph and component-level selection problem. It is not a filter that keeps
only `DifficultyID = 0`, and it is not a whole-spell replacement. For a requested difficulty, each
difficulty-bearing singleton component selects the first available row on
`requested -> fallback -> ... -> 0`; effects select independently per effect index, powers per order
index, and spell visuals use a separate first-nonempty-vector policy. Mixing every difficulty row
into one spell is also wrong.

This conclusion is **strongly verified** by the current Wago graph and TrinityCore's executable
`SpellMgr::LoadSpellInfoStore`/`GetSpellInfo` behavior. Core's `DifficultyRoute`,
`resolve_spell_misc`, and `resolve_spell_mechanic_masks` implement the same conservative route and
the two surfaces they currently own. Other components remain outside that Core contract.

Evidence versions: Wago `12.1.0.69497` at `2ddced452a6f9076de5c86bc92f73de5b60f8556`;
Core `9bd3b8ed6f0f57587f443b857204318e7339b038`; TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`; SimC
`b48def9c26d7532db2e612d97d433ec66bd8eede`. DuckDB 1.5.5 scanned every current row; a second
`uv run --with duckdb` schema-driven pass independently enumerated every spell table carrying
`DifficultyID`.

## Authoritative graph census

`Difficulty.csv` contains 60 unique IDs, 22 nonzero fallback edges, no duplicate IDs, no dangling
nonzero fallback, and no cycle. Difficulty zero is not a row: it is the terminal base coordinate.
The nonzero edges are:

| Difficulty | Name | Fallback |
|---:|---|---:|
| 2 | Heroic dungeon | 1 |
| 5 | 10-player heroic | 3 |
| 6 | 25-player heroic | 4 |
| 7 | Looking For Raid | 4 |
| 8 | Mythic Keystone | 23 |
| 11 | Heroic Scenario | 12 |
| 15 | Heroic raid | 14 |
| 16 | Mythic raid | 233 |
| 17 | Looking For Raid | 14 |
| 23 | Mythic dungeon | 2 |
| 24 | Timewalking dungeon | 2 |
| 33 | Timewalking raid | 14 |
| 39 | Heroic scenario | 38 |
| 40 | Mythic scenario | 39 |
| 45 | PvP scenario | 39 |
| 151 | Looking For Raid | 17 |
| 153 | Teeming Island | 39 |
| 205 | Follower dungeon | 1 |
| 216 | Quest dungeon | 1 |
| 233 | Mythic flexible raid | 15 |
| 250 | World raid | 14 |
| 257 | Timewalking raid | 14 |

The longest current routes include `16 -> 233 -> 15 -> 14 -> 0` and
`8 -> 23 -> 2 -> 1 -> 0`. This directly falsifies one-hop fallback. `151 -> 17 -> 14 -> 0` and
`45 -> 39 -> 38 -> 0` show that indirect fallback is not confined to the familiar modern raid
quartet.

## Complete spell-table population

| Table | Rows | Nonbase rows | Distinct spells | Distinct difficulties | Source-coordinate rule |
|---|---:|---:|---:|---:|---|
| SpellAuraOptions | 32,162 | 313 | 31,849 | 20 | one row per spell/difficulty |
| SpellAuraRestrictions | 10,631 | 13 | 10,629 | 6 | one row per spell/difficulty |
| SpellCategories | 93,201 | 108 | 93,116 | 17 | one row per spell/difficulty |
| SpellCooldowns | 36,331 | 72 | 36,259 | 14 | one row per spell/difficulty |
| SpellEffect | 629,298 | 30,557 | 413,805 | 36 | one row per spell/difficulty/effect-index |
| SpellInterrupts | 122,150 | 51 | 122,099 | 18 | one row per spell/difficulty |
| SpellLevels | 34,485 | 136 | 34,349 | 13 | one row per spell/difficulty |
| SpellMisc | 417,571 | 6,982 | 410,591 | 34 | one row per spell/difficulty |
| SpellTargetRestrictions | 48,821 | 580 | 48,258 | 17 | one row per spell/difficulty |
| SpellXSpellVisual | 248,513 | 468 | 234,589 | 17 | multiple visual rows are legitimate |

`SpellPowerDifficulty` is different: its 25 rows map `SpellPower.ID` to nine nonbase difficulty IDs
(2, 4, 5, 6, 7, 14, 16, 170, 171) and provide an order index; it does not contain `SpellID`.
Every difficulty used by every table above exists in the 60-node authoritative graph. There are no
unknown source difficulties in this build.

The high nonbase count in `SpellEffect` is not evidence that 30,557 spells are wholly replaced.
Effects are inherited slot by slot. Conversely, the 6,982 nonbase `SpellMisc` rows are atomic
records: attributes, cast-time/duration/range references, school, missile speed, and visual metadata
come from one selected row and must not be independently mixed with a fallback `SpellMisc` row.

## Executable behavior

The implementation history supports treating this as a cross-table assembly rule rather than a
`SpellEffect` peculiarity. Trinity commit `c7306439e7004288fb85890d6a5f730cf1761d71`
(2020-06-12, “Implement using different difficulty data from all spell related db2s, not just
SpellEffect and SpellPower”) introduced the broader per-table handling. The current executable
loader retains that design.

**Strongly verified — per-component fill.** Trinity groups all source rows by `(SpellID,Difficulty)`.
For each constructed difficulty record it follows `Difficulty.FallbackDifficultyID`. Missing
`AuraOptions`, `AuraRestrictions`, `CastingRequirements`, `Categories`, `ClassOptions`, `Cooldowns`,
`EquippedItems`, `Interrupts`, `Levels`, `Misc`, `Reagents`, `Scaling`, `Shapeshift`,
`TargetRestrictions`, and `Totems` are filled from the first fallback that supplies that component.
Existing exact rows are never merged field by field.

**Strongly verified — indexed components.** Each missing effect index inherits independently. Each
missing power order index inherits independently. Visuals are an explicit exception: they fall back
only when the exact vector is empty, and then take the first fallback vector as a whole rather than
accumulating visuals across the chain. Empty label/reagent-currency/empower vectors similarly use a
fallback vector. These behaviors make “one universal row-selection helper for every table” too
strong unless the helper is parameterized by component cardinality and inheritance policy.

**Strongly verified — lookup after construction.** Trinity's `GetSpellInfo(spell,difficulty)` first
looks for an exact constructed spell record and then walks the same fallback graph if that record is
absent. Triggered spells frequently request the cast/map difficulty, while some learning and global
metadata paths explicitly request base difficulty. Difficulty is therefore contextual call data,
not an intrinsic single value of a spell ID.

**Strongly verified — Core's current boundary.** `DifficultyRoute::try_new` validates the complete
graph, rejects a base edge, duplicates, dangling targets, cycles, and unknown nonbase requests, and
stores the exact route including terminal zero. `resolve_spell_misc` chooses one atomic row per
required spell. `resolve_spell_mechanic_masks` selects one category mechanic plus one row per effect
index before unioning mechanics. Both reject duplicate source coordinates and source difficulties
outside the authoritative graph. This is a sound fail-closed subset of the broader DB2 assembly.

## Outliers and falsification

`SpellMisc` has 3,349 spells with multiple rows and 6,980 rows beyond one per spell. Of those, 236
spells have two or three distinct 17-word attribute signatures and 18 have both an all-zero and a
nonzero signature. Two spells have no base row at all: spell 173760 has only difficulty 8, while
spell 1312344 has difficulties 15 and 17. Selecting base rows globally would drop both and silently
misclassify the 18 mixed-signature spells.

For the earlier aura/attribute census, all difficulties and base-only happened to expose the same
521 nonzero aura identities and 489 set attribute identities. That coincidence does not validate a
base-only loader: nonzero-aura owning spells are 189,194 across all rows versus 189,140 at base, and
attribute-owning spells are 383,044 versus 383,032. Identity coverage and correct spell resolution
are different questions.

**Rejected interpretation — difficulty rows are additive variants.** Loading all rows at once would
combine mutually exclusive raid/dungeon payloads and duplicate component coordinates. The current
data's differing signatures and Trinity's first-match fill semantics directly reject it.

**Rejected interpretation — nonbase row replaces the complete spell.** The 30,557 nonbase effect
rows coexist with independently optional categories, cooldowns, target restrictions, misc, and
other records. Trinity's per-component and per-index fallback is executable counterevidence.

**Rejected interpretation — fallback is “requested, then base.”** Current routes of length four and
five disprove it. In particular, mythic raid 16 can inherit from 233, 15, or 14 before base.

**Partially characterized — SimC selection.** SimC's generated spell model consumes resolved child
records for ranges, cooldowns, categories, target restrictions, aura options, levels, and effects,
but no generic current runtime fallback consumer was found in the inspected generator/runtime
surface. Its generated data is evidence for the selected simulation slice, not independent proof of
the full fallback contract.

## Missing evidence and durable requirements

**Unknown after exhaustive available evidence — universal client parity and future-table policy.**
The available sources do not establish whether every client subsystem applies precisely the server
fallback rules, nor do they establish one generic inheritance policy for future DB2 tables. Those
questions need client executable evidence or authoritative format documentation.

Any later Core expansion should retain: requested difficulty; selected source difficulty for each
component/index; source row ID; the validated complete graph; and a typed inheritance policy. A
missing permitted row should remain explicit. A result assembled from fallback rows should not be
misreported as an authored row at the requested difficulty.

## Reproduction notes

The census discovered tables from CSV headers rather than a hard-coded initial list, grouped by the
appropriate source coordinate, anti-joined every nonzero `DifficultyID` against `Difficulty.ID`, and
validated graph degree and targets. The graph audit treated zero as an implicit terminal, not a
missing `Difficulty` record.

The principal executable comparison was Trinity
`src/server/game/Spells/SpellMgr.cpp` (`LoadSpellInfoStore` and `GetSpellInfo`), with DB2 field
provenance checked in `src/server/game/DataStores/DB2Structure.h`. Core's graph and current
component policies were traced through `crates/data-adapter/src/difficulty.rs`,
`spell_misc.rs`, and `spell_mechanic_mask.rs`. SimC's generated DBC model was searched as an
independent but deliberately simulation-scoped comparison.
