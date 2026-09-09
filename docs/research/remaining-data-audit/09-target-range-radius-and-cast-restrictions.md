# Target, range, radius, and cast restrictions

## Scope and evidence

This report audits `SpellEffect.ImplicitTarget_{0,1}` and radius indices,
`SpellTargetRestrictions`, `SpellMisc.{RangeIndex,CastingTimeIndex}`, `SpellRange`, `SpellRadius`,
`SpellCastTimes`, `SpellCastingRequirements`, `SpellAuraRestrictions`, `SpellShapeshift`, and
`SpellEquippedItems`. It separates selector identity, explicit cast payload requirements, spatial
limits, area-search geometry, target caps, and actor-state admission.

Evidence versions are Wago `12.1.0.69497` (`2ddced452a6f9076de5c86bc92f73de5b60f8556`), Core
`9bd3b8ed6f0f57587f443b857204318e7339b038`, TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimC
`b48def9c26d7532db2e612d97d433ec66bd8eede`. DuckDB 1.5.5 scanned the complete CSV population;
independent queries checked every foreign-key count and the selector union.

## Population census

### Implicit selectors

The 629,298 effect rows / 413,805 spells contain 145 distinct target-A raws, 110 target-B raws, and
147 distinct identities in their union. Values are within Core's complete dense 0..152 catalog.
The six currently unused identities are 12, 13, 19, 114, 117, and 146. Both selectors are zero on
23,225 effect rows. Across both columns there are 1,258,596 occurrences; the most frequent are zero
552,228, caster 331,259, explicit any-unit target 47,751, hostile unit target 44,056, source/caster
40,878, caster destination 31,925, existing destination 26,225, source-area enemy 24,434,
destination-area enemy 23,979, source-area entry 13,933, nearby entry 11,645, and
destination-area entry 11,474.

Core catalogs every raw 0..152 as five orthogonal properties: produced object, reference anchor,
selection algorithm, relationship check, and direction. That decomposition matches Trinity's
`SpellImplicitTargetInfo` table and executable dispatcher. It is stronger evidence than historical
enum names alone.

Ten populated raws remain reserved/not-implemented in Core or Trinity's semantic table: 10 (2
occurrences), 14 (2), 111 (2), 139 (2), 141 (119), 143 (35), 144 (43), 145 (253), 147 (165), and
152 (63). Their current presence falsifies “reserved means absent.” Their generic selection
semantics remain **unknown after exhaustive available evidence**; callers must retain the raw and
fail closed rather than map them to `None`.

History does not rescue those identities. Trinity's table has left raws 139, 141, and 143–147
not implemented since the 2015 packet-layout rewrite at commit `f45ae7af431`; raw 152 has remained
not implemented since its 2022 addition. Raw 142, by contrast, received an executable implementation
only in commit `3790c1e3da` (2023). That contrast is evidence that the neighboring unknown raws
require individual behavioral proof, not inference from adjacency.

### Target restrictions

`SpellTargetRestrictions.csv` has 48,821 unique `(SpellID,DifficultyID)` rows / 48,258 spells,
including 580 nonbase rows. `Targets` has 25 distinct masks and is nonzero on 32,475 rows. The common
masks are destination `0x40` (26,583), item `0x10` (2,687), gameobject-or-item `0x4000` (1,598),
source `0x20` (827), passenger `0x100000` (242), ally `0x100` (200), minipet `0x10000` (153), and
corpse ally `0x8000` (52); combinations also occur.

`MaxTargets` is nonzero on 10,506 rows. Common literal caps are 1 (6,566), 3 (1,152), 2 (885), 5
(711), 4 (333), 10 (250), 6 (126), and 8 (111); 59 rows carry 255. No evidence establishes 255 as a
sentinel, and Trinity treats any nonzero value as a numerical random-resize cap. `MaxTargetLevel` is
nonzero on 257 rows, but no current executable Trinity consumer was found beyond loading/storage.
`TargetCreatureType` is nonzero on 513 rows and has 74 distinct masks; Trinity intersects it with
the target's creature-type mask, with a dedicated ignore-requirement aura exception.

`ConeDegrees` is nonzero on 5,493 rows and `Width` on 1,360. Positive and negative cone values both
occur; common angles are 60 (2,171), 45 (484), 90 (464), 30 (447), and 40 (239), while -82 and -50
are populated outliers. Trinity explicitly interprets positive as front and negative as rear.
Width is an authored line/cone width in yards; when zero, Trinity substitutes caster combat reach.

### Range, radius, and cast time

`SpellMisc` contains 201 distinct `RangeIndex` values including zero. Zero occurs on 113 rows. Every
nonzero reference joins `SpellRange.ID`; 200 of 222 range definitions are currently referenced.
The 22 unused table IDs are 200, 273, 298, 327, 328, 445, 463, 469, 475, 483, 486, 489, 490, 507,
518, 519, 520, 525, 534, 539, 551, and 558. Range definitions contain hostile/negative and
friendly/positive min/max pairs. Ten definitions are asymmetric, including hostile 30/friendly 40,
hostile 20/friendly 30, hostile dead-zone 10..40/friendly 0..40, and charge-specific asymmetries.
Flags have values 0 (208 rows), melee 1 (8), and ranged 2 (6).

`SpellRadius.csv` has 375 unique definitions. Across the two effect columns there are 141,461
nonzero references to 341 distinct IDs and no orphan reference. All 375 current `RadiusPerLevel`
values are zero; 259 definitions have nonzero `RadiusMin`; maximum `RadiusMax` is 50,000. Only six
rows have nominal `Radius != RadiusMax`: IDs 150, 244, 400, 401, 421, and 497. These include real
min/nominal/max triples, nominal-with-zero-max outliers, and very small values; a loader must retain
all four fields rather than collapse to one scalar merely because the current per-level term is
zero.

`SpellCastTimes.csv` has 272 unique definitions, range -1,000,000..600,000 milliseconds; 62 have
`Base != Minimum` and one is negative. `SpellMisc` references 236 cast-time IDs plus zero; zero occurs
94 times and every nonzero reference joins. A negative or differing-minimum row is not safely
interpretable from the lookup alone. SimC's generated spell model intentionally reduces this
surface for simulation, so it is not evidence that the discarded fields are semantically inert.

### Cast and aura-state restrictions

`SpellCastingRequirements.csv` has 28,149 rows, exactly one per spell: 5,704 nonzero facing masks,
27 minimum-faction IDs, 110 nonzero reputation values, 17,020 required-area-group IDs, 233 required
aura-vision values, and 5,347 spell-focus references. Every nonzero focus reference joins one of 834
`SpellFocusObject` rows. Facing masks are mainly 1 (5,643), with composite values 6, 7, 8, 14, and
15 also present. Trinity generically consumes facing, required area group, and spell focus. It loads
but does not copy minimum faction, minimum reputation, or required aura vision into `SpellInfo`; no
generic executable consumer was found for those three current DB2 fields.

`SpellAuraRestrictions.csv` has 10,631 rows / 10,629 spells, 13 nonbase rows. Nonzero counts are:
caster/target required aura state 32/103; excluded caster/target state 300/35; required caster/target
spell 2,193/1,532; excluded caster/target spell 3,788/4,156; required caster/target aura subtype 0/2;
excluded caster/target subtype 2/4. Trinity checks caster conditions during cast admission and target
conditions during target validation. Trigger flags can deliberately bypass caster aura-state checks,
so these fields are not unconditional predicates on every execution path.

`SpellShapeshift` has 6,431 one-per-spell rows: 1,112 nonzero exclusion masks and 797 nonzero required
masks. `SpellEquippedItems` has 3,758 one-per-spell rows; all constrain item class, 3,551 constrain
subclass, and 1,104 constrain inventory type. These are separate actor/equipment admission domains,
not target selector properties.

## Verified semantic boundaries

**Strongly verified — selector versus explicit target mask.** An implicit selector says how an
effect obtains an object/destination and what relationship it checks. Trinity derives required
explicit payload flags from both selectors and effect requirements, then ORs in
`SpellTargetRestrictions.Targets`. Therefore the authored `Targets` field is only one contribution
to the final explicit target mask. Treating it as the complete mask is rejected.

**Strongly verified — range versus radius.** Spell range gates the cast's explicit unit,
game-object, corpse, or destination relative to the caster and selects friendly/hostile columns.
Runtime reach, movement tolerance, ranged-weapon modifiers, and spell modifiers can adjust it.
Effect radius determines the search/placement extent of an implicit area, cone, line, or random
destination and can be modified separately. A 40-yard cast range and a 10-yard effect radius are not
competing measurements.

**Strongly verified — radius formula is contextual.** Trinity starts with `RadiusMin/RadiusMax`,
limits max by nominal plus per-level when a caster exists, applies spell radius modifiers and a
movement allowance unless disabled by an attribute, and uses special random-destination handling.
The current all-zero `RadiusPerLevel` population is a build fact, not proof the field is permanently
obsolete.

**Strongly verified — cone/line/cap composition.** Radius supplies cone length or line search range;
`ConeDegrees` supplies angular aperture/direction; `Width` supplies line width; `MaxTargets` caps the
post-script candidate list when nonzero. Target scripts and conditions can further edit candidates.
None of these fields alone defines the complete selected set.

**Strongly verified — state restrictions have owner and phase.** Caster aura state/spell/subtype,
form, equipment, area, focus, and facing are evaluated on the caster at admission or strict range
validation. Target aura and creature-type restrictions are evaluated on each target. Required and
excluded variants invert the predicate. Trigger flags and explicit spell attributes can bypass
particular layers; they do not erase the metadata.

## Partial, rejected, and unknown findings

**Rejected interpretation — selector raw zero means the effect has no target.** Many effect kinds
provide or infer targets from the other selector, explicit cast payload, caster, or effect contract.
Only the pair plus effect kind and final explicit mask is meaningful.

**Rejected interpretation — zero radius means global or unlimited.** A missing radius entry yields
no area radius in Trinity; global behavior, when real, comes from another operation/selector or a
large authored range. Zero cannot be promoted to infinity.

**Rejected interpretation — `MaxTargets = 0` means select zero targets.** Trinity applies the cap
only when nonzero. Zero is uncapped by this field, while selector/conditions/other mechanics may
still yield no targets.

**Partially characterized — range polarity and flags.** Trinity's executable consumers establish
separate positive/friendly and negative/hostile pairs and special melee/ranged reach handling. The
full client presentation semantics of `SpellRange.Flags`, especially beyond the current values 0,
1, and 2, are not established. SimC stores one selected min/max pair, which is deliberately narrower.

**Unknown after exhaustive available evidence — populated reserved selectors.** Current data proves
their existence and owner/effect contexts, but the inspected Core, Trinity, SimC, and history do not
define generic algorithms for raws 10, 14, 111, 139, 141, 143, 144, 145, 147, or 152. Missing
evidence is client executable behavior or authoritative target-enum documentation for this build.

**Unknown after exhaustive available evidence — casting faction/reputation/aura-vision fields and
`MaxTargetLevel`.** Names and correlations are insufficient. Current Trinity does not execute them,
and SimC omits them. A future contract needs client behavior or another independent generic
consumer, including comparison direction and bypass rules.

## Core-facing conclusion

Core's dense implicit-target identity catalog is an appropriate preservation boundary, including
reserved raws, but the broader runtime currently stores only limited radius facts and does not own a
generic range/geometry/restriction pipeline. Future ingestion should preserve selected difficulty,
both selectors, both radius IDs, the full range and radius records, target-restriction masks and
geometry, and owner/phase provenance. Unsupported selectors or fields should produce typed failures;
they must not be coerced to caster, target, zero distance, or no restriction.

## Reproduction notes

Queries unioned both selector columns, anti-joined their dense domain, joined every nonzero range,
radius, cast-time, and focus reference to its lookup, and grouped every restriction field without
sampling. Zero references were counted separately. Difficulty-bearing restriction rows were left as
source coordinates; their correct selection is governed by the fallback contract in
`06-difficulty-resolution.md`.

The principal executable comparison points were Trinity
`src/server/game/Spells/SpellInfo.cpp` (`SpellImplicitTargetInfo`, range, and target admission) and
`src/server/game/Spells/Spell.cpp` (selector dispatch, area/cone/line selection, explicit range,
focus, and target caps). Core's catalog was traced in `crates/dbc/src/implicit_target.rs`. SimC's
`engine/dbc/spell_data.hpp`, `sc_spell_info.cpp`, and action consumers were checked to distinguish
its reduced simulation model from generic game behavior.
