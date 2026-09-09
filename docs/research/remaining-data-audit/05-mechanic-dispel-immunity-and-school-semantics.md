# Mechanic, dispel, immunity, and school semantics

## Scope and evidence

This report audits the complete current Wago population for `SpellCategories.Mechanic`,
`SpellEffect.EffectMechanic`, `SpellCategories.DispelType`,
`SpellCategories.{DefenseType,DiminishType,PreventionType}`, `SpellMisc.SchoolMask`, and the
immunity-bearing effect/aura families. It is a research result, not a proposal to broaden Core's
runtime contract.

The data snapshot is WoW `12.1.0.69497`, repository commit
`2ddced452a6f9076de5c86bc92f73de5b60f8556`. DuckDB 1.5.5 scanned every source row. Important
counts were independently repeated with grouped queries over the same CSVs. Comparison sources
were Core `9bd3b8ed6f0f57587f443b857204318e7339b038`, TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimulationCraft
`b48def9c26d7532db2e612d97d433ec66bd8eede`.

## Census

`SpellCategories.csv` has 93,201 rows / 93,116 spells. Its spell mechanic is zero on 83,444 rows
and nonzero on 9,757 rows / 9,738 spells. The populated nonzero identities are every raw in 1..36
except 8. `SpellEffect.csv` has 629,298 rows / 413,805 spells. Its effect mechanic is zero on
619,459 rows and nonzero on 9,839 rows / 7,247 spells; six nonzero-mechanic rows have effect kind
zero, leaving 9,833 executable-effect rows / 7,241 spells. The populated nonzero identities are
every raw in 1..36 except 19. Their union is exactly 1..36. Every nonzero reference joins
`SpellMechanic.ID`; there are no orphan values.

At `(SpellID,DifficultyID)` granularity, 9,757 coordinates have a nonzero category mechanic and
9,126 have at least one nonzero mechanic on an executable effect. Both occur on 1,710 coordinates;
103 of those have no effect mechanic equal to the category mechanic, and 173 coordinates have more
than one distinct effect mechanic. These are not redundant columns.

The lookup names are useful identity evidence, not complete behavior: raw 1..36 are respectively
the familiar charm, disorient, disarm, distract, fear, knockback/grip, root, slow attack, silence,
sleep, snare, stun, freeze, incapacitate/knockout, bleed, healing/bandage, polymorph, banish,
shield, shackle, mount, infected, turn, horror, invulnerability, interrupt, daze, discovery,
invulnerability/immune shield, sap, enrage, wound, three further infected slots, and taunt family.
The exact Wago labels are authoritative only as labels in `SpellMechanic.csv`.

`SpellCategories.DispelType` has these complete counts:

| Raw | Wago identity | Rows | Distinct spells |
|---:|---|---:|---:|
| 0 | none | 78,693 | 78,653 |
| 1 | magic | 9,824 | 9,820 |
| 2 | curse | 845 | 844 |
| 3 | disease | 666 | 666 |
| 4 | poison | 1,239 | 1,235 |
| 5 | stealth | 250 | 250 |
| 6 | invisibility | 58 | 57 |
| 8 | special/NPC only | 20 | 20 |
| 9 | enrage | 720 | 717 |
| 11 | bleed | 886 | 880 |

Raws 7 (`All(M+C+D+P)`) and 10 (`ZG Trinkets`) exist in the 12-row lookup but are unused as an
owning spell's `DispelType` in this build. Every current value joins the lookup. The lookup's `Mask`
is exactly `1 << raw` except raw 0 maps to zero and raw 7 maps to 30, the union of magic, curse,
disease, and poison. Its `ImmunityPossible` is true for 1, 2, 3, 4, 9, and 11.

The adjacent categorical/mask fields balance as follows:

- `DefenseType`: raw 0/1/2/3 = 27,539 / 50,242 / 11,844 / 3,576 rows. Core's enum and Trinity's
  hit-resolution use establish none, magic, melee, and ranged attack-resolution families.
- `PreventionType`: all masks 0..7 occur, with 58,271 / 21,312 / 7,840 / 2,289 / 1,380 / 549 /
  1,126 / 434 rows. Trinity checks bits 1, 2, and 4 independently for silence, pacify, and
  no-actions, proving this is a composable mask rather than an eight-value enum.
- `DiminishType`: 0 appears 91,599 times; nonzero values are 1, 2, 4, 8, 12, 16, 20, 32, 48, 64,
  80, 128, 192, and 256 across 1,602 rows. Neither Trinity's current spell construction nor SimC has
  an executable consumer of this DB2 field; Trinity derives diminishing groups separately. The
  powers and combinations suggest bits, but their identities are not established.

`SpellMisc.SchoolMask` has 417,571 rows / 410,591 spells and 53 distinct masks. All values are
subsets of `0x7f`; no row sets a bit outside the seven physical/holy/fire/nature/frost/shadow/arcane
bits. Raw zero occurs on 2,628 rows / 2,624 spells. Frequent masks are physical `1` (317,638 rows),
shadow `32` (26,574), nature `8` (24,491), fire `4` (17,521), arcane `64` (11,677), frost `16`
(5,898), holy `2` (5,362), all schools `127` (334), and all magic schools `126` (74). Trinity and
SimC both implement intersection over the underlying bits; a multischool payload is one payload
whose mask intersects multiple school-specific queries, not several independent damage events.
Core preserves exactly the nonzero seven-bit domain in `SpellSchoolMask`; its rejection of zero is a
simulation input invariant, not proof that zero Wago rows are malformed.

## Executable composition semantics

**Strongly verified — mechanic ownership and precedence.** Trinity
`SpellInfo::{GetAllEffectsMechanicMask,GetEffectMechanicMask,GetSpellMechanicMaskByEffectMask}` adds
the spell-level category mechanic and applicable effect mechanics to a 64-bit mask. The singular
`GetEffectMechanic` returns the effect mechanic first, falling back to the spell mechanic only when
the effect has none. Thus the category mechanic is spell-owned fallback/classification; an effect
mechanic is effect-owned and can add or override the singular answer. Core's
`resolve_spell_mechanic_masks` independently selects the category coordinate and each effect-index
coordinate along the difficulty route before building the union. The 103 disjoint current
coordinates falsify any claim that the fields are interchangeable.

**Strongly verified — dispel classification versus dispel operation.** The owning aura's
`SpellCategories.DispelType` supplies `SpellInfo::GetDispelMask`. Effect kind 38 (`Dispel`) has 398
rows / 261 spells and uses its own `misc0` as the class of auras eligible for random dispel attempts;
effect kind 108 (`Dispel Mechanic`) has 368 rows / 177 spells and uses `misc0` as a mechanic identity,
removing every owned aura whose complete mechanic mask contains that identity. Effect kind 126 has
seven rows and is spellsteal, a distinct operation constrained to beneficial auras. Classification,
selection operation, mechanic removal, and spellsteal must remain separate contracts.

**Partially characterized — negative dispel selectors.** Fourteen effect-38 rows use `misc0 = -1`,
including Mass Dispel, Wisp Detonate, Sear Magic, and Reinforcing Ward families; twelve aura-41 rows
also use -1. Trinity's generic paths cast or shift these values and do not explain the negative
variant. It plausibly denotes a broad selection mode, but neither spell names nor that broken generic
fit proves its extent. The terminal result is an opaque negative mode pending client behavior or an
authoritative enum; it must not be normalized to raw 7 or raw 0.

**Rejected interpretation — dispel raw 11 is unused.** Trinity's enum calls raw 11
`DESPEL_OLD_UNUSED`, a label introduced in commit `f70a5817e1c` while sizing creature-immunity
bitsets. Current Wago calls it `Bleed`, marks it immunity-capable, and assigns it to 886 rows / 880
spells, including Garrote, Rend, Rip, Rake, and Rupture families. The historical Trinity label is
stale for this build. This establishes the class identity; it does not establish which player
abilities can remove bleeds.

**Strongly verified — immunity channels are typed separately.** Trinity builds per-effect immunity
information for effect kind, aura state/subtype, school application, damage school, dispel class,
and mechanic. Aura raws 37, 38, 39, 40, 41, 77, 147, and 267 have respectively 364, 112, 616, 933,
75, 1,655, 606, and 17 current rows. In the generic paths, misc0 is respectively an effect kind, aura
subtype, school mask, damage-school mask, dispel raw (shifted to its bit), mechanic raw, composite
immunity-record ID, and harmful-aura school mask. Application registers the typed immunity; removal
unregisters it. Attribute-controlled purge behavior is an additional action and must not be implied
by the word “immunity.” School immunity and damage immunity are also not synonyms: the former gates
spell/effect admission while the latter gates damage of matching schools in dedicated checks.

**Partially characterized — exceptional immunity payloads.** Complete populations falsify a
blanket “misc0 is always the obvious enum” rule. Aura 39 has values as high as 108,415, aura 40 as
high as 109,695, and aura 77 has four rows above mechanic 36 (144, 1,825, and 2,078). Trinity contains
spell-ID exceptions for several mechanic-immunity spells and otherwise shifts a single mechanic raw;
that generic code does not explain these current high payloads. They may be packed modes/masks or
new encodings, but current local evidence does not prove which. They require client executable
tracing or authoritative field documentation before admission.

**Rejected interpretation — aura 147 misc0 universally joins current `CreatureImmunities`.** Only
2 of 606 aura-147 rows join the five-row Wago table (both use ID 1921); two are zero and 602 are
nonzero misses. Trinity resolves this aura through its server `creature_immunities` store, not by
loading this sparse client table directly. The numeric field is demonstrably an external composite
immunity-record namespace, but the current Wago table is not a complete foreign-key target for the
population.

**Partially characterized — current client `CreatureImmunities`.** The table has only five IDs.
School and dispel columns are zero throughout; flags are 4 or 5. Three rows carry mechanic-word
payloads (1058; 1,090,941,990; 1,100,381,350), and one enables `MechanicsAllowed` plus `StatesAllowed`
with state word 4128. Decoding set positions is mechanically possible, but the meaning of “allowed,”
flags, and the absent historical ID population is not established by current Wago plus Trinity's
different server schema. Terminal disposition: unknown after exhaustive available evidence for a
generic client-table ingestion contract.

## Core-facing conclusions and missing evidence

Core already has strong closed contracts for `DefenseType`, nonzero seven-bit school masks, open
effect mechanics 0..36, and difficulty-resolved complete mechanic masks. It does not thereby possess
generic dispel, immunity, prevention, or diminishing-return behavior. Safe future ingestion should
preserve these separations and retain source row, spell, effect index, and selected difficulty.

The remaining evidence gaps are: authoritative interpretation of high immunity misc values; a
complete source for composite immunity records referenced by aura 147; generic lifecycle/stacking
rules for simultaneous immunity sources; and an executable definition of `DiminishType`. These gaps
are terminal research outcomes, not reasons to guess.

## Reproduction notes

Counts came from `read_csv_auto` views and complete `GROUP BY`/anti-join queries. The key integrity
checks were `EffectMechanic -> SpellMechanic.ID`, `DispelType -> SpellDispelType.ID`, school
`(SchoolMask & ~127) = 0`, and the full join of category/effect mechanic sets by
`(SpellID,DifficultyID)`. Zero was counted explicitly in every field and disabled effect kind zero
was not silently treated as executable.

The principal executable comparison points were Trinity
`src/server/game/Spells/SpellInfo.cpp` (mechanic-mask, dispel-mask, and immunity construction),
`src/server/game/Spells/SpellEffects.cpp` (dispel and dispel-mechanic operations), and
`src/server/game/Spells/Auras/SpellAuraEffects.cpp` (typed immunity application/removal). Core
boundaries were checked in `crates/data-adapter/src/spell_mechanic_mask.rs` and
`crates/data/src/spell.rs`; SimC's generated spell-data declarations and consumers were searched for
independent generic behavior.
