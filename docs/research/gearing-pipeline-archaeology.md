# Gearing pipeline archaeology

## Scope and result

This audit answers one question end to end:

> Given an equipped item on a current Player, how does the game derive every
> combat-relevant fact contributed by that item?

It traces the answer from the checked-in Wago source tables through the direct
TrinityCore consumers, and ships an executable reconstruction
(`scripts/research/gearing/`, driven by `scripts/research/item_scaling.py`) that
reproduces the proved pipeline from those tables, with per-stage provenance.

Evidence is the Wago snapshot **`12.1.0.69497`** in `data/tables/` at
`c738030f59de193ea6536be4b5852fbbd2f07b83`, and TrinityCore
**`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`** (master, `CURRENT_EXPANSION ==
EXPANSION_MIDNIGHT`, `GetMaxLevelForExpansion(11) == 90`). Every claim below
names the table and the consumer function it came from. Nothing is taken from a
tooltip site; where external naming is used it is navigation only and is
labelled as such.

**Headline results.**

1. The current item-level authority is **not** `ItemBonus` level deltas. It is
   `ItemBonus` type 49/51 → `ItemScalingConfig` → `ItemOffsetCurve` → `Curve`,
   consumed by the *else* branch of `Item::GetItemLevel`. On that branch
   `BonusData::ItemLevelBonus` is **not** added at all.
2. Midnight introduced a real item squish, expressed as `ItemSquishEra` 2
   (`Patch = 120000`, `CurveID = 92181`), applied inside `Item::GetItemLevel`
   to every item whose `ItemSquishEraID` is lower than the realm's patch. A
   Dragonflight 441 chest becomes item level **74**.
3. Difficulty does not scale an ItemID. `MapDifficulty`/`Difficulty` produce an
   `ItemContext`, the context selects `ItemBonusTreeNode` rows, those select
   bonus lists, and the bonus lists carry the item level. This is fully
   direct-consumer backed.
4. Item sets are canonical template state (`ItemSparse.ItemSet`), not bonus-list
   state. The hypothesis *equipped instances → set membership → piece count →
   threshold → ordinary spell* is **verified**, with one correction: the spec
   and trait-subtree gates filter the *cast*, not the count.
5. "Current season" is **not derivable** from this snapshot or from the direct
   consumer. Every roster command therefore requires an explicit selector.

---

## 1. Item identity and equipped-instance composition

`ObjectMgr::LoadItemTemplates` builds an `ItemTemplate` from two DB2 rows plus
an assembled effect list:

| Fact | Source | Kind |
|---|---|---|
| ItemID, ClassID, SubclassID, `ItemSquishEraID` | `Item.db2` | canonical |
| quality, inventory type, base item level, required level, stats, sockets, flags, delay, damage variance, `ItemSet`, `Gem_properties` | `ItemSparse.db2` | canonical |
| item effects | `ItemXItemEffect` → `ItemEffect` | canonical |
| bonus lists | item-instance state (`ItemInstance::ItemBonus::BonusListIDs`) | instance |
| gems | item-instance state (`UF::ItemData::Gems`) | instance |
| enchantments | item-instance state (`EnchantmentSlot` array) | instance |
| `ITEM_MODIFIER_TIMEWALKER_LEVEL` | item-instance state | instance |
| azerite level | item-instance state (`AzeriteItem`) | instance |
| PvP item-level context | player state (`Player::IsUsingPvpItemLevels`) | player |
| `UnitData::MinItemLevel` / `MinItemLevelCutoff` / `MaxItemLevel` | player state | player |

`ItemTemplate` is a *pair of pointers*, `BasicData` (Item.db2) and
`ExtendedData` (ItemSparse.db2); Trinity renames a few ItemSparse columns
(`GetGemProperties()` reads `Gem_properties`, `GetSocketBonus()` reads
`Socket_match_enchantment_ID`, `GetDelay()` reads `ItemDelay`). All 103
ItemSparse fields match TrinityCore's `ItemSparseLoadInfo` positionally in this
snapshot; see §20.

`BonusData` (`Entities/Item/Item.h`) is the accumulator into which the
instance's bonus lists are folded. `BonusData::Initialize(ItemTemplate const*)`
seeds it from the template; `AddBonusList` → `AddBonus` mutates it.

**Random property / suffix.** `ITEM_BONUS_SUFFIX` (type 5) still exists (726
rows) and is still handled, but the *legacy* random-enchant machinery is gone
from the data path: `GenerateItemRandomBonusListId` reads
`item_template_addon.RandomBonusListTemplateId` from Trinity's own world
database, not from DB2. Random property points survive only as
`RandPropPoints`, which is now a pure item-level → allocation-budget table and
has nothing random about it.

**Crafted modifiers.** Represented, not modelled: `ItemBonus` type 25
(`MODIFIED_CRAFTING_STAT`, 35 rows) is `NYI` in the direct consumer, and
`ItemSparse.ModifiedCraftingReagentItemID` has no gearing consumer. Recorded as
unsupported (§18).

**Effect ordering caveat.** `ObjectMgr` inserts each effect at
`lower_bound(LegacySlotIndex)`, so two effects sharing a slot index end up in
*reverse* cross-table order. The port reproduces this (`ItemStore._effect_index`)
and pins it with a test.

## 2. Effective item level

Authoritative path: `Item::GetItemLevel(ItemTemplate const*, BonusData const&,
uint32 level, uint32 fixedLevel, uint32 minItemLevel, uint32 minItemLevelCutoff,
uint32 maxItemLevel, bool pvpBonus, uint32 azeriteLevel)` in
`src/server/game/Entities/Item/Item.cpp`.

Exact evaluation order, with the rounding boundary at each stage:

```
1  itemLevel = BonusData::ItemLevel                     (ItemSparse.ItemLevel,
                                                         overridden by bonus type 42)
2  if azeriteLevel: itemLevel = AzeriteLevelInfo[azeriteLevel].ItemLevel
3  if !ItemLevelOffsetCurveId:
       if PlayerLevelToItemLevelCurveId:
           level = fixedLevel
                   else clamp(playerLevel, ContentTuning.Min, ContentTuning.Max)
           itemLevel = uint32(std::round(Curve(PlayerLevelToItemLevelCurveId, level)))
       itemLevel += BonusData::ItemLevelBonus            (sum of bonus type 1)
   else:
       itemLevel = ItemLevelOffset
                 + uint32(std::round(Curve(ItemLevelOffsetCurveId,
                                           ItemLevelOffsetItemLevel)))
       # ItemLevelBonus is NOT added on this branch
4  itemLevel += sum(GemItemLevelBonus[0..2])
5  itemLevelBeforeUpgrades = itemLevel                   (snapshot for step 7)
6  if pvpBonus:
       if PvpItemLevel: itemLevel = PvpItemLevel
       itemLevel += PvpItemLevelBonus
7  if !IgnoreSquish:
       for squishId in ItemSquishEraID+1 .. maxSquishId:
           skip if Flags & 0x1
           break if Patch > realmPatch
           itemLevel = uint32(std::round(Curve(squish.CurveID, itemLevel)))
8  if InventoryType != NON_EQUIP:
       if minItemLevel and (!cutoff or itemLevelBeforeUpgrades >= cutoff)
          and itemLevel < minItemLevel: itemLevel = minItemLevel
       if maxItemLevel and itemLevel > maxItemLevel: itemLevel = maxItemLevel
9  return clamp(itemLevel, MIN_ITEM_LEVEL=1, MAX_ITEM_LEVEL=1300)
```

Notes that matter:

* **`std::round`, not banker's rounding.** Every `std::round` above is
  half-away-from-zero. Python's `round()` is not; the port uses
  `gearing.curves.round_half_away` and tests the difference explicitly.
* **The min floor is gated on the *pre-upgrade* level**, `itemLevelBeforeUpgrades`,
  which is captured *before* the PvP and squish stages. That ordering is
  discriminating and is pinned by a test.
* **`realmPatch`** comes from `ClientBuild::GetMinorMajorBugfixVersionForBuild`
  as `major*10000 + minor*100 + bugfix` — for 12.1.0 that is `120100`. It is a
  *realm* fact, not an item fact, so the tool takes it as `--squish-patch` and
  applies no squish when it is absent.

### The current mechanism: ItemScalingConfig

For current items, step 3 takes the *else* branch. The chain is:

```
ItemBonus.Type = 49 (SCALING_CONFIG_AND_REQ_LEVEL) or 51 (SCALING_CONFIG)
  Value[0] -> ItemScalingConfig.ID
              .ItemOffsetCurveID -> ItemOffsetCurve.CurveID + .Offset
              .ItemLevel         -> BonusData::ItemLevelOffsetItemLevel  (type 49 only)
              .RequiredLevel     -> BonusData::RequiredLevelOverride     (type 49 only)
              .ItemSquishEraID   -> BonusData::ItemSquishEraID
              .Flags & 0x1       -> BonusData::IgnoreSquish
```

Every current raid/dungeon `ItemScalingConfig` points at `ItemOffsetCurve` 47 →
**curve 88583**, which is the identity `(0,0) → (1300,1300)` with `Offset = 0`.
So the effective item level is exactly `ItemScalingConfig.ItemLevel`. The
indirection exists so that Blizzard can re-target a whole content tier's item
levels by editing one curve.

Witness, tier head `271519` at `ItemContext::Raid_Mythic`:

```
bonus list 12849 -> ItemBonus 30830 type 49 value[0]=318
                 -> ItemScalingConfig 318: offset curve 47, ItemLevel 318,
                    RequiredLevel 90, ItemSquishEraID 2
                 -> ItemOffsetCurve 47: CurveID 88583, Offset 0
Item::GetItemLevel -> 0 + round(Curve(88583, 318)) = 318
```

### The Midnight item squish

`ItemSquishEra` has exactly two rows in this snapshot:

| ID | Patch | CurveID | Flags |
|---:|---:|---:|---:|
| 1 | 0 | 0 | 0 |
| 2 | 120000 | 92181 | 2 |

Curve 92181 is a 54-point linear curve mapping pre-squish to post-squish item
level (`(1,1) (10,10) (342,70) (571,80) … (1300,…)`). 170,820 ItemSparse rows
carry `ItemSquishEraID = 0`, 7 carry 1 and 4,337 carry 2. Any item still in era
0 or 1 is squished when the realm's patch is ≥ 120000.

```
$ python3 scripts/research/item_scaling.py show 210352 --squish-patch 120100
  base                  item_level=441
  item-squish-era       item_squish_era_id=2, curve_id=92181,
                        raw_y=74.32314410480349, item_level=74
```

`Flags & 0x1` skips an era (era 2's `Flags` is 2, so it is *not* skipped);
`ItemScalingConfig.Flags & 0x1` sets `IgnoreSquish` on the item instead.

## 3. Difficulty and context variants

**Difficulty never scales an ItemID directly.** The proved chain is:

```
Map + Difficulty
  -> MapDifficulty row
  -> ItemBonusMgr::GetContextForPlayer:
       context = evalContext(NONE,   Difficulty.ItemContext)
       context = evalContext(context, MapDifficulty.ItemContext)
       if MapDifficulty.ItemContextPickerID:
           context = evalContext(context, <selected ItemContextPickerEntry>)
  -> ItemContext
  -> ItemBonusMgr::GetBonusListsForItem(itemId, {context, m+ level, pvp tier})
  -> ItemBonusTreeNode selection
  -> bonus lists
  -> BonusData
  -> Item::GetItemLevel / Item::GetItemStatValue
```

`evalContext(current, new)` keeps `current` when `new == NONE`, resets to `NONE`
when `new == Force_to_NONE` (21), and otherwise replaces.

For the current raid (`Map` 3004, *The Venomous Abyss*):

| DifficultyID | Difficulty name | `Difficulty.ItemContext` | resolved ItemContext |
|---:|---|---:|---|
| 14 | Normal | 3 | `Raid_Normal` |
| 15 | Heroic | 5 | `Raid_Heroic` |
| 16 | Mythic | 6 | `Raid_Mythic` |
| 17 | Looking For Raid | 4 | `Raid_Raid_Finder` |
| 220 | Story | 0 | `NONE` |
| 241 | Lorewalking | 0 | `NONE` |

and the resulting item levels for tier head `271519` at player level 90:

| Context | Bonus lists | Effective ilvl |
|---|---|---:|
| `NONE` | 13692, 13698 | 219 |
| `Raid_Raid_Finder` (4) | 12825, 13332, 13692, 13698 | 279 |
| `Raid_Normal` (3) | 12833, 13333, 13692, 13698 | 292 |
| `Raid_Heroic` (5) | 12841, 13334, 13692, 13698 | 305 |
| `Raid_Mythic` (6) | 12849, 13335, 13692, 13698 | 318 |

So the answer to "which mechanism expresses difficulty variants" is: **bonus
lists reached through context-gated `ItemBonusTreeNode` rows**, whose payload is
an `ItemScalingConfig`. Not distinct ItemIDs; not `ItemLevelSelector` for
current content (selectors are a previous-expansion mechanism, still used by
e.g. item 237602); not a curve keyed on difficulty.

### The ItemContextPicker branch is player state

`MapDifficulty.ItemContextPickerID` selects between contexts using a
`PlayerCondition` per entry, with `Flags & 0x1` inverting the test, and the
highest `OrderIndex` winning. Picker 278 (used by every current dungeon's Normal
difficulty) has two entries with the *same* `PlayerConditionID` (145273), one
inverted, yielding `Dungeon_Lvl_Up_1` (17) or `Dungeon_Normal` (1).

The tool refuses to guess: `RosterDiscovery.difficulty_contexts` reports the
pre-picker context plus **every** picker alternative with its condition id, and
the caller decides.

### Variant discovery

`GearResolver.discover_variants` enumerates only contexts the item's own
reachable trees expose:

* `ItemBonusTreeNode.ItemContext` on any reachable node;
* every `ItemContext` member of a referenced `ItemCreationContextGroupID`;
* the contexts named by the hardcoded sequence-level switch, and only when one
  of those trees is reachable;
* `NONE`, always.

Keystone levels come from reachable `Min`/`MaxMythicPlusLevel` bounds. Tier head
`271519` yields 1,133 raw variants collapsing to 43 distinct results.

### A sharp edge in the group selector

`ApplyBonusTreeHelper`'s group-entry filter is

```cpp
if ((resolvedSequenceLevel > 0 || entry->SequenceValue <= 0) &&
    resolvedSequenceLevel != entry->SequenceValue)
    continue;
```

With `resolvedSequenceLevel == 0` and a positive `SequenceValue`, the left
operand is false, so the skip never fires and the **first** group entry in row
order is taken. That is why an item with no context still picks up the first
step of its upgrade track. This is reproduced and pinned by a test rather than
"fixed".

## 4. Curves

`Curve` has 62,533 rows and `CurvePoint` 171,168; 50,234 curves have at least
one point. The evaluator is `DB2Manager::GetCurveValueAt` and the mode selector
is the file-local `DetermineCurveType`.

**Points are ordered by `CurvePoint.OrderIndex`, not by X.** `DB2Manager::LoadStores`
sorts by `OrderIndex` and the scanning interpolators then assume X is
non-decreasing. Six curves violate that (see §18).

**Mode selection** (`DetermineCurveType`):

| `Curve.Type` | points | mode |
|---:|---|---|
| 1 | < 4 | Cosine |
| 1 | ≥ 4 | CatmullRom |
| 2 | 1 / 2 / 3 / 4 / ≥5 | Constant / Linear / Bezier3 / Bezier4 / Bezier |
| 3 | any | Cosine |
| 0, **4**, **5**, other | 1 / else | Constant / Linear |

**Clamping.** Linear and Cosine return the first point's Y below the domain and
the last point's Y above it. CatmullRom clamps to `points[1].Y` and
`points[n-2].Y` — it never reaches the outer two points. Bezier variants do not
clamp at all: they extrapolate the polynomial.

**Numeric domain.** `DBCPosition2D` is a pair of **`float`**, and the evaluator
returns `float`. The port computes in Python `double`. Section "Differential
verification" bounds the consequence.

### Curve families that participate in item scaling

| Family | Chosen by | x | y | Consumer |
|---|---|---|---|---|
| player-level → item level | `ItemSparse.PlayerLevelToItemLevelCurveID`, or `ItemBonus` type 11/13 `Value[3]` | player level (clamped by ContentTuning, or `fixedLevel`) | item level | `Item::GetItemLevel` |
| item-level offset | `ItemScalingConfig.ItemOffsetCurveID` → `ItemOffsetCurve.CurveID`; or `ItemBonus` type 48 `Value[0]` | `BonusData::ItemLevelOffsetItemLevel` | item level | `Item::GetItemLevel` (else branch) |
| item squish | `ItemSquishEra.CurveID` | current item level | squished item level | `Item::GetItemLevel` |
| rating diminishing | `GlobalCurve.Type` → `CurveID`, per `CombatRating` | linear rating percentage | effective percentage | `Player::ApplyRatingDiminishing` |
| Mythic+ sequence level | hardcoded per `IblGroupPointsModSetID` (tree 4079 only) | keystone level | sequence value | `ApplyBonusTreeHelper` |
| gem relic item level | hardcoded `CURVE_ID_ARTIFACT_RELIC_ITEM_LEVEL_BONUS = 1718` | gem item level | item-level delta | `Item::SetGem` |
| required level | `ItemBonus` type 27 `Value[0]` | — | — | stored in `BonusData::RequiredLevelCurve`, **never evaluated** by the gearing path |

These are *not* unified behind one abstraction in the port. `gearing.curves`
evaluates a curve; choosing which curve and what x belongs to the consumer,
because the consumers genuinely disagree about both.

The rating-diminishing curves in this snapshot:

| GlobalCurve type | meaning | CurveID |
|---:|---|---:|
| 0 | CritDiminishing | 21024 |
| 1 | MasteryDiminishing | 21024 |
| 2 | HasteDiminishing | 21024 |
| 5 | VersatilityDoneDiminishing | 21024 |
| 11 | VersatilityTakenDiminishing | 21035 |
| 3 / 4 / 6 | Speed / Avoidance / Lifesteal | 21025 |
| 7 / 8 / 9 | Dodge / Parry / Block | 21505 |

Curve 21505 is the identity `(0,0) → (100,100)`: avoidance ratings are currently
undiminished. Crit/haste/mastery/versatility-done share curve 21024.

## 5. Primary and secondary stat generation

```
BonusData::ItemStatType[i]            <- ItemSparse.StatModifier_bonusStat_i,
                                          set/overwritten by ItemBonus type 2
BonusData::StatPercentEditor[i]       <- ItemSparse.StatPercentEditor_i,
                                          *accumulated* by ItemBonus type 2
BonusData::ItemStatSocketCostMultiplier[i] <- ItemSparse.StatPercentageOfSocket_i

Item::GetItemStatValue(i, owner):
    if statType in {CORRUPTION, CORRUPTION_RESISTANCE}:
        return StatPercentEditor[i]                      # verbatim, unscaled
    randomPropPoints = GetRandomPropertyPoints(itemLevel, quality,
                                               inventoryType, subClass)
    if !randomPropPoints: return 0
    value  = float(StatPercentEditor[i] * randomPropPoints) * 0.0001f
    value -= ItemStatSocketCostMultiplier[i] * ItemSocketCostPerLevel[itemLevel]

Player::_ApplyItemBonuses:
    if statType == STAMINA:  value *= StaminaMultByILvl[itemLevel][slotColumn]
    elif statType in RATING_SET: value *= CombatRatingsMultByILvl[itemLevel][slotColumn]
    value = std::round(value)                            # <- the rounding boundary
    ... then int32(value) at each ApplyRatingMod / HandleStatFlatModifier site
```

**Where stat identity comes from**: `ItemSparse.StatModifier_bonusStat_*`
(`ItemModType`), mutated by `ItemBonus` type 2. **Where allocation comes from**:
`StatPercentEditor` — a budget share in units of 1/10000, mutated additively by
the same bonus type.

**How item level scales it**: entirely through `RandPropPoints[itemLevel]`,
which is keyed by item level (`ID == item level`, 1..1300).

**How inventory slot affects it**: `GetRandomPropertyPoints` maps inventory type
to one of five RandPropPoints columns —

| column | inventory types |
|---:|---|
| 0 | HEAD, BODY, CHEST, LEGS, RANGED, 2HWEAPON, ROBE, THROWN, RANGEDRIGHT (non-wand) |
| 1 | SHOULDERS, WAIST, FEET, HANDS, TRINKET |
| 2 | NECK, WRISTS, FINGER, SHIELD, CLOAK, HOLDABLE |
| 3 | WEAPON, WEAPONMAINHAND, WEAPONOFFHAND, RANGEDRIGHT (wand) |
| 4 | RELIC |

— and separately through `GetIlvlStatMultiplier`, which picks the Armor / Weapon
/ Trinket / Jewelry column of `CombatRatingsMultByILvl` and `StaminaMultByILvl`.
The two slot mappings are **different** and must not be merged.

**How quality affects it**: `GetRandomPropertyPoints` picks `GoodF` (Uncommon),
`SuperiorF` (Rare, Heirloom) or `EpicF` (Epic, Legendary, Artifact). Poor,
Common and WoW Token get zero. Quality here is `BonusData::Quality`, i.e. after
`ItemBonus` type 3.

**Primary-stat selection**: the source does not pick one. `ItemModType` 71–74
are the combined identities `AGI_STR_INT`, `AGI_STR`, `AGI_INT`, `STR_INT`, and
`Player::_ApplyItemBonuses` applies the *same* rounded value to *each* named
`UNIT_MOD_STAT_*`. Which one a character actually benefits from is a
character-side question and is out of scope here.

**Stamina** is the only stat with its own ilvl multiplier table
(`StaminaMultByILvl`), and it is applied *before* the `std::round`.

**Secondary ratings** route through `MOD_TO_RATINGS`; note that one ItemModType
can feed several `CombatRating` slots: `ITEM_MOD_VERSATILITY` feeds three
(damage done, damage taken, healing done), `ITEM_MOD_HASTE_RATING` feeds melee,
ranged and spell, `ITEM_MOD_CRIT_RATING` likewise.

**Tertiary stats** (`CR_SPEED`, `CR_LIFESTEAL`, `CR_AVOIDANCE`,
`CR_STURDINESS`) are ordinary `ItemModType` values (61–64) that go through the
identical allocation formula and the identical `CombatRatingsMultByILvl`
multiplier. There is no separate tertiary mechanism in the current data.

**Armor** (`ItemTemplate::GetArmor`) is derived, not allocated:

```
non-shield:  uint32(ItemArmorQuality[ilvl].Qualitymod[quality]
                  * ItemArmorTotal[ilvl].<Cloth|Leather|Mail|Plate>
                  * ArmorLocation[invType].<Cloth|Leather|Chain|Plate>modifier
                  + 0.5f)
shield:      uint32(ItemArmorShield[ilvl].Quality[quality] + 0.5f)
```

Heirloom is treated as Rare; anything above Artifact yields 0; `INVTYPE_ROBE` is
normalised to `INVTYPE_CHEST`; armor subclasses outside Cloth..Plate yield 0.
Note the final conversion is a *truncating cast of a biased float*, not
`std::round`.

**Weapon damage** (`ItemTemplate::GetDPS` / `GetDamage`):

```
dps = ItemDamage<table>[ilvl].Quality[quality]
avg = dps * ItemDelay * 0.001f
min = (DmgVariance * -0.5f + 1.0f) * avg
max = floor(avg * (DmgVariance * 0.5f + 1.0f) + 0.5f)     # min is NOT floored
weaponAttackPower = int32(dps * 6.0f)                      # _ApplyWeaponDamage
```

Table selection is by inventory type, then caster flag, then subclass for the
ranged family:

| inventory type | subclass | `ITEM_FLAG2_CASTER_WEAPON` | table |
|---|---|---|---|
| 2HWEAPON | any | no / yes | TwoHand / TwoHandCaster |
| WEAPON, MAINHAND, OFFHAND | any | no / yes | OneHand / OneHandCaster |
| RANGED, THROWN, RANGEDRIGHT | wand (19) | — | OneHandCaster |
| RANGED, THROWN, RANGEDRIGHT | bow/gun/crossbow | no / yes | TwoHand / TwoHandCaster |
| AMMO | — | — | Ammo |

All `ItemDamage*` and `ItemArmor*` tables are keyed `ID == item level`, verified
for all 1,300 rows (1,000 for `ItemDamageAmmo`) by a test.

## 6. Player stat application

`Player::_ApplyItemMods(item, slot, apply)` is the single entry point, in this
order:

```
CorrectMetaGemEnchants          (only if socket 0 is coloured)
_ApplyItemBonuses               stats, armor, weapon
ApplyItemEquipSpell             ItemEffect TriggerType 1
ApplyItemDependentAuras         + UpdateWeaponDependentAuras(attackType)
ApplyArtifactPowers
ApplyAzeritePowers
ApplyEnchantment                all EnchantmentSlots
```

and `Player::_ApplyAllItemMods` drives it across every slot, with
`AddItemsSetItem` / `RemoveItemsSetItem` handled separately at equip/unequip
(`Player.cpp:11532`, `8928`, `8872`).

Additive versus percentage: **everything the gearing path produces is additive**.
`_ApplyItemBonuses` only ever calls `HandleStatFlatModifier(..., BASE_VALUE|TOTAL_VALUE, ...)`
and `ApplyRatingMod`. Primary stats and stamina use `BASE_VALUE`; attack power,
extra armor and gear armor use `TOTAL_VALUE`. Percentage stages exist only in
aura handlers, i.e. outside the gearing boundary.

Rating accumulation: `ApplyRatingMod(cr, value, apply)` adds into
`m_baseRatingValue[cr]` then calls `UpdateRating(cr)`, which folds in
`SPELL_AURA_MOD_COMBAT_RATING_FROM_COMBAT_RATING` and `SPELL_AURA_MOD_RATING_PCT`
auras, clamps at 0, writes `ActivePlayerData::CombatRatings[cr]`, and fans out to
the per-rating update (crit percentage, haste attack-time mod, `UpdateMastery`,
…). Those fan-outs are ordinary player state, not gearing.

Weapon state (`_ApplyWeaponDamage`): sets `MINDAMAGE`/`MAXDAMAGE`, sets
`BASE_ATTACK_TIME` from `ItemDelay` unless a shapeshift overrides it, sets the
per-hand weapon attack power to `int32(dps * 6)`, then `UpdateDamagePhysical`.

**Ownership boundary.** Everything through "rounded per-item stat value" is
item-side and fully reconstructable from source. Everything after
`ApplyRatingMod` / `HandleStatFlatModifier` is character-side.

## 7. Rating conversion and derived percentages

```
CombatRatings[cr]                                   (accumulated int32)
  * Player::GetRatingMultiplier(cr)
        = 1.0f / CombatRatings.txt[playerLevel][cr]  (1.0 if row or column is 0)
  -> linear percentage
  -> Player::ApplyRatingDiminishing(cr, value)
        = Curve(GlobalCurve[<type for cr>], value)  when a curve exists
  -> effective percentage
```

`CombatRatings.txt` column N (after the `Level` key) **is** `CombatRating` N:
index 0 = Amplify, 1 = DefenseSkill, … 25 = Mastery, 28/29/30 = Versatility
damage-done / healing-done / damage-taken. Pinned by a test.

`CR_RESILIENCE_PLAYER_DAMAGE` has one extra stage in `GetRatingBonusValue`:
`(1 - 0.99^value) * 100`.

At player level 90 in this snapshot:

| Rating | per point | 20,000 rating → linear | → after DR | curve |
|---|---:|---:|---:|---:|
| CritMelee/Ranged/Spell | 0.0217391 | 434.783 % | 126.0 % | 21024 |
| HasteMelee/Ranged/Spell | 0.0227273 | 454.545 % | 126.0 % | 21024 |
| Mastery | 0.0217391 | 434.783 % | 126.0 % | 21024 |
| VersatilityDamageDone/HealingDone | 0.0185185 | 370.370 % | 126.0 % | 21024 |
| VersatilityDamageTaken | 0.0092593 | 185.185 % | 63.0 % | 21035 |
| Lifesteal | 0.0144925 | 289.851 % | 49.0 % | 21025 |
| Avoidance | 0.0271735 | 543.471 % | 49.0 % | 21025 |
| Speed | 0.0869553 | 1739.106 % | 49.0 % | 21025 |

Curves 21024, 21025 and 21035 are hard-capped step curves: past their last
point they clamp, which is why several very different linear inputs land on the
same 126 / 49 / 63 ceiling.

(`python3 scripts/research/item_scaling.py ratings --amount 20000`.)

### Mastery, traced separately

```
equipped gear
  -> ITEM_MOD_MASTERY_RATING (49) allocation
  -> Item::GetItemStatValue * CombatRatingsMultByILvl -> std::round
  -> Player::ApplyRatingMod(CR_MASTERY)      -> CombatRatings[25]
  -> Player::UpdateMastery():
         if !CanUseMastery(): Mastery = 0 and stop
         value  = GetTotalAuraModifier(SPELL_AURA_MASTERY)
         value += GetRatingBonusValue(CR_MASTERY)      # multiplier + curve 21024
         ActivePlayerData::Mastery = value
         then recalculate every owned aura whose SpellInfo has
         SPELL_ATTR8_MASTERY_AFFECTS_POINTS and a non-zero BonusCoefficient
  -> SpellEffectInfo::CalcValue:
         if SPELL_ATTR8_MASTERY_AFFECTS_POINTS and caster is a Player:
             value += ActivePlayerData::Mastery * BonusCoefficient
```

Two facts follow. First, **the spec-specific part is not special**: once
`ActivePlayerData::Mastery` exists, a mastery spell is an ordinary aura whose
effect amount happens to read one player field with a per-effect coefficient.
Second, `CanUseMastery()` is `HasSpell(ChrSpecialization.MasterySpellID[0]) ||
HasSpell(...[1])` — so the spec's mastery *spell* must be known to the player
before the rating produces any value at all. `ChrSpecialization.MasterySpellID1/2`
is the acquisition root; the gearing path stops here.

## 8. Enchantments

`Player::ApplyEnchantment(item, slot, apply, ...)` dispatches on the three
`SpellItemEnchantment.Effect[0..2]` slots. Population in this snapshot (5,342
enchantment rows, effect-slot counts):

| Effect type | name | rows | what it does |
|---:|---|---:|---|
| 5 | Stat | 4,359 | direct `ItemModType` addition (`HandleStatFlatModifier` / `ApplyRatingMod`) |
| 3 | EquipSpell | 913 | `CastSpell(this, spell, item)` on apply |
| 4 | Resistance | 477 | `UNIT_MOD_RESISTANCE_*` addition |
| 11 | BonusListId | 430 | **gem only**: item-level delta for the host item |
| 9 | ArtifactPowerBonusRankByType | 352 | legacy, no live consumer path |
| 12 | BonusListCurve | 62 | **gem only**: curve-selected item-level delta |
| 2 | Damage | 41 | weapon damage-done modifier |
| 7 | UseSpell | 39 | on-use root, `Player::CastItemUseSpell` |
| 1 | CombatSpell | 35 | chance-on-hit root, `Player::CastItemCombatSpell` |
| 8 | PrismaticSocket | 15 | adds a socket; no stats |
| 14 | *(absent from the consumer enum)* | 58 | **unsupported**, see §18 |
| 13, 10 | artifact rank picker/by-id | 11, 8 | legacy |

**Classification of current enchants.** Effect type 1 (chance-on-hit) is a
*legacy* mechanism: the highest-id row using it is 5274 (Cataclysm era). Modern
"proc enchants" are effect type 3 granting an aura whose own spell data carries
the proc (`SpellAuraOptions.ProcTypeMask`, `SpellProcsPerMinute`). That is the
same shape modern proc trinkets use (§10) and it means **gear acquisition
reaches the ordinary spell graph with no item-level proc machinery at all**.

Witness, enchant 8684 (a current gem enchant):

```
slot 0  Stat        arg 71 (Agi|Str|Int)  scaling class -7  -> 22 at level 90
slot 1  EquipSpell  arg 377360                              -> aura root
```

**Scaled amounts.** When `ScalingClass != 0`:

```
scalingClass = ScalingClassRestricted if (MinItemLevel||MaxItemLevel are set on
               the *unit*) and ScalingClassRestricted else ScalingClass
minLevel = 1 if Flags & ScaleAsAGem(0x4) else 60
maxLevel = MaxLevel or (SpellScaling row count - 1)
scalingLevel = clamp(playerLevel, minLevel, maxLevel)
amount = uint32(EffectScalingPoints[s] * SpellScaling[scalingLevel][column])
amount = max(amount, 1)
```

The column comes from `GetSpellScalingColumnForClass`: positive `ScalingClass`
is a `Classes` id, and −1/−7 both map to `Item`, −2 `Consumable`, −3..−5
`Gem1..3`, −6 `Health`, −8 `DamageReplaceStat`, −9 `DamageSecondary`, −10
`ManaConsumable`. The product is **truncated** by the `uint32` cast, not
rounded.

Gating that the port reproduces but does not evaluate (it is player/instance
state): `ConditionID`, `MinLevel > playerLevel`, `RequiredSkillID`/`Rank`, and
the prismatic-socket requirement for a colourless socket.

## 9. Gems and sockets

```
item socket:  ItemSparse.SocketType_0..2  (a SocketColor enum value)
              + ItemBonus type 6 can add sockets to empty slots
gem:          item-instance UF::SocketedGem { ItemID, Context, BonusListIDs[16] }
gem identity: ItemSparse.Gem_properties -> GemProperties.{Enchant_ID, Type}
fit test:     Item::GemsFitSockets:
                  GemProperties.Type & SocketColorToGemTypeMask[socketColor]
gem payload:  GemProperties.Enchant_ID -> SpellItemEnchantment
                  applied through Player::ApplyEnchantment on the socket slot
host ilvl:    Item::SetGem reads only enchant effect types 11 and 12 and
              accumulates BonusData::GemItemLevelBonus[socket]
```

`SocketColorToGemTypeMask` has 31 entries; index 7 is prismatic
(`RED|YELLOW|BLUE`), 22 is the Domination triple, and 24–30 are Tinker,
Primordial, Fragrance, Singing Thunder/Sea/Wind and Fiber. Current items use
socket types 7 (49 items), 24 (20), 26, 27 and 30.

Current gems (`GemProperties.Type = 14 = RED|YELLOW|BLUE`) carry no type-11/12
effect, so **they add no item level to the host item**; their whole contribution
is the enchantment's stat and equip-spell slots. Verified by a witness test.

**Socket bonus.** `ItemSparse.Socket_match_enchantment_ID` still exists and
`ItemTemplate::GetSocketBonus()` still reads it, but no gearing consumer applies
it in this checkout — `GemsFitSockets` is used for meta-gem correction only
(`CorrectMetaGemEnchants`). Recorded as an unresolved relationship.

## 10. Trinkets and item spell effects

`ItemEffect` trigger populations (61,778 rows):

| Trigger | name | rows | equipped-item route |
|---:|---|---:|---|
| 0 | OnUse | 39,678 | `Player::CastItemUseSpell` — needs item runtime policy |
| 1 | OnEquip | 8,855 | `Player::ApplyItemEquipSpell` → `CastSpell(this, spell, item)` |
| 2 | OnProc | 184 | `Player::CastItemCombatSpell` — **legacy** |
| 6 | OnLearn | 9,706 | acquisition-time only |
| 7 | OnLooted | 1,127 | acquisition-time only |
| 5 | OnPickup | 1,119 | acquisition-time only |
| 9 / 10 | forced pickup / looted | 536 / 343 | acquisition-time only |
| 4 / 3 | OnDeath / SummonedBySpell | 10 / 2 | not equipped routes |
| **15** | *(absent from the consumer enum)* | 218 | **unsupported**, see §18 |

**TriggerType 2 is dead for current content.** No item with
`ItemSparse.ExpansionID >= 10` uses it. A modern "proc trinket" is an OnEquip
effect granting a passive aura whose spell carries the proc data. Example,
`270169` *Hex Lord's Dooming Idol*:

```
ItemEffect trigger 1 -> spell 1295884
   SpellAuraOptions: ProcTypeMask_0 = 87312, ProcCategoryRecovery = 6000
ItemEffect trigger 0 -> spell 1295885   (the on-use)
```

So the classification for a future importer is:

* **becomes ordinary spell semantics on acquisition** — OnEquip effects, and by
  extension every proc that lives in `SpellAuraOptions`/`SpellProcsPerMinute`;
* **needs additional item runtime policy** — OnUse (cooldown, charges,
  `SpellCategoryID`, `ChrSpecializationID`, `PlayerConditionID` are all on the
  `ItemEffect` row, not on the spell), and the legacy OnProc path;
* **not an equipped-item route at all** — every other trigger.

`ITEM_FLAG_LEGACY` (0x100 in `Flags_0`) makes `ApplyItemEquipSpell` skip every
effect; the resolver raises this as a warning.

## 11. Tier sets and set bonuses

The hypothesis was stated as:

> equipped item instances → canonical set membership → equipped set-piece count
> → threshold reached → ordinary spell/aura granted → normal spell semantics

**Verified, with one correction.** From `AddItemsSetItem` / `RemoveItemsSetItem`
/ `UpdateItemSetAuras` (`Entities/Item/Item.cpp`):

```
setid = ItemTemplate::GetItemSet()            # ItemSparse.ItemSet, canonical
skip if ItemSet row missing (logged error)
skip if set->RequiredSkill and player's skill < RequiredSkillRank
skip if set->SetFlags & ITEM_SET_FLAG_LEGACY_INACTIVE (0x1)
heirloom guard: PlayerLevelToItemLevelCurveId + ContentTuning max level check
eff->EquippedItems.insert(item)               # a set of Item*, i.e. per instance
for each ItemSetSpell of setid:
    skip if Threshold > EquippedItems.size()
    skip if the spell was already banked
    ---- threshold is now banked ----
    skip the *cast* if ChrSpecID and != player's primary spec
    skip the *cast* if TraitSubTreeID and != CurrentCombatTraitConfigSubTreeID
    Player::ApplyEquipSpell(spellInfo, nullptr, true)
```

The correction: the spec and trait-subtree gates are evaluated **after** the
bonus is inserted into `ItemSetEffect::SetBonuses`, so changing spec re-runs
`UpdateItemSetAuras` without re-counting pieces. The port preserves that
distinction (`SetEngine.satisfied_bonuses` reports the gate values and only
filters when the caller supplies a spec).

Counting is **per equipped `Item*`**, so two distinct instances of the same
ItemID both count.

Current tier, ItemSet 2061 *Guile of the Monkey King* (5 pieces, base item level
219 each):

| Threshold | SpellID | ChrSpecID |
|---:|---:|---:|
| 2 | 1296621 | 269 (Windwalker) |
| 4 | 1296624 | 269 |
| 2 | 1296619 | 270 |
| 4 | 1296620 | 270 |
| 2 | 1296617 | 268 |
| 4 | 1296618 | 268 |

**Catalyst / conversion.** `ItemConversion` and `ItemConversionEntry` exist in
the snapshot but have **no TrinityCore consumer** (only DB2 metadata). Modern
tier pieces are therefore reachable in source only as ordinary `ItemSet` members;
the catalyst relationship is unresolved (§18).

Of the twelve current tier sets (2055–2067), **none** appear in
`JournalEncounterItem` — only ItemSet 2070 (*Bite of Zul'jan*, a 3-piece
weapon/trinket set) is journal-listed raid loot. Tier pieces nonetheless carry
the raid bonus trees and resolve to exactly the raid difficulty item levels.

## 12. Gear-granted spell acquisition topology

Every proved route by which equipment makes a spell relevant, with the exact
consumer:

```
item instance
├─ ItemXItemEffect -> ItemEffect
│    ├─ TriggerType 1  -> Player::ApplyItemEquipSpell -> ApplyEquipSpell
│    │                    -> CastSpell(this, spell, item)          [ORDINARY]
│    ├─ TriggerType 0  -> Player::CastItemUseSpell                 [ITEM POLICY]
│    └─ TriggerType 2  -> Player::CastItemCombatSpell   (legacy)   [ITEM POLICY]
├─ EnchantmentSlot -> SpellItemEnchantment
│    ├─ Effect 3  EquipSpell  -> CastSpell(this, spell, item)      [ORDINARY]
│    ├─ Effect 1  CombatSpell -> Player::CastItemCombatSpell       [ITEM POLICY]
│    ├─ Effect 7  UseSpell    -> Player::CastItemUseSpell          [ITEM POLICY]
│    └─ Effect 5/4 Stat/Resist -> stat addition, no spell
├─ SocketedGem -> ItemSparse.Gem_properties -> GemProperties.Enchant_ID
│    └─ SpellItemEnchantment (same four routes as above)
└─ ItemSparse.ItemSet -> ItemSetSpell (Threshold, ChrSpecID, TraitSubTreeID)
     └─ Player::ApplyEquipSpell(spellInfo, nullptr, true)          [ORDINARY]

triggered children: reached from the granted spell through ordinary
SpellEffect/TriggerSpell edges -- outside the gearing boundary entirely
```

Acquisition census: **40,899 distinct gear-reachable SpellIDs** across
`ItemEffect.SpellID`, `ItemSetSpell.SpellID` and the three spell-granting
enchant effect types.

Acquisition is separate from executability throughout: reaching a SpellID
through gear says nothing about whether that spell does anything.

## 13. Current-data population census

`python3 scripts/research/item_scaling.py --json census`
(committed at `docs/research/gearing-corpora/census.json`):

| population | count |
|---|---:|
| `Item` rows | 213,349 |
| `ItemSparse` rows | 175,164 |
| equippable items (`InventoryType != 0`) | 111,497 |
| stat-bearing items | 109,134 |
| items with a template socket | 16,155 |
| `ItemEffect` rows / `ItemXItemEffect` edges | 61,778 / 60,278 |
| on-use / equip / item-proc roots | 39,678 / 8,855 / 184 |
| `SpellItemEnchantment` rows | 5,342 |
| enchant stat / equip-spell / combat / use effects | 4,359 / 913 / 35 / 39 |
| `GemProperties` rows | 2,409 |
| `ItemSet` / `ItemSetSpell` rows | 1,008 / 2,971 |
| **distinct gear-reachable spell ids** | **40,899** |
| `ItemBonus` rows / distinct bonus lists | 20,224 / 10,086 |
| distinct `ItemBonus` types in use | 47 |
| `ItemBonusTree` / `ItemBonusTreeNode` / `ItemXBonusTree` | 4,786 / 19,095 / 199,940 |
| `ItemBonusListGroupEntry` / `ItemLevelSelector` / `ItemBonusListLevelDelta` | 2,473 / 1,971 / 1,801 |
| `ItemScalingConfig` / `ItemOffsetCurve` / `ItemSquishEra` | 417 / 69 / 2 |
| `Curve` / `CurvePoint` / curves with points / `GlobalCurve` | 62,533 / 171,168 / 50,234 / 38 |

**Architectural leverage read.** 31 of the 47 live `ItemBonus` types have a
consumer branch, covering 15,777 of 20,224 rows (78 %). The 4,447 unhandled rows
are overwhelmingly presentation (name subtitle, description text, toast method,
icon) plus the NYI upgrade/conversion types. Item-level authority is
concentrated: 417 `ItemScalingConfig` rows and 69 `ItemOffsetCurve` rows behind
every current item level, versus 1,801 legacy `ItemBonusListLevelDelta` rows.
That is the single highest-leverage table in the gearing surface.

## 14. Concrete witnesses

Committed as `scripts/research/tests/fixtures/witnesses.json`, with expected
values derived by `fixtures/derive_witnesses.py` (which never imports the
package under test).

| Role | ItemID | Context | Effective ilvl |
|---|---:|---|---:|
| current tier head (raid mythic / normal / LFR) | 271519 | 6 / 3 / 4 | 318 / 292 / 279 |
| current raid trinket | 270168 | 6 | 318 |
| current raid two-hand weapon | 268213 | 6 | 318 |
| current M+ pool mail head (dungeon heroic) | 251158 | 2 | 220 |
| current M+ pool mail head (end of run) | 251158 | 16 | 259 |
| current M+ pool two-hand weapon (end of run) | 251149 | 16 | 259 |
| current M+ pool trinket (end of run) | 250229 | 16 | 259 |
| current M+ pool shield (end of run) | 251150 | 16 | 259 |
| socketed PvP ring | 270575 | 0 | 298 |
| previous-tier plate legs | 249951 | 6 | 272 |
| world-quest two-hand weapon | 272274 | 42 | 292 |
| pre-squish Dragonflight chest, no squish | 210352 | 0 | 441 |
| **same item, squished at realm patch 120100** | 210352 | 0 | **74** |

Plus: gems 241143 / 240983; enchants 8157 (direct two-stat), 8684 (gem stat +
equip spell), 8689 (pure equip spell), 4067 (legacy chance-on-hit); item effects
270169 (equip proc + on-use) and 274495 (equip only); ItemSet 2061; upgrade
track group 616; curves 88583, 92181, 6243.

**Nontrivial curve-scaled item**: 210352 through the squish curve.
**Unusual scaling case**: 720 *Brawler Gloves* — see the consumer gap in §18.

## 15–17. The Python reconstruction

`scripts/research/gearing/` is a standard-library-only package; the entry point
`scripts/research/item_scaling.py` runs under both `uv run --script` and plain
`python3`. Module boundaries follow the semantic boundaries:

| module | mirrors |
|---|---|
| `enums.py` | transcribed TrinityCore enums, with file:symbol coordinates |
| `tables.py` | CSV / GameTable loading and indexing |
| `curves.py` | `DetermineCurveType`, `GetCurveValueAt`, `std::round` |
| `items.py` | `ItemTemplate`, `ObjectMgr::LoadItemTemplates` |
| `bonus.py` | `BonusData::Initialize` / `AddBonusList` / `AddBonus` |
| `tree.py` | `ItemBonusMgr::*` |
| `scaling.py` | `Item::GetItemLevel`, `GetItemStatValue`, `GetRandomPropertyPoints`, `GetArmor`/`GetDPS`/`GetDamage`, `GetIlvlStatMultiplier` |
| `ratings.py` | `GetRatingMultiplier`, `ApplyRatingDiminishing`, `UpdateMastery` boundary |
| `enchants.py` | `ApplyEnchantment`, `GetSpellScalingColumnForClass`, `Item::SetGem` |
| `effects.py` | `ItemEffect` trigger classification |
| `sets.py` | `AddItemsSetItem` / `UpdateItemSetAuras` |
| `resolver.py` | the per-item facade and variant discovery |
| `roster.py`, `content.py` | journal-tier / season / difficulty-context discovery |
| `loadout.py`, `report.py`, `census.py`, `cli.py` | input, output, counts, CLI |

Every mirrored function carries a `Mirrors:` line naming its consumer.

### CLI

```
item-scaling show <item> [--context N] [--bonus-list N]... [--gem SOCKET:ITEM]...
                        [--enchant N]... [--player-level N] [--pvp]
                        [--fixed-level N] [--min-item-level N]
                        [--min-item-level-cutoff N] [--max-item-level N]
                        [--squish-patch N] [--no-auto-bonus-lists]
item-scaling items <item>[:<ctx>][/<bonus>,...] ... [--file items.txt]
item-scaling loadout --json-file loadout.json [--explain]
item-scaling variants <item> [--all] [--explain]
item-scaling upgrades <item> [--evaluate]
item-scaling curve <curve> [x]
item-scaling gem <item> | enchant <id> [--restricted] | item-set <id>
item-scaling ratings [--amount N]
item-scaling census
item-scaling tiers | seasons [--journal-tier N] | instances --season S
item-scaling raid --season S [--all-difficulties] [--tier-only]
                  [--context N]... [--journal-instance N]
item-scaling mythic-plus --season S [--population base|end-of-run|vault|all]
                         [--keystone-level N]
item-scaling corpus --season S [--compact] [--keystone-level N]
```

Global: `--tables DIR`, `--json`; most commands take `--format table|markdown|csv`
and `--output FILE`.

A loadout entry accepts `item_id` plus any of `context`, `bonus_list_ids`,
`gems` (ids or `{socket_index, gem_item_id, bonus_list_ids}`), `enchant_ids`,
`mythic_plus_keystone_level`, `pvp_tier`, `label`/`slot`, `auto_bonus_lists`;
the loadout itself accepts `player_level`, `chr_spec_id`, `trait_sub_tree_id`,
`pvp_bonus`, `current_build_patch`. Unknown keys are rejected rather than
ignored.

### What the loadout aggregates — and deliberately does not

`resolve_loadout` returns per-item resolutions plus raw stat totals, raw combat
rating totals, satisfied item-set thresholds, and the gear-reachable spell roots.
It does **not** compute character combat stats: base stats, class/spec policy and
aura stages are not proved by this pass, and a test asserts that no
`health`/`attack_power`/`spell_power`/`crit_chance` key appears in the output.
Every payload carries that caveat in-band.

### Committed reports

Under `docs/research/gearing-corpora/`:

| file | contents |
|---|---|
| `current-raid-all-difficulties.md` | every current-raid item × every MapDifficulty-derived context |
| `current-raid-tier-pieces.md` | the ItemSet-bearing subset |
| `current-mythic-plus-base.md` | the current dungeon pool's base contexts |
| `current-mythic-plus-end-of-run-key10.md` | the same pool at keystone level 10 |
| `current-gear-corpus.json` | raid + M+ in one compact machine-readable dump |
| `census.json` | the population/coverage census |

Regenerate with the commands in §20.

## 18. Source conflicts and exceptional behaviour

Classified as either **(A) incorrect/incomplete source fact → version-scoped
input correction candidate**, or **(B) correct source plus irreducibly bespoke
consumer behaviour → external semantic package candidate**, or **(C) consumer
gap**.

### C1. ItemLevelOffsetCurve is evaluated at x = 0 for `ITEM_BONUS_SCALING_CONFIG`

`Item::GetItemLevel` always evaluates the offset curve at
`BonusData::ItemLevelOffsetItemLevel`. `ITEM_BONUS_SCALING_CONFIG` (type 51)
explicitly sets that field to `0`. 32 of the 33 `ItemScalingConfig` rows reached
through type 51 have `ItemLevel = 0`, and several of their offset curves have a
*player-level* domain:

```
$ python3 scripts/research/item_scaling.py show 720 --context 1
  base                     item_level=18
  item-level-offset-curve  curve_id=6243, x=0, raw_y=1.0, offset=3, item_level=4
  warnings
    ! ItemLevelOffsetCurve 6243 was evaluated at x=0 which is below-first-point
      of its domain [1.0, 80.0]
```

Curve 6243 is the identity over `[1, 80]`; curve 16520 (used by
`ItemScalingConfig` 143–147) runs `(0,1) → (80,74)`. Both domains are player
level, strongly suggesting the intended x is the player's level rather than 0.
**21,408 items** are reachable through type-51 bonus lists.

The port reproduces the consumer exactly and *raises a warning* rather than
guessing. Reopen condition: a TrinityCore change to `Item::GetItemLevel`, or a
`ItemScalingConfig` row with a non-zero `ItemLevel` reached through type 51.
Classification: **(C)**.

### C2. `Curve.Type` 4 and 5 have no `DetermineCurveType` case

One type-4 curve (87112) and 19 type-5 curves exist. Type-5 curve **16520 is
reachable from item scaling** through `ItemOffsetCurve` 34–38. They fall through
to the `default` branch, i.e. Linear. Classification: **(C)** — the consumer may
simply not have been updated; no evidence in the data says what 4 and 5 mean.

### C3. Six curves are non-monotonic in `OrderIndex` order

Curves 75453, 77788, 83357, 89182, 92040, 92490 each have exactly one point
whose X is lower than its predecessor's in `OrderIndex` order. The scanning
interpolators assume monotonic X, so these curves will return a wrong bracket
for some x. None is reachable from the item-scaling path in this snapshot.
Classification: **(A)** — a data defect, reported by
`Curves.non_monotonic_curves()`.

### A1. `ItemBonus` types outside the consumer enum

Types **52** (43 rows) and **53** (7 rows) are beyond `ITEM_BONUS_SCALING_CONFIG
= 51`, the end of TrinityCore's `ItemBonusType`. Types 24, 26, 29, 32 (8, 31, 2,
1 rows) fall in gaps the enum skips. 826 rows use type **0**, which the enum does
not define at all — bonus list 13698, applied to every current tier piece,
consists of a single type-0 row. Classification: **(A)** for type 0 (looks like a
placeholder/no-op row), **(C)** for 52/53 and the gap types.

### A2. `ItemEffect.TriggerType` 15 and `SpellItemEnchantment.Effect` 14

218 `ItemEffect` rows use trigger 15, all with `SpellID = 0`; 58 enchantment
effect slots use type 14. Neither value exists in the consumer's enum.
Classification: **(C)**, with the zero SpellID making a spell interpretation of
trigger 15 internally contradictory.

### B1. Hardcoded bonus-tree policy

`ApplyBonusTreeHelper` hardcodes sequence levels for tree ids 4001, 4079, 4125,
4126, 4127, 4128, 4140, and six curve ids (62951, 62952, 62954, 64388, 64389,
64395) for tree 4079's keystone mapping. Nothing in
`ItemBonusTree`/`ItemBonusTreeNode`/`ItemBonusListGroup` carries these numbers.
In this snapshot 4001/4125/4126/4127 are still referenced by 54/26/26/13 items;
**4079, 4128 and 4140 are referenced by nothing**, so the keystone→sequence curve
path is dead here. Classification: **(B)** — this is consumer policy and must be
carried as an explicit exceptional relationship, not generalised.

### B2. `GetBonusTreeIdOverride` is a no-op

`ChallengeModeItemBonusOverride` is loaded but never fires: the function's
`passedTimeEvents` is an empty local marked `TODO: configure globally`.
Classification: **(B)** — the table is real, the policy that drives it is
external.

### B3. `GetRedirectedContentTuningId` compares a bit index, not a bit

```cpp
uint32 flag = conditionalContentTuning->RedirectEnum % 32;
if (flag & redirectFlag[block])     // <- `flag`, not `1 << flag`
```

Reproduced verbatim by the port. Classification: **(C)**, suspected consumer
bug; it only matters when a caller passes non-empty redirect flags, which the
gearing path never does.

### Source-name conflicts (navigation hazards, not defects)

TrinityCore's struct field names differ from the client column names in several
tables this pipeline reads. Positional layout is identical in all cases.

| Table | TrinityCore name | client column |
|---|---|---|
| `ContentTuning` | `MinLevel` / `MaxLevel` | `MinLevelSquish` / `MaxLevelSquish` |
| `ContentTuning` | `MinLevelType` / `MaxLevelType` | `MinLevelScalingOffset` / `MaxLevelScalingOffset` |
| `ContentTuning` | `MinItemLevel` | `ILevel` |
| `SpellItemEnchantment` | `MinItemLevel` / `MaxItemLevel` | `ItemLevelMin` / `ItemLevelMax` |
| `SpellItemEnchantment` | `ConditionID` | `Condition_ID` |
| `AzeriteUnlockMapping` | `ItemLevel` / `ItemBonusListHead` / `AzeriteUnlockMappingSetID` | `MinItemLevel` / `HeadBonus` / `SetID` |
| `CurvePoint` | `Pos.X` / `Pos.Y` | `Pos_0` / `Pos_1` |
| `ItemSparse` | `FactionRelated` / `DamageDamageType` | `OppositeFactionItemID` / `DamageType` |
| `ItemLevelSelector` | `AzeriteUnlockMappingSet` | `AzeriteUnlockMappingSetID` |

### Nondeterminism in the consumer

`ItemBonusMgr` stores item→tree in an `unordered_multimap` and tree→nodes in a
`std::set<Entry const*>` ordered by pointer. Since
`*itemLevelSelectorId = ...` is last-write-wins, the result can depend on
iteration order whenever two nodes in one resolution set a selector. The port
iterates `ItemXBonusTree` and `ItemBonusTreeNode` in ascending row order, which
matches the DB2 store layout the pointers index into, and pins determinism with
a test.

### Unresolved relationships

* `ItemBonus` type 34 (`ITEM_BONUS_LIST_GROUP`, 696 rows, NYI). Payload is
  `(ItemBonusListGroupID, <unidentified id>)` — e.g. bonus list 12833 carries
  `(616, 973)` and every list in group 616 shares `973`. The second value is
  almost certainly the upgrade-track display name, but no table in this snapshot
  resolves it.
* `ItemBonusSeason` / `ItemBonusSeasonBonusListGroup` /
  `ItemBonusSeasonUpgradeCost` map upgrade-track groups to a season and a track
  index (1..7). **No TrinityCore consumer.**
* `ItemGroupIlvlScalingEntry`, `ItemLevelWatermark`, `ItemConversion`,
  `ItemConversionEntry`, `ItemRecraft`, `ItemReforge`, `ItemBonusSequenceSpell`.
  All present, all unconsumed.
* `ItemSparse.Socket_match_enchantment_ID` (socket bonus) — read by an accessor,
  applied by nothing.
* `BonusData::RequiredLevelCurve` — set by `ItemBonus` type 27 (1,076 rows),
  never evaluated.

## 19. Testing strategy and coverage

`uv run --with pytest --with hypothesis python -m pytest tests/ -q` from
`scripts/research/`.

| file | tests | what it proves |
|---|---:|---|
| `test_curves.py` | 71 | every interpolation mode against an independently transcribed reference; `DetermineCurveType` across type × point-count; clamping at both ends of each mode; `OrderIndex` ordering; `std::round` vs Python `round` |
| `test_bonus.py` | 39 | every handled `ItemBonusType`; additive vs priority vs first-wins; hostile-ordering cases; duplicate application; recursion guard |
| `test_tree.py` | 42 | context match / mismatch / inversion / creation-context group; slot mask; caster and CC-trinket flags; every child kind; keystone gates; each hardcoded tree id; recursion guard; real tier-piece difficulty levels |
| `test_scaling.py` | 87 | each item-level stage in isolation and in order; content-tuning clamp; squish ordering; unit min/max with cutoff; global clamp; every RandPropPoints family and quality column; the full stat formula; every armor subclass/location; every damage-table selection |
| `test_ratings.py` | 30 | `CombatRatings` column mapping; multiplier fallbacks; per-rating diminishing curve; resilience's extra stage; fan-out of one ItemModType to several rating slots; the mastery hand-off boundary |
| `test_enchants_sets_effects.py` | 36 | enchant effect classification; SpellScaling column map and truncation; level clamping and the gem flag; gem chain and socket fit; effect trigger classification and ordering; set membership, thresholds, per-instance counting, spec/subtree gates |
| `test_tables_items.py` | 21 | table/GameTable loading, indexing, fail-closed paths; `ItemTemplate` accessors; the real snapshot's column names and `ID == ItemLevel` invariants |
| `test_roster.py` | 18 | journal tiers, season windows and their TimeEvent gates; difficulty→context for raid and dungeon; picker alternatives; loot provenance; population bucketing; keystone effect |
| `test_loadout_cli.py` | 28 | loadout/spec parsing and its fail-closed rejections; aggregation; every CLI command including the fail-closed ones |
| `test_witnesses.py` | 40 | the committed corpus of real items, curves, sets, gems, enchants, effects and the upgrade track |
| `test_properties.py` | 19 | hypothesis properties over the curve engine plus snapshot invariants |
| `test_differential.py` | 80 | comparison against compiled TrinityCore expressions |
| **total** | **511** | |

Deliberately **absent**: any property asserting that item level or stats increase
monotonically with context or upgrade step. Nothing in the source proves that,
and several items disprove it.

### Differential verification

`scripts/research/tools/tc_probe/` is a small C++ probe. `extract.py` pulls
three function bodies **verbatim** out of the sibling TrinityCore checkout —
`DB2Manager::GetCurveValueAt(CurveInterpolationMode, span, float)`,
`DetermineCurveType`, and `ItemTemplate::GetDamage` — into a generated include;
`probe.cpp` supplies only the scaffolding those bodies need and exposes them over
a line protocol. Build with `make -C scripts/research/tools/tc_probe`.

This is stronger than Python-vs-Python in two ways: a TrinityCore change breaks
the build or the comparison rather than diverging silently, and the arithmetic
runs in C++ `float`, which is what TrinityCore actually uses.

**Result**: all 80 differential assertions pass, including the whole domain of
the Midnight squish curve sampled every 7 item levels.

**One real divergence is documented rather than hidden.** `DBCPosition2D` holds
`float`; the port computes in `double`. For curve 92181 at x = 441:

```
C++   74.323143005371094
Python 74.32314410480349
```

Both round to 74. The tests assert agreement on the *rounded* result (what every
consumer uses) and separately bound the raw divergence to float precision
(`rel_tol = 1e-6`). A future production implementation should use `f32` for curve
evaluation if bit-exactness with the server is ever required.

Not attempted: running TrinityCore's own `ItemBonusMgr`/`Item` in-process. That
needs the full DB2 loader, `ObjectMgr`, `World` config and a realm, which is far
beyond a research probe. The bonus-tree traversal is therefore verified against
the source rows by hand-audited traces, not differentially.

## 20. Current raid and Mythic+ roster discovery

### What "current" means in this snapshot

`JournalTier` contains a rolling tier with the sentinel `Expansion = 9000`
(ID 505, named *Current Season*), alongside per-expansion tiers (Midnight is
1200). `JournalTierXInstance` lists that tier's instances, each gated by an
`AvailabilityCondition`:

```
JournalTierXInstance.AvailabilityCondition
  -> PlayerCondition.ModifierTreeID
  -> ModifierTree (Operator = All)
       child (Operator = SingleTrue)  Type 289 HasTimeEventPassed  Asset = <start>
       child (Operator = SingleFalse) Type 289 HasTimeEventPassed  Asset = <end>
```

so a season is a `[start, end)` window over `TimeEvent` ids:

| AvailabilityCondition | ModifierTree | start TimeEvent | end TimeEvent | instances |
|---:|---:|---:|---:|---:|
| 156363 | 445192 | 1552 | 2771 | 11 |
| 149388 | 425759 | 1525 | 1550 | 4 |
| 0 | — | — | — | 1 |

**`TimeEvent` is not in this snapshot**, and TrinityCore only hardcodes
timestamps for seven old ids in `CriteriaHandler.cpp`
(`ModifierTreeType::HasTimeEventPassed`), defaulting unknown ids to "now" —
which makes a season's start *and* its end both evaluate as passed, so both
windows evaluate false. **Season activation is therefore underivable from the
data and from the direct consumer.**

Consequence, enforced in the CLI: `raid`, `mythic-plus`, `instances` and
`corpus` all require `--season <AvailabilityCondition>`. A
`--season latest-in-source` convenience exists; it orders windows by their start
`TimeEvent` id and prints, to stderr, that this is a heuristic over source rows
and not proof that the season is live. A test asserts that `TimeEvent` is absent,
so if a future snapshot adds it this decision gets revisited.

### The five questions

**1. Which items are encounter loot from the current raid?**
`JournalTierXInstance` → `JournalInstance` (filtered to `Map.InstanceType == 2`)
→ `JournalEncounter` → `JournalEncounterItem`. For window 156363 that is two
raids — *The Tidebound Grotto* (map 2987, 13 items) and *The Venomous Abyss*
(map 3004, 8 encounters, 118 items) — **131 distinct items**.

Authority caveat, recorded on every roster: TrinityCore loads `JournalInstance`,
`JournalEncounter` and `JournalTier` (for hyperlink validation and LFG) but
**never loads `JournalEncounterItem`**. Server loot lives in Trinity's own world
database, which is not part of this snapshot. So this is the client's encounter
journal — a real source relationship, navigation-grade for loot, *not* a
loot-table authority.

**2. Which of those are tier-set pieces?** `--tier-only` filters on a non-zero
`ItemSparse.ItemSet`. In the current raid that yields exactly three items, all in
ItemSet 2070 (*Bite of Zul'jan*). The twelve class tier sets (2055–2067) are
**not** journal-listed raid loot — they are catalyst/vault items, and the
catalyst relationship has no consumer (§18).

**3. Which contexts correspond to LFR/Normal/Heroic/Mythic?** Derived, not
assumed, from `MapDifficulty` + `Difficulty` through
`ItemBonusMgr::GetContextForPlayer` — see the table in §3.

**4. Can each variant's bonus lists and effective item level be derived?** Yes,
through the machinery already implemented: the resolved `ItemContext` is fed
straight into `GetBonusListsForItem`. `content.resolve_roster` only resolves an
item in a context that the item's *own* reachable trees expose, so no variant is
invented.

**5. What establishes that this raid is "current"?** Nothing in the snapshot.
See above.

### Mythic+

Same discovery, filtered to `Map.InstanceType == 1`. Window 156363 yields nine
journal instances: *Altar of Fangs*, *Den of Nalorakk*, *Murder Row*, *The
Blinding Vale*, *Voidscar Arena*, plus three legacy dungeons carrying
`JournalInstance.Flags = 1` (*Kings' Rest*, *Ruby Life Pools*, *Temple of
Sethraliss*), plus the aggregator instance *Keystone Dungeons* (map 3029, no
loot of its own). **247 distinct items.**

Every one of those maps has `DifficultyID 8` (*Mythic Keystone*) whose
`Difficulty.ItemContext` is `16 = MythicPlus_End_of_Run`. The three populations
are kept separate:

| population | contexts |
|---|---|
| `base` | the dungeon's own Normal / Heroic / Mythic / level-up contexts |
| `end-of-run` | 16, 33, 34, 87 |
| `vault` | 35, 72, 73 |

`--keystone-level N` feeds `params.MythicPlusKeystoneLevel`, which gates
`ItemBonusTreeNode.Min`/`MaxMythicPlusLevel`. Those bounds are live in this
snapshot (trees 5847, 5923, 6020, 6175 and others carry them, up to level 14);
tree 4079's keystone→sequence *curve* path is not reachable from any item here.

### Regenerating the corpora

```bash
cd /home/dev/pallet/wowlab-data
P="python3 scripts/research/item_scaling.py"

$P tiers
$P seasons                                   # lists the AvailabilityCondition windows
$P instances --season 156363

$P raid --season 156363 --all-difficulties --format markdown \
   --output docs/research/gearing-corpora/current-raid-all-difficulties.md
$P raid --season 156363 --all-difficulties --tier-only --format markdown \
   --output docs/research/gearing-corpora/current-raid-tier-pieces.md
$P mythic-plus --season 156363 --population base --format markdown \
   --output docs/research/gearing-corpora/current-mythic-plus-base.md
$P mythic-plus --season 156363 --population end-of-run --keystone-level 10 \
   --format markdown \
   --output docs/research/gearing-corpora/current-mythic-plus-end-of-run-key10.md
$P corpus --season 156363 --compact \
   --output docs/research/gearing-corpora/current-gear-corpus.json
$P --json census > docs/research/gearing-corpora/census.json
```

Current output: raid **131 items / 368 distinct variants**, Mythic+ **247 items /
1,230 distinct variants**.

## 21. Future preparation boundary

Only now, after the archaeology, the smallest evidence-backed boundary:

```
equipped item-instance facts
  (ItemID, bonus list ids, gems, enchant ids, upgrade step, timewalker level,
   azerite level) + player level + realm patch + PvP/unit ilvl constraints
        ↓   [ gearing preparation — everything in §§2-11 ]
resolved per-item facts
  (effective item level, rounded stat contributions keyed by ItemModType,
   armor, weapon min/max/speed/AP, socket colours, item-set id)
        ↓   [ aggregation ]
character-facing gear facts
  + ordinary granted spell/effect roots (SpellID + acquisition route)
  + explicit exceptional relationships (on-use policy, legacy proc roots)
        ↓
existing Core preparation / compiler
```

**What can be discarded at each stage.**

After bonus-list resolution: the `ItemBonusTree`/`ItemBonusTreeNode` graph, the
`ItemContext` itself, `ItemLevelSelector*`, `ItemBonusListLevelDelta`,
`ItemCreationContextGroup`. They exist only to *choose* bonus lists.

After `BonusData` is folded: the bonus lists themselves, `ItemScalingConfig`,
`ItemOffsetCurve`, `ItemSquishEra` and every scaling curve. They exist only to
produce `(effective item level, quality, stat allocations, socket colours,
effect list)`.

After stat generation: `RandPropPoints`, `ItemSocketCostPerLevel`,
`CombatRatingsMultByILvl`, `StaminaMultByILvl`, `ItemArmor*`, `ItemDamage*`, the
item level, the inventory type and the quality. They exist only to produce
rounded amounts.

**What must survive.** `ItemModType` must survive to the character stage,
because the combined identities (71–74) cannot be resolved without class/spec
context. `ItemSet` membership must survive to the aggregation stage, because
thresholds are counted across items. `ChrSpecializationID` and `TraitSubTreeID`
on `ItemSetSpell` and `ItemEffect` must survive as *gates on the granted spell*.
The on-use policy fields (`CoolDownMSec`, `CategoryCoolDownMSec`,
`SpellCategoryID`, `Charges`) must survive with the on-use root, because they
live on the `ItemEffect` row and not on the spell.

**Can `CombatProgram`/`CombatState` stay ignorant of ItemIDs, bonus lists,
upgrade tracks, item-level selectors, gems, enchants, tier-piece counts and
scaling curves?**

Yes for all of them, on this evidence — with one qualification each:

| fact | safe to drop? | why |
|---|---|---|
| ItemID | yes | nothing downstream of stat/spell resolution reads it; only diagnostics want it |
| bonus lists | yes | fully consumed by `BonusData` |
| upgrade track / step | yes | it is just another bonus list |
| item-level selectors | yes | fully consumed during selection |
| gems | yes, **after** both of their contributions are folded: the enchantment's stats/spells *and* `GemItemLevelBonus` on the host item |
| enchants | yes, after stats are folded and equip/use/proc spell roots are extracted |
| tier-piece counts | yes, after thresholds are evaluated — but the *count* must be computed at aggregation, not per item |
| stat-scaling curves | yes | fully consumed by `Item::GetItemLevel` |
| **item level** | **no, not entirely** | `ChallengeModeItemBonusOverride`, PvP scaling and `UnitData::Min/MaxItemLevel` are runtime-variable inputs; if any of those is ever modelled, the item level must be recomputable, which means keeping the instance facts |

Provenance that must survive *validation* even though runtime never reads it:
the `(ItemID, bonus lists, context)` triple, so a resolved stat block can be
re-derived and diffed after a data refresh. The compact corpus JSON is exactly
that shape.

## 22. Prioritised questions before production gear import

1. **What is the intended x for `ItemLevelOffsetCurve` under
   `ITEM_BONUS_SCALING_CONFIG`?** 21,408 items resolve to a near-1 item level
   today. Highest-impact open question. (§18 C1)
2. **Which season is live?** Requires an external fact — `TimeEvent` timestamps
   or a caller-supplied season. Everything downstream of "current roster"
   depends on it. (§20)
3. **How does the catalyst reach a tier set?** `ItemConversion`/
   `ItemConversionEntry` are unconsumed, and the current tier sets are not
   journal loot. Without this, a tier-set importer must be handed the ItemIDs.
4. **What do `Curve.Type` 4 and 5 mean?** One type-5 curve is reachable from
   item scaling. (§18 C2)
5. **Is the socket bonus (`Socket_match_enchantment_ID`) live?** No consumer
   applies it; if it is live in retail, the port is missing a stat source.
6. **Is `float` precision required?** Decide before a production implementation
   picks `f32` or `f64` for curve evaluation. (§19)
7. **What resolves `ItemBonus` type 34's second value?** Needed only for
   upgrade-track display, but it is the last unexplained field in the
   item-level path.
8. **Do types 52/53 and trigger 15 matter?** 50 and 218 rows respectively, all
   outside the consumer's enums.

## 23. Reproducibility

| item | value |
|---|---|
| data snapshot | Wago `12.1.0.69497` (`product=wow`), `changes/metadata/12.1.0.69497.json` |
| wowlab-data commit | `c738030f59de193ea6536be4b5852fbbd2f07b83` |
| TrinityCore commit | `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` (master) |
| TrinityCore expansion | `CURRENT_EXPANSION = EXPANSION_MIDNIGHT (11)`, max level 90 |
| Python | 3.12.3, standard library only for the package |
| test dependencies | `pytest==8.4.2`, `hypothesis==6.140.3` |
| probe toolchain | `g++ 13.3.0`, `-std=c++20 -O0 -ffp-contract=off` |
| exploration | DuckDB CLI 1.5.5 (`~/.duckdb/cli/latest/duckdb`) over `data/tables/*.csv` |

### Layout verification

TrinityCore's `DB2LoadInfo` field order was compared against every CSV header
this pipeline reads. All match positionally; only the names in the §18 table
differ. `ItemSparse` is 103 fields, exact positional match.

### Source table hashes (sha256)

```
Item.csv                     819228ed78803eda2a4806bfb71b1cfb94c86524e0cfaa00df9597da3a1369c7
ItemSparse.csv               fd952738df48e8ff35ddb36f84348b2f8e6f0c03c108c32b224d3ba275b9e89a
ItemBonus.csv                41b13220f89ffe3c89c4ee26c9d7460d947fe30fc56b0936cfa21681566f5d63
ItemBonusTree.csv            a6b04b8fd8f3a995a04b2f7fa82e3fb2f0bab16310bb541e1f84b05e6630ba6b
ItemBonusTreeNode.csv        5eaa5a48cb4c1cc3bee94cd8b150ddb3a4b785037aa07c95081ea3b87a10185d
ItemXBonusTree.csv           6642435c5afa9a66ee5f11feecae31b3d209cf676dbaec3b0d228b0b6ee0fd85
ItemBonusListGroupEntry.csv  f07be2bfe219aa9ebd0d2fcfffe716678d951cc8b7b9fd01d7bed5a3cb5ec31c
ItemBonusListLevelDelta.csv  c8b0cbb597e85f83e615d1e002caff881a29d9e37f5e734a72bb2bffbb6cb9ce
ItemLevelSelector.csv        78b5af8366ad718875eef5bcacfdf7c149967fdcfabbe41b8725e4810e1ca41e
ItemScalingConfig.csv        ba9cfda12ee7db88596ac1782a0cea2f49b35f6b591153c66523fad980323d57
ItemOffsetCurve.csv          6b3be81d45fedb1ea03f58f0d71b9fd286e30e2e9d80b77e0e81568eca409e7a
ItemSquishEra.csv            8ff478e3635e171a85a08c3824297b6a6ca7050df1d1d7369763cb86dd0c54d7
Curve.csv                    b03672f8feb93cd76abb7e7acf066adc25f80ee4b0e8f146319f8c5639faa819
CurvePoint.csv               3925edf8c03182ef93718630cd16fba064a46b8d68a2adc8f157325c178396e9
RandPropPoints.csv           970a37886081b86dbc4a460481e2e1325f7f2bf6b5f39a557de2a40a5ebfd30a
GlobalCurve.csv              5b9bc6b0ec505797aa68cd08b232256a9ba4416b2ffdaf97fcd6708434c0de89
ItemEffect.csv               0c67af8e153238e45f20ac4f8a576966b1655bef8179ac8e77b47c61a7be6efb
ItemXItemEffect.csv          41d812c51bd8d31b10712a83bddc83832072426e6c266400c9ad0f956dba512e
SpellItemEnchantment.csv     109ec309bc966e421cc79a0d425bef58044cb4fef0061fbcccd1c7516b490d18
GemProperties.csv            8985ac1145749b62d4933903bee5755b9965066db5a78506630ed83f504fcb9b
ItemSet.csv                  0ae48ff20e117ba0d4bc30d6934819b752785cca0d61cbb0b5812ed99bd19bc6
ItemSetSpell.csv             5f81a5ee83788a17314f610de58654363a1a285eef7843ab4a6da600a078903e
JournalTier.csv              472b131ea8f5d13a0f59f86f36779082da700c2642c7c54faea50517136fad3f
JournalTierXInstance.csv     2d30245b78cc3bfb10244e19b199090512d41c9c59760f8de285d465385462c7
JournalInstance.csv          8970a8f9aa62d327bfe583f5aafc292008c2c02feef5f972dc462dba0730491f
JournalEncounter.csv         0012e24a1726e1b5ddd5f95c2749bca727b410d5ae877f26bd3b702a9c0d251d
JournalEncounterItem.csv     8462f7b7ad37e2546a83801388464893666abe54ee68dfcbf42da82168adb8d1
MapDifficulty.csv            a956f1ed19d9cfbb952191951123d8a89f65bbe4ec1c766c591511d00ccf2b9b
Difficulty.csv               5b5909ea3319a9596ba4472256ef8901e24c67d2b10638259bfabc652268a23e
Map.csv                      52bef5ce091465846a6871672d6757340b82fd34e0e034ff82c494f07e28c48e
ItemContextPickerEntry.csv   d16b37c281a2a16a2c5187c959558f6f5f7a9aff8fc7d452397ce48bca524c08
ItemCreationContext.csv      e168f67ba1e6e458f078876ada06bfe9f96ca111017d376f694426155a4b3c66
ContentTuning.csv            1616849da9bc55e7f083751acc0f28cfefbc925ab0d4e665335d6fb73a516ec8
ChrSpecialization.csv        4e67c0772fca8cfc857e8a7fc9952538c35018e91f2734284f71089087d60c69
CombatRatings.txt            e2f50545113411e6f9455cc2c5217bbd516b5c5288bb27002917fc6ca66fd7a3
CombatRatingsMultByILvl.txt  46cd27dd8e255dec3d9c44f3cfaa099b21409fb4e2cb17be2dd13bdc482bbb95
StaminaMultByILvl.txt        58f6992af5a14ada4b78d29479eae36ad699e882f9197b8e8c9b24b6704d018d
ItemSocketCostPerLevel.txt   8cb8cc59d68599d89ac7e69f3204709168e6b057ea4d5e618b0f449c2e65c3d1
SpellScaling.txt             (see data/tables/SpellScaling.txt)
```

### Direct consumer coordinates

```
src/server/game/Entities/Item/ItemBonusMgr.cpp
    Load, GetContextForPlayer, GetItemBonuses, GetItemBonusListForItemLevelDelta,
    CanApplyBonusTreeToItem, GetBonusTreeIdOverride, ApplyBonusTreeHelper,
    GetAzeriteUnlockBonusList, GetBonusListsForItem
src/server/game/Entities/Item/Item.cpp
    Item::GetItemLevel (both overloads), Item::GetItemStatValue, Item::SetGem,
    Item::GemsFitSockets, BonusData::Initialize/AddBonusList/AddBonus,
    AddItemsSetItem, RemoveItemsSetItem, UpdateItemSetAuras
src/server/game/Entities/Item/ItemTemplate.cpp
    SocketColorToGemTypeMask, ItemTemplate::GetArmor/GetDPS/GetDamage
src/server/game/Entities/Item/ItemEnchantmentMgr.cpp
    GetRandomPropertyPoints
src/server/game/Entities/Player/Player.cpp
    _ApplyItemMods, _ApplyItemBonuses, _ApplyWeaponDamage, ApplyItemEquipSpell,
    ApplyEquipSpell, ApplyEnchantment, GetRatingMultiplier, GetRatingBonusValue,
    ApplyRatingDiminishing, ApplyRatingMod, UpdateRating, CanUseMastery,
    GetGameTableColumnForCombatRating
src/server/game/Entities/Unit/StatSystem.cpp
    Player::UpdateMastery
src/server/game/DataStores/DB2Stores.cpp
    DB2Manager::LoadStores (curve point assembly), DetermineCurveType,
    GetCurveValueAt, GetCurveXAxisRange, GetContentTuningData,
    GetRedirectedContentTuningId
src/server/game/DataStores/GameTables.h / .cpp
    GameTable<T>::GetRow, GetIlvlStatMultiplier, GetSpellScalingColumnForClass
src/server/game/Globals/ObjectMgr.cpp
    LoadItemTemplates (ItemXItemEffect assembly)
src/server/game/Spells/SpellInfo.cpp
    SpellEffectInfo::CalcValue (SPELL_ATTR8_MASTERY_AFFECTS_POINTS)
src/server/game/Achievements/CriteriaHandler.cpp
    ModifierTreeType::HasTimeEventPassed
src/server/shared/Realm/ClientBuildInfo.cpp
    GetMinorMajorBugfixVersionForBuild
```

### Commands

```bash
# package + CLI
python3 scripts/research/item_scaling.py --help
python3 scripts/research/item_scaling.py show 271519 --context 6
python3 scripts/research/item_scaling.py variants 271519
python3 scripts/research/item_scaling.py upgrades 271519 --evaluate
python3 scripts/research/item_scaling.py curve 92181 441

# tests
cd scripts/research
uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/ -q

# differential probe
make -C scripts/research/tools/tc_probe
uv run --with pytest==8.4.2 python -m pytest tests/test_differential.py -q

# witness regeneration (only after a data refresh; re-read the diff)
python3 scripts/research/tests/fixtures/derive_witnesses.py \
        scripts/research/tests/fixtures/witness_inputs.json

# ad-hoc exploration
~/.duckdb/cli/latest/duckdb -c "
  SELECT * FROM read_csv('data/tables/ItemScalingConfig.csv', header=true)
  WHERE ID IN (310, 318)"
```

### External documentation

None used as semantic authority. Item and set names throughout are navigation
aids taken from the snapshot's own `*_lang` columns.
