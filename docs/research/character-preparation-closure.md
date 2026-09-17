# Character preparation closure

## Scope and result

This pass answers:

> Given a Retail character description or export, what exact semantic inputs are required to
> construct Core's initial Player state, and which of them are derivable from existing data
> and research?

It is semantic preparation archaeology, not a website integration. It joins the existing
gearing, character-stat, proc and Dummy research (imports, not copies), adds the controlled-unit
and weapon hooks from the two sibling reports, defines a provider-neutral fixture, and ships a
fail-closed fixture compiler plus a Trinity differential. Character preparation is kept
strictly separate from encounter initialization.

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
| world DB | `TDB_full_world_1200.26021_2026_02_06.sql` + update replay, via `docs/research/world-db-corpora/player-base-stats.json` |
| compiler for probes | `g++ 13.3.0 -std=c++20 -O0 -ffp-contract=off` |

| deliverable | path |
|---|---|
| this report | `docs/research/character-preparation-closure.md` |
| research package / CLI | `scripts/research/character_prep/`, `scripts/research/character_prep.py` |
| corpora | `docs/research/character-prep-corpora/` (input-inventory, dependency-graph, initial-state, fixture-schema, server-inputs, base-stat-gap, differential, unknowns) |
| fixtures | `docs/research/character-prep-corpora/fixtures/` (12 synthetic fixtures + compiled output + `index.json`) |
| base-stat extract | `docs/research/world-db-corpora/player-base-stats.json` |
| differential C++ probe | `scripts/research/tools/tc_prep_probe/` |
| tests | `scripts/research/tests/test_cp_*.py` (237 tests after the closeout review, §15) |

Sections 1–6 (part E) cover the input inventory, dependency graph, initial state, fixture format,
compiler and fixtures; sections 7–10 (part F) cover server-authored inputs, the base-stat gap and
the Trinity differential.

**Headline results.**

1. **The identity of a character is small, but not everything in it is modelled yet.** It consists of:
   - build provenance, race, class, spec and level;
   - a set of `(TraitNodeEntryID, rank)` plus the hero sub-tree;
   - equipped item instances per slot (item, context, bonus lists, gems, enchants, modifiers);
   - **learned-spell state that no automatic path grants** (§14). Since the closeout review it is
     the optional `learned_spells` section (§4, §15);
   - controlled-unit state (hunter stable slot / family / spec).

   Three character-DB inputs that `Player::LoadFromDB` restores are **not** in the format yet. They
   are listed as unresolved identity gaps: glyphs, saved skill values, and quest/trainer spells
   beyond `learned_spells` (§1, R3-2..R3-4). Stats, set membership, weapon configuration, armour
   specialization and mastery are derived and must never be requested (§1).
2. **Trait validity is decidable from spec, level and the config alone** for the current class
   trees. Trinity picks one tree per class from the class skill line (`SkillLineXTraitTree`), and
   saved ranks exclude granted ranks (`TraitMgr.cpp:262-354, 766-832`, `Player.cpp:28533-28553`).
   The claim rests on a census: no class-tree condition reads quests, achievements, account
   elements or non-trait currencies (E3, re-derived in §15) (§5).
3. **Default-skill learning is a distinct acquisition path** that the existing current-player
   scope does not follow. It supplies Dual Wield 674 (skill 118) and a universal +8 Mastery
   passive 114585 (skill 183) (`ObjectMgr.cpp:4059-4072`, `Player.cpp:25259-25441`,
   `StatSystem.cpp:549-550`). Armour specialization for six classes also comes from the class
   skill line, not from specialization spells (§3, §5).
4. **Base primary stats come from the pinned TDB for levels 1–80 only.** Trinity serves levels
   81–90 as an unchanged **copy of level 80** (`ObjectMgr.cpp:4349-4356`); the Retail values are
   **unresolved**. The compiler never interpolates. Trinity's fill rule is an explicit, labelled
   opt-in (`server_inputs.base_stats.fill_rule`), and every filled cell is reported as unresolved
   Retail truth (§8).
5. **Every current-season item requires level 90** (ItemBonus 49 → `ItemScalingConfig.RequiredLevel`).
   `Player::CanUseItem` refuses such an item below level 90 (`Player.cpp:11148-11149`), and
   `_LoadInventory` mails it (`Player.cpp:19295-19299`). The ten original level-80 fixtures were
   therefore not loadable characters, although they reported `validation.ok`. The compiler now
   checks the required level and the rest of `CanUseItem`, and the synthetic fixtures are level 90
   (§5, §6, §15).
6. **No server rate scales a prepared maximum.** Only `MaxPlayerLevel` and the disabled
   `Stats.Limits.*` shape prepared state (§7).
7. **Trinity differential:** 25 witnesses, 1,389 compared records and 36 mismatches, all classified:
   - 25 are the missing +8 mastery in the existing character-stat oracle;
   - 10 are armour-spec scope differences between the compiler and that oracle;
   - 1 is a reachable binary32 truncation. Armour specialization's 5 % is `1.0499999523f`, so 4,045
     totals below 120,000 land one point lower in Trinity (§9).
8. **The fixture format carries observations now.** An `observations` list is keyed by JSON
   pointer into the derived state and records observer, build, units and precision, so a future
   Retail WASM/V8 oracle can attach values without a redesign (§4).
9. **Health-dependent passives start one update late** (`Unit.cpp:474-483, 6078-6098`) (§3).
   "Full health at start" is an encounter convention: no Trinity construction path refills health
   after gear is applied, except `GiveLevel` (§3, R3-6).

## 0. Part conventions

### Part E

Pins: snapshot Wago `12.1.0.69497`; TrinityCore `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`; world DB `TDB_full_world_1200.26021_2026_02_06.sql`
(through `docs/research/world-db-corpora/player-base-stats.json`). Coordinates are relative to
`TrinityCore/src/server/game/` unless another root is named. Code: `scripts/research/character_prep/{fixture,inputs,graph,compiler,report}.py`.
Corpora: `docs/research/character-prep-corpora/{input-inventory,dependency-graph,initial-state,fixture-schema}.json`, `fixtures/*.json`.


Headline results:

| # | finding | evidence | coordinates |
|---|---|---|---|
| E1 | A Combat trait config loads **exactly one tree per class**: the `SkillLineXTraitTree` tree of the class's category-7 skill line. `TraitTreeLoadout` does not select trees, and TraitMgr's per-spec loadout map has **no consumer**. Fury's newest `TraitTreeLoadout` (954) points at tree 880, which Trinity never loads for warriors. | trinity-consumer + db2-fact | `Spells/TraitMgr.cpp:262-284`, `:338-354`, `:300-309` |
| E2 | The saved trait rank **excludes granted ranks**. Granted entries are computed on an **empty** config (rank 0), and the saved `Rank` then overwrites them. `rank + granted > MaxRanks` is invalid, and the load path silently zeroes such a rank. The previous draft's "9 of 10 fixtures fail" came from counting granted ranks twice. | trinity-consumer | `Entities/Player/Player.cpp:28533-28553`; `Spells/TraitMgr.cpp:766-820`, `:822-836`, `:906-926`; `Handlers/TraitHandler.cpp:191-192` |
| E3 | Across the 13 class trees (2,896 nodes, 734 distinct `TraitCond` rows), **no condition uses a QuestID, AchievementID or account element**. The 44 quest/achievement `TraitCurrencySource` rows of their 12 currencies all have `Amount` 0. Trait validity is therefore fully decidable from spec + level + config. | db2-fact | `Spells/TraitMgr.cpp:605-609`, `:446-450` |
| E4 | `TraitTreeLoadoutEntry.NumPoints` is the **cumulative** rank at each `OrderIndex` step, and the authored starter builds spend the **level-90** budget: 34 class + 34 spec points, against 31/30 owned at level 80. At level 80 the tiered apex node fails its level conditions. | db2-fact + trinity-consumer | `Spells/TraitMgr.cpp:409-473`, `:681-764` |
| E5 | **Default-skill grants are a distinct acquisition path** (outside spec spells, traits, gear and the category-7 scope). Default-skill acquisition was ported (Availability 1 → range type → AcquireMethod 1/2/4 → race/class mask → level). Dual Wield 674 reaches **rogues, hunters and demon hunters** (classes 4/3/12) through skill 118 (category 6), and Mastery 114585 reaches **every** player through skill 183. `charstats.racial_abilities` returns 37 extra spells for a Human Warrior (AcquireMethod 0, never auto-learned) and raises for races 70/84/85/86/91. | trinity-consumer | `Globals/ObjectMgr.cpp:4059-4072`, `:9002-9023`; `Entities/Player/Player.cpp:25259-25321`, `:25391-25441` (switch `:25404-25418`) |
| E6 | `RaceMask` bits are **not** `race_id - 1` for races ≥ 34 (34→11, 35→12, 36→13, 37→14, 52→16, 70→15, 84→17, 85→18, 86→20, 91→19). `ChrRaces.PlayableRaceBit` agrees with Trinity's table for all 31 checked races. `charstats.acquisition.racial_abilities` uses `race_id - 1`. | trinity-consumer + db2-fact | `Miscellaneous/RaceMask.h:97-146`; `scripts/research/charstats/acquisition.py:260-266` |
| E7 | Armour specialization for **Rogue 86092, Hunter 86538, Warlock 86091, Mage 89744, Priest 89745 and Evoker 366524** arrives through the class skill line, not through `SpecializationSpells`. charstats' discovery sees only `SpecializationSpells`, so it applies nothing for 6 classes. The compiler reuses charstats' rule on the whole acquired set; the gate is now closed for all 10 fixtures (charstats blocker 5). | db2-fact + trinity-consumer | `Entities/Player/Player.cpp:26203-26213`, `:8280-8300`; `Entities/Item/Item.cpp:1477-1506` |
| E8 | A passive gated by a health-derived `CasterAuraState` (21/23/24 hold at full health) is **not** cast on learn. It is cast on the first `Unit::Update` through `ModifyAuraState`. Example: Priest 373456 (state 23). ENRAGED (17) stays gated (Fury 76856, 392931). | trinity-consumer | `Entities/Player/Player.cpp:3102-3103`; `Entities/Unit/Unit.cpp:474-483`, `:6078-6098` |
| E9 | `Player.cpp:2225-2257` is **`Player::GiveLevel`**, not `Player::Create`. `Create` fills the powers flagged `SetToMaxOnInitialLogIn` (0x2000) at `:500-502`. `SetToMaxOnLevelUp` (0x1000) is the level-up rule. | trinity-consumer | `Entities/Player/Player.cpp:387`, `:490-506`, `:2185`, `:2254-2257`; `DataStores/DBCEnums.h:2294-2308` |
| E10 | All 12 synthetic fixtures compile with `validation.ok`. They are 10 plan fixtures, the Arms learned-spell dual-wield witness, and a variant without the fill rule. All are level 90 since the closeout review (§15). The variant without the fill rule reports `/derived/stats` as unresolved (`level-row-missing`, never interpolated). The other 11 opt into Trinity's fill rule and report the Retail base stats as unresolved. Output is byte-deterministic across runs. *(Before the review, the 10 level-80 fixtures passed only because the compiler did not check the item required level; R3-1.)* | differential (self) | `fixtures/index.json` |

---

## 1. Canonical input inventory

Corpus: `input-inventory.json` (58 rows: identity 21, derived 12, server 7, encounter 10, excluded 8; closeout review §15 added 7). Each row has
`name, kind, required, fixture_path, derived_path, source_tables, consumer, evidence_class, derivation_research, notes`.
A test resolves every `derived_path` in a compiled fixture and checks that every `fixture_path` names a schema section. An
identity row without a `fixture_path` must be `unresolved`: it is a format gap, not an input the format carries.

| kind | inputs (abridged) | why |
|---|---|---|
| **identity** (required) | race, class, spec, level (1..90, `World/World.cpp:750`); trait selections `{node_entry_id, rank}`; equipment slot → `LoadoutEntry` | nothing derives them. The slot is identity because one ItemID can be MAINHAND or OFFHAND (`Player.cpp:9243-9249`). |
| **identity** (optional) | active hero sub-tree; PvP talents; **explicitly learned spells** (`learned_spells`, the `character_spell` rows no automatic path reproduces); per item: context (difficulty), bonus lists, gems, enchants, keystone level, PvP tier; controlled-unit section (hunter pet family / pet spec / creature, warlock demon); options | Difficulty is `ItemContext` **inside each equipment entry**, not encounter state. PvP talents are acquired only with `options.enable_pvp_talents` (`Player.cpp:27675-27702`, `:27730`, `:28467`). Learned spells are restored by `_LoadSpells` (`Player.cpp:18609`) → `AddSpell`; 3,789 AcquireMethod-0 class-line abilities are never auto-learned (`Player.cpp:25404-25417`). |
| **identity**, format gap (unresolved) | glyphs (`_LoadGlyphs` / `_LoadGlyphAuras`, `Player.cpp:18614-18616`, `:19110-19114`); saved skills and skill values (`_LoadSkills`, `Player.cpp:18591`, `:27228-27345`) | The fixture has no section for them. 686 glyph spells carry non-cosmetic auras (PROC_TRIGGER_SPELL, ADD_FLAT_MODIFIER, DUMMY …). Saved skills of any availability teach their AcquireMethod 1/2 spells on load, and item `RequiredSkill` reads them. The compiler assumes default-skill values only (R3-3, R3-4). |
| **identity**, unresolved semantics | crafted stat modifiers | expressible only as bonus lists. `ITEM_BONUS_MODIFIED_CRAFTING_STAT = 25 /*NYI*/` (`DataStores/DBCEnums.h:1273`). The snapshot has no `ItemModifiedCraftingStat`. |
| **derived** (never asked for) | trait tree / node / granted ranks / owned-spent currency; item level, stats, ratings; set membership + thresholds; weapon configuration (dps, delay, DW/TG gates); gem/enchant payloads; acquired spells; passives active at preparation; proc providers / marker Dummy auras; mastery; armour specialization; stats, max health / power; initial health / power / cooldown / charge state | `gearing`, `charstats`, `procs`, `dummy_semantics`, plus the E ports (`TraitEngine`, `default_skill_spells`) |
| **server** | `player_classlevelstats` + `player_racestats` (required, levels ≤ 80 only); the explicit fill-rule opt-in (`server_inputs.base_stats.fill_rule`); `playercreateinfo` pair (`Player.cpp:396-402`); MaxPlayerLevel / Stats.Limits; `skill_tiers` (SKILL_RANGE_RANK default skills, `Globals/ObjectMgr.cpp:9008-9009`); `conditions` for AcquireMethod 4 (`Player.cpp:25409-25415`); spell_* tables (other tracks) | The values and gaps are **agent F's** (`server-inputs.json`, `base-stat-gap.json`). |
| **encounter** | consumables; temporary weapon enchants; raid buffs and lust; target and position; PvP talent activation (`Player.cpp:27778`, `:27856`); PvP item-level context (`Entities/Player/Player.h:2754`, `Player.cpp:30928-30929`, `Entities/Item/Item.cpp:2270`); area item-level scaling (`Player.cpp:30768`); saved auras (`_LoadAuras`, `Player.cpp:18615`); saved mid-fight health and cooldowns; RNG seed | state of a fight or host |
| **excluded** | appearance (`Player.cpp:415`, `:471`, `:18186-18217`); loadout string (opaque provenance, never decoded); legacy azerite and artifacts; transmog, titles, names; rewarded quests / achievements / currencies (E3); reputation (no current-gear item has `MinFactionID`; the compiler reports any such item as unresolved); action bars, equipment sets, mail, garrison; professions | not combat preparation |

**Legacy azerite (legacy-only).** `ApplyAllAzeriteItemMods` (`Player.cpp:8965`) → `ApplyAzeritePowers` (`:8492-8514`).
Empowered-item powers apply only while `ITEM_ID_HEART_OF_AZEROTH` = 158075 (`Entities/Item/AzeriteItem/AzeriteItem.h:23`) is
equipped (`Player.cpp:8509`). The current-gear corpus does list **14 `AzeriteEmpoweredItem` items** among the M+ rotation's
legacy dungeon loot, but the heart is not in the corpus, so those powers stay inert. Input needed to reach it: a fixture that
equips 158075, plus the azerite power selections and heart level (character DB).

## 2. Dependency graph

Corpus: `dependency-graph.json` (26 nodes, 41 edges, acyclic, topologically ordered; before the closeout review 25 nodes and 40 edges — the "51 edges" printed here was wrong). CLI: `character_prep.py graph [--dot|--json]`.

The graph has four node kinds:
- **input**: the 6 fixture sections (identity, traits, pvp_traits, learned_spells, equipment, controlled_units) and `/server_inputs/base_stats`.
- **derived**: the 10 compiler nodes.
- **research**: `gearing.loadout`, `charstats.character`, `procs.spells` (+ `procs-corpora/census.json`), `dummy_semantics.loaders` (+ `dummy-corpora/markers.json`, `bindings.json`), and the E ports.
- **hook**: `controlled_units.ownership.CHARACTER_PREP_HOOK` and `weapon_combat.weapon.CHARACTER_PREP_HOOK`.

Research and hook modules are imported lazily. An import failure, a missing attribute or a missing corpus marks the node
`unresolved`, and every derived node computed by it inherits that state (tested with monkeypatched module names).

| derived node | derived from | computed by | evidence | consumer |
|---|---|---|---|---|
| `/derived/identity` | `/fixture/identity` | charstats | db2-fact | `Player.cpp:387` |
| `/derived/traits` | `/fixture/traits`, identity | TraitEngine | db2-fact | `Spells/TraitMgr.cpp:838-1015`, `Player.cpp:29380-29409` |
| `/derived/pvp_traits` | `/fixture/pvp_traits`, identity | compiler | db2-fact | `Player.cpp:28467` |
| `/derived/gear` | `/fixture/equipment`, identity | gearing | db2-fact | `Player.cpp:7847`, `:13417` |
| `/derived/spells` | `/fixture/learned_spells`, traits, gear, pvp_traits, identity | charstats, procs, dummy markers, default skills | trinity-consumer | `Player.cpp:30631-30648`, `:25259-25441`, `:3079-3104`, `:18609` |
| `/derived/armor_specialization` | gear, spells | charstats (rule) | trinity-consumer | `Player.cpp:26203-26213` |
| `/derived/stats` | gear, armor_specialization, base_stats, identity | charstats (+ agent F's fill-rule adapter when opted in) | trinity-consumer (base cells `trinity-consumer(fill-rule)` when filled) | `Entities/Unit/StatSystem.cpp:198-345` |
| `/derived/controlled_units` | `/fixture/controlled_units`, spells, stats | **hook (absent → unresolved)** | unresolved | Track A |
| `/derived/weapon` | gear, stats | hook `weapon_combat` (**present**, `trinity-probe`), gearing | structural-inference | `Entities/Unit/Unit.cpp:326` |
| `/derived/initial_state` | stats, spells, gear, identity | compiler | trinity-consumer (health: structural-inference convention) | `Player.cpp:2323-2498`, `:490-502`; `Spells/SpellHistory.cpp:147-179` |

The graph's derived-from edge set equals the `dependencies` recorded by every compiled fixture (test
`test_compiler_dependencies_match_graph_edges`).

## 3. Initial-state inventory

Corpus: `initial-state.json`. It holds 9 classes plus the PowerType / ChrClassesXPowerTypes facts (db2-fact: 21 PowerType rows).
Per fixture: `character_prep.py initial-state --fixture F` → `derived.initial_state`.

| class | content | consumer |
|---|---|---|
| immutable facts | race/class/spec/level, items + resolved stats, trait config, server base stats | `Player.cpp:17925` LoadFromDB, `:2323` InitStatsForLevel |
| compiled program | spec spells (SpellLevel ≤ level), mastery spell, **applied** trait entries (selected) out of the one class tree (possible), default-skill spells, gear roots, PvP talents | `Player.cpp:30631-30648`, `:29380-29409`, `:25259-25441`, `:8315` |
| mutable combat state | health **full at the prepared maximum**. This is a convention (structural-inference): `InitStatsForLevel` (`:2487`) and Create (`:497-498`) fill health before gear is applied; `Unit::SetMaxHealth` never refills (`Entities/Unit/Unit.cpp:10016-10030`); login restores the saved value (`:18708`); only GiveLevel fills after stats (`:2254`). Powers: InitStatsForLevel fills mana/energy/focus (`:2488-2492`), leaves rage unchanged but clamps it if above max (`:2490-2491`), zeroes runic power (`:2493`). Create then fills every PowerType flagged SetToMaxOnInitialLogIn (`:500-502`). GiveLevel fills SetToMaxOnLevelUp (`:2254-2257`). Login restores saved values clamped to max (`:18708-18723`) and zeroes lunar power (`:18725`). Max = `GetCreatePowerValue` (`Entities/Unit/StatSystem.cpp:90-99`: mana → BaseMp, else `PowerType.MaxBasePower`) → `UpdateMaxPower` `lroundf` (`:331-345`) | as listed |
| active-at-start passives | cast on learn (`Player.cpp:2792-2794`, `:2916-2929`) → `HandlePassiveSpellLearn` (`:3079-3104`). Stance gate (`:3083-3085`); equipment gate for aura spells: AddAura only if the item fits (`:3089-3099`, `:26158-26221`, `Entities/Item/Item.cpp:1477-1506`); CasterAuraState (`:3102-3103`; E8); set thresholds; marker Dummy auras (dummy corpus) | `derived.initial_state.value.auras` |
| cooldowns / charges | All ready: `SpellHistory::LoadFromDB` replays only persisted rows (`Spells/SpellHistory.cpp:147-179`). This holds on the load path (`QuickEquipItem`). A host that equips through `Player::EquipItem` starts a 30 s cooldown on on-use item spells (`Player.cpp:25146-25186`, R3-9). Charges full: `GetMaxCharges` = `SpellCategory.MaxCharges` + `SPELL_AURA_MOD_MAX_CHARGES` (`:964-973`). The compiled `max_charges` includes the aura term of passives active at preparation since the closeout review; before it, e.g. Arms Overpower 7384 (category 1680, +1 from 385571) was one charge short (R3-7). | trinity-consumer (aura sum: structural-inference) |
| weapon / swing | `BASE_ATTACK_TIME` for every attack type (`Player.cpp:2394-2395`); weapon delay on equip (`:5396`); `m_attackTimer = {}` = ready (`Entities/Unit/Unit.cpp:326`) | Track B |
| pet state | hook | Track A (unresolved) |
| RNG identity | not consumed by preparation; `options.rng` is reserved and must be null | structural-inference |
| encounter-only | target, position, buffs, consumables, lust, PvP item levels and activation, area scaling | — |

Item context from difficulty is an **identity input inside the loadout** (`/equipment/<slot>/context`), not encounter state.

**Default skills (E5).** The port covers these steps:
- **Skill list.** `SkillRaceClassInfo.Availability == 1`, RaceMask empty or `HasRace`, and ClassMask −1/0/bit (`Globals/ObjectMgr.cpp:4059-4072`). The first row per skill wins (`HasSkill`, `Player.cpp:25267`), and `MinLevel ≤ level` (`:25270`).
- **Skill value by range type** (`Globals/ObjectMgr.cpp:9002-9023`). LANGUAGE → 300. MONO → 1. LEVEL → 1, or `level*5` with SKILL_FLAG_ALWAYS_MAX_VALUE (`Entities/Unit/Unit.h:931`); the DK formula applies to class 6. RANK needs world-DB `skill_tiers`.
- **Abilities** (`Player.cpp:25391-25441`). AcquireMethod 1/2 are learned, 4 is reported as unresolved, and everything else is skipped (`:25416-25417`). The race and class masks must match, `max(SpellLevel, BaseLevel)` must be ≤ level, and AutomaticSkillRank needs `value ≥ MinSkillLineRank`.
  - AcquireMethod 4 splits into two groups (R3-5).
    - Spells without `ShowFutureSpellPlayerConditionID` (54197, 50977) are learned unless a `conditions` row of source type 35 exists. The pinned TDB has 14 such rows and none for these two.
    - The others need PlayerCondition 83446: not on the NPE maps 2175/2236/2261/2369, plus ContentTuning 958. That is host location and level.
  - The compiler keeps both groups unresolved and names the reason for each spell.
  - The port reads `SkillTierID ≠ 0` as SKILL_RANGE_RANK. Trinity requires the tier to exist in world `skill_tiers` (`ObjectMgr.cpp:9008`).

**Default-skill grants are a distinct acquisition path.** They are neither spec spells, nor trait spells, nor gear, nor `dummy_semantics.scope` class lines: `Scope(include_class_skills=True)` follows only SkillLine CategoryID 7, so it misses skill 118 (CategoryID 6) and skill 183 ("GENERIC (DND)", CategoryID 12). Two findings of this pass come only through this path:
- **Dual Wield 674** for Rogue/Hunter/Demon Hunter through SkillRaceClassInfo 131/1661. This is the path that closes the Rogue off-hand gate (`subtlety-rogue-leather-dw`: `gates.dual_wield.by == [674]`, validation ok; Track C's hook reports `dual_wielding` with no equip errors).
- **Mastery 114585** (+8 SPELL_AURA_MASTERY, agent F) for every player through skill 183 (SkillRaceClassInfo 5, ClassMask 16383).

The path was also ported independently by agent C (Track B package) as `weapon_combat.weapon.default_skill_grants` (same coordinates `Globals/ObjectMgr.cpp:4061-4070`, `Player.cpp:25259-25440`). The two ports were cross-checked class by class (classes 1–13, level 80): both give 674 for classes 3/4/12 and nothing else (test `test_default_skill_weapon_grants_match_track_c`).

Example counts at level 80 (and 90): Human Warrior has 32 skills and 134 spells, of which 34 are race-gated (the text said 33
before the closeout review). Every fixture has 2–10 AcquireMethod-4 spells that stay unresolved; see above for which of them the world
`conditions` table alone would decide.

## 4. Neutral fixture format

Corpus: `fixture-schema.json` (schema + field classes + slot vocabulary + FindEquipSlot rules). Validation is stdlib-only and fails
closed (`character_prep/fixture.py`). It rejects:
- unknown keys at every level, and duplicate JSON keys;
- wrong types (a bool is not an int);
- missing identity inputs;
- non-`EquipmentSlots` names (`Entities/Player/Player.h:729-749`);
- duplicate trait entries, PvP slots or learned spells, and rank < 1;
- a non-null `options.rng`;
- observation paths outside `/derived/`.

The closeout review found these cases fail-open and closed them (§15):
- `fixture_version: true` or `1.0`;
- a bool, NaN or negative observation `precision`, and non-string observation or provenance strings;
- the bare pointer `/derived/`, and `NaN` / `Infinity` JSON constants;
- inline base stats whose content is not class → level → five integers (the compiler used to crash on them);
- a base-stats `path` that is absolute or contains `..`;
- a `fill_rule` outside `none` / `trinity`, or on a non-corpus source.

| section | class | content |
|---|---|---|
| `fixture_version` | provenance | = 1 |
| `provenance` | provenance | snapshot_build, source_kind (synthetic, retail-export, addon-export, simc-profile, armory, manual), captured_at, external_ids, notes, generator |
| `identity` | identity | race_id, class_id, spec_id, level |
| `traits` | identity | `entries[{node_entry_id, rank}]` (purchased ranks, E2), `hero_subtree_id`, `loadout_string` (opaque provenance) |
| `pvp_traits` | identity | `[{pvp_talent_id, slot}]` |
| `learned_spells` | identity | `[SpellID, …]`: `character_spell` rows that no automatic path reproduces (trainer, quest, AcquireMethod 0 "Learned", e.g. 296087). Added by the closeout review; roots of kind `learned-spell`; passed to the weapon hook as `identity.learned_spells` |
| `equipment` | identity | slot → exactly the `gearing.loadout.LoadoutEntry` keys (item_id, context, bonus_list_ids, gems, enchant_ids, mythic_plus_keystone_level, pvp_tier, label, auto_bonus_lists) |
| `server_inputs.base_stats` | server | `source` = world-db-corpus (+ repository-relative path, + optional `fill_rule` `none`/`trinity`), inline (+values, provenance), or unsupplied |
| `controlled_units` | hook | extensible objects (Track A) |
| `options` | identity | enable_pvp_talents, include_class_skill_lines (reporting only), rng (reserved) |
| `observations` | provenance | `{observer, build, captured_at, path (JSON pointer into compiled output), value, units, precision, notes}` for a future Retail WASM/V8 oracle. The compiler returns `observations_report` rows with status match, mismatch, path-missing or expected-unresolved, plus a delta. |

Provider survey: the SimC profile shape, the Blizzard armory shape and the addon loadout string were used **for completeness
only** (navigation, not semantic authority).
- The shared content maps 1:1 onto the sections above: class/spec/race/level, talent string, and per-slot
  id/bonus_id/gem_id/enchant_id/context or difficulty.
- A loadout string is kept opaque, and its decoded content is exactly `traits.entries` + `hero_subtree_id`.
- The format is provider-neutral: no section names a provider, and `provenance.external_ids` is opaque.
- *Overstated before the review:* "no provider field outside this list feeds preparation" holds for combat values the
  compiler models. Character-DB state such as known spells, glyphs and skill values is not carried by these provider shapes
  either, so it needs its own sections (`learned_spells` exists; glyphs and skills do not; R3-3, R3-4).

The wowlab MCP `decode_loadout` / `parse_simc` tools were **not** called.

**Can `observations` address every derived value?**
- Yes for every value inside `/derived/*`, including list elements by index (RFC 6901, `resolve_pointer`).
- Not for `validation`, `unresolved` and `blocked`. These are compiler findings, not derived state (by design).
- A path that resolves to `null` reports `expected-unresolved` and is never counted as a match.

## 5. Fixture compiler

CLI:
- `character_prep.py compile --fixture F [--base-stats FILE | --world-db-corpus PATH] [--json] [--output]` (relative paths are resolved against the cwd)
- `explain --fixture F --path /derived/...`
- `validate --fixture F`
- `compile-all`

About 10 s to load, then 0.07–0.4 s per fixture. `all` takes about 21 s.

**Validation (fail closed, each error is a string with the Trinity coordinate):**
- **Identity.** Spec belongs to class; player class 1..13; `PlayableRaceBit ≥ 0`; level 1..90.
- **Traits** (port of TraitMgr, E1–E4). Entry exists; exactly one node in the class trees; `IsValidEntry` (`rank + granted ≤ MaxRanks`, `Spells/TraitMgr.cpp:832`); selection count (`:877-879`); `NodeMeetsTraitConditions` with the Visible/Available/RanksAllowed sufficient-flag semantics (`:681-764`); parent edges (`:884-901`); hero sub-tree (selectable for the spec, consistent with the selection entry); spent ≤ owned for TraitSourced currencies (`:966-980`).
- **Trinity's lenient load path** (`removeInvalidEntries=true`) is simulated and reported under `trinity_load_path_removed`. It is never applied.
- **PvP.** Talent exists and matches the spec; slot < 4 (`DataStores/DBCEnums.h:2795`); unlock level; category slot mask (`Player.cpp:27696-27702`).
- **Equipment.** Checks that were there before the review:
  - the item exists;
  - its InventoryType is legal for the slot (`Player.cpp:9194-9290`);
  - armour `ArmorTypeMask`, with cloaks exempt (`:11282-11287`);
  - the off-hand DW/TG gates (`:9249`, `:9261`);
  - **weapon proficiency** from default skills or SKILL/SKILL_STEP spells (`:11279-11280`; `Entities/Item/ItemTemplate.cpp:97-140`).
- **Equipment, added by the closeout review** (`_LoadInventory` → `CanEquipItem` at `:19240` mails every failing item, `:19295-19299`):
  - **required level** after bonuses (`Item::GetRequiredLevel`, `Entities/Item/Item.cpp:2864-2874`; `CanUseItem` `:11148-11149`);
  - AllowableClass / AllowableRaces (`:11204`), faction flags (`:11198-11202`) and `ITEM_FLAG2_INTERNAL_ITEM` (`:11195`);
  - RequiredAbility, which must be acquired or in `learned_spells` (`:11215-11216`);
  - unique-equippable duplicates (`:27408-27414`);
  - INVTYPE_WEAPONOFFHAND needs dual wield unless `ITEM_FLAG3_ALWAYS_ALLOW_DUAL_WIELD` (`:10860-10862`);
  - a one-hand polearm never goes off-hand (`:10852-10853`);
  - nothing goes off-hand while `IsTwoHandUsed` (`:13189-13199`, `:10870-10871`);
  - the Titan's Grip subclass mask (`:13125-13147`).
- **Equipment, reported unresolved** (character or host state): RequiredSkill outside the default skills, reputation, holiday, a
  required-level curve, and several items in one ItemLimitCategory.

**Derivations:**
- gear via `gearing.loadout.resolve_loadout`;
- spells (roots: spec, mastery, trait, race-gated default skill, gear, PvP, **learned spell**; other default-skill spells are reported separately);
- DW/TG gates (`Spells/SpellEffects.cpp:2237-2242`, `:4954-4960`);
- armour specialization (E7);
- stats via `CharacterResolver` with the world-DB base stats (agent F's `basestats_tdb` table when `fill_rule = trinity`; the filled level is reported under `/derived/stats/value/base` as unresolved Retail truth);
- hooks;
- initial state;
- `blocked`: build-skew markers, the mastery model gap (114585 +8, `Entities/Unit/StatSystem.cpp:549-550`, agent F), and unmodelled aura stages.

Every derived node carries `{value, evidence_class, provenance[], derived_from[]}` (tested).

**Numeric boundary.** The compiler performs no arithmetic of its own on stats. The integer and float rules are those of
`gearing` / `charstats`, and the binary32 versus binary64 differentials belong to agent F (armour-spec `1.05f` truncation,
`lroundf` in `UpdateMaxPower`). The trait engine is integer-only (int32 ranks and costs, `Spells/TraitMgr.cpp:511`), so no
float divergence is reachable there.

| fixture | spec | tree / hero | entries (multi-rank) / granted | sets, gems, enchants | spells (passive active) | armour spec | Str / Agi / Int / Sta | HP | mastery | gates |
|---|---|---|---|---|---|---|---|---|---|---|
| arms-warrior-plate-2h | Human Arms 90 | 850 / 60 Slayer | 71 (10) / 4 | 2070×2, 1, 3 | 128 (80) | 86101 | 20269 / 12768 / 13156 / 118447 | 2368940 | 12.783 | — |
| arms-warrior-plate-dw-learned | Human Arms 90 | 850 / 60 | 71 (10) / 4 | —, 1, 2 | 125 (77; 2 equip-gated) | 86101 | 19904 / 12502 / 13783 / 118378 | 2367560 | 13.652 | DW **296087 (learned_spells)** |
| fury-warrior-plate-titan-grip | Orc Fury 90 | 850 / 60 | 71 (10) / 4 | —, 1, 2 | 126 (77; 2 state-17 gated) | 86110 | 19443 / 12728 / 12883 / 108097 | 2161940 | 18.174 | DW 231842, TG 46917 |
| frost-dk-plate-dw-tier4 | Undead Frost 90 | 750 / 32 | 75 (6) / 3 | 2055×2+4, 1, 3 | 138 (91; 1 equip-gated) | 86113 | 19939 / 15003 / 11310 / 118448 | 2368960 | 17.304 | DW 674 |
| bm-hunter-mail-ranged-pet | Dwarf BM 90 | 774 / 43 | 72 (9) / 3 | —, 1, 2 | 127 (86) | **86538** (skill line) | 11043 / 20105 / 15780 / 118448 | 2368960 | 14.978 | DW 674 |
| enhancement-shaman-mail-dw | Troll Enh 90 | 786 / 54 | 75 (6) / 3 | 2070×2, 1, 2 | 131 (84) | 86099 | 7877 / 19573 / 18820 / 103729 | 2074580 | 9.500 | DW 86629 |
| subtlety-rogue-leather-dw | NE Sub 90 | 852 / 51 | 75 (6) / 3 | —, 1, 3 | 135 (89) | **86092** (skill line) | 14729 / 19551 / 12846 / 102392 | 2047840 | 12.196 | DW 674 (skill 118) |
| balance-druid-leather-caster | Tauren Balance 90 | 793 / 23 | 74 (7) / 5 | —, 1, 2 | 138 (90) | 86104 | 7835 / 19117 / 19935 / 118449 | 2368980 | 17.370 | — |
| demonology-warlock-cloth-pet | BE Demo 90 | 720 / 57 | 73 (8) / 3 | —, 1, 2 | 128 (86) | **86091** (skill line) | 7029 / 13834 / 20233 / 118650 | 2373000 | 14.717 | — |
| holy-priest-cloth-healer-tier4 | Draenei Holy 90 | 795 / 19 | 74 (7) / 4 | 2063×2+4, 1, 3 | 135 (83; 1 after-first-update) | **89745** (skill line) | 10481 / 15051 / 19937 / 118449 | 2368980 | 13.587 | — |
| windwalker-monk-leather-dw-tier4 | Pandaren WW 90 | 1000 / 64 | 76 (5) / 5 | 2061×2+4, 1, 2 | 135 (93) | 120227 | 7641 / 19818 / 18749 / 109281 | 2185620 | 13.826 | DW 124146 |
| arms-warrior-plate-2h-no-fill-rule | Human Arms 90 | 850 / 60 | 71 (10) / 4 | 2070×2, 1, 3 | 128 (80) | 86101 | **unresolved** (no level-90 row, no opt-in) | unresolved | — | — |

All level-90 values above stand on the **level-80 base cell** served by Trinity's fill rule (explicit opt-in;
`trinity-consumer(fill-rule)`, Retail unresolved). With the same base cell and gear, the primary stats and HP are the same as the
level-80 table this section printed before the review. Mastery differs because the CombatRatings column at 90 is 46 instead of
12.158. Before the review the table was a level-80 table, and every one of its characters was unloadable in Trinity (R3-1).

These stats are **not ground truth**. They omit every aura stage except armour specialization (`blocked`), and
`mastery_value` omits the +8 of 114585. For Hunter, Rogue, Warlock and Priest the stored stats include armour specialization,
which a plain charstats run would not apply (E7). The weapon hook (Track C) resolves with `trinity-probe` evidence; the
controlled-unit hook is absent and therefore `unresolved`.

## 6. Synthetic fixtures

Generator: `character_prep.py synth-fixtures` (inside `all`). Every fixture is labelled `source_kind: synthetic`, and a
`SYNTHETIC` note says every id is source-valid for the pinned snapshot.

- **Level.** 90 since the closeout review. Every current-season item requires level 90 (`ItemScalingConfig.RequiredLevel` through ItemBonus 49), so a level-80 character cannot load them in Trinity (R3-1). Base stats opt into Trinity's fill rule (`fill_rule: trinity`); `arms-warrior-plate-2h-no-fill-rule` does not.
- **Traits.** The newest `TraitTreeLoadout` on the class tree is replayed in `OrderIndex` order (E4) at the fixture level. A step is kept only if the whole config stays valid within budget. At level 90: **68 of 68 steps kept for every spec**. At level 80 it was 61 of 68, with 7 skipped. Granted ranks are subtracted.
- **Hero sub-tree.** The lowest-id sub-tree the spec may select. Nodes are added greedily within the hero budget: 13 nodes at level 90 (10 at level 80).
- **Items.** From `gearing-corpora/current-gear-corpus.json`, restricted to `ItemSparse.ExpansionID == 11`. The M+ rotation's legacy dungeon items (expansions 7/9) resolve at item level 59/250 in gearing, so they are excluded. Contexts are 6 (Raid_Mythic) and 16 (M+, keystone 10). Every fixture item is at item level ≥ 259 and requires level 90 (tests).
- **Tier pieces.** Tier sets 2055/2061/2063 are not in the corpus, so the pieces come from `ItemSet` / `ItemSparse` (context 6), as in `charstats-corpora/gear-to-character-proof.json`.
- **Gems and enchants.** Socket bonus list 12234 on the neck with gem 240857 or 240863; enchants 8055, 8158, 7934, 8042.

Coverage (asserted by `test_fixture_plan_coverage`):
- plate 2H (Arms), plate DW (Frost DK) and plate Titan's Grip (Fury);
- BM Hunter with `controlled_units.hunter_pet` (family 1 Wolf, pet spec 74 = ChrSpecialization "Ferocity", ClassID 0; verified db2-fact in the closeout review; Track A owns the rest);
- Enhancement (mail DW), leather agility DW (Subtlety, Windwalker), leather caster with forms (Balance);
- cloth pet class (Demonology, `active_demon.summon_spell_id` 30146 = "Summon Felguard", acquired by the fixture; verified db2-fact); cloth healer (Holy);
- **Arms one-hand dual wield through `learned_spells: [296087]`** (`arms-warrior-plate-dw-learned`); without the section the OFFHAND gate fails (test);
- multi-rank traits in all fixtures; 2-piece (2070) and 2+4-piece (2055/2061/2063) sets; gems, enchants, ratings, mastery in all;
- a PvP talent (Arms, PvpTalent 34, slot 0).

`fixtures/index.json` lists, for each fixture:
- the fixture and compiled sha256;
- `validation_ok`;
- `level_90_base_stat_gap`;
- `base_stats_fill_rule` and `base_stats_filled`.

It also records the level-90 gap. Without the fill-rule opt-in, `/derived/stats` is unresolved for **every** fixture (test).
The committed `arms-warrior-plate-2h-no-fill-rule` is the file-level witness that agent F consumes. It was called
`arms-warrior-plate-2h-level90` before the review.

## 7. Server-authored preparation inputs

Question: which inputs that shape a Player's prepared state (the state after
`Player::InitStatsForLevel` → `UpdateAllStats`) come from the server rather than
the client snapshot, and what does the pinned server supply for each?

Every coordinate is TrinityCore `7f3d43b` (`src/server/…`), re-read for this
part. Row counts come from the pinned TDB `TDB_full_world_1200.26021` plus a
replay of the 522 files in `sql/updates/world/master`
(`tools/tdb_world_extract.py`: 0 unparsed statements, 0 missing tables). The
registry is `character-prep-corpora/server-inputs.json` (18 inputs, 7 phases).
The world-DB tables in it add up to 33 counted tables, and each one is attached
to an input (a test enforces this).

### 7.1 Phases

| phase | meaning |
|---|---|
| `prepared` | changes a prepared value of an existing character |
| `identity-gate` | decides whether a (race, class, level) character can exist at all |
| `creation-only` | read only by `Player::Create`, so irrelevant to a character that is imported whole |
| `acquisition` | spell-learning edges (overlaps `charstats.acquisition`) |
| `runtime` | read only during the encounter (regen and similar) |
| `reference-other-track` | owned by Track A/B or the dummy/proc passes; listed for completeness |
| `irrelevant` | loaded by the server, but never touches the prepared Player |

### 7.2 Registry

| input | phase | pinned value / rows | loader | prepared-state consumer | stronger source / observation |
|---|---|---|---|---|---|
| `player_classlevelstats` | prepared | **1,032** | `Globals/ObjectMgr.cpp:4287` | `ObjectMgr.cpp:4437-4450` → `Player.cpp:2332, 2362-2366` (SetCreateStat/SetStat), `:2373` base armor | naked Retail character sheet per (class, level): `UnitData::Stats[]` minus the race modifier |
| `player_racestats` | prepared | **31** | `ObjectMgr.cpp:4263` | `ObjectMgr.cpp:4328` (`row + StatModifier[i]`) | two naked observations, same class and level, different race |
| `MaxPlayerLevel` (conf) | prepared | **90** (`worldserver.conf.dist:912`; default `GetMaxLevelForExpansion(CURRENT_EXPANSION)`, `World/World.cpp:750`) | `World.cpp:750` | `ObjectMgr.cpp:4309-4317` (drops rows above it), `:4324` (array size), `:4349` (fill bound), `:4446-4449`, `:4424-4425` (base-mana clamp) | caller-supplied server fact |
| `Stats.Limits.*` (conf) | prepared | Enable **0**; Dodge/Parry/Block/Crit **95.0** (`conf.dist:2727, 2734-2737`) | `World.cpp` | `Entities/Unit/StatSystem.cpp:495-496, 505-506, 665-666, 703-704` | disabled by default: inert unless a server opts in |
| `playercreateinfo` | identity-gate | **281** (race, class) pairs (263 base + 18 Haranir from `2026_03_07_00_world.sql`) | `ObjectMgr.cpp:3813` | `ObjectMgr.cpp:4321-4323` (only these pairs get `levelInfo`); `Player.cpp:396-402` (refuses an unknown pair); `ObjectMgr.cpp:10517-10520` | DB2 has no equivalent pair table that Trinity trusts |
| `Expansion` (conf) | identity-gate | **11** (`conf.dist:718`) | `World.cpp:795` | `Player.cpp:2334-2339` (MaxLevel update field only) | — |
| session expansion | identity-gate | account state | `WorldSession::GetExpansion()` | `Player.cpp:2334-2339` | caller-supplied |
| `playercreateinfo_item / _spell_custom / _cast_spell / _action` | creation-only | **1 / 0 / 31 / 1,942** | `ObjectMgr.cpp:3993, 4080, 4142, 4205` | `Player.cpp:509, 521-522` (inside `Player::Create` only) | none needed: an imported character's gear and spells are fixture inputs |
| `race_unlock_requirement / class_expansion_requirement` | creation-only | **31 / 230** | `ObjectMgr.cpp:10522, 10528, 10573` | `Handlers/CharacterHandler.cpp` creation checks | — |
| `Start*PlayerLevel` (conf) | creation-only | 1 / DK 8 / DH 8 / Evoker **10** (`conf.dist:927/935/943/951`) | `World.cpp:752-755` | creation level | — |
| `spell_learn_spell` | acquisition | **5** | `Spells/SpellMgr.cpp:1009` | `AddSpell`/`LearnSpell` chains | already covered by the `dummy-corpora` overlay |
| `Rate.Health`, `Rate.Mana`, other power rates (conf) | runtime | all **1** (`conf.dist:2405-2406`) | `World.cpp:941-942` ff. | **only** `Player.cpp:1748` (RegenerateHealth) and `StatSystem.cpp:891` (`UpdatePowerRegen`, rate table `PowerRegenInfo` `:809-835`) | — |
| `player_xp_for_level` | irrelevant | **0** | `ObjectMgr.cpp:4370` (pre-filled from `xp.txt` at 4367-4374) | `ObjectMgr.cpp:7916-7921`, `Player.cpp:2340` | `xp.txt` is used unchanged |
| `item_template_addon / item_random_bonus_list_template` | irrelevant | **625 / 0**; `RandomBonusListTemplateId` is 0 on all 625 rows | `ObjectMgr.cpp:3381`; `Item/ItemEnchantmentMgr.cpp:48` | random bonus lists at item creation | the fixture's explicit bonus ids |
| `skill_tiers / skill_fishing_base_level` | irrelevant | 59 / 105 | `ObjectMgr.cpp:8936, 8900` | professions | — |
| `spell_totem_model`, `player_factionchange_spells`, `exploration_basexp`, … | irrelevant | 342 / 113 / 80 | ObjectMgr / SpellMgr | cosmetic, faction change | — |
| `spell_proc` 1,280, `spell_custom_attr` 141, `spell_script_names` 4,016, `spell_pet_auras` 2, `serverside_spell_effect` 3,197, `spell_linked_spell` 223, `spell_area` 952, `spell_group` 297 + `_stack_rules` 13, `spell_required` 21, `spell_threat` 21, `spell_enchant_proc_data` 16 | reference-other-track | (counts as listed) | `SpellMgr.cpp:1506, 2986, 1972, 2752, 2081, 2307, 1274, 1355, 1920, 2038`; `ObjectMgr.cpp:5971` | proc/dummy/aura passes | existing overlays |
| `creature_template / creature_template_spell / pet_levelstats` | reference-other-track | – / 9,586 / 2,715 | `ObjectMgr.cpp:347, 370, 3686` | `Guardian::InitStatsForLevel` (Track A/B) | Track A corpora |

### 7.3 Config that could scale prepared values (`trinity-consumer`)

`grep getRate(RATE_` finds **no** rate that multiplies a prepared maximum.
`Rate.Health` is read only in `Player::RegenerateHealth` (`Player.cpp:1748`) for players.
The power rates are read only in `Player::UpdatePowerRegen` (`StatSystem.cpp:891`, through the `PowerRegenInfo` table at
`:809-835`) and, for rage income, in `Unit.cpp:13133`.
The closeout review re-grepped every `getRate(` / `getIntConfig(` / `getFloatConfig(` / `getBoolConfig(` in `StatSystem.cpp` and in
`InitStatsForLevel`:
- `Stats.Limits` (`StatSystem.cpp:495-506, 665-666, 703-704`);
- `getRate` at `:891`;
- `CONFIG_MAX_PLAYER_LEVEL` (`Player.cpp:2335`).
`UpdateMaxHealth` (`StatSystem.cpp:314-324`), `UpdateMaxPower` (`:331-345`) and
`GetHealthBonusFromStamina` (`:283-293`) read no config at all. Only two config
values shape prepared state:
- `MaxPlayerLevel` fixes the size of the base-stat array (see §8).
- `Stats.Limits.*` caps dodge, parry, block and crit, but only when a server
  enables it (the default is disabled).

Power regeneration is runtime. It is marked `runtime` and is not prepared.

### 7.4 What must stay caller-supplied

- The account's session expansion (it matters only for the MaxLevel update field).
- `MaxPlayerLevel` itself, if the importing server is not the default Midnight build.

Everything else in the `prepared` phase has a pinned value, listed above.
`absent from pinned Trinity != absent from Retail`: the registry says what this
server supplies, not what Retail uses.

## 8. The base-stat gap, reopened

Corpus: `world-db-corpora/player-base-stats.json` (`world-db-fact`).
Characterisation: `character-prep-corpora/base-stat-gap.json`.

### 8.1 Coverage (world-db-fact)

| item | fact |
|---|---|
| classes with rows | **13** (1–13). Classes 14 (Adventurer) and 15 (Traveler) exist in ChrClasses but have no rows |
| levels | classes 1–12: **1..80** (80 rows each). **Evoker (13): 72 rows**, levels 1 and 10..80; **2–9 absent**, which matches `StartEvokerPlayerLevel = 10` |
| above 80 | **no row for any class** |
| `VerifiedBuild` | **0** on all 1,032 rows. The loader never selects it (`ObjectMgr.cpp:4287`) |
| zero-strength rows | none |
| monotonicity | one anomaly, in **all 13 classes**: Stamina **6655 at level 69 → 6397 at level 70** |
| level-80 Warrior row | 17647 / 12176 / 86452 / 12000 / 0 |
| update touching the tables | only `2026_03_07_01_world.sql`: `DELETE … WHERE race IN (86,91)` then an `INSERT` of **all-zero** rows for 86 and 91 (29 → 31 racestats rows) |

### 8.2 Race coverage (db2-fact × world-db-fact)

- ChrRaces has **59** rows. Exactly **31** races have `PlayableRaceBit ≥ 0` and
  lack the NPCOnly flag (0x1). That set of 31 is **identical** to:
  - the `player_racestats` races;
  - the `playercreateinfo` races;
  - the `race_unlock_requirement` races.
- **No playable race lacks a race-stats row.**
- Races 95 and 96 ("TBD NPC Race 1/2") have `PlayableRaceBit` 32 and 33, but
  also carry NPCOnly. They have no row anywhere, and character creation refuses
  them (`CharacterHandler.cpp:743`).
- Races **52, 70, 84, 85, 86, 91** have **all-zero** modifiers (world-db-fact).
  Whether that is Retail truth is unresolved.
- Legacy races carry ±3 deltas. For example, Orc (2) is +3 / −3 / +1 / −1 / 0.
- Evoker pairs exist only for races **52 and 70**. Demon Hunter pairs exist
  only for races **4 and 10**.

**What Trinity would do with a missing race row** (`trinity-consumer`):
- If the race has a `playercreateinfo` pair, that pair's `levelInfo` stays
  null, and `ABORT()` fires at `ObjectMgr.cpp:4342-4346`, so the server does
  not start.
- Otherwise the race has no `PlayerInfo`, and `Player::Create` refuses it
  (`Player.cpp:396-402`).

**Zero-strength crossings.** Five (race, Evoker) combinations would make the
level-1 race-modified Strength 0: races **7, 9, 10, 29, 35**.
- If any of them were created, the sentinel below would abort the server.
- **None of them is a `playercreateinfo` pair.**

### 8.3 What `ObjectMgr::LoadPlayerInfo` does (re-verified line by line)

The function is `ObjectMgr::LoadPlayerInfo` (`ObjectMgr.cpp:3807`); the
base-stat block runs from 4263 to 4357.

1. **Rows above the maximum are dropped** (`:4309-4317`): a row with
   `level > CONFIG_MAX_PLAYER_LEVEL` is ignored, with `TC_LOG_ERROR` if the
   level is > `STRONG_MAX_LEVEL` (255, `DBCEnums.h:49`), otherwise
   `TC_LOG_INFO`.
2. **Cells are written per pair** (`:4319-4330`): for each racestats race that
   has a `PlayerInfo` (a `playercreateinfo` pair):
   - `levelInfo = make_unique<PlayerLevelInfo[]>(MaxPlayerLevel)` (`:4324`).
     Each cell is `int32 stats[MAX_STATS] = {}`, i.e. zeroed (`ObjectMgr.h:628-631`).
   - `levelInfo[level-1].stats[i] = row + StatModifier[i]` (`:4328`).
3. **Integrity check** (`:4342-4346`): a null `levelInfo`, or
   `levelInfo[0].stats[0] == 0`, logs an error and calls **`ABORT()`**.
4. **Gap fill** (`:4349-4356`): for 0-based index `level = 1 .. MaxPlayerLevel-1`,
   if `stats[0] == 0`:
   - it logs `TC_LOG_ERROR("sql.sql", "… does not have stats data. Using stats data of level {}.")`;
   - then it runs **`levelInfo[level] = levelInfo[level-1]`**, a plain int32 copy
     with no extrapolation.
   - **The sentinel is the race-modified Strength.** A real row whose Strength
     plus race modifier is 0 would be overwritten as well.
5. **Lookup** (`:4437-4450`): if `level <= MaxPlayerLevel`, it reads
   `levelInfo[level-1]`. Otherwise it calls `BuildPlayerLevelInfo`
   (`:4452-4530`), a Classic-era switch over classes 1–9 and 11; classes 6, 10,
   12 and 13 have no case.
6. **Max level** (`World.cpp:750`): `MaxPlayerLevel` defaults to
   `GetMaxLevelForExpansion(CURRENT_EXPANSION)`, with `CURRENT_EXPANSION =
   EXPANSION_MIDNIGHT` (`SharedDefines.h:107`), which maps to 90
   (`SharedDefines.h:135-136`). The allowed range is 1..`MAX_LEVEL` 123
   (`DBCEnums.h:45`), and `conf.dist:912` sets 90.

**Level-90 verdict.** With the pinned TDB and the default configuration, a
level-81..90 character of any class receives the **level-80 cell unchanged**.

| item | value |
|---|---|
| filled cells per (race, class) pair | 10 (levels 81–90); Evoker 18 (8 more for levels 2–9) |
| filled (class, level) cells | **138** = 13 × 10 + 8 |
| probe (the extracted loop) | 10 error lines for Warrior/Human, 18 for Evoker; for all 13 classes, the level-90 stats equal the level-80 row |
| Warrior level 90 served | **17647 / 12176 / 86452 / 12000 / 0** (`trinity-consumer(fill-rule)`) |
| as Retail truth | **unresolved** |
| `BuildPlayerLevelInfo` | reached only when `MaxPlayerLevel` is below the character's level. With `MaxPlayerLevel = 80`, Warrior 80→90 would get **17669 / 12187 / 86474 / 12005 / 0** (trinity-probe) |
| base mana | `GetPlayerClassLevelInfo` clamps the level to Max (`:4424-4425`), then applies `uint32(GetGameTableColumnForClass(BaseMp))` (`:4434`). BaseMp.txt has real level-90 values (db2-fact; Priest 250,000 at 90 vs 50,000 at 80) |
| HpPerSta | db2-fact: **1.0 at level 1**, 20.0 at 79, **20.0 at 80, 81 and 90**. The fallback is 10.0f when the row is missing (`StatSystem.cpp:286`) |

**The fill rule is Trinity's behaviour, not truth.**
- `basestats_tdb.py` reproduces it only with the explicit opt-in
  `--fill-rule trinity`.
- Every filled cell is tagged `trinity-consumer(fill-rule)`.
- Without the opt-in, level 81 and above raises `MissingBaseStats`.
- The opt-in also refuses a pair whose race-modified Strength would be 0,
  because Trinity would ABORT there.

### 8.4 Adapter

`character_prep/basestats_tdb.py` is an adapter from the corpus to
`charstats.basestats.BaseStatTable`:
- `load()`, `coverage()`, `monotonicity()`, `zero_strength_crossings()`;
- `table(fill_rule, max_player_level, pairs)` builds a `TaggedBaseStatTable`
  whose `describe()` / `evidence()` return, for each cell, its evidence class
  and its source row or fill source;
- `export_json` produces the charstats `--base-stats` format;
- CLI: `character_prep.py basestats --class --race --level [--fill-rule trinity]`.

Identity query: `character_prep.py server-inputs --class C --race R --level L`
lists every server input the identity needs and whether it is satisfied. It
exits 1 when anything is unsatisfied, for example level 90 without the fill
rule, or an absent pair.

### 8.5 Contradictions with existing reports (quoted)

- `character-stat-pipeline-archaeology.md` §Scope, headline 1:
  > "Base primary stats are not in the snapshot. They come from TrinityCore's
  > *world database* tables … and the TrinityCore repository ships only their
  > `CREATE TABLE` statements."

  Still true for the snapshot and the repository. **Superseded** for the
  values: the pinned TDB release supplies 1,032 + 31 rows (world-db-fact),
  which this pass extracted.
- The same report, §1:
  > "above `CONFIG_MAX_PLAYER_LEVEL`, `BuildPlayerLevelInfo` extrapolates with a
  > hardcoded per-class `switch` …"

  True, but **not reached by default**: Max is 90, and levels 81–90 are
  **copies of level 80** (§8.3 step 4). The level-80→90 extrapolation happens
  only if a server sets `MaxPlayerLevel` below 90.

## 9. Trinity differential of prepared values

### 9.1 Probe (`tools/tc_prep_probe/`, tc_probe pattern)

`make -C tools/tc_prep_probe` runs `extract.py`, which writes `tc_extracted.inc`
(544 lines). The build is `g++ -std=c++20 -O0 -ffp-contract=off`, and a clean
rebuild was verified. `.gitignore` covers `probe` and `*.inc`.

**Extracted verbatim** (function bodies copied unchanged at build time):
- `CalculatePct`, `AddPct` (`common/Utilities/Util.h`);
- `GetGameTableColumnForClass` (`DataStores/GameTables.h`);
- `GetGameTableColumnForCombatRating`;
- `DetermineCurveType` and `DB2Manager::GetCurveValueAt` (`DB2Stores.cpp`);
- `Unit::GetTotalStatValue` (`Unit.cpp:9828`);
- `Player::GetHealthBonusFromStamina`, `UpdateMaxHealth`, `UpdateMaxPower`, `UpdateArmor` (`StatSystem.cpp`);
- `Player::GetRatingMultiplier`, `GetRatingBonusValue`, `ApplyRatingDiminishing`, `ApplyRatingMod`;
- `ObjectMgr::BuildPlayerLevelInfo`;
- the player "Fill gaps and check integrity" block (`ObjectMgr.cpp:4336`), wrapped in a function.

**Re-typed.** These lines need update-field state. Each one is asserted
verbatim by `extract.py`'s `RETYPED` list, so the build fails if the source
moves:
- AP and RAP (`StatSystem.cpp` strength/agility lines and `SetAttackPower(int32(base_attPower))`);
- melee crit `float value = 5.0f` and `applyCritLimit(...)`;
- spell crit (`:711-721`);
- `UpdateMastery` (`:549-550`);
- `SpellEffectInfo::CalcValue`'s `value += Mastery * BonusCoefficient`
  (`SpellInfo.cpp:602`). Here `SpellEffectValue` is **double**
  (`SpellDefines.h:490`), so the step is `double += float*float`.

**Not probed.**
- Power regeneration is `runtime`.
- `UpdateSpellDamageAndHealingBonus` is only the flat-SP path; charstats already
  proves it is Intellect → SP with no float stage.
- Weapon damage belongs to agent C.
- `GetDodgeFromAgility` (`Player.cpp:5014`) has a fully commented-out body.
- Hit chance is 7.5f / 15.0f (`StatSystem.cpp:753-767`).
- The last two are **legacy-only**: no current DB2 input reaches them except the
  unused CR_HIT slots.

### 9.2 Result (`differential.json`)

Both sides get the same inputs:
- **Python:** `charstats` + `gearing.ratings` + the adapter, all in binary64.
- **Probe:** the extracted C++, in binary32.
- **Base stats:** from the TDB corpus. Level-90 and gap witnesses opt into the
  fill rule.

| metric | value |
|---|---|
| witnesses | **25** = 13 built-in + **12 agent-E compiled fixtures** (0 refused) |
| compared records | **1,389** (592 built-in + 797 fixture) |
| mismatches | **36** = 25 × `mastery_value.charstats` (model-gap) + 1 × `stat.Strength` (binary32) + 10 × `compiled.*` (compiler-vs-charstats, §9.6) |
| max float rel_diff (excluding the classified fields) | 1.09e-7 |
| integer fields | stats, max health, create/max mana, armor, bonus armor, AP, RAP, fill-rule cells, mastery int32: all match, apart from the 1 binary32 case |
| float fields | rating linear/final percent × 10 ratings, crit, spell crit, mastery, mastery effect amount: all within rel ≤ 1.6e-7, and `int32()` agrees except in the classified cases |

| witness | race, class, spec, level | base evidence | base stats | max HP | armor | AP | create mana | mastery (charstats / Trinity) | records / mismatches |
|---|---|---|---|---|---|---|---|---|---|
| warrior-arms-human-80 | 1, 1, 71, 80 | world-db-fact | 17647/12176/86452/12000/0 | 2,029,040 | 29,352 | 27,647 | 0 | 32.609496 / 40.609497 | 44 / 1 |
| warrior-arms-orc-80 | 2, 1, 71, 80 | world-db-fact | 17650/12173/86453/11999/0 | 2,029,060 | 29,346 | 27,650 | 0 | 32.609496 / 40.609497 | 44 / 1 |
| priest-shadow-human-80 | 1, 5, 258, 80 | world-db-fact | 10235/14647/86452/17647/0 | 2,029,040 | 32,294 | 0 | 50,000 | same | 44 / 1 |
| hunter-bm-orc-80 | 2, 3, 253, 80 | world-db-fact | 10768/17644/86453/14470/0 | 2,029,060 | 39,288 | 27,644 | 0 | same | 44 / 1 |
| evoker-devastation-dracthyr-80 | 52, 13, 1467, 80 | world-db-fact | 7765/12000/86452/17647/0 | 2,029,040 | 27,000 | 12,300 | 50,000 | same | 44 / 1 |
| warrior-arms-human-90-fill | 1, 1, 71, 90 | trinity-consumer(fill-rule) | 17647/12176/86452/12000/0 | 2,029,040 | 29,352 | 27,647 | 0 | 8.695652 / 16.695652 | 49 (+1 fill record) / 1 |
| priest-shadow-human-90-fill | 1, 5, 258, 90 | trinity-consumer(fill-rule) | 10235/14647/86452/17647/0 | 2,029,040 | 32,294 | 0 | 250,000 | 8.695652 / 16.695652 | 49 (+1) / 1 |
| evoker-devastation-dracthyr-5-fill | 52, 13, 1467, 5 | trinity-consumer(fill-rule) | 3/4/6/6/0 (level-1 cell) | 15,006 | 3,008 | 304 | 750 | 91.433661 / 99.433662 | 49 (+1) / 1 |
| warrior-arms-human-80-armorspec | 1, 1, 71, 80 | world-db-fact | as Human 80 | 2,029,040 | 29,352 | 29,029 | 0 | 32.609496 / 40.609497 | 44 / 1 |
| warrior-arms-human-80-armorspec-crossing | 1, 1, 71, 80 | world-db-fact | as Human 80 | 2,029,040 | 29,352 | 29,022 (py) | 0 | same | 44 / **2** |
| warrior-arms-haranir-80 | 86, 1, 71, 80 | world-db-fact | as Human 80 (zero race modifiers) | 2,029,040 | 29,352 | 27,647 | 0 | same | 44 / 1 |
| naked-warrior-human-80 | 1, 1, 71, 80 | world-db-fact | as above | 1,729,040 | 24,352 | 17,647 | 0 | 0 / 8 | 44 / 1 |
| naked-warrior-human-90-fill | 1, 1, 71, 90 | trinity-consumer(fill-rule) | as above | 1,729,040 | 24,352 | 17,647 | 0 | 0 / 8 | 49 (+1) / 1 |

Witness contributions are **synthetic round numbers, not Retail gear**:
- 10,000 primary stat, 15,000 stamina, 300 off-stats;
- ratings: crit 500, haste 400, mastery 400, versatility 300;
- armor 3,000–5,000.

At level 80 the CombatRatings columns are Mastery **12.15826163** and
Versatility **14.27274192**. At level 90 CritMelee and Mastery are **46**.

### 9.3 Finding A: generic mastery 114585 (`trinity-consumer` static trace + `db2-fact`; model gap in charstats)

Trinity's `UpdateMastery` computes
`value = GetTotalAuraModifier(SPELL_AURA_MASTERY) + GetRatingBonusValue(CR_MASTERY)`
(`StatSystem.cpp:549-550`). charstats uses the rating term only. The aura term
is **+8.0 for every player whose spec mastery spell is known**.

The chain, with each DB2 fact re-read by `generic_mastery_evidence()` and stored
in the corpus under `generic_mastery_114585`:
1. **The spell.** Spell 114585 "Mastery" has `SpellMisc.Attributes_0 = 464`
   (0x40, **PASSIVE**). SpellEffect 127950 is Effect 6 (APPLY_AURA), aura
   **318** (`SPELL_AURA_MASTERY`), BasePoints **8**. It has no SpellShapeshift,
   SpellAuraRestrictions or SpellEquippedItems row, and SpellLevel/BaseLevel are 0.
2. **Skill grant.** SkillLineAbility 25920 puts it on skill line **183**
   "GENERIC (DND)", with AcquireMethod **2** (AutomaticCharLevel), ClassMask 0
   and no race mask. SkillRaceClassInfo 5 covers SkillID 183 with ClassMask
   16383, RaceMasks −1, **Availability 1** and MinLevel 0.
3. **Default skills.** `ObjectMgr.cpp:4063-4070` adds that SkillRaceClassInfo
   row to every pair's default skills. Then `Player::LearnDefaultSkills`
   (`Player.cpp:25259`) calls `SetSkill`, which calls
   `LearnSkillRewardedSpells` (`:25391`). That function accepts AutomaticCharLevel
   (`:25407`), and the required level is 0.
4. **Passive cast.** `AddSpell` checks `IsPassive`, then
   `HandlePassiveSpellLearn` (`:3079`). With no stances, `EquippedItemClass < 0`
   and no CasterAuraState, the spell must be cast, so
   `CastSpell(this, id, true)` runs (`:2919-2929`).
5. **Mastery update.** `HandleMastery` (`SpellAuraEffects.cpp:5571`) calls
   `UpdateMastery`. The gate `CanUseMastery` (`StatSystem.cpp:543-547`,
   `Player.cpp:30525`) zeroes Mastery unless the spec's MasterySpellID is known.
6. **Effect.** Mastery 0 rating gives Trinity 8.0 and charstats 0.0. Spec
   mastery effect amounts downstream (`base + Mastery × coef`) then differ by
   `8 × coef`.

**Evidence.** The chain is a static consumer trace, and the aura amount is a
db2-fact. Runtime application was not executed.

**Contradicts:**
- `character-stat-pipeline-archaeology.md` §1 table:
  > "| base mastery | `0` unless `CanUseMastery()` | n/a |"
- the charstats code (`charstats/character.py:334`):
  `mastery_value = next(c["final_percent"] for c in rating_conversions if c["rating"] == "Mastery")`.

That report's own §9 pseudocode lists
`value = GetTotalAuraModifier(SPELL_AURA_MASTERY)` but never names a source for
the term. The differential records both `mastery_value.charstats` (mismatch,
classified `model-gap`) and `mastery_value.with_114585` (match on every
witness). charstats was **not** modified.

### 9.4 Finding B: armour specialisation truncates one point lower in Trinity (`differential`, reachable)

**Mechanism.** `Unit::GetTotalAuraMultiplier` accumulates in **double** and
returns `static_cast<float>(multiplier)` (`Unit.cpp:5016-5033`). A 5% armour
specialisation (`SPELL_AURA_MOD_TOTAL_STAT_PERCENTAGE`) therefore sets
TOTAL_PCT = `f32(1.05)` = **1.0499999523162842**. Then:
- `GetTotalStatValue` multiplies in float (`Unit.cpp:9828`);
- `UpdateStats` stores `int32(value)` (`StatSystem.cpp:110-112`).

charstats multiplies by the double 1.05 and truncates.

**Sweep** (`armor_spec_truncation_sweep`, pre-percent totals 1..120,000):
- **4,045 totals diverge**. **All** are multiples of 20 (4,045 of the 6,000 in range).
- Python − Trinity is **always +1**.
- Examples: 60 → 63 vs 62, 100 → 105 vs 104, 120000 → 126000 vs 125999.

**Reachable witness** `warrior-arms-human-80-armorspec-crossing`: Human Warrior
L80 create Strength 17647 + 9,993 item Strength = 27,640.
- charstats gives **29,022**.
- The Trinity probe gives 29021.998046875, which truncates to **29,021**.
- The raw float values agree to rel 6.7e-8, so only `int32()` exposes the
  difference. The records are classified `binary32-vs-binary64`.
- This is the same class of divergence as the cooldown 41,999 / 42,000 ms case.

Which precision Retail uses is **unresolved**.

### 9.5 Other type witnesses (`trinity-probe`)

| case | Trinity | Python | coordinate | reachable? |
|---|---|---|---|---|
| `UpdateMaxPower` rounding | `(int32)std::lroundf` half-away: 2.5 → **3** | `round(2.5)` = 2 | `StatSystem.cpp:344` | only through a percent mana aura that produces an exact .5; charstats models no mana pct stage |
| `m_baseRatingValue` accumulator | **int16**: 30000 + 5000 = **−30536** | 35000 | `Player.h:3254`, `Player.cpp:5233` | **unresolved**: needs one CombatRatings slot above 32,767 |
| HpPerSta row missing | 10.0f fallback: 5 stamina → 50 | — | `StatSystem.cpp:286` | no (HpPerSta has rows 1..90+) |
| max health 0 | `SetMaxHealth` floors to **1** | — | `Unit.cpp:10018-10019` | only for a zero-stamina character |
| stat int conversion | `int32()` truncation: 29029.35 → 29029 | same | `StatSystem.cpp:112` | yes (agrees except §9.4) |
| rating sweeps | Mastery@80, CritMelee@90, VersDone@80 over amounts 1..4,000 | — | `Player.cpp:5155-5163` | **0** `int32` divergences; max rel 1.54e-7 |

Item stat routing: Trinity puts item primary stats into `BASE_VALUE`
(`Player.cpp:7914-7932`), while charstats `Contributions` uses `TOTAL_VALUE`.
- The probe is fed Trinity's routing.
- The results are identical under default percentages and under TOTAL_PCT.
- They would diverge only under a non-default `BASE_PCT` or
  `BASE_PCT_EXCLUDE_CREATE` aura. Neither is reached by any witness
  (structural-inference).

### 9.6 Agent E fixtures

**Input.** `differential` reads every `character-prep-corpora/fixtures/*.compiled.json`: 12 fixtures. They are the 11
in `FIXTURE_PLAN` (including the learned-spell Arms witness) plus `arms-warrior-plate-2h-no-fill-rule`, all level 90
since the closeout review.

**The adapter** is `witness_from_e_compiled`. It fails closed unless all of
these hold:
- `validation.ok` is true;
- `fixture.identity` is present;
- `derived.stats.provenance` contains exactly one `boundary` step whose
  `handed_over` keys are exactly the `Contributions` keys;
- `derived.armor_specialization.value.applied_spell_id` is present;
- `derived.stats.value.stat_stages` has default base stages.

Other behaviour:
- The adapter applies the fill rule in two cases, and says so in the witness note.
  - The fixture declared the fill rule (`provenance.base_stats_source.fill_rule = trinity`). This case was added by the
    closeout review; without it every level-90 fixture would have raised `MissingBaseStats`.
  - The compiler left stats unresolved only because a level row is missing. This happened for
    `arms-warrior-plate-2h-no-fill-rule`: the compiler reported `level-row-missing`, levels present 1–80.
- The old flat `{identity, contributions}` shape is still accepted.
- In directory mode, an unusable fixture is recorded under `refused_fixtures`
  with its reason; a fixture named explicitly with `--fixture` raises instead.

**Three record families per fixture:**
1. **Trinity vs charstats.** The same records as the built-in witnesses, with
   charstats re-resolving from the compiler's handed-over contributions.
2. **`compiled_trinity.*`.** The probe is driven from the compiler's **own**
   stat stages, max health and AP. **All match on all 11 fixtures that store compiled stats** (every fixture except the no-fill-rule witness).
3. **`compiled.*`.** The compiler's stored answers are compared with the
   charstats re-resolution. This is a consistency check, not a Trinity
   comparison.

| fixture | level | fill rule | compared records | mismatches | max HP (charstats) |
|---|---|---|---|---|---|
| arms-warrior-plate-2h-no-fill-rule | 90 | trinity (adapter opted in) | 49 | 1 (mastery) | 2,368,940 |
| arms-warrior-plate-2h | 90 | trinity (fixture) | 68 | 1 | 2,368,940 |
| arms-warrior-plate-dw-learned | 90 | trinity (fixture) | 68 | 1 | 2,367,560 |
| balance-druid-leather-caster | 90 | trinity (fixture) | 68 | 1 | 2,368,980 |
| bm-hunter-mail-ranged-pet | 90 | trinity (fixture) | 68 | 4 (mastery + 3 `compiled.*`) | 2,368,960 |
| demonology-warlock-cloth-pet | 90 | trinity (fixture) | 68 | 3 (mastery + 2) | 2,373,000 |
| enhancement-shaman-mail-dw | 90 | trinity (fixture) | 68 | 1 | 2,074,580 |
| frost-dk-plate-dw-tier4 | 90 | trinity (fixture) | 68 | 1 | 2,368,960 |
| fury-warrior-plate-titan-grip | 90 | trinity (fixture) | 68 | 1 | 2,161,940 |
| holy-priest-cloth-healer-tier4 | 90 | trinity (fixture) | 68 | 3 (mastery + 2) | 2,368,980 |
| subtlety-rogue-leather-dw | 90 | trinity (fixture) | 68 | 4 (mastery + 3) | 2,047,840 |
| windwalker-monk-leather-dw-tier4 | 90 | trinity (fixture) | 68 | 1 | 2,185,620 |

**`compiler-vs-charstats` (10 records on 4 fixtures).**
- The compiler applies an armour specialisation found through the class skill
  line: Hunter 86538, Rogue 86092, Warlock 86091, Priest 89745.
- `charstats.acquisition.armor_specializations` searches SpecializationSpells
  only, so a plain charstats re-resolution applies no 5%.
- Example: Hunter Agility is 20,105 (compiler) vs 19,148 (charstats), and AP
  and RAP follow.
- This is a charstats scope gap, **not a Trinity disagreement**: the Trinity
  arithmetic over the compiler's stages matches.

The no-fill-rule fixture has no `compiled.*` records, because the compiler stored
no stats for it. The base stats of every fixture are the level-80 copy
(`trinity-consumer(fill-rule)`).

No fixture's pre-percent total hit a §9.4 crossing (0 `binary32` records among
the fixtures).

## 10. Witnesses (server/differential view)

| witness | purpose | outcome | evidence |
|---|---|---|---|
| Warrior L80 Human / Orc | race modifier path (Orc +3/−3/+1/−1) | all integer fields match; Orc Strength 17650 | world-db-fact + differential |
| Priest L80 (Shadow) | caster; base mana 50,000; spell power from Intellect | match (except the mastery gap) | differential |
| Hunter L80 (BM, Orc) | Agility AP and RAP | match; RAP from `(level + agi) × RAPPerAgi` | differential |
| Evoker L80 (Devastation, Dracthyr 52) | missing-class question | **Evoker is present** (72 rows; levels 2–9 absent); race 52 modifiers are zero | world-db-fact |
| Evoker L5 with fill rule | gap inside the rows | Trinity serves the **level-1 cell** (3/4/6/6/0); unreachable at creation (start level 10) | trinity-consumer(fill-rule) |
| Warrior L90 / Priest L90 with fill rule | levels above the rows | level-80 cell copied; 10 error lines per pair; probe = adapter | trinity-consumer(fill-rule); Retail **unresolved** |
| playable race missing from `player_racestats` | — | **none exists** (31 = 31). Nearest: Haranir 86/91, zero modifiers from an update (`warrior-arms-haranir-80` equals Human); 95/96 are NPC-only | world-db-fact + db2-fact |
| HpPerSta at 80 vs 90 | charstats says 20 at 90 | **20.0 at 80, 81 and 90**; 13.0 at 70; 1.0 at 1 | db2-fact |
| armour spec / crossing | float TOTAL_PCT | §9.4 | differential |
| naked Warrior 80 / 90 | pure base pipeline | max HP 1,729,040 = 86452 × 20; armor 24,352 = 12176 × 2; AP 17,647 | differential |
| BuildPlayerLevelInfo 80→90 | non-default config | 17669/12187/86474/12005/0 | trinity-probe |

## 11. Reproducibility

### Part E

```
cd scripts/research   # from the wowlab-data root
python3 character_prep.py all                    # ~21 s; schema, inventory, initial-state, graph, 12 fixtures, 12 compiled, index
python3 character_prep.py compile --fixture ../../docs/research/character-prep-corpora/fixtures/arms-warrior-plate-2h.json [--json]
python3 character_prep.py explain --fixture <f> --path /derived/stats/value/stats/Strength
python3 character_prep.py validate --fixture <f>
python3 character_prep.py initial-state --fixture <f>
python3 character_prep.py graph --dot
python3 character_prep.py inventory
uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/test_cp_fixture.py tests/test_cp_compiler.py tests/test_cp_graph.py -q -p no:cacheprovider
# 153 passed after the closeout review (50 + 86 + 17); full tests/test_cp_*.py: 237 passed
```

Two consecutive `all` runs produced byte-identical output.

| corpus | rows | sha256 (after the closeout review) |
|---|---|---|
| fixture-schema.json | 11 sections, 19 slots | `d8df96cded5366c9162c8f5057a2101c6aa6961154ad03f1d84763826ab54864` |
| input-inventory.json | 58 inputs | `5d87b5f8fc95874ac18dfad8eabc8427df9865b10b9bf515f029f8e3780b5db6` |
| initial-state.json | 9 classes, 21 power types | `d7998786468eae44f47e61f3e895953fbe21f790c09c55c149f7880cf5e8cf25` |
| dependency-graph.json | 26 nodes, 41 edges | `29eb157310422bac1ab2d2ab0e5d013ee6b05cdb51f121e4027cf2f75356619e` |
| fixtures/index.json | 12 fixtures | `a7b0a3302778d12e640645df3490129b289dfcf4c8cda25fe295135be41316b1` |
| fixtures/*.json, *.compiled.json | 12 + 12 (255–286 KB each compiled) | see `index.json` |

The closeout review ran `all` and then `differential` twice. All corpora and fixtures were byte-identical across the two runs.
No compiled fixture or corpus contains a host path (`rg '/home/|/tmp/'`). `fixture_path` and `base_stats_source.path` are
repository-relative, or the bare file name outside the repository (test).

### Part F

Run from `scripts/research`. `TDB=../../../../bag/tdb/TDB_full_world_1200.26021_2026_02_06.sql`.

```
make -C tools/tc_prep_probe                                   # extract + build (g++ 13.3.0 -std=c++20 -O0 -ffp-contract=off)
python3 character_prep.py base-stat-gap --tdb $TDB --probe --out ../../docs/research/character-prep-corpora/base-stat-gap.json   # ~5 s; run first (pairs)
python3 character_prep.py server-inputs --tdb $TDB --out ../../docs/research/character-prep-corpora/server-inputs.json           # ~17 s
python3 character_prep.py differential --out ../../docs/research/character-prep-corpora/differential.json                        # ~12 s; reads fixtures/*.compiled.json (run after E's `all`)
python3 character_prep.py server-inputs --class 1 --race 1 --level 90        # identity query (exit 1 if unsatisfied)
python3 character_prep.py basestats --class 1 --race 2 --level 90 --fill-rule trinity
uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/test_cp_server_inputs.py tests/test_cp_differential.py -q -p no:cacheprovider
#   -> 84 passed (unchanged by the closeout review; base-stat-gap.json and server-inputs.json regenerated with identical sha256)
```

| corpus | size | sha256 |
|---|---|---|
| `character-prep-corpora/base-stat-gap.json` | 40 K | `3963c0be0c182302c6a04aab14503473f4b3968439c67a90f722a7c112d868cf` |
| `character-prep-corpora/server-inputs.json` | 36 K | `cfd9870e7f5208c4618b6785e1055021c59b4b8d7fb1aa6b7ec9260b21243e31` |
| `character-prep-corpora/differential.json` | 767 K | `8893370bc31c82eefb21812b56050da5224fde4cbd6a1dd436d1c97fdbe0132f` (13 built-in + 12 E fixtures, closeout-review rerun; regenerate after any `character_prep.py all`) |

All three were regenerated twice with byte-identical output. Provenance blocks
carry the snapshot `12.1.0.69497`, Trinity `7f3d43b…`, the TDB release, the
generator command and wowlab-data `2773a88`.

Files:
- `character_prep/{basestats_tdb,server_inputs,differential}.py`
- `tools/tc_prep_probe/{extract.py,probe.cpp,Makefile,.gitignore}`
- `tests/test_cp_{server_inputs,differential}.py`
- 3 lines in `character_prep/cli.py` `COMMAND_MODULES`

## 12. Unresolved and build-skew populations

### Part E

| item | class | why | reopen condition |
|---|---|---|---|
| base stats for levels 81–90 | unresolved | `player_classlevelstats` stops at 80; Trinity copies level 80 (`Globals/ObjectMgr.cpp:4349-4356`, agent F) | a TDB release with rows for 81–90, or an observed Retail value |
| AcquireMethod 4 default-skill spells (2–10 per fixture, e.g. 54197, 163201) | unresolved | learned only if `ShowFutureSpellPlayerConditionID` and the conditions table allow it (`Player.cpp:25409-25415`). 54197/50977: conditions table only (no row in the pinned TDB, so learned); the rest: PlayerCondition 83446 = host map + level (R3-5) | evaluate PlayerCondition + world `conditions` (CONDITION_SOURCE_TYPE_SKILL_LINE_ABILITY) |
| SKILL_RANGE_RANK default-skill values | unresolved | the value needs world-DB `skill_tiers` (`Globals/ObjectMgr.cpp:9008-9009`); it matters only for AutomaticSkillRank abilities | supply `skill_tiers` from the TDB |
| controlled-unit hook | unresolved | `controlled_units.ownership` has no `CHARACTER_PREP_HOOK` | Track A exposes `CHARACTER_PREP_HOOK(context) -> dict` |
| aura stages other than armour spec | unresolved | charstats blocker 6; passives listed but their percent stages are not applied | generic `SPELL_AURA_MOD_*_PERCENT` evaluation |
| mastery_value −8 | model-gap | 114585 SPELL_AURA_MASTERY not summed by charstats (`Entities/Unit/StatSystem.cpp:549-550`) | charstats adds the aura term |
| crafted stat modifiers | unresolved | ItemBonus type 25 is NYI (`DataStores/DBCEnums.h:1273`); no ItemModifiedCraftingStat | Trinity implements type 25, or the table appears |
| legacy azerite | legacy-only | powers need the equipped Heart of Azeroth 158075 (`Player.cpp:8509`) | a fixture equips 158075 (then selections + heart level are identity) |
| hunter pet spec 74 / demon 30146 | db2-fact (closeout review) | 74 = "Ferocity" (ClassID 0), 30146 = "Summon Felguard"; the section's semantics stay Track A's | Track A validation of the controlled-unit section (R3-10) |
| learned-spell state beyond the fixture's `learned_spells` | unresolved | 3,789 AcquireMethod-0 class-line abilities are character state; `character_spell` active/disabled flags and `spell_learn_spell` dependents are not modelled | a Retail export of known spells (R3-2) |
| glyphs, saved skill values | unresolved | restored by `LoadFromDB`, absent from the fixture format (R3-3, R3-4) | format sections + combat-relevance census |
| item requirements that need character/host state | unresolved | reputation, profession skill, holiday, required-level curve, ItemLimitCategory quantities (R3-1, R3-8) | the corresponding fixture inputs |
| quest/achievement trait conditions | structural-inference (none today) | class trees use none (E3) | any class-tree TraitCond gains a QuestID/AchievementID, or a quest-gated currency source gets Amount > 0 |
| marker / newer spells in fixtures (e.g. 1295219, 1295323, 1295328) | build-skew | spell newer than Trinity's 12.0.7 client build (`dummy-corpora/build-skew.json`) | a Trinity revision that supports 12.1.0.69497 |
| PvP talent activation, PvP item levels | encounter | host/area state (`Player.cpp:27778`, `:30928`) | a host input specification |
| float/double in max power | differential (F) | `lroundf` (`Entities/Unit/StatSystem.cpp:344`) reachable only through a percent power aura | agent F probe |

### Part F

| item | class | why | reopen condition |
|---|---|---|---|
| base stats at levels 81–90 | unresolved (Trinity: `trinity-consumer(fill-rule)` = copy of level 80) | the pinned TDB has no rows above 80 | a TDB release with rows 81–90, or naked Retail character-sheet observations at 81–90 |
| Stamina 6655 (L69) → 6397 (L70) in all 13 classes | world-db-fact; Retail unresolved | authored non-monotonicity; `VerifiedBuild` = 0 gives no attestation | Retail naked observations at 69 and 70 |
| all-zero race modifiers for 52, 70, 84, 85, 86, 91 | world-db-fact; Retail unresolved | 86/91 were written as zeros by `2026_03_07_01_world.sql` | two naked Retail observations differing only in race |
| Evoker levels 2–9 | trinity-consumer(fill-rule) | rows absent; unreachable by creation (start level 10) | a boosted or scripted path that creates an Evoker below 10 |
| 114585 applied to every player | trinity-consumer (static) + db2-fact | runtime application not executed | a Trinity character dump or Retail character sheet showing Mastery 8 + rating term |
| armour-spec float truncation (−1 point) | differential; Retail unresolved | Trinity float vs charstats double | a Retail observation of a total that is a multiple of 20 with an armour spec |
| int16 `m_baseRatingValue` wrap | unresolved | needs one rating slot above 32,767 | gearing census of the maximum per-CR rating total |
| `lroundf` half-away in `UpdateMaxPower` | trinity-probe; reachability unresolved | needs a percent mana aura that yields an exact .5 | a current percent-mana aura in scope |
| `BuildPlayerLevelInfo` | trinity-consumer; not the default path | only when `MaxPlayerLevel` < level | a server configuration below 90 |
| `GetDodgeFromAgility`, hit chance 7.5f/15.0f | legacy-only | commented-out body / no current CR_HIT input | current gear carrying hit rating |
| session expansion, non-default `MaxPlayerLevel` | caller-supplied | account and server state | the importing server's configuration |
| differential vs a changed E fixture set | pending by design | `differential.json` reflects the 12 fixtures present at generation (closeout review rerun) | E regenerates fixtures, then rerun `differential` |

Invariants hold throughout:

    absent from pinned Trinity != absent from Retail
    unsupported by research     != inert
    structural similarity       != semantic equivalence
    Trinity is the best available consumer oracle, not unquestionable Retail truth

## 13. Cross-part overlaps

### Part E

**Contradictions with existing research reports** (charstats is not modified):
- `character-stat-pipeline-archaeology.md:614` says "armour specialization | a `SpecializationSpells` row (see §12) | **yes**". E7 finds that for 6 classes the passive comes from the class skill line, so charstats applies nothing for Rogue, Hunter, Warlock, Mage, Priest and Evoker.
- `charstats/acquisition.py:260` computes `bit = race_id - 1`, while Trinity uses `RaceMask::GetRaceBit` (`Miscellaneous/RaceMask.h:97-146`). This gives wrong bits for races 34–37 and 52 and raises for 70/84/85/86/91. The same function also accepts AcquireMethod 0 (37 extra spells for Human Warrior), which `LearnSkillRewardedSpells` never auto-learns (`Player.cpp:25416-25417`).

**Corrections to earlier notes in this pass** (not report text):
- The previous E draft cited `Player.cpp:2225-2257` as `Player::Create`. It is `Player::GiveLevel`.
- The loader is `ObjectMgr::LoadPlayerInfo` (`Globals/ObjectMgr.cpp:3807`), per agent F; this is now fixed in `compiler.py`.

**Handoffs to other agents:**
- **Agent F** consumes `fixtures/*.compiled.json`; the shape is in `E/HANDOFF.md` ("Compiled fixture shape (for F)"). **The compiled fixtures were regenerated after F finished, so the lead must rerun `character_prep.py differential`.**
  - To reproduce E's stats for Hunter, Rogue, Warlock and Priest, F's re-resolution must apply the same armour-spec spell: use `character_prep.compiler.armor_specializations_as`.
  - F's 114585 (+8 mastery) finding is recorded as a `blocked` model-gap in every compiled fixture.
- **Agent C's weapon hook** (`weapon_combat.weapon.CHARACTER_PREP_HOOK`, Track B package; "Track C" here meant agent C) is joined. E passes `identity` (charstats identity dicts) and `section.slots` (gearing weapon facts). DW/TG capability is computed twice: from E's acquired spells (`derived.spells.value.gates`) and from Track C's `weapon-sources.json` `capability_by_spec`. The two agreed on 9 of 11 compiled fixtures and **disagreed on both Arms fixtures (spec 71)**. After reviewer G2's fix, the hook counts 296087 only when it is supplied as learned. After this review, the compiler passes `identity.learned_spells`. The two now agree on all 12 fixtures, including the learned-spell witness (§14).
  - Track C reports dual wield through **296087 "Dual Wield"**, which is `SkillLineAbility` 40461 on the Warrior class line 840 with ClassMask 0 and **AcquireMethod 0 (Learned)**. It reaches C's `class_skill_lines` scope (`dummy_semantics.scope` with `include_class_skills=True`, which does not filter on AcquireMethod).
  - `LearnSkillRewardedSpells` skips AcquireMethod 0 (`Player.cpp:25416-25417`), so a fresh Arms warrior has **no** Dual Wield in Trinity unless 296087 was learned another way (trainer or quest; character-DB state). E's gate says false (trinity-consumer).
  - Both computations agree on Fury: DW through 231842 (spec spell) and TG through 46917. The lead should dedupe the two computations. Reopen condition: a consumer that teaches 296087 to warriors.
- **Track A** should expose `controlled_units.ownership.CHARACTER_PREP_HOOK(context: dict) -> dict`. The context has keys `fixture, identity, gear, spells, stats, section, snapshot_build, trinitycore_commit`.
- **Default-skill Dual Wield.** Track C also ported the default-skill path (`weapon-sources.json` `default_skill_grants_by_class`) and lists 674 for classes 3/4/12. E's port agrees.
  - SkillRaceClassInfo 913 does give Death Knights skill 118, but `SkillLineAbility` 610's ClassMask 2573 (classes 1, 3, 4, 10, 12) excludes class 6, so Frost gets 674 from `SpecializationSpells` (spec 251).
  - SkillRaceClassInfo 883 (class 7) has Availability 2, so it is not a default skill; shamans get dual wield from spec spell 86629.
  - Warriors and Monks are in the ability's ClassMask but have no Availability-1 SkillRaceClassInfo row for skill 118.

### Part F

- **E (compiler).**
  - `compiler.py` names the loader "ObjectMgr::LoadPlayerLevelInfo". The real
    function is `ObjectMgr::LoadPlayerInfo` (`ObjectMgr.cpp:3807`).
  - E's copy-loop coordinate `4349-4356` is correct. The race-cell range is
    `4319-4330`; E cites `4319-4329`.
  - E's armour-specialisation discovery over all acquired spells
    (86538 / 86092 / 86091 / 89745 via class skill lines) is **ahead of
    charstats**. F classifies the resulting stat differences
    `compiler-vs-charstats`.
  - E's `--world-db-corpus` requires an absolute path (`relative_to(ROOT)`
    raises `ValueError` on `../../…`).
- **E (inventory/graph).** Server inputs and their phases (§7) overlap E's
  input inventory. F's `server-inputs.json` is the server-side authority for
  row counts and coordinates.
- **A/B (controlled units).** `pet_levelstats` (2,715 rows, 32 entries) and
  `creature_template_spell` (9,586) are listed as reference-other-track only.
- **C (weapon).** AP enters here only as prepared stat totals. Weapon damage is
  not probed.
- **D (attack table).** Hit chance 7.5f/15.0f and the commented-out
  `GetDodgeFromAgility` are listed as legacy-only. D owns the attack table.
- **Lead / charstats owners.** Two model gaps belong in the cross-track
  unknowns, not in charstats edits by this pass:
  - the 114585 mastery term (§9.3);
  - the double-vs-float TOTAL_PCT (§9.4).

## 14. Lead reconciliation

**Arms dual wield (parts E and weapon-combat part C disagreed).** Part C listed spell 296087
"Dual Wield" for specs 71/72/73 because it sits on the Warrior class skill line 840
(`SkillLineAbility` 40461, AcquireMethod 0). Part E's compiler says Arms cannot dual wield. The
source settles it:

- `SkillLineAbilityAcquireMethod::Learned = 0` (`DBCEnums.h:2362`) falls into
  `default: continue` in `Player::LearnSkillRewardedSpells` (`Player.cpp:25404-25417`), so it is
  never learned automatically.
- Spell 296088 "Learn Dual Wield" has `SPELL_EFFECT_LEARN_SPELL` (36) → 296087 (SpellEffect
  765855) and a second learn effect → 296306; no `SkillLineAbility`, `SpecializationSpells`,
  `TraitDefinition` or authored trigger edge grants 296088 in the snapshot.

The closeout review (§15) re-checked this by hand and extended it.
- **DB2.**
  - 296087 appears only in SkillLineAbility 40461 (skill 840, AcquireMethod 0, ClassMask 0) and in its own SpellEffect 765854 (effect 40).
  - 296088 appears only in its own SpellEffect rows 765855 and 765857.
  - No SpellEffect triggers 296088, and no ItemEffect names either spell.
  - The one ModifierTree hit (428002, Type 200 `PlayerHasTransmog`, Asset 296088) is an ItemModifiedAppearance id, not the spell.
- **World DB** (pinned TDB + update replay): no `quest_template.RewardSpell`, `trainer_spell`, `spell_learn_spell`, `playercreateinfo_cast_spell` or `playercreateinfo_spell_custom` row names 296087 or 296088. The only trainer row for a dual-wield spell is legacy 674 (trainer 40, ReqLevel 20).
- **Scripts:** no hit in `src/`.
- **Skills.** No SkillRaceClassInfo row for skill 118 includes class 1. A warrior's saved skill 118 would be deleted as forbidden (`Player.cpp:27250-27258`), so 674 cannot reach Arms through a saved skill either.

Verdict: whether an Arms warrior may dual wield is **learned-spell character state**: a
`character_spell` row restored by `_LoadSpells` (`Player.cpp:18609`), not derivable.
- *Corrected claim:* this section previously said the fixture format "needs, and has, room for explicitly learned
  spells". That was false when written: `fixture.py` had no such section. The closeout review added the optional
  identity section `learned_spells` (§4). It is:
  - validated fail-closed;
  - rooted by the compiler as `learned-spell`;
  - passed to the weapon hook as `identity.learned_spells`;
  - listed in the inventory and the graph.
- The witness `arms-warrior-plate-dw-learned` (`learned_spells: [296087]`) passes the off-hand gate. The same fixture
  without the section fails it (tests).
- Absent that input, the compiler's "no" is the correct fail-closed answer. Fury's dual wield is unaffected (spec spell).

Reopen: a Retail character export listing known spells, or a data source for 296088.

**Differential freshness.** Part E regenerated its compiled fixtures after part F's last run;
the lead reran `python3 character_prep.py differential` afterwards. Result unchanged:
24 witnesses, 1,271 records, 35 mismatches, 0 refused fixtures. The closeout review changed the fixtures (level 90, fill-rule
opt-in, learned-spell witness) and reran `all` and then `differential`: 25 witnesses, 1,389 records, 36 mismatches, 0 refused
(§9.2).

## 15. Closeout review

Hostile reviewer G3, 2026-09-16. Scope: this report (including the lead's "Scope and result" list and §14), the package
`scripts/research/character_prep/`, `tests/test_cp_*.py`, `tools/tc_prep_probe/`, the corpora under
`docs/research/character-prep-corpora/` and `docs/research/world-db-corpora/player-base-stats.json`.

### 15.1 Method

- **Claim list.** Every headline, every "never/always/only/all/none", every count and every evidence class stronger than
  `structural-inference`.
- **Source re-reads.** Each high-value claim was re-derived from TrinityCore `7f3d43b`, reading the whole surrounding function
  (early returns, branches, callers). Functions read:
  - `Player::LoadFromDB` (`Player.cpp:17925-18840`) and its `_Load*` helpers: `_LoadSkills`, `_LoadSpells`, `_LoadGlyphAuras`,
    `_LoadInventory`;
  - `LearnDefaultSkills` and `LearnSkillRewardedSpells`;
  - `CanEquipItem`, `CanUseItem`, `CanEquipUniqueItem`, `IsTwoHandUsed`, `CanTitanGrip`, `AutoUnequipOffhandIfNeed`;
  - `TraitMgr` 400-1031;
  - `ObjectMgr::LoadPlayerInfo`, `GetSkillRangeType`, `World.cpp:750`, `SharedDefines.h:107-138`;
  - `Unit::GetTotalAuraMultiplier`, `HandleModTotalPercentStat`, `UpdateAllStats`, `UpdatePowerRegen`;
  - `SpellHistory::LoadFromDB`, `GetMaxCharges`, `ApplyEquipCooldown`, `Unit::Update`, `ModifyAuraState`,
    `HandlePassiveSpellLearn`, `AddSpell`, `ConditionMgr::IsPlayerMeetingCondition`.
- **Independent queries** over `data/tables` (scripts in the scratchpad `G3/`):
  - the E3 trait-condition census (`e3.py`);
  - every DB2 reference to 296087/296088;
  - the SkillRaceClassInfo rows of skills 118/183/840;
  - 114585;
  - item required levels of every fixture item through `gearing` (`reqlvl.py`);
  - ItemSparse requirement columns over the current-gear corpus;
  - glyph spell auras;
  - AcquireMethod-0 class-line abilities;
  - PlayerCondition 83446 and ModifierTree 143919;
  - SPELL_AURA_MOD_MAX_CHARGES passives.
- **New world-DB extraction** (`tools/tdb_world_extract.py`, pinned TDB + 522 updates, 2 unparsed locale statements):
  `quest_template`, `trainer_spell`, `spell_learn_spell`, `playercreateinfo_spell_custom` / `_cast_spell`,
  `conditions` (17,344 rows, 14 of source type 35).
- **Numeric checks.**
  - The armour-spec sweep was reproduced in numpy binary32: 4,045 divergences, all +1, all multiples of 20. 27,640 × `1.05f`
    = 29,021.998046875 was also checked by hand.
  - The probe was rebuilt (`make -B`), and `extract.py`'s verbatim/`RETYPED` contract was read.
- **Fail-closed checks.** 28 malformed fixtures were fed to `validate_fixture` and `load_fixture_json`.
- **Determinism.** `all` + `differential` were run twice (identical sha256). `base-stat-gap` and `server-inputs` were
  regenerated: identical to the committed sha256.

### 15.2 Findings

| id | claim | verdict | action | evidence |
|---|---|---|---|---|
| G3-1 | "All 11 synthetic fixtures compile with `validation.ok`" (E10); level-80 stats table (§5) | **defect** | Every item in the 10 level-80 fixtures has required level 90 (ItemBonus 49 → ItemScalingConfig 219/318 RequiredLevel 90). `CanUseItem` refuses such an item (`Player.cpp:11148-11149`), and `_LoadInventory` mails it (`:19295-19299`). Fixed: the compiler checks the required level; the fixtures are level 90 with an explicit fill-rule opt-in; the tables were rewritten; a test guards it. | trinity-consumer + db2-fact; R3-1 |
| G3-2 | §14 / headline 1: the format "needs, and has, room for explicitly learned spells" | **defect** (the lead self-reported it) | Added the optional identity section `learned_spells`: fail-closed, compiler root `learned-spell`, hook `identity.learned_spells`, inventory row, graph node and edge, schema. Witness `arms-warrior-plate-dw-learned`; a negative test (without it the OFFHAND gate fails); the hook and the compiler agree on all 12 fixtures (test). | trinity-consumer; R3-2 |
| G3-3 | §14: nothing grants 296088 / 296087 | confirmed + extended | DB2 (SpellEffect, SkillLineAbility, ItemEffect, ModifierTree coincidence), world DB (quest, trainer, spell_learn_spell, playercreateinfo) and `src/` all checked. SkillRaceClassInfo gives skill 118 to no class-1 row, so no saved-skill path exists either. | db2-fact + world-db-fact |
| G3-4 | Completeness of the input inventory versus `LoadFromDB` | **defect** (missing rows) | Added: learned spells (identity); glyphs and saved skills (identity format gaps, `unresolved`); fill-rule opt-in (server); saved auras (encounter); reputation; action bars, equipment sets, garrison, artifacts (excluded). Talents (`_LoadTalents`), currencies, quests, achievements, collections, pet stable, PvP talents and trait configs were already covered. 58 rows. | trinity-consumer; R3-3, R3-4 |
| G3-5 | `CanUseItem` / `CanEquipItem` checks in the compiler | **defect** (fail-open) | Added: required level, AllowableClass/Race, faction flags, internal item, RequiredAbility, unique-equippable, INVTYPE_WEAPONOFFHAND dual wield, one-hand polearm off-hand, `IsTwoHandUsed`, Titan's Grip subclass mask. Reported unresolved: RequiredSkill outside default skills, reputation, holiday, required-level curve, ItemLimitCategory quantity. Tests added. | trinity-consumer; R3-8 |
| G3-6 | "Every fixture has 2–10 AcquireMethod-4 spells that stay unresolved" | **overstated** | 54197 and 50977 have no ShowFutureSpellPlayerConditionID and no source-35 conditions row, so Trinity learns them. The rest depend on PlayerCondition 83446 (not on NPE maps 2175/2236/2261/2369, ContentTuning 958), which is host state. The compiler now states the exact reason per spell. The spells stay unresolved because the conditions table is not a compiler input. | world-db-fact + db2-fact; R3-5 |
| G3-7 | "Health full" as `trinity-consumer` | **overstated** | No Trinity construction path refills health after gear, except GiveLevel (`Unit::SetMaxHealth` `Unit.cpp:10016-10030`). Lowered to structural-inference (a convention) in the compiled output, the corpus and the text. | R3-6 |
| G3-8 | Charges "full: MaxCharges + SPELL_AURA_MOD_MAX_CHARGES" | **defect** (the value omitted the aura term) | Compiled `max_charges` now adds the base points of active passives with aura 411. Example: Arms Overpower 7384 + 385571. The sum is structural-inference. | R3-7 |
| G3-9 | "Cooldowns start ready" | **overstated** | True on the load path (`QuickEquipItem`). `EquipItem` starts a 30 s cooldown on on-use item spells (`Player.cpp:25146-25186`). Qualified in the text. | trinity-consumer; R3-9 |
| G3-10 | Dependency graph "25 nodes, 51 edges" | **defect** | The corpus had 40 edges. Now 26 nodes and 41 edges (learned_spells). Acyclic (graphlib), every derived node has an owner and incoming edges, and the edge set equals the compiler dependencies (test). | differential (self) |
| G3-11 | "Human Warrior … 33 race-gated" default-skill spells | **defect** | 34 (`default_skill_spells(1, 1, 80/90)`). | db2-fact |
| G3-12 | E5 "674 reaches rogues and hunters" | **defect** (incomplete) | Rogues, hunters **and demon hunters** (SkillRaceClassInfo 131/1661; ClassMask 2573). | db2-fact |
| G3-13 | Fail-closed structural validation | **defect** (fail-open cases) | Rejected now: `fixture_version` true/1.0; observation precision bool/NaN/negative; non-string units/captured_at/generator; the bare pointer `/derived/`; NaN/Infinity literals; malformed inline base stats (the compiler used to crash); absolute/`..` base path; a bad `fill_rule`. 16 new test cases. | — |
| G3-14 | Host paths in compiled output | **defect** (latent) | `load_base_stats` embedded an absolute path for a corpus outside the repository. A relative `--fixture` path outside the repository was embedded as given. Both now reduce to a repository-relative path or the bare name (test). The committed corpora contain no host path. | — |
| G3-15 | "Trait validity is decidable from spec, level and config alone" | confirmed, scoped | Census re-derived: 13 trees, 2,896 nodes, 734 conditions, 0 quest/achievement/account conditions. All 12 tree currencies are TraitSourced (so the gold, CurrencyTypes and data-element branches `TraitMgr.cpp:425-466` are unreachable). 284 sources, 44 quest/achievement sources, all Amount 0. The claim holds only for the current class trees (reopen: E-12). | db2-fact |
| G3-16 | Set membership, weapon configuration, armour spec and mastery are derived | confirmed, with caveats | Armour spec rests on HasItemFitToSpellRequirements. Weapon configuration is derived except for learned-only DW (now an input). Mastery 114585 is derived but not summed by charstats (E-8). | trinity-consumer |
| G3-17 | 114585 chain (SLA 25920, AcquireMethod 2, ClassMask 0, no race mask; SRCI 5 ClassMask 16383, Availability 1, Flags 1170; effect 127950 aura 318 BasePoints 8; SpellMisc 464; no SpellLevels/shapeshift/aura-state/equipped rows) | confirmed | Re-queried. `LearnSkillRewardedSpells` `:25404-25432`, `HandlePassiveSpellLearn` `:3079-3104`, `AddSpell` passive cast `:2920-2929`. `CanUseMastery` gates the whole value (`StatSystem.cpp:543-547`), so the +8 applies only when the spec mastery spell is known. That holds for every fixture (spec mastery spells are roots). | trinity-consumer |
| G3-18 | Level 81–90 copy rule is exact | confirmed | `ObjectMgr.cpp:4348-4356` copies cell by cell (index `level` ← `level-1`), so 81…90 all equal 80. The sentinel is race-modified Strength. The compiler's unresolved text now names the originating row level (80), not the previous cell. | trinity-consumer |
| G3-19 | Default MaxPlayerLevel 90 | confirmed | `World.cpp:750` default `GetMaxLevelForExpansion(CURRENT_EXPANSION)`; `SharedDefines.h:107` (`EXPANSION_MIDNIGHT`), `:135-136` (→ 90); `worldserver.conf.dist:912` `MaxPlayerLevel = 90`; `:718` `Expansion = 11`. | trinity-consumer |
| G3-20 | Stamina drop 69 → 70 | confirmed | Raw TDB tuple `(1,69,1981,1367,6655,1347,0,0)` / `(1,70,2089,1442,6397,1421,0,0)`. The corpus shows the drop in all 13 classes, and no update touches `player_classlevelstats`. | world-db-fact |
| G3-21 | Armour-spec binary32 truncation, float path | confirmed | `GetTotalAuraMultiplier` accumulates in `SpellEffectValue` (double) and returns `static_cast<float>` (`Unit.cpp:5016-5033`). `HandleModTotalPercentStat` stores it in TOTAL_PCT (`SpellAuraEffects.cpp:3915-3925`). `GetTotalStatValue` multiplies in float; `UpdateStats` does `int32` (`StatSystem.cpp:110-112`). numpy binary32 sweep: 4,045 of 120,000, all +1, all multiples of 20. | differential |
| G3-22 | "No rate scales a prepared maximum" | confirmed; coordinate corrected | Config reads in `StatSystem.cpp`: Stats.Limits (`:495-506, 665-666, 703-704`) and `getRate` only at `:891` (`UpdatePowerRegen`; the text cited the table `:809-835` as the read). `InitStatsForLevel` reads only `CONFIG_MAX_PLAYER_LEVEL` (`Player.cpp:2335`). | trinity-consumer |
| G3-23 | Health-dependent passives one update late | confirmed | `HandlePassiveSpellLearn` `:3102-3103` checks the aura-state bit (0 at creation). `Unit::Update` sets states 2/6/13/21/23/24/25 only while alive (`Unit.cpp:474-483`). `ModifyAuraState` casts passives with a matching `CasterAuraState` on a 0→1 transition (`:6078-6098`). | trinity-consumer |
| G3-24 | Powers at start | confirmed | Rage carries PowerType flag 0x2000 (SetToMaxOnInitialLogIn), so it is full after Create (`Player.cpp:500-502`); InitStatsForLevel clamps rage (`:2490-2491`) and zeroes runic power (`:2493`); login zeroes lunar power (`:18725`). | db2-fact + trinity-consumer |
| G3-25 | Controlled-unit hook fails closed | confirmed | `call_hook` catches import, attribute, exception and non-dict results, and the node becomes `unresolved`. R3-10 states the required contract. Pet spec 74 (Ferocity) and 30146 (Summon Felguard) were verified. | db2-fact |
| G3-26 | Fixture format is provider-neutral; observations address every derived value | confirmed, scoped | No provider-bound field. `observations` resolve any `/derived/…` pointer, including list indices. Findings (`validation`, `unresolved`) are not addressable by design. The "no provider field outside this list feeds preparation" claim was weakened (§4). | structural-inference |
| G3-27 | Swing base attack time coordinate `Player.cpp:2395-2396` / `2396-2397` | defect (coordinate) | `:2394-2395`. | — |
| G3-28 | §13 "Track C (`weapon_combat…`)" | defect (naming) | The weapon hook is agent C's module in the Track B package. Wording fixed; the hooks now agree on all 12 fixtures. | — |
| G3-29 | Differential totals (headline 6, §9.2) | updated | After regeneration: 25 witnesses, 1,389 compared records (592 + 797), 36 mismatches (25 + 1 + 10), 0 refused. The level-90 fill witnesses have 49 compared records plus 1 informational fill record. The adapter now honours a fixture's declared fill rule. | differential |
| G3-30 | Probe compiles verbatim Trinity text | confirmed (spot check) | `make -B` rebuilt from the checkout. `RETYPED` lines exist verbatim (the build asserts it). The fill loop and `GetTotalStatValue` are extracted bodies. | trinity-probe |
| G3-31 | Graph "every derived node has an owner"; hook degrade | confirmed | Corpus check plus the existing monkeypatch test. | — |
| G3-32 | "Pandaren WW" fixture uses race 24 (Pandaren neutral) | noted, not changed | Trinity accepts the race (TeamForRace → PANDARIA_NEUTRAL, no faction-flagged item). A level-90 neutral Pandaren is not a realistic Retail character. | residual risk |

Counts: 32 findings over about 45 claims checked.

| verdict | count | findings |
|---|---|---|
| defects fixed | 14 | G3-1, G3-2, G3-4, G3-5, G3-8, G3-10, G3-11, G3-12, G3-13, G3-14, G3-27, G3-28, G3-29 (numbers), and the §7.3 coordinate within G3-22 |
| weakened | 4 | G3-6, G3-7, G3-9, and the provider sentence in §4 |
| newly unresolved (unknowns) | 10 | R3-1 … R3-10 |
| confirmed | 14 | G3-3, G3-15 … G3-21, G3-23 … G3-26, G3-30, G3-31 |

### 15.3 Fixes (files)

- **`character_prep/fixture.py`**
  - `learned_spells` section and accessor;
  - `server_inputs.base_stats.fill_rule`;
  - strict numbers, strings and paths;
  - inline base-stat structure;
  - NaN/Infinity rejection.
- **`character_prep/compiler.py`**
  - learned-spell roots and hook identity;
  - `_validate_item_requirements`;
  - extended `_validate_offhand`;
  - fill-rule base stats, with `/derived/stats/value/base` reported unresolved;
  - AcquireMethod-4 reasons;
  - `max_charges` aura term;
  - health convention and equip-cooldown note;
  - host-path helpers `_shown_path` and the `load_base_stats` path;
  - swing coordinate;
  - index fields `base_stats_fill_rule` / `base_stats_filled`.
- **`character_prep/report.py`**
  - `FIXTURE_LEVEL = 90`, fill-rule opt-in;
  - new plan `arms-warrior-plate-dw-learned`;
  - `NO_FILL_RULE_VARIANTS` replaces `LEVEL_90_VARIANTS`;
  - stale fixture cleanup.
- **`character_prep/inputs.py`**: 7 inventory rows; health/charges/cooldown/swing rules.
- **`character_prep/graph.py`**: `/fixture/learned_spells` node and edge.
- **`character_prep/differential.py`**: the adapter honours `provenance.base_stats_source.fill_rule`.
- **Tests**
  - `tests/test_cp_compiler.py`: +12 test functions, one agreement test parametrized over the fixtures, and the renamed witness;
  - `tests/test_cp_fixture.py`: +16 cases;
  - `tests/test_cp_graph.py`: identity-gap rule.
- **Corpora** (regenerated): `fixture-schema.json`, `input-inventory.json`, `initial-state.json`, `dependency-graph.json`,
  `fixtures/*` (12 + 12 + index; the level-80 files were removed; `arms-warrior-plate-2h-level90` was renamed
  `arms-warrior-plate-2h-no-fill-rule`), `differential.json`. `base-stat-gap.json` and `server-inputs.json` were
  regenerated unchanged.
- **`unknowns.json`**: R3-1 … R3-10; the E-1 witness was renamed; the E-13 text was brought up to date.
- **This report**: pins, headline list, E5, E10, §1–§6, §7.3, §9.2, §9.6, §11–§14.

### 15.4 Weakened conclusions

- **AcquireMethod-4 spells.** They are unresolved because the compiler does not read the `conditions` table or the host
  location. They are not all intrinsically undecidable (R3-5).
- **Initial health.** It is full by convention (structural-inference), not by a Trinity path (R3-6).
- **Ready cooldowns.** They hold on the load path only (R3-9).
- **Charge maxima.** They sum aura base points structurally (R3-7).
- **Provider completeness.** The provider shapes do not carry character-DB state (known spells, glyphs, skill values), and
  the format still lacks glyphs and skills (R3-3, R3-4).
- **Stat numbers in §5.** They now stand on Trinity's fill rule (level-80 cell at level 90). They are Trinity authoring,
  not Retail values.

### 15.5 Confirmed conclusions

The following were confirmed; §15.2 lists how each was verified (G3-3, G3-15 … G3-26, G3-30, G3-31):
- E1–E4, E6–E9;
- the §8 base-stat facts;
- Finding A (114585);
- Finding B (binary32);
- the rates claim;
- the one-update passive delay;
- the power rules;
- the fail-closed controlled-unit hook;
- the acyclic graph with owners;
- §14's verdict.

### 15.6 Residual risks

- **No Retail observation exists for any prepared value.** Every number is Trinity-consumer or Python.
  `absent from pinned Trinity != absent from Retail`.
- **All fixture stats depend on the fill rule** (level-80 cell served at 90). A TDB release with level-90 rows will change
  every stat in §5 and §9.6.
- **Glyphs, saved skills and most learned-spell state are identity inputs the format still lacks.** A real character
  export may therefore compile "ok" while missing combat-relevant spells (R3-2 … R3-4).
- **Item requirements that need character state are reported, not decided.** These are reputation, profession skill,
  holiday and limit-category quantity (R3-8). Gem unique-equip is not checked.
- **The trait-validity census is snapshot-bound.** Any class-tree TraitCond that gains a quest, achievement or data element
  reopens it (E-12).
- **The windwalker fixture uses race 24** (neutral Pandaren), which Trinity accepts but Retail characters at 90 do not use.
- **The combined cross-track artifact is stale.** `combat-prep-corpora/cross-track-unknowns.json` was not rewritten by this
  review (`merge_unknowns.py --check` only; 131 entries validate). The lead must rerun the merge.
