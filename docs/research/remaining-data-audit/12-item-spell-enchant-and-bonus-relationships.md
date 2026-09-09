# Item, spell, enchant, and bonus relationships

## Scope and result

The current item surface is a graph, not an `Item -> Spell` column. This audit follows
`Item`, `ItemSparse`, `ItemEffect`, `ItemXItemEffect`, `SpellItemEnchantment`, `GemProperties`, item bonus
lists/trees/groups, `ItemSet`/`ItemSetSpell`, `ItemBonusSequenceSpell`, and spell effects that target or
mutate items. It distinguishes immutable source edges from a concrete owned item, its selected bonuses and
enchants, an actor's equipped set, and host inventory/persistence authority.

Evidence is Wago `12.1.0.69497` at `2ddced452a6f9076de5c86bc92f73de5b60f8556`, Core
`9bd3b8ed6f0f57587f443b857204318e7339b038`, TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimC
`b48def9c26d7532db2e612d97d433ec66bd8eede`. DuckDB 1.5.5 scanned the complete tables.

## Graph census

| Relation | Rows | Distinct source objects | Integrity observation |
|---|---:|---:|---|
| `Item` | 213,349 | 213,349 IDs | base item identity |
| `ItemSparse` | 175,164 | 175,164 IDs | sparse gameplay/presentation record; not total over `Item` |
| `ItemEffect` | 61,778 | 38,567 nonzero spell identities | 59,970 of 61,556 nonzero spell references join current `Spell` |
| `ItemXItemEffect` | 60,278 | 48,163 item IDs | 60,276 effect IDs; no duplicate `(ItemID,ItemEffectID)` pair |
| `SpellItemEnchantment` | 5,342 | 5,342 IDs | up to three typed effect slots per enchantment |
| `GemProperties` | 2,409 | 2,409 IDs | 2,306 nonzero enchant references, all joining; 103 zero |
| `ItemBonus` | 20,224 | 10,086 parent bonus lists | 47 populated bonus-type identities |
| `ItemBonusTreeNode` | 19,095 | tree/list/group child edges | recursive/contextual selection graph |
| `ItemXBonusTree` | 199,940 | item-to-tree edges | static eligibility, not a selected instance list |
| `ItemSet` | 1,008 | 1,008 sets | embedded item membership slots |
| `ItemSetSpell` | 2,971 | 2,273 spell IDs | threshold/spec/trait-conditioned set edges |
| `ItemBonusSequenceSpell` | 98 | 3 nonzero spell IDs | 97 nonzero spell references, 96 joining current `Spell` |

The item-effect bipartite graph has maximum item degree five, average degree 1.2515 among linked items, and
11,881 items with multiple effects. It cannot be represented losslessly by a scalar. `ItemXItemEffect`
has 60,278 nonzero item references, of which 59,605 join the current `Item` snapshot: the 673 misses are
real snapshot/origin outliers and must not be silently dropped. Both nonzero `ItemEffect.SpellID` and
`ItemBonusSequenceSpell.SpellID` also contain current-table misses, so referential validity is explicitly
not equivalent to semantic invalidity in a build-local export.

## Item-effect origin and trigger roles

`ItemEffect.TriggerType` populations are: raw 0 = 39,678 rows, 1 = 8,855, 2 = 184, 3 = 2, 4 = 10,
5 = 1,119, 6 = 9,706, 7 = 1,127, 9 = 536, 10 = 343, and 15 = 218. Four rows in raws 0/1 have a zero
spell; all 218 raw-15 rows do. Trinity's executable enum and consumers establish 0 use, 1 equip, 2 proc,
3 summoned-by-spell, 4 death, 5 pickup, 6 learn, 7 looted, 9 forced pickup, and 10 forced looted. Raw 15
is absent from the current Trinity enum and SimC's narrower trigger enum.

**Strongly verified — source item and executing actor are separate owners.** The cross table says which
base item can carry an item-effect record. The effect record supplies spell, trigger role, charges,
cooldowns, category, specialization, and player-condition selectors. Trinity then evaluates the concrete
owned item, its owner, equipment slot, trade state, and triggering event. A spell reached through an item
edge is not thereby a player spellbook ability, and an equip edge is not a permanent aura fact.

**Strongly verified — use/equip/proc/learn are distinct event contracts.** Trinity has separate paths for
equipping, using, item combat procs, pickup/loot, and learning. SimC deliberately models primarily use,
equip, and chance-on-hit effects. Its absence of other trigger roles is a simulation-scope boundary, not
evidence those current Wago rows are inert.

**Unknown after exhaustive available evidence — item-effect trigger raw 15.** Its zero spell payload on
all 218 rows makes a spell-operation interpretation internally contradictory, and no generic executable
consumer or authoritative name was found. The raw record and item edges must be preserved. Client behavior
or current layout documentation is the smallest resolving evidence.

## Enchant and gem relationships

Across the three enchant-effect slots, type counts are: raw 0 = 9,226, 1 = 35, 2 = 41, 3 = 913,
4 = 477, 5 = 4,359, 7 = 39, 8 = 15, 9 = 352, 10 = 8, 11 = 430, 12 = 62, 13 = 11, and 14 = 58.
Raw 6 is absent. Most raw-zero slots are empty, but 186 carry a nonzero argument; this outlier rejects
discarding companion fields solely because the type is zero.

**Strongly verified — enchant slots are typed tuples.** Trinity's current enum and consumers distinguish
combat spell, direct damage, equip spell, resistance, stat, totem, use spell, prismatic socket, artifact
rank, bonus-list, and curve-related slot types. `EffectArg_n`, `EffectPointsMin_n`, and
`EffectScalingPoints_n` belong to the corresponding slot and change interpretation with `Effect_n`.
Flattening all arguments into spell IDs or stat IDs is rejected.

**Strongly verified — applying an enchant crosses ownership boundaries.** Permanent/temporary enchant
spell effects take an enchant ID from the spell effect, require a target item, and may have a caster other
than the target item's owner during trade. Trinity removes/reapplies the concrete enchant slot and uses
`SpellItemEnchantment.Duration` for temporary lifetime. The source spell, caster, target item, item owner,
enchant definition, concrete slot, and persistence operation are therefore separate provenance facts.
Prismatic-enchant application additionally verifies a type-8 slot.

`GemProperties.Enchant_ID` proves a gem-to-enchant-definition edge for every nonzero current value. It does
not prove socket admission, uniqueness, replacement, or inventory mutation; those depend on the concrete
item, sockets, conditions, actor inventory, and host transaction.

**Unknown after exhaustive available evidence — enchant effect raw 14.** It is populated in 58 slots but
is absent from Trinity's enum ending at 13, and no independent generic SimC consumer establishes it. The
argument distributions alone are insufficient to name its payload.

## Bonus-list and tree semantics

The 20,224 bonus entries use 47 raw types. Particularly important current populations are type 23
(attach `ItemEffect`, 1,474 rows), 50 (recursive bonus-list application, 522), 52 (43), and 53 (7).
Additional types populated but absent from Trinity's named current enum include 24 (8), 26 (31), 29 (2),
32 (1), 52, and 53; raw 0 itself has 826 rows. Enum adjacency supplies no identity.

**Strongly verified — a bonus list is an ordered transformation program.** Trinity iterates the entries
returned for a list and applies type-specific composition. Some values add (item level, stat editor,
required level), some multiply (repair cost), some choose the smallest priority (suffix, appearance,
scaling and curve families), quality chooses the maximum, item-limit category uses first-wins state, and
type 50 recursively applies another list. Type 23 appends an item-effect definition. Consequently neither
set union nor last-write-wins is a valid universal merge rule; entry type, authored order, priority, and
prior accumulator state are required.

**Partially characterized — bonus types 52 and 53.** Trinity has no corresponding enum/consumer in the
inspected revision. SimC explicitly names them crafting quality and post-squish item level and consumes
them in item-level derivation; type 53 is gated by its Midnight-scaling context, while type 52 also derives
a crafting-quality ordinal. This is useful independent implementation evidence, but SimC comments and
neighboring logic leave some priority/disable rules uncertain. The conservative contract is their feature
family and payload preservation, not universal server/client parity.

**Partially characterized — bonus-tree selection.** `ItemXBonusTree` provides item-to-root eligibility;
tree nodes select child trees, lists, list groups, item-level selectors, contexts, and Mythic+ ranges.
These source edges do not identify which list a concrete item instance received. That choice needs item
creation context, bonus-list instance data, conditions, and host history. Tree reachability must not be
materialized as “all bonuses active.”

**Rejected inference — unknown bonus types are ignorable.** Trinity's switch does not execute all current
raws, but SimC's support for newer raws 52/53 is direct counterevidence to equating one server's missing
case with no semantic. Unknown entries remain typed opaque transformations.

## Set spells and static versus mutable membership

Current set thresholds are 1 (2 rows), 2 (1,275), 3 (151), 4 (1,276), 5 (65), 6 (125), and 8 (77).
Specialization-restricted rows are concentrated at thresholds two and four; one edge also has a trait
subtree selector.

**Strongly verified — set activation is mutable actor state.** Trinity groups `ItemSetSpell` by set,
validates required skill/rank and legacy/heirloom conditions, counts qualifying equipped set items, then
applies or removes a set spell when a threshold is crossed, with specialization/trait filtering. The
definition is immutable metadata; currently equipped members, threshold crossing, applied aura/spell, and
specialization are mutable state. The set record's item slots do not mean the bonus is always active.

**Partially characterized — crafting, upgrade, and sequence relationships.** The corpus exposes crafting
reagent/quality tables, bonus groups, item-level selectors, and the sparse `ItemBonusSequenceSpell` edge,
but neither numeric joins nor names prove a single upgrade/downgrade lifecycle. Current Core, Trinity, and
SimC do not jointly establish transactional replacement, refund, rollback, or persistence behavior for
the complete surface. Preserve the graph and delegate mutations to an inventory-authoritative host.

## Core-facing conclusions

The safe prebindable facts are source-row identities and edges: item-to-effect, effect-to-spell, trigger
role, gem-to-enchant, item-to-bonus-tree, bonus entry order, set membership, and set threshold selectors.
Selected bonus lists, concrete enchant slots, charges/cooldowns, equipment state, ownership, trade target,
and persistence are runtime/host facts. Missing current joins should retain raw identities and provenance;
they must not silently delete edges. Any future semantic compiler needs separate identities for base item,
item instance, source item, target item, caster, item owner, definition, and mutable slot.

## Reproduction notes

Complete DuckDB queries grouped every item-effect edge and trigger, all three enchant slots, every bonus
type/list/tree edge, and all set thresholds; candidate foreign keys were anti-joined with zeros reported
separately. Reusable inventory, reference-coverage, item graph, and two-build diff support is in
`scripts/research/{remaining_data_audit.py,wago_research.py}`. Run with `uv run` as documented in report
17.

Executable evidence was traced through Trinity `ItemTemplate.h`, `Item.cpp`, `Player.cpp`,
`SpellEffects.cpp`, `ItemBonusMgr`, and DB2 structures. SimC's `sc_item_data.cpp`, item-effect consumers,
and `data_enums.hh` provided an independent but simulation-scoped comparison.
