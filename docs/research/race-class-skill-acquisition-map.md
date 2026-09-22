# Race/class skills and automatic acquisition: what the skill tables author

Status: complete 2026-09-22. Read-only research pass. It is navigation, **not Core semantic authority**, and it
proposes no Core change. Core (`../core`, dirty tree from another session) was read only to learn how Core
describes 114585 today (§7). No existing file in any repository was modified. The pass added this report, the
script `scripts/research/skill_acquisition.py`, and the corpus directory
`docs/research/skill-acquisition-corpora/`.

Evidence classes used below:
- **db2-fact**: a row reopened from `data/tables/*.csv` in this pass.
- **trinity-consumer**: a pinned TrinityCore line that reads the row. All TrinityCore paths are
  `src/server/game/…` at the pin unless stated.
- **world-db-fact**: a row from the pinned TDB replay.
- **replay**: a count from the static replay in `skill_acquisition.py`. The replay follows the consumers in
  order. It does not execute Trinity.
- **UNPROVED**: the claim cannot be shown from current source plus the pinned direct consumers. Every such
  claim is labelled where it appears and collected in §10.

## 0. Pins and reproduction

| input | pin |
|---|---|
| wowlab-data | branch `research`, HEAD `5de3ddc`. The working tree has unrelated uncommitted edits to `selected-package-corpora/*.json` and an untracked `data/latest.duckdb`. Neither is used as evidence. |
| DB2 snapshot | Wago client build **12.1.0.69497**, `data/tables/` (1,104 tables). SkillRaceClassInfo `ab5ff4b5…`, SkillLine `fefa339f…`, SkillLineAbility `b96e163a…`. The sha256 of every table the replay reads is in `summary.json`. |
| TrinityCore | `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` (clean), read-only |
| TDB world | `TDB_full_world_1200.26021_2026_02_06.sql` (sha256 `54ddf4c1…`) plus 522 `sql/updates/world/master` files. Replayed with `scripts/research/tools/tdb_world_extract.py` into `skill-acquisition-corpora/world-inputs.json`, which covers `skill_tiers`, `conditions` (source type 35), `spell_learn_spell`, `playercreateinfo`, `playercreateinfo_spell_custom` (0 rows) and `skill_discovery_template`. |
| character universe | the 281 `playercreateinfo` (race, class) pairs: 31 races × 13 classes. `Player::Create` refuses any other pair (`Entities/Player/Player.cpp:396-402`). |
| level | 90 unless stated; the replay was also run at 1, 10, 20 and 80 (§5.9) |

```
python3 scripts/research/tools/tdb_world_extract.py --tdb ../../bag/tdb/TDB_full_world_1200.26021_2026_02_06.sql \
    --tables skill_tiers,conditions,playercreateinfo_spell_custom,playercreateinfo,spell_learn_spell,skill_discovery_template \
    --keep conditions.SourceTypeOrReferenceId=35 --out docs/research/skill-acquisition-corpora/world-inputs.json
python3 scripts/research/skill_acquisition.py --world docs/research/skill-acquisition-corpora/world-inputs.json \
    --out docs/research/skill-acquisition-corpora            # ~5 s; add --check to prove the corpus is fresh
```

Corpus files: `summary.json` (the level-90 counts; the other-level table in §5.9 comes from `--level N` runs), `srci-rows.json` (all 352 SkillRaceClassInfo rows
with range type and matched pairs), `sla-rows.json` (the 1,693 SkillLineAbility rows with an automatic method (1, 2 or 4),
with tags and the pairs that auto-acquire each), `pairs.json` (skills with value, max, range and path, and
acquired spells with path, for each pair), and `gates.json` (level, rank and condition gates, the
specialization delta, and raw-36 edges).

## 1. Short answer

- **SkillRaceClassInfo (SRCI) says which skills a (race, class) may hold.** Only `Availability == 1` rows are
  default skills. Trinity reads `Availability` in exactly one place, `Globals/ObjectMgr.cpp:4064`. The same
  table also gives the skill's value range and tier, its `MinLevel` gate and its flags. It also maps each class
  to its class skill line, and so to its talent tree (`Spells/TraitMgr.cpp:262-282`).
- **SkillLineAbility (SLA) says which spells a held skill grants, and how.** A spell is learned automatically
  only when its `AcquireMethod` is 1, 2 or 4 and its race mask, class mask, spell level and (for method 1)
  skill rank all pass (`Entities/Player/Player.cpp:25391-25441`). Method 0 (Learned, 15,620 rows) and method 3
  (NeverLearned, 639 rows) are only associations: trainer eligibility, quest-reward vetting, ranks and UI.
- **Composition**: create or login → `LearnDefaultSkills` → `LearnDefaultSkill` (range type sets the value) →
  `SetSkill` → `LearnSkillRewardedSpells` → `AddSpell`. `AddSpell` then casts passives and can pull in further
  skills. Learning *any* spell that has a method-2 SLA row on a skill the player lacks installs that skill, even
  when the skill's SRCI row has `Availability 0` (`Player.cpp:2998-3016`). Specialization spells use this path
  to reach the skill system (§5.8).
- **Population at level 90**: each pair holds 18–35 skills and learns 107–147 spells automatically (mean
  127.8). Across all pairs, **623 SLA rows (620 distinct spells)** are auto-acquired by at least one pair, and
  **78 rows are acquired by all 281 pairs**.
- **Skill 183 / SLA 25920 / spell 114585**: SRCI 5 gives skill 183 "GENERIC (DND)" to every class
  (ClassMask 16383) and every race, with Availability 1 and MinLevel 0. SLA 25920 is AutomaticCharLevel with
  empty masks. Spell 114585 has no SpellLevel. So **every character learns it at creation**, in
  `Player::Create → LearnDefaultSkills` (`Player.cpp:505`), and relearns it at every login from the saved skill
  (`Player.cpp:27336`) and from `LearnDefaultSkills` (`:18644`). It is a clean passive raw-6/aura-318 with
  +8 Mastery. Its value reaches the Mastery field only while `CanUseMastery()` holds, which needs the
  specialization's mastery spell (level 10) (`Entities/Unit/StatSystem.cpp:542-551`,
  `Player.cpp:30525-30530`). §7 traces it fully.
- **Armor and weapon proficiency is owned by the skill value, not by the proficiency spell.** In Trinity the
  server-side equip gate is `GetSkillValue(ItemTemplate::GetSkill()) != 0` (`Player.cpp:11155-11179`). The
  subclass→skill mapping is hardcoded in `Entities/Item/ItemTemplate.cpp:97-140`, and SRCI supplies the skill.
  The SLA-granted effect-60 spells only fill a bitmask that is sent to the client and never read on the server
  (`Spells/SpellEffects.cpp:1845-1864`). `ChrClasses.ArmorTypeMask` is a third, different fact: it holds the
  class's *primary* armor type and gates only need-rolls (`Player.cpp:11282-11286`). Whether retail's server
  works the same way is **UNPROVED** (§5.4).
- **Raw 36 (Learn Spell) plays no part in this population.** None of the auto-acquired spells, and nothing they
  reach, carries a raw-36 edge (`gates.json runtime_raw36: []`). The dependent learning that does occur comes
  from `SpellLearnSpell.db2` (4 edges), `spell_learn_spell` (1 edge) and SupercedesSpell rank chains (§6).

## 2. What each table is (per its consumers)

### 2.1 SkillLine: the skill identity (397 rows)

Trinity reads four fields: `CategoryID` (range type and profession handling), `ParentSkillLineID` and
`ParentTierIndex` (parent and child skill activation), and `Flags` (only the race-change flag 0x8000 has
behaviour). The rest are client and UI fields. `SkillCategory` is defined at
`Miscellaneous/SharedDefines.h:6367-6378`:

| Category | meaning (Trinity enum) | rows |
|---:|---|---:|
| 5 | ATTRIBUTES (e.g. 899 "Racial - Pandaren", 821 guild perks) | 7 |
| 6 | WEAPON (weapon skills, Defense 95, Unarmed 162, Dual Wield 118) | 18 |
| 7 | CLASS (13 class lines, pet families, covenants, Mounts 777, Companions 778, Runeforging 960) | 103 |
| 8 | ARMOR (Cloth 415, Leather 414, Mail 413, Plate 293, Shield 433) | 5 |
| 9 | SECONDARY (the name is historical: racial lines, Riding 762, All Classes 2727, secondary professions) | 73 |
| 10 | LANGUAGES | 23 |
| 11 | PROFESSION | 157 |
| 12 | GENERIC (183 GENERIC (DND), 810 All - Glyphs, 934 All - Specializations, 1830 Unused, 2817 Language: Cypher, …) | 10 |
| 27 | not in Trinity's enum (1 row, "Journeyman Cookbook") | 1 |

### 2.2 SkillRaceClassInfo: skill availability per race and class (352 rows, 309 skills)

`DataStores/DB2Structure.h:3728-3738`. Each field and its consumer:

| field | consumer | semantics |
|---|---|---|
| `SkillID` | `DB2Stores.cpp:1530-1532` | index key. The index drops rows whose SkillID is not in SkillLine. SRCI 86 and 925 (skill 129, removed First Aid) are dropped here, but `ObjectMgr.cpp:4063` still iterates the raw store, so SRCI 925 (Availability 1, DK) reaches `LearnDefaultSkill` and does nothing because its range is NONE |
| `RaceMasks_0/1` | `DB2Stores.cpp:2995`; `ObjectMgr.cpp:4066`; bit = `RaceMask::GetRaceBit` (`Miscellaneous/RaceMask.h:97-146`) | empty means all races. The Trinity bit equals `ChrRaces.PlayableRaceBit` for all 31 playable races (checked in this pass) |
| `ClassMask` | `DB2Stores.cpp:2997`; `ObjectMgr.cpp:4068` | 0 or −1 means all classes |
| `Availability` | `ObjectMgr.cpp:4064` only | `1` = default skill, put in `PlayerInfo::skills`. `0` and `2` are never read, so those rows only allow the skill (for `_LoadSkills`, `SetSkill` ranges, trainers and cascades) |
| `MinLevel` | `Player.cpp:25270` only | level gate on the default grant |
| `SkillTierID` | `ObjectMgr.cpp:9008` (world `skill_tiers`) | a tier present in `skill_tiers` makes the range RANK. Tier values are world-db, not DB2 |
| `Flags` | `Player.cpp:25288, 25300, 2987, 5625`; `SpellEffects.cpp:2386` | only 0x10 ALWAYS_MAX_VALUE has server behaviour (`DataStores/DBCEnums.h:2376-2384`). 0x2, 0x80, 0x100 and 0x400 are client or display flags |

A lookup returns the **first** matching row (`DB2Stores.cpp:2991-3004`). Five skills have more than one row
matching the same pair (152, 171, 778, 2465, 2819). All of them are Availability 0 profession or companion
lines, so no default skill depends on the order within an `unordered_multimap` bucket. Order-dependent tier
selection for skills 171 and 152 is **UNPROVED** and does not affect combat preparation.

### 2.3 SkillLineAbility: the spells attached to a skill (17,952 rows, 17,476 spells)

`DB2Structure.h:3696-3718`; `AcquireMethod` enum at `DBCEnums.h:2360-2367`.

| field | consumer | semantics |
|---|---|---|
| `SkillLine`, `SkillupSkillLineID` | `DB2Stores.cpp:1527-1528` | **Grant key = `SkillupSkillLineID` if non-zero, else `SkillLine`.** 11,180 rows redirect this way, all of them profession (10,473) or secondary (707). `AddSpell`'s reverse map keys on `Spell` (`SpellMgr.cpp:1952-1962`) |
| `AcquireMethod` | `Player.cpp:25404-25418` | 0 Learned: never automatic. 1 AutomaticSkillRank: learned while value ≥ `MinSkillLineRank`, otherwise removed (`:25434-25435`). 2 AutomaticCharLevel: learned with the skill. 3 NeverLearned. 4 LearnedOrAutomaticCharLevel: automatic only if `SpellMisc.ShowFutureSpellPlayerConditionID` and every `conditions` source-35 row pass (`:25409-25415`), otherwise a quest or trainer path (`:25361-25369`) |
| `RaceMasks`, `ClassMask` | `Player.cpp:25421-25427`; also `IsSpellFitByClassAndRace` (`:25836-25860`, trainer gate) | a zero mask means all |
| `MinSkillLineRank` | `Player.cpp:25434` | only meaningful for method 1 |
| `SupercedesSpell` | `SpellMgr.cpp:823-…` (`LoadSpellRanks`) | builds rank chains; `AddSpell` learns lower ranks first (`Player.cpp:2845-2851`) |
| `Flags` | none | `CanFallbackToLearnedOnSkillLearn` (0x80) is declared at `DBCEnums.h:2371` and never read |
| `TrivialSkillLineRankHigh` | `Player.cpp:3011` | Runeforging special case only |
| spell level | `Player.cpp:25430-25432` | the gate is `max(SpellLevels.SpellLevel, BaseLevel)`, not an SLA column |

Rows by method: Learned 15,620 · NeverLearned 639 · AutomaticCharLevel 1,084 · AutomaticSkillRank 562 ·
LearnedOrAutomaticCharLevel 47. By category, 11,541 of the 17,952 rows are professions (10,473 with a
SkillupSkillLineID redirect) and 1,325 are secondary lines (707 redirected). No other category uses the redirect.

### 2.4 Directly related tables

| table | role | consumer |
|---|---|---|
| `SpellLevels` | spell level gate for automatic grants | `Player.cpp:25430` |
| `SpellMisc.ShowFutureSpellPlayerConditionID` → `PlayerCondition` | method-4 gate | `Player.cpp:25411` |
| world `conditions` (SourceType 35) | method-4 gate (14 rows) | `Player.cpp:25413`; loader check `Conditions/ConditionMgr.cpp:2104-2118` |
| world `skill_tiers` | RANK ranges and max values | `ObjectMgr.cpp:9008`; `Player.cpp:25300-25311` |
| `SpellEffect` raw 118 SKILL / raw 40 DUAL_WIELD | a spell that installs a skill when learned | `SpellMgr.cpp:958-999`; `Player.cpp:2948-2994` |
| `SpellEffect` raw 36, `SpellLearnSpell`, world `spell_learn_spell` | dependent learning | `SpellMgr.cpp:1001-1153`; `Player.cpp:3018-3030` |
| `SkillLineXTraitTree` | class skill line → talent tree. SRCI `ClassMask` chooses the class | `Spells/TraitMgr.cpp:262-282` |
| `CreatureFamily.SkillLine[2]` | pet family skill lines → pet level-up and pet passive spells (321 SLA rows) | `SpellMgr.cpp:2152-2190`, `:5557-5586` |
| `ItemSubClass`, `ChrClasses.ArmorTypeMask` | **not** part of skill acquisition (§5.4). `ItemSubClass.Prerequisite/PostrequisiteProficiency` has no Trinity consumer | `Player.cpp:11282-11286` only |
| `ChrRaceRacialAbility` (154 rows) | UI text only, no Trinity consumer. Racial spells come from the racial skill lines | none |
| `ChrSpecialization.MasterySpellID`, `SpecializationSpells` | outside the skill system, but spec spells can trigger skill cascades | `Player.cpp:30631-30646`, `:28950-28955` |

## 3. The composition, stage by stage

```
race, class, level
  └─ ObjectMgr::LoadPlayerInfo (ObjectMgr.cpp:4059-4072): SRCI rows with Availability==1 that match → PlayerInfo.skills
Player::Create (Player.cpp:387-515)
  ├─ InitStatsForLevel (:490) … InitializeSkillFields (:493; :5641-5654): every SRCI-matching skill gets a slot at rank 0
  ├─ LearnDefaultSkills (:505; :25259-25275): skip if held; skip if MinLevel > level
  │    └─ LearnDefaultSkill (:25277-25314) → GetSkillRangeType (ObjectMgr.cpp:9002-9025)
  │         LANGUAGE 300/300 · LEVEL 1 (or level×5 if flag 0x10; DK (L−1)×5) / level×5 · MONO 1/1 · RANK tier[0] · NONE no-op
  │         └─ SetSkill (:5659-5880) → LearnSkillRewardedSpells(skill, value) (:5713/:5871; :25391-25441)
  │              for SLA rows keyed by (SkillupSkillLineID||SkillLine): method∈{1,2,4}, [method 4: PlayerCondition + conditions],
  │              race mask, class mask, max(SpellLevel,BaseLevel) ≤ level, [method 1: value ≥ MinSkillLineRank]
  │              └─ AddSpell(spell, fromSkill) (:2690-3060)
  │                   ├─ lower ranks first (SupercedesSpell chain)                                 (:2845-2851)
  │                   ├─ passive → HandlePassiveSpellLearn → CastSpell(self)                       (:2920-2929; :3079-3101)
  │                   ├─ spell has raw 118/40 → SetSkill(that skill)                               (:2948-2994)
  │                   ├─ else SLA row of this spell with method 2 on an unheld skill (any Availability) → LearnDefaultSkill (:2998-3016)
  │                   └─ dependent learn: SpellLearnSpell.db2 / spell_learn_spell / raw 36 when not auto (:3018-3030)
  ├─ LearnCustomSpells (:506; world playercreateinfo_spell_custom has 0 rows)
  └─ default spec → … ActivateTalentGroup → LearnSpecializationSpells (:28950; :30631-30646), which can re-enter AddSpell
Player::LoadFromDB (:17925-…)
  InitStatsForLevel (:18583) → _LoadSkills (:18591; :27228-27336: SRCI must match or the skill is deleted; range re-imposed;
  LearnSkillRewardedSpells for each saved skill) → UpdateSkillsForLevel (:18592) → _LoadSpells (:18609) →
  LearnSpecializationSpells (:18612) → _LoadAuras (:18615) → InitTalentForLevel, LearnDefaultSkills, LearnCustomSpells (:18643-18645)
Player::GiveLevel (:2185-2245): UpdateSkillsForLevel → LearnDefaultSkills → LearnSpecializationSpells (:2231-2233)
```

A saved character can hold skills that are not defaults, such as professions, quest-granted skills or GM
additions. They are revalidated against SRCI at load (`:27253-27261`). This report replays only the
default-skill path and the specialization cascade (§5.8). Other learned history is host state.

## 4. Census

### 4.1 SkillRaceClassInfo

| Availability / category | rows | notes |
|---|---:|---|
| 1 / weapon | 20 | 18 skills |
| 1 / armor | 7 | 5 skills (Mail and Plate each have two rows) |
| 1 / class | 16 | 13 class lines, Mounts 777, Adventurer 2816 and Traveler 2929 (the last two are classes 14/15, which have no playercreateinfo pair) |
| 1 / languages | 19 | race-masked |
| 1 / secondary | 31 | 25 racial lines, Riding 762 (3 rows), All Classes 2727, All - Warbands 2902, … |
| 1 / generic | 5 | 183, 810, 934, 1830, 2817 |
| 1 / attributes | 1 | 899 Racial - Pandaren |
| 1 / missing skill | 1 | SRCI 925 (skill 129), no-op |
| 2 / weapon | 1 | SRCI 883, Dual Wield for Shaman. Availability 2 is never read |
| 0 / * | 251 | professions 161, secondary 50, languages 24, class 10 (Runeforging, covenants, Companions, Internal), generic 3, attributes 1, other 2 |

- ClassMask: −1 on 251 rows, 16383 (all 14 bits) on 26, a single class bit on 21, 0 on 5, other multi-class masks on the rest.
- RaceMasks: all races on 268 rows, restricted on 84.
- MinLevel: 0 on 339 rows, 1 on 10, 10 on 2 (Riding SRCI 890/2081), 8 on 1 (Riding SRCI 934, DK+DH).
- Range type of the 100 Availability-1 rows: LEVEL 69, LANGUAGE 19, MONO 7, RANK 4 (Riding ×3, Unused 1830), NONE 1.

### 4.2 Per pair (level 90)

- Default skills held: 18 (Pandaren-neutral Priest, 24:5) to 35 (Dark Iron Dwarf Warrior, 34:1).
- Automatically learned spells: 107 (24:5) to 147 (Dracthyr-Horde Hunter, 70:3), mean 127.8.
- Auto-acquired SLA rows (by at least one pair): **623** (620 spells). By method: 612 AutomaticCharLevel and
  11 LearnedOrAutomaticCharLevel with no gating condition. By scope: every-pair 78, class-restricted 301,
  race-restricted 163, race+class-restricted 81.
- By category (rows / passives): class 279/63 · secondary (racial lines, riding, All Classes) 198/122 ·
  generic 93/31 · weapon 23/22 · languages 18/18 · armor 6/6 · attributes 6/5.
- Tags of auto-acquired spells: passive 267, active 356, weapon-proficiency (raw 60, item class 2) 16,
  armor-proficiency (raw 60, item class 4) 7, language (raw 39) 18, dual-wield (raw 40) 1, learn-skill
  (raw 118, the riding tiers) 3, and mastery-aura (raw 6/aura 318) 3: 114585, 365575 Awakened, and 462854
  Skyfury (an active spell on the Shaman line).
- Method-4 rows that stay conditional: 32 (the list is in `gates.json conditional`). Most carry PlayerCondition
  83446 (not on the NPE maps, plus ContentTuning 958; see `character-preparation-closure.md` R3-5) or 94589
  (Forbidden Reach). The rest have world conditions: "outside Exile's Reach", "never as Evoker", or quest
  24593 / 65101 / 65613 completed. Examples: 163201 Execute (SLA 43270, Warrior), 118 Polymorph (25214, Mage),
  and every Hunter pet-control row (35942-35954, 43579).

## 5. Semantic categories, with representative rows and consumers

### 5.1 Every-character automatic baseline (78 rows, all 281 pairs)

Everything comes from Availability-1 SRCI rows with ClassMask −1 or 16383 and no race mask: 95 Defense (SRCI 4),
183 GENERIC (SRCI 5), 162 Unarmed (SRCI 6), 415 Cloth (SRCI 148), 810 Glyphs (SRCI 998), 934 Specializations
(SRCI 1160), 2727 All Classes (SRCI 2325) and 2902 All - Warbands (SRCI 2480). Representative rows:

| SLA | skill | spell | what it is |
|---:|---:|---|---|
| 25920 | 183 | 114585 Mastery | raw 6 / aura 318, +8 Mastery points (§7) |
| 3999 | 183 | 6603 Auto Attack | active melee auto attack |
| 1441 | 183 | 2382 Generic | raw 25 + raw 60, weapon subclass mask 16384 (Misc) |
| 5008 | 183 | 9125 Generic | raw 60, armor subclass mask 33 (Misc + Cosmetic) |
| 4999 | 415 | 9078 Cloth | raw 60, armor mask 2 |
| 689 / 490 / 1424 | 95 | 81 Dodge / 204 Defense / 522 SPELLDEFENSE | raw 20 / 26 / 37. Trinity maps all three to `EffectUnused` (`SpellEffects.cpp:112, 118, 129`) |
| 1236 | 162 | 203 Unarmed | raw 25 (`EffectUnused`, `SpellEffects.cpp:117`), weapon mask 8192 |
| 1869… | 183 | Opening / Closing / Duel / Grovel / Stuck / Summon Friend / Inspecting | world-interaction and UI actives |
| 32677… | 183 | Garrison / Covenant / Signature / Delves / Pact / Companion Ability, Pocopoc, lorewalking, gem actions | zone-ability UI actives |
| 23502/23589/25573 | 810 | Clear Glyph ×3 | glyph UI |
| 25873 | 934 | 113873 Remove Talent (plus 127649/127650 by rank chain) | talent UI |
| 40581, 56413-57261 | 2727 | Heart Essence, transmog outfit actions, Recuperate | UI actives |

The every-pair set has four passives with combat meaning: 114585, the two proficiency "Generic" spells and
Cloth. Defense and Dodge are inert in Trinity.

### 5.2 Class baseline (class skill lines, 279 rows)

Each class line (795 Hunter, 796 DK, 798 Druid, 800 Paladin, 804 Priest, 829 Monk, 840 Warrior, 849 Warlock,
904 Mage, 921 Rogue, 924 Shaman, 1848 DH, 2810 Evoker) is an Availability-1 SRCI row with one class bit
(SRCI 980…2399). Druid SRCI 983 also carries a race mask, but it covers all 9 Druid pairs. These lines are the
class baseline spellbook (100 Charge, 6552 Pummel, 1464 Slam, 6673 Battle Shout, …). Most are method 2 and are
gated only by spell level (maximum 56). Class passives with combat meaning, all class-restricted:

- **Class aura packages**, one per class, multi-effect aura 416/457/379/108/…: 137047 Warrior, 137014 Hunter,
  137018 Mage, 137030 Priest, 137034 Rogue, 137042 Warlock, 137005 DK, 137009 Druid, 137026 Paladin,
  137038 Shaman, 137022 and 130610 Monk, 212611 DH, 353167 Evoker. Together they cover all 281 pairs.
- **Armor-type +5% primary stat** (aura 137): 86091 Nethermancy (Warlock), 86092 Leather Specialization
  (Rogue), 86538 Mail Specialization (Hunter), 89744 Wizardry (Mage), 89745 Mysticism (Priest), 366524 Mail
  Specialization (Evoker). The other classes get their armor specialization from SpecializationSpells, which
  is outside this system.
- **Critical Strikes** +5% (aura 290): 157442 (Rogue), 157443 (Hunter).
- **Parry capability** (raw 22 → `SetCanParry`, `SpellEffects.cpp:3703-3710`): 3127 Warrior, 82245 Rogue,
  116812 Monk, 82246 DK, 82242 Paladin, 203724 DH.
- **Block** (aura 51): 123829 Warrior, 123830 Paladin, 123831 Shaman.
- Resource and mechanic drivers: 121039 Mana Attunement, 246985 Soul Shards, 107500 Monk Energy Driver,
  157361 Roll Speed Controls, 197147 Festering Wound, 51986 On a Pale Horse, and others.
- Skill 183 also carries per-class "Weapon Skills" / "Armor Skills" dummies (76249…366522) and **75 Auto Shot
  (Hunter, SLA 32924)**.

### 5.3 Weapon proficiency

- **Held skill** (the equip gate): SRCI category-6 rows. Weapons per class are identical across every race of
  that class (checked over all pairs). The one exception is DK skill 118, below.
- **Spell** (a client mirror): the SLA method-2 row on each weapon skill, for example 252 → 201 One-Handed Swords
  (raw 25 + raw 60, item class 2, subclass mask 128).
- Consumers: equip gate `Player.cpp:11155-11179` (`GetSkillValue(pItem->GetSkill()) == 0` →
  PROFICIENCY_NEEDED); subclass → skill table `Entities/Item/ItemTemplate.cpp:99-106`; raw 60 handler
  `SpellEffects.cpp:1845-1864` writes `m_WeaponProficiency` and sends `SMSG_SET_PROFICIENCY`. The server never
  reads `m_WeaponProficiency` (the only readers are that handler and `Player.h:1639`).
- **Dual wield is a different fact.** Skill 118 alone grants nothing. The capability is the raw-40 cast of
  674 Dual Wield (`SpellEffects.cpp:2237-2242`). Default skill 118 goes to Hunter, Rogue and DH through
  SRCI 131/1661, where SLA 610 grants 674. SRCI 913 gives skill 118 to 14 of 24 DK races, but SLA 610's class
  mask excludes class 6, so those DKs hold the skill without 674. Frost gets 674 from its specialization spells.
  SRCI 883 (Shaman) is Availability 2 and is never read.

### 5.4 Armor proficiency: where the capability actually comes from

Three current-data facts disagree, so each is stated separately:

| fact | source | Warrior example | consumer |
|---|---|---|---|
| armor **skill held** | SRCI 148 Cloth (all), 147 Leather, 146 Mail (ClassMask 16419), 21 Plate (16387, MinLevel 1), 246 Shield | cloth, leather, mail, plate, shield | **equip gate**, `Player.cpp:11155-11179` via `ItemTemplate.cpp:108-111` (subclass 1-4 → 415/414/413/293, 6 → 433). Range MONO, value 1 (`ObjectMgr.cpp:9016-9017`) |
| armor **proficiency spell** | SLA 4999/4998/26238/4141/5006 → 9078/9077/119811/750/9116 (raw 60, item class 4) | same set | `SpellEffects.cpp:1845-1864`: bitmask plus client packet only |
| **class armor type** | `ChrClasses.ArmorTypeMask` | 113 = Misc, Plate, Cosmetic, Shield | need-roll only (`CanRollNeedForItem`, `Player.cpp:11282-11286`); one script (`scripts/Spells/spell_item.cpp:914-920`) |

So in the pinned consumer, whether a character may equip armor is **authored by SRCI (skill) plus Trinity's
hardcoded subclass→skill table**. `ArmorTypeMask` holds only the class's primary armor type, so it is not a
proficiency list (Warrior lacks Cloth, Leather and Mail there). Heirloom armor has one more hardcoded exception
(`Player.cpp:11161-11175`). **UNPROVED:** that retail's server enforces equip through the skill value. The DB2
data and this one consumer agree, but no retail server code is available. The client may also use the
effect-60 bitmask, and that cannot be checked here.

### 5.5 Languages

These are Availability-1, race-masked SRCI rows in category 10 → range LANGUAGE (300/300). One method-2 SLA
row per language grants a raw-39 passive (18 auto rows): 668 Common, 669 Orcish, 1282215 Hara'ni, and so on.
Racial skill lines add "Languages" dummies (79738…). 815 Demon Tongue is class-restricted to DH through
skill 139. Languages have no combat meaning.

### 5.6 Professions and secondary tradeskills

They are represented, but none is a default. All 161 profession SRCI rows and all 50 secondary non-racial rows
are Availability 0. They are learned through trainer spells carrying raw 118 SKILL (`SpellEffects.cpp:4641-4670`
steps; `Player.cpp:2948-2994`). After that, 478 method-1 rows grant recipes as the skill value rises, and
10,544 method-0 rows are trainer, recipe or discovery associations. This path is entirely lifecycle state
(skill value, tier step, `UpdateSkillsForLevel`, and profession slots at `Player.cpp:5735-5737`). The profession
talent trees are 118 `SkillLineXTraitTree` rows, which `TraitMgr` marks as Profession configs.

### 5.7 Riding and mounts

- Riding 762 is a **level-gated default skill**. SRCI 890 (most classes, main races, MinLevel 10), 2081
  (allied races, MinLevel 10) and 934 (DK and DH, MinLevel 8) all use RANK tier 223 (value 75 at grant).
- SLA grants 33391 Journeyman / 34090 Expert / 90265 Master Riding. Each carries raw 118 on skill 762 (steps
  2/3/5), which raises the tier through `AddSpell`. Their rank chains pull in 33388 and 34091. The rows also
  grant 54197 Cold Weather Flying (method 4, no condition), 441143 Skyriding Basics, 90267 Flight Master's
  License (DH), and 367961/369536 Soar (ClassMask 413: Dracthyr of non-Evoker classes).
- Mounts 777 (SRCI 938, all, MinLevel 1) grants 48778 Acherus Deathcharger to 12 DK pairs.
- `SetSkill(SKILL_RIDING)` calls `UpdateMountCapability` (`Player.cpp:5718-5719`).
- None of this is combat state.

### 5.8 Specialization-independent combat capabilities, and the spec cascade

Among capabilities with combat meaning, the skill system authors auto attack (6603, and 75 for Hunters),
Mastery +8 (114585), Parry (class lines, 6 classes), Block (raw 23 via 107 on Defense for Warrior, Paladin and
Shaman; aura 51 class blocks), Dual Wield (skill 118 for Hunter, Rogue and DH), the class aura packages, the
armor-type +5% auras for 6 classes, Critical Strikes for 2 classes, and racial passives (§5.10). All of these
are specialization-independent: they depend only on race, class and level.

**Specialization spells feed back into the skill system** (`gates.json spec_delta`, 118 (pair, spec) rows).
Learning these spec spells installs skill 118 through raw 40, which then grants 674 through SLA 610 where the
class mask allows:

| spec spell | pairs × specs |
|---|---:|
| 124146 (Monk, every Monk spec) | 58 |
| 231842 (Warrior 72 Fury) | 31 |
| 86629 (Shaman 263 Enhancement) | 19 |
| 674 (Frost DK, the 10 races that SRCI 913 misses) | 10 |

So the specialization choice can change skill state. This is a composition of SpecializationSpells and the
skill system, not a skill-table fact.

### 5.9 Level-gated acquisition

Two independent gates apply: SRCI `MinLevel` on the skill (Riding 8/10; Plate 1, Mail 1, Mounts 1, Dual
Wield 1, Language: Cypher 1, Unused 1), and spell level on each SLA spell (class lines up to 56). Replay counts:

| level | skill-min-level gates | spell-level gates | auto rows reached | spells per pair |
|---:|---:|---:|---:|---|
| 1 | 273 | 4,376 | 416 | 94–117 |
| 10 | 0 | 2,385 | 527 | 102–138 |
| 20 | 0 | 1,213 | 570 | 106–141 |
| 80 / 90 | 0 | 0 | 623 | 107–147 |

The 72 `skill-rank` gates at every level are one row: SLA 32653 → 161211 on skill 1830 "Unused" (method 1,
rank 35 against value 1).

### 5.10 Race-only, class-only and race+class restrictions

- Race-restricted (163 auto rows): the 25 racial skill lines, languages, and riding for allied races. Examples:
  20598 The Human Spirit, 154742 Arcane Acuity, 59224 Might of the Mountain / 154743 Brawn, 6562 Heroic
  Presence, 365575 Awakened (Dracthyr, aura 318, base 1), 292751 Embrace of the Loa, 20555 Regeneration,
  436340 Titan-Wrought Frame.
- Race+class-restricted (81): 790 Goblin (SRCI 961 ClassMask 1535 excludes Monk, DH and Evoker, so **Goblin
  Monk, 9:10, is the one pair with no racial line**); Gnome Expansive Mind variants per power type (20591,
  154744-154747, 227057); **Worgen**: for 8 of 9 Worgen classes, Viciousness, Aberration, Flayer, Darkflight,
  Two Forms and Altered Form are method 0 (Learned), and only the DK rows (ClassMask 32) are method 2. Calm the
  Wolf is method 4 behind quest 24593 or "outside Gilneas". So in this consumer, non-DK Worgens get their
  racials only through quest history. Which retail system teaches them is **UNPROVED**.
- Class-restricted (301): class lines, weapon, armor and Dual Wield rows, and per-class 183 rows.

### 5.11 Other things materially represented

- **Class → talent tree**: SRCI ClassMask on category-7 lines with a `SkillLineXTraitTree` row (13 lines)
  decides which combat trait tree a class has (`TraitMgr.cpp:262-282`).
- **Trainer eligibility**: `IsSpellFitByClassAndRace` (`Player.cpp:25836-25860`, used at `Trainer.cpp:53, 178`)
  needs an SLA row whose masks pass and whose skill has an SRCI match.
- **Quest-reward vetting**: `LearnQuestRewardedSpells` casts a reward only if the learned spell has a method-4
  SLA row (`Player.cpp:25361-25369`).
- **Temporary race change**: skill lines with flag 0x8000 are swapped by
  `spell_gen_battleground_mercenary_shapeshift` (`scripts/Spells/spell_generic.cpp:482-530`).
- **Pet families**: `CreatureFamily.SkillLine` → method-2 SLA rows become pet level-up and pet passive spells
  (`SpellMgr.cpp:2152-2190, 5557-5586`). This is a pet fact, not a player fact.
- **Runeforging** (960, Availability 0, DK) has a special case at `Player.cpp:3011` and a world condition
  "outside Ebon Hold" on SLA 26154.

## 6. What "associated with a skill" does and does not mean

| relationship | how to recognise it | proved by |
|---|---|---|
| skill **available** | any SRCI row matches (race, class) | `DB2Stores.cpp:2991-3004`; `_LoadSkills` deletes skills without it (`Player.cpp:27253-27261`) |
| skill **held by default** | a matching SRCI row with Availability 1 and MinLevel ≤ level | `ObjectMgr.cpp:4064`; `Player.cpp:25259-25275` |
| spell **automatically learned** | the skill is held, the SLA row (by grant key) has method 1/2/4 and passes masks, level, rank and conditions | `Player.cpp:25391-25441` |
| spell **merely associated** | method 0 or 3, or any row on a skill the pair cannot hold | the method switch `continue`s (`Player.cpp:25417`); consumers are trainer, quest and rank code only |
| **skill installed by a learned spell** | the spell has raw 118 or raw 40, or a method-2 SLA row on an unheld skill | `Player.cpp:2948-3016` |
| **runtime raw 36** | a SpellEffect Learn Spell. Passive, talent, pet-targeted and skill-step sources are "auto" and fire only when cast; the others are dependent-learned at `AddSpell` | `SpellMgr.cpp:1057-1101`; `SpellEffects.cpp:2087-…`. **No auto-acquired skill spell in this population has such an edge** |
| **specialization / talent / loadout** | `SpecializationSpells`, `ChrSpecialization.MasterySpellID`, trait configs | `Player.cpp:30631-30646, 28788-28960`. Outside the skill tables; can feed them (§5.8) |
| **equipment eligibility** | skill value (weapon and armor categories), plus item class, race and level fields | `Player.cpp:11129-11226`; the effect-60 bitmask is not read on the server |

## 7. Skill 183, SLA 25920, spell 114585 (+8 Mastery)

**Rows** (db2-fact):

- SRCI 5: `SkillID 183, ClassMask 16383, Flags 1170 (0x492 = NO_SKILLUP_MESSAGE | ALWAYS_MAX_VALUE |
  INCLUDE_IN_SORT | MONO_VALUE), Availability 1, MinLevel 0, SkillTierID 0, RaceMasks −1/−1`.
- SkillLine 183: "GENERIC (DND)", CategoryID 12, Flags 1170, no parent.
- SLA 25920: `SkillLine 183, Spell 114585, MinSkillLineRank 1, ClassMask 0, SupercedesSpell 0,
  AcquireMethod 2, Flags 0, SkillupSkillLineID 0, RaceMasks 0/0`. It is the only SLA row for 114585.
- Spell 114585 "Mastery":
  - SpellMisc: `Attributes_0 = 464` (0x40 PASSIVE, 0x10 IS_ABILITY, 0x80 DO_NOT_DISPLAY, 0x100 DO_NOT_LOG), no
    ShowFutureSpellPlayerCondition, no duration.
  - SpellLevels 20769: all zero.
  - Only effect is SpellEffect 127950: `Effect 6, EffectAura 318, BasePoints 8, Misc 0, ImplicitTarget 1`.
  - No SpellShapeshift, SpellAuraRestrictions, SpellEquippedItems, SpellCastingRequirements, SpellClassOptions
    or SpellLabel rows.
  - No reference in Trinity `src/`, and no spell_* row in the TDB. The 32 textual matches in the dump are
    creature and waypoint numbers.

**Why every character**: ClassMask 16383 sets bits for classes 1-14 and the race mask is all. ObjectMgr puts
SRCI 5 into all 281 `PlayerInfo`s (`ObjectMgr.cpp:4063-4070`). The empty masks on SLA 25920 and the zero spell
level mean no gate can fail (`Player.cpp:25421-25432`). Replay: 281/281 pairs, at every level from 1.

**At which stage**:
1. **Creation.** `Player::Create` → `InitStatsForLevel` (`:490`), then `InitializeSkillFields` (`:493`), then
   `LearnDefaultSkills` (`:505`). Range is LEVEL (category 12, no tier, `ObjectMgr.cpp:9002-9025`); flag 0x10
   sets the value to max, level×5 (`Player.cpp:25285-25294`). Then `SetSkill` → `LearnSkillRewardedSpells` →
   `AddSpell(114585, fromSkill 183)` (`:25438-25440`). The spell is passive with no stance, equipped-item or
   aura-state requirement, so `HandlePassiveSpellLearn` returns true and the spell is cast on self
   (`:2920-2929, 3079-3101`) → aura 318 +8. This happens **before** the default specialization is set, and
   before any specialization spell or trait.
2. **Login.** `LoadFromDB` runs `_LoadSkills` (`:18591`), which restores 183 and calls
   `LearnSkillRewardedSpells` (`:27333-27336`), then `_LoadSpells` (`:18609`), `LearnSpecializationSpells`
   (`:18612`) and `_LoadAuras` (`:18615`). `LearnDefaultSkills` at `:18644` is then a no-op, because the skill
   is already held (`HasSkill`). 114585 is added again through the skill path before auras load, and as a
   passive it is re-cast on add.
3. **Level-up** (`GiveLevel`, `UpdateSkillsForLevel`): the skill max is raised again; the spell is unchanged.

**What the +8 does**: `UpdateMastery` (`Entities/Unit/StatSystem.cpp:542-556`) returns 0 unless
`CanUseMastery()`, which needs the primary specialization's `MasterySpellID` to be known
(`Player.cpp:30525-30530`). That spell is in `SpecializationSpells` for all 40 specializations that have one.
Its SpellLevel is 10, except 1468 Preservation, which has no SpellLevels row. It is learned by
`LearnSpecializationSpells` (`:30638`). Only then is the value `GetTotalAuraModifier(SPELL_AURA_MASTERY) +
rating`. So 114585 is **acquired unconditionally at the cold default-skill stage**, and its **effect on the
Mastery field is conditional on the specialization's mastery spell** at level 10 or above. The static trace
is replayed here. A runtime Trinity character dump showing Mastery = 8 + rating was not produced and stays
**UNPROVED** (as in `character-preparation-closure.md` §9.3).

**How Core describes it today** (read only, `../core@201f96b`, dirty tree):
`crates/dbc/src/aura_subtype.rs:564` admits 114585 as "one host-asserted acquired spell", and says "Core does
not derive acquisition". The acquisition fact that Core takes from the host is exactly the SRCI 5 → SLA 25920
chain above.

## 8. Relationships similar to 114585, ranked by breadth only

Every row below has the 114585 shape: an **SRCI default skill → method-2 SLA → passive spell** whose effects
are character baseline, with no runtime trigger. The rows are ordered by the number of current characters
((race, class) pairs, of 281) they reach, then by classes and specializations (40 with mastery). This ranking
measures breadth and reuse only. It is **not** an implementation priority and makes no claim that Core should
consume any of it.

| # | relationship (SRCI → SLA → spell) | pairs | classes | specs | effect family |
|---:|---|---:|---:|---:|---|
| 1 | 5 → 25920 → **114585** Mastery | 281 | 13 | 40 | aura 318 +8 |
| 2 | class line (one per class) → **class aura package** (137047, 137014, 137018, 137030, 137034, 137042, 137005, 137009, 137026, 137038, 137022+130610, 212611, 353167) | 281 (one package per pair) | 13 | 40 | multi-effect: aura 416/417/457/379/108/51/… (spell-list, label and power modifiers) |
| 3 | 5 → 3999 → **6603 Auto Attack** | 281 | 13 | 40 | active capability, no aura |
| 4 | weapon and armor SRCI (category 6 and 8, plus 183 generic) → proficiency skill **values** (plus raw-60 client mirrors) | 281 | 13 | 40 | equipment eligibility, per-class uniform (§5.3/5.4) |
| 5 | racial skill line → **racial passives** (25 lines) | 280 (Goblin Monk has none) | 13 | 40 | per race: stat, crit, crit damage, versatility, mastery (365575 +1), resistances, profession skill |
| 6 | class line → **armor-type +5% primary stat** (86091, 86092, 86538, 89744, 89745, 366524) | 157 | 6 | 18 | aura 137 |
| 7 | class line → **Parry** capability (3127, 82245, 116812, 82246, 82242, 203724) | 127 | 6 | 18 | raw 22 → `SetCanParry` |
| 8 | 118 (SRCI 131/1661) → 610 → **674 Dual Wield**; plus the spec cascade (§5.8) | 64 default (+118 spec-induced pair×spec rows) | 3 default (+4 via spec: Monk, Warrior, Shaman, DK) | 9 default (+6 via spec) | raw 40 → `SetCanDualWield` |
| 9 | Defense 95 → 107 **Block** (Warrior, Paladin, Shaman) and aura-51 class blocks | 60 | 3 | 9 | raw 23 → `SetCanBlock`, aura 51 |
| 10 | class line → **Critical Strikes** +5% (157442 Rogue, 157443 Hunter) | 62 | 2 | 6 | aura 290 |
| 11 | 183 → 32924 → **75 Auto Shot** | 31 | 1 | 3 | active capability |
| 12 | 2808 Dracthyr racial → **365575 Awakened** (+1 Mastery) | 14 | 7 | 21 | aura 318, the same shape as 114585 |

Two adjacent relationships sit **outside** the skill tables but have the same cold shape: the specialization
mastery spell (`ChrSpecialization.MasterySpellID`, all 40 specs) and SpecializationSpells passives, such as
the armor specializations for Warrior, Paladin, DK, Shaman, Druid, Monk and DH. They are keyed by
specialization rather than by (race, class, level).

## 9. Boundary map: cold preparation vs runtime or lifecycle

**Suitable for immutable cold preparation** — a pure function of (race, class, level, and for §5.8 spec) and
the pinned DB2 plus two world tables:
- the default-skill set and each skill's value and max: SRCI Availability, masks, MinLevel and Flags 0x10;
  SkillLine category; `skill_tiers` for Riding and 1830;
- auto-acquired spells: SLA methods 1 and 2, method 4 with no condition, masks, `max(SpellLevel, BaseLevel)`,
  rank chains, and `SpellLearnSpell.db2` / `spell_learn_spell` dependents;
- the presence of 114585, the class aura packages, racial passives, armor-type auras, Critical Strikes, auto
  attack and auto shot;
- capability flags set by effects of those passives (Parry, Block, Dual Wield). Each flag is static once the
  passive is known, but Trinity sets it only when the passive is cast (`HandlePassiveSpellLearn`);
- equipment eligibility by skill (weapon and armor), and languages;
- the class → talent-tree mapping, and the specialization-spell cascade into skill 118.

**Needs runtime or lifecycle semantics** (or host state):
- method-4 rows with PlayerCondition 83446 or 94589 (map, ContentTuning) or world conditions (zone, quest
  completion): 32 rows;
- method-0 grants (trainer, quest, or items such as 296087 Arms Dual Wield) — the non-DK Worgen racials are an
  example — and every profession or secondary skill (Availability 0, raw-118 steps, method-1 rank growth);
- level change (`GiveLevel`, `UpdateSkillsForLevel`), specialization change, and the `CanUseMastery` gate on
  114585's contribution;
- passive application conditions: stance or shapeshift form, equipped-item class and caster aura state
  (`Player.cpp:3079-3101`). **UNPROVED** for the auto set: this pass did not check every one of the 267 passives
  for stance or equipped-item fields. 114585 is checked;
- temporary race change (flag 0x8000), pet families, mounts and riding capability, and the saved skill and
  spell history that `_LoadSkills` and `_LoadSpells` revalidate.

## 10. Not proved from current source plus pinned consumers

1. Retail server behaviour. Every semantic claim is Trinity-at-pin plus DB2. In particular: that equip
   eligibility is skill-based (§5.4), that method-0 racials (Worgen) are quest-taught, and what Availability 2
   means (Trinity never reads it).
2. Runtime values. The replay is static, and no Trinity character was created. The +8 in the Mastery field is
   inferred from `UpdateMastery`, not observed.
3. PlayerCondition 83446 and 94589 are not evaluated here. The 32 method-4 rows stay conditional.
4. The order within `unordered_multimap` equal ranges, for the five multi-row skills (all Availability 0,
   §2.2).
5. Stance, equipped-item and aura-state gates on the 267 auto passives (§9), other than 114585.
6. Client use of the effect-60 proficiency bitmask, `ItemSubClass.Prerequisite/PostrequisiteProficiency`, the
   SkillLine UI flags, and the "Weapon Skills" / "Armor Skills" dummies. These have no server consumer.

## 11. Navigation

- Explain one pair: `jq '.pairs["<race>:<class>"]' docs/research/skill-acquisition-corpora/pairs.json`. Every
  skill shows its path (`default:SRCI n`, or `spell X (SLA y, AutomaticCharLevel)`), and every spell shows its
  (`skill S / SLA n`, rank chain or dependent edge).
- Find who gets a spell: `jq '.rows[]|select(.spell==<id>)' docs/research/skill-acquisition-corpora/sla-rows.json`
  (`auto_pairs`, `auto_scope`).
- Related reports: `character-preparation-closure.md` (§9.3, E5, R3-5), `combat-preparation-synthesis.md`,
  `character-stat-pipeline-archaeology.md`.
