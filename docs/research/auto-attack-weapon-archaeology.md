# Auto-attack and weapon-combat archaeology

## Scope and result

This pass asks, of the checked-in snapshot and the pinned TrinityCore checkout:

> How does an ordinary player weapon swing work (prepared weapon, scheduling, outcome table,
> damage arithmetic, proc events), and where are the boundaries between white swings,
> special weapon attacks, queued/replacement attacks and weapon-derived spell damage?

It reuses the gearing archaeology for item scaling and the proc archaeology for proc
chance and state; neither is rebuilt here. Legacy mechanics are judged by **reachability
from current data**, never by their presence in the code.

Research only: nothing here touches Core, its catalogs or its milestones.

    absent from pinned Trinity != absent from Retail
    unsupported by research     != inert
    structural similarity       != semantic equivalence
    Trinity is the best available consumer oracle, not unquestionable Retail truth

| pin | value |
|---|---|
| data snapshot | Wago `12.1.0.69497` (`data/tables/`) |
| wowlab-data base | `2773a88` (branch `research`) |
| TrinityCore | `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` (client builds ≤ 12.0.7.68453) |
| world DB | `TDB_full_world_1200.26021_2026_02_06.sql` + update replay (creature level deltas only) |
| compiler for probes | `g++ 13.3.0 -std=c++20 -O0 -ffp-contract=off` |

| deliverable | path |
|---|---|
| this report | `docs/research/auto-attack-weapon-archaeology.md` |
| research package / CLI | `scripts/research/weapon_combat/`, `scripts/research/weapon_combat.py` |
| corpora | `docs/research/weapon-combat-corpora/` (weapon-sources, damage-arithmetic, special-attacks, witnesses-c, swing, attack-table, proc-events, witnesses-d, creature-level-deltas, unknowns) |
| differential C++ probes | `scripts/research/tools/tc_weapon_probe/`, `scripts/research/tools/tc_swing_probe/` |
| tests | `scripts/research/tests/test_wc_*.py` (145 tests after the closeout review) |

Sections 1–5 (part C) cover the prepared weapon, attack power, damage arithmetic and special
attacks; sections 6–10 (part D) cover scheduling, the outcome table, proc events and legacy
reachability.

**Headline results.**

1. **`DoMeleeAttackIfReady` never swings main hand and off hand in the same update.** Main hand
   wins; the off hand is pushed to +200 ms (`Unit.cpp:2210-2242`). Queued extra attacks, drained
   earlier in `Unit::Update`, are the one exception; none is reachable in scope (R2-6). With a
   fixed tick T and period P, melee fires every `ceil(P/T)` updates and auto-shot every
   `ceil(P/T)+1` (`Spell.cpp:3467`) (§6).
2. **Haste application is not reversible in binary32.** Trinity removes a haste aura by multiplying
   by the inverse factor rather than by recomputing from the aura totals
   (`ApplyPercentModFloatVar`, `Unit.cpp:10978-10981`). Applying and removing the same haste
   percentage can therefore leave the attack-speed multiplier below 1.0, so the period returns 1 ms short:
   21,480 of 99,010 (speed, haste) pairs; in-scope amounts 3, 5 and 15 are affected
   (`Unit.cpp:10978-11007`, probe) (§6).
3. **The white-swing outcome is one integer roll in basis points** (`urand(0, 9999)`,
   `Unit.cpp:2410`). The same swing also draws the damage roll before it, the block-crit roll
   after it on a block, and later durability, daze and proc rolls (§7.3, R2-1). Converting a chance
   with `int32(x*100.0f)` loses a basis point for 282 of 4,999 hundredths (probe). A level-93 boss
   (+3, which is not enough for glancing) gives 16.5 % miss dual-wielding, 0 % two-handed, 0 dodge,
   3 % parry and 7.5 % block from the front (§7).
4. **Crushing blows are unreachable for every attacker type.** Operator precedence makes the
   chance term negative for any uint8 level (`Unit.cpp:2489`, R2-3). **Glancing** requires
   `attackerLevel + 3 < victimLevel` on `GetLevelForTarget` levels, i.e. at least +4; in the pinned world
   DB only 13 creature entries reach +4 against a level-90 player and none is current content;
   current raid bosses are absent from the TDB, so glancing against them is **unresolved**, not
   legacy-only (§7, §10, §14 reconciliation).
5. **Player block is a fraction consumed as a percent.** `Player::GetBlockPercent` returns
   `min(ShieldBlock/(ShieldBlock+C), 0.85f)` and the melee path feeds it to `CalculatePct`, so a
   player victim blocks at most 0.85 % of a white hit (0.37 % at ShieldBlock 2000, C = 3430); the
   weapon-spell path truncates it to `uint32` 0 first. Creatures block 30 %; only `Unit` and
   `Player` define `GetBlockPercent` (`Player.cpp:26822`, `Unit.cpp:1459`, `:1239`, `Unit.h:987`) (§3, §7).
6. **Crit multiplier truncation**: a 1.3 multiplier turns 1000 into 2599 (`Unit.cpp:1430-1436`); the
   critical-block multiplier (aura 638) is read from the *attacker* (`Unit.cpp:1463`, `:1243`).
7. **Weapon specials apply effects in index order and can double-count** (`SpellEffects.cpp:2911-2935`).
   No in-scope spell has two fixed or two percent effects; 9 do snapshot-wide. Specials with
   `addPctMods == false` (ATTR6 or a non-physical school) also lose the 0.5 off-hand factor
   (`:2889-2908`). In the class-skill-lines scope that is 4 of 11 weapon specials, and 205547 Odyn's
   Fury is the only off-hand one (§4).
8. **Versatility and the off-hand factor are folded into the prepared min/max**; weapon AP
   (`dps×6`) is excluded from white swings but included in AP-coefficient spells
   (`StatSystem.cpp:447-479`, `Unit.cpp:6872`, `:9793`) (§1–§3).
9. **Dual wield for Rogue, Hunter and Demon Hunter arrives through default-skill learning**
   (skill 118 → spell 674), a path the current-player `Scope` does not follow. Warrior 296087 is
   AcquireMethod 0 and never auto-learned, so Arms and Protection dual wield is character state
   (fixed in the closeout review, §15) (§1).
10. **Every `DmgClass` MELEE spell carries the MH or OH swing proc flag** (250 in scope;
   `Spell.cpp:2350-2355`), on its cast, hit and finish events. The existing `procs/events.py` oracle
   agrees with every white-swing and auto-shot case checked (§8).

## 0. Part conventions

### Part C

Pins: data `12.1.0.69497`, TrinityCore `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, wowlab-data `2773a88` (branch
`research`). All coordinates are `path:line` under `src/server/game/` unless another path is given. Code:
`scripts/research/weapon_combat/{weapon,damage,special,witnesses_c}.py`; probe `tools/tc_weapon_probe/`;
corpora `docs/research/weapon-combat-corpora/{weapon-sources,damage-arithmetic,special-attacks,witnesses-c}.json`.


Numeric convention used throughout: every C++ `float` stage is reproduced in binary32 with the exactly rounded
helpers of `procs/chance.py` (`f32/lit/add/mul/div`), and every such claim is checked against Trinity's own text
compiled at `g++ 13.3.0 -std=c++20 -O0 -ffp-contract=off` (§5.3).

### Part D

part D (Track B: swing scheduling, attack outcome table, proc events, probe). All Trinity
coordinates: the sibling `TrinityCore` checkout @ 7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f.
U = src/server/game/Entities/Unit/Unit.cpp, P = .../Entities/Player/Player.cpp,
S = src/server/game/Spells/Spell.cpp, A = src/server/game/Spells/Auras/SpellAuraEffects.cpp.
Snapshot 12.1.0.69497. Scope = dummy_semantics.scope.Scope(include_class_skills=False): 4,836 spells
(8,975 with class skill lines).

## 1. Prepared weapon sources

### 1.1 What equipping a weapon writes (equipment-only stage)

| Step | Consumer | Types | Coordinates | Evidence |
|---|---|---|---|---|
| Attack type of a slot | `Player::GetAttackBySlot`: MAINHAND → `RANGED_ATTACK` iff InventoryType ∈ {RANGED 15, RANGEDRIGHT 26}, else `BASE_ATTACK`; OFFHAND → `OFF_ATTACK` | enum | Entities/Player/Player.cpp:9649-9657 | trinity-consumer |
| Weapon damage range | `proto->GetDamage(itemLevel, min, max)` → `SetBaseWeaponDamage` into `float m_weaponDamage[MAX_ATTACK][2]`, each bound written only if `> 0`; unequip restores `BASE_MINDAMAGE 1.0f` / `BASE_MAXDAMAGE 2.0f` | float | Player.cpp:8135-8157; Unit.h:1573, 1940; UnitDefines.h:33-34 | trinity-consumer |
| Item scaling | `ItemTemplate::GetDamage` / `GetDPS`: **reused** from the gearing archaeology (`gearing.resolver.GearResolver._resolve_weapon`); rounded to binary32 at the `SetBaseWeaponDamage` boundary | double→float | gearing-pipeline-archaeology.md weapon section | db2-fact (reused) |
| Base attack time | `SetBaseAttackTime(att, ItemDelay)` unless the current form has `CombatRoundTime` | uint32 ms | Player.cpp:8159-8161 | trinity-consumer |
| No weapon | `SetRegularAttackTime`: `BASE_ATTACK_TIME` 2000 | uint32 ms | Player.cpp:5387-5401; UnitDefines.h:35 | trinity-consumer |
| Weapon attack power | `int32(proto->GetDPS(itemLevel) * 6.0f)` → `UnitData::MainHand/OffHand/RangedWeaponAttackPower` (separate UF fields) | float→int32 (truncation) | Player.cpp:8163-8176; Unit.h:1567-1569; UpdateFields.h:411-413 | trinity-consumer |
| Shield block value | `ShieldBlock = int32(armor * 2.5f)` for an armor/shield item | uint32*float→int32 | Player.cpp:8123-8128 | trinity-consumer |
| Melee school | `GetMeleeDamageSchoolMask = 1 << ItemSparse.DamageType` for `GetWeaponForAttack(att, true)` | mask | Player.cpp:8183-8189 | trinity-consumer |
| Weapon for an attack type | RANGED reads the MAINHAND slot; item must be `ITEM_CLASS_WEAPON` and `(att == RANGED) == IsRangedWeapon()` (BOW/GUN/CROSSBOW/WAND); broken → null | — | Player.cpp:9582-9609; Entities/Item/ItemTemplate.h:940-953 | trinity-consumer |
| Unit constructor | `m_weaponDamage[i] = {1.0f, 2.0f}` for every attack type | float | Entities/Unit/Unit.cpp:355-356 | trinity-consumer |

A shield or holdable in the off hand therefore gives `GetWeaponForAttack(OFF_ATTACK) == nullptr`. A bow or gun in
the main hand leaves `BASE_ATTACK` weaponless: `m_weaponDamage[BASE]` keeps 1/2, and the AP multiplier is 2.0.

### 1.2 `Unit::GetAPMultiplier` (Unit.cpp:11051-11088)

| Branch | Value (all `float` literals) |
|---|---|
| not a player, or `IsInFeralForm() && !normalized` | `GetBaseAttackTime(att) / 1000.0f` |
| no usable weapon | `2.0f` |
| `!normalized` | `ItemDelay / 1000.0f` |
| normalized AXE2, MACE2, POLEARM, SWORD2, STAFF, FISHING_POLE | `3.3f` |
| normalized AXE, MACE, SWORD, WARGLAIVES, EXOTIC, EXOTIC2, FIST | `2.4f` |
| normalized DAGGER | `1.7f` |
| normalized THROWN | `2.0f` |
| normalized other (bow, gun, crossbow, wand, spear, misc) | `ItemDelay / 1000.0f` |

**Current data and the normalized constants.** `normalized = true` is set only by effect 121: 116 rows
snapshot-wide, 0 spells in the default scope and 8 in the class-skill-lines scope (all with base points 0 or 1, §4.3):
Fury of the Illidari ×2, Windburst, Consumption, Odyn's Fury ×2, Warbreaker and Apocalypse. For those the equipped
weapon's subclass picks the constant (3.3 for a two-hander, 2.4 for one-handers and warglaives, 1.7 for daggers; a bow
or gun falls to the delay branch). The other consumer is `MeleeDamageBonusDone`'s AP-bonus term, which has no in-scope
input (§3).

`IsInFeralForm` is true for forms {1 Cat, 5 Bear, 8 Dire Bear, 16 `FORM_GHOST_WOLF`} (Unit.cpp:9539-9543).
**Current Ghost Wolf (2645) applies `SpellShapeshiftForm` 48 ("Ghost Wolf"), not 16** [db2-fact], so Trinity's
feral branch never fires for it (SpellAuraDefines.h:778). This is only reachable in the class-skill-lines scope.
Build skew is not excluded, because the skew reference carries only `SpellName`.

### 1.3 `Player::CalculateMinMaxDamage` — exact formula (StatSystem.cpp:427-480)

```
attackPowerMod = max(GetAPMultiplier(att, normalized), 0.25f)                           :445
baseValue  = Flat(BASE_VALUE) + GetTotalAttackPowerValue(att, /*includeWeapon*/false) / 3.5f * attackPowerMod   :447
basePct    = Pct(BASE_PCT); totalValue = Flat(TOTAL_VALUE); totalPct = addTotalPct ? Pct(TOTAL_PCT) : 1.0f  :448-450
wmin/wmax  = GetWeaponDamageRange(att, MIN/MAX)   // OFF_ATTACK without an off-hand weapon -> 0.0f (Unit.cpp:9949-9955)
versa = 1.0f; AddPct(versa, GetRatingBonusValue(CR_VERSATILITY_DAMAGE_DONE) + float(GetTotalAuraModifier(SPELL_AURA_MOD_VERSATILITY)))  :455-457
if (form && form->CombatRoundTime)  w = w * CombatRoundTime / 1000.0f / attackPowerMod    :459-464  (every attType)
else if (!CanUseAttackType(att))    att != BASE -> min = max = 0, return; else w = 1.0f / 2.0f   :465-476
min = ((wmin + baseValue) * basePct + totalValue) * totalPct * versa                    :478-479
```

Types and boundaries:
- Everything is `float`. `CombatRoundTime` is `int16` (DB2Structure.h:4189) and converts exactly.
- The versatility percent is `float + float(int32)`.
- No Player or StatSystem setter exists for `UNIT_MOD_DAMAGE_*` `BASE_VALUE`/`BASE_PCT`, so players see 0.0f/1.0f
  [structural-inference].
- **Versatility is folded into the prepared min/max. It is not part of `MeleeDamageBonusDone`.**
- **The weapon's `dps*6` AP is excluded here**, because `includeWeapon=false`.
- `GetTotalAttackPowerValue` (Unit.cpp:9919-9947) computes `float ap = int32(AttackPower + ModPos + ModNeg)`, with the
  sum done in int32 and converted to float once.
- With `includeWeapon`, BASE/RANGED add `max<float>(MH, Ranged weapon AP)`; OFF computes `(ap + OH weapon AP) / 2`.
- `ap < 0` returns 0; otherwise the result is `ap * (1.0f + Multiplier)`.

Aura-modified stages (not equipment-only):
- `TOTAL_VALUE` = `SPELL_AURA_MOD_DAMAGE_DONE` (physical) + `ITEM_ENCHANTMENT_TYPE_DAMAGE` (+ TOTEM × delay/1000 for
  shamans) (Unit.cpp:9745-9773, Player.cpp:4921-4973).
- `TOTAL_PCT` = `factor` × Π `SPELL_AURA_MOD_DAMAGE_PERCENT_DONE` (physical, attack-fit) × (OFF only) Π
  `SPELL_AURA_MOD_OFFHAND_DAMAGE_PCT`, where **`factor = 0.5f` for OFF_ATTACK** (Unit.cpp:9781-9819; the literal is at
  :9793). **The off-hand penalty lives in `TOTAL_PCT`.** The off-hand AP term is not halved.
- `UpdateAttackPowerAndDamage` refreshes OFF_ATTACK only if an off-hand weapon exists and
  (`CanDualWield()` or `ITEM_FLAG3_ALWAYS_ALLOW_DUAL_WIELD` 0x80000) (StatSystem.cpp:410-412; ItemTemplate.h:281).
- `Unit::CalculateDamage` (Unit.cpp:2501-2551):
  - It reads `UnitData::Min/MaxDamage` unless `normalized || !addTotalPct`, in which case it recomputes the range.
  - Feral BASE adds the off-hand range.
  - It clamps at 0, swaps if inverted, and calls `urand(uint32(min), uint32(max))`, which truncates; `urand` is an
    inclusive `uniform_int_distribution` (common/Utilities/Random.cpp:42-47).

### 1.4 Shapeshift `CombatRoundTime` [db2-fact]

| Form | Name | CombatRoundTime | IsInFeralForm | Reachable |
|---|---|---|---|---|
| 1 | Cat Form | 1000 | yes | yes (Feral) |
| 5 | Bear Form | 2500 | yes | yes (Guardian) |
| 8 | Dire Bear Form | 2500 | yes | no applier located |
| 44 | Xuen, the White Tiger | **1** | no | no applier |
| 45 / 46 / 49 | Yu'lon / Chi-ji / Niuzao | 2000 | no | 426268 applies 46; in neither scope |

`InitDataForForm` sets BASE/OFF to CRT and RANGED to 2000 (Player.cpp:23384-23406). Because the CRT branch of
`CalculateMinMaxDamage` is not gated on the feral forms or on attack type, a non-feral CRT form scales every hand; the
Python originally gated RANGED on feral and has been fixed. For Cat/Bear the branch is an identity at the point of
use for **non-normalized** calls: `apMod = CRT/1000`, so `w * CRT / 1000 / apMod == w` in float for the witnesses. A
normalized call in a feral form uses the weapon's normalized speed as `apMod` (e.g. 3.3 for a staff), so the weapon
term is divided by it; no druid weapon-effect spell reaches this (§12).

### 1.5 Two-hand, Titan's Grip, dual wield

- `IsTwoHandUsed` (Player.cpp:13189-13198) is true for:
  - 2H without `CanTitanGrip`;
  - INVTYPE_RANGED;
  - INVTYPE_RANGEDRIGHT weapons other than wands.
- `CanEquipItem` off-hand gates (Player.cpp:10850-10871) run in this order:
  1. polearm → `WRONG_SLOT`;
  2. INVTYPE_WEAPON needs `CanDualWield`;
  3. WEAPONOFFHAND needs `CanDualWield` or flag 0x80000;
  4. 2H needs `CanDualWield && CanTitanGrip`;
  5. then **for any off-hand item, including shields and holdables**, `IsTwoHandUsed()` → `2HANDED_EQUIPPED`. The
     draft Python limited this gate to weapons; that is fixed.
- `CanTitanGrip`: an empty subclass mask allows every subclass (Player.cpp:13125-13147).
- `EffectDualWield` / `EffectTitanGrip`: SpellEffects.cpp:2237-2243 and 4954-4961. Unlearning resets them
  (Player.cpp:3344-3357).
- 46917 Titan's Grip: effect 155 has MiscValue 0, so there is no penalty spell and `CheckTitanGripPenalty` is a no-op
  [db2-fact].

**Where dual wield comes from.** The census is in `weapon-sources.json.capability_by_spec`; each grant is tagged
with the path that reaches it.

| Spec(s) | Grant | Path | Evidence |
|---|---|---|---|
| 72 Fury | 231842 | default Scope | db2-fact |
| 71 / 72 / 73 Warrior | 296087 (SkillLine 840, AcquireMethod 0) | **learned-spell character state only**: `LearnSkillRewardedSpells` skips AcquireMethod 0 (Player.cpp:25404-25417); listed in `capability_by_spec_learned_only`, counted only with `--learned-spell 296087` | trinity-consumer |
| 251 Frost DK | 674 | default Scope | db2-fact |
| 255 Survival | 1277760 | default Scope | db2-fact |
| 263 Enhancement | 86629 | default Scope | db2-fact |
| 268 / 269 Monk | 124146 | default Scope | db2-fact |
| **253 / 254 / 255 Hunter, 259 / 260 / 261 Rogue, 577 / 581 / 1480 Demon Hunter** | **674** | **default skill** | trinity-consumer |

Arms and Protection therefore have **no automatic dual wield**; Fury keeps 231842. The draft corpus counted 296087 as
a grant, which `weapon`, the Track C hook and `witnesses-c.json` then reported as `dual_wield: true` for 71/73. The
closeout review fixed this (see `character-preparation-closure.md` lead reconciliation, R2-2).

The default-skill path was the handoff's open question Q1, now resolved.
1. `ObjectMgr` loads every `SkillRaceClassInfo` row with `Availability == 1` into `PlayerInfo::skills`
   (Globals/ObjectMgr.cpp:4061-4070).
2. `LearnDefaultSkills` → `LearnDefaultSkill` (Player.cpp:25259-25300) → `LearnSkillRewardedSpells`
   (Player.cpp:25391-25440) then learns the ability.
3. The rows involved:
   - SRCI 131: skill 118 "Dual Wield", ClassMask 16396 = Hunter|Rogue|bit14, MinLevel 1.
   - SRCI 1661: ClassMask 18432 = Demon Hunter|bit14, race-restricted.
   - SkillLineAbility 610: spell 674, AcquireMethod 2 `AutomaticCharLevel`, ClassMask 2573 = 1|3|4|10|12, no
     `SpellLevels` row.

`dummy_semantics.scope.Scope` misses this path in both modes: its extended mode follows only SkillLine CategoryID 7,
and skill 118 has CategoryID 6. The implementation is `weapon.default_skill_grants`. Specs with no grant are 62-66,
70, and 102-105; they get `EQUIP_ERR_2HSKILLNOTFOUND` for a one-hand off-hand weapon.

## 2. Attack power and level constants

- `Player::UpdateAttackPowerAndDamage` (StatSystem.cpp:347-425) is **reused** from character-stat-pipeline-archaeology.md
  §5 and re-verified here:
  - melee: `max(STR*APperStr, 0) + max(AGI*APperAgi, 0)`, plus `AGI*APperStr` when the form has `Flags & 0x20` (:362-365);
  - ranged: `(level + max(AGI,0)) * RangedAPperAgi`;
  - `SPELL_AURA_OVERRIDE_ATTACK_POWER_BY_SP_PCT` → `CalculatePct(float(min SP), pct)`;
  - stored as `AttackPower = int32(base)`, `ModPos = int32(TOTAL_VALUE)`, `Multiplier = TOTAL_PCT - 1.0f`.
- The aura names `..._OF_ARMOR` / `..._OF_STAT_PERCENT` do not exist in the enum at this commit.
- **Contradiction with the charstats report.** character-stat-pipeline-archaeology.md:298-300 says *"`ITEM_MOD_ATTACK_POWER`
  and the weapon's `int32(dps * 6)` land in `TOTAL_VALUE`, i.e. they are reported as `AttackPowerModPos`"*. Only
  `ITEM_MOD_ATTACK_POWER` does (Player.cpp:8022-8024). The weapon's `int32(dps * 6.0f)` goes to the dedicated
  `UnitData::*WeaponAttackPower` fields (Player.cpp:8163-8176) and is added only by `GetTotalAttackPowerValue(att, true)`.
  - It therefore does **not** enter white-swing min/max (StatSystem.cpp:447 passes `false`).
  - It **does** enter AP-coefficient spells (Unit.cpp:6872).
  - An OFF_ATTACK AP-coefficient spell uses `(AP + OH weapon AP)/2` (Unit.cpp:9938-9941).
  - The gearing report (gearing-pipeline-archaeology.md:498, "per-hand weapon attack power") is consistent with this.
- Level constants that reach weapon/damage formulas:

| Constant | Value | Source | Evidence |
|---|---|---|---|
| `GetMaxLevelForExpansion(EXPANSION_MIDNIGHT)` | 90 | Miscellaneous/SharedDefines.h:101, 109-140 | trinity-consumer |
| `ExpectedStat.ArmorConstant` (Lvl 90, ExpansionID −2) | 3430.0 | ExpectedStat row 475 | db2-fact |
| `ExpectedStat.CreatureArmor` (same row) | 1470.0 | ExpectedStat row 475 | db2-fact (before `creature_template_difficulty.ArmorModifier`) |
| max-level armour curve | `GlobalCurve 18 → CurveID 27400` of `AvgItemLevel[EquippedBase] − ItemLevelByLevel[90]` | Unit.cpp:1756-1765 | trinity-consumer; **`ItemLevelByLevel.txt` absent → unresolved** |
| ArP cap level term | `L < 60 ? 400+85L : 400+85L+4.5f·85·(L−59)` | Unit.cpp:1727-1731 | trinity-probe |
| glancing step | `0.1f` per level, cap 3 | Unit.cpp:1474-1479 | trinity-probe |
| `MAX_LEVEL` (scalable creature clamp `+3`) | 123 | DataStores/DBCEnums.h:45; Creature.cpp:3157 | trinity-consumer |

- Level-90 AP for the witnesses is **unresolved**: `player_classlevelstats` stops at level 80
  (world-db-corpora/player-base-stats.json). Witnesses therefore report min/max at AP = 0 plus the exact per-AP-point
  coefficient `apMod/3.5f`.

## 3. White-swing damage arithmetic (types and boundaries)

Corpus: `damage-arithmetic.json` (16 stage rows, 6 type-boundary rows, 9 discriminating inputs). CLI:
`weapon_combat.py damage --hand mh|oh|ranged --roll R --outcome O --armor N [--level N] [--armor-curve-factor F] [--mods JSON]`
prints every intermediate and exits 2 with `unresolved` at level 90 without a curve factor.

| # | Stage | Input → arithmetic → output | Rounding / conversion | Coordinates | Evidence |
|---|---|---|---|---|---|
| 1 | `CalculateDamage(att, false, true)` | float UD range → `urand` | `uint32(float)` truncation | Unit.cpp:1382-1383, 2544-2550 | trinity-probe |
| 2 | `MeleeDamageBonusDone` | uint32 → `int32` parameter; `DoneFlatBenefit` int32 (`MOD_DAMAGE_DONE_CREATURE` + `int32(APbonus/3.5f*GetAPMultiplier)`); `float DoneTotalMod` = [non-physical school only: player `ModDamageDonePercent[max]`] × Π `AddPct(MOD_AUTOATTACK_DAMAGE)` × `DONE_VERSUS` × `VERSUS_AURASTATE` × `BY_TARGET_AURA_MECHANIC`; `float(damage+flat)*mod` | `int32(max(damageF, 0.0f))` | Unit.cpp:8020-8127 (damageF :8121) | trinity-probe (tail re-typed) |
| 3 | `MeleeDamageBonusTaken` | int32 flat (`MOD_DAMAGE_TAKEN` by the attacker's **BASE_ATTACK** school, :8140; `MOD_MELEE_DAMAGE_TAKEN`); `pdamage < -flat` → 0; float product `PERCENT_TAKEN` × `MELEE_DAMAGE_FROM_CASTER` × `AddPct(45182)` × `MELEE_DAMAGE_TAKEN_PCT`; versatility `AddPct(-(rating + aura/2.0f))` gated on `GetSpellModOwner()` (players **and pets via their owner**); if `< 1.0f` rewrite `1 − AddPct(1 − mod, −IGNORE_TARGET_RESIST)` | `int32(max(tmp, 0.0f))` | Unit.cpp:8132-8240 (tmp :8237) | trinity-probe (tail re-typed incl. the rewrite) |
| — | `sScriptMgr->ModifyMeleeDamage` | script hook | — | Unit.cpp:1387 | not modelled |
| 4 | `CalcArmorReducedDamage` (physical mask only) | `float(GetArmor())` × `GetArmorMultiplierForTarget` → `CalculatePct<float,double>(armor, 100 − min(bypass,100))` → `+ int32 MOD_TARGET_RESISTANCE` → per `IGNORE_TARGET_RESIST` `floor(AddPct)` → **TYPEID_PLAYER only**: ArP cap block → `G3D::fuzzyLe(armor, 0)` (double, eps `5e-7·(|a|+1)`) → `armorConstant = EvaluateExpectedStat(ArmorConstant, lvl, −2, 0, CLASS_NONE for player-owned, 0)` → **if `GetCharmerOrOwnerPlayerOrPlayerItself()` (players and player-owned pets) and lvl == 90**: `× Curve27400(ilvlDelta)` → `min(armor/(armor+C), 0.85f)` | `uint32(max(damage*(1.0f−m), 0.0f))`; `CleanDamage += damage − reduced` | Unit.cpp:1390-1396, 1675-1682, 1684-1774 | trinity-probe (ArP cut + tail cut) |
| 5 | `RollMeleeOutcomeAgainst` | supplied input (agent D owns probability) | — | Unit.cpp:1398, 2389-2499 | — |
| 6 | outcome switch | see below | uint32 | Unit.cpp:1402-1494 | trinity-probe (whole switch extracted) |
| 7 | resilience | `CanApplyResilience = !IsVehicle() && GetOwnerGUID().IsPlayer()`; a Player's `GetOwnerGUID()` is `SummonedBy`, which is empty | int32 | Unit.cpp:1499-1505, 12416-12419; Unit.h:1190 | legacy-only (player attacker) |
| 8 | `CalcAbsorbResist` | when `int32(Damage) > 0`; `DamageInfo` copy | uint32 | Unit.cpp:1507-1524 | not modelled (victim auras) |
| 9 | `DealMeleeDamage` → `DealDamage(uint32)` | — | uint32 | Unit.cpp:1570-1571 | trinity-consumer |

Outcome switch (`CalcDamageInfo` fields are all `uint32`, Unit.h:530-549):
- **crit** (Unit.cpp:1424-1440): `Damage *= 2` (uint32, :1430), then
  `mod = (GetTotalAuraMultiplierByMiscMask(MOD_CRIT_DAMAGE_BONUS 163) − 1.0f) * 100` (:1433), then `AddPct<uint32,float>`
  if non-zero.
  - A 1.3 multiplier gives `mod` = 29.9999962f (binary32), so 1000 → **2599**, not 2600 (probe; agent D pins the same).
  - The ×2 applies to every attacker; there is no 1.5.
  - A product below 1 makes `CalculatePct` convert a **negative float to uint32**. That is undefined behaviour; g++13
    on x86-64 wraps, so AddPct subtracts (probe: 1000 at ×0.5 → 1000).
  - Negative aura-163 amounts exist only on 139883 and 154429, and neither is in scope.
- **block** (Unit.cpp:1455-1469): `Blocked = CalculatePct(Damage, Target->GetBlockPercent(GetLevel()))` (:1459).
  - A creature victim returns `30.0f` percent (Unit.h:987).
  - **A player victim returns the fraction** `min(ShieldBlock/(ShieldBlock+ArmorConstant), 0.85f)`
    (Player.cpp:26822-26831). Fed in as a percent, this blocks ≤ 0.85 % of the hit: ShieldBlock 2000 with C 3430
    blocks 368 of 100000 [trinity-probe].
  - Critical block: `Target->IsBlockCritical()` (the victim's aura 253) doubles `Blocked`, and then
    **`Blocked *= GetTotalAuraMultiplier(MOD_CRITICAL_BLOCK_AMOUNT 638)` is read from the attacker (`this`)**
    (Unit.cpp:1460-1464; the spell path repeats this at :1243) [trinity-probe].
  - In scope, 76857 Mastery: Critical Block (aura 253, spec 73) and 429638 Martial Expert (aura 638, specs 71/73)
    exist. Under this consumer, Martial Expert never scales the warrior's own blocks.
- **glancing** (Unit.cpp:1470-1483): `leveldif = min(int32(victim->GetLevel()) − int32(GetLevel()), 3)` (:1474), then
  `Damage = uint32((1.f − leveldif*0.1f) * Damage)`. `CleanDamage` would wrap as uint32 when `leveldif < 0`, but that is
  unreachable from the chance path: a scalable creature's raw level is `ScalingLevelMax + delta` (Creature.cpp:1618),
  which is at least its `GetLevelForTarget`, so a selected glancing blow always has `leveldif ≥ 4 → 3` and the factor
  `0.69999999f` (1000 → 700, probe; R2-5).
- **crushing** (Unit.cpp:1484-1490): `Damage += Damage/2` (integer).
- **parry/dodge** (Unit.cpp:1441-1454): `CleanDamage += Damage`, then `Damage = 0`.
- **miss/evade** (Unit.cpp:1404-1419): 0; evade returns early. All branches set `OriginalDamage`.

Reachability, judged from data rather than from the code:

| Mechanic | Needed input | Current data | Class |
|---|---|---|---|
| glancing | player/pet vs non-player/non-pet with `attacker+3 < victim` (both `GetLevelForTarget`) | `Creature::GetLevelForTarget` = `clamp(90+Δplayer, CTmin, CTmax) + LevelScalingDelta`, clamp `[1, 126]` (Creature.cpp:3131-3160). **599** `creature_template_difficulty` rows (lead extraction, 320,629 rows) have delta ≥ 4 with a ContentTuningID; whether any CT range reaches 90 needs `DB2Manager::GetContentTuningData` | **unresolved** (structurally reachable, not legacy-only) |
| crushing | `!IsControlledByPlayer()` attacker ≥ victim+4 | player attackers never; creature attackers never either, because the chance expression `attackerLevel - victimLevel * 1000 - 1500` (Unit.cpp:2489) is negative for every level | legacy-only (dead code for every attacker; §7.4 T2, R2-3) |
| `CR_ARMOR_PENETRATION` rating | `ITEM_MOD_ARMOR_PENETRATION_RATING` 44 (Player.cpp:8037) or `MOD_RATING` bit 24 | 0 ItemSparse rows (any expansion) and 0 enchants with stat 44; bit 24 not in scope | legacy-only |
| `MOD_ARMOR_PENETRATION_PCT` 280 | aura | 450990 Martial Precision (specs 268/269) | live |
| `BYPASS_ARMOR_FOR_CASTER` 345 | aura | 403695 Truth's Wake (spec 70) | live |
| `MOD_OFFHAND_DAMAGE_PCT` 122 | aura | 0 spells in either scope | inert in current scope: OFF `TOTAL_PCT` = 0.5 × physical `DAMAGE_PERCENT_DONE` |
| `MOD_AUTOATTACK_DAMAGE` 344 | aura | 57 (default) / 58 (class lines) | live |
| `MOD_CRIT_DAMAGE_BONUS` 163 | aura | 5: 388118, 459783, 1251026, 1258209, 1272989 | live |
| `MOD_IGNORE_TARGET_RESIST` 269 / `MELEE_DAMAGE_FROM_CASTER` 343 / `DONE_VERSUS_AURASTATE` 303 | aura | 5/9, 2/2, 1/1 | live |
| `DAMAGE_DONE_CREATURE` 59, `MELEE_AP_VERSUS` 102, `MELEE_AP_ATTACKER_BONUS` 165, `TARGET_RESISTANCE` 123, `DAMAGE_TAKEN` 14, `MELEE_DAMAGE_TAKEN(_PCT)` 125/126, `BY_TARGET_AURA_MECHANIC` 249, `DONE_FOR_MECHANIC` 276 | aura | 0 in both scopes | no player-scope input |
| resilience | PvP victim | — | legacy-only / PvP |

Discriminating inputs (all probe-confirmed):
- `CalculatePct(16777219, 30)` is 5033166 in binary32 but 5033165 in a double oracle; `123456789 @ 30` gives
  37037040 vs 37037036.
  - **Correction to the draft:** `16777217 @ 30` does *not* discriminate, because 5033164.8 rounds to 5033165.0f.
  - **Lower bound (synthesis closeout review, R4-3):** at 30 % the first base where binary32 and a double oracle differ is 2,236,990 (671,096 vs 671,097), confirmed by binary32 emulation and `tc_weapon_probe`.
- The versatility `AddPct(1.0f, 3.25f)` and the AP term `12345/3.5f*2.6f` are exercised by `minmax`.
- Armor: `100000` at armor 5000 and C 3430 → 40688.
- `std::round(0.49999999999999994)` = 0, whereas `floor(x+0.5)` gives 1.
- A crit multiplier of 1.3 gives 2599, not 2600.

## 4. Special weapon attacks

### 4.1 `Spell::EffectWeaponDmg` (SpellEffects.cpp:2812-2949) [trinity-probe: loops cut verbatim]
- **When it runs:** only at `LAUNCH_TARGET`, and only on the **last** weapon effect in the target's effect mask
  (:2825-2843).
- **Loop 1** (:2864-2883):
  - `double fixed_bonus += CalculateDamage(eff)` for 58/17/121; 121 also sets `normalized`.
  - For 31: `ApplyPct(float weaponDamagePercentMod, double value)`. `CalculatePct<float,double>` casts the value to
    float (common/Utilities/Util.h:72).
  - `Spell::CalculateDamage` returns `SpellEffectValue` = `double` (Spell.h:546; SpellDefines.h:490).
  - `CalcValue` has already `std::round`ed 17/31/58/121 and clamped the result to Min/MaxValue
    (Spells/SpellInfo.cpp:622, 659).
- **`addPctMods`** = `!ATTR6_IGNORE_CASTER_DAMAGE_MODIFIERS && (m_spellSchoolMask & NORMAL)` (:2889). When true,
  `fixed_bonus *= float TOTAL_PCT`.
- **The roll:** `weaponDamage = double(CalculateDamage(m_attackType, normalized, addPctMods))` (:2908).
  - **When `addPctMods` is false, the range is recomputed with `totalPct = 1`.** That also removes the **0.5 off-hand
    factor**, because it lives in `TOTAL_PCT`.
  - Probe check: OFF + ATTR6 with wmin 100, AP 3500, a 2.6 s weapon and +10 gives 2710, not a halved value.
- **Loop 2** (:2911-2935) walks the effects **again in index order**:
  - it runs `+= fixed_bonus` once per 58/17/121 effect and `*= weaponDamagePercentMod` once per 31 effect;
  - since those are already the sum and the product, **spells with two fixed or two percent effects double-count**
    (probe: 58+58 of 10 on 1234 gives 1274, not 1254);
  - **the order of 31 vs 58 matters**: (31,58) gives 1861 and (58,31) gives 1866 on roll 1234.
- **Tail:** `+= spell_bonus (0)`, `*= totalDamagePercentMod (1.0f)`, then `max(std::round(x), 0.0)` (:2941). The double
  then goes to `MeleeDamageBonusDone(int32)` as a truncating conversion (:2944), and
  `m_damage (int32, Spell.h:818) += MeleeDamageBonusTaken(...)` (:2945).
- `MeleeDamageBonusDone` also runs `Spell::CallScriptCalcDamageHandlers` (Unit.cpp:8116-8119); no spell script is
  bound to any of the 11 in-scope weapon specials at this commit (checked in `src/server/scripts`).
- **Bonus-done for spells** differs from white swings:
  - it uses `spellProto->GetSchoolMask()` and `DONE_FOR_MECHANIC`;
  - it applies the `HealingAndDamage` spellmod to `damageF`;
  - `normalized` for APbonus = the spell has effect 121.
- **Legacy:** the Shaman script override 5634 → 38430 (:2849-2857) is legacy-only.

### 4.1a After `EffectWeaponDmg`: `Unit::CalculateSpellDamageTaken` (Unit.cpp:1193-1257) [trinity-consumer]
`Spell` passes `m_damage` with `blocked = (MissCondition == SPELL_MISS_BLOCK)` (Spell.cpp:2924). Armor and outcome are
applied here, **after** the bonus stages; the white swing applies them in the opposite order. The MELEE/RANGED branch:
- **Skip condition:** `SPELL_ATTR4_IGNORE_DAMAGE_TAKEN_MODIFIERS` skips everything below (:1205).
- **Armor:** `CalcArmorReducedDamage(..., spellInfo, attackType)` (:1208), which adds the `TargetResistance` spellmod and
  `ATTR0_CU_IGNORE_ARMOR`.
- **Crit:**
  - `uint32 crit_bonus = damage` (:1222), then the `CritDamageAndHealing` spellmod (:1225), then
    `damage += crit_bonus` (:1226);
  - then `AddPct<int32,float>` of the aura-163 product (:1229-1232). A 1.3 multiplier gives 2599 here too.
- **Block:**
  - `uint32 value = victim->GetBlockPercent(GetLevel())` (:1239) **truncates the percent first**. A creature gives 30.
    **A player's fraction gives 0, so a player victim never blocks any part of a weapon special under this consumer.**
  - A critical block runs `value *= 2` and `value *= GetTotalAuraMultiplier(MOD_CRITICAL_BLOCK_AMOUNT)` on the
    **attacker** (:1242-1243).
  - `blocked = CalculatePct(damage, value)` (:1246); `damage <= blocked` → full block (:1247-1251).
  - A partial block still deals damage; only `SPELL_ATTR3_COMPLETELY_BLOCKED` stops the hit (Spell.cpp:2763).
- **Resilience:** (:1255-1256) legacy-only / PvP.

The Python mirror is `damage.spell_weapon_damage_taken`. It is unit-tested and agrees with agent D's
`attack-table.json` `numeric_rules` (Unit.cpp:1239). It is not probe-extracted, because the branch needs
`SpellNonMeleeDamage` and spellmod plumbing.

### 4.2 Attack type and equipment
- `SpellInfo::GetAttackType` (SpellInfo.cpp:1931-1955):
  - MELEE → OFF if `ATTR3_REQUIRES_OFF_HAND_WEAPON`, else BASE;
  - RANGED → `IsRangedWeaponSpell ? RANGED : BASE`;
  - otherwise → `AUTO_REPEAT ? RANGED : BASE`.
- It is fixed at `Spell::Spell` (Spell.cpp:491).
- `IsRangedWeaponSpell` (SpellInfo.cpp:1904-1909) is true for any of:
  - the hunter family without `Flags[1] & 0x10000000`;
  - a subclass mask containing bow/gun/crossbow;
  - `ATTR0_USES_RANGED_SLOT`.
- **The AP-coefficient path picks its attack type differently** (Unit.cpp:6861-6870):
  - RANGED for any ranged-weapon spell whose DmgClass is not MELEE;
  - OFF only if `REQUIRES_OFF_HAND && !REQUIRES_MAIN_HAND`.
  - It then adds `int32(stack * coeff * (attackerBonus + GetTotalAttackPowerValue(att)))`, **including weapon AP**
    (:6851-6873).
- Next swing: `IsNextMeleeSwingSpell` = `ATTR0_ON_NEXT_SWING | ON_NEXT_SWING_NO_DAMAGE` (SpellInfo.cpp:1899-1902),
  which maps to `CURRENT_MELEE_SPELL` (Spell.cpp:8157-8160). **0 such spells in either scope.** Scheduling is agent D's.

### 4.3 Census (`special-attacks.json`)
Snapshot-wide rows:

| Effect | Rows |
|---|---|
| 58 | 307 |
| 17 | 6 |
| 121 | 116 |
| 31 | 7814 |

- These span 7,937 spells; 91 of them are newer than the Trinity build [build-skew].
- 9 spells (DifficultyID 0) carry more than one fixed effect or more than one percent effect: 56920, 110956, 129375,
  131976, 246092, 251601, 316037, 324836, 332292. This ignores per-target effect masks.

| Scope | Reachable spells | Weapon-effect spells | AP-coef spells (no weapon effect) | Next-swing |
|---|---|---|---|---|
| default `Scope()` | 4,836 | **2** (31 ×2; BASE 1, RANGED 1; addPctMods=false 1) | 210 (210) | 0 |
| `Scope(include_class_skills=True)` | 8,975 | **11** (121 ×8, 31 ×11; BASE 6, OFF 3, RANGED 2; fixed+pct 8; multi 0; addPctMods=false 4) | 288 (287) | 0 |

The class-skill-lines scope is the meaningful one for B5, because baseline class abilities are learned through
SkillLineAbility rather than through spec spells. The default-skill path (§1.5) adds no weapon-effect spell.

| Spell | Name | Scope | Specs | Effects (index:effect bp) | Attack | School | addPctMods | Divergence from the white-swing primitive |
|---|---|---|---|---|---|---|---|---|
| 114089 | Windlash | default | 262-264 | 0:31 416 | BASE | 1 | yes | — |
| 114093 | Windlash Off-Hand | lines | 262-264 | 0:31 416 | OFF | 1 | yes | OH range + 0.5 via `MinOffHandDamage` |
| 201628 | Fury of the Illidari | lines | 577/581/1480 | 0:121 0, 1:31 38 | BASE | 127 | yes | normalized recompute |
| 201789 | Fury of the Illidari | lines | 577/581/1480 | 0:121 0, 1:31 38 | OFF | 127 | yes | normalized recompute, OFF with 0.5 |
| 204147 | Windburst | lines | 253-255 | 0:31 272, 1:121 0 | RANGED | 1 | yes | pct before fixed (fixed bp 0 → inert) |
| 205223 | Consumption | lines | 250-252 | 0:121 0, 1:31 87 | BASE | 1 | yes | normalized |
| 205546 | Odyn's Fury | lines | 71-73 | 0:121 0, 1:31 91 | BASE | 4 (frost) | **no** | no TOTAL_PCT; frost `ModDamageDonePercent` in bonus-done |
| 205547 | Odyn's Fury | lines | 71-73 | 0:121 0, 1:31 91 | OFF | 4 (frost) | **no** | **no 0.5 off-hand factor** |
| 209577 | Warbreaker | lines | 71-73 | 0:31 119, 1:121 0 | BASE | 32 | no | pct before fixed (inert); no TOTAL_PCT |
| 220143 | Apocalypse | lines | 250-252 | 0:121 1, 1:31 306 | BASE | 1 | yes | `(roll + 1·TOTAL_PCT) × 3.06` |
| 467718 | Bleak Arrows | default | 253/254 | 0:31 300 | RANGED | 32 | no | no ranged TOTAL_PCT; shadow pct in bonus-done |

All 11 share the `Unit::CalculateDamage` + `MeleeDamageBonusDone/Taken` primitive; they diverge from the white swing
only through `normalized`, `addPctMods` and the spell-specific bonus-done branches listed. `weapon_combat.py special
[--spec ID] [--spell ID]` prints the rows. The oracle is `damage.special_weapon_damage(weapon_roll, effects=[(Effect, value)…])`.

## 5. Witnesses (weapon/damage view)

### 5.1 Prepared parameters
Source: `witnesses-c.json`, 15 witnesses. Items come from current-gear-corpus.json at Raid_Normal (context 3),
ilvl 292, player level 90, AP 0.
- `apm` = AP multiplier, and `n` = the normalized speed.
- `per` = the exact per-AP-point coefficient added to both bounds (`max(apm,0.25f)/3.5f`).
- Bounds are the `urand` arguments.

| Spec | Items (subclass, delay, dps) | BASE bounds / apm / n / per | OFF bounds (total_pct 0.5) | RANGED | Capability |
|---|---|---|---|---|---|
| 72 Fury | 268214 SWORD2 3.6 s 95.97 + 268213 AXE2 3.6 s | [259,432] 3.6 / 3.3 / 1.02857 | [112,233] | [1,2] placeholder | DW 231842, TG 46917 (mask 0) |
| 71 Arms | 268214 SWORD2 | [259,432] 3.6 / 3.3 | none → [0,0] | [1,2] | no automatic DW (296087 learned-only), 2H used |
| 260 Outlaw | 268202 SWORD 2.6 s 72.46 + 268208 AXE | [141,236] 2.6 / 2.4 / 0.74286 | [61,127] | [1,2] | DW 674 (default skill) |
| 259 Assassination | 268204 + 268264 DAGGER 1.8 s | [97,163] 1.8 / 1.7 / 0.51429 | [48,81] | [1,2] | DW 674 (default skill) |
| 263 Enhancement | 268208 AXE + 268206 MACE | [122,254] 2.6 / 2.4 | [82,106] | [1,2] | DW 86629 |
| 251 Frost DK (2H) | 268213 AXE2 | [224,466] 3.6 / 3.3 | [0,0] | [1,2] | DW 674 |
| 251 Frost DK (DW) | 268208 + 268206 | [122,254] | [82,106] | [1,2] | DW 674 |
| 269 Windwalker | 270930 FIST ×2 | [155,221] 2.6 / 2.4 | [77,110] | [1,2] | DW 124146 |
| 103 Feral (Cat) | 268199 STAFF 3.6 s | [293,397] bat 1000, apm **1.0**, n 3.3, per 0.28571 | [0,0] bat 1000 | **[0,1]** (CRT branch with apm 2.0) | none |
| 104 Guardian (Bear) | 268199 STAFF | [293,397] bat 2500, apm 2.5, per 0.71429 | [0,0] | [1,2] | none |
| 253 BM (bow) | 268207 BOW 3.0 s | BASE weaponless [1,2], apm 2.0 | [0,0] | [244,331] apm 3.0, n 3.0 | DW 674 (default skill) |
| 254 MM (gun) | 268200 GUN 3.0 s | [1,2] | [0,0] | [201,374] | DW 674 (default skill) |
| 70 Retribution | 268198 MACE2 3.6 s | [302,389] 3.6 / 3.3 | [0,0] | [1,2] | none |
| 64 Frost Mage | 268205 STAFF 47.99 dps | [146,199] (prepared, unused) | [0,0] | [1,2] | none |
| 73 Protection | 268209 AXE + 268262 shield | [131,245] 2.6 / 2.4 | shield → null weapon, [0,0] | [1,2] | no automatic DW (296087 learned-only); ShieldBlock 2427 |

### 5.2 Damage view and cross-checks
- **Damage view:** the main hand at the lower `urand` bound, a normal hit on armor 1470 (`ExpectedStat.CreatureArmor`)
  at level 90. It is **unresolved** for every witness because the curve factor needs `ItemLevelByLevel`.
  - The conditional result with the curve factor fixed at 1.0 (Curve 27400 is 1.0 for delta ≤ 17): Fury 181, Arms 181,
    Outlaw 98, Assassination 67, Enhancement 85, Frost DK 156 / 85, Windwalker 108, Feral 205, Guardian 205, BM 170,
    MM 140, Retribution 211, Frost Mage 102, Protection 91.
- **Weapon AP** (`int32(dps·6)`, excluded from these bounds): 575 for 2H/bow/gun, 434 for one-handers, 287 for the
  caster staff.
- **Hook for Track C:** `weapon.CHARACTER_PREP_HOOK(context)` implements `character_prep/compiler.py:97 WEAPON_HOOK`.
  It returns the same per-hand parameters at AP = 0 for a compiled fixture; the Outlaw pair gives BASE [141,236] and an
  OFF total_pct of 0.5.

### 5.3 Probe (`tools/tc_weapon_probe`)
`extract.py` pulls three kinds of text from the checkout.
- **Whole bodies:** `GetAPMultiplier`, `GetTotalAttackPowerValue`, `GetWeaponDamageRange`, `CalculateDamage`,
  `Player::CalculateMinMaxDamage`, `Player::GetBlockPercent`, the Util.h percent templates and the G3D `eps`/`fuzzyLe`.
- **Enums:** SharedDefines, Unit.h, UnitDefines, DBCEnums, SpellAuraDefines and ItemTemplate.
- **Documented cuts:**
  - the ArP block and the tail of `CalcArmorReducedDamage`;
  - the whole outcome `switch` of `CalculateMeleeDamage`;
  - **new:** both effect loops of `EffectWeaponDmg` through the rounding. Its three locals above the cut are re-typed
    with their source initialisers.

The `bonus`, `taken` and `special` commands are **re-typed**; `taken` now includes the "Sanctified Wrath" rewrite.
Two inherited scaffolding bugs were fixed:
- `flat[MAX_ATTACK][2]` was indexed with `TOTAL_VALUE == 2`, which is out of bounds. Earlier `minmax` agreement had
  been aliasing.
- The critical-block multiplier was stubbed on the victim; it now sits on the attacker.

`tests/test_wc_differential.py` has 19 tests, mostly hypothesis-driven at 150 examples each. All agree bit-exactly
(`%a`) with the Python oracle for `apmult`, `minmax`, `calcdamage`, `apvalue`, `arpcap`, `armor`, `outcome`,
`blockpct`, `pct`, `addpctf`, `bonus`, `taken` and `wdmg` [differential].

## 6. Swing scheduling

Weapon speed, weapon damage and attack power are inputs owned by part C. This section covers when a swing happens.
The executable oracle is `weapon_combat/swing.py`. Its corpus is `swing.json`, which holds 30 ordered rules (S01–S30), 6 numeric records and 12 timelines.
Every rule has class `trinity-consumer`.

### 6.1 State and types

| member | type | units | init | coordinate |
|---|---|---|---|---|
| `m_baseAttackSpeed[MAX_ATTACK]` | `std::array<uint32,3>` | ms | `{}` | Unit.h:1519, U:325 |
| `m_modAttackSpeedPct[MAX_ATTACK]` | `std::array<float,3>` | multiplier (1.0 = unhasted) | `fill(1.0f)` | Unit.h:1520, U:327 |
| `m_attackTimer[MAX_ATTACK]` | `std::array<uint32,3>` | ms remaining | `{}` (**ready**) | Unit.h:1521, U:326 |
| `isAttackReady(t)` | `timer == 0` | — | — | Unit.h:700 |
| `ATTACK_DISPLAY_DELAY` | `#define 200` | ms | — | Unit.h:630 |
| `BASE_ATTACK_TIME` | `#define 2000` | ms | — | UnitDefines.h:35 |
| `GetBaseAttackTime` | returns `uint32` | ms | — | Unit.h:831 |

`haveOffhandWeapon()` (U:525-531) has two meanings:
- **Players:** `GetWeaponForAttack(OFF_ATTACK, useable=true) != nullptr`. The off-hand slot must hold a non-broken `ITEM_CLASS_WEAPON` that is not a ranged weapon (P:9582-9612).
- **Creatures:** `CanDualWield()` (Unit.h:702).

### 6.2 Update order (one `Unit::Update(p_time)`)

1. `WorldObject::Update` → `m_Events.Update(diff)` (Object.cpp:245-247). Pending `SpellEvent`s execute here, including queued auto-repeat casts (U:428).
2. `_UpdateSpells` (U:433) → `_UpdateAutoRepeatSpell` when `CURRENT_AUTOREPEAT_SPELL` is set (U:2962-2963).
3. `m_combatManager.Update` (U:439).
4. `_lastDamagedTargetGuid` is cleared (U:441). Then, **only if `_lastExtraAttackSpell` is set**, queued extra attacks are drained: `HandleProcExtraAttackFor` → `AttackerStateUpdate(victim, BASE_ATTACK, extra=true)` (U:442-454, 2365-2372).
5. Timer decrement (U:456-469):
   - For BASE, OFF and RANGED: `if (t) t = p_time >= t ? 0 : t - p_time`. This is a saturating `uint32` decrement.
   - The whole step is skipped while the current GENERIC or CHANNELED spell has `SPELL_ATTR6_DELAY_COMBAT_TIMER_DURING_CAST` (0x00080000, SharedDefines.h:678).
6. `Player::Update` (P:919, `Unit::Update` at P:944) and `Creature::Update` (Creature.cpp:825/848) then call `DoMeleeAttackIfReady` in the same tick (P:1012, Creature.cpp:876).

**Tick (S27).** There is no fixed combat tick.
- `Unit::Update` receives the map diff.
- The world loop sleeps to `MinWorldUpdateTime` (default 1 ms, Main.cpp:537). `MapUpdateInterval` defaults to 10 ms (World.cpp:721).
- `UpdateSessions` (World.cpp:2268) runs before `sMapMgr->Update` (World.cpp:2312).
- The oracle therefore takes the tick as a **host parameter** (`--tick`). Events are applied at the start of the update in which they fall.

### 6.3 `DoMeleeAttackIfReady` and the MH/OH interleave (U:2176-2248)

**Gates, in order:**
1. `UNIT_STATE_MELEE_ATTACKING`
2. not `CHARGING`
3. creature `CanMelee()` (Creature.h:232)
4. `UNIT_STATE_CASTING` returns early, unless the current channel has `SPELL_ATTR5_ALLOW_ACTIONS_DURING_CHANNEL`. Four spells in scope have that attribute: 101546, 386731, 443028, 1263824.
5. a victim exists

**Swing error.** Out of melee range, or outside a 2π/3 arc beyond the boundary radius, gives an error. The timer is then set to **100 ms** rather than reset (U:2225, 2245). Players also get `SetAttackSwingError` (U:2227-2228).

**Interleave (S12), when BASE is ready:**
1. If there is an off hand and `OFF < 200`, set `OFF = 200` (U:2215-2217).
2. `AttackerStateUpdate(BASE)` (U:2221).
3. `resetAttackTimer(BASE)` (U:2222).

Then, in the same call, if the unit is not feral, has an off hand and `OFF == 0`:
1. If `BASE < 200`, set `BASE = 200`.
2. Swing OFF.
3. Reset OFF (U:2231-2242).

**Consequences:**
- MH and OH **never swing in the same update**. When both reach 0 together, MH wins and OH moves 200 ms later.
- A dual-wielding player starts with both timers at 0 (S02). There is no half-swing offset for players (U:5944 is non-player only). The first update swings MH, and OH follows `ceil(200/tick)` updates later:

  ```text
  python3 weapon_combat.py swing --mh-speed 2600 --oh-speed 2600 --duration 6000 --brief
  -> base 0, off 200, base 2600, off 2800, base 5200, off 5400
  ```

**Suppressed swings still cost a period (S28).** The timer is reset *after* `AttackerStateUpdate`, even when that function returns early for any of these reasons (U:2264-2280):
- `UNIT_FLAG_PACIFIED`
- `UNIT_STATE_CANNOT_AUTOATTACK` (non-extra swings only)
- aura 264 `DISABLE_ATTACKING_EXCEPT_ABILITIES` or aura 371 `DISABLE_AUTOATTACK` (0 of each in scope)
- dead victim
- no line of sight

**Casting and timers (S29).** A cast blocks the swing but not the timer: the timer saturates at 0, and the swing fires on the first update after the cast ends. A cast window from 2000 to 4000 ms moves the 2600 ms swing to 4000 ms.

### 6.4 Attack start, target switch, stop

**`Unit::Attack`** (U:5852-5962):
- Never touches the MH timer.
- For **non-players** with an off hand: `OFF = max(OFF, BASE + uint32(CalculatePct(GetBaseAttackTime(BASE), 50)))` (U:5944-5946). `CalculatePct<uint32,int>` is `uint32(base*50.0f/100.0f)` (Util.h:72-75).
  - With `tick = 100`, a creature's first OH swing lands at 900 ms, because the offset is decremented in the same update.
- Switching target interrupts `CURRENT_MELEE_SPELL` and keeps both timers (U:5909-5912).

**`AttackStop`** (U:5964-5988) and **`CombatStop`** (U:6011-6044) clear `MELEE_ATTACKING` and do **not** reset timers. A unit that re-engages resumes with the time that was left on each timer; a timer at 0 means an immediate swing.

### 6.5 Haste

**Arithmetic.** `ApplyAttackTimePercentMod(att, float val, bool apply)` (U:10983-11007):

```cpp
remainingTimePct = float(timer) / (base * modPct);                   // uint32*float -> float
val > 0 ? ApplyPercentModFloatVar(modPct, val, !apply)               // var *= 100/(100+val)
        : ApplyPercentModFloatVar(modPct, -val, apply);              // var *= (100+|val|)/100   (U:10978-10981)
UpdateAttackTimeField(att);  // AttackRoundBaseTime[att] / RangedAttackRoundBaseTime = uint32(base*modPct) (U:10962-10976)
timer = uint32(base * modPct * remainingTimePct);                    // float chain, truncation
```

- The fraction of the swing remaining is preserved, then truncated.
- `ModHaste` (BASE) and `ModRangedHaste` (RANGED) update fields mirror the change (U:10990-11002).
- `resetAttackTimer` is `uint32(GetBaseAttackTime * m_modAttackSpeedPct)` (U:665-668).
- Examples:
  - 20% haste on 2600 gives `m_modAttackSpeedPct` 0.833333313 and a 2166 ms period.
  - 10% haste on 3600 gives 0.909090936 and 3272 ms.

**Sources** (handlers A:4528-4603; table rows A:81, 210, 212, 264, 265, 289, 324, 391, 414; in-scope counts from the live census):

| aura / rating | handler | timers | exclusivity | in scope |
|---|---|---|---|---|
| 138 `MOD_MELEE_HASTE`, 217 `_2`, 319 `_3` | `HandleModMeleeSpeedPct` | BASE, OFF | exclusive group vs 138 | 0 / 0 / **9** (386196 +3, 390354 +5, 391688 +30, 394313 +10, 403509 +20, 441817 +35, 1263345 +10, 391568, 392778) |
| 192 `MOD_MELEE_RANGED_HASTE`, 342 `_2` | `HandleModMeleeRangedSpeedPct` | BASE, OFF, RANGED | none (a ToDo notes they should not stack) | 0 / **8** (13750 +20, 185313 +25, 382889 +15, 404542 +15, 1272685 +30, 5760 −15, 194879, 1250646) |
| 193 `MELEE_SLOW`, 252 (`MOD_SPEED_SLOW_ALL` in the enum) | `HandleModCombatSpeedPct` | BASE, OFF, RANGED + cast time | exclusive group keyed on 193 for both | **44** / 0 |
| 9 `MOD_ATTACKSPEED` | `HandleModAttackSpeed` | BASE only (+ `UpdateDamagePhysical`) | — | 0 |
| 140 `MOD_RANGED_HASTE` | `HandleAuraModRangedHaste` | RANGED | — | 0 |
| `CR_HASTE_MELEE` | `Player::UpdateRating` | BASE, OFF | the old diminished value is unapplied, then the new one applied | via item stat 36 (1,481 ExpansionID-11 items) and `MOD_RATING` bit 17 (4 in scope) |
| `CR_HASTE_RANGED` | same | RANGED | same | stat 36, bit 18 |

The `CR_HASTE_*` coordinates are P:5317-5340. `ITEM_MOD_HASTE_RATING` feeds all three `CR_HASTE_*` ratings (P:8014-8017). Aura amounts are `SpellEffectValue = double` (SpellDefines.h:490), cast with `(float)` at every call.

**Finding H1 (trinity-probe): applying and then removing the same haste value does not always restore the period.**
- In binary32, `modPct *= 100/(100+v)` followed by `modPct *= (100+v)/100` leaves `m_modAttackSpeedPct` = 0.99999994 or lower.
- As a result, `AttackRoundBaseTime` and every later reset come out **1 ms short**.
- Measured exhaustively over weapon speeds {1500, 1800, 2000, 2400, 2600, 2700, 3000, 3300, 3600, 3800} and v = 1.00…100.00:
  - 21,480 of 99,010 pairs are 1 ms short.
  - For integer v: 190 of 1,000.
- These in-scope amounts drift for all 10 speeds: **3** (386196 Berserker Stance), **5** (390354 Furious Blows) and **15** (382889 Flurry, 404542 Crusading Strikes). 20 and 30 round-trip exactly.
- The drift is bounded. The product reaches a fixed point within 100 cycles and the period stays at base − 1 ms; for 2600 it is 2599 after 10,000 Flurry cycles.
- Evidence: `tests/test_wc_swing.py::test_probe_haste_round_trip_drift`, which compares against the verbatim `ApplyAttackTimePercentMod`.
- A binary64 port would *not* reproduce this. It is reachable and discriminating.

### 6.6 Casts that reset or pause swings

**Reset.** `Spell::ResetCombatTimers` (S:8640-8650) resets BASE, OFF (only with an off hand) and RANGED. It runs when `IsAutoActionResetSpell` holds (S:8310-8320), meaning all of the following:
- the spell is not triggered;
- `SpellInterrupts.InterruptFlags & Combat (0x8)` (SpellDefines.h:65);
- the spell lacks `SPELL_ATTR2_DO_NOT_RESET_COMBAT_TIMERS` (0x20000);
- it is not (instant and `SPELL_ATTR6_DOESNT_RESET_SWING_TIMER_IF_INSTANT` (0x02000000)).

**When the reset happens:**
- normally in `Spell::_cast` after the launch phase (S:3886-3887);
- in `Spell::prepare` instead when the spell has `SPELL_ATTR7_RESET_SWING_TIMER_AT_SPELL_START` (0x8000, S:3572-3573).

**Reachability:**

| attribute | in scope | class-skill only | catalog |
|---|---|---|---|
| `InterruptFlags` has Combat (DifficultyID 0) | 282 | 3,604 | 76,332 |
| `ATTR2_DO_NOT_RESET` | 29 (e.g. 19434 Aimed Shot, 53351 Kill Shot, 193455 Cobra Shot, 100780 Tiger Palm) | 9 | 1,318 |
| `ATTR6_DOESNT_RESET_IF_INSTANT` | 15 (e.g. 188196 Lightning Bolt, 51505 Lava Burst) | 7 | 155 |
| `ATTR7_RESET_AT_START` | **0** | 0 | 658 |

**Pause (S18).** `ATTR6_DELAY_COMBAT_TIMER_DURING_CAST` freezes all three timers while that spell is the current generic or channeled spell. There are 9 in scope:
- 1513 Scare Beast
- 51690 Killing Spree
- 113656 Fists of Fury
- 123986 Chi Burst
- 198013 Eye Beam
- 198898 Song of Chi-Ji
- 436358 Demolish
- 473728 Void Ray
- 1261193 Boomstick

Nothing else pauses the timers. In the oracle, pausing from 500 to 1500 ms moves the 2600 ms swing to 3600 ms.

### 6.7 Queued, replacement and extra attacks

**Queued melee spells (S19).**
- A spell is queued when `IsNextMeleeSwingSpell` holds, i.e. it has `ATTR0` 0x4 | 0x400 (SpellInfo.cpp:1899-1902). Its container is `CURRENT_MELEE_SPELL` (S:8157-8160).
- On a ready BASE swing that is not an extra attack, the queued spell is cast instead of the white swing (U:2292-2293). There is no white damage and no white proc event, but the timer still resets.
- In scope: **0** (189 and 396 in the catalog). Verdict: `legacy-only`.

**Auto-attack override, aura 361 (S20, U:2299-2316, 2352-2361).**
- A BASE swing casts the front aura's `TriggerSpell`. An OFF swing casts the `MiscValue` of the first aura whose `MiscValue != 0`.
- The swing is cast as a triggered spell, and Trinity sends a fake `HITINFO_NO_ANIMATION` state.
- In scope: **404542 Crusading Strikes (Retribution)**, which also carries aura 342 +15.

**Extra attacks (S21).**
- `EffectAddExtraAttacks` (SpellEffects.cpp:3688-3701) calls `AddExtraAttacks` (U:2374-2387), which queues against `_lastDamagedTargetGuid` or else the selection.
- `_handle_finish_phase` sets `_lastExtraAttackSpell` (S:4209-4213).
- The drain happens in the **next** `Unit::Update`, before the decrement.
- `extra=true` bypasses only `CANNOT_AUTOATTACK` and the queued-spell branch (U:2267, 2292). It touches no timer.
- Any non-extra MH **or OH** swing clears `_lastExtraAttackSpell` (U:2288-2289). Queued extra attacks then stay in `extraAttacksTargets` until another extra-attack spell finishes.
- In scope and class skills: **0** (28 in the catalog, none build-skew; e.g. 32910 Windfury, 465660/465661 Skyfury, 1270413 Peerless Blade). Verdict: `legacy-only`.

### 6.8 Ranged auto-shot (S22-S24, S30)

**Mechanism.** Auto Shot is a spell. `_UpdateAutoRepeatSpell` (U:3019-3055) runs before the decrement. When `isAttackReady(RANGED)` holds and the current auto-repeat spell is not `PREPARING`, it does the following:
1. `CheckCast(true)`: on failure, non-75 auto-repeat spells are interrupted (U:3041-3048).
2. `new Spell(this, info, TRIGGERED_IGNORE_GCD)->prepare()` (U:3052-3053).

`prepare` schedules the `SpellEvent` at `EventProcessor::m_time + 1 ms` (S:3466-3467). Because `EventProcessor::Update` adds `p_time` before it runs events (EventProcessor.cpp:40-47), the cast lands in the **next** update. That cast goes through `_cast` → `SendSpellCooldown` → `resetAttackTimer(RANGED)` (S:3893, S:4226-4235). `CheckCast` also refuses when RANGED is not ready (S:5800).

**Cadence with a fixed tick T and period P** (P = `uint32(base*modPct)`):
- melee: `ceil(P/T)` updates;
- auto-shot: `ceil(P/T) + 1` updates. With P = 3000, T = 100 this is 3100 ms; with T = 1 it is 3001 ms.
- Test: `test_auto_shot_cadence_is_ceil_plus_one_update`, for T ∈ {1, 33, 50, 100}.

**Spell 75.** CastingTimeIndex 1 (0 ms), `Attributes[2]` = 32 (`AUTO_REPEAT`), DmgClass 3, InterruptFlags 32. It is learned through SkillLineAbility 32924 → SkillLine 183 "GENERIC (DND)" (category 12), ClassMask 4, AcquireMethod 2 = `AutomaticCharLevel` (DBCEnums.h:2360-2367, P:25391). Consequently **75 is not in `Scope`** in either variant, because the scope roots only use category-7 class skill lines (db2-fact). The one auto-repeat spell in scope is **467718 Bleak Arrows** (Beast Mastery and Marksmanship).

**Special cases for id 75 (S23, S30).**
- Every hard-coded exemption is keyed on id 75 (U:3028-3033, 3043, 3104-3106, 3119-3122, 3133).
- Consequently 467718 is interrupted by any cast with a cast time, any channel, and `IsNonMeleeSpellCast` while repeating. 75 is not.
- This is a consumer fact, not a Retail claim.

**Ranged haste (S24).** Only aura 342 (8 in scope), aura 193 (44), and `CR_HASTE_RANGED` move the RANGED timer. Aura 319 does not.

### 6.9 Parry haste and rage

**Parry haste (S25, U:1536-1567).** This is victim-side float arithmetic, applied unless the victim is a creature with `CREATURE_FLAG_EXTRA_NO_PARRY_HASTEN` (0x8).
- The timer adjusted is OFF when the victim has an off hand and `off < base`; otherwise BASE.
- Let `p20 = base_time*0.20f` and `p60 = 3*p20`:
  - timer in (p20, p60]: timer becomes `uint32(p20)`;
  - timer > p60: timer becomes `uint32(t - 2*p20)`.
- Example: timer 2000 with base 2600 gives 960; timer 1500 gives 520.

**Rage (S26).** `uint32(base/1000.f*1.75f)`, halved with integer division for OFF. It is not paid on a miss (U:2250-2260, 2331). Examples: 3600 gives 6, and 3 for the off hand.

### 6.10 Numeric records (swing)

| value | source_type | intermediate | aggregation | rounding | integer_conversion | units |
|---|---|---|---|---|---|---|
| `m_attackTimer` | uint32 | uint32 | exact | saturating decrement | U:463-468 (reachable every update) | ms |
| `m_modAttackSpeedPct` | float | float | binary32 multiplicative chain, order-dependent (H1) | none | — | multiplier |
| reset timer | uint32 × float | float | binary32 | `uint32()` truncation | U:667 | ms |
| haste re-time | uint32, uint32, float | float | binary32 | truncation | U:11006 | ms |
| creature OH offset | uint32 | float (`CalculatePct`) | binary32 | truncation ×2 | U:5946 | ms |
| parry haste | uint32 timers | float | binary32 | truncation | U:1548-1564 | ms |
| rage per swing | uint32 | float | binary32 | truncation, then `/= 2` for OFF | U:2255-2257 | rage units |

## 7. Attack outcome table

The oracle is `weapon_combat/attack_table.py`; the corpus is `attack-table.json` (5 paths, 16 witness examples).
It is verified end to end by `tools/tc_swing_probe`, which compiles Trinity's own function bodies, not re-typed ones:
- `RollMeleeOutcomeAgainst`, `MeleeSpellHitResult`, `MeleeSpellMissChance`
- `GetUnit{Dodge,Parry,Block,Miss}Chance` and `GetUnitCriticalChance{Done,Taken,Against}`
- `Player::GetExpertiseDodgeOrParryReduction` and `Player::GetBlockPercent`
- the crit, block and glancing cases of `CalculateMeleeDamage`, and the parry-haste block

For random tables, the probe's run-length encoding of the outcome for every roll 0…9999 must equal the oracle's.

### 7.1 Eligibility (who may produce which outcome)

| rule | coordinate |
|---|---|
| Evade: a creature victim with `IsEvadingAttacks` returns `MELEE_HIT_EVADE` **before any draw** | U:2391-2392 |
| White swings exist only for BASE/OFF (ranged returns at U:2285-2286; `CalculateMeleeDamage` default branch at U:1367-1368) | — |
| `canParryOrBlock = victim->HasInArc(π, attacker) \|\| aura 288` | U:2416 |
| `canDodge = victim is not a player \|\| canParryOrBlock`: creatures dodge from behind, players do not | U:2419 |
| A victim that is casting (`IsNonMeleeSpellCast(false,false,true)`) or has `UNIT_STATE_CONTROLLED` can neither dodge, parry nor block | U:2422-2426 |
| Sitting player victim with crit > 0: auto-crit, tested **after miss** | U:2433-2435 |
| Glancing: attacker is a player or `IsPet`, victim is neither a player nor a pet, and `attackerLevel + 3 < victimLevel` | U:2455-2458 |
| Crushing: `attackerLevel >= victimLevel + 4`, `!IsControlledByPlayer()`, no `NO_CRUSHING_BLOWS` (0x20) | U:2481-2486 |
| Player parry needs `CanParry()` and a useable MH (or OH) weapon | U:2804-2811 |
| Player block needs `CanBlock()` and a useable, non-broken `INVTYPE_SHIELD` off hand | U:2851-2856 |
| Creature parry/block need the absence of `NO_PARRY` (0x4) / `NO_BLOCK` (0x10); totems never dodge/parry/block | U:2770, 2816, 2860 |
| Levels: `GetLevelForTarget`. Player: `GetLevel()` (Unit.h:758). Scalable creature: `clamp(targetLevel + targetDelta, ScalingLevelMin, Max) + ScalingLevelDelta`, clamped to [1, MAX_LEVEL+3] | Creature.cpp:3130-3160 |

The creature level inputs come from:
- `ScalingLevelMin/Max` = ContentTuning MinLevel/MaxLevel (Creature.cpp:3060-3066, DB2Stores.cpp:2192-2237);
- `ScalingLevelDelta` = `irand(creature_template_difficulty.LevelScalingDeltaMin, Max)` (Creature.cpp:3068-3073).

### 7.2 Probability construction (all `float`, left to right)

| getter | formula (binary32) | coordinate |
|---|---|---|
| miss (`MeleeSpellMissChance`) | `0` if spell has `ATTR7_NO_ATTACK_MISS`; else `5.0` (`GetUnitMissChance`, U:2836-2841) `+19` if (no spell ∧ `haveOffhandWeapon` ∧ att≠RANGED ∧ no `CURRENT_MELEE_SPELL` ∧ ¬feral ∧ no aura 458) `−(resistMissChance−100)` (`SpellModOp::HitChance`, spells only) `− m_modMelee/RangedHitChance` `−` attacker aura 54 `−` victim aura 184/185, `max(0)` | U:12454-12488 |
| player hit chance | `m_modMeleeHitChance = 7.5f + GetRatingBonusValue(CR_HIT_MELEE)` (ranged likewise); constructor 7.5; creatures 0 | StatSystem.cpp:753-761, P:171-172, U:364-365 |
| dodge | player victim: `DodgePercentage` field (no level term); creature: `3.0 + aura 49`, `+1.5*diff` if diff>0; `+ aura 248(misc DODGE) + aura 251`; `− expertise`; `max(0)` | U:2760-2794 |
| parry | player: `ParryPercentage` if eligible; creature: `6.0 + aura 47 (+1.5*diff)`; `− expertise`; `max(0)` | U:2796-2834 |
| block | player: `BlockPercentage` if eligible; creature: `3.0 + aura 51 (+1.5*diff)`; **no expertise**; `max(0)` | U:2843-2872 |
| expertise | player: `7.5f + {Main,Off}handExpertise / 4.0f` for BASE/OFF, `0` for RANGED; creature attacker: `aura 240 / 4.0f` | P:5216-5229, U:2789-2792 |
| `MainhandExpertise` | `int32(GetRatingBonusValue(CR_EXPERTISE)) + aura 240 (weapon-filtered)`, `max(0)` | StatSystem.cpp:769-796 |
| crit done | player: `Crit`/`OffhandCrit`/`RangedCritPercentage` field; creature: `5.0 + aura 52 + aura 290` unless `NO_CRIT` (0x20000) | U:2874-2907 |
| crit taken | `+ aura 187` (not RANGED) `+ aura 183` (health-filtered) `+ aura 306` (caster = attacker) `+ aura 339` (TempSummon's summoner) `+ aura 197`, `max(0)` | U:2909-2938 |
| white crit | `crit_against + aura 334` (auto-attack only) | U:2398 |
| conversion | `int32(x * 100.0f)`: truncation to basis points | U:2395-2402 |

**Finding T1 (trinity-probe): `int32(x*100.0f)` loses one basis point for 282 of the 4,999 hundredths 0.01…49.99.**
- Example: 0.53f → 52.
- Composite example: a level-93 creature block of 3 + 0.11 + 4.5 gives **760**, not 761 (`test_probe_basis_point_truncation_in_band_edges`).
- A port that rounds, or works in binary64, would shift band edges.

### 7.3 Draws per swing and per spell

| path | draws, in order |
|---|---|
| white MH/OH | (1) damage `urand(uint32 min, uint32 max)` in `CalculateDamage` (U:1383 → U:2550), skipped only on physical immunity (U:1371-1380); (2) outcome `int32 roll = urand(0, 9999)` (U:2410), skipped on evade and on physical immunity; (3) block only: `IsBlockCritical` = `roll_chance(float aura 253)` (U:2575-2580, U:1460); (4) magic-school weapon only: resist `rand_norm()` in `CalcSpellResistedDamage` (U:1777-1799); (5) in `DealDamage`, when damage > 0 and the victim survives: durability `roll_chance(float RATE_DURABILITY_LOSS_DAMAGE)` (default 0.5, World.cpp:1015) for a player victim and again for a player attacker, each followed by `urand(0, EQUIPMENT_SLOT_END-1)` on success (U:1090-1107); (6) creature attacker, from behind, on a hit, crit or glancing blow: daze `roll_chance(float)` (U:1573-1594); (7) item and aura proc rolls (`CastItemCombatSpell`, `ProcSkillsAndAuras`) |
| melee/ranged spell | (1) reflect `roll_chance(float)` only if `canReflect` and the victim has reflect auras (Object.cpp:1972-1979); (2) `uint32 roll = urand(0, 9999)` (U:2633), unless `ATTR3_NO_AVOIDANCE` (U:2623) or an earlier short-circuit; (3) crit `roll_chance(float critChance)` only when `MissCondition == NONE` (S:8540-8563); (4) damage in the effect handler (part C) |

The counts are distribution calls; a `uniform_int_distribution` call may consume more than one engine output, so a
stream-exact replay is not modelled (R2-1). `urand` is `std::uniform_int_distribution<uint32>` (Random.cpp:42-47). `roll_chance(float)` is `chance > rand_chance()`, and `rand_chance()` is **`std::uniform_real_distribution<float>(0,100)`** (Random.h:48, 54-58; Random.cpp:81-85). **Both sides are binary32**; the first agent's note of a `double` draw was wrong.

### 7.4 The white roll (U:2389-2499)

`sum` is an `int32` accumulator. Each band is taken when `tmp > 0 && roll < (sum += tmp)`, tested in this order:
1. miss
2. sitting auto-crit
3. dodge (if `canDodge`)
4. parry (if `canParryOrBlock`)
5. glancing: `tmp = (10 + 10*(vic−atk))*100`, **no cap** despite the 40% comment
6. block (if `canParryOrBlock`)
7. crit
8. crushing
9. normal

**Finding T2 (structural proof plus probe): crushing is unselectable for every attacker type.**
- `tmp = attackerLevel − victimLevel * 1000 − 1500` (U:2489). All operands are `int32` (no unsigned promotion). By operator precedence this is negative for every `attackerLevel < victimLevel*1000 + 1500`; since levels come from `uint8 GetLevelForTarget` (≤ 255) and the victim level is ≥ 0, that holds for every attacker, player, pet or creature.
- The crushing test has no `tmp > 0` guard. Every earlier band that added to `sum` failed `roll < sum`, so `sum_before ≤ roll`, and therefore `roll < sum_before + tmp` is false.
- The eligible 97-vs-90 case (W9) never yields crushing, in either the oracle or the compiled Trinity code.

**Outcome application** (`CalculateMeleeDamage`, U:1402-1490; oracle `OUTCOME_APPLICATION`):

| outcome | HitInfo / TargetState | damage |
|---|---|---|
| evade | MISS \| SWINGNOHITSOUND / EVADES | 0, returns |
| miss | MISS / INTACT | 0 |
| normal | — / HIT | unchanged |
| crit | CRITICALHIT / HIT | `*= 2`; `mod = (GetTotalAuraMultiplierByMiscMask(163) − 1.0f)*100`; `AddPct` if mod ≠ 0 |
| parry, dodge | — / PARRY, DODGE | 0 (`CleanDamage += Damage`) |
| block | BLOCK / **HIT** (never `VICTIMSTATE_BLOCKS`) | `Blocked = CalculatePct(Damage, victim->GetBlockPercent(attacker GetLevel()))`; critical: `*= 2`, `*= float(aura 638 multiplier)`; `Damage −= Blocked` |
| glancing | GLANCING / HIT | `uint32((1.f − min(vicRaw − atkRaw, 3)*0.1f) * Damage)` using raw `GetLevel()` |
| crushing | CRUSHING / HIT | `Damage += Damage/2` (dead) |

Afterwards come resilience (U:1499-1505) and, if `int32(Damage) > 0`, `TAKE_ANY_DAMAGE` plus absorb/resist (U:1508-1526).

**Finding T3 (trinity-probe): crit-damage truncation.**
- With a crit multiplier of 1.3, `mod` = 29.9999962f (binary32), so 1000 crits for **2599**, not 2600.
- The multiplier aura 163 is reachable: 5 in scope, including 1258209 Powerful Eversong Diamond, which reaches every spec.

**Finding T4 (trinity-probe): block-percent units differ by victim type.**
- The virtual `Unit::GetBlockPercent` returns `30.0f`, a **percent**, for every non-player (Unit.h:987).
- `Player::GetBlockPercent` returns `min(ShieldBlock/(ShieldBlock+ArmorConstant), 0.85f)`, a **fraction** (P:26822-26831). ArmorConstant = `EvaluateExpectedStat(ArmorConstant, attackerLevel, −2, …)`, which is 3430 at levels 90 and 93 (ExpectedStat rows 475/478, db2-fact).
- White swings (U:1459): a player victim with ShieldBlock 2000 blocks `CalculatePct(1000, 0.3683)` = **3** of 1000 damage. A creature blocks 300, or 900 on a ×1.5 critical block.
- Weapon spells (U:1239): `uint32 value = GetBlockPercent()` truncates the player's fraction to **0**, so a blocked weapon spell on a player blocks nothing. A creature blocks 30%.
- Reachability: `SPELL_EFFECT_BLOCK` spell 107 comes from SkillLine 95 (not in Scope). Block auras 51 (76671, 76857, 386011 +8, 1258163 +8), 253 (76857) and 638 (429638 +20) are in scope for Protection Warrior and Protection Paladin.
- This is a consumer fact about Trinity, not Retail.

### 7.5 Spell table (`MeleeSpellHitResult`, U:2621-2758) and its entry

**Entry.** `WorldObject::SpellHitResult` (Object.cpp:1949-1993) checks, in order:
1. immune
2. damage-immune
3. positive and not hostile
4. self
5. **evade (1968)**
6. reflect roll
7. **`ATTR3_ALWAYS_HIT` (1980)**
8. DmgClass MELEE/RANGED goes to `MeleeSpellHitResult`

The first agent's oracle checked always-hit before evade; that is now fixed. In scope: `ATTR3_ALWAYS_HIT` 65, `ATTR3_NO_AVOIDANCE` 0 (1,042 in the catalog).

**Sequence** on a `uint32 tmp` with a single `uint32 roll = urand(0,9999)`:
1. `NO_AVOIDANCE` returns NONE.
2. DmgClass RANGED sets `attType = RANGED`.
3. miss: `uint32(miss*100f)`. There is no dual-wield penalty for spells; `SpellModOp::HitChance` applies.
4. resist: `int32(GetMechanicResistChance*100f)`.
5. `ATTR0_NO_ACTIVE_DEFENSE` returns NONE (46 in scope).
6. `canDodge/Parry/Block = !ATTR7 0x800000 / !ATTR7 0x1000000 / !ATTR8 0x1` (18 / 31 / 15 in scope). Casting or controlled clears all three.
7. RANGED: no dodge and no parry. Deflect (aura 287) applies when **not `UNIT_STATE_CONTROLLED`** (casting does *not* remove it) and the victim faces the attacker or has aura 288 (U:2665-2678). There are 0 aura-287 spells in scope.
8. From behind: a player victim loses dodge; parry and block are lost; with aura 288, only `CU_REQ_CASTER_BEHIND_TARGET` removes parry (U:2680-2697).
9. aura 202 ignore (0 in the catalog).
10. dodge, parry, block, each `max(0)`.

**Crit.** It is a separate draw:
- `SpellCritChanceDone` returns 0 for creatures without `GetSpellModOwner()` (U:7122-7123); pets and guardians of players have one. It also returns 0 without `CU_CAN_CRIT`.
- For MELEE/RANGED it adds `GetUnitCriticalChanceDone(attackType)`, then `SpellModOp::CritChance`.
- `SpellCritChanceTaken` routes to `GetUnitCriticalChanceTaken` (U:7174-7245).

**Finding T5: a blocked weapon spell still hits, reports a full block, and never crits.**
- `SPELL_MISS_BLOCK` without `ATTR3_COMPLETELY_BLOCKED` (0x8; 6 in scope, e.g. 1776 Gouge, 96231 Rebuke) still runs effects and deals damage (S:2763-2766, 4875, 8570).
- The crit roll only happens for `MissCondition == NONE` (S:8540-8563), so a blocked weapon spell **cannot crit**, despite the comment "CAN BE crit & blocked" at U:1235.

Auto Shot (75 / 467718) takes this path with `RANGED_ATTACK`.

### 7.6 Worked tables (`attack-table.json` / `witnesses-d.json`; all 16 match the compiled Trinity code)

Player at level 90 with crit 20%; the boss is a level-93 creature, attacked from the front unless stated.

| id | case | miss | dodge | parry | glancing | block | crit | normal |
|---|---|---|---|---|---|---|---|---|
| W1 | dual wield vs boss, MH = OH | 16.5 | 0 | 3.0 | — | 7.5 | 20 | 53.0 |
| W1b | same, from behind | 16.5 | 0 | — | — | — | 20 | 63.5 |
| W1c | Fury with Enrage (458) | 0 | 0 | 3.0 | — | 7.5 | 20 | 69.5 |
| W2 | two-hand vs boss | 0 | 0 | 3.0 | — | 7.5 | 20 | 69.5 |
| W2b | tank with aura 240 = 3 | 0 | 0 | 2.25 | — | 7.5 | 20 | 70.25 |
| W3 | Auto Shot vs boss (spell) | 0 | — | — | — | 7.5 | 20 (separate draw) | 92.5 none |
| W3b | Auto Shot from behind | 0 | — | — | — | — | 20 | 100 none |
| W4 | player vs player (dodge 8, parry 10, block 30) | 0 | 0.5 | 2.5 | — | 30 | 20 | 47 |
| W4b | same, victim sitting | 0 | — | — | — | — | 100 | — |
| W5 | boss (93) vs player (90) | 5 | 8 | 10 | — | 30 | 5 | 42 |
| W5b | dual-wielding boss vs player | **24** | 8 | 10 | — | 30 | 5 | 23 |
| W6 | boss vs Protection Warrior (187 −6) | 5 | 8 | 15 | — | 30 | **0** | 42 |
| W7 | melee-class spell vs boss | 0 | — | 3.0 | — | 7.5 | 20 | 89.5 none |
| W7b | spell with NO_ATTACK_DODGE, PARRY and BLOCK | 0 | — | — | — | — | 20 | 100 none |
| W8 | player at 86 vs creature at 93 | 0 | 6 | 9 | **80** | 5 (13.5 truncated by the sum) | 0 (unreachable) | 0 |
| W9 | creature at 97 vs player at 90 (crushing eligible) | 5 | 8 | 10 | — | 30 | 5 | 42 (**no crushing**) |

Notes on the table:
- **Player vs creature:** a player's flat 7.5 expertise (P:5218) cancels a +3 creature's 7.5% dodge exactly. The player's flat 7.5 hit chance (P:171) removes base miss entirely unless the player dual-wields.
- **Creature attackers:** they take the 19% dual-wield penalty too (W5b).
- **Player victims:** the victim's level adds nothing to player dodge, parry or block.

### 7.7 Numeric records (table)

| value | source_type | intermediate | aggregation | rounding | integer_conversion | units |
|---|---|---|---|---|---|---|
| chances | float getters; player fields `UpdateField<float>` | float | binary32, left to right | `max(x,0f)`, then `int32(x*100f)` truncation (T1) | U:2395-2402, 2635-2646, 2726-2748 (every swing) | percent → basis points |
| roll | `urand(0,9999)` | int32 (white) / uint32 (spell) | exact | — | — | basis points |
| level diff | uint8 `GetLevelForTarget` | int32 | exact | — | U:2412-2413, 2762, 2798, 2845 | levels |
| glancing chance | int32 | int32 | exact | — | — | bp `(10+10d)*100` |
| crushing chance | int32 | int32 | exact, negative | — | — | bp (dead) |
| expertise | int32 field | float | binary32 | `int32()` of rating (StatSystem.cpp:774), `/4.0f` | StatSystem.cpp:774 | points → percent |
| spell/block crit | float chance vs float draw | float | binary32 both sides | — | — | percent |
| block amount | player: int32 ShieldBlock and float ArmorConstant; creature: 30.0f | float | binary32 | `min(q,0.85f)`; `CalculatePct<uint32,float>` truncation | U:1459; spell U:1239 (`uint32` of the percent first) | fraction vs percent (T4) |
| crit damage | uint32 × 2, float mod | float | binary32 | truncation (T3) | U:1432-1437 | damage |
| glancing damage | uint32, int32 leveldif | float | binary32 | truncation | U:1480-1481 | damage |

## 8. Proc event production for weapon attacks

The oracle is `weapon_combat/proc_events.py` (facts P01–P16, builders joined to `procs.events`). The corpus is `proc-events.json`: 33 events, `cross_check_contradictions: []`.

### 8.1 White swing (MH/OH)

**The call** (U:2346-2347):

```cpp
ProcSkillsAndAuras(attacker, victim, ProcAttacker, ProcVictim, PROC_SPELL_TYPE_NONE, PROC_SPELL_PHASE_NONE,
                   DamageInfo(CalcDamageInfo).GetHitMask(), spell=nullptr, &dmgInfo, nullptr)
```

It runs after `DealMeleeDamage`. `ProcEventInfo::GetSpellInfo()` and `GetProcSpell()` are both null.

**Actor flags** (U:1356-1368):
- BASE: `DEAL_MELEE_SWING | MAIN_HAND_WEAPON_SWING` (0x4 | 0x400000).
- OFF: `DEAL_MELEE_SWING | OFF_HAND_WEAPON_SWING` (0x800000), plus `HITINFO_OFFHAND`.

**Victim flags.** Always `TAKE_MELEE_SWING`. `TAKE_ANY_DAMAGE` is added iff `int32(Damage) > 0` after outcome, armor, block and resilience, but **before absorb** (U:1508-1510).

MH and OH differ **only** in the hand bit (test `test_hand_flags_differ_only_in_the_hand_bit`).

**Hit mask** (`DamageInfo(CalcDamageInfo)`, U:142-192):

| outcome | hit mask | `TAKE_ANY_DAMAGE` |
|---|---|---|
| normal / glancing / crushing | NORMAL | if damage > 0 |
| crit | CRITICAL | if damage > 0 |
| block (partial) | BLOCK \| NORMAL (or \| CRITICAL, never for white since block and crit are separate bands) | if damage > 0 |
| miss / dodge / parry | MISS / DODGE / PARRY | no |
| evade | EVADE | no |
| physically immune target | **IMMUNE \| EVADE** (returns at U:1371-1380 with `HitOutCome` still `MELEE_HIT_EVADE` from U:1347) | no |
| full absorb | ABSORB only (the `damageNullified` guard covers NORMAL **and** CRITICAL, U:165-191) | **yes** (the flag was set before absorb) |
| partial absorb | ABSORB \| NORMAL/CRITICAL | yes |

- `VICTIMSTATE_BLOCKS` (→ FULL_BLOCK) is never produced by `CalculateMeleeDamage`.
- `PROC_HIT_NORMAL` does not require damage > 0.

**Extra attacks.** The masks are identical to a normal MH swing; nothing marks an event as an extra attack (U:2365-2372, P07).

**Suppression.** Nothing reachable suppresses a white swing event (P08):
- `SUPPRESS_CASTER/TARGET_PROCS` need a `spellInfo` (U:5591-5595).
- The chain limit and `IsProcDisabled` need a `spell` (U:5574-5578, S:8300-8303).
- `CanProc()` is consulted only in `DoDamageAndTriggers` (S:2851).

**Actor-side reactives.** `ProcSkillsAndReactives` runs first and sets `AURA_STATE_DEFENSIVE` on the victim on dodge, parry or block; rogues skip it on dodge (U:10493-10533).

**Owner and pet (P09).** `TriggerAurasProcOnEvent` visits the spell-mod owner's auras only if `spell != nullptr`, and only those in `spell->m_appliedMods` (U:10587-10604). A pet's white swing therefore **never** visits the owner's aura list.

**Item and enchant procs (P10).** `CastItemCombatSpell` runs from `DealMeleeDamage` before the aura event (U:1596-1600). It is gated on NORMAL | CRITICAL | ABSORB (P:8646-8650); weapon items proc only for their own hand (P:8612-8630).

### 8.2 Auto-attack override (aura 361)

The white event is replaced by `CastSpell(victim, id, true)`. The replacement spell's own DmgClass decides its flags: MELEE gives `DEAL_MELEE_ABILITY` plus a hand bit taken from the spell's `m_attackType`, not from the swing hand (S:2350-2355). This is the path for 404542 → 408385 Crusading Strikes.

### 8.3 Melee abilities and "counts as a swing" (P14)

- `PROC_FLAG_DEAL_MELEE_SWING` is produced only at U:1359 and U:1363. No attribute converts an ability into a swing.
- However, **every DmgClass-MELEE spell carries `MAIN_HAND_WEAPON_SWING`** (or `OFF_HAND_…` when `m_attackType == OFF_ATTACK`) together with `DEAL_MELEE_ABILITY` (S:2350-2355). A proc keyed only on the hand bits therefore fires for white swings and melee abilities alike. There are 250 DmgClass-2 spells in scope.

### 8.4 Ranged auto-shot (spell path)

`prepareDataForTriggerSystem` with DmgClass RANGED and `ATTR2_AUTO_REPEAT` gives `DEAL_RANGED_ATTACK` / `TAKE_RANGED_ATTACK` (S:2339-2371). Each shot raises four events:

| event | site |
|---|---|
| CAST (`| 2_CAST_SUCCESSFUL`) | S:3898-3915 |
| per-target HIT (P12, P16) | S:2825-2989 |
| FINISH | S:4218-4224 |
| `CAST_ENDED` | S:4401 |

HIT-event masks by outcome:

| outcome | hit mask | spell type | `TAKE_ANY_DAMAGE` |
|---|---|---|---|
| hit / crit | NORMAL / CRITICAL (createProcHitMask) | DAMAGE | yes |
| full absorb | ABSORB | DAMAGE | **yes** |
| damage-immune | IMMUNE | DAMAGE | no (S:2912-2916) |
| blocked (partial, not `COMPLETELY_BLOCKED`) | **BLOCK \| FULL_BLOCK** (U:10433-10438), never NORMAL/CRITICAL | DAMAGE | yes (T5) |
| miss / deflect / evade / resist / spell-immune | MISS / DEFLECT / EVADE / FULL_RESIST / IMMUNE | NO_DMG_HEAL | no |

Two gates apply:
- `CastItemCombatSpell` is skipped for `ATTR0_CANCELS_AUTO_ATTACK_COMBAT` (20 in scope) and `ATTR4_SUPPRESS_WEAPON_PROCS` (87 in scope) (S:2985-2987).
- `_UpdateAutoRepeatSpell` uses `TRIGGERED_IGNORE_GCD`, which does not include `DISALLOW_PROC_EVENTS`.

### 8.5 Cross-check with `procs/events.py`

`proc_events.cross_check()` returns `[]`. It compares:
- `melee_swing` for both hands, including type and phase and the 0-damage `TAKE_ANY_DAMAGE` case;
- `melee_hit_mask` for all 9 outcomes, plus full absorb, immune and partial block;
- `spell_hit_mask("block")`;
- `spell_hit` against `ranged_auto_event` for 8 outcomes on 4 fields.

**No contradiction** with the proc report was found. That includes the blocked-spell case, where `spell_hit(miss="block", damage>0)` already yields BLOCK | FULL_BLOCK with `TAKE_ANY_DAMAGE`.

## 9. Witnesses (scheduling/table/proc view)

| brief item | witness | result | class |
|---|---|---|---|
| dual wield vs a level-93 boss | W1, 2600/2600 timeline | 16.5% miss on both hands; OH 200 ms behind MH; parry 3 / block 7.5 from the front | trinity-probe |
| two-hand vs the same boss | W2, 3600 with 10% haste | 0 miss; period 3272 ms (0.909090936) | trinity-probe |
| ranged auto-shot | 75 (skill-line learned, outside Scope) / 467718 Bleak Arrows (in scope) | spell table (W3); cadence `ceil(P/T)+1` updates; interruptible unless id 75 | trinity-consumer + db2-fact |
| player vs player | W4 / W4b | no level term; sitting victim is auto-crit | trinity-probe |
| player attacked by an NPC (crushing) | W5 / W9 | crushing eligible for a +4 creature, arithmetically dead (T2) | trinity-probe |
| block-capable victim spec | W6 Protection Warrior (auras 187 −6, 51 +8, 253 mastery, 638 +20; `SPELL_EFFECT_BLOCK` 107 via SkillLine 95) | blocks 3 of 1000 as a player (T4) | trinity-probe + db2-fact |
| extra-attack source in scope | none: 0 in scope and class skills; the catalog has 28 (Windfury 32910/38229/65976/78147/163084, Skyfury 465660/465661, Peerless Blade 1270413 …, none build-skew) | `legacy-only` | db2-fact |
| ON_NEXT_SWING candidate (expected none) | none: 0 / 0 (catalog 189 / 396) | `legacy-only` | db2-fact |
| override swing | 404542 Crusading Strikes (Retribution) | white event replaced (§8.2) | db2-fact + trinity-consumer |
| dual-wield penalty removal | 184362 Enrage (Fury; aura 458) | W1c | db2-fact + trinity-probe |
| auto-attack crit | 31884 Avenging Wrath +20, 389539 Sentinel +10, 406154 Heart of the Crusader +10, 1272781 Tiger Fang +15 (aura 334) | added to the white crit only | db2-fact |
| expertise | aura 240 = 3 on 105805, 137008, 320380, 1258153, 1258163 (tank passives) | W2b | db2-fact + trinity-probe |
| hit debuff | 325153 Exploding Keg (Brewmaster; carries aura 54 with −99; which unit holds the aura is not modelled here) | an attacker holding it has miss 5 + 99 = 104%, so 10,400 bp exceeds the roll space and every white roll misses | db2-fact + trinity-consumer |
| facing | 212800 Blur (aura 288, Havoc/Devourer); 186265 Aspect of the Turtle (class-skill only) | a player victim keeps dodge/parry/block from behind | db2-fact |
| haste drift | 382889 Flurry (+15), 386196 (+3), 390354 (+5), 404542 (+15) | H1: period −1 ms after the aura fades | trinity-probe |

`witnesses-d.json` also carries three more blocks:
- the live scope census (`--census`: 20 attributes, 36 auras, 4 effects, `MOD_RATING` bits, interrupt flags, Auto Shot acquisition, item-stat census);
- the block-percent witness;
- the glancing/crushing level census (§10).

## 10. Legacy mechanics reachability verdicts

| mechanic | verdict | input that would reach it | coordinate |
|---|---|---|---|
| glancing | **legacy-only for current content in the pinned world DB**; current raid bosses are **build-skew/unresolved** (§14: live code with a reachable input, so not legacy-only as a mechanic) | a creature whose `GetLevelForTarget` against a level-90 player is ≥ 94, i.e. `clamp(90, CT.MinLevel, CT.MaxLevel) + LevelScalingDelta ≥ 94` (for a CT spanning 90: delta ≥ 4). A +3 boss does not reach it. See the level census note below | U:2455-2464; Creature.cpp:3060-3075, 3130-3160 |
| crushing | **legacy-only (dead code for every attacker)** | the same +4 creature **and** a fix to the precedence at U:2489 | U:2481-2494 |
| hit rating (`CR_HIT_*`) | legacy-only | item stat 31 or a `MOD_RATING` bit 5-7. The ExpansionID-11 items (7,812) carry stats {3, 4, 5, 7, 24, 25, 32, 36, 40, 49, 61, 71-74, 76-82}; stat 31 is on no ItemSparse row of any expansion and on no enchant (R2-4); `MOD_RATING` bits in scope are {8, 9, 10, 13, 17, 18, 19, 25, 28, 29, 30} | P:7991-7995, StatSystem.cpp:753-761 |
| expertise rating (`CR_EXPERTISE`) | legacy-only (the rating); the flat aura 240 is reachable | item stat 37 (no ItemSparse row or enchant of any expansion) or `MOD_RATING` bit 23 (not in scope) | P:8019-8021, StatSystem.cpp:769-796 |
| base 7.5 hit / 7.5 expertise | reachable (every player) | — | P:171-172, P:5218 |
| dual-wield +19 miss | reachable | a useable off-hand weapon (5 `SPELL_EFFECT_DUAL_WIELD` spells in scope: 674, 86629, 124146, 231842, 1277760) | U:12463-12465 |
| dodge/parry/block ratings | legacy-only | item stats 13/14/15 or `MOD_RATING` bits 2-4. They are absent from ExpansionID-11 items; dodge/parry survive only on legacy items (ExpansionID ≤ 10), 375 legacy enchants (IDs ≤ 5260, MaxLevel ≤ 35) and legacy bonus lists 6676/6677, none used by the current gear corpus (R2-4) | P:7991-8037 |
| weapon/defense skill | absent from the table; within combat code `GetMaxSkillValueForLevel` (= level × 5) is used only by the creature daze roll | none: no weapon-skill input exists in the table code | U:1585-1586, Unit.h:931 |
| resilience vs crit | legacy-only; `CR_RESILIENCE_CRIT_TAKEN` is not read by the crit-taken getter, and `CR_RESILIENCE_PLAYER_DAMAGE` only scales damage | a PvP victim with resilience and a player-owned attacker (`CanApplyResilience`) | U:1499-1505, U:2909-2938, U:12416-12419 |
| ON_NEXT_SWING / queued melee spell | legacy-only | a reachable spell with ATTR0 0x4/0x400 | U:2292-2293 |
| extra attacks | legacy-only | a reachable `ADD_EXTRA_ATTACKS` spell | U:441-454 |
| deflect (aura 287) | not reachable (0 in scope, 24 in the catalog) | an in-scope aura-287 spell (read on the ranged spell path and in `MagicSpellHitResult`, Object.cpp:1933) | U:2670-2677 |
| `ATTR7_RESET_SWING_TIMER_AT_SPELL_START` | not reachable (0 in scope, 658 in the catalog) | — | S:3572 |
| `ATTR3_NO_AVOIDANCE` | not reachable (0, 1,042 in the catalog) | — | U:2623 |
| auras 138/217/192/9/140 (haste) | not reachable; 319/342/193 are | — | A:4528-4603 |

**Level census** (`witnesses_d.level_census`, model in the corpus):
- `creature_template_difficulty` has 320,629 rows after the full update replay (`creature-level-deltas.json`); the base dump alone has 320,285, and gives the same result.
- Against a level-90 player, only **19 rows (13 entries)** reach +4. None of them sits on an ExpansionID-11 ContentTuning.
  - 12 entries are companion or pet NPCs (15066 Cleo, 15071 Underfoot, 63063 Shifty, 63068 Miles, 63069 Fester, 63074 Fluffy, 63084 Poe, 65097 Hoo, 72006 Allie, 119390 Marcus "Bagman" Brown, 147070 Micro Zoox, 150987 Sean Wilkers) on max-level-scaling ContentTuning 373/846.
  - 1 is 179738 Captain Caulle Whiphook (delta 62 on ContentTuning 748, level 60).
- **The nine encounters of JournalInstance 1317/1320 (maps 2987/3004) are absent from TDB 1200.26021** (name search: 0 hits). Their level delta is therefore unknown to the pinned server data: build skew.
- The DB2 `CreatureDifficulty.csv` is a partial client cache with renamed delta columns. It has rows with delta ≥ 4 (39 at 4), but no row for the raid bosses. Absence there is not evidence.
- The ContentTuning mapping is positional between the 12.1 and 12.0.7 schemas (`MinLevelSquish`→`MinLevel`, `MaxLevelScalingOffset`→`MaxLevelType`, …): **structural-inference**.

## 11. Reproducibility

### Part C

```
cd scripts/research   # from the wowlab-data root
make -C tools/tc_weapon_probe                       # regenerates *.inc from ../../../../TrinityCore and builds probe
python3 weapon_combat.py all-c                      # sources + arithmetic + special + witnesses (~33 s)
python3 weapon_combat.py weapon --loadout FILE --class 4 --spec 260 --level 90 [--form ID] [--ap N] [--dual-wield]
python3 weapon_combat.py damage --hand mh --roll 1000 --outcome crit --armor 1470 --armor-curve-factor 1.0
python3 weapon_combat.py special [--spec 72] [--spell 205547]
uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/test_wc_weapon.py tests/test_wc_damage.py tests/test_wc_differential.py -q -p no:cacheprovider
# -> 68 passed (after the closeout review; 64 before)
```

Running `all-c` twice gave identical bytes.

| Corpus | Bytes | Rows | sha256 |
|---|---|---|---|
| weapon-sources.json | 17,048 | 21 source rows; DW 14 specs automatic (+71/72/73 learned-only 296087), TG 1; default-skill grants for 3 classes | `17d55dc8e3bc1700a7ca2ae9460562ef0d1e6b7a4d08010185fe7cabdbaf5f11` |
| damage-arithmetic.json | 14,306 | 16 stages, 6 boundaries, 9 discriminating inputs | `15de92d8914c5d452c9ecea534b860573214c1f51eb5246bfc8a674e125eb766` |
| special-attacks.json | 114,207 | default 2 / class lines 11 weapon spells; 210 AP-coef rows | `940fcafdb6bcae70c7e390978186235c85e4bbcff5c437fbc5098d497082b200` |
| witnesses-c.json | 131,146 | 15 witnesses | `9f3183311bbcedc59130836bd39c5189f5b5dddcdd3c5d2f6e0085b951299997` |

The sha256 values embed `provenance.wowlab_data_commit` (2773a88…). The glancing reachability count uses the lead's
`scratchpad/lead/ctd_full.json` (`tdb_world_extract.py` with the key index; 320,629 rows, 1 unparsed statement).

### Part D

```text
cd scripts/research
make -C tools/tc_swing_probe                     # extracts verbatim bodies, builds ./probe (g++ -std=c++20 -O0 -ffp-contract=off)
python3 weapon_combat.py all-d --census --levels ../../docs/research/weapon-combat-corpora/creature-level-deltas.json
#   = swing-corpus, table-corpus, proc-events-corpus, witnesses-d --census --levels ...
python3 weapon_combat.py swing --mh-speed 2600 --oh-speed 2600 --haste-pct 20 --duration 6000 --brief
python3 weapon_combat.py table --attacker-level 90 --victim-level 93 --victim npc --dual-wield --crit 20
python3 weapon_combat.py proc-event --hand oh --outcome block --damage 97 --blocked 3
uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/test_wc_swing.py tests/test_wc_attack_table.py tests/test_wc_proc_events.py -q -p no:cacheprovider
```

SHA256 values:

| corpus | sha256 |
|---|---|
| `swing.json` | `54cae731f25831c2acf1842d3434404b288708e14bb8d5181b8a067b844b07c2` (28,905 B; 30 rules, 12 timelines) |
| `attack-table.json` | `b27c04812a6e2d2f6f195077cfca689bc646db4b36340f141162623eb70dc04d` (91,185 B; 16 examples) |
| `proc-events.json` | `72a4baa86aaf0854fdd5db770c413a0bb443516d27e19f8fd857052139514103` (62,920 B; 33 events, 16 facts, 0 contradictions) |
| `witnesses-d.json` | `891a120f5d934c4bc6f316a6989f61fbd0d5f079c718c81972a19f3db289f381` (132,819 B; 16 table witnesses, live census, level census) |

| `creature-level-deltas.json` | `cc93842e2df07a8dd6f98fa25ead5798c5439bfe51d2162749c4701b54bf90be` (5.62 MB; `creature_template_difficulty` projected to Entry, DifficultyID, LevelScalingDeltaMin/Max, ContentTuningID; 320,629 rows after replaying the updates; 1 unparsed statement recorded in its provenance, 2026_05_01_00_world.sql) |

All four D corpora are byte-identical across reruns (verified). `creature-level-deltas.json` is regenerated with:

```text
python3 tools/tdb_world_extract.py --tdb ../../../../bag/tdb/TDB_full_world_1200.26021_2026_02_06.sql --tables creature_template_difficulty \
  --project creature_template_difficulty=Entry,DifficultyID,LevelScalingDeltaMin,LevelScalingDeltaMax,ContentTuningID \
  --out ../../docs/research/weapon-combat-corpora/creature-level-deltas.json    # ~19 s with the lead's key index
python3 weapon_combat.py all-d --census --levels ../../docs/research/weapon-combat-corpora/creature-level-deltas.json   # ~44 s
```

## 12. Unresolved and build-skew populations

### Part C

| Item | Class | Why | Reopen condition |
|---|---|---|---|
| Armor constant at level 90 for players and player-owned pets | unresolved | `ItemLevelByLevel.txt` is absent (14 GameTables present); `ASSERT_NOTNULL` in Unit.cpp:1762 | add `ItemLevelByLevel.txt` to `data/tables`; evaluate Curve 27400 at `AvgItemLevel − ItemLevelByLevel[90]` |
| Level-90 attack power | unresolved | `player_classlevelstats` covers levels ≤ 80 | a level-90 base-stat source (Track F) or a supplied `UnitData::AttackPower` |
| Glancing vs current creatures | unresolved | 599 scalable `creature_template_difficulty` rows have delta ≥ 4; CT level ranges not evaluated | evaluate `DB2Manager::GetContentTuningData` for those CT IDs and test `90+3 < clamp(...)+delta` |
| Crushing from player/pet attackers | legacy-only | `!IsControlledByPlayer()` gate; the chance term is negative for creature attackers too (R2-3) | an upstream fix to Unit.cpp:2489 (creature attackers only) |
| `CR_ARMOR_PENETRATION` rating | legacy-only | 0 items carry stat 44 | an item or enchant with `ITEM_MOD_ARMOR_PENETRATION_RATING` |
| Resilience in white swings | legacy-only | player `GetOwnerGUID()` is empty; PvE victims no-op | a PvP victim study |
| `MOD_OFFHAND_DAMAGE_PCT` (122) | structural-inference | no spell in either scope | a current spell carrying aura 122 |
| Player block fraction (≤ 0.85 % of a white swing; 0 of a weapon special) | trinity-probe / trinity-consumer (consumer quirk) | `GetBlockPercent` returns a fraction where a percent is consumed; the spell path truncates it to uint32 0 (Unit.cpp:1239) | Retail block semantics from an independent oracle |
| Critical block amount read from the attacker (429638 Martial Expert) | trinity-probe (consumer quirk) | Unit.cpp:1463 `this->GetTotalAuraMultiplier` | an independent oracle for aura 638 ownership |
| Odyn's Fury OH (205547) without the 0.5 factor; ATTR6/non-physical OFF specials | trinity-probe | `addPctMods=false` recompute drops TOTAL_PCT | independent Retail combat-log evidence |
| Multi-fixed / multi-percent double counting | trinity-probe; not reachable in scope | 0 player-scope spells; 9 snapshot-wide (mask not evaluated) | a scope spell with two such effects hitting one target |
| Negative crit-damage product → UB wrap | trinity-probe (UB) | x86-64 behaviour only; 139883/154429 not in scope | an in-scope negative aura 163 |
| Ghost Wolf form 48 vs `FORM_GHOST_WOLF` 16 | unresolved (possible build-skew) | `IsInFeralForm` misses current Ghost Wolf | a 12.0.7 `SpellEffect` for 2645, or a Trinity enum update |
| Non-feral CRT forms 44-49 (Xuen CRT = 1) | legacy-only | only 426268 (form 46) applies one; not in scope | a current applier in scope |
| Feral normalized special adds the OFF AP term | structural-inference, unreachable | no druid spec reaches a weapon-effect spell | a druid weapon-effect spell in scope |
| `Spell::CalculateDamage` spellmods / `ApplyEffectModifiers` in special attacks | unresolved (not modelled) | values supplied as base points | model SpellMod ops for the 11 spells |
| `CalcAbsorbResist`, `ModifyMeleeDamage` script hook | unresolved (not modelled) | victim auras / scripts | a victim-aura model; script index (dummy_semantics) |
| 91 weapon-effect spells newer than 12.0.7 | build-skew | no consumer is possible in the pinned build | a Trinity update to ≥ 12.1 |
| Creature armor 1470 used in the damage view | structural-inference | `ArmorModifier` from `creature_template_difficulty` not applied | Track A/B creature armor |

### Part D

| item | class | why | reopen condition |
|---|---|---|---|
| level delta of the current raid bosses (JournalInstance 1317/1320, 9 encounters) | build-skew / unresolved | the creatures are absent from TDB 1200.26021 (Trinity supports ≤ 12.0.7) | a TDB with those `creature_template_difficulty` rows |
| glancing against current bosses | unresolved | depends on the row above; the pinned DB has 0 ExpansionID-11 rows at +4 | as above, with `LevelScalingDeltaMax ≥ 4` |
| ContentTuning 12.1 → Trinity field mapping | structural-inference | column names differ (Squish/ScalingOffset/AllowedOffset/Lfg) | a 12.1-aware `ContentTuningLoadInfo` or a Wago schema diff |
| ConditionalContentTuning redirects, `MaxCreatureScalingLevel`, `ScalingPlayerLevelDelta` in the level census | unresolved (assumed absent) | runtime player fields and redirect rows not modelled | model `GetRedirectedContentTuningId` and those player fields |
| `SpellModOp::HitChance` / `CritChance` stacking | supplied input | `ApplySpellMod` flat and pct order is owned by the proc/spellmod research | a spellmod oracle |
| aura amounts that are 0 in DB2 (253 Mastery: Critical Block, 51 Divine Bulwark, 458 Enrage) | unresolved (amount only) | amount comes from mastery or scripts | the part-C / charstats mastery scalar |
| Retail semantics of auto-shot replacements (467718 interrupted by any cast in Trinity) | trinity-consumer only | Trinity keys exemptions on id 75 | Retail observation |
| `GetBlockPercent` unit mismatch (player fraction vs percent) | trinity-consumer | consumer behaviour, possibly a Trinity bug; not a Retail claim | upstream fix or Retail block-amount evidence |
| host tick | host parameter | Trinity has no fixed tick | — |
| crushing | legacy-only (dead code) | precedence bug at U:2489 | upstream fix and a +4 creature |

## 13. Cross-part overlaps

### Part C

- **Agent D:**
  - D's `attack-table.json` and C agree on:
    - the 1.3 crit truncation (1000 → 2599);
    - the white-swing player block fraction (Player.cpp:26822, Unit.cpp:1459) and the creature 30 % (Unit.h:987);
    - the spell-path truncation to 0 (Unit.cpp:1239);
    - partial blocks dealing damage unless `COMPLETELY_BLOCKED` (Spell.cpp:2763).
  - Two additions from C:
    - the critical-block multiplier comes from the **attacker** (Unit.cpp:1463, 1243); D's corpus does not name the
      owner;
    - D's switch coordinates are the same statements, one or two lines apart (C uses the `case` line through `break`).
  - D's `creature-level-deltas.json` is the canonical extraction for the glancing-reachability count.
  - `RollMeleeOutcomeAgainst` probabilities (glancing/crushing chance, the crushing precedence
    `attackerLevel - victimLevel * 1000 - 1500` at Unit.cpp:2489, dual-wield miss +19 %, Unit.cpp:12463-12465). These
    are used here only as supplied outcomes.
  - The glancing reachability count (599 rows) is shared input for D's attack table.
  - Next-swing spells: 0 in either scope.
  - `COMMAND_MODULES` now holds C's and D's lines; I did not edit D's line.
- **Agent E (Track C):** `weapon_combat.weapon.CHARACTER_PREP_HOOK` now exists, so E's WEAPON_HOOK stops degrading to
  `unresolved`. It reports AP as unresolved and applies no aura stages.
- **Agents E/F:** prepared AP and base stats at level 90 are theirs; the witnesses use AP = 0.
  `ItemLevelByLevel` is also needed by any armor-dependent differential.
- **Agents A/B (controlled units):**
  - Player-owned pets take the max-level armor curve with the **owner's** `AvgItemLevel` (Unit.cpp:1747-1748).
  - Pet victims get the owner's versatility-taken (`GetSpellModOwner`, Unit.cpp:8212).
  - Pets can apply resilience (`CanApplyResilience` needs an owner player).
  - Creature armor multipliers (`ArmorModifier`) belong to A/B.
- **dummy_semantics.scope:** the extended mode misses default-skill spells (SkillLine CategoryID ≠ 7, e.g. 674).
  `weapon.default_skill_grants` reproduces Trinity's path for DW/TG only. The scope itself was not modified.
- **Contradicts character-stat-pipeline-archaeology.md:298-300** (weapon `dps*6` lands in `TOTAL_VALUE`): see §2.
  The gearing report is consistent.

### Part D

**Part C (weapon model).**
- Owns the inputs used here: `GetBaseAttackTime` values, `CalculateDamage` (U:2550, the first draw of a white swing), and weapon-spell damage.
- In C's `damage.py:330`, Python warns about an invalid escape in a docstring (`SyntaxWarning: invalid escape sequence '\ '`).

**Pet swings (parts A and B).**
- `Pet`/`Guardian` melee uses the same `DoMeleeAttackIfReady` (Creature.cpp:876). `haveOffhandWeapon` for pets is `CanDualWield()`.
- Pets get the creature offset in `Unit::Attack` (U:5944-5946). Their `m_modMeleeHitChance` is 0 (U:364-365), so their base miss is 5.
- Glancing applies to `IsPet()` attackers (U:2455).
- Pet spell crit needs `GetSpellModOwner()` (U:7122).
- Pet white swings never visit the owner's auras (U:10587-10604).
- Parry-haste on pets is not suppressed; only the creature flag 0x8 suppresses it.

**Track C (character prep).**
- The table consumes `DodgePercentage`, `ParryPercentage`, `BlockPercentage`, `CritPercentage`, `ShieldBlock`, `MainhandExpertise` and `CanParry`/`CanBlock` (`SPELL_EFFECT_PARRY` 22 is class-skill only; `SPELL_EFFECT_BLOCK` 23 comes from SkillLine 95).
- ArmorConstant comes from ExpectedStat (rows 475/478).

**Proc report.**
- No contradiction with `procs/events.py`. §8.3 (hand bits on melee abilities) and T5 (blocked weapon spells report FULL_BLOCK while dealing damage and never crit) add detail to the event-identity section.

**Shared world-DB extraction.**
- `creature_template_difficulty`: D's committed projection `weapon-combat-corpora/creature-level-deltas.json` (all rows, 5 columns) overlaps agent A's `world-db-corpora/creature-templates.json` (their entries) and the lead's full extract (`scratchpad/lead/ctd_full.json`, same 320,629 rows).
- The first agent D used the wrong column names (`DeltaLevelMin/Max`); the correct names are `LevelScalingDeltaMin/Max`.

## 14. Lead reconciliation between parts C and D

Both parts were written in parallel. Where they overlap, the lead re-read the source.

| topic | part C | part D | reconciled statement |
|---|---|---|---|
| player block amount | "≤ 0.85 % of a white swing" | "about 0.37 % of a white hit" | Not a conflict: 0.85 % is the `min(…, 0.85f)` cap (`Player.cpp:26831`); 0.37 % is ShieldBlock 2000 against ArmorConstant 3430. |
| glancing reachability | 599 scaling-enabled `creature_template_difficulty` rows with delta ≥ 4, ContentTuning ranges not evaluated → unresolved | level census: 19 rows / 13 entries reach +4 against a level-90 player, none current content → "legacy-only for current content" | The mechanic is live code with a reachable input, so it is **not** legacy-only. Pinned-TDB verdict: reachable only against 13 non-current entries; current raid bosses are absent from TDB 1200.26021, so glancing against them is **unresolved (build skew)**. D's census supersedes C's raw row count. |
| crit ×1.3 | 1000 → 2599 | 1000 → 2599 | agree (`Unit.cpp:1430-1436`) |
| `rand_chance()` | — | float (`Random.cpp:81-85`) | D's correction of the earlier handoff stands. |
| Arms dual wield | capability table lists 296087 (SkillLine 840, AcquireMethod 0) | — | See `character-preparation-closure.md` lead reconciliation: 296087 is `Learned` (never auto-learned); it is taught only by 296088 "Learn Dual Wield", which no automatic source grants. Arms dual wield is **character state**, not derivable. The closeout review (§15) applied this to the code: `weapon-sources.json` now keeps 296087 in `capability_by_spec_learned_only`, and `weapon`, the Track C hook and `witnesses-c.json` no longer count it. |

## 15. Closeout review

**Method.** An independent reviewer (G2) read the report end to end and listed 41 claims: the headline
list, every "never/always/only/every" statement, the numeric claims and every claim stronger than
`structural-inference`. Each high-value claim was re-derived from TrinityCore `7f3d43b`. The reviewer read
the whole cited function, including early returns, overrides and later calls in the same swing, rather
than only the cited line. The data counts were rerun against `data/tables` and the corpora. Both probes
were rebuilt from a fresh `extract.py` run. Probe case W1 (dual wield against a level-93 creature) was
recomputed by hand from the C++ text:
- miss `5 + 19 − 7.5 = 16.5`;
- dodge `3 + 1.5·3 − 7.5 = 0`, so there is no band;
- parry `6 + 4.5 − 7.5 = 3`;
- block `3 + 4.5 = 7.5`;
- crit 20.

The probe prints `miss@0 parry@1650 block@1950 crit@2700 normal@4700`, which matches. Both `extract.py`
files take function bodies with brace-matched `definition`/`body` and take the documented `cut` ranges
from the checkout. The only edits are the documented `break;`→`return;` change and the `GetBlockPercent`
rename. `all-c` and `all-d` were each run twice: the generated corpora are byte-identical between runs,
and no corpus contains a host path.

**Findings.**

| id | claim | verdict | action | evidence |
|---|---|---|---|---|
| G2-1 | Headline 1: MH and OH never swing in the same update | overstated | Scoped to `DoMeleeAttackIfReady`. Queued extra attacks, drained earlier in `Unit::Update`, can share an update with an OH swing; none is reachable in scope (R2-6) | U:441-454, 2210-2242 |
| G2-2 | Auto-shot cadence `ceil(P/T)+1` | confirmed | — | The event is added at `m_time+1` inside `_UpdateSpells` (S:3466) and runs in the next `m_Events.Update` (EventProcessor.cpp:40-47); `SendSpellCooldown` resets RANGED at cast (S:4225-4234); neither 75 nor 467718 has `ATTR12_START_COOLDOWN_ON_CAST_START` (SpellMisc `Attributes_12` = 0) |
| G2-3 | Haste removal drifts in binary32 | confirmed | Headline now names the mechanism | Removal multiplies by the inverse factor (U:10978-10981, 10988/10997); nothing recomputes `m_modAttackSpeedPct` (its only writers are U:327 and those two lines). By hand in binary32: v = 15, 3 or 5 gives 0.99999994, so 2600 → 2599 and 3600 → 3599; v = 20, 30 or 10 round-trips exactly. An independent exhaustive recount gives the same totals: 21,480 of 99,010 pairs, 190 of them at integer v |
| G2-4 | "One roll per swing"; the §7.3 draw list | defect (incomplete) | The outcome is one `urand(0,9999)`. The table, `attack-table.json` and the headline now list all draws: the damage draw first; the block-crit draw; a resist draw for magic-school weapons; `DealDamage` durability draws for player victims and attackers (default rate 0.5); daze; procs. Physical immunity skips the damage draw. Engine-output consumption is not modelled (R2-1) | U:1090-1107, 1371-1383, 1775-1799, 2410, 2550, 2575-2580; World.cpp:1015 |
| G2-5 | Crushing unreachable (precedence) | confirmed; part C's wording was incomplete | Part C's §3/§12 rows and unknown C-4 said "player/pet attackers". The expression is int32 on uint8 levels, so it is negative for **every** attacker. With the `roll ≥ sum` invariant, the band is dead code for creatures too (R2-3) | U:2410-2413, 2481-2494 |
| G2-6 | Glancing: `+3 <`, `GetLevelForTarget`, +3 boss | confirmed | §10 now states the input as `clamp(90, CTmin, CTmax) + delta ≥ 94` instead of "MaxLevel 90 and delta ≥ 4", and says that a +3 boss never glances | U:2412-2413, 2455-2459; Creature.cpp:3130-3160 (target's `GetEffectiveLevel`; `ScalingFactionGroup` is never written for creatures, so that branch is dead) |
| G2-7 | Level census (19 rows / 13 entries; 0 on ExpansionID 11) and the 599 count | confirmed | Added a supersession note to C-3 | Recomputed 599 (601 including CT 0); census rerun is byte-identical; the model matches `GetContentTuningData`'s type adjustments (DB2Stores.cpp:2192-2216) |
| G2-8 | Glancing damage: `CleanDamage` wraps when `leveldif < 0` | overstated | This is unreachable from the chance path: raw level `ScalingLevelMax + delta ≥ GetLevelForTarget`, so the factor is always `0.69999999f` (1000 → 700, probe) (R2-5) | Creature.cpp:1614-1620; U:1474-1481 |
| G2-9 | Player block is a fraction consumed as a percent; the spell path truncates it to 0 | confirmed | Headline notes that there are only two definitions | `CalculatePct<T,U>` = `T(base*float(pct)/100.0f)` (Util.h:72-75); `GetBlockPercent` exists only at Unit.h:987 and Player.cpp:26822 (grep); `uint32 value` (U:1239); probe `block 1000 1 2000 3430` → 997/3 |
| G2-10 | Crit ×1.3 → 2599 | confirmed | Coordinate `:1432` → `:1430-1436` | `GetTotalAuraMultiplier` aggregates in double and returns `static_cast<float>` (U:5009-5034); probe `crit 1000 1.3` → 2599 |
| G2-11 | Normalized speed constants | confirmed | Added the current-data population to §1.2 (effect 121: 116 rows; 0 default / 8 class-line spells) | U:11051-11088; SpellEffect.csv |
| G2-12 | The 0.5 off-hand factor lives in `TOTAL_PCT`, and `addPctMods=false` drops it | confirmed | Headline quantified (4 of 11; 205547 is the only off-hand one) | U:9793, 9781-9819; SpellEffects.cpp:2889-2908; StatSystem.cpp:450 |
| G2-13 | Weapon specials double-count (headline 7) | overstated | The headline now says that no in-scope spell can double-count (9 snapshot-wide) | SpellEffects.cpp:2911-2935; `special-attacks.json` `multi_fixed_or_multi_pct: 0` |
| G2-14 | `EffectWeaponDmg` coordinates `:2887`, `:2944`, `:2947`, `:2948` | defect | Corrected to `:2889`, `:2941`, `:2944`, `:2945`. Added the spell-script hook in `MeleeDamageBonusDone` (U:8116-8119); no script is bound to the 11 specials | grep at `7f3d43b` |
| G2-15 | Default-skill dual wield (674) for Hunter, Rogue and DH | confirmed | — | SkillLineAbility 610 (AcquireMethod 2, ClassMask 2573); SRCI 131; existing snapshot test |
| G2-16 | Capability table: 296087 grants dual wield to 71/72/73 | **defect** (code + corpus) | `weapon.py` now moves AcquireMethod-0 class-line grants to `capability_by_spec_learned_only`. `weapon` (`--learned-spell`), the Track C hook (`identity.learned_spells`) and `witnesses-c.json` count them only when the spell is supplied as learned. Arms and Protection now report `dual_wield: false` (R2-2) | Player.cpp:25404-25417; SkillLineAbility 40461; lead ruling in character-preparation-closure.md |
| G2-17 | Every "melee-class ability" carries the MH/OH swing flag | confirmed; wording ambiguous | Now reads "every `DmgClass` MELEE spell (250 in scope)". The flag is also on the spell's cast and finish events | S:2350-2355, 2846, 3902, 4219 |
| G2-18 | Extra attacks are unmarked; nothing suppresses a white event | confirmed | — | U:2262-2363 (`extra` changes only two gates), U:5568-5596 (the suppress attributes need `spellInfo`, the chain limit needs `spell`) |
| G2-19 | §10 hit / expertise / ArP rating verdicts | confirmed, evidence strengthened | Stats 31/37/44 appear on no ItemSparse row in any expansion and on no enchant; `MOD_RATING` bits 23/24 are not in scope | ItemSparse / SpellItemEnchantment / ItemBonus census (R2-4) |
| G2-20 | §10 "dodge/parry/block ratings: item stats 13/14/15 absent" | defect (wording) | Stats 13/14 are on 1,785 legacy items (ExpansionID ≤ 10), on 375 legacy enchants and in legacy bonus lists 6676/6677; none is in the current gear corpus. The verdict (legacy-only) stands; the text now names the inputs | R2-4 |
| G2-21 | Weapon skill, deflect, resilience verdicts | confirmed | The reaching input is now named for resilience and deflect | Unit.h:931 (`level × 5`, used in combat only by daze at U:1585-1586); aura 287 at U:2670-2677 and Object.cpp:1933; resilience needs `CanApplyResilience` (U:12416-12419) |
| G2-22 | §13 coordinates `Unit.cpp:2491` and "+19 % at 2460-2466" | defect | → `:2489` and `:12463-12465` | grep |
| G2-23 | §1.4: the feral CRT branch is an identity | overstated | It is an identity for non-normalized calls only; a normalized call divides by the normalized speed | StatSystem.cpp:445, 459-464 |
| G2-24 | Probe fidelity | confirmed | — | Both `extract.py` files read verbatim text; W1 was checked by hand; the probe's `GetTotalAuraMultiplierByMiscMask` stub (`float(double)`) matches the real single-aura cast |
| G2-25 | Remaining numeric claims: T1 (`0.53f` → 52), W2 two-hand miss 0, 20 % / 10 % haste periods 2166 / 3272, parry-haste 960 / 520, OH creature offset | confirmed | — | Probe `atkmod 0 2600 1 15 1` → 0.869565189 / 2260; the other outputs match the committed `attack-table.json` and `swing.json` |

The other 16 claims checked were confirmed without change, by source read or corpus rerun:
- §1.1 slot and attack-type rules;
- §1.3 `CalculateMinMaxDamage` and `GetTotalAttackPowerValue`, including weapon AP on the AP-coefficient path (U:6851-6873);
- §4.1 loop semantics and `ApplyPct<float,double>`;
- §4.2 `GetAttackType`;
- the §4.3 census (7,937 spells; 2 and 11 weapon specials; 210 and 288 AP-coefficient rows, via the `special` CLI);
- the §6.2 update order;
- the §6.3 gates and 100 ms error timer;
- §6.6 reset and pause;
- the §6.8 id-75 exemptions;
- `MeleeSpellMissChance` (+19 is white-only; the flat 7.5 hit);
- the expertise flat 7.5;
- the §7.2 getters;
- the §8.1 flags and hit masks;
- the §8.4 FINISH/CAST sites;
- the §14 crit and `rand_chance` rows.

**Fixes applied.**
- `scripts/research/weapon_combat/weapon.py`:
  - added `learned_only_skill_line_spells` and `_dual_wield_from_capability`;
  - `sources` now writes `capability_by_spec_learned_only`;
  - `weapon` gained `--learned-spell`;
  - `CHARACTER_PREP_HOOK` honours `identity.learned_spells` (ints or `{spell_id}`).
- `weapon_combat/witnesses_c.py` uses the shared loader and records `learned_only_dual_wield`.
- `weapon_combat/attack_table.py` lists the full white-swing draw sequence in the `white_mh` path record.
- `tests/test_wc_weapon.py` has 4 new tests.
- Regenerated corpora:
  - `weapon-sources.json` → `17d55dc8…`;
  - `witnesses-c.json` → `9f318331…` (71/73 `dual_wield` false; hands unchanged);
  - `attack-table.json` → `b27c0481…`;
  - the other corpora are unchanged.
- `unknowns.json` gained R2-1…R2-6, and C-3 and C-4 gained notes and `related` links.
- Report text: the headline list, §1.2, §1.4, §1.5, §3, §4.1, §5.1, §7.3, §7.4, §10, §11, §12, §13 and §14 as listed above.

**Weakened conclusions.**
- Headline 1 (the MH/OH rule is scoped).
- Headline 3: "one roll" now means the outcome draw only.
- Headline 7: double counting is not reachable in scope.
- Headline 10: wording.
- §1.4: identity only for non-normalized calls.
- §3: the glancing wrap is unreachable.
- §10: the glancing input formula.

**Confirmed conclusions** are G2-2, 3, 5, 6, 7, 9, 10, 11, 12, 15, 17, 18, 19, 21, 24 and 25, verified as described in the table.

**Residual risks.**
- **Stale Track C fixtures.** The compiled Arms fixtures in `character-prep-corpora/fixtures/` still embed the
  hook's old `capability.dual_wield: true`, and will read false once Track C recompiles them. Their `test_cp_compiler`
  currency tests were already failing during this review because of concurrent Track C changes; this review did not
  regenerate Track C corpora.
- **RNG stream not reproducible.** A stream-exact RNG replay stays unresolved (R2-1).
- **Consumer facts, not Retail truth.** The glancing and crushing verdicts rest on Trinity. Current raid bosses are
  still absent from the pinned TDB (D-1).
- **Recompute-order assumption.** The haste drift finding assumes auras are applied and removed one at a time through
  `ApplyAttackTimePercentMod`. A future Trinity change that recomputes the multiplier would remove it.
- **Tree-sitter-free extraction.** The probes' `extract.py` matches braces textually. A brace inside a string or
  comment in a future Trinity revision could shift a cut; the Makefile regenerates the extraction on every build, but
  nothing checks the cut boundaries.
