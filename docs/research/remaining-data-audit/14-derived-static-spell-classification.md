# Derived static spell classification

## Result and evidence

There is no single Wago “spell classification” column. Some predicates are direct attribute tests;
others are derived from resolved effects, selectors, ranges, family metadata, and server corrections.
The two highest-risk derived products are per-effect positivity and the explicit/required target
masks. They are versioned executable interpretations, not lossless facts recoverable from spell
name, effect kind, target polarity, or amount sign alone.

Evidence uses WoW `12.1.0.69497`, wowlab-data
`2ddced452a6f9076de5c86bc92f73de5b60f8556`, Core
`9bd3b8ed6f0f57587f443b857204318e7339b038`, TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimC
`b48def9c26d7532db2e612d97d433ec66bd8eede`. DuckDB 1.5.5 scanned the complete current
population. All counts below are source-row counts unless explicitly called spells; the difficulty
route in `06-difficulty-resolution.md` must be applied before constructing a requested-difficulty
classification.

## Direct and composite predicate census

`SpellMisc.csv` has 417,571 rows / 410,591 spells. Current attribute-backed predicates are:

| Predicate/input | Flattened raw | Set rows | Distinct spells |
|---|---:|---:|---:|
| passive | 6 | 17,271 | 17,271 |
| force aura debuff | 26 | 31,156 | 27,777 |
| channelled | 34 | 13,343 | 13,207 |
| self-channelled | 38 | 9,795 | 9,671 |
| no AI autocast | 49 | 7,601 | 7,601 |
| auto-repeat | 69 | 13 | 13 |
| ignore caster modifiers | 125 | 4,779 | 4,680 |
| force aura buff | 140 | 4,635 | 4,587 |
| autocast off by default | 305 | 13 | 13 |
| do not fail if no target | 431 | 207 | 205 |

The flattened raw is `word_index * 32 + bit_index`, not a word-local mask. For example passive is
word 0 bit 6/mask `0x40`, while force-buff is word 4 bit 12/mask `0x1000`.

**Strongly verified — thin aliases.** Trinity `IsPassive` tests raw 6. `IsChanneled` is the OR of
raws 34 and 38: 20,782 rows / 20,536 spells have either bit and 2,356 rows have both.
`IsAutoRepeatRangedSpell` tests raw 69. `IsAffectedBySpellMods` is the inverse of raw 125. These
names describe the exact predicates only; they do not prove application lifecycle or user-interface
classification.

**Strongly verified — autocast predicates are separate.** `IsAutocastable` rejects passive spells
and raw-49 no-AI-autocast spells, leaving 392,730 source rows; 31 rows have both exclusion inputs.
`IsAutocastEnabledByDefault` is independently the inverse of raw 305. “Can AI autocast” and “starts
enabled” must not be collapsed.

**Partially characterized — other composites.** Trinity derives ranged-weapon status from hunter
family exceptions, equipped-item subclass, and a ranged-slot attribute; attack type then composes
damage class, off-hand requirement, ranged status, and auto-repeat. Allow-dead-target composes an
attribute, authored explicit-target bits, and implicit selector object types. Group-buff scans
party/raid selector checks. Hit-delay composes missile speed and launch delay. These executable
predicates are defensible for their named server use, but they do not form mutually exclusive
universal spell categories.

## Positivity is an executable fixed-point heuristic

`SpellInfo::IsPositive` means that its derived `NegativeEffects` bitset is empty;
`IsPositiveEffect(i)` negates one bit. It is not equivalent to friendly target, positive base
points, beneficial aura, or “does no damage.” Trinity computes it after DB2 assembly and server
corrections, then applies a few later hard-coded negative-effect corrections.

The complete current source inputs demonstrate why a shortcut fails. `SpellEffect.csv` has 629,298
rows: 457 disabled effect-kind-zero rows and 628,841 executable rows. `EffectBasePointsF` is negative
on 15,771 rows, zero on 407,094, and positive on 206,433. `EffectRealPointsPerLevel` is negative on
5, zero on 628,815, and positive on 478. Only 84 rows / 67 spells set exact-effect `IsHarmful`.
Sixteen enemy-check selector identities occur on 104,553 rows / 63,398 spells (104,544 executable
rows). No one input covers the others.

**Strongly verified — evaluation order and composition.** Trinity's recursive implementation:

1. treats disabled effects as positive and preserves any pre-seeded negative bit;
2. treats passive spells as positive, then applies force-debuff before force-buff;
3. checks exact-effect harmful metadata, spell-ID/family/mechanic hardcodes, sibling heal/kill/aura
   effects, effect kind, target enemy checks, amount and per-level signs, aura subtype, dispel or
   mechanic selector, and spellmod operation;
4. recursively inspects triggered spells with a visited `(spell,effect-index)` set; and
5. makes a second pass that can copy negative status to selected aura families sharing the same
   target pair with a later negative effect.

The ordering is observable. Force-debuff and force-buff coexist on 51 current rows; two are passive.
Passive wins first for those two, while force-debuff wins over force-buff for the other 49. Any
unordered “flags say buff/debuff” reduction gets this population wrong.

Trigger recursion is material, not theoretical. There are 99,523 nonzero trigger rows / 78,638
owner spells and 95,568 distinct directed spell edges. Ninety-one rows self-trigger, representing
80 unique self-loop spells. A full graph pass finds 975 multi-spell strongly connected components,
containing 2,432 spells, with maximum size 12. The visited set is therefore part of the semantic
contract. Also, 3,734 trigger references / 1,657 trigger IDs have no current owning row in
`SpellEffect`; absence cannot be silently classified as positive or negative without defining the
missing-record policy.

**Rejected interpretation — amount sign determines positivity.** Damage-taken and cost/time
modifiers invert sign meaning; attack-speed families also consult target polarity; unconditional
damage/leech/control effects remain negative independently of positive points; healing/energize
families can be positive. The five negative per-level rows and hundreds of thousands of zero base
points make sign-only inference especially incomplete.

**Rejected interpretation — hostile selector means the whole spell is negative.** Target checks
are consulted only in particular branches. Passive precedes them, force metadata can precede them,
sibling effects can decide the result, and each effect receives its own bit. Whole-spell positivity
is only the final `none()` query over those bits.

**Partially characterized — generic portability.** The main positivity rewrite dates to Trinity
commit `1e1415a49128d034c8d48aa8cbb5d157200371b0` (2018), but current lines include changes from
many later commits and explicit spell/family exceptions. This proves current Trinity behavior, not
a client protocol contract. SimC actions carry a mutable `harmful` property and class modules often
override it; no independent generic implementation of Trinity's recursive positivity algorithm was
found.

**Unknown after exhaustive available evidence — authoritative client positivity.** Wago supplies
the inputs but not the derived result. Current local repositories cannot prove that Trinity's
heuristic exactly matches every client subsystem or current server build. Missing evidence is the
client/server implementation that authors aura polarity, combat-log disposition, and assist/attack
admission for this build. A future adapter should preserve inputs and provenance rather than store
an unexplained boolean as source truth.

## Explicit and required target masks are derived separately

The source population is 629,298 effect rows with two implicit selectors; both are zero on 23,225
rows. `SpellTargetRestrictions.csv` has 48,821 `(SpellID,DifficultyID)` rows / 48,258 spells and 25
distinct authored `Targets` masks; 32,475 rows are nonzero. Exact-effect
`DontFailSpellOnTargetingFailure` occurs on 53 rows / 42 spells, while spell attribute raw 431
occurs on 207 rows / 205 spells. These are independent contributors.

**Strongly verified — construction algorithm.** Trinity derives two masks after resolved effects
and corrections:

- Each target selector contributes a required source/destination or relationship-specific explicit
  object flag according to its object, reference, and check semantics. Source/destination-set state
  is shared across effect iteration.
- For effect kinds whose operation requires an explicit object, it adds the missing object type not
  already supplied by the selectors. If both positive and negative maximum cast ranges are zero,
  unit/game-object/corpse/destination additions from this step are removed.
- Every contribution enters `ExplicitTargetMask`. It enters `RequiredExplicitTargetMask` only when
  that effect does not set exact-effect `DontFailSpellOnTargetingFailure`.
- The authored `SpellTargetRestrictions.Targets` mask is ORed into the explicit mask. It enters the
  required mask unless raw 431 is set.

`CheckExplicitTarget` consumes the required mask for cast admission and relationship validation.
Thus “accepted payload kind” and “failure when absent” are different classifications. The explicit
mask is also not identical to the authored `Targets` column, nor to the union of target selector
object kinds.

**Rejected interpretation — selector zero means no target requirement.** The other selector, the
effect kind's used object type, and the authored target-restriction mask can still require a target.
Conversely, an implicit selector can provide the object internally and eliminate an otherwise
explicit requirement.

**Rejected interpretation — target mask can be computed before difficulty/effect correction.**
Effects resolve independently by index, range and target restrictions resolve as separate atomic
components, and Trinity corrections can replace target selectors. The constructed mask is valid
only for the resolved, corrected spell record and must record requested/source difficulty.

**Unknown after exhaustive available evidence — Wago-only exact output census.** Neither explicit
mask is stored in the CSV. Reproducing Trinity's exact outputs also requires its versioned
effect-kind target-object table and server correction database/code, including server-only spells.
The complete input census above is reproducible; presenting a CSV-only inferred mask as authoritative
would be false precision.

## Core boundary and reproduction

Core currently preserves school, defense family, open spell family, effects, and a dense implicit-
target semantic catalog, but its `SpellDefinition` does not own positivity, explicit/required target
masks, passive/channel/autocast categories, family masks, or labels. Future construction should
separate direct source predicates from derived facts, version the derivation, retain per-effect
negative provenance, and distinguish explicit from required target bits.

Counts used DuckDB `read_csv_auto`, complete conditional aggregates, both-selector unions, and exact
attribute word/bit tests. Trigger cycles were independently computed with `uv run --with duckdb`
from the complete distinct trigger edge set using Tarjan strongly connected components. Source
provenance is Trinity `src/server/game/Spells/SpellInfo.cpp` (`_InitializeSpellPositivity`,
`_InitializeExplicitTargetMask`, direct predicates), `SpellMgr.cpp` (resolution, corrections, and
initialization order), and `Spell.cpp` (target use); SimC `engine/dbc/spell_data.cpp` and
`engine/action`; Core `crates/data/src/spell.rs` and `crates/dbc/src/implicit_target.rs`.
