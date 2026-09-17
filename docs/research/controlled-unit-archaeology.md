# Controlled-unit archaeology (pets, guardians, summons, charmed units)

## Scope and result

This pass asks, of the checked-in snapshot and the pinned TrinityCore checkout:

> Which semantic dimensions does a current player's pet, guardian, summon, totem or
> controlled unit need (population, ownership, stat inheritance, abilities, lifecycle),
> and which of them are supplied by client data, by Trinity's world database, by
> Trinity code, or by nothing we can see?

It deliberately does **not** propose a universal Pet object model. Every unit is
described by the branch the pinned consumer takes for its source integers
(`SummonProperties.Control/Title/Slot/Flags`, effect type, aura type), and a
category is only a name for that branch.

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
| world DB | `TDB_full_world_1200.26021_2026_02_06.sql` (sha256 `54ddf4c1…5172d`) + `sql/updates/world/master` replay |
| coordinates | `path:line` under `src/server/game/` unless another root is named |

| deliverable | path |
|---|---|
| this report | `docs/research/controlled-unit-archaeology.md` |
| research package / CLI | `scripts/research/controlled_units/`, `scripts/research/controlled_units.py` |
| corpora | `docs/research/controlled-unit-corpora/` (vocabulary, population, census, ownership, lifecycle, witnesses-a, stats, inheritance, spells, witnesses-b, unknowns) |
| world-DB extracts | `docs/research/world-db-corpora/{creature-templates,pet-witness-templates,player-base-stats}.json`; extraction id set `creature-templates.ids.json` |
| world-DB extractor | `scripts/research/tools/tdb_world_extract.py` (generic, indexed; shared with the other tracks) |
| differential C++ probe | `scripts/research/tools/tc_pet_probe/` |
| tests | `scripts/research/tests/test_cu_*.py` (134 tests) |

Sections 1–5 (part A) cover vocabulary, population, ownership, lifecycle; sections 6–11 (part B)
cover stat inheritance, spell acquisition, execution and the probe.

**Headline results.**

1. **Population (current player scope, default roots).** 62 unit-creating/controlling records on
   61 spells and 53 creatures. By Trinity branch: guardian 25, totem 17, ally-summon 5,
   controllable-guardian 4, wild-summon 3, permanent pet 2, charmed 2, possessed 1,
   possessed-own-pet 1, companion 1, no consumer 1. By C++ class: Guardian 29, Totem 17,
   TempSummon 8, Pet 2, Minion 1, none 5 (§2).
2. **World-DB absence tracks build skew exactly in the default scope.** Only 2 creatures are
   absent from the pinned TDB, and they belong to exactly the 2 spells newer than 12.0.7.
   With class skill lines, 64 are absent: 42 build skew, 22 world-DB gaps (§2).
3. **Owner spell mods and KILL-proc forwarding reach only `Pet` and `Totem`.** Guardians,
   Minions and plain TempSummons get neither (`Object.cpp:1656`, `Unit.cpp:11383-11388`).
   Spell proc actor is `m_originalCaster ?: m_caster`; melee proc actor is the attacker (§3).
   Other owner paths exist and are not limited to Pet/Totem (closeout review, §15):
   `SPELL_ATTR6_ORIGINATE_FROM_CONTROLLER` makes the charmer/owner the caster for any class
   (`Spell.cpp:475`); PvP resilience uses the player owner of any owned unit
   (`Unit.cpp:12416-12440`); `SpellHealingBonusDone` reads the owner's `OVERRIDE_CLASS_SCRIPTS`
   for any owned unit (`Unit.cpp:7342`); a Totem forwards its whole spell damage, healing and
   absorb bonus (`Unit.cpp:6834, 6915, 7330, 7442, 7627, 7693`).
4. **Pinned-Trinity regression:** `GetCharmerOrOwnerOrOwnGUID` is inverted
   (`Object.cpp:1600-1602`, commit `4ba5e27055c`), so `IsCharmedOwnedByPlayerOrPlayer` is false
   for uncharmed players and for player-owned pets/guardians, and true for a player charmed by a
   creature. 8 call sites are affected. This is a defect of the oracle, not a semantic (§3).
5. **No pinned Trinity code reads owner haste, crit or mastery for a controlled unit**
   (`trinity-consumer`, absence; the rule itself stays `unresolved`). Guardians also get no
   owner versatility, no owner spell mods and no spell crit (`Object.cpp:1648-1670`,
   `Unit.cpp:7124`). `SPELL_ATTR4_OWNER_POWER_SCALING` has no reader, although the hunter-pet
   passive 34902 carries it. Inheritance is a per-class, per-stat table, not a family rule, and
   scripts add per-entry exceptions: Divine Image 198236 snapshots the owner's holy healing
   bonus (`scripts/Pet/pet_priest.cpp:43-49`) (§6–§7, §15).
6. **Guardian arithmetic has reachable binary32 and double-count effects** (differential against
   extracted C++): non-Pet Guardians add ExpectedStat health twice (Greater Fire Elemental
   95061: 16,681,309 max health for the level-90 test owner); an Imp at level 80 gets 128,128
   max health where exact arithmetic gives 128,129; a negative owner spell-power aura raises the
   pet's bonus (§7, §10).
7. **The pet-specific aura types that current masteries use have no Trinity handler.** Aura 429
   `MOD_SUMMON_DAMAGE` is `HandleNULL` and appears on 63 in-scope spells, including the Beast
   Mastery, Demonology and Unholy masteries; auras 381/382/157 likewise (§9).
8. **Several current summons are inert or unlinked in Trinity.** The player's Raise Dead (46584)
   summons nothing (script hook no longer matches the effect layout); 7 casts have no link to
   their summon; Guardian of Ancient Kings (86659) has no 12.1 summon effect. Grove Guardians
   (54983) are prepared at level 0 on every map, and Mirror Image (31216) on maps whose
   difficulty resolves to the DIFFICULTY_NONE row (open world, raids, delves). In 5-player
   dungeon difficulties 1/2/8/23/24, and in difficulties 205 (Follower) and 216 (Quest), whose
   fallback chains also reach row 1 or 2, 31216 has ContentTuning 482 instead (§7.2 #13; R4-2). With the
   default `MaxPlayerLevel` 90, hunter pets at levels 81–90 get placeholder 1-valued stats from
   the TDB (§8, §7).
9. **Hunter-pet identity is character-database state** (stable slot, family, spec, creature
   entry), i.e. a caller-supplied preparation input, not DB2 or world-DB data (§5).
10. **17 of the 837 census spells have no Trinity acquisition path** (e.g. Felstorm 89751);
   14 have no spell record. The census covers pet-family skill-line spells, the template spells
   of *every* entry a 12.1 effect 28/56 summons (NPC summons included, not only current-player
   units) and pet spec spells. Of the 806 with a path, 599 have only the structural
   `template-ai` path, whose use depends on the selected AI (§8).

## 0. Part conventions

### Part A

Pins: snapshot `12.1.0.69497`; TrinityCore `7f3d43b7c8dd` (all `path:line` below are relative to
`src/server/game/` unless another root is given); world DB `TDB_full_world_1200.26021_2026_02_06.sql`
plus `sql/updates/world/master`. Code: `scripts/research/controlled_units/{vocabulary,population,census,ownership,lifecycle,witnesses_a}.py`.
Corpora: `docs/research/controlled-unit-corpora/{vocabulary,population,census,ownership,lifecycle,witnesses-a}.json`
and `docs/research/world-db-corpora/creature-templates.json`.


No universal Pet object model is proposed. Each controlled unit is described by the branch the pinned
consumer takes for its source integers (`SummonProperties.Control/Title/Slot/Flags[0]`, effect type,
aura type). A category is a name for that branch and is never inferred from a spell or creature name.

### Part B

Part B — Track A3/A4/A7: controlled-unit stat inheritance, spell acquisition/execution, pet probe

Pins: TrinityCore `7f3d43b7c8dd` (all `path:line` below are under `src/server/game/` unless stated),
snapshot `12.1.0.69497`, TDB `TDB_full_world_1200.26021_2026_02_06.sql` (+ `sql/updates/world/master`).
Code: `scripts/research/controlled_units/{stats,inheritance,spells,witnesses_b}.py`,
`tools/tc_pet_probe/`. Corpora: `docs/research/controlled-unit-corpora/{stats,inheritance,spells,witnesses-b}.json`,
`docs/research/world-db-corpora/pet-witness-templates.json`.

## 1. Source vocabulary

### 1.1 Discriminants and their readers

| source | field(s) | reader | coordinate |
|---|---|---|---|
| `SpellEffect.Effect` | 28 SUMMON, 55 TAMECREATURE, 56 SUMMON_PET, 153 CREATE_TAMED_PET | handler table | `Spells/SpellEffects.cpp:120, 147, 148, 245` |
| `SpellEffect.Effect` (lifecycle) | 102 DISMISS_PET → `EffectDismissPet`; 135 CALL_PET, 168 ALLOW_CONTROL_PET, 188 SUMMON_STABLED_PET_AS_GUARDIAN, 199 DESPAWN_SUMMON, 260 SUMMON_STABLED_PET → `EffectNULL` | handler table | `SpellEffects.cpp:194, 227, 260, 280, 291, 352` |
| `SpellEffect.Effect` (not units) | 76/104 object summons (excluded); 119/202 area auras → `EffectUnused` | handler table | `SpellEffects.cpp:168, 196, 211, 294` |
| `SpellEffectAura` | 2 MOD_POSSESS, 6 MOD_CHARM, 177 AOE_CHARM (→ `HandleCharmConvert`), 378 MOD_POSSESS_PET | aura table | `Spells/Auras/SpellAuraEffects.cpp:74, 78, 249, 450` |
| `SpellEffectAura` (pet-scoped, no handler) | 157 PET_DAMAGE_MULTI, 381, 382, 429 → `HandleNULL`; 146, 339 → `HandleNoImmediateEffect` | aura table | `SpellAuraEffects.cpp:229, 453, 454, 501, 218, 411` |
| `EffectMiscValue_0` | creature entry (28/56/153) | `EffectSummonType` / `EffectSummonPet` | `SpellEffects.cpp:1872, 2669` |
| `EffectMiscValue_1` | `SummonProperties.ID` | `sSummonPropertiesStore.LookupEntry` | `SpellEffects.cpp:1876-1881` |
| `SummonProperties.Control` | `SummonCategory` 0 WILD, 1 ALLY, 2 PET, 3 PUPPET, 4 POSSESSED_VEHICLE, 5 VEHICLE | `EffectSummonType`, `Map::SummonCreature`, `Minion::IsGuardianPet` | `Miscellaneous/SharedDefines.h:6656-6663`; `Entities/Object/Object.cpp:1198-1240`; `Entities/Creature/TemporarySummon.cpp:499-502` |
| `SummonProperties.Title` | `SummonTitle` (0..44) | both switches, Guardian ctor, `SetMinion` (Companion) | `SharedDefines.h:6666-6711`; `TemporarySummon.cpp:518`; `Entities/Unit/Unit.cpp:6304` |
| `SummonProperties.Slot` | `SummonSlot` −1 ANY_TOTEM, 0 PET, 1-4 TOTEM, 5 MINIPET, 6 QUEST | `TempSummon::InitStats`, `Totem::InitStats` | `SharedDefines.h:6713-6725`; `TemporarySummon.cpp:224-239`; `Entities/Totem/Totem.cpp:58-70` |
| `SummonProperties.Flags` | `SummonPropertiesFlags`; **only `Flags[0]` is read** (`GetFlags`) | see 1.2 | `DataStores/DB2Structure.h:4317-4327`; `DataStores/DBCEnums.h:2754-2790` |
| `SummonProperties.Faction` | faction override | `TempSummon::InitStats` | `TemporarySummon.cpp:250-255` |
| `SpellDuration` | duration | `SpellInfo::CalcDuration` | `Spells/SpellInfo.cpp:3975-3991` |
| `ChrSpecialization` (ClassID 0) | pet specs 74/79/81 and override set 535/536/537 (Flags 32) | `Pet::LoadPetFromDB` spec remap, `Pet::SetSpecialization` | `Entities/Pet/Pet.cpp:413-417, 1904-1928` |
| `CreatureFamily` | family name, scale | `Pet::CreateBaseAtCreature*`, `GetNativeObjectScale` | `Pet.cpp:768-836, 1805-1822` |
| world `creature_template` | `type` (IsPermanentPetFor), `family`, `unit_class`, `unit_flags*`, `flags_extra`, `VehicleId`, `AIName`, `ScriptName` | `Pet::IsPermanentPetFor`, `Creature::Create` | `Pet.cpp:1657-1678` |
| world `creature_summoned_data` | `CreatureIDVisibleToSummoner` | `TempSummon::InitStats` (player summoner only) | `TemporarySummon.cpp:210-218` |

Snapshot sizes (`db2-fact`): `SummonProperties.csv` 3,610 rows (Slot: 0 → 3,097, 6 → 257, −1 → 130,
5 → 49, 1 → 32, 4 → 19, 2 → 14, 3 → 12; Control: 1 → 2,176, 0 → 1,024, 4 → 266, 2 → 89, 5 → 38,
3 → 17). Every Slot value is inside `m_SummonSlot` (size `MAX_SUMMON_SLOT` 7), and no Control value
reaches the `return nullptr` arm. `CreatureFamily.csv` has 85 rows. Unit-type effect rows over all
difficulties: 28 → 25,464; 56 → 244; 55 → 3; 153 → 85; 102 → 3; 135 → 1; 168 → 2; 188 → 6; 199 → 244;
260 → 1. Aura rows: 2 → 60; 6 → 118; 177 → 180; 378 → 1.

### 1.2 SummonPropertiesFlags consumed by the pinned checkout

Of 32 flags, only these are read (`rg SummonPropertiesFlags::`). All other flags are declared NYI or
have no reader: `DespawnOnSummonerDeath`, `DespawnOnSummonerLogout`, `DespawnWhenReplaced`,
`GuardianActsLikePet`, `UseLevelOffset`, `CannotDismissPet` and the rest.

| flag | bit | consumer |
|---|---|---|
| OnlyVisibleToSummoner[Group] | 0x10 / 0x10000 | `SpellEffects.cpp:1889-1899` (private object owner) |
| UseDemonTimeout | 0x40 | `SpellEffects.cpp:2006`; `TemporarySummon.cpp:199` → `TIMED_DESPAWN_OUT_OF_COMBAT` |
| UseCreatureLevel | 0x100 | `TemporarySummon.cpp:241` (skip the summoner-level clamp) |
| JoinSummonerSpawnGroup | 0x200 | `SpellEffects.cpp:1944` (→ SummonGuardian), `Object.cpp:1232` (→ GUARDIAN mask). The header marks it NYI, but it **is** read. |
| UseSummonerFaction | 0x1000 | `TemporarySummon.cpp:251` (then overwritten by `Minion::InitStats`, `:463`) |
| IgnoreSummonerPhase | 0x8000 | `Object.cpp:1278` |
| SummonFromBattlePetJournal | 0x200000 | `TemporarySummon.cpp:257`; `Unit.cpp:6309`; `Spells/SpellMgr.cpp:2521` |

### 1.3 The two switches (branch mirror, `vocabulary.summon_branch` / `unit_mask_for`)

`Spell::EffectSummonType` (`SpellEffects.cpp:1867-2085`) runs only in the LAUNCH phase. It returns
early when the entry is 0 or the SummonProperties row is missing (1872-1881). The caster is
`m_originalCaster ?: m_caster` (1883-1885), and `duration = CalcDuration(caster)` (1902). The
summoner passed to `Map::SummonCreature` is `GetUnitCasterForEffectHandlers()` (`Spell.cpp:8353-8356`).
`Map::SummonCreature` (`Object.cpp:1193-1323`) picks the C++ class **independently** from the same row.

| Control | Title / flag | EffectSummonType path | mask → class (`Object.cpp`) | category |
|---|---|---|---|---|
| WILD/ALLY | `JoinSummonerSpawnGroup` | SummonGuardian (1944-1948) | by title; default arm → GUARDIAN (1232) | guardian / controllable-guardian / totem … |
| WILD/ALLY | Pet, Guardian, Runeblade, Minion | SummonGuardian (1952-1957) | Minion/Guardian/Runeblade → Guardian (1215-1218); **Pet → default arm → TempSummon** unless Join | guardian (Pet: controllable-guardian with Join, else `wild-summon-via-SummonGuardian`) |
| WILD/ALLY | Vehicle, Mount | `SummonCreature` (1959-1968) | TempSummon (1224-1226) | vehicle-summon |
| WILD/ALLY | Lightwell | `SummonCreature` | **Totem** (1221) | lightwell-totem |
| WILD/ALLY | Totem | `SummonCreature`; a non-zero effect value sets max health (1969-1984) | Totem (1220) | totem |
| WILD/ALLY | Companion | `SummonCreature` + `SetImmuneToAll` (1985-1996) | Minion (1228-1229) | companion-minion |
| ALLY | any other | default arm (1997-2031): `SetOwnerGUID(caster)` only; **returns before `SetCreatorGUID`** | TempSummon | ally-summon |
| WILD | any other | default arm: `SetDemonCreatorGUID(caster)` if the caster is a player (2025-2026) | TempSummon | wild-summon |
| PET | any | SummonGuardian (2035-2037) | Guardian, plus CONTROLABLE_GUARDIAN (`TemporarySummon.cpp:518-522`) | controllable-guardian |
| PUPPET | any | `SummonCreature` (2038-2045) | Puppet | puppet |
| POSSESSED_VEHICLE/VEHICLE | any | `SummonCreature` + ride cast (2046-2077) | Minion | vehicle |
| other | — | — | `return nullptr` (1238-1239) | unresolved |

Consequences (`trinity-consumer`):
- `Title::Minion` never yields a `Minion` object. The Minion class is reached only through
  Companion or the vehicle controls.
- A SummonGuardian-path unit never passes through the trailing `SetCreatorGUID(caster)` (2080-2084):
  `SummonGuardian` keeps its own local `summon` (`SpellEffects.cpp:5037`). Its creator is set only
  by `Minion::InitStats` (`TemporarySummon.cpp:462`), so a plain TempSummon on that path has no owner
  and no creator.
- The two TempSummonType sources disagree on −1:
  - the default arm maps duration −1 to `MANUAL_DESPAWN` (2004-2005);
  - `TempSummon::InitStats`, which every other path relies on, maps `duration <= 0` to
    `DEAD_DESPAWN` (`TemporarySummon.cpp:195-203`).
- Possible defect, not probed: 1896 tests `caster->IsPlayer()` but dereferences
  `m_originalCaster->ToPlayer()`.

The mirror is exhaustive over every (Control 0..6, Title class, Join) combination:
`vocabulary.json` `branch_table` has 154 rows, and a hypothesis test checks that the mirror is total.

### 1.4 Other creators

| path | coordinate | class / identity |
|---|---|---|
| `EffectSummonPet` | `SpellEffects.cpp:2656-2744` | `Pet(SUMMON_PET)` through `Player::SummonPet(…, duration 0)` (2721); a non-player caster gets `SummonGuardian(properties 67)` (2671-2677); an existing same-entry pet (or entry 0) is teleported, no new unit (2682-2706); a different entry triggers `RemovePet(NOT_IN_SLOT)` (2708-2712); entry 0 → `PetSaveMode(effect value)` (2714-2716) |
| `EffectTameCreature` | `SpellEffects.cpp:2601-2654` | `Unit::CreateTamedPetFrom(Creature*)` (`Unit.cpp:11089-11111`) → `Unit::InitTamedPet` (`Unit.cpp:11133-11168`, needs a free active stable slot) |
| `EffectCreateTamedPet` | `SpellEffects.cpp:4911-4940` | `CreateTamedPetFrom(entry)` (`Unit.cpp:11113-11131`) |
| `EffectResurrectPet` | `SpellEffects.cpp:4271-4298` | `SummonPet(0, slot, …, 0)` |
| charm/possess auras | `SpellAuraEffects.cpp:3238-3331` | `Unit::SetCharmedBy(caster, CHARM/POSSESS/CONVERT)` (`Unit.cpp:11790-11962`). A creature caster of MOD_POSSESS falls back to CHARM (3248-3252). MOD_POSSESS_PET needs a player caster and its own `GetPet()` (3266-3281). |
| script `SummonCreature` | e.g. `scripts/Spells/spell_shaman.cpp:1395` (Healing Rain stalker) | `properties = nullptr` → TempSummon, no owner |

### 1.5 Hunter pet identity is a caller input (A1.3)

A tamed or called hunter pet is stored in the **characters DB** (`character_pet`, loaded into
`PetStable::PetInfo`, `Entities/Pet/PetDefines.h:147-164`). None of it exists in DB2 or the world DB,
so a Track C fixture must supply it. The field list with consumers is in
`witnesses-a.json` → `hunter_pet_caller_input`.

- **Load-bearing fields:**
  - `CreatureId`: `Pet::Create`, `Pet.cpp:253`; the tameable check at 231-240.
  - `Type`: `setPetType`, `Pet.cpp:258`; HUNTER_PET → class WARRIOR (293-299).
  - `CreatedBySpellId`: `Pet.cpp:260`; `isTemporarySummon = GetDuration() > 0`, 225-229.
  - `SpecializationId`: remapped by `ChrSpecialization.OrderIndex` to class 0, or to
    `PET_SPEC_OVERRIDE_CLASS_INDEX` under `SPELL_AURA_OVERRIDE_PET_SPECS` (413-417).
  - Stable slot: Call Pet N is `SPELL_EFFECT_SUMMON_PET` with entry 0 and base points N−1 (883 → 0,
    83242..83245 → 1..4). That value is the `PetSaveMode` → `ActivePets[N−1]` (`Pet.cpp:122-131`;
    `PET_SAVE_LAST_ACTIVE_SLOT` = 5 exclusive, `PetDefines.h:45`).
  - `ReactState`, `ActionBar`: `Pet.cpp:325, 376-377`.
- **Overwritten:** `Level` is overwritten by `SynchronizeLevelWithOwner` (`Pet.cpp:312, 1784-1798`).
- **legacy-only:** `Experience`.
- **Cosmetic:** `Name`, `DisplayId`.
- **Runtime state:** `Health/Mana`, `LastSaveTime`.
- **Side tables:** `pet_aura`, `pet_aura_effect`, `pet_spell`, `pet_spell_cooldown`,
  `pet_spell_charges`, `character_pet_declinedname`
  (`src/server/database/Database/Implementation/CharacterDatabase.cpp:751-761`).

## 2. Population and census

Method (`population.py`, `census.py`):
- Scope is `dummy_semantics.scope.Scope(Bundle())` with `include_class_skills=False`: 4,836 reachable
  spells. Class skill lines are reported separately as `class-skill`.
- One record per reachable DIFFICULTY_NONE effect row that has a unit-creating effect (28/55/56/153),
  a pet-lifecycle effect (102/135/168/188/199/260) or a control aura (2/6/177/378).
- Each record is joined to:
  - its SummonProperties row;
  - `EffectMiscValue_0`;
  - the pinned-TDB `creature_template` (`world-db-corpora/creature-templates.json`: 1,780 of 1,847
    requested entries; `creature_template` has 222,359 rows in total);
  - the client `Creature.csv` row. That table has 23,074 rows and is a partial cache, so it is shown
    for identity only and **never** used as an absence signal.
- Categories come from the branch in §1.3.
- Other difficulties contribute 0 population rows for the default spells.

### 2.1 Default scope (current player)

| measure | value |
|---|---|
| records / spells / distinct creature entries | **62 / 61 / 53** |
| by category | guardian 25, totem 17, ally-summon 5, controllable-guardian 4, wild-summon 3, permanent-class-pet 2 (30146 Felguard, 31687 Water Elemental), charmed 2 (111673, 205364), possessed 1 (605), possessed-own-pet 1 (321297), companion-minion 1 (1304581), no-consumer 1 (273277) |
| by C++ class | Guardian 29, Totem 17, TempSummon 8, Pet 2, Minion 1, none 5 |
| by handler | EffectSummonType 55, EffectSummonPet 2, HandleModCharm 2, HandleModPossess 1, HandleModPossessPet 1, EffectNULL 1 |
| TempSummonType | TIMED_DESPAWN 54, DEAD_DESPAWN 1 (1304581, duration −1 via `InitStats`) |
| SummonProperties slot | ANY_TOTEM 30, PET(0) 22, TOTEM(1) 2 (46585 Raise Dead, 26573 Consecration), MINIPET 1 |
| TDB `creature_template` | present 55, **absent 2**, not applicable 5 |
| build-skew spells | 2: 1304581 Soulcoil Remnant, 1308399 Answered Calling |

In the default scope, TDB absence and build skew coincide exactly: the two absent entries 269501 and
271402 belong to the two build-skew spells (`world-db-fact` + `build-skew`).

`IsPermanentPetFor` (`world-db-fact` join):
- 17252 Felguard has `type` 3 (demon) → true for warlock (class 9);
- 78116 Water Elemental has `type` 4 (elemental) → true for mage (class 8).

Both therefore get the client pet number, pet auras and the spell list (`Pet.cpp:279, 1734`;
`Player.cpp:22443`). The Water Elemental is **not** build skew.

Per-spec views: `census.json` → `default.per_spec`, or `python3 controlled_units.py census --spec ID`.
Every current spec with a controlled unit is listed there. 1263077 Uprooted Lasher and 1304581
Soulcoil Remnant are gear-reached, so they are attributed to all 40 specs.

Notable identities: see the table in §5 and `census.json` → `default.identities`.

Default-scope rows by branch:
- **ALLY default arm** (owner GUID set, not in `m_Controlled`): 26573 Consecration, 115313 Jade Serpent
  Statue, 342245 Alter Time, 1254168 Waking Nightmare, 1308399 Answered Calling.
- **WILD default arm** (no owner, demon creator = player): 1122 Summon Infernal (entry 47319, 250 ms),
  108287 Totemic Projection, 111771 Demonic Gateway.
- **Control::PET controllable guardians:** 123904 Xuen, 132578 Niuzao.
- **Title::Pet + Join controllable guardians:** 102693 Grove Guardians, 248280 Force of Nature.

### 2.2 Other scopes

| scope | records | spells | notes |
|---|---|---|---|
| script-cast (`families.json` player hooks) | 2 | 2 | 392990 Divine Image (guardian, via `spell_pri_divine_image`); 73920 Healing Rain script `SummonCreature(73400, …, properties nullptr)` → TempSummon, no owner (`scripts/Spells/spell_shaman.cpp:1395`); `structural-inference` |
| class-skill (extended, reported separately) | 1,711 | 1,711 | companion-minion 1,685, permanent-class-pet 9, guardian 4, ally-summon 3, totem 2, no-consumer 2, hunter-pet 1, puppet 1, wild-summon 1, charmed 1, charmed-convert 1, lifecycle-op 1. TDB: present 1,636, absent 64 (**42 build-skew, 22 world-db gap**: older companion spells whose creature is not authored), n/a 11 |
| `serverside_spell` summon/control effects | 97 | — | wild-summon 83, permanent-class-pet 7, unresolved 3, charmed 2, charmed-convert 1, vehicle 1; **0** referenced from player scope (hook child or `spell_linked_spell`) |

No Totem-class unit in any scope has a SpellDuration ≤ 0, so the first-update removal rule in §4.3 is
structural only.

### 2.3 Unresolved population rows (default)

| spell | row | reason | class |
|---|---|---|---|
| 273277 Summon Animal Companion | eff 0 `SUMMON_STABLED_PET` (260) | `EffectNULL` (`SpellEffects.cpp:352`) | trinity-consumer (no consumer) |
| 1304581 Soulcoil Remnant | eff 0 → 269501 | spell newer than 12.0.7; creature not in TDB → `Creature::Create` fails (`Object.cpp:1265-1269`) | build-skew |
| 1308399 Answered Calling | eff 0 → 271402 | same | build-skew |

## 3. Ownership and attribution

Identities (Trinity names):

| identity | storage | coordinate |
|---|---|---|
| owner | `UnitData::SummonedBy` | `Unit.h:1190` |
| creator | `UnitData::CreatedBy` | `Unit.h:1192` |
| summoner | `TempSummon::m_summonerGUID` | `TemporarySummon.cpp:41-42` |
| demon creator | `UnitData::DemonCreator` | `Unit.h:1202` |
| charmer | `UnitData::CharmedBy` / `m_charmer` | `Unit.h:1207-1208` |
| original caster | `Spell::m_originalCasterGUID` | `Spell.cpp:510-521` |
| aura caster | `Aura::m_casterGuid` | `SpellAuras.cpp:476-478` |

`ownership.json` → `consumers` holds 49 rows (function, coordinate, identity returned, kind,
evidence class). The collapse/distinct summary:

| consumer | identity | collapse? | coordinate |
|---|---|---|---|
| `GetCharmerOrOwner[GUID]` | charmer if charmed, else owner | collapse (1 level) | `Unit.h:1215, 1220` |
| `GetCharmerOrOwnerPlayerOrPlayerItself` | player charmer/owner, else self-as-player | collapse, **one level only** (a guardian owned by a pet → nullptr) | `Object.cpp:1628-1635` |
| `GetAffectingPlayer` | two levels | collapse | `Object.cpp:1637-1646` |
| `GetControllingPlayer` | recursive | collapse | `Unit.cpp:6201-6212` |
| `GetSpellModOwner` | owner player **only for `IsPet() \|\| IsTotem()`** (1656); Guardian/Minion/TempSummon → nullptr | collapse, pet/totem only | `Object.cpp:1648-1670` |
| `Spell::Spell` `m_caster` | the unit; its charmer/owner with `SPELL_ATTR6_ORIGINATE_FROM_CONTROLLER` | distinct unless attribute set | `Spell.cpp:475` |
| `m_originalCaster` | explicit GUID, else `m_caster`; nullptr when not in world | distinct | `Spell.cpp:510-521` |
| spell-hit proc actor + damage source | `m_originalCaster ?: m_caster` ("calculate damage/healing from him data") | distinct (original caster) | `Spell.cpp:2840-2842, 2980` |
| cast/finish proc actor | `m_originalCaster` | distinct | `Spell.cpp:3915, 4223` |
| melee/other proc actor | the acting unit; only its own auras proc | distinct | `Unit.cpp:5569-5599` |
| proc-trigger aura | `triggerCaster = aurApp->GetTarget()` (holder) | distinct | `SpellAuraEffects.cpp:6155-6172` |
| combat log | `SpellNonMeleeDamage.attacker` = unit | distinct | `Unit.cpp:303-304, 5539-5543` |
| `Unit::Kill` KILL proc | attacker always (11392); **also `GetOwner()` only if attacker `IsPet() \|\| IsTotem()`** (11383-11388) | collapse, pet/totem only | `Unit.cpp:11382-11392` |
| kill reward / achievements / tap | `GetCharmerOrOwnerPlayerOrPlayerItself` | collapse | `Unit.cpp:11251-11254, 11405`; `Creature.cpp:1370-1389` |
| threat list existence | none for Pet/Totem/Trigger and for MINION\|GUARDIAN summoned by a player | distinct | `Combat/ThreatManager.cpp:172-187` |
| threat entries on enemies | keyed by the attacking unit; no owner redirect | distinct | `ThreatManager.cpp:382-471` |
| owner combat flag | `UpdatePetCombatState` from `m_Controlled` | pet → owner | `Combat/CombatManager.cpp:421-422`; `Unit.cpp:9281-9300` |
| pet auras | owner dummy aura → pet self-cast, only `IsPermanentPetFor` | owner → pet | `Pet.cpp:1730-1762`; `Player.cpp:22179-22191`; `SpellMgr.cpp:1965-2012` |

**Where identities collapse:**
- owner spell mods, KILL-proc forwarding and summon duration mods collapse only for `Pet` and
  `Totem`;
- kill credit, loot, tap and achievements collapse to the player for every owned or charmed unit;
- added in the closeout review, not limited to Pet/Totem:
  - `SPELL_ATTR6_ORIGINATE_FROM_CONTROLLER`: the charmer/owner becomes `m_caster` for any class
    (`Spell.cpp:475`). No spell in current-player scope carries it; 24 census (§8.2) spells do,
    all vehicle, quest or covenant abilities (e.g. 62544 Thrust, 352093 Kyrian Strafing Run);
  - PvP resilience: `CanApplyResilience` is true for any unit whose owner GUID is a player, and
    `ApplyResilience` uses the player owner of an owned victim (`Unit.cpp:12416-12440`);
  - `SpellHealingBonusDone` reads `SPELL_AURA_OVERRIDE_CLASS_SCRIPTS` from `GetOwner()` for any owned
    unit (`Unit.cpp:7342-7357`). Only misc value 3736 (a legacy totem) has an effect there;
  - a Pet's combat-rating reduction is its owner's (`Unit.cpp:12548-12557`);
  - a Totem forwards its whole `SpellDamageBonusDone`/`SpellDamagePctDone`/`SpellHealingBonusDone`/
    `SpellHealingPctDone`/`SpellAbsorbBonusDone`/`SpellAbsorbPctDone` to its owner
    (`Unit.cpp:6834, 6915, 7330, 7442, 7627, 7693`), and its healing credit (`Unit.cpp:6543-6544`).

**Where identities stay distinct:**
- caster of the unit's own casts, aura caster, combat-log source, proc actor for its own actions,
  threat entry.
- Controlled units never proc the owner's auras through `ProcSkillsAndAuras`, with two exceptions:
  1. the KILL proc of a Pet or Totem;
  2. script casts that pass `OriginalCaster = owner`, which make the owner the proc actor and the
     damage-data source (`Spell.cpp:2842`).

This matches `docs/research/proc-pipeline-archaeology.md` §11 ("pets | mod-owner auras are evaluated for
pet-caused events only if the owner's aura modified the Spell"), which is not rebuilt here.

**Regression in the pinned checkout (`trinity-consumer`, confirmed via git history).**
`WorldObject::GetCharmerOrOwnerOrOwnGUID` (`Object.cpp:1597-1603`) reads
`if (!guid.IsEmpty()) guid = GetGUID(); return guid;`. Lines 1601-1602 come from `4ba5e27055c`
(2026-01-02, "Reorder Object type casting functions…", a refactor). Before that commit the body was
`if (!guid.IsEmpty()) return guid; return GetGUID();`. As a result,
`Unit::IsCharmedOwnedByPlayerOrPlayer` (`Unit.h:1216`) is:
- **false** for uncharmed players (it was true);
- **false** for player-owned pets and guardians (it was true);
- **true** for a player charmed by a creature (it was false).

Consumers:
- `Spell.cpp:2774` (PvP enabling)
- `Entities/Creature/Creature.cpp:1775, 1802` (sparring)
- `Unit.cpp:4444` (`RemoveAurasOnEvade`)
- `Entities/GameObject/GameObject.cpp:3170`
- scripts: `spell_generic.cpp:2295`, `boss_alysrazor.cpp:205`, `instance_azjol_nerub.cpp:89`

The truth table is in `ownership.json` → `regression_GetCharmerOrOwnerOrOwnGUID` and is pinned by tests.
This is a consumer defect, not a Retail semantic, and a Track A consumer must not copy it.

**Pet → owner attribution witnesses** (`dummy-corpora/families.json`, family `pet-owner-forward-cast`,
`structural-inference`):
- 8092 and 32379: `spell_pri_inescapable_torment`, `scripts/Spells/spell_priest.cpp:2596-2637`. It scans
  `m_Controlled` for entries 19668/62982/224466; the summon casts 373441 without an original caster
  (so the summon is the proc actor); `ModifyTimer` at 2630.
- 136511: Ring of Frost, `spell_mage.cpp:1795-1839`. The aura holder casts 82691 at the summon's
  position.

Other attribution witnesses (`trinity-consumer`):
- 49028 DRW (`scripts/Spells/spell_dk.cpp:452-503`) calls `DealDamage(drw, victim, int32(damage)/2)`,
  so the damage source and combat-log caster are the DRW, not the DK. The script's own comment says
  "not correct".
- 392988/392990 Divine Image: see §5.

## 4. Lifecycle

`lifecycle.json` holds ordered phases per creation path:
- `CREATE_TEMPSUMMON`: 22 phases
- `CREATE_SUMMON_PET`: 7
- `CREATE_HUNTER_PET`: 5
- `CHARM`: 6
- `UPDATE_DESPAWN`: 10
- `DEATH`: 5
- `OWNER_EVENTS`: 13

It also carries 10 replacement rules, 5 numeric records and a per-record `policy()` for the default
population.

### 4.1 Creation order (TempSummon family)

1. `EffectSummonType` (caster, private owner, duration).
2. `SummonGuardian` / `Map::SummonCreature` mask.
3. `Creature::Create`, with a fresh `GenerateLowGuid<HighGuid::Creature>` (`Object.cpp:1265`). There
   is no generation counter or reuse, and the call fails without a `creature_template`.
4. Phase inherit (1278).
5. `SetCreatedBySpell` (1281).
6. `InitStats` (1285):
   - `TempSummon::InitStats`: timer, TempSummonType, `creature_summoned_data`, **slot**, **level
     clamp**, **faction** (`TemporarySummon.cpp:188-259`).
   - `Minion::InitStats`: REACT_PASSIVE, `CreatorGUID = owner`, **faction = owner faction**,
     `owner->SetMinion` (456-466).
   - `Guardian::InitStats`: `InitStatsForLevel` [Track B], charm create spells, REACT_AGGRESSIVE
     (525-535).
7. `AddToMap` (1306).
8. `InitSummon` (1316): guardian `CharmSpellInitialize`; totem casts its spells; puppet
   `SetCharmedBy(POSSESS)`.
9. The default arm then sets the TempSummonType and owner/demon creator (2022-2028). Non-guardian arms
   set the creator (2082).

What `SetMinion(apply)` (`Unit.cpp:6246-6337`) snapshots:
- `OwnerGUID` (6264) and the `m_Controlled` insert (6266);
- `m_ControlledByPlayer` and PLAYER_CONTROLLED (6268-6272);
- the guardian-pet rule (6274-6295) and `MinionGUID` (6297-6301);
- Critter/battle pet (6303-6322);
- **PvP flags copied** (6325); a player owner re-applies later changes to every `m_Controlled` unit
  (`Player::SetPvP`, `Player.cpp:24052-24057`; FFA flag, `Player.cpp:24027-24035`), so they are not a pure snapshot;
- Pet speed (6328-6330);
- cooldown-on-event (6332-6336).

Level policy:
- **Snapshot** for TempSummons: `clamp(summoner level, ScalingLevelMin/Max + Delta)` unless
  UseCreatureLevel (`TemporarySummon.cpp:241-247`).
- **Dynamic** for Pets: `SynchronizeLevelWithOwner` on `Player::GiveLevel` / `InitStatsForLevel`
  (`Player.cpp:2260, 2497`).

### 4.2 Permanent pet (`Pet::LoadPetFromDB`, `Pet.cpp:205-448`)

1. Stable lookup; stabled slots are refused (215).
2. The current pet is not reloaded (222).
3. `isTemporarySummon` (227).
4. Tameable check (231-240).
5. Temporary-unsummon (242-246).
6. `HighGuid::Pet` GUID (251), `Create` (253).
7. Type, faction, CreatedBySpell (258-260).
8. `SetPetNumber(…, IsPermanentPetFor)` (279).
9. SUMMON_PET level = owner level (290).
10. `CreatorGUID` (307).
11. `InitStatsForLevel` [B] (309); `SynchronizeLevelWithOwner` (312).
12. React state (325); health/mana (328-341).
13. Stable index (346-372).
14. `SetMinion` (374).
15. Action bar (376-377); `AddToMap` (379).
16. Async: auras; if not temporary, spells, cooldowns, `LearnPetPassives`, `InitLevelupSpellsForLevel`,
    `CastPetAuras` (395-408).
17. Spec remap and `SetSpecialization` (413-417).
18. `PetSpellInitialize` if there is no spec (420-425).
19. Declined names; mount; template immunities (429-445).

The fresh path is `Player::SummonPet` (`Player.cpp:30462-30522`): a new `PetNumber` from
`sObjectMgr->GeneratePetNumber()`. The TODO at `Pet.cpp:1685` says the GUID counter should encode a
summon count, but it does not.

### 4.3 Duration and timers

| unit class | timer | coordinate | note |
|---|---|---|---|
| TempSummon/Guardian/Minion/Puppet | `m_timer` per TempSummonType. DEAD → UnSummon for every type. `TIMED_DESPAWN` counts down always. `TIMED_DESPAWN_OUT_OF_COMBAT` resets `m_timer = m_lifetime` while in combat. UnSummon when `m_timer <= diff`. | `TemporarySummon.cpp:73-186` | `simulate_tempsummon` mirror plus hypothesis test |
| Totem (Title Totem **and Lightwell**) | ignores TempSummonType. UnSummon when the owner or totem is dead, or when `m_duration <= diff`. | `Totem.cpp:34-51`; `Totem.h:57` | **duration ≤ 0 → removed on the first update** (signed `Milliseconds`); none of the 17 default-scope totems is affected (3,000–120,000 ms) |
| Pet | `Pet::Update` calls `Creature::Update`, never `TempSummon::Update`. `m_duration` (int32) counts down only if > 0. | `Pet.cpp:619-709`; `Pet.h:151` | both `SummonPet` callers pass 0 (`SpellEffects.cpp:2721, 4298`): spell pets are untimed |
| charm | the control aura's own duration | `SpellAuraEffects.cpp:3238-3331` | — |

Numeric record for summon duration (brief §4):

| field | value |
|---|---|
| `source_type` | `SpellDuration.Duration` int32 |
| reader | `GetDuration`: no entry → passive ? −1 : 0; −1 kept; `abs()` |
| `intermediate_type` | `Player::ApplySpellMod<int32>` computes `T((double(base) + int32 flat) * float totalmul)` (`Player.cpp:22844-22852`); `totalmul` is a binary32 product from `GetSpellModValues` (`:22627`) |
| `rounding` | `int32()` truncation toward zero |
| `units` | ms |
| reachability | any summon spell with a Duration spell mod on a Player, Pet or Totem caster (only those have a `GetSpellModOwner`) |

This is the same pattern as the op-11 41,999 vs 42,000 ms divergence. The −1 sentinel is not guarded
against Duration mods. No Track A probe was built, because the pattern is already covered by the
cooldown differential.

### 4.4 Replacement

| rule | coordinate | statement |
|---|---|---|
| slot | `TemporarySummon.cpp:224-239` | `Slot != 0` → the previous occupant of `m_SummonSlot[slot]` is UnSummoned; **Slot 0 does no bookkeeping** |
| any-totem-slot | `TemporarySummon.cpp:376-434` | Slot −1 picks, in order: (1) the same GUID; (2) a shared TotemCategory/Totem; (3) an empty slot 1..4; (4) a slot with the same entry; otherwise no slot |
| guardian-pet | `Unit.cpp:6274-6295` | `IsGuardianPet` = `IsPet() \|\| Control == PET`; the old unit is removed if either is a Pet or the entries differ; **same-entry non-Pet guardian pets coexist** |
| one-pet-per-player | `SpellEffects.cpp:2679-2712`; `Player.cpp:30481-30482`; `Pet.cpp:222-223` | — |
| minion-succession | `Unit.cpp:6379-6413`; `TemporarySummon.cpp:477-497` | — |
| num-summons | `SpellEffects.cpp:1915-1937` | 15 hard-coded MiscValueB values read the count from the effect value |
| by-entry-removal | `Unit.cpp:6429-6440`; totem removal 6357-6370 | — |
| totem-duration | `Totem.cpp:42-48, 87` | — |
| pet-not-tempsummon-timed | `Pet.cpp:619-709` | — |
| timer-mutation | `TemporarySummon.h:64-65` | script-only |

Witnesses from the default population:
- **Slot 1 (`SUMMON_SLOT_TOTEM`):** Raise Dead 46585 (SummonProperties 4973) and Consecration 26573
  (3002, ALLY/None). A recast replaces the previous unit. Consecration's unit is *not* in
  `m_Controlled` (ALLY default arm) but is still in `m_SummonSlot`, so it falls to
  `UnsummonAllTotems` on owner death.

### 4.5 Death, owner death, logout, reset

**Unit death:** `Unit::setDeathState` (`Unit.cpp:9160-9180`) → `UnsummonAllTotems` (6743-6754, all 7
slots) + `RemoveAllControlled` (6612-6637: charm auras removed, owned summons UnSummoned).

**Owner death / removal:**
- `Player::setDeathState` → `RemovePet(NOT_IN_SLOT, reagent)` (`Player.cpp:1157`).
- Logout: `WorldSession::LogoutPlayer` → `RemovePet(AS_CURRENT)` (`Server/WorldSession.cpp:619`).
- `Player::RemoveFromWorld` → `StopCastingCharm` + `UnsummonPetTemporaryIfAny` (1538-1540);
  `Unit::RemoveFromWorld` → `RemoveCharmAuras`, `UnsummonAllTotems`, `RemoveAllControlled`
  (`Unit.cpp:10273-10285`).

**Consequences:**
- An **unowned** summon survives its summoner's death and logout unless it is slotted:
  - the WILD default arm;
  - the ALLY default arm (owner GUID set, but not in `m_Controlled`);
  - a Title::Pet TempSummon.
  - The `DespawnOnSummonerDeath`/`Logout` flags are NYI.

**Other resets:**
- Far teleport and flying → temporary unsummon (`Player.cpp:1311, 1402, 27931-27997`).
- Spec change → `RemovePet`, `UnsummonAllTotems`, `RemoveAllControlled` (`Player.cpp:28801-28807`).
- `ResetTalents` → `RemovePet` (3458).
- A recast always creates a new GUID. Only a Pet keeps its `PetNumber` identity across summons.

## 5. Witnesses (population/ownership/lifecycle view)

Source: `witnesses-a.json` (18 sets, 38 spells). "props" is SummonProperties `ID: Control/Title/Slot`
followed by the flags that are set (NYI flags included, marked \*). Every creature entry below is
present in the pinned TDB. No witness spell is build skew.

| witness | spell (eff, row) | props / flags | entry | branch → class | category | owner / creator | lifecycle |
|---|---|---|---|---|---|---|---|
| Hunter Call Pet 1 | 883 (0, 366) | — | 0 (slot 0 = ActivePets[0]) | EffectSummonPet → Pet | permanent-class-pet (HUNTER_PET via stable) | owner player (SetMinion), creator player (`Pet.cpp:307`) | untimed; one-pet; identity from `character_pet` (§1.5) |
| Hunter Tame Beast | 1515 → 13481 (0, 5970) | — | target creature | periodic trigger → EffectTameCreature → Pet(HUNTER_PET) | hunter-pet | caster | `Unit.cpp:11133-11168` |
| Eyes of the Beast | 321297 (0, 808885) | — | own Pet | HandleModPossessPet | possessed-own-pet | charmer = player; owner unchanged | aura timer; out-of-range pet removed on end (`SpellAuraEffects.cpp:3289-3290`) |
| Warlock Felguard | 30146 (0, 19732) | — | 17252 (type 3) | EffectSummonPet → Pet | permanent-class-pet | owner/creator player; spell mods and KILL proc forwarded | untimed (duration −1 is irrelevant: SummonPet gets 0) |
| Warlock Imp | 688 (0, 270) | — | 416 | EffectSummonPet → Pet | permanent-class-pet (class-skill scope) | as above | as above |
| Grimoire: Felguard | 111898 (1, 124456) | 3313: 1/2/−1; Join, Help\*, DespawnWhenExpired\*, GuardianActsLikePet\* | 17252 | Join → SummonGuardian → Guardian | guardian | owner via SetMinion; no owner spell mods | TIMED 17 s; any-totem-slot |
| Wild Imp | 104317 (0, 113739) | 3210: 1/2/0; Join, UnitClutter\* | 55659 | Guardian | guardian | as guardian | TIMED; slot 0 = no replacement |
| Hand of Gul'dan | 105174 | eff 0 Dummy | — | none | **unresolved** | — | no bound script |
| Call Dreadstalkers | 104316 → 193332 (0, 283646) | 3701: 1/2/−1; Join | 98035 | Guardian | parent unresolved (Dummy); child guardian | — | TIMED 12 s |
| Vilefiend / Tyrant | 264119; 265187 (0 & 3) | 4266: 1/2/−1 (DespawnOnSummonerDeath\*); 4255: 1/36/−1 | 135816; 135002, 250289 | Guardian | guardian | as guardian | TIMED 15 s; any-totem-slot |
| DK Raise Dead | 46585 (0, 38713) | 4973: 1/2/**1**; Join, DoNotToggle\* | 26125 | Guardian | guardian | as guardian | TIMED 60 s; **slot 1 replacement** |
| Army of the Dead | 42650 → 42651 (1, 34356) | 687: 1/3/0; Join | 24207 | Title Minion → **Guardian** | parent unresolved; child guardian | as guardian | TIMED |
| Apocalypse | 275699 | eff 1 Dummy | — | none | **unresolved** | — | — |
| Dancing Rune Weapon | 49028 (0, 41134) | 3242: 1/6/0; Join | 27893 | Runeblade → Guardian; weapon display copied (`SpellEffects.cpp:5054-5064`) | guardian | as guardian; script damage is sourced from the DRW (`spell_dk.cpp:452-503`) | TIMED 8 s |
| Control Undead | 111673 (0, 124129) | — | target | HandleModCharm | charmed | charmer = aura caster; faction/PvP snapshot | aura 300 s |
| Mage Water Elemental | 31687 (0, 21314) | — | 78116 (type 4) | EffectSummonPet → Pet | permanent-class-pet | as pet | untimed |
| Mirror Image | 55342 | eff 1 Dummy | — | none | **unresolved** | — | — |
| Ring of Frost | 113724 (0, 126711) | 3018: 1/0/0; Join, UseCreatureLevel, UseSummonerFaction | 44199 | Join → Guardian | guardian | as guardian | TIMED; level from template; script dedupe (`spell_mage.cpp:1795-1839`) |
| Shadowfiend | 34433 | effs 0-2 Dummy; bound only to `spell_pri_shadow_covenant` | — | none | **unresolved** | — | — |
| Mindbender | 200174 (0, 294666) | 3254: 1/3/−1; Join | 62982 | Guardian | guardian | as guardian; `inescapable_torment` casts from the summon | TIMED 10 s; `ModifyTimer` by script |
| Divine Image | 392988 → 392990 (0, 1031612) | 5504: 1/2/0; Join | 198236 | script-cast → Guardian | guardian (structural-inference) | as guardian | TIMED 9 s |
| Mind Control / Dominate Mind | 605; 205364 | — | target | HandleModPossess (creature caster → charm); HandleModCharm | possessed; charmed | charmer = player | aura 30 s |
| Fire / Earth Elemental, Feral Spirit | 198067, 198103, 51533 | Dummy | — | none | **unresolved** | — | — |
| Totems | 5394 (0, 1925); 192058 (1, 324844) | 3402 / 3407: 1/4/−1 | 3527; 61245 | Totem | totem | owner via SetMinion; **spell mods + KILL proc forwarded** | `Totem::m_duration` (15 s / 3 s); any-totem-slot |
| Force of Nature | 205636 → 248280 (0, 465060) | 3097: 1/1/0; Join, SavePetAutocast\* | 103822 | Title Pet + Join → Guardian + CONTROLABLE | controllable-guardian | MinionGUID if empty; succession | TIMED 10 s |
| Invoke Xuen | 123904 (0, 156549) | 3262: **2**/2/−1; Join | 63508 | Control PET → Guardian + CONTROLABLE | controllable-guardian | PetGUID via guardian-pet rule | TIMED 20 s; guardian-pet replacement |
| Invoke Chi-Ji | 325197 (0, 815332) | 3733: 1/2/−1; Join | 166949 | Guardian | guardian | as guardian | TIMED 25 s |
| Guardian of Ancient Kings | 86659 | TRANSFORM + Dummy auras; script uses it only for a cooldown mod (`spell_paladin.cpp:83, 1482, 1500`) | — | none | **unresolved** | — | — |

Unresolved witnesses (10): 105174, 104316, 42650, 275699, 55342, 34433, 198067, 198103, 51533, 86659.
Their client effects are Dummy, and no `spell_script_names` binding creates a unit in the pinned
checkout (unsupported by research != inert). Two parents classify through authored TriggerSpell
children: 1515 → 13481 and 205636 → 248280.

## 6. Stat sources by unit class

### 6.1 Which Trinity class a 12.1 summon becomes

The class is decided twice. First `Spell::EffectSummonType` routes the effect (Spells/SpellEffects.cpp:1940-2076). Then the unit-mask switch of `Map::SummonCreature` allocates the object (Entities/Object/Object.cpp:1195-1240). SPELL_EFFECT_SUMMON_PET (56) always goes to `Player::SummonPet`, which does `new Pet(this, SUMMON_PET)` (Entities/Player/Player.cpp:30438-30523). Entry 0 means Call Pet, which goes through `Pet::LoadPetFromDB`. A non-player caster uses SummonProperties 67 (SpellEffects.cpp:2671-2677).

| SummonProperties | EffectSummonType route | mask → class | Guardian stat code? |
|---|---|---|---|
| Control PET (2) | SummonGuardian :2036 | GUARDIAN (Object.cpp:1200) + CONTROLABLE (TemporarySummon.cpp:517-521) | yes |
| WILD/ALLY + flag 0x200 JoinSummonerSpawnGroup | SummonGuardian :1945 | by title; default → GUARDIAN (Object.cpp:1230-1233) | yes, unless the title is Totem/Companion/Vehicle |
| WILD/ALLY, Title Guardian/Minion/Runeblade | SummonGuardian :1953 | GUARDIAN | yes |
| WILD/ALLY, **Title Pet (1), no 0x200** | SummonGuardian :1953 | **SUMMON → plain TempSummon** (Object.cpp:1194) | **no** |
| WILD/ALLY, Title Totem / Lightwell | SummonCreature (+health override for Totem, :1978-1982) | TOTEM | no |
| WILD/ALLY, Title Companion | SummonCreature + immune | MINION | no |
| WILD/ALLY, Vehicle/Mount | SummonCreature | SUMMON | no |
| WILD/ALLY, any other title | SummonCreature × numSummons; owner GUID only for ALLY (:2023-2024) | SUMMON | no |
| PUPPET (3) / VEHICLE (4,5) | SummonCreature | PUPPET / MINION | no |

`resolve_summon()` implements this table (`python3 controlled_units.py resolve-summon --spell ID`). Track A owns the population census of the rules.

### 6.2 Per unit class × stat (stats.json: 102 consumer rows, 10 recalculation triggers)

| stat | Pet (SUMMON_PET / HUNTER_PET) | Guardian (non-Pet) | Minion / Puppet / TempSummon | Totem |
|---|---|---|---|---|
| level | owner level at create/load (Player.cpp:30491, Pet.cpp:289-290), re-synced by `SynchronizeLevelWithOwner` on owner level-up (Player.cpp:2261, 2497; Pet.cpp:1784-1798); tame = max(target, owner−5) (Unit.cpp:11102) | `clamp(owner, ScalingLevelMin+Δ, ScalingLevelMax+Δ)` (TemporarySummon.cpp:241-247) unless UseCreatureLevel 0x100; **stats are computed at this level** (Guardian::InitStats → InitStatsForLevel(GetLevel()), TemporarySummon.cpp:529) | SelectLevel = ScalingLevelMax+Δ (Creature.cpp:1614-1623) computes the stats; TempSummon::InitStats then **re-labels** the level without recomputing | as Minion |
| base STR/AGI/STA/INT/SPI | `pet_levelstats` (creature 1 for hunters) else fake 22/22/25/28 (Pet.cpp:891-917) | same | ExpectedStat only (UpdateLevelDependantStats) | same |
| STA share | +CalculatePct(ownerSta,30); ghoul 26125: +ownerSta×0.3f (StatSystem.cpp:1146-1160) | **same** (Guardian code) | none | none |
| STR share | ghoul 26125 only: ×0.7f (:1146-1155) | same | none | none |
| INT share | warlock or mage owner: +30% (:1162-1169) | same | none | none |
| health | `(BASE_VALUE + BaseHealth)·BASE_PCT + TOTAL + (STA−createSTA)·mult`, then ·TOTAL_PCT, then `(uint32)` (:1253-1276). mult: 416→8.4f, 1860/17252→11, 1863→9.1f, 417→9.5f, 28017→1, else 10. BASE_VALUE = 0 (Pet::Create → InitEntry only, Pet.cpp:1680-1700) | **same formula, but BASE_VALUE = ExpectedStat health at the SelectLevel level** (Creature.cpp:1643). The health is counted twice (§7.3) | `uint32(ceil(ES·HealthModifier)·rate)` (Creature.cpp:1632-1643, 3077-3083) | Creature value, overridden by a non-zero summon effect value (SpellEffects.cpp:1978-1982) only on the Title::Totem arm; a Join totem (e.g. 458101) goes through SummonGuardian and keeps the creature value |
| armor | BASE = lvl·50 or pet_levelstats.armor (Pet.cpp:872, 898-899); +100% owner armor (Pet) / `float(CalculatePct(ownerArmor,70))` (hunter; the comment says 35%) (StatSystem.cpp:1239-1242) | BASE only, no owner share; 29264: BASE = ownerArmor·0.35f (Pet.cpp:1028) | ExpectedStat·ArmorModifier | same |
| AP | `val = STR−10` (imp) or `2·STR−20`; +bonusAP: hunter RAP·0.22f, ghoul AP·0.22f, spirit wolf AP·0.31f, other Pet max(fire,shadow SP)·0.57f (:1293-1359) | same code: the ghoul and spirit-wolf branches fire; the "other Pet" branch does not | creature_classlevelstats AP (0 if the row is missing) | same |
| spell power (`m_bonusSpellDamage`) | hunter RAP·0.1287f, ghoul AP·0.1287f, wolf AP·0.31f, other Pet max(fire,shadow)·0.15f; InitStatsForLevel entry branches (510, 1964, 15438, 19668, 31216, 27829, 28017) | same | only SPELL_AURA_MOD_DAMAGE_DONE (Unit.cpp:7083-7118) | **whole SpellDamageBonusDone forwarded to the owner** (Unit.cpp:6833-6836) |
| weapon damage | SUMMON/HUNTER: `lvl∓lvl/4` (integer division); MinDamage = `((BASE + AP/3.5f·speed + bonus + wmin)·BASE_PCT + TOTAL)·TOTAL_PCT`, clamped ≥0 (StatSystem.cpp:1361-1402; BaseEntity.h:299-303); speed forced to 2000 ms (Pet.cpp:874-876) | entry branches, or the default ExpectedStat CreatureAutoAttackDps ×1 / ×1.5 at **pet level** (Pet.cpp:1065-1079) | ExpectedStat dps at SelectLevel with DamageModifier and variance (StatSystem.cpp:1070-1117) | same |
| haste / crit / mastery | **no owner propagation anywhere** (Player::Update* 108-424, Player::UpdateRating Player.cpp:5237). Crit is 5% melee + auras; spell crit only when GetSpellModOwner (Unit.cpp:7124-7125) | no spell crit at all (a Guardian has no spell-mod owner) | same | spell mod owner = player |
| versatility / spell mods | dynamic from the owner at damage time (Unit.cpp:6926-6928, 7018-7024, 8211-8217, 6854-6858, 6895-6896; GetSpellModOwner Object.cpp:1648-1670) | **none** | none | dynamic (Totem is included in GetSpellModOwner) |
| resistances | +40% of owner resistances (:1210-1229) — legacy-only in current scope (§7.5) | template only | template | template |

## 7. Inheritance classification and arithmetic

### 7.1 Classification (inheritance.json `classification_matrix`)

- **creation-snapshot**: the Guardian level, and the InitStatsForLevel entry-branch bonuses that read `SpellBaseDamageBonusDone` (510/1964/15438/19668/31216) or owner AP (27829/28017). Script-level exception (closeout review): `npc_pet_pri_divine_image::IsSummonedBy` sets the Divine Image Guardian's bonus damage to the summoner's `SpellBaseHealingBonusDone(HOLY)` (`scripts/Pet/pet_priest.cpp:43-49`). The generic "non-Pet Guardian gets no owner spell power" row in §6.2 therefore does not hold for entry 198236.
- **explicit-recalculation**: everything in `Guardian::Update*`. It re-runs only on these triggers:
  - `Player::UpdateStats` for STA/INT/STR → `GetPet()` (StatSystem.cpp:114-119);
  - `Player::UpdateArmor` → `GetPet()` (:276-278);
  - `Player::UpdateAttackPowerAndDamage`: ranged → hunter `GetPet()`; melee → ghoul `GetPet()` and spirit-wolf `GetGuardianPet()` (:400-424). The spirit-wolf branch never fires with 12.1 data: both summons of entry 29264 use SummonProperties 1161 (Control ALLY), so the wolf never becomes the owner's guardian pet (R4-1);
  - `AuraEffect::HandleModDamageDone` on the owner → `GetGuardianPet()` (Spells/Auras/SpellAuraEffects.cpp:4708-4733). This one **does** reach a non-Pet Guardian that holds the owner's PetGUID (`SummonProperties.Control == PET`, `Unit.cpp:6230-6243, 6274-6295`): in scope Xuen 123904 and Niuzao 132578 (R4-1).
  - `Player::UpdateResistances` → `GetPet()->UpdateResistances` (StatSystem.cpp:245-247; legacy-only input, §7.5). Added in the closeout review.

  **No direct trigger** exists for item spell power (`ApplySpellPowerBonus` :151-168), `UpdateSpellDamageAndHealingBonus` (:170-196), `UpdateMaxHealth` (:314-324) or ratings. Both spell-power functions call `UpdateAttackPowerAndDamage` when the owner has aura 404 `OVERRIDE_ATTACK_POWER_BY_SP_PCT` (:163-167, :190-194), which reaches the hunter pet, ghoul pet and spirit-wolf guardian pet. The in-scope 404 carriers (137012, 137013, 1258016, 1258138: Restoration/Balance Druid, Holy Paladin, Mistweaver Monk) own none of those units, so this indirect trigger is structural only (closeout review). The `GetPet()` triggers never reach a non-Pet Guardian; the `GetGuardianPet()` trigger reaches a Control-PET guardian (above).
- **gate**: `UpdateUnitMod` returns while `!CanModifyStats()` (Unit.cpp:9682-9685). `SetCanModifyStats(true)` appears only at Creature.cpp:668 (non-guardians), Pet.cpp:326 (LoadPetFromDB), Player.cpp:18704, and StatSystem.cpp:940/953 (Player `_Apply/_RemoveAllStatBonuses`). A Map::SummonCreature guardian, or a Pet fresh from `Player::SummonPet`, therefore never re-runs `Update*` when its own stat auras change (trinity-consumer, static).
- **dynamic-owner-lookup**: versatility and spell mods for Pet/Totem; the whole spell bonus for Totem.
- **independent-pet-value**: base stats, creature ExpectedStat values, own auras.
- **unresolved**: owner haste, crit and mastery (no consumer). Also the 10 snapshot "[DNT] Periodic Scaling Summons" spells (432343-432346, 1288017-1288026; 1288017 is build-skew `added`). No Trinity consumer reads them.
- **legacy-only** code (no 12.1 input reaches it):
  - `scripts/Spells/spell_pet.cpp:89-1571`: its scaling scripts reference absent spell IDs, and `AddSC_pet_spell_scripts` (spell_pet.cpp:1628) is never called by `spell_script_loader.cpp`;
  - `spell_pet_auras` row 20895→24529 (both spells absent);
  - InitStatsForLevel entries 510, 1964, 15352, 15438, 19833, 19921, 28017. Nothing in 12.1 summons them: 12.1 summons 78116 / 103822 / 54983 / 95072 / 95061 instead.

### 7.2 Arithmetic facts pinned (evidence trinity-consumer unless noted)

| # | fact | coordinates | numeric |
|---|---|---|---|
| 1 | every intermediate is `float` (binary32); the oracle rounds after each operation (`-ffp-contract=off`) | StatSystem.cpp:1136-1402 | Imp 416 at lvl 80, owner STA 50000: MaxHealth **128128**. Exact decimal gives 128129 (8.4f = 8.3999996). **differential** |
| 2 | `(uint32)value` of a negative float is UB. It is reached transiently for the hunter pet: the first `UpdateMaxHealth` in `UpdateAllStats` runs before `UpdateStats`, so stamina = 0 − createSTA | StatSystem.cpp:1197, 1275 | x86-64 g++: `(uint32)-9.0f` = 4294967287 (probe `ucast`). The value is overwritten by the next call. **trinity-probe** |
| 3 | MinDamage/MaxDamage are clamped ≥ 0 | BaseEntity.h:299-303 | `std::max(value, T(0))` |
| 4 | `ModDamageDoneNeg` is stored as a **negative** sum, and the Pet branch computes `Pos − Neg` → **a negative owner spell-power aura raises the pet bonus** | StatSystem.cpp:180-188, 1330-1331; SpellAuraEffects.cpp:4728 | Pos 500, Neg −300 → fire 800 → bonus int32(800·0.15f) = 120. **differential** |
| 5 | InitStatsForLevel SUMMON_PET uses **Pos only**. UpdateAllStats then overwrites it with Pos−Neg | Pet.cpp:929-935 | |
| 6 | the power BASE_PCT is written at the index of the **pre-gate** `CalculateDisplayPowerType()`. `SetPowerType` then drops powers without `IsUsedByNPCs` (0x80); rage (Flags 8192) never displays, so a warrior-class unit keeps mana at BASE_PCT 1.0 | Pet.cpp:887,896,920; Creature.cpp:1647-1649; Unit.cpp:5705-5708; DBCEnums.h:2302 | **differential** (spirit wolf, ghoul cases) |
| 7 | `GetCreatePowerValue`: mana → BaseMana; otherwise PowerType.MaxBasePower, 0 unless IsUsedByNPCs | StatSystem.cpp:90-98, 964-971 | |
| 8 | pet_levelstats fields are uint16 (armor too); the loader gap-fills `health==0` levels from the previous level and ABORTs without level 1 | ObjectMgr.h:675-681; ObjectMgr.cpp:3681-3779 | entry 1 levels 81-85 are all 1s, so a **hunter pet at 81-90 gets CreateHealth 1, armor BASE 1, stats 1** (world-db-fact) |
| 9 | `GetPetLevelInfo(id, 0)` indexes `[-1]` (UB) | ObjectMgr.cpp:3777 | reached only if level 0 **and** the entry has rows; not reached by any witness |
| 10 | ExpectedStat: `(Lvl, ExpansionID)`, fallback `(Lvl, −2)`, else 1.0f; ContentTuningXExpected mods multiplied in ID order (M+ season gate with season 0); class mod rows 4/2/3/1 for warrior/paladin/rogue/mage | DB2Stores.cpp:1287-1319, 2451-2580 | lvl 90 CreatureHealth 141482.015625, AutoAttackDps 6855.703125 (ID 475). Key collision only at (73, 6). db2-fact |
| 11 | ContentTuning levels: MaxLevelType 2 → +GetMaxLevelForExpansion(11) = 90; an empty redirect span means no ConditionalContentTuning redirect | DB2Stores.cpp:2173-2239; SharedDefines.h:109-139 | CT 482 → 1..90. 12.1 column names differ (`MinLevelSquish`…) but the 19 columns match `ContentTuningLoadInfo` (DB2LoadInfo.h:1360-1383) positionally: **structural-inference** |
| 12 | no difficulty row → static DefaultCreatureDifficulty (CT 0, modifiers 1) | Creature.cpp:252-289 | Difficulty.db2 has no ID 0 |
| 13 | ScalingLevelMin/Max default to 0 (`T _value = {}`); CT 0 (no row) → SelectLevel level 1, TempSummon `clamp(owner,0,0)` = **level 0** | UpdateField.h:921; Creature.cpp:1617-1620, 3059-3075; TemporarySummon.cpp:243-246 | **Mirror Image 31216 and Grove Guardian treant 54983 have ContentTuningID 0 in their DifficultyID 0 row** (world-db-fact) → level 0 on that row. The row is chosen by map difficulty (`Creature.cpp:511`, fallback chain `Creature.cpp:252-261`). 54983 has only row 0 → level 0 everywhere. 31216 also has rows 1 and 2 with CT 482, so dungeon difficulties 1, 2, 8→23→2, 23→2 and 24→2, plus 205 and 216 (synthesis closeout review, R4-2), give level `clamp(owner, 1+Δ, 90+Δ)`. Open world (0) and raid/delve difficulties whose chain ends at 0 (14, 15, 16→233→15→14→0, 17, 208) give level 0 (closeout review; `Difficulty.csv` FallbackDifficultyID) |
| 14 | Guardian ctor sets UNIT_MASK_GUARDIAN before `Create` | TemporarySummon.cpp:513-523 | makes #15 reachable |

### 7.3 Guardian health is counted twice (differential)

The chain is Map::SummonCreature (Object.cpp:1265) → `Creature::Create` → `CreateFromProto` (Creature.cpp:1151) → `UpdateEntry(updateLevel=true)` (:647) → `SelectLevel` → `UpdateLevelDependantStats`. That last call does `SetStatFlatModifier(UNIT_MOD_HEALTH, BASE_VALUE, (float)uint32(ceil(ES·HealthModifier)·rate))` (:1635-1643) at the SelectLevel level. The `!IsGuardian()` block (:657-670) is skipped, and InitStatsForLevel never resets UNIT_MOD_HEALTH. `Guardian::UpdateMaxHealth` therefore returns `BASE_VALUE + BaseHealth + …`. The same run also leaves power BASE_PCT = ManaModifier (Creature.cpp:1648) unless pet_levelstats resets it to 1.0.

The probe confirms this. `tools/tc_pet_probe` runs the verbatim `UpdateLevelDependantStats` + `InitStatsForLevel` and matches the Python oracle. For ghoul 26125 at lvl 90, the Guardian's MaxHealth minus the Pet's equals 141483, the BASE_VALUE. World example: Greater Fire Elemental 95061 (`HealthModifier` 73.425) → Guardian MaxHealth **16,681,309** under the standard owner (inheritance.json).

### 7.4 Oracle (`controlled_units.py inherit --spell ID …`)

The oracle is `stats.GuardianOracle`, a statement-order transcription of `InitStatsForLevel` + `Guardian::Update*`. Engine inputs come from `engine_inputs_from_sources`: ExpectedStat and ContentTuning from DB2; creature_template, `_difficulty` and creature_classlevelstats from `pet-witness-templates.json`.

It fails closed for each of these, with a reason:
- a non-Guardian class;
- a nondeterministic `irand` level delta;
- a missing owner `SpellBaseDamageBonusDone` for an entry branch;
- a missing world row;
- a Guardian entry branch that keeps level-dependent weapon damage it cannot derive;
- level-0 UB.

Not modelled: auras applied after creation, family passives and `spell_pet_auras` amounts (spells.json), and Rate.Creature.* ≠ 1.

Standard-owner runs (lvl 90; STR/AGI/INT 1000, STA 20000; armor 5000; AP = RAP = 10000; SP 10000 in all schools), from inheritance.json. Every run uses the creature's DifficultyID 0 row (open-world map); a dungeon map can select another row (§7.2 #13):

| witness (spell → entry) | class | lvl | MaxHealth | AP | bonus SP | armor base+bonus | MinDmg |
|---|---|---|---|---|---|---|---|
| Call Pet 883 → tamed (stats row 1) | Pet/HUNTER_PET | 90 | 60,001 | 2,182 | 1,287 | 1+3,500 | 1,314.86 |
| Summon Imp 688 → 416 | Pet/SUMMON_PET | 90 | 52,946 | 5,886 | 1,500 | 3,574+5,000 | 3,431.43 |
| Grimoire: Imp 111859 → 416 | Guardian | 90 | 194,430 | 186 | 0 | 3,574+0 | 6,961.99 |
| Felguard 30146 → 17252 | Pet/SUMMON_PET | 90 | 73,497 | 6,058 | 1,500 | 13,219+5,000 | 3,529.71 |
| Grimoire: Felguard 111898 → 17252 | Guardian | 90 | 214,980 | 358 | 0 | 13,219+0 | 7,060.27 |
| Wild Imp 104317 → 55659 | Guardian | 90 | 76,977 | 24 | 0 | 4,500+0 | 6,869.42 |
| Raise Dead pet 52150 → 26125 | Pet/SUMMON_PET | 90 | 64,551 | 4,242 | 1,287 | 4,513+5,000 | 2,492.00 |
| Raise Dead guardian 46585 → 26125 | Guardian | 90 | 206,034 | 4,242 | 1,287 | 4,513+0 | 9,279.70 |
| Army ghoul 42651 → 24207 | Guardian | 90 | 145,247 | 24 | 0 | 4,500+0 | 6,869.42 |
| Ebon Gargoyle 49206 → 27829 | Guardian | 90 | 204,211 | 24 | 5,000 | 4,500+0 | 81.71 |
| Mirror Image 321686 → 31216 | Guardian | **0** | 60,047 | 24 | 3,300 | 0+0 | 18.11 |
| Water Elemental 31687 → 78116 | Pet (MAX_PET_TYPE) | 90 | 144,889 | 5,724 | 1,500 | 4,500+5,000 | 9,612.38 |
| Shadowfiend 1280172 → 19668 | Guardian | 90 | 303,725 | 462 | 0 | 4,500+0 | 3,534.00 |
| Mindbender 123040 → 62982 | Guardian | 90 | 541,039 | 24 | 0 | 4,500+0 | 6,869.42 |
| Fire Elemental 188592 → 95061 | Guardian | 90 | 16,681,309 | 24 | 0 | 4,500+0 | 6,355.24 |
| Fire Elemental 372335 → 95061 | Pet/SUMMON_PET (shaman) | 90 | 8,370,654 | 5,724 | 1,500 | 4,500+5,000 | 3,338.86 |
| Earth Elemental 188616 → 95072 | Guardian | 90 | 427,853 | 24 | 0 | 4,500+0 | 6,869.42 |
| Feral Spirit 228562 → 29264 | Guardian | 90 | 264,183 | 3,124 | 3,100 | 1,750+0 | 1,608.86 |
| Force of Nature 248280 → 103822 | Guardian | 90 | 1,474,821 | 24 | 0 | 4,500+0 | 6,869.42 |
| Grove Guardians 102693 → 54983 | Guardian (CONTROLABLE) | **0** | 60,079 | 24 | 0 | 0+0 | 14.71 |
| Xuen 123904 → 63508 | Guardian (Control PET) | 90 | 342,965 | 24 | 0 | 4,500+0 | 6,869.42 |

Notes on the table:
- **Wild Imp etc.:** AP 24 = 2·22−20. The fake create STR is used and a non-Pet Guardian gets no owner AP.
- **Mage owner:** `InitStatsForLevel` logs "Unknown type pet" and leaves `petType` = MAX_PET_TYPE (Pet.cpp:863). The default ExpectedStat branch runs, but the unit is still `IsPet`, so it gets the fire/shadow AP branch, 100% owner armor and 40% resistances. `IsPermanentPetFor` uses `m_petType` (SUMMON_PET) and returns true for MAGE+ELEMENTAL (Pet.cpp:1657-1678).

### 7.5 Legacy-only / reachability

- The Pet 40% school-resistance share is legacy-only in current scope. No current-player-scope spell carries aura 22/83/101/142 with a non-physical school bit (Scope, `include_class_skills=False`). Reopen with an item or in-scope aura granting fire..arcane resistance.
- Hardcoded pet spell IDs absent from 12.1 SpellName: 27, including 34903/34904/61017 hunter scaling, 35696 Demonic Knowledge (Pet.cpp:1757) and 24529/20895 (db2-fact).
- The water-elemental branches for entry 510 (StatSystem.cpp:1339-1345; Pet.cpp:958-962) are unreachable for any 12.1 summon: 31687 summons 78116, and a Pet 510 would take the IsPet branch first.

## 8. Spell acquisition

### 8.1 Paths (spells.json `acquisition_paths`; every path re-read at 7f3d43b)

| path | applies to | rule | coordinates |
|---|---|---|---|
| levelup | Pet | family SkillLine[0..1] SLA, AcquireMethod 2, SpellInfo, SpellLevel≠0; learned at ≤ pet level, unlearned above | SpellMgr.cpp:2152-2199; Pet.cpp:1485-1501 |
| family-passive | Pet | SLA spell with no DIFFICULTY_NONE SpellLevels row of SpellLevel≠0, IsPassive, AcquireMethod 2 → PETSPELL_FAMILY, cast on self | SpellMgr.cpp:5557-5588; Pet.cpp:1709-1728 |
| default | Pet | creature_template_spell idx 0..3 of any entry summoned by a DIFFICULTY_NONE 28/56 effect, minus the levelup set | SpellMgr.cpp:2201-2294; Pet.cpp:1503-1519 |
| spec | hunter Pet | SpecializationSpells of pet spec 74/79/81; override 535/536/537 when the owner has aura 451 | Pet.cpp:1850-1928, 413-417; SpellAuraEffects.cpp:6325-6346 |
| charm | CONTROLABLE Guardian with player owner | m_spells[0..3]; ATTR5 0x4000 skipped; passive → cast; autocast ENABLED only if autocastable, not ATTR9 0x20000, and NeedsExplicitUnitTarget | CharmInfo.cpp:113-164; TemporarySummon.cpp:525-535 |
| totem | Totem | m_spells[0] if passive totem, m_spells[1] always | Totem.cpp:78-96 |
| template-ai | any creature | m_spells[0..7] (Creature.cpp:575); the generic CombatAI family reads [0] | CombatAI.cpp:198-230; PassiveAI.cpp:113-123 |
| script | any | SPELL_* constant in `scripts/Pet/*.cpp` (structural index) | dummy-corpora/script-index.json |
| pet-aura | Pet with IsPermanentPetFor | spell_pet_auras → AddPetAura → CastPetAuras/CastPetAura | SpellMgr.cpp:1965-2029; Pet.cpp:1730-1762; Player.cpp:22179-22191 |
| learn-pet-spell | Pet | effect 57 → learnSpell(TriggerSpell) | SpellEffects.cpp:2746-2770 |

`Pet::addSpell` ACT_DECIDE (Pet.cpp:1394-1402):
- not autocastable → PASSIVE; enabled by default → ENABLED; otherwise DISABLED.
- `IsAutocastable` is `!passive && !ATTR1_NO_AUTOCAST_AI(0x20000)`, and `IsAutocastEnabledByDefault` is `!ATTR9_AUTOCAST_OFF_BY_DEFAULT` (SpellInfo.cpp:1758-1770).

### 8.2 Census (spells.json `ability_census`)

**Population:** pet-family SLA spells, plus creature_template_spell of every entry summoned by a 12.1 effect 28/56, plus ClassID-0 spec spells. **837 spells.**

| status | count |
|---|---|
| has an applicable Trinity path | 806 |
| no SpellInfo (no SpellName row) | 14 |
| unresolved (no applicable path) | 17 |
| build-skew | 0 |

**Paths used:** template-ai 657, levelup 139, totem 25, charm 20, family-passive 12, default 10, spec 7, script 3, learn-pet-spell 1. A path counts only if the entry is summoned as the class that runs it (`entry_unit_classes`).

**Family SLA rows (85 families × 78 skill lines, 1,175 family-row pairs, 176 distinct spells):**

| outcome | rows |
|---|---|
| levelup | 364 |
| family-passive | 332 |
| no SpellInfo | 334 (e.g. 115043, 142689, 178839, 178840) |
| AcquireMethod 0 | 128 |
| SpellLevel 0 and not passive → no path | 17 |

**Unresolved (17):**
- 19577 and 24394 Intimidation. These are AcquireMethod-0 player commands, not pet spells.
- 19647 Spell Lock
- 25026 Activate MG Turret
- 25027 Flamethrower
- 30153 Pursuit
- **89751 Felstorm.** SpellLevel 0, not passive, and 17252 has no creature_template_spell.
- 117588 Meteor
- 118297 Immolate
- 118345 Pulverize
- 134477 Threatening Presence
- 135029 Water Jet
- 159953 Feast
- 171011 and 171012 Burning Presence
- 267922 Eternal Guardian
- 1247078 Shadow Jump

The **template-ai** count (657) is structural; 599 of the 806 path-bearing spells have no other path (closeout review). Whether the selected AI ever casts a template spell depends on AIName, ScriptName, or the generic AI's use of slot 0; smart_scripts was not extracted.

### 8.3 Links from the player cast to the summon (witnesses-b.json `link`)

| cast → summon | Trinity edge |
|---|---|
| 205636 Force of Nature → 248280 | authored TRIGGER_SPELL (db2-fact) |
| 688, 30146, 31687, 123040, 883, 5394 | direct summon effect |
| **46584 Raise Dead → 52150** | only through `spell_dk_raise_dead` (scripts/Spells/spell_dk.cpp:1198-1215). Its EFFECT_0 DUMMY hook is **inert**: 46584 eff0 is `64 TRIGGER_SPELL → 1242866` in both 12.1 and 12.0.7 (63326dd), and 1242866 is an APPLY_AURA(DUMMY) with no summon. At runtime the hook is filtered by `EffectHook::IsEffectAffected` (Spell.cpp:8895) through `SpellScript::EffectBase::CheckEffect` (SpellScript.cpp:179-189: effect type must equal `SPELL_EFFECT_DUMMY`); SpellScript.cpp:292 only logs the mismatch at load. dummy-corpora/bindings.json (`executes: false`) records the same. **The player's Raise Dead summons nothing in Trinity.** |
| 105174 → 104317, 42650 → 42651, 55342 → 321686, 34433 → 1280172, 198067 → 188592, 198103 → 188616, 51533 → 228562 | **no Trinity edge**: no spell_linked_spell, no binding, no script constant, no authored trigger. 34433/123040 bind only to `spell_pri_shadow_covenant`. |
| 86659 Guardian of Ancient Kings | no 12.1 DIFFICULTY_NONE effect 28/56 summons 46499. The cast has only auras 56/4/87, and scripts reference 86659 only as a cooldown target (spell_paladin.cpp:1482-1500) → **unresolved** |

## 9. Availability, targeting, AI and execution

- **AI selection:**
  - `IsPet()` → PetAI unconditionally (CreatureAISelector.cpp:88-91).
  - Otherwise ScriptName, then AIName, then the highest permit (ScriptMgr::GetCreatureAI :93-102; SelectFactory :62-84 — AIName registry item :70-73, else max permit :76-79).
  - `PetAI::Permissible` gives PROACTIVE to CONTROLABLE_GUARDIAN units with a player owner (PetAI.cpp:35-45), so a controllable Guardian without a script gets PetAI.
  - Witness outcomes:
    - PetAI: Pets and the treants (54983, 103822).
    - ScriptName: `npc_pet_mage_mirror_image` (31216), `npc_pet_pri_shadowfiend_mindbender` (19668, 62982) and `npc_pet_dk_risen_ghoul` (26125 as a Guardian).
    - A generic highest-permit AI, not resolved here: Wild Imp, Army ghoul, elementals, spirit wolf.
- **Autocast:** `PetAI::UpdateAI` (PetAI.cpp:55-222) collects candidates from `GetPetAutoSpellOnPos`. For a creature these are charm slots 0..3 in ACT_ENABLED (Creature.cpp:3374-3385); a Pet uses `m_autospells`. Positive spells go to self/owner/allies out of combat, offensive spells to the victim, JUMP_DEST only to enemies. **One random candidate is cast per update** (`urand`, PetAI.cpp:199).
- **Action bar and commands:** PET_SPELL slots 3..6 of 10 (CharmInfo.h:80-82); ActiveStates 0x01/0x81/0xC1/0x07/0x06 and React/Command enums (UnitDefines.h:529-565); `HandlePetActionHelper` / `HandlePetCastSpellOpcode` (Handlers/PetHandler.cpp:141, 678).
- **Execution differences:** see §6.2 and spells.json `execution_differences`. The Guardian bonus is added to DoneAdvertisedBenefit (Unit.cpp:6845-6848). A non-Pet creature is scaled by Rate.Creature.*.SpellDamage (Unit.cpp:6922-6924). Victim-side aura 339 matches the summoner GUID of any TempSummon (Unit.cpp:2927-2933, 7261-7267).
- **Pet-related aura types** (`controlled_units.py aura-types --pet`; the 12.1 count is distinct DIFFICULTY_NONE spells; the scope count is distinct spells in current-player scope):

| aura | handler (SpellAuraEffects.cpp row) | 12.1 | scope | in-scope witnesses |
|---|---|---|---|---|
| 2 MOD_POSSESS | HandleModPossess (:74) | 60 | 1 | 605 Mind Control |
| 6 MOD_CHARM | HandleModCharm (:78) | 118 | 2 | 111673, 205364 |
| 146 ALLOW_TAME_PET_TYPE | NoImmediate (:218) | 1 | 1 | 53270 Exotic Beasts |
| **157 PET_DAMAGE_MULTI** | **HandleNULL** (:229) | 26 | 1 | 400533 Wild Synthesis |
| 177 AOE_CHARM | HandleCharmConvert (:249) | 180 | 0 | |
| 258 OVERRIDE_SUMMONED_OBJECT | NoImmediate (:330) | 25 | 0 | |
| 339 MOD_CRIT_CHANCE_FOR_CASTER_PET | NoImmediate (:411) → Unit.cpp:2929, 7263 | 5 | 0 | |
| 356 PROVIDE_TOTEM_CATEGORY | NoImmediate (:428) | 1 | 0 | |
| 378 MOD_POSSESS_PET | HandleModPossessPet (:450) | 1 | 1 | 321297 Eyes of the Beast |
| **381 MOD_DAMAGE_TAKEN_FROM_CASTER_PET** | **HandleNULL** (:453) | 62 | 7 | 32390, 48181 Haunt, 55078 Blood Plague, 191587, 196414, 1240996, 1241521 |
| **382 MOD_PET_STAT_PCT** | **HandleNULL** (:454) | 14 | 6 | 378241, 386617 Demonic Fortitude, 429215, 429227, 429581, 1265971 |
| 420 MOD_BATTLE_PET_XP_PCT | NoImmediate (:492) | 8 | 0 | |
| 428 LINKED_SUMMON | HandleLinkedSummon (:500) | 2,473 | 0 | |
| **429 MOD_SUMMON_DAMAGE** | **HandleNULL** (:501) | **393** | **63** | 76657 Mastery: Master of Beasts, 77219 Master Demonologist, 77515 Dreadblade, 137006-137011 spec auras |
| 451 OVERRIDE_PET_SPECS | HandleOverridePetSpecs (:523) | 2 | 0 | |
| 493 (unnamed; used by 267116) | HandleNULL (:565) | – | – | |

The in-scope counts for 429 and 382 are **63 spells / 65 effect rows** and **6 spells / 16 rows**. The rows agree with dummy-server-semantics-archaeology.md §"Player-scope unimplemented aura types" (65, 16), which counts effect rows.

## 10. Probe and differential results

`tools/tc_pet_probe/` (`make -C tools/tc_pet_probe`; `.gitignore`: `probe`, `*.inc`). `extract.py` pulls 33 bodies/blocks verbatim:
- StatSystem.cpp: the ENTRY_* defines, `Guardian::Update*`, `SetBonusDamage`, `Unit::UpdateAllResistances`, `Unit/Creature::GetCreatePowerValue`, `Creature::GetPowerIndex`.
- Pet.cpp: `InitStatsForLevel`.
- Unit.cpp: the modifier getters/setters, `UpdateUnitMod`, `GetTotalStatValue`, `GetTotalAuraModValue`, `GetTotalAttackPowerValue`, `GetWeaponDamageRange`, `GetBaseAttackTime`.
- Creature.cpp: `UpdateLevelDependantStats`, `GetMaxHealthByLevel`, `GetBaseDamageForLevel`, `GetBaseArmorForLevel`.
- Enums from SharedDefines/Unit.h/PetDefines/DBCEnums, `PetLevelInfo`, and `CalculatePct`.

The probe is **bounded**. Update-field plumbing, SetMaxHealth/SetMaxPower/SetPowerType semantics and UpdateStatBuffMod are stubbed; the cut points are documented in probe.cpp. ExpectedStat, pet_levelstats and base-mana lookups come from the driver. Built with g++ 13.3.0, `-std=c++20 -O0 -ffp-contract=off`.

`tests/test_cu_differential.py` (skips if the binary is absent) has 19 tests, all passing:
- **14 fixed cases:** imp pet binary32; hunter pet placeholder lvl 90; ghoul pet / ghoul Guardian; felguard Guardian; spirit wolf (rage-gated power, 1500 ms); wild imp default branch; mirror image level 0; mage water elemental (MAX_PET_TYPE); shadowfiend entry branch; negative-SP sign quirk; gargoyle; legacy treant 1964; legacy bloodworm 28017.
- **4 pinned claims:** binary32 discrimination (128128 vs 128129); the health double count; negative owner SP increasing the pet bonus; the platform result of `(uint32)` of a negative float.
- **Hypothesis:** 300 random cases over 19 entries × 3 classes × 7 owner classes × levels 1-90 × random owner stats, modifiers and ExpectedStat values.

Every compared field matches **bit-for-bit** (%a floats): stats, MaxHealth, MaxPower, armor, AP, AP multiplier, bonus SP, min/max damage, weapon range, create health/mana and attack time.

The world-db slice was also checked differentially. `world-b` filters rows at insert and skips 19,331 UPDATEs that provably cannot touch kept rows. Its 33,790 `creature_template_difficulty` rows are identical to the lead's full 320,629-row extract (0 key diffs, 0 value diffs).

## 11. Witnesses (stats/spells view)

witnesses-b.json has 16 witnesses, each with cast→summon link, resolution, standard-owner oracle, template spells with paths, AI and entry-switch flags.

| witness | Trinity class / entry | stats view | spells view |
|---|---|---|---|
| Hunter pet (883; tame 1515 → 13481) | Pet/HUNTER_PET, stats row 1 | lvl 81-90 placeholder base (hp 1, armor 1); RAP·0.22 AP; 70% armor | family 1 (Wolf): levelup 2649 Growl (1), 17253 Bite (10), 61684 Dash (10), 263840 (14); passives 34902, 19581, 8875, 65220, 88680; 883 not in default scope (class skill line) |
| Warlock Imp 688 | Pet 416 | mult 8.4, AP = STR−10 | 3110 Firebolt: default + levelup |
| Felguard 30146 | Pet 17252 (PET_FELGUARD 30146 is a spell ID, not an entry) | mult 11 | levelup 30213 (1), 32233 Avoidance (1), 30151 (14), 89766 (17); passive 117225; **Felstorm 89751 and 134477: no path** |
| Wild Imp 104317 (from 105174) | Guardian 55659 | default ExpectedStat branch; HealthModifier 0.06 | none; no Trinity edge from Hand of Gul'dan |
| DK Raise Dead 46584 | 52150 → Pet 26125; 46585 → Guardian 26125 | ghoul STA 0.3 / STR 0.7 / AP 0.22 in both classes; Guardian health double count | 47468/47481/47482/47484 default + levelup; **46584 → 52150 script hook inert** |
| DK Army ghoul 42651 | Guardian 24207 | default branch | none; no edge from 42650 |
| Mage Mirror Image 321686 | Guardian 31216 | **level 0** (DifficultyID 0 row, §7.2 #13); bonus = int32(frost SBDB·0.33f) | 59638 Frostbolt: script + template-ai; no edge from 55342 |
| Mage Water Elemental 31687 | Pet 78116, MAX_PET_TYPE | default branch + IsPet owner shares | no template spells |
| Priest Shadowfiend 1280172 / Mindbender 123040 | Guardian 19668 / 62982 | 19668 entry branch: weapon `lvl·3..lvl·5 + int32(shadow·0.3f)` | 63619 template-ai; forward-cast hook `spell_pri_inescapable_torment` (spell_priest.cpp:2593-2637) |
| Shaman Fire / Earth Elemental | Guardian 95061 / 95072 (and Pet 95061 via 372335) | default branch; 95061 HealthModifier 73.425 | no template spells; no edge from 198067 / 198103 |
| Shaman Feral Spirit 228562 | Guardian 29264 | wolf AP 0.31, armor BASE = 0.35·owner, STA BASE = 0.3·owner, 1500 ms | 58875 / 58867 template-ai; no edge from 51533 |
| Shaman Healing Stream Totem 5394 | Totem 3527 | Creature stats, health = effect value | 5672 totem path |
| Druid treants 248280 / 102693 | Guardian 103822 / 54983 (CONTROLABLE, PetAI) | 54983 **level 0** | none |
| Paladin GoAK 86659 | – | unresolved | unresolved |
| Interaction: pet-only damage multiplier | aura 429 | HandleNULL: 63 in-scope spells inert | – |
| Interaction: pet-owner-forward-cast | 3 hook rows (8092, 32379, 136511) | – | trinity-consumer |

## 12. Reproducibility

### Part A

Run from `scripts/research/` (stdlib only):

```
python3 controlled_units.py creature-ids --keep-file "$TMPDIR/cu-ids.json"   # ~11 s; writes world-db-corpora/creature-templates.ids.json
K="$TMPDIR/cu-ids.json"
python3 tools/tdb_world_extract.py --tdb ../../../../bag/tdb/TDB_full_world_1200.26021_2026_02_06.sql \
  --tables creature_template,creature_template_addon,creature_summoned_data,creature_template_spell,creature_summon_groups,creature_classlevelstats,creature_template_model \
  --project creature_template=entry,name,subname,RequiredExpansion,faction,npcflag,speed_walk,speed_run,scale,Classification,dmgschool,BaseAttackTime,RangeAttackTime,BaseVariance,RangeVariance,unit_class,unit_flags,unit_flags2,unit_flags3,family,type,VehicleId,AIName,MovementType,RegenHealth,CreatureImmunitiesId,flags_extra,ScriptName,VerifiedBuild \
  --keep creature_template.entry=@$K --keep creature_template_addon.entry=@$K --keep creature_template_spell.CreatureID=@$K \
  --keep creature_summon_groups.summonerId=@$K --keep creature_template_model.CreatureID=@$K \
  --out ../../docs/research/world-db-corpora/creature-templates.json      # ~31 s with the indexed extractor (was 663 s)
python3 controlled_units.py all-a                                          # ~11 s, deterministic (verified twice)
python3 controlled_units.py classify --spell 46585 | ownership --spell 123904 | lifecycle --spell 5394 | census --spec 266
uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/test_cu_population.py tests/test_cu_ownership.py tests/test_cu_lifecycle.py -q -p no:cacheprovider
```

The `--keep` id set is committed as `world-db-corpora/creature-templates.ids.json` (1,847 entries, provenance first;
`controlled_units.py creature-ids`, deterministic). It is the union of:
- `EffectMiscValue_0` of DIFFICULTY_NONE effects 28/56/153 in the extended scope (1,750) and in `serverside_spell`;
- Trinity's hard-coded entries (`TemporarySummon.h:26-38`, `Pet.cpp:956-1080`, `StatSystem.cpp:1130-1134`, script
  `NPC_*` constants; each listed with its coordinate in the corpus);
- Track B's 40 witness entries.

`test_cu_population.py::test_creature_id_list_is_committed_reproducible_and_covers_every_census_and_witness_entry`
regenerates the set, compares it with the committed list, and checks that every census (default, class-skill,
script-cast, serverside) and Track B witness entry is in it. Closeout review: the rerun with this id list reproduced
`creature-templates.json` byte for byte (sha256 below).

`creature_template_difficulty` is deliberately excluded (see Unresolved).

| corpus | bytes | sha256 |
|---|---|---|
| controlled-unit-corpora/vocabulary.json | 50,951 | `70c583250f09c5cbcea945ce31fc8a43a6b7eb3ae612843a2d372c9987c4fbe1` |
| controlled-unit-corpora/population.json | 1,503,421 | `e5ec144e196bc1a26eca8012ce017a8a1b19f2dda4fe0d832948ab142077ba02` |
| controlled-unit-corpora/census.json | 120,810 | `b74c2620a962b791b9969fa84fb68826414e038b5fdc82bf17fb3cda8b734983` |
| controlled-unit-corpora/ownership.json | 225,118 | `dd705058a3c793376441f142fbcf9dfd2f14a1af1d8d4ba2d2e80f45264ae99b` |
| controlled-unit-corpora/lifecycle.json | 112,997 | `48760917da9d6fc24e10d4c1cd966f00122281cf6a9b30123a51e736c604c1e8` |
| controlled-unit-corpora/witnesses-a.json | 241,293 | `6daea5ac9e9f94658de8ddfc1575f2a4ac28a5b20323d6fa2b4673d4f813d9fe` |
| world-db-corpora/creature-templates.json | 261,977 | `8bd33c55e17c27f3304d506d1be59b4c2a8807b75b71df9422fb6277c052f8fb` |
| world-db-corpora/creature-templates.ids.json | 40,010 | `8044ec46b325b88cf6b907b0de19bec33de659864c9e9724f5cb2daede70be3b` |

Rows emitted in `creature-templates.json` (emitted / total):

| table | emitted | total |
|---|---|---|
| creature_template | 1,780 | 222,359 |
| creature_template_addon | 104 | 23,400 |
| creature_template_model | 1,935 | 366,584 |
| creature_template_spell | 47 | 9,586 |
| creature_summoned_data | 54 | 54 |
| creature_classlevelstats | 460 | 460 |
| creature_summon_groups | 0 | 1,018 |

The replay left 3 statements unparsed.

### Part B

```
cd scripts/research
python3 controlled_units.py world-b          # ~82 s; needs ../../../../bag/tdb/TDB_full_world_1200.26021_2026_02_06.sql
python3 controlled_units.py all-b            # stats, inheritance, spells, witnesses-b (~19 s, deterministic)
python3 controlled_units.py inherit --spell 688 --owner-level 80 --owner-stamina 50000 --owner-int 40000 \
    --owner-armor 3000 --owner-sp-pos 0 40000 40000 40000 40000 40000 40000
python3 controlled_units.py spells --spell 89751
python3 controlled_units.py aura-types --pet
make -C tools/tc_pet_probe
uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest \
    tests/test_cu_stats.py tests/test_cu_spells.py tests/test_cu_differential.py -q -p no:cacheprovider
# 80 passed in 49.07s
```

| corpus | sha256 |
|---|---|
| controlled-unit-corpora/stats.json | 3912ce3a713f7bc76ee960daf077d1ca3bb873f5ad6c3398591b88f8e0400d14 |
| controlled-unit-corpora/inheritance.json | 57f2526a89628581182dfcfdf45ed380e72b48dd9667a15d015abe0788c2bd03 |
| controlled-unit-corpora/spells.json | 61b823849d6b1bca8ed5918b3ecff4cf8f14d2bb7ea75613d726b3d5e0d8ff13 |
| controlled-unit-corpora/witnesses-b.json | 7fe34a388eac82347715566e6422a94f9d039c122613b8aaf5190444382df668 |
| world-db-corpora/pet-witness-templates.json | f11acfe13791d06b7f91fd65a7c0ae3c242c40c0f2dd7889d5f2869917714c98 |

## 13. Unresolved and build-skew populations

### Part A

| item | class | why | reopen condition |
|---|---|---|---|
| 105174, 104316, 42650, 275699, 55342, 34433, 198067, 198103, 51533, 86659 | unresolved | client effect is Dummy; no bound script creates a unit | a Trinity script binding that summons |
| 273277 Summon Animal Companion (+ effects 135/168/188/199/260) | trinity-consumer (no consumer) | `EffectNULL` | a real handler |
| 1304581 Soulcoil Remnant, 1308399 Answered Calling | build-skew | spell > 12.0.7; creature 269501/271402 absent from TDB | a Trinity revision supporting 12.1 |
| 42 class-skill companion spells | build-skew | same | same |
| 22 class-skill companion spells (e.g. 1254822, 1256380, 1269627) | world-db-fact (gap) | spell predates 12.0.7 but the creature is not authored in the TDB | a later TDB |
| `GetCharmerOrOwnerOrOwnGUID` inversion | trinity-consumer | refactor 4ba5e27055c inverted it; 8 consumers affected | upstream fix / probe |
| 24 unread SummonPropertiesFlags (incl. DespawnOnSummonerDeath/Logout, DespawnWhenReplaced, GuardianActsLikePet) | unresolved | no reader: unowned summons outlive their owner unless slotted | a Trinity consumer |
| auras 157, 381, 382, 429 | unresolved | `HandleNULL` | a consumer (Track B) |
| summon duration with Duration spell mods | trinity-consumer | float×double product + int32 truncation; −1 sentinel unguarded; not probed here | a differential probe (op-11 pattern) |
| `EffectSummonType` 1896 null dereference | structural-inference | tests `caster`, dereferences `m_originalCaster` | a probe / upstream fix |
| `creature_template_difficulty` | unresolved | 22,774 UPDATEs, each a full scan in `tdb_replay` (hours); summon level/ContentTuning not joined | indexed replay (tool owner) or the Track B/D extract |
| 3 unparsed TDB statements (`ON DUPLICATE KEY UPDATE` PvPFlags, `@PATH`, duplicate-key summon_groups) | unresolved | restricted replay grammar | replay support |
| hunter pet identity | trinity-consumer | characters DB, not DB2/world | Track C fixture input |

Machine-readable list: `docs/research/controlled-unit-corpora/unknowns.json` (track A; A-1..A-23 from part A, B-1..B-23 from part B, R1-* from the closeout review).

### Part B

| item | class | why | reopen condition |
|---|---|---|---|
| owner haste / crit / mastery → pet | unresolved | no Trinity consumer propagates them (StatSystem.cpp:108-424, Player.cpp:5237) | a consumer, or Retail evidence for the inheritance rule |
| aura 429 MOD_SUMMON_DAMAGE (63 in-scope spells) | unresolved | HandleNULL (SpellAuraEffects.cpp:501) | a handler, or a script on the witness spells |
| aura 382 MOD_PET_STAT_PCT (6), 381 (7), 157 (1) | unresolved | HandleNULL (:454, :453, :229) | as above |
| 10 "[DNT] Periodic Scaling Summons" (1288017 build-skew `added`) | unresolved / build-skew | no consumer; 1288017 is newer than 12.0.7 | a script binding |
| cast → summon edges (105174, 42650, 55342, 34433, 198067, 198103, 51533) | unresolved | no linked spell, binding, script or authored trigger | a spell_linked_spell / spell_script_names row or a script |
| 46584 Raise Dead | unresolved (trinity-consumer: hook inert) | eff0 is TRIGGER_SPELL → 1242866 (aura dummy), not DUMMY | the script updated to the 12.x effect layout |
| GoAK 86659 → 46499 | unresolved | no 12.1 summon effect for 46499 | any 12.1 spell summoning 46499 |
| Felstorm 89751 + 16 other pet abilities | unresolved | no acquisition path (§8.2) | creature_template_spell rows or SLA SpellLevel |
| ContentTuning positional mapping | structural-inference | 12.1 column names differ from Trinity's LoadInfo | a 12.1 Trinity DB2 layout |
| Guardian level 0 (31216, 54983) | trinity-consumer + world-db-fact | CT 0 → ScalingLevel 0; for 31216 only on maps whose difficulty resolves to row 0 (§7.2 #13) | a TDB ContentTuningID, or the UseCreatureLevel flag |
| Guardians with LevelScalingDelta min ≠ max | unresolved | `irand` (Creature.cpp:3071) | fix the delta |
| template-ai path use (657) | structural-inference | AI-dependent; smart_scripts not extracted | extract smart_scripts; resolve AI per entry |
| Rate.Creature.* | assumption (default 1.0) | worldserver.conf defaults | a server config other than the default |
| Guardian aura stat changes after creation | trinity-consumer (gate) | CanModifyStats false → no Update* | an explicit trigger |
| Pet 40% school resistances | legacy-only | no in-scope non-physical resistance input | an item or aura granting school resistance |
| InitStatsForLevel entries 510/1964/15352/15438/19833/19921/28017; spell_pet.cpp | legacy-only | no 12.1 summon of these entries; AddSC never called | a 12.1 summon of those entries |

## 14. Cross-part overlaps

### Part A

- **Track B (stats/spells):**
  - `Guardian::InitStatsForLevel` / `Pet::InitStatsForLevel` (`Pet.cpp:839-1090`), `pet_levelstats`,
    `CastPetAuras` / `spell_pet_auras`, `LearnPetPassives`, pet specs: A cites only the call order.
  - A's `is_permanent_pet_for()` mirror and the `creature_template.type` join are also inputs to B's
    pet-aura and spell-list logic.
  - `creature-templates.json` includes B's 12 witness entries (`B/witness_ids.json`).
  - A's `population.CreatureTemplates` loader reads that file; B may reuse it.
- **Track B/D/F (world DB):**
  - `creature_template_difficulty` was not extracted by A; agent D runs its own projected extraction
    (`scratchpad/D/ctd.json`).
  - The slowness is a `tools/tdb_replay.py` property (22,774 UPDATEs), for the lead to decide on.
- **Track C (fixtures):**
  - §1.5 / `witnesses-a.json` `hunter_pet_caller_input` lists the character-DB pet state a fixture must
    supply.
  - Owner spell mods (Duration) reach Pet/Totem casts only.
- **Track D (proc events):**
  - Spell proc actor = `m_originalCaster ?: m_caster` (`Spell.cpp:2842, 3915, 4223`); melee actor =
    attacker.
  - The KILL proc is forwarded to the owner only for Pet/Totem (`Unit.cpp:11383-11388`).
  - Consistent with `proc-pipeline-archaeology.md` §11; no contradiction found.
- **`dummy-server-semantics-archaeology.md`:** family `pet-owner-forward-cast` player hooks (8092, 32379,
  136511, plus 392988 with a pet-owner action) are reused as `structural-inference` attribution
  witnesses; no contradiction found.
- **Contradiction with existing reports:** none found. The `GetCharmerOrOwnerOrOwnGUID` regression is
  new; no existing report mentions it.

### Part B

- **Track A:**
  - Summon class routing: my `inheritance.route_summon_effect` computes it independently. It should agree with A's population census; A owns the counts.
  - World rows: A's `creature-templates.json` (1,780 templates, no difficulty) is read first by `stats.WorldRows`, and my `pet-witness-templates.json` adds `creature_template_difficulty`, `_resistance`, `_spell` and `creature_classlevelstats` for 22,136 summoned entries. A may reuse it.
  - A's `is_permanent_pet_for()` mirrors Pet.cpp:1657-1678, which §8.1 also uses.
- **Track F:** `player-base-stats.json` pet_levelstats is read unchanged (32 entries, 2,715 rows). Its provenance lacks a `generator` key.
- **Track C/D:** Guardian and creature weapon damage (§6.2) feed white swings. ExpectedStat evaluation (`stats.Db2Scaling`) is a local implementation; C only records the ArmorConstant row.
- **dummy_semantics:** reused `bindings.json` (`executes`), `families.json` (pet-owner-forward-cast), `script-index.json` constants, the Scope and the overlay. There is no contradiction: the dummy report's 65/16 unimplemented rows equal my row counts.
- **character-stat-pipeline-archaeology.md:494** lists `MOD_SUMMON_DAMAGE` among auras "whose amount a generic engine rule consumes". In the pinned Trinity, aura 429 is `HandleNULL` (SpellAuraEffects.cpp:501) and has no consumer. That report's classification is about expressibility, not Trinity support. The lead should reconcile the wording.

## 15. Closeout review

Hostile review G1 (fresh context), on the same pins: snapshot `12.1.0.69497`, TrinityCore `7f3d43b`,
TDB `TDB_full_world_1200.26021_2026_02_06.sql`.

### 15.1 Method

- Built a claim list from the headline results and §1–§14: every only/never/all claim, every number,
  and every evidence class stronger than `structural-inference`.
- Re-read each cited Trinity function in full at the pin, including early returns, virtual dispatch,
  callers and scripts, instead of trusting the cited line.
- Used `git -C TrinityCore show 4ba5e27055c` for the regression.
- Re-derived the two binary32/health numbers by hand with a `struct`-packed binary32 emulation that
  rounds after every operation.
- Rebuilt `tools/tc_pet_probe` from a clean tree (`make clean && make`). The extracted
  `tc_pet_bodies.inc` sha256 `f478da72…` is unchanged, and `Guardian::UpdateMaxHealth` is
  byte-verbatim.
- Re-queried `data/tables` (SpellEffect, SpellMisc, ContentTuning, ContentTuningXExpected,
  ExpectedStat, ExpectedStatMod, Difficulty), `pet-witness-templates.json`,
  `player-base-stats.json` and the dummy overlay (`spell_script_names`, `spell_linked_spell`,
  `spell_scripts`, `spell_area`, `conditions`, `spell_learn_spell`).
- Reran `all-a`/`all-b` twice each, reran the world-DB extraction, and ran the test suite.

### 15.2 Findings

| id | claim | verdict | action | evidence |
|---|---|---|---|---|
| G1-1 | §12 extraction is reproducible (`--keep …=@IDS`) | **defect**: the id list lived only in the scratchpad | new `controlled_units.py creature-ids` (`population.py`), committed `world-db-corpora/creature-templates.ids.json` (1,847 ids, per-component and per-coordinate), new test; §12 rewritten | the derivation reproduces the scratch list exactly; the rerun extraction (31 s) gives `creature-templates.json` sha256 `8bd33c55…`, byte-identical |
| G1-2 | Part B §12 TDB path `../../../bag/…` | **defect** | 4 `..` from `scripts/research` | path arithmetic |
| G1-3 | headline 3 "Only Pet and Totem forward to their owner" | **overstated** | reworded to spell mods and KILL proc; the other owner paths are listed in §3 and R1-4 | `Spell.cpp:475`; `Unit.cpp:7342, 12416-12440, 12548-12557, 6543` |
| G1-4 | `GetCharmerOrOwnerOrOwnGUID` inversion, commit `4ba5e27055c` | **confirmed** | headline wording made exact (uncharmed players; charmed player → true) | the `git show` diff swaps `return guid; return GetGUID();` for `guid = GetGUID(); return guid;`. Single definition (`Object.h:440`). 8 call sites re-read (`Spell.cpp:2774`, `Creature.cpp:1775, 1802`, `Unit.cpp:4444`, `GameObject.cpp:3170` — party-only spellcaster GOs now reject every non-owner group member — and 3 scripts) |
| G1-5 | owner haste/crit/mastery reach no controlled unit | **confirmed** as code absence; evidence stays `unresolved` for the rule | headline wording; new R1-1 | creature crit = 5% + own auras (`Unit.cpp:2896-2905`); spell crit 0 without a mod owner (`:7124`); all spell/melee bonus owner reads go through `GetSpellModOwner`; no pet script reads owner ratings. `SPELL_ATTR4_OWNER_POWER_SCALING` has no reader but sits on 34902 "Hunter Pet" |
| G1-6 | snapshot/dynamic triggers (§7.1) | **overstated** (incomplete) | added the `Player::UpdateResistances` → pet trigger and the aura-404 indirect spell-power trigger | `StatSystem.cpp:245-247, 163-167, 190-194`; the in-scope 404 carriers are 137012/137013/1258016/1258138 |
| G1-7 | "PvP flags copied once" (§4.1) | **defect** | reworded; R1-5 | `Player.cpp:24027-24035, 24052-24057` |
| G1-8 | Divine Image and similar per-entry behaviour hidden by the Guardian rule | **defect** (omission) | §7.1 exception; R1-3 | `scripts/Pet/pet_priest.cpp:43-49` |
| G1-9 | Guardian health double count, 95061 → 16,681,309 | **confirmed** | none | by hand: ES 141482.015625 × class mod 0.8 (95061 `unit_class` 8 → ExpectedStatMod 1; CT 482 has no ContentTuningXExpected row) = 113185.6171875 f32. `ceil(double·73.425f)` = 8,310,655 = BASE_VALUE (`Creature.cpp:1643`); CreateHealth `uint32(f32 product)` = 8,310,654; + (6025−25)·10. The only writer of `UNIT_MOD_HEALTH BASE_VALUE` is `Creature.cpp:1643` |
| G1-10 | Imp 128,128 vs exact 128,129 (binary32) | **confirmed** | none | 8.4f = 8.39999961853; 15000·8.4f → f32 125999.9921875; + 2129 = 128128.9921875 (representable at 2^16..2^17) → `(uint32)` 128128. Stamina = int32(119 + 15000) − 119 |
| G1-11 | aura 429 (and 157/381/382) has no consumer | **confirmed** | none | the only other occurrence of `SPELL_AURA_MOD_SUMMON_DAMAGE` is a hook type filter in `boss_anduin_wrynn.cpp:3190`; no script names 76657/77219/77515; 137006-137008 appear only as spec checks in `spell_dk.cpp`. Counts re-queried: 393/63/65, 14/6/16, 62/7, 26/1 |
| G1-12 | 46584 Raise Dead summons nothing | **confirmed**; coordinate **defect** | §8.3 names the runtime gate | 46584 eff0 = 64 → 1242866 (aura 4). Hook filtered at `Spell.cpp:8895` via `SpellScript.cpp:179-189`; `SpellScript.cpp:292` only logs |
| G1-13 | 7 unlinked casts | **confirmed** | none | `spell_script_names` has only 34433 (`spell_pri_shadow_covenant`: AfterCast → covenant effect) and 46584; 0 rows in `spell_linked_spell`, `spell_scripts`, `spell_area`, `conditions`, `spell_learn_spell`; no authored `EffectTriggerSpell`; the only hard-coded mentions (51533/55342) are in `PlayerAI.cpp` (charmed-player AI) |
| G1-14 | Mirror Image / Grove Guardians level 0 | **overstated** | headline 8, §7.2 #13, §7.4, §11 and §13 qualified; R1-2 | 31216 difficulty rows 1/2 carry CT 482; `Creature.cpp:511` picks the row by map difficulty; Difficulty fallback chains resolved from `Difficulty.csv` |
| G1-15 | hunter pet placeholder stats 81–90 | **confirmed**, with a configuration dependency | headline wording; R1-6 | rows 81–85 are 1s; 86–90 are the loader gap fill (`ObjectMgr.cpp:3756-3762`) up to `MaxPlayerLevel` = 90 (`worldserver.conf.dist:912`); `PetLevelInfo` fields are uint16 (armor read as uint32, then narrowed) |
| G1-16 | hunter pet identity is caller input | **confirmed** | none | `Pet.cpp:205-262, 286-315, 413-417`; Call Pet 883/83242-83245 `EffectBasePointsF` 0..4 → `PetSaveMode` (`SpellEffects.cpp:2714-2716`) |
| G1-17 | category derivation from the branch | **confirmed** | none | hand-checked 26573 (ALLY, no Join → ally-summon), 46585, 115313 vs 115315 (Join decides), 113724, 1122 and 111771 (WILD default), 1308399, 1254168, 102693 (Title Pet + Join → CONTROLABLE), 101643 (Join 0x200 set), 458101 (Join totem → SummonGuardian → Totem), and 273277 (`EffectNULL`, `SpellEffects.cpp:352`, no script) against `SpellEffects.cpp:1867-2085` and `Object.cpp:1193-1240` |
| G1-18 | headline 10 "17 current pet abilities … 806 do" | **overstated** | reworded | the census population covers the template spells of every 12.1-summoned entry; 599 of the 806 have only the structural `template-ai` path |
| G1-19 | Totem health = effect value | **overstated** | §6.2 qualified | the override is only on the `Title::Totem` arm (`SpellEffects.cpp:1969-1984`); Join totems (458101) take `SummonGuardian` |
| G1-20 | duration spell-mod arithmetic | **confirmed** | none | `Player.cpp:22842-22849`; `SpellInfo.cpp:3975-3991` |
| G1-21 | probe is verbatim / Python not written from the same misreading | **confirmed** for the checked case | none | G1-10 re-derived independently of the Python; probe stubs match Trinity types (`GetCreateHealth` uint32, `GetStat` float(int32)). The probe takes ExpectedStat values and the level from the driver, so G1-9 and G1-14 rest on the hand derivation and the world-DB rows, not on the probe |
| G1-22 | corpora deterministic, no host paths | **confirmed** | none | `all-a` twice and `all-b` twice → identical sha256 (= §12 tables); no `/home` or `/tmp` strings in the corpora |
| G1-23 | §13 unknowns pointer to `parts/A-unknowns.json` | **defect** (scratch path) | now points to the committed corpus | — |

Counts: 23 claim groups checked.
- 6 defects fixed: G1-1, G1-2, G1-7, G1-8, G1-12 (coordinate only), G1-23.
- 5 overstated and weakened: G1-3, G1-6, G1-14, G1-18, G1-19.
- 12 confirmed: G1-4, 5, 9, 10, 11, 13, 15, 16, 17, 20, 21, 22. G1-5 and G1-15 also had their
  wording tightened.
- 0 left unsupported: the new gaps are unknowns R1-1..R1-6.

### 15.3 Fixes (before → after)

- §12 Part A: `--keep …=@IDS` (scratch file) → `controlled_units.py creature-ids --keep-file …`
  plus committed `creature-templates.ids.json`; 663 s → 31 s; output sha256 unchanged.
- §12 Part B: `../../../bag/tdb/…` → `../../../../bag/tdb/…`.
- §4.1: "PvP flags copied once" → copied, then re-applied by the player owner.
- §8.3: "SpellScript.cpp:292 drops the hook" → runtime gate `Spell.cpp:8895` / `SpellScript.cpp:179-189`
  (292 only logs).
- §7.1: two missing recalculation triggers and the Divine Image script snapshot added.
- §13: unknowns pointer → `docs/research/controlled-unit-corpora/unknowns.json`.
- Code: `controlled_units/population.py` (`creature-ids`, `HARDCODED_ENTRIES`, `TRACK_B_WITNESS_ENTRIES`);
  test `test_cu_population.py::test_creature_id_list_is_committed_reproducible_and_covers_every_census_and_witness_entry`.

### 15.4 Weakened conclusions

- Headline 3: spell mods and the KILL proc are Pet/Totem-only. "Only Pet and Totem forward" is
  no longer claimed.
- Headline 5: this is a code-absence statement (`trinity-consumer`); the inheritance rule stays
  `unresolved` (B-1, R1-1).
- Headline 8 and §7.2 #13: level 0 depends on map difficulty for 31216 (R1-2).
- Headline 8: hunter-pet levels 86–90 depend on the default `MaxPlayerLevel` (R1-6).
- Headline 10: the census is not current-player-only; 599 "has-path" spells rest on `template-ai`
  alone (B-18).

### 15.5 Confirmed conclusions (how verified)

- Population/census category counts: branch re-read, 12 records hand-classified (G1-17); the test
  pins the counts.
- TDB absence equals build skew in the default scope: test plus `creature-templates.json` rerun.
- `GetCharmerOrOwnerOrOwnGUID` regression: git diff plus all 8 callers.
- The 16,681,309 and 128,128 numbers: hand binary32 derivation (G1-9, G1-10).
- Aura 157/381/382/429 `HandleNULL` with no consumer: repository-wide grep plus counts.
- 46584 inert, 7 unlinked casts, GoAK 86659 (effects 56/4/87 only): overlay tables, scripts and
  SpellEffect rows.
- Hunter-pet caller input: `Pet::LoadPetFromDB` read in full.
- Determinism: `all-a` reruns are byte-identical (the six §12 sha256 values are unchanged).
  `all-b` reruns are byte-identical as well (`stats` `3912ce3a…`, `inheritance` `57f2526a…`,
  `spells` `61b82384…`, `witnesses-b` `7fe34a38…`, equal to §12 Part B).

### 15.6 Residual risks

- Creature AI scripts bound to summoned entries (`ScriptName` in `creature-templates.json`) were
  audited only for stat setters and owner reads in `scripts/Pet` and `scripts/Spells`. Other script
  directories and SmartAI (`smart_scripts` not extracted) can still hide per-entry behaviour (R1-3, B-18).
- The probe is bounded: TempSummon level clamping, `CreatureTemplate::GetDifficulty` and
  `EvaluateExpectedStat` are driver inputs, so difficulty- and ContentTuning-dependent results are
  checked only by the hand derivation.
- The ContentTuning column mapping stays `structural-inference` (B-16). A wrong mapping would shift
  every Guardian level/ExpectedStat number that depends on CT 482.
- The resilience and `ORIGINATE_FROM_CONTROLLER` paths are PvP/vehicle contexts and were not
  modelled (R1-4).
