# Applicability selectors

## Result and evidence

WoW has several independent applicability namespaces. This report focuses on the two current
spell-to-spell selectors not owned by the mechanic/school/category reports: the namespaced
128-bit `SpellClassMask` family system and `SpellLabel` membership. A family mask is not a global
set of 128 named properties, and a label is not a family bit with a larger number. Category,
school, mechanic, aura subtype, and exact spell-ID selection remain separate contracts.

The audited snapshot is WoW `12.1.0.69497`, wowlab-data
`2ddced452a6f9076de5c86bc92f73de5b60f8556`. DuckDB 1.5.5 scanned every CSV row. Comparison
sources were Core `9bd3b8ed6f0f57587f443b857204318e7339b038`, TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimC
`b48def9c26d7532db2e612d97d433ec66bd8eede`.

## Complete population

`SpellClassOptions.csv` has 26,406 rows and exactly 26,406 spells. It contains 27 current raw
`SpellClassSet` identities. The common sets are 9 (2,568 rows), 3 (2,353), 10 (2,200), 7 (2,177),
11 (2,015), 5 (1,989), 6 (1,983), 53 (1,873), 4 (1,859), 15 (1,778), 8 (1,640), 107
(1,478), 224 (862), and 227 (530). The remaining populated raws are 0, 1, 12, 13, 17, 50, 57,
66, 71, 78, 91, 100, and 110. Core correctly preserves this as an open compact identity through
`SpellFamilyId`, rather than assigning behavior to every observed raw.

Only 5,323 class-option rows have a nonzero owner `SpellClassMask`; 21,083 are zero. Family zero
has 477 rows, of which 429 have nonzero masks. Across all families every bit position 0..127 is set
by at least one candidate spell. There are 1,661 distinct `(family,bit)` pairs, and every raw bit
position is reused by multiple families. Therefore “bit 17 means X” without a family is an invalid
identity.

The 629,298 `SpellEffect` rows contain 8,231 nonzero `EffectSpellClassMask` selectors / 5,268 owner
spells; none is a disabled effect-kind-zero row. Their dominant operations are aura 108 percent
spell modifier (4,813 rows) and aura 107 flat spell modifier (2,513), followed by a long tail of
23 other aura identities. All 128 bit positions occur in selectors, forming 1,532 distinct
`(owner family,bit)` pairs. Sixty-nine of those pairs have no current candidate bit in
`SpellClassOptions`. A complete family-equality/intersection join leaves 62 effect selectors with
zero current matching candidate spell; examples include old set bonuses and removed Moonfire,
Mana Shield, and Holy Radiance masks. An empty current result is therefore valid data evidence, not
permission to broaden a selector.

The CSV represents each 32-bit mask word as signed. There are 289 class-option rows and 388 effect
rows with at least one negative word; `-1` is all 32 low bits, not a negative selector. One owner
row and six effect rows have all four words equal to `-1`, hence all 128 bits. Population analysis
must reinterpret each word modulo `2^32` before popcount or serialization.

`SpellLabel.csv` has 142,908 rows, 142,814 distinct `(LabelID,SpellID)` pairs, 3,465 label IDs, and
52,106 member spells. Ninety-four pairs occur twice; Trinity's `unordered_set` membership and
SimC's linked membership make those duplicates idempotent. Three memberships (spells 293012,
293014, and 293015, all label 630) lack a current `SpellName` row. Labels range from 12 through
7,114 but are sparse. There are 1,356 singleton labels; label 292 has 21,238 members, label 16 has
16,545, and label 969 has 11,557. A spell has 1..39 distinct labels (mean 2.74); 137 spells have at
least ten. Cardinality is many-to-many and carries no priority or multiplicity.

Spell-label-consuming current effects supply 2,637 candidate references to 1,312 label values
when fields are interpreted by operation: effect 212 and auras 143, 182, 307, 320, 470, 507, and
537 use `misc0`; auras 218, 219, 648, and 649 use `misc1`; aura 143 can additionally use a nonzero
second label in `misc1`. One reference is zero. Thirty-seven references / 27 nonzero labels have
no current member in `SpellLabel`. That is an empty set, not a failed foreign key: `SpellLabel` is
the association population, not an authoritative label-definition table.

## Executable contracts

**Strongly verified — generic family/mask predicate.** Trinity `SpellInfo::IsAffected` treats a
zero selector family as unconstrained. Otherwise the candidate spell's family must equal the
selector family; a nonzero selector mask must then intersect the candidate spell's owner mask.
An empty mask under a nonzero family selects the family. The selector's owner supplies the family
namespace; its own owner mask is not part of the match. Generic consumers include proc family
filters and `AuraEffect::IsAffectingSpell`.

**Strongly verified — spell modifiers deliberately use a narrower predicate and multiplicity.**
For class-mask spellmods, Trinity does not call the generic wildcard helper. Modifier-source and
candidate families must be equal, including family zero, and the return value is the popcount of
all intersecting bits across four words. Thus one cast may receive the same modifier multiple
times. Commit `dfcd41abefec371e6af9db7db92c1dd55aa701ff` (2025-03-28) introduced this after
client/tooltip verification. In the current full join, 319 selectors can match at least one
candidate on multiple bits; the maximum is 128. Label flat/percent spellmods instead return exactly
one for membership, never the number of labels or duplicate membership rows.

**Strongly verified — label membership is exact and operation-owned.** Trinity `HasLabel` is set
membership. Aura 143 modifies cooldown recovery for either of up to two labels; 182 suppresses
matching item passive auras; 307 permits movement while casting matching spells; 507 modifies
damage taken from a matching spell; 537 adds same-caster ownership to that damage predicate; and
effect 212 removes applied auras whose owning spell has the label. These operations differ in
owner, phase, and payload field. A label establishes membership only; it does not itself mean
damage, cooldown, class, positivity, or ownership.

**Partially characterized — additional label operations.** Auras 470, 648, and 649 are present in
current data but their Trinity handlers are null/not implemented. SimC names and displays them and
implements simulation-scoped subsets. Aura 320 has 12 current rows with two nonzero values in
`misc0` and zero throughout `misc1`; SimC's display path reads `misc0`, while one passive-selector
path reads `misc1`. Current population plus comparison code therefore supports the label field and
feature family, but not a trustworthy generic runtime contract for these four aura identities.

**Partially characterized — SimC union helper.** SimC's `affected_by_all` accepts class-mask,
label, or category matching. Its class-mask branch always requires family equality and boolean
intersection; it neither implements Trinity's generic family-zero wildcard nor spellmod popcount
multiplicity. This is useful simulation behavior, not a universal replacement for the operation-
specific predicates above.

## Falsification and terminal findings

**Rejected interpretation — class-mask bits are global spell properties.** All 128 positions are
reused across families, and executable checks require the family namespace. The data and source
both reject global bit names.

**Rejected interpretation — family and label selectors are interchangeable whitelists.** Of the
52,106 labeled spells, 20,809 also have `SpellClassOptions`, but only 4,767 have a nonzero family
mask. Label modifiers 218/219 have 1,980 rows, no nonzero class selector on the same row, and six
rows with label zero. The mechanisms can coexist on a spell but retain different matching and
multiplicity rules.

**Rejected interpretation — every field called label addresses `SpellLabel`.** Effect kinds 316
and 317 have 74 rows whose `misc0` values do not join current spell labels. Trinity executes them as
quest-objective “kill with label” payloads, not spell membership. Creature and game-object labels
are also separate namespaces.

**Unknown after exhaustive available evidence — semantic names for raw family bits.** Current data
and both comparison engines establish matching, but do not establish stable generic meanings for
the 1,661 observed family-bit pairs. Names inferred from a few class abilities fail on historical,
NPC, set-bonus, and all-bits masks. Missing evidence is an authoritative per-family catalog with
versioning; raw family plus four exact words is the terminal safe identity.

`SpellClassOptions.ModalNextSpell` is nonzero on 53 rows and references only four spells (mostly
Auto Shot); three rows self-reference. Trinity stores no generic consumer and calls its paired
attribute client-only. It is partially characterized as client modal chaining, not an
applicability selector.

## Core boundary and reproduction

Core's open `SpellFamilyId` is appropriate but Core does not yet preserve owner family masks,
effect selector masks, labels, or match multiplicity. A future contract must type the operation's
matching mode; a single boolean `affects` helper would erase the proven generic/spellmod difference.
It must retain signed-source words as their exact 32-bit patterns and deduplicate label membership.

Counts used DuckDB `read_csv_auto`, complete `GROUP BY`, bit expansion over `range(128)`, and a
family-equality/mask-intersection join. Important counts were repeated in separate queries. Source
provenance is Trinity `src/server/game/Spells/SpellInfo.cpp`, `SpellMgr.cpp`,
`Auras/SpellAuraEffects.cpp`, `SpellHistory.cpp`, and `Entities/Unit/Unit.cpp`; SimC
`engine/dbc/spell_data.cpp`, `sc_const_data.cpp`, and `engine/player/player.cpp`; Core
`crates/data/src/spell.rs`. No sample was used for a population conclusion.
