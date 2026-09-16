# Character stat pipeline archaeology

## Scope and result

Companion to [`gearing-pipeline-archaeology.md`](gearing-pipeline-archaeology.md).
That audit explains how an equipped item becomes stat *contributions*; this one
answers:

> Given race, class, specialization and player level, how does TrinityCore
> construct the Player's authoritative combat-relevant base and derived stats,
> before and after ordinary equipment stat contributions?

It ships an executable reconstruction (`scripts/research/charstats/`, driven by
`scripts/research/character_stats.py`) and a research-only end-to-end proof
(`scripts/research/tools/gear_to_character.py`) that runs a real gear loadout
through the gearing resolver and hands the result to the character resolver.

Evidence is the Wago snapshot **`12.1.0.69497`** in `data/tables/` at
`c738030f59de193ea6536be4b5852fbbd2f07b83`, and TrinityCore
**`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`**.

**Headline results.**

1. **Base primary stats are not in the snapshot.** They come from TrinityCore's
   *world database* tables `player_classlevelstats` (class, level) and
   `player_racestats` (race), and the TrinityCore repository ships only their
   `CREATE TABLE` statements. Every command that needs them requires
   `--base-stats FILE`.
2. **A Player has no base health.** `InitStatsForLevel` calls
   `SetCreateHealth(0)`; max health is entirely `stamina * HpPerSta[level]` plus
   item/aura modifiers. At level 90 the ratio is **20**.
3. **Nothing selects one stat for a combined `ItemModType`.**
   `Player::_ApplyItemBonuses` applies the same rounded value to *every* stat an
   `AGI_STR_INT`/`AGI_STR`/`AGI_INT`/`STR_INT` item names. What makes one matter
   is downstream and coefficient-driven.
4. **Armour specialization is fully source-derivable**, including its 5 %: a
   `SpecializationSpells` row whose spell has
   `SPELL_ATTR8_REQUIRES_EQUIPPED_INV_TYPES`, an armour `SpellEquippedItems`
   gate, and a `SPELL_AURA_MOD_TOTAL_STAT_PERCENTAGE` effect whose
   `EffectMiscValue_1` is a stat bitmask. 22 such passives exist.
5. **Mastery's spec-specific half is not special.** Once
   `ActivePlayerData::Mastery` exists, each mastery effect amount is
   `base_points + Mastery * EffectBonusCoefficient` and then behaves as whatever
   aura type it is. Classifying all 40 specs by that aura type gives **18
   ordinary / 17 script-dependent / 5 blocked**.

---

## 1. Base character identity

`Player::InitStatsForLevel` (`src/server/game/Entities/Player/Player.cpp`) is
the single place base values are established. What each fact comes from:

| Fact | Source | In the snapshot? |
|---|---|---|
| race, class | `ChrRaces` / `ChrClasses` | yes |
| specialization | `ChrSpecialization` | yes |
| level | character state | n/a |
| **base primary stats** | `player_classlevelstats` + `player_racestats` (**world DB**) | **no** |
| base stamina | same as above (stat index 2) | **no** |
| base health | literal `SetCreateHealth(0)` | n/a — it is zero |
| base mana | `BaseMp.txt` via `GetGameTableColumnForClass` | yes |
| base armor | `int32(createStats[AGILITY] * 2)` | derived |
| base attack power | `SetAttackPower(0)`, then `UpdateAttackPowerAndDamage` | derived |
| base spell power | `m_baseSpellPower = 0` | n/a |
| base crit | literal `5.0f` (melee/offhand/ranged/spell) | n/a |
| base haste | `SetModCastingSpeed/SpellHaste/Haste/RangedHaste/HasteRegen(1.0f)` | n/a |
| base mastery | `0` unless `CanUseMastery()` | n/a |
| base versatility | 0 | n/a |
| base block | literal `5.0f` when `CanBlock()` | n/a |
| base hit | literal `7.5f` | n/a |
| base expertise | literal `7.5f` | n/a |

### The base-stat boundary

```cpp
// ObjectMgr::LoadPlayerLevelInfo
QueryResult raceStatsResult = WorldDatabase.Query(
    "SELECT race, str, agi, sta, inte, spi FROM player_racestats");
QueryResult result = WorldDatabase.Query(
    "SELECT class, level, str, agi, sta, inte, spi FROM player_classlevelstats");
...
levelInfo.stats[i] = fields[i + 2].GetInt32() + raceStats.StatModifier[i];
```

Three things follow:

* base stats are keyed by **(class, level)**, not by race — race contributes a
  flat per-race modifier added to every class's row;
* `sql/base/dev/world_database.sql` contains the schema for both tables and
  **zero** `INSERT` statements, so the values are not in the TrinityCore
  checkout either;
* above `CONFIG_MAX_PLAYER_LEVEL`, `BuildPlayerLevelInfo` extrapolates with a
  hardcoded per-class `switch` full of level thresholds from Classic. That is
  consumer policy and is not reproduced.

The tool therefore raises `MissingBaseStats` rather than inventing values. Tests
use `scripts/research/tests/fixtures/synthetic_base_stats.json`, whose every
field is loudly labelled synthetic.

Base mana *is* derivable:

```
ObjectMgr::GetPlayerClassLevelInfo:
    baseMana = uint32(GetGameTableColumnForClass(sBaseMPGameTable.GetRow(level), class))
```

`BaseMp.txt`'s column order after the `Level` key is
`Rogue, Druid, Hunter, Mage, Paladin, Priest, Shaman, Warlock, Warrior,
DeathKnight, Monk, DemonHunter, Evoker, Adventurer, Traveler` — *not* the
`Classes` enum order. `SpellScaling.txt` uses the same order for its first 15
columns. A test pins both headers against the transcribed mapping.

## 2. Stat update dependency graph

`Player::UpdateStats(stat)` is the fan-out point:

```
UpdateStats(stat):
    SetStat(stat, int32(GetTotalStatValue(stat)))
    stat == AGILITY   -> UpdateAllCritPercentages, UpdateDodgePercentage
    stat == STAMINA   -> UpdateMaxHealth
    stat == INTELLECT -> UpdateSpellCritChance
    stat == STRENGTH  -> UpdateAttackPowerAndDamage(false)
    stat == AGILITY   -> UpdateAttackPowerAndDamage(false) and (true)
    always            -> UpdateArmor, UpdateSpellDamageAndHealingBonus,
                         UpdatePowerRegen(POWER_MANA)
    STAMINA|INTELLECT|STRENGTH also propagate to the pet
```

and `UpdateArmor` itself ends with `UpdateAttackPowerAndDamage()`, because
`SPELL_AURA_MOD_ATTACK_POWER_OF_ARMOR` exists. So armour and attack power are
mutually reachable; the order in `UpdateAllStats` is what breaks the cycle:

```
UpdateAllStats:
    for each stat: SetStat(int32(GetTotalStatValue(stat)))
    UpdateArmor                       (which calls UpdateAttackPowerAndDamage())
    UpdateAttackPowerAndDamage(true)  ranged
    UpdateMaxHealth
    for each power: UpdateMaxPower
    UpdateAllRatings                  -> per-rating fan-out
    UpdateAllCritPercentages, UpdateSpellCritChance
    UpdateBlockPercentage, UpdateParryPercentage, UpdateDodgePercentage
    UpdateSpellDamageAndHealingBonus
    UpdatePowerRegen x4
    UpdateExpertise(BASE_ATTACK), UpdateExpertise(OFF_ATTACK)
    RecalculateRating(CR_ARMOR_PENETRATION)
    UpdateAllResistances
```

Classification of the dependencies:

| Dependency | Kind |
|---|---|
| stat -> `GetTotalStatValue` modifier stages | generic `Unit` |
| stamina -> health via `HpPerSta` | generic `Player` |
| STR/AGI -> attack power | **class-specific** (`ChrClasses` coefficients) |
| AGI -> ranged attack power | class-specific |
| INT -> spell power | **spec-specific** (`ChrSpecialization.PrimaryStatPriority`) |
| AGI -> crit, dodge | generic (crit has no agility term any more; the call exists only to refresh the rating-derived value) |
| rating -> percentage | generic, level-keyed |
| rating -> diminishing | generic, `GlobalCurve` |
| mastery -> effect amounts | spell/aura-driven |
| armour specialization | spell/aura-driven, equipment-gated |
| shapeshift AP-from-strength, `OVERRIDE_*_BY_*_PCT` | spell/aura-driven |

### `Unit::GetTotalStatValue`

```
value  = BASE_VALUE * max(BASE_PCT_EXCLUDE_CREATE, -100%)
value += createStat                      # <- after the exclude-create percentage
value *= BASE_PCT
value += TOTAL_VALUE
value *= TOTAL_PCT
SetStat(stat, int32(value))              # truncating cast, not std::round
```

Item stats land in `BASE_VALUE` (`ITEM_MOD_STRENGTH` etc. use `BASE_VALUE`)
while enchantment stats land in `TOTAL_VALUE`
(`Player::ApplyEnchantment` uses `TOTAL_VALUE` for
agility/strength/intellect) — a real asymmetry between the two gear paths, and
one that changes the result whenever a `BASE_PCT` aura is active.

Armour specialization writes `TOTAL_PCT` on `UNIT_MOD_STAT_*`, so it multiplies
everything including enchantments.

## 3. Combined primary-stat ItemModTypes

The direct consumer is blunt:

```cpp
case ITEM_MOD_AGI_STR_INT:
    HandleStatFlatModifier(UNIT_MOD_STAT_AGILITY,   BASE_VALUE, float(val), apply);
    HandleStatFlatModifier(UNIT_MOD_STAT_STRENGTH,  BASE_VALUE, float(val), apply);
    HandleStatFlatModifier(UNIT_MOD_STAT_INTELLECT, BASE_VALUE, float(val), apply);
    UpdateStatBuffMod(STAT_AGILITY); UpdateStatBuffMod(STAT_STRENGTH);
    UpdateStatBuffMod(STAT_INTELLECT);
```

**There is no selection.** The full rounded value goes to every named stat.
Selection does not depend on class, spec, form, equipment or aura state — it does
not happen at all. What differs downstream:

| Consumer | Selector | Source |
|---|---|---|
| melee attack power | `ChrClasses.AttackPowerPerStrength`, `AttackPowerPerAgility` | class |
| ranged attack power | `ChrClasses.RangedAttackPowerPerAgility` | class |
| spell power from intellect | `Player::GetPrimaryStat() == STAT_INTELLECT` | spec (`PrimaryStatPriority`) |
| armour-from-stat aura with miscValue −2 | `Player::GetPrimaryStat()` | spec |
| armour specialization | the granted spell's `EffectMiscValue_1` bitmask | spec |

`Player::GetPrimaryStat()`:

```cpp
priority = spec ? spec->PrimaryStatPriority : ChrClasses[class].PrimaryStatPriority;
if (priority >= 4) return STAT_STRENGTH;
if (priority >= 2) return STAT_AGILITY;
return STAT_INTELLECT;
```

Current class coefficients (`ChrClasses`, this snapshot):

| Class | PrimaryStatPriority | AP/Str | AP/Agi | RangedAP/Agi |
|---|---:|---:|---:|---:|
| Warrior | 5 | 1 | 0 | 0 |
| Paladin | 4 | 1 | 0 | 0 |
| Hunter | 2 | 0 | 1 | 1 |
| Rogue | 3 | 0 | 1 | 1 |
| Priest | 0 | 0 | 0 | 0 |
| Death Knight | 5 | 1 | 0 | 0 |
| Shaman | 0 | 0 | 1 | 0 |
| Mage | 0 | 0 | 0 | 0 |
| Warlock | 0 | 0 | 0 | 0 |
| Monk | 2 | 0 | 1 | 0 |
| Druid | 0 | 0 | 1 | 0 |
| Demon Hunter | 3 | 0 | 1 | 0 |
| Evoker | 0 | 0 | 1 | 0 |

Spec-level `PrimaryStatPriority` overrides the class one, which is how e.g.
Shaman splits: Elemental/Restoration are 0 (Intellect) while Enhancement is 2
(Agility), and Paladin Holy is 1 (Intellect) while Protection/Retribution are 5.

Across the 40 playable specs the routing is **8 Strength, 13 Agility,
19 Intellect**.

The full per-spec table is at
[`charstats-corpora/combined-primary-stat-routing.md`](charstats-corpora/combined-primary-stat-routing.md).
Regenerate with `character_stats.py routing`.

**This is the information-dropping boundary.** An item cannot resolve
`ItemModType` 71–74; only the character stage can. So the ItemModType must
survive gear preparation.

## 4. Stamina -> health

```
Player::GetHealthBonusFromStamina:
    ratio = HpPerSta.txt[level].Health          (10.0f if the row is missing)
    return GetStat(STAT_STAMINA) * ratio

Player::UpdateMaxHealth:
    value  = (BASE_VALUE + GetCreateHealth()) * BASE_PCT
    value += TOTAL_VALUE + GetHealthBonusFromStamina()
    value *= TOTAL_PCT
    SetMaxHealth((uint32)value)                 # truncating cast
```

* `GetCreateHealth()` is **0** for a Player (`InitStatsForLevel`).
* `ITEM_MOD_HEALTH` feeds `BASE_VALUE`, so it *is* scaled by `BASE_PCT`.
* the stamina term is added **after** `BASE_PCT` and **before** `TOTAL_PCT`, so a
  base-percentage health aura does not scale it but a total-percentage one does.
  A test constructs a fixture where swapping those two stages changes the answer.
* `HpPerSta` in this snapshot: level 1 -> 1, 60 -> 11, 70 -> 13, **80 and above
  -> 20**.

Numeric witness at level 90 with 17,794 stamina and nothing else:
`17794 * 20 = 355880` max health.

No class or spec modifier exists in the source path; class differences in health
come only from the base stamina row and from auras.

## 5. Primary stat -> attack power and spell power

```
Player::UpdateAttackPowerAndDamage(ranged):
    melee : val2 = max(STR * AttackPowerPerStrength, 0)
                 + max(AGI * AttackPowerPerAgility, 0)
            (+ max(AGI * AttackPowerPerStrength, 0) when the current
             SpellShapeshiftForm has Flags & 0x20)
    ranged: val2 = (level + max(AGI, 0)) * RangedAttackPowerPerAgility
    SetStatFlatModifier(unitMod, BASE_VALUE, val2)
    reported AP      = int32(BASE_VALUE * BASE_PCT)
    reported AP mod  = int32(TOTAL_VALUE)            <- separate field
    AP multiplier    = TOTAL_PCT - 1.0
```

`ITEM_MOD_ATTACK_POWER` and the weapon's `int32(dps * 6)` land in `TOTAL_VALUE`,
i.e. they are reported as `AttackPowerModPos`, **not** folded into
`AttackPower`. `Unit::GetTotalAttackPowerValue` recombines them at use time.

```
Unit::SpellBaseDamageBonusDone(schoolMask):
    if OverrideSpellPowerByAPPercent > 0:
        return int32(CalculatePct(GetTotalAttackPowerValue(BASE_ATTACK), pct) + 0.5f)
    benefit  = GetTotalAuraModifierByMiscMask(SPELL_AURA_MOD_DAMAGE_DONE, schoolMask)
    benefit += GetBaseSpellPowerBonus()            # m_baseSpellPower, fed by
                                                   # ITEM_MOD_SPELL_POWER
    if GetPrimaryStat() == STAT_INTELLECT:
        benefit += max(0, int32(GetStat(STAT_INTELLECT)))
    benefit += SPELL_AURA_MOD_SPELL_DAMAGE_OF_STAT_PERCENT terms
```

`SpellBaseHealingBonusDone` is the same shape.

**AP and SP are not the same mechanism and must not be collapsed:**

| | attack power | spell power |
|---|---|---|
| stats used | Strength and Agility, both | Intellect only |
| coefficient | per-class integer from `ChrClasses` | fixed 1:1 |
| gate | none (a zero coefficient is the gate) | `GetPrimaryStat() == INTELLECT` |
| item contribution lands in | `TOTAL_VALUE` (reported separately) | `m_baseSpellPower` (folded in) |
| level term | ranged only | none |

`SPELL_AURA_OVERRIDE_ATTACK_POWER_BY_SP_PCT` and
`SPELL_AURA_OVERRIDE_SPELL_POWER_BY_AP_PCT` cross-convert the two; both are
ordinary aura state and are the point where spell semantics take over.

## 6. Armor

```
Player::UpdateArmor:
    value  = GetFlatModifierValue(UNIT_MOD_ARMOR, BASE_VALUE)
    value *= GetPctModifierValue(UNIT_MOD_ARMOR, BASE_PCT)
    value += sum over SPELL_AURA_MOD_ARMOR_PCT_FROM_STAT of
             CalculatePct(GetStat(stat), amount)
             where stat = miscValue, or GetPrimaryStat() when miscValue == -2
    baseValue = value
    value += GetFlatModifierValue(UNIT_MOD_ARMOR, TOTAL_VALUE)
    value *= GetPctModifierValue(UNIT_MOD_ARMOR, TOTAL_PCT)
    value *= GetTotalAuraMultiplier(SPELL_AURA_MOD_BONUS_ARMOR_PCT)
    SetArmor(int32(value), int32(value - baseValue))
```

* base armour at create time is `createStats[AGILITY] * 2`
  (`InitStatsForLevel`), and `UpdateArmor` does not re-derive it — it reads
  whatever `UNIT_MOD_ARMOR BASE_VALUE` currently holds.
* **gear armour** (`ItemTemplate::GetArmor`, see the gearing doc §5) and
  `ITEM_MOD_EXTRA_ARMOR` both land in `TOTAL_VALUE`, so they are reported as
  *bonus armor* and are not scaled by `BASE_PCT`.
* primary-stat-derived armour is an aura (`SPELL_AURA_MOD_ARMOR_PCT_FROM_STAT`)
  and counts as **base**, not bonus.

"Character armor amount" stops here. Turning it into damage reduction against a
specific attacker is a combat-side calculation (`Unit::CalcArmorReducedDamage`,
`ArmorMitigationByLvl.txt`, `GlobalCurve::ArmorItemLevelDiminishing`) and is out
of scope; the boundary is exactly `ActivePlayerData`'s armour fields.

## 7. Crit

```
Player::UpdateAllCritPercentages:
    SetBaseModPctValue(CRIT_PERCENTAGE,         5.0f)
    SetBaseModPctValue(OFFHAND_CRIT_PERCENTAGE, 5.0f)
    SetBaseModPctValue(RANGED_CRIT_PERCENTAGE,  5.0f)
    then UpdateCritPercentage for each

Player::UpdateCritPercentage(attType):
    value = GetBaseModValue(slot, FLAT_MOD)
          + GetBaseModValue(slot, PCT_MOD)          # the 5.0 above -- additive
          + GetRatingBonusValue(CR_CRIT_MELEE|RANGED)

Player::UpdateSpellCritChance:
    crit = 5.0f
         + GetTotalAuraModifier(SPELL_AURA_MOD_SPELL_CRIT_CHANCE)
         + GetTotalAuraModifier(SPELL_AURA_MOD_CRIT_PCT)
         + GetRatingBonusValue(CR_CRIT_SPELL)
```

Notes:

* despite the name, `PCT_MOD` on a `BaseModGroup` is **added**, not multiplied.
* **there is no agility-to-crit conversion left.** `UpdateStats(STAT_AGILITY)`
  calls `UpdateAllCritPercentages` only to refresh the rating-derived part.
* melee / ranged / spell crit are still **three separate identities** in the
  current implementation: three `CombatRating` slots
  (`CR_CRIT_MELEE`/`RANGED`/`SPELL`), three `ActivePlayerData` fields, and two
  different code paths (`UpdateCritPercentage` vs `UpdateSpellCritChance`).
  `ITEM_MOD_CRIT_RATING` feeds all three, which is why they usually agree — but
  that is a property of the item routing, not of the character model.
* `CONFIG_STATS_LIMITS_ENABLE` can cap the result; it is off by default and is
  server configuration, not source data.

## 8. Haste

Haste has no percentage field of its own. The chain is:

```
CR_HASTE_MELEE|RANGED|SPELL accumulate in CombatRatings[17..19]
Player::UpdateRating(cr) for a haste rating:
    multiplier = GetRatingMultiplier(cr)             # 1 / CombatRatings.txt
    oldVal = ApplyRatingDiminishing(cr, oldRating * multiplier)
    newVal = ApplyRatingDiminishing(cr, amount     * multiplier)
    CR_HASTE_MELEE  -> ApplyAttackTimePercentMod(BASE_ATTACK, old, false)
                       ApplyAttackTimePercentMod(OFF_ATTACK,  old, false)
                       ApplyAttackTimePercentMod(BASE_ATTACK, new, true)
                       ApplyAttackTimePercentMod(OFF_ATTACK,  new, true)
                       + UpdatePowerRegen(POWER_RUNES) for Death Knights
    CR_HASTE_RANGED -> ApplyAttackTimePercentMod(RANGED_ATTACK, old/new)
    CR_HASTE_SPELL  -> ApplyCastTimePercentMod(old/new)
```

So haste is applied as a **differential**: the old value is removed and the new
one added, against `ModHaste` / `ModRangedHaste` / `ModCastingSpeed`, which
`InitStatsForLevel` seeds at `1.0f`.

Separating the layers:

| Layer | Where |
|---|---|
| haste rating | `CombatRatings[17..19]`, fed by `ITEM_MOD_HASTE_RATING` (all three) |
| rating conversion | `1 / CombatRatings.txt[level][cr]` |
| diminishing | `GlobalCurve` type 2 -> curve 21024 |
| generic haste percentage | none — it only exists as the three attack/cast-time mods |
| melee/ranged/spell identity | three ratings, three mods, three call sites |
| multiplicative aura haste | `SPELL_AURA_MOD_MELEE_HASTE` etc., outside this boundary |
| GCD / periodic consequences | combat side, outside this boundary |

The preparation boundary is therefore *the three rating amounts and their
converted percentages*. Everything after `ApplyAttackTimePercentMod` is existing
Core haste semantics.

## 9. Mastery

### Generic half

```
ITEM_MOD_MASTERY_RATING (49)
  -> Item::GetItemStatValue * CombatRatingsMultByILvl -> std::round
  -> Player::ApplyRatingMod(CR_MASTERY) -> CombatRatings[25]
  -> Player::UpdateMastery (StatSystem.cpp):
        if (!CanUseMastery()) { Mastery = 0.0f; return; }
        value  = GetTotalAuraModifier(SPELL_AURA_MASTERY)
        value += GetRatingBonusValue(CR_MASTERY)
                   = CombatRatings[25] * (1 / CombatRatings.txt[level][25])
                     then GlobalCurve type 1 -> curve 21024
        ActivePlayerData::Mastery = value
        for each owned aura cast by self with SPELL_ATTR8_MASTERY_AFFECTS_POINTS:
            for each effect with a non-zero BonusCoefficient:
                auraEff->RecalculateAmount(this)
```

`CanUseMastery()` is `HasSpell(ChrSpecialization.MasterySpellID[0]) ||
HasSpell(...[1])`. Without the spec's mastery spell known, mastery rating
produces **nothing**, no matter how much of it is equipped. That is the
acquisition dependency the gearing side cannot see.

At level 90 the mastery column of `CombatRatings.txt` is **46**, so one point of
mastery rating is `1/46 ≈ 0.021739 %` before diminishing.

### Spec-specific half

```cpp
// SpellEffectInfo::CalcValue, src/server/game/Spells/SpellInfo.cpp
if (_spellInfo->HasAttribute(SPELL_ATTR8_MASTERY_AFFECTS_POINTS))
    if (Player const* playerCaster = Object::ToPlayer(caster))
        value += *playerCaster->m_activePlayerData->Mastery * BonusCoefficient;
```

So the three terms a tooltip conflates are precisely:

* **"Mastery rating"** — `CombatRatings[CR_MASTERY]`, an integer;
* **"Mastery points"** / `ActivePlayerData::Mastery` — the converted, diminished
  percentage-shaped scalar, **one per character, not per spec**;
* **"Mastery percentage"** as shown for a spec — `Mastery * BonusCoefficient` of
  one specific effect of the spec's mastery spell. There is no single spec-level
  mastery percentage anywhere in the data.

All 40 playable specs set `SPELL_ATTR8_MASTERY_AFFECTS_POINTS` on their mastery
spell (verified by test).

### Classification

Classifying each spec by the aura types of its mastery-scaled effects
(`scripts/research/charstats/mastery.py`, regenerate with
`character_stats.py mastery`):

**Apparently expressible through ordinary spell semantics (18)** — every
mastery-scaled effect uses an aura whose amount a generic engine rule consumes
(`ADD_PCT_MODIFIER`, `ADD_FLAT_MODIFIER`, the `*_BY_SPELL_LABEL` variants,
`MOD_ATTACK_POWER_PCT`, `MOD_DAMAGE_PERCENT_DONE`, `MOD_AUTOATTACK_DAMAGE`,
`MOD_INCREASE_HEALTH_PERCENT`, `MOD_ABSORB_TAKEN_PCT`, `MOD_BLOCK_PERCENT`,
`MOD_MAX_POWER_PCT`, `MOD_MANA_REGEN_PCT`, `MOD_SUMMON_DAMAGE`,
`MOD_BLOCK_CRIT_CHANCE`):

> Death Knight Frost; Demon Hunter Devourer; Demon Hunter Havoc; Druid Balance;
> Druid Feral; Druid Guardian; Hunter Marksmanship; Mage Arcane; Mage Frost;
> Monk Brewmaster; Paladin Protection; Rogue Assassination; Rogue Subtlety;
> Shaman Enhancement; Warlock Affliction; Warrior Arms; Warrior Fury; Warrior
> Protection

**Genuinely script/consumer-dependent (17)** — at least one mastery-scaled
effect is `SPELL_AURA_DUMMY`, which has no generic value semantics:

> Death Knight Blood; Demon Hunter Vengeance; Druid Restoration; Evoker
> Augmentation; Evoker Devastation; Evoker Preservation; Hunter Survival; Mage
> Fire; Monk Mistweaver; Monk Windwalker; Paladin Holy; Priest Discipline;
> Priest Holy; Priest Shadow; Rogue Outlaw; Shaman Elemental; Warlock
> Destruction

**Blocked on a currently unsupported generic semantic (5)** — the effect aura is
not a dummy but this classification does not yet cover it
(`SPELL_AURA_NONE`, the undocumented `SPELL_AURA_531`,
`MOD_HEALING_DONE_PCT_VERSUS_TARGET_HEALTH`):

> Death Knight Unholy; Hunter Beast Mastery; Paladin Retribution; Shaman
> Restoration; Warlock Demonology

The four structurally different masteries the task asked about:

| Spec | Mastery spell | Shape | Category |
|---|---:|---|---|
| Warrior Arms | 1258398 *Master of Arms* | 3 × modifier auras, coefficient 1.1 | ordinary |
| Death Knight Blood | 77513 *Blood Shield* | `DUMMY` coef 2.0 + `MOD_ATTACK_POWER_PCT` coef 1.0 + `DUMMY` base 50 | scripted |
| Monk Mistweaver | 117907 *Gust of Mists* | `DUMMY` coef 31.185 + `ADD_FLAT_MODIFIER` coef 0.31185 | scripted |
| Priest Discipline | 271534 *Grace* | a single `DUMMY` coef 1.35 | scripted |
| Druid Guardian | 155783 *Nature's Guardian* | `MOD_INCREASE_HEALTH_PERCENT` + `MOD_ABSORB_TAKEN_PCT`, coef 0.7 | ordinary |

Mistweaver is the interesting case: the *same* mastery value drives a dummy at
coefficient 31.185 and an ordinary flat modifier at coefficient 0.31185 — a
factor of exactly 100 apart, i.e. one is a raw amount and the other a
percentage. Nothing in the data says which; only the script does.

No mastery effect is implemented here.

## 10. Versatility and tertiary ratings

```
Player::GetRatingBonusValue(cr):
    base = ApplyRatingDiminishing(cr, CombatRatings[cr] * GetRatingMultiplier(cr))
    if cr == CR_RESILIENCE_PLAYER_DAMAGE: return (1 - 0.99^base) * 100
    return base
```

`GlobalCurve` assignment in this snapshot:

| Rating | GlobalCurve type | CurveID | level-90 per point |
|---|---:|---:|---:|
| CritMelee/Ranged/Spell | 0 | 21024 | 1/46 |
| Mastery | 1 | 21024 | 1/46 |
| HasteMelee/Ranged/Spell | 2 | 21024 | 1/44 |
| VersatilityDamageDone | 5 | 21024 | 1/54 |
| VersatilityHealingDone | 5 | 21024 | 1/54 |
| VersatilityDamageTaken | 11 | 21035 | 1/108 |
| Speed | 3 | 21025 | 1/11.5 |
| Avoidance | 4 | 21025 | 1/36.8 |
| Lifesteal (leech) | 6 | 21025 | 1/69 |
| Dodge / Parry / Block | 7 / 8 / 9 | 21505 (identity) | — |

**One source rating feeds several derived quantities, and the branch points are
explicit:**

* `ITEM_MOD_VERSATILITY` -> three `CombatRating` slots. Damage-done and
  healing-done share curve 21024 and the `1/54` coefficient; damage-taken uses
  curve 21035 and `1/108`, i.e. exactly half the rate. The branch is in
  `Player::_ApplyItemBonuses`, which issues three `ApplyRatingMod` calls.
* the three versatility slots then diverge again in consumption:
  `UpdateVersatilityDamageDone` writes `ActivePlayerData::Versatility` and
  refreshes physical damage; `UpdateHealingDonePercentMod` folds
  `VersatilityHealingDone` plus `SPELL_AURA_MOD_VERSATILITY` into
  `ModHealingDonePercent`; `VersatilityDamageTaken` has **no** update branch at
  all and is read directly at damage time.
* `ITEM_MOD_HASTE_RATING` and `ITEM_MOD_CRIT_RATING` likewise each feed three
  slots.
* leech, avoidance and speed are single-slot and share curve 21025.

**Cross-check against the gearing tool's `MOD_TO_RATINGS`.** That mapping was
transcribed from `Player::_ApplyItemBonuses`; re-reading the consumer confirms
it covers every `ApplyRatingMod` call site, including the ones that are commented
out in the C++ (`ITEM_MOD_HIT_TAKEN_RATING`, `ITEM_MOD_CRIT_TAKEN_RATING`) which
are therefore **not** in the mapping. Two entries are worth flagging:
`ITEM_MOD_CRIT_TAKEN_RANGED_RATING` (26) and `ITEM_MOD_RESILIENCE_RATING` (35)
both route to `CR_RESILIENCE_PLAYER_DAMAGE`, which is the only rating with a
non-linear tail. The mapping is complete for the current snapshot's populated
ItemModTypes.

## 11. Specialization passive acquisition

```
Player::LearnSpecializationSpells:
    for each SpecializationSpells row of GetPrimarySpecialization():
        skip if !SpellInfo or SpellInfo->SpellLevel > GetLevel()
        LearnSpell(SpellID, true)
        if (OverridesSpellID) AddOverrideSpell(OverridesSpellID, SpellID)
```

plus, in the spec-change path (`Player.cpp:28952`):

```cpp
if (CanUseMastery())
    for (uint32 i = 0; i < MAX_MASTERY_SPELLS; ++i)
        if (uint32 mastery = spec->MasterySpellID[i])
            LearnSpell(mastery, true);
```

So a spec's baseline package is:

| Component | Source | Derivable here? |
|---|---|---|
| specialization passives and actives | `SpecializationSpells(SpecID)` | **yes** |
| spell replacement | `SpecializationSpells.OverridesSpellID` | **yes** |
| mastery spell | `ChrSpecialization.MasterySpellID1/2` | **yes** |
| armour specialization | a `SpecializationSpells` row (see §12) | **yes** |
| baseline stat modifiers | ordinary auras on those spells | yes, as spell roots |
| armour proficiency | `ChrClasses.ArmorTypeMask` plus `SkillRaceClassInfo` | partially |
| level gate | `SpellLevels.SpellLevel` at difficulty 0 | yes |

Census: **633 `SpecializationSpells` rows**, **605** of which are reachable at
level 90 across the 40 playable specs.

**Answer to the architectural question:** yes — a
`CharacterBuild { race, class, spec, level, talents, gear }` *can* acquire the
baseline spec package from source without the host listing SpellIDs. Two
caveats:

* 13 `SpecializationSpells` rows (12 distinct SpellIDs) point at a SpellID with
  no `SpellName` row in this snapshot. That is a real integrity gap, pinned by a
  test rather than papered over.
* `SpellInfo::SpellLevel` comes from `SpellLevels` at difficulty 0; rows without
  a `SpellLevels` entry are treated as ungated, matching the consumer.

Acquisition stays separate from executability throughout: reaching a SpellID
here says nothing about whether the spell does anything.

## 12. Armour specialization / primary-stat bonus

Fully source-derivable, with nothing hardcoded — not the spell ids, not the
armour types, not the percentage.

**Discovery rule** (`charstats.acquisition.armor_specializations`):

1. the spell is granted by a `SpecializationSpells` row for the spec;
2. `SpellMisc.Attributes_8` has `SPELL_ATTR8_REQUIRES_EQUIPPED_INV_TYPES`
   (`0x00100000`);
3. `SpellEquippedItems.EquippedItemClass == 4` (armour), with
   `EquippedItemSubclass` a subclass bitmask and `EquippedItemInvTypes` an
   inventory-type bitmask;
4. the spell applies `SPELL_AURA_MOD_TOTAL_STAT_PERCENTAGE` (137) with a
   positive `EffectBasePointsF`, whose `EffectMiscValue_1` is a `Stats` bitmask.

**Qualification policy** (`Player::HasItemFitToSpellRequirements`): because of
the attribute, the armour branch takes the "requires item equipped in all armor
slots" path and demands a fitting item in **every one** of
`HEAD, SHOULDERS, CHEST, WAIST, LEGS, FEET, WRISTS, HANDS`. The
`EquippedItemInvTypes` mask in the data is `0x1007EA`, i.e. bits
`{1, 3, 5, 6, 7, 8, 9, 10, 20}` — exactly those eight slots plus `ROBE` as a
chest variant. `Player::ApplyItemDependentAuras` adds or removes the aura as
equipment changes.

**Amount**: `EffectBasePointsF`, which is **5.0** for all 22 current passives —
read from source, never assumed. It is applied as `TOTAL_PCT` on
`UNIT_MOD_STAT_<stat>` through `AuraEffect::HandleModTotalPercentStat`, which
uses `GetTotalAuraMultiplier`, so 5.0 becomes a ×1.05 multiplier.

Worked examples from this snapshot:

| Spell | Spec | Armour | Stat bitmask | Stat | Percent |
|---:|---|---|---:|---|---:|
| 86101 | Warrior Arms | Plate | 1 | Strength | 5.0 |
| 86102 | Paladin Protection | Plate | 4 | Stamina | 5.0 |
| 86103 | Paladin Holy | Plate | 8 | Intellect | 5.0 |
| 86537 | Death Knight Blood | Plate | 4 | Stamina | 5.0 |
| 86092 | (leather) | Leather | 2 | Agility | 5.0 |
| 86108 | Shaman Elemental | Mail | 8 | Intellect | 5.0 |

So yes: this is **generic equipment qualification -> ordinary aura semantics**.
Nothing about it needs a script. The one thing a future importer must supply is
the equipment check itself, because the aura is conditional on gear the
character side does not otherwise see.

The tool's `--armor-specialization` flag applies it and says in its provenance
that it *assumes* all eight slots match; it does not inspect the caller's gear.

## 13. Racial acquisition boundary

| Contribution | Mechanism | Derivable here? |
|---|---|---|
| base stat difference | `player_racestats.StatModifier[5]`, added to the class/level row | **no** (world DB) |
| passive and active racial spells | `SkillLineAbility` with a `RaceMasks` + `ClassMask`, gated on `SkillRaceClassInfo(SkillLine, race, class)` | **yes** |
| rating/stat modifiers | ordinary auras on those spells | yes, as spell roots |
| create-time custom spells | `playercreateinfo_spell_custom` (world DB), only under `CONFIG_START_ALL_SPELLS` | **no** |

The DB2-backed path is `Player::IsSpellFitByClassAndRace` /
`LearnDefaultSkills` / `LearnSkillRewardedSpells`:

```cpp
if (!_spell_idx->second->RaceMask.IsEmpty() && !RaceMask.HasRace(race)) continue;
if (_spell_idx->second->ClassMask && !(ClassMask & classmask))           continue;
if (!sDB2Manager.GetSkillRaceClassInfo(SkillLine, race, class))          continue;
```

397 `SkillLineAbility` rows carry a race mask. An Orc Death Knight resolves to
70 racial-gated abilities including `20572 Blood Fury` (skill line 125,
`AcquireMethod = 2`), `20573 Hardiness` and `21563 Command`; a Human Death
Knight resolves to a different set. No racial combat ability is implemented.

`ChrRaces` itself carries **no** stat fields — the race stat contribution is
entirely the world-database modifier.

## 14. The Python reconstruction

`scripts/research/charstats/` is a standard-library-only package; the entry
point `scripts/research/character_stats.py` runs under both `uv run --script`
and plain `python3`.

| module | mirrors |
|---|---|
| `identity.py` | `ChrRaces` / `ChrClasses` / `ChrSpecialization`, `GetPrimaryStat`, `GetGameTableColumnForClass` |
| `basestats.py` | `InitStatsForLevel` base values, `GetPlayerLevelInfo`, `GetPlayerClassLevelInfo` |
| `primary.py` | the `ITEM_MOD_*` stat routing in `_ApplyItemBonuses` |
| `derived.py` | `GetTotalStatValue`, `UpdateMaxHealth`, `UpdateArmor`, `UpdateAttackPowerAndDamage`, `SpellBaseDamageBonusDone`, `UpdateCritPercentage` |
| `mastery.py` | `UpdateMastery` + `SpellEffectInfo::CalcValue`'s mastery term |
| `acquisition.py` | `LearnSpecializationSpells`, `HasItemFitToSpellRequirements`, `IsSpellFitByClassAndRace` |
| `character.py` | the facade |
| `census.py`, `report.py`, `cli.py` | counts, output, CLI |
| `_aura_names.py` | generated from `SpellAuraDefines.h`; readability only |

It reuses `gearing.tables` (snapshot access), `gearing.curves` (the evaluator)
and `gearing.ratings` (`GetRatingBonusValue`). The last is shared deliberately:
it is literally the same consumer function on both sides of the boundary.

### CLI

```
character-stats naked  --race X --class Y --spec Z --level 90
                       [--armor-specialization]
character-stats stats  --race X --class Y --spec Z --level 90
                       [--strength N --agility N --stamina N --intellect N]
                       [--crit-rating N --haste-rating N --mastery-rating N
                        --versatility-rating N --leech-rating N
                        --avoidance-rating N --speed-rating N ...]
                       [--armor N --attack-power N --spell-power N --health N]
                       [--gear-json <gearing loadout --json payload>]
character-stats specs        [--class-id N]
character-stats mastery      [--spec-id N | --class-id N] [--mastery-value F]
character-stats acquisition  --race X --class Y --spec Z --level 90
character-stats routing      [--class-id N] [--item-mod N]
character-stats corpus       [--level N]
character-stats census
```

Global: `--tables DIR`, `--base-stats FILE`, `--json`; most commands take
`--format table|markdown|csv` and `--output FILE`.

Every value carries provenance — the source base stat, the supplied
contribution, the intermediate, the conversion table or curve, and the derived
value. Nothing applies talents, buffs, consumables or gear implicitly.

### Committed corpora

| file | contents |
|---|---|
| `charstats-corpora/class-spec-identity.md` | every class/spec with its priority, coefficients and mastery root |
| `charstats-corpora/mastery-classification.md` | the three-bucket mastery classification |
| `charstats-corpora/combined-primary-stat-routing.md` | which granted stats actually contribute, per spec, per ItemModType |
| `charstats-corpora/spec-corpus.md` | every spec's naked shape at level 90 |
| `charstats-corpora/census.json` | the population census |
| `charstats-corpora/gear-to-character-proof.{txt,json}` | the cross-tool proof |

## 15. Cross-tool integration proof

`scripts/research/tools/gear_to_character.py` runs, for four real current tier
loadouts:

```
equipped loadout (ItemIDs + ItemContext)
  -> gearing.loadout.resolve_loadout
  -> {ItemModType: amount}, {CombatRating: amount}, armor, set spell roots
  -> charstats.character.CharacterResolver
  -> derived naked+gear quantities
```

The boundary payload is asserted by test to contain **only** stats, ratings,
armor and item-set spell roots — no ItemID, bonus list, item level or curve.

Result (level 90, `ItemContext::Raid_Mythic`, synthetic base stats):

| Case | Tier set | Boundary stats | Primary stat | Spell power | Mastery |
|---|---|---|---|---:|---:|
| Blood Death Knight (Strength) | 2055 | `ItemModType 74 (Str\|Int) 730`, `Stamina 14642` | Strength | 0 | 9.04 % |
| Windwalker Monk (Agility) | 2061 | `ItemModType 73 (Agi\|Int) 730`, `Stamina 14642` | Agility | 0 | 6.67 % |
| Mistweaver Monk (Intellect healer) | 2061 | *identical to Windwalker* | Intellect | 4,921 | 6.67 % |
| Discipline Priest (Intellect caster) | 2063 | `Intellect 730`, `Stamina 14642` | Intellect | 4,871 | 6.04 % |

The Windwalker/Mistweaver pair is the proof: **identical items, identical
boundary payload, different derived character** — different spell power from
`PrimaryStatPriority`, and different `ItemSetSpell` roots from `ChrSpecID`.
Nothing on the gear side needed to know the spec except to filter set-bonus
casts, and nothing on the character side needed to know the items.

This is not a Core integration and computes no combat result.

## 16. Verification

`uv run --with pytest --with hypothesis python -m pytest tests/ -q` from
`scripts/research/` runs both packages' suites together.

| file | tests | what it proves |
|---|---:|---|
| `test_charstats_identity.py` | 23 | `PrimaryStatPriority` thresholds; GameTable class-column order against the real headers; identity lookups and their fail-closed paths; base-stat absence, race modifier, class/level keying, `SetCreateHealth(0)`, create-time armour; base-stat file validation |
| `test_charstats_derived.py` | 22 | `GetTotalStatValue` stage order with a discriminating fixture; truncation vs rounding; the −100 % floor; health stage order (a fixture where swapping `BASE_PCT` and the stamina term changes the answer); armour base-vs-bonus split; both AP formulas and their per-term clamps; SP gating and its asymmetry with AP; additive crit |
| `test_charstats_corpus.py` | 30 | every playable spec constructs naked at level 90; AP and SP follow the class/spec rules for all 40; combined ItemModType routing; identical gear routing differently per spec; every spec's mastery classification and attribute; armour specializations discovered not hardcoded, and their 5 % source-derived; racial gating; census; the end-to-end proof |
| `test_charstats_properties_cli.py` | 20 | hypothesis properties over the stat pipeline and the mastery affine form; determinism; serialisation round trip; every mastery root resolves; the 13 dangling `SpecializationSpells` references pinned as a source fact; every CLI command including its fail-closed paths and the `--gear-json` hand-off |
| **charstats subtotal** | **95** | |
| gearing suite (see its own document) | 511 | |
| **total** | **606** | |

Expected values are reconstructed from source rows or from independently
transcribed formulas, never from the implementation's own output. The
discriminating fixtures are deliberately chosen so that a stage-order mistake
changes the number rather than being invisible.

Property tests assert only source-proven properties. In particular there is no
property claiming stats or health increase monotonically with anything except
where a diminishing curve provably is non-decreasing.

## 17. Census

`python3 scripts/research/character_stats.py --json census`
(committed at `docs/research/charstats-corpora/census.json`):

| population | count |
|---|---:|
| `ChrRaces` rows | 59 |
| `ChrClasses` rows | 15 |
| playable specializations (excluding `Initial`) | 40 |
| per-class `Initial` pseudo-specs | 14 |
| specs with a mastery spell | 40 |
| distinct mastery spell roots | 40 |
| `SpecializationSpells` rows | 633 |
| baseline spec passive roots reachable at level 90 | 605 |
| armour-specialization passives discovered | 22 |
| `SkillLineAbility` rows with a race mask | 397 |
| `CombatRatings.txt` / `HpPerSta.txt` / `BaseMp.txt` levels | 123 each |
| `GlobalCurve` rows | 38 |

Mastery representation categories: **18 ordinary / 17 scripted / 5 blocked**.

Primary-stat routing across specs: **8 Strength / 13 Agility / 19 Intellect**.

Base-stat table/curve families actually used by this pipeline: three GameTables
(`HpPerSta`, `BaseMp`, `CombatRatings`), one curve family (`GlobalCurve`
diminishing, 3 distinct curve ids: 21024, 21025, 21035, plus the identity
21505), and two world-database tables that are **absent**.

Unsupported source relationships encountered:

* `player_classlevelstats` / `player_racestats` — world DB, absent;
* `playercreateinfo_spell_custom` — world DB, absent, and config-gated;
* `BuildPlayerLevelInfo`'s above-max-level extrapolation — hardcoded per-class
  `switch`, not reproduced;
* 13 `SpecializationSpells` rows with no `SpellName`;
* `SPELL_AURA_531` — present in the data, undocumented in the consumer;
* `ChrClasses.ArmorTypeMask` / `SkillRaceClassInfo` armour proficiency — read but
  not modelled, because no gearing or stat consumer reads it.

## 18. Future production boundary

```
CharacterBuild identity  (race, class, spec, level)
   + resolved gear contributions  ({ItemModType: amount}, {CombatRating: amount},
                                    armor, weapon state, set spell roots)
   + selected trait effects
        ↓  [ character preparation -- everything in §§1-13 ]
prepared character facts
   (concrete STR/AGI/STA/INT, max health, armor, AP, SP,
    per-rating effective percentages, Mastery value,
    + the acquired spell roots: spec passives, mastery spell, armour
      specialization, racials, set bonuses)
        ↓
ordinary semantic compiler
        ↓
CombatProgram / CombatState
```

**When each identity becomes safe to discard.**

| Fact | Safe to drop after | Why |
|---|---|---|
| `ItemModType` | the routing in §3 has run | only the character stage can resolve 71–74; after that the concrete stats carry everything |
| `RaceID` | base stats resolved **and** racial spell roots extracted | race contributes exactly two things, and both are consumed early |
| `ClassID` | attack power computed **and** `SpellClassSet` recorded on the acquired spells | the AP coefficients are the only numeric use; `SpellClassSet` matters to spell-family modifiers downstream |
| `ChrSpecializationID` | `GetPrimaryStat`, spec passives, mastery spell, armour specialization and the `ItemSetSpell` / `ItemEffect` spec gates have all been evaluated | that is four separate consumers, so this is the **last** identity to become droppable |
| raw ratings | the per-rating effective percentages are computed | nothing downstream reads `CombatRatings[cr]` except `UpdateRating` itself and the `MOD_RATING_PCT` aura family — if those auras are modelled, the raw rating must survive |
| mastery source ids | the mastery spell is acquired and `ActivePlayerData::Mastery` exists | the spell then behaves as an ordinary aura; only `CanUseMastery` needed the id |

**What must not be pushed downstream.** None of `RaceID`, `ClassID`,
`ChrSpecializationID`, `ItemModType` or the mastery spell id has a runtime
semantic that observes it once the above consumers have run — with the two
exceptions noted (`SpellClassSet`, and raw ratings if rating-modifying auras are
in scope). Forcing them into `CombatProgram`/`CombatState` would be carrying
identity for its own sake.

**What must survive to validation.** The `(race, class, spec, level)` tuple and
the base-stat source identity, so a prepared character can be re-derived and
diffed after a data refresh — exactly as the gearing side keeps
`(ItemID, bonus lists, context)`.

## 19. Source conflicts and fail-closed records

| # | Finding | Classification |
|---|---|---|
| C1 | base primary stats live in the world DB; the TrinityCore repo ships schema only | **absent input** — requires an external table |
| C2 | `BuildPlayerLevelInfo` extrapolates above max level with a hardcoded per-class `switch` of Classic-era thresholds | **consumer policy** |
| C3 | 13 `SpecializationSpells` rows (12 distinct SpellIDs) reference a SpellID with no `SpellName` row | **incomplete source fact** |
| C4 | `SPELL_AURA_531` appears as a mastery-scaled effect aura but has no name or handler in the consumer | **consumer gap** |
| C5 | `ChrRaces` carries no stat fields at all; the race contribution is entirely `player_racestats` | source shape, recorded |
| C6 | item stats use `UNIT_MOD_STAT_* BASE_VALUE` while enchantment stats use `TOTAL_VALUE` | real asymmetry between two gear paths |
| C7 | `BaseModGroup` `PCT_MOD` is **added**, not multiplied, despite the name | naming hazard |
| C8 | `ChrClasses.PrimaryStatPriority` is only a fallback; the spec value wins whenever a spec exists | naming hazard |
| C9 | `MAX_STATS` is 5 (Spirit included) but no current class uses Spirit | vestigial |
| C10 | `playercreateinfo_spell_custom` is only read under `CONFIG_START_ALL_SPELLS` | config-gated world-DB path |

Nothing above is silently patched. Where the tool cannot proceed it raises
`MissingBaseStats` or `CharacterSourceError`; where the consumer has a policy
the tool reproduces it and names it.

## 20. Reproducibility

| item | value |
|---|---|
| data snapshot | Wago `12.1.0.69497` (`product=wow`) |
| wowlab-data commit | `c738030f59de193ea6536be4b5852fbbd2f07b83` |
| TrinityCore commit | `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` (master) |
| expansion | `CURRENT_EXPANSION = EXPANSION_MIDNIGHT (11)`, max level 90 |
| Python | 3.12.3, standard library only for the package |
| test dependencies | `pytest==8.4.2`, `hypothesis==6.140.3` |
| test counts | 95 charstats + 511 gearing = **606** |
| corpus identities | 40 playable specs; 4 integration cases (ItemSets 2055, 2061 ×2, 2063) |

### Source table hashes (sha256)

```
ChrRaces.csv                 d21a2b58e9e332b12972a16074039077cf4d55f8a4ff14b9766295d7764d3a83
ChrClasses.csv               679168021cdee163c317b93cc7d9b540e37c77183102f25cdefa16a8ec81584f
ChrSpecialization.csv        4e67c0772fca8cfc857e8a7fc9952538c35018e91f2734284f71089087d60c69
SpecializationSpells.csv     91c46e3b75aee668250e8c27b29c76e6210152d1c5fd93e996c06886b2c51a3c
SpellEquippedItems.csv       c741bb56267dbe0d4578f56d5cf5d77485c8a1f9db5e9c983316eb0c50fe870e
SpellEffect.csv              157f4d949964b05531f75dca8b5e9c45a91637104125a7f9e86474b4a3ec196f
SpellMisc.csv                3ef1315badafd00ceec0640b6859831893cba47a428bdbf869f2ecf0682ce4fa
SpellName.csv                d715dbf11027d5baa981e833099014ea19e38eaf1b27815af69c079bc94dbee7
SpellLevels.csv              b779655706f50bec0fe130f220f537e2440d35f340e86822acfd23b0541aa196
SkillLineAbility.csv         b96e163a5eeef135f1674db67109e538f44b15d144d6eb6a1de592494c8596e8
SkillRaceClassInfo.csv       ab5ff4b573e9863269d5b05bcd93abdf24aecd52dc54e9a02029f9fc8515963c
GlobalCurve.csv              5b9bc6b0ec505797aa68cd08b232256a9ba4416b2ffdaf97fcd6708434c0de89
Curve.csv                    b03672f8feb93cd76abb7e7acf066adc25f80ee4b0e8f146319f8c5639faa819
CurvePoint.csv               3925edf8c03182ef93718630cd16fba064a46b8d68a2adc8f157325c178396e9
PowerType.csv                e40b3d0f6662d86ed7f83a5b1e0e31fcd5ff5a573e3d04d017ce98fd8f48e163
CombatRatings.txt            e2f50545113411e6f9455cc2c5217bbd516b5c5288bb27002917fc6ca66fd7a3
HpPerSta.txt                 8dae32a8a44b5c6ab819e12d9449c26af64ac011465bd7c551e9b986d2d9f3b3
BaseMp.txt                   49f13159b1db2ba5b09a1a1b3e920f3025befb49f8a777dd61e8a77aabdf9642
SpellScaling.txt             (see data/tables/SpellScaling.txt)
```

### Direct consumer coordinates

```
src/server/game/Entities/Player/Player.cpp
    InitStatsForLevel, LearnSpecializationSpells, RemoveSpecializationSpells,
    LearnDefaultSkills, LearnDefaultSkill, LearnCustomSpells,
    ApplyItemDependentAuras, HasItemFitToSpellRequirements,
    CheckAttackFitToAuraRequirement, IsSpellFitByClassAndRace,
    GetRatingMultiplier, GetRatingBonusValue, ApplyRatingDiminishing,
    ApplyRatingMod, UpdateRating, UpdateAllRatings, CanUseMastery,
    GetExpertiseDodgeOrParryReduction
src/server/game/Entities/Unit/StatSystem.cpp
    Player::UpdateStats, UpdateAllStats, UpdateArmor, GetHealthBonusFromStamina,
    GetPrimaryStat, UpdateMaxHealth, UpdateMaxPower,
    UpdateAttackPowerAndDamage, CalculateMinMaxDamage, UpdateCritPercentage,
    UpdateAllCritPercentages, UpdateSpellCritChance, UpdateMastery,
    UpdateVersatilityDamageDone, UpdateHealingDonePercentMod,
    UpdateBlockPercentage, ApplySpellPowerBonus, UpdateSpellDamageAndHealingBonus
src/server/game/Entities/Unit/Unit.cpp
    Unit::GetTotalStatValue, SpellBaseDamageBonusDone, SpellBaseHealingBonusDone
src/server/game/Globals/ObjectMgr.cpp
    LoadPlayerInfo (player_racestats, player_classlevelstats),
    GetPlayerLevelInfo, GetPlayerClassLevelInfo, BuildPlayerLevelInfo
src/server/game/Spells/Auras/SpellAuraEffects.cpp
    AuraEffect::HandleModTotalPercentStat, HandleModArmorPctFromStat
src/server/game/Spells/SpellInfo.cpp
    SpellEffectInfo::CalcValue (SPELL_ATTR8_MASTERY_AFFECTS_POINTS)
src/server/game/DataStores/GameTables.h
    GetGameTableColumnForClass
sql/base/dev/world_database.sql
    player_classlevelstats, player_racestats  (schema only, no rows)
```

### Commands

```bash
cd /home/dev/pallet/wowlab-data
C="python3 scripts/research/character_stats.py"
B="--base-stats scripts/research/tests/fixtures/synthetic_base_stats.json"

$C specs
$C mastery --class-id 6
$C routing --class-id 10
$C acquisition --race Orc --class "Death Knight" --spec Blood --level 90
$C census
$C $B naked --race Orc --class "Death Knight" --spec Blood --level 90
$C $B stats --race-id 1 --class-id 10 --spec Mistweaver \
   --intellect 5000 --mastery-rating 9000

# gear -> character hand-off
python3 scripts/research/item_scaling.py --json loadout --json-file my.json \
        --output gear.json
$C $B stats --race-id 1 --class-id 10 --spec Windwalker --gear-json gear.json

# the committed proof
python3 scripts/research/tools/gear_to_character.py $B

# tests (both packages)
cd scripts/research
uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/ -q
```

### External documentation

None used as semantic authority. Race, class, spec and spell names come from the
snapshot's own `*_lang` columns and are navigation only.

## 21. End-to-end map, and what still blocks a production importer

```
race / class / spec / level
  ├─ base primary stats        <- player_classlevelstats + player_racestats  [ABSENT]
  ├─ base mana                 <- BaseMp.txt
  ├─ base health               <- literally 0
  ├─ base armor                <- createStats[AGI] * 2
  └─ base crit/block/hit/expertise <- literal constants in the consumer
        +
gear stats  (from the gearing pass)
  ├─ {ItemModType: amount}     -> concrete STR/AGI/STA/INT via §3
  ├─ {CombatRating: amount}    -> effective percentages via §§7-10
  ├─ armor                     -> UNIT_MOD_ARMOR TOTAL_VALUE
  └─ weapon min/max/speed/AP   -> weapon state
        +
selected traits / talents      [OUT OF SCOPE for this pass]
        ↓
prepared character
  ├─ stats            = ((base_flat * base_pct_excl) + create) * base_pct
                        + total_flat, then * total_pct, then int32()
  ├─ max health       = stamina * HpPerSta[level]  (+ item/aura stages)
  ├─ attack power     = STR * AP/Str + AGI * AP/Agi        (class coefficients)
  ├─ spell power      = INT if GetPrimaryStat() == INTELLECT, else 0, + item SP
  ├─ crit/haste/mastery/versatility/leech/avoidance/speed
                      = rating * (1 / CombatRatings.txt[level][cr])
                        then GlobalCurve diminishing
  └─ acquired spell roots
        ├─ SpecializationSpells(spec)        -> passives and overrides
        ├─ ChrSpecialization.MasterySpellID  -> mastery, gated by CanUseMastery
        ├─ armour specialization             -> equipment-gated stat aura
        ├─ SkillLineAbility race/class gates -> racials
        └─ ItemSetSpell thresholds           -> set bonuses (from the gearing pass)
        ↓
ordinary spell semantics
```

**Unresolved blockers to a production character importer, in priority order.**

1. **Base primary stats.** Nothing else on this list matters until a source for
   `player_classlevelstats` + `player_racestats` exists — a TDB world-database
   dump, a captured export, or an authored version-scoped input. Everything
   downstream is exact once this is supplied.
2. **The 17 script-dependent masteries.** Nearly half the specs have a mastery
   whose scaled effect is `SPELL_AURA_DUMMY`. Each needs either a script or a
   spell-specific rule; they cannot be compiled generically.
3. **The 5 blocked masteries.** `SPELL_AURA_531` in particular is undocumented in
   the consumer, so its semantics are unknown from both sides.
4. **Talents and trait effects.** Entirely out of scope here, and they sit
   between gear and prepared character in the pipeline above.
5. **The equipment check for armour specialization.** The rule is fully derived,
   but applying it needs the caller's equipped armour subclasses across eight
   slots — a fact that crosses the gear/character boundary in the opposite
   direction to everything else.
6. **Aura stages.** This pass models only the flat, item-driven stages. Any
   percentage stage (`BASE_PCT`, `TOTAL_PCT` other than armour specialization,
   `SPELL_AURA_MOD_*_PERCENT`) comes from auras and is unmodelled by design.
7. **13 dangling `SpecializationSpells` references.** Small, but a spec-package
   importer must decide whether to drop or fail on them.
