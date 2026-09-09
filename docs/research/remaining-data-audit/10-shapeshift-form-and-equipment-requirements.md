# Shapeshift, form, and equipment requirements

## Result

The two `SpellShapeshift` masks are 64-bit form sets assembled from two 32-bit words. They are not
standalone allow/deny lists: the exclusion mask has first priority, an explicit inclusion admits the
current form, and otherwise the result depends on whether the current form acts as a shapeshift and
on two spell attributes. This admission contract and the bit-to-form mapping are **strongly
verified**. `StanceBarOrder` is only a signed ordinal in the available evidence; its behavioral or UI
contract is **unknown after exhaustive available evidence**.

`SpellEquippedItems` carries three different selectors: an item-class raw, a class-relative subclass
mask, and an inventory-type mask. Their current identities and joins are **strongly verified**, but
their operation depends on the call site. Target-item suitability, explicit main/off-hand cast
admission, equipped-item discovery, passive-aura proc admission, and enchant targeting use different
subsets. In particular, the inventory-type mask is checked generically by Trinity only for enchant
targets; it is not a universal equipped-slot rule.

Evidence versions are Wago `12.1.0.69497`
(`2ddced452a6f9076de5c86bc92f73de5b60f8556`), TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimC
`b48def9c26d7532db2e612d97d433ec66bd8eede`. DuckDB 1.5.5 scanned the complete current tables;
row totals, expanded masks, and anti-joins were independently repeated.

## Shapeshift population

`SpellShapeshift.csv` contains 6,431 rows for 6,431 spells; `SpellID` is unique. The authored mask
shapes are:

| Included forms | Excluded forms | Spells |
|---|---|---:|
| none | none | 4,613 |
| none | one or more | 1,021 |
| one or more | none | 706 |
| one or more | one or more | 91 |

Expanding both words with `form_id = word_index * 32 + bit_index + 1` produces 1,038 inclusion
memberships on 797 spells and 2,581 exclusion memberships on 1,112 spells. Inclusion uses 35
distinct forms; exclusion uses 28. Every one of those raw form IDs joins
`SpellShapeshiftForm.ID`; there are no unknown set bits. No spell includes and excludes the same
form.

`SpellShapeshiftForm` has exactly 64 definitions, IDs 1..64. Several high IDs are named
`zzOLD`/`zzReuseMe`, but they remain valid members of the raw domain. Current second-word usage is
material: 26 spells set 33 inclusion memberships for forms 33..64, while 66 spells set 94 exclusion
memberships for those forms. A signed CSV value in word 0 is simply bit 31 set: 119 inclusion words
and 199 exclusion words are negative when parsed as signed 32-bit values. Mask expansion must first
normalize each word to its unsigned 32-bit representation.

The most common exclusions are Tree of Life/form 2 (642 memberships), Flight/form 29 (262), Epic
Flight/form 27 (259), Travel/form 3 (232), Spirit of Redemption/form 32 (199), Ghost Wolf/form 16
(169), Moonkin/form 31 (140), and Shadowform/form 28 (129). The most common inclusions are Undead/form
25 (183), Shadowform/form 28 (172), Spirit of Redemption/form 32 (119), Cat/form 1 (117), Moonkin/form
31 (80), and Bear/form 5 (71). These are population descriptions, not independent operation names.

### `StanceBarOrder`

The field ranges from -1 to 6. Its complete distribution is:

| Value | Spells | Both form masks zero |
|---:|---:|---:|
| -1 | 4,063 | 4,063 |
| 0 | 1,804 | 6 |
| 1 | 513 | 501 |
| 2 | 31 | 29 |
| 3 | 8 | 6 |
| 4 | 5 | 3 |
| 5 | 6 | 5 |
| 6 | 1 | 0 |

The six spells with value 0 and no form mask are `Throw Scroll` (105794), `Bash` (106041),
`Mantid Summon Visual` (130575), `Bug 365757 Fired` (144890), `Horizontal Line` (153206), and
`JKL test` (158081). This falsifies a simple “nonnegative means a populated form restriction”
reading. Trinity loads the byte but has no executable read after assembly; SimC's DB2 format names it
`unk` and does not export it. Its numeric shape and table name suggest action-bar ordering, but no
available executable evidence establishes the owner, ordering base, sentinel contract, or effect.

## Executable form admission

**Strongly verified — 64-bit assembly and identity.** Trinity combines
`ShapeshiftMask_0/1` and `ShapeshiftExclude_0/1` with `MAKE_PAIR64`
(`SpellInfo.cpp:1498-1499`). `CheckShapeshift` computes the current-form bit as
`1ULL << (form - 1)` (`SpellInfo.cpp:2111-2157`). The `+1` in the raw mapping is therefore required:
bit 0 means form ID 1, not form ID 0.

**Strongly verified — precedence.** `CheckShapeshift` first rejects a current form present in
`StancesNot`, then accepts one present in `Stances`. Only an unmatched form falls through to default
logic. Although the current data has no same-row mask overlap, exclusion-first ordering is an
executable part of the contract.

**Strongly verified — masks compose with form flags and attributes.** Trinity reads the current
`SpellShapeshiftForm` flags to distinguish forms acting as shapeshifts from forms acting as stances.
For a shapeshift-like form, raw attribute 16 (`SPELL_ATTR0_NOT_SHAPESHIFTED`) or the form flag
`CanOnlyCastShapeshiftSpells` rejects an otherwise-unmatched spell; a nonzero inclusion mask then
requires some other form. For caster form/no form, raw attribute 83
(`SPELL_ATTR2_ALLOW_WHILE_NOT_SHAPESHIFTED_CASTER_FORM`) permits ordinary caster-form use despite a
nonzero inclusion mask; without it, the mask requires shapeshifting. Thus “inclusion mask is a
whitelist in every form” is **rejected**.

This composition is visible in the full current population. Considering attributes 16 and 83
independently of every other bit, the 706 inclusion-only spells split into 372 with both, 54 with
only 16, 101 with only 83, and 179 with neither. The 91 spells with both masks split into 80 with
both attributes, ten with only 83, and one with neither. The 1,021 exclusion-only spells split into
five with both, 512 with only 16, ten with only 83, and 494 with neither. No single attribute is
intrinsic to either mask shape.

**Strongly verified — evaluation and bypass.** Ordinary player cast checking calls
`CheckShapeshift`; triggered casts carrying `TRIGGERED_IGNORE_SHAPESHIFT` bypass that admission
check. A `SPELL_AURA_MOD_IGNORE_SHAPESHIFT` effect can also exempt affected spells. Other aura
lifecycle code separately checks required stances when deciding whether an aura survives a form
change (`SpellAuraEffects.cpp:1596,1631`), so cast admission does not by itself specify aura-removal
policy.

**Historical falsification.** Trinity commit `f56bb2e0a6` (“Extended spell required shapeshift masks
to 64 bits”) replaced the former 32-bit handling. Current Wago rows actually use the second word.
Any first-word-only model is therefore **rejected** by both history and current population.

## Equipment population

`SpellEquippedItems.csv` contains 3,758 rows for 3,758 spells. Unlike Trinity's internal default
`EquippedItemClass = -1` for a spell with no component, every present row has a current class raw
from 0 through 19. There are 125 class-only rows with both masks zero, 207 rows with no subclass
mask, and 2,654 with no inventory-type mask.

The correct class join is `SpellEquippedItems.EquippedItemClass = ItemClass.ClassID`:

| Class raw | Current identity | Spells |
|---:|---|---:|
| 2 | Weapon | 2,611 |
| 4 | Armor | 1,079 |
| 19 | Profession | 49 |
| 15 | Miscellaneous | 11 |
| 7 | Tradeskill | 2 |
| 13 | Key | 2 |
| 17 | Battle Pets | 1 |
| 1 | Container | 1 |
| 10 | Money (obsolete) | 1 |
| 0 | Consumable | 1 |

All 3,758 rows resolve on `ClassID`. Joining the same raw to `ItemClass.ID` gives plausible but wrong
labels (for example raw 2 becomes Container and raw 4 becomes Gem) and leaves an orphan. That
foreign-key interpretation is **rejected**.

The subclass masks expand to 22,691 memberships across 3,551 spells and 43 distinct
`(EquippedItemClass, subclass_bit)` pairs. Every pair resolves exactly on
`ItemSubClass.(ClassID,SubClassID)`; there are no unresolved set bits. This establishes that a
subclass bit is class-relative, not a globally meaningful subclass identity.

Inventory masks expand to 1,816 memberships on 1,104 spells. Current bits cover every raw
inventory type 0..29 and no bit above 29; each has a Trinity `InventoryType` definition. Common
memberships include legs (175), shoulders (153), wrists (131), feet (116), finger/chest/robe (111
each), hands (109), cloak (108), off hand (102), and main hand (91). This complete range proves the
bit position is the inventory-type raw, but not that every runtime consumer applies it.

Sixty-eight rows use classes other than weapon or armor. Six of those have both masks zero. They are
important outliers: the selectors are structurally valid and often useful for target-item checks,
but Trinity's equipped-item discovery supports only weapon and armor classes.

## Executable equipment semantics

**Strongly verified — target-item fit.** `Item::IsFitToSpellRequirements`
(`Item.cpp:1477-1508`) first checks the class when the spell has one, then interprets a nonzero
subclass mask against that item's subclass. Zero subclass mask means any subclass within the
required class. For enchant effects only, it also tests a nonzero inventory-type mask against the
target item's inventory type. A generic one-hand weapon is accepted for an enchant asking for main
or off hand. An enchantable item carrying `ITEM_FLAG3_CAN_STORE_ENCHANTS` is accepted before the
class/subclass checks. These exceptions belong to item-target suitability, not to the raw masks.

**Rejected — inventory mask is a universal third selector.** The same item-fit helper ignores
`EquippedItemInvTypes` for non-enchant spells. `Spell::CheckItems` uses item-fit on an explicit item
target, or otherwise asks the player to find fitted equipped gear
(`Spell.cpp:7507-7527`). Neither path turns the inventory mask into a standalone global slot check.

**Strongly verified — hand-specific cast admission is attribute-composed.** Later player-cast
checking examines the main-hand slot only when raw attribute 106
(`SPELL_ATTR3_REQUIRES_MAIN_HAND_WEAPON`) is set, and the off-hand slot only for raw 120
(`SPELL_ATTR3_REQUIRES_OFF_HAND_WEAPON`). The item must exist, be usable and unbroken, and pass the
class/subclass item-fit check. `TRIGGERED_IGNORE_EQUIPPED_ITEM_REQUIREMENT` bypasses both equipped
discovery and these hand checks (`Spell.cpp:7958-7991`).

The 2,611 weapon rows separate cleanly when passive, enchant, hand, and proc-bypass state are grouped.
There are 516 active non-enchant rows with main-hand only, another 19 with main-hand plus proc
bypass, 54 with off-hand only, 22 with both hands, and one with both hands plus proc bypass. Nineteen
carry proc bypass without a hand bit. Another 1,614 active non-enchant rows carry neither hand bit or
proc bypass; 319 are enchant effects with none of those attributes and 47 are passive rows with none.
This outlier population rejects interpreting weapon class alone as an exact required slot; it can
still participate in generic equipped discovery and other systems.

**Strongly verified — equipped discovery.** `Player::HasItemFitToSpellRequirements`
(`Player.cpp:26158-26220`) searches usable main/off-hand items for a weapon requirement. For armor,
it normally searches fitted worn armor (with a shield/off-hand optimization and a nonpassive aura
exception). Raw attribute 276 (`SPELL_ATTR8_REQUIRES_EQUIPPED_INV_TYPES`) changes armor semantics to
require a fitted item in all eight enumerated armor slots: head, shoulders, chest, waist, legs,
feet, wrists, and hands. It does not mean “apply the inventory mask” in this function. All 28 current
rows carrying attribute 276 are passive armor requirements.

Classes other than weapon and armor log “not handled” and fail equipped discovery. This does not
invalidate those 68 data rows: explicit target-item operations can still use their class/subclass
requirements. Their generic non-target owner and client behavior are **partially characterized**.

**Strongly verified — passive proc-time requirement.** Before a passive aura procs, Trinity checks
its equipment requirement unless raw attribute 97
(`SPELL_ATTR3_NO_PROC_EQUIP_REQUIREMENT`) is set (`SpellAuras.cpp:1925-1958`). For weapon auras it
selects main or off hand from the triggering damage event and rejects feral form; for armor it uses
off hand. A missing, broken, or mismatched item suppresses the proc. This is a later event-time
predicate and must not be collapsed into cast admission. Forty-eight current equipment rows carry
the bypass attribute.

## SimulationCraft boundary

SimC exports equipped class, subclass mask, and inventory-type mask. Generic
`action_t::verify_actor_weapon` handles only player weapon-class actions and only tests a hand when
the corresponding main/off-hand attribute is set (`action.cpp:1138-1165`). It does not implement
the armor discovery or passive proc paths above. `spell_data_t::valid_item_enchantment` tests only
the inventory mask, but no current caller was found; it is name/implementation evidence, not a
generic operational consumer.

For forms, SimC's generator exports only `SpellShapeshift.flags_1` to a 32-bit `_stance_mask`
(`generator.py:3512-3515`). It omits both exclusion words, the second inclusion word, and
`StanceBarOrder`. Generic spell data has no full form-admission consumer. Druid actions cache that
first mask, while rogue code uses its form-30 bit to infer a stealth spell. Those spec-specific uses
corroborate individual current cases but are too narrow to define the Wago table. The 33 current
second-word inclusions and 94 exclusions are direct counterexamples to treating SimC's reduction as
lossless.

## Terminal dispositions and missing evidence

| Surface | Disposition | Conservative conclusion |
|---|---|---|
| inclusion/exclusion mask bit identity | **Strongly verified** | two-word 64-bit set; bit `n` selects form ID `n+1` |
| form admission operation | **Strongly verified** | exclusion first, inclusion second, then form-flag/attribute-dependent fallback |
| aura persistence across form change | **Partially characterized** | separate lifecycle checks exist; mask admission alone is not the full removal contract |
| `StanceBarOrder` | **Unknown after exhaustive available evidence** | signed ordinal -1..6, loaded but unconsumed in both comparison runtimes |
| equipped item class identity | **Strongly verified** | joins `ItemClass.ClassID`, not record `ID` |
| subclass bit identity | **Strongly verified** | class-relative `ItemSubClass.SubClassID` |
| inventory bit identity | **Strongly verified** | raw `InventoryType` bit 0..29 |
| target-item and enchant operation | **Strongly verified** | class/subclass universally in item fit; inventory mask and special relaxations for enchant effects |
| equipped cast/proc operation | **Partially characterized** | strong Trinity behavior for weapon/armor; other classes and client-side behavior remain unresolved |

Authoritative client executable evidence would be needed to establish `StanceBarOrder`, the UI/action
bar owner of the row, and behavior for non-weapon/non-armor equipped requirements outside explicit
item targeting. Current names or a single content family are insufficient.

## Reproduction notes

The census loaded `SpellShapeshift`, `SpellShapeshiftForm`, `SpellEquippedItems`, `ItemClass`,
`ItemSubClass`, `SpellMisc`, `SpellEffect`, and `SpellName` into DuckDB views. It normalized signed
mask words, expanded every one of 64 form positions and 32 item-mask positions, anti-joined all
candidate domains, grouped complete attribute/effect co-occurrence, and separately enumerated every
class and selectorless outlier. Source searches covered exact field reads, cast checks, target-item
fit, equipped discovery, passive learning/proc checks, form-change removal, generated metadata, and
SimC action consumers. Trinity history was searched for the 32-to-64-bit mask transition.
