# Combat preparation synthesis (controlled units, weapon combat, character preparation)

## Scope and result

This document joins three research reports written in one coordinated pass:

| report | question |
|---|---|
| [`controlled-unit-archaeology.md`](controlled-unit-archaeology.md) | pets, guardians, summons, totems and charmed units: population, ownership, inheritance, abilities, lifecycle |
| [`auto-attack-weapon-archaeology.md`](auto-attack-weapon-archaeology.md) | the prepared weapon, swing scheduling, the white-swing table, damage arithmetic, weapon specials, proc events |
| [`character-preparation-closure.md`](character-preparation-closure.md) | which source inputs build an initial Player, which are derivable, and which are server- or caller-supplied |

It answers the seven cross-track questions of the pass, lists the defects of the pinned oracle
that a consumer must not copy, the numeric boundaries found, the errata against earlier
reports, and the observations that would best falsify the uncertain conclusions. The
machine-readable companion is
[`combat-prep-corpora/cross-track-unknowns.json`](combat-prep-corpora/cross-track-unknowns.json).

Research only. Nothing here designs, selects or steers Core work. Where Core vocabulary is
named, §3 says where Core uses the term: `core/docs/architecture.md`, or (marked as such) an
identifier in Core's code or a term of `core/docs/development-ledger.md`. All three were read
read-only. The mapping only says which findings have a matching Core term and which do not.
It does not claim that Core supports a finding.

    absent from pinned Trinity != absent from Retail
    unsupported by research     != inert
    structural similarity       != semantic equivalence
    Trinity is the best available consumer oracle, not unquestionable Retail truth

| pin | value |
|---|---|
| data snapshot | Wago `12.1.0.69497` (`data/tables/`) |
| wowlab-data base | `2773a88` (branch `research`) |
| TrinityCore | `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` (client builds ≤ 12.0.7.68453) |
| world DB | `TDB_full_world_1200.26021_2026_02_06.sql`, sha256 `54ddf4c12d6034a3c61e9b5683c2578de82d826d9090041a5e0477a164f5172d`, plus the 522-file `sql/updates/world/master` replay |
| toolchain | Python 3.12.3, `uv 0.12.4`, `pytest==8.4.2`, `hypothesis==6.140.3`, `g++ 13.3.0 -std=c++20 -O0 -ffp-contract=off` |
| dbc-resolver / WoWDBDefs | not used by this pass. Columns are read by their snapshot CSV names. Where the 12.1 names differ from Trinity's (ContentTuning), the reports map them by position against `DB2LoadInfo` and class the mapping `structural-inference` (CU §7.2 #11, WC §10). |

Evidence classes used everywhere: `db2-fact`, `world-db-fact`, `trinity-consumer`,
`trinity-probe` (verbatim Trinity text compiled and executed), `differential` (Python oracle
against the probe), `structural-inference`, `legacy-only`, `build-skew`, `unresolved`.

---

## 1. Exact inputs per prepared object

"Identity" means the caller must supply it. "Derived" means the preparation must compute it
and must not accept it from a caller. "Server" means Trinity takes it from its world database
or configuration. "Unresolved" means no source available to this pass supplies it.

### 1.1 Player

| input | class | source / consumer | report |
|---|---|---|---|
| build (snapshot, Trinity, TDB pins) | identity (provenance) | fixture `provenance` | CP §4 |
| race, class, spec, level (1..90) | identity | `Player.cpp:387`, `World.cpp:750` | CP §1 |
| (race, class) pair exists in `playercreateinfo` | server, identity gate (281 pairs; `Player::Create` refuses any other pair) | `Player.cpp:396-402`, `ObjectMgr.cpp:4321-4323` | CP §7.2 |
| session expansion | caller-supplied (it affects only the MaxLevel update field) | `Player.cpp:2334-2339` | CP §7.4 |
| trait selections `{TraitNodeEntryID, rank}` (purchased ranks only) + hero sub-tree | identity | `TraitMgr.cpp:766-832`, `Player.cpp:28533-28553` | CP §1, §5 |
| PvP talents (+ enable flag) | identity (optional) | `Player.cpp:27675-27702` | CP §1 |
| equipped item instances per slot: item, context, bonus lists, gems, enchants, keystone level, PvP tier | identity | gearing report; slot is identity (`Player.cpp:9243-9249`) | CP §1 |
| **explicitly learned spells that no automatic path grants** (quest / trainer rewards, `SkillLineAbility.AcquireMethod = Learned`) | identity (fixture `learned_spells`) | `Player.cpp:25404-25417`; example 296087 "Dual Wield" via 296088 | CP §1, §14 |
| glyphs, saved skill values | identity, **not yet representable** in the fixture format | character DB | CP §1, §15 |
| equip legality (required level from item bonuses, class/race masks, unique-equip, off-hand rules) | derived check on identity inputs; current-season items require level 90. Checks that need character or host state are **unresolved** and only reported: RequiredSkill outside the default skills, reputation, holiday, required-level curve, ItemLimitCategory quantity | `Player.cpp:11148-11149, 19295-19299` | CP §5, §15 (R3-8) |
| PvP talent activation, PvP item-level context, area item-level scaling | encounter / host | `Player.cpp:27778, 30768, 30928-30929` | CP §1 |
| crafted stat modifiers (`ITEM_BONUS_MODIFIED_CRAFTING_STAT`, type 25) | identity, semantics **unresolved** (Trinity NYI) | `DBCEnums.h:1273` | CP §1 |
| base primary stats | **server** (levels 1–80); **unresolved** (81–90); the committed fixtures are level 90 and opt into Trinity's fill rule explicitly (`fill_rule`) | `player_classlevelstats` + `player_racestats`; Trinity copies level 80 upward (`ObjectMgr.cpp:4349-4356`) | CP §8 |
| `MaxPlayerLevel`, `Stats.Limits.*` | server configuration | `worldserver.conf.dist` | CP §7 |
| `skill_tiers`, `conditions` (AcquireMethod 4) | server. Most AcquireMethod-4 spells also need PlayerCondition 83446 (map and ContentTuning), which is host state. The compiler keeps them unresolved | `ObjectMgr.cpp:9008-9009`, `Player.cpp:25409-25415` | CP §1, §3 (R3-5) |
| trait tree / granted ranks / currencies | derived | `SkillLineXTraitTree`, `TraitMgr.cpp` | CP §5 |
| item level, stats, ratings, sets, gem/enchant payloads | derived | gearing report | — |
| acquired spells: spec, mastery, traits, gear, sets, **default skills** | derived | `Player.cpp:30631-30648, 25259-25441` | CP §3 |
| armour specialization (spec spell **or class skill line**) | derived | `Player.cpp:26203-26213` | CP §3 |
| total stats, max health, max power, rating percentages, mastery (incl. +8 from 114585, which the existing charstats oracle does not sum) | derived | character-stat report; `StatSystem.cpp:549-550` | CP §9 |

### 1.2 Weapons and swing state

| input | class | source / consumer | report |
|---|---|---|---|
| equipped MH / OH / ranged item instances | identity (inside equipment) | — | WC §1 |
| weapon min/max, delay, DPS | derived | gearing (`ItemTemplate::GetDamage`) | WC §1 |
| usable weapon per attack type (off-hand shield or holdable → no OFF weapon; bow or gun in the main hand → BASE weaponless) | derived | `Player.cpp:9582-9609, 9649-9657` | WC §1.1 |
| per-hand weapon attack power `int32(dps × 6)`, shield block value `int32(armor × 2.5)`, melee school mask from `ItemSparse.DamageType` | derived | `Player.cpp:8123-8128, 8163-8176, 8183-8189` | WC §1.1, §2 |
| temporary weapon enchants / oils | encounter | — | CP §1 |
| dual-wield capability | derived **except** learned-spell grants | spec spell, default skill 118 → 674, or learned 296087 | WC §1, CP §14 |
| Titan's Grip, two-hand-in-one-hand | derived | spec spell 46917 | WC §1 |
| shapeshift form → `SpellShapeshiftForm.CombatRoundTime` | derived from the runtime form | `StatSystem.cpp:459` | WC §1 |
| prepared min/max (includes versatility and the 0.5 off-hand factor) | derived at recalculation time | `StatSystem.cpp:455-479`, `Unit.cpp:9793` | WC §1–§3 |
| attack power (stat → AP) | derived rule. At level 90 the value is **unresolved**, because it rests on the base stats of levels 81–90 | `ChrClasses.AttackPowerPer*`; `StatSystem.cpp:347-425` | WC §2, CP §8 |
| base attack time per attack type | derived (`BASE_ATTACK_TIME`, then weapon delay) | `Player.cpp:2394-2395, 5396` | CP §3 |
| initial attack timers | derived: 0 = ready | `Unit.cpp:326` | CP §3, WC §6 |
| haste | runtime (auras + ratings) | `Unit.cpp:10978-11007` | WC §6 |
| update tick | **host parameter**; Trinity uses a variable `diff` | — | WC §6 |

### 1.3 Permanent pet

| input | class | source / consumer | report |
|---|---|---|---|
| hunter pet: stable slot, creature entry, family, pet spec, react state, action bar (the name is cosmetic) | identity (character DB `character_pet`) | `Pet.cpp:122-131, 205-448` | CU §1.5, §4.2 |
| warlock / other class pet choice | identity (which summon spell was cast; fixture `controlled_units.active_demon`) | `Player::SummonPet` (`Player.cpp:30462-30522`) | CU §1.4, CP §6 |
| pet level | derived: owner level, re-synced on owner level-up | `Pet.cpp:289-290, 1784-1798` | CU §4.1, §6.2 |
| base stats | server: `pet_levelstats` (entry 1 for hunters). Levels 81–85 are 1-valued rows in the pinned TDB, and 86–90 are the loader's gap fill up to the default `MaxPlayerLevel` 90 → **unresolved** | `Pet.cpp:891-917`, `ObjectMgr.cpp:3756-3762` | CU §7.2 #8, §15 (R1-6) |
| owner shares (STA 30 %; INT 30 % for warlock and mage owners only; owner armor 70 % for a hunter pet and 100 % for any other `Pet`, none for a non-Pet Guardian; AP/SP by family) | derived by recalculation from owner totals | `StatSystem.cpp:1136-1402` | CU §6.2, §7 |
| owner spell mods, versatility | dynamic owner lookup | `Object.cpp:1648-1670`, `Unit.cpp:6926-6928` | CU §6.2 |
| owner haste / crit / mastery | **unresolved** (no Trinity consumer) | — | CU §6.2, §7.1 |
| abilities | server (`creature_template_spell`, levelup map, family skill line) + owner grants. In the CU census, 17 of 837 spells have no Trinity path; that census also covers every creature a 12.1 summon effect creates, not only current-player pets | `SpellMgr.cpp:2152-2294`, `Pet.cpp:1485-1519` | CU §8 |

### 1.4 Temporary guardian

| input | class | source / consumer | report |
|---|---|---|---|
| summoning spell and effect row | identity of the *event*, not of the character | — | CU §2 |
| SummonProperties (Control, Title, Slot, Flags) | db2-fact → selects the C++ class | `SpellEffects.cpp:1940-2076`, `Object.cpp:1195-1240` | CU §1, §6 |
| creature template, difficulty row, class-level stats | server (world DB) | `Creature.cpp:252-289, 1614-1643` | CU §6 |
| level | creation snapshot: `clamp(owner level, CT range + delta)` unless `UseCreatureLevel`, using the creature's difficulty row for the current map (`FallbackDifficultyID` chain). CT 0 → level 0: Grove Guardians (54983) on every map. For Mirror Image (31216), only map difficulties that resolve to its row 1 or 2 give CT 482: 1, 2, 8, 23, 24, and by the 12.1 `Difficulty.csv` also 205 (Follower) and 216 (Quest). Every other difficulty gives level 0 | `TemporarySummon.cpp:241-247`, `Creature.cpp:252-261` | CU §7.2 #13 (R1-2, R4-2) |
| health / AP / SP / weapon damage | creation snapshot plus explicit recalculation. The owner's `GetPet()` triggers never reach a non-Pet Guardian. Its `GetGuardianPet()` triggers (an owner `SPELL_AURA_MOD_DAMAGE_DONE` change; the spirit-wolf melee-AP branch) reach one only when it holds the owner's PetGUID, i.e. `SummonProperties.Control == PET`: Xuen 123904 and Niuzao 132578 in scope. Both summons of the spirit wolf (29264) are Control ALLY, so its branch never fires | `StatSystem.cpp:114-119, 276-278, 400-424`; `SpellAuraEffects.cpp:4731-4732`; `Unit.cpp:6230-6243, 6274-6295`; `TemporarySummon.cpp:499-502` | CU §7.1 (R4-1) |
| owner spell mods, owner versatility, spell crit | **none** for Guardian / Minion / TempSummon, except where a creature script copies owner values (Divine Image copies the owner's holy healing bonus at summon, `pet_priest.cpp:43-49`) | `Object.cpp:1656`, `Unit.cpp:7124` | CU §3, §6, §7.1 |
| duration | `SpellDuration` → `Player::ApplySpellMod<int32>` (binary32 multiplier, int32 truncation) only when the caster has a spell-mod owner | `Player.cpp:22843-22852` | CU §4.3 |
| slot replacement | `SummonProperties.Slot` (0 = no bookkeeping; −1 = any-totem-slot search, which can also end at 0) | `TemporarySummon.cpp:224-239, 376-434` | CU §4.4 |

### 1.5 Initial passive and marker state

| input | class | source / consumer | report |
|---|---|---|---|
| passives cast on learn | derived from the acquired spell set | `Player.cpp:2792-2794, 2916-2929, 3079-3104` | CP §3 |
| stance / form gate | derived (runtime form) | `Player.cpp:3083-3085` | CP §3 |
| equipment gate (aura spells with equipped-item requirements) | derived from equipment | `Player.cpp:3089-3099, 26158-26221` | CP §3 |
| CasterAuraState gate (e.g. health above 75 %) | derived, but applied **one update late** | `Unit.cpp:474-483, 6078-6098` | CP §3 |
| set-bonus thresholds | derived | gearing report | — |
| Dummy marker auras | derived; semantics per the Dummy report (only 152 of 2,069 Dummy-aura owners are observed by a strong consumer) | `dummy-corpora/markers.json` | Dummy headline 6, §9 |
| auras saved in the character database | **encounter / host** (excluded from preparation) | `Player::_LoadAuras` | CP §1 |
| full health, ready cooldowns, full charges at start | full health is an encounter convention (`structural-inference`); cooldowns are ready only on the load path (equipping an on-use item in game starts a 30 s cooldown); max charges include `SPELL_AURA_MOD_MAX_CHARGES` | `SpellHistory.cpp:147-179, 964-973` | CP §3, §15 |

---

## 2. Where each value comes from

| provenance | examples | how a consumer obtains it |
|---|---|---|
| **client-derived** (DB2 snapshot + existing research) | item stats and levels, trait trees and validity, acquired spells, armour specialization, ratings, mastery coefficient, weapon damage and speed, SummonProperties routing, ExpectedStat creature values, ContentTuning ranges (column mapping `structural-inference`), difficulty fallback chains, PowerType maxima | the packages (`gearing`, `charstats`, `character_prep`, `weapon_combat`, `controlled_units`) |
| **Trinity world DB** | base primary stats ≤ 80, race modifiers, `playercreateinfo` pairs, `pet_levelstats`, `creature_template*`, `creature_classlevelstats`, `creature_template_spell`, `skill_tiers`, `conditions`, `spell_script_names` | `tools/tdb_world_extract.py` corpora; pinned to TDB 1200.26021 |
| **Trinity configuration** | `MaxPlayerLevel`, `Stats.Limits.*`, `Rate.Creature.*` (default 1.0 assumed) | `worldserver.conf.dist` defaults |
| **Trinity code** (server semantics) | swing interleaving, outcome table, block and crit arithmetic, guardian stat programs, default-skill learning, level-80 copy rule, slot replacement | the `trinity-consumer` / `trinity-probe` rows of each report |
| **runtime** | haste, auras, forms, current health and power, attack timers after the first swing, pet recalculation, charges after use | the combat engine |
| **encounter** | target, position, facing, victim level and type, consumables, raid buffs, PvP item-level context, RNG seed, update tick | the host |
| **currently unknowable from these sources** | base stats 81–90 (and so level-90 attack power); hunter-pet base stats 81–90; owner haste/crit/mastery inheritance; pet aura types 429/381/382/157; current raid boss levels (glancing); the 10 summon casts whose client effect is a Dummy with no bound creating script (CU §5); crafted stat modifiers; `ItemLevelByLevel` armor curve at max level (the GameTable is absent from the snapshot); glyphs and saved skill values (format gap); Retail behaviour wherever Trinity has a defect or suspected defect (§8) | a Retail observation (§7), a newer Trinity/TDB, or a snapshot that ships the missing table |

---

## 3. Findings that match existing Core vocabulary

Each row names the Core term and where Core uses it. Most rows cite `core/docs/architecture.md`;
rows marked *code* or *ledger* cite a Core identifier or `core/docs/development-ledger.md`. A
match of words is not a claim that Core supports the finding, or that the finding should
be expressed that way. This is a vocabulary mapping, not an implementation proposal.

| Core term (where Core uses it) | findings that use the same vocabulary |
|---|---|
| exact source actors (architecture.md:121-125); `ActorId::Pet { index: PetIndex }` (*code*, `crates/model/src/actor.rs:120-125`) | pet and guardian are actors; the spell proc actor is `m_originalCaster ?: m_caster`; the melee proc actor is the attacker; the aura caster and combat-log source stay distinct (CU §3) |
| exact-effect identity and provenance (architecture.md:21-22, 74-81) | the summon effect row selects the unit class; weapon specials read per-effect base points in index order (WC §4); default-skill grants are identified by `SkillLineAbility` rows |
| validated immutable records, pure transforms, fail-closed preparation (architecture.md:24-26, 59); "immutable preparation" (*ledger*, development-ledger.md:7946) | the character fixture compiles into immutable facts plus a program; the guardian's creation snapshot (level, entry-branch bonuses) is prepared data (CU §7) |
| deadline (architecture.md:133-144, used there for recovery and charges) | per-hand attack timers are integer-millisecond countdowns; summon and totem durations are countdowns; a spell-summoned `Pet` has none (CU §4.3, WC §6) |
| resource, as an admission gate (architecture.md:127-131) | initial power per `PowerType` flag (full, empty, clamped, zeroed) (CP §3) |
| directed actor relationships in immutable topology, which does not conflate disposition with ownership (architecture.md:90-101); "Party and raid membership, pet/master/summoner and vehicle/passenger ancestry, and mutable position, visibility, line of sight, and range remain separate future authorities" (*ledger*, development-ledger.md:1916-1917) | ownership is a directed relation between actors (CU §3) |
| defense types (architecture.md:21-22); defense family (*ledger*, development-ledger.md:699-708); `AttackOutcomeOrder` (*code*, `crates/combat/src/attack.rs:13`) | miss/dodge/parry/block/crit eligibility and order for white swings and weapon specials (WC §7) |

Findings for which no matching term was found in architecture.md:
- **Equipment and stance gates of passives, `SpellEquippedItems` on weapon specials, and main-hand/off-hand requirements.**
  architecture.md's "selector" means implicit-target selectors (architecture.md:83-109), not these gates. The ledger's
  "applicability" is a damage-modifier source scope (development-ledger.md:3044).
- **Passive auras cast on learn, marker Dummy auras, and the one-update delay of CasterAuraState passives** (CP §3).
  "Aura state" occurs only in passing in the ledger and README.
- **Proc event identity.** White MH/OH swings carry the swing flags in `ProcFlags2`; every `DmgClass` MELEE spell
  carries them too (250 in scope); hit masks are per outcome; the existing `procs/events.py` agrees (WC §8). No Core
  document defines a proc-event term. The ledger only mentions published proc events (development-ledger.md:23051).

---

## 4. Semantics the evidence shows that architecture.md does not name

Each item is a semantic that the evidence shows Trinity's consumers need, and for which §3 found no matching term in
architecture.md. Whether, when or how Core should express any of them is not addressed here.

1. **Owner forwarding depends on the owned unit's C++ class.** Owner spell mods, owner versatility and KILL-proc
   forwarding apply to `Pet` and `Totem` only. Other owner paths apply to any owned unit:
   `SPELL_ATTR6_ORIGINATE_FROM_CONTROLLER`, PvP resilience, and the owner's class-script healing auras. A Totem
   forwards its whole spell bonus. Kill credit collapses for every owned unit. The proc actor does not collapse, except
   for script casts that pass the owner as `OriginalCaster` (CU §3).
2. **Per-hand swing scheduling with coupling.** An actor has two integer-millisecond timers. A same-update rule applies
   (main hand wins; the off hand is pushed to +200 ms). Extra attacks are queued, and cast-related reset rules apply
   (WC §6). A haste change rescales the remaining time of the running swing. The same-update rule holds for
   `DoMeleeAttackIfReady`. Queued extra attacks may share an update with an off-hand swing, though none is reachable in
   scope. The two timers are therefore not independent.
3. **Controlled-unit stats are recomputed on specific triggers.** The code differs per C++ class and per creature
   entry. It re-runs only on specific owner triggers (`Player::UpdateStats`, `UpdateArmor`,
   `UpdateAttackPowerAndDamage`, owner damage-done auras), reaches a non-Pet Guardian only through the owner's PetGUID,
   and is gated by `CanModifyStats` (CU §7.1, R4-1).
4. **Summon slot bookkeeping**: `SummonProperties.Slot`, the any-totem-slot search, guardian-pet replacement and
   same-entry coexistence (CU §4.4).
5. **Learned-spell character state is an input**, distinct from traits and from automatic acquisition (CP §14).
6. **Default-skill acquisition** is an acquisition path distinct from spec, trait, gear and class-line spells. It
   covers skill lists by race and class, skill values by range type, and ability learning by acquire method (CP §3).

---

## 5. Apparent exceptions that reduce to parameterized ordinary semantics

| apparent exception | parameters it reduces to | evidence |
|---|---|---|
| "hunter pets", "warlock demons", "DK ghouls" as separate systems | C++ class (Pet / Guardian / Minion / Totem / TempSummon) selected by `SummonProperties` + effect type, plus a per-entry stat branch table | CU §6.1 (structural; the per-entry branches are genuinely entry-specific) |
| Rogue / Hunter / Demon Hunter dual wield | the default-skill path (skill 118 → 674) | WC §1, CP §3 |
| the universal +8 mastery | the default-skill path (skill 183 → 114585) plus an ordinary `SPELL_AURA_MASTERY`, counted only while `CanUseMastery` holds (spec mastery spell known) | CP §9.3 (static consumer trace + db2-fact; runtime application not executed) |
| armour specialization for Rogue, Hunter, Warlock, Mage, Priest, Evoker | the class skill line instead of specialization spells; same aura rule | CP §3 (E7) |
| totem health | on the `Title::Totem` arm only, a non-zero summon effect value overrides creature health; a Join totem (e.g. 458101) goes through `SummonGuardian` and keeps the creature value | CU §6.2 (G1-19) |
| Water Elemental as a "permanent pet" for a mage | `IsPermanentPetFor` with creature type 4 (pet number, pet auras, spell list). Its stats do **not** reduce to the ordinary pet path: `InitStatsForLevel` logs "Unknown type pet" and leaves `MAX_PET_TYPE` | CU §2.1, §7.4 |
| auto-shot cadence | the ordinary attack timer plus the auto-repeat spell's one-update lag (`ceil(P/T)+1`). This does **not** cover interruption: every hard-coded exemption is keyed on spell id 75, so the in-scope 467718 Bleak Arrows is interrupted where 75 is not | WC §6.8 |
| blocked weapon specials still dealing damage | one `COMPLETELY_BLOCKED` attribute | WC §7.5 (T5) |

What does **not** reduce: the InitStatsForLevel entry branches (510, 1964, 15352, 15438,
19668, 19833, 19921, 28017, 31216, 27829, 26125, 29264), the DRW script damage, the
Divine Image script snapshot (198236), and the 10 summon casts whose client effect is a
Dummy with no bound creating script (CU §5). Seven of the twelve entry branches (510, 1964,
15352, 15438, 19833, 19921, 28017) are legacy-only for 12.1 (CU §7.1, §7.5); 19668, 31216,
27829, 26125 and 29264 are reached. The rest is script territory.

The first row is a structural reduction only: the report shows that the C++ class and the
entry branch table decide the stat code, not that the resulting semantics are equivalent.

---

## 6. Snapshot versus dynamic lookup

| value | policy in Trinity | where it is captured | report |
|---|---|---|---|
| guardian level | creation snapshot (row chosen by map difficulty) | `TempSummon::InitStats` | CU §4.1, §7.2 #13 |
| pet level | dynamic (owner level-up resync) | `SynchronizeLevelWithOwner` | CU §4.1, §6.2 |
| pet / guardian stat shares | explicit recalculation on owner triggers. A non-Pet Guardian is reached only through `GetGuardianPet()`, i.e. when it holds the owner's PetGUID (Xuen, Niuzao) | `StatSystem.cpp:114-119, 276-278, 400-424`; `SpellAuraEffects.cpp:4731-4732` | CU §7.1 (R4-1) |
| entry-branch spell/attack bonuses | creation snapshot | `Pet::InitStatsForLevel` | CU §7.1 |
| owner spell mods / versatility (Pet, Totem) | dynamic at damage time | `GetSpellModOwner` | CU §6 |
| totem spell bonus | fully forwarded to the owner at damage time | `Unit.cpp:6833-6836` | CU §6 |
| summon duration | snapshot when the summon effect launches (`CalcDuration(caster)` with spell mods) | `SpellEffects.cpp:1902`; `Player::ApplySpellMod` | CU §1.3, §4.3 |
| PvP flags of a minion | copied at summon, then re-applied from the owner on every later owner change | `Unit.cpp:6325`; `Player.cpp:24027-24035, 24052-24057` | CU §4.1 |
| prepared weapon min/max | recalculation (`UpdateDamagePhysical`), includes versatility at that moment | `StatSystem.cpp:455-479` | WC §1.3 |
| attack speed | **incremental**: each haste change multiplies the stored multiplier by the factor or its inverse; nothing recomputes it from the aura totals (binary32, not reversible) | `Unit.cpp:10978-11007` | WC §6.5 |
| swing deadline after a haste change | dynamic: the remaining fraction of the running swing is preserved and the timer rescaled to the new period, `uint32(base * modPct * remainingPct)` (binary32, truncation) | `Unit.cpp:10983-11007` | WC §6.5 |
| outcome chances | computed per swing from current state | `RollMeleeOutcomeAgainst` | WC §7 |
| character base stats | snapshot at `InitStatsForLevel` | `Player.cpp:2323` | CP §3 |

---

## 7. Retail observations that would best falsify the uncertain conclusions

Ordered by how much preparation depends on them. For items 1–6 and 10 the last column names
where a future Retail WASM/V8 oracle would attach the observation: a JSON pointer into the
compiled fixture (CP §4) or a fixture section. Items 7–9 are encounter observations, and the
fixture format has no place for them.

| # | observation | resolves | fixture path |
|---|---|---|---|
| 1 | naked level-90 character sheet for one race per class (primary stats) | base stats 81–90 (Trinity copies 80) | `/derived/stats/value/stats/*` |
| 2 | same at levels 69 and 70 | the stamina drop in the TDB rows | same |
| 3 | two naked characters differing only in race | the zero race modifiers of races 52/70/84/85/86/91 | same |
| 4 | mastery with zero mastery rating | the +8 of 114585 | `/derived/stats/value/mastery*` |
| 5 | a strength total that is a multiple of 20 with armour specialization active | the binary32 `1.05f` truncation | `/derived/stats/value/stats/Strength` |
| 6 | hunter pet and guardian health / AP / SP for a known owner sheet | pet/guardian arithmetic, double-counted guardian health, owner haste/crit/mastery inheritance | `/derived/controlled_units/value` (currently `unresolved`: no controlled-unit hook, CP §2) |
| 7 | swing log with a known haste buff applied and removed | the 1 ms drift, MH/OH same-update rule | none: encounter observation (host) |
| 8 | block amounts on a player victim; Martial Expert (429638) on a Protection Warrior's critical blocks | the fraction-as-percent defect; the attacker-side critical-block multiplier | none: encounter observation |
| 9 | melee against a current raid boss: glancing occurrences | glancing reachability, boss level | none: encounter observation |
| 10 | a character export listing known spells, glyphs and skill values | learned-spell state (296087), AcquireMethod-4 spells, the missing glyph / skill sections | fixture `learned_spells` (glyphs and skills: format gap) |

---

## 8. Defects of the pinned oracle (do not copy)

These are consumer facts about Trinity, **not** Retail semantics. The first table lists places
where the pinned code is demonstrably inconsistent with itself, its comments or its own
history. The second table lists behaviour that a consumer should not copy without Retail
evidence, but that is not proven to be a defect: Retail might behave the same way.

**Demonstrated inconsistencies.**

| defect | coordinate | effect | report |
|---|---|---|---|
| `GetCharmerOrOwnerOrOwnGUID` inverted by a refactor (before commit `4ba5e27055c` it returned the charmer/owner GUID if set, else its own) | `Object.cpp:1597-1603` | `IsCharmedOwnedByPlayerOrPlayer` is false for uncharmed players and for player-owned pets and guardians, and true for a player charmed by a creature; 8 call sites | CU §3 |
| player block percent is a fraction: the same virtual returns `30.0f` (a percent) for every other unit | `Player.cpp:26822-26831`, `Unit.h:987`, `Unit.cpp:1459, 1239` | ≤ 0.85 % blocked on white swings, 0 on weapon specials | WC §3, §7.4 (T4) |
| weapon-special effect accumulation contradicts the loop's own assumption ("at most one fixed_bonus and at most one weaponDamagePercentMod") | `SpellEffects.cpp:2911-2935` | a spell with two fixed or two percent effects double-counts. 9 snapshot spells have such effects; none is in scope. The dependence on effect order is intended ("Sequence is important") | WC §4.1 |
| negative owner spell-power aura raises the pet bonus: the owner field `ModDamageDoneNeg` is stored as a negative sum, and the Pet branch subtracts it | `StatSystem.cpp:180-188, 1330-1331` | sign error | CU §7.2 #4 |
| unsigned cast of a negative float | `StatSystem.cpp:1197, 1275` | UB, transient | CU §7.2 #2 |
| `GetPetLevelInfo(id, 0)` indexes `[-1]` | `ObjectMgr.cpp:3777` | UB, not reached by witnesses | CU §7.2 #9 |
| `spell_dk_raise_dead` hooks effect 0 of 46584 as `SPELL_EFFECT_DUMMY`, but that effect is TRIGGER_SPELL → 1242866 in 12.1 and in the 12.0.7 reference, so the runtime filter drops the hook | `spell_dk.cpp:1198-1215`; filter `Spell.cpp:8895`, `SpellScript.cpp:179-189` | the player's Raise Dead summons nothing | CU §8.3 |

**Suspected defects and unimplemented consumers** (do not copy; reopen with the stated evidence).

| behaviour | coordinate | effect | why only suspected | reopen condition | report |
|---|---|---|---|---|---|
| crushing chance `attackerLevel - victimLevel * 1000 - 1500` | `Unit.cpp:2489` | crushing never occurs for any attacker; the eligibility checks around it are dead | the expression is evidently mis-parenthesized, but its effect (no crushing) may match Retail | Retail evidence for or against crushing blows (C-4, D-2, R2-3) | WC §7.4 (T2) |
| critical-block multiplier (aura 638) read from the attacker | `Unit.cpp:1243, 1463` | the defender's Martial Expert (429638) never scales its own blocks | no second source shows which unit owns aura 638 | an independent oracle for aura 638 ownership (C-9) | WC §3 |
| `addPctMods = false` recomputes the weapon range without `TOTAL_PCT` | `SpellEffects.cpp:2887-2908` | off-hand specials with ATTR6 or a non-physical school (205547 Odyn's Fury) lose the 0.5 off-hand factor. The code comment says the percent mods are then added in `MeleeDamageBonusDone`, which does not apply the factor | Retail may exempt these spells from the off-hand penalty | Retail combat-log evidence for 205547 (C-10) | WC §4.1 |
| non-Pet guardian health counted twice | `Creature.cpp:1643`, `StatSystem.cpp:1253-1276` | guardian max health includes ExpectedStat health twice | no Trinity text states the intended formula | Retail guardian health for a known owner (B-12; §7 item 6) | CU §7.3 |
| Ghost Wolf uses form 48 in the snapshot; `IsInFeralForm` tests `FORM_GHOST_WOLF` 16 | `Unit.cpp:9539-9543` | the feral AP-multiplier branch never fires for current Ghost Wolf | possible build skew; the skew reference carries only `SpellName` | a 12.0.7 `SpellEffect` for 2645, or a Trinity enum update (C-14) | WC §1.2 |
| aura types 429/381/382/157 are `HandleNULL` | `SpellAuraEffects.cpp:229, 453, 454, 501` | current mastery pet-damage terms are ignored | unimplemented, not inconsistent | a handler or a script on the witness spells (B-2 ff.) | CU §9 |
| `EffectSummonType` tests `caster->IsPlayer()` but dereferences `m_originalCaster->ToPlayer()` | `SpellEffects.cpp:1896` | possible null dereference | not probed | a probe or upstream fix | CU §1.3 |

---

## 9. Numeric boundaries found

| boundary | types | reachable? | evidence | report |
|---|---|---|---|---|
| armour specialization `1.05f` | binary32 multiplier → int32 truncation | yes: 4,045 of the pre-percent totals 1..120,000 (all multiples of 20, always one point lower) | differential | CP §9.4 |
| attack-speed haste apply/remove | binary32 product, uint32 period | yes: in-scope haste 3/5/15 | trinity-probe | WC §6 |
| chance → basis points `int32(x*100.0f)` | binary32 → int32 | yes: 282 of 4,999 hundredths | trinity-probe | WC §7 |
| crit multiplier 1.3 | binary32 `AddPct` → integer | yes: 1000 → 2599 | trinity-probe | WC §3 |
| `CalculatePct<uint32,float>` float vs double | `T(base * float(pct) / 100.0f)` | at 30 %, the first discriminating base is 2,236,990 (671,096 vs 671,097). 16,777,219 is a further witness, not a threshold, and not every larger base discriminates. Reachability in current damage magnitudes is unresolved (R4-3) | trinity-probe | WC §3 |
| Imp health `8.4f` | binary32 | yes: 128,128 vs 128,129 (level-80 Imp, owner stamina 50,000) | differential | CU §7.2 #1 |
| summon duration spell mods | `T((double(base) + flat) * float totalmul)` → int32 truncation | wherever a Duration mod applies to a Pet/Totem/Player caster; not probed | trinity-consumer (same shape as the cooldown op-11 41,999/42,000 ms case) | CU §4.3 |
| `UpdateMaxPower` `lroundf` | binary32 → long, half away from zero | reachability unresolved | trinity-probe | CP §9 |
| rating accumulator int16 | wraps at 32,767 | unresolved (needs the largest per-rating gear total) | trinity-probe | CP §9 |
| `rand_chance()` | binary32 | yes | trinity-consumer | WC §7 |

---

## 10. Errata against earlier reports

The earlier reports and their packages were **not** modified by this pass. The following
statements in them are contradicted or narrowed by direct evidence found here.

| earlier statement | where | correction | evidence |
|---|---|---|---|
| "Base primary stats are not in the snapshot. … the TrinityCore repository ships only their `CREATE TABLE` statements." | `character-stat-pipeline-archaeology.md`, headline 1 (:24-27) | still true for the snapshot and the repository; the pinned TDB dump supplies 1,032 class-level rows (levels ≤ 80) and 31 race rows, extracted to `world-db-corpora/player-base-stats.json` | world-db-fact |
| "above `CONFIG_MAX_PLAYER_LEVEL`, `BuildPlayerLevelInfo` extrapolates" | same report, §1 (:93) | true only if `MaxPlayerLevel` is set below the character level; by default levels 81–90 are copies of level 80 | trinity-consumer + trinity-probe |
| "base mastery \| `0` unless `CanUseMastery()`"; `mastery_value` is the rating conversion alone | same report, §1 table (:68); `charstats/character.py:334` | every player learns 114585 (+8 `SPELL_AURA_MASTERY`) through skill 183 | trinity-consumer |
| armour specialization is "a `SpecializationSpells` row" | same report, §11 table (:614) | six classes get it from the class skill line (86092, 86538, 86091, 89744, 89745, 366524) | db2-fact + trinity-consumer |
| "`ITEM_MOD_ATTACK_POWER` and the weapon's `int32(dps * 6)` land in `TOTAL_VALUE`" | same report, §5 (:298-300) | only `ITEM_MOD_ATTACK_POWER` does; weapon AP goes to `UnitData::*WeaponAttackPower` (`Player.cpp:8163-8176`) | trinity-consumer |
| `MOD_SUMMON_DAMAGE` is listed among auras "whose amount a generic engine rule consumes" | same report, §9 (:489-494) | aura 429 is `HandleNULL` in the pinned Trinity (`SpellAuraEffects.cpp:501`); the statement is at most "expressible generically" | trinity-consumer |
| racial ability lookup uses race bit `race_id - 1` (`bit = race_id - 1`); acquire method 0 abilities are included | `charstats/acquisition.py:260` (same function) | Trinity's `RaceMask::GetRaceBit` differs for races ≥ 34; acquire method 0 is never learned automatically; races 70/84/85/86/91 raise errors | trinity-consumer |
| "Class skill lines (`SkillLine.CategoryID = 7`) are available as an extended scope" (the current-player `Scope`) | `dummy-server-semantics-archaeology.md` §1 (:144-147); `dummy_semantics/scope.py:56, 183-186`. The proc report's "player scope" (`proc-pipeline-archaeology.md`:787-789) roots at traits, spec spells and items and follows no skill line at all | default-skill lines (category 6 skill 118, category 12 skill 183) are an additional acquisition path that neither scope follows; any "no current-player owner" count in either report excludes them | trinity-consumer |

None of these corrections changes a committed corpus of the earlier passes. Each is a reopen
condition for the owning package.

---

## 11. Reproducibility

All commands run from `scripts/research`.

```bash
# world-DB extracts (optional; needs the pinned dump)
python3 tools/regen_combat_prep.py --stage tdb --tdb ../../../../bag/tdb/TDB_full_world_1200.26021_2026_02_06.sql
# probes + every derived corpus + the Trinity differential + merged unknowns, then prove nothing changed
python3 tools/regen_combat_prep.py --check
# tests of this pass
uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest \
  tests/test_cu_*.py tests/test_wc_*.py tests/test_cp_*.py \
  tests/test_tdb_world_extract.py tests/test_cross_track_unknowns.py -q -p no:cacheprovider
```

Shared tooling added by this pass:

| tool | purpose |
|---|---|
| `tools/tdb_world_extract.py` | generic, projected, filtered extraction of any world-DB table from the pinned dump plus updates; a key index makes update-heavy tables tractable (full `creature_template_difficulty`: 320,629 rows in ~20 s instead of hours); equivalence with the plain replay is tested |
| `tools/merge_unknowns.py` | validates and merges the three track unknowns corpora into `combat-prep-corpora/cross-track-unknowns.json` |
| `tools/regen_combat_prep.py` | runs every regeneration step in dependency order; `--check` proves the committed corpora are fresh |

Each world-DB extract records how many replay statements it could not parse
(`unparsed_statement_count` in the provenance; `pet-witness-templates.json` also keeps the full
text under `unparsed`). The `creature_template_difficulty` replay leaves one: an
`INSERT INTO creature_template_difficulty` in `2026_05_01_00_world.sql` whose values contain a
constant bitwise-OR expression (`268435456 | 0x02000000`). It covers 12 creature entries
(57207–59499), none of which is in any census or in `creature-templates.ids.json`. It is recorded,
not guessed (L-1). `creature-templates.json` records 3 other unparsed statements (CU §12) and
`player-base-stats.json` none.

---

## 12. Closeout review

Hostile reviewer G4, 2026-09-16/17, fresh context, same pins (snapshot `12.1.0.69497`,
TrinityCore `7f3d43b`, TDB 1200.26021). Scope: this document. The three track reports and their
code were only read. Changes outside this file: three new unknowns (R4-1, R4-2 in
`controlled-unit-corpora/unknowns.json`, R4-3 in `weapon-combat-corpora/unknowns.json`) and the
regenerated `combat-prep-corpora/cross-track-unknowns.json`.

### 12.1 Method

- **Faithfulness.** Every row of §1–§11 was checked against the *current* text of the cited
  report section, including the three closeout reviews (CU §15, WC §15, CP §15). Every
  Trinity coordinate that carries an argument was re-read at `7f3d43b` with the surrounding
  function.
  - Read in full: `GetGuardianPet`, `SetMinion`'s guardian-pet rule, `Minion::IsGuardianPet`,
    `Player::UpdateAttackPowerAndDamage`, `HandleModDamageDone`, `Guardian::UpdateMaxHealth`,
    the `EffectWeaponDmg` loops, `GetBlockPercent`, `ApplyAttackTimePercentMod`,
    `ApplySpellMod`, `CalcDuration`, `CreatureTemplate::GetDifficulty`,
    `GetCharmerOrOwnerOrOwnGUID`, `spell_dk_raise_dead`, `npc_pet_pri_divine_image`, and the pet
    `pet_levelstats` gap fill.
  - `git show 4ba5e27055c` for the regression.
- **Data.** Queried `SpellEffect`, `SummonProperties` and `Difficulty` (fallback chains)
  directly. Compared §1 with `character-prep-corpora/input-inventory.json` (58 rows). Resolved
  the §7 pointers in `fixtures/arms-warrior-plate-2h.compiled.json`. Checked the unparsed
  TDB statement against every census corpus and `creature-templates.ids.json`.
- **Core vocabulary.** Read `core/docs/architecture.md` in full. Grepped Core (read only) for
  every §3 term.
- **Numerics.** An independent `struct`-packed binary32 emulation (rounding after every
  operation) reproduced five §9 boundaries. `tools/tc_weapon_probe` (`make`, up to date)
  confirmed the `CalculatePct` threshold on the verbatim template.
- **Errata.** Opened each quoted statement at its cited location.
- **Commands.** Ran `merge_unknowns.py` (write mode, twice: identical sha256),
  `regen_combat_prep.py --check` and the test command of §11.

### 12.2 Findings

| id | claim (before) | verdict | action | evidence |
|---|---|---|---|---|
| G4-1 | §1.4, §6: recalculation "that the owner never triggers for non-Pet guardians" | **defect** | reworded in §1.4, §4 item 3 and §6; R4-1 | `GetGuardianPet` returns the PetGUID holder with `UNIT_MASK_GUARDIAN` (`Unit.cpp:6230-6243`). PetGUID is set for `IsPet() \|\| Control == PET` (`Unit.cpp:6274-6295`, `TemporarySummon.cpp:499-502`). `HandleModDamageDone` → `GetGuardianPet()->UpdateAttackPowerAndDamage()` (`SpellAuraEffects.cpp:4731-4732`) therefore reaches Xuen 123904 (SummonProperties 3262, Control 2) and Niuzao 132578 (3353, Control 2). Both summons of 29264 (228562, 363941) use 1161 (Control 1), so the spirit-wolf branch (`StatSystem.cpp:421-422`) never fires |
| G4-2 | §1.2 base attack time `Player.cpp:2395-2396` | **defect** (stale after G3-27) | `:2394-2395` | sed at the pin |
| G4-3 | §1.3 hunter-pet levels 81–90 "are placeholder 1s in the pinned TDB" | overstated | 81–85 are rows; 86–90 are the loader's gap fill up to `MaxPlayerLevel` (R1-6) | `ObjectMgr.cpp:3754-3762`; CU §15 G1-15 |
| G4-4 | §1.3 owner shares "STA 30 %, INT 30 %, armor" | **defect** | INT 30 % only for warlock and mage owners; armor 70 % hunter pet, 100 % other `Pet`, none for a non-Pet Guardian | `StatSystem.cpp:1160-1168, 1238-1242` |
| G4-5 | §1.3 section references CU §5 / §4 / §6 / §7 | **defect** | CU §1.5, §4.2 / §1.4 / §4.1, §6.2 / §7.2 #8 | CU headings |
| G4-6 | §1.3 "17 abilities have no path" | overstated | now 17 of 837 census spells, and the census covers every 12.1-summoned creature | CU §8.2, G1-18 |
| G4-7 | §1.4 Mirror Image is level 0 "outside 5-player dungeon difficulties" | overstated | exact difficulty list; R4-2 | `Creature.cpp:252-261`. 12.1 `Difficulty.csv` chains: →row 1: 1, 205, 216; →row 2: 2, 8, 23, 24; all other difficulties →row 0, including party difficulties 19, 150, 232, 236 |
| G4-8 | §1 is complete | **defect** (omissions) | added: the `playercreateinfo` pair (server, identity gate); session expansion (caller); PlayerCondition 83446 for AcquireMethod 4; item requirements that need character or host state; PvP/area item-level context; temporary weapon enchants; usable weapon per attack type; weapon AP; ShieldBlock; school mask; level-90 AP unresolved | `input-inventory.json` rows; CP §7.2, §7.4, §3, §15 (R3-5, R3-8); WC §1.1, §2; `Player.cpp:396-402, 2334-2339, 8123-8189, 9582-9657` |
| G4-9 | pins: "column order is checked against `DB2LoadInfo` where a package reads a table" | unsupported | narrowed to the ContentTuning positional mapping (`structural-inference`) | CU §7.2 #11, WC §10; no report claims a general check |
| G4-10 | §3 "Core's architecture names these axes" | **defect** | table rewritten with the Core source of each term; unmatched findings listed separately | `ActorId::Pet { index: PetIndex }` is code (`crates/model/src/actor.rs:120-125`). `AttackOutcomeOrder` is code (`crates/combat/src/attack.rs:13`). "Immutable preparation" and "defense family" are ledger terms. architecture.md's "selector" means implicit-target selectors (:83-109), not equipment/stance gates. No Core document defines "proc event identity" or an "aura state" axis. The ledger quote was spliced; the exact text is at development-ledger.md:1916-1917 |
| G4-11 | §4 "concepts … genuinely new"; "A single 'owner' edge is not enough: the edge needs …"; "A prepared stat program"; "the proc actor never collapses" | overstated (steering wording and an absolute) | heading and items restated as evidence without a proposed construct; proc-actor exception for script casts with `OriginalCaster = owner` | CU §3 (`Spell.cpp:2842`) |
| G4-12 | §5 "totem health: summon effect value overrides creature health" | overstated | only on the `Title::Totem` arm; Join totems keep the creature value | CU §6.2, G1-19; `SpellEffects.cpp:1969-1984` |
| G4-13 | §5 Water Elemental reduces to `IsPermanentPetFor` | overstated | only its pet identity reduces; its stats take the `MAX_PET_TYPE` path | CU §7.4 |
| G4-14 | §5 auto-shot cadence reduces to "the same deadline" | overstated | cadence only; id-75-keyed interruption exemptions do not reduce | WC §6.8 |
| G4-15 | §5 +8 mastery; "the first group is mostly legacy-only" | confirmed, made exact | `CanUseMastery` gate noted; 7 of 12 branches legacy-only, listed; first row marked structural | CP §9.3, G3-17; CU §7.1, §7.5 |
| G4-16 | §6 attack speed: "recalculation on each haste change" | **defect** | incremental multiply by the factor or its inverse | `Unit.cpp:10978-10988`; WC headline 2, G2-3 |
| G4-17 | §6 summon duration "spell mods at cast" | imprecise | snapshot at effect launch | `SpellEffects.cpp:1902`; `SpellInfo.cpp:3975-3983` |
| G4-18 | §7 "Every item names the fixture observation path" | **defect** | items 7–9 have none (encounter); item 6 points at `/derived/controlled_units/value` | CP §4 has no controlled-unit or encounter observation block; pointers 1–5 resolve in a compiled fixture |
| G4-19 | §8: all rows are "demonstrably inconsistent" | overstated | split into demonstrated inconsistencies and suspected or unimplemented behaviour, each with a reopen condition. Moved: crushing (effect may match Retail), attacker-side critical block, `addPctMods` off-hand factor, guardian double health, Ghost Wolf (possible build skew), `HandleNULL` auras (unimplemented). Added the `EffectSummonType` null-dereference suspicion (CU §1.3). Weapon specials: order dependence is intended ("Sequence is important"); double counting contradicts the loop's own assumption comment | `SpellEffects.cpp:2887-2888, 2911-2918`; `Unit.cpp:2489, 1243, 1463, 9539-9543`; WC §12 C-4/C-9/C-10/C-14; CU B-12 |
| G4-20 | §8 `GetCharmerOrOwnerOrOwnGUID` regression | confirmed | wording made exact (uncharmed players; charmed player → true); coordinate `Object.cpp:1597-1603` | `git show 4ba5e27055c`: `return guid; return GetGUID();` became `guid = GetGUID(); return guid;` |
| G4-21 | §8 player block fraction | confirmed | the `Unit.h:987` percent added as the inconsistency | `Unit.h:987` returns `30.0f`; `Player.cpp:26822-26831`; `Unit.cpp:1459, 1239` |
| G4-22 | §8 negative owner spell power | confirmed | coordinate `1330-1331` | `StatSystem.cpp:180-188` stores Neg as a negative sum and Pos = SBDB − Neg; the Pet branch computes Pos − Neg |
| G4-23 | §8 Raise Dead, `[-1]`, `(uint32)` UB | confirmed | Raise Dead wording names the effect mismatch and the filter | `spell_dk.cpp:1198-1215` (hook on `EFFECT_0, SPELL_EFFECT_DUMMY`); `ObjectMgr.cpp:3777`; `StatSystem.cpp:1195-1197, 1275` |
| G4-24 | §9 `CalculatePct` float vs double "at 16,777,219 and above" | **defect** | first discriminating base at 30 % is 2,236,990; reachability unresolved; R4-3 | emulation: 671,096 vs 671,097. Probe `pct 2236990 30` → 671096, `pct 16777217 30` → 5033165, `pct 16777219 30` → 5033166. `Util.h:71-75` |
| G4-25 | §9 armour spec `1.05f`: 4,045 totals | confirmed | wording (range, sign, multiples of 20) | emulation: 4,045 of 1..120,000, all +1, all multiples of 20; 27,640 → 29,021 vs 29,022 |
| G4-26 | §9 `int32(x*100.0f)` loses 282 of 4,999 | confirmed | — | emulation: 282; 0.53f → 52; 3 + 0.11 + 4.5 → 760 |
| G4-27 | §9 crit 1.3: 1000 → 2599 | confirmed | — | `mod` = f32 29.9999962; `CalculatePct(2000, mod)` = 599 |
| G4-28 | §9 haste apply/remove drift | confirmed | — | emulation of `Unit.cpp:10978-10988`: 21,480 of 99,010 pairs short, 190 at integer v |
| G4-29 | §9 Imp 128,128 vs 128,129 | confirmed | condition added | `f32(15000 × 8.4f)` = 125,999.9921875; + 2,129 → 128,128.99 → 128,128 |
| G4-30 | §9 duration spell mods "double + binary32" | confirmed | formula written out; "not probed" | `Player.cpp:22843-22852` |
| G4-31 | §10 row 1 quotation | **defect** (not verbatim) | exact text with `:24-27` | charstats report headline 1 reads "the TrinityCore repository ships only their `CREATE TABLE` statements" |
| G4-32 | §10 rows 2–7 | confirmed | section and line references added | `character-stat-pipeline-archaeology.md:93, 68, 614, 298-300, 489-494`; `charstats/character.py:334`; `charstats/acquisition.py:260` |
| G4-33 | §10 row 8 "current-player `Scope` (class skill lines = category 7) … `procs` census" | **defect** | quotation added. The proc report's player scope roots at traits, spec spells and items and follows no skill line, so it is described separately | `dummy-server-semantics-archaeology.md:144-147`; `scope.py:56, 183-186`; `proc-pipeline-archaeology.md:787-789` |
| G4-34 | §11 "The world-DB replay leaves one statement unparsed … recorded in every affected corpus's provenance" | overstated | per-table counts; the provenance holds a count, and `pet-witness-templates.json` holds the text | `unparsed_statement_count`: level deltas 1, pet-witness 1, creature-templates 3, player-base-stats 0. The 12 entries 57207–59499 are in no census corpus and not in `creature-templates.ids.json` |
| G4-35 | §11 commands | confirmed | — | `regen_combat_prep.py --help` has `--stage tdb --tdb` and `--check`; results in §12.5 |
| G4-36 | §2 provenance table | overstated / incomplete | ContentTuning mapping marked; `playercreateinfo`, difficulty chains and a configuration row added; level-90 AP, glyphs and skills added to "unknowable" | CP §7, CU §7.2 #11 |
| G4-37 | §1.5 Dummy marker count 152 of 2,069 | confirmed | wording "observed by a strong consumer" | `dummy-server-semantics-archaeology.md:76-77` |
| G4-38 | §1.1 and §1.5 Trinity coordinates | confirmed | — | `Player.cpp:387, 9243-9249, 11148-11149, 19295-19299, 25404-25417, 26203-26213`; `World.cpp:750`; `ObjectMgr.cpp:4349-4356, 9008-9009`; `DBCEnums.h:1273`; `StatSystem.cpp:549-550`; `SpellHistory.cpp:964-973` |
| G4-39 | §7 pointers 1–5 | confirmed | — | `/derived/stats/value/{stats,mastery,mastery_value,base}` exist in the compiled fixture |
| G4-40 | §1.3, §1.4, §6 owner lookups (spell mods, versatility, totem bonus, spell crit, PvP flags, Divine Image) | confirmed | — | `Object.cpp:1648-1660`; `Unit.cpp:6323-6325, 6831-6836, 6926-6928, 7120-7125`; `Player.cpp:24025-24058`; `pet_priest.cpp:43-49` |

Counts: 40 claim groups checked. 11 defects fixed (G4-1, 2, 4, 5, 8, 10, 16, 18, 24, 31, 33).
12 weakened (G4-3, 6, 7, 9, 11, 12, 13, 14, 17, 19, 34, 36); of these, G4-9 was unsupported and
the rest overstated. 17 confirmed (G4-15, 20–23, 25–30, 32, 35, 37–40). The remaining uncertainty
is held in R4-1, R4-2 and R4-3.

### 12.3 Fixes (before → after)

- §1.4 / §4 / §6: "never for non-Pet guardians" → reachable through `GetGuardianPet()` for PetGUID holders (Xuen, Niuzao); spirit-wolf branch unreachable in 12.1 data.
- §1.2: `Player.cpp:2395-2396` → `2394-2395`; four rows added.
- §1.1: three rows added; two rows qualified.
- §1.3: pet base-stat wording, owner-share conditions, section references, census scope.
- §3: architecture.md attribution → per-term Core source; exact ledger quote; unmatched findings listed.
- §6: attack speed "recalculation" → incremental.
- §7: fixture paths for items 6–9.
- §8: one table → demonstrated vs suspected/unimplemented, with reopen conditions.
- §9: `CalculatePct` "16,777,219 and above" → first discriminating base 2,236,990 (at 30 %).
- §10: row 1 quotation exact; row 8 rewritten; line references.
- §11: unparsed-statement paragraph made per-table.
- Unknowns: R4-1, R4-2 (Track A), R4-3 (Track B); `cross-track-unknowns.json` regenerated (138 entries).

### 12.4 Weakened conclusions

- The Core mapping (§3/§4) no longer says Core "names" these axes. It says where the words occur.
- Six former "defects" (§8) are now suspected or unimplemented, because Retail could behave the same way.
- Four §5 reductions (totem health, Water Elemental, auto-shot, the hunter/warlock/DK row) are partial or structural only.
- The Mirror Image level-0 condition depends on the difficulty fallback chain, and 205/216 are not in the CU list (R4-2).
- The `CalculatePct` boundary is a probe fact whose reachability is open (R4-3).

### 12.5 Confirmed conclusions and checks

- `GetCharmerOrOwnerOrOwnGUID` regression (git history); the block-fraction, negative-spell-power and Raise Dead defects (source).
- Five §9 numbers reproduced independently (G4-25 … G4-29); the probe agrees on the `CalculatePct` values.
- The §10 errata statements exist at the cited lines.
- `merge_unknowns.py --check`: ok, 138 entries. Write mode run twice: identical sha256 `a6533faa…`.
- `python3 tools/regen_combat_prep.py --check` (after the edits): `ok: 59 corpus files unchanged`, exit 0.
- The §11 test command: `528 passed in 114.08s`.

### 12.6 Residual risks

- The Core vocabulary check is lexical: Core documents other than architecture.md and the ledger, and Core code beyond the grepped identifiers, were not read.
- The Difficulty fallback chains come from the 12.1 snapshot. Trinity loads the 12.0.7 client's store (R4-2).
- R4-1 rests on a static read. No runtime trace confirms that Xuen/Niuzao hold PetGUID after `SetMinion`, and the effect of their recalculation (which reads no owner value in the non-Pet branches) was not modelled.
- The §8 "suspected" rows need Retail evidence that this pass cannot produce (§7).
- Track-report text that §12 found incomplete was not edited: CU §7.1 on the spirit-wolf trigger, CU §7.2 #13 on difficulties 205/216, and WC §3 on the `CalculatePct` lower bound (R4-1 … R4-3).
